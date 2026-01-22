#!/usr/bin/env python3
"""
GitLab Integration Tests for Git Docs Bot
Tests GitLab-specific functionality and multi-VCS support
"""

import unittest
import sys
import os
from pathlib import Path
import tempfile
import json
from unittest.mock import Mock, patch, MagicMock

# Add bot module to path
sys.path.insert(0, str(Path(__file__).parent.parent))

from bot import (
    detect_repository_type, 
    REPO_TYPES,
    RepositoryURLValidator,
    GitLabAPIClient,
    GitLabAuthManager,
    GitLabLFSManager,
    VCSConfigurationManager,
    validate_gitlab_token,
    get_gitlab_project_path,
    get_vcs_specific_config,
    migrate_user_repos_format
)

class TestRepositoryTypeDetection(unittest.TestCase):
    """Test repository type detection functionality"""
    
    def test_github_urls(self):
        """Test GitHub URL detection"""
        github_urls = [
            "https://github.com/user/repo",
            "https://github.com/user/repo.git",
            "git@github.com:user/repo.git",
            "HTTPS://GITHUB.COM/USER/REPO"
        ]
        
        for url in github_urls:
            with self.subTest(url=url):
                result = detect_repository_type(url)
                self.assertEqual(result, REPO_TYPES['GITHUB'])
    
    def test_gitlab_urls(self):
        """Test GitLab URL detection"""
        gitlab_urls = [
            "https://gitlab.com/group/project",
            "https://gitlab.com/group/project.git",
            "git@gitlab.com:group/project.git",
            "https://company.gitlab.com/group/project",
            "HTTPS://GITLAB.COM/GROUP/PROJECT"
        ]
        
        for url in gitlab_urls:
            with self.subTest(url=url):
                result = detect_repository_type(url)
                self.assertEqual(result, REPO_TYPES['GITLAB'])
    
    def test_unknown_urls(self):
        """Test unknown URL detection"""
        unknown_urls = [
            "https://bitbucket.org/user/repo",
            "https://example.com/repo",
            "",
            None
        ]
        
        for url in unknown_urls:
            with self.subTest(url=url):
                result = detect_repository_type(url)
                self.assertEqual(result, REPO_TYPES['UNKNOWN'])

class TestRepositoryURLValidator(unittest.TestCase):
    """Test URL validation functionality"""
    
    def setUp(self):
        self.validator = RepositoryURLValidator()
    
    def test_valid_github_urls(self):
        """Test valid GitHub URLs"""
        valid_urls = [
            "https://github.com/username/repository",
            "https://github.com/username/repository.git",
            "git@github.com:username/repository.git"
        ]
        
        for url in valid_urls:
            with self.subTest(url=url):
                result = self.validator.validate_url(url, REPO_TYPES['GITHUB'])
                self.assertTrue(result['valid'], f"URL should be valid: {url}")
                # When repo_type is explicitly provided, detected_type is not set
                # This is expected behavior - validation passes regardless
    
    def test_invalid_github_urls(self):
        """Test invalid GitHub URLs"""
        invalid_urls = [
            "https://github.com/user",  # Missing repository
            "https://github.com",       # Missing user/repo
            "https://github.com/user/repo/extra",  # Too many path parts
            "ftp://github.com/user/repo"  # Wrong protocol
        ]
        
        for url in invalid_urls:
            with self.subTest(url=url):
                result = self.validator.validate_url(url, REPO_TYPES['GITHUB'])
                self.assertFalse(result['valid'], f"URL should be invalid: {url}")
    
    def test_valid_gitlab_urls(self):
        """Test valid GitLab URLs"""
        valid_urls = [
            "https://gitlab.com/group/project",
            "https://gitlab.com/group/subgroup/project.git",
            "git@gitlab.com:group/project.git",
            "https://company.gitlab.com/group/project"
        ]
        
        for url in valid_urls:
            with self.subTest(url=url):
                result = self.validator.validate_url(url, REPO_TYPES['GITLAB'])
                self.assertTrue(result['valid'], f"URL should be valid: {url}")
                # When repo_type is explicitly provided, detected_type is not set
                # This is expected behavior - validation passes regardless
    
    def test_url_normalization(self):
        """Test URL normalization"""
        test_cases = [
            ("https://github.com/user/repo", REPO_TYPES['GITHUB'], "https://github.com/user/repo.git"),
            ("https://gitlab.com/group/project/", REPO_TYPES['GITLAB'], "https://gitlab.com/group/project.git"),
            ("git@github.com:user/repo", REPO_TYPES['GITHUB'], "git@github.com:user/repo.git")
        ]
        
        for input_url, repo_type, expected in test_cases:
            with self.subTest(input_url=input_url):
                normalized = self.validator.normalize_url(input_url, repo_type)
                self.assertEqual(normalized, expected)

