"""Remote storage implementations.

This module provides remote storage implementations of the Storage interface,
allowing files to be stored and retrieved from remote systems. It includes
a base RemoteStorage class and specific implementations like SFTPStorage
for different remote storage protocols.
"""

import os
import subprocess
import logging
import sys
import paramiko
from pdr_run.storage.base import Storage

# Set up logging
logger = logging.getLogger(__name__)

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

    def __init__(self, host, user, password, base_dir):
        """Initialize SFTP storage with extensive debugging."""
        import logging
        self.logger = logging.getLogger("dev")
        
        self.logger.debug("=== SFTP STORAGE INITIALIZATION ===")
        self.logger.debug(f"Host: {host}")
        self.logger.debug(f"User: {user}")
        self.logger.debug(f"Password type: {type(password)}")
        self.logger.debug(f"Password length: {len(password) if password else 0} chars")
        self.logger.debug(f"Password is None: {password is None}")
        self.logger.debug(f"Password is empty string: {password == ''}")
        self.logger.debug(f"Password bool: {bool(password)}")
        self.logger.debug(f"Base dir: {base_dir}")
        
        super().__init__(host, user, password, base_dir)
        
        # Test connection immediately with detailed logging
        self.logger.debug("Testing SFTP connection...")
        try:
            self._test_connection()
            self.logger.info("SFTP connection test successful")
        except Exception as e:
            self.logger.error(f"SFTP connection test failed: {e}")
            raise
    
    def _test_connection(self):
        """Test SFTP connection with extensive debugging."""
        import paramiko
        
        self.logger.debug("=== SFTP CONNECTION TEST ===")
        self.logger.debug(f"Connecting to: {self.host}")
        self.logger.debug(f"Username: {self.user}")
        self.logger.debug(f"Password provided: {bool(self.password)}")
        
        # Set up paramiko logging
        paramiko.util.log_to_file('/home/roellig/pdr/pdr/test_run/logs/paramiko.log', level=paramiko.util.DEBUG)
        
        client = paramiko.SSHClient()
        client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
        
        try:
            self.logger.debug("Creating SSH connection...")
            
            # Try different authentication methods
            if self.password:
                self.logger.debug("Attempting password authentication")
                client.connect(
                    self.host, 
                    username=self.user, 
                    password=self.password,
                    timeout=10,
                    allow_agent=False,
                    look_for_keys=False
                )
            else:
                self.logger.debug("Attempting key-based authentication (no password provided)")
                client.connect(
                    self.host, 
                    username=self.user,
                    timeout=10
                )
            
            self.logger.debug("SSH connection successful, opening SFTP channel...")
            sftp = client.open_sftp()
            
            # Test base directory
            self.logger.debug(f"Testing base directory: {self.base_dir}")
            try:
                sftp.stat(self.base_dir)
                self.logger.debug(f"Base directory {self.base_dir} exists and is accessible")
            except FileNotFoundError:
                self.logger.warning(f"Base directory {self.base_dir} does not exist")
            except PermissionError:
                self.logger.error(f"Permission denied accessing {self.base_dir}")
                
            sftp.close()
            self.logger.debug("SFTP connection test completed successfully")
            
        except paramiko.AuthenticationException as e:
            self.logger.error(f"Authentication failed: {e}")
            self.logger.error("Possible causes:")
            self.logger.error("1. Incorrect password")
            self.logger.error("2. Account locked or disabled")
            self.logger.error("3. SSH keys required but not provided")
            self.logger.error("4. Two-factor authentication required")
            raise
        except paramiko.SSHException as e:
            self.logger.error(f"SSH connection failed: {e}")
            raise
        except Exception as e:
            self.logger.error(f"Connection test failed: {e}")
            raise
        finally:
            client.close()
    
    def store_file(self, local_path, remote_path):
        """Store a file using SFTP with extensive debugging."""
        self.logger.debug("=== SFTP STORE FILE ===")
        self.logger.debug(f"Local path: {local_path}")
        self.logger.debug(f"Remote path: {remote_path}")
        self.logger.debug(f"Full remote path: {os.path.join(self.base_dir, remote_path)}")
        
        if not os.path.exists(local_path):
            self.logger.error(f"Local file does not exist: {local_path}")
            raise FileNotFoundError(f"Local file not found: {local_path}")
            
        file_size = os.path.getsize(local_path)
        self.logger.debug(f"Local file size: {file_size} bytes")
        
        client = paramiko.SSHClient()
        client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
        
        try:
            self.logger.debug(f"Connecting to SFTP server {self.host}")
            client.connect(self.host, username=self.user, password=self.password)
            sftp = client.open_sftp()
            
            # Ensure directory exists
            full_remote_path = os.path.join(self.base_dir, remote_path)
            remote_dir = os.path.dirname(full_remote_path)
            
            self.logger.debug(f"Ensuring remote directory exists: {remote_dir}")
            self._ensure_remote_directory(sftp, remote_dir)
            
            # Upload file
            self.logger.debug(f"Starting file upload to {full_remote_path}")
            sftp.put(local_path, full_remote_path)
            
            # Verify upload
            try:
                remote_stat = sftp.stat(full_remote_path)
                self.logger.debug(f"Upload successful - remote file size: {remote_stat.st_size} bytes")
                if remote_stat.st_size != file_size:
                    self.logger.warning(f"File size mismatch: local={file_size}, remote={remote_stat.st_size}")
            except Exception as e:
                self.logger.error(f"Failed to verify uploaded file: {e}")
                
            self.logger.info(f"Successfully stored file via SFTP: {local_path} -> {full_remote_path}")
            return True
            
        except Exception as e:
            self.logger.error(f"SFTP store_file failed: {e}")
            import traceback
            self.logger.debug(f"Full traceback: {traceback.format_exc()}")
            return False
        finally:
            client.close()
    
    def _ensure_remote_directory(self, sftp, remote_dir):
        """Ensure remote directory exists with debugging."""
        try:
            sftp.stat(remote_dir)
            self.logger.debug(f"Remote directory already exists: {remote_dir}")
        except FileNotFoundError:
            self.logger.debug(f"Creating remote directory: {remote_dir}")
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
                self.logger.debug(f"Creating directory: {directory}")
                sftp.mkdir(directory)
    
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

