"""Tests for the database module."""

import os
import pytest
from unittest.mock import patch, MagicMock

from sqlalchemy import text
from pdr_run.database.connection import get_engine
from pdr_run.database.migration import create_tables
from pdr_run.database.models import Base

# Use this fixture to create a temporary database file
@pytest.fixture
def temp_db_file():
    """Create a temporary database file."""
    import tempfile
    db_file = tempfile.mktemp(suffix='.db')
    yield db_file
    if os.path.exists(db_file):
        os.remove(db_file)

def test_sqlite_connection(temp_db_file):
    """Test SQLite connection creation."""
    # Set environment for SQLite
    os.environ["PDR_DB_TYPE"] = "sqlite"
    os.environ["PDR_DB_FILE"] = temp_db_file
    
    # Create connection
    engine = get_engine()
    
    # Verify connection is valid
    assert engine is not None
    
    # Clean up
    engine.dispose()  # Use dispose() instead of close() for SQLAlchemy engines

def test_create_tables(temp_db_file):
    """Test creating tables in database."""
    # Set environment for SQLite
    os.environ["PDR_DB_TYPE"] = "sqlite"
    os.environ["PDR_DB_FILE"] = temp_db_file
    
    # Create connection and tables
    engine = get_engine()
    create_tables(engine)
    
    # Verify tables exist - using SQLAlchemy approach
    with engine.connect() as conn:
        # Check if a known table exists
        result = conn.execute(text("SELECT name FROM sqlite_master WHERE type='table' AND name='pdr_model_jobs'"))  # Changed from pdr_model_job to pdr_model_jobs
        tables = result.fetchall()
        assert len(tables) > 0
    
    # Clean up
    engine.dispose()

@pytest.mark.parametrize("db_type", ["sqlite", "mysql"])
def test_mock_mysql_connection(db_type):
    """Test MySQL database connection using mocks."""
    # Reset the engine by re-importing the module
    import importlib
    import pdr_run.database.connection as db_conn
    
    # Store original create_engine
    original_create_engine = db_conn.create_engine
    
    try:
        # Set environment for database type
        os.environ["PDR_DB_TYPE"] = db_type
        
        if db_type == "mysql":
            # Use the correct environment variable names
            os.environ["PDR_DB_HOST"] = "localhost"
            os.environ["PDR_DB_USERNAME"] = "pdr_user"
            os.environ["PDR_DB_PASSWORD"] = "password"
            os.environ["PDR_DB_DATABASE"] = "pdr_db"
            
            # Patch both create_engine and the event system
            with patch('pdr_run.database.db_manager.create_engine') as mock_create_engine, \
                 patch('pdr_run.database.db_manager.event') as mock_event:
                 
                mock_engine = MagicMock()
                mock_create_engine.return_value = mock_engine
                
                # Mock the event.listens_for decorator to do nothing
                mock_event.listens_for.return_value = lambda func: func
                
                # Reset the global db_manager to force recreation
                from pdr_run.database.db_manager import reset_db_manager
                reset_db_manager()
                
                # Reload to pick up the mock
                importlib.reload(db_conn)
                
                # Get a fresh engine
                engine = db_conn.get_engine()
                assert mock_create_engine.called, "Mock was not called"
                
                # Verify the connection string was built correctly
                call_args = mock_create_engine.call_args
                connection_string = call_args[0][0]  # First positional argument
                assert 'mysql+mysqlconnector://' in connection_string
                assert 'pdr_user:password@localhost' in connection_string
                assert 'pdr_db' in connection_string
        else:
            # For SQLite, just check it returns an engine
            engine = db_conn.get_engine()
            assert engine is not None
    
    finally:
        # Clean up environment variables
        env_vars_to_clean = ["PDR_DB_TYPE", "PDR_DB_HOST", "PDR_DB_USERNAME", "PDR_DB_PASSWORD", "PDR_DB_DATABASE"]
        for var in env_vars_to_clean:
            if var in os.environ:
                del os.environ[var]
                
        # Clean up by restoring original create_engine and reloading module
        db_conn.create_engine = original_create_engine
        
        # Reset the db_manager to clear any cached instances
        from pdr_run.database.db_manager import reset_db_manager
        reset_db_manager()
        
        importlib.reload(db_conn)

def test_store_model_run(temp_db_file):
    """Test storing model run data in the database."""
    # Ensure we start with a clean slate by reloading modules
    import importlib
    import pdr_run.database.connection as db_conn
    importlib.reload(db_conn)
    
    # Set environment for SQLite
    os.environ["PDR_DB_TYPE"] = "sqlite"
    os.environ["PDR_DB_FILE"] = temp_db_file
    
    # Create tables
    engine = db_conn.get_engine()
    create_tables(engine)
    
    # Import models and create a new session
    from pdr_run.database.models import PDRModelJob
    
    # Create a fresh session that's not from cache
    from sqlalchemy.orm import Session
    session = Session(engine)
    
    try:
        # Create and save a simple model for testing
        model_run = PDRModelJob(
            # Remove the explicit ID - let SQLAlchemy auto-generate it
            model_job_name="test_run",
            status="completed"
        )
        
        session.add(model_run)
        session.commit()
        
        # Verify model run was saved and ID was auto-generated
        saved_run = session.query(PDRModelJob).filter_by(model_job_name="test_run").first()
        assert saved_run is not None
        assert saved_run.id is not None  # Check that ID was assigned
        assert saved_run.model_job_name == "test_run"
    
    finally:
        # Clean up
        session.close()
        engine.dispose()