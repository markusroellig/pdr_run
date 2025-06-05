"""Debug the store_file issue specifically."""

import os
import tempfile
import subprocess
import shutil
from pathlib import Path

from pdr_run.storage.remote import RCloneStorage


def debug_store_file():
    """Debug what's happening with store_file."""
    # Set up test directories
    test_base_dir = tempfile.mkdtemp(prefix='rclone_debug_store_base_')
    test_remote_dir = tempfile.mkdtemp(prefix='rclone_debug_store_remote_')
    
    try:
        # Create a simple local rclone remote for this test
        remote_name = 'debug_store_local'
        
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
        
        # Set up storage
        config = {
            'base_dir': test_base_dir,
            'rclone_remote': f"{remote_name}:{test_remote_dir}",
            'use_mount': False
        }
        storage = RCloneStorage(config)
        
        print(f"Storage remote: {storage.remote}")
        print(f"Storage remote_name: {storage.remote_name}")
        print(f"Storage remote_base_path: {storage.remote_base_path}")
        
        # Create test file
        test_content = "This is a test file for RClone storage.\nLine 2\nLine 3"
        test_file = Path(test_base_dir) / 'test_input.txt'
        test_file.write_text(test_content)
        print(f"Created test file: {test_file}")
        
        # Store file
        remote_path = 'test_models/model1/input.txt'
        print(f"Storing file with remote_path: {remote_path}")
        
        # Get the full remote path that will be used
        full_remote_path = storage._get_full_remote_path(remote_path)
        print(f"Full remote path: {full_remote_path}")
        
        # Call store_file
        result = storage.store_file(str(test_file), remote_path)
        print(f"store_file result: {result}")
        
        # Check what files actually exist in the remote directory
        print(f"\nFiles in remote directory {test_remote_dir}:")
        for root, dirs, files in os.walk(test_remote_dir):
            for file in files:
                full_path = os.path.join(root, file)
                rel_path = os.path.relpath(full_path, test_remote_dir)
                print(f"  Found: {rel_path}")
                
                # Show file content
                with open(full_path, 'r') as f:
                    content_preview = f.read()[:50]
                print(f"    Content preview: {content_preview}...")
        
        # Also check with rclone ls to see what rclone sees
        print(f"\nUsing rclone ls to check remote:")
        cmd = ['rclone', 'ls', f"{remote_name}:{test_remote_dir}"]
        result_ls = subprocess.run(cmd, capture_output=True, text=True)
        print(f"rclone ls exit code: {result_ls.returncode}")
        print(f"rclone ls output: {result_ls.stdout}")
        if result_ls.stderr:
            print(f"rclone ls error: {result_ls.stderr}")
        
        # Test a manual rclone copy to see how it behaves
        print(f"\nTesting manual rclone copy:")
        manual_test_file = Path(test_base_dir) / 'manual_test.txt'
        manual_test_file.write_text("Manual test content")
        
        # Create target directory manually
        manual_target_dir = Path(test_remote_dir) / 'manual_test_dir'
        manual_target_dir.mkdir(parents=True, exist_ok=True)
        
        cmd_manual = ['rclone', 'copy', str(manual_test_file), f"{remote_name}:{manual_target_dir}"]
        print(f"Running: {' '.join(cmd_manual)}")
        result_manual = subprocess.run(cmd_manual, capture_output=True, text=True)
        print(f"Manual copy exit code: {result_manual.returncode}")
        print(f"Manual copy stderr: {result_manual.stderr}")
        
        # Check result
        expected_manual = manual_target_dir / 'manual_test.txt'
        print(f"Manual copy result exists: {expected_manual.exists()}")
        
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
    debug_store_file()
    