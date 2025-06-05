"""Fixed RClone storage tests based on debug output."""

import os
import tempfile
import subprocess
import pytest
import shutil
from pathlib import Path

from pdr_run.storage.remote import RCloneStorage


@pytest.fixture(scope="class")
def rclone_test_setup():
    """Set up test environment with local rclone remote."""
    # Check if rclone is available
    try:
        subprocess.run(['rclone', 'version'], check=True, 
                     stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    except (subprocess.SubprocessError, FileNotFoundError):
        pytest.skip("rclone not available")
    
    # Create test directories
    test_base_dir = tempfile.mkdtemp(prefix='rclone_test_base_')
    test_remote_dir = tempfile.mkdtemp(prefix='rclone_test_remote_')
    
    # Create local rclone remote configuration
    remote_name = 'pdr_test_local'
    _create_local_rclone_remote(remote_name)
    
    # Return test configuration
    test_config = {
        'test_base_dir': test_base_dir,
        'test_remote_dir': test_remote_dir,
        'remote_name': remote_name
    }
    
    yield test_config
    
    # Cleanup
    shutil.rmtree(test_base_dir, ignore_errors=True)
    shutil.rmtree(test_remote_dir, ignore_errors=True)
    
    # Remove test rclone remote
    try:
        subprocess.run(['rclone', 'config', 'delete', remote_name], 
                     check=False, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    except:
        pass


def _create_local_rclone_remote(remote_name):
    """Create a local rclone remote for testing."""
    config_content = f"""[{remote_name}]
type = local
"""
    
    # Ensure rclone config directory exists
    config_dir = Path.home() / '.config' / 'rclone'
    config_dir.mkdir(parents=True, exist_ok=True)
    config_file = config_dir / 'rclone.conf'
    
    # Read existing config or create new
    existing_config = ""
    if config_file.exists():
        existing_config = config_file.read_text()
    
    # Add our test remote if not already present
    if f"[{remote_name}]" not in existing_config:
        with open(config_file, 'a') as f:
            f.write(f"\n{config_content}")


@pytest.mark.usefixtures("rclone_test_setup")
class TestRCloneIntegration:
    """Comprehensive RClone storage tests."""
    
    def test_store_and_retrieve_file(self, rclone_test_setup):
        """Test storing and retrieving a file with filename preservation."""
        config = {
            'base_dir': rclone_test_setup['test_base_dir'],
            'rclone_remote': f"{rclone_test_setup['remote_name']}:{rclone_test_setup['test_remote_dir']}",
            'use_mount': False
        }
        
        storage = RCloneStorage(config)
        
        # Create test file
        test_content = "This is a test file for RClone storage.\nLine 2\nLine 3"
        test_file = Path(rclone_test_setup['test_base_dir']) / 'test_input.txt'
        test_file.write_text(test_content)
        
        # Store file with different target name (this should work now!)
        remote_path = 'test_models/model1/input.txt'
        result = storage.store_file(str(test_file), remote_path)
        assert result is True, "store_file should succeed"
        
        # Debug: Check what actually exists
        print(f"\nDEBUG: Files in {rclone_test_setup['test_remote_dir']}:")
        for root, dirs, files in os.walk(rclone_test_setup['test_remote_dir']):
            for file in files:
                full_path = os.path.join(root, file)
                rel_path = os.path.relpath(full_path, rclone_test_setup['test_remote_dir'])
                print(f"  Found: {rel_path}")
        
        # Based on debug output, the file should be stored with the target name
        expected_file = Path(rclone_test_setup['test_remote_dir']) / 'test_models' / 'model1' / 'input.txt'
        assert expected_file.exists(), f"File should exist at {expected_file}"
        assert expected_file.read_text() == test_content, "Content should match"
        
        # Test retrieve using the target path
        retrieved_file = Path(rclone_test_setup['test_base_dir']) / 'retrieved_input.txt'
        result = storage.retrieve_file(remote_path, str(retrieved_file))
        assert result is True, "retrieve_file should succeed"
        assert retrieved_file.exists(), "Retrieved file should exist"
        assert retrieved_file.read_text() == test_content, "Retrieved content should match"
    
    def test_list_files(self, rclone_test_setup):
        """Test listing files in remote storage."""
        config = {
            'base_dir': rclone_test_setup['test_base_dir'],
            'rclone_remote': f"{rclone_test_setup['remote_name']}:{rclone_test_setup['test_remote_dir']}",
            'use_mount': False
        }
        
        storage = RCloneStorage(config)
        
        # Create test directory and files directly
        test_dir = Path(rclone_test_setup['test_remote_dir']) / 'test_list'
        test_dir.mkdir(parents=True, exist_ok=True)
        
        (test_dir / 'file1.txt').write_text('content1')
        (test_dir / 'file2.dat').write_text('content2')
        
        # List files
        files = storage.list_files('test_list')
        
        print(f"DEBUG: list_files returned: {files}")
        
        # Should return clean filenames
        assert len(files) >= 2, f"Expected at least 2 files, got {len(files)}: {files}"
        assert 'file1.txt' in files, f"file1.txt not found in {files}"
        assert 'file2.dat' in files, f"file2.dat not found in {files}"
    
    def test_file_exists(self, rclone_test_setup):
        """Test file existence checking."""
        config = {
            'base_dir': rclone_test_setup['test_base_dir'],
            'rclone_remote': f"{rclone_test_setup['remote_name']}:{rclone_test_setup['test_remote_dir']}",
            'use_mount': False
        }
        
        storage = RCloneStorage(config)
        
        # Create test file directly
        test_file_path = Path(rclone_test_setup['test_remote_dir']) / 'exists_test.txt'
        test_file_path.parent.mkdir(parents=True, exist_ok=True)
        test_file_path.write_text('exists')
        
        # Test existing file
        assert storage.file_exists('exists_test.txt') is True
        
        # Test non-existing file
        assert storage.file_exists('nonexistent.txt') is False


def test_rclone_configuration():
    """Test rclone configuration and basic functionality."""
    try:
        # Check rclone installation
        result = subprocess.run(['rclone', 'version'], capture_output=True, text=True)
        print(f"RClone version: {result.stdout.split()[1] if result.returncode == 0 else 'Not found'}")
        
        # List configured remotes
        result = subprocess.run(['rclone', 'listremotes'], capture_output=True, text=True)
        print(f"Configured remotes: {result.stdout.strip()}")
        
        assert result.returncode == 0
    except FileNotFoundError:
        pytest.skip("RClone not installed")


if __name__ == "__main__":
    import sys
    sys.exit(pytest.main([__file__, "-v", "-s"]))