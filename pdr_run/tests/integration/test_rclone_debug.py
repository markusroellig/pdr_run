"""Debug version of RClone tests to understand the issues."""

import os
import tempfile
import subprocess
import pytest
import shutil
from pathlib import Path

from pdr_run.storage.remote import RCloneStorage


def test_rclone_command_debugging():
    """Debug what rclone commands are actually being run."""
    # Set up test directories
    test_base_dir = tempfile.mkdtemp(prefix='rclone_debug_base_')
    test_remote_dir = tempfile.mkdtemp(prefix='rclone_debug_remote_')
    
    try:
        # Create a simple local rclone remote for this test
        remote_name = 'debug_local'
        
        # Create rclone config
        config_dir = Path.home() / '.config' / 'rclone'
        config_dir.mkdir(parents=True, exist_ok=True)
        config_file = config_dir / 'rclone.conf'
        
        # Add test remote
        config_content = f"\n[{remote_name}]\ntype = local\n"
        with open(config_file, 'a') as f:
            f.write(config_content)
        
        print(f"Test base dir: {test_base_dir}")
        print(f"Test remote dir: {test_remote_dir}")
        
        # Test 1: Simple remote name only
        print("\n=== Test 1: Simple remote name ===")
        config1 = {
            'base_dir': test_base_dir,
            'rclone_remote': remote_name,
            'use_mount': False
        }
        storage1 = RCloneStorage(config1)
        print(f"Remote name: {storage1.remote_name}")
        print(f"Remote base path: '{storage1.remote_base_path}'")
        
        # Test 2: Remote with path
        print("\n=== Test 2: Remote with path ===")
        config2 = {
            'base_dir': test_base_dir,
            'rclone_remote': f"{remote_name}:{test_remote_dir}",
            'use_mount': False
        }
        storage2 = RCloneStorage(config2)
        print(f"Remote name: {storage2.remote_name}")
        print(f"Remote base path: '{storage2.remote_base_path}'")
        
        # Test actual rclone commands manually
        print("\n=== Testing manual rclone commands ===")
        
        # Create a test file
        test_file = Path(test_base_dir) / 'debug_test.txt'
        test_file.write_text('Debug test content')
        print(f"Created test file: {test_file}")
        
        # Test rclone ls on the remote directory
        cmd_ls = ['rclone', 'ls', f"{remote_name}:{test_remote_dir}"]
        print(f"Running: {' '.join(cmd_ls)}")
        result = subprocess.run(cmd_ls, capture_output=True, text=True)
        print(f"Exit code: {result.returncode}")
        print(f"Stdout: {result.stdout}")
        print(f"Stderr: {result.stderr}")
        
        # Test rclone copy
        target_dir = Path(test_remote_dir) / 'test_copy'
        target_dir.mkdir(exist_ok=True)
        
        cmd_copy = ['rclone', 'copy', str(test_file), f"{remote_name}:{target_dir}"]
        print(f"\nRunning: {' '.join(cmd_copy)}")
        result = subprocess.run(cmd_copy, capture_output=True, text=True)
        print(f"Exit code: {result.returncode}")
        print(f"Stdout: {result.stdout}")
        print(f"Stderr: {result.stderr}")
        
        # Check if file was copied
        copied_file = target_dir / 'debug_test.txt'
        print(f"File exists at {copied_file}: {copied_file.exists()}")
        
        # Test our storage implementation
        print(f"\n=== Testing storage implementation ===")
        result = storage2.store_file(str(test_file), 'storage_test/debug.txt')
        print(f"store_file result: {result}")
        
        # Check where the file actually ended up
        print("\nLooking for files in remote directory:")
        for root, dirs, files in os.walk(test_remote_dir):
            for file in files:
                full_path = os.path.join(root, file)
                rel_path = os.path.relpath(full_path, test_remote_dir)
                print(f"  Found: {rel_path}")
        
    finally:
        # Cleanup
        shutil.rmtree(test_base_dir, ignore_errors=True)
        shutil.rmtree(test_remote_dir, ignore_errors=True)
        
        # Remove test rclone remote
        try:
            subprocess.run(['rclone', 'config', 'delete', remote_name], 
                         check=False, capture_output=True)
        except:
            pass


if __name__ == "__main__":
    test_rclone_command_debugging()