"""Comprehensive MySQL integration test for PDR framework.

This test suite validates MySQL functionality with real database connections,
including the full PDR workflow, environment variable handling, connection
recovery, and error scenarios that users might encounter.
"""

import os
import sys
import pytest
from unittest.mock import patch, MagicMock
import tempfile
import time
import threading
import uuid
from pathlib import Path
import subprocess
import logging
from sqlalchemy import text

# Configure logging for detailed debugging
logging.basicConfig(level=logging.DEBUG)
logger = logging.getLogger(__name__)

# Fix the Python path - go up to the directory containing the pdr_run package
# Current: /home/roellig/pdr/pdr_run/pdr_run/tests/integration/test_mysql_integration.py
# Target:  /home/roellig/pdr/pdr/pdr_run (contains pdr_run package)
current_file = Path(__file__).resolve()
project_root = current_file.parent.parent.parent.parent  # Go up 4 levels
sys.path.insert(0, str(project_root))

# Debug: Print paths
print(f"Current file: {current_file}")
print(f"Project root: {project_root}")
print(f"pdr_run package exists: {(project_root / 'pdr_run').exists()}")
print(f"db_manager.py exists: {(project_root / 'pdr_run' / 'database' / 'db_manager.py').exists()}")

# Now try the imports
try:
    from pdr_run.database.db_manager import DatabaseManager
    print("✓ Successfully imported DatabaseManager")
except ImportError as e:
    print(f"✗ Still can't import DatabaseManager: {e}")
    sys.exit(1)

MYSQL_AVAILABLE = False
try:
    import mysql.connector
    from mysql.connector import errorcode
    MYSQL_AVAILABLE = True
except ImportError:
    logger.warning("mysql-connector-python not available. Install with: pip install mysql-connector-python")

# Helper to check for MySQL availability
def is_mysql_available():
    """Check if MySQL server is available using a direct, raw connection."""
    if not MYSQL_AVAILABLE:
        return False
    try:
        # Attempt a direct connection, ignoring environment variables
        conn = mysql.connector.connect(
            host='localhost',
            port=3306,
            user='root',
            password='rootpassword', # Standard password for test setup
            connection_timeout=3  # Fail fast
        )
        conn.close()
        return True
    except mysql.connector.Error:
        return False

