"""Remote storage implementations.

This module provides remote storage implementations of the Storage interface,
allowing files to be stored and retrieved from remote systems. It includes
a base RemoteStorage class and specific implementations like SFTPStorage
for different remote storage protocols.
"""

import os
import paramiko
from pdr_run.storage.base import Storage

# Alias for backward compatibility
class RemoteStorage(Storage):
    """Generic remote storage implementation.
    
    This class serves as a base for various remote storage implementations.
    It defines the common attributes and interface methods that all remote
    storage classes should implement. Specific implementations like SFTPStorage
    extend this class to provide concrete functionality for different protocols.
    """
    
    def __init__(self, host, user, password, base_dir):
        """Initialize remote storage.
        
        Sets up the remote storage connection parameters needed to establish
        connections to the remote system.
        
        Args:
            host (str): Hostname or IP address of the remote server
            user (str): Username for authentication on the remote server
            password (str): Password for authentication on the remote server
            base_dir (str): Base directory on the remote system where files
                           will be stored and retrieved from
        """
        self.host = host
        self.user = user
        self.password = password
        self.base_dir = base_dir
    
    def store_file(self, local_path, remote_path):
        """Store a file remotely.
        
        Uploads a local file to the remote storage system at the specified path.
        
        Args:
            local_path (str): Source file path on the local filesystem
            remote_path (str): Destination path within the remote storage system
                              (relative to base_dir)
                              
        Raises:
            NotImplementedError: This method must be implemented by subclasses
        """
        raise NotImplementedError("This is a base class, use a specific implementation")
    
    def retrieve_file(self, remote_path, local_path):
        """Retrieve a file from remote storage.
        
        Downloads a file from the remote storage system to the local filesystem.
        
        Args:
            remote_path (str): Path to the file within the remote storage system
                              (relative to base_dir)
            local_path (str): Destination path where the file should be saved
                             on the local filesystem
                             
        Raises:
            NotImplementedError: This method must be implemented by subclasses
        """
        raise NotImplementedError("This is a base class, use a specific implementation")
    
    def list_files(self, path):
        """List files in remote storage.
        
        Retrieves a list of all files and directories located at the specified
        path within the remote storage system.
        
        Args:
            path (str): Directory path within the remote storage system to list
                       (relative to base_dir)
                       
        Returns:
            list: List of filenames (strings) in the specified directory
            
        Raises:
            NotImplementedError: This method must be implemented by subclasses
        """
        raise NotImplementedError("This is a base class, use a specific implementation")

class SFTPStorage(RemoteStorage):
    """SFTP storage implementation."""
    
    def store_file(self, local_path, remote_path):
        """Store a file using SFTP."""
        client = paramiko.SSHClient()
        client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
        
        try:
            client.connect(self.host, username=self.user, password=self.password)
            sftp = client.open_sftp()
            
            # Ensure directory exists
            remote_dir = os.path.dirname(os.path.join(self.base_dir, remote_path))
            try:
                sftp.stat(remote_dir)
            except FileNotFoundError:
                # Create directory structure
                dirs_to_create = []
                temp_dir = remote_dir
                while True:
                    try:
                        sftp.stat(temp_dir)
                        break
                    except FileNotFoundError:
                        dirs_to_create.insert(0, temp_dir)
                        temp_dir = os.path.dirname(temp_dir)
                
                for directory in dirs_to_create:
                    sftp.mkdir(directory)
            
            # Upload file
            sftp.put(local_path, os.path.join(self.base_dir, remote_path))
            return True
        finally:
            client.close()
    
    def retrieve_file(self, remote_path, local_path):
        """Retrieve a file using SFTP."""
        client = paramiko.SSHClient()
        client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
        
        try:
            client.connect(self.host, username=self.user, password=self.password)
            sftp = client.open_sftp()
            
            # Ensure local directory exists
            local_dir = os.path.dirname(local_path)
            os.makedirs(local_dir, exist_ok=True)
            
            # Download file
            sftp.get(os.path.join(self.base_dir, remote_path), local_path)
            return True
        finally:
            client.close()
    
    def list_files(self, path):
        """List files using SFTP."""
        client = paramiko.SSHClient()
        client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
        
        try:
            client.connect(self.host, username=self.user, password=self.password)
            sftp = client.open_sftp()
            
            # List files
            full_path = os.path.join(self.base_dir, path)
            try:
                return sftp.listdir(full_path)
            except FileNotFoundError:
                return []
        finally:
            client.close()