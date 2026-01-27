import asyncio
import os
import logging
import logging.handlers
import time
import subprocess
import json
import re
import requests
from pathlib import Path
from datetime import datetime, timedelta

# Load environment variables from .env file
try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    # python-dotenv not installed, continue without it
    pass
# This module provides the main bot implementation using python-telegram-bot

logging.basicConfig(level=logging.INFO)
# Add file logging to persist logs for monitoring
try:
    LOG_DIR = Path(os.getenv('LOG_DIR', 'logs'))
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    log_file = LOG_DIR / 'bot.log'
    fh = logging.handlers.RotatingFileHandler(str(log_file), maxBytes=5_000_000, backupCount=3, encoding='utf-8')
    fh.setLevel(logging.INFO)
    # SECURITY: Sanitize log format to prevent log injection
    formatter = logging.Formatter('%(asctime)s %(levelname)s %(name)s: %(message)s')
    fh.setFormatter(formatter)
    logging.getLogger().addHandler(fh)
except Exception:
    # If file logging can't be set up, continue using console logging
    logging.exception('Failed to set up file logging')

# SECURITY: Rate limiting for user actions
user_action_times = {}
ACTION_RATE_LIMIT = 1.0  # seconds between actions

def check_rate_limit(user_id: int) -> bool:
    """Check if user action is within rate limits."""
    now = time.time()
    last_action = user_action_times.get(user_id, 0)
    if now - last_action < ACTION_RATE_LIMIT:
        return False
    user_action_times[user_id] = now
    return True
TOKEN = os.getenv("BOT_TOKEN")
if not TOKEN:
    logging.error("BOT_TOKEN not provided via environment. Set BOT_TOKEN before starting the bot.")
    raise SystemExit("BOT_TOKEN environment variable is required")

# REPO_PATH can be configured with env var REPO_PATH; default to local ./repo for local runs
REPO_PATH = Path(os.getenv("REPO_PATH", "repo"))
# Per-user repos base dir
USER_REPOS_DIR = Path(os.getenv("USER_REPOS_DIR", "user_repos"))
USER_REPOS_DIR.mkdir(exist_ok=True)
USER_REPOS_FILE = Path(os.getenv("USER_REPOS_FILE", "/app/data/user_repos.json"))
LOCKS_FILE = Path(os.getenv("LOCKS_FILE", "/app/data/locks.json"))

# Repository type detection constants
REPO_TYPES = {
    'GITHUB': 'github',
    'GITLAB': 'gitlab',
    'UNKNOWN': 'unknown'
}

def detect_repository_type(repo_url: str) -> str:
    """Detect repository type (GitHub/GitLab) based on URL"""
    if not repo_url:
        return REPO_TYPES['UNKNOWN']
    
    url_lower = repo_url.lower().strip()
    
    # GitHub detection
    if ('github.com' in url_lower or 
        url_lower.startswith('git@github.com:') or 
        url_lower.startswith('https://github.com/')):
        return REPO_TYPES['GITHUB']
    
    # GitLab detection
    if ('gitlab.com' in url_lower or 
        url_lower.startswith('git@gitlab.com:') or 
        url_lower.startswith('https://gitlab.com/')):
        return REPO_TYPES['GITLAB']
    
    # Self-hosted GitLab detection (common patterns)
    if ('.gitlab.' in url_lower or 
        'gitlab-' in url_lower or 
        url_lower.endswith('/gitlab') or
        # Detect self-hosted GitLab instances by common naming patterns
        ('gitlab' in url_lower and not 'github' in url_lower)):
        return REPO_TYPES['GITLAB']
    
    return REPO_TYPES['UNKNOWN']

class RepositoryURLValidator:
    """Validate repository URLs for different VCS platforms"""
    
    def __init__(self):
        self.url_patterns = {
            REPO_TYPES['GITHUB']: {
                'https_pattern': r'^https://github\.com/[\w.-]+/[\w.-]+(?:\.git)?/?$',
                'ssh_pattern': r'^git@github\.com:[\w.-]+/[\w.-]+(?:\.git)?/?$',
                'allowed_domains': ['github.com'],
                'min_path_parts': 2  # user/repo
            },
            REPO_TYPES['GITLAB']: {
                'https_pattern': r'^https://(?:[^/]+\.)?gitlab[\w.-]*/[\w.-]+(?:/[\w.-]+)*/[\w.-]+(?:\.git)?/?$',
                'ssh_pattern': r'^git@(?:[^:]+\.)?gitlab[\w.-]*:[\w.-]+(?:/[\w.-]+)*/[\w.-]+(?:\.git)?/?$',
                'allowed_domains': ['gitlab.com'],
                'min_path_parts': 2  # group/project or group/subgroup/project
            }
        }
    
    def validate_url(self, repo_url: str, repo_type: str = None) -> dict:
        """Validate repository URL and return validation result"""
        result = {
            'valid': False,
            'errors': [],
            'warnings': [],
            'detected_type': None,
            'normalized_url': None
        }
        
        if not repo_url:
            result['errors'].append("URL не может быть пустым")
            return result
        
        # Normalize URL
        normalized_url = repo_url.strip().rstrip('/')
        result['normalized_url'] = normalized_url
        
        # Auto-detect repository type if not provided
        if not repo_type:
            repo_type = detect_repository_type(normalized_url)
            result['detected_type'] = repo_type
        
        if repo_type == REPO_TYPES['UNKNOWN']:
            result['errors'].append("Не удалось определить тип репозитория. Поддерживаются только GitHub и GitLab.")
            return result
        
        # Get validation patterns for the repository type
        patterns = self.url_patterns.get(repo_type)
        if not patterns:
            result['errors'].append(f"Неподдерживаемый тип репозитория: {repo_type}")
            return result
        
        import re
        
        # Check HTTPS format
        if normalized_url.startswith('https://'):
            if not re.match(patterns['https_pattern'], normalized_url):
                result['errors'].append("Неверный формат HTTPS URL. Ожидается: https://domain/group/project(.git)")
            else:
                result['valid'] = True
        
        # Check SSH format
        elif normalized_url.startswith('git@'):
            if not re.match(patterns['ssh_pattern'], normalized_url):
                result['errors'].append("Неверный формат SSH URL. Ожидается: git@domain:group/project(.git)")
            else:
                result['valid'] = True
        
        # Invalid protocol
        else:
            result['errors'].append("Неподдерживаемый протокол. Используйте https:// или git@")
            
        # Additional validations
        if result['valid']:
            self._perform_additional_validations(normalized_url, repo_type, result)
        
        return result
    
    def _perform_additional_validations(self, url: str, repo_type: str, result: dict):
        """Perform additional validation checks"""
        # Check for common mistakes
        if '.git.git' in url:
            result['warnings'].append("Обнаружено дублирование .git в URL")
        
        if url.count('//') > 1:
            result['warnings'].append("Обнаружены множественные слэши в URL")
        
        # Check path structure
        path_parts = self._extract_path_parts(url)
        if len(path_parts) < 2:
            result['errors'].append("Недостаточно компонентов пути. Ожидается: группа/проект")
        
        # Check for reserved names
        reserved_names = ['api', 'dashboard', 'groups', 'help', 'users']
        for part in path_parts:
            if part.lower() in reserved_names:
                result['warnings'].append(f"Имя '{part}' может быть зарезервировано платформой")
    
    def _extract_path_parts(self, url: str) -> list:
        """Extract path components from URL"""
        try:
            if url.startswith('https://'):
                # Remove protocol and domain
                path = url.split('/', 3)[3] if len(url.split('/')) > 3 else ""
            elif url.startswith('git@'):
                # Extract path after colon
                path = url.split(':', 1)[1] if ':' in url else ""
            else:
                return []
            
            # Remove .git suffix and split by /
            path = path.replace('.git', '')
            return [part for part in path.split('/') if part]
        except Exception:
            return []
    
    def get_url_examples(self, repo_type: str) -> list:
        """Get URL format examples for repository type"""
        examples = {
            REPO_TYPES['GITHUB']: [
                "https://github.com/username/repository",
                "https://github.com/username/repository.git",
                "git@github.com:username/repository.git"
            ],
            REPO_TYPES['GITLAB']: [
                "https://gitlab.com/group/project",
                "https://gitlab.com/group/subgroup/project.git",
                "git@gitlab.com:group/project.git",
                "https://company.gitlab.com/group/project"  # Self-hosted
            ]
        }
        
        return examples.get(repo_type, [])
    
    def normalize_url(self, url: str, repo_type: str) -> str:
        """Normalize URL to canonical form"""
        if not url:
            return url
        
        normalized = url.strip().rstrip('/')
        
        # Add .git suffix for consistency
        if repo_type == REPO_TYPES['GITHUB'] and not normalized.endswith('.git'):
            normalized += '.git'
        elif repo_type == REPO_TYPES['GITLAB'] and not normalized.endswith('.git'):
            normalized += '.git'
        
        return normalized

# OPTIMIZATION: Removed validate_repository_accessibility() - unused legacy function
# This function tested repository accessibility but is not called anywhere.
# Repository validation is done implicitly during git clone/push operations.

def get_gitlab_project_path(repo_url: str) -> str:
    """Extract GitLab project path from repository URL"""
    try:
        if repo_url.startswith('https://'):
            # Remove https:// and .git suffix
            path_part = repo_url.replace('https://', '').replace('.git', '')
            # Split by / and get group/project parts (skip domain)
            parts = path_part.split('/')
            if len(parts) >= 3:  # domain/group/project
                return f"{parts[1]}/{parts[2]}"
        elif repo_url.startswith('git@'):
            # git@gitlab.com:group/project.git
            path_part = repo_url.split(':')[1].replace('.git', '')
            return path_part
        
        return ""
    except Exception:
        return ""

def get_vcs_specific_config(repo_type: str) -> dict:
    """Get VCS-specific configuration settings"""
    configs = {
        REPO_TYPES['GITHUB']: {
            'api_base_url': 'https://api.github.com',
            'web_base_url': 'https://github.com',
            'auth_method': 'token',
            'lfs_server_url': 'https://github.com',
            'credential_helper': 'store'
        },
        REPO_TYPES['GITLAB']: {
            'api_base_url': 'https://gitlab.com/api/v4',
            'web_base_url': 'https://gitlab.com',
            'auth_method': 'private_token',
            'lfs_server_url': 'https://gitlab.com',
            'credential_helper': 'store'
        }
    }
    
    return configs.get(repo_type, configs[REPO_TYPES['GITHUB']])  # Default to GitHub

def get_auth_prompt_message(repo_type: str) -> str:
    """Get VCS-specific authentication prompt message"""
    prompts = {
        REPO_TYPES['GITHUB']: (
            "🔐 Введите ваш GitHub логин и Personal Access Token (PAT):\n\n"
            "1. Логин GitHub (username)\n"
            "2. Personal Access Token (с доступом к repo)\n\n"
            "💡 Как создать PAT: Settings → Developer settings → Personal access tokens"
        ),
        REPO_TYPES['GITLAB']: (
            "🔐 Для GitLab требуется SSH-ключ:\n\n"
            "1. Бот сгенерирует SSH-ключ для вас\n"
            "2. Вы получите публичный ключ\n"
            "3. Добавьте его в ваш GitLab: Profile → SSH Keys\n"
            "4. Введите ваш GitLab username\n\n"
            "Нажмите любую кнопку для продолжения..."
        )
    }
    
    return prompts.get(repo_type, prompts[REPO_TYPES['GITHUB']])

def validate_gitlab_token(token: str) -> bool:
    """Validate GitLab token format (basic validation)"""
    if not token:
        return False
    
    # GitLab tokens are typically 20+ characters
    if len(token) < 20:
        return False
    
    # Basic format validation (should contain only alphanumeric and -_)
    import re
    if not re.match(r'^[a-zA-Z0-9_-]+$', token):
        return False
    
    return True

class SSHKeyManager:
    """Manage SSH key generation and storage for users"""
    
    def __init__(self):
        self.ssh_dir = Path("/app/data/ssh_keys")
        self.ssh_dir.mkdir(exist_ok=True)
    
    def generate_ssh_key_pair(self, user_id: int, email: str = None) -> dict:
        """Generate SSH key pair for user"""
        try:
            if not email:
                email = f"bot-user-{user_id}@git-docs.local"
            
            # Create user-specific directory
            user_key_dir = self.ssh_dir / str(user_id)
            user_key_dir.mkdir(exist_ok=True)
            
            private_key_path = user_key_dir / "id_ed25519"
            public_key_path = user_key_dir / "id_ed25519.pub"
            
            # Check if keys already exist
            if private_key_path.exists() and public_key_path.exists():
                # Load existing keys
                private_key = private_key_path.read_text().strip()
                public_key = public_key_path.read_text().strip()
                return {
                    'private_key': private_key,
                    'public_key': public_key,
                    'private_key_path': str(private_key_path),
                    'public_key_path': str(public_key_path)
                }
            
            # Generate new key pair
            import subprocess
            
            # Generate Ed25519 key (more secure than RSA)
            cmd = [
                "ssh-keygen",
                "-t", "ed25519",
                "-f", str(private_key_path),
                "-N", "",  # No passphrase
                "-C", email
            ]
            
            result = subprocess.run(cmd, capture_output=True, text=True, check=True)
            
            # Read generated keys
            private_key = private_key_path.read_text().strip()
            public_key = public_key_path.read_text().strip()
            
            # Set proper permissions
            private_key_path.chmod(0o600)
            public_key_path.chmod(0o644)
            
            logging.info(f"Generated SSH key pair for user {user_id}")
            
            return {
                'private_key': private_key,
                'public_key': public_key,
                'private_key_path': str(private_key_path),
                'public_key_path': str(public_key_path)
            }
            
        except subprocess.CalledProcessError as e:
            logging.error(f"Failed to generate SSH key for user {user_id}: {e.stderr}")
            return {}
        except Exception as e:
            logging.error(f"Error generating SSH key for user {user_id}: {e}")
            return {}
    
    def get_user_ssh_key(self, user_id: int) -> dict:
        """Get existing SSH key for user"""
        user_key_dir = self.ssh_dir / str(user_id)
        private_key_path = user_key_dir / "id_ed25519"
        public_key_path = user_key_dir / "id_ed25519.pub"
        
        if private_key_path.exists() and public_key_path.exists():
            return {
                'private_key': private_key_path.read_text().strip(),
                'public_key': public_key_path.read_text().strip(),
                'private_key_path': str(private_key_path),
                'public_key_path': str(public_key_path)
            }
        
        return {}
    
    def delete_user_ssh_keys(self, user_id: int) -> bool:
        """Delete SSH keys for user"""
        try:
            user_key_dir = self.ssh_dir / str(user_id)
            if user_key_dir.exists():
                import shutil
                shutil.rmtree(user_key_dir)
                logging.info(f"Deleted SSH keys for user {user_id}")
            return True
        except Exception as e:
            logging.error(f"Failed to delete SSH keys for user {user_id}: {e}")
            return False
    
    def format_public_key_for_gitlab(self, public_key: str, user_id: int) -> str:
        """Format public key for GitLab deployment key"""
        # Add descriptive comment
        key_parts = public_key.strip().split()
        if len(key_parts) >= 2:
            key_type, key_data = key_parts[0], key_parts[1]
            comment = f"git-docs-bot-user-{user_id}-key"
            return f"{key_type} {key_data} {comment}"
        return public_key

def setup_gitlab_ssh_access(user_id: int, repo_url: str) -> dict:
    """Setup SSH access for GitLab repository"""
    try:
        ssh_manager = SSHKeyManager()
        
        # Generate SSH key pair
        ssh_keys = ssh_manager.generate_ssh_key_pair(user_id)
        if not ssh_keys:
            return {'success': False, 'error': 'Failed to generate SSH keys'}
        
        # Format public key for GitLab
        formatted_public_key = ssh_manager.format_public_key_for_gitlab(
            ssh_keys['public_key'], user_id
        )
        
        # Extract GitLab instance URL
        from urllib.parse import urlparse
        parsed_url = urlparse(repo_url)
        gitlab_host = f"{parsed_url.scheme}://{parsed_url.netloc}"
        
        result = {
            'success': True,
            'public_key': formatted_public_key,
            'private_key_path': ssh_keys['private_key_path'],
            'gitlab_host': gitlab_host,
            'instructions': f"""🔐 SSH Setup Instructions:

1. Copy the public key below
2. Go to your GitLab instance: {gitlab_host}
3. Navigate to: Profile → SSH Keys
4. Paste the public key and save it
5. The bot will use the private key for Git operations

Public Key:
```
{formatted_public_key}
```"""
        }
        
        return result
        
    except Exception as e:
        logging.error(f"Failed to setup GitLab SSH access for user {user_id}: {e}")
        return {'success': False, 'error': str(e)}

def convert_https_to_ssh(https_url: str) -> str:
    """Convert HTTPS GitLab URL to SSH format"""
    try:
        from urllib.parse import urlparse
        parsed = urlparse(https_url)
        
        # Remove /-/tree/master part if present
        path = parsed.path
        if '/-/' in path:
            path = path.split('/-/')[0]
        
        # Remove leading slash and .git suffix
        path = path.lstrip('/').rstrip('/')
        if path.endswith('.git'):
            path = path[:-4]
        
        # Construct SSH URL
        ssh_url = f"git@{parsed.hostname}:{path}.git"
        return ssh_url
        
    except Exception as e:
        logging.error(f"Failed to convert HTTPS to SSH URL: {e}")
        return https_url

def configure_ssh_for_git_operation(private_key_path: str, repo_path: str = None):
    """Configure SSH key for Git operation"""
    try:
        import os
        # Set GIT_SSH_COMMAND to use specific private key
        os.environ['GIT_SSH_COMMAND'] = f"ssh -i {private_key_path} -o StrictHostKeyChecking=no"
        
        # Also configure core.sshCommand in repository config for Git LFS
        if repo_path:
            subprocess.run(["git", "config", "core.sshCommand", f"ssh -i {private_key_path} -o StrictHostKeyChecking=no"], 
                          cwd=repo_path, capture_output=True)
            logging.info(f"Configured SSH key for repo {repo_path}: {private_key_path}")
            
            # Save Git configuration for persistence
            try:
                # Extract user_id from repo_path if possible
                import re
                user_id_match = re.search(r'/user_repos/(\d+)/?', repo_path)
                if user_id_match:
                    user_id = int(user_id_match.group(1))
                    save_git_config_to_user_data(user_id, repo_path)
            except (ValueError, TypeError, AttributeError):
                pass
        else:
            logging.info(f"Configured global SSH key: {private_key_path}")
            
    except Exception as e:
        logging.error(f"Failed to configure SSH for Git: {e}")

def configure_gitlab_credentials(repo_path: str, gitlab_username: str, private_token: str, user_id: int = None):
    """Configure Git credentials specifically for GitLab"""
    try:
        # Set GitLab-specific user configuration
        subprocess.run(["git", "config", "user.name", gitlab_username], cwd=repo_path, check=True, capture_output=True)
        email = f"{gitlab_username}@users.noreply.gitlab.com"
        subprocess.run(["git", "config", "user.email", email], cwd=repo_path, check=True, capture_output=True)
        
        # Configure GitLab LFS for the specific instance
        # Get the GitLab host from the repository remote URL
        try:
            remote_result = subprocess.run(["git", "remote", "get-url", "origin"], 
                                         cwd=repo_path, capture_output=True, text=True)
            if remote_result.returncode == 0:
                remote_url = remote_result.stdout.strip()
                import re
                # Extract host from SSH or HTTPS URL
                if remote_url.startswith('git@'):
                    host_match = re.match(r'git@([^:]+):', remote_url)
                else:
                    host_match = re.match(r'https://([^/]+)/', remote_url)
                
                if host_match:
                    gitlab_host = host_match.group(1)
                    lfs_url = f"https://{gitlab_host}"
                    subprocess.run(["git", "config", "lfs.url", lfs_url], cwd=repo_path, check=True, capture_output=True)
                else:
                    # Fallback to gitlab.com
                    subprocess.run(["git", "config", "lfs.url", "https://gitlab.com"], cwd=repo_path, check=True, capture_output=True)
            else:
                # Fallback to gitlab.com
                subprocess.run(["git", "config", "lfs.url", "https://gitlab.com"], cwd=repo_path, check=True, capture_output=True)
        except Exception:
            # Fallback to gitlab.com
            subprocess.run(["git", "config", "lfs.url", "https://gitlab.com"], cwd=repo_path, check=True, capture_output=True)
        
        # Create personal credential file for GitLab
        if user_id:
            cred_filename = f".git-credentials-gitlab-{user_id}"
        else:
            cred_filename = ".git-credentials-gitlab-default"
            
        cred_file = Path("/app/data") / cred_filename
        # GitLab credential format: https://oauth2:TOKEN@host
        # Use the same host detection logic
        gitlab_host = "gitlab.com"  # default
        try:
            remote_result = subprocess.run(["git", "remote", "get-url", "origin"], 
                                         cwd=repo_path, capture_output=True, text=True)
            if remote_result.returncode == 0:
                remote_url = remote_result.stdout.strip()
                import re
                if remote_url.startswith('git@'):
                    host_match = re.match(r'git@([^:]+):', remote_url)
                else:
                    host_match = re.match(r'https://([^/]+)/', remote_url)
                if host_match:
                    gitlab_host = host_match.group(1)
        except Exception:
            pass
            
        cred_content = f"https://oauth2:{private_token}@{gitlab_host}\n"
        cred_file.write_text(cred_content)
        cred_file.chmod(0o600)
        
        # Configure Git to use personal credential file for this repository
        subprocess.run(["git", "config", "credential.helper", f"store --file={cred_file}"], 
                      cwd=repo_path, check=True, capture_output=True)
        
        logging.info(f"GitLab credentials configured for user {user_id} ({gitlab_username})")
        return True
        
    except Exception as e:
        logging.error(f"Failed to configure GitLab credentials: {e}")
        return False

def setup_gitlab_lfs_credentials(repo_path: str, repo_url: str, user_id: int = None):
    """Setup credentials for Git LFS operations on GitLab repositories.
    For SSH repositories, this is a no-op. For HTTPS, it configures credential helper."""
    try:
        # For SSH repositories, no credentials needed - skip this
        if repo_url.startswith('git@'):
            logging.info(f"SSH repository detected, skipping credential helper setup")
            return True
        
        # For HTTPS repositories, ensure Git credentials are available
        if repo_url.startswith('https://'):
            import re
            match = re.match(r'https://([^/]+)/', repo_url)
            if not match:
                logging.warning(f"Could not extract GitLab host from {repo_url}")
                return False
            
            gitlab_host = match.group(1)
            
            # For Docker, credentials should be in /app/data
            app_data_creds = Path("/app/data/.git-credentials")
            
            # Configure git to use this credentials file
            subprocess.run(["git", "config", "--global", "credential.helper", f"store --file={str(app_data_creds)}"], 
                          capture_output=True)
            
            # Also configure for the specific repository
            subprocess.run(["git", "config", "credential.helper", f"store --file={str(app_data_creds)}"], 
                          cwd=str(repo_path), capture_output=True)
            
            logging.info(f"Git credentials helper configured for HTTPS repository {gitlab_host}")
            return True
        else:
            logging.warning(f"Unknown repository protocol for {repo_url}")
            return True
            
    except Exception as e:
        logging.error(f"Failed to setup GitLab LFS credentials: {e}")
        return False

class GitLabAuthManager:
    """Manage GitLab authentication and token validation"""
    
    def __init__(self):
        self.token_cache = {}
    
    def validate_and_store_token(self, user_id: int, token: str, project_path: str = None) -> bool:
        """Validate GitLab token and store it securely"""
        if not validate_gitlab_token(token):
            logging.warning(f"Invalid GitLab token format for user {user_id}")
            return False
        
        # Test token validity using GitLab API
        client = GitLabAPIClient(private_token=token)
        try:
            # Simple API call to verify token
            response = client.session.get(f"{client.api_url}/version", timeout=10)
            if response.status_code == 200:
                # Store valid token
                self.token_cache[user_id] = {
                    'token': token,
                    'validated_at': datetime.now().isoformat(),
                    'project_path': project_path
                }
                logging.info(f"GitLab token validated and stored for user {user_id}")
                return True
            else:
                logging.warning(f"GitLab token validation failed: {response.status_code}")
                return False
        except Exception as e:
            logging.error(f"GitLab token validation error: {e}")
            return False
    
    def get_user_token(self, user_id: int) -> str:
        """Retrieve stored GitLab token for user"""
        user_data = self.token_cache.get(user_id, {})
        return user_data.get('token', '')
    
    def is_token_valid(self, user_id: int) -> bool:
        """Check if stored token is still valid (basic check)"""
        user_data = self.token_cache.get(user_id, {})
        if not user_data.get('token'):
            return False
        
        # Check if token was validated recently (within 24 hours)
        validated_at = user_data.get('validated_at')
        if validated_at:
            validated_time = datetime.fromisoformat(validated_at)
            if datetime.now() - validated_time < timedelta(hours=24):
                return True
        
        return False
    
    def invalidate_token(self, user_id: int):
        """Remove invalidated token from cache"""
        if user_id in self.token_cache:
            del self.token_cache[user_id]
            logging.info(f"Invalidated GitLab token for user {user_id}")


