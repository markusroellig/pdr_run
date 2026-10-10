"""First-iteration stall watchdog (pdr.stall_first_iteration_s) and the
head+tail size guard for the pdrexe error log (storage.error_log_head_tail_bytes).

The run_pdr tests start a real fake ``pdrexe`` (a small shell script in the
job directory) that writes TEXTOUT progress lines like KOSMA-tau:

    ***** current shell #:    1 ***** current iteration step:   1 ***********
"""

import os
import stat
import time
from unittest.mock import patch

import pytest

from pdr_run.models import kosma_tau
from pdr_run.models.job_status import (
    ALL_STATUSES, COMPLETE_OUTPUT_STATUSES, STATUS_FINISHED, STATUS_MISSING_OUTPUT,
    STATUS_STALLED, STATUS_TIMEOUT, determine_job_status,
)

STEP = "***** current shell #:    {sh} ***** current iteration step:   {it} ***********"


def _line(sh, it):
    return STEP.format(sh=sh, it=it)


@pytest.fixture
def workdir(tmp_path, monkeypatch):
    (tmp_path / 'pdroutput').mkdir()
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(kosma_tau, 'STALL_POLL_S', 0.05)
    return tmp_path


def _fake_pdrexe(workdir, body):
    """./pdrexe = /bin/sh script *body*; stdout goes to pdroutput/TEXTOUT."""
    exe = workdir / 'pdrexe'
    exe.write_text('#!/bin/sh\n' + body)
    exe.chmod(exe.stat().st_mode | stat.S_IXUSR)
    return exe


CONVERGED = ("mkdir -p pdroutput; : > pdroutput/pdrstruct_s.hdf5\n"
             "echo 'Model CONVERGED in 2 iterations (eps=1.0E-03)'\n")


# ------------------------------------------------------------- scanner unit

class TestFirstIterationScanner:
    def test_detects_step_two_across_split_reads(self, tmp_path):
        p = tmp_path / 'TEXTOUT'
        p.write_text(_line(1, 1) + '\n' + _line(560, 1) + '\n')
        sc = kosma_tau._FirstIterationScanner(str(p))
        assert sc.poll() is False
        full = _line(1, 2) + '\n'
        with open(p, 'a') as f:
            f.write(full[:40])                    # line cut in the middle
        assert sc.poll() is False
        with open(p, 'a') as f:
            f.write(full[40:])
        assert sc.poll() is True
        assert sc.poll() is True                  # sticky

    def test_two_digit_steps_and_step_one_only(self, tmp_path):
        p = tmp_path / 'TEXTOUT'
        p.write_text(_line(12, 1) + '\n' + 'current iteration step:   1\n')
        sc = kosma_tau._FirstIterationScanner(str(p))
        assert sc.poll() is False
        with open(p, 'a') as f:
            f.write(_line(3, 10) + '\n')
        assert sc.poll() is True

    def test_missing_file_is_not_progress(self, tmp_path):
        sc = kosma_tau._FirstIterationScanner(str(tmp_path / 'nope'))
        assert sc.poll() is False


def test_determine_job_status_stalled_is_terminal_and_logs_only(tmp_path):
    (tmp_path / 'pdroutput').mkdir()
    status, fields = determine_job_status(-15, False, str(tmp_path), stalled=True)
    assert status == STATUS_STALLED
    assert STATUS_STALLED in ALL_STATUSES
    assert STATUS_STALLED not in COMPLETE_OUTPUT_STATUSES


# ------------------------------------------------------- run_pdr, fake pdrexe

def _run(job, db_session, config):
    t0 = time.monotonic()
    kosma_tau.run_pdr(job.id, tmp_dir='.', session=db_session, config=config)
    db_session.refresh(job)
    return time.monotonic() - t0


