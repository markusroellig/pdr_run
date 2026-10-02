"""Tests for pdr_run.models.kosma_tau: job-status wiring, the wall-time
cap / process-group kill, and the UV continuum post-processing step
(including its ordering relative to onion/SIMLINE/copy_pdroutput).

No real pdrexe/subprocess is ever invoked - subprocess.Popen/run are
mocked throughout.
"""

import json
import os
import signal
import subprocess
from unittest.mock import MagicMock, patch

import pytest

from pdr_run.models import kosma_tau
from pdr_run.models.job_status import (
    STATUS_FINISHED, STATUS_FINISHED_RELAXED, STATUS_NOT_CONVERGED,
    STATUS_TIMEOUT,
)


# ---------------------------------------------------------------------------
# _kill_process_group
# ---------------------------------------------------------------------------

class TestKillProcessGroup:
    def test_sigterm_reaps_within_term_timeout(self, monkeypatch):
        proc = MagicMock()
        proc.pid = 4242
        proc.wait.return_value = -signal.SIGTERM

        killpg_calls = []
        monkeypatch.setattr(os, 'getpgid', lambda pid: 4242)
        monkeypatch.setattr(os, 'killpg', lambda pgid, sig: killpg_calls.append((pgid, sig)))

        rc = kosma_tau._kill_process_group(proc, term_timeout=1, kill_timeout=1)

        assert rc == -signal.SIGTERM
        assert killpg_calls == [(4242, signal.SIGTERM)]
        proc.wait.assert_called_once_with(timeout=1)

    def test_escalates_to_sigkill_when_sigterm_does_not_reap(self, monkeypatch):
        proc = MagicMock()
        proc.pid = 99
        # First wait() (after SIGTERM) times out; second wait() (after
        # SIGKILL) succeeds.
        proc.wait.side_effect = [subprocess.TimeoutExpired(cmd='x', timeout=1), -9]

        killpg_calls = []
        monkeypatch.setattr(os, 'getpgid', lambda pid: 99)
        monkeypatch.setattr(os, 'killpg', lambda pgid, sig: killpg_calls.append((pgid, sig)))

        rc = kosma_tau._kill_process_group(proc, term_timeout=1, kill_timeout=1)

        assert rc == -9
        assert killpg_calls == [(99, signal.SIGTERM), (99, signal.SIGKILL)]
        assert proc.wait.call_count == 2

    def test_gives_up_and_returns_none_if_unreapable(self, monkeypatch):
        proc = MagicMock()
        proc.pid = 7
        proc.wait.side_effect = subprocess.TimeoutExpired(cmd='x', timeout=1)

        monkeypatch.setattr(os, 'getpgid', lambda pid: 7)
        monkeypatch.setattr(os, 'killpg', lambda pgid, sig: None)

        rc = kosma_tau._kill_process_group(proc, term_timeout=1, kill_timeout=1)

        assert rc is None

    def test_process_already_gone_before_sigterm(self, monkeypatch):
        proc = MagicMock()
        proc.pid = 5
        proc.poll.return_value = 0

        def _raise_lookup(pid):
            raise ProcessLookupError()
        monkeypatch.setattr(os, 'getpgid', _raise_lookup)

        rc = kosma_tau._kill_process_group(proc)
        assert rc == 0
        proc.wait.assert_not_called()


# ---------------------------------------------------------------------------
# run_pdr: status classification wiring + wall-time cap
# ---------------------------------------------------------------------------

def _fake_popen(returncode=0, wait_side_effect=None):
    """Build a MagicMock standing in for subprocess.Popen(...)."""
    proc = MagicMock()
    proc.pid = 1234
    if wait_side_effect is not None:
        proc.wait.side_effect = wait_side_effect
    else:
        proc.wait.return_value = returncode
    return proc


@pytest.fixture
def pdr_workdir(tmp_path, monkeypatch):
    """Chdir into a fresh directory with an empty pdroutput/ (as engine.py
    would have created before invoking run_pdr), and hand it back."""
    outdir = tmp_path / 'pdroutput'
    outdir.mkdir()
    monkeypatch.chdir(tmp_path)
    return tmp_path


