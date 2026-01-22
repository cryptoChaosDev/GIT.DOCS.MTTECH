#!/usr/bin/env python3
"""
Verify that LFS lock operations now use filename-only approach
"""

import subprocess
import os
from pathlib import Path

def verify_lfs_fix(repo_path, test_doc_name="ОПЗ_Диспетчеризация_1901260300.docx"):
    """Verify LFS operations use filename-only approach"""
    print(f"Verifying LFS fix in: {repo_path}")
    
    if not os.path.exists(repo_path):
        print(f"Repository path doesn't exist: {repo_path}")
        return
    
    # Check if it's a git repository
    git_dir = os.path.join(repo_path, '.git')
    if not os.path.exists(git_dir):
        print(f"Not a git repository: {repo_path}")
        return
    
    print("\n=== Testing Filename-Only Approach ===")
    
    # Try to find the test document
    doc_path = None
    for file_path in Path(repo_path).rglob(test_doc_name):
        if (file_path.suffix.lower() == '.docx' and 
            not any(part.startswith('.') for part in file_path.parts) and
            '.git' not in file_path.parts):
            doc_path = file_path
            break
    
    if not doc_path:
        print(f"Document {test_doc_name} not found")
        return
    
    print(f"Found document: {doc_path}")
    filename_only = doc_path.name
    rel_path = str(doc_path.relative_to(repo_path)).replace('\\', '/')
    
    print(f"Full relative path: {rel_path}")
    print(f"Filename only: {filename_only}")
    
    # Test lock with filename only (this should work)
    print(f"\n--- Testing lock with filename only ---")
    try:
        result = subprocess.run(['git', 'lfs', 'lock', filename_only], 
                              cwd=repo_path, capture_output=True, text=True, timeout=30)
        print(f"Return code: {result.returncode}")
        if result.stdout:
            print(f"STDOUT: {result.stdout}")
        if result.stderr:
            print(f"STDERR: {result.stderr}")
    except subprocess.TimeoutExpired:
        print("Command timed out")
    except Exception as e:
        print(f"Error: {e}")
    
    # Test lock with full path (this should fail with missing protocol)
    print(f"\n--- Testing lock with full path (should fail) ---")
    try:
        result = subprocess.run(['git', 'lfs', 'lock', rel_path], 
                              cwd=repo_path, capture_output=True, text=True, timeout=30)
        print(f"Return code: {result.returncode}")
        if result.stdout:
            print(f"STDOUT: {result.stdout}")
        if result.stderr:
            print(f"STDERR: {result.stderr}")
    except subprocess.TimeoutExpired:
        print("Command timed out")
    except Exception as e:
        print(f"Error: {e}")
    
    # Check current locks
    print(f"\n--- Current locks ---")
    try:
        result = subprocess.run(['git', 'lfs', 'locks'], 
                              cwd=repo_path, capture_output=True, text=True, timeout=10)
        print(f"Return code: {result.returncode}")
        if result.stdout:
            print(f"Locks:\n{result.stdout}")
        if result.stderr:
            print(f"Errors:\n{result.stderr}")
    except Exception as e:
        print(f"Error checking locks: {e}")

def main():
    repo_paths = [
        '/app/user_repos/309462378',
        '/app/repo',
        './repo'
    ]
    
    for repo_path in repo_paths:
        print(f"\n{'='*70}")
        print(f"Verifying fix in: {repo_path}")
        print('='*70)
        verify_lfs_fix(repo_path)
        print()

if __name__ == '__main__':
    main()