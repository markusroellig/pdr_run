"""Debug tests for CLI arguments."""

import pytest
from unittest.mock import patch
from pdr_run.cli.runner import parse_arguments

def test_cli_args_debug():
    """Debug test for CLI argument parsing."""
    with patch('sys.argv', ['pdr_run', '--model-name', 'debug_test', '--single', '--dens', '3.0']):
        args = parse_arguments()
        # Print all args attributes
        print("\nArguments parsed:")
        for attr in dir(args):
            if not attr.startswith('_'):  # Skip internal attributes
                print(f"  {attr}: {getattr(args, attr)}")