# test_framework.py
import os
import logging
import logging.config
import pytest
from unittest.mock import patch, MagicMock
import subprocess
import datetime

from pdr_run.core.engine import run_model
from pdr_run.config.default_config import DEFAULT_PARAMETERS
from pdr_run.config.logging_config import LOGGING_CONFIG
from pdr_run.database.connection import init_db  # Ensure this import is present

from sqlalchemy import Column, String
from pdr_run.database.models import KOSMAtauExecutable

# Configure logging
logging.config.dictConfig(LOGGING_CONFIG)
logger = logging.getLogger('dev')

def add_comment_column_to_executables(session):
    """Add the 'comment' column to the kosmatau_executables table for testing."""
    if not hasattr(KOSMAtauExecutable, 'comment'):
        KOSMAtauExecutable.comment = Column(String, nullable=True)
        session.execute("ALTER TABLE kosmatau_executables ADD COLUMN comment TEXT")

@pytest.fixture
def db_session_with_comment():
    """Fixture to provide a test database session with the 'comment' column."""
    db_config = {
        'type': 'sqlite',
        'location': 'local',
        'path': ':memory:',
    }
    session, _ = init_db(db_config)
    add_comment_column_to_executables(session)
    yield session
    session.close()

# Use the mock_executables fixture
@pytest.mark.usefixtures("mock_executables")
def test_run_model(db_session_with_comment):  # Use the updated fixture
    """Test running a model with mocked executables."""
    test_params = DEFAULT_PARAMETERS.copy()
    test_params['dens'] = ["3.0"]
    test_params['chi'] = ["1.0"]

    # Fix: Make sure alpha and rcore are scalar values, not lists
    test_params['alpha'] = 1.0
    test_params['rcore'] = 0.2

    # Patch at the proper module level to catch all calls
    with patch('pdr_run.io.file_manager.get_code_revision', return_value="mocked_revision"), \
         patch('pdr_run.core.engine.get_compilation_date', return_value=datetime.datetime(2023, 1, 1, 12, 0, 0)), \
         patch('pdr_run.io.file_manager.get_compilation_date', return_value=datetime.datetime(2023, 1, 1, 12, 0, 0)), \
         patch('pdr_run.database.db_manager.get_db_manager') as mock_get_db_manager:  # Patch the db_manager instead
        
        # Configure the mock to return a manager that returns our test session
        mock_db_manager = MagicMock()
        mock_db_manager.get_session.return_value = db_session_with_comment
        mock_get_db_manager.return_value = mock_db_manager

# ...existing code...        job_id = run_model(params=test_params, model_name="test_model")

logger.info("Test completed!")