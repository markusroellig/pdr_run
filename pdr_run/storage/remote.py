"""Remote storage implementations.

This module provides remote storage implementations of the Storage interface,
allowing files to be stored and retrieved from remote systems. It includes
a base RemoteStorage class and specific implementations like SFTPStorage
for different remote storage protocols.
"""

import os
import re
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

def parse_rclone_version(output):
    """``(major, minor)`` from ``rclone version`` output (``rclone v1.75.1``,
    ``rclone v1.53.3-DEV``), or None if it cannot be read."""
    if isinstance(output, bytes):
        output = output.decode('utf-8', 'replace')
    m = re.search(r'rclone v(\d+)\.(\d+)', output if isinstance(output, str) else '')
    return (int(m.group(1)), int(m.group(2))) if m else None


class _RClonePermanentError(Exception):
    """An rclone failure that retrying cannot fix (bad credentials, missing
    bucket, key too long, fatal rclone exit status). Deliberately NOT a
    RuntimeError/OSError, so it is outside ``_RCLONE_RETRYABLE``."""


# rclone remote types that are flat object stores: "directories" are only key
# prefixes, ``rclone mkdir`` of a path inside a bucket does nothing, and
# ``mkdir``/``copyto`` towards a bucket that does not exist tries to CREATE
# the bucket (which hangs or fails on a server that does not allow it).
_OBJECT_STORE_TYPES = frozenset(
    {'s3', 'b2', 'swift', 'azureblob', 'google cloud storage'})

# rclone exit codes (rclone docs, "Exit Code"): 3 = directory not found,
# 4 = file not found, 7 = fatal error (more retries will not help).
_RC_DIR_NOT_FOUND, _RC_FILE_NOT_FOUND, _RC_FATAL = 3, 4, 7

# Lower-case stderr fragments of failures that are identical on every retry.
_RCLONE_PERMANENT_MARKERS = (
    'accessdenied', 'access denied', 'invalidaccesskeyid',
    'signaturedoesnotmatch', 'nosuchbucket', 'status code: 403',
    'status code: 401', 'entitytoolarge', 'keytoolong',
)
_RCLONE_NOT_FOUND_MARKERS = ('directory not found', 'object not found',
                             'file not found')

_MIB = 1024 * 1024
# ``lsjson --stat`` (rclone >= 1.57) returns the one object at the path (one
# HEAD on S3). Older rclone lists the whole parent prefix and filters it, which
# costs ~12 s per call in a prefix of 65 000 objects (halley, 2026-10-07).
_RCLONE_STAT_MIN_VERSION = (1, 57)
_S3_MAX_PARTS = 9000          # hard limit is 10 000; keep a margin
_S3_MAX_KEY_BYTES = 1024


