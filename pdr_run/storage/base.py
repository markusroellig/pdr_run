"""Base storage class and utilities."""

import os
import logging
from abc import ABC, abstractmethod

logger = logging.getLogger("dev")

def get_storage_backend():
    """Get appropriate storage backend based on environment variables.
    
    Returns:
        Storage: Storage backend instance
    """
    storage_type = os.environ.get("PDR_STORAGE_TYPE", "local")
    
    if storage_type == "local":
        from pdr_run.storage.local import LocalStorage
        storage_dir = os.environ.get("PDR_STORAGE_DIR", "/tmp/pdr_storage")
        return LocalStorage(storage_dir)
    elif storage_type == "sftp":
        from pdr_run.storage.remote import SFTPStorage
        host = os.environ.get("PDR_STORAGE_HOST", "localhost")
        user = os.environ.get("PDR_STORAGE_USER", "")
        password = os.environ.get("PDR_STORAGE_PASSWORD", "")
        base_dir = os.environ.get("PDR_STORAGE_DIR", "/tmp")
        return SFTPStorage(host, user, password, base_dir)
    elif storage_type == "remote":
        from pdr_run.storage.remote import RemoteStorage
        host = os.environ.get("PDR_STORAGE_HOST", "localhost")
        user = os.environ.get("PDR_STORAGE_USER", "")
        password = os.environ.get("PDR_STORAGE_PASSWORD", "")
        base_dir = os.environ.get("PDR_STORAGE_DIR", "/tmp")
        return RemoteStorage(host, user, password, base_dir)
    else:
        raise ValueError(f"Unsupported storage type: {storage_type}")

class Storage(ABC):
    """Abstract base class for storage backends."""
    
    @abstractmethod
    def store_file(self, local_path, remote_path):
        """Store a file in the storage backend."""
        pass
    
    @abstractmethod
    def retrieve_file(self, remote_path, local_path):
        """Retrieve a file from the storage backend."""
        pass
    
    @abstractmethod
    def list_files(self, path):
        """List files in the given path."""
        pass