"""Database connection management for PDR framework.

This module provides a centralized database connection management system for the PDR 
(Photo-Dissociation Region) framework. It handles the initialization, configuration, and 
maintenance of database connections used throughout the application.

The module supports multiple database types:
    - SQLite: For lightweight, file-based database storage
    - MySQL: For more robust, server-based database management

Key components:
    - Connection initialization with configurable parameters
    - Session management for database operations
    - Engine access for direct database interactions
    - Automatic table creation based on SQLAlchemy models

Global objects:
    - Session: SQLAlchemy session factory for creating new database sessions
    - engine: SQLAlchemy engine instance for direct database access

Configuration is provided via a dictionary with the following structure:
    {
        'type': 'sqlite' or 'mysql',
        'path': '/path/to/sqlite/file' (for SQLite only),
        'user': 'username' (for MySQL),
        'password': 'password' (for MySQL),
        'host': 'hostname' (for MySQL),
        'port': port_number (for MySQL),
        'database': 'database_name' (for MySQL)
    }

If no configuration is provided, it defaults to the settings in DATABASE_CONFIG
from pdr_run.config.default_config.

Typical usage:
    from pdr_run.database.connection import get_session, init_db
    
    # Use default configuration
    session = get_session()
    
    # Custom configuration
    custom_config = {
        'type': 'sqlite',
        'path': '/custom/path/to/database.db'
    }
    session, engine = init_db(custom_config)
    
    # Use the session for database operations
    results = session.query(MyModel).all()
"""

import os
import logging
import warnings
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker, scoped_session

logger = logging.getLogger("dev")

# Global variables for database connection
_ENGINE = None
_SESSION_FACTORY = None

def init_db(config=None):
    """Initialize database connection."""
    global _ENGINE, _SESSION_FACTORY
    
    # Database initialization code...
    
    # Default to SQLite in-memory database if no config
    if config is None:
        # Import from default_config
        from pdr_run.config.default_config import DATABASE_CONFIG
        config = DATABASE_CONFIG
        
    if isinstance(config, str):
        connection_string = config
    else:
        db_type = config.get('type', 'sqlite')
        if db_type == 'sqlite':
            connection_string = f"sqlite:///{config.get('path', 'kosma_tau.db')}"
        elif db_type == 'mysql':
            # Format MySQL connection string
            user = config.get('username', '')
            password = config.get('password', '')
            host = config.get('host', 'localhost')
            port = config.get('port', 3306)
            database = config.get('database', 'kosma_tau')
            
            # Build the MySQL connection string
            connection_string = (
                f"mysql+mysqlconnector://{user}:{password}@{host}:{port}/{database}"
            )
            
            logger.info(f"Using MySQL connection to {host}:{port}/{database}")
        else:
            # Handle other database types
            raise ValueError(f"Unsupported database type: {db_type}")
    
    # Create engine with appropriate connection parameters
    connect_args = {}
    if 'pool_recycle' in config:
        connect_args['pool_recycle'] = config.get('pool_recycle')
    
    _ENGINE = create_engine(
        connection_string, 
        connect_args=connect_args,
        pool_pre_ping=config.get('pool_pre_ping', False)
    )
    
    # Create session factory
    _SESSION_FACTORY = scoped_session(sessionmaker(bind=_ENGINE))
    
    # Get session
    session = _SESSION_FACTORY()
    
    # Create tables if they don't exist
    from .models import Base
    Base.metadata.create_all(_ENGINE)
    
    logger.info(f"Database initialized: {connection_string.split('@')[-1]}")  # Log without credentials
    
    return session, _ENGINE

def get_session():
    """Get a database session."""
    global _SESSION_FACTORY
    
    if _SESSION_FACTORY is None:
        # Initialize with default settings
        logger.info("Initializing new database connection")
        init_db()
    else:
        logger.debug("Using existing database connection")
    
    session = _SESSION_FACTORY()
    logger.debug(f"Created new database session: {id(session)}")
    return session

def get_engine():
    """Get SQLAlchemy engine."""
    global _ENGINE
    
    if _ENGINE is None:
        # Initialize with default settings
        init_db()
    
    return _ENGINE

def get_db_connection():
    """Return a raw DBAPI connection for tests needing .cursor()/.close()."""
    engine = get_engine()
    return engine.raw_connection()  # Allows .cursor() and .close()


# Add compatibility imports with deprecation warnings
# This needs to be at the bottom to avoid circular imports
def _import_json_handler(name):
    """Import a function from json_handlers module."""
    import importlib
    module = importlib.import_module('pdr_run.database.json_handlers')
    return getattr(module, name)

def register_json_template(*args, **kwargs):
    """Compatibility function for register_json_template."""
    warnings.warn(
        "Importing register_json_template from connection is deprecated. "
        "Use pdr_run.database.json_handlers.register_json_template instead.",
        DeprecationWarning,
        stacklevel=2
    )
    return _import_json_handler('register_json_template')(*args, **kwargs)

def process_json_template(*args, **kwargs):
    """Compatibility function for process_json_template."""
    warnings.warn(
        "Importing process_json_template from connection is deprecated. "
        "Use pdr_run.database.json_handlers.process_json_template instead.",
        DeprecationWarning,
        stacklevel=2
    )
    return _import_json_handler('process_json_template')(*args, **kwargs)

def prepare_job_json(*args, **kwargs):
    """Compatibility function for prepare_job_json."""
    warnings.warn(
        "Importing prepare_job_json from connection is deprecated. "
        "Use pdr_run.database.json_handlers.prepare_job_json instead.",
        DeprecationWarning, 
        stacklevel=2
    )
    return _import_json_handler('prepare_job_json')(*args, **kwargs)

def validate_json(*args, **kwargs):
    """Compatibility function for validate_json."""
    warnings.warn(
        "Importing validate_json from connection is deprecated. "
        "Use pdr_run.database.json_handlers.validate_json instead.",
        DeprecationWarning,
        stacklevel=2
    )
    return _import_json_handler('validate_json')(*args, **kwargs)

# Add any other functions needed by tests