class TestRunPdrStatus:
    def test_strict_convergence_sets_finished_and_run_status_fields(
            self, pdr_workdir, make_job, db_session):
        job = make_job()

        def _write_status_and_return(*a, **k):
            (pdr_workdir / 'pdroutput' / 'run_status.json').write_text(json.dumps({
                'converged': 'strict', 'global_iterations': 11, 'eps_final': 1e-6,
                'tsearch_flagged_shells': 0, 'chem_relaxed_calls': 2,
                'deferred_iterations': 0, 'code_version': 'v2.3.0', 'git_hash': 'deadbeef',
            }))
            (pdr_workdir / 'pdroutput' / 'pdrstruct_s.hdf5').write_bytes(b'x')
            return 0

        proc = _fake_popen()
        proc.wait.side_effect = _write_status_and_return
        with patch('pdr_run.models.kosma_tau.subprocess.Popen', return_value=proc):
            kosma_tau.run_pdr(job.id, tmp_dir='.', session=db_session)

        db_session.refresh(job)
        assert job.status == STATUS_FINISHED
        assert job.run_status_global_iterations == 11
        assert job.run_status_eps_final == 1e-6
        assert job.run_status_code_version == 'v2.3.0'
        assert job.active is False
        assert job.pending is False

    def test_relaxed_convergence_sets_finished_relaxed(self, pdr_workdir, make_job, db_session):
        job = make_job()

        def _write_status_and_return(*a, **k):
            (pdr_workdir / 'pdroutput' / 'run_status.json').write_text(json.dumps({
                'converged': 'relaxed', 'global_iterations': 55, 'eps_final': 5e-2,
                'tsearch_flagged_shells': 0,
            }))
            (pdr_workdir / 'pdroutput' / 'pdrstruct_s.hdf5').write_bytes(b'x')
            return 0

        proc = _fake_popen()
        proc.wait.side_effect = _write_status_and_return
        with patch('pdr_run.models.kosma_tau.subprocess.Popen', return_value=proc):
            kosma_tau.run_pdr(job.id, tmp_dir='.', session=db_session)

        db_session.refresh(job)
        assert job.status == STATUS_FINISHED_RELAXED

    def test_not_converged_still_exits_zero_but_is_not_finished(
            self, pdr_workdir, make_job, db_session):
        job = make_job()

        def _write_status_and_return(*a, **k):
            (pdr_workdir / 'pdroutput' / 'run_status.json').write_text(json.dumps({
                'converged': 'no', 'global_iterations': 600, 'eps_final': 0.9,
            }))
            (pdr_workdir / 'pdroutput' / 'pdrstruct_s.hdf5').write_bytes(b'x')
            return 0  # pdrexe's plain-STOP-style exit: 0 even though it failed

        proc = _fake_popen()
        proc.wait.side_effect = _write_status_and_return
        with patch('pdr_run.models.kosma_tau.subprocess.Popen', return_value=proc):
            kosma_tau.run_pdr(job.id, tmp_dir='.', session=db_session)

        db_session.refresh(job)
        assert job.status == STATUS_NOT_CONVERGED
        # exit code alone (0) must NOT be interpreted as success.
        assert job.status != STATUS_FINISHED

    def test_nonzero_exit_is_aborted(self, pdr_workdir, make_job, db_session):
        job = make_job()
        proc = _fake_popen(returncode=1)
        with patch('pdr_run.models.kosma_tau.subprocess.Popen', return_value=proc):
            kosma_tau.run_pdr(job.id, tmp_dir='.', session=db_session)

        db_session.refresh(job)
        assert job.status == 'aborted'
        assert job.active is False
        assert job.pending is False

    def test_execution_time_is_pdrexe_wall_time(self, pdr_workdir, make_job, db_session):
        """execution_time = time_of_finish - time_of_start (pdrexe only)."""
        job = make_job()
        assert job.execution_time is None
        proc = _fake_popen(returncode=0)
        (pdr_workdir / 'pdroutput' / 'pdrstruct_s.hdf5').write_bytes(b'x')
        with patch('pdr_run.models.kosma_tau.subprocess.Popen', return_value=proc):
            kosma_tau.run_pdr(job.id, tmp_dir='.', session=db_session)

        db_session.refresh(job)
        assert job.execution_time is not None
        assert job.execution_time == job.time_of_finish - job.time_of_start
        assert job.execution_time.total_seconds() >= 0

    def test_execution_time_is_set_for_failed_runs_too(self, pdr_workdir, make_job, db_session):
        job = make_job()
        proc = _fake_popen(returncode=1)
        with patch('pdr_run.models.kosma_tau.subprocess.Popen', return_value=proc):
            kosma_tau.run_pdr(job.id, tmp_dir='.', session=db_session)
        db_session.refresh(job)
        assert job.status == 'aborted' and job.execution_time is not None

    def test_uses_list_argv_not_shell_true(self, pdr_workdir, make_job, db_session):
        """The historical bug: shell=True means the process group we can
        kill is the shell, not pdrexe. Guard that we now launch pdrexe
        directly (no shell) in its own session/process group."""
        job = make_job()
        proc = _fake_popen(returncode=0)
        (pdr_workdir / 'pdroutput' / 'pdrstruct_s.hdf5').write_bytes(b'x')
        with patch('pdr_run.models.kosma_tau.subprocess.Popen', return_value=proc) as popen_mock:
            kosma_tau.run_pdr(job.id, tmp_dir='.', session=db_session)

        args, kwargs = popen_mock.call_args
        assert kwargs.get('start_new_session') is True
        assert 'shell' not in kwargs  # i.e. not shell=True
        # first positional arg is the argv list, not a single shell string
        argv = args[0]
        assert isinstance(argv, list)
        assert argv[0] == './pdrexe'