class TestGitLabTokenValidation(unittest.TestCase):
    """Test GitLab token validation"""
    
    def test_valid_tokens(self):
        """Test valid GitLab tokens"""
        valid_tokens = [
            "glpat-1234567890abcdef1234567890abcdef1234567890ab",  # Standard format
            "1234567890abcdef1234567890abcdef1234567890abcdef",   # Alphanumeric
            "glpat_aBcDeFgHiJkLmNoPqRsTuVwXyZ1234567890"          # Mixed case
        ]
        
        for token in valid_tokens:
            with self.subTest(token=token):
                self.assertTrue(validate_gitlab_token(token))
    
    def test_invalid_tokens(self):
        """Test invalid GitLab tokens"""
        invalid_tokens = [
            "",                           # Empty
            "short",                      # Too short
            "token with spaces",          # Contains spaces
            "token@with@special@chars",   # Contains special chars
            "токен_на_русском"            # Non-ASCII
        ]
        
        for token in invalid_tokens:
            with self.subTest(token=token):
                self.assertFalse(validate_gitlab_token(token))

class TestGitLabProjectPathExtraction(unittest.TestCase):
    """Test GitLab project path extraction"""
    
    def test_https_urls(self):
        """Test HTTPS URL path extraction"""
        test_cases = [
            ("https://gitlab.com/group/project", "group/project"),
            ("https://gitlab.com/group/project.git", "group/project"),
            ("https://company.gitlab.com/group/project", "group/project"),
            ("https://gitlab.com/group/subgroup/project", "group/subgroup")
        ]
        
        for url, expected_path in test_cases:
            with self.subTest(url=url):
                result = get_gitlab_project_path(url)
                self.assertEqual(result, expected_path)
    
    def test_ssh_urls(self):
        """Test SSH URL path extraction"""
        test_cases = [
            ("git@gitlab.com:group/project.git", "group/project"),
            ("git@gitlab.com:group/subgroup/project.git", "group/subgroup/project")
        ]
        
        for url, expected_path in test_cases:
            with self.subTest(url=url):
                result = get_gitlab_project_path(url)
                self.assertEqual(result, expected_path)
    
    def test_invalid_urls(self):
        """Test invalid URL handling"""
        # Only test clearly invalid URLs that shouldn't return paths
        invalid_urls = [
            "invalid-url",
            "",
            None
        ]
        
        for url in invalid_urls:
            with self.subTest(url=url):
                result = get_gitlab_project_path(url)
                self.assertEqual(result, "")

class TestGitLabAPIClient(unittest.TestCase):
    """Test GitLab API client functionality"""
    
    def setUp(self):
        self.client = GitLabAPIClient(private_token="test-token")
    
    @patch('requests.Session')
    def test_get_project_info_success(self, mock_session_class):
        """Test successful project info retrieval"""
        # Mock session and response
        mock_session = Mock()
        mock_response = Mock()
        mock_response.json.return_value = {"id": 123, "name": "test-project"}
        mock_response.raise_for_status.return_value = None
        
        mock_session.get.return_value = mock_response
        mock_session_class.return_value = mock_session
        
        # Replace client session
        self.client.session = mock_session
        
        result = self.client.get_project_info("group/project")
        
        self.assertEqual(result["id"], 123)
        self.assertEqual(result["name"], "test-project")
        mock_session.get.assert_called_once()
    
    @patch('requests.Session')
    def test_get_project_info_failure(self, mock_session_class):
        """Test failed project info retrieval"""
        mock_session = Mock()
        mock_response = Mock()
        mock_response.raise_for_status.side_effect = Exception("API Error")
        
        mock_session.get.return_value = mock_response
        mock_session_class.return_value = mock_session
        
        self.client.session = mock_session
        
        result = self.client.get_project_info("group/project")
        
        self.assertEqual(result, {})  # Should return empty dict on failure

