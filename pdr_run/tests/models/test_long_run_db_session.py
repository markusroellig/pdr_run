"""Long model runs must not lose their job status to a dropped DB connection.

Grid-1 tier 0 (halley, 2026-10): jobs 1474/1482/1490 hit the 30 h wall-time
cap; run_pdr wrote 'timeout', but run_instance had held a session (and its
pooled connection) idle across the whole run. The MySQL server dropped it
after wait_timeout (86400 s), session.close() raised in run_instance's
finally, and run_instance_wrapper overwrote 'timeout' with 'exception'.

Locked in here:
* run_instance holds no session / transaction while the model runs;
* run_pdr / run_simline hold no transaction during the external process;
* a failing close/rollback is non-fatal (close_session);
* an exception after a terminal status never overwrites it
  (mark_job_exception), while a running job still becomes 'exception';
* the MySQL session idle timeout follows pdr.max_walltime_s.
"""

import os
from unittest.mock import MagicMock, patch

import pytest
from sqlalchemy.exc import OperationalError

from pdr_run.core import engine
from pdr_run.database import db_manager as dbm
from pdr_run.database.db_manager import (
    DEFAULT_SESSION_TIMEOUT_S, MYSQL_MAX_SESSION_TIMEOUT_S, close_session,
    database_config_for_run, get_db_manager, release_connection, reset_db_manager,
    session_timeout_for_walltime,
)
from pdr_run.database.models import (
    ChemicalDatabase, KOSMAtauExecutable, KOSMAtauParameters, ModelNames, PDRModelJob,
)
from pdr_run.database.queries import mark_job_exception, update_job_status
from pdr_run.models import kosma_tau


def _dropped(msg="The client was disconnected by the server because of inactivity"):
    return OperationalError("ROLLBACK", {}, Exception(msg))


# ----------------------------------------------------------- wait_timeout

class TestSessionTimeout:
    @pytest.mark.parametrize("walltime, expected", [
        (None, 86400), (0, 86400), ('bad', 86400),
        (3600, 86400),            # 2 x cap below the floor -> floor
        (108000, 216000),         # grid-1: 30 h cap -> 60 h
        (108000.2, 216001),       # rounded up
        (1e9, 31536000),          # capped at MySQL's maximum
    ])
    def test_derivation(self, walltime, expected):
        assert session_timeout_for_walltime(walltime) == expected

    def test_config_without_cap_is_returned_unchanged(self):
        db = {'type': 'mysql'}
        assert database_config_for_run({'database': db, 'pdr': {}}) is db
        assert database_config_for_run({'pdr': {'max_walltime_s': None}}) is None
        assert database_config_for_run(None) is None

    def test_config_with_cap_gets_timeout_and_input_is_not_mutated(self):
        db = {'type': 'mysql', 'host': 'h'}
        out = database_config_for_run({'database': db, 'pdr': {'max_walltime_s': 108000}})
        assert out == {'type': 'mysql', 'host': 'h', 'session_timeout_s': 216000}
        assert 'session_timeout_s' not in db
        # no database section (MySQL via PDR_DB_* environment) still gets it
        assert database_config_for_run({'pdr': {'max_walltime_s': 108000}}) == {
            'session_timeout_s': 216000}

    @pytest.mark.parametrize("cfg_timeout, expected", [
        (None, DEFAULT_SESSION_TIMEOUT_S), (216000, 216000),
        (10, DEFAULT_SESSION_TIMEOUT_S), (10**9, MYSQL_MAX_SESSION_TIMEOUT_S)])
    def test_mysql_connect_sets_wait_and_interactive_timeout(
            self, monkeypatch, cfg_timeout, expected):
        for var in ('PDR_DB_TYPE', 'PDR_DB_HOST', 'PDR_DB_DATABASE',
                    'PDR_DB_USERNAME', 'PDR_DB_PASSWORD', 'PDR_DB_PORT'):
            monkeypatch.delenv(var, raising=False)
        cfg = {'type': 'mysql', 'host': 'h', 'database': 'd', 'username': 'u',
               'password': 'p', 'session_timeout_s': cfg_timeout}
        manager = dbm.DatabaseManager(cfg)
        listeners = []
        monkeypatch.setattr(dbm.event, 'listens_for',
                            lambda target, name: (lambda fn: listeners.append(fn) or fn))
        manager._setup_engine_events(object())
        cursor = MagicMock()
        conn = MagicMock()
        conn.cursor.return_value = cursor
        listeners[0](conn, None)
        sql = [c.args[0] for c in cursor.execute.call_args_list]
        assert f"SET SESSION wait_timeout={expected}" in sql
        assert f"SET SESSION interactive_timeout={expected}" in sql


# ----------------------------------------------------------- close / release

