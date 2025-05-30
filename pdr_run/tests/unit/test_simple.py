"""Simple tests for PDR framework."""

import pytest
from unittest.mock import patch, MagicMock
import argparse

from pdr_run.cli.runner import parse_arguments, main

def test_simple_argument_parsing():
    """Test basic argument parsing."""
    with patch('sys.argv', ['pdr_run', '--model-name', 'simple_model', '--dens', '3.0', '4.0']):
        args = parse_arguments()
        assert args.model_name == 'simple_model'
        assert args.dens == ['3.0', '4.0']  # Note: parameters are stored as lists

def test_simple_runner():
    """Test the main runner with direct patches."""
    # Create a mock for run_model
    mock_run = MagicMock(return_value="test_job_id")
    
    # Test with minimal arguments
    test_args = ['pdr_run', '--model-name', 'direct_test', '--single', '--dens', '3.0']
    
    with patch('sys.argv', test_args), \
         patch('pdr_run.cli.runner.run_model', mock_run):
        
        # Run the main function
        main()
        
        # Check how run_model was called
        mock_run.assert_called_once()
        args, kwargs = mock_run.call_args
        
        # Print the kwargs for debugging
        print(f"Called with: {kwargs}")
        
        assert kwargs["model_name"] == "direct_test"
        assert kwargs["params"]["dens"] == ["3.0"]  # Check for list parameter