#!/usr/bin/env python3
"""
Test script for GitLab repository connection
"""

import sys
from pathlib import Path

# Add bot module to path
sys.path.insert(0, str(Path(__file__).parent))

from bot import (
    detect_repository_type, 
    REPO_TYPES,
    RepositoryURLValidator,
    GitLabAPIClient,
    validate_gitlab_token
)

def test_your_repository():
    """Test your specific GitLab repository"""
    
    # Your repository URL
    repo_url = "https://gitlab.mosmetro.tech/ctr/ascup/analytics/documents"
    
    print("🔍 Testing repository URL analysis...")
    print(f"Repository URL: {repo_url}")
    
    # Test repository type detection
    repo_type = detect_repository_type(repo_url)
    print(f"Detected repository type: {repo_type}")
    
    # Test URL validation
    validator = RepositoryURLValidator()
    validation_result = validator.validate_url(repo_url)
    
    print(f"\n📋 URL Validation Result:")
    print(f"Valid: {validation_result['valid']}")
    print(f"Normalized URL: {validation_result['normalized_url']}")
    print(f"Detected type: {validation_result.get('detected_type', 'Not detected')}")
    
    if validation_result['errors']:
        print("Errors:")
        for error in validation_result['errors']:
            print(f"  • {error}")
    
    if validation_result['warnings']:
        print("Warnings:")
        for warning in validation_result['warnings']:
            print(f"  • {warning}")
    
    # Test GitLab token validation
    token = "CdWphdjS3Q3jfrNXBuj3"
    is_valid_token = validate_gitlab_token(token)
    print(f"\n🔐 Token validation: {'✅ Valid' if is_valid_token else '❌ Invalid'}")
    
    # Test GitLab API connection
    if validation_result['valid'] and is_valid_token:
        print(f"\n🌐 Testing GitLab API connection...")
        client = GitLabAPIClient(private_token=token, api_url="https://gitlab.mosmetro.tech/api/v4")
        
        # Extract project path
        project_path = "ctr/ascup/analytics/documents"
        print(f"Project path: {project_path}")
        
        # Test project info retrieval
        project_info = client.get_project_info(project_path)
        
        if project_info:
            print("✅ API Connection successful!")
            print(f"Project ID: {project_info.get('id')}")
            print(f"Project name: {project_info.get('name')}")
            print(f"Full path: {project_info.get('path_with_namespace')}")
        else:
            print("❌ Failed to retrieve project info")
    
    print(f"\n🎯 Summary:")
    print(f"Repository type detection: {'✅ Working' if repo_type == REPO_TYPES['GITLAB'] else '❌ Failed'}")
    print(f"URL validation: {'✅ Working' if validation_result['valid'] else '❌ Failed'}")
    print(f"Token validation: {'✅ Working' if is_valid_token else '❌ Failed'}")
    print(f"API connection: {'✅ Working' if 'project_info' in locals() and project_info else '❌ Failed'}")

if __name__ == "__main__":
    test_your_repository()