class TestCloseSession:
    def test_close_failure_is_logged_and_connection_invalidated(self, caplog):
        session = MagicMock()
        session.close.side_effect = _dropped()
        with caplog.at_level('WARNING', logger='dev'):
            close_session(session, 'test')          # must not raise
        session.invalidate.assert_called_once()
        assert any(r.levelname == 'WARNING' and 'Closing DB session (test) failed' in r.message
                   for r in caplog.records)

    def test_invalidate_failure_is_also_swallowed(self):
        session = MagicMock()
        session.close.side_effect = _dropped()
        session.invalidate.side_effect = RuntimeError('gone')
        close_session(session)

    def test_session_scope_keeps_original_error_when_rollback_fails(self, monkeypatch):
        manager = MagicMock()
        session = MagicMock()
        session.rollback.side_effect = _dropped()
        session.close.side_effect = _dropped()
        manager.session_factory.return_value = session
        with pytest.raises(ValueError, match='original'):
            with dbm.DatabaseManager.session_scope(manager):
                raise ValueError('original')

    def test_release_connection_commits_only_open_transactions(self):
        session = MagicMock()
        session.in_transaction.return_value = False
        release_connection(session)
        session.commit.assert_not_called()
        session.in_transaction.return_value = True
        release_connection(session)
        session.commit.assert_called_once()


# ----------------------------------------------------------- real DB fixtures

@pytest.fixture
def sqlite_run(tmp_path, monkeypatch):
    """A file SQLite database behind the global DatabaseManager singleton,
    one job row, and a run config with the grid-1 wall-time cap."""
    for var in ('PDR_DB_TYPE', 'PDR_DB_FILE', 'PDR_DB_HOST', 'PDR_DB_DATABASE',
                'PDR_DB_USERNAME', 'PDR_DB_PASSWORD', 'PDR_DB_PORT'):
        monkeypatch.delenv(var, raising=False)
    reset_db_manager()
    base_dir = tmp_path / 'pdr'
    base_dir.mkdir()
    config = {'database': {'type': 'sqlite', 'path': str(tmp_path / 'jobs.db')},
              'pdr': {'base_dir': str(base_dir), 'max_walltime_s': 108000}}
    manager = get_db_manager(database_config_for_run(config))
    manager.create_tables()
    with manager.session_scope() as s:
        name = ModelNames(model_name='m', model_path=str(tmp_path / 'store'))
        exe = KOSMAtauExecutable(executable_file_name='pdrexe')
        chem = ChemicalDatabase(chem_rates_file_name='chem_rates.dat')
        s.add_all([name, exe, chem])
        s.flush()
        par = KOSMAtauParameters(model_name_id=name.id)
        s.add(par)
        s.flush()
        job = PDRModelJob(model_name_id=name.id, model_job_name='j1',
                          kosmatau_parameters_id=par.id, kosmatau_executable_id=exe.id,
                          chemical_database_id=chem.id, onion_species='', status='pending')
        s.add(job)
        s.flush()
        job_id = job.id

    # Track every session the manager hands out.
    sessions = []
    factory = manager.session_factory

    def _tracked():
        s = factory()
        sessions.append(s)
        return s
    monkeypatch.setattr(manager, 'get_session', _tracked)
    monkeypatch.setattr(engine, '_setup_execution_environment', lambda *a, **k: None)
    yield dict(config=config, job_id=job_id, manager=manager, sessions=sessions)
    reset_db_manager()


def _status(manager, job_id):
    with manager.session_scope() as s:
        return s.get(PDRModelJob, job_id).status


class TestRunInstanceHoldsNoSession:
    def test_no_open_transaction_during_the_model_run(self, sqlite_run):
        seen = {}

        def fake_run_kosma_tau(job_id, tmp_dir, **kw):
            seen['open'] = [s for s in sqlite_run['sessions'] if s.in_transaction()]
            seen['n'] = len(sqlite_run['sessions'])
            update_job_status(job_id, 'timeout')

        with patch.object(engine, 'run_kosma_tau', side_effect=fake_run_kosma_tau):
            engine.run_instance_wrapper(sqlite_run['job_id'], sqlite_run['config'])
        assert seen['n'] >= 1, "run_instance should have looked the job up"
        assert seen['open'] == []
        assert _status(sqlite_run['manager'], sqlite_run['job_id']) == 'timeout'

    def test_wait_timeout_derived_for_the_worker_config(self, sqlite_run):
        assert sqlite_run['manager'].config['session_timeout_s'] == 216000


