"""Test storage backends."""

import os
import pytest
from unittest.mock import patch, MagicMock
import tempfile
import shutil
import json

# Assuming these modules exist
from pdr_run.storage.base import get_storage_backend, Storage
from pdr_run.storage.local import LocalStorage
from pdr_run.storage.remote import RemoteStorage, SFTPStorage

def test_get_local_storage_backend(temp_storage_dir):
    """Test retrieving a local storage backend."""
    # Set environment for local storage
    os.environ["PDR_STORAGE_TYPE"] = "local"
    os.environ["PDR_STORAGE_DIR"] = temp_storage_dir
    
    # Get backend
    backend = get_storage_backend()
    
    # Verify backend type and configuration
    assert isinstance(backend, LocalStorage)
    assert backend.base_dir == temp_storage_dir

def test_local_storage_save_file(temp_storage_dir):
    """Test saving a file with local storage."""
    # Create storage backend
    storage = LocalStorage(temp_storage_dir)
    
    # Create test data
    test_data = {"test": "data", "value": 42}
    
    # Create a temporary source file
    with tempfile.NamedTemporaryFile(mode='w', delete=False) as temp_file:
        json.dump(test_data, temp_file)
        source_path = temp_file.name
    
    try:
        # Save file
        file_path = "test_model/results.json"
        storage.store_file(source_path, file_path)
        
        # Verify file was saved
        full_path = os.path.join(temp_storage_dir, file_path)
        assert os.path.exists(full_path)
        
        # Verify content
        with open(full_path, 'r') as f:
            saved_data = json.load(f)
            assert saved_data == test_data
    finally:
        # Clean up
        if os.path.exists(source_path):
            os.unlink(source_path)

def test_local_storage_load_file(temp_storage_dir):
    """Test loading a file with local storage."""
    # Create storage backend
    storage = LocalStorage(temp_storage_dir)
    
    # Create test data
    test_data = {"test": "data", "value": 42}
    
    # Save file first
    file_path = "test_model/results.json"
    dir_path = os.path.join(temp_storage_dir, os.path.dirname(file_path))
    os.makedirs(dir_path, exist_ok=True)
    
    with open(os.path.join(temp_storage_dir, file_path), 'w') as f:
        json.dump(test_data, f)
    
    # Create a temporary destination file
    with tempfile.NamedTemporaryFile(delete=False) as temp_file:
        dest_path = temp_file.name
    
    try:
        # Retrieve file
        storage.retrieve_file(file_path, dest_path)
        
        # Verify content
        with open(dest_path, 'r') as f:
            loaded_data = json.load(f)
            assert loaded_data == test_data
    finally:
        # Clean up
        if os.path.exists(dest_path):
            os.unlink(dest_path)

def test_mock_remote_storage():
    """Test remote storage backend with mocks."""
    # Set environment for remote storage
    os.environ["PDR_STORAGE_TYPE"] = "remote"
    os.environ["PDR_STORAGE_HOST"] = "test.example.com"
    os.environ["PDR_STORAGE_USER"] = "testuser"
    os.environ["PDR_STORAGE_PASSWORD"] = "password123"
    os.environ["PDR_STORAGE_DIR"] = "/remote/path"
    
    with patch('pdr_run.storage.remote.RemoteStorage') as MockRemoteStorage:
        # Configure the mock
        mock_instance = MagicMock()
        MockRemoteStorage.return_value = mock_instance
        
        # Call get_storage_backend, which should use our mock
        backend = get_storage_backend()
        
        # Verify our mock was used
        assert backend == mock_instance
        
        # Verify constructor args
        MockRemoteStorage.assert_called_once_with(
            "test.example.com", 
            "testuser", 
            "password123", 
            "/remote/path"
        )