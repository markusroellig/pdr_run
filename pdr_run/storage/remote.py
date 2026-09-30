"""Remote storage implementations.

This module provides remote storage implementations of the Storage interface,
allowing files to be stored and retrieved from remote systems. It includes
a base RemoteStorage class and specific implementations like SFTPStorage
for different remote storage protocols.
"""

import os
import subprocess
import logging
import socket
import sys
import paramiko
from pdr_run.storage.base import Storage
from pdr_run.utils.logging import get_password_status
from pdr_run.utils.retry import retry_with_backoff

# Exceptions worth retrying for network/remote-endpoint transfers: transient
# connection drops, timeouts, and subprocess launch failures. NOT retried:
# authentication failures, local FileNotFoundError for the source file, or
# any other programming error - those will fail identically on every retry
# and just waste a grid node's time.
_SFTP_RETRYABLE = (paramiko.SSHException, socket.error, socket.timeout,
                    ConnectionError, OSError, EOFError)
# FileNotFoundError and paramiko authentication errors are subclasses of the
# retryable OSError/SSHException but are permanent: never retried.
_SFTP_GIVEUP = (FileNotFoundError, paramiko.AuthenticationException)
_RCLONE_RETRYABLE = (subprocess.SubprocessError, RuntimeError, OSError)

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
        self.logger.debug(f"Password status: {get_password_status(password)}")
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
        """Store a file using SFTP with extensive debugging.

        Retries the connect+upload attempt (bounded, with backoff) on
        transient connection errors, since a single dropped SSH session
        must not lose a multi-hour grid node's result file.
        """
        self.logger.debug("=== SFTP STORE FILE ===")
        self.logger.debug(f"Local path: {local_path}")
        self.logger.debug(f"Remote path: {remote_path}")
        self.logger.debug(f"Full remote path: {os.path.join(self.base_dir, remote_path)}")

        if not os.path.exists(local_path):
            self.logger.error(f"Local file does not exist: {local_path}")
            raise FileNotFoundError(f"Local file not found: {local_path}")

        file_size = os.path.getsize(local_path)
        self.logger.debug(f"Local file size: {file_size} bytes")

        @retry_with_backoff(max_retries=3, initial_delay=2.0, backoff=2.0,
                             exceptions=_SFTP_RETRYABLE, giveup=_SFTP_GIVEUP)
        def _attempt():
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
                # Write next to the target, then rename over it, so that an
                # interrupted upload never destroys an existing file.
                self.logger.debug(f"Starting file upload to {full_remote_path}")
                part_path = full_remote_path + '.part'
                try:
                    sftp.put(local_path, part_path)
                    sftp.posix_rename(part_path, full_remote_path)
                except BaseException:
                    try:
                        sftp.remove(part_path)
                    except Exception:  # noqa: BLE001 - best-effort cleanup
                        pass
                    raise

                # Verify upload
                try:
                    remote_stat = sftp.stat(full_remote_path)
                    self.logger.debug(f"Upload successful - remote file size: {remote_stat.st_size} bytes")
                    if remote_stat.st_size != file_size:
                        self.logger.warning(f"File size mismatch: local={file_size}, remote={remote_stat.st_size}")
                except Exception as e:
                    self.logger.error(f"Failed to verify uploaded file: {e}")

                self.logger.info(f"Successfully stored file via SFTP: {local_path} -> {full_remote_path}")
            finally:
                client.close()

        try:
            _attempt()
            return True
        except Exception as e:
            self.logger.error(f"SFTP store_file failed after retries: {e}")
            import traceback
            self.logger.debug(f"Full traceback: {traceback.format_exc()}")
            return False
    
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
    
    @retry_with_backoff(max_retries=3, initial_delay=2.0, backoff=2.0,
                         exceptions=_SFTP_RETRYABLE, giveup=_SFTP_GIVEUP)
    def retrieve_file(self, remote_path, local_path):
        """Retrieve a file using SFTP.

        Bounded retry with backoff on transient connection errors (see
        ``store_file``); a ``FileNotFoundError`` from a genuinely missing
        remote file is not retried (not in ``_SFTP_RETRYABLE``).
        """
        client = paramiko.SSHClient()
        client.set_missing_host_key_policy(paramiko.AutoAddPolicy())

        try:
            client.connect(self.host, username=self.user, password=self.password)
            sftp = client.open_sftp()

            # Ensure local directory exists
            local_dir = os.path.dirname(local_path)
            if local_dir:  # Only create directory if it's not empty
                os.makedirs(local_dir, exist_ok=True)

            # Download file
            sftp.get(os.path.join(self.base_dir, remote_path), local_path)
            return True
        finally:
            client.close()

    @retry_with_backoff(max_retries=3, initial_delay=2.0, backoff=2.0,
                         exceptions=_SFTP_RETRYABLE, giveup=_SFTP_GIVEUP)
    def list_files(self, path):
        """List files using SFTP. Bounded retry on transient connection errors."""
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

    def file_exists(self, remote_path):
        """Check if a file exists on the remote server.

        A transient connection failure here must not be mistaken for "file
        does not exist" - that would make ``run_kosma_tau`` needlessly
        re-run a multi-hour PDR model that is already stored remotely. The
        connect+stat attempt is retried (bounded, with backoff); only a
        genuine ``FileNotFoundError`` from the remote stat (i.e. we did
        reach the server) is treated as "does not exist".

        Args:
            remote_path (str): Path to check on the remote server

        Returns:
            bool: True if file exists, False otherwise
        """
        @retry_with_backoff(max_retries=3, initial_delay=2.0, backoff=2.0,
                             exceptions=_SFTP_RETRYABLE, giveup=_SFTP_GIVEUP)
        def _attempt():
            with paramiko.SSHClient() as ssh:
                ssh.set_missing_host_key_policy(paramiko.AutoAddPolicy())
                ssh.connect(self.host, username=self.user, password=self.password)

                with ssh.open_sftp() as sftp:
                    # Convert relative path to absolute
                    if not remote_path.startswith('/'):
                        full_path = os.path.join(self.base_dir, remote_path)
                    else:
                        full_path = remote_path

                    try:
                        # Try to get file stats - if successful, file exists
                        sftp.stat(full_path)
                        return True
                    except FileNotFoundError:
                        return False

        try:
            return _attempt()
        except Exception as e:
            self.logger.warning(
                f"Could not determine whether {remote_path} exists on "
                f"{self.host} after retries (treating as absent): {e}")
            return False