class RCloneStorage(Storage):
    """RClone-based remote storage implementation.
    
    This class implements the Storage interface using rclone commands to
    interact with various remote storage systems supported by rclone.
    """
    
    def __init__(self, config):
        """Initialize RClone storage.
        
        Args:
            config (dict): Configuration dictionary containing:
                - base_dir: Base directory for local operations
                - rclone_remote: Name of the rclone remote to use (e.g., 'kosmatau')
                - mount_point (optional): Directory to use for mounting the remote
        """
        self.base_dir = config.get('base_dir', './data')
        self.remote = config.get('rclone_remote', 'default')
        self.mount_point = config.get('mount_point', os.path.join(self.base_dir, 'mnt'))
        self.use_mount = config.get('use_mount', False)
        
        # Verify rclone is installed
        try:
            subprocess.run(['rclone', 'version'], check=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        except (subprocess.SubprocessError, FileNotFoundError):
            logger.error("rclone is not installed or not in PATH")
            raise RuntimeError("rclone is not installed or not in PATH")
    
    def mount(self):
        """Mount the remote storage locally.
        
        Returns:
            bool: True if mount was successful, False otherwise
        """
        if not os.path.exists(self.mount_point):
            os.makedirs(self.mount_point, exist_ok=True)
            
        try:
            cmd = [
                'rclone', 'mount', 
                f"{self.remote}:", 
                self.mount_point,
                '--daemon',
                '--no-check-certificate'
            ]
            subprocess.run(cmd, check=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
            logger.info(f"Mounted {self.remote} at {self.mount_point}")
            return True
        except subprocess.SubprocessError as e:
            logger.error(f"Failed to mount remote storage: {e}")
            return False
    
    def umount(self):
        """Unmount the remote storage.
        
        Returns:
            bool: True if unmount was successful, False otherwise
        """
        try:
            subprocess.run(['fusermount', '-u', self.mount_point], check=True, 
                          stdout=subprocess.PIPE, stderr=subprocess.PIPE)
            logger.info(f"Unmounted {self.mount_point}")
            return True
        except subprocess.SubprocessError as e:
            logger.error(f"Failed to unmount remote storage: {e}")
            return False
    
    def store_file(self, local_path, remote_path):
        """Store a file to remote storage using rclone.
        
        Args:
            local_path (str): Source file path on the local filesystem
            remote_path (str): Destination path within the remote storage system
                              (relative to base_dir)
                              
        Returns:
            bool: True if the file was successfully stored, False otherwise
        """
        try:
            # Ensure remote directory exists
            remote_dir = os.path.dirname(remote_path)
            if remote_dir:
                mkdir_cmd = ['rclone', 'mkdir', f"{self.remote}:{remote_dir}"]
                subprocess.run(mkdir_cmd, check=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
            
            # Copy the file
            cmd = [
                'rclone', 'copy', 
                local_path, 
                f"{self.remote}:{remote_path}"
            ]
            subprocess.run(cmd, check=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
            logger.info(f"Copied {local_path} to {self.remote}:{remote_path}")
            return True
        except subprocess.SubprocessError as e:
            logger.error(f"Failed to upload file with rclone: {e}")
            return False
    
    def retrieve_file(self, remote_path, local_path):
        """Download a file from remote storage using rclone.
        
        Args:
            remote_path (str): Path to the file within the remote storage system
                              (relative to base_dir)
            local_path (str): Destination path where the file should be saved
                             on the local filesystem
                             
        Returns:
            bool: True if the file was successfully retrieved, False otherwise
        """
        try:
            # Ensure local directory exists
            local_dir = os.path.dirname(local_path)
            if local_dir:
                os.makedirs(local_dir, exist_ok=True)
            
            # Copy the file
            cmd = [
                'rclone', 'copy',
                f"{self.remote}:{remote_path}",
                local_path
            ]
            subprocess.run(cmd, check=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
            logger.info(f"Downloaded {self.remote}:{remote_path} to {local_path}")
            return True
        except subprocess.SubprocessError as e:
            logger.error(f"Failed to download file with rclone: {e}")
            return False
    
    def list_files(self, path):
        """List files in remote storage using rclone.
        
        Args:
            path (str): Directory path within the remote storage system to list
                       (relative to base_dir)
                       
        Returns:
            list: List of filenames (strings) in the specified directory
        """
        try:
            cmd = [
                'rclone', 'lsf', '--format=ps',
                f"{self.remote}:{path}"
            ]
            result = subprocess.run(cmd, check=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                   universal_newlines=True)
            
            # Split the output into lines and strip whitespace
            files = [line.strip() for line in result.stdout.splitlines() if line.strip()]
            return files
        except subprocess.SubprocessError as e:
            logger.error(f"Failed to list files with rclone: {e}")
            return []

    def sync_directory(self, local_dir, remote_dir):
        """Synchronize an entire directory to remote storage.
        
        Args:
            local_dir (str): Source directory path on local filesystem
            remote_dir (str): Destination directory path within remote storage
            
        Returns:
            bool: True if synchronization was successful, False otherwise
        """
        try:
            # Ensure remote directory exists
            mkdir_cmd = ['rclone', 'mkdir', f"{self.remote}:{remote_dir}"]
            subprocess.run(mkdir_cmd, check=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
            
            # Sync the directory
            cmd = [
                'rclone', 'copy', 
                local_dir, 
                f"{self.remote}:{remote_dir}"
            ]
            with open(os.path.join(self.base_dir, "rclone.log"), "a+") as log_file:
                subprocess.run(cmd, check=True, stdout=log_file, stderr=log_file)
            
            logger.info(f"Synchronized {local_dir} to {self.remote}:{remote_dir}")
            return True
        except Exception as e:
            logger.error(f"Failed to sync directory: {str(e)}", exc_info=True)
            return False