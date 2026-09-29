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


# ---------------------------------------------------------------------------
# --reset-stale-jobs — crashed-driver recovery CLI wiring
# ---------------------------------------------------------------------------

def test_parse_arguments_reset_stale_jobs():
    with patch('sys.argv', ['pdr_run', '--reset-stale-jobs', '--stale-after-hours', '2.5']):
        args = parse_arguments()

    assert args.reset_stale_jobs is True
    assert args.stale_after_hours == 2.5


def test_parse_arguments_reset_stale_jobs_defaults_off():
    with patch('sys.argv', ['pdr_run', '--single']):
        args = parse_arguments()

    assert args.reset_stale_jobs is False
    assert args.stale_after_hours is None


def test_main_reset_stale_jobs_runs_and_exits_without_launching_models():
    """--reset-stale-jobs is a standalone utility action: it must call
    reset_stale_jobs() and return before run_model/run_parameter_grid ever
    execute (no model should be launched)."""
    from pdr_run.cli.runner import main

    with patch('sys.argv', ['pdr_run', '--reset-stale-jobs', '--stale-after-hours', '1']), \
         patch('pdr_run.database.db_manager.get_db_manager') as mock_get_db_manager, \
         patch('pdr_run.database.queries.reset_stale_jobs', return_value=[7, 9]) as mock_reset, \
         patch('pdr_run.core.engine.run_model') as mock_run_model, \
         patch('pdr_run.core.engine.run_parameter_grid') as mock_run_grid:
        mock_get_db_manager.return_value.create_tables.return_value = None
        main()

    mock_reset.assert_called_once()
    _, kwargs = mock_reset.call_args
    assert kwargs['stale_after_s'] == 3600  # 1 hour
    assert kwargs['dry_run'] is False
    mock_run_model.assert_not_called()
    mock_run_grid.assert_not_called()


def test_main_reset_stale_jobs_dry_run_passes_through():
    from pdr_run.cli.runner import main

    with patch('sys.argv', ['pdr_run', '--reset-stale-jobs', '--dry-run']), \
         patch('pdr_run.database.db_manager.get_db_manager') as mock_get_db_manager, \
         patch('pdr_run.database.queries.reset_stale_jobs', return_value=[]) as mock_reset:
        mock_get_db_manager.return_value.create_tables.return_value = None
        main()

    _, kwargs = mock_reset.call_args
    assert kwargs['dry_run'] is True