class RCloneStorage(Storage):
    """RClone-based remote storage implementation.

    Object stores (S3): no ``mkdir``; an existing object is deleted
    explicitly before the upload; every call has rclone connect/idle timeouts
    and a hard subprocess timeout scaled with the file size; large files go
    through multipart upload with a chunk size that keeps the part count below
    the S3 limit; the stored object is verified (size, MD5 if the server
    knows it) after every upload. See README, "RClone storage on S3".

    Optional config keys: ``rclone_remote_type`` (skip auto-detection),
    ``rclone_chunk_size_mb`` (64), ``rclone_upload_cutoff_mb`` (256),
    ``rclone_upload_concurrency`` (4), ``rclone_contimeout_s`` (30),
    ``rclone_idle_timeout_s`` (300), ``rclone_min_rate_mb_s`` (1.0; the
    subprocess timeout is 300 s + size / rate), ``rclone_verify`` (True),
    ``rclone_max_retries`` (3), ``rclone_call_timeout_s`` (120; bound of
    every metadata call, 2.5x of it is the base of the transfer timeout),
    ``rclone_binary`` ("rclone"; path of the rclone executable, ``~`` expanded).

    Single-object lookups (overwrite check, verification, download size,
    existence) use ``lsjson --stat`` when the binary is rclone >= 1.57 and
    the parent-listing ``lsjson``/``lsf`` otherwise (``self.use_stat``).
    """

    def __init__(self, config):
        """Initialize RClone storage."""
        self.base_dir = config.get('base_dir', './data')
        self.remote = config.get('rclone_remote', 'default')
        self.mount_point = config.get('mount_point', os.path.join(self.base_dir, 'mnt'))
        self.use_mount = config.get('use_mount', False)
        self.remote_path_prefix = config.get('remote_path_prefix', None)
        self.remote_type = (config.get('rclone_remote_type') or '').lower()
        self.chunk_size_mb = int(config.get('rclone_chunk_size_mb') or 64)
        self.upload_cutoff_mb = int(config.get('rclone_upload_cutoff_mb') or 256)
        self.upload_concurrency = int(config.get('rclone_upload_concurrency') or 4)
        self.contimeout_s = float(config.get('rclone_contimeout_s') or 30)
        self.idle_timeout_s = float(config.get('rclone_idle_timeout_s') or 300)
        self.min_rate_mb_s = float(config.get('rclone_min_rate_mb_s') or 1.0)
        self.call_timeout_s = float(config.get('rclone_call_timeout_s') or 120)
        self.verify = bool(config.get('rclone_verify', True))
        retries = config.get('rclone_max_retries')
        self.max_retries = 3 if retries is None else int(retries)
        self.last_error = None   # message of the last failed operation
        self.rclone_binary = os.path.expanduser(str(config.get('rclone_binary') or 'rclone'))

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

        # Verify rclone is installed; its version decides the lookup method
        try:
            res = subprocess.run([self.rclone_binary, 'version'], check=True,
                                 stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=60)
        except (subprocess.SubprocessError, OSError):
            logger.error(f"rclone binary '{self.rclone_binary}' is not installed or not in PATH")
            raise RuntimeError(
                f"rclone binary '{self.rclone_binary}' is not installed or not in PATH")
        self.rclone_version = parse_rclone_version(res.stdout)
        self.use_stat = (self.rclone_version is not None
                         and self.rclone_version >= _RCLONE_STAT_MIN_VERSION)

    # ------------------------------------------------------------ helpers

    def _retrying(self):
        return retry_with_backoff(
            max_retries=self.max_retries, initial_delay=2.0, backoff=2.0,
            exceptions=_RCLONE_RETRYABLE,
            giveup=(FileNotFoundError, _RClonePermanentError))

    def _detect_remote_type(self):
        """Backend type of the configured remote ('s3', 'sftp', ...), cached;
        '' if it cannot be determined (then the generic, non-S3 path is used)."""
        if self.remote_type:
            return self.remote_type
        env = os.environ.get(f"RCLONE_CONFIG_{self.remote_name.upper()}_TYPE")
        if env:
            self.remote_type = env.lower()
            return self.remote_type
        try:
            res = subprocess.run([self.rclone_binary, 'listremotes', '--long'],
                                 capture_output=True, text=True, timeout=30)
            if res.returncode == 0:
                for line in str(res.stdout).splitlines():
                    name, _, rtype = line.partition(':')
                    if name.strip() == self.remote_name:
                        self.remote_type = rtype.strip().lower()
                        break
        except (subprocess.SubprocessError, OSError, TypeError):
            pass
        return self.remote_type

    def _is_object_store(self):
        return self._detect_remote_type() in _OBJECT_STORE_TYPES

    def _global_flags(self):
        flags = ['--contimeout', f"{int(self.contimeout_s)}s",
                 '--timeout', f"{int(self.idle_timeout_s)}s",
                 '--retries', '1', '--low-level-retries', '3']
        if self._detect_remote_type() == 's3':
            # never let rclone create/check a bucket: a missing bucket must
            # fail with NoSuchBucket, not hang in CreateBucket
            flags.append('--s3-no-check-bucket')
        return flags

    def _run(self, args, timeout, extra_flags=()):
        """Run ``rclone <args> <flags>``; returns the CompletedProcess.
        ``timeout`` (s) is a hard bound: a hang can never block a worker."""
        cmd = [self.rclone_binary] + list(args) + self._global_flags() + list(extra_flags)
        return subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)

    def _check(self, result, what):
        """Raise on a non-zero rclone exit: permanent errors as
        _RClonePermanentError, everything else as (retryable) RuntimeError."""
        if result.returncode == 0:
            return
        err = (result.stderr or '').strip()
        low = err.lower()
        if 'nosuchbucket' in low:
            raise _RClonePermanentError(
                f"{what}: bucket does not exist (ask an admin to create it); {err[-200:]}")
        if result.returncode == _RC_FATAL or any(m in low for m in _RCLONE_PERMANENT_MARKERS):
            raise _RClonePermanentError(f"{what} failed (rc={result.returncode}): {err[-300:]}")
        raise RuntimeError(f"{what} failed (rc={result.returncode}): {err[-300:]}")

    def _transfer_timeout(self, size):
        return self.call_timeout_s * 2.5 + size / (self.min_rate_mb_s * _MIB)

    def _upload_flags(self, size):
        if self._detect_remote_type() != 's3':
            return []
        # keep the part count below the S3 limit of 10 000
        chunk = max(self.chunk_size_mb, -(-size // (_S3_MAX_PARTS * _MIB)))
        return ['--s3-chunk-size', f"{chunk}M",
                '--s3-upload-cutoff', f"{max(self.upload_cutoff_mb, 5)}M",
                '--s3-upload-concurrency', str(self.upload_concurrency)]

    def _get_full_remote_path(self, remote_path):
        """Construct the full ``remote:path`` from base path and relative path.

        - ``remote_path_prefix`` is stripped when it is a whole leading path
          component sequence (``/a/b`` strips ``/a/b/x`` but not ``/a/bc/x``).
        - Empty and ``.`` components are dropped, so no ``//`` ends up in a key.
        - ``..`` is refused: it would leave the configured prefix.
        - Without a base path the result is ``remote:/path`` (absolute) for
          ordinary remotes and ``remote:path`` for object stores, where the
          first component is the bucket and a leading ``/`` is meaningless.
        """
        rel = str(remote_path).replace('\\', '/')
        prefix = (self.remote_path_prefix or '').replace('\\', '/').rstrip('/')
        if prefix and (rel == prefix or rel.startswith(prefix + '/')):
            rel = rel[len(prefix):]
        parts = [p for p in rel.split('/') if p not in ('', '.')]
        if '..' in parts:
            raise ValueError(f"'..' in remote path is not allowed: {remote_path!r}")
        rel = '/'.join(parts)

        base = self.remote_base_path.rstrip('/')
        if base:
            return f"{self.remote_name}:{base}/{rel}" if rel else f"{self.remote_name}:{base}"
        if rel and not self._is_object_store():
            rel = '/' + rel
        return f"{self.remote_name}:{rel}"

    @staticmethod
    def _local_md5(path):
        import hashlib
        h = hashlib.md5()
        with open(path, 'rb') as fh:
            for block in iter(lambda: fh.read(4 * _MIB), b''):
                h.update(block)
        return h.hexdigest()

    def _remote_stat(self, full_remote_path, hashes=True):
        """``{'size': int, 'md5': str|None}`` of one object, or None if absent.

        rclone >= 1.57: ``lsjson --stat`` (one object; on S3 a missing key
        comes back as a directory entry, which counts as absent). Older rclone:
        ``lsjson`` of the path, which lists the parent prefix."""
        args = ['lsjson'] + (['--hash'] if hashes else []) + (['--stat'] if self.use_stat else [])
        res = self._run(args + [full_remote_path], timeout=self.call_timeout_s)
        if res.returncode in (_RC_DIR_NOT_FOUND, _RC_FILE_NOT_FOUND) or (
                res.returncode != 0 and any(
                    m in (res.stderr or '').lower() for m in _RCLONE_NOT_FOUND_MARKERS)):
            return None
        self._check(res, 'rclone lsjson')
        import json
        data = json.loads(res.stdout or '[]')
        entries = [e for e in (data if isinstance(data, list) else [data])
                   if e and not e.get('IsDir')]
        if not entries:
            return None
        e = entries[0]
        md5 = (e.get('Hashes') or {}).get('MD5') or (e.get('Hashes') or {}).get('md5')
        return {'size': int(e.get('Size', -1)), 'md5': md5 or None}

    def delete_file(self, remote_path):
        """Delete one object/file; True if it is gone afterwards (an absent
        file counts as deleted). Bounded retry with backoff."""
        @self._retrying()
        def _attempt():
            full = self._get_full_remote_path(remote_path)
            res = self._run(['deletefile', full], timeout=self.call_timeout_s)
            if res.returncode in (_RC_DIR_NOT_FOUND, _RC_FILE_NOT_FOUND) or (
                    res.returncode != 0 and any(
                        m in (res.stderr or '').lower() for m in _RCLONE_NOT_FOUND_MARKERS)):
                return
            self._check(res, 'rclone deletefile')
        try:
            _attempt()
            return True
        except Exception as e:  # noqa: BLE001
            self.last_error = str(e)
            self.logger.error(f"Failed to delete {remote_path} with rclone: {e}")
            return False

    # -------------------------------------------------------------- store

    def store_file(self, local_path, remote_path):
        """Store a file with rclone; True only if it was stored and verified.

        Object stores (S3): there is no rename; the old object is deleted
        explicitly, then ``copyto`` writes the final key (multipart for large
        files; the object only becomes visible when the upload completes), then
        size and MD5 are checked. Any failure restarts the whole sequence
        (bounded retry with backoff), so a partial or mismatching object is
        deleted and uploaded again. Other remotes: mkdir (bounded), copyto.
        """
        @self._retrying()
        def _attempt():
            size = os.path.getsize(local_path)      # FileNotFoundError: never retried
            full = self._get_full_remote_path(remote_path)
            object_store = self._is_object_store()
            if object_store and len(full.split(':', 1)[1].encode()) > _S3_MAX_KEY_BYTES:
                raise _RClonePermanentError(
                    f"object name longer than {_S3_MAX_KEY_BYTES} bytes: {full}")

            if object_store:
                if self._remote_stat(full) is not None:
                    res = self._run(['deletefile', full], timeout=self.call_timeout_s)
                    if res.returncode not in (_RC_DIR_NOT_FOUND, _RC_FILE_NOT_FOUND):
                        self._check(res, 'rclone deletefile (replace)')
            else:
                remote_dir = os.path.dirname(full.split(':', 1)[1])
                if remote_dir:
                    self._check(self._run(['mkdir', f"{self.remote_name}:{remote_dir}"],
                                          timeout=self.call_timeout_s), 'rclone mkdir')

            res = self._run(['copyto', local_path, full],
                            timeout=self._transfer_timeout(size),
                            extra_flags=self._upload_flags(size))
            self._check(res, 'rclone copyto')

            if self.verify:
                self._verify(full, local_path, size)
            self.logger.info(f"Stored {local_path} as {full}")

        try:
            _attempt()
            self.last_error = None
            return True
        except Exception as e:  # noqa: BLE001 - public contract: bool, never raise
            self.last_error = str(e)
            self.logger.error(f"Failed to store {local_path} with rclone after retries: {e}")
            return False

    def _verify(self, full, local_path, size):
        """Size always; MD5 whenever the server reports one (single-part
        objects: the ETag; rclone also stores the MD5 of multipart objects
        as metadata). Raises RuntimeError (retryable) on any mismatch."""
        stat = self._remote_stat(full)
        if stat is None:
            raise RuntimeError(f"verification failed: {full} not found after upload")
        if stat['size'] != size:
            raise RuntimeError(
                f"verification failed: {full} has {stat['size']} bytes, local file {size}")
        if stat['md5']:
            local = self._local_md5(local_path)
            if stat['md5'].lower() != local:
                raise RuntimeError(
                    f"verification failed: MD5 of {full} is {stat['md5']}, local {local}")

    def retrieve_file(self, remote_path, local_path):
        """Download a file from remote storage using rclone.

        Bounded retry with backoff on transient rclone/transport failures.
        """
        @self._retrying()
        def _attempt():
            full_remote_path = self._get_full_remote_path(remote_path)

            # Ensure local directory exists
            local_dir = os.path.dirname(local_path)
            if local_dir:
                os.makedirs(local_dir, exist_ok=True)

            stat = self._remote_stat(full_remote_path) if self._is_object_store() else None
            timeout = self._transfer_timeout(stat['size'] if stat else 0) if stat else 3600.0
            res = self._run(['copyto', full_remote_path, local_path], timeout=timeout)
            self._check(res, 'rclone retrieve')
            if stat and os.path.getsize(local_path) != stat['size']:
                raise RuntimeError(
                    f"downloaded {local_path} has {os.path.getsize(local_path)} bytes, "
                    f"remote {stat['size']}")
            self.logger.info(f"Retrieved {full_remote_path} to {local_path}")

        try:
            _attempt()
            self.last_error = None
            return True
        except Exception as e:  # noqa: BLE001
            self.last_error = str(e)
            self.logger.error(f"Failed to download file with rclone after retries: {e}")
            return False

    def list_files(self, path):
        """List the entries of one remote directory with a single ``lsf``
        (sub-directories carry a trailing ``/``). A directory that does not
        exist lists as empty. Bounded retry on transient failures."""
        @self._retrying()
        def _attempt():
            full_remote_path = self._get_full_remote_path(path)
            res = self._run(['lsf', full_remote_path], timeout=self.call_timeout_s)
            if res.returncode == _RC_DIR_NOT_FOUND or (
                    res.returncode != 0 and any(
                        m in (res.stderr or '').lower() for m in _RCLONE_NOT_FOUND_MARKERS)):
                return []
            self._check(res, 'rclone lsf')
            return [line.strip() for line in res.stdout.splitlines() if line.strip()]

        try:
            result = _attempt()
            self.last_error = None
            return result
        except Exception as e:  # noqa: BLE001
            self.last_error = str(e)
            self.logger.error(f"Failed to list files with rclone after retries: {e}")
            return []

    def sync_directory(self, local_dir, remote_dir):
        """Copy an entire directory to remote storage. Bounded retry on
        transient rclone/transport failures. No mkdir on object stores."""
        @self._retrying()
        def _attempt():
            full_remote_path = self._get_full_remote_path(remote_dir)
            if not self._is_object_store():
                self._check(self._run(['mkdir', full_remote_path], timeout=self.call_timeout_s),
                            'rclone mkdir')
            total = sum(os.path.getsize(os.path.join(d, f))
                        for d, _, fs in os.walk(local_dir) for f in fs)
            res = self._run(['copy', local_dir, full_remote_path],
                            timeout=self._transfer_timeout(total),
                            extra_flags=self._upload_flags(total))
            self._check(res, 'rclone copy')
            self.logger.info(f"Synchronized {local_dir} to {full_remote_path}")

        try:
            _attempt()
            self.last_error = None
            return True
        except Exception as e:  # noqa: BLE001
            self.last_error = str(e)
            self.logger.error(f"Failed to sync directory after retries: {e}")
            return False

    def file_exists(self, remote_path):
        """Check if a file exists on the remote: one ``lsjson --stat`` with
        rclone >= 1.57 (``self.use_stat``), otherwise one ``lsf``.

        ``rclone lsf`` exits 0 with empty output for a missing file in an
        existing directory, and 3 ("directory not found") if the directory
        or bucket is missing: both mean "absent". Any other failure
        (timeout, connection, credentials) must not be mistaken for "absent"
        (a stored multi-hour node would be recomputed): it is retried, and if
        it persists it is logged as an ERROR and False is returned (the
        contract of the other backends).
        """
        @self._retrying()
        def _attempt():
            full_remote_path = self._get_full_remote_path(remote_path)
            if self.use_stat:
                return self._remote_stat(full_remote_path, hashes=False) is not None
            res = self._run(['lsf', full_remote_path], timeout=self.call_timeout_s)
            if res.returncode in (_RC_DIR_NOT_FOUND, _RC_FILE_NOT_FOUND) or (
                    res.returncode != 0 and any(
                        m in (res.stderr or '').lower() for m in _RCLONE_NOT_FOUND_MARKERS)):
                return False
            self._check(res, 'rclone lsf')
            return bool(res.stdout.strip())

        try:
            result = _attempt()
            self.last_error = None
            return result
        except Exception as e:  # noqa: BLE001
            self.last_error = str(e)
            self.logger.error(
                f"Could not determine whether {remote_path} exists via rclone "
                f"(treated as absent): {e}")
            return False

    def bucket_of_remote(self):
        """Bucket (first path component) of an object-store remote with a
        base path, e.g. ``noices`` for ``kosmatau:noices/grid1``; None if the
        remote has no base path or is not an object store."""
        if not self._is_object_store():
            return None
        parts = [p for p in self.remote_base_path.split('/') if p]
        return parts[0] if parts else None

    def bucket_exists(self, bucket):
        """True/False if *bucket* is/is not listed by ``rclone lsd remote:``;
        None if the listing itself failed (no permission, no connection).
        pdr_run never creates buckets."""
        try:
            res = self._run(['lsd', f"{self.remote_name}:"], timeout=self.call_timeout_s)
        except (subprocess.SubprocessError, OSError) as e:
            self.last_error = str(e)
            return None
        if res.returncode != 0:
            self.last_error = (res.stderr or '').strip()[-300:]
            return None
        names = [line.split()[-1] for line in res.stdout.splitlines() if line.strip()]
        return bucket in names