class TestRunPdrWallTimeCap:
    def test_no_cap_by_default_passes_timeout_none(self, pdr_workdir, make_job, db_session):
        job = make_job()
        proc = _fake_popen(returncode=0)
        (pdr_workdir / 'pdroutput' / 'pdrstruct_s.hdf5').write_bytes(b'x')
        with patch('pdr_run.models.kosma_tau.subprocess.Popen', return_value=proc):
            kosma_tau.run_pdr(job.id, tmp_dir='.', session=db_session)
        proc.wait.assert_called_once_with(timeout=None)

    def test_config_max_walltime_is_passed_to_wait(self, pdr_workdir, make_job, db_session):
        job = make_job()
        proc = _fake_popen(returncode=0)
        (pdr_workdir / 'pdroutput' / 'pdrstruct_s.hdf5').write_bytes(b'x')
        config = {'pdr': {'max_walltime_s': 1800}}
        with patch('pdr_run.models.kosma_tau.subprocess.Popen', return_value=proc):
            kosma_tau.run_pdr(job.id, tmp_dir='.', session=db_session, config=config)
        proc.wait.assert_called_once_with(timeout=1800)

    def test_expiry_kills_process_group_and_marks_timeout(
            self, pdr_workdir, make_job, db_session):
        job = make_job()
        proc = _fake_popen()
        proc.wait.side_effect = subprocess.TimeoutExpired(cmd='./pdrexe', timeout=10)
        config = {'pdr': {'max_walltime_s': 10}}

        with patch('pdr_run.models.kosma_tau.subprocess.Popen', return_value=proc), \
             patch('pdr_run.models.kosma_tau._kill_process_group', return_value=-9) as kill_mock:
            kosma_tau.run_pdr(job.id, tmp_dir='.', session=db_session, config=config)

        kill_mock.assert_called_once_with(proc)
        db_session.refresh(job)
        assert job.status == STATUS_TIMEOUT
        assert job.active is False
        assert job.pending is False


# ---------------------------------------------------------------------------
# run_uv_continuum
# ---------------------------------------------------------------------------

