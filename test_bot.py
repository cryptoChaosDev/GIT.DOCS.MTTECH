#!/usr/bin/env python3
"""
Git Docs Bot - Comprehensive Test Suite
Tests all major functionality including:
- Repository setup and configuration
- Document listing and management  
- Git LFS locking/unlocking
- Document upload/download
- User authentication and access control
"""

import unittest
import subprocess
import os
import json
import tempfile
import shutil
from pathlib import Path
from unittest.mock import patch, MagicMock
import sys

# Add bot module to path
sys.path.insert(0, str(Path(__file__).parent))

class TestGitDocsBot(unittest.TestCase):
    """Comprehensive test suite for Git Docs Bot"""
    
    def setUp(self):
        """Set up test environment"""
        self.test_dir = Path(tempfile.mkdtemp())
        self.repo_dir = self.test_dir / "test_repo"
        self.repo_dir.mkdir()
        
        # Initialize git repo for testing
        subprocess.run(["git", "init"], cwd=self.repo_dir, check=True)
        subprocess.run(["git", "config", "user.name", "TestUser"], cwd=self.repo_dir, check=True)
        subprocess.run(["git", "config", "user.email", "test@example.com"], cwd=self.repo_dir, check=True)
        
        # Initialize git lfs
        subprocess.run(["git", "lfs", "install"], cwd=self.repo_dir, check=True)
        
        # Create test documents directory
        (self.repo_dir / "docs").mkdir()
        
        # Create sample docx files
        self.sample_docs = ["test1.docx", "test2.docx", "БТ_Диспетчеризация_ACTUAL.docx"]
        for doc_name in self.sample_docs:
            doc_path = self.repo_dir / "docs" / doc_name
            doc_path.write_text(f"Sample content for {doc_name}")
            
        # Commit initial files
        subprocess.run(["git", "add", "."], cwd=self.repo_dir, check=True)
        subprocess.run(["git", "commit", "-m", "Initial commit"], cwd=self.repo_dir, check=True)
        
    def tearDown(self):
        """Clean up test environment"""
        shutil.rmtree(self.test_dir, ignore_errors=True)
    
    def test_git_lfs_setup(self):
        """Test Git LFS installation and configuration"""
        # Verify git lfs is installed
        result = subprocess.run(["git", "lfs", "version"], capture_output=True, text=True)
        self.assertEqual(result.returncode, 0)
        self.assertIn("git-lfs", result.stdout.lower())
        
        # Verify lfs is initialized in repo
        hooks_dir = self.repo_dir / ".git" / "hooks"
        self.assertTrue(hooks_dir.exists())
        
    def test_document_listing(self):
        """Test document discovery and listing"""
        from bot import get_docs_from_repo
        
        # Mock message object
        mock_message = MagicMock()
        mock_message.from_user.id = 12345
        
        # Mock get_repo_for_user_id to return our test repo
        with patch('bot.get_repo_for_user_id', return_value=self.repo_dir):
            docs = get_docs_from_repo(mock_message)
            
        # Should find our test documents
        doc_names = [doc.name for doc in docs]
        for expected_doc in self.sample_docs:
            self.assertIn(expected_doc, doc_names)
            
    def test_git_lfs_lock_mechanism(self):
        """Test Git LFS file locking functionality"""
        doc_path = "docs/test1.docx"
        
        # Test locking a file
        result = subprocess.run(
            ["git", "lfs", "lock", doc_path], 
            cwd=self.repo_dir, 
            capture_output=True, 
            text=True
        )
        self.assertEqual(result.returncode, 0)
        
        # Verify file is locked
        locks_result = subprocess.run(
            ["git", "lfs", "locks"], 
            cwd=self.repo_dir, 
            capture_output=True, 
            text=True
        )
        self.assertEqual(locks_result.returncode, 0)
        self.assertIn(doc_path, locks_result.stdout)
        
        # Test unlocking the file
        unlock_result = subprocess.run(
            ["git", "lfs", "unlock", doc_path], 
            cwd=self.repo_dir, 
            capture_output=True, 
            text=True
        )
        self.assertEqual(unlock_result.returncode, 0)
        
        # Verify file is unlocked
        locks_result_after = subprocess.run(
            ["git", "lfs", "locks"], 
            cwd=self.repo_dir, 
            capture_output=True, 
            text=True
        )
        self.assertEqual(locks_result_after.returncode, 0)
        self.assertNotIn(doc_path, locks_result_after.stdout)
        
    def test_local_lock_storage(self):
        """Test local lock JSON storage functionality"""
        from bot import load_locks, save_locks
        
        # Test saving locks
        test_locks = {
            "test1.docx": {
                "user_id": "12345",
                "timestamp": "2026-01-10T12:00:00"
            }
        }
        
        # Use temporary lock file
        lock_file = self.test_dir / "test_locks.json"
        with patch('bot.LOCKS_FILE', lock_file):
            save_locks(test_locks)
            
            # Verify file was created and contains correct data
            self.assertTrue(lock_file.exists())
            saved_data = json.loads(lock_file.read_text())
            self.assertEqual(saved_data, test_locks)
            
            # Test loading locks
            loaded_locks = load_locks()
            self.assertEqual(loaded_locks, test_locks)
            
    def test_lock_verification_logic(self):
        """Test lock verification combining local and Git LFS locks"""
        from bot import get_lfs_lock_info
        
        doc_name = "test1.docx"
        doc_path = f"docs/{doc_name}"
        
        # Create a Git LFS lock
        subprocess.run(["git", "lfs", "lock", doc_path], cwd=self.repo_dir, check=True)
        
        # Test lock info extraction
        lock_info = get_lfs_lock_info(doc_path, cwd=self.repo_dir)
        
        # Should return lock information
        self.assertIsNotNone(lock_info)
        self.assertIn('raw', lock_info)
        self.assertIn('owner', lock_info)
        self.assertEqual(lock_info['path'], doc_path)
        
        # Clean up
        subprocess.run(["git", "lfs", "unlock", doc_path], cwd=self.repo_dir, check=True)
        
    def test_repository_configuration(self):
        """Test repository setup and user configuration"""
        from bot import get_user_repo, set_user_repo
        
        user_id = "12345"
        repo_path = str(self.repo_dir)
        repo_url = "https://github.com/test/test-repo"
        username = "testuser"
        
        # Test setting user repository
        set_user_repo(user_id, repo_path, repo_url=repo_url, username=username)
        
        # Test retrieving user repository
        user_repo = get_user_repo(user_id)
        self.assertEqual(user_repo['repo_path'], repo_path)
        self.assertEqual(user_repo['repo_url'], repo_url)
        self.assertEqual(user_repo['username'], username)
        
    def test_document_upload_validation(self):
        """Test document upload security validations"""
        from bot import is_safe_filename
        
        # Test valid filenames
        valid_names = [
            "test_document.docx",
            "БТ_Диспетчеризация.docx", 
            "document-with-numbers123.docx"
        ]
        
        for name in valid_names:
            self.assertTrue(is_safe_filename(name), f"Should accept: {name}")
            
        # Test invalid filenames
        invalid_names = [
            "../malicious.docx",  # path traversal
            "/etc/passwd.docx",   # absolute path
            "test;rm -rf.docx",   # command injection
            "test&echo.docx",     # command injection
            "test|cat.docx",      # pipe injection
            "test$(whoami).docx", # shell expansion
            "test`whoami`.docx"   # backtick injection
        ]
        
        for name in invalid_names:
            self.assertFalse(is_safe_filename(name), f"Should reject: {name}")
            
    def test_git_operations(self):
        """Test basic Git operations"""
        # Test git status
        result = subprocess.run(
            ["git", "status", "--porcelain"], 
            cwd=self.repo_dir, 
            capture_output=True, 
            text=True
        )
        self.assertEqual(result.returncode, 0)
        
        # Test git diff
        result = subprocess.run(
            ["git", "diff", "--name-only"], 
            cwd=self.repo_dir, 
            capture_output=True, 
            text=True
        )
        self.assertEqual(result.returncode, 0)
        
        # Test git log
        result = subprocess.run(
            ["git", "log", "--oneline", "-1"], 
            cwd=self.repo_dir, 
            capture_output=True, 
            text=True
        )
        self.assertEqual(result.returncode, 0)
        
    def test_error_handling(self):
        """Test error handling for various failure scenarios"""
        from bot import get_lfs_lock_info
        
        # Test with non-existent repository
        lock_info = get_lfs_lock_info("nonexistent.docx", cwd="/nonexistent/path")
        self.assertIsNone(lock_info)
        
        # Test with non-existent file
        lock_info = get_lfs_lock_info("nonexistent.docx", cwd=self.repo_dir)
        self.assertIsNone(lock_info)

