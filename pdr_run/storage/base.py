"""Base storage class and utilities."""

import os
import logging
import shutil
from abc import ABC, abstractmethod
from pdr_run.utils.logging import sanitize_config

logger = logging.getLogger("dev")


class LocalCopy:
    """Local mirror of the files stored on a remote backend (rclone/sftp).

    ``storage.use_local_copy``: after every successful (verified) upload the
    stored file is also written under *root*, with the same relative key as on
    the remote, i.e. the path below ``remote_path_prefix`` (else below
    ``base_dir``). Files are copied exactly as stored (a ``.gz`` stays a
    ``.gz``). The copy is atomic (``<name>.part``, then rename) and replaces an
    older copy, so ``--rerun`` keeps it in sync. A failure is logged as a
    WARNING and never fails the job.
    """

    def __init__(self, root, prefix=None, base_dir=None):
        self.root = os.path.abspath(root)
        self.prefix = prefix
        self.base_dir = base_dir

    def relative_key(self, remote_path):
        """Key of *remote_path* relative to the prefix (``..`` is refused)."""
        rel = str(remote_path).replace('\\', '/')
        for strip in (self.prefix, self.base_dir):
            strip = (strip or '').replace('\\', '/').rstrip('/')
            if strip and (rel == strip or rel.startswith(strip + '/')):
                rel = rel[len(strip):]
                break
        parts = [p for p in rel.split('/') if p not in ('', '.')]
        if '..' in parts:
            raise ValueError(f"'..' in path is not allowed: {remote_path!r}")
        return '/'.join(parts)

    def local_path(self, remote_path):
        return os.path.join(self.root, self.relative_key(remote_path))

    def store(self, local_path, remote_path):
        """Copy *local_path* to its local-copy location; True on success,
        False (WARNING logged) on any failure. Never raises."""
        part = None
        try:
            dest = self.local_path(remote_path)
            if os.path.abspath(local_path) == os.path.abspath(dest):
                return True
            os.makedirs(os.path.dirname(dest), exist_ok=True)
            part = dest + '.part'
            shutil.copyfile(local_path, part)
            os.replace(part, dest)
            part = None
            logger.info(f"Kept local copy {dest}")
            return True
        except Exception as exc:  # noqa: BLE001 - must never fail the job
            logger.warning(f"Local copy of {remote_path} failed (job not affected): {exc}")
            return False
        finally:
            if part and os.path.exists(part):
                try:
                    os.remove(part)
                except OSError:
                    pass


def resolve_local_copy(storage_config, storage_type):
    """The ``LocalCopy`` for a ``storage`` config section, or None.

    None for ``local`` storage (the stored file is the local file), when
    ``use_local_copy`` is false, or when no root can be determined. Root:
    ``storage.local_copy_dir``; otherwise ``base_dir`` for rclone. For sftp,
    ``base_dir`` is a path on the server, so ``local_copy_dir`` is required.
    """
    sc = storage_config or {}
    if storage_type == 'local' or not sc.get('use_local_copy', True):
        return None
    root = sc.get('local_copy_dir')
    if not root and storage_type == 'rclone':
        root = sc.get('base_dir')
    if not root:
        return None
    return LocalCopy(root, sc.get('remote_path_prefix'), sc.get('base_dir'))


def get_storage_backend(config=None):
    """Backend for *config*; remote backends carry ``.local_copy`` (see
    ``LocalCopy``, None if disabled)."""
    backend = _create_storage_backend(config)
    sc = (config or {}).get('storage')
    if sc:
        backend.local_copy = resolve_local_copy(sc, sc.get('type', 'local'))
    return backend