class TestRunUvContinuum:
    def _model_dir(self, tmp_path):
        outdir = tmp_path / 'pdroutput'
        outdir.mkdir()
        (outdir / 'pdrstruct_s.hdf5').write_bytes(b'x')
        return tmp_path

    def test_missing_kosma_tau_dir_raises_value_error(self, tmp_path, make_job, db_session):
        job = make_job()
        self._model_dir(tmp_path)
        with pytest.raises(ValueError):
            kosma_tau.run_uv_continuum(job.id, tmp_dir=str(tmp_path),
                                       config={'uv_continuum': {}}, session=db_session)

    def test_missing_tool_raises_file_not_found(self, tmp_path, make_job, db_session):
        job = make_job()
        self._model_dir(tmp_path)
        kt_dir = tmp_path / 'kosma-tau-checkout'
        kt_dir.mkdir()
        with pytest.raises(FileNotFoundError):
            kosma_tau.run_uv_continuum(
                job.id, tmp_dir=str(tmp_path),
                config={'uv_continuum': {'kosma_tau_dir': str(kt_dir)}},
                session=db_session)

    def _make_tool(self, tmp_path):
        kt_dir = tmp_path / 'kosma-tau-checkout'
        (kt_dir / 'h2py').mkdir(parents=True)
        tool = kt_dir / 'h2py' / 'postprocess_uv_continuum.py'
        tool.write_text('#!/usr/bin/env python3\n')
        return kt_dir

    def test_success_sets_applied_and_closure_ok(self, tmp_path, make_job, db_session):
        job = make_job()
        self._model_dir(tmp_path)
        kt_dir = self._make_tool(tmp_path)
        completed = MagicMock(returncode=0)
        with patch('pdr_run.models.kosma_tau.subprocess.run', return_value=completed) as run_mock:
            result = kosma_tau.run_uv_continuum(
                job.id, tmp_dir=str(tmp_path),
                config={'uv_continuum': {'kosma_tau_dir': str(kt_dir)}},
                session=db_session)

        assert result is True
        db_session.refresh(job)
        assert job.uvcont_applied is True
        assert job.uvcont_closure_ok is True
        assert job.uvcont_error is None
        # PYTHONPATH must include h2py/ so the tool can import kosma_h2.
        _, run_kwargs = run_mock.call_args
        assert str(kt_dir / 'h2py') in run_kwargs['env']['PYTHONPATH']

    def test_closure_gate_failure_is_not_an_exception(self, tmp_path, make_job, db_session):
        job = make_job()
        self._model_dir(tmp_path)
        kt_dir = self._make_tool(tmp_path)
        completed = MagicMock(returncode=kosma_tau.UV_CONTINUUM_CLOSURE_EXIT)
        with patch('pdr_run.models.kosma_tau.subprocess.run', return_value=completed):
            result = kosma_tau.run_uv_continuum(
                job.id, tmp_dir=str(tmp_path),
                config={'uv_continuum': {'kosma_tau_dir': str(kt_dir)}},
                session=db_session)

        assert result is False
        db_session.refresh(job)
        assert job.uvcont_applied is False
        assert job.uvcont_closure_ok is False

    def test_other_nonzero_exit_raises_runtime_error(self, tmp_path, make_job, db_session):
        job = make_job()
        self._model_dir(tmp_path)
        kt_dir = self._make_tool(tmp_path)
        completed = MagicMock(returncode=2)
        with patch('pdr_run.models.kosma_tau.subprocess.run', return_value=completed):
            with pytest.raises(RuntimeError):
                kosma_tau.run_uv_continuum(
                    job.id, tmp_dir=str(tmp_path),
                    config={'uv_continuum': {'kosma_tau_dir': str(kt_dir)}},
                    session=db_session)

    def test_force_flag_is_passed_through(self, tmp_path, make_job, db_session):
        job = make_job()
        self._model_dir(tmp_path)
        kt_dir = self._make_tool(tmp_path)
        completed = MagicMock(returncode=0)
        with patch('pdr_run.models.kosma_tau.subprocess.run', return_value=completed) as run_mock:
            kosma_tau.run_uv_continuum(
                job.id, tmp_dir=str(tmp_path),
                config={'uv_continuum': {'kosma_tau_dir': str(kt_dir), 'force': True}},
                session=db_session)
        argv = run_mock.call_args[0][0]
        assert '--force' in argv


# ---------------------------------------------------------------------------
# run_kosma_tau: gating + ordering of the UV continuum step
# ---------------------------------------------------------------------------

@pytest.fixture
def orchestration_mocks(monkeypatch, db_session):
    """Patch out everything run_kosma_tau touches except the pieces under
    test (retrieve_job_parameters, storage backend existence check,
    template generation, run_pdr/run_onion/run_simline/copy_pdroutput/
    run_uv_continuum), recording call order in a shared list.

    run_kosma_tau always creates (and, in its ``finally``, closes) its own
    session via ``get_db_manager().get_session()`` - it takes no session
    argument - so that call is patched here to hand back the test's
    ``db_session`` fixture instead of opening a second, unrelated
    in-memory database.
    """
    order = []

    def _record(name):
        def _f(*a, **k):
            order.append(name)
        return _f

    storage = MagicMock()
    storage.file_exists = MagicMock(return_value=False)

    fake_db_manager = MagicMock()
    fake_db_manager.get_session.return_value = db_session

    monkeypatch.setattr(kosma_tau, 'retrieve_job_parameters',
                        lambda job_id, session: ('1', '3.0', '0.0', '1.0', '0.0'))
    monkeypatch.setattr('pdr_run.storage.base.get_storage_backend', lambda config=None: storage)
    monkeypatch.setattr(kosma_tau, 'get_db_manager', lambda *a, **k: fake_db_manager)
    monkeypatch.setattr(kosma_tau, 'create_json_from_job_id', _record('create_json'))
    monkeypatch.setattr(kosma_tau, 'create_pdrnew_from_job_id',
                        MagicMock(side_effect=FileNotFoundError()))
    monkeypatch.setattr(kosma_tau, 'run_pdr', _record('run_pdr'))
    monkeypatch.setattr(kosma_tau, 'run_uv_continuum', _record('run_uv_continuum'))
    monkeypatch.setattr(kosma_tau, 'run_onion', _record('run_onion'))
    monkeypatch.setattr(kosma_tau, 'copy_onionoutput', _record('copy_onionoutput'))
    monkeypatch.setattr(kosma_tau, 'set_oniondir', _record('set_oniondir'))
    monkeypatch.setattr(kosma_tau, 'run_simline', _record('run_simline'))
    monkeypatch.setattr(kosma_tau, 'copy_pdroutput', _record('copy_pdroutput'))
    monkeypatch.setattr(kosma_tau, 'update_db_pdr_output_entries', _record('update_db_pdr_output_entries'))

    return order


