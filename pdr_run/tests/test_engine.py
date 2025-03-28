"""Test the PDR model execution engine."""

import os
import pytest
from unittest.mock import patch, MagicMock

from pdr_run.core.engine import run_model, run_parameter_grid
from sqlalchemy.orm import sessionmaker
from pdr_run.database.connection import init_db

@pytest.fixture
def db_session():
    """Fixture to provide a test database session."""
    db_config = {
        'type': 'sqlite',
        'location': 'local',
        'path': ':memory:',
    }
    session, _ = init_db(db_config)
    yield session
    session.close()

def test_run_single_model(mock_model_runner, mock_env_config, monkeypatch):
    """Test running a single PDR model."""
    
    # This doesn't work because we're patching and then calling the same function 
    # which leads to infinite recursion. Let's fix it:
    
    # Instead of patching run_model itself, patch the functions it calls
    with patch('pdr_run.core.engine.run_parameter_grid', return_value=['test_job_123']):
        # Pass all required parameters
        result = run_model(model_name="test_model", params={
            "dens": ["3.5"],
            "chi": ["2.0"],
            "mass": ["1.0"],   # Missing required parameter
            "metal": ["1.0"],  # Missing required parameter
            "col": ["0.0"]    # Missing required parameter
        })
        
        assert result == 'test_job_123'

def test_run_parameter_grid(mock_model_runner, mock_env_config, db_session):  # Add db_session
    """Test running a grid of PDR models."""
    grid_params = {
        "model_name": "test_grid", 
        "params": {
            "dens": ["3.0", "3.5", "4.0"],
            "chi": ["1.0", "2.0"],
            "mass": ["1.0"],
            "metal": ["1.0"],
            "col": ["0.0"]
        },
        "parallel": False
    }
    
    # Create expected job IDs for the 6 combinations
    expected_job_ids = [f'test_job_{i}' for i in range(6)]
    
 
    # Patch all the database and execution functions
    with patch('pdr_run.core.engine.run_kosma_tau', return_value={'success': True}), \
         patch('pdr_run.core.engine.create_database_entries', return_value=({}, expected_job_ids)), \
         patch('pdr_run.core.engine.multiprocessing.Pool') as mock_pool, \
         patch('pdr_run.database.queries.get_session', return_value=db_session):  # Use the db_session fixture

        mock_pool_instance = MagicMock()
        mock_pool.return_value.__enter__.return_value = mock_pool_instance

        # Configure mock pool to return the same job IDs
        mock_pool_instance.starmap.return_value = expected_job_ids

        # Run parameter grid
        results = run_parameter_grid(**grid_params)
        
        # Verify results
        assert len(results) == 6  # 3 densities × 2 chi values
        assert results == expected_job_ids

def test_cpu_calculation():
    """Test automatic CPU calculation."""
    with patch('pdr_run.core.engine.multiprocessing.cpu_count') as mock_cpu_count:
        # System has 8 CPUs
        mock_cpu_count.return_value = 8
        
        # With default reserved CPUs (2)
        cpus = run_parameter_grid._calculate_cpu_count(0, 2)
        assert cpus == 6
        
        # With custom reserved CPUs
        cpus = run_parameter_grid._calculate_cpu_count(0, 4)
        assert cpus == 4
        
        # With explicitly specified CPUs
        cpus = run_parameter_grid._calculate_cpu_count(3, 2)
        assert cpus == 3