class GitLabAPIClient:
    """GitLab API client for repository operations"""
    
    def __init__(self, private_token: str = None, api_url: str = None):
        self.private_token = private_token
        self.api_url = api_url or "https://gitlab.com/api/v4"
        self.session = requests.Session() if 'requests' in globals() else None
        
        if self.session and self.private_token:
            self.session.headers.update({
                'PRIVATE-TOKEN': self.private_token,
                'Content-Type': 'application/json'
            })
    
    def get_project_info(self, project_id_or_path: str) -> dict:
        """Get project information from GitLab"""
        try:
            if not self.session:
                logging.warning("Requests library not available for GitLab API")
                return {}
            
            # Handle both project ID and path formats
            if '/' in project_id_or_path:
                # URL-encoded project path
                encoded_path = requests.utils.quote(project_id_or_path, safe='')
                url = f"{self.api_url}/projects/{encoded_path}"
            else:
                # Numeric project ID
                url = f"{self.api_url}/projects/{project_id_or_path}"
            
            response = self.session.get(url, timeout=30)
            response.raise_for_status()
            return response.json()
            
        except Exception as e:
            logging.error(f"Failed to get GitLab project info: {e}")
            return {}
    
    def get_project_files(self, project_id: str, path: str = "", ref: str = "main") -> list:
        """List files in a GitLab project directory"""
        try:
            if not self.session:
                return []
            
            encoded_path = requests.utils.quote(path, safe='')
            url = f"{self.api_url}/projects/{project_id}/repository/tree"
            params = {
                'path': encoded_path,
                'ref': ref,
                'recursive': False
            }
            
            response = self.session.get(url, params=params, timeout=30)
            response.raise_for_status()
            return response.json()
            
        except Exception as e:
            logging.error(f"Failed to get GitLab project files: {e}")
            return []
    
    def get_file_content(self, project_id: str, file_path: str, ref: str = "main") -> str:
        """Get file content from GitLab repository"""
        try:
            if not self.session:
                return ""
            
            encoded_path = requests.utils.quote(file_path, safe='')
            url = f"{self.api_url}/projects/{project_id}/repository/files/{encoded_path}/raw"
            params = {'ref': ref}
            
            response = self.session.get(url, params=params, timeout=30)
            response.raise_for_status()
            return response.text
            
        except Exception as e:
            logging.error(f"Failed to get GitLab file content: {e}")
            return ""
    
    def create_branch(self, project_id: str, branch_name: str, ref: str = "main") -> dict:
        """Create a new branch in GitLab project"""
        try:
            if not self.session:
                return {}
            
            url = f"{self.api_url}/projects/{project_id}/repository/branches"
            data = {
                'branch': branch_name,
                'ref': ref
            }
            
            response = self.session.post(url, json=data, timeout=30)
            response.raise_for_status()
            return response.json()
            
        except Exception as e:
            logging.error(f"Failed to create GitLab branch: {e}")
            return {}
    
    def create_commit(self, project_id: str, branch: str, commit_message: str, 
                     actions: list, author_email: str = None, author_name: str = None) -> dict:
        """Create a commit in GitLab project"""
        try:
            if not self.session:
                return {}
            
            url = f"{self.api_url}/projects/{project_id}/repository/commits"
            data = {
                'branch': branch,
                'commit_message': commit_message,
                'actions': actions
            }
            
            if author_email:
                data['author_email'] = author_email
            if author_name:
                data['author_name'] = author_name
            
            response = self.session.post(url, json=data, timeout=30)
            response.raise_for_status()
            return response.json()
            
        except Exception as e:
            logging.error(f"Failed to create GitLab commit: {e}")
            return {}
    
    def get_lfs_locks(self, project_id: str) -> list:
        """Get LFS locks for a GitLab project"""
        try:
            if not self.session:
                return []
            
            url = f"{self.api_url}/projects/{project_id}/lfs/locks"
            response = self.session.get(url, timeout=30)
            response.raise_for_status()
            return response.json()
            
        except Exception as e:
            logging.error(f"Failed to get GitLab LFS locks: {e}")
            return []
    
    def create_lfs_lock(self, project_id: str, path: str) -> dict:
        """Create LFS lock for a file in GitLab project"""
        try:
            if not self.session:
                return {}
            
            url = f"{self.api_url}/projects/{project_id}/lfs/locks"
            data = {'path': path}
            
            response = self.session.post(url, json=data, timeout=30)
            response.raise_for_status()
            return response.json()
            
        except Exception as e:
            logging.error(f"Failed to create GitLab LFS lock: {e}")
            return {}
    
    def delete_lfs_lock(self, project_id: str, lock_id: str) -> bool:
        """Delete LFS lock in GitLab project"""
        try:
            if not self.session:
                return False
            
            url = f"{self.api_url}/projects/{project_id}/lfs/locks/{lock_id}/unlock"
            response = self.session.delete(url, timeout=30)
            response.raise_for_status()
            return True
            
        except Exception as e:
            logging.error(f"Failed to delete GitLab LFS lock: {e}")
            return False

# SECURITY: validate_path_safety function was removed as it was not used

# Try to import python-telegram-bot (official library)
PTB_AVAILABLE = False
try:
    from telegram import ReplyKeyboardMarkup as PTBReplyKeyboardMarkup, KeyboardButton as PTBKeyboardButton, InputFile as PTBInputFile, ReplyKeyboardMarkup
    from telegram import Update
    from telegram.ext import ApplicationBuilder, CommandHandler, MessageHandler, filters, ContextTypes
    PTB_AVAILABLE = True
except Exception:
    PTB_AVAILABLE = False

# Logging to group configuration
LOG_GROUP_ID = -1003579467282


def initialize_persistent_credentials():
    """Initialize personal Git credentials system on startup"""
    try:
        data_dir = Path("/app/data")
        # Look for existing personal credential files
        personal_cred_files = list(data_dir.glob(".git-credentials-*"))
        
        if personal_cred_files:
            logging.info(f"Found {len(personal_cred_files)} personal credential files")
            # Each repository will use its own credential file configured during setup
        else:
            logging.info("No personal credentials found, will create on first user setup")
            
        # Clean up old global credential file if exists
        old_cred_file = data_dir / ".git-credentials"
        if old_cred_file.exists():
            old_cred_file.unlink()
            logging.info("Removed old global credential file")
            
    except Exception as e:
        logging.error(f"Failed to initialize personal credentials system: {e}")

async def log_to_group(message, message_text):
    """Send log messages to the specified group"""
    try:
        # Try to use the bot from the message context (PTBMessageAdapter)
        if hasattr(message, 'context') and hasattr(message.context, 'bot'):
            # This is a PTBMessageAdapter, use the real bot
            await message.context.bot.send_message(chat_id=LOG_GROUP_ID, text=message_text)
        else:
            # Fallback to global bot if available
            global bot
            if 'bot' in globals() and hasattr(bot, 'send_message'):
                # Check if bot is a stub by checking if it has the expected methods
                # The stub bot has a different implementation than a real bot
                if hasattr(bot, 'token') and 'Stub' in str(type(bot)):
                    # This is a stub bot, don't use it
                    logging.warning(f"Cannot send log to group {LOG_GROUP_ID}: using stub bot")
                else:
                    await bot.send_message(chat_id=LOG_GROUP_ID, text=message_text)
            else:
                logging.warning(f"Cannot send log to group {LOG_GROUP_ID}: no bot instance available")
    except Exception as e:
        logging.warning(f"Failed to send log to group {LOG_GROUP_ID}: {e}")

# Admins (comma-separated user ids) can force-unlock etc. Provide via env var ADMIN_IDS
ADMIN_IDS = set([s for s in os.getenv("ADMIN_IDS", "").split(",") if s.strip()])
ADMIN_IDS.add("309462378")  # Adding default admin ID
AUTO_UNLOCK_ON_UPLOAD = os.getenv("AUTO_UNLOCK_ON_UPLOAD", "false").lower() in ("1", "true", "yes")

# Create locks file if it doesn't exist
if not LOCKS_FILE.exists():
    # ensure parent directory exists
    try:
        LOCKS_FILE.parent.mkdir(parents=True, exist_ok=True)
        LOCKS_FILE.write_text(json.dumps({}))
    except Exception:
        # if creation fails (e.g., permission issues), proceed and helpers will handle missing file
        pass

# Create user repos file if it doesn't exist
if not USER_REPOS_FILE.exists() or not USER_REPOS_FILE.is_file():
    try:
        USER_REPOS_FILE.parent.mkdir(parents=True, exist_ok=True)
        # If the path exists but is a directory (e.g., due to Docker volume mount), we can't safely remove it
        if USER_REPOS_FILE.exists() and USER_REPOS_FILE.is_dir():
            logging.warning(f"USER_REPOS_FILE path exists as directory, cannot create as file: {USER_REPOS_FILE}")
        else:
            USER_REPOS_FILE.write_text(json.dumps({}))
    except Exception:
        # if creation fails (e.g., permission issues), proceed and helpers will handle missing file
        pass


# Local lock functions removed - using Git LFS locks exclusively
# get_repo_header function was removed as it was deprecated and unused

# Global cache for user repositories
global user_repos_cache
user_repos_cache = None

def load_user_repos() -> dict:
    global user_repos_cache
    
    # Return cached data if available
    if user_repos_cache is not None:
        return user_repos_cache
    
    try:
        # Check if the path exists and is a file (not a directory)
        if USER_REPOS_FILE.exists():
            if USER_REPOS_FILE.is_file():
                user_repos_cache = json.loads(USER_REPOS_FILE.read_text())
                return user_repos_cache
            else:
                # Path exists but is a directory (likely due to Docker volume mount when file didn't exist)
                # Return empty dict since we can't safely remove a mounted directory
                logging.warning(f"USER_REPOS_FILE path exists as directory: {USER_REPOS_FILE}. This may be due to Docker volume mounting behavior.")
                return {}
    except Exception:
        logging.exception("Failed to load user repos file")
    return {}


def _mask_repo_url(url: str) -> str:
    """Mask credentials in an https URL for safe logging."""
    try:
        if url.startswith('https://') and '@' in url:
            # https://user:pass@host/... -> https://user:***@host/...
            prefix, rest = url.split('://', 1)
            creds, host = rest.split('@', 1)
            if ':' in creds:
                user, _ = creds.split(':', 1)
                return f"https://{user}:***@{host}"
    except Exception:
        pass
    return url


def save_user_repos(m: dict):
    global user_repos_cache
    try:
        # Update cache first
        user_repos_cache = m
        
        # Ensure parent directory exists before writing
        USER_REPOS_FILE.parent.mkdir(parents=True, exist_ok=True)
        # If the path exists but is a directory (e.g., due to Docker volume mount), we can't safely remove it
        if USER_REPOS_FILE.exists() and USER_REPOS_FILE.is_dir():
            logging.warning(f"Cannot save to USER_REPOS_FILE: path exists as directory: {USER_REPOS_FILE}")
            return
        USER_REPOS_FILE.write_text(json.dumps(m, ensure_ascii=False, indent=2))
    except Exception:
        logging.exception("Failed to save user repos file")


def set_user_repo(user_id: int, repo_path: str, repo_url: str = None, username: str = None, 
                  telegram_username: str = None, repo_type: str = None, auth_token: str = None):
    """Store user repository mapping using composite key: telegram_id:git_username"""
    m = load_user_repos()
    
    # Create composite key
    if username:
        composite_key = f"{user_id}:{username}"
    else:
        # Fallback to just user_id if no username provided
        composite_key = str(user_id)
    
    # Detect repository type if not provided
    if not repo_type and repo_url:
        repo_type = detect_repository_type(repo_url)
    
    m[composite_key] = {
        'telegram_id': user_id,
        'telegram_username': telegram_username,
        'git_username': username,
        'repo_path': str(repo_path),
        'repo_url': repo_url,
        'repo_type': repo_type or REPO_TYPES['UNKNOWN'],
        'auth_token': auth_token,  # Store encrypted token reference
        'created_at': datetime.now().isoformat(),
        'last_updated': datetime.now().isoformat()
    }
    save_user_repos(m)


def get_user_repo(user_id: int, git_username: str = None):
    """Get user repository by Telegram ID and optional Git username.
    If git_username is provided, looks for exact match.
    If not provided, returns first match for the user_id."""
    m = load_user_repos()
    
    if git_username:
        # Look for exact composite key match
        composite_key = f"{user_id}:{git_username}"
        if composite_key in m:
            return m[composite_key]
        # Fallback: look for any entry with this user_id
        
    # Find any entry for this user_id
    for key, repo_data in m.items():
        if str(repo_data.get('telegram_id')) == str(user_id):
            return repo_data
    
    return None

class VCSConfigurationManager:
    """Manage VCS-specific configurations and settings"""
    
    def __init__(self):
        self.config_cache = {}
    
    def get_user_vcs_config(self, user_id: int, git_username: str = None) -> dict:
        """Get VCS-specific configuration for user"""
        user_repo = get_user_repo(user_id, git_username)
        if not user_repo:
            return {}
        
        repo_type = user_repo.get('repo_type', REPO_TYPES['UNKNOWN'])
        repo_url = user_repo.get('repo_url', '')
        
        # Get base VCS configuration
        base_config = get_vcs_specific_config(repo_type)
        
        # Add user-specific settings
        user_config = {
            'repo_type': repo_type,
            'repo_url': repo_url,
            'repo_path': user_repo.get('repo_path', ''),
            'git_username': user_repo.get('git_username', ''),
            'base_config': base_config,
            'credentials_configured': self._check_credentials_configured(user_id, repo_type)
        }
        
        return user_config
    
    def _check_credentials_configured(self, user_id: int, repo_type: str) -> bool:
        """Check if credentials are configured for user and VCS type"""
        try:
            # Check for credential files
            data_dir = Path("/app/data")
            cred_patterns = []
            
            if repo_type == REPO_TYPES['GITHUB']:
                cred_patterns = [f".git-credentials-{user_id}", f".git-credentials-github-{user_id}"]
            elif repo_type == REPO_TYPES['GITLAB']:
                cred_patterns = [f".git-credentials-gitlab-{user_id}", f".git-credentials-lfs-{user_id}"]
            
            for pattern in cred_patterns:
                cred_file = data_dir / pattern
                if cred_file.exists():
                    return True
            
            return False
        except Exception:
            return False
    
    def update_user_repo_config(self, user_id: int, updates: dict, git_username: str = None) -> bool:
        """Update user repository configuration"""
        try:
            user_repos = load_user_repos()
            
            # Find the correct entry
            target_key = None
            if git_username:
                target_key = f"{user_id}:{git_username}"
            else:
                # Find any entry for this user
                for key, repo_data in user_repos.items():
                    if str(repo_data.get('telegram_id')) == str(user_id):
                        target_key = key
                        break
            
            if not target_key or target_key not in user_repos:
                logging.warning(f"No repository found for user {user_id}")
                return False
            
            # Update the entry
            user_repos[target_key].update(updates)
            user_repos[target_key]['last_updated'] = datetime.now().isoformat()
            
            save_user_repos(user_repos)
            logging.info(f"Updated repository config for user {user_id}")
            return True
            
        except Exception as e:
            logging.error(f"Failed to update user repo config: {e}")
            return False
    
    def get_repository_status(self, user_id: int, git_username: str = None) -> dict:
        """Get comprehensive repository status for user"""
        user_repo = get_user_repo(user_id, git_username)
        if not user_repo:
            return {'status': 'not_configured', 'details': 'Repository not set up'}
        
        repo_path = Path(user_repo.get('repo_path', ''))
        repo_url = user_repo.get('repo_url', '')
        repo_type = user_repo.get('repo_type', REPO_TYPES['UNKNOWN'])
        
        status_info = {
            'repo_type': repo_type,
            'repo_url': repo_url,
            'repo_path': str(repo_path),
            'git_username': user_repo.get('git_username', ''),
            'created_at': user_repo.get('created_at', ''),
            'last_updated': user_repo.get('last_updated', '')
        }
        
        # Check repository existence
        if not repo_path.exists():
            status_info['status'] = 'path_missing'
            status_info['details'] = 'Repository directory does not exist'
            return status_info
        
        # Check if it's a git repository
        git_dir = repo_path / ".git"
        if not git_dir.exists():
            status_info['status'] = 'not_git_repo'
            status_info['details'] = 'Directory exists but is not a git repository'
            return status_info
        
        # Check remote connectivity
        try:
            result = subprocess.run(["git", "-C", str(repo_path), "remote", "show", "origin"], 
                                  capture_output=True, text=True, timeout=10)
            if result.returncode == 0:
                status_info['status'] = 'connected'
                status_info['details'] = 'Repository connected and accessible'
            else:
                status_info['status'] = 'connection_error'
                status_info['details'] = 'Cannot connect to remote repository'
        except subprocess.TimeoutExpired:
            status_info['status'] = 'timeout'
            status_info['details'] = 'Connection timeout when checking repository'
        except Exception as e:
            status_info['status'] = 'error'
            status_info['details'] = f'Error checking repository: {str(e)}'
        
        return status_info
    
    def reset_user_repository(self, user_id: int, git_username: str = None) -> bool:
        """Reset user repository configuration and clean up files"""
        try:
            user_repo = get_user_repo(user_id, git_username)
            if not user_repo:
                return False
            
            repo_path = Path(user_repo.get('repo_path', ''))
            
            # Remove repository directory
            if repo_path.exists() and repo_path.is_dir():
                import shutil
                shutil.rmtree(repo_path)
                logging.info(f"Removed repository directory: {repo_path}")
            
            # Remove credential files
            self._cleanup_user_credentials(user_id, user_repo.get('repo_type', ''))
            
            # Remove from user repos
            user_repos = load_user_repos()
            target_key = f"{user_id}:{user_repo.get('git_username', '')}"
            if target_key in user_repos:
                del user_repos[target_key]
                save_user_repos(user_repos)
                logging.info(f"Removed user repository entry for {user_id}")
            
            return True
            
        except Exception as e:
            logging.error(f"Failed to reset user repository: {e}")
            return False
    
    def _cleanup_user_credentials(self, user_id: int, repo_type: str):
        """Clean up credential files for user"""
        try:
            data_dir = Path("/app/data")
            patterns = []
            
            if repo_type == REPO_TYPES['GITHUB']:
                patterns = [f".git-credentials-{user_id}*", f".git-credentials-github-{user_id}*"]
            elif repo_type == REPO_TYPES['GITLAB']:
                patterns = [f".git-credentials-gitlab-{user_id}*", f".git-credentials-lfs-{user_id}*"]
            
            for pattern in patterns:
                for cred_file in data_dir.glob(pattern):
                    if cred_file.exists():
                        cred_file.unlink()
                        logging.info(f"Removed credential file: {cred_file}")
                        
        except Exception as e:
            logging.error(f"Failed to cleanup credentials: {e}")

def migrate_user_repos_format() -> bool:
    """Migrate existing user_repos to new format with VCS support"""
    try:
        user_repos = load_user_repos()
        migrated = False
        
        for key, repo_data in user_repos.items():
            # Add missing fields
            if 'repo_type' not in repo_data:
                repo_url = repo_data.get('repo_url', '')
                repo_data['repo_type'] = detect_repository_type(repo_url)
                migrated = True
            
            if 'last_updated' not in repo_data:
                repo_data['last_updated'] = repo_data.get('created_at', datetime.now().isoformat())
                migrated = True
            
            if 'auth_token' not in repo_data:
                repo_data['auth_token'] = None
                migrated = True
        
        if migrated:
            save_user_repos(user_repos)
            logging.info("User repos format migrated successfully")
            return True
        
        return False
        
    except Exception as e:
        logging.error(f"Failed to migrate user repos format: {e}")
        return False


def create_basic_user_entry(user_id: int, telegram_username: str = None):
    """Create basic user entry for new user"""
    try:
        user_repos = load_user_repos()
        
        # Create basic user structure
        user_key = str(user_id)
        repo_path = f"/app/user_repos/{user_id}"
        
        user_repos[user_key] = {
            'telegram_id': user_id,
            'telegram_username': telegram_username or f"user_{user_id}",
            'git_username': None,  # Will be set by user later
            'repo_path': repo_path,
            'repo_url': None,  # Will be set by user
            'repo_type': REPO_TYPES['UNKNOWN'],  # Will be detected from URL
            'auth_token': None,  # Will be set during authentication
            'created_at': datetime.now().isoformat(),
            'last_updated': datetime.now().isoformat()
        }
        
        save_user_repos(user_repos)
        
        logging.info(f"Created basic user entry for user {user_id}")
        return user_repos[user_key]
        
    except Exception as e:
        logging.error(f"Failed to create basic user entry: {e}")
        return None


def configure_git_with_credentials(repo_path: str, git_username: str, pat: str, user_id: int = None):
    """Configure Git with personal credentials for specific user"""
    try:
        # Set user configuration
        subprocess.run(["git", "config", "user.name", git_username], cwd=repo_path, check=True, capture_output=True)
        email = f"{git_username}@users.noreply.github.com"
        subprocess.run(["git", "config", "user.email", email], cwd=repo_path, check=True, capture_output=True)
        
        # Create personal credential file for this user
        if user_id:
            cred_filename = f".git-credentials-{user_id}"
        else:
            cred_filename = ".git-credentials-default"
            
        cred_file = Path("/app/data") / cred_filename
        cred_content = f"https://{git_username}:{pat}@github.com\n"
        cred_file.write_text(cred_content)
        cred_file.chmod(0o600)
        
        # Configure Git to use personal credential file for this repository only
        subprocess.run(["git", "config", "credential.helper", f"store --file={cred_file}"], 
                      cwd=repo_path, check=True, capture_output=True)
        
        logging.info(f"Personal Git credentials configured for user {user_id} ({git_username})")
        
    except Exception as e:
        logging.error(f"Failed to configure personal Git credentials: {e}")


def configure_git_credentials(repo_path: str, user_id: int = None):
    """Configure Git credentials for repository - user must set their own credentials"""
    try:
        # Set user name from user repo config
        user_info = get_user_repo(user_id) if user_id else None
        git_username = user_info.get('git_username') if user_info else None
        
        if git_username:
            subprocess.run(["git", "config", "user.name", git_username], cwd=repo_path, check=True, capture_output=True)
            email = f"{git_username}@users.noreply.github.com"
            subprocess.run(["git", "config", "user.email", email], cwd=repo_path, check=True, capture_output=True)
        
        # Configure credential helper
        subprocess.run(["git", "config", "credential.helper", "store"], cwd=repo_path, check=True, capture_output=True)
        
        # Inform user that they need to set up authentication
        logging.info(f"Git credentials configured for user {user_id}. User must authenticate with their GitHub credentials when needed.")
        
    except Exception as e:
        logging.error(f"Failed to configure Git credentials: {e}")


def format_datetime() -> str:
    """Format current datetime as YYYY-MM-DD HH:MM:SS with UTC+3 offset"""
    # Add 3 hours for UTC+3
    utc_plus_3 = datetime.now() + timedelta(hours=3)
    return utc_plus_3.strftime("%Y-%m-%d %H:%M:%S")


def format_user_name(message) -> str:
    """Format user name as Telegram hyperlink: [@username](https://t.me/username) or first_name"""
    user_id = None
    username = None
    first_name = None
    
    # Try to get user info from message object
    if hasattr(message, 'from_user'):
        user_id = getattr(message.from_user, 'id', None)
        username = getattr(message.from_user, 'username', None)
        first_name = getattr(message.from_user, 'first_name', None)
    
    # Fallback: try to get from update if available (for PTBMessageAdapter)
    if not user_id and hasattr(message, 'update') and hasattr(message.update, 'effective_user'):
        effective_user = message.update.effective_user
        if effective_user:
            user_id = getattr(effective_user, 'id', None)
            username = getattr(effective_user, 'username', None)
            first_name = getattr(effective_user, 'first_name', None)
    
    # Format as Telegram hyperlink: prefer username, then first_name
    if username:
        return f"[ @{username} ](https://t.me/{username})"
    elif first_name:
        return first_name
    elif user_id:
        return f"user_{user_id}"
    else:
        return "unknown"


def get_repo_header_for_user(user_id: int) -> str:
    """Return header showing configured repo and connection status for the user."""
    try:
        u = get_user_repo(user_id)
        if not u:
            return ""
        rp = Path(u.get('repo_path'))
        url = u.get('repo_url')
        status = "не настроен"
        if rp.exists() and (rp / '.git').exists():
            # Check remote connectivity quickly
            try:
                proc = subprocess.run(["git", "-C", str(rp), "remote", "show", "origin"], check=True, capture_output=True, text=True, timeout=5)
                status = "подключен"
            except Exception:
                status = "не подключен"
        header = f"📂 Репозиторий: {url or rp} — {status}\n\n"
        return header
    except Exception:
        return ""


def get_repo_for_user_id(user_id: int) -> Path:
    """Return the repository Path to use for given user_id (per-user if configured, otherwise global REPO_PATH)."""
    u = get_user_repo(user_id)
    if u:
        p = Path(u.get('repo_path'))
        if p.exists():
            return p
    return REPO_PATH


async def require_user_repo(message):
    """Ensure the user has a configured repository. If not, send a prompt and return None.
    On success, return Path to repo root."""
    u = get_user_repo(message.from_user.id)
    if not u:
        await message.answer("❌ Репозиторий не настроен. Пожалуйста, настройте репозиторий сначала.", reply_markup=get_main_keyboard(message.from_user.id))
        return None
    p = Path(u.get('repo_path'))
    if not p.exists() or not (p / '.git').exists():
        await message.answer("❌ Репозиторий пользователя не доступен или не склонирован. Пожалуйста, настройте репозиторий повторно.", reply_markup=get_main_keyboard(message.from_user.id))
        return None
    return p


def git_pull_rebase_autostash(cwd: str, auto_commit_paths=None):
    """Attempt to `git pull --rebase --autostash` and fall back to explicit stash/pull/pop when unstaged changes block rebase.
    Returns (True, None) on success, (False, error_message) on failure.
    """
    try:
        subprocess.run(["git", "pull", "--rebase", "--autostash"], cwd=cwd, check=True, capture_output=True)
        return True, None
    except subprocess.CalledProcessError as e:
        out = (e.stderr or e.stdout or b'')
        try:
            err = out.decode(errors='ignore') if isinstance(out, (bytes, bytearray)) else str(out)
        except Exception:
            err = str(out)
        # Detect unstaged/uncommitted change messages and try options:
        # 1) If the specific `auto_commit_paths` are provided, attempt a simple auto-commit flow
        # 2) Otherwise, attempt stash/pull/pop
        if 'unstaged' in err.lower() or 'please commit or stash' in err.lower() or 'cannot pull with rebase' in err.lower():
            try:
                status_result = subprocess.run(["git", "status", "--porcelain"], cwd=cwd, check=True, capture_output=True)
                status = status_result.stdout.decode('utf-8', errors='replace') if isinstance(status_result.stdout, bytes) else status_result.stdout
                status = status.strip()
            except subprocess.CalledProcessError:
                status = ''

            # Log status for diagnostics
            logging.info("git status before autostash: %s", status)

            if auto_commit_paths:
                try:
                    # Stage the paths (no-op if already staged)
                    subprocess.run(["git", "add"] + auto_commit_paths, cwd=cwd, check=True, capture_output=True)
                    # Try commit; if nothing to commit, commit.returncode != 0
                    commit = subprocess.run(["git", "commit", "-m", "Auto-commit: prepare for pull by bot"], cwd=cwd, capture_output=True, text=True)
                    if commit.returncode == 0:
                        logging.info("Auto-commit succeeded: %s", commit.stdout)
                        subprocess.run(["git", "pull", "--rebase"], cwd=cwd, check=True, capture_output=True)
                        return True, None
                    else:
                        logging.info("Auto-commit produced no changes or failed: %s", commit.stdout + commit.stderr)
                except subprocess.CalledProcessError as e2:
                    out2 = (e2.stderr or e2.stdout or b'')
                    try:
                        err2 = out2.decode(errors='ignore') if isinstance(out2, (bytes, bytearray)) else str(out2)
                    except Exception:
                        err2 = str(out2)
                    logging.warning("Auto-commit attempt failed: %s", err2)

            # Fallback: try stash / pull / pop, but capture diagnostics for failure cases
            try:
                subprocess.run(["git", "stash", "push", "-u", "-m", "autostash-by-bot"], cwd=cwd, check=True, capture_output=True)
                subprocess.run(["git", "pull", "--rebase"], cwd=cwd, check=True, capture_output=True)
                # Try to pop stash; if it conflicts this will leave stash intact and we report it
                pop_result = subprocess.run(["git", "stash", "pop"], cwd=cwd, capture_output=True)
                if pop_result.returncode != 0:
                    pop_stdout = pop_result.stdout.decode('utf-8', errors='replace') if isinstance(pop_result.stdout, bytes) else pop_result.stdout
                    pop_stderr = pop_result.stderr.decode('utf-8', errors='replace') if isinstance(pop_result.stderr, bytes) else pop_result.stderr
                    logging.warning("git stash pop failed: %s", pop_stdout + pop_stderr)
                return True, None
            except subprocess.CalledProcessError as e3:
                out3 = (e3.stderr or e3.stdout or b'')
                try:
                    err3 = out3.decode(errors='ignore') if isinstance(out3, (bytes, bytearray)) else str(out3)
                except Exception:
                    err3 = str(out3)

                # Gather some diagnostics to help triage
                try:
                    status_after_result = subprocess.run(["git", "status", "--porcelain"], cwd=cwd, check=True, capture_output=True)
                    status_after = status_after_result.stdout.decode('utf-8', errors='replace') if isinstance(status_after_result.stdout, bytes) else status_after_result.stdout
                    status_after = status_after.strip()
                except subprocess.CalledProcessError:
                    status_after = ''
                try:
                    stash_list_result = subprocess.run(["git", "stash", "list"], cwd=cwd, check=True, capture_output=True)
                    stash_list = stash_list_result.stdout.decode('utf-8', errors='replace') if isinstance(stash_list_result.stdout, bytes) else stash_list_result.stdout
                    stash_list = stash_list.strip()
                except subprocess.CalledProcessError:
                    stash_list = ''

                diagnostics = f"{err3[:800]}\n-- git status --porcelain --before\n{status}\n-- git status --porcelain --after\n{status_after}\n-- git stash list\n{stash_list}"
                logging.error("Autostash/pull failed: %s", diagnostics)
                return False, f"Autostash/pull failed: {err3[:300]} (diagnostics logged)"

            # Fall back to stash/pull/pop
            try:
                subprocess.run(["git", "stash", "push", "-u", "-m", "autostash by bot"], cwd=cwd, check=True, capture_output=True)
                subprocess.run(["git", "pull", "--rebase"], cwd=cwd, check=True, capture_output=True)
                # Attempt to restore stashed changes; if this conflicts, leave stash for manual inspection
                pop_result = subprocess.run(["git", "stash", "pop"], cwd=cwd, capture_output=True)
                if pop_result.returncode != 0:
                    pop_stdout = pop_result.stdout.decode('utf-8', errors='replace') if isinstance(pop_result.stdout, bytes) else pop_result.stdout
                    pop_stderr = pop_result.stderr.decode('utf-8', errors='replace') if isinstance(pop_result.stderr, bytes) else pop_result.stderr
                    return False, f"Pulled, but failed to pop stash: {pop_stdout}\n{pop_stderr}"
                return True, None
            except subprocess.CalledProcessError as e2:
                out2 = (e2.stderr or e2.stdout or b'')
                try:
                    err2 = out2.decode(errors='ignore') if isinstance(out2, (bytes, bytearray)) else str(out2)
                except Exception:
                    err2 = str(out2)
                return False, f"Autostash/pull failed: {err2[:300]}"
        return False, err[:300]