class TestRunKosmaTauUvContinuumOrdering:
    def test_uvcont_disabled_by_default_not_called(self, orchestration_mocks, make_job, db_session):
        job = make_job(onion_species='')
        kosma_tau.run_kosma_tau(job.id, tmp_dir='.', config={})
        assert 'run_uv_continuum' not in orchestration_mocks

    def test_uvcont_enabled_runs_before_simline_and_copy(
            self, orchestration_mocks, make_job, db_session):
        job = make_job(onion_species='')

        # run_pdr's mock doesn't set job.status; simulate a converged run.
        def _run_pdr_success(job_id, tmp_dir, session=None, config=None):
            orchestration_mocks.append('run_pdr')
            j = session.get(kosma_tau.PDRModelJob, job_id)
            j.status = 'finished'

        config = {'uv_continuum': {'enabled': True, 'kosma_tau_dir': '/x'},
                 'simline': {'enabled': True}}
        with patch.object(kosma_tau, 'run_pdr', side_effect=_run_pdr_success):
            kosma_tau.run_kosma_tau(job.id, tmp_dir='.', config=config)

        assert 'run_uv_continuum' in orchestration_mocks
        i_pdr = orchestration_mocks.index('run_pdr')
        i_uv = orchestration_mocks.index('run_uv_continuum')
        i_simline = orchestration_mocks.index('run_simline')
        i_copy = orchestration_mocks.index('copy_pdroutput')
        assert i_pdr < i_uv < i_simline < i_copy

    def test_uvcont_not_run_when_model_did_not_succeed(
            self, orchestration_mocks, make_job, db_session):
        job = make_job(onion_species='')

        def _run_pdr_not_converged(job_id, tmp_dir, session=None, config=None):
            orchestration_mocks.append('run_pdr')
            j = session.get(kosma_tau.PDRModelJob, job_id)
            j.status = 'not_converged'

        config = {'uv_continuum': {'enabled': True, 'kosma_tau_dir': '/x'}}
        with patch.object(kosma_tau, 'run_pdr', side_effect=_run_pdr_not_converged):
            kosma_tau.run_kosma_tau(job.id, tmp_dir='.', config=config)

        assert 'run_uv_continuum' not in orchestration_mocks

    def test_uvcont_failure_does_not_abort_the_workflow(
            self, orchestration_mocks, make_job, db_session):
        job = make_job(onion_species='')
        job_id = job.id

        def _run_pdr_success(job_id, tmp_dir, session=None, config=None):
            orchestration_mocks.append('run_pdr')
            j = session.get(kosma_tau.PDRModelJob, job_id)
            j.status = 'finished'

        config = {'uv_continuum': {'enabled': True, 'kosma_tau_dir': '/x'}}
        with patch.object(kosma_tau, 'run_pdr', side_effect=_run_pdr_success), \
             patch.object(kosma_tau, 'run_uv_continuum',
                          side_effect=RuntimeError('tool exploded')):
            # must not raise: a broken post-processing step must not fail
            # an already-successful model run.
            kosma_tau.run_kosma_tau(job_id, tmp_dir='.', config=config)

        # run_kosma_tau closed db_session in its finally block; re-fetch
        # (a plain attribute refresh() on the now-detached job instance
        # would raise) to check what was actually committed.
        reloaded = db_session.get(kosma_tau.PDRModelJob, job_id)
        assert reloaded.status == 'finished'
        assert reloaded.uvcont_error == 'tool exploded'
        # the rest of the pipeline still ran
        assert 'copy_pdroutput' in orchestration_mocks


# ---------------------------------------------------------------------------
# run_kosma_tau: failure handling (status kept, post-processing gated,
# storage failures, --rerun)
# ---------------------------------------------------------------------------

def _pdr_sets_status(order, status):
    def _run_pdr(job_id, tmp_dir, session=None, config=None):
        order.append('run_pdr')
        session.get(kosma_tau.PDRModelJob, job_id).status = status
        session.commit()   # as run_pdr's update_job_status does
    return _run_pdr


def _reload(db_session, job_id):
    return db_session.get(kosma_tau.PDRModelJob, job_id)


