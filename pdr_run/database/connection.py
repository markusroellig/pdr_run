"""Database connection management for PDR framework.

This module maintains backward compatibility while delegating to the new
DatabaseManager for actual implementation.
"""

import os
import logging
import warnings
from typing import Optional, Tuple, Dict, Any
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker, scoped_session, Session
from sqlalchemy.engine import Engine

from pdr_run.database.db_manager import get_db_manager, DatabaseManager

logger = logging.getLogger("dev")

# Backward compatibility warning
warnings.warn(
    "Direct use of connection.py is deprecated. Use db_manager.py instead.",
    DeprecationWarning,
    stacklevel=2
)

# Global variables for backward compatibility
_ENGINE: Optional[Engine] = None
_SESSION_FACTORY: Optional[scoped_session] = None
_DB_MANAGER: Optional[DatabaseManager] = None


def get_db_uri(config: Dict[str, Any]) -> str:
    """Construct the database URI from the configuration.
    
    DEPRECATED: Use DatabaseManager._build_connection_string() instead.
    """
    manager = DatabaseManager(config)
    return manager._build_connection_string()


def validate_db_config(config: Dict[str, Any]) -> bool:
    """Validate database configuration.
    
    DEPRECATED: Validation is now handled internally by DatabaseManager.
    """
    try:
        manager = DatabaseManager(config)
        return manager.test_connection()
    except Exception:
        return False


def init_db(config: Optional[Dict[str, Any]] = None) -> Tuple[Session, Engine]:
    """Initialize database connection.
    
    DEPRECATED: Use get_db_manager() instead.
    """
    global _ENGINE, _SESSION_FACTORY, _DB_MANAGER
    
    # Get or create database manager
    _DB_MANAGER = get_db_manager(config)
    
    # Create tables if needed
    _DB_MANAGER.create_tables()
    
    # Get engine and session for backward compatibility
    _ENGINE = _DB_MANAGER.engine
    _SESSION_FACTORY = _DB_MANAGER.session_factory
    
    return _SESSION_FACTORY(), _ENGINE


def get_session() -> Session:
    """Get a database session.
    
    DEPRECATED: Use get_db_manager().get_session() instead.
    """
    warnings.warn(
        "get_session from connection.py is deprecated. Use get_db_manager().get_session() instead.",
        DeprecationWarning,
        stacklevel=2
    )
    
    global _DB_MANAGER
    
    if _DB_MANAGER is None:
        _DB_MANAGER = get_db_manager()
        
    return _DB_MANAGER.get_session()


def get_engine() -> Engine:
    """Get SQLAlchemy engine.
    
    DEPRECATED: Use get_db_manager().engine instead.
    """
    global _DB_MANAGER
    
    if _DB_MANAGER is None:
        _DB_MANAGER = get_db_manager()
        
    return _DB_MANAGER.engine


def get_db_connection():
    """Return a raw DBAPI connection for tests needing .cursor()/.close().
    
    DEPRECATED: Use get_db_manager().engine.raw_connection() instead.
    """
    engine = get_engine()
    return engine.raw_connection()


def get_database_config() -> Dict[str, Any]:
    """Get database configuration with environment variable overrides.
    
    DEPRECATED: Configuration is now handled internally by DatabaseManager.
    """
    from pdr_run.config.default_config import DATABASE_CONFIG
    config = DATABASE_CONFIG.copy()
    
    # Override with environment variables if they exist
    db_password = os.environ.get('PDR_DB_PASSWORD')
    if db_password:
        config['password'] = db_password
    
    return config


# Remove deprecated imports at the bottom of the file
# The JSON handler imports should be removed from here


# Add compatibility imports with deprecation warnings
# This needs to be at the bottom to avoid circular imports
# def _import_json_handler(name):
#     """Import a function from json_handlers module."""
#     import importlib
#     module = importlib.import_module('pdr_run.database.json_handlers')
#     return getattr(module, name)

# def register_json_template(*args, **kwargs):
#     """Compatibility function for register_json_template."""
#     warnings.warn(
#         "Importing register_json_template from connection is deprecated. "
#         "Use pdr_run.database.json_handlers.register_json_template instead.",
#         DeprecationWarning,
#         stacklevel=2
#     )
#     return _import_json_handler('register_json_template')(*args, **kwargs)

# def process_json_template(*args, **kwargs):
#     """Compatibility function for process_json_template."""
#     warnings.warn(
#         "Importing process_json_template from connection is deprecated. "
#         "Use pdr_run.database.json_handlers.process_json_template instead.",
#         DeprecationWarning,
#         stacklevel=2
#     )
#     return _import_json_handler('process_json_template')(*args, **kwargs)

# def prepare_job_json(*args, **kwargs):
#     """Compatibility function for prepare_job_json."""
#     warnings.warn(
#         "Importing prepare_job_json from connection is deprecated. "
#         "Use pdr_run.database.json_handlers.prepare_job_json instead.",
#         DeprecationWarning, 
#         stacklevel=2
#     )
#     return _import_json_handler('prepare_job_json')(*args, **kwargs)

# def validate_json(*args, **kwargs):
#     """Compatibility function for validate_json."""
#     warnings.warn(
#         "Importing validate_json from connection is deprecated. "
#         "Use pdr_run.database.json_handlers.validate_json instead.",
#         DeprecationWarning,
#         stacklevel=2
#     )
#     return _import_json_handler('validate_json')(*args, **kwargs)

# Add any other functions needed by tests