def _get_session(user_id):
    return user_doc_sessions.get(user_id)


def _clear_action(user_id):
    s = user_doc_sessions.get(user_id)
    if not s:
        return
    s.pop('action', None)
    if not s:
        user_doc_sessions.pop(user_id, None)


def get_lfs_lock_info(doc_rel_path: str, cwd: Path = REPO_PATH, repo_type: str = None):
    """Return lock info for a path using modern GitLab API or git lfs locks as fallback. cwd specifies repository root."""
    try:
        # Normalize path - remove leading/trailing slashes and convert backslashes
        normalized_path = doc_rel_path.replace('\\', '/').strip('/')
        logging.info(f"Getting LFS lock info for {normalized_path} in repository {cwd}")
        
        proc = subprocess.run(["git", "lfs", "locks"], cwd=str(cwd), capture_output=True, text=True, encoding='utf-8', errors='replace')
        
        # Log deprecation warning if present
        if proc.stderr and "deprecated" in proc.stderr.lower():
            logging.warning(f"Git LFS locks API deprecation warning: {proc.stderr.strip()}")
            
        out = proc.stdout or ""
        # Parse Git LFS locks output format: "path    owner    ID:id_number"
        for line in out.splitlines():
            if line.strip():
                parts = line.strip().split()
                if len(parts) >= 2:
                    locked_path = parts[0].replace('\\', '/').strip('/')
                    owner_part = parts[1]
                    lock_id = None
                    
                    # Parse lock ID from "ID:6" format
                    if len(parts) > 2:
                        id_part = parts[2]
                        if id_part.startswith('ID:'):
                            lock_id = id_part[3:]  # Extract number after "ID:"
                    
                    # Check if this is the file we're looking for
                    # Match both full path and just filename
                    if (locked_path == normalized_path or 
                        locked_path.endswith('/' + normalized_path) or
                        normalized_path.endswith('/' + locked_path) or
                        locked_path.split('/')[-1] == normalized_path.split('/')[-1]):
                        logging.info(f"Found lock for {normalized_path}: owner={owner_part}, path={locked_path}, id={lock_id}")
                        return {
                            "raw": line.strip(),
                            "path": locked_path,
                            "owner": owner_part,
                            "id": lock_id
                        }
    except subprocess.CalledProcessError as e:
        logging.warning(f"Failed to get LFS lock info via git command: {e}")
        
    # Fallback: try to get lock status through GitLab API if it's a GitLab repo
    if repo_type == REPO_TYPES['GITLAB']:
        try:
            # Try to get user_id from context
            user_id = None
            import inspect
            frame = inspect.currentframe()
            try:
                # Walk up call stack to find message context
                caller_frame = frame.f_back
                while caller_frame:
                    if 'message' in caller_frame.f_locals:
                        message = caller_frame.f_locals['message']
                        if hasattr(message, 'from_user') and hasattr(message.from_user, 'id'):
                            user_id = message.from_user.id
                            break
                    caller_frame = caller_frame.f_back
            finally:
                del frame
            
            return get_lock_info_via_gitlab_api(doc_rel_path, cwd, user_id)
        except Exception as e:
            logging.warning(f"Failed to get lock info via GitLab API: {e}")
    
    return None

def get_current_user_context():
    """Get current user context from active session or message"""
    # This is a placeholder - in practice, you'd get this from the current message context
    # For now, we'll use a global approach or pass user_id as parameter
    import inspect
    frame = inspect.currentframe()
    try:
        # Walk up the call stack to find message context
        while frame:
            if 'message' in frame.f_locals:
                message = frame.f_locals['message']
                if hasattr(message, 'from_user') and hasattr(message.from_user, 'id'):
                    return message.from_user.id
            frame = frame.f_back
    finally:
        del frame
    return None

def get_lock_info_via_gitlab_api(doc_rel_path: str, cwd: Path = REPO_PATH, user_id: int = None):
    """Get lock information via GitLab's /users/locks/activity API endpoint
    If doc_rel_path is None, returns all locks"""
    try:
        # Get user_id from parameter or context
        if not user_id:
            user_id = get_current_user_context()
        
        if not user_id:
            logging.warning("No user context available for GitLab API call")
            return None
        
        # Get repository URL to determine GitLab instance
        remote_result = subprocess.run(["git", "remote", "get-url", "origin"], 
                                     cwd=str(cwd), capture_output=True, text=True)
        if remote_result.returncode != 0:
            return None
            
        repo_url = remote_result.stdout.strip()
        
        # Extract GitLab host and project info
        if repo_url.startswith('https://'):
            # https://gitlab.example.com/group/project.git
            host_and_path = repo_url.replace('https://', '').replace('.git', '')
            parts = host_and_path.split('/', 2)
            if len(parts) >= 3:
                gitlab_host = parts[0]
                project_path = f"{parts[1]}/{parts[2]}"
            else:
                return None
        elif repo_url.startswith('git@'):
            # git@gitlab.example.com:group/project.git
            host_and_path = repo_url.split(':', 1)[1].replace('.git', '')
            gitlab_host = repo_url.split('@')[1].split(':')[0]
            project_path = host_and_path
        else:
            return None
            
        # Look for GitLab credentials
        gitlab_token = None
        credential_files = [
            Path("/app/data") / f".git-credentials-gitlab-{user_id}",
            Path("/app/data") / f".git-credentials-lfs-{user_id}"
        ]
        
        for cred_file in credential_files:
            if cred_file.exists():
                try:
                    content = cred_file.read_text().strip()
                    if 'oauth2:' in content:
                        gitlab_token = content.split('oauth2:')[1].split('@')[0]
                        break
                except Exception:
                    continue
        
        if not gitlab_token:
            # Try to get from git config
            try:
                config_result = subprocess.run(["git", "config", "lfs.gitlabToken"], 
                                             cwd=str(cwd), capture_output=True, text=True)
                if config_result.returncode == 0:
                    gitlab_token = config_result.stdout.strip()
            except Exception:
                pass
        
        if not gitlab_token:
            logging.warning("No GitLab token found for API access")
            return None
        
        # Make API request to /users/locks/activity
        import requests
        api_url = f"https://{gitlab_host}/api/v4/projects/{project_path.replace('/', '%2F')}/users/locks/activity"
        
        headers = {
            'Authorization': f'Bearer {gitlab_token}',
            'Content-Type': 'application/json'
        }
        
        response = requests.get(api_url, headers=headers, timeout=10)
        
        if response.status_code == 200:
            locks_data = response.json()
            
            if doc_rel_path is None:
                # Return all locks
                return locks_data
            else:
                # Find lock for our specific file
                for lock in locks_data:
                    if lock.get('path') == doc_rel_path:
                        return {
                            "path": lock.get('path'),
                            "owner": lock.get('user', {}).get('username', 'unknown'),
                            "id": lock.get('id'),
                            "created_at": lock.get('created_at')
                        }
        else:
            logging.warning(f"GitLab API request failed: {response.status_code} - {response.text}")
            
    except Exception as e:
        logging.warning(f"Error getting lock info via GitLab API: {e}")
    
    return None

class GitLabLFSManager:
    """Manage Git LFS operations for GitLab repositories"""
    
    def __init__(self, api_client: GitLabAPIClient = None):
        self.api_client = api_client
    
    def get_project_id_from_url(self, repo_url: str) -> str:
        """Extract project ID or path from repository URL"""
        try:
            # Handle HTTPS URLs
            if repo_url.startswith('https://'):
                # Remove https:// and .git suffix
                path_part = repo_url.replace('https://', '').replace('.git', '')
                # Split by / and get group/project parts
                parts = path_part.split('/')
                if len(parts) >= 3:  # gitlab.com/group/project
                    return f"{parts[1]}/{parts[2]}"
            
            # Handle SSH URLs
            elif repo_url.startswith('git@'):
                # git@gitlab.com:group/project.git
                path_part = repo_url.split(':')[1].replace('.git', '')
                return path_part
            
            return ""
        except Exception as e:
            logging.error(f"Failed to extract project ID from URL {repo_url}: {e}")
            return ""
    
    def get_lfs_locks_via_api(self, repo_url: str) -> list:
        """Get LFS locks using GitLab API"""
        if not self.api_client:
            return []
        
        try:
            project_id = self.get_project_id_from_url(repo_url)
            if not project_id:
                return []
            
            return self.api_client.get_lfs_locks(project_id)
        except Exception as e:
            logging.error(f"Failed to get LFS locks via API: {e}")
            return []
    
    def create_lfs_lock_via_api(self, repo_url: str, file_path: str) -> dict:
        """Create LFS lock using GitLab API"""
        if not self.api_client:
            return {}
        
        try:
            project_id = self.get_project_id_from_url(repo_url)
            if not project_id:
                return {}
            
            return self.api_client.create_lfs_lock(project_id, file_path)
        except Exception as e:
            logging.error(f"Failed to create LFS lock via API: {e}")
            return {}
    
    def delete_lfs_lock_via_api(self, repo_url: str, lock_id: str) -> bool:
        """Delete LFS lock using GitLab API"""
        if not self.api_client:
            return False
        
        try:
            project_id = self.get_project_id_from_url(repo_url)
            if not project_id:
                return False
            
            return self.api_client.delete_lfs_lock(project_id, lock_id)
        except Exception as e:
            logging.error(f"Failed to delete LFS lock via API: {e}")
            return False
    
    def configure_gitlab_lfs(self, repo_path: str, repo_url: str) -> bool:
        """Configure Git LFS specifically for GitLab repository"""
        try:
            # Initialize Git LFS
            subprocess.run(["git", "lfs", "install"], cwd=str(repo_path), check=True, capture_output=True)
            
            # CRITICAL: Get the actual remote URL from the repository, not the stored one
            # The stored repo_url may be outdated or in wrong format
            actual_remote_url = None
            try:
                result = subprocess.run(["git", "remote", "get-url", "origin"], 
                                      cwd=str(repo_path), capture_output=True, text=True, check=True)
                actual_remote_url = result.stdout.strip()
                logging.info(f"Read actual remote URL from repository: {actual_remote_url}")
            except Exception as e:
                logging.warning(f"Could not read actual remote URL, falling back to stored URL: {e}")
                actual_remote_url = repo_url
            
            # Use actual remote URL for LFS configuration
            logging.info(f"Configuring Git LFS for repository URL: {actual_remote_url}")
            
            # For self-hosted GitLab, configure LFS properly based on repo URL type
            if actual_remote_url.startswith('git@'):
                # SSH: For SSH repos, use SSH for LFS too to avoid authentication issues
                # git@gitlab.example.com:group/project.git -> ssh://git@gitlab.example.com/group/project.git
                import re
                ssh_match = re.match(r'git@([^:]+):(.+?)(?:\.git)?/?$', actual_remote_url)
                if ssh_match:
                    gitlab_host = ssh_match.group(1)
                    project_path = ssh_match.group(2)
                    # Use SSH protocol for LFS - avoids HTTPS auth issues
                    ssh_lfs_url = f"ssh://git@{gitlab_host}/{project_path}.git"
                    subprocess.run(["git", "config", "lfs.url", ssh_lfs_url], cwd=str(repo_path), check=True, capture_output=True)
                    
                    # Configure SSH command for Git LFS to use the correct SSH key
                    # Extract user_id from repo_path if possible
                    try:
                        user_id_match = re.search(r'/user_repos/(\d+)/?', repo_path)
                        if user_id_match:
                            user_id = user_id_match.group(1)
                            ssh_key_path = f"/app/data/ssh_keys/{user_id}/id_ed25519"
                            subprocess.run(["git", "config", "core.sshCommand", f"ssh -i {ssh_key_path} -o StrictHostKeyChecking=no"], 
                                          cwd=str(repo_path), capture_output=True)
                            logging.info(f"Configured SSH key for Git LFS: {ssh_key_path}")
                    except Exception as ssh_config_error:
                        logging.warning(f"Failed to configure SSH key for LFS: {ssh_config_error}")
                    
                    logging.info(f"Configured LFS URL for SSH repository: {ssh_lfs_url}")
                else:
                    logging.warning(f"Could not parse SSH URL: {actual_remote_url}")
                    return False
                    
            elif actual_remote_url.startswith('https://'):
                # HTTPS: Use HTTPS for LFS and ensure credential helper is configured
                import re
                https_match = re.match(r'https://([^/]+)/(.+?)(?:\.git)?/?$', actual_remote_url)
                if https_match:
                    gitlab_host = https_match.group(1)
                    project_path = https_match.group(2)
                    https_lfs_url = f"https://{gitlab_host}/{project_path}.git"
                    subprocess.run(["git", "config", "lfs.url", https_lfs_url], cwd=str(repo_path), check=True, capture_output=True)
                    
                    # For HTTPS, ensure credential helper is configured for LFS operations
                    # Use store helper which reads from ~/.git-credentials or configured credential file
                    subprocess.run(["git", "config", "credential.helper", "store"], cwd=str(repo_path), check=True, capture_output=True)
                    
                    logging.info(f"Configured LFS URL for HTTPS repository: {https_lfs_url}")
                else:
                    logging.warning(f"Could not parse HTTPS URL: {actual_remote_url}")
                    return False
            else:
                logging.warning(f"Unsupported repository URL format: {actual_remote_url}")
                return False
            
            logging.info(f"GitLab LFS configured for repository: {repo_path}")
            return True
            
        except Exception as e:
            logging.error(f"Failed to configure GitLab LFS: {e}")
            return False
    
    def sync_lfs_objects(self, repo_path: str) -> bool:
        """Sync LFS objects with GitLab"""
        try:
            # Fetch LFS objects
            subprocess.run(["git", "lfs", "fetch", "--all"], cwd=str(repo_path), check=True, capture_output=True)
            
            # Push LFS objects
            subprocess.run(["git", "lfs", "push", "origin", "HEAD"], cwd=str(repo_path), check=True, capture_output=True)
            
            logging.info(f"LFS objects synced for repository: {repo_path}")
            return True
            
        except Exception as e:
            logging.error(f"Failed to sync LFS objects: {e}")
            return False

# OPTIMIZATION: Removed 2 unused/deprecated functions:
# 1. get_gitlab_project_info() - replaced by GitLabAPIClient.get_project_info()
# 2. initialize_gitlab_lfs() - replaced by GitLabLFSManager.configure_gitlab_lfs()
# These were legacy functions with redundant functionality. Removed ~110 lines.

# Initialize stub bot since we're using python-telegram-bot as the main library
AIORGRAM_AVAILABLE = False


# Define stub bot classes for compatibility
class _StubBot:
    def __init__(self, token=None):
        self.token = token

    async def send_message(self, chat_id, text, **kwargs):
        # Stub: include repo header in logged message for test/runtime visibility
        logging.info("Stub send_message to %s: %s", chat_id, str(text))
        return None

    async def send_document(self, *args, **kwargs):
        return None

class _StubDispatcher:
    def message(self, *args, **kwargs):
        def decorator(f):
            return f
        return decorator

bot = _StubBot(token=TOKEN)
dp = _StubDispatcher()

# Global variable to track per-user selection and intent
# user_doc_sessions[user_id] = { 'doc': 'name.docx', 'action': 'download' }
user_doc_sessions = {}

# Simple per-user config state (used for setup flow when not using aiogram FSM)
user_config_state = {}
user_config_data = {}


class PTBMessageAdapter:
    """Adapter to present a minimal 'message' interface expected by existing handlers.
    Wraps a python-telegram-bot Update and Context to provide .from_user, .chat, .text, .document and async answer/send_document methods."""
    def __init__(self, update, context):
        self.update = update
        self.context = context
        # Create from_user object with id, username, and first_name
        effective_user = update.effective_user if update.effective_user else None
        self.from_user = type('U', (), {
            'id': effective_user.id if effective_user else None,
            'username': effective_user.username if effective_user else None,
            'first_name': effective_user.first_name if effective_user else None
        })
        self.chat = type('C', (), {'id': update.effective_chat.id if update.effective_chat else None})
        self.text = update.message.text if update.message and update.message.text else None
        self.document = update.message.document if update.message and update.message.document else None

    async def answer(self, text, **kwargs):
        # Convert reply_markup if needed (function get_main_keyboard returns PTB markup when available)
        reply = kwargs.get('reply_markup')
        # Send message without automatic repo header
        await self.context.bot.send_message(chat_id=self.chat.id, text=str(text), reply_markup=reply)

    async def send_document(self, document, caption=None):
        # document can be a path string or PTB InputFile
        if isinstance(document, str):
            await self.context.bot.send_document(chat_id=self.chat.id, document=PTBInputFile(open(document, 'rb')) , caption=caption)
        else:
            await self.context.bot.send_document(chat_id=self.chat.id, document=document, caption=caption)

# Minimal states representation for compatibility with earlier handlers
class UserConfigStates:
    waiting_for_repo_url = 'waiting_for_repo_url'
    waiting_for_username = 'waiting_for_username'
    waiting_for_password = 'waiting_for_password'

# Create keyboard
def get_main_keyboard(user_id=None):
    """Главное меню - улучшенная структура с логической группировкой"""
    # Check if user is admin
    is_admin = False
    if user_id is not None:
        try:
            is_admin = str(user_id) in ADMIN_IDS
        except Exception:
            is_admin = False
    
    if is_admin:
        # Admin view - grouped by functionality
        keyboard = [
            ["📂 Документы"],  # Document operations
            ["🔧 Git операции", "🔒 Блокировки"],  # Git operations with admin functions
            ["ℹ️ О репозитории", "⚙️ Настройки"],  # Repository info and settings
            ["📖 Инструкции"]  # Help section
        ]
    else:
        # Regular user view - simplified and focused
        keyboard = [
            ["📂 Документы"],  # Main document operations
            ["🔧 Git операции"],  # Git operations without admin functions
            ["ℹ️ О репозитории"],  # Repository info with setup option
            ["📖 Инструкции"]  # Help section
        ]

    if PTB_AVAILABLE:
        return PTBReplyKeyboardMarkup(keyboard=keyboard, resize_keyboard=True)

    # For fallback, flatten the keyboard
    fallback_keyboard = []
    for row in keyboard:
        if isinstance(row, list):
            fallback_keyboard.append(row)
        else:
            fallback_keyboard.append([row])
    return fallback_keyboard

def get_docs_keyboard(docs, locks=None):
    """Меню списка документов"""
    if locks is None:
        locks = {}
    
    keyboard = []
    for doc in docs:
        # Check if document is locked
        if doc in locks:
            # Document is locked
            keyboard.append([f"📄🔒 {doc}"])
        else:
            # Document is not locked
            keyboard.append([f"📄 {doc}"])
    
    keyboard.append(["◀️ Назад в главное меню"])
    
    if PTB_AVAILABLE:
        return PTBReplyKeyboardMarkup(keyboard=keyboard, resize_keyboard=True)
    return keyboard

def get_document_keyboard(doc_name, is_locked=False, can_unlock=False, current_user_id=None, repo_root=None):
    """Меню работы с конкретным документом
    
    Args:
        doc_name: Name of the document
        is_locked: Whether document is locked
        can_unlock: Whether current user can unlock the document
        current_user_id: Current user's Telegram ID (for upload permission check)
        repo_root: Repository root path (for lock verification)
    """
    # Check if current user can upload (is lock owner)
    can_upload = False
    if is_locked and current_user_id and repo_root:
        # Check if user is the lock owner via Git LFS
        try:
            rel_path = str((Path('docs') / doc_name).as_posix())
            lfs_lock_info = get_lfs_lock_info(rel_path, cwd=repo_root)
            if lfs_lock_info:
                # Get user's GitHub username
                user_repo_info = get_user_repo(current_user_id)
                user_github_username = user_repo_info.get('git_username') if user_repo_info else None
                
                # Check if LFS lock owner matches user's GitHub username
                lfs_owner = lfs_lock_info.get('owner')
                if (lfs_owner == str(current_user_id) or 
                    lfs_owner == user_github_username or
                    (user_github_username and lfs_owner.lower() == user_github_username.lower())):
                    can_upload = True
        except Exception:
            pass
    
    if PTB_AVAILABLE:
        # Build keyboard with conditional upload button
        keyboard = [["📥 Скачать"]]
        
        # Add upload button only if user can upload or document is not locked
        if not is_locked or can_upload:
            keyboard[0].append("📤 Загрузить изменения")
        
        if is_locked:
            if can_unlock:
                keyboard.insert(1, ["🔓 Разблокировать"])
        else:
            keyboard.insert(1, ["🔒 Заблокировать"])
        keyboard.append(["◀️ Назад к документам"])
        return PTBReplyKeyboardMarkup(keyboard=keyboard, resize_keyboard=True)
    
    # Fallback structure
    keyboard = [["📥 Скачать"]]
    if not is_locked or can_upload:
        keyboard[0].append("📤 Загрузить изменения")
    keyboard.append(["🧾 Статус документа"])
    
    if is_locked:
        if can_unlock:
            keyboard.insert(1, ["🔓 Разблокировать"])
    else:
        keyboard.insert(1, ["🔒 Заблокировать"])
    keyboard.append(["◀️ Назад к документам"])
    return keyboard

def get_git_operations_keyboard(user_id=None):
    """Меню Git операций"""
    is_admin = False
    if user_id is not None:
        try:
            is_admin = str(user_id) in ADMIN_IDS
        except Exception:
            is_admin = False  # Default to non-admin if there's an error
    
    keyboard = [
        ["🔄 Обновить репозиторий", "🧾 Git статус"]
    ]
    
    # Add admin-only operations
    if is_admin:
        keyboard.extend([
            ["🔧 Исправить LFS проблемы"],
            ["🔄 Пересинхронизировать репозиторий"]
        ])
    
    keyboard.append(["◀️ Назад в главное меню"])
    
    if PTB_AVAILABLE:
        return PTBReplyKeyboardMarkup(keyboard=keyboard, resize_keyboard=True)
    
    # For fallback, flatten the keyboard
    fallback_keyboard = []
    for row in keyboard:
        if isinstance(row, list):
            fallback_keyboard.append(row)
        else:
            fallback_keyboard.append([row])
    return fallback_keyboard

def get_locks_keyboard(user_id=None):
    """Меню блокировок"""
    is_admin = False
    if user_id is not None:
        try:
            is_admin = str(user_id) in ADMIN_IDS
        except Exception:
            is_admin = False  # Default to non-admin if there's an error
    
    keyboard = []
    
    # Add admin-only operation
    if is_admin:
        keyboard.append(["🔒 Статус всех блокировок"])
    
    keyboard.append(["◀️ Назад в главное меню"])
    
    if PTB_AVAILABLE:
        return PTBReplyKeyboardMarkup(keyboard=keyboard, resize_keyboard=True)
    
    # For fallback, flatten the keyboard
    fallback_keyboard = []
    for row in keyboard:
        if isinstance(row, list):
            fallback_keyboard.append(row)
        else:
            fallback_keyboard.append([row])
    return fallback_keyboard

def get_settings_keyboard(user_id=None):
    """Меню настроек"""
    # Check if user already has a configured repository
    has_repo = False
    if user_id:
        user_repo = get_user_repo(user_id)
        has_repo = user_repo is not None

    # Check if user is admin
    is_admin = False
    if user_id is not None:
        try:
            is_admin = str(user_id) in ADMIN_IDS
        except Exception:
            is_admin = False
    
    keyboard_buttons = []

    # Only show repository setup if no repository is configured OR if user_id is None (backward compatibility)
    if not has_repo or user_id is None:
        keyboard_buttons.append("🔧 Настроить репозиторий")
    
    # Admin functions
    if is_admin:
        keyboard_buttons.append("👥 Управление пользователями")

    keyboard_buttons.append("◀️ Назад в главное меню")

    if PTB_AVAILABLE:
        keyboard = [[btn] for btn in keyboard_buttons]
        return PTBReplyKeyboardMarkup(keyboard=keyboard, resize_keyboard=True)
    return [keyboard_buttons]

async def start(message, state=None):
    await state.clear()
    await message.answer(
        "🤖 Добро пожаловать в систему управления документами!\n\n"
        "Для начала работы настройте репозиторий и учетные данные.",
        reply_markup=get_main_keyboard(message.from_user.id)
    )
    # Log user interaction
    user_name = format_user_name(message)
    timestamp = format_datetime()
    log_message = f"🔄 Пользователь {user_name} запустил бота [{timestamp}]"
    await log_to_group(message, log_message)

# OPTIMIZATION: Removed 4 unused FSM handlers (setup_repo, process_repo_url, process_username, process_password)
# These were legacy aiogram FSM handlers that are no longer used.
# The bot now uses setup_repository_simple() and handle_repo_action_simple() for repository configuration.
# Removed lines saved ~270 lines of code.

