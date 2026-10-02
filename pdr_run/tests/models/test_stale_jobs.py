"""Tests for stale-'running'-job detection/recovery after a crashed driver.

See pdr_run.database.queries.find_stale_jobs / reset_stale_jobs. Uses the
in-memory SQLite ``db_session`` fixture (pdr_run/tests/conftest.py) and the
``make_job`` factory (pdr_run/tests/models/conftest.py) - no real DB/network.
"""

from datetime import datetime, timedelta

import pytest

from pdr_run.database.queries import (
    find_stale_jobs,
    reset_stale_jobs,
    STATUS_RESET_STALE,
    DEFAULT_STALE_AFTER_S,
)


def test_find_stale_jobs_detects_old_running_job(db_session, make_job):
    old_start = datetime.now() - timedelta(hours=10)
    job = make_job(status='running', time_of_start=old_start, active=True, pending=False)

    stale = find_stale_jobs(db_session, stale_after_s=DEFAULT_STALE_AFTER_S)

    assert [j.id for j in stale] == [job.id]


def test_find_stale_jobs_ignores_recent_running_job(db_session, make_job):
    make_job(status='running', time_of_start=datetime.now(), active=True, pending=False)

    stale = find_stale_jobs(db_session, stale_after_s=DEFAULT_STALE_AFTER_S)

    assert stale == []


def test_find_stale_jobs_ignores_non_running_status(db_session, make_job):
    old_start = datetime.now() - timedelta(hours=10)
    make_job(status='finished', time_of_start=old_start, active=False, pending=False)

    stale = find_stale_jobs(db_session, stale_after_s=DEFAULT_STALE_AFTER_S)

    assert stale == []


def test_find_stale_jobs_ignores_running_without_time_of_start(db_session, make_job):
    # A job whose time_of_start was never set (e.g. crashed before run_pdr
    # got that far) cannot be judged stale by age - must not be flagged.
    make_job(status='running', time_of_start=None, active=True, pending=False)

    stale = find_stale_jobs(db_session, stale_after_s=DEFAULT_STALE_AFTER_S)

    assert stale == []


def test_find_stale_jobs_respects_custom_threshold(db_session, make_job):
    job = make_job(status='running', time_of_start=datetime.now() - timedelta(hours=2),
                    active=True, pending=False)

    # 2h old job: not stale under a 6h default threshold...
    assert find_stale_jobs(db_session, stale_after_s=6 * 3600) == []
    # ...but is stale under a tighter, run-specific threshold (e.g. derived
    # from a configured max_walltime_s).
    stale = find_stale_jobs(db_session, stale_after_s=3600)
    assert [j.id for j in stale] == [job.id]


def test_reset_stale_jobs_marks_and_commits(db_session, make_job):
    old_start = datetime.now() - timedelta(hours=10)
    job = make_job(status='running', time_of_start=old_start, active=True, pending=False)

    reset_ids = reset_stale_jobs(db_session, stale_after_s=DEFAULT_STALE_AFTER_S)

    assert reset_ids == [job.id]
    db_session.refresh(job)
    assert job.status == STATUS_RESET_STALE
    assert job.active is False
    assert job.pending is False


def test_reset_stale_jobs_dry_run_does_not_mutate(db_session, make_job):
    old_start = datetime.now() - timedelta(hours=10)
    job = make_job(status='running', time_of_start=old_start, active=True, pending=False)

    reset_ids = reset_stale_jobs(db_session, stale_after_s=DEFAULT_STALE_AFTER_S, dry_run=True)

    assert reset_ids == [job.id]
    db_session.refresh(job)
    # Unchanged - dry_run must not touch the database.
    assert job.status == 'running'
    assert job.active is True


def test_reset_stale_jobs_no_stale_jobs_returns_empty(db_session, make_job):
    make_job(status='running', time_of_start=datetime.now(), active=True, pending=False)

    assert reset_stale_jobs(db_session, stale_after_s=DEFAULT_STALE_AFTER_S) == []


def test_reset_stale_jobs_leaves_unrelated_jobs_untouched(db_session, make_job):
    """A legitimately still-running job (recent time_of_start) from a
    concurrent, unrelated grid run must never be reset."""
    old_start = datetime.now() - timedelta(hours=10)
    stale_job = make_job(status='running', time_of_start=old_start,
                          model_job_name='stale_job', active=True, pending=False)
    fresh_job = make_job(status='running', time_of_start=datetime.now(),
                          model_job_name='fresh_job', active=True, pending=False)

    reset_ids = reset_stale_jobs(db_session, stale_after_s=DEFAULT_STALE_AFTER_S)

    assert reset_ids == [stale_job.id]
    db_session.refresh(fresh_job)
    assert fresh_job.status == 'running'
    assert fresh_job.active is True


def test_reset_clears_pending_on_running_rows(db_session, make_job):
    """A killed driver leaves rows with pending = 1; the reset clears it."""
    job = make_job(status='running', time_of_start=datetime.now() - timedelta(hours=10),
                   active=True, pending=True)
    assert reset_stale_jobs(db_session, stale_after_s=DEFAULT_STALE_AFTER_S) == [job.id]
    db_session.refresh(job)
    assert (job.status, job.active, job.pending) == (STATUS_RESET_STALE, False, False)


def test_reset_also_covers_never_started_pending_rows(db_session, make_job):
    old = datetime.now() - timedelta(hours=10)
    job = make_job(status='pending', pending=True, active=False, time_created=old)
    assert find_stale_jobs(db_session, stale_after_s=DEFAULT_STALE_AFTER_S) == []  # grid warning: running only
    assert reset_stale_jobs(db_session, stale_after_s=DEFAULT_STALE_AFTER_S) == [job.id]
    db_session.refresh(job)
    assert (job.status, job.pending) == (STATUS_RESET_STALE, False)


def test_young_rows_need_a_small_threshold(db_session, make_job):
    """Default threshold keeps young rows; --stale-after-hours 0 resets them."""
    running = make_job(status='running', time_of_start=datetime.now() - timedelta(minutes=5),
                       active=True, pending=True, model_job_name='r')
    pending = make_job(status='pending', pending=True, time_created=datetime.now() - timedelta(minutes=5),
                       model_job_name='p')
    assert reset_stale_jobs(db_session, stale_after_s=DEFAULT_STALE_AFTER_S) == []
    ids = reset_stale_jobs(db_session, stale_after_s=0)
    assert sorted(ids) == sorted([running.id, pending.id])
    for j in (running, pending):
        db_session.refresh(j)
        assert j.pending is False and j.status == STATUS_RESET_STALE


def test_reset_leaves_finished_rows_alone(db_session, make_job):
    done = make_job(status='finished', pending=False, active=False,
                    time_created=datetime.now() - timedelta(hours=10))
    assert reset_stale_jobs(db_session, stale_after_s=0) == []
    db_session.refresh(done)
    assert done.status == 'finished'
