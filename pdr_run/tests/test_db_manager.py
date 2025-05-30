"""Test the new database manager implementation."""

import os
import pytest
import tempfile
from unittest.mock import patch, MagicMock

from pdr_run.database import DatabaseManager, get_db_manager


class TestDatabaseManager:
    """Test DatabaseManager functionality."""
    
    def setup_method(self):
        """Setup before each test method."""
        from pdr_run.database.db_manager import reset_db_manager
        reset_db_manager()
    
    def teardown_method(self):
        """Teardown after each test method."""
        from pdr_run.database.db_manager import reset_db_manager
        reset_db_manager()
    
    def test_password_from_environment(self):
        """Test that PDR_DB_PASSWORD environment variable is used."""
        with patch.dict(os.environ, {'PDR_DB_PASSWORD': 'secret123'}):
            manager = DatabaseManager()
            assert manager.config['password'] == 'secret123'
    
    def test_password_not_logged(self):
        """Test that passwords are not logged."""
        config = {
            'type': 'mysql',
            'host': 'localhost',
            'username': 'test',
            'password': 'secret123',
            'database': 'test_db'
        }
        
        # Mock the create_engine to avoid actual connection
        with patch('sqlalchemy.create_engine') as mock_create_engine:
            mock_engine = MagicMock()
            mock_create_engine.return_value = mock_engine
            
            manager = DatabaseManager(config)
            conn_string = manager._build_connection_string()
            
            # Password should be in connection string
            assert 'secret123' in conn_string
            
            # But when we create engine, it should be masked in logs
            with patch('pdr_run.database.db_manager.logger') as mock_logger:
                engine = manager.engine  # This triggers _create_engine
                
                # Check that password was not logged
                for call in mock_logger.info.call_args_list:
                    assert 'secret123' not in str(call)

    def test_postgresql_support(self):
        """Test PostgreSQL connection string building."""
        config = {
            'type': 'postgresql',
            'host': 'localhost',
            'port': 5432,
            'username': 'postgres',
            'password': 'pgpass',
            'database': 'pdr_test'
        }
        
        # Mock the create_engine to avoid actual connection
        with patch('sqlalchemy.create_engine') as mock_create_engine:
            mock_engine = MagicMock()
            mock_create_engine.return_value = mock_engine
            
            manager = DatabaseManager(config)
            conn_string = manager._build_connection_string()
            
            assert conn_string.startswith('postgresql+psycopg2://')
            assert 'postgres:' in conn_string
            assert '@localhost:5432/pdr_test' in conn_string

    def test_sqlite_with_memory(self):
        """Test SQLite with in-memory database."""
        config = {
            'type': 'sqlite',
            'path': ':memory:'
        }
        
        manager = DatabaseManager(config)
        
        # Should be able to create tables
        manager.create_tables()
        
        # Should be able to get a session
        session = manager.get_session()
        assert session is not None
        session.close()
    
    def test_connection_pooling_config(self):
        """Test that connection pooling is properly configured."""
        config = {
            'type': 'mysql',
            'host': 'localhost',
            'username': 'test_user',
            'password': 'test_password',  # Add required password
            'database': 'test_db',        # Add required database
            'pool_size': 10,
            'max_overflow': 20,
            'pool_recycle': 1800,
            'pool_pre_ping': True
        }
        
        # Mock the create_engine to avoid actual connection
        with patch('sqlalchemy.create_engine') as mock_create_engine:
            mock_engine = MagicMock()
            mock_create_engine.return_value = mock_engine
            
            manager = DatabaseManager(config)
            options = manager._get_engine_options()
            
            assert options['pool_size'] == 10
            assert options['max_overflow'] == 20
            assert options['pool_recycle'] == 1800
            assert options['pool_pre_ping'] is True
    
    def test_session_scope_context_manager(self):
        """Test session scope context manager."""
        # Reset any existing manager
        from pdr_run.database.db_manager import reset_db_manager
        reset_db_manager()
        
        # Explicitly import models here to ensure they are loaded in the test context
        # before DatabaseManager might rely on Base.metadata. This is speculative.
        import pdr_run.database.models
        from pdr_run.database.models import User
        
        # Create manager with SQLite in-memory database
        manager = DatabaseManager({'type': 'sqlite', 'path': ':memory:'})
        
        # Create tables first - this is crucial
        manager.create_tables()
        
        # Import models to ensure they're available (User is already imported above)
        # from pdr_run.database.models import User 
    
        # Test successful transaction
        with manager.session_scope() as session:
            user_obj = User(username='test', email='test@example.com') # Renamed to avoid conflict if User was a type hint
            session.add(user_obj)
            # Should auto-commit on exit
    
        # Verify it was committed by using a new session
        with manager.session_scope() as session:
            users = session.query(User).all()
            assert len(users) == 1
            assert users[0].username == 'test'
            assert users[0].email == 'test@example.com'
    
        # Test rollback on error
        with pytest.raises(Exception):
            with manager.session_scope() as session:
                user_obj_rollback = User(username='test2', email='test2@example.com') # Renamed
                session.add(user_obj_rollback)
                raise Exception("Test error")
    
        # Verify it was rolled back - should still be only one user
        with manager.session_scope() as session:
            users = session.query(User).all()
            assert len(users) == 1  # Still only one user
    
    def test_backward_compatibility(self):
        """Test that old API still works with deprecation warning."""
        with pytest.warns(DeprecationWarning):
            from pdr_run.database.connection import get_session
            # Actually call the function to trigger the warning
            session = get_session()
            assert session is not None
            session.close()

    def test_debug_mysql_config(self):
        """Debug test to see where MySQL config is being changed."""
        config = {
            'type': 'mysql',
            'host': 'localhost',
            'username': 'test',
            'password': 'secret123',
            'database': 'test_db'
        }
        
        print(f"Input config: {config}")
        
        manager = DatabaseManager(config)
        print(f"Manager config: {manager.config}")
        
        conn_string = manager._build_connection_string()
        print(f"Connection string: {conn_string}")
        
        # This should NOT be SQLite
        assert 'mysql+mysqlconnector://' in conn_string, f"Expected MySQL, got: {conn_string}"


if __name__ == "__main__":
    pytest.main([__file__])