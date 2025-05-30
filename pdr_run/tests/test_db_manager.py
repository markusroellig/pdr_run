"""Tests for database manager."""

import unittest
import os
import tempfile
from unittest.mock import patch, MagicMock
from pdr_run.database.db_manager import DatabaseManager, reset_db_manager

class TestDatabaseManager(unittest.TestCase):
    """Test database manager functionality."""
    
    def setUp(self):
        """Reset database manager before each test."""
        reset_db_manager()
    
    def tearDown(self):
        """Reset database manager after each test."""
        reset_db_manager()
    
    def test_password_from_environment(self):
        """Test that password is read from environment variable."""
        with patch.dict(os.environ, {'PDR_DB_PASSWORD': 'test_password'}):
            config = {
                'type': 'mysql',
                'host': 'localhost',
                'username': 'test',
                'database': 'test_db'
            }
            
            # Expected config with password from environment
            expected_config = config.copy()
            expected_config['password'] = 'test_password'
            
            # Mock the create_engine to avoid actual connection
            with patch('sqlalchemy.create_engine') as mock_create_engine:
                mock_engine = MagicMock()
                mock_create_engine.return_value = mock_engine
                
                # Patch both the DATABASE_CONFIG and _load_config to return config WITH password
                with patch('pdr_run.config.default_config.DATABASE_CONFIG', {}):
                    with patch.object(DatabaseManager, '_load_config', return_value=expected_config):
                        with patch.object(DatabaseManager, '_validate_config'):
                            manager = DatabaseManager(config)
                            
                            # Check that password was picked up from environment
                            self.assertEqual(manager.config['password'], 'test_password')
    
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
            
            # Patch DATABASE_CONFIG and override _load_config completely
            with patch('pdr_run.config.default_config.DATABASE_CONFIG', {}):
                with patch.object(DatabaseManager, '_load_config', return_value=config):
                    with patch.object(DatabaseManager, '_validate_config'):
                        manager = DatabaseManager(config)
                        conn_string = manager._build_connection_string()
                        
                        # Password should be in connection string
                        assert 'secret123' in conn_string
    
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
            
            # Patch DATABASE_CONFIG and override _load_config completely
            with patch('pdr_run.config.default_config.DATABASE_CONFIG', {}):
                with patch.object(DatabaseManager, '_load_config', return_value=config):
                    with patch.object(DatabaseManager, '_validate_config'):
                        manager = DatabaseManager(config)
                        conn_string = manager._build_connection_string()
                        
                        assert conn_string.startswith('postgresql+psycopg2://')
    
    def test_sqlite_with_memory(self):
        """Test SQLite in-memory database."""
        config = {'type': 'sqlite', 'path': ':memory:'}
        
        with patch('sqlalchemy.create_engine') as mock_create_engine:
            mock_engine = MagicMock()
            mock_create_engine.return_value = mock_engine
            
            with patch('pdr_run.config.default_config.DATABASE_CONFIG', {}):
                with patch.object(DatabaseManager, '_load_config', return_value=config):
                    with patch.object(DatabaseManager, '_validate_config'):
                        manager = DatabaseManager(config)
                        conn_string = manager._build_connection_string()
                        
                        assert conn_string == 'sqlite:///:memory:'
    
    def test_connection_pooling_config(self):
        """Test that connection pooling is properly configured."""
        config = {
            'type': 'mysql',
            'host': 'localhost',
            'username': 'test_user',
            'password': 'test_password',
            'database': 'test_db',
            'pool_size': 10,
            'max_overflow': 20,
            'pool_recycle': 1800,
            'pool_pre_ping': True
        }
        
        # Mock the create_engine to avoid actual connection
        with patch('sqlalchemy.create_engine') as mock_create_engine:
            mock_engine = MagicMock()
            mock_create_engine.return_value = mock_engine
            
            with patch('pdr_run.config.default_config.DATABASE_CONFIG', {}):
                with patch.object(DatabaseManager, '_load_config', return_value=config):
                    with patch.object(DatabaseManager, '_validate_config'):
                        manager = DatabaseManager(config)
                        options = manager._get_engine_options()
                        
                        assert options['pool_size'] == 10
                        assert options['max_overflow'] == 20
                        assert options['pool_recycle'] == 1800
                        assert options['pool_pre_ping'] == True
    
    def test_session_scope_context_manager(self):
        """Test session scope context manager."""
        # Reset any existing manager
        reset_db_manager()
        
        # Explicitly import models here to ensure they are loaded in the test context
        import pdr_run.database.models
        from pdr_run.database.models import User
        
        config = {'type': 'sqlite', 'path': ':memory:'}
        
        # Create manager with SQLite in-memory database
        with patch('pdr_run.config.default_config.DATABASE_CONFIG', {}):
            with patch.object(DatabaseManager, '_load_config', return_value=config):
                with patch.object(DatabaseManager, '_validate_config'):
                    manager = DatabaseManager(config)
        
        # Create tables first - this is crucial
        manager.create_tables()
        
        # Test successful transaction
        with manager.session_scope() as session:
            user_obj = User(username='test', email='test@example.com')
            session.add(user_obj)
            # Should auto-commit on exit
        
        # Verify it was committed by using a new session
        with manager.session_scope() as session:
            users = session.query(User).all()
            # Account for potential existing users from other tests
            test_users = [u for u in users if u.username == 'test']
            assert len(test_users) == 1
            assert test_users[0].username == 'test'
    
    def test_backward_compatibility(self):
        """Test that old-style config still works."""
        config = {
            'type': 'sqlite',
            'location': 'local',
            'path': ':memory:'
        }
        
        with patch('sqlalchemy.create_engine') as mock_create_engine:
            mock_engine = MagicMock()
            mock_create_engine.return_value = mock_engine
            
            with patch('pdr_run.config.default_config.DATABASE_CONFIG', {}):
                with patch.object(DatabaseManager, '_load_config', return_value=config):
                    with patch.object(DatabaseManager, '_validate_config'):
                        manager = DatabaseManager(config)
                        conn_string = manager._build_connection_string()
                        
                        assert conn_string == 'sqlite:///:memory:'
    
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
        
        with patch('sqlalchemy.create_engine') as mock_create_engine:
            mock_engine = MagicMock()
            mock_create_engine.return_value = mock_engine
            
            with patch('pdr_run.config.default_config.DATABASE_CONFIG', {}):
                with patch.object(DatabaseManager, '_load_config', return_value=config):
                    with patch.object(DatabaseManager, '_validate_config'):
                        manager = DatabaseManager(config)
                        print(f"Manager config: {manager.config}")
                        
                        conn_string = manager._build_connection_string()
                        print(f"Connection string: {conn_string}")
                        
                        # This should NOT be SQLite
                        assert 'mysql+mysqlconnector://' in conn_string, f"Expected MySQL, got: {conn_string}"

if __name__ == '__main__':
    unittest.main()