class TestRunPdrStallWatchdog:
    def test_no_step_two_is_killed_as_stalled(self, workdir, make_job, db_session):
        _fake_pdrexe(workdir, f"echo '{_line(1, 1)}'\necho '{_line(2, 1)}'\nexec sleep 30\n")
        job = make_job()
        el = _run(job, db_session, {'pdr': {'stall_first_iteration_s': 0.5,
                                            'max_walltime_s': 60}})
        assert job.status == STATUS_STALLED
        assert job.active is False and job.pending is False
        assert el < 15                          # killed long before sleep 30 / the cap

    def test_step_two_seen_is_not_killed(self, workdir, make_job, db_session):
        # step 2 at once, then the run continues well past the stall limit
        _fake_pdrexe(workdir, f"echo '{_line(1, 1)}'\necho '{_line(1, 2)}'\nsleep 1.5\n"
                     + CONVERGED)
        job = make_job()
        el = _run(job, db_session, {'pdr': {'stall_first_iteration_s': 0.5,
                                            'max_walltime_s': 60}})
        assert job.status == STATUS_FINISHED
        assert el >= 1.5

    def test_step_two_after_a_while_is_not_killed(self, workdir, make_job, db_session):
        _fake_pdrexe(workdir, f"echo '{_line(1, 1)}'\nsleep 0.3\necho '{_line(1, 2)}'\n"
                     "sleep 1.2\n" + CONVERGED)
        job = make_job()
        _run(job, db_session, {'pdr': {'stall_first_iteration_s': 1.0}})
        assert job.status == STATUS_FINISHED

    def test_feature_off_is_unchanged(self, workdir, make_job, db_session):
        # same stalled-looking output, but no watchdog: the run ends on its own
        _fake_pdrexe(workdir, f"echo '{_line(1, 1)}'\nsleep 1.0\n")
        job = make_job()
        with patch.object(kosma_tau, '_wait_with_stall_watchdog') as wd:
            _run(job, db_session, {'pdr': {'max_walltime_s': 60}})
        wd.assert_not_called()
        assert job.status == STATUS_MISSING_OUTPUT     # exit 0, no pdrstruct: as before

    def test_feature_none_is_off(self, workdir, make_job, db_session):
        _fake_pdrexe(workdir, f"echo '{_line(1, 1)}'\nsleep 0.8\n" + CONVERGED)
        job = make_job()
        _run(job, db_session, {'pdr': {'stall_first_iteration_s': None}})
        assert job.status == STATUS_FINISHED

    def test_wall_time_cap_still_applies_with_watchdog(self, workdir, make_job, db_session):
        _fake_pdrexe(workdir, f"echo '{_line(1, 2)}'\nexec sleep 30\n")
        job = make_job()
        el = _run(job, db_session, {'pdr': {'stall_first_iteration_s': 100,
                                            'max_walltime_s': 0.7}})
        assert job.status == STATUS_TIMEOUT
        assert el < 15

    def test_wall_time_cap_before_stall_limit(self, workdir, make_job, db_session):
        _fake_pdrexe(workdir, f"echo '{_line(1, 1)}'\nexec sleep 30\n")
        job = make_job()
        _run(job, db_session, {'pdr': {'stall_first_iteration_s': 100,
                                       'max_walltime_s': 0.5}})
        assert job.status == STATUS_TIMEOUT


# ----------------------------------------------------------- preflight line

def test_preflight_walltime_reports_stall_watchdog():
    from unittest.mock import MagicMock
    from pdr_run.cli import preflight
    ctx = MagicMock()
    ctx.eff = {'pdr': {'max_walltime_s': 108000}}
    st, msg = preflight.check_walltime(ctx)
    assert st == preflight.PASS and 'stall watchdog off' in msg
    ctx.eff = {'pdr': {'max_walltime_s': 108000, 'stall_first_iteration_s': 28800}}
    st, msg = preflight.check_walltime(ctx)
    assert st == preflight.PASS and '28800' in msg and '8.0 h' in msg
    ctx.eff = {'pdr': {'max_walltime_s': 3600, 'stall_first_iteration_s': 7200}}
    assert preflight.check_walltime(ctx)[0] == preflight.WARN