async def list_documents(message):
    repo_root = await require_user_repo(message)
    if not repo_root:
        return

    # Pull latest changes from the repository
    try:
        subprocess.run(["git", "fetch"], cwd=str(repo_root), check=True, capture_output=True)
        subprocess.run(["git", "pull"], cwd=str(repo_root), check=True, capture_output=True)
    except subprocess.CalledProcessError:
        # If pull fails, continue anyway as there might be local files
        pass

    # Search for .docx files in the entire repository, not just /docs directory
    docs = list(repo_root.rglob("*.docx"))
    
    # Filter out files in .git directory and other hidden/system directories
    docs = [doc for doc in docs 
            if not any(part.startswith('.') for part in doc.parts) 
            and '.git' not in doc.parts
            and '__pycache__' not in doc.parts
            and 'node_modules' not in doc.parts]
    if not docs:
        await message.answer("📂 В репозитории нет документов .docx", reply_markup=get_main_keyboard(message.from_user.id))
        return
    
    doc_names = [f.name for f in docs]
    
    # Get Git LFS locks to show lock icons for locked documents
    git_lfs_locks = {}
    try:
        user_repo_path = get_repo_for_user_id(message.from_user.id)
        if user_repo_path and user_repo_path.exists():
            # Debug: log repository info
            user_repo_info = get_user_repo(message.from_user.id)
            repo_url = user_repo_info.get('repo_url', 'unknown') if user_repo_info else 'unknown'
            logging.info(f"User {message.from_user.id} checking locks for repo: {repo_url} at {user_repo_path}")
            
            # Check remote repository URL to ensure all users use the same repo
            remote_url = None
            try:
                remote_result = subprocess.run(["git", "remote", "get-url", "origin"], cwd=str(user_repo_path), capture_output=True, text=True, encoding='utf-8', errors='replace')
                if remote_result.returncode == 0:
                    remote_url = remote_result.stdout.strip()
                    logging.info(f"User {message.from_user.id} remote URL: {remote_url}")
                else:
                    logging.warning(f"User {message.from_user.id} failed to get remote URL: {remote_result.stderr}")
            except Exception as e:
                logging.error(f"Error checking remote URL for user {message.from_user.id}: {e}")
            
            # CRITICAL: Reconfigure Git LFS before attempting to get locks
            # This ensures LFS is properly configured with the correct protocol URL
            if remote_url:
                try:
                    lfs_manager = GitLabLFSManager()
                    lfs_manager.configure_gitlab_lfs(str(user_repo_path), remote_url)
                    logging.info(f"Reconfigured LFS for user {message.from_user.id} before getting locks")
                except Exception as e:
                    logging.error(f"Failed to reconfigure LFS for user {message.from_user.id}: {e}")
            
            # Get LFS locks - credentials stored globally
            proc = subprocess.run(["git", "lfs", "locks"], cwd=str(user_repo_path), capture_output=True, text=True, encoding='utf-8', errors='replace')
            logging.info(f"LFS locks command result for user {message.from_user.id}: returncode={proc.returncode}, stdout={proc.stdout[:200]}, stderr={proc.stderr[:200] if proc.stderr else 'none'}")
            
            # If locks command fails, log it but continue (may be SSH auth issue)
            if proc.returncode != 0:
                logging.warning(f"Failed to get LFS locks for user {message.from_user.id}: {proc.stderr[:500]}")
                # Don't fail completely - just proceed without lock info
                proc.stdout = ""
            
            if proc.returncode == 0 and proc.stdout.strip():
                for line in proc.stdout.splitlines():
                    if line.strip():
                        parts = line.strip().split()
                        if len(parts) >= 3:
                            path = parts[0]
                            owner = parts[1]
                            lock_id = parts[2] if len(parts) > 2 else None
                            # Store both full path and filename as keys for flexibility
                            git_lfs_locks[path] = {"owner": owner, "id": lock_id, "path": path}
                            # Also store by filename alone for compatibility
                            filename = path.split("/")[-1] if "/" in path else path
                            if filename not in git_lfs_locks:
                                git_lfs_locks[filename] = {"owner": owner, "id": lock_id, "path": path}
                            logging.info(f"Found lock: {path} (filename: {filename}) locked by {owner}")
    except Exception as e:
        logging.error(f"Error getting LFS locks for user {message.from_user.id}: {e}")
    
    keyboard = get_docs_keyboard(doc_names, locks=git_lfs_locks)
    await message.answer("Выберите документ:", reply_markup=keyboard)

async def handle_doc_selection(message):
    doc_text = message.text.strip()
    
    # Remove prefix - could be "📄 " (unlocked) or "📄🔒 " (locked)
    if doc_text.startswith("📄🔒 "):
        doc_name = doc_text[len("📄🔒 "):].strip()  # Remove "📄🔒 " prefix
    elif doc_text.startswith("📄 "):
        doc_name = doc_text[len("📄 "):].strip()  # Remove "📄 " prefix
    else:
        # Fallback: just take the text as is if it doesn't match expected format
        doc_name = doc_text
    
    # Normalize document name to handle potential encoding issues
    doc_name = doc_name.strip()
    
    # Set selected document in user's session
    user_doc_sessions[message.from_user.id] = {'doc': doc_name}
    repo_root = get_repo_for_user_id(message.from_user.id)
    
    # Search for document in entire repository (not just docs/ directory)
    doc_path = None
    for file_path in repo_root.rglob(doc_name):
        # Check if it's a .docx file and not in hidden/system directories
        if (file_path.suffix.lower() == '.docx' and 
            not any(part.startswith('.') for part in file_path.parts) and
            '.git' not in file_path.parts and
            '__pycache__' not in file_path.parts and
            'node_modules' not in file_path.parts):
            doc_path = file_path
            break
    
    if not doc_path or not doc_path.exists():
        # Document doesn't exist - return to document list
        logging.warning(f"Document not found: {doc_name} in repository {repo_root}")
        await list_documents(message)
        return
    
    # Check if file is locked via Git LFS
    # Use relative path from repository root
    rel_path = str(doc_path.relative_to(repo_root)).replace('\\', '/')
    try:
        lfs_lock_info = get_lfs_lock_info(rel_path, cwd=repo_root)
        is_locked = lfs_lock_info is not None
    except Exception as e:
        logging.warning(f"Failed to get LFS lock info for {doc_name}: {e}")
        is_locked = False
    
    if is_locked:
        # Determine if current user can unlock (is owner or admin)
        try:
            lfs_owner = lfs_lock_info.get('owner', '')
            # Check if user is lock owner (by Telegram ID or GitHub username)
            user_repo_info = get_user_repo(message.from_user.id)
            user_github_username = user_repo_info.get('git_username') if user_repo_info else None
            
            is_lock_owner = (
                lfs_owner == str(message.from_user.id) or
                lfs_owner == user_github_username or
                (user_github_username and lfs_owner.lower() == user_github_username.lower())
            )
            can_unlock = is_lock_owner or (str(message.from_user.id) in ADMIN_IDS)
        except Exception:
            can_unlock = False
            
        reply_markup = get_document_keyboard(doc_name, is_locked=True, can_unlock=can_unlock, 
                                           current_user_id=message.from_user.id, repo_root=repo_root)

        # Load user repos to find Telegram username
        user_repos = load_user_repos()
        
        # Get actual lock timestamp (current time since Git LFS doesn't provide real timestamp)
        lock_timestamp = format_datetime()
        
        # Get lock owner's Telegram username from user_repos
        lock_owner_id = lfs_lock_info.get('owner', 'unknown')
        telegram_username = None
        
        # Try to find user by GitHub username in our user mapping
        for user_id, repo_data in user_repos.items():
            if repo_data.get('git_username') == lock_owner_id:
                # Found user with matching GitHub username, get their Telegram username
                telegram_username = repo_data.get('telegram_username')
                if telegram_username and not telegram_username.startswith('@'):
                    telegram_username = f"@{telegram_username}"
                break
        
        # Format lock owner display
        if telegram_username:
            owner_display = f"[ {telegram_username} ](https://t.me/{telegram_username.lstrip('@')})"
        else:
            # Fallback to GitHub profile link
            owner_display = f"[ {lock_owner_id} ](https://github.com/{lock_owner_id})"
        
        message_text = (
            f"📄 {doc_name}\n"
            f"🔒 Заблокирован через Git LFS:\n"
            f"👤 Пользователь: {owner_display}\n"
            f"🕐 Время блокировки: {lock_timestamp}\n"
            "\n"
            "Вы можете скачать документ, но не сможете загрузить изменения, пока он заблокирован."
        )
        await message.answer(message_text, reply_markup=reply_markup)
    else:
        reply_markup = get_document_keyboard(doc_name, is_locked=False)
        await message.answer(
            f"📄 {doc_name}\n"
            f"🔓 Не заблокирован\n\n"
            "Вы можете скачать документ, внести изменения и загрузить обновленную версию.",
            reply_markup=reply_markup
        )

async def download_document(message):
    # Ensure repository configured for this user
    repo_root = await require_user_repo(message)
    if not repo_root:
        return

    # Prefer selected document
    session = user_doc_sessions.get(message.from_user.id)
    if session and session.get('doc'):
        doc_name = session['doc']
        
        # Search for document in entire repository
        doc_path = None
        for file_path in repo_root.rglob(doc_name):
            if (file_path.suffix.lower() == '.docx' and 
                not any(part.startswith('.') for part in file_path.parts) and
                '.git' not in file_path.parts and
                '__pycache__' not in file_path.parts and
                'node_modules' not in file_path.parts):
                doc_path = file_path
                break
                
        if not doc_path or not doc_path.exists():
            await message.answer(f"❌ Документ {doc_name} не найден!", reply_markup=get_main_keyboard())
            return
        # Prefer message-level send_document (PTBMessageAdapter) which uses context.bot when available
        try:
            if hasattr(message, 'send_document'):
                await message.send_document(str(doc_path), caption=f"Документ {doc_name}")
            else:
                # Fallback to global bot (legacy) behaviour
                if PTB_AVAILABLE:
                    await bot.send_document(chat_id=message.chat.id, document=PTBInputFile(open(str(doc_path), 'rb')), caption=f"Документ {doc_name}")
                else:
                    await bot.send_document(chat_id=message.chat.id, document=str(doc_path), caption=f"Документ {doc_name}")
        except Exception as e:
            logging.exception("Failed to send document %s: %s", doc_name, e)
            await message.answer(f"❌ Не удалось отправить документ: {str(e)[:200]}", reply_markup=get_main_keyboard())
        # Return to document menu after download
        # Check if document is locked via Git LFS
        rel_path = str((Path('docs') / doc_name).as_posix())
        try:
            lfs_lock_info = get_lfs_lock_info(rel_path, cwd=repo_root)
            is_locked = lfs_lock_info is not None
        except Exception as e:
            logging.warning(f"Failed to get LFS lock info for {doc_name}: {e}")
            is_locked = False
        
        # Check if user can unlock (is owner or admin)
        can_unlock = False
        if is_locked and lfs_lock_info:
            try:
                lfs_owner = lfs_lock_info.get('owner', '')
                # Check if user is lock owner (by Telegram ID or GitHub username)
                user_repo_info = get_user_repo(message.from_user.id)
                user_github_username = user_repo_info.get('git_username') if user_repo_info else None
                
                is_lock_owner = (
                    lfs_owner == str(message.from_user.id) or
                    lfs_owner == user_github_username or
                    (user_github_username and lfs_owner.lower() == user_github_username.lower())
                )
                can_unlock = is_lock_owner or (str(message.from_user.id) in ADMIN_IDS)
            except Exception:
                can_unlock = False
        reply_markup = get_document_keyboard(doc_name, is_locked=is_locked, can_unlock=can_unlock,
                                           current_user_id=message.from_user.id, repo_root=repo_root)
        await message.answer("✅ Документ отправлен!", reply_markup=reply_markup)
        # Log document download
        user_name = format_user_name(message)
        timestamp = format_datetime()
        log_message = f"📥 Пользователь {user_name} скачал документ: {doc_name} [{timestamp}]"
        await log_to_group(message, log_message)
        return

    # Fallback: ask for name
    user_doc_sessions[message.from_user.id] = {'action': 'download'}
    await message.answer("Введите имя документа для скачивания (например, document.docx):")

async def upload_changes(message):
    # Ensure user repo configured
    repo_root = await require_user_repo(message)
    if not repo_root:
        return

    session = user_doc_sessions.get(message.from_user.id)
    if session and session.get('doc'):
        session['action'] = 'upload'
        await message.answer(f"Отправьте обновленный файл .docx для документа `{session['doc']}`.")
        return

    await message.answer("Пожалуйста, выберите документ (📋 Документы) и затем нажмите 'Загрузить изменения'.")

async def handle_doc_name_input(message):
    user_id = message.from_user.id
    doc_name = message.text.strip()
    session = user_doc_sessions.get(user_id)
    intent = session.get('action') if session else None
    # Handle fallback actions when user typed a filename instead of selecting
    if intent == 'download':
        _clear_action(user_id)
        # ensure user repo configured
        repo_root = await require_user_repo(type('M', (), {'from_user': type('U', (), {'id': user_id}), 'answer': message.answer}))
        if not repo_root:
            return
        # Search for document in entire repository
        doc_path = None
        for file_path in repo_root.rglob(doc_name):
            if (file_path.suffix.lower() == '.docx' and 
                not any(part.startswith('.') for part in file_path.parts) and
                '.git' not in file_path.parts and
                '__pycache__' not in file_path.parts and
                'node_modules' not in file_path.parts):
                doc_path = file_path
                break
                
        if not doc_path or not doc_path.exists():
            await message.answer(f"❌ Документ {doc_name} не найден!", reply_markup=get_main_keyboard())
            return
        try:
            if hasattr(message, 'send_document'):
                await message.send_document(str(doc_path), caption=f"Документ {doc_name}")
            else:
                if PTB_AVAILABLE:
                    await bot.send_document(chat_id=message.chat.id, document=PTBInputFile(open(str(doc_path), 'rb')), caption=f"Документ {doc_name}")
                else:
                    await bot.send_document(chat_id=message.chat.id, document=str(doc_path), caption=f"Документ {doc_name}")
        except Exception as e:
            logging.exception("Failed to send document by name %s: %s", doc_name, e)
            try:
                await message.answer(f"❌ Не удалось отправить документ: {str(e)[:200]}", reply_markup=get_main_keyboard())
            except Exception:
                pass
        await message.answer("✅ Документ отправлен!", reply_markup=get_main_keyboard())
        return


    # No pending action: treat as selecting a document by name (compatibility)
    user_doc_sessions[user_id] = {'doc': doc_name}
    await message.answer(f"Выбран документ: {doc_name}. Используйте соответствующие кнопки для работы с ним.", reply_markup=get_main_keyboard())