class RCloneStorage(Storage):
    """RClone-based remote storage implementation."""
    
    def __init__(self, config):
        """Initialize RClone storage."""
        self.base_dir = config.get('base_dir', './data')
        self.remote = config.get('rclone_remote', 'default')
        self.mount_point = config.get('mount_point', os.path.join(self.base_dir, 'mnt'))
        self.use_mount = config.get('use_mount', False)
        self.remote_path_prefix = config.get('remote_path_prefix', None)
        
        # Add logger for consistency with SFTPStorage
        self.logger = logging.getLogger("dev")
        
        # Parse remote configuration
        if ':' in self.remote:
            # Remote includes path: "remote_name:/path/to/base"
            self.remote_name, self.remote_base_path = self.remote.split(':', 1)
        else:
            # Remote is just the name: "remote_name"
            self.remote_name = self.remote
            self.remote_base_path = ''
        
        # Verify rclone is installed
        try:
            subprocess.run(['rclone', 'version'], check=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        except (subprocess.SubprocessError, FileNotFoundError):
            logger.error("rclone is not installed or not in PATH")
            raise RuntimeError("rclone is not installed or not in PATH")
    
    def _get_full_remote_path(self, remote_path):
        """Construct full remote path combining base path and relative path."""

        # If a prefix is defined, remove it from the remote path
        if self.remote_path_prefix and remote_path.startswith(self.remote_path_prefix):
            remote_path = remote_path[len(self.remote_path_prefix):]
            # Remove leading slash if any to make it a relative path
            remote_path = remote_path.lstrip('/')

        # Remove leading slash from remote_path if present to avoid double slashes
        # when joining with base path
        clean_remote_path = remote_path.lstrip('/')

        if self.remote_base_path:
            # Join base path with remote path
            full_path = os.path.join(self.remote_base_path, clean_remote_path).replace('\\', '/')
            return f"{self.remote_name}:{full_path}"
        else:
            # No base path - for consistency, ensure path starts with '/' if not empty
            if clean_remote_path: # Only prepend '/' if there is actually a path
                clean_remote_path = '/' + clean_remote_path
            return f"{self.remote_name}:{clean_remote_path}"
    
    def store_file(self, local_path, remote_path):
        """Store a file to remote storage using rclone with exact filename control.

        Uses rclone copyto for atomic file-to-file transfer, which avoids race
        conditions in parallel execution (fixes GitHub issue #10). The
        mkdir+copyto attempt is retried (bounded, with backoff) on transient
        rclone/transport failures - a single flaky remote-endpoint call must
        not lose a multi-hour grid node's result file.
        """
        @retry_with_backoff(max_retries=3, initial_delay=2.0, backoff=2.0,
                             exceptions=_RCLONE_RETRYABLE)
        def _attempt():
            full_remote_path = self._get_full_remote_path(remote_path)

            # Get the target directory
            remote_dir = os.path.dirname(full_remote_path.split(':', 1)[1])

            # Ensure remote directory exists
            if remote_dir:
                mkdir_cmd = ['rclone', 'mkdir', f"{self.remote_name}:{remote_dir}"]
                subprocess.run(mkdir_cmd, check=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE)

            # Use copyto for atomic file-to-file transfer
            # This is safer for parallel execution than copy+moveto
            cmd = ['rclone', 'copyto', local_path, full_remote_path]
            result = subprocess.run(cmd, capture_output=True, text=True)

            if result.returncode != 0:
                raise RuntimeError(f"RClone copyto failed (rc={result.returncode}): {result.stderr}")

            self.logger.info(f"Stored {local_path} as {full_remote_path}")

        try:
            _attempt()
            return True
        except subprocess.SubprocessError as e:
            self.logger.error(f"Failed to upload file with rclone after retries: {e}")
            return False
        except Exception as e:
            self.logger.error(f"Unexpected error in store_file after retries: {e}")
            return False

    def retrieve_file(self, remote_path, local_path):
        """Download a file from remote storage using rclone.

        Bounded retry with backoff on transient rclone/transport failures.
        """
        @retry_with_backoff(max_retries=3, initial_delay=2.0, backoff=2.0,
                             exceptions=_RCLONE_RETRYABLE)
        def _attempt():
            full_remote_path = self._get_full_remote_path(remote_path)

            # Ensure local directory exists
            local_dir = os.path.dirname(local_path)
            if local_dir:
                os.makedirs(local_dir, exist_ok=True)

            # Use rclone copyto for exact file-to-file copy
            cmd = ['rclone', 'copyto', full_remote_path, local_path]
            result = subprocess.run(cmd, capture_output=True, text=True)

            if result.returncode != 0:
                raise RuntimeError(f"RClone retrieve failed (rc={result.returncode}): {result.stderr}")

            self.logger.info(f"Retrieved {full_remote_path} to {local_path}")

        try:
            _attempt()
            return True
        except subprocess.SubprocessError as e:
            self.logger.error(f"Failed to download file with rclone after retries: {e}")
            return False
        except Exception as e:
            self.logger.error(f"Unexpected error in retrieve_file after retries: {e}")
            return False

    def list_files(self, path):
        """List files in remote storage using rclone. Bounded retry on
        transient rclone/transport failures."""
        @retry_with_backoff(max_retries=3, initial_delay=2.0, backoff=2.0,
                             exceptions=_RCLONE_RETRYABLE)
        def _attempt():
            full_remote_path = self._get_full_remote_path(path)

            # Use simple lsf which just returns filenames - much cleaner!
            cmd = ['rclone', 'lsf', full_remote_path]
            result = subprocess.run(cmd, capture_output=True, text=True)

            if result.returncode != 0:
                raise RuntimeError(f"RClone lsf failed (rc={result.returncode}): {result.stderr}")

            # Split the output into lines and strip whitespace
            return [line.strip() for line in result.stdout.splitlines() if line.strip()]

        try:
            return _attempt()
        except subprocess.SubprocessError as e:
            self.logger.error(f"Failed to list files with rclone after retries: {e}")
            return []
        except Exception as e:
            self.logger.error(f"Unexpected error in list_files after retries: {e}")
            return []

    def sync_directory(self, local_dir, remote_dir):
        """Synchronize an entire directory to remote storage. Bounded retry
        on transient rclone/transport failures."""
        @retry_with_backoff(max_retries=3, initial_delay=2.0, backoff=2.0,
                             exceptions=_RCLONE_RETRYABLE)
        def _attempt():
            full_remote_path = self._get_full_remote_path(remote_dir)

            # Ensure remote directory exists
            mkdir_cmd = ['rclone', 'mkdir', full_remote_path]
            subprocess.run(mkdir_cmd, check=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE)

            # Sync the directory
            cmd = ['rclone', 'copy', local_dir, full_remote_path]
            result = subprocess.run(cmd, capture_output=True, text=True)

            if result.returncode != 0:
                raise RuntimeError(f"RClone sync failed (rc={result.returncode}): {result.stderr}")

            self.logger.info(f"Synchronized {local_dir} to {full_remote_path}")

        try:
            _attempt()
            return True
        except Exception as e:
            self.logger.error(f"Failed to sync directory after retries: {str(e)}", exc_info=True)
            return False
    
    # stderr substrings that indicate rclone could not reach the remote at
    # all (worth retrying), as opposed to a normal "path does not exist"
    # non-zero exit (the expected, common case for a not-yet-computed grid
    # node - must not be retried, or every fresh node pays 3 retries).
    _RCLONE_TRANSPORT_ERROR_MARKERS = (
        'timeout', 'timed out', 'connection refused', "couldn't connect",
        'no such host', 'network is unreachable', 'i/o timeout',
        'temporary failure', 'connection reset', 'broken pipe',
    )

    def file_exists(self, remote_path):
        """Check if a file exists on the remote server using rclone.

        A transient connection failure here must not be mistaken for "file
        does not exist" - that would make ``run_kosma_tau`` needlessly
        re-run a multi-hour PDR model that is already stored remotely. Only
        exit statuses whose stderr looks like a transport/connectivity
        problem are retried; the ordinary "path not found" non-zero exit is
        not retried (it is the expected, common case for a fresh node and
        must stay cheap).
        """
        @retry_with_backoff(max_retries=3, initial_delay=2.0, backoff=2.0,
                             exceptions=_RCLONE_RETRYABLE)
        def _attempt():
            full_remote_path = self._get_full_remote_path(remote_path)

            # Use rclone lsf to check if the specific file exists
            cmd = ['rclone', 'lsf', full_remote_path]
            result = subprocess.run(cmd, capture_output=True, text=True)

            if result.returncode != 0:
                stderr_lower = (result.stderr or '').lower()
                if any(marker in stderr_lower for marker in
                       self._RCLONE_TRANSPORT_ERROR_MARKERS):
                    raise RuntimeError(
                        f"RClone lsf transport error (rc={result.returncode}): {result.stderr}")
                # Otherwise: treat as "path does not exist" (not retryable).

            # If lsf returns output, the file exists
            return bool(result.stdout.strip())

        try:
            return _attempt()
        except subprocess.SubprocessError as e:
            # If the command fails, likely the file doesn't exist
            self.logger.debug(f"Error checking file existence with rclone after retries: {e}")
            return False
        except Exception as e:
            self.logger.warning(
                f"Could not determine whether {remote_path} exists via "
                f"rclone after retries (treating as absent): {e}")
            return False