"""Tests using RClone's built-in test backend."""

import subprocess
import tempfile
import pytest
from pathlib import Path

from pdr_run.storage.remote import RCloneStorage


def setup_rclone_test_backend():
    """Set up RClone test backend configuration."""
    # Create test backend configuration
    cmd = [
        'rclone', 'config', 'create', 'pdr_test_backend', 'memory'
    ]
    
    try:
        subprocess.run(cmd, check=True, capture_output=True)
        return True
    except subprocess.SubprocessError:
        return False


def test_rclone_with_memory_backend():
    """Test RClone storage with memory backend."""
    if not setup_rclone_test_backend():
        pytest.skip("Could not set up RClone test backend")
    
    with tempfile.TemporaryDirectory() as temp_dir:
        config = {
            'base_dir': temp_dir,
            'rclone_remote': 'pdr_test_backend',
            'use_mount': False
        }
        
        storage = RCloneStorage(config)
        
        # Create test file
        test_file = Path(temp_dir) / 'memory_test.txt'
        test_file.write_text('Memory backend test content')
        
        # Test store
        assert storage.store_file(str(test_file), 'test/memory_test.txt') is True
        
        # Test retrieve
        retrieved_file = Path(temp_dir) / 'retrieved.txt'
        assert storage.retrieve_file('test/memory_test.txt', str(retrieved_file)) is True
        assert retrieved_file.read_text() == 'Memory backend test content'
        
        # Test list
        files = storage.list_files('test')
        assert 'memory_test.txt' in files
        
        # Test file exists
        assert storage.file_exists('test/memory_test.txt') is True
        assert storage.file_exists('test/nonexistent.txt') is False


# Cleanup
def cleanup_rclone_test_backend():
    """Clean up test backend."""
    try:
        subprocess.run(['rclone', 'config', 'delete', 'pdr_test_backend'], 
                      check=False, capture_output=True)
    except:
        pass