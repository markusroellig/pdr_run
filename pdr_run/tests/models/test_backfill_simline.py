"""Tests for scripts/backfill_simline.py (run_simline mocked, in-memory DB)."""

import importlib.util
import os
from unittest.mock import patch

import pytest

from pdr_run.database.models import PDRModelJob

_SCRIPT = os.path.join(os.path.dirname(__file__), '..', '..', '..', 'scripts',
                       'backfill_simline.py')
_spec = importlib.util.spec_from_file_location('backfill_simline', _SCRIPT)
bf = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(bf)

OLD = ("SIMLINE: SIMLINE pipeline exited with 1 for job 7 "
       "(see simlineoutput/TEXTOUT_SIMLINE)")
PARTIAL = "SIMLINE: partial: failed species C+, 13C+; missing outputs O [fits]"


@pytest.mark.parametrize('text,new,expected', [
    (OLD, None, None),
    (OLD, 'boom', 'SIMLINE: boom'),
    ('ONION CO: x; ' + OLD, None, 'ONION CO: x'),
    ('ONION CO: a; b; ' + OLD, 'c', 'ONION CO: a; b; SIMLINE: c'),
    (None, None, None),
    (PARTIAL, None, None),
    ('ONION CO: x; ' + PARTIAL, 'partial: failed species C', 'ONION CO: x; SIMLINE: partial: failed species C'),
])
def test_replace_simline_segment(text, new, expected):
    assert bf.replace_simline_segment(text, new) == expected


def test_select_jobs(db_session, make_job):
    a = make_job(status='finished', postproc_error=OLD)
    make_job(status='not_converged', postproc_error=OLD)
    make_job(status='finished', postproc_error=None)
    make_job(status='finished', postproc_error='ONION CO: x')
    b = make_job(status='finished', postproc_error=PARTIAL)
    assert [j.id for j in bf.select_jobs(db_session, 'testmodel')] == [a.id, b.id]
    assert bf.select_jobs(db_session, 'other') == []


@pytest.mark.parametrize('ret,exc,result,pp,status', [
    (True, None, 'ok', None, 'finished'),
    (None, RuntimeError('again'), 'failed', 'SIMLINE: again', 'finished'),
    (False, None, 'failed_storage', OLD, 'failed_storage'),
])
def test_backfill_job(db_session, make_job, tmp_path, ret, exc, result, pp, status):
    job = make_job(status='finished', postproc_error=OLD)
    seen = {}

    def fake_run_simline(job_id, tmp_dir, config=None, session=None):
        seen['tmp_dir'] = tmp_dir
        assert os.path.isdir(tmp_dir) and not os.listdir(tmp_dir)
        if exc:
            os.makedirs(os.path.join(tmp_dir, 'simlineoutput'))
            with open(os.path.join(tmp_dir, 'simlineoutput', 'TEXTOUT_SIMLINE'), 'w') as f:
                f.write('log')
            raise exc
        return ret

    with patch('pdr_run.models.kosma_tau.run_simline', side_effect=fake_run_simline):
        got = bf.backfill_job(job.id, {}, db_session, fail_dir=str(tmp_path / 'fail'),
                              tmp_root=str(tmp_path))
    assert got == result
    job = db_session.get(PDRModelJob, job.id)
    assert job.postproc_error == pp
    assert job.status == status
    assert not os.path.exists(seen['tmp_dir'])
    if exc:
        assert (tmp_path / 'fail' / f'TEXTOUT_SIMLINE.job{job.id}').read_text() == 'log'
