"""Test script for MySQL database connections."""
import os
from unittest import mock
import pdr_run.database.connection as db_conn
from pdr_run.config.default_config import DATABASE_CONFIG

def test_mysql_connection():
    """Test MySQL connection with mock."""
    # Create a copy of the default config
    mysql_config = DATABASE_CONFIG.copy()
    
    # Update with MySQL settings
    mysql_config.update({
        'type': 'mysql',
        'host': 'mysql.example.com',  # Replace with your MySQL host
        'port': 3306,
        'database': 'pdr_test',
        'username': 'test_user',
        'password': 'test_password'
    })
    
    # Patch create_engine where it's used, not where it's defined
    with mock.patch('pdr_run.database.connection.create_engine') as mock_create_engine:
        # Create a mock engine
        mock_engine = mock.MagicMock()
        mock_create_engine.return_value = mock_engine
        
        # Set up mock connection and metadata behavior
        mock_connection = mock.MagicMock()
        mock_engine.connect.return_value = mock_connection
        mock_engine.raw_connection.return_value = mock_connection
        
        # Make Base.metadata.create_all not actually try to create tables
        with mock.patch('pdr_run.database.models.Base.metadata.create_all') as mock_create_all:
            # Initialize the database with MySQL config
            session, engine = db_conn.init_db(mysql_config)
            
            # Verify the mock was called with MySQL connection string
            mock_create_engine.assert_called_once()
            conn_args = mock_create_engine.call_args[0][0]
            
            # Basic verification of connection string
            assert 'mysql+mysqlconnector://' in conn_args
            assert '@mysql.example.com:3306/pdr_test' in conn_args
            
            print("MySQL connection string generated successfully:", 
                  conn_args.replace(mysql_config['username'], '***').replace(mysql_config['password'], '***'))
            
            # Cleanup
            session.close()

if __name__ == "__main__":
    test_mysql_connection()
    print("MySQL connection test completed successfully!")