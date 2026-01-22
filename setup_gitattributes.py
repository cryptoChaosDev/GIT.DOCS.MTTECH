#!/usr/bin/env python3
"""
Setup .gitattributes for LFS lockable docx files
"""

import subprocess
import os
from pathlib import Path

def setup_gitattributes(repo_path):
    """Setup .gitattributes for docx files"""
    print(f"Setting up .gitattributes in: {repo_path}")
    
    if not os.path.exists(repo_path):
        print(f"Repository path doesn't exist: {repo_path}")
        return False
    
    # Check if it's a git repository
    git_dir = os.path.join(repo_path, '.git')
    if not os.path.exists(git_dir):
        print(f"Not a git repository: {repo_path}")
        return False
    
    # Path to .gitattributes
    gitattributes_path = os.path.join(repo_path, '.gitattributes')
    
    # Read existing content
    existing_content = ""
    if os.path.exists(gitattributes_path):
        with open(gitattributes_path, 'r', encoding='utf-8') as f:
            existing_content = f.read()
        print(f"Existing .gitattributes content:")
        print(existing_content)
    
    # Check if docx is already configured
    if '*.docx' in existing_content and 'lockable' in existing_content:
        print(f"*.docx already configured as lockable")
        return True
    
    # Add docx lockable configuration
    docx_config = "*.docx filter=lfs diff=lfs merge=lfs -text lockable"
    
    # Append to existing content or create new
    if existing_content.strip():
        new_content = existing_content.rstrip() + '\n' + docx_config + '\n'
    else:
        new_content = docx_config + '\n'
    
    # Write the file
    try:
        with open(gitattributes_path, 'w', encoding='utf-8') as f:
            f.write(new_content)
        print(f"Updated .gitattributes with docx lockable configuration")
        
        # Add and commit .gitattributes
        try:
            subprocess.run(['git', 'add', '.gitattributes'], cwd=repo_path, check=True, capture_output=True)
            subprocess.run(['git', 'commit', '-m', 'Configure .docx files as lockable for LFS'], 
                         cwd=repo_path, check=True, capture_output=True)
            print(f"Committed .gitattributes changes")
            
            # Push to remote
            try:
                subprocess.run(['git', 'push'], cwd=repo_path, check=True, capture_output=True)
                print(f"Pushed .gitattributes to remote repository")
            except subprocess.CalledProcessError as e:
                print(f"Warning: Failed to push .gitattributes: {e.stderr.decode() if e.stderr else str(e)}")
                print(f"You may need to push manually: git push origin main")
                
        except subprocess.CalledProcessError as e:
            print(f"Warning: Failed to commit .gitattributes: {e.stderr.decode() if e.stderr else str(e)}")
            
        return True
        
    except Exception as e:
        print(f"Error writing .gitattributes: {e}")
        return False

def main():
    # Common repository locations
    repo_paths = [
        '/app/repo',
        '/app/user_repos/309462378',
        './repo'
    ]
    
    for repo_path in repo_paths:
        print(f"\n{'='*60}")
        print(f"Processing repository: {repo_path}")
        print('='*60)
        if setup_gitattributes(repo_path):
            print(f"Successfully configured .gitattributes for {repo_path}")
        else:
            print(f"Failed to configure .gitattributes for {repo_path}")

if __name__ == '__main__':
    main()