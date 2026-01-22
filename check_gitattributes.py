#!/usr/bin/env python3
"""
Check .gitattributes configuration for LFS lockable files
"""

import subprocess
import os
from pathlib import Path

def check_gitattributes(repo_path):
    """Check .gitattributes configuration"""
    print(f"Checking .gitattributes in: {repo_path}")
    
    if not os.path.exists(repo_path):
        print(f"Repository path doesn't exist: {repo_path}")
        return
    
    # Check if it's a git repository
    git_dir = os.path.join(repo_path, '.git')
    if not os.path.exists(git_dir):
        print(f"Not a git repository: {repo_path}")
        return
    
    # Check .gitattributes file
    gitattributes_path = os.path.join(repo_path, '.gitattributes')
    if os.path.exists(gitattributes_path):
        print(f"\n.gitattributes content:")
        with open(gitattributes_path, 'r', encoding='utf-8') as f:
            content = f.read()
            print(content)
            
        # Check for lockable attributes
        lockable_lines = [line for line in content.split('\n') if 'lockable' in line]
        if lockable_lines:
            print(f"\nLockable file patterns found:")
            for line in lockable_lines:
                print(f"  {line}")
        else:
            print(f"\nNo lockable file patterns found in .gitattributes")
    else:
        print(f"\n.gitattributes file not found")
    
    # Check LFS tracked files
    try:
        result = subprocess.run(['git', 'lfs', 'track'], cwd=repo_path, capture_output=True, text=True)
        if result.returncode == 0:
            print(f"\nLFS tracked patterns:")
            print(result.stdout)
        else:
            print(f"\nFailed to get LFS track info: {result.stderr}")
    except Exception as e:
        print(f"Error checking LFS track: {e}")

def main():
    # Check common repository locations
    repo_paths = [
        '/app/repo',
        '/app/user_repos/309462378',
        './repo'
    ]
    
    for repo_path in repo_paths:
        print(f"\n{'='*60}")
        print(f"Checking repository: {repo_path}")
        print('='*60)
        check_gitattributes(repo_path)

if __name__ == '__main__':
    main()