# --------------------------------------------- error-log head+tail size guard

def test_head_tail_copy(tmp_path):
    src = tmp_path / 'big.log'
    src.write_bytes(b'A' * 1000 + b'M' * 5000 + b'Z' * 1000)
    dst = tmp_path / 'out.log'
    kosma_tau.head_tail_copy(str(src), str(dst), 2000)
    out = dst.read_bytes()
    assert out.startswith(b'A' * 1000) and out.endswith(b'Z' * 1000)
    assert b'M' not in out
    assert b'5000 bytes omitted, original size 7000 bytes' in out


@pytest.fixture
def failed_run(tmp_path, monkeypatch, make_job, db_session):
    """A stalled job's directory: TEXTOUT and a large pdrexe_error.log, local storage."""
    from pdr_run.storage.local import LocalStorage
    run = tmp_path / 'run'
    (run / 'pdroutput').mkdir(parents=True)
    (run / 'pdroutput' / 'TEXTOUT').write_bytes(b'log\n')
    log = b'H' * 3000 + b'WARNING Tier1 MAXIT fail: O\n' * 2000 + b'T' * 3000
    (run / 'pdrexe_error.log').write_bytes(log)
    monkeypatch.chdir(run)
    storage = LocalStorage(str(tmp_path / 'store'))
    monkeypatch.setattr('pdr_run.storage.base.get_storage_backend',
                        lambda config=None: storage)
    job = make_job(status=STATUS_STALLED)
    job.model_name.model_path = str(tmp_path / 'store' / 'm')
    db_session.commit()
    return dict(run=run, job=job, grid=tmp_path / 'store' / 'm' / 'pdrgrid',
                session=db_session, log=log)


def _copy_failed(fr, storage_cfg):
    return kosma_tau.copy_pdroutput(fr['job'].id, config={'storage': storage_cfg},
                                    session=fr['session'], model_status=STATUS_STALLED)


def test_error_log_follows_compress_files(failed_run):
    import gzip
    assert _copy_failed(failed_run, {'compress_files': ['TEXTOUT*', 'pdrexe_error*.log']})
    gz = failed_run['grid'] / 'pdrexe_errorj001.log.gz'
    assert gzip.decompress(gz.read_bytes()) == failed_run['log']
    assert not (failed_run['grid'] / 'pdrexe_errorj001.log').exists()
    assert not (failed_run['grid'] / 'pdrstructj001.hdf5').exists()   # logs only


def test_error_log_without_pattern_is_stored_in_full(failed_run):
    assert _copy_failed(failed_run, {'compress_files': ['TEXTOUT*']})
    assert (failed_run['grid'] / 'pdrexe_errorj001.log').read_bytes() == failed_run['log']


def test_error_log_head_tail_guard(failed_run):
    import gzip
    assert _copy_failed(failed_run, {'compress_files': ['pdrexe_error*.log'],
                                     'error_log_head_tail_bytes': 6000})
    out = gzip.decompress((failed_run['grid'] / 'pdrexe_errorj001.log.gz').read_bytes())
    assert out.startswith(b'H' * 3000) and out.endswith(b'T' * 3000)
    assert b'bytes omitted' in out and len(out) < len(failed_run['log'])
    assert not os.path.exists('pdrexe_error.log.headtail')
    # the local log itself is untouched
    assert (failed_run['run'] / 'pdrexe_error.log').read_bytes() == failed_run['log']


def test_error_log_below_guard_is_unchanged(failed_run):
    assert _copy_failed(failed_run, {'error_log_head_tail_bytes': 10 ** 9})
    assert (failed_run['grid'] / 'pdrexe_errorj001.log').read_bytes() == failed_run['log']


def test_status_json_class_of_stalled_is_bad():
    from pdr_run.cli.status import status_class
    assert status_class(STATUS_STALLED) == 'bad'
