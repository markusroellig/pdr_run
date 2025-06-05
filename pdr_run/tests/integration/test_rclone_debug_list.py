"""Debug the list_files issue specifically."""

import os
import tempfile
import subprocess
import shutil
from pathlib import Path

from pdr_run.storage.remote import RCloneStorage


def debug_list_files():
    """Debug what's happening with list_files."""
    # Set up test directories
    test_base_dir = tempfile.mkdtemp(prefix='rclone_debug_list_base_')
    test_remote_dir = tempfile.mkdtemp(prefix='rclone_debug_list_remote_')
    
    try:
        # Create a simple local rclone remote for this test
        remote_name = 'debug_list_local'
        
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
        
        # Create test directory structure
        test_dir = Path(test_remote_dir) / 'test_list'
        test_dir.mkdir(parents=True, exist_ok=True)
        
        # Create test files
        (test_dir / 'file1.txt').write_text('content1')
        (test_dir / 'file2.dat').write_text('content2')
        (test_dir / 'subdir').mkdir(exist_ok=True)
        (test_dir / 'subdir' / 'file3.txt').write_text('content3')
        
        print(f"\nCreated files in {test_dir}:")
        for root, dirs, files in os.walk(test_dir):
            for file in files:
                full_path = os.path.join(root, file)
                rel_path = os.path.relpath(full_path, test_dir)
                print(f"  {rel_path}")
        
        # Test manual rclone command
        full_remote_path = storage._get_full_remote_path('test_list')
        print(f"\nFull remote path: {full_remote_path}")
        
        cmd = ['rclone', 'lsf', '--format=ps', full_remote_path]
        print(f"Running command: {' '.join(cmd)}")
        result = subprocess.run(cmd, capture_output=True, text=True)
        
        print(f"Exit code: {result.returncode}")
        print(f"Stdout: '{result.stdout}'")
        print(f"Stderr: '{result.stderr}'")
        
        # Parse the output manually
        print(f"\nParsing stdout:")
        files = [line.strip() for line in result.stdout.splitlines() if line.strip()]
        print(f"Raw files list: {files}")
        
        filenames = []
        for file_entry in files:
            if file_entry.strip():
                parts = file_entry.strip().split()
                print(f"  Entry: '{file_entry}' -> Parts: {parts}")
                if len(parts) >= 2:
                    filenames.append(parts[-1])
        
        print(f"Extracted filenames: {filenames}")
        file_basenames = [os.path.basename(f) for f in filenames]
        print(f"File basenames: {file_basenames}")
        
        # Test our storage implementation
        print(f"\nTesting storage.list_files('test_list'):")
        storage_files = storage.list_files('test_list')
        print(f"Storage returned: {storage_files}")
        
        # Try different rclone commands
        print(f"\nTrying different rclone commands:")
        
        # Try simple ls
        cmd_ls = ['rclone', 'ls', full_remote_path]
        print(f"Running: {' '.join(cmd_ls)}")
        result_ls = subprocess.run(cmd_ls, capture_output=True, text=True)
        print(f"ls exit code: {result_ls.returncode}")
        print(f"ls stdout: '{result_ls.stdout}'")
        
        # Try lsf without format
        cmd_lsf = ['rclone', 'lsf', full_remote_path]
        print(f"Running: {' '.join(cmd_lsf)}")
        result_lsf = subprocess.run(cmd_lsf, capture_output=True, text=True)
        print(f"lsf exit code: {result_lsf.returncode}")
        print(f"lsf stdout: '{result_lsf.stdout}'")
        
        # Try lsf with different format
        cmd_lsf_f = ['rclone', 'lsf', '--format=f', full_remote_path]
        print(f"Running: {' '.join(cmd_lsf_f)}")
        result_lsf_f = subprocess.run(cmd_lsf_f, capture_output=True, text=True)
        print(f"lsf -f exit code: {result_lsf_f.returncode}")
        print(f"lsf -f stdout: '{result_lsf_f.stdout}'")
        
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
    debug_list_files()