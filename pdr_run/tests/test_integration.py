"""Integration tests for PDR framework."""

import os
import sys
import pytest
import tempfile
import yaml
from unittest.mock import patch, MagicMock

from pdr_run.cli.runner import main, parse_arguments
from pdr_run.core.engine import run_model, run_parameter_grid

@pytest.fixture
def mock_environment():
    """Set up a controlled test environment with mocks."""
    # Save original environment variables
    original_env = {}
    env_vars = ["PDR_DB_TYPE", "PDR_DB_FILE", "PDR_STORAGE_TYPE", "PDR_STORAGE_DIR"]
    for var in env_vars:
        if (var in os.environ):
            original_env[var] = os.environ[var]
    
    # Setup temporary environment
    with tempfile.TemporaryDirectory() as temp_dir:
        db_path = os.path.join(temp_dir, "test.db")
        storage_path = os.path.join(temp_dir, "storage")
        os.makedirs(storage_path, exist_ok=True)
        
        os.environ["PDR_DB_TYPE"] = "sqlite"
        os.environ["PDR_DB_FILE"] = db_path
        os.environ["PDR_STORAGE_TYPE"] = "local"
        os.environ["PDR_STORAGE_DIR"] = storage_path
        
        yield {
            "temp_dir": temp_dir,
            "db_path": db_path,
            "storage_path": storage_path
        }
    
    # Restore original environment
    for var in env_vars:
        if var in original_env:
            os.environ[var] = original_env[var]
        elif var in os.environ:
            del os.environ[var]

@pytest.fixture
def test_config_file():
    """Create a temporary config file for testing."""
    with tempfile.NamedTemporaryFile(mode="w", suffix=".yaml", delete=False) as f:
        config = {
            "database": {
                "type": "sqlite",
                "file": ":memory:"
            },
            "storage": {
                "type": "local",
                "path": "/tmp/test_storage"
            },
            "model_params": {
                "metal": ["1.0"],
                "dens": ["1.0", "2.0"],
                "chi": ["1.0"],
                "col": ["1e22"]
            }
        }
        yaml.dump(config, f)
        config_path = f.name
    
    yield config_path
    
    # Clean up
    if os.path.exists(config_path):
        os.unlink(config_path)

@pytest.fixture
def mock_engine():
    """Mock the PDR engine to avoid running actual models."""
    with patch("pdr_run.core.engine.run_model") as mock_run_model, \
         patch("pdr_run.core.engine.run_parameter_grid") as mock_run_grid:
        
        # Configure mock responses
        mock_run_model.return_value = "test_job_id_single"
        mock_run_grid.return_value = ["job_id_1", "job_id_2", "job_id_3"]
        
        yield {
            "run_model": mock_run_model,
            "run_grid": mock_run_grid
        }

def test_argument_parsing():
    """Test command line argument parsing."""
    test_args = ['pdr_run', '--model-name', 'test_model', '--dens', '1.0', '2.0', '--chi', '1.0']
    with patch('sys.argv', test_args):
        args = parse_arguments()
        assert args.model_name == 'test_model'
        assert args.dens == ['1.0', '2.0']
        assert args.chi == ['1.0']
        assert not args.parallel
        assert not args.single

def test_single_model_run(mock_environment, mock_engine):
    """Test running a single model."""
    test_args = ['pdr_run', '--model-name', 'test_model', '--single', '--dens', '3.0', '--chi', '2.0']
    
    # Patch the function directly in the runner module, not in core.engine
    with patch('sys.argv', test_args), \
         patch('pdr_run.cli.runner.run_model', return_value="job_id_1") as mock_run:
        main()
        
        # Check if run_model was called with correct parameters
        mock_run.assert_called_once()
        args, kwargs = mock_run.call_args
        assert kwargs["model_name"] == "test_model"
        assert kwargs["params"]["dens"] == ["3.0"]  # Check for list instead of string
        assert kwargs["params"]["chi"] == ["2.0"]   # Same for chi parameter

def test_parameter_grid_run(mock_environment, mock_engine):
    """Test running a parameter grid."""
    test_args = ['pdr_run', '--model-name', 'grid_test', '--dens', '1.0', '2.0', '--chi', '1.0', '2.0']
    
    # Patch the function directly in the runner module, not just in core.engine
    with patch('sys.argv', test_args), \
         patch('pdr_run.cli.runner.run_parameter_grid', return_value=["job_1", "job_2", "job_3", "job_4"]) as mock_grid:
        main()
        
        # Check if run_parameter_grid was called with correct parameters
        mock_grid.assert_called_once()
        args, kwargs = mock_grid.call_args
        assert kwargs["model_name"] == "grid_test"
        assert set(kwargs["params"]["dens"]) == {"1.0", "2.0"}
        assert set(kwargs["params"]["chi"]) == {"1.0", "2.0"}

def test_parallel_execution(mock_environment, mock_engine):
    """Test parallel execution of model grid."""
    test_args = ['pdr_run', '--model-name', 'parallel_test', '--parallel', '--workers', '4',
                '--dens', '1.0', '2.0', '--chi', '1.0', '2.0']
    
    with patch('sys.argv', test_args), \
         patch('pdr_run.cli.runner.run_parameter_grid', return_value=["job_1", "job_2"]) as mock_grid:
        main()
        
        # Check if run_parameter_grid was called with parallel flag
        mock_grid.assert_called_once()
        args, kwargs = mock_grid.call_args
        assert kwargs["parallel"] is True
        assert kwargs["n_workers"] == 4

def test_config_file_loading(mock_environment, mock_engine, test_config_file):
    """Test loading configuration from file."""
    test_args = ['pdr_run', '--model-name', 'config_test', '--config', test_config_file]
    
    # Use a more direct patching approach
    with patch('sys.argv', test_args), \
         patch('pdr_run.cli.runner.run_parameter_grid', mock_engine["run_grid"]):
        main()
        
        # Check if configuration was properly loaded
        mock_engine["run_grid"].assert_called_once()
        args, kwargs = mock_engine["run_grid"].call_args
        assert kwargs["model_name"] == "config_test"
        assert "config" in kwargs

def test_error_handling(mock_environment):
    """Test error handling during model execution."""
    test_args = ['pdr_run', '--model-name', 'error_test', '--single', '--dens', '3.0']
    
    with patch('sys.argv', test_args), \
         patch('pdr_run.core.engine.run_model', side_effect=Exception("Test error")), \
         patch('pdr_run.cli.runner.logger') as mock_logger:
        
        # Should log the error but not crash
        main()
        
        # Verify error was logged
        mock_logger.error.assert_called()