async def handle_document_upload(message):
    # SECURITY: Rate limiting
    if not check_rate_limit(message.from_user.id):
        await message.answer("❌ Слишком частые действия. Подождите немного.")
        return

    # Ensure user repo configured
    repo_root = await require_user_repo(message)
    if not repo_root:
        return

    # First check if this is actually a document upload
    logging.info(f"=== DEBUG MESSAGE STRUCTURE ===")
    logging.info(f"Message type: {type(message)}")
    logging.info(f"Has document attr: {hasattr(message, 'document')}")
    logging.info(f"Document object: {getattr(message, 'document', None)}")
    logging.info(f"Has caption attr: {hasattr(message, 'caption')}")
    logging.info(f"Caption value: {getattr(message, 'caption', 'NOT_FOUND')}")
    logging.info(f"Has text attr: {hasattr(message, 'text')}")
    logging.info(f"Text value: {getattr(message, 'text', 'NOT_FOUND')}")
    logging.info(f"All message attrs: {dir(message)}")
    if hasattr(message, 'document') and message.document:
        logging.info(f"Document has caption: {hasattr(message.document, 'caption')}")
        logging.info(f"Document caption: {getattr(message.document, 'caption', 'NOT_FOUND')}")
        logging.info(f"Document attrs: {dir(message.document)}")
    
    # Check update object
    if hasattr(message, 'update'):
        logging.info(f"Update type: {type(message.update)}")
        logging.info(f"Update has caption: {hasattr(message.update, 'caption')}")
        logging.info(f"Update caption: {getattr(message.update, 'caption', 'NOT_FOUND')}")
        if hasattr(message.update, 'to_dict'):
            update_dict = message.update.to_dict()
            logging.info(f"Update dict sample: {str(update_dict)[:500]}")  # Больше данных
            logging.info(f"Update dict has caption key: {'caption' in update_dict}")
            if 'caption' in update_dict:
                logging.info(f"Update dict caption: {repr(update_dict['caption'])}")
            
            # Check message object inside update
            if 'message' in update_dict:
                msg_dict = update_dict['message']
                logging.info(f"Message dict keys: {list(msg_dict.keys())}")
                logging.info(f"Message dict sample: {str(msg_dict)[:300]}")
                if 'caption' in msg_dict:
                    logging.info(f"FOUND CAPTION IN MESSAGE DICT: {repr(msg_dict['caption'])}")
                    caption = msg_dict['caption']  # Найден caption!
    
    logging.info(f"=== END DEBUG ===")
    
    if not hasattr(message, 'document') or not message.document:
        # Check if this might be a text message sent after "Upload changes"
        if hasattr(message, 'text') and message.text:
            # Store text as pending caption for next document upload
            session = user_doc_sessions.get(message.from_user.id, {})
            if session.get('action') == 'upload':
                session['pending_caption'] = message.text.strip()
                user_doc_sessions[message.from_user.id] = session
                logging.info(f"Stored pending caption for next upload: {repr(message.text)}")
                await message.answer(
                    f"✅ Сохранен комментарий: '{message.text}'\n\n"
                    "Теперь отправьте **файл .docx** (не текст!), и этот комментарий будет использован для коммита."
                )
                return
            else:
                await message.answer(
                    f"❌ Вы отправили текстовое сообщение: '{message.text}'\n\n"
                    "❗ Нужно отправить именно **файл .docx**, а не текст!\n\n"
                    "📥 Правильный порядок:\n"
                    "1. Нажмите скрепку/прикрепить файл\n"
                    "2. Выберите файл .docx из файловой системы\n"
                    "3. Добавьте описание (caption) к файлу\n"
                    "4. Отправьте файл"
                )
        else:
            await message.answer(
                "❌ Пожалуйста, отправьте файл .docx, а не текстовое сообщение!\n\n"
                "📥 Нажмите скрепку -> выберите файл .docx -> добавьте описание (caption) -> отправьте"
            )
        return

    # Check for mandatory commit message (caption/description)
    # Try multiple sources for caption
    caption = None
        
    # Source 1: Check for pending caption from previous text message
    session = user_doc_sessions.get(message.from_user.id, {})
    if session.get('action') == 'upload' and 'pending_caption' in session:
        caption = session['pending_caption']
        logging.info(f"Using pending caption from session: {repr(caption)}")
        # Clear pending caption
        del session['pending_caption']
        user_doc_sessions[message.from_user.id] = session
        
    # Source 2: Message caption (main source)
    if not caption:
        caption = getattr(message, 'caption', None)
        logging.info(f"Message caption: {repr(caption)}")
        
        # Additional check: maybe caption is in update or context?
        if not caption and hasattr(message, 'update'):
            update_caption = getattr(message.update, 'caption', None)
            logging.info(f"Update caption: {repr(update_caption)}")
            if update_caption:
                caption = update_caption
        
        # Check if caption might be in message entities or other fields
        if not caption:
            # Try to get raw update data
            if hasattr(message, 'update') and hasattr(message.update, 'to_dict'):
                update_dict = message.update.to_dict()
                logging.info(f"Update dict keys: {list(update_dict.keys())}")
                if 'caption' in update_dict:
                    caption = update_dict['caption']
                    logging.info(f"Found caption in update dict: {repr(caption)}")
                
                # Check message object inside update (most likely place)
                if 'message' in update_dict and 'caption' in update_dict['message']:
                    caption = update_dict['message']['caption']
                    logging.info(f"FOUND AND SET CAPTION FROM MESSAGE DICT: {repr(caption)}")
        
    # Source 3: Document caption (alternative source)
    if not caption and hasattr(message.document, 'caption'):
        doc_caption = getattr(message.document, 'caption', None)
        logging.info(f"Document caption: {repr(doc_caption)}")
        if doc_caption and doc_caption.strip():
            caption = doc_caption
        
    logging.info(f"Final caption value: {repr(caption)}")
    
    if not caption or not caption.strip():
        await message.answer(
            "❌ Обязательно укажите комментарий к изменениям!\n\n"
            "📝 В Telegram Desktop: при отправке файла кликните на файл -> выберите 'Add a description' -> введите комментарий\n"
            "📱 В мобильном Telegram: при отправке файла введите текст в поле описания под файлом\n\n"
            "Этот комментарий будет записан в историю коммитов для сохранения истории изменений."
        )
        return

    if not message.document or not message.document.file_name:
        await message.answer("❌ Файл не найден.")
        return

    # SECURITY: Double-check file extension (case-insensitive)
    if not message.document.file_name.lower().endswith('.docx'):
        await message.answer("❌ Пожалуйста, отправьте файл с расширением .docx")
        return

    # SECURITY: Check file size (limit to 50MB to prevent DoS)
    if hasattr(message.document, 'file_size') and message.document.file_size > 50 * 1024 * 1024:
        await message.answer("❌ Файл слишком большой (максимум 50 МБ).")
        return
    
    uploaded_file_name = message.document.file_name

    # SECURITY: Sanitize filename to prevent path traversal and injection
    if not uploaded_file_name:
        await message.answer("❌ Пустое имя файла.")
        return

    # Check for path traversal attempts
    if '..' in uploaded_file_name or '/' in uploaded_file_name or '\\' in uploaded_file_name:
        await message.answer("❌ Недопустимое имя файла. Используйте только буквы, цифры, пробелы и точку.")
        return

    # Check for suspicious characters that could be used for injection
    if re.search(r'[;&|`$(){}[\]<>\'"\\]', uploaded_file_name):
        await message.answer("❌ Недопустимые символы в имени файла.")
        return

    # Limit filename length
    if len(uploaded_file_name) > 255:
        await message.answer("❌ Слишком длинное имя файла (максимум 255 символов).")
        return

    doc_name = uploaded_file_name
    
    # Check if user has a selected document in session (from "Загрузить изменения")
    session = user_doc_sessions.get(message.from_user.id)
    if session and session.get('action') == 'upload' and session.get('doc'):
        expected_doc_name = session['doc']
        # Verify that uploaded file name matches the selected document name
        if uploaded_file_name != expected_doc_name:
            await message.answer(
                f"❌ Ошибка: имя загружаемого файла не совпадает с выбранным документом!\n\n"
                f"📄 Выбранный документ: `{expected_doc_name}`\n"
                f"📤 Загружаемый файл: `{uploaded_file_name}`\n\n"
                f"Пожалуйста, переименуйте файл в `{expected_doc_name}` и попробуйте снова.",
                reply_markup=get_document_keyboard(expected_doc_name, is_locked=False)
            )
            return
        # Use the expected document name
        doc_name = expected_doc_name
    
    # Search for document in entire repository or create in docs/ if uploading new
    doc_path = None
    for file_path in repo_root.rglob(doc_name):
        if (file_path.suffix.lower() == '.docx' and 
            not any(part.startswith('.') for part in file_path.parts) and
            '.git' not in file_path.parts and
            '__pycache__' not in file_path.parts and
            'node_modules' not in file_path.parts):
            doc_path = file_path
            break
    
    # If document not found, create path in docs/ directory for new uploads
    if not doc_path:
        docs_dir = repo_root / "docs"
        docs_dir.mkdir(parents=True, exist_ok=True)
        doc_path = docs_dir / doc_name
    
    # Check LFS lock status (Git LFS is now the only lock mechanism)
    # Use relative path from repository root
    rel_path = str(doc_path.relative_to(repo_root)).replace('\\', '/')
    lfs_lock_info = get_lfs_lock_info(rel_path, cwd=repo_root)
    
    # Check if locked by another user
    lfs_locked_by_other = False
    if lfs_lock_info:
        # Get user's GitHub username for ownership check
        user_repo_info = get_user_repo(message.from_user.id)
        user_github_username = user_repo_info.get('git_username') if user_repo_info else None
        
        lfs_lock_owner = lfs_lock_info.get('owner', '')
        
        # Check if current user owns the lock (either by Telegram ID or GitHub username)
        is_lock_owner = (
            lfs_lock_owner == str(message.from_user.id) or
            lfs_lock_owner == user_github_username or
            (user_github_username and lfs_lock_owner.lower() == user_github_username.lower())
        )
        
        if not is_lock_owner:
            lfs_locked_by_other = True
    
    # Check Git LFS lock first (this is the authoritative source)
    if lfs_lock_info:
        # There's an active Git LFS lock - check ownership
        lfs_lock_owner = lfs_lock_info.get('owner')
        
        # Get user's mapped GitHub username using composite key
        user_repo_info = get_user_repo(message.from_user.id)
        user_github_username = user_repo_info.get('git_username') if user_repo_info else None
        
        # Check if current user owns the lock (either by Telegram ID or GitHub username)
        is_lock_owner = (
            lfs_lock_owner == str(message.from_user.id) or  # Direct ID match
            lfs_lock_owner == user_github_username or       # GitHub username match
            (user_github_username and lfs_lock_owner.lower() == user_github_username.lower())  # Case-insensitive username match
        )
        
        lfs_locked_by_other = not is_lock_owner
    else:
        # No Git LFS lock exists - document is available for locking
        # Local locks are ignored when no Git LFS lock exists
        pass
    
    # Only check Git LFS locks (local locks removed)
    if lfs_locked_by_other:
        lock_owner = lfs_lock_info.get('owner', 'unknown')
        lock_timestamp = format_datetime()
        
        # Load user repos to find Telegram username
        user_repos = load_user_repos()
        
        # Get Telegram username for lock owner
        telegram_username = None
        for user_id, repo_data in user_repos.items():
            if repo_info.get('git_username') == lock_owner:
                telegram_username = repo_info.get('telegram_username')
                if telegram_username and not telegram_username.startswith('@'):
                    telegram_username = f"@{telegram_username}"
                break
        
        # Format lock owner display
        if telegram_username:
            owner_display = f"[ {telegram_username} ](https://t.me/{telegram_username.lstrip('@')})"
        else:
            # Fallback to GitHub profile link
            owner_display = f"[ {lock_owner} ](https://github.com/{lock_owner})"
        
        # Show error but return to document menu
        error_msg = f"❌ Документ {doc_name} заблокирован другим пользователем\n"
        error_msg += f"👤 Владелец: {owner_display}\n"
        error_msg += f"🕐 Время блокировки: {lock_timestamp}\n\n"
        
        # Get user info for better error message
        user_repo_info = get_user_repo(message.from_user.id)
        user_github_username = user_repo_info.get('git_username') if user_repo_info else None
        
        if lfs_locked_by_other:
            if user_github_username and lock_owner.lower() == user_github_username.lower():
                error_msg += "⚠️ Конфликт идентификации: Ваш GitHub аккаунт совпадает с владельцем блокировки, но система не смогла установить связь. "
            else:
                error_msg += "Документ заблокирован через Git LFS. "
        elif local_locked_by_other:
            error_msg += "Обнаружена локальная блокировка. "
        
        error_msg += "Обратитесь к владельцу блокировки для разблокировки или убедитесь, что ваш GitHub аккаунт правильно связан с Telegram."
        await message.answer(error_msg, reply_markup=get_document_keyboard(doc_name, is_locked=True, can_unlock=False))
        return
    
    # Download and save the document
    # calculate old hash and size if exists
    old_hash = None
    old_size = None
    if doc_path.exists():
        try:
            import hashlib
            with open(doc_path, 'rb') as f:
                data = f.read()
                old_hash = hashlib.sha256(data).hexdigest()
                old_size = len(data)
        except Exception:
            old_hash = None
            old_size = None

    # SECURITY: Ensure the target directory exists and is writable
    doc_path.parent.mkdir(parents=True, exist_ok=True)

    # Download the document using the available client implementation.
    try:
        # Prefer PTB context bot (has async get_file/download_to_drive)
        if hasattr(message, 'context') and hasattr(message.context, 'bot'):
            file = await message.context.bot.get_file(message.document.file_id)
            # async download helper in PTB v20+: download_to_drive
            if hasattr(file, 'download_to_drive'):
                await file.download_to_drive(custom_path=str(doc_path))
            elif hasattr(file, 'download'):
                # fallback to sync download method
                file.download(str(doc_path))
            else:
                raise RuntimeError('No compatible download method on File object')
        # Fallback to global bot (legacy/aiogram) if it supports download
        elif hasattr(bot, 'download'):
            await bot.download(message.document.file_id, destination=str(doc_path))
        # Try message.document.get_file() if available (some adapters)
        elif hasattr(message.document, 'get_file'):
            f = message.document.get_file()
            if hasattr(f, 'download_to_drive'):
                await f.download_to_drive(custom_path=str(doc_path))
            elif hasattr(f, 'download'):
                f.download(str(doc_path))
            else:
                raise RuntimeError('No compatible download method on file from message')
        else:
            raise RuntimeError('No method available to download the document')

        # SECURITY: Verify file was actually downloaded and has reasonable size
        if not doc_path.exists() or doc_path.stat().st_size == 0:
            await message.answer("❌ Ошибка при сохранении файла.", reply_markup=get_document_keyboard(doc_name, is_locked=False))
            return

        # SECURITY: Double-check file size after download
        actual_size = doc_path.stat().st_size
        if actual_size > 50 * 1024 * 1024:
            doc_path.unlink()  # Remove the file
            await message.answer("❌ Файл слишком большой после загрузки.", reply_markup=get_document_keyboard(doc_name, is_locked=False))
            return

        await message.answer(f"✅ Документ {doc_name} обновлен!")
    except Exception as e:
        await message.answer(f"❌ Не удалось загрузить файл: {str(e)[:200]}", reply_markup=get_document_keyboard(doc_name, is_locked=False))
        return

    # calculate new hash and size
    new_hash = None
    new_size = None
    try:
        import hashlib
        with open(doc_path, 'rb') as f:
            data = f.read()
            new_hash = hashlib.sha256(data).hexdigest()
            new_size = len(data)
    except Exception:
        pass
    
    # Configure git user if not already set, then commit and push changes
    try:
        # Set git config if not already set - use user's credentials
        try:
            subprocess.run(["git", "config", "--get", "user.name"], cwd=str(repo_root), check=True, capture_output=True)
        except subprocess.CalledProcessError:
            # Get username from user repo config
            user_info = get_user_repo(message.from_user.id)
            if user_info and user_info.get('git_username'):
                subprocess.run(["git", "config", "user.name", user_info['git_username']], cwd=str(repo_root), check=True, capture_output=True)
            else:
                subprocess.run(["git", "config", "user.name", str(message.from_user.id)], cwd=str(repo_root), check=True, capture_output=True)
        
        try:
            subprocess.run(["git", "config", "--get", "user.email"], cwd=str(repo_root), check=True, capture_output=True)
        except subprocess.CalledProcessError:
            # Get username from user repo config for email
            user_info = get_user_repo(message.from_user.id)
            if user_info and user_info.get('git_username'):
                email = f"{user_info['git_username']}@users.noreply.github.com"
                subprocess.run(["git", "config", "user.email", email], cwd=str(repo_root), check=True, capture_output=True)
            else:
                subprocess.run(["git", "config", "user.email", f"user-{message.from_user.id}@gitdocs.local"], cwd=str(repo_root), check=True, capture_output=True)

        # Pull latest changes first to avoid non-fast-forward error. Use autostash/fallback.
        # Allow auto-committing the specific doc we just uploaded if it's the only unstaged change.
        rel_path = str(doc_path.relative_to(repo_root))
        ok, err = git_pull_rebase_autostash(str(repo_root), auto_commit_paths=[rel_path])
        if not ok:
            await message.answer(f"❌ Ошибка при обновлении репозитория перед коммитом: {err}", reply_markup=get_document_keyboard(doc_name, is_locked=False))
            return
        
        # Stage the file
        try:
            subprocess.run(["git", "add", str(doc_path.relative_to(repo_root))], cwd=str(repo_root), check=True, capture_output=True, text=True, encoding='utf-8', errors='replace')
        except subprocess.CalledProcessError as e:
            err_msg = (e.stderr or e.stdout or '').strip()
            if isinstance(err_msg, bytes):
                err_msg = err_msg.decode('utf-8', errors='replace')
            logging.error(f"git add failed for {doc_name}: {err_msg}")
            await message.answer(f"❌ Ошибка при добавлении файла в git: {err_msg[:200] if err_msg else 'Неизвестная ошибка'}", reply_markup=get_document_keyboard(doc_name, is_locked=False))
            return
        
        # Check if there are changes to commit
        status_result = subprocess.run(["git", "status", "--porcelain"], cwd=str(repo_root), capture_output=True, text=True, encoding='utf-8', errors='replace')
        has_changes = bool(status_result.stdout.strip())
        
        # Commit changes only if there are staged changes
        commit_created = False
        if has_changes:
            user_name = format_user_name(message)
            # Use enhanced commit message format with user info and timestamp
            if caption:
                # Enhanced format with user info and t.me link
                telegram_username = getattr(message.from_user, 'username', None)
                if telegram_username:
                    user_link = f"[{telegram_username}](https://t.me/{telegram_username})"
                else:
                    user_link = f"User {message.from_user.id}"
                timestamp = format_datetime()  # Already includes +3h offset
                
                commit_message = (
                    f"{caption.strip()}\n\n"
                    f"Кто изменил: {user_link}\n"
                    f"Дата/Время изменения: {timestamp}"
                )
            else:
                commit_message = f"Update {doc_name} by {user_name}"
            commit_result = subprocess.run(["git", "commit", "-m", commit_message], 
                          cwd=str(repo_root), capture_output=True, text=True, encoding='utf-8', errors='replace')
            if commit_result.returncode == 0:
                commit_created = True
            else:
                # Check if it's just "nothing to commit" (not a real error)
                output = (commit_result.stdout + commit_result.stderr).lower()
                if 'nothing to commit' in output or 'working tree clean' in output:
                    # File was already committed or unchanged - this is OK
                    commit_created = False
                    logging.info(f"No changes to commit for {doc_name} - file may be unchanged")
                else:
                    # Real error
                    err_msg = (commit_result.stderr or commit_result.stdout or '').strip()
                    logging.error(f"git commit failed for {doc_name}: {err_msg}")
                    await message.answer(f"❌ Ошибка при создании коммита: {err_msg[:200] if err_msg else 'Неизвестная ошибка'}", reply_markup=get_document_keyboard(doc_name, is_locked=False))
                    return
        else:
            logging.info(f"No staged changes for {doc_name} - skipping commit")
        
        # Push to remote only if commit was created
        if commit_created:
            # Check if file is locked by LFS and unlock it temporarily if needed
            # Use relative path from repository root
            rel_path = str(doc_path.relative_to(repo_root)).replace('\\', '/')
            lfs_lock_info = get_lfs_lock_info(rel_path, cwd=repo_root)
            
            # If file is locked by current user, unlock it temporarily for push
            if lfs_lock_info and lfs_lock_info.get('owner') == str(message.from_user.id):
                try:
                    # Use only filename to avoid protocol issues
                    temp_filename = Path(rel_path).name
                    subprocess.run(["git", "lfs", "unlock", temp_filename], cwd=str(repo_root), check=True, capture_output=True)
                    logging.info(f"Temporarily unlocked {doc_name} for push")
                except subprocess.CalledProcessError:
                    # If unlock fails, continue anyway - might not be critical
                    pass
            
            # Push LFS objects first (only current branch)
            try:
                lfs_push_result = subprocess.run(["git", "lfs", "push", "origin", "HEAD"],
                                               cwd=str(repo_root), capture_output=True, text=True)
                if lfs_push_result.returncode != 0:
                    logging.warning(f"LFS push failed: {lfs_push_result.stderr}")
            except subprocess.CalledProcessError as lfs_err:
                logging.warning(f"LFS push error: {lfs_err}")

            # Then push commits
            try:
                subprocess.run(["git", "push"], cwd=str(repo_root), check=True, capture_output=True, text=True, encoding='utf-8', errors='replace')
                
                # Re-lock the file after successful push if it was unlocked
                if lfs_lock_info and lfs_lock_info.get('owner') == str(message.from_user.id):
                    try:
                        # Use only filename to avoid protocol issues
                        temp_filename = Path(rel_path).name
                        subprocess.run(["git", "lfs", "lock", temp_filename], cwd=str(repo_root), check=True, capture_output=True)
                        logging.info(f"Re-locked {doc_name} after push")
                    except subprocess.CalledProcessError:
                        # If re-lock fails, continue - file will remain unlocked
                        pass
                        
            except subprocess.CalledProcessError as e:
                err_msg = (e.stderr or e.stdout or '').strip()
                if isinstance(err_msg, bytes):
                    err_msg = err_msg.decode('utf-8', errors='replace')
                logging.error(f"git push failed for {doc_name}: {err_msg}")
                await message.answer(f"❌ Ошибка при отправке в удаленный репозиторий: {err_msg[:300] if err_msg else 'Неизвестная ошибка'}\n\nВозможные причины:\n• Нет доступа к репозиторию\n• Требуется обновление токена доступа\n• Конфликт с удаленными изменениями", reply_markup=get_document_keyboard(doc_name, is_locked=False))
                return
        
        # Prepare summary
        commit = None
        if commit_created:
            try:
                commit = subprocess.run(["git", "rev-parse", "HEAD"], cwd=str(repo_root), check=True, capture_output=True, text=True).stdout.strip()
            except Exception:
                commit = None

        if commit_created:
            summary = f"🚀 Документ {doc_name} успешно отправлен в репозиторий!"
            if commit:
                summary += f"\n• Commit: `{commit}`"
        else:
            summary = f"✅ Документ {doc_name} сохранен локально.\n\nℹ️ Изменений для коммита не обнаружено (файл не изменился или уже был закоммичен)."
        
        if old_hash or new_hash:
            summary += f"\n• Old SHA256: `{old_hash}` size={old_size if old_size else 'unknown'}`\n• New SHA256: `{new_hash}` size={new_size if new_size else 'unknown'}`"
        # Add unlock suggestion (user may choose to unlock explicitly)
        summary += "\n\nЕсли хотите, нажмите \"🔓 Разблокировать\" чтобы снять блокировку (если есть)."

        # Return to document menu after upload
        # doc_name is already set correctly (either from session or from uploaded file name)
        # Check if document is locked via Git LFS
        # Use relative path from repository root
        rel_path = str(doc_path.relative_to(repo_root)).replace('\\', '/')
        try:
            lfs_lock_info = get_lfs_lock_info(rel_path, cwd=repo_root)
            is_locked = lfs_lock_info is not None
        except Exception as e:
            logging.warning(f"Failed to get LFS lock info for {doc_name}: {e}")
            is_locked = False
        
        # Check if user can unlock (is owner or admin)
        can_unlock = False
        if is_locked and lfs_lock_info:
            try:
                lfs_owner = lfs_lock_info.get('owner', '')
                # Check if user is lock owner (by Telegram ID or GitHub username)
                user_repo_info = get_user_repo(message.from_user.id)
                user_github_username = user_repo_info.get('git_username') if user_repo_info else None
                
                is_lock_owner = (
                    lfs_owner == str(message.from_user.id) or
                    lfs_owner == user_github_username or
                    (user_github_username and lfs_owner.lower() == user_github_username.lower())
                )
                can_unlock = is_lock_owner or (str(message.from_user.id) in ADMIN_IDS)
            except Exception:
                can_unlock = False
        reply_markup = get_document_keyboard(doc_name, is_locked=is_locked, can_unlock=can_unlock,
                                           current_user_id=message.from_user.id, repo_root=repo_root)
        await message.answer(summary, reply_markup=reply_markup)
        
        # Log document upload
        user_name = format_user_name(message)
        timestamp = format_datetime()
        log_message = f"📤 Пользователь {user_name} загрузил документ: {doc_name} [{timestamp}]"
        await log_to_group(message, log_message)

        # Clear upload action but keep document selected in session
        session = user_doc_sessions.get(message.from_user.id)
        if session:
            session.pop('action', None)
            # Ensure doc_name is set in session
            session['doc'] = doc_name
        else:
            user_doc_sessions[message.from_user.id] = {'doc': doc_name}

        # Auto-unlock after upload if configured
        if AUTO_UNLOCK_ON_UPLOAD:
            try:
                # call unlock flow (owner should be uploader)
                await unlock_document_by_name(message, doc_name)
            except Exception:
                # don't fail on auto-unlock errors
                pass
    except subprocess.CalledProcessError as e:
        # This should not be reached if we handle errors above, but keep as fallback
        err_msg = ''
        try:
            if e.stderr:
                err_msg = e.stderr.decode('utf-8', errors='replace') if isinstance(e.stderr, bytes) else str(e.stderr)
            elif e.stdout:
                err_msg = e.stdout.decode('utf-8', errors='replace') if isinstance(e.stdout, bytes) else str(e.stdout)
            else:
                err_msg = str(e)
        except Exception:
            err_msg = str(e)
        logging.exception(f"Unexpected subprocess error during upload of {doc_name}")
        # SECURITY: Don't expose internal error details to users
        await message.answer(f"❌ Ошибка при отправке в репозиторий. Попробуйте позже.", reply_markup=get_document_keyboard(doc_name, is_locked=False) if 'doc_name' in locals() else get_main_keyboard())
    except Exception as e:
        logging.exception(f"Unexpected error during upload: {e}")
        # SECURITY: Don't expose internal error details to users
        await message.answer(f"❌ Неожиданная ошибка при отправке в репозиторий. Попробуйте позже.", reply_markup=get_document_keyboard(doc_name, is_locked=False) if 'doc_name' in locals() else get_main_keyboard())

async def lock_document(message):
    repo_root = await require_user_repo(message)
    if not repo_root:
        return

    # Prefer selected document
    session = user_doc_sessions.get(message.from_user.id)
    if session and session.get('doc'):
        await lock_document_by_name(message, session['doc'])
        return
    # Ask user to select a document first
    await message.answer("Пожалуйста, выберите документ из списка (📋 Документы), затем нажмите 'Заблокировать'.")


async def unlock_document(message):
    repo_root = await require_user_repo(message)
    if not repo_root:
        return

    session = user_doc_sessions.get(message.from_user.id)
    if session and session.get('doc'):
        await unlock_document_by_name(message, session['doc'])
        return
    await message.answer("Пожалуйста, выберите документ из списка (📋 Документы), затем нажмите 'Разблокировать'.")


async def unlock_document_by_name(message, doc_name: str):
    repo_root = get_repo_for_user_id(message.from_user.id)
    
    # Search for document in entire repository
    doc_path = None
    for file_path in repo_root.rglob(doc_name):
        if (file_path.suffix.lower() == '.docx' and 
            not any(part.startswith('.') for part in file_path.parts) and
            '.git' not in file_path.parts and
            '__pycache__' not in file_path.parts and
            'node_modules' not in file_path.parts):
            doc_path = file_path
            break
    
    if not doc_path or not doc_path.exists():
        await message.answer(f"❌ Документ {doc_name} не найден!", reply_markup=get_document_keyboard(doc_name, is_locked=False))
        return

    # Ensure Git LFS is properly configured for this repository
    try:
        user_repo = get_user_repo(message.from_user.id)
        if user_repo:
            repo_url = user_repo.get('repo_url')
            if repo_url:
                # For HTTPS repositories, ensure credentials are set up for LFS
                if repo_url.startswith('https://'):
                    setup_gitlab_lfs_credentials(str(repo_root), repo_url, message.from_user.id)
                
                # Re-configure LFS to ensure it's using the correct protocol-specific URL
                lfs_manager = GitLabLFSManager()
                lfs_manager.configure_gitlab_lfs(str(repo_root), repo_url)
                logging.info(f"Re-configured LFS for {repo_root} with URL {repo_url}")
    except Exception as e:
        logging.warning(f"Failed to ensure LFS configuration: {e}")

    # Check if document is locked via Git LFS
    # Use relative path from repository root
    rel = str(doc_path.relative_to(repo_root)).replace('\\', '/')
    try:
        lfs_lock_info = get_lfs_lock_info(rel, cwd=repo_root)
        is_locked = lfs_lock_info is not None
    except Exception as e:
        logging.warning(f"Failed to get LFS lock info for {doc_name}: {e}")
        is_locked = False
    
    if not is_locked:
        await message.answer(f"ℹ️ Документ {doc_name} не заблокирован через Git LFS.", reply_markup=get_document_keyboard(doc_name, is_locked=False))
        return

    # Check if user is allowed to unlock (is owner or admin)
    is_allowed_to_unlock = False
    try:
        lfs_owner = lfs_lock_info.get('owner', '')
        # Check if user is lock owner (by Telegram ID or GitHub username)
        user_repo_info = get_user_repo(message.from_user.id)
        user_github_username = user_repo_info.get('git_username') if user_repo_info else None
        
        is_lock_owner = (
            lfs_owner == str(message.from_user.id) or
            lfs_owner == user_github_username or
            (user_github_username and lfs_owner.lower() == user_github_username.lower())
        )
        is_allowed_to_unlock = is_lock_owner or (str(message.from_user.id) in ADMIN_IDS)
    except Exception:
        pass  # Default to not being allowed if there's an error
    
    if not is_allowed_to_unlock:
        await message.answer(f"❌ У вас нет прав для разблокировки документа {doc_name} (владелец {lfs_lock_info.get('owner', 'unknown')}).", reply_markup=get_document_keyboard(doc_name, is_locked=True, can_unlock=False))
        return
    # Try to unlock via git-lfs using lock ID for better reliability
    # Use relative path to ensure consistency with what git lfs locks returns
    rel = str(doc_path.relative_to(repo_root)).replace('\\', '/')
    logging.info(f"Attempting to unlock document for user {message.from_user.id}: rel_path={rel}, lock_id={lfs_lock_info.get('id', 'unknown')}")
    try:
        # Use lock ID for unlock instead of path, since git lfs unlock requires exact match
        # with how the lock was originally created
        lock_id = lfs_lock_info.get('id')
        if lock_id:
            # Unlock using lock ID (more reliable)
            proc = subprocess.run(["git", "lfs", "unlock", "--id", str(lock_id)], cwd=str(repo_root), check=True, capture_output=True, text=True, encoding='utf-8', errors='replace')
        else:
            # Fallback: try using just the filename (how git lfs locks stores it)
            filename_only = doc_path.name
            proc = subprocess.run(["git", "lfs", "unlock", filename_only], cwd=str(repo_root), check=True, capture_output=True, text=True, encoding='utf-8', errors='replace')
        
        # Return to document menu
        reply_markup = get_document_keyboard(doc_name, is_locked=False)
        await message.answer(f"🔓 Документ {doc_name} успешно разблокирован через git-lfs!", reply_markup=reply_markup)
        
        # Log document unlock
        user_name = format_user_name(message)
        timestamp = format_datetime()
        log_message = f"🔓 Пользователь {user_name} разблокировал документ: {doc_name} [{timestamp}]"
        await log_to_group(message, log_message)
    except subprocess.CalledProcessError as e:
        err_raw = (e.stderr or e.stdout or '')
        try:
            err = err_raw.decode() if isinstance(err_raw, (bytes, bytearray)) else str(err_raw)
        except Exception:
            err = str(err_raw)
        err = err.strip()
        # If unlocking failed due to uncommitted changes, attempt to auto-commit pointer and retry once
        if 'uncommitted' in err.lower() or 'cannot unlock file with uncommitted changes' in err.lower():
            try:
                # Add and commit the file to clear uncommitted changes that block unlock
                subprocess.run(["git", "add", rel], cwd=str(repo_root), check=True, capture_output=True)
                subprocess.run(["git", "commit", "-m", f"Auto-commit for unlock {doc_name}"], cwd=str(repo_root), check=True, capture_output=True)
                # Retry unlock using lock ID or filename
                lock_id = lfs_lock_info.get('id')
                if lock_id:
                    proc2 = subprocess.run(["git", "lfs", "unlock", "--id", str(lock_id)], cwd=str(repo_root), check=True, capture_output=True, text=True, encoding='utf-8', errors='replace')
                else:
                    filename_only = doc_path.name
                    proc2 = subprocess.run(["git", "lfs", "unlock", filename_only], cwd=str(repo_root), check=True, capture_output=True, text=True, encoding='utf-8', errors='replace')
                
                # Return to document menu
                reply_markup = get_document_keyboard(doc_name, is_locked=False)
                await message.answer(f"🔓 Документ {doc_name} разблокирован после авто-коммита", reply_markup=reply_markup)
                return
            except subprocess.CalledProcessError as e2:
                # Report error
                err2 = (e2.stderr or e2.stdout or '').strip()
                # Return to document menu
                reply_markup = get_document_keyboard(doc_name, is_locked=True)
                await message.answer(f"⚠️ Ошибка при автокоммите/разблокировке: {err2[:200]}", reply_markup=reply_markup)
                return
        # Check for SSH authentication errors
        if 'exit status 255' in err or 'Permission denied' in err or 'ssh' in err.lower():
            logging.warning(f"SSH error during unlock: {err[:200]}")
            await message.answer(f"⚠️ Ошибка SSH: не удалось подключиться к серверу Git LFS. Проверьте SSH ключи и доступ в сети.", reply_markup=get_document_keyboard(doc_name, is_locked=True))
            return
        # Other errors: report error
        # Return to document menu
        reply_markup = get_document_keyboard(doc_name, is_locked=True)
        await message.answer(f"⚠️ Ошибка при разблокировке: {err[:200]}", reply_markup=reply_markup)

async def lock_document_by_name(message, doc_name: str):
    repo_root = get_repo_for_user_id(message.from_user.id)
    
    # Search for document in entire repository
    doc_path = None
    for file_path in repo_root.rglob(doc_name):
        if (file_path.suffix.lower() == '.docx' and 
            not any(part.startswith('.') for part in file_path.parts) and
            '.git' not in file_path.parts and
            '__pycache__' not in file_path.parts and
            'node_modules' not in file_path.parts):
            doc_path = file_path
            break
    
    if not doc_path or not doc_path.exists():
        await message.answer(f"❌ Документ {doc_name} не найден!", reply_markup=get_document_keyboard(doc_name, is_locked=False))
        return
    
    # Ensure Git LFS is properly configured for this repository
    try:
        user_repo = get_user_repo(message.from_user.id)
        if user_repo:
            repo_url = user_repo.get('repo_url')
            if repo_url:
                # For HTTPS repositories, ensure credentials are set up for LFS
                if repo_url.startswith('https://'):
                    setup_gitlab_lfs_credentials(str(repo_root), repo_url, message.from_user.id)
                
                # Re-configure LFS to ensure it's using the correct protocol-specific URL
                lfs_manager = GitLabLFSManager()
                lfs_manager.configure_gitlab_lfs(str(repo_root), repo_url)
                logging.info(f"Re-configured LFS for {repo_root} with URL {repo_url}")
    except Exception as e:
        logging.warning(f"Failed to ensure LFS configuration: {e}")
    
    # Check if already locked via Git LFS
    # Use relative path from repository root
    rel = str(doc_path.relative_to(repo_root)).replace('\\', '/')
    try:
        lfs_lock_info = get_lfs_lock_info(rel, cwd=repo_root)
        if lfs_lock_info:
            lock_owner = lfs_lock_info.get('owner', 'unknown')
            lock_timestamp = format_datetime()
            
            # Load user repos to find Telegram username
            user_repos = load_user_repos()
            
            # Get Telegram username for lock owner
            telegram_username = None
            for user_id, repo_data in user_repos.items():
                if repo_data.get('git_username') == lock_owner:
                    telegram_username = repo_data.get('telegram_username')
                    if telegram_username and not telegram_username.startswith('@'):
                        telegram_username = f"@{telegram_username}"
                    break
            
            # Format lock owner display
            if telegram_username:
                owner_display = f"[ {telegram_username} ](https://t.me/{telegram_username.lstrip('@')})"
            else:
                # Fallback to GitHub profile link
                owner_display = f"[ {lock_owner} ](https://github.com/{lock_owner})"
            
            message_text = (
                f"❌ Документ {doc_name} уже заблокирован через Git LFS\n\n"
                f"👤 Владелец: {owner_display}\n"
                f"🕐 Время блокировки: {lock_timestamp}"
            )
            await message.answer(message_text, reply_markup=get_document_keyboard(doc_name, is_locked=True, can_unlock=False))
            return
    except Exception as e:
        logging.warning(f"Failed to check LFS lock status for {doc_name}: {e}")
    
    # Create lock
    # Try to lock via git-lfs first (so others see it)
    # Use relative path to ensure consistency with what git lfs locks returns
    rel = str(doc_path.relative_to(repo_root)).replace('\\', '/')
    logging.info(f"Attempting to lock document for user {message.from_user.id}: rel_path={rel}")
    try:
        # Use relative path instead of just filename for proper SSH support
        proc = subprocess.run(["git", "lfs", "lock", rel], cwd=str(repo_root), check=True, capture_output=True, text=True, encoding='utf-8', errors='replace')
        # Git LFS lock created successfully - no local lock needed
        # Return to document menu
        reply_markup = get_document_keyboard(doc_name, is_locked=True, can_unlock=True)
        await message.answer(f"🔒 Документ {doc_name} успешно заблокирован через git-lfs!\n{proc.stdout.strip()}", reply_markup=reply_markup)
        
        # Log document lock
        user_name = format_user_name(message)
        timestamp = format_datetime()
        log_message = f"🔒 Пользователь {user_name} заблокировал документ: {doc_name} [{timestamp}]"
        await log_to_group(message, log_message)
    except subprocess.CalledProcessError as e:
        # If git-lfs locking fails, check if it's already locked
        err_raw = e.stderr or e.stdout or b''
        try:
            if isinstance(err_raw, (bytes, bytearray)):
                err = err_raw.decode('utf-8', errors='replace').strip()
            else:
                err = str(err_raw).strip()
        except Exception:
            err = str(err_raw).strip()
        
        logging.warning(f"Failed to lock document {doc_name}: {err}")
        
        # Check if error is "already locked"
        if "already locked" in err.lower():
            logging.info(f"Document {doc_name} is already locked: {err}")
            # Try to get lock info to show who locked it
            try:
                lfs_lock_info = get_lfs_lock_info(rel, cwd=repo_root)
                if lfs_lock_info:
                    lock_owner = lfs_lock_info.get('owner', 'unknown')
                    lock_timestamp = format_datetime()
                    
                    # Check if current user locked it
                    user_repo = get_user_repo(message.from_user.id)
                    current_git_username = user_repo.get('git_username') if user_repo else None
                    can_unlock = (current_git_username == lock_owner)
                    
                    # Load user repos to find Telegram username
                    user_repos = load_user_repos()
                    
                    # Get Telegram username for lock owner
                    telegram_username = None
                    for user_id, repo_data in user_repos.items():
                        if repo_data.get('git_username') == lock_owner:
                            telegram_username = repo_data.get('telegram_username')
                            if telegram_username and not telegram_username.startswith('@'):
                                telegram_username = f"@{telegram_username}"
                            break
                    
                    # Format lock owner display
                    if telegram_username:
                        owner_display = f"[ {telegram_username} ](https://t.me/{telegram_username.lstrip('@')})"
                    else:
                        owner_display = lock_owner
                    
                    message_text = (
                        f"❌ Документ {doc_name} уже заблокирован через Git LFS\n\n"
                        f"👤 Владелец: {owner_display}\n"
                        f"🕐 Время блокировки: {lock_timestamp}"
                    )
                    await message.answer(message_text, reply_markup=get_document_keyboard(doc_name, is_locked=True, can_unlock=can_unlock))
                    return
            except Exception as lock_check_error:
                logging.warning(f"Failed to get lock info for already locked document: {lock_check_error}")
        
        # Check if it's an SSH authentication error
        if "exit status 255" in err or "ssh" in err.lower():
            await message.answer(
                f"⚠️ Ошибка SSH аутентификации при блокировке документа: {err[:200]}\n\n"
                f"Это может быть временной проблемой с подключением. Попробуйте позже.",
                reply_markup=get_document_keyboard(doc_name, is_locked=False)
            )
            return
        
        # Git LFS is required - no local fallback
        await message.answer(f"❌ Не удалось заблокировать через git-lfs: {err[:200]}.")

