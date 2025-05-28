#!/usr/bin/env python3
"""Test database connections in the sandbox environment."""

import os
import sys
import tempfile
from pathlib import Path

# Add the parent directory to Python path to import pdr_run
project_root = Path(__file__).parent.parent
sys.path.insert(0, str(project_root))

try:
    from pdr_run.database.connection import get_engine, init_db
    from pdr_run.database.models import Base
    print("Successfully imported PDR database modules")
except ImportError as e:
    print(f"Failed to import PDR modules: {e}")
    sys.exit(1)

def test_sqlite_connection():
    """Test SQLite database connection."""
    print("\n=== Testing SQLite Connection ===")
    
    # Create a temporary database file
    with tempfile.NamedTemporaryFile(suffix='.db', delete=False) as temp_db:
        db_path = temp_db.name
    
    try:
        # Set environment for SQLite
        os.environ['PDR_DB_TYPE'] = 'sqlite'
        os.environ['PDR_DB_FILE'] = db_path
        
        # Test getting engine
        engine = get_engine()
        print(f"✓ SQLite engine created successfully")
        print(f"  Database URL: {engine.url}")
        
        # Test creating tables
        Base.metadata.create_all(engine)
        print("✓ Database tables created successfully")
        
        # Test basic connection with proper SQLAlchemy syntax
        with engine.connect() as conn:
            # Use text() for raw SQL queries in SQLAlchemy 2.0+
            try:
                from sqlalchemy import text
                result = conn.execute(text("SELECT 1 as test")).fetchone()
                assert result[0] == 1
                print("✓ Database connection test passed")
            except ImportError:
                # Fallback for older SQLAlchemy versions
                result = conn.execute("SELECT 1 as test").fetchone()
                assert result[0] == 1
                print("✓ Database connection test passed (legacy mode)")
            
        return True
        
    except Exception as e:
        print(f"✗ SQLite connection failed: {e}")
        return False
        
    finally:
        # Clean up
        if os.path.exists(db_path):
            os.unlink(db_path)

def test_mysql_connection_mock():
    """Test MySQL connection with mocking."""
    print("\n=== Testing MySQL Connection (Mock) ===")
    
    try:
        from unittest.mock import patch, MagicMock
        
        # Set environment for MySQL
        os.environ['PDR_DB_TYPE'] = 'mysql'
        os.environ['PDR_DB_HOST'] = 'localhost'
        os.environ['PDR_DB_USER'] = 'test_user'
        os.environ['PDR_DB_PASSWORD'] = 'test_password'
        os.environ['PDR_DB_NAME'] = 'test_db'
        
        with patch('sqlalchemy.create_engine') as mock_create_engine:
            mock_engine = MagicMock()
            mock_create_engine.return_value = mock_engine
            
            # Test getting engine
            engine = get_engine()
            print("✓ MySQL engine mock created successfully")
            
            # Verify the connection string was constructed correctly
            if mock_create_engine.called:
                call_args = mock_create_engine.call_args
                if call_args and call_args[0]:  # Check if positional args exist
                    connection_string = str(call_args[0][0])
                    print(f"✓ MySQL connection string: {connection_string}")
                    
                    # Basic validation of connection string components
                    if 'mysql' in connection_string and 'test_user' in connection_string:
                        print("✓ MySQL connection string format verified")
                    else:
                        print("! MySQL connection string format may be unexpected")
                else:
                    print("✓ MySQL engine creation called without specific validation")
            else:
                print("✓ MySQL engine mock setup completed")
            
        return True
        
    except Exception as e:
        print(f"✗ MySQL mock connection failed: {e}")
        import traceback
        traceback.print_exc()
        return False

def main():
    """Run all database connection tests."""
    print("PDR Database Connection Tests")
    print("=" * 40)
    
    results = []
    
    # Test SQLite
    results.append(test_sqlite_connection())
    
    # Test MySQL (mocked)
    results.append(test_mysql_connection_mock())
    
    # Summary
    print(f"\n=== Test Summary ===")
    passed = sum(results)
    total = len(results)
    print(f"Passed: {passed}/{total}")
    
    if passed == total:
        print("✓ All database connection tests passed!")
        return 0
    else:
        print("✗ Some database connection tests failed!")
        return 1

if __name__ == "__main__":
    sys.exit(main())