class MySQLIntegrationTest:
    """Comprehensive MySQL integration test suite."""
    
    def __init__(self):
        self.test_db_name = f"pdr_test_{uuid.uuid4().hex[:8]}"
        self.original_env = {}
        self.connection = None
        
    def setup_environment(self):
        """Set up test environment variables."""
        # Save original environment
        env_vars = [
            'PDR_DB_TYPE', 'PDR_DB_HOST', 'PDR_DB_PORT', 'PDR_DB_DATABASE',
            'PDR_DB_USERNAME', 'PDR_DB_PASSWORD', 'PDR_STORAGE_TYPE', 'PDR_STORAGE_DIR'
        ]
        
        for var in env_vars:
            if var in os.environ:
                self.original_env[var] = os.environ[var]
        
        # Set test environment
        test_env = {
            'PDR_DB_TYPE': 'mysql',
            'PDR_DB_HOST': 'localhost',
            'PDR_DB_PORT': '3306',
            'PDR_DB_DATABASE': self.test_db_name,
            'PDR_DB_USERNAME': 'pdr_user',
            'PDR_DB_PASSWORD': 'pdr_password',
            'PDR_STORAGE_TYPE': 'local',
            'PDR_STORAGE_DIR': tempfile.mkdtemp()
        }
        
        for key, value in test_env.items():
            os.environ[key] = value
            
        logger.info(f"Test environment configured with database: {self.test_db_name}")
    
    def restore_environment(self):
        """Restore original environment variables."""
        # Remove test variables and restore originals
        for var in ['PDR_DB_TYPE', 'PDR_DB_HOST', 'PDR_DB_PORT', 'PDR_DB_DATABASE',
                   'PDR_DB_USERNAME', 'PDR_DB_PASSWORD', 'PDR_STORAGE_TYPE', 'PDR_STORAGE_DIR']:
            if var in os.environ:
                del os.environ[var]
            if var in self.original_env:
                os.environ[var] = self.original_env[var]
    
    def create_test_database(self):
        """Create test database using root credentials."""
        root_config = {
            'host': 'localhost',
            'port': 3306,
            'user': 'root',
            'password': 'rootpassword',
            'autocommit': True
        }
        
        try:
            logger.info("Connecting to MySQL as root to create test database...")
            root_conn = mysql.connector.connect(**root_config)
            cursor = root_conn.cursor()
            
            # Create database
            cursor.execute(f"DROP DATABASE IF EXISTS {self.test_db_name}")
            cursor.execute(f"CREATE DATABASE {self.test_db_name}")
            logger.info(f"Created database: {self.test_db_name}")
            
            # Ensure user exists and has permissions
            cursor.execute(f"DROP USER IF EXISTS 'pdr_user'@'%'")
            cursor.execute(f"CREATE USER 'pdr_user'@'%' IDENTIFIED BY 'pdr_password'")
            cursor.execute(f"GRANT ALL PRIVILEGES ON {self.test_db_name}.* TO 'pdr_user'@'%'")
            cursor.execute("FLUSH PRIVILEGES")
            logger.info("User 'pdr_user' created and granted permissions")
            
            cursor.close()
            root_conn.close()
            return True
            
        except mysql.connector.Error as err:
            logger.error(f"Failed to create test database: {err}")
            return False
    
    def cleanup_test_database(self):
        """Clean up test database."""
        root_config = {
            'host': 'localhost',
            'port': 3306,
            'user': 'root',
            'password': 'rootpassword',
            'autocommit': True
        }
        
        try:
            root_conn = mysql.connector.connect(**root_config)
            cursor = root_conn.cursor()
            cursor.execute(f"DROP DATABASE IF EXISTS {self.test_db_name}")
            cursor.close()
            root_conn.close()
            logger.info(f"Cleaned up database: {self.test_db_name}")
        except mysql.connector.Error as err:
            logger.warning(f"Failed to cleanup test database: {err}")
    
    def test_mysql_service_availability(self):
        """Test if MySQL service is available."""
        logger.info("Testing MySQL service availability...")
        
        try:
            # Try to connect to MySQL service
            test_config = {
                'host': 'localhost',
                'port': 3306,
                'user': 'root',
                'password': 'rootpassword',
                'connection_timeout': 5
            }
            
            conn = mysql.connector.connect(**test_config)
            cursor = conn.cursor()
            cursor.execute("SELECT VERSION()")
            version = cursor.fetchone()[0]
            logger.info(f"✓ MySQL service available, version: {version}")
            
            cursor.close()
            conn.close()
            return True
            
        except mysql.connector.Error as err:
            logger.error(f"✗ MySQL service not available: {err}")
            return False
        except Exception as e:
            logger.error(f"✗ Unexpected error testing MySQL: {e}")
            return False
    
    def test_pdr_database_manager_mysql(self):
        """Test PDR DatabaseManager with MySQL."""
        logger.info("Testing PDR DatabaseManager with MySQL...")
        
        try:
            from pdr_run.database.db_manager import DatabaseManager
            
            # Test with environment variables
            manager = DatabaseManager()
            logger.info(f"✓ DatabaseManager created with config: {manager.config['type']}")
            
            # Test connection
            if manager.test_connection():
                logger.info("✓ DatabaseManager connection test passed")
            else:
                logger.error("✗ DatabaseManager connection test failed")
                return False
            
            # Test table creation
            manager.create_tables()
            logger.info("✓ Database tables created successfully")
            
            # Test session creation - FIX: Use text() wrapper
            session = manager.get_session()
            result = session.execute(text("SELECT 1 as test")).fetchone()
            assert result[0] == 1
            session.close()
            logger.info("✓ Session creation and query execution successful")
            
            return True
            
        except Exception as e:
            logger.error(f"✗ PDR DatabaseManager test failed: {e}")
            import traceback
            traceback.print_exc()
            return False
    
    def test_environment_variable_precedence(self):
        """Test that environment variables override config files."""
        logger.info("Testing environment variable precedence...")
        
        try:
            # Fix: Use the correct import
            from pdr_run.database.db_manager import DatabaseManager
            
            # Test that PDR_DB_PASSWORD environment variable is used
            config = {
                'type': 'mysql',
                'host': 'localhost',
                'username': 'pdr_user',
                'password': 'wrong_password',  # This should be overridden
                'database': self.test_db_name
            }
            
            # PDR_DB_PASSWORD should be set in environment to correct value
            manager = DatabaseManager(config)
            
            # The manager should use the environment variable password
            assert manager.config['password'] == 'pdr_password'
            logger.info("✓ Environment variable correctly overrode config file password")
            
            # Test connection with environment variable password
            if manager.test_connection():
                logger.info("✓ Connection successful with environment variable password")
                return True
            else:
                logger.error("✗ Connection failed with environment variable password")
                return False
                
        except Exception as e:
            logger.error(f"✗ Environment variable precedence test failed: {e}")
            return False
    
    def test_connection_recovery(self):
        """Test connection recovery scenarios."""
        logger.info("Testing connection recovery...")
        
        try:
            from pdr_run.database.db_manager import DatabaseManager
            
            manager = DatabaseManager()
            
            # Test initial connection - FIX: Use text() wrapper
            with manager.session_scope() as session:
                result = session.execute(text("SELECT 1")).fetchone()
                assert result[0] == 1
            logger.info("✓ Initial connection successful")
            
            # Test that connection can be re-established
            time.sleep(1)  # Brief pause
            
            with manager.session_scope() as session:
                result = session.execute(text("SELECT 2")).fetchone()
                assert result[0] == 2
            logger.info("✓ Connection re-establishment successful")
            
            return True
            
        except Exception as e:
            logger.error(f"✗ Connection recovery test failed: {e}")
            return False
    
    def test_concurrent_connections(self):
        """Test concurrent database connections."""
        logger.info("Testing concurrent connections...")
        
        def worker_function(worker_id, results):
            try:
                from pdr_run.database.db_manager import DatabaseManager
                
                manager = DatabaseManager()
                with manager.session_scope() as session:
                    # FIX: Use text() wrapper
                    result = session.execute(text(f"SELECT {worker_id}")).fetchone()
                    assert result[0] == worker_id
                    results[worker_id] = True
                    logger.info(f"✓ Worker {worker_id} completed successfully")
                    
            except Exception as e:
                logger.error(f"✗ Worker {worker_id} failed: {e}")
                results[worker_id] = False
        
        # Start multiple threads
        results = {}
        threads = []
        
        for i in range(5):
            thread = threading.Thread(target=worker_function, args=(i, results))
            threads.append(thread)
            thread.start()
        
        # Wait for all threads to complete
        for thread in threads:
            thread.join()
        
        # Check results
        success_count = sum(1 for success in results.values() if success)
        logger.info(f"Concurrent connections: {success_count}/5 successful")
        
        return success_count == 5
    
    def test_full_pdr_workflow(self):
        """Test complete PDR workflow with MySQL backend."""
        logger.info("Testing full PDR workflow with MySQL...")
        
        try:
            from pdr_run.database.db_manager import DatabaseManager
            from pdr_run.database.models import User, ModelNames, KOSMAtauExecutable
            from pdr_run.database.queries import get_or_create
            import datetime
            
            manager = DatabaseManager()
            
            # FIX: Ensure tables are created and verify
            manager.create_tables()
            
            # Verify tables exist
            with manager.engine.connect() as conn:
                result = conn.execute(text("SHOW TABLES")).fetchall()
                table_names = [row[0] for row in result]
                logger.info(f"Available tables: {table_names}")
                
                if 'users' not in table_names:
                    logger.error("Users table not found!")
                    return False
            
            with manager.session_scope() as session:
                # Create test user
                user = get_or_create(
                    session,
                    User,
                    username="mysql_test_user",
                    email="test@mysql.example.com"
                )
                logger.info(f"✓ Created user: {user.username}")
                
                # Create test model
                model = get_or_create(
                    session,
                    ModelNames,
                    model_name="mysql_test_model",
                    model_path="/test/mysql/path"
                )
                logger.info(f"✓ Created model: {model.model_name}")
                
                # Create test executable
                executable = get_or_create(
                    session,
                    KOSMAtauExecutable,
                    code_revision="mysql_test_v1.0",
                    compilation_date=datetime.datetime.now(),
                    executable_file_name="test_mysql_exe",
                    executable_full_path="/test/mysql/exe/path",
                    sha256_sum="mysql_test_hash_123"
                )
                logger.info(f"✓ Created executable: {executable.executable_file_name}")
                
            logger.info("✓ Full PDR workflow test completed successfully")
            return True
            
        except Exception as e:
            logger.error(f"✗ Full PDR workflow test failed: {e}")
            import traceback
            traceback.print_exc()
            return False
    
    def test_error_scenarios(self):
        """Test various error scenarios."""
        logger.info("Testing error scenarios...")
        
        test_results = []
        
        # Test 1: Invalid password
        try:
            from pdr_run.database.db_manager import DatabaseManager
            
            config = {
                'type': 'mysql',
                'host': 'localhost',
                'username': 'pdr_user',
                'password': 'wrong_password',
                'database': self.test_db_name
            }
            
            # Temporarily override environment variable
            original_password = os.environ.get('PDR_DB_PASSWORD')
            os.environ['PDR_DB_PASSWORD'] = 'wrong_password'
            
            try:
                manager = DatabaseManager(config)
                connection_success = manager.test_connection()
                
                if not connection_success:
                    logger.info("✓ Correctly rejected invalid password")
                    test_results.append(True)
                else:
                    logger.error("✗ Invalid password was accepted")
                    test_results.append(False)
                    
            finally:
                # Restore original environment
                if original_password:
                    os.environ['PDR_DB_PASSWORD'] = original_password
                else:
                    if 'PDR_DB_PASSWORD' in os.environ:
                        del os.environ['PDR_DB_PASSWORD']
                        
        except Exception as e:
            logger.info(f"✓ Exception correctly raised for invalid password: {type(e).__name__}")
            test_results.append(True)
        
        # Test 2: Invalid database - FIX: Use test_connection properly
        try:
            from pdr_run.database.db_manager import DatabaseManager
            
            # Temporarily change database name in environment
            original_db = os.environ.get('PDR_DB_DATABASE')
            os.environ['PDR_DB_DATABASE'] = 'nonexistent_database_12345'
            
            try:
                manager = DatabaseManager()  # Will use environment variables
                connection_success = manager.test_connection()
                
                if not connection_success:
                    logger.info("✓ Correctly rejected invalid database")
                    test_results.append(True)
                else:
                    logger.error("✗ Invalid database was accepted")
                    test_results.append(False)
            finally:
                # Restore original database name
                if original_db:
                    os.environ['PDR_DB_DATABASE'] = original_db
                    
        except Exception as e:
            logger.info(f"✓ Exception correctly raised for invalid database: {type(e).__name__}")
            test_results.append(True)

        success_count = sum(test_results)
        logger.info(f"Error scenario tests: {success_count}/{len(test_results)} passed")
        
        return success_count == len(test_results)
    
    def run_all_tests(self):
        """Run all MySQL integration tests."""
        logger.info("=" * 60)
        logger.info("STARTING MYSQL INTEGRATION TESTS")
        logger.info("=" * 60)
        
        # Check if MySQL connector is available
        if not MYSQL_AVAILABLE:
            logger.error("MySQL connector not available. Install with: pip install mysql-connector-python")
            return False
        
        # Setup
        self.setup_environment()
        
        # Test results
        test_results = {}
        
        try:
            # Test MySQL service availability
            test_results['service_availability'] = self.test_mysql_service_availability()
            
            if not test_results['service_availability']:
                logger.error("MySQL service not available. Ensure MySQL is running.")
                logger.info("To start MySQL with Docker: docker-compose up -d mysql")
                return False
            
            # Create test database
            if not self.create_test_database():
                logger.error("Failed to create test database")
                return False
            
            # Run tests
            test_results['database_manager'] = self.test_pdr_database_manager_mysql()
            test_results['environment_variables'] = self.test_environment_variable_precedence()
            test_results['connection_recovery'] = self.test_connection_recovery()
            test_results['concurrent_connections'] = self.test_concurrent_connections()
            test_results['full_workflow'] = self.test_full_pdr_workflow()
            test_results['error_scenarios'] = self.test_error_scenarios()
            
        finally:
            # Cleanup
            self.cleanup_test_database()
            self.restore_environment()
        
        # Results summary
        logger.info("=" * 60)
        logger.info("TEST RESULTS SUMMARY")
        logger.info("=" * 60)
        
        passed_tests = []
        failed_tests = []
        
        for test_name, result in test_results.items():
            status = "PASSED" if result else "FAILED"
            logger.info(f"{test_name:<25}: {status}")
            
            if result:
                passed_tests.append(test_name)
            else:
                failed_tests.append(test_name)
        
        total_tests = len(test_results)
        passed_count = len(passed_tests)
        
        logger.info("-" * 60)
        logger.info(f"TOTAL: {passed_count}/{total_tests} tests passed")
        
        if failed_tests:
            logger.error(f"FAILED TESTS: {', '.join(failed_tests)}")
        
        success = passed_count == total_tests
        
        if success:
            logger.info("🎉 ALL MYSQL INTEGRATION TESTS PASSED!")
        else:
            logger.error("❌ SOME MYSQL INTEGRATION TESTS FAILED!")
            
        return success


def test_mysql_integration_with_pytest():
    """Pytest wrapper for MySQL integration tests."""
    pytest.skip("Requires running MySQL service")  # Skip by default
    
    test_suite = MySQLIntegrationTest()
    assert test_suite.run_all_tests(), "MySQL integration tests failed"


@pytest.mark.mysql
@pytest.mark.integration
def test_mysql_integration_manual():
    """Manual MySQL integration test (use with pytest -m mysql)."""
    # Skip test if MySQL is not available
    if not is_mysql_available():
        pytest.skip("MySQL service not available on localhost:3306. Skipping integration test.")

    test_suite = MySQLIntegrationTest()
    assert test_suite.run_all_tests(), "MySQL integration tests failed"


if __name__ == "__main__":
    # Run tests directly
    test_suite = MySQLIntegrationTest()
    success = test_suite.run_all_tests()
    sys.exit(0 if success else 1)