async def check_lock_status(message):
    # Only admins can view all locks; regular users can only see their own locks
    is_admin = False
    try:
        user_id = str(message.from_user.id)
        is_admin = user_id in ADMIN_IDS
    except Exception:
        logging.warning(f"Admin check failed for user {getattr(message.from_user, 'id', 'unknown')}")
    
    if not is_admin:
        await message.answer("❌ Только администраторы могут просматривать статус всех блокировок.", reply_markup=get_main_keyboard(user_id=message.from_user.id))
        return
        
    # Try to get lock status using modern approach
    try:
        repo_root = await require_user_repo(message)
        if not repo_root:
            return
            
        # Try to get lock status using modern GitLab API first
        repo_type = detect_repository_type(str(repo_root))
        if repo_type == REPO_TYPES['GITLAB']:
            # Try GitLab API approach first
            try:
                lock_status = get_lock_info_via_gitlab_api(None, repo_root)  # None means get all locks
                if lock_status:
                    # Format the output nicely
                    formatted_output = ""
                    for lock in lock_status:
                        path = lock.get('path', 'unknown')
                        owner = lock.get('user', {}).get('username', 'unknown')
                        timestamp = lock.get('created_at', '')
                        formatted_output += f"📄 {path}\n   👤 {owner}\n   🕐 {timestamp}\n\n"
                    
                    await message.answer(f"🔒 Активные блокировки:\n\n{formatted_output}", reply_markup=get_locks_keyboard(user_id=message.from_user.id))
                    return
            except Exception as e:
                logging.warning(f"Failed to get locks via GitLab API: {e}")
        
        # Fallback to git-lfs locks command (may show deprecation warning)
        proc = subprocess.run(["git", "lfs", "locks"], cwd=str(repo_root), capture_output=True, text=True, encoding='utf-8', errors='replace')
        
        # Log deprecation warning if present
        if proc.stderr and "deprecated" in proc.stderr.lower():
            logging.info(f"Git LFS API deprecation notice: {proc.stderr.strip()}")
        
        out = (proc.stdout or "").strip()
        if not out:
            await message.answer("🔓 Нет активных блокировок", reply_markup=get_locks_keyboard(user_id=message.from_user.id))
            return
            
        # Format the output nicely
        formatted_output = ""
        for line in out.splitlines():
            if line.strip():
                parts = line.strip().split()
                if len(parts) >= 2:
                    path = parts[0]
                    owner = parts[1]
                    timestamp = parts[2] if len(parts) > 2 else ""
                    formatted_output += f"📄 {path}\n   👤 {owner}\n   🕐 {timestamp}\n\n"
                else:
                    formatted_output += f"{line}\n"
        
        await message.answer(f"🔒 Активные блокировки:\n\n{formatted_output}", reply_markup=get_locks_keyboard(user_id=message.from_user.id))
        
        # Log lock status check
        user_name = format_user_name(message)
        timestamp = format_datetime()
        log_message = f"🔒 Администратор {user_name} проверил статус всех блокировок [{timestamp}]"
        await log_to_group(message, log_message)
        
    except subprocess.CalledProcessError as e:
        error_msg = str(e)
        if e.stderr:
            error_msg = e.stderr.decode('utf-8', errors='replace') if isinstance(e.stderr, bytes) else str(e.stderr)
        
        # If it's the deprecation error, provide helpful message
        if "deprecated" in error_msg.lower() or "endpoint" in error_msg.lower():
            await message.answer(
                "⚠️ API блокировок устарел. Статус блокировок может отображаться некорректно.\n"
                "Для получения актуальной информации обратитесь к администратору GitLab.", 
                reply_markup=get_locks_keyboard(user_id=message.from_user.id)
            )
        else:
            await message.answer(f"❌ Ошибка получения статуса блокировок: {error_msg[:200]}", reply_markup=get_locks_keyboard(user_id=message.from_user.id))


async def update_repository(message):
    repo_root = await require_user_repo(message)
    if not repo_root:
        return

    try:
        # First check repository status
        status_result = subprocess.run(["git", "status", "--porcelain"], cwd=str(repo_root), capture_output=True)
        status_output = status_result.stdout.decode('utf-8', errors='replace') if isinstance(status_result.stdout, bytes) else status_result.stdout
        has_changes = bool(status_output.strip())

        # Try to fetch first
        fetch_result = subprocess.run(["git", "fetch"], cwd=str(repo_root), capture_output=True, text=True)
        if fetch_result.returncode != 0:
            error_msg = f"❌ Ошибка при получении обновлений с сервера:\n{fetch_result.stderr[:200]}"
            await message.answer(error_msg, reply_markup=get_git_operations_keyboard())
            return

        # Check and fix default branch configuration
        try:
            # First, ensure we have remote tracking
            remote_result = subprocess.run(["git", "remote"], cwd=str(repo_root), capture_output=True, text=True)
            if remote_result.returncode == 0 and "origin" in remote_result.stdout:
                # Get the default branch from remote
                remote_head = subprocess.run(["git", "symbolic-ref", "refs/remotes/origin/HEAD"],
                                           cwd=str(repo_root), capture_output=True, text=True)
                if remote_head.returncode == 0:
                    default_branch = remote_head.stdout.strip().replace("refs/remotes/origin/", "")
                    # Update local branch to track the correct remote branch
                    upstream_result = subprocess.run(["git", "branch", "--set-upstream-to", f"origin/{default_branch}"],
                                                   cwd=str(repo_root), capture_output=True, text=True)
                    if upstream_result.returncode == 0:
                        logging.info(f"Updated default branch to: {default_branch}")
                    else:
                        logging.warning(f"Failed to set upstream to {default_branch}: {upstream_result.stderr}")
                else:
                    # Fallback: try to find any branch that exists on remote
                    remote_branches = subprocess.run(["git", "branch", "-r"], cwd=str(repo_root), capture_output=True, text=True)
                    if remote_branches.returncode == 0:
                        branches = [b.strip() for b in remote_branches.stdout.split('\n')
                                  if b.strip() and not b.strip().endswith('->') and 'origin/' in b]
                        if branches:
                            # Use the first remote branch found (prefer main, then master)
                            preferred_branches = ['main', 'master']
                            selected_branch = None

                            for pref in preferred_branches:
                                for branch in branches:
                                    if f'origin/{pref}' in branch:
                                        selected_branch = pref
                                        break
                                if selected_branch:
                                    break

                            if not selected_branch:
                                selected_branch = branches[0].replace('origin/', '').strip()

                            upstream_result = subprocess.run(["git", "branch", "--set-upstream-to", f"origin/{selected_branch}"],
                                                           cwd=str(repo_root), capture_output=True, text=True)
                            if upstream_result.returncode == 0:
                                logging.info(f"Fallback: set upstream to {selected_branch}")
                            else:
                                logging.warning(f"Failed to set upstream to {selected_branch}: {upstream_result.stderr}")
        except subprocess.CalledProcessError as branch_err:
            logging.warning(f"Could not fix branch configuration: {branch_err}")
        except Exception as branch_ex:
            logging.warning(f"Unexpected error fixing branch: {branch_ex}")
            # Continue anyway, the pull might still work

        # Check repository status
        try:
            status_result = subprocess.run(["git", "status", "-uno"], cwd=str(repo_root), capture_output=True)
            status_lines = status_result.stdout.decode('utf-8', errors='replace') if isinstance(status_result.stdout, bytes) else status_result.stdout

            # Check if we have commits ahead/behind
            ahead_count = 0
            behind_count = 0
            if "ahead" in status_lines:
                import re
                ahead_match = re.search(r'ahead (\d+)', status_lines)
                if ahead_match:
                    ahead_count = int(ahead_match.group(1))

            if "behind" in status_lines:
                behind_match = re.search(r'behind (\d+)', status_lines)
                if behind_match:
                    behind_count = int(behind_match.group(1))

            # If we have commits ahead, push them first
            if ahead_count > 0:
                await message.answer(f"📤 У вас есть {ahead_count} локальных коммитов. Отправляю их сначала...")
                try:
                    # Push LFS objects first
                    subprocess.run(["git", "lfs", "push", "origin", "--all"],
                                 cwd=str(repo_root), capture_output=True, check=True)
                    # Then push commits
                    subprocess.run(["git", "push"], cwd=str(repo_root), capture_output=True, check=True)
                    await message.answer("✅ Локальные коммиты отправлены.")
                except subprocess.CalledProcessError as push_err:
                    error_msg = f"❌ Не удалось отправить локальные коммиты: {str(push_err)[:100]}"
                    await message.answer(error_msg, reply_markup=get_git_operations_keyboard())
                    return

            # Now try to pull if we're behind
            if behind_count > 0:
                await message.answer(f"📥 Есть {behind_count} обновлений с сервера. Загружаю...")

        except subprocess.CalledProcessError:
            # If status check fails, continue anyway
            pass

        # Check if we're ahead/behind
        status_result = subprocess.run(["git", "status", "-uno"], cwd=str(repo_root), capture_output=True)
        status_lines = status_result.stdout.decode('utf-8', errors='replace') if isinstance(status_result.stdout, bytes) else status_result.stdout

        # Try pull with rebase and autostash to handle local changes
        ok, err = git_pull_rebase_autostash(str(repo_root))
        if not ok:
            # If pull fails, provide detailed diagnostics
            error_msg = f"❌ Ошибка при обновлении репозитория.\n\n"

            # Check if there are uncommitted changes
            if has_changes:
                error_msg += f"⚠️ У вас есть незакоммиченные изменения.\n"

            # Check branch status
            if "ahead" in status_lines:
                error_msg += f"📤 У вас есть локальные коммиты, которые нужно отправить.\n"
            if "behind" in status_lines:
                error_msg += f"📥 Есть новые изменения на сервере.\n"

            error_msg += f"\nВозможные решения:\n"
            error_msg += f"• Закоммитить изменения: '💾 Закоммитить все изменения'\n"
            error_msg += f"• Проверить статус: '🧾 Git статус'\n"
            error_msg += f"• Переключить репозиторий в настройках\n\n"
            error_msg += f"Детали: {err[:150]}"
            await message.answer(error_msg, reply_markup=get_git_operations_keyboard(user_id=message.from_user.id))
            return

        # Success - try LFS refresh
        try:
            subprocess.run(["git", "lfs", "install"], cwd=str(repo_root), check=True, capture_output=True)
            subprocess.run(["git", "lfs", "fetch"], cwd=str(repo_root), check=True, capture_output=True)
            await message.answer("✅ Репозиторий и Git LFS обновлены.", reply_markup=get_git_operations_keyboard(user_id=message.from_user.id))
        except subprocess.CalledProcessError:
            await message.answer("✅ Репозиторий обновлен. ⚠️ Git LFS недоступен.", reply_markup=get_git_operations_keyboard(user_id=message.from_user.id))
        
        # Log repository update
        user_name = format_user_name(message)
        timestamp = format_datetime()
        log_message = f"🔄 Пользователь {user_name} обновил репозиторий [{timestamp}]"
        await log_to_group(message, log_message)

    except Exception as e:
        logging.exception(f"Unexpected error in update_repository: {e}")
        error_msg = f"❌ Неожиданная ошибка: {str(e)[:200]}"
        await message.answer(error_msg, reply_markup=get_git_operations_keyboard(user_id=message.from_user.id))


async def git_status(message):
    repo_root = await require_user_repo(message)
    if not repo_root:
        return
    
    session = user_doc_sessions.get(message.from_user.id)
    try:
        if session and session.get('doc'):
            rel = str((Path('docs') / session['doc']).as_posix())
            # Run git status with proper encoding handling
            st_result = subprocess.run(["git", "status", "--short", rel], cwd=str(repo_root), check=True, capture_output=True)
            st = st_result.stdout.decode('utf-8', errors='replace') if isinstance(st_result.stdout, bytes) else st_result.stdout
            st = st.strip()
            
            # Run git log with proper encoding handling
            log_result = subprocess.run(["git", "log", "-n", "5", "--pretty=oneline", "--", rel], cwd=str(repo_root), check=True, capture_output=True)
            log = log_result.stdout.decode('utf-8', errors='replace') if isinstance(log_result.stdout, bytes) else log_result.stdout
            log = log.strip()
            
            # Check Git LFS lock status
            rel_path = str((Path('docs') / session['doc']).as_posix())
            try:
                lfs_lock_info = get_lfs_lock_info(rel_path, cwd=repo_root)
                is_locked = lfs_lock_info is not None
                
                if is_locked:
                    # Get user's GitHub username for ownership check
                    user_repo_info = get_user_repo(message.from_user.id)
                    user_github_username = user_repo_info.get('git_username') if user_repo_info else None
                    
                    lfs_owner = lfs_lock_info.get('owner', '')
                    is_lock_owner = (
                        lfs_owner == str(message.from_user.id) or
                        lfs_owner == user_github_username or
                        (user_github_username and lfs_owner.lower() == user_github_username.lower())
                    )
                    can_unlock = is_lock_owner or (str(message.from_user.id) in ADMIN_IDS)
                    
                    lock_status = f"\n\n🔒 Заблокирован через Git LFS: {lfs_owner}"
                else:
                    can_unlock = False
                    lock_status = "\n\n🔓 Не заблокирован"
            except Exception as e:
                is_locked = False
                can_unlock = False
                lock_status = f"\n\n⚠️ Ошибка проверки блокировки: {str(e)[:100]}"
            
            out = f"📄 {session['doc']}\n\nСтатус:\n{st if st else 'все файлы в актуальном состоянии, нет несохранённых изменений'}\n\nRecent commits:\n{log if log else 'none'}{lock_status}"
            # Return to document menu if viewing document status
            reply_markup = get_document_keyboard(session['doc'], is_locked=is_locked, can_unlock=can_unlock,
                                               current_user_id=message.from_user.id, repo_root=repo_root)
        else:
            # Run git status with proper encoding handling
            st_result = subprocess.run(["git", "status", "--short"], cwd=str(repo_root), check=True, capture_output=True)
            st = st_result.stdout.decode('utf-8', errors='replace') if isinstance(st_result.stdout, bytes) else st_result.stdout
            st = st.strip()
            out = f"Git status (repo):\n{st if st else 'все файлы в актуальном состоянии, нет несохранённых изменений'}"
            reply_markup = get_git_operations_keyboard(user_id=message.from_user.id)
        await message.answer(out, reply_markup=reply_markup)
        
        # Log git status check
        user_name = format_user_name(message)
        timestamp = format_datetime()
        log_message = f"🔍 Пользователь {user_name} проверил статус Git репозитория [{timestamp}]"
        await log_to_group(message, log_message)
    except subprocess.CalledProcessError as e:
        err = (e.stderr or e.stdout or '')
        try:
            err = err.decode() if isinstance(err, (bytes, bytearray)) else str(err)
        except Exception:
            err = str(err)
        await message.answer(f"❌ Ошибка при выполнении git: {err[:200]}", reply_markup=get_git_operations_keyboard(user_id=message.from_user.id))


def get_repo_info_keyboard(user_id=None):
    """Клавиатура для раздела "О репозитории" с кнопкой настройки"""
    is_admin = False
    if user_id is not None:
        try:
            is_admin = str(user_id) in ADMIN_IDS
        except Exception:
            is_admin = False
    
    if is_admin:
        # Admin view with settings
        keyboard = [
            ["⚙️ Настроить репозиторий", "⚙️ Настройки"],
            ["🏠 Главное меню"]
        ]
    else:
        # Regular user view with setup option
        keyboard = [
            ["⚙️ Настроить репозиторий"],
            ["🏠 Главное меню"]
        ]
    
    if PTB_AVAILABLE:
        return PTBReplyKeyboardMarkup(keyboard=keyboard, resize_keyboard=True)
    
    # Fallback for aiogram
    return ReplyKeyboardMarkup(keyboard, resize_keyboard=True)


async def repo_info(message):
    """Показывает информацию о репозитории и предоставляет возможность настройки."""
    u = get_user_repo(message.from_user.id)
    if not u:
        # Репозиторий не настроен - предлагаем настройку
        await message.answer(
            "ℹ️ Репозиторий не настроен для вашего пользователя.\n\n"
            '⚙️ Нажмите "Настроить репозиторий" чтобы начать работу.', 
            reply_markup=get_repo_info_keyboard(message.from_user.id)
        )
        return
    
    # Получаем информацию о репозитории
    repo_root = Path(u.get('repo_path'))
    repo_url = u.get('repo_url', 'Не указан')
    
    # Получаем абсолютный путь для отображения
    abs_repo_path = repo_root.resolve()
    abs_docs_path = (abs_repo_path / "docs").resolve()
    
    # Формируем информацию (заголовок уже будет добавлен автоматически через PTBMessageAdapter)
    info_text = f"ℹ️ Дополнительная информация:\n\n"
    info_text += f"🔗 Удаленный URL: {repo_url}\n\n"
    
    # Проверяем статус подключения
    if repo_root.exists() and (repo_root / '.git').exists():
        try:
            subprocess.run(["git", "-C", str(repo_root), "remote", "show", "origin"], 
                          check=True, capture_output=True, text=True, timeout=5)
            info_text += f"✅ Подключение: активно\n"
        except Exception:
            info_text += f"⚠️ Подключение: неактивно\n"
    else:
        info_text += f"❌ Репозиторий не найден локально\n"
    
    # Заголовок будет добавлен автоматически через PTBMessageAdapter.answer
    await message.answer(info_text, reply_markup=get_repo_info_keyboard(message.from_user.id))
    
    # Log repo info check
    user_name = format_user_name(message)
    timestamp = format_datetime()
    log_message = f"ℹ️ Пользователь {user_name} запросил информацию о репозитории [{timestamp}]"
    await log_to_group(message, log_message)


async def commit_all_changes(message):
    """Commit all changes (including deletions) and push to remote repository."""
    repo_root = await require_user_repo(message)
    if not repo_root:
        return
    
    try:
        # Check if there are any changes to commit
        status_result = subprocess.run(["git", "status", "--porcelain"], cwd=str(repo_root), check=True, capture_output=True)
        status = status_result.stdout.decode('utf-8', errors='replace') if isinstance(status_result.stdout, bytes) else status_result.stdout
        status = status.strip()
        if not status:
            await message.answer("ℹ️ Нет изменений для коммита. Репозиторий уже синхронизирован.", reply_markup=get_git_operations_keyboard(user_id=message.from_user.id))
            return
        
        # Set git config if not already set - use user's credentials
        try:
            subprocess.run(["git", "config", "--get", "user.name"], cwd=str(repo_root), check=True, capture_output=True)
        except subprocess.CalledProcessError:
            # Get username from user repo config
            user_info = get_user_repo(message.from_user.id)
            if user_info and user_info.get('git_username'):
                subprocess.run(["git", "config", "user.name", user_info['git_username']], cwd=str(repo_root), check=True, capture_output=True)
            else:
                subprocess.run(["git", "config", "user.name", str(message.from_user.id)], cwd=str(repo_root), check=True, capture_output=True)
        
        try:
            subprocess.run(["git", "config", "--get", "user.email"], cwd=str(repo_root), check=True, capture_output=True)
        except subprocess.CalledProcessError:
            # Get username from user repo config for email
            user_info = get_user_repo(message.from_user.id)
            if user_info and user_info.get('git_username'):
                email = f"{user_info['git_username']}@users.noreply.github.com"
                subprocess.run(["git", "config", "user.email", email], cwd=str(repo_root), check=True, capture_output=True)
            else:
                subprocess.run(["git", "config", "user.email", f"user-{message.from_user.id}@gitdocs.local"], cwd=str(repo_root), check=True, capture_output=True)
        
        # Pull latest changes first to avoid conflicts
        ok, err = git_pull_rebase_autostash(str(repo_root))
        if not ok:
            await message.answer(f"⚠️ Предупреждение при обновлении репозитория: {err[:200]}. Продолжаю коммит...")
        
        # Add all changes (including deletions) - git add -A adds all changes including deletions
        subprocess.run(["git", "add", "-A"], cwd=str(repo_root), check=True, capture_output=True)
        
        # Get list of changed files for commit message
        changed_files_result = subprocess.run(["git", "status", "--short"], cwd=str(repo_root), check=True, capture_output=True)
        changed_files = changed_files_result.stdout.decode('utf-8', errors='replace') if isinstance(changed_files_result.stdout, bytes) else changed_files_result.stdout
        changed_files = changed_files.strip()
        files_list = changed_files.split("\n")
        file_list = "\n".join(files_list[:5])  # First 5 files
        if len(files_list) > 5:
            remaining = len(files_list) - 5
            file_list += f"\n... и еще {remaining} файлов"
        
        # Commit with descriptive message
        user_name = format_user_name(message)
        commit_msg = f"Update repository by {user_name}\n\nChanges:\n{file_list}"
        subprocess.run(["git", "commit", "-m", commit_msg], cwd=str(repo_root), check=True, capture_output=True)
        
        # Push LFS objects first (only current branch)
        await message.answer("📤 Отправляю LFS объекты...")
        try:
            lfs_push_result = subprocess.run(["git", "lfs", "push", "origin", "HEAD"],
                                           cwd=str(repo_root), capture_output=True, text=True, timeout=60)
            if lfs_push_result.returncode != 0:
                logging.warning(f"LFS push failed: {lfs_push_result.stderr}")
                await message.answer(f"⚠️ Предупреждение: проблемы с отправкой LFS объектов: {lfs_push_result.stderr[:100]}")
            else:
                await message.answer("✅ LFS объекты отправлены.")
        except subprocess.CalledProcessError as lfs_err:
            logging.warning(f"LFS push error: {lfs_err}")
            await message.answer(f"⚠️ Предупреждение: ошибка отправки LFS: {str(lfs_err)[:100]}")
        except subprocess.TimeoutExpired:
            await message.answer("⚠️ LFS push timed out, продолжаю...")

        # Push commits
        await message.answer("📤 Отправляю коммиты...")
        subprocess.run(["git", "push"], cwd=str(repo_root), check=True, capture_output=True)
        
        # Get commit hash
        try:
            commit_result = subprocess.run(["git", "rev-parse", "HEAD"], cwd=str(repo_root), check=True, capture_output=True)
            commit = commit_result.stdout.decode('utf-8', errors='replace') if isinstance(commit_result.stdout, bytes) else commit_result.stdout
            commit = commit.strip()
            await message.answer(f"✅ Все изменения успешно закоммичены и отправлены в репозиторий!\n\nCommit: `{commit}`", reply_markup=get_git_operations_keyboard(user_id=message.from_user.id))
        except Exception:
            await message.answer("✅ Все изменения успешно закоммичены и отправлены в репозиторий!", reply_markup=get_git_operations_keyboard(user_id=message.from_user.id))
            
        # Log commit operation
        user_name = format_user_name(message)
        timestamp = format_datetime()
        log_message = f"💾 Пользователь {user_name} закоммитил и отправил изменения в репозиторий [{timestamp}]"
        await log_to_group(message, log_message)
            
    except subprocess.CalledProcessError as e:
        err = (e.stderr or e.stdout or b'')
        try:
            err = err.decode(errors='ignore') if isinstance(err, (bytes, bytearray)) else str(err)
        except Exception:
            err = str(err)
        await message.answer(f"❌ Ошибка при коммите/пуше: {err[:300]}", reply_markup=get_git_operations_keyboard(user_id=message.from_user.id))
    except Exception as e:
        await message.answer(f"❌ Ошибка: {str(e)[:200]}", reply_markup=get_main_keyboard())


async def force_unlock_request(message):
    # request doc name to force-unlock
    is_admin = False
    try:
        user_id = str(message.from_user.id)
        is_admin = user_id in ADMIN_IDS
    except Exception:
        logging.warning(f"Admin check failed for user {getattr(message.from_user, 'id', 'unknown')}")
    
    if not is_admin:
        await message.answer("❌ Только админы могут инициировать принудительную разблокировку.", reply_markup=get_main_keyboard(user_id=message.from_user.id))
        return
    session = user_doc_sessions.get(message.from_user.id)
    if session and session.get('doc'):
        await force_unlock_by_name(message, session['doc'])
        return
    await message.answer("Пожалуйста, выберите документ из списка (📋 Документы), затем нажмите 'Разблокировать (принудительно)'.")