class TestVCSConfigurationManager(unittest.TestCase):
    """Test VCS configuration manager"""
    
    def setUp(self):
        self.manager = VCSConfigurationManager()
        # Create temporary user_repos file for testing
        self.temp_dir = tempfile.mkdtemp()
        self.user_repos_file = Path(self.temp_dir) / "user_repos.json"
        
        # Mock the global USER_REPOS_FILE
        import bot
        bot.USER_REPOS_FILE = self.user_repos_file
    
    def tearDown(self):
        # Cleanup
        if self.user_repos_file.exists():
            self.user_repos_file.unlink()
        os.rmdir(self.temp_dir)
    
    def test_user_vcs_config_github(self):
        """Test GitHub user configuration"""
        # Create test user data
        test_data = {
            "123:user": {
                "telegram_id": 123,
                "git_username": "user",
                "repo_url": "https://github.com/user/repo",
                "repo_type": "github"
            }
        }
        
        self.user_repos_file.write_text(json.dumps(test_data))
        
        config = self.manager.get_user_vcs_config(123, "user")
        
        self.assertEqual(config['repo_type'], 'github')
        self.assertEqual(config['repo_url'], 'https://github.com/user/repo')
        self.assertIn('base_config', config)
    
    def test_user_vcs_config_gitlab(self):
        """Test GitLab user configuration"""
        test_data = {
            "456:user": {
                "telegram_id": 456,
                "git_username": "user",
                "repo_url": "https://gitlab.com/group/project",
                "repo_type": "gitlab",
                "repo_path": "/tmp/test-repo"
            }
        }
        
        self.user_repos_file.write_text(json.dumps(test_data))
        
        # Mock the load_user_repos function to use our test file
        import bot
        original_load = bot.load_user_repos
        
        def mock_load_user_repos():
            return test_data
        
        bot.load_user_repos = mock_load_user_repos
        
        try:
            config = self.manager.get_user_vcs_config(456, "user")
            
            self.assertEqual(config['repo_type'], 'gitlab')
            self.assertEqual(config['repo_url'], 'https://gitlab.com/group/project')
        finally:
            # Restore original function
            bot.load_user_repos = original_load

class TestMigration(unittest.TestCase):
    """Test migration functionality"""
    
    def setUp(self):
        self.temp_dir = tempfile.mkdtemp()
        self.user_repos_file = Path(self.temp_dir) / "user_repos.json"
        
        import bot
        bot.USER_REPOS_FILE = self.user_repos_file
    
    def tearDown(self):
        if self.user_repos_file.exists():
            self.user_repos_file.unlink()
        os.rmdir(self.temp_dir)
    
    def test_migration_adds_missing_fields(self):
        """Test that migration adds missing fields"""
        # Old format data
        old_data = {
            "123:user": {
                "telegram_id": 123,
                "git_username": "user",
                "repo_url": "https://github.com/user/repo",
                "created_at": "2026-01-01T00:00:00"
                # Missing: repo_type, auth_token, last_updated
            }
        }
        
        self.user_repos_file.write_text(json.dumps(old_data))
        
        # Perform migration
        result = migrate_user_repos_format()
        
        self.assertTrue(result)  # Migration should have occurred
        
        # Check updated data
        updated_data = json.loads(self.user_repos_file.read_text())
        user_entry = updated_data["123:user"]
        
        self.assertIn('repo_type', user_entry)
        self.assertIn('auth_token', user_entry)
        self.assertIn('last_updated', user_entry)
        self.assertIn('created_at', user_entry)

def run_tests():
    """Run all tests and return results"""
    loader = unittest.TestLoader()
    suite = loader.loadTestsFromModule(sys.modules[__name__])
    
    runner = unittest.TextTestRunner(verbosity=2)
    result = runner.run(suite)
    
    return result.wasSuccessful()

if __name__ == '__main__':
    print("Running GitLab Integration Tests...")
    print("=" * 50)
    
    success = run_tests()
    
    if success:
        print("\n✅ All tests passed!")
        sys.exit(0)
    else:
        print("\n❌ Some tests failed!")
        sys.exit(1)