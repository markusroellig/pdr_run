"""Base storage class and utilities."""

import os
import logging
from abc import ABC, abstractmethod

logger = logging.getLogger("dev")

def get_storage_backend(config=None):
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
    logger.debug(f"Config parameter value: {config}")
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
        else:
            rclone_config = {
                'base_dir': os.environ.get("PDR_STORAGE_DIR", "/tmp"),
                'rclone_remote': os.environ.get("PDR_STORAGE_RCLONE_REMOTE", "default"),
                'use_mount': os.environ.get("PDR_STORAGE_USE_MOUNT", "false").lower() == "true",
                'remote_path_prefix': os.environ.get("PDR_STORAGE_REMOTE_PATH_PREFIX", None)
            }
        logger.debug(f"RClone config: {rclone_config}")
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