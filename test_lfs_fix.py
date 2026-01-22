#!/usr/bin/env python3
"""
Test script to verify LFS configuration and attempt document locking
"""

import subprocess
import os
from pathlib import Path

def test_lfs_configuration(repo_path, test_doc_name="ОПЗ_Диспетчеризация_1901260300.docx"):
    """Test LFS configuration and locking"""
    print(f"Testing LFS configuration in: {repo_path}")
    
    if not os.path.exists(repo_path):
        print(f"Repository path doesn't exist: {repo_path}")
        return
    
    # Check if it's a git repository
    git_dir = os.path.join(repo_path, '.git')
    if not os.path.exists(git_dir):
        print(f"Not a git repository: {repo_path}")
        return
    
    print("\n=== Git Configuration ===")
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
    
    print("\n=== LFS Configuration ===")
    # Check LFS configs
    lfs_configs = ['lfs.url', 'lfs.pushurl']
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
    
    print("\n=== Current LFS Locks ===")
    # Check current locks
    try:
        result = subprocess.run(['git', 'lfs', 'locks'], 
                              cwd=repo_path, capture_output=True, text=True)
        print(f"Return code: {result.returncode}")
        if result.stdout:
            print(f"Current locks:\n{result.stdout}")
        if result.stderr:
            print(f"Errors:\n{result.stderr}")
    except Exception as e:
        print(f"Error checking LFS locks: {e}")
    
    print("\n=== Testing Document Lock ===")
    # Try to find and lock the test document
    try:
        # Search for the document
        doc_path = None
        for file_path in Path(repo_path).rglob(test_doc_name):
            if (file_path.suffix.lower() == '.docx' and 
                not any(part.startswith('.') for part in file_path.parts) and
                '.git' not in file_path.parts):
                doc_path = file_path
                break
        
        if doc_path:
            print(f"Found document: {doc_path}")
            rel_path = str(doc_path.relative_to(repo_path)).replace('\\', '/')
            print(f"Relative path: {rel_path}")
            
            # Try to lock the document
            print(f"Attempting to lock: {rel_path}")
            result = subprocess.run(['git', 'lfs', 'lock', rel_path], 
                                  cwd=repo_path, capture_output=True, text=True)
            print(f"Lock command return code: {result.returncode}")
            if result.stdout:
                print(f"Lock stdout: {result.stdout}")
            if result.stderr:
                print(f"Lock stderr: {result.stderr}")
                
            # Check locks again
            print("\nChecking locks after attempt:")
            result2 = subprocess.run(['git', 'lfs', 'locks'], 
                                   cwd=repo_path, capture_output=True, text=True)
            if result2.stdout:
                print(f"Current locks:\n{result2.stdout}")
                
        else:
            print(f"Document {test_doc_name} not found in repository")
            
    except Exception as e:
        print(f"Error during lock test: {e}")

def main():
    # Common repository locations
    repo_paths = [
        '/app/user_repos/309462378',  # Most likely location for user repo
        '/app/repo',
        './repo'
    ]
    
    for repo_path in repo_paths:
        print(f"\n{'='*70}")
        print(f"Testing repository: {repo_path}")
        print('='*70)
        test_lfs_configuration(repo_path)
        print()

if __name__ == '__main__':
    main()