def _create_storage_backend(config=None):
    """Get the appropriate storage backend based on configuration.
    
    Args:
        config (dict, optional): Storage configuration from config file
        
    Returns:
        Storage: Storage backend instance
    """
    import logging
    logger = logging.getLogger("dev")
    
    # Log all inputs
    logger.debug("=== STORAGE BACKEND SELECTION DEBUG ===")
    logger.debug(f"Config parameter type: {type(config)}")
    logger.debug(f"Config parameter value: {sanitize_config(config) if isinstance(config, dict) else config}")
    # Check environment variables first
    env_storage_type = os.environ.get("PDR_STORAGE_TYPE", "local")
    logger.debug(f"PDR_STORAGE_TYPE environment variable: {env_storage_type}")
    
    # Try config first, then environment variables
    if config and 'storage' in config:
        storage_config = config['storage']
        storage_type = storage_config.get('type', 'local')
        logger.debug(f"Using storage config from file: type={storage_type}")
    else:
        storage_type = os.environ.get("PDR_STORAGE_TYPE", "local")
        logger.debug(f"Using storage config from environment: type={storage_type}")
    
    if storage_type == "local":
        logger.debug("Creating LocalStorage backend")
        from pdr_run.storage.local import LocalStorage
        if config and 'storage' in config:
            storage_dir = config['storage'].get('base_dir', '/tmp/pdr_storage')
        else:
            storage_dir = os.environ.get("PDR_STORAGE_DIR", "/tmp/pdr_storage")
        logger.debug(f"LocalStorage base_dir: {storage_dir}")
        return LocalStorage(storage_dir)
    elif storage_type == "sftp":
        logger.debug("Creating SFTPStorage backend")
        from pdr_run.storage.remote import SFTPStorage
        if config and 'storage' in config:
            sc = config['storage']
            host = sc.get('host', 'localhost')
            user = sc.get('username', '')
            password = sc.get('password') or os.environ.get("PDR_STORAGE_PASSWORD", "")
            base_dir = sc.get('base_dir', '/tmp')
            logger.debug(f"SFTP config from file - host: {host}, user: {user}, base_dir: {base_dir}")
            
            # Enhanced password debugging
            config_password = sc.get('password')
            env_password = os.environ.get("PDR_STORAGE_PASSWORD", "")
            logger.debug(f"Config password: {'SET' if config_password else 'NULL'}")
            logger.debug(f"Environment PDR_STORAGE_PASSWORD: {'SET ({} chars)' if env_password else 'NOT SET'}")
            logger.debug(f"Final password: {'SET ({} chars)' if password else 'EMPTY'}")
            
        else:
            host = os.environ.get("PDR_STORAGE_HOST", "localhost")
            user = os.environ.get("PDR_STORAGE_USER", "")
            password = os.environ.get("PDR_STORAGE_PASSWORD", "")
            base_dir = os.environ.get("PDR_STORAGE_DIR", "/tmp")
            logger.debug(f"SFTP config from env - host: {host}, user: {user}, base_dir: {base_dir}")
            logger.debug(f"Environment password: {'SET ({} chars)' if password else 'NOT SET'}")

        logger.debug(f"Creating SFTPStorage({host}, {user}, '***', {base_dir})")
        return SFTPStorage(host, user, password, base_dir)
    elif storage_type == "rclone":
        logger.debug("Creating RCloneStorage backend")
        from pdr_run.storage.remote import RCloneStorage
        if config and 'storage' in config:
            rclone_config = {
                'base_dir': config['storage'].get('base_dir', '/tmp'),
                'rclone_remote': config['storage'].get('rclone_remote', 'default'),
                'use_mount': config['storage'].get('use_mount', False),
                'remote_path_prefix': config['storage'].get('remote_path_prefix', None)
            }
            # tuning/override keys (rclone_remote_type, rclone_chunk_size_mb, ...)
            rclone_config.update({k: v for k, v in config['storage'].items()
                                  if k.startswith('rclone_') and k != 'rclone_remote'})
        else:
            rclone_config = {
                'base_dir': os.environ.get("PDR_STORAGE_DIR", "/tmp"),
                'rclone_remote': os.environ.get("PDR_STORAGE_RCLONE_REMOTE", "default"),
                'use_mount': os.environ.get("PDR_STORAGE_USE_MOUNT", "false").lower() == "true",
                'remote_path_prefix': os.environ.get("PDR_STORAGE_REMOTE_PATH_PREFIX", None)
            }
        logger.debug(f"RClone config: {sanitize_config(rclone_config)}")
        return RCloneStorage(rclone_config)
    elif storage_type == "remote":
        from pdr_run.storage.remote import RemoteStorage
        if config and 'storage' in config:
            sc = config['storage']
            host = sc.get('host', 'localhost')
            user = sc.get('username', '')
            password = sc.get('password') or os.environ.get("PDR_STORAGE_PASSWORD", "")
            base_dir = sc.get('base_dir', '/tmp')
        else:
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