async def force_unlock_by_name(message, doc_name: str):
    # Only admins call this
    is_admin = False
    try:
        user_id = str(message.from_user.id)
        is_admin = user_id in ADMIN_IDS
    except Exception:
        logging.warning(f"Admin check failed for user {getattr(message.from_user, 'id', 'unknown')}")
    
    if not is_admin:
        await message.answer("❌ У вас нет прав для принудительной разблокировки.", reply_markup=get_main_keyboard(user_id=message.from_user.id))
        return

    # Search for document in entire repository
    repo_root = get_repo_for_user_id(message.from_user.id)
    doc_path = None
    for file_path in repo_root.rglob(doc_name):
        if (file_path.suffix.lower() == '.docx' and 
            not any(part.startswith('.') for part in file_path.parts) and
            '.git' not in file_path.parts and
            '__pycache__' not in file_path.parts and
            'node_modules' not in file_path.parts):
            doc_path = file_path
            break
    
    if not doc_path or not doc_path.exists():
        await message.answer(f"❌ Документ {doc_name} не найден!", reply_markup=get_document_keyboard(doc_name, is_locked=False))
        return
    
    # Use only filename to avoid protocol issues with SSH repositories
    filename_only = doc_path.name
    try:
        proc = subprocess.run(["git", "lfs", "unlock", "--force", filename_only], cwd=str(repo_root), check=True, capture_output=True, text=True, encoding='utf-8', errors='replace')
        await message.answer(f"🔓 Документ {doc_name} успешно принудительно разблокирован (git-lfs).\n{proc.stdout.strip()}", reply_markup=get_document_keyboard(doc_name, is_locked=False))
    except subprocess.CalledProcessError as e:
        err = (e.stderr or e.stdout or '').strip()
        await message.answer(f"⚠️ Ошибка при принудительной разблокировке: {err[:200]}", reply_markup=get_document_keyboard(doc_name, is_locked=False))


async def fix_lfs_issues(message):
    """Diagnose and fix common Git LFS issues"""
    # Only admins can fix LFS issues across all repositories
    is_admin = False
    try:
        user_id = str(message.from_user.id)
        is_admin = user_id in ADMIN_IDS
    except Exception:
        logging.warning(f"Admin check failed for user {getattr(message.from_user, 'id', 'unknown')}")
    
    if not is_admin:
        await message.answer("❌ Только администраторы могут исправлять проблемы Git LFS.", reply_markup=get_main_keyboard(user_id=message.from_user.id))
        return
        
    repo_root = await require_user_repo(message)
    if not repo_root:
        return

    try:
        await message.answer("🔧 Диагностика и исправление проблем Git LFS...")

        # Step 1: Check LFS status
        await message.answer("1️⃣ Проверяю статус Git LFS...")
        try:
            lfs_status_result = subprocess.run(["git", "lfs", "status"], cwd=str(repo_root), capture_output=True, timeout=30)
            if lfs_status_result.returncode != 0:
                await message.answer("❌ Git LFS не инициализирован. Инициализирую...")
                subprocess.run(["git", "lfs", "install"], cwd=str(repo_root), check=True, capture_output=True)
                await message.answer("✅ Git LFS инициализирован.")
            else:
                lfs_status = lfs_status_result.stdout.decode('utf-8', errors='replace') if isinstance(lfs_status_result.stdout, bytes) else lfs_status_result.stdout
                await message.answer("✅ Git LFS готов.")
        except subprocess.CalledProcessError:
            await message.answer("❌ Git LFS не установлен. Установите Git LFS на сервере.")
            return
        except subprocess.TimeoutExpired:
            await message.answer("⏰ Таймаут при проверке LFS статуса.")

        # Step 2: Fetch LFS objects
        await message.answer("2️⃣ Загружаю LFS объекты...")
        try:
            fetch_result = subprocess.run(["git", "lfs", "fetch", "--all"], cwd=str(repo_root),
                                        capture_output=True, timeout=120)
            if fetch_result.returncode == 0:
                await message.answer("✅ LFS объекты загружены.")
            else:
                fetch_stderr = fetch_result.stderr.decode('utf-8', errors='replace') if isinstance(fetch_result.stderr, bytes) else fetch_result.stderr
                await message.answer(f"⚠️ Проблемы при загрузке LFS: {fetch_stderr[:100]}")
        except subprocess.TimeoutExpired:
            await message.answer("⏰ Таймаут при загрузке LFS объектов.")

        # Step 3: Check LFS locks status
        await message.answer("3️⃣ Проверяю LFS блокировки...")
        try:
            # Get LFS locks - credentials stored globally
            locks_result = subprocess.run(["git", "lfs", "locks"], cwd=str(repo_root), capture_output=True, timeout=30)
            if locks_result.returncode == 0 and locks_result.stdout.strip():
                locks_output = locks_result.stdout.decode('utf-8', errors='replace') if isinstance(locks_result.stdout, bytes) else locks_result.stdout
                await message.answer(f"🔒 Активные блокировки:\n{locks_output[:200]}")
            else:
                await message.answer("✅ Нет активных LFS блокировок.")
        except subprocess.TimeoutExpired:
            await message.answer("⏰ Таймаут при проверке блокировок.")

        # Step 4: Push LFS objects with force flag
        await message.answer("4️⃣ Отправляю LFS объекты...")
        try:
            # Try multiple approaches
            push_success = False

            # First try with current branch
            try:
                current_branch_result = subprocess.run(["git", "rev-parse", "--abbrev-ref", "HEAD"],
                                              cwd=str(repo_root), capture_output=True)
                current_branch = current_branch_result.stdout.decode('utf-8', errors='replace') if isinstance(current_branch_result.stdout, bytes) else current_branch_result.stdout
                current_branch = current_branch.strip()
                push_result = subprocess.run(["git", "lfs", "push", "origin", current_branch],
                                           cwd=str(repo_root), capture_output=True, timeout=120)
                if push_result.returncode == 0:
                    push_success = True
                    await message.answer("✅ LFS объекты отправлены.")
                else:
                    push_stderr = push_result.stderr.decode('utf-8', errors='replace') if isinstance(push_result.stderr, bytes) else push_result.stderr
                    logging.warning(f"LFS push failed for branch {current_branch}: {push_stderr}")
            except Exception as e:
                logging.warning(f"LFS push branch-specific failed: {e}")

            # Fallback: try --all
            if not push_success:
                try:
                    push_all_result = subprocess.run(["git", "lfs", "push", "origin", "--all"],
                                                   cwd=str(repo_root), capture_output=True, timeout=120)
                    if push_all_result.returncode == 0:
                        push_success = True
                        await message.answer("✅ LFS объекты отправлены (--all).")
                    else:
                        push_all_stderr = push_all_result.stderr.decode('utf-8', errors='replace') if isinstance(push_all_result.stderr, bytes) else push_all_result.stderr
                        logging.warning(f"LFS push --all failed: {push_all_stderr}")
                except Exception as e:
                    logging.warning(f"LFS push --all failed: {e}")

            if not push_success:
                await message.answer("⚠️ Не удалось отправить LFS объекты. Возможно, они уже отправлены или есть проблемы с аутентификацией.")

        except subprocess.TimeoutExpired:
            await message.answer("⏰ Таймаут при отправке LFS объектов.")

        # Step 5: Clean up orphaned objects
        await message.answer("5️⃣ Очищаю orphaned LFS объекты...")
        try:
            prune_result = subprocess.run(["git", "lfs", "prune"], cwd=str(repo_root),
                                        capture_output=True, timeout=60)
            if prune_result.returncode == 0:
                prune_output = prune_result.stdout.decode('utf-8', errors='replace') if isinstance(prune_result.stdout, bytes) else prune_result.stdout
                if prune_output.strip():
                    await message.answer(f"🗑️ Очищено: {prune_output.strip()}")
                else:
                    await message.answer("✅ Orphaned объекты отсутствуют.")
            else:
                await message.answer("⚠️ Не удалось выполнить очистку LFS.")
        except subprocess.TimeoutExpired:
            await message.answer("⏰ Таймаут при очистке LFS.")

        await message.answer("✅ Диагностика LFS завершена!\n\nПопробуйте выполнить коммит или обновление репозитория снова.", reply_markup=get_git_operations_keyboard(user_id=message.from_user.id))

    except Exception as e:
        logging.exception(f"LFS fix failed: {e}")
        await message.answer(f"❌ Ошибка при исправлении LFS: {str(e)[:200]}", reply_markup=get_git_operations_keyboard(user_id=message.from_user.id))


async def resync_repository(message):
    """Force resync repository - dangerous operation, use as last resort"""
    # Only admins can perform dangerous operations like resync
    is_admin = False
    try:
        user_id = str(message.from_user.id)
        is_admin = user_id in ADMIN_IDS
    except Exception:
        logging.warning(f"Admin check failed for user {getattr(message.from_user, 'id', 'unknown')}")
    
    if not is_admin:
        await message.answer("❌ Только администраторы могут пересинхронизировать репозиторий.", reply_markup=get_main_keyboard(user_id=message.from_user.id))
        return
        
    repo_root = await require_user_repo(message)
    if not repo_root:
        return
    
    try:
        # Fetch latest changes
        await message.answer("🔄 Начинаю пересинхронизацию репозитория...")
        
        # Fetch from remote
        subprocess.run(["git", "fetch", "origin"], cwd=str(repo_root), check=True, capture_output=True)
        
        # Reset hard to origin/main (this removes all local changes)
        subprocess.run(["git", "reset", "--hard", "origin/main"], cwd=str(repo_root), check=True, capture_output=True)
        
        # Clean untracked files
        subprocess.run(["git", "clean", "-fd"], cwd=str(repo_root), check=True, capture_output=True)
        
        # Update git-lfs
        subprocess.run(["git", "lfs", "fetch"], cwd=str(repo_root), check=True, capture_output=True)
        subprocess.run(["git", "lfs", "pull"], cwd=str(repo_root), check=True, capture_output=True)
        
        await message.answer("✅ Репозиторий успешно пересинхронизирован!", reply_markup=get_git_operations_keyboard(user_id=message.from_user.id))
        
        # Log resync operation
        user_name = format_user_name(message)
        timestamp = format_datetime()
        log_message = f"🔄 Пользователь {user_name} пересинхронизировал репозиторий [{timestamp}]"
        await log_to_group(message, log_message)
        
    except subprocess.CalledProcessError as e:
        error_msg = f"❌ Ошибка при пересинхронизации: {str(e)[:200]}"
        if e.stderr:
            error_msg += f"\nДетали: {e.stderr.decode()[:100]}"
        await message.answer(error_msg, reply_markup=get_git_operations_keyboard(user_id=message.from_user.id))
    except Exception as e:
        await message.answer(f"❌ Ошибка при пересинхронизации: {str(e)[:200]}", reply_markup=get_git_operations_keyboard(user_id=message.from_user.id))

async def setup_repository_simple(msg, data):
    """Простая настройка репозитория"""
    try:
        repo_url = data['repo_url']
        username = data['username']
        password = data['password']

        user_id = msg.from_user.id
        repo_dir = USER_REPOS_DIR / str(user_id)

        # Build credentialized URL
        repo_url_with_creds = None
        if username and password and repo_url:
            repo_url_with_creds = "https://" + username + ":" + password + "@" + repo_url.replace("https://", "")

        # For initial setup, always proceed with cloning (no conflict resolution needed)
        # Remove any existing repo directory to ensure clean setup
        if repo_dir.exists():
            import shutil
            shutil.rmtree(repo_dir, ignore_errors=True)
        
        # Proceed with fresh clone
            # Clone new repo
            await handle_repo_action_simple(msg, "auto_clone")

    except Exception as e:
        logging.exception("Error in repo setup: %s", e)
        await msg.answer("❌ Ошибка при настройке репозитория.", reply_markup=get_main_keyboard())
        user_config_state.pop(msg.from_user.id, None)
        user_config_data.pop(msg.from_user.id, None)


async def handle_repo_action_simple(msg, action):
    """Обработка выбора действия с репозиторием"""
    data = user_config_data.get(msg.from_user.id, {})
    repo_url = data.get('repo_url')
    username = data.get('username')
    password = data.get('password')
    user_id = msg.from_user.id
    repo_dir = USER_REPOS_DIR / str(user_id)

    if action == "❌ Отмена":
        await msg.answer("❌ Настройка репозитория отменена.", reply_markup=get_main_keyboard())
        user_config_state.pop(msg.from_user.id, None)
        user_config_data.pop(msg.from_user.id, None)
        return

    # Build credentialized URL
    repo_url_with_creds = None
    if username and password and repo_url:
        repo_url_with_creds = "https://" + username + ":" + password + "@" + repo_url.replace("https://", "")

    if action == "🔄 Переключиться на новый репозиторий":
        try:
            if repo_url_with_creds:
                subprocess.run(["git", "remote", "set-url", "origin", repo_url_with_creds], cwd=str(repo_dir), check=True, capture_output=True)
            subprocess.run(["git", "fetch", "origin"], cwd=str(repo_dir), check=True, capture_output=True)
            subprocess.run(["git", "reset", "--hard", "origin/main"], cwd=str(repo_dir), check=True, capture_output=True)
            subprocess.run(["git", "clean", "-fd"], cwd=str(repo_dir), check=True, capture_output=True)
            await msg.answer("✅ Репозиторий успешно переключен!", reply_markup=get_main_keyboard())
        except subprocess.CalledProcessError as e:
            logging.error("Failed to switch repo: %s", e.stderr.decode(errors='ignore') if e.stderr else '')
            await msg.answer("❌ Ошибка при переключении репозитория.", reply_markup=get_main_keyboard())

    elif action == "🗑️ Удалить старую папку и клонировать заново":
        try:
            import shutil
            if repo_dir.exists():
                shutil.rmtree(repo_dir)
            if not repo_url_with_creds:
                await msg.answer("❌ Неверный URL репозитория.", reply_markup=get_main_keyboard())
                return
            repo_dir.parent.mkdir(parents=True, exist_ok=True)
            subprocess.run(["git", "clone", repo_url_with_creds, str(repo_dir)], check=True, capture_output=True)
            await msg.answer("✅ Репозиторий клонирован!", reply_markup=get_main_keyboard())
        except Exception as e:
            logging.error("Failed to clone repo: %s", str(e))
            await msg.answer("❌ Ошибка при клонировании.", reply_markup=get_main_keyboard())

    elif action == "auto_clone":
        try:
            if not repo_url_with_creds:
                await msg.answer("❌ Неверный URL репозитория.", reply_markup=get_main_keyboard())
                return
            repo_dir.parent.mkdir(parents=True, exist_ok=True)
            
            # If the directory exists but is not a git repo, remove it first
            if repo_dir.exists() and not (repo_dir / '.git').exists():
                import shutil
                shutil.rmtree(repo_dir)
            
            subprocess.run(["git", "clone", repo_url_with_creds, str(repo_dir)], check=True, capture_output=True)
            await msg.answer("✅ Репозиторий настроен!", reply_markup=get_main_keyboard())
        except subprocess.CalledProcessError as e:
            logging.error("Clone failed: %s", e.stderr.decode(errors='ignore') if e.stderr else '')
            await msg.answer("❌ Ошибка при клонировании.", reply_markup=get_main_keyboard())

    # Configure git and git-lfs - use user's credentials
    try:
        # Only set git config if not already configured
        subprocess.run(["git", "config", "--get", "user.name"], cwd=str(repo_dir), check=True, capture_output=True)
    except subprocess.CalledProcessError:
        # Set user name from provided username
        try:
            subprocess.run(["git", "config", "user.name", username], cwd=str(repo_dir), check=True, capture_output=True)
        except subprocess.CalledProcessError:
            pass
    
    # Configure GitLab-specific settings if it's a GitLab repository
    if repo_url and 'gitlab.' in repo_url:
        try:
            configure_gitlab_credentials(str(repo_dir), username, password, user_id)
            # Save Git configuration for persistence
            save_git_config_to_user_data(user_id, str(repo_dir))
        except Exception as e:
            logging.warning(f"Failed to configure GitLab credentials: {e}")
    
    try:
        subprocess.run(["git", "config", "--get", "user.email"], cwd=str(repo_dir), check=True, capture_output=True)
    except subprocess.CalledProcessError:
        # Set email based on username
        try:
            email = f"{username}@users.noreply.github.com"
            subprocess.run(["git", "config", "user.email", email], cwd=str(repo_dir), check=True, capture_output=True)
        except subprocess.CalledProcessError:
            pass

    try:
        subprocess.run(["git", "lfs", "install"], cwd=str(repo_dir), check=True, capture_output=True)
        subprocess.run(["git", "lfs", "fetch"], cwd=str(repo_dir), check=True, capture_output=True)
    except subprocess.CalledProcessError:
        pass

    # Save user repo mapping
    set_user_repo(user_id, str(repo_dir), repo_url=repo_url, username=username)

    # List documents - search entire repository for .docx files
    docs = list(repo_dir.rglob("*.docx"))
    
    # Filter out files in .git directory and other hidden/system directories
    docs = [doc for doc in docs 
            if not any(part.startswith('.') for part in doc.parts) 
            and '.git' not in doc.parts
            and '__pycache__' not in doc.parts
            and 'node_modules' not in doc.parts]
    if docs:
        await list_documents(msg)

    # Clean up state
    user_config_state.pop(msg.from_user.id, None)
    user_config_data.pop(msg.from_user.id, None)


async def go_back(message, state=None):
    """Возврат в главное меню"""
    if state and hasattr(state, 'clear'):
        await state.clear()
    # Clear document session when going back
    user_doc_sessions.pop(message.from_user.id, None)
    await message.answer("🏠 Главное меню", reply_markup=get_main_keyboard(message.from_user.id))

async def main():
    # Initialize personal credentials system on startup
    initialize_persistent_credentials()
    
    # Restore LFS configuration for all user repositories on startup
    try:
        user_repos = load_user_repos()
        for composite_key, repo_data in user_repos.items():
            try:
                repo_path = Path(repo_data.get('repo_path', ''))
                repo_url = repo_data.get('repo_url', '')
                
                if repo_path.exists() and repo_url and (repo_path / '.git').exists():
                    # Re-configure LFS for each repository to ensure correct protocol-specific URL
                    lfs_manager = GitLabLFSManager()
                    result = lfs_manager.configure_gitlab_lfs(str(repo_path), repo_url)
                    if result:
                        logging.info(f"LFS configuration restored for {repo_path}")
                    else:
                        logging.warning(f"Failed to restore LFS configuration for {repo_path}")
            except Exception as e:
                logging.warning(f"Failed to restore LFS for repository {composite_key}: {e}")
    except Exception as e:
        logging.error(f"Failed to restore LFS configurations: {e}")
    
    # Apply saved Git configurations for all users
    try:
        user_repos = load_user_repos()
        for user_id_str in user_repos.keys():
            try:
                user_id = int(user_id_str)
                apply_user_git_config(user_id)
            except (ValueError, TypeError):
                continue
    except Exception as e:
        logging.error(f"Failed to apply user Git configs: {e}")
    
    # Migrate user repos format if needed
    try:
        migrated = migrate_user_repos_format()
        if migrated:
            logging.info("User repositories format migrated to support VCS types")
    except Exception as e:
        logging.error(f"Failed to migrate user repos format: {e}")
    
    logging.info("GitHub DOCX Document Management Bot запущен!")
    logging.info(f"Репозиторий: {REPO_PATH}")
    # If START_POLLING is explicitly disabled via env var, do not attempt network connection
    start_polling_env = os.getenv("START_POLLING", "true").lower()
    if start_polling_env in ("0", "false", "no"):
        logging.warning("START_POLLING is disabled. Running in offline mode.")
        return

    # Warn if token appears hardcoded (encourage using env var)
    if not TOKEN:
        logging.error("BOT_TOKEN not provided via environment. Set BOT_TOKEN before starting the bot.")
        return

    # If PTB is available and selected, run with python-telegram-bot
    use_ptb = os.getenv("USE_PTB", "true").lower() in ("1", "true", "yes") and PTB_AVAILABLE
    if use_ptb:
        # Register PTB handlers and run application
        # Create and configure the application
        app = ApplicationBuilder().token(TOKEN).build()

        # Command/start
        async def start_ptb(update: Update, context: ContextTypes.DEFAULT_TYPE):
            msg = PTBMessageAdapter(update, context)
            await msg.answer(
                "🤖 Добро пожаловать в систему управления документами!\n\n"
                "📂 Документы - работа с документами и блокировками\n"
                "🔧 Git операции - обновление и коммиты\n"
                "ℹ️ О репозитории - информация и настройка репозитория\n"
                "📖 Инструкции - помощь и руководство\n\n"
                "Для начала работы настройте репозиторий в разделе ℹ️ О репозитории.",
                reply_markup=get_main_keyboard(msg.from_user.id)
            )

        app.add_handler(CommandHandler('start', start_ptb))
        
# Command handler removed - using text router instead

        # Direct text handlers map to existing functions via adapter
        async def text_router(update: Update, context: ContextTypes.DEFAULT_TYPE):
            text = (update.message.text or "").strip()
            msg = PTBMessageAdapter(update, context)
            
            # DEBUG: Log all incoming text
            print(f"DEBUG: Received text: '{text}'")

            # DEBUG: Check if we reach this point
            print(f"DEBUG: Processing text: '{text}'")
            
            # Handle user edit buttons
            if text.startswith("✏️ Редактировать "):
                try:
                    target_user_id = text.replace("✏️ Редактировать ", "")
                    if target_user_id.isdigit():
                        await show_user_edit_menu(msg, target_user_id)
                    else:
                        await msg.answer("❌ Неверный формат ID пользователя")
                    return
                except Exception as e:
                    await msg.answer(f"❌ Ошибка: {str(e)}")
                    return
            
            # Главное меню
            if text == "📂 Документы":
                await list_documents(msg)
                return
            if text == "🔧 Git операции":
                await msg.answer("🔧 Git операции", reply_markup=get_git_operations_keyboard(user_id=msg.from_user.id))
                return
            if text == "🔒 Блокировки":
                await msg.answer("🔒 Управление блокировками", reply_markup=get_locks_keyboard(user_id=msg.from_user.id))
                return
            if text == "⚙️ Настройки":
                await msg.answer("⚙️ Настройки репозитория", reply_markup=get_settings_keyboard(msg.from_user.id))
                return
            
            if text == "⚙️ Настроить репозиторий":
                await setup_user_own_repository(msg)
                return
            
            # Admin user management
            if text == "👥 Управление пользователями":
                await show_users_management(msg)
                return
            
            if text == "🔄 Обновить список":
                await show_users_management(msg)
                return
            
            # User editing field handlers
            if text.startswith("📱 Изменить Telegram"):
                # Ask for new Telegram username
                user_sessions = globals().get('user_edit_sessions', {})
                session = user_sessions.get(msg.from_user.id)
                if session:
                    await msg.answer("Введите новый Telegram username (без @):")
                    user_sessions[msg.from_user.id]['editing_field'] = 'telegram_username'
                    globals()['user_edit_sessions'] = user_sessions
                return
            
            if text.startswith("🐙 Изменить GitHub"):
                # Ask for new GitHub username
                user_sessions = globals().get('user_edit_sessions', {})
                session = user_sessions.get(msg.from_user.id)
                if session:
                    await msg.answer("Введите новый GitHub username:")
                    user_sessions[msg.from_user.id]['editing_field'] = 'git_username'
                    globals()['user_edit_sessions'] = user_sessions
                return
            
            if text.startswith("🔗 Изменить репозиторий"):
                # Ask for new repository URL
                user_sessions = globals().get('user_edit_sessions', {})
                session = user_sessions.get(msg.from_user.id)
                if session:
                    await msg.answer("Введите новый URL репозитория:")
                    user_sessions[msg.from_user.id]['editing_field'] = 'repo_url'
                    globals()['user_edit_sessions'] = user_sessions
                return
            
            if text == "⚙️ Настроить репозиторий":
                await setup_user_own_repository(msg)
                return
            
            if text == "💾 Сохранить изменения":
                await save_user_changes(msg)
                return
            
            if text == "◀️ Назад к списку":
                await show_users_management(msg)
                return
            if text == "ℹ️ О репозитории":
                await repo_info(msg)
                return
            if text == "🏠 Главное меню":
                await msg.answer("🏠 Главное меню", reply_markup=get_main_keyboard(msg.from_user.id))
                return
            if text == "📖 Инструкции":
                await show_instructions(msg)
                return

            # Git операции меню
            if text == "🔄 Обновить репозиторий":
                await update_repository(msg)
                return
            if text == "🧾 Git статус":
                await git_status(msg)
                return

            if text == "🔧 Исправить LFS проблемы":
                await fix_lfs_issues(msg)
                return
            if text == "🔄 Пересинхронизировать репозиторий":
                await resync_repository(msg)
                return

            # Меню блокировок
            if text == "🔒 Статус всех блокировок":
                await check_lock_status(msg)
                return

            # Меню настроек
            if text == "🔧 Настроить репозиторий":
                user_config_state[msg.from_user.id] = 'waiting_for_repo_url'
                await msg.answer("Введите URL репозитория (например, https://github.com/user/repo):")
                return

            # Handle user editing input
            user_sessions = globals().get('user_edit_sessions', {})
            session = user_sessions.get(msg.from_user.id)
            
            # Handle Git username collection (works for both GitHub and GitLab)
            if session and session.get('collect_git_username'):
                git_username = text.strip()
                if git_username.startswith('@'):
                    git_username = git_username[1:]  # Remove @ prefix
                
                # Store username and determine next step based on repository type
                user_id = session['user_id']
                repo_type = session.get('repo_type', REPO_TYPES['GITHUB'])
                repo_url = session.get('repo_url', '')
                
                user_sessions = globals().get('user_edit_sessions', {})
                user_sessions[user_id]['git_username'] = git_username
                user_sessions[user_id]['collect_git_username'] = False
                globals()['user_edit_sessions'] = user_sessions
                
                # Different messages based on repository type
                if repo_type == REPO_TYPES['GITLAB']:
                    # For GitLab, we already have SSH setup, just need username
                    # Update user data
                    user_repos = load_user_repos()
                    for key, repo_data in user_repos.items():
                        if str(repo_data.get('telegram_id')) == str(user_id):
                            user_repos[key]['git_username'] = git_username
                            break
                    save_user_repos(user_repos)
                    
                    # Clear session
                    user_sessions = globals().get('user_edit_sessions', {})
                    del user_sessions[msg.from_user.id]
                    globals()['user_edit_sessions'] = user_sessions
                    
                    await msg.answer(
                        f"✅ Отлично! Репозиторий полностью настроен через SSH!\n\n"
                        f"📁 Репозиторий: {repo_url}\n"
                        f"👤 GitLab пользователь: {git_username}\n\n"
                        f"Теперь вы можете работать с документами.\n"
                        f"Git операции будут использовать SSH аутентификацию.",
                        reply_markup=get_main_keyboard(msg.from_user.id)
                    )
                else:
                    # For GitHub, continue with PAT collection
                    user_sessions[user_id]['collect_pat'] = True
                    globals()['user_edit_sessions'] = user_sessions
                    
                    await msg.answer(
                        f"✅ GitHub username ({git_username}) сохранен!\n\n"
                        f"🔑 Теперь введите ваш Personal Access Token (PAT) для GitHub:\n"
                        f"(Создайте его на GitHub: Settings → Developer settings → Personal access tokens)\n\n"
                        f"⚠️ ВНИМАНИЕ: Это НЕ ваш пароль от GitHub!\n"
                        f"Токен должен иметь права `repo`"
                    )
                return
            
            # Handle PAT collection
            if session and session.get('collect_pat'):
                pat = text.strip()
                git_username = session['git_username']
                repo_url = session['repo_url']
                user_id = session['user_id']
                
                # Update user data
                user_repos = load_user_repos()
                user_key = str(user_id)
                
                if user_key in user_repos:
                    user_repos[user_key]['git_username'] = git_username
                    user_repos[user_key]['repo_url'] = repo_url
                    save_user_repos(user_repos)
                    
                    # Configure Git with personal credentials
                    repo_path = user_repos[user_key]['repo_path']
                    configure_git_with_credentials(repo_path, git_username, pat, user_id)
                    
                    # Clear session
                    user_sessions = globals().get('user_edit_sessions', {})
                    del user_sessions[msg.from_user.id]
                    globals()['user_edit_sessions'] = user_sessions
                    
                    await msg.answer(
                        f"✅ Отлично! Репозиторий полностью настроен!\n\n"
                        f"📁 Репозиторий: {repo_url}\n"
                        f"👤 GitHub пользователь: {git_username}\n\n"
                        f"Теперь вы можете работать с документами.\n"
                        f"Git LFS операции будут использовать сохраненные учетные данные."
                    )
                else:
                    await msg.answer("❌ Ошибка: пользователь не найден в системе.")
                return
            
            # Handle user's own repository setup
            if session and session.get('setup_own_repo'):
                repo_url = text.strip()
                if repo_url.startswith('https://'):
                    await perform_user_repo_setup(msg, session, repo_url)
                else:
                    await msg.answer("❌ Неверный формат URL. Используйте: https://github.com/username/repository")
                return
            
            # Handle SSH key confirmation for GitLab
            if session and session.get('waiting_for_ssh_confirmation'):
                if text == "✅ Я установил ключ в репозитории":
                    # Proceed with repository setup using SSH
                    repo_url = session['repo_url']
                    ssh_setup_result = session['ssh_setup_result']
                    user_id = session['user_id']
                    
                    # Clear session
                    user_sessions = globals().get('user_edit_sessions', {})
                    del user_sessions[msg.from_user.id]
                    globals()['user_edit_sessions'] = user_sessions
                    
                    # Hide keyboard and continue with repository setup
                    from telegram import ReplyKeyboardRemove
                    await msg.context.bot.send_message(
                        chat_id=msg.chat.id,
                        text="⏳ Начинаем настройку репозитория...",
                        reply_markup=ReplyKeyboardRemove()
                    )
                    await continue_gitlab_setup_after_ssh(msg, user_id, repo_url, ssh_setup_result)
                    return
                elif text == "❌ Отмена":
                    # Cancel setup
                    user_sessions = globals().get('user_edit_sessions', {})
                    del user_sessions[msg.from_user.id]
                    globals()['user_edit_sessions'] = user_sessions
                    
                    from telegram import ReplyKeyboardRemove
                    await msg.context.bot.send_message(
                        chat_id=msg.chat.id,
                        text="❌ Настройка репозитория отменена.",
                        reply_markup=ReplyKeyboardRemove()
                    )
                    await msg.answer("🏠 Возвращаемся в главное меню...", reply_markup=get_main_keyboard(msg.from_user.id))
                    return
            
            # Handle full repository setup mode (deprecated - removed for security reasons)
            if session and session.get('setup_repo_mode'):
                await msg.answer("❌ Эта функция больше недоступна. Пользователь должен настраивать свой репозиторий самостоятельно.")
                # Clear session
                user_sessions = globals().get('user_edit_sessions', {})
                del user_sessions[msg.from_user.id]
                globals()['user_edit_sessions'] = user_sessions
                return
            
            if session and 'editing_field' in session:
                field_to_update = session['editing_field']
                new_value = text.strip()
                
                # Remove @ prefix if present for Telegram username
                if field_to_update == 'telegram_username' and new_value.startswith('@'):
                    new_value = new_value[1:]
                
                await update_user_field(msg, field_to_update, new_value)
                
                # Remove editing flag
                del session['editing_field']
                user_sessions[msg.from_user.id] = session
                globals()['user_edit_sessions'] = user_sessions
                
                # Show edit menu again
                await show_user_edit_menu(msg, session['target_user_id'])
                return
            
            # Работа с документами
            if text.startswith("📄 ") or text.startswith("📄🔒 "):
                # Выбор документа из списка (включая заблокированные документы)
                await handle_doc_selection(type('M', (), {'text': text, 'from_user': msg.from_user, 'answer': msg.answer}))
                return
            if text == "📥 Скачать":
                await download_document(msg)
                return
            if text == "📤 Загрузить изменения":
                await upload_changes(msg)
                return

            if text == "🔒 Заблокировать":
                await lock_document(msg)
                return
            if text == "🔓 Разблокировать":
                await unlock_document(msg)
                return
            if text == "🔓 Разблокировать (принудительно)":
                await force_unlock_request(msg)
                return

            # Навигация
            if text == "◀️ Назад в главное меню" or text == "◀️ Назад":
                await go_back(msg)
                return
            if text == "◀️ Назад к документам":
                await list_documents(msg)
                return
            # Handle file-name typed fallback for download
            if text.endswith('.docx'):
                # build a fake message object compatible with existing handler
                await handle_doc_name_input(type('M', (), {'text': text, 'from_user': msg.from_user, 'answer': msg.answer, 'chat': msg.chat}))
                return

            # If user is in setup flow
            state = user_config_state.get(msg.from_user.id)
            if state == 'waiting_for_repo_url':
                user_config_data[msg.from_user.id] = {'repo_url': text}
                user_config_state[msg.from_user.id] = 'waiting_for_username'
                await msg.answer("Введите ваше имя пользователя GitHub:")
                return
            if state == 'waiting_for_username':
                user_config_data[msg.from_user.id]['username'] = text
                user_config_state[msg.from_user.id] = 'waiting_for_password'
                await msg.answer("Введите ваш токен доступа GitHub (Personal Access Token):")
                return
            if state == 'waiting_for_password':
                data = user_config_data.get(msg.from_user.id, {})
                data['password'] = text
                await setup_repository_simple(msg, data)
                return

            if state == 'waiting_for_repo_action':
                await handle_repo_action_simple(msg, text)
                return
        async def document_router(update: Update, context: ContextTypes.DEFAULT_TYPE):
            msg = PTBMessageAdapter(update, context)
            await handle_document_upload(msg)

        # Register message handlers
        app.add_handler(MessageHandler(filters.Document.ALL, document_router))
        app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, text_router))

        # Run the application properly by using run_polling as a blocking call
        # This will handle the entire lifecycle properly
        await app.initialize()
        try:
            await app.start()
            await app.updater.start_polling(drop_pending_updates=True)
            # Run forever - this is the main loop
            await asyncio.Event().wait()  # Keep running indefinitely
        finally:
            await app.updater.stop()
            await app.stop()
        return

