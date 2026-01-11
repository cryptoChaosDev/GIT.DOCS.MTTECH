import asyncio
import os
import logging
import logging.handlers
import time
import subprocess
import json
import re
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


def set_user_repo(user_id: int, repo_path: str, repo_url: str = None, username: str = None, telegram_username: str = None):
    """Store user repository mapping using composite key: telegram_id:git_username"""
    m = load_user_repos()
    
    # Create composite key
    if username:
        composite_key = f"{user_id}:{username}"
    else:
        # Fallback to just user_id if no username provided
        composite_key = str(user_id)
    
    m[composite_key] = {
        'telegram_id': user_id,
        'telegram_username': telegram_username,
        'git_username': username,
        'repo_path': str(repo_path),
        'repo_url': repo_url,
        'created_at': datetime.now().isoformat()
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
    for key, repo_info in m.items():
        if str(repo_info.get('telegram_id')) == str(user_id):
            return repo_info
    
    return None


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


def get_lfs_lock_info(doc_rel_path: str, cwd: Path = REPO_PATH):
    """Return lock info for a path according to `git lfs locks` output or None. cwd specifies repository root."""
    try:
        proc = subprocess.run(["git", "lfs", "locks"], cwd=str(cwd), check=True, capture_output=True, text=True, encoding='utf-8', errors='replace')
        out = proc.stdout or ""
        # Parse Git LFS locks output format: "path    owner    timestamp"
        for line in out.splitlines():
            if doc_rel_path in line:
                parts = line.strip().split()
                if len(parts) >= 2:
                    # First part is path, second is owner
                    path_part = parts[0]
                    owner_part = parts[1]
                    return {
                        "raw": line.strip(),
                        "path": path_part,
                        "owner": owner_part,
                        "timestamp": parts[2] if len(parts) > 2 else None
                    }
                else:
                    # Fallback to raw parsing
                    return {"raw": line.strip()}
    except subprocess.CalledProcessError:
        return None
    return None

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
    """Главное меню - основной экран бота"""
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
    
    keyboard = [
        ["📋 Документы"],
        ["🔄 Git операции"],
        ["⚙️ Настроить репозиторий"]  # New button for all users
    ]
    
    # Add locks button only for admins
    if is_admin:
        keyboard[1].append("🔒 Блокировки")

    # Only show settings if repository is not configured OR if user_id is None (backward compatibility)
    if not has_repo or user_id is None:
        keyboard.append(["⚙️ Настройки"])

    # Always show repository info and instructions
    keyboard.append(["ℹ️ О репозитории", "📖 Инструкции"])

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

async def setup_repo(message, state=None):
    await state.set_state(UserConfigStates.waiting_for_repo_url)
    await message.answer("Введите URL репозитория (например, https://github.com/user/repo):")

async def process_repo_url(message, state=None):
    repo_url = message.text.strip()
    await state.update_data(repo_url=repo_url)
    await state.set_state(UserConfigStates.waiting_for_username)
    await message.answer("Введите ваше имя пользователя GitHub:")

async def process_username(message, state=None):
    username = message.text.strip()
    await state.update_data(username=username)
    await state.set_state(UserConfigStates.waiting_for_password)
    await message.answer("Введите ваш токен доступа GitHub (Personal Access Token):")

async def process_password(message, state=None):
    password = message.text.strip()
    user_data = await state.get_data()
    
    # Store credentials in state for this user session
    await state.update_data(password=password)
    
    # Clone repository using credentials
    try:
        repo_url = user_data['repo_url']
        username = user_data['username']
        
        # Check if the repo already exists
        git_dir = REPO_PATH / ".git"
        if git_dir.exists():
            # If it's already a git repo, set the remote URL with credentials for this session
            repo_url_with_creds = "https://" + username + ":" + password + "@" + repo_url.replace("https://", "")
            subprocess.run(["git", "remote", "set-url", "origin", repo_url_with_creds], cwd=str(REPO_PATH), check=True, capture_output=True)
            
            # Pull latest changes
            try:
                subprocess.run(["git", "fetch"], cwd=str(REPO_PATH), check=True, capture_output=True)
                subprocess.run(["git", "pull"], cwd=str(REPO_PATH), check=True, capture_output=True)
            except subprocess.CalledProcessError:
                # If pull fails, continue anyway - might be due to no commits to pull
                pass
        else:
            # If not a git repo yet, we need to clone
            # Use the URL with credentials for cloning
            repo_url_with_creds = "https://" + username + ":" + password + "@" + repo_url.replace("https://", "")
            subprocess.run(["git", "clone", repo_url_with_creds, str(REPO_PATH)], check=True, capture_output=True)
        # Ensure git-lfs is available and initialized in the repo
        try:
            subprocess.run(["git", "lfs", "install"], cwd=str(REPO_PATH), check=True, capture_output=True)
            subprocess.run(["git", "lfs", "fetch"], cwd=str(REPO_PATH), check=True, capture_output=True)
        except subprocess.CalledProcessError:
            # If git-lfs commands fail, continue but inform user
            await message.answer("⚠️ Внимание: git-lfs не установлен или команда завершилась с ошибкой. Блокировки LFS могут не работать.")
        
        # Clone into per-user repo directory
        user_id = message.from_user.id
        repo_dir = USER_REPOS_DIR / str(user_id)
        if not repo_dir.exists():
            # Use credentials in clone URL for simplicity during setup
            repo_url_with_creds = "https://" + username + ":" + password + "@" + repo_url.replace("https://", "")
            subprocess.run(["git", "clone", repo_url_with_creds, str(repo_dir)], check=True, capture_output=True)
        # Preserve existing git config or set user-specific config
        # Only set if not already configured
        try:
            subprocess.run(["git", "config", "--get", "user.name"], cwd=str(repo_dir), check=True, capture_output=True)
        except subprocess.CalledProcessError:
            # User name not set, use the provided username
            subprocess.run(["git", "config", "user.name", username], cwd=str(repo_dir), check=True, capture_output=True)
        
        try:
            subprocess.run(["git", "config", "--get", "user.email"], cwd=str(repo_dir), check=True, capture_output=True)
        except subprocess.CalledProcessError:
            # Email not set, create from username
            email = f"{username}@users.noreply.github.com"
            subprocess.run(["git", "config", "user.email", email], cwd=str(repo_dir), check=True, capture_output=True)
        # Save user repo mapping
        telegram_username = getattr(message.from_user, 'username', None)
        set_user_repo(user_id, str(repo_dir), repo_url=repo_url, username=username, telegram_username=telegram_username)
        
        # After successful repository setup, list the documents
        await message.answer("✅ Репозиторий успешно настроен!")
        
        # List documents in the repository
        docs_dir = REPO_PATH / "docs"
        if not docs_dir.exists():
            docs_dir.mkdir(parents=True, exist_ok=True)
        
        docs = list(docs_dir.rglob("*.docx"))
        if not docs:
            await message.answer("📂 В репозитории нет документов .docx", reply_markup=get_main_keyboard())
        else:
            doc_names = [f.name for f in docs]
            
            # Get Git LFS locks for this repository
            git_lfs_locks = {}
            try:
                # Get current user's repo path
                user_repo_path = get_repo_for_user_id(message.from_user.id)
                if user_repo_path and user_repo_path.exists():
                    # Get all LFS locks
                    proc = subprocess.run(["git", "lfs", "locks"], cwd=str(user_repo_path), capture_output=True, text=True, encoding='utf-8', errors='replace')
                    if proc.returncode == 0:
                        for line in proc.stdout.splitlines():
                            if line.strip():
                                parts = line.strip().split()
                                if len(parts) >= 3:
                                    # Format: path owner ID
                                    path = parts[0]
                                    owner = parts[1]
                                    # Extract filename from path (docs/filename.docx -> filename.docx)
                                    if "/" in path:
                                        filename = path.split("/")[-1]
                                        git_lfs_locks[filename] = {"owner": owner, "id": parts[2]}
            except Exception:
                pass
            
            # Use only Git LFS locks
            combined_locks = git_lfs_locks
            
            keyboard = get_docs_keyboard(doc_names, locks=combined_locks)
            await message.answer("Доступные документы:", reply_markup=keyboard)
        
        # Log repository setup
        user_name = format_user_name(message)
        timestamp = format_datetime()
        log_message = f"🔧 Пользователь {user_name} настроил репозиторий: {repo_url} [{timestamp}]"
        await log_to_group(message, log_message)
        
        await state.clear()
    except Exception as e:
        await message.answer(f"❌ Ошибка настройки репозитория: {str(e)[:100]}")
        await state.clear()

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

    docs_dir = repo_root / "docs"
    if not docs_dir.exists():
        docs_dir.mkdir(parents=True, exist_ok=True)
    
    docs = list(docs_dir.rglob("*.docx"))
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
            try:
                remote_result = subprocess.run(["git", "remote", "get-url", "origin"], cwd=str(user_repo_path), capture_output=True, text=True, encoding='utf-8', errors='replace')
                if remote_result.returncode == 0:
                    remote_url = remote_result.stdout.strip()
                    logging.info(f"User {message.from_user.id} remote URL: {remote_url}")
                else:
                    logging.warning(f"User {message.from_user.id} failed to get remote URL: {remote_result.stderr}")
            except Exception as e:
                logging.error(f"Error checking remote URL for user {message.from_user.id}: {e}")
            
            proc = subprocess.run(["git", "lfs", "locks"], cwd=str(user_repo_path), capture_output=True, text=True, encoding='utf-8', errors='replace')
            logging.info(f"LFS locks command result for user {message.from_user.id}: returncode={proc.returncode}, stdout={proc.stdout[:200]}, stderr={proc.stderr[:200] if proc.stderr else 'none'}")
            
            if proc.returncode == 0:
                for line in proc.stdout.splitlines():
                    if line.strip():
                        parts = line.strip().split()
                        if len(parts) >= 3:
                            path = parts[0]
                            owner = parts[1]
                            if "/" in path:
                                filename = path.split("/")[-1]
                                git_lfs_locks[filename] = {"owner": owner, "id": parts[2]}
                                logging.info(f"Found lock: {filename} locked by {owner}")
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
    doc_path = repo_root / "docs" / doc_name
    
    if not doc_path.exists():
        # Document doesn't exist - return to document list
        logging.warning(f"Document not found: {doc_name} at path {doc_path}")
        await list_documents(message)
        return
    
    # Check if file is locked via Git LFS
    rel_path = str((Path('docs') / doc_name).as_posix())
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
        for user_id, repo_info in user_repos.items():
            if repo_info.get('git_username') == lock_owner_id:
                # Found user with matching GitHub username, get their Telegram username
                telegram_username = repo_info.get('telegram_username')
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
        doc_path = repo_root / 'docs' / doc_name
        if not doc_path.exists():
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
        doc_path = repo_root / 'docs' / doc_name
        if not doc_path.exists():
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
    
    doc_path = repo_root / "docs" / doc_name
    
    # Check LFS lock status (Git LFS is now the only lock mechanism)
    rel_path = str((Path('docs') / doc_name).as_posix())
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
        for user_id, repo_info in user_repos.items():
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
            commit_result = subprocess.run(["git", "commit", "-m", f"Update {doc_name} by {user_name}"], 
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
            rel_path = str((Path('docs') / doc_name).as_posix())
            lfs_lock_info = get_lfs_lock_info(rel_path, cwd=repo_root)
            
            # If file is locked by current user, unlock it temporarily for push
            if lfs_lock_info and lfs_lock_info.get('owner') == str(message.from_user.id):
                try:
                    subprocess.run(["git", "lfs", "unlock", rel_path], cwd=str(repo_root), check=True, capture_output=True)
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
                        subprocess.run(["git", "lfs", "lock", rel_path], cwd=str(repo_root), check=True, capture_output=True)
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
    doc_path = repo_root / "docs" / doc_name

    if not doc_path.exists():
        await message.answer(f"❌ Документ {doc_name} не найден!", reply_markup=get_document_keyboard(doc_name, is_locked=False))
        return

    # Check if document is locked via Git LFS
    rel = str((Path('docs') / doc_name).as_posix())
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
    # Try to unlock via git-lfs
    try:
        proc = subprocess.run(["git", "lfs", "unlock", rel], cwd=str(repo_root), check=True, capture_output=True, text=True, encoding='utf-8', errors='replace')
        # Return to document menu
        reply_markup = get_document_keyboard(doc_name, is_locked=False)
        await message.answer(f"🔓 Документ {doc_name} успешно разблокирован через git-lfs!\n{proc.stdout.strip()}", reply_markup=reply_markup)
        
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
                # Retry unlock
                proc2 = subprocess.run(["git", "lfs", "unlock", rel], cwd=str(REPO_PATH), check=True, capture_output=True, text=True, encoding='utf-8', errors='replace')
                # Return to document menu
                reply_markup = get_document_keyboard(doc_name, is_locked=False)
                await message.answer(f"🔓 Документ {doc_name} разблокирован после авто-коммита: {proc2.stdout.strip()}", reply_markup=reply_markup)
                return
            except subprocess.CalledProcessError as e2:
                # Report error
                err2 = (e2.stderr or e2.stdout or '').strip()
                # Return to document menu
                reply_markup = get_document_keyboard(doc_name, is_locked=True)
                await message.answer(f"⚠️ Ошибка при автокоммите/разблокировке: {err2[:200]}", reply_markup=reply_markup)
                return
        # Other errors: report error
        # Return to document menu
        reply_markup = get_document_keyboard(doc_name, is_locked=True)
        await message.answer(f"⚠️ Ошибка при разблокировке: {err[:200]}", reply_markup=reply_markup)
        # Return to document menu
        reply_markup = get_document_keyboard(doc_name, is_locked=True)
        await message.answer(f"⚠️ Ошибка при попытке разблокировать через git-lfs: {err[:200]}", reply_markup=reply_markup)

async def lock_document_by_name(message, doc_name: str):
    repo_root = get_repo_for_user_id(message.from_user.id)
    doc_path = repo_root / "docs" / doc_name
    
    if not doc_path.exists():
        await message.answer(f"❌ Документ {doc_name} не найден!", reply_markup=get_document_keyboard(doc_name, is_locked=False))
        return
    
    # Check if already locked via Git LFS
    rel = str((Path('docs') / doc_name).as_posix())
    repo_root = get_repo_for_user_id(message.from_user.id)
    try:
        lfs_lock_info = get_lfs_lock_info(rel, cwd=repo_root)
        if lfs_lock_info:
            lock_owner = lfs_lock_info.get('owner', 'unknown')
            lock_timestamp = format_datetime()
            
            # Load user repos to find Telegram username
            user_repos = load_user_repos()
            
            # Get Telegram username for lock owner
            telegram_username = None
            for user_id, repo_info in user_repos.items():
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
    rel = str((Path('docs') / doc_name).as_posix())
    try:
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
        # If git-lfs locking fails, present the error and fallback to local lock
        err_raw = e.stderr or e.stdout or b''
        try:
            if isinstance(err_raw, (bytes, bytearray)):
                err = err_raw.decode('utf-8', errors='replace').strip()
            else:
                err = str(err_raw).strip()
        except Exception:
            err = str(err_raw).strip()
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
        
    # Use git-lfs to show authoritative lock status when possible
    try:
        repo_root = await require_user_repo(message)
        if not repo_root:
            return
        proc = subprocess.run(["git", "lfs", "locks"], cwd=str(repo_root), check=True, capture_output=True, text=True, encoding='utf-8', errors='replace')
        out = (proc.stdout or "").strip()
        if not out:
            await message.answer("🔓 Нет активных блокировок (git-lfs)", reply_markup=get_locks_keyboard(user_id=message.from_user.id))
            return
        await message.answer(f"🔒 Активные блокировки (git-lfs):\n{out}", reply_markup=get_locks_keyboard(user_id=message.from_user.id))
        
        # Log lock status check
        user_name = format_user_name(message)
        timestamp = format_datetime()
        log_message = f"🔒 Администратор {user_name} проверил статус всех блокировок (git-lfs) [{timestamp}]"
        await log_to_group(message, log_message)
    except subprocess.CalledProcessError as e:
        await message.answer(f"❌ Ошибка получения статуса блокировок: {str(e)[:200]}", reply_markup=get_locks_keyboard(user_id=message.from_user.id))


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


async def repo_info(message):
    """Показывает информацию о репозитории. 
    Примечание: заголовок репозитория добавляется автоматически через PTBMessageAdapter.answer,
    поэтому здесь выводим только дополнительную информацию."""
    u = get_user_repo(message.from_user.id)
    if not u:
        # Заголовок не будет добавлен, так как репозиторий не настроен
        await message.answer("ℹ️ Репозиторий не настроен для вашего пользователя.", reply_markup=get_main_keyboard())
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
    await message.answer(info_text, reply_markup=get_main_keyboard())
    
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

    rel = str((Path('docs') / doc_name).as_posix())
    repo_root = get_repo_for_user_id(message.from_user.id)
    try:
        proc = subprocess.run(["git", "lfs", "unlock", "--force", rel], cwd=str(repo_root), check=True, capture_output=True, text=True, encoding='utf-8', errors='replace')
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

    # List documents
    docs_dir = repo_dir / "docs"
    if not docs_dir.exists():
        docs_dir.mkdir(parents=True, exist_ok=True)

    docs = list(docs_dir.rglob("*.docx"))
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
                "📋 Документы - работа с документами\n"
                "🔄 Git операции - обновление и коммиты\n"
                "🔒 Блокировки - управление блокировками\n"
                "⚙️ Настройки - настройка репозитория\n"
                "ℹ️ О репозитории - информация о репозитории\n\n"
                "Для начала работы настройте репозиторий в разделе ⚙️ Настройки.",
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
            if text == "📋 Документы":
                await list_documents(msg)
                return
            if text == "🔄 Git операции":
                await msg.answer("🔄 Git операции", reply_markup=get_git_operations_keyboard(user_id=msg.from_user.id))
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
            
# Handler for "⚙️ Настроить репозиторий" removed for security reasons
# Users should configure their own repositories
            
            if text == "💾 Сохранить изменения":
                await save_user_changes(msg)
                return
            
            if text == "◀️ Назад к списку":
                await show_users_management(msg)
                return
            if text == "ℹ️ О репозитории":
                await repo_info(msg)
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
            
            # Handle user's own repository setup
            if session and session.get('setup_own_repo'):
                repo_url = text.strip()
                if repo_url.startswith('https://'):
                    await perform_user_repo_setup(msg, session, repo_url)
                else:
                    await msg.answer("❌ Неверный формат URL. Используйте: https://github.com/username/repository")
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
    """Show instructions for repository setup and GPG key generation"""
    instructions = """📖 ИНСТРУКЦИИ ПО НАСТРОЙКЕ

📋 Как получить адрес удаленного репозитория:
1. Перейдите на GitHub
2. Откройте нужный репозиторий
3. Нажмите зеленую кнопку "Code"
4. Выберите HTTPS или SSH
5. Скопируйте полный URL
   Пример: https://github.com/username/repository.git

👤 Как узнать свой логин на GitHub:
1. Зайдите в свой профиль на GitHub
2. Ваш логин отображается в URL профиля
3. Или посмотрите в правом верхнем углу
4. Пример: если URL https://github.com/johnsmith, то логин - johnsmith

🔐 Как сгенерировать Personal Access Token (рекомендуется вместо пароля):
1. На GitHub перейдите в Settings → Developer settings → Personal access tokens
2. Нажмите "Generate new token"
3. Выберите classic token или fine-grained token
4. Установите срок действия
5. Выберите необходимые права (repo, workflow, etc.)
6. Сгенерируйте и СОХРАНИТЕ токен (показывается только один раз!)

🔑 Альтернатива - SSH ключи:
1. Откройте терминал/Git Bash
2. Выполните: ssh-keygen -t ed25519 -C "your_email@example.com"
3. Сохраните ключ в ~/.ssh/id_ed25519
4. Добавьте ключ в ssh-agent: ssh-add ~/.ssh/id_ed25519
5. Скопируйте публичный ключ: cat ~/.ssh/id_ed25519.pub
6. Добавьте ключ в GitHub: Settings → SSH and GPG keys → New SSH key

🛡️ Безопасность:
• Никогда не публикуйте свои токены или приватные ключи
• Используйте разные токены для разных целей
• Регулярно обновляйте токены
• Для работы с этим ботом достаточно прав на чтение/запись репозитория

💡 Советы:
• Для частного использования рекомендуется Personal Access Token
• Для автоматических систем лучше SSH ключи
• Храните токены в безопасном месте (менеджер паролей)
• При утере токена немедженно отзовите его в GitHub

Нужна помощь? Обратитесь к официальной документации GitHub!"""
    
    await message.answer(instructions, reply_markup=get_main_keyboard())

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
    
    for key, repo_info in user_repos.items():
        telegram_id = repo_info.get('telegram_id', 'unknown')
        telegram_username = repo_info.get('telegram_username', 'не задан')
        git_username = repo_info.get('git_username', 'не задан')
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
    
    for key, repo_info in user_repos.items():
        if str(repo_info.get('telegram_id')) == str(target_user_id):
            user_key = key
            user_info = repo_info
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
    """Execute user's own repository setup"""
    try:
        user_id = session['user_id']
        
        # Get user repository info
        user_repo = get_user_repo(user_id)
        if not user_repo:
            await message.answer("❌ Репозиторий не настроен. Обратитесь к администратору.")
            return
        
        # Get repository path
        repo_path = Path(user_repo['repo_path'])
        
        # Remove old repository if exists
        if repo_path.exists():
            import shutil
            shutil.rmtree(repo_path)
            await message.answer("🗑️ Старый репозиторий удален")
        
        # Clone new repository
        await message.answer("📥 Клонируем новый репозиторий...")
        subprocess.run(['git', 'clone', repo_url, str(repo_path)], check=True, capture_output=True)
        
        # Configure Git credentials for the new repository
        await message.answer("🔐 Настраиваем Git credentials...")
        configure_git_credentials(str(repo_path), user_id)
        
        # Update user data
        user_repos = load_user_repos()
        # Find user entry and update repo_url
        for key, repo_info in user_repos.items():
            if str(repo_info.get('telegram_id')) == str(user_id):
                user_repos[key]['repo_url'] = repo_url
                break
        
        save_user_repos(user_repos)
        
        # Clear session
        user_sessions = globals().get('user_edit_sessions', {})
        del user_sessions[message.from_user.id]
        globals()['user_edit_sessions'] = user_sessions
        
        await message.answer(
            f"✅ Ваш репозиторий успешно настроен!\n"
            f"URL: {repo_url}\n"
            f"Путь: {repo_path}\n\n"
            f"Теперь вы будете видеть документы из нового репозитория."
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


if __name__ == "__main__":
    asyncio.run(main())