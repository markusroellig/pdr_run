"""Comprehensive RClone tests with multiple backend support."""

import os
import subprocess
import tempfile
import pytest
from pathlib import Path
from unittest.mock import patch

from pdr_run.storage.remote import RCloneStorage


class RCloneTestConfig:
    """Configuration for different RClone test scenarios."""
    
    LOCAL_BACKEND = {
        'name': 'local',
        'setup_cmd': ['rclone', 'config', 'create', 'pdr_test_local', 'local'],
        'remote_name': 'pdr_test_local'
    }
    
    MEMORY_BACKEND = {
        'name': 'memory',
        'setup_cmd': ['rclone', 'config', 'create', 'pdr_test_memory', 'memory'],
        'remote_name': 'pdr_test_memory'
    }


@pytest.fixture(params=[RCloneTestConfig.LOCAL_BACKEND, RCloneTestConfig.MEMORY_BACKEND])
def rclone_backend(request):
    """Parameterized fixture for different RClone backends."""
    backend_config = request.param
    
    # Check if rclone is available
    try:
        subprocess.run(['rclone', 'version'], check=True, capture_output=True)
    except (subprocess.SubprocessError, FileNotFoundError):
        pytest.skip("rclone not available")
    
    # Set up backend
    try:
        subprocess.run(backend_config['setup_cmd'], check=True, capture_output=True)
    except subprocess.SubprocessError:
        pytest.skip(f"Could not set up {backend_config['name']} backend")
    
    yield backend_config
    
    # Cleanup
    try:
        subprocess.run(['rclone', 'config', 'delete', backend_config['remote_name']], 
                      check=False, capture_output=True)
    except:
        pass


def test_rclone_full_workflow(rclone_backend):
    """Test complete RClone workflow with different backends."""
    with tempfile.TemporaryDirectory() as temp_dir:
        if rclone_backend['name'] == 'local':
            remote_dir = tempfile.mkdtemp()
            remote_spec = f"{rclone_backend['remote_name']}:{remote_dir}"
        else:
            remote_spec = rclone_backend['remote_name']
        
        config = {
            'base_dir': temp_dir,
            'rclone_remote': remote_spec,
            'use_mount': False
        }
        
        storage = RCloneStorage(config)
        
        # Test 1: Store multiple files
        test_files = {}
        for i in range(3):
            file_path = Path(temp_dir) / f'test_file_{i}.txt'
            content = f'Test content for file {i}\nMultiple lines\nLine {i+2}'
            file_path.write_text(content)
            test_files[f'models/test_{i}/data.txt'] = content
            
            result = storage.store_file(str(file_path), f'models/test_{i}/data.txt')
            assert result is True
        
        # Test 2: List files in directory
        files = storage.list_files('models')
        assert len([f for f in files if 'test_' in f]) >= 3
        
        # Test 3: Retrieve files
        for remote_path, expected_content in test_files.items():
            local_path = Path(temp_dir) / f'retrieved_{remote_path.replace("/", "_")}'
            result = storage.retrieve_file(remote_path, str(local_path))
            assert result is True
            assert local_path.read_text() == expected_content
        
        # Test 4: File existence checks
        for remote_path in test_files.keys():
            assert storage.file_exists(remote_path) is True
        
        assert storage.file_exists('models/nonexistent/file.txt') is False
        
        # Test 5: Directory sync
        sync_dir = Path(temp_dir) / 'sync_source'
        sync_dir.mkdir()
        (sync_dir / 'sync1.txt').write_text('Sync content 1')
        (sync_dir / 'sync2.dat').write_text('Sync content 2')
        
        result = storage.sync_directory(str(sync_dir), 'synced_data')
        assert result is True
        
        # Verify synced files
        synced_files = storage.list_files('synced_data')
        assert 'sync1.txt' in synced_files
        assert 'sync2.dat' in synced_files


def test_rclone_performance_benchmark(rclone_backend):
    """Benchmark RClone operations."""
    import time
    
    with tempfile.TemporaryDirectory() as temp_dir:
        if rclone_backend['name'] == 'local':
            remote_dir = tempfile.mkdtemp()
            remote_spec = f"{rclone_backend['remote_name']}:{remote_dir}"
        else:
            remote_spec = rclone_backend['remote_name']
        
        config = {
            'base_dir': temp_dir,
            'rclone_remote': remote_spec,
            'use_mount': False
        }
        
        storage = RCloneStorage(config)
        
        # Create test files of different sizes
        file_sizes = [1024, 10240, 102400]  # 1KB, 10KB, 100KB
        
        for size in file_sizes:
            # Create file
            test_file = Path(temp_dir) / f'perf_test_{size}.dat'
            test_file.write_bytes(b'x' * size)
            
            # Time store operation
            start_time = time.time()
            result = storage.store_file(str(test_file), f'perf/test_{size}.dat')
            store_time = time.time() - start_time
            
            assert result is True
            print(f"Store {size} bytes: {store_time:.3f}s")
            
            # Time retrieve operation
            retrieve_file = Path(temp_dir) / f'retrieved_{size}.dat'
            start_time = time.time()
            result = storage.retrieve_file(f'perf/test_{size}.dat', str(retrieve_file))
            retrieve_time = time.time() - start_time
            
            assert result is True
            assert retrieve_file.stat().st_size == size
            print(f"Retrieve {size} bytes: {retrieve_time:.3f}s")