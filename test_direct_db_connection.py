"""Direct test of database connection with different password configurations."""

import os
from pdr_run.database.connection import get_database_uri
from pdr_run.config.default_config import get_database_config

def test_db_uri_construction():
    """Test database URI construction with different password scenarios."""
    
    # Test 1: No password
    os.environ.pop('PDR_DB_PASSWORD', None)
    config = get_database_config()
    config.update({'type': 'mysql', 'host': 'test.com', 'username': 'user', 'database': 'db'})
    uri = get_database_uri(config)
    print(f"No password: {uri}")
    assert 'None' not in uri, "URI should not contain 'None' string"
    
    # Test 2: With password from environment
    os.environ['PDR_DB_PASSWORD'] = 'secret123'
    config = get_database_config()
    config.update({'type': 'mysql', 'host': 'test.com', 'username': 'user', 'database': 'db'})
    uri = get_database_uri(config)
    print(f"With password: {uri}")
    assert 'secret123' in uri, "URI should contain the password"
    assert 'None' not in uri, "URI should not contain 'None' string"

if __name__ == "__main__":
    test_db_uri_construction()
    print("✓ Database URI construction tests passed!")