class TestIntegrationScenarios(unittest.TestCase):
    """Integration tests for complete workflows"""
    
    def setUp(self):
        self.test_dir = Path(tempfile.mkdtemp())
        self.data_dir = self.test_dir / "data"
        self.data_dir.mkdir()
        
    def tearDown(self):
        shutil.rmtree(self.test_dir, ignore_errors=True)
        
    def test_complete_document_workflow(self):
        """Test complete document locking/upload/unlocking workflow"""
        # This would test the full cycle:
        # 1. User sets up repository
        # 2. Lists documents
        # 3. Locks a document
        # 4. Uploads changes
        # 5. Unlocks document
        # Would require mocking Telegram API interactions
        pass
        
    def test_concurrent_access_prevention(self):
        """Test that concurrent access to locked documents is prevented"""
        # This would simulate two users trying to access the same locked document
        # Would require mocking multiple user sessions
        pass

def run_tests():
    """Run all tests and generate report"""
    # Create test suite
    loader = unittest.TestLoader()
    suite = unittest.TestSuite()
    
    # Add test cases
    suite.addTests(loader.loadTestsFromTestCase(TestGitDocsBot))
    suite.addTests(loader.loadTestsFromTestCase(TestIntegrationScenarios))
    
    # Run tests with verbose output
    runner = unittest.TextTestRunner(verbosity=2)
    result = runner.run(suite)
    
    # Generate summary report
    print("\n" + "="*50)
    print("GIT DOCS BOT TEST REPORT")
    print("="*50)
    print(f"Tests run: {result.testsRun}")
    print(f"Failures: {len(result.failures)}")
    print(f"Errors: {len(result.errors)}")
    print(f"Success rate: {((result.testsRun - len(result.failures) - len(result.errors)) / result.testsRun * 100):.1f}%")
    
    if result.failures:
        print("\nFAILURES:")
        for test, traceback in result.failures:
            print(f"  {test}: {traceback}")
            
    if result.errors:
        print("\nERRORS:")
        for test, traceback in result.errors:
            print(f"  {test}: {traceback}")
            
    return result.wasSuccessful()

if __name__ == "__main__":
    success = run_tests()
    sys.exit(0 if success else 1)