class TestPostProcessingGating:
    @pytest.mark.parametrize('status', ['aborted', 'timeout', 'missing_output'])
    def test_unusable_model_skips_postprocessing_keeps_status_and_stores_logs(
            self, status, orchestration_mocks, make_job, db_session):
        job = make_job(onion_species='CO')
        job_id = job.id
        config = {'simline': {'enabled': True},
                  'uv_continuum': {'enabled': True, 'kosma_tau_dir': '/x'}}
        with patch.object(kosma_tau, 'run_pdr',
                          side_effect=_pdr_sets_status(orchestration_mocks, status)):
            kosma_tau.run_kosma_tau(job_id, tmp_dir='.', config=config)

        for step in ('run_onion', 'set_oniondir', 'copy_onionoutput',
                     'run_simline', 'run_uv_continuum'):
            assert step not in orchestration_mocks
        assert 'copy_pdroutput' in orchestration_mocks      # logs are stored
        assert _reload(db_session, job_id).status == status  # not exception_runtime

    def test_not_converged_is_stored_but_not_postprocessed(
            self, orchestration_mocks, make_job, db_session):
        job = make_job(onion_species='CO')
        with patch.object(kosma_tau, 'run_pdr',
                          side_effect=_pdr_sets_status(orchestration_mocks, 'not_converged')):
            kosma_tau.run_kosma_tau(job.id, tmp_dir='.', config={})
        assert 'run_onion' not in orchestration_mocks
        assert 'copy_pdroutput' in orchestration_mocks

    def test_onion_failure_keeps_status_records_error_and_stores_output(
            self, orchestration_mocks, make_job, db_session):
        job = make_job(onion_species='CO')
        job_id = job.id
        with patch.object(kosma_tau, 'run_pdr',
                          side_effect=_pdr_sets_status(orchestration_mocks, 'finished')), \
             patch.object(kosma_tau, 'run_onion',
                          side_effect=FileNotFoundError('pdroutput/CTRL_IND')):
            kosma_tau.run_kosma_tau(job_id, tmp_dir='.', config={})   # must not raise
        reloaded = _reload(db_session, job_id)
        assert reloaded.status == 'finished'
        assert 'ONION CO' in reloaded.postproc_error
        assert 'CTRL_IND' in reloaded.postproc_error
        assert 'copy_pdroutput' in orchestration_mocks

    def test_simline_failure_still_stores_output(
            self, orchestration_mocks, make_job, db_session):
        job = make_job(onion_species='')
        job_id = job.id
        with patch.object(kosma_tau, 'run_pdr',
                          side_effect=_pdr_sets_status(orchestration_mocks, 'finished_relaxed')), \
             patch.object(kosma_tau, 'run_simline', side_effect=RuntimeError('simline died')):
            kosma_tau.run_kosma_tau(job_id, tmp_dir='.', config={'simline': {'enabled': True}})
        reloaded = _reload(db_session, job_id)
        assert reloaded.status == 'finished_relaxed'
        assert 'SIMLINE: simline died' in reloaded.postproc_error
        assert 'copy_pdroutput' in orchestration_mocks

    def test_postprocessing_storage_failure_gives_failed_storage_not_overwritten(
            self, orchestration_mocks, make_job, db_session):
        job = make_job(onion_species='CO')
        job_id = job.id
        received = {}

        def _copy_pdroutput(job_id, config=None, session=None, model_status=None):
            received['model_status'] = model_status
            received['status_then'] = session.get(kosma_tau.PDRModelJob, job_id).status
        with patch.object(kosma_tau, 'run_pdr',
                          side_effect=_pdr_sets_status(orchestration_mocks, 'finished')), \
             patch.object(kosma_tau, 'copy_onionoutput', return_value=False), \
             patch.object(kosma_tau, 'copy_pdroutput', side_effect=_copy_pdroutput):
            kosma_tau.run_kosma_tau(job_id, tmp_dir='.', config={})
        assert _reload(db_session, job_id).status == 'failed_storage'
        # the result files are still judged by the model's own status
        assert received['model_status'] == 'finished'


