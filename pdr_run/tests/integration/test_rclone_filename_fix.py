"""Test the filename preservation fix for RClone storage."""

import os
import tempfile
import subprocess
import shutil
from pathlib import Path

from pdr_run.storage.remote import RCloneStorage


def test_rclone_filename_preservation():
    """Test that RClone stores files with the exact target filename."""
    # Set up test directories
    test_base_dir = tempfile.mkdtemp(prefix='rclone_filename_test_base_')
    test_remote_dir = tempfile.mkdtemp(prefix='rclone_filename_test_remote_')
    
    try:
        # Create a simple local rclone remote for this test
        remote_name = 'filename_test_local'
        
        # Create rclone config
        config_dir = Path.home() / '.config' / 'rclone'
        config_dir.mkdir(parents=True, exist_ok=True)
        config_file = config_dir / 'rclone.conf'
        
        # Add test remote
        config_content = f"\n[{remote_name}]\ntype = local\n"
        with open(config_file, 'a') as f:
            f.write(config_content)
        
        # Set up storage
        config = {
            'base_dir': test_base_dir,
            'rclone_remote': f"{remote_name}:{test_remote_dir}",
            'use_mount': False
        }
        storage = RCloneStorage(config)
        
        # Test Case 1: Different source and target filenames
        test_content = "Test content for filename preservation"
        source_file = Path(test_base_dir) / 'source_name.txt'
        source_file.write_text(test_content)
        
        # Store with different target name
        target_path = 'models/test1/target_name.txt'
        result = storage.store_file(str(source_file), target_path)
        assert result is True, "store_file should succeed"
        
        # Check the file exists with the exact target name
        expected_file = Path(test_remote_dir) / 'models' / 'test1' / 'target_name.txt'
        assert expected_file.exists(), f"File should exist at {expected_file}"
        assert expected_file.read_text() == test_content, "Content should match"
        
        # Verify old filename doesn't exist
        old_file = Path(test_remote_dir) / 'models' / 'test1' / 'source_name.txt'
        assert not old_file.exists(), f"Old filename {old_file} should not exist"
        
        # Test Case 2: Retrieve the file
        retrieved_file = Path(test_base_dir) / 'retrieved_target.txt'
        result = storage.retrieve_file(target_path, str(retrieved_file))
        assert result is True, "retrieve_file should succeed"
        assert retrieved_file.exists(), "Retrieved file should exist"
        assert retrieved_file.read_text() == test_content, "Retrieved content should match"
        
        # Test Case 3: Same source and target filenames
        source_file2 = Path(test_base_dir) / 'same_name.dat'
        source_file2.write_text("Same name test")
        
        target_path2 = 'models/test2/same_name.dat'
        result = storage.store_file(str(source_file2), target_path2)
        assert result is True, "store_file with same name should succeed"
        
        expected_file2 = Path(test_remote_dir) / 'models' / 'test2' / 'same_name.dat'
        assert expected_file2.exists(), f"File should exist at {expected_file2}"
        
        print("✓ All filename preservation tests passed!")
        
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
    test_rclone_filename_preservation()