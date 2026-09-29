"""Regression tests locking in: create_tables()/ensure_additive_columns run
exactly once, in the main process, before any parallel worker starts.

Issue #11 (connection leaks in parallel mode, fixed in 37fe8f1) established
that create_database_entries() - called once in run_parameter_grid() before
Parallel(...) - is the only call site for DatabaseManager.create_tables(),
and that run_instance()/run_instance_wrapper() (the functions joblib workers
execute) must never call it. This test makes that invariant explicit so a
future change cannot silently reintroduce a create_tables() call inside a
worker.
"""

from unittest.mock import MagicMock, patch

from pdr_run.core.engine import create_database_entries, run_instance_wrapper


def test_create_database_entries_calls_create_tables_exactly_once():
    mock_db_manager = MagicMock()
    mock_session = MagicMock()
    mock_db_manager.get_session.return_value = mock_session

    fake_exe = MagicMock(id=1)
    fake_user = MagicMock(id=1)
    fake_chem = MagicMock(id=1)
    fake_param = MagicMock(id=1)
    fake_job = MagicMock(id=42)

    with patch('pdr_run.core.engine.get_db_manager', return_value=mock_db_manager), \
         patch('pdr_run.core.engine.get_or_create',
               side_effect=[fake_exe, fake_user, fake_chem, fake_param, fake_job]), \
         patch('pdr_run.core.engine.get_model_name_id', return_value=1), \
         patch('pdr_run.core.engine.get_code_revision', return_value='rev'), \
         patch('pdr_run.core.engine.get_compilation_date') as mock_date, \
         patch('pdr_run.core.engine.get_digest', return_value='deadbeef'), \
         patch('os.path.exists', return_value=True):
        import datetime
        mock_date.return_value = datetime.datetime.now()

        create_database_entries(
            model_name='test_model',
            model_path='/tmp/test_model',
            param_combinations=[('100', '3.0', '1.0', '1.0')],
            config={'pdr': {'base_dir': '/tmp'}, 'parameters': {}},
        )

    mock_db_manager.create_tables.assert_called_once()


def test_run_instance_wrapper_never_calls_create_tables():
    """The function joblib workers execute must not call create_tables() -
    that call belongs exclusively to the main process (see
    create_database_entries above); calling it per-worker was issue #11's
    root cause (connection-pool exhaustion under --parallel)."""
    mock_db_manager = MagicMock()

    with patch('pdr_run.core.engine.get_db_manager', return_value=mock_db_manager), \
         patch('pdr_run.core.engine.run_instance', return_value=['done']) as mock_run_instance:
        run_instance_wrapper(job_id=1, config={'database': {'type': 'sqlite', 'path': ':memory:'}})

    mock_run_instance.assert_called_once()
    mock_db_manager.create_tables.assert_not_called()
