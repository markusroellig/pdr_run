"""Test the command line interface."""

import os
import sys
import pytest
from unittest.mock import patch
from pdr_run.cli.runner import parse_arguments

def test_parse_arguments_single_model():
    """Test parsing command line arguments for single model."""
    with patch('sys.argv', ['pdr_run', '--model-name', 'test_model', '--single',
                           '--dens', '3.5', '--chi', '2.0']):
        args = parse_arguments()

        assert args.model_name == 'test_model'
        assert args.single is True
        assert args.grid is False
        assert args.dens == ['3.5']  # Updated to expect a list
        assert args.chi == ['2.0']   # Updated to expect a list
        
def test_parse_arguments_grid():
    """Test parsing command line arguments for grid run."""
    with patch('sys.argv', ['pdr_run', '--model-name', 'test_grid', '--grid',
                           '--cpus', '4']):
        args = parse_arguments()
        
        assert args.model_name == 'test_grid'
        assert args.grid is True
        assert args.single is False
        assert args.cpus == 4
        
def test_mutually_exclusive_args():
    """Test that --single and --grid are mutually exclusive."""
    with patch('sys.argv', ['pdr_run', '--model-name', 'test', '--single', '--grid']):
        # This should raise a SystemExit due to argument conflict
        with pytest.raises(SystemExit):
            args = parse_arguments()