class TestCopyPdroutputStorage:
    @pytest.fixture
    def outdir(self, tmp_path, monkeypatch):
        (tmp_path / 'pdroutput').mkdir()
        (tmp_path / 'pdroutput' / 'TEXTOUT').write_text('screen log')
        (tmp_path / 'pdroutput' / 'run_status.json').write_text('{}')
        (tmp_path / 'pdroutput' / 'pdrstruct_s.hdf5').write_text('partial')
        (tmp_path / 'pdrexe_error.log').write_text('boom')
        monkeypatch.chdir(tmp_path)
        return tmp_path

    def test_aborted_stores_logs_but_no_result_files(
            self, outdir, make_job, db_session, monkeypatch):
        job = make_job()
        storage = MagicMock()
        storage.store_file.return_value = True
        monkeypatch.setattr('pdr_run.storage.base.get_storage_backend', lambda config=None: storage)
        assert kosma_tau.copy_pdroutput(job.id, session=db_session, model_status='aborted') is True
        stored = [os.path.basename(c.args[1]) for c in storage.store_file.call_args_list]
        assert 'TEXTOUTj001' in stored
        assert 'run_statusj001.json' in stored
        assert 'pdrexe_errorj001.log' in stored
        # a partial structure file must never look like a finished node
        assert 'pdrstructj001.hdf5' not in stored

    @pytest.mark.parametrize('failure', ['returns_false', 'raises'])
    def test_storage_failure_after_retries_gives_failed_storage(
            self, failure, outdir, make_job, db_session, monkeypatch):
        job = make_job(status='finished')
        job_id = job.id
        storage = MagicMock()
        if failure == 'returns_false':
            storage.store_file.return_value = False
        else:
            storage.store_file.side_effect = OSError('disk full')
        monkeypatch.setattr('pdr_run.storage.base.get_storage_backend', lambda config=None: storage)
        assert kosma_tau.copy_pdroutput(job_id, session=db_session,
                                        model_status='finished') is False
        assert _reload(db_session, job_id).status == 'failed_storage'

    def test_one_failed_file_does_not_stop_the_others(
            self, outdir, make_job, db_session, monkeypatch):
        job = make_job(status='finished')
        storage = MagicMock()
        storage.store_file.side_effect = lambda src, dst: 'TEXTOUT' not in dst
        monkeypatch.setattr('pdr_run.storage.base.get_storage_backend', lambda config=None: storage)
        kosma_tau.copy_pdroutput(job.id, session=db_session, model_status='finished')
        stored = [os.path.basename(c.args[1]) for c in storage.store_file.call_args_list]
        assert 'pdrstructj001.hdf5' in stored


class TestRerun:
    @pytest.fixture
    def existing_model(self, orchestration_mocks, monkeypatch):
        """Storage says the node exists."""
        storage = MagicMock()
        storage.file_exists.return_value = True
        monkeypatch.setattr('pdr_run.storage.base.get_storage_backend', lambda config=None: storage)
        return orchestration_mocks

    def _previous_job(self, make_job, db_session, status):
        prev = make_job(status=status)
        # second job for the same node (same model_name_id / model_job_name)
        job = kosma_tau.PDRModelJob(
            model_name_id=prev.model_name_id, model_job_name=prev.model_job_name,
            kosmatau_parameters_id=prev.kosmatau_parameters_id,
            kosmatau_executable_id=prev.kosmatau_executable_id,
            chemical_database_id=prev.chemical_database_id, onion_species='')
        db_session.add(job)
        db_session.commit()
        return job.id

    def test_default_skips_existing_not_converged_node(
            self, existing_model, make_job, db_session):
        job_id = self._previous_job(make_job, db_session, 'not_converged')
        kosma_tau.run_kosma_tau(job_id, tmp_dir='.', config={})
        assert 'run_pdr' not in existing_model

    def test_rerun_not_converged_recomputes_it(self, existing_model, make_job, db_session):
        job_id = self._previous_job(make_job, db_session, 'not_converged')
        with patch.object(kosma_tau, 'run_pdr',
                          side_effect=_pdr_sets_status(existing_model, 'finished')):
            kosma_tau.run_kosma_tau(job_id, tmp_dir='.', config={},
                                    rerun=('not_converged',))
        assert 'run_pdr' in existing_model
        assert 'copy_pdroutput' in existing_model

    def test_rerun_not_converged_leaves_converged_node_skipped(
            self, existing_model, make_job, db_session):
        job_id = self._previous_job(make_job, db_session, 'finished')
        kosma_tau.run_kosma_tau(job_id, tmp_dir='.', config={}, rerun=('not_converged',))
        assert 'run_pdr' not in existing_model
        assert _reload(db_session, job_id).status == 'skipped'

    def test_rerun_selects(self):
        sel = kosma_tau.rerun_selects
        assert sel('aborted', ('failed',)) and sel('not_converged', ('failed',))
        assert not sel('finished', ('failed',)) and not sel('flagged', ('failed',))
        assert sel('finished', ('all',)) and sel(None, ('all',))
        assert not sel(None, ('failed',)) and not sel('aborted', ('not_converged',))
        assert not sel('aborted', None)


