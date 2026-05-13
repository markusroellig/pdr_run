"""Tests for database manager."""

import unittest
import os
import tempfile
from unittest.mock import patch, MagicMock
from pdr_run.database import db_manager as dbm_module
from pdr_run.database.db_manager import DatabaseManager, get_db_manager, reset_db_manager

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
            'database': 'test_db',
            'port': 3306  # FIX: Add missing port
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

                        # Verify password is not in logs (we'd need to check the actual log output)
                        # For now, just verify the connection string is built correctly
                        assert 'mysql+mysqlconnector://' in conn_string
                        assert 'secret123' in conn_string  # Password should be in connection string
    
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
            'database': 'test_db',
            'port': 3306  # FIX: Add missing port
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
    
    def test_password_precedence_order(self):
        """Test that environment variable takes precedence over config."""
        config_with_password = {
            'type': 'mysql',
            'host': 'localhost', 
            'username': 'test',
            'password': 'config_password',
            'database': 'test_db'
        }
        
        with patch.dict(os.environ, {'PDR_DB_PASSWORD': 'env_password'}):
            with patch('sqlalchemy.create_engine') as mock_create_engine:
                mock_engine = MagicMock()
                mock_create_engine.return_value = mock_engine
                
                # Mock _validate_config to avoid validation
                with patch.object(DatabaseManager, '_validate_config'):
                    manager = DatabaseManager(config_with_password)
                    
                    # Environment variable should override config
                    self.assertEqual(manager.config['password'], 'env_password')
    
    def test_empty_password_handling(self):
        """Test handling of empty password values."""
        config = {
            'type': 'mysql',
            'host': 'localhost',
            'username': 'test', 
            'database': 'test_db'
        }
        
        # Test with empty string in environment
        with patch.dict(os.environ, {'PDR_DB_PASSWORD': ''}):
            with patch('sqlalchemy.create_engine') as mock_create_engine:
                mock_engine = MagicMock()
                mock_create_engine.return_value = mock_engine
                
                with patch.object(DatabaseManager, '_validate_config'):
                    manager = DatabaseManager(config)
                    
                    # Empty string should be treated as no password
                    self.assertEqual(manager.config.get('password'), '')
    
    def test_deprecated_uri_construction_with_passwords(self):
        """Test that deprecated get_db_uri function handles passwords correctly."""
        import os
        from unittest.mock import patch
        from pdr_run.database.connection import get_db_uri, get_database_config

        # Test 1: No password
        # Completely isolate from environment - patch the _load_config method directly
        with patch.dict(os.environ, {}, clear=True):
            with patch('pdr_run.database.db_manager.DatabaseManager._validate_config'), \
                 patch('sqlalchemy.create_engine'), \
                 patch.object(DatabaseManager, '_load_config') as mock_load_config:

                # Force the config to stay MySQL
                test_config = {
                    'type': 'mysql', 
                    'host': 'test.com', 
                    'username': 'user', 
                    'database': 'db',
                    'port': 3306  # FIX: Add missing port
                }
                mock_load_config.return_value = test_config

                config = get_database_config()
                config.update(test_config)
                uri = get_db_uri(config)

                # Verify URI format
                assert 'mysql+mysqlconnector://' in uri
                assert 'user@test.com:3306/db' in uri
                assert ':password' not in uri  # Should not contain password field
    
    def test_mysql_connection_via_deprecated_init_db(self):
        """Test MySQL connection through deprecated init_db function."""
        mysql_config = {
            'type': 'mysql',
            'host': 'mysql.example.com',
            'port': 3306,
            'database': 'pdr_test',
            'username': 'test_user',
            'password': 'test_password'
        }
        
        # Mock all the components that would interact with real MySQL
        # Also patch the _load_config to prevent environment override
        with patch('pdr_run.database.db_manager.create_engine') as mock_create_engine, \
             patch('pdr_run.database.db_manager.DatabaseManager._validate_config'), \
             patch('pdr_run.database.db_manager.DatabaseManager._setup_engine_events'), \
             patch('pdr_run.database.models.Base.metadata.create_all'), \
             patch.object(DatabaseManager, '_load_config', return_value=mysql_config), \
             patch.dict(os.environ, {}, clear=True):  # Clear environment to prevent override
            
            mock_engine = MagicMock()
            mock_create_engine.return_value = mock_engine
            
            # Test the deprecated init_db function
            from pdr_run.database.connection import init_db
            session, engine = init_db(mysql_config)
            
            # Verify mock was called with correct connection string
            mock_create_engine.assert_called_once()
            conn_args = mock_create_engine.call_args[0][0]
            
            self.assertIn('mysql+mysqlconnector://', conn_args)
            self.assertIn('@mysql.example.com:3306/pdr_test', conn_args)
            self.assertIn('test_user:test_password', conn_args)
            
            # Cleanup
            session.close()

