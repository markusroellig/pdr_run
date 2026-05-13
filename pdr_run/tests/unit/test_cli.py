"""Test the command line interface."""

import os
import sys
import logging
import pytest
from unittest.mock import patch
from pdr_run.cli.runner import parse_arguments, validate_config

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


# ---------------------------------------------------------------------------
# validate_config — regression coverage for issue #17
# ---------------------------------------------------------------------------

def test_validate_config_accepts_legitimate_storage_keys():
    """Issue #17: keys like mount_point and remote_path_prefix are consumed
    by the storage backends and must not abort validation."""
    config = {
        'storage': {
            'type': 'rclone',
            'base_dir': '/tmp/storage',
            'rclone_remote': 'kosmatau',
            'use_mount': True,
            'mount_point': '/tmp/mnt',
            'remote_path_prefix': '/some/prefix',
        }
    }
    # Should return cleanly without sys.exit
    validate_config(config)


def test_validate_config_warns_on_unknown_param_but_does_not_abort(caplog):
    """Unknown parameter keys inside a known section should warn, not abort.

    Pre-fix this called sys.exit(1) and broke valid configs whenever the
    framework consumed a key that wasn't listed in default_config.py.
    """
    config = {
        'storage': {
            'type': 'local',
            'this_key_does_not_exist_anywhere': 42,
        }
    }
    with caplog.at_level(logging.WARNING):
        validate_config(config)  # must not raise SystemExit

    messages = " ".join(rec.message for rec in caplog.records)
    assert 'this_key_does_not_exist_anywhere' in messages


def test_validate_config_aborts_on_unknown_top_level_section():
    """Unknown top-level sections remain a hard error (almost always a typo)."""
    config = {'totally_made_up_section': {'foo': 'bar'}}
    with pytest.raises(SystemExit):
        validate_config(config)


def test_validate_config_accepts_section_aliases():
    """non_default_params is an alias for non_default_parameters."""
    config = {
        'non_default_params': {'ih2meth': 0, 'tgasc': 50.0}
    }
    validate_config(config)  # must not raise