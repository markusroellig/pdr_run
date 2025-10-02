#!/usr/bin/env python3
"""Test database connections."""

import os
import sys
sys.path.insert(0, '.')

def test_mysql():
    # Clear cached modules to ensure fresh connection
    for module in list(sys.modules.keys()):
        if module.startswith('pdr_run.database'):
            del sys.modules[module]

    os.environ.update({
        'PDR_DB_TYPE': 'mysql',
        'PDR_DB_HOST': 'localhost',
        'PDR_DB_PORT': '3306',
        'PDR_DB_DATABASE': 'pdr_test',
        'PDR_DB_USERNAME': 'pdr_user',
        'PDR_DB_PASSWORD': 'pdr_password'
    })

    try:
        from pdr_run.database.connection import init_db
        from sqlalchemy import text
        session, engine = init_db()
        print("✓ MySQL connection successful")

        # Test query
        result = session.execute(text("SELECT message FROM connection_test LIMIT 1"))
        message = result.fetchone()[0]
        print(f"✓ MySQL query successful: {message}")
        session.close()

    except Exception as e:
        print(f"✗ MySQL connection failed: {e}")

def test_sqlite():
    os.environ.update({
        'PDR_DB_TYPE': 'sqlite',
        'PDR_DB_FILE': './sandbox/sqlite/test.db'
    })
    
    try:
        from pdr_run.database.connection import init_db
        session, engine = init_db()
        print("✓ SQLite connection successful")
        session.close()
        
    except Exception as e:
        print(f"✗ SQLite connection failed: {e}")

if __name__ == "__main__":
    print("Testing database connections...")
    test_sqlite()
    test_mysql()