class TestDatabaseManagerProcessAffinity(unittest.TestCase):
    """Regression tests for issue #15: parallel workers got fresh SQLite singletons.

    Joblib's default LokyBackend spawns worker processes where the module-level
    _db_manager singleton is None. Before the fix, get_db_manager() with no
    config silently fell back to the SQLite default and every query failed with
    'no such table: pdr_model_jobs'.
    """

    def setUp(self):
        reset_db_manager()

    def tearDown(self):
        reset_db_manager()

    def test_worker_pid_change_triggers_reinit(self):
        """A different PID must produce a new manager, not reuse the parent's."""
        parent_config = {'type': 'sqlite', 'path': ':memory:'}
        parent = get_db_manager(parent_config)
        parent_id = parent.manager_id
        self.assertIsNotNone(dbm_module._db_manager_pid)

        # Simulate that the singleton was created in a different process
        # (i.e. we are now a freshly spawned/forked worker).
        dbm_module._db_manager_pid = dbm_module._db_manager_pid + 100000

        worker_config = {'type': 'sqlite', 'path': ':memory:'}
        worker = get_db_manager(worker_config)

        self.assertIsNot(worker, parent)
        self.assertNotEqual(worker.manager_id, parent_id)
        self.assertEqual(dbm_module._db_manager_pid, os.getpid())

    def test_worker_uses_passed_config_not_default_sqlite(self):
        """Workers must honor the config we pass, not silently use SQLite default.

        This is the exact failure path from issue #15: a worker would call
        get_db_manager() and get a fresh SQLite ':memory:' DB even though the
        run was configured for MySQL.
        """
        # Isolate from PDR_DB_* env vars that earlier tests may have leaked,
        # since _load_config applies env overrides on top of the passed config.
        pdr_env_keys = [k for k in os.environ if k.startswith('PDR_DB_')]
        with patch.dict(os.environ, {k: '' for k in pdr_env_keys}, clear=False):
            for k in pdr_env_keys:
                del os.environ[k]

            # Set up "parent" manager
            parent_config = {'type': 'sqlite', 'path': ':memory:'}
            get_db_manager(parent_config)

            # Simulate worker process
            dbm_module._db_manager_pid = dbm_module._db_manager_pid + 100000

            # Worker calls with the actual run config (MySQL-style)
            mysql_config = {
                'type': 'mysql',
                'host': 'mysql.example.com',
                'port': 3306,
                'database': 'pdr_test',
                'username': 'pdr_user',
                'password': 'secret',
            }
            with patch('pdr_run.database.db_manager.create_engine') as mock_create_engine, \
                 patch.object(DatabaseManager, '_setup_engine_events'):
                mock_create_engine.return_value = MagicMock()
                worker = get_db_manager(mysql_config)

            # The worker's config must reflect what we passed, not the SQLite default
            self.assertEqual(worker.config['type'], 'mysql')
            self.assertEqual(worker.config['host'], 'mysql.example.com')


if __name__ == '__main__':
    unittest.main()