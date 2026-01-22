#!/usr/bin/env python3
"""
Debug script to check Git LFS configuration
"""

import subprocess
import os
from pathlib import Path

def check_git_config(repo_path):
    """Check Git configuration for LFS"""
    print(f"Checking Git config in: {repo_path}")
    
    if not os.path.exists(repo_path):
        print(f"Repository path doesn't exist: {repo_path}")
        return
    
    # Check if it's a git repository
    git_dir = os.path.join(repo_path, '.git')
    if not os.path.exists(git_dir):
        print(f"Not a git repository: {repo_path}")
        return
    
    # Check remote URL
    try:
        result = subprocess.run(['git', 'remote', 'get-url', 'origin'], 
                              cwd=repo_path, capture_output=True, text=True)
        if result.returncode == 0:
            remote_url = result.stdout.strip()
            print(f"Remote URL: {remote_url}")
        else:
            print(f"Failed to get remote URL: {result.stderr}")
    except Exception as e:
        print(f"Error getting remote URL: {e}")
    
    # Check LFS configuration
    lfs_configs = [
        'lfs.url',
        'lfs.pushurl', 
        'remote.origin.url',
        'remote.origin.pushurl'
    ]
    
    for config in lfs_configs:
        try:
            result = subprocess.run(['git', 'config', '--get', config], 
                                  cwd=repo_path, capture_output=True, text=True)
            if result.returncode == 0:
                value = result.stdout.strip()
                print(f"{config}: {value}")
            else:
                print(f"{config}: not set")
        except Exception as e:
            print(f"Error checking {config}: {e}")
    
    # Try to get LFS locks to reproduce the error
    print("\n--- Testing LFS locks ---")
    try:
        result = subprocess.run(['git', 'lfs', 'locks'], 
                              cwd=repo_path, capture_output=True, text=True)
        print(f"Return code: {result.returncode}")
        print(f"STDOUT: {result.stdout}")
        print(f"STDERR: {result.stderr}")
    except Exception as e:
        print(f"Error running git lfs locks: {e}")

def main():
    # Check common repository locations
    repo_paths = [
        '/app/repo',
        '/app/user_repos/309462378',
        './repo'
    ]
    
    for repo_path in repo_paths:
        print(f"\n{'='*50}")
        print(f"Checking repository: {repo_path}")
        print('='*50)
        check_git_config(repo_path)

if __name__ == '__main__':
    main()