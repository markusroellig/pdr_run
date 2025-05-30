"""Shared fixtures for testing the PDR framework.

This module provides pytest fixtures that are used across multiple test files.
Fixtures create a consistent test environment, including mocked dependencies,
temporary storage locations, and database connections for testing without
affecting real data or requiring external services.
"""

# Standard library imports for file operations and temporary storage
import os
import tempfile

# Testing framework
import pytest

# For configuration file parsing
import yaml

# For creating test doubles (mocks)
from unittest.mock import MagicMock

# For testing code that calls external processes
import subprocess

# Database related imports
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from pdr_run.database.models import Base  # SQLAlchemy declarative base with model definitions


@pytest.fixture
def mock_model_runner():
    """Mock the actual PDR model runner.
    
    Creates a mock object that simulates the behavior of a model runner
    without executing actual computations. The mock returns predefined 
    success results with sample output files and execution statistics.
    
    Returns:
        MagicMock: A configured mock object that can be used in place of
                   a real model runner in tests.
    """
    # Create the mock object
    model_mock = MagicMock()
    
    # Configure the mock's run method to return a success result
    # with predefined output files and runtime information
    model_mock.run.return_value = {
        'success': True,                                     # Flag indicating successful completion
        'output_files': ['/path/to/output1.dat',             # List of generated output files
                        '/path/to/output2.dat'],
        'runtime_seconds': 42.5,                             # Simulated execution time
        'status': 'completed'                                # Final execution status
    }
    return model_mock

@pytest.fixture
def temp_storage_dir():
    """Create a temporary directory for local storage tests."""
    with tempfile.TemporaryDirectory() as tmp_dir:
        yield tmp_dir

@pytest.fixture
def temp_db_file():
    """Create a temporary SQLite database file."""
    with tempfile.NamedTemporaryFile(suffix='.db', delete=False) as tmp_file:
        db_path = tmp_file.name
        yield db_path
        if os.path.exists(db_path):
            os.unlink(db_path)

@pytest.fixture
def mock_env_config():
    """Setup and teardown environment variables for testing."""
    # Save original environment
    original_env = {}
    env_vars = ["PDR_DB_TYPE", "PDR_DB_FILE", "PDR_STORAGE_TYPE", "PDR_STORAGE_DIR"]
    
    for var in env_vars:
        if var in os.environ:
            original_env[var] = os.environ[var]
    
    # Set test environment variables
    test_env = {
        "PDR_STORAGE_TYPE": "local",
        "PDR_STORAGE_DIR": "/tmp/pdr_test",
        "PDR_DB_TYPE": "sqlite",
        "PDR_DB_FILE": ":memory:"
    }
    
    for key, value in test_env.items():
        os.environ[key] = value
        
    yield test_env
    
    # Restore original environment
    for key in test_env:
        if key in original_env:
            os.environ[key] = original_env[key]
        else:
            del os.environ[key]

@pytest.fixture
def mock_executables(monkeypatch):
    """Mock executable calls to avoid file not found errors."""
    def mock_check_output(*args, **kwargs):
        if args and isinstance(args[0], list):
            cmd = args[0]
            if any(x.endswith('--version') for x in cmd):
                return b"PDR Version 1.0 (revision: test_rev)"
            elif any(x.endswith('--date') for x in cmd):
                return b"Jan 01 2023 at 12:00:00"
        # Default response in correct date format
        return b"Jan 01 2023 at 12:00:00"
    
    def mock_run_process(*args, **kwargs):
        mock_process = MagicMock()
        mock_process.returncode = 0
        mock_process.stdout = b"Success\n"
        return mock_process
    
    # Apply patches
    monkeypatch.setattr(subprocess, 'check_output', mock_check_output)
    monkeypatch.setattr(subprocess, 'run', mock_run_process)
    
    return mock_check_output

@pytest.fixture(scope="function")
def db_session():
    """Create an in-memory database for testing."""
    engine = create_engine("sqlite:///:memory:", 
                          connect_args={"check_same_thread": False})
    
    # Create all tables
    Base.metadata.create_all(engine)
    
    # Create session
    Session = sessionmaker(bind=engine)
    session = Session()
    
    yield session
    
    # Clean up
    session.close()
    Base.metadata.drop_all(engine)

