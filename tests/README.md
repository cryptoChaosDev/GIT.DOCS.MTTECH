# GitLab Integration Tests

This directory contains comprehensive tests for GitLab functionality and multi-VCS support in the Git Docs Bot.

## 📋 Test Coverage

### Core Functionality Tests
- ✅ Repository type detection (GitHub vs GitLab)
- ✅ URL validation and normalization
- ✅ GitLab token validation
- ✅ Project path extraction from URLs
- ✅ API client functionality
- ✅ Configuration management
- ✅ Data migration

### Integration Tests
- ✅ GitLab API integration
- ✅ Authentication system
- ✅ LFS operations
- ✅ User repository management

## 🚀 Running Tests

### In Docker Container
```bash
# Run all GitLab integration tests
docker-compose exec git-docs-bot /app/tests/run_gitlab_tests.sh

# Or run specific test file
docker-compose exec git-docs-bot python3 /app/tests/test_gitlab_integration.py
```

### Local Development
```bash
# Navigate to tests directory
cd tests

# Run tests directly
python3 test_gitlab_integration.py

# Run with verbose output
python3 -m pytest test_gitlab_integration.py -v
```

## 🧪 Test Categories

### 1. Repository Type Detection
Tests automatic detection of repository types from URLs:
- GitHub URLs (https://github.com/user/repo)
- GitLab URLs (https://gitlab.com/group/project)
- Self-hosted GitLab URLs
- Invalid/unknown URLs

### 2. URL Validation
Validates repository URLs according to platform-specific formats:
- Protocol validation (HTTPS/SSH)
- Path structure validation
- Domain validation
- Normalization tests

### 3. Authentication Tests
- GitLab token format validation
- Token length and character validation
- Invalid token rejection

### 4. API Integration Tests
- Project info retrieval
- Error handling
- Response parsing
- Mock API responses

### 5. Configuration Management
- User repository configuration
- VCS-specific settings
- Data migration
- Credential management

## 📊 Test Output

Sample test output:
```
🚀 Starting GitLab Integration Tests...
======================================
🔧 Checking environment...
✅ Environment checks passed
📦 Installing test dependencies...
🧪 Running GitLab Integration Tests...
--------------------------------------
Running GitLab Integration Tests...
==================================================
test_github_urls (__main__.TestRepositoryTypeDetection)
Test GitHub URL detection ... ok
test_gitlab_urls (__main__.TestRepositoryTypeDetection)
Test GitLab URL detection ... ok
test_valid_github_urls (__main__.TestRepositoryURLValidator)
Test valid GitHub URLs ... ok
...

----------------------------------------------------------------------
Ran 15 tests in 2.345s

OK
✅ All tests passed!
```

## 🛠 Test Development

### Adding New Tests
1. Add test methods to existing test classes
2. Use descriptive test method names
3. Include proper docstrings
4. Use subTests for parameterized testing

### Example Test Structure
```python
class TestNewFeature(unittest.TestCase):
    def setUp(self):
        # Setup test environment
        pass
    
    def test_feature_works(self):
        """Test that the feature works correctly"""
        # Arrange
        input_data = "test_input"
        
        # Act
        result = some_function(input_data)
        
        # Assert
        self.assertEqual(result, "expected_output")
    
    def tearDown(self):
        # Cleanup test environment
        pass
```

## 🎯 Continuous Integration

The tests are designed to:
- Run in Docker container environment
- Work with mocked external services
- Provide clear pass/fail indicators
- Generate detailed error messages
- Maintain backward compatibility

## 📈 Future Enhancements

Planned test additions:
- End-to-end workflow tests
- Performance benchmarks
- Security validation tests
- Multi-user scenario tests
- Edge case coverage

---
*Last updated: January 2026*