class TestTerminalStatusIsKept:
    """Regression for jobs 1474/1482/1490: a connection dropped while a
    session is closed after the run must not replace the run's status."""

    @pytest.mark.parametrize('terminal', ['timeout', 'finished'])
    def test_dropped_connection_at_close_keeps_status(self, sqlite_run, monkeypatch, terminal):
        manager = sqlite_run['manager']
        real_get_session = manager.get_session

        def fake_run_kosma_tau(job_id, tmp_dir, **kw):
            update_job_status(job_id, terminal)
            # From now on the server has dropped every idle connection:
            # closing (= rolling back) any session opened before raises.
            for s in list(sqlite_run['sessions']):
                monkeypatch.setattr(s, 'close', MagicMock(side_effect=_dropped()))
                monkeypatch.setattr(s, 'rollback', MagicMock(side_effect=_dropped()))

        with patch.object(engine, 'run_kosma_tau', side_effect=fake_run_kosma_tau):
            engine.run_instance_wrapper(sqlite_run['job_id'], sqlite_run['config'])
        assert _status(manager, sqlite_run['job_id']) == terminal
        assert manager.get_session is real_get_session

    @pytest.mark.parametrize('terminal', ['timeout', 'finished', 'failed_storage'])
    def test_exception_after_terminal_status_keeps_it(self, sqlite_run, terminal):
        def fake_run_kosma_tau(job_id, tmp_dir, **kw):
            update_job_status(job_id, terminal)
            raise _dropped()   # e.g. run_kosma_tau's own session close

        with patch.object(engine, 'run_kosma_tau', side_effect=fake_run_kosma_tau):
            engine.run_instance_wrapper(sqlite_run['job_id'], sqlite_run['config'])
        assert _status(sqlite_run['manager'], sqlite_run['job_id']) == terminal

    def test_wrapper_does_not_overwrite_terminal_status(self, sqlite_run):
        update_job_status(sqlite_run['job_id'], 'timeout')
        with patch.object(engine, 'run_instance', side_effect=_dropped()):
            engine.run_instance_wrapper(sqlite_run['job_id'], sqlite_run['config'])
        assert _status(sqlite_run['manager'], sqlite_run['job_id']) == 'timeout'

    def test_running_job_still_becomes_exception(self, sqlite_run):
        def fake_run_kosma_tau(job_id, tmp_dir, **kw):
            update_job_status(job_id, 'running')
            raise RuntimeError('driver bug')

        with patch.object(engine, 'run_kosma_tau', side_effect=fake_run_kosma_tau):
            engine.run_instance_wrapper(sqlite_run['job_id'], sqlite_run['config'])
        assert _status(sqlite_run['manager'], sqlite_run['job_id']) == 'exception_runtime'

    def test_mark_job_exception_return_values(self, sqlite_run):
        jid = sqlite_run['job_id']
        assert mark_job_exception(jid) is True
        assert _status(sqlite_run['manager'], jid) == 'exception'
        update_job_status(jid, 'not_converged')
        assert mark_job_exception(jid) is False
        assert _status(sqlite_run['manager'], jid) == 'not_converged'
        assert mark_job_exception(10**6) is False


# ----------------------------------------------------------- external processes

class TestNoTransactionDuringExternalProcess:
    def test_run_pdr(self, tmp_path, monkeypatch, make_job, db_session):
        job = make_job()
        (tmp_path / 'pdroutput').mkdir()
        monkeypatch.chdir(tmp_path)
        # a pending read, as run_kosma_tau leaves one before calling run_pdr
        db_session.get(PDRModelJob, job.id)
        seen = {}

        def _wait(timeout=None):
            seen['in_transaction'] = db_session.in_transaction()
            return 0
        proc = MagicMock(pid=1)
        proc.wait.side_effect = _wait
        with patch('pdr_run.models.kosma_tau.subprocess.Popen', return_value=proc):
            kosma_tau.run_pdr(job.id, tmp_dir='.', session=db_session,
                              config={'pdr': {'max_walltime_s': 108000}})
        assert seen == {'in_transaction': False}

    def test_run_simline(self, tmp_path, monkeypatch):
        base = tmp_path / 'simline'
        (base / 'python').mkdir(parents=True)
        (base / 'python' / 'run_simline.py').write_text('')
        (base / 'python' / 'simline_config.json').write_text('{"simline_dir": "x"}\n')
        work = tmp_path / 'work'
        (work / 'pdroutput').mkdir(parents=True)
        (work / 'pdroutput' / 'pdrstruct_s.hdf5').write_bytes(b'x')
        monkeypatch.setattr('pdr_run.storage.base.get_storage_backend',
                            lambda config=None: MagicMock())
        session = MagicMock()
        session.get.return_value = MagicMock(model_job_name='j1')
        session.in_transaction.return_value = True

        class Stop(Exception):
            pass

        def _run(*a, **k):
            assert session.commit.called, "transaction still open while SIMLINE runs"
            raise Stop()
        monkeypatch.setattr(kosma_tau.subprocess, 'run', _run)
        with pytest.raises(Stop):
            kosma_tau.run_simline(1, tmp_dir=str(work),
                                  config={'simline': {'simline_dir': str(base)}},
                                  session=session)