async def show_instructions(message):
    """Show simple and clear instructions for using the bot"""
    instructions = """📖 ПОЛЬЗОВАТЕЛЬСКАЯ ИНСТРУКЦИЯ

Добро пожаловать в систему управления документами!

🚀 БЫСТРЫЙ СТАРТ:
1. Нажмите кнопку "ℹ️ О репозитории"
2. Нажмите "⚙️ Настроить репозиторий"
3. Введите URL вашего репозитория на GitHub
4. Введите ваш логин на GitHub
5. Введите Personal Access Token
6. Готово! Теперь вы можете работать с документами

📂 РАБОТА С ДОКУМЕНТАМИ:
• Нажмите "📂 Документы" в главном меню
• Выберите нужный .docx файл из списка
• Чтобы предотвратить одновременное редактирование:
  - Нажмите "🔒 Заблокировать" - документ станет доступен только вам
  - После завершения работы нажмите "🔓 Разблокировать"

🔧 GIT ОПЕРАЦИИ:
• "🔄 Обновить репозиторий" - загрузить последние изменения с GitHub
• "⬆️ Загрузить изменения" - отправить ваши изменения на GitHub
• "🧾 Git статус" - посмотреть состояние файлов

🔐 СОЗДАНИЕ PERSONAL ACCESS TOKEN:
1. Зайдите на GitHub → Settings
2. В левом меню выберите "Developer settings"
3. Выберите "Personal access tokens" → "Tokens (classic)"
4. Нажмите "Generate new token"
5. Дайте имя токену (например, "Git Docs Bot")
6. Выберите срок действия (рекомендуется 90 дней)
7. Поставьте галочку напротив "repo" (все пункты внутри)
8. Нажмите "Generate token"
9. СКОПИРУЙТЕ токен (показывается только один раз!)

⚠️ ВАЖНО:
• Токен - это как ваш пароль, никому его не говорите
• Если токен утерян, создайте новый в настройках GitHub
• При повторной настройке репозитория старые данные удалятся

❓ ЧАСТЫЕ ВОПРОСЫ:

Q: Что делать, если документ заблокирован другим пользователем?
A: Дождитесь, пока владелец разблокирует документ, или свяжитесь с ним

Q: Можно ли редактировать документ без блокировки?
A: Да, но это может привести к конфликтам при одновременной работе

Q: Где хранятся мои данные?
A: В вашем репозитории на GitHub и локально в боте

Нужна дополнительная помощь? Обратитесь к администратору!"""
    
    await message.answer(instructions, reply_markup=get_main_keyboard(message.from_user.id))

# === Admin User Management Functions ===

async def show_users_management(message):
    """Show list of all users with configured repositories"""
    user_repos = load_user_repos()
    
    if not user_repos:
        await message.answer("📭 Нет пользователей с настроенными репозиториями.", 
                           reply_markup=get_settings_keyboard(message.from_user.id))
        return
    
    # Build user list with edit buttons
    user_list = "👥 Пользователи с настроенными репозиториями:\n\n"
    
    keyboard = []
    
    for key, repo_data in user_repos.items():
        telegram_id = repo_data.get('telegram_id', 'unknown')
        telegram_username = repo_data.get('telegram_username', 'не задан')
        git_username = repo_data.get('git_username', 'не задан')
        repo_url = repo_info.get('repo_url', 'не задан')
        
        user_list += f"👤 ID: {telegram_id}\n"
        user_list += f"   📱 Telegram: @{telegram_username}\n"
        user_list += f"   🐙 GitHub: {git_username}\n"
        user_list += f"   🔗 Репозиторий: {repo_url}\n\n"
        
        # Add edit button for each user
        keyboard.append([f"✏️ Редактировать {telegram_id}"])
    
    # Add navigation buttons
    keyboard.append(["🔄 Обновить список"])
    keyboard.append(["◀️ Назад в настройки"])
    
    if PTB_AVAILABLE:
        reply_markup = PTBReplyKeyboardMarkup(keyboard=keyboard, resize_keyboard=True)
    else:
        reply_markup = keyboard
    
    await message.answer(user_list, reply_markup=reply_markup)


async def show_user_edit_menu(message, target_user_id):
    """Show user editing menu with buttons"""
    user_repos = load_user_repos()
    
    # Check if there's an active editing session
    user_sessions = globals().get('user_edit_sessions', {})
    session = user_sessions.get(message.from_user.id, {})
    
    # Find user by ID
    user_info = None
    user_key = None
    
    for key, repo_data in user_repos.items():
        if str(repo_data.get('telegram_id')) == str(target_user_id):
            user_key = key
            user_info = repo_data
            break
    
    if not user_info:
        await message.answer("❌ Пользователь не найден.", 
                           reply_markup=get_settings_keyboard(message.from_user.id))
        return
    
    # Use session data if available, otherwise use file data
    display_info = session.get('user_info', user_info) if session.get('target_user_id') == str(target_user_id) else user_info
    
    # Show current data
    current_data = f"📝 Редактирование пользователя ID: {target_user_id}\n\n"
    current_data += "Текущие данные:\n"
    current_data += f"📱 Telegram: @{display_info.get('telegram_username', 'не задан')}\n"
    current_data += f"🐙 GitHub: {display_info.get('git_username', 'не задан')}\n"
    current_data += f"🔗 Репозиторий: {display_info.get('repo_url', 'не задан')}\n\n"
    current_data += "Выберите поле для изменения:"
    
    # Create editing buttons
    keyboard = [
        ["📱 Изменить Telegram"],
        ["🐙 Изменить GitHub"],
        ["🔗 Изменить репозиторий"],
        ["💾 Сохранить изменения"],
        ["❌ Отмена"]
    ]
    
    if PTB_AVAILABLE:
        reply_markup = PTBReplyKeyboardMarkup(keyboard=keyboard, resize_keyboard=True)
    else:
        reply_markup = keyboard
    
    # Store user data for editing session
    user_sessions = globals().get('user_edit_sessions', {})
    user_sessions[message.from_user.id] = {
        'target_user_id': target_user_id,
        'user_key': user_key,
        'user_info': user_info.copy()
    }
    globals()['user_edit_sessions'] = user_sessions
    
    await message.answer(current_data, reply_markup=reply_markup)


async def update_user_field(message, field_name, new_value):
    """Update specific field for user in user_repos"""
    user_sessions = globals().get('user_edit_sessions', {})
    session = user_sessions.get(message.from_user.id)
    
    if not session:
        await message.answer("❌ Сессия редактирования не найдена. Начните заново.",
                           reply_markup=get_settings_keyboard(message.from_user.id))
        return
    
    # Update the field in session
    session['user_info'][field_name] = new_value
    user_sessions[message.from_user.id] = session
    globals()['user_edit_sessions'] = user_sessions
    
    # Special handling for repo_url change
    if field_name == 'repo_url':
        try:
            # Clone new repository
            repo_path = Path(session['user_info']['repo_path'])
            if repo_path.exists():
                # Remove old repository
                import shutil
                shutil.rmtree(repo_path)
            
            # Clone new repository
            subprocess.run(['git', 'clone', new_value, str(repo_path)], check=True, capture_output=True)
            await message.answer(f"✅ Репозиторий успешно переключен на: {new_value}")
        except Exception as e:
            await message.answer(f"⚠️ Репозиторий обновлен в настройках, но возникла ошибка при клонировании: {str(e)}")
    
    # Confirm update
    field_names = {
        'telegram_username': 'Telegram username',
        'git_username': 'GitHub username',
        'repo_url': 'URL репозитория'
    }
    
    if field_name != 'repo_url':  # Don't send duplicate message for repo_url
        await message.answer(f"✅ {field_names.get(field_name, field_name)} обновлен на: {new_value}")


async def perform_user_repo_setup(message, session, repo_url):
    """Execute user's own repository setup with VCS-specific authentication"""
    try:
        user_id = session['user_id']
        
        # Detect repository type
        repo_type = detect_repository_type(repo_url)
        await message.answer(f"🔍 Обнаружен тип репозитория: {repo_type.upper()}")
        
        # For GitLab repositories, check if SSH setup is needed
        if repo_type == REPO_TYPES['GITLAB']:
            # Setup SSH access for GitLab
            ssh_setup_result = setup_gitlab_ssh_access(user_id, repo_url)
            if not ssh_setup_result['success']:
                await message.answer(f"❌ Ошибка настройки SSH для GitLab: {ssh_setup_result['error']}")
                return
            
            # Send SSH setup instructions to user
            await message.answer(ssh_setup_result['instructions'])
            
            # Store SSH info in session and wait for user confirmation
            user_sessions = globals().get('user_edit_sessions', {})
            user_sessions[user_id] = {
                'user_id': user_id,
                'repo_url': repo_url,
                'repo_type': repo_type,
                'ssh_setup_result': ssh_setup_result,
                'waiting_for_ssh_confirmation': True
            }
            globals()['user_edit_sessions'] = user_sessions
            
            # Send confirmation button
            keyboard = [
                ["✅ Я установил ключ в репозитории"],
                ["❌ Отмена"]
            ]
            
            if PTB_AVAILABLE:
                reply_markup = PTBReplyKeyboardMarkup(keyboard=keyboard, resize_keyboard=True)
            else:
                reply_markup = keyboard
            
            await message.answer(
                "🔐 После добавления SSH ключа в ваш GitLab, нажмите кнопку ниже для продолжения настройки.",
                reply_markup=reply_markup
            )
            return
        else:
            # For GitHub or unknown repositories, use HTTPS
            repo_url_to_use = repo_url
        
        # Check if user exists, if not - create basic user entry
        user_repo = get_user_repo(user_id)
        
        if not user_repo:
            # Create basic user entry for new user
            await message.answer("🆕 Обнаружен новый пользователь. Создаем базовую запись...")
            user_repo = create_basic_user_entry(user_id, message.from_user.username)
            
        # Debug information
        logging.info(f"User ID: {user_id}")
        logging.info(f"User repo found: {user_repo is not None}")
        if user_repo:
            logging.info(f"Repo path: {user_repo.get('repo_path')}")
        
        # Check if user_repo is valid
        if not user_repo:
            await message.answer("❌ Ошибка создания записи пользователя. Обратитесь к администратору.")
            return
        
        # Get repository path
        repo_path = Path(user_repo['repo_path'])
        
        # Remove old repository if exists
        if repo_path.exists():
            import shutil
            shutil.rmtree(repo_path)
            await message.answer("🗑️ Старый репозиторий удален")
        
        # Clone new repository with appropriate authentication
        await message.answer("📥 Клонируем новый репозиторий...")
        subprocess.run(['git', 'clone', repo_url_to_use, str(repo_path)], check=True, capture_output=True)
        
        # Configure Git credentials and VCS-specific settings
        await message.answer("🔐 Настраиваем Git credentials...")
        configure_git_credentials(str(repo_path), user_id)
        
        # Configure VCS-specific settings
        if repo_type == REPO_TYPES['GITLAB']:
            # For HTTPS repositories, setup credentials for LFS
            if repo_url.startswith('https://'):
                setup_gitlab_lfs_credentials(str(repo_path), repo_url, user_id)
            
            # Configure GitLab LFS (handles both SSH and HTTPS URLs properly)
            lfs_manager = GitLabLFSManager()
            lfs_manager.configure_gitlab_lfs(str(repo_path), repo_url)
            
            # Update user data with SSH key info
            user_repos = load_user_repos()
            for key, repo_data in user_repos.items():
                if str(repo_data.get('telegram_id')) == str(user_id):
                    user_repos[key]['repo_url'] = repo_url
                    user_repos[key]['repo_type'] = REPO_TYPES['GITLAB']
                    user_repos[key]['ssh_private_key_path'] = ssh_setup_result.get('private_key_path')
                    user_repos[key]['gitlab_host'] = ssh_setup_result.get('gitlab_host')
                    break
            save_user_repos(user_repos)
        else:
            # Update user data for GitHub/other repositories
            user_repos = load_user_repos()
            for key, repo_data in user_repos.items():
                if str(repo_data.get('telegram_id')) == str(user_id):
                    user_repos[key]['repo_url'] = repo_url
                    user_repos[key]['repo_type'] = repo_type
                    break
            save_user_repos(user_repos)
        
        # Update session to collect credentials
        user_sessions = globals().get('user_edit_sessions', {})
        user_sessions[user_id]['collect_git_username'] = True
        user_sessions[user_id]['repo_url'] = repo_url  # Store repo URL for later use
        user_sessions[user_id]['repo_type'] = repo_type  # Store repository type
        globals()['user_edit_sessions'] = user_sessions
        
        # Different messages based on repository type
        if repo_type == REPO_TYPES['GITLAB']:
            await message.answer(
                f"✅ Репозиторий клонирован через SSH!\n"
                f"URL: {repo_url}\n"
                f"Путь: {repo_path}\n\n"
                f"🔧 Теперь введите ваш GitLab username (без @):"
            )
        else:
            await message.answer(
                f"✅ Репозиторий клонирован!\n"
                f"URL: {repo_url}\n"
                f"Путь: {repo_path}\n\n"
                f"🔧 Теперь введите ваш GitHub username (без @):"
            )
        
    except subprocess.CalledProcessError as e:
        error_msg = e.stderr.decode() if e.stderr else str(e)
        await message.answer(f"❌ Ошибка при клонировании репозитория:\n{error_msg}")
    except Exception as e:
        await message.answer(f"❌ Произошла ошибка:\n{str(e)}")


async def continue_gitlab_setup_after_ssh(message, user_id, repo_url, ssh_setup_result):
    """Continue GitLab repository setup after SSH key confirmation"""
    try:
        # Check if user exists, if not - create basic user entry
        user_repo = get_user_repo(user_id)
        
        if not user_repo:
            # Create basic user entry for new user
            await message.answer("🆕 Обнаружен новый пользователь. Создаем базовую запись...")
            user_repo = create_basic_user_entry(user_id, message.from_user.username)
            
        # Get repository path
        repo_path = Path(user_repo['repo_path'])
        
        # Use SSH URL for cloning
        ssh_url = convert_https_to_ssh(repo_url)
        
        # Configure SSH for this operation
        configure_ssh_for_git_operation(ssh_setup_result['private_key_path'], str(repo_path))
        
        # Remove old repository if exists
        if repo_path.exists():
            import shutil
            shutil.rmtree(repo_path)
            await message.answer("🗑️ Старый репозиторий удален")
        
        # Clone new repository with SSH authentication
        await message.answer("📥 Клонируем новый репозиторий через SSH...")
        subprocess.run(['git', 'clone', ssh_url, str(repo_path)], check=True, capture_output=True)
        
        # Configure Git credentials and VCS-specific settings
        await message.answer("🔐 Настраиваем Git credentials...")
        configure_git_credentials(str(repo_path), user_id)
        
        # Configure GitLab LFS (handles both SSH and HTTPS URLs properly)
        lfs_manager = GitLabLFSManager()
        lfs_manager.configure_gitlab_lfs(str(repo_path), repo_url)
        
        # Update user data with SSH key info
        user_repos = load_user_repos()
        for key, repo_data in user_repos.items():
            if str(repo_data.get('telegram_id')) == str(user_id):
                user_repos[key]['repo_url'] = repo_url
                user_repos[key]['repo_type'] = REPO_TYPES['GITLAB']
                user_repos[key]['ssh_private_key_path'] = ssh_setup_result.get('private_key_path')
                # Extract host from repo_url instead of ssh_setup_result
                import re
                if repo_url.startswith('https://'):
                    host_match = re.match(r'https://([^/]+)/', repo_url)
                else:  # SSH format
                    host_match = re.match(r'git@([^:]+):', repo_url)
                if host_match:
                    user_repos[key]['gitlab_host'] = host_match.group(1)
                else:
                    user_repos[key]['gitlab_host'] = 'gitlab.com'  # fallback
                break
        save_user_repos(user_repos)
        
        # Update session to collect GitLab username
        user_sessions = globals().get('user_edit_sessions', {})
        user_sessions[user_id] = {
            'user_id': user_id,
            'collect_git_username': True,
            'repo_url': repo_url,
            'repo_type': REPO_TYPES['GITLAB']
        }
        globals()['user_edit_sessions'] = user_sessions
        
        await message.answer(
            f"✅ Репозиторий успешно клонирован через SSH!\n"
            f"URL: {repo_url}\n"
            f"Путь: {repo_path}\n\n"
            f"🔧 Теперь введите ваш GitLab username (без @):"
        )
        
    except subprocess.CalledProcessError as e:
        error_msg = e.stderr.decode() if e.stderr else str(e)
        await message.answer(f"❌ Ошибка при клонировании репозитория:\n{error_msg}")
    except Exception as e:
        await message.answer(f"❌ Произошла ошибка:\n{str(e)}")


async def setup_user_own_repository(message):
    """Allow user to setup their own repository"""
    user_id = message.from_user.id
    
    await message.answer(
        "Введите URL вашего репозитория:\n"
        "Например: https://github.com/username/repository\n\n"
        "⚠️ ВНИМАНИЕ: Это удалит ваш текущий репозиторий и все локальные изменения!"
    )
    
    # Set up session for user's own repository setup
    user_sessions = globals().get('user_edit_sessions', {})
    user_sessions[user_id] = {
        'user_id': user_id,
        'setup_own_repo': True  # Flag for user's own repository setup
    }
    globals()['user_edit_sessions'] = user_sessions


# Function perform_full_repo_setup removed for security reasons
# Admins should not configure repositories for other users
# Each user must configure their own repository with their credentials


# Function setup_user_repository removed for security reasons
# Admins should not configure repositories for other users
# Each user must configure their own repository with their credentials


async def save_user_changes(message):
    """Save all user changes to user_repos.json"""
    user_sessions = globals().get('user_edit_sessions', {})
    session = user_sessions.get(message.from_user.id)
    
    if not session:
        await message.answer("❌ Сессия редактирования не найдена.",
                           reply_markup=get_settings_keyboard(message.from_user.id))
        return
    
    try:
        # Load current user_repos
        user_repos = load_user_repos()
        
        # Update the user data
        target_key = session['user_key']
        if target_key in user_repos:
            user_repos[target_key] = session['user_info']
            
            # Save changes
            save_user_repos(user_repos)
            
            await message.answer("✅ Изменения успешно сохранены!",
                               reply_markup=get_settings_keyboard(message.from_user.id))
        else:
            await message.answer("❌ Пользователь не найден в базе данных.",
                               reply_markup=get_settings_keyboard(message.from_user.id))
            
    except Exception as e:
        await message.answer(f"❌ Ошибка при сохранении: {str(e)}",
                           reply_markup=get_settings_keyboard(message.from_user.id))
    
    # Clear session
    if message.from_user.id in user_sessions:
        del user_sessions[message.from_user.id]
        globals()['user_edit_sessions'] = user_sessions


def apply_user_git_config(user_id: int):
    """Apply saved Git configuration for user"""
    user_repos = load_user_repos()
    
    for key, repo_data in user_repos.items():
        if str(repo_data.get('telegram_id')) == str(user_id):
            repo_path = repo_data.get('repo_path')
            git_config = repo_data.get('git_config', {})
            
            if repo_path and os.path.exists(repo_path):
                # Apply each Git config setting
                for config_key, config_value in git_config.items():
                    try:
                        subprocess.run([
                            "git", "config", config_key, config_value
                        ], cwd=repo_path, check=True, capture_output=True)
                        logging.info(f"Applied Git config {config_key}={config_value} for user {user_id}")
                    except Exception as e:
                        logging.warning(f"Failed to apply Git config {config_key}: {e}")
            break


def save_git_config_to_user_data(user_id: int, repo_path: str):
    """Save current Git configuration to user data for persistence"""
    try:
        # Get current Git config
        git_config = {}
        config_items = [
            "lfs.url",
            "core.sshCommand"
        ]
        
        for config_key in config_items:
            try:
                result = subprocess.run([
                    "git", "config", "--get", config_key
                ], cwd=repo_path, capture_output=True, text=True)
                if result.returncode == 0 and result.stdout.strip():
                    git_config[config_key] = result.stdout.strip()
            except Exception:
                pass
        
        # Save to user_repos.json
        user_repos = load_user_repos()
        for key, repo_data in user_repos.items():
            if str(repo_data.get('telegram_id')) == str(user_id):
                user_repos[key]['git_config'] = git_config
                save_user_repos(user_repos)
                logging.info(f"Saved Git config for user {user_id}: {git_config}")
                break
                
    except Exception as e:
        logging.error(f"Failed to save Git config for user {user_id}: {e}")


if __name__ == "__main__":
    asyncio.run(main())