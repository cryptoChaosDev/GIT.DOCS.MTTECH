#!/usr/bin/env python3
"""
Quick fix for LFS configuration in SSH repositories
"""

import subprocess
import os
import re

def fix_lfs_for_ssh_repo(repo_path):
    """Fix LFS configuration for SSH repository"""
    print(f"Fixing LFS configuration in: {repo_path}")
    
    if not os.path.exists(repo_path):
        print(f"Repository path doesn't exist: {repo_path}")
        return False
    
    # Check if it's a git repository
    git_dir = os.path.join(repo_path, '.git')
    if not os.path.exists(git_dir):
        print(f"Not a git repository: {repo_path}")
        return False
    
    # Get remote URL
    try:
        result = subprocess.run(['git', 'remote', 'get-url', 'origin'], 
                              cwd=repo_path, capture_output=True, text=True)
        if result.returncode == 0:
            remote_url = result.stdout.strip()
            print(f"Current remote URL: {remote_url}")
        else:
            print(f"Failed to get remote URL: {result.stderr}")
            return False
    except Exception as e:
        print(f"Error getting remote URL: {e}")
        return False
    
    # Check if it's an SSH URL
    if remote_url.startswith('git@'):
        print("Detected SSH repository - configuring LFS for HTTPS operations")
        
        # Extract GitLab host from SSH URL
        # git@gitlab.example.com:group/project.git -> https://gitlab.example.com
        ssh_match = re.match(r'git@([^:]+):(.+)', remote_url)
        if ssh_match:
            gitlab_host = ssh_match.group(1)
            https_lfs_url = f"https://{gitlab_host}"
            
            print(f"Configuring LFS URL: {https_lfs_url}")
            
            # Set LFS URL
            try:
                subprocess.run(['git', 'config', 'lfs.url', https_lfs_url], 
                             cwd=repo_path, check=True, capture_output=True)
                print("✓ LFS URL configured successfully")
                
                # Verify configuration
                result = subprocess.run(['git', 'config', '--get', 'lfs.url'], 
                                      cwd=repo_path, capture_output=True, text=True)
                if result.returncode == 0:
                    configured_url = result.stdout.strip()
                    print(f"Verified LFS URL: {configured_url}")
                    
                return True
            except subprocess.CalledProcessError as e:
                print(f"Failed to configure LFS URL: {e.stderr.decode() if e.stderr else str(e)}")
                return False
        else:
            print("Could not parse SSH URL format")
            return False
    else:
        print("Repository is not using SSH - no LFS fix needed")
        return True

def main():
    # Common repository locations
    repo_paths = [
        '/app/user_repos/309462378',
        '/app/repo',
        './repo'
    ]
    
    fixed_any = False
    for repo_path in repo_paths:
        print(f"\n{'='*60}")
        print(f"Processing repository: {repo_path}")
        print('='*60)
        if fix_lfs_for_ssh_repo(repo_path):
            fixed_any = True
            print(f"✓ Successfully processed {repo_path}")
        else:
            print(f"✗ Failed to process {repo_path}")
    
    if fixed_any:
        print(f"\n{'='*60}")
        print("LFS configuration fix completed!")
        print("You can now test document locking operations.")
        print('='*60)
    else:
        print(f"\n{'='*60}")
        print("No repositories were successfully fixed.")
        print('='*60)

if __name__ == '__main__':
    main()