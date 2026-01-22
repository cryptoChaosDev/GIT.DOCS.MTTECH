#!/usr/bin/env python3
"""
Test SSH key generation and GitLab SSH setup
"""

import sys
from pathlib import Path

# Add bot module to path
sys.path.insert(0, str(Path(__file__).parent))

from bot import (
    SSHKeyManager,
    setup_gitlab_ssh_access,
    convert_https_to_ssh
)

def test_ssh_key_generation():
    """Test SSH key generation"""
    print("🔐 Testing SSH Key Generation...")
    
    ssh_manager = SSHKeyManager()
    
    # Test key generation
    user_id = 12345
    ssh_keys = ssh_manager.generate_ssh_key_pair(user_id, "test@example.com")
    
    if ssh_keys:
        print("✅ SSH key pair generated successfully")
        print(f"Private key path: {ssh_keys['private_key_path']}")
        print(f"Public key path: {ssh_keys['public_key_path']}")
        print(f"Public key preview: {ssh_keys['public_key'][:50]}...")
        
        # Test getting existing keys
        existing_keys = ssh_manager.get_user_ssh_key(user_id)
        if existing_keys:
            print("✅ Existing keys retrieved successfully")
        else:
            print("❌ Failed to retrieve existing keys")
            
        # Test key formatting for GitLab
        formatted_key = ssh_manager.format_public_key_for_gitlab(
            ssh_keys['public_key'], user_id
        )
        print(f"Formatted key preview: {formatted_key[:60]}...")
        
        # Cleanup
        if ssh_manager.delete_user_ssh_keys(user_id):
            print("✅ SSH keys cleaned up")
        else:
            print("❌ Failed to cleanup SSH keys")
    else:
        print("❌ Failed to generate SSH key pair")

def test_url_conversion():
    """Test HTTPS to SSH URL conversion"""
    print("\n🔄 Testing URL Conversion...")
    
    test_urls = [
        "https://gitlab.mosmetro.tech/ctr/ascup/analytics/documents",
        "https://gitlab.mosmetro.tech/ctr/ascup/analytics/documents/-/tree/master",
        "https://gitlab.example.com/group/project",
        "https://gitlab.example.com/group/subgroup/project.git"
    ]
    
    for url in test_urls:
        ssh_url = convert_https_to_ssh(url)
        print(f"HTTPS: {url}")
        print(f"SSH:   {ssh_url}")
        print()

def test_gitlab_ssh_setup():
    """Test complete GitLab SSH setup"""
    print("🔧 Testing GitLab SSH Setup...")
    
    repo_url = "https://gitlab.mosmetro.tech/ctr/ascup/analytics/documents"
    user_id = 99999
    
    result = setup_gitlab_ssh_access(user_id, repo_url)
    
    if result['success']:
        print("✅ GitLab SSH setup successful")
        print(f"GitLab host: {result['gitlab_host']}")
        print("Instructions generated:")
        print(result['instructions'][:200] + "...")
        
        # Test cleanup
        ssh_manager = SSHKeyManager()
        if ssh_manager.delete_user_ssh_keys(user_id):
            print("✅ Test keys cleaned up")
    else:
        print(f"❌ GitLab SSH setup failed: {result['error']}")

if __name__ == "__main__":
    print("🧪 SSH Integration Tests\n" + "="*50)
    
    test_ssh_key_generation()
    test_url_conversion()
    test_gitlab_ssh_setup()
    
    print("\n" + "="*50)
    print("✅ All SSH tests completed!")