class TestSkipStoredGz:
    """A node stored whole-file compressed (pdrstruct...hdf5.gz) counts as stored."""

    @pytest.fixture
    def gz_node(self, orchestration_mocks, monkeypatch, tmp_path, make_job, db_session):
        from pdr_run.storage.local import LocalStorage
        storage = LocalStorage(str(tmp_path))
        monkeypatch.setattr('pdr_run.storage.base.get_storage_backend',
                            lambda config=None: storage)
        job = make_job(status='finished')
        job.model_name.model_path = str(tmp_path / 'm')
        db_session.commit()
        grid = tmp_path / 'm' / 'pdrgrid'
        grid.mkdir(parents=True)
        (grid / 'pdrstructj001.hdf5.gz').write_bytes(b'x')
        return job.id, orchestration_mocks

    def test_skip_existing_detects_gz(self, gz_node, db_session):
        job_id, order = gz_node
        kosma_tau.run_kosma_tau(job_id, tmp_dir='.', config={})
        assert 'run_pdr' not in order
        assert _reload(db_session, job_id).status == 'skipped'

    def test_rerun_all_recomputes_gz_node(self, gz_node):
        job_id, order = gz_node
        with patch.object(kosma_tau, 'run_pdr', side_effect=_pdr_sets_status(order, 'finished')):
            kosma_tau.run_kosma_tau(job_id, tmp_dir='.', config={}, rerun=('all',))
        assert 'run_pdr' in order and 'copy_pdroutput' in order


# ---------------------------------------------------------------------------
# run_simline: per-job simline directory (no shared simline.obs)
# ---------------------------------------------------------------------------

_FAKE_SIMLINE_DRIVER = '''\
import json, re, sys, time
from pathlib import Path
args = sys.argv[1:]
hdf5 = args[0]
cfg = args[args.index('--config') + 1]
text = Path(cfg).read_text()
sdir = Path(re.search(r'"simline_dir"\\s*:\\s*"([^"]*)"', text).group(1))
# the mocked "binary": per-model beam into simline.obs, then a window in which
# a second worker sharing the directory would overwrite it
tag = Path(hdf5).name
obs = sdir / 'simline.obs'
obs.write_text('beam ' + tag)
assert (sdir / 'obs.template').is_file() and (sdir / 'bin' / 'simline').is_file()
time.sleep(0.5)
if obs.read_text() != 'beam ' + tag:
    sys.exit(7)
Path('simlineoutput').mkdir(exist_ok=True)
Path('simlineoutput/obs_dir.txt').write_text(str(sdir))
'''


class TestRunSimlinePerJobDir:
    def test_concurrent_jobs_get_distinct_obs_files(self, tmp_path, monkeypatch):
        import threading

        base = tmp_path / 'frozen_simline'
        (base / 'python').mkdir(parents=True)
        (base / 'bin').mkdir()
        (base / 'bin' / 'simline').write_text('#!/bin/sh\n')
        (base / 'molecules').mkdir()
        (base / 'obs.template').write_text('beam KT_BEAM\n')
        (base / 'python' / 'run_simline.py').write_text(_FAKE_SIMLINE_DRIVER)
        (base / 'python' / 'simline_config.json').write_text(
            '{"simline_dir": "../simline"}\n')
        before = sorted(p.name for p in base.rglob('*'))

        def make_job(name):
            job = MagicMock()
            job.model_job_name = name
            job.model_name.model_path = 'm'
            return job
        jobs = {1: make_job('A'), 2: make_job('B')}
        session = MagicMock()
        session.get.side_effect = lambda _cls, jid: jobs[jid]

        stored = {}
        lock = threading.Lock()

        def fake_store(_storage, src, remote, _patterns):
            if src.endswith('obs_dir.txt'):
                with lock:
                    stored[src] = open(src).read()
            return True
        monkeypatch.setattr(kosma_tau, '_store_maybe_gz', fake_store)
        monkeypatch.setattr(kosma_tau, '_store', lambda *a, **k: True)
        monkeypatch.setattr('pdr_run.storage.base.get_storage_backend',
                            lambda cfg: MagicMock())
        monkeypatch.setattr(kosma_tau, 'retrieve_decompressed',
                            lambda _s, _r, dst: open(dst, 'w').write('x'))

        cfg = {'simline': {'simline_dir': str(base)}}
        results, errors = {}, []

        def worker(jid):
            wd = tmp_path / f'job{jid}'
            wd.mkdir()
            try:
                results[jid] = kosma_tau.run_simline(
                    jid, tmp_dir=str(wd), config=cfg, session=session)
            except Exception as exc:  # pragma: no cover - reported below
                errors.append(exc)

        threads = [threading.Thread(target=worker, args=(j,)) for j in jobs]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        assert not errors, errors
        assert results == {1: True, 2: True}
        dirs = sorted(stored.values())
        assert len(dirs) == 2 and dirs[0] != dirs[1]
        for d in dirs:
            assert d.startswith(str(tmp_path / 'job'))   # inside the job dir
        # nothing written in the shared installation
        assert sorted(p.name for p in base.rglob('*')) == before
