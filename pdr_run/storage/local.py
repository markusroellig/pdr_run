"""Local storage implementation.

This module provides a local file system implementation of the Storage interface,
allowing files to be stored and retrieved from the local file system. It's used
for file operations when remote storage is not required or available.
"""

import os
import shutil
from pdr_run.storage.base import Storage

class LocalStorage(Storage):
    """Local file system storage.
    
    This class implements the abstract Storage interface for local file system operations.
    It provides methods to store, retrieve, and list files within a designated base directory
    on the local filesystem. This storage backend is useful for development, testing, or
    when remote storage is not needed.
    """
    
    def __init__(self, base_dir):
        """Initialize local storage.
        
        Sets up the local storage system with the specified base directory.
        Creates the base directory if it doesn't already exist to ensure
        the storage system is immediately usable after initialization.
        
        Args:
            base_dir (str): Base directory for storage. All files will be stored
                           relative to this location on the local filesystem.
        """
        self.base_dir = base_dir
        os.makedirs(self.base_dir, exist_ok=True)
    
    def store_file(self, local_path, remote_path):
        """Store a file in local storage.
        
        Copies a file from a source location to the storage system at the specified
        destination path. Automatically creates any necessary directory structure
        in the destination path.
        
        Args:
            local_path (str): Source file path on the local filesystem
            remote_path (str): Destination path within the storage system
                              (relative to base_dir)
                              
        Raises:
            FileNotFoundError: If the source file does not exist
            PermissionError: If there are permission issues with source or destination
            IOError: If there are other I/O related issues during the copy
        """
        full_path = os.path.join(self.base_dir, remote_path)
        os.makedirs(os.path.dirname(full_path), exist_ok=True)
        shutil.copy2(local_path, full_path)  # copy2 preserves file metadata
    
    def retrieve_file(self, remote_path, local_path):
        """Retrieve a file from local storage and copy it to a local destination.
        
        This method retrieves a file stored in the local storage system at the specified
        remote path and copies it to the provided local destination path. It automatically
        creates any necessary parent directories for the local path.
        
        Args:
            remote_path (str): Path to the file within the storage system
                               (relative to base_dir)
            local_path (str): Destination path where the file should be saved
                             on the local filesystem
                             
        Raises:
            FileNotFoundError: If the source file does not exist in storage
            IOError: If there are permission or disk space issues
        """
        full_path = os.path.join(self.base_dir, remote_path)
        os.makedirs(os.path.dirname(local_path), exist_ok=True)
        shutil.copy2(full_path, local_path)
    
    def list_files(self, path):
        """List files in the given path.
        
        Retrieves a list of all files and directories located at the specified
        path within the storage system. Returns an empty list if the path
        doesn't exist rather than raising an exception.
        
        Args:
            path (str): Directory path within the storage system to list
                       (relative to base_dir)
                       
        Returns:
            list: List of filenames (strings) in the specified directory.
                 Returns an empty list if the path doesn't exist.
        """
        full_path = os.path.join(self.base_dir, path)
        if not os.path.exists(full_path):
            return []
        return os.listdir(full_path)
    
    def file_exists(self, path):
        """Check if a file exists locally.
        
        Args:
            path (str): Path to check
            
        Returns:
            bool: True if file exists, False otherwise
        """
        if not path.startswith('/'):
            full_path = os.path.join(self.base_dir, path)
        else:
            full_path = path
            
        return os.path.isfile(full_path)