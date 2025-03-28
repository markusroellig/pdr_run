"""Remote storage management for the PDR framework."""

import os
import logging
import subprocess
import tempfile
import requests
import paramiko
from ftplib import FTP

from pdr_run.config.default_config import STORAGE_CONFIG

logger = logging.getLogger('dev')

class StorageManager:
    """Storage manager factory class."""
    
    @staticmethod
    def get_storage(config=None):
        """Get appropriate storage handler based on configuration.
        
        Args:
            config (dict, optional): Storage configuration. Defaults to None.
            
        Returns:
            Storage: Storage handler instance
        """
        if config is None:
            config = STORAGE_CONFIG
        
        storage_type = config.get('type', 'local')
        
        if storage_type == 'local':
            return LocalStorage(config)
        elif storage_type == 'rclone':
            return RCloneStorage(config)
        elif storage_type == 'sftp':
            return SFTPStorage(config)
        elif storage_type == 'ftp':
            return FTPStorage(config)
        else:
            raise ValueError(f"Unsupported storage type: {storage_type}")

class Storage:
    """Base storage class."""
    
    def __init__(self, config=None):
        """Initialize storage.
        
        Args:
            config (dict, optional): Storage configuration. Defaults to None.
        """
        self.config = config or STORAGE_CONFIG
        self.base_dir = self.config.get('base_dir', '.')
    
    def upload_file(self, local_path, remote_path):
        """Upload a file to storage.
        
        Args:
            local_path (str): Local file path
            remote_path (str): Remote file path
            
        Raises:
            NotImplementedError: Method must be implemented by subclasses
        """
        raise NotImplementedError("Subclasses must implement upload_file")
    
    def download_file(self, remote_path, local_path):
        """Download a file from storage.
        
        Args:
            remote_path (str): Remote file path
            local_path (str): Local file path
            
        Raises:
            NotImplementedError: Method must be implemented by subclasses
        """
        raise NotImplementedError("Subclasses must implement download_file")
    
    def list_files(self, path):
        """List files in storage.
        
        Args:
            path (str): Directory path
            
        Raises:
            NotImplementedError: Method must be implemented by subclasses
        """
        raise NotImplementedError("Subclasses must implement list_files")

class LocalStorage(Storage):
    """Local file storage implementation."""
    
    def upload_file(self, local_path, remote_path):
        """Upload (copy) a file to local storage.
        
        Args:
            local_path (str): Source file path
            remote_path (str): Destination file path
            
        Returns:
            bool: True if successful
        """
        import shutil
        
        full_remote_path = os.path.join(self.base_dir, remote_path)
        os.makedirs(os.path.dirname(full_remote_path), exist_ok=True)
        
        shutil.copy2(local_path, full_remote_path)
        logger.info(f"Copied {local_path} to {full_remote_path}")
        
        return True
    
    def download_file(self, remote_path, local_path):
        """Download (copy) a file from local storage.
        
        Args:
            remote_path (str): Source file path
            local_path (str): Destination file path
            
        Returns:
            bool: True if successful
        """
        import shutil
        
        full_remote_path = os.path.join(self.base_dir, remote_path)
        os.makedirs(os.path.dirname(local_path), exist_ok=True)
        
        shutil.copy2(full_remote_path, local_path)
        logger.info(f"Copied {full_remote_path} to {local_path}")
        
        return True
    
    def list_files(self, path):
        """List files in local storage.
        
        Args:
            path (str): Directory path
            
        Returns:
            list: List of file paths
        """
        full_path = os.path.join(self.base_dir, path)
        
        if not os.path.exists(full_path):
            return []
        
        return os.listdir(full_path)

class RCloneStorage(Storage):
    """RClone-based remote storage implementation."""
    
    def __init__(self, config=None):
        """Initialize RClone storage.
        
        Args:
            config (dict, optional): Storage configuration. Defaults to None.
        """
        super().__init__(config)
        self.remote = self.config.get('rclone_remote', 'default')
        self.use_local_copy = self.config.get('use_local_copy', True)
        
        # Verify rclone is installed
        try:
            subprocess.run(['rclone', 'version'], check=True, stdout=subprocess.PIPE)
        except (subprocess.SubprocessError, FileNotFoundError):
            logger.error("rclone is not installed or not in PATH")
            raise RuntimeError("rclone is not installed or not in PATH")
    
    def upload_file(self, local_path, remote_path):
        """Upload a file to remote storage using rclone.
        
        Args:
            local_path (str): Local file path
            remote_path (str): Remote file path
            
        Returns:
            bool: True if successful
        """
        cmd = [
            'rclone', 'copy', 
            local_path,
            f"{self.remote}:{remote_path}"
        ]
        
        try:
            subprocess.run(cmd, check=True)
            logger.info(f"Uploaded {local_path} to {self.remote}:{remote_path}")
            
            # Optionally store in local storage too
            if self.use_local_copy:
                local_storage = LocalStorage(self.config)
                local_storage.upload_file(local_path, remote_path)
                
            return True
        except subprocess.SubprocessError as e:
            logger.error(f"Failed to upload file with rclone: {e}")
            return False
    
    def download_file(self, remote_path, local_path):
        """Download a file from remote storage using rclone.
        
        Args:
            remote_path (str): Remote file path
            local_path (str): Local file path
            
        Returns:
            bool: True if successful
        """
        # First check local copy if enabled
        if self.use_local_copy:
            try:
                local_storage = LocalStorage(self.config)
                if local_storage.download_file(remote_path, local_path):
                    return True
            except Exception as e:
                logger.warning(f"Could not use local copy, falling back to remote: {e}")
        
        # Download from remote
        os.makedirs(os.path.dirname(local_path), exist_ok=True)
        
        cmd = [
            'rclone', 'copy',
            f"{self.remote}:{remote_path}",
            local_path
        ]
        
        try:
            subprocess.run(cmd, check=True)
            logger.info(f"Downloaded {self.remote}:{remote_path} to {local_path}")
            return True
        except subprocess.SubprocessError as e:
            logger.error(f"Failed to download file with rclone: {e}")
            return False
    
    def list_files(self, path):
        """List files in remote storage using rclone.
        
        Args:
            path (str): Directory path
            
        Returns:
            list: List of file paths
        """
        if self.use_local_copy:
            try:
                local_storage = LocalStorage(self.config)
                local_files = local_storage.list_files(path)
                if local_files:
                    return local_files
            except Exception as e:
                logger.warning(f"Could not use local copy, falling back to remote: {e}")
        
        cmd = [
            'rclone', 'lsf', '--format=ps',
            f"{self.remote}:{path}"
        ]
        
        try:
            result = subprocess.run(cmd, check=True, stdout=subprocess.PIPE, text=True)
            files = result.stdout.strip().split('\n')
            return [f for f in files if f]  # Filter out empty strings
        except subprocess.SubprocessError as e:
            logger.error(f"Failed to list files with rclone: {e}")
            return []

# Add implementations for SFTPStorage and FTPStorage similar to the existing ones