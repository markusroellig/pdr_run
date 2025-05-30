"""Database manager providing a clean abstraction layer for database operations.

This module provides a unified interface for database operations across different
database backends (SQLite, MySQL, PostgreSQL) with proper password handling,
connection pooling, and security measures.
"""

import os
import logging
from typing import Optional, Dict, Any, Union
from contextlib import contextmanager
from urllib.parse import quote_plus

from sqlalchemy import create_engine, event
from sqlalchemy.engine import Engine
from sqlalchemy.orm import sessionmaker, Session
from sqlalchemy.pool import NullPool, QueuePool, StaticPool

from pdr_run.database.base import Base
import pdr_run.database.models 

logger = logging.getLogger('dev')


class DatabaseManager:
    """Centralized database management with proper abstraction and security."""
    
    def __init__(self, config: Optional[Dict[str, Any]] = None):
        """Initialize database manager with configuration.
        
        Args:
            config: Database configuration dictionary. If None, uses environment/defaults.
        """
        self.config = self._load_config(config)
        self._engine: Optional[Engine] = None
        self._session_factory: Optional[sessionmaker] = None
        
    def _load_config(self, config: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        """Load and validate database configuration with proper precedence.
        
        Precedence order:
        1. Environment variables (highest)
        2. Provided config dictionary
        3. Default configuration (lowest)
        """
        from pdr_run.config.default_config import DATABASE_CONFIG
        
        # Start with defaults
        final_config = DATABASE_CONFIG.copy()
        
        # Override with provided config
        if config:
            final_config.update(config)
            
        # Override with environment variables (highest precedence)
        env_overrides = {
            'PDR_DB_TYPE': 'type',
            'PDR_DB_HOST': 'host',
            'PDR_DB_PORT': 'port',
            'PDR_DB_DATABASE': 'database',
            'PDR_DB_USERNAME': 'username',
            'PDR_DB_PASSWORD': 'password',
            'PDR_DB_FILE': 'path',  # For SQLite
        }
        
        for env_var, config_key in env_overrides.items():
            env_value = os.environ.get(env_var)
            if env_value is not None:
                # Special handling for port (must be int)
                if config_key == 'port' and env_value:
                    try:
                        final_config[config_key] = int(env_value)
                    except ValueError:
                        logger.warning(f"Invalid port value in {env_var}: {env_value}")
                else:
                    final_config[config_key] = env_value

        # VALIDATE CONFIGURATION - NO FALLBACK TO SQLITE
        self._validate_config(final_config)
                    
        # Log configuration (without password)
        safe_config = final_config.copy()
        if 'password' in safe_config and safe_config['password']:
            safe_config['password'] = '***'
        logger.debug(f"Database configuration: {safe_config}")
        
        return final_config
    
    def _validate_config(self, config: Dict[str, Any]) -> None:
        """Validate database configuration and report missing information.
        
        Raises:
            ValueError: If required configuration is missing with detailed instructions.
        """
        db_type = config.get('type', 'sqlite')
        
        if db_type == 'sqlite':
            # SQLite validation
            path = config.get('path')
            if not path:
                raise ValueError(
                    "SQLite database configuration incomplete:\n"
                    "Missing required field: 'path'\n\n"
                    "Please set one of:\n"
                    "  - Environment variable: PDR_DB_FILE=/path/to/database.db\n"
                    "  - Configuration file: database.path: /path/to/database.db\n\n"
                    "Example:\n"
                    "  export PDR_DB_FILE=/home/user/pdr/kosma_tau.db"
                )
                
        elif db_type == 'mysql':
            # MySQL validation - fix the logic here
            missing_fields = []
            required_fields = {
                'host': 'PDR_DB_HOST',
                'database': 'PDR_DB_DATABASE', 
                'username': 'PDR_DB_USERNAME',
                'password': 'PDR_DB_PASSWORD'
            }
            
            for field, env_var in required_fields.items():
                value = config.get(field)
                # Fix: Check for None, empty string, or missing key
                if value is None or value == '':
                    missing_fields.append(f"  - {field}: Set {env_var}=<value>")
        
            if missing_fields:
                # Check if MySQL driver is available
                try:
                    import mysql.connector
                except ImportError:
                    driver_msg = "\nAdditionally, MySQL driver is not installed:\n  pip install mysql-connector-python\n"
                else:
                    driver_msg = ""
                    
                raise ValueError(
                    f"MySQL database configuration incomplete:\n"
                    f"Missing required fields:\n" + "\n".join(missing_fields) + "\n\n"
                    f"Please set the following environment variables:\n"
                    f"  export PDR_DB_TYPE=mysql\n"
                    f"  export PDR_DB_HOST=your_mysql_host\n"
                    f"  export PDR_DB_DATABASE=your_database_name\n"
                    f"  export PDR_DB_USERNAME=your_username\n"
                    f"  export PDR_DB_PASSWORD=your_password\n\n"
                    f"Optional settings:\n"
                    f"  export PDR_DB_PORT=3306  # Default is 3306\n"
                    + driver_msg
                )
                
        elif db_type == 'postgresql':
            # PostgreSQL validation - fix the logic here  
            missing_fields = []
            required_fields = {
                'host': 'PDR_DB_HOST',
                'database': 'PDR_DB_DATABASE',
                'username': 'PDR_DB_USERNAME', 
                'password': 'PDR_DB_PASSWORD'
            }
            
            for field, env_var in required_fields.items():
                value = config.get(field)
                # Fix: Check for None, empty string, or missing key
                if value is None or value == '':
                    missing_fields.append(f"  - {field}: Set {env_var}=<value>")
        
            if missing_fields:
                # Check if PostgreSQL driver is available
                try:
                    import psycopg2
                except ImportError:
                    driver_msg = "\nAdditionally, PostgreSQL driver is not installed:\n  pip install psycopg2-binary\n"
                else:
                    driver_msg = ""
                    
                raise ValueError(
                    f"PostgreSQL database configuration incomplete:\n"
                    f"Missing required fields:\n" + "\n".join(missing_fields) + "\n\n"
                    f"Please set the following environment variables:\n"
                    f"  export PDR_DB_TYPE=postgresql\n"
                    f"  export PDR_DB_HOST=your_postgres_host\n"
                    f"  export PDR_DB_DATABASE=your_database_name\n"
                    f"  export PDR_DB_USERNAME=your_username\n"
                    f"  export PDR_DB_PASSWORD=your_password\n\n"
                    f"Optional settings:\n"
                    f"  export PDR_DB_PORT=5432  # Default is 5432\n"
                    + driver_msg
                )
        else:
            raise ValueError(
                f"Unsupported database type: '{db_type}'\n\n"
                f"Supported types:\n"
                f"  - sqlite: For local SQLite databases\n"
                f"  - mysql: For MySQL databases\n"
                f"  - postgresql: For PostgreSQL databases\n\n"
                f"Set PDR_DB_TYPE to one of the supported values."
            )
        
    def _build_connection_string(self) -> str:
        """Build database connection string with proper escaping and security."""
        db_type = self.config.get('type', 'sqlite')
        
        if db_type == 'sqlite':
            path = self.config.get('path', 'kosma_tau.db')
            return f"sqlite:///{path}"
            
        elif db_type == 'mysql':
            return self._build_mysql_connection_string()
            
        elif db_type == 'postgresql':
            return self._build_postgresql_connection_string()
            
        else:
            raise ValueError(f"Unsupported database type: {db_type}")
            
    def _build_mysql_connection_string(self) -> str:
        """Build MySQL connection string with proper password handling."""
        user = self.config.get('username', 'root')
        password = self.config.get('password', '')
        host = self.config.get('host', 'localhost')
        port = self.config.get('port', 3306)
        database = self.config.get('database', 'kosma_tau')
        
        # Build connection string with proper password handling
        if password:
            # URL-encode password to handle special characters
            encoded_password = quote_plus(str(password))
            auth = f"{user}:{encoded_password}"
        else:
            auth = user
            
        return f"mysql+mysqlconnector://{auth}@{host}:{port}/{database}"
        
    def _build_postgresql_connection_string(self) -> str:
        """Build PostgreSQL connection string with proper password handling."""
        user = self.config.get('username', 'postgres')
        password = self.config.get('password', '')
        host = self.config.get('host', 'localhost')
        port = self.config.get('port', 5432)
        database = self.config.get('database', 'kosma_tau')
        
        # Build connection string with proper password handling
        if password:
            # URL-encode password to handle special characters
            encoded_password = quote_plus(str(password))
            auth = f"{user}:{encoded_password}"
        else:
            auth = user
            
        return f"postgresql+psycopg2://{auth}@{host}:{port}/{database}"
        
    def _create_engine(self) -> Engine:
        """Create SQLAlchemy engine with proper pooling and configuration."""
        connection_string = self._build_connection_string()
        
        # Log connection info without password
        safe_string = connection_string
        if '@' in safe_string and ':' in safe_string.split('@')[0]:
            # Mask password in connection string
            parts = safe_string.split('@')
            user_pass = parts[0].split('://')[-1]
            if ':' in user_pass:
                user = user_pass.split(':')[0]
                parts[0] = parts[0].replace(user_pass, f"{user}:***")
                safe_string = '@'.join(parts)
        logger.info(f"Creating database engine: {safe_string}")
        
        # Configure engine options based on database type
        engine_options = self._get_engine_options()
        
        # Create engine
        engine = create_engine(connection_string, **engine_options)
        
        # Add event listeners for connection management
        self._setup_engine_events(engine)
        
        return engine
        
    def _get_engine_options(self) -> Dict[str, Any]:
        """Get engine options based on database type."""
        db_type = self.config.get('type', 'sqlite')
        
        options: Dict[str, Any] = {
            'echo': False,  # Don't log SQL statements (security)
            'future': True,  # Use SQLAlchemy 2.0 API
        }
        
        if db_type == 'sqlite':
            path = self.config.get('path', 'kosma_tau.db')
            
            # Use StaticPool for in-memory databases to ensure same connection
            if path == ':memory:':
                options.update({
                    'connect_args': {'check_same_thread': False},
                    'poolclass': StaticPool,  # Ensures same connection for all operations
                })
                logger.debug("Using StaticPool for SQLite in-memory database")
            else:
                options.update({
                    'connect_args': {'check_same_thread': False},
                    'poolclass': NullPool,  # No connection pooling for file-based SQLite
                })
                
        elif db_type in ('mysql', 'postgresql'):
            options.update({
                'pool_size': self.config.get('pool_size', 5),
                'max_overflow': self.config.get('max_overflow', 10),
                'pool_timeout': self.config.get('pool_timeout', 30),
                'pool_recycle': self.config.get('pool_recycle', 3600),
                'pool_pre_ping': self.config.get('pool_pre_ping', True),
            })
            
            # Add specific options for MySQL
            if db_type == 'mysql':
                options['connect_args'] = self.config.get('connect_args', {})
                
        return options
        
    def _setup_engine_events(self, engine: Engine) -> None:
        """Set up engine event listeners for connection management."""
        db_type = self.config.get('type', 'sqlite')
        
        if db_type == 'sqlite':
            # Enable foreign keys for SQLite
            @event.listens_for(engine, "connect")
            def set_sqlite_pragma(dbapi_connection, connection_record):
                cursor = dbapi_connection.cursor()
                cursor.execute("PRAGMA foreign_keys=ON")
                cursor.close()
                
        elif db_type == 'mysql':
            # Set MySQL session variables
            @event.listens_for(engine, "connect")
            def set_mysql_session(dbapi_connection, connection_record):
                cursor = dbapi_connection.cursor()
                cursor.execute("SET SESSION sql_mode='TRADITIONAL'")
                cursor.execute("SET SESSION time_zone='+00:00'")
                cursor.close()
                
    @property
    def engine(self) -> Engine:
        """Get or create database engine."""
        if self._engine is None:
            self._engine = self._create_engine()
        return self._engine
        
    @property
    def session_factory(self) -> sessionmaker:
        """Get or create session factory."""
        if self._session_factory is None:
            self._session_factory = sessionmaker(
                bind=self.engine,
                expire_on_commit=False  # Prevent issues with detached instances
            )
        return self._session_factory
        
    def get_session(self) -> Session:
        """Get a new database session."""
        return self.session_factory()
        
    @contextmanager
    def session_scope(self):
        """Provide a transactional scope for database operations."""
        session = self.get_session()
        try:
            yield session
            session.commit()
        except Exception:
            session.rollback()
            raise
        finally:
            session.close()  # Use close() instead of remove() for regular sessions
            
    def create_tables(self) -> None:
        """Create all database tables defined in models."""
        try:
            # Force import all models to ensure they're registered with Base
            # Import the entire models module to trigger all model definitions
            import pdr_run.database.models # This should make all models known to Base
            
            # Also explicitly import each model class to ensure registration - this might be redundant if the above works
            # from pdr_run.database.models import (
            #     ModelNames, User, KOSMAtauExecutable, ChemicalDatabase,
            #     KOSMAtauParameters, PDRModelJob, HDFFile, JSONTemplate, 
            #     JSONFile, ModelRun
            # )
            
            logger.info("Attempting to create database tables...")
            logger.debug(f"Engine URL for create_tables: {self.engine.url}")
            logger.debug(f"Base object ID in create_tables: {id(Base)}")
            logger.debug(f"Tables registered in Base.metadata before create_all: {list(Base.metadata.tables.keys())}")
            
            # Create all tables
            Base.metadata.create_all(self.engine)
            
            # Verify tables were created
            inspector = None
            try:
                from sqlalchemy import inspect
                inspector = inspect(self.engine)
                created_tables = inspector.get_table_names()
                logger.info(f"Successfully created/verified {len(created_tables)} tables: {created_tables}")
                if not created_tables:
                    logger.warning("No tables were reported as created by the inspector.")
                elif 'users' not in created_tables:
                    logger.warning("'users' table not found in created tables list.")

            except Exception as e:
                logger.warning(f"Could not verify table creation via inspector: {e}")
                
            logger.info("Database table creation process finished.")
            
        except Exception as e:
            logger.error(f"Failed to create database tables: {e}", exc_info=True)
            raise
        
    def drop_tables(self) -> None:
        """Drop all database tables."""
        logger.warning("Dropping all database tables...")
        Base.metadata.drop_all(self.engine)
        logger.warning("Database tables dropped")
        
    def test_connection(self) -> bool:
        """Test database connection."""
        try:
            with self.engine.connect() as conn:
                # Use database-specific test query
                db_type = self.config.get('type', 'sqlite')
                if db_type == 'sqlite':
                    conn.execute("SELECT 1")
                elif db_type == 'mysql':
                    conn.execute("SELECT 1")
                elif db_type == 'postgresql':
                    conn.execute("SELECT 1")
                    
            logger.info("Database connection test successful")
            return True
        except Exception as e:
            logger.error(f"Database connection test failed: {e}")
            return False
            
    def close(self) -> None:
        """Close database connections and cleanup."""
        if self._engine:
            self._engine.dispose()
        logger.info("Database connections closed")


# Global instance for backward compatibility
_db_manager: Optional[DatabaseManager] = None


def get_db_manager(config: Optional[Dict[str, Any]] = None) -> DatabaseManager:
    """Get or create global database manager instance."""
    global _db_manager
    # Always create new instance if config is provided
    if _db_manager is None or config is not None:
        _db_manager = DatabaseManager(config)
    return _db_manager


def reset_db_manager():
    """Reset the global database manager instance."""
    global _db_manager
    if _db_manager:
        _db_manager.close()
    _db_manager = None

