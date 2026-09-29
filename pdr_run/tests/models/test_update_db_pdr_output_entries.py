"""Tests for the placeholder-checksum path used when a model already exists
in storage and PDR execution is skipped (pdr_run.models.kosma_tau.
update_db_pdr_output_entries / _placeholder_file_stat).

No real pdrexe/network - all filesystem access is via pytest's tmp_path.
"""

import os

from pdr_run.database.models import HDFFile
from pdr_run.models.kosma_tau import (
    _placeholder_file_stat,
    update_db_pdr_output_entries,
    UNVERIFIED_CHECKSUM_SENTINEL,
)


# ---------------------------------------------------------------------------
# _placeholder_file_stat
# ---------------------------------------------------------------------------

def test_placeholder_file_stat_returns_real_size_for_local_file(tmp_path):
    f = tmp_path / "pdrstruct.hdf5"
    f.write_bytes(b"x" * 12345)

    size, warned = _placeholder_file_stat(str(f))

    assert size == 12345
    assert warned is False


def test_placeholder_file_stat_falls_back_to_zero_when_unreachable(tmp_path):
    missing = tmp_path / "does_not_exist.hdf5"

    size, warned = _placeholder_file_stat(str(missing))

    assert size == 0
    assert warned is True


# ---------------------------------------------------------------------------
# update_db_pdr_output_entries
# ---------------------------------------------------------------------------

def test_sentinel_is_not_a_valid_sha256_hex_digest():
    # A real sha256_sum is 64 lowercase hex characters - the sentinel must
    # never be mistaken for one by naive downstream code.
    assert len(UNVERIFIED_CHECKSUM_SENTINEL) != 64
    assert not all(c in '0123456789abcdef' for c in UNVERIFIED_CHECKSUM_SENTINEL)


def test_creates_hdf_file_entry_with_sentinel_checksum_and_zero_size_when_unreachable(
        db_session, make_job, tmp_path):
    job = make_job(model_job_name='j_skip_test')
    job.model_name.model_path = str(tmp_path)  # pdrgrid/ subdir left empty
    db_session.commit()

    update_db_pdr_output_entries(job.id, db_session)

    hdf_entry = db_session.query(HDFFile).filter_by(
        parameter_id=job.kosmatau_parameters_id,
        model_name_id=job.model_name_id,
    ).first()
    assert hdf_entry is not None
    assert hdf_entry.sha256_sum == UNVERIFIED_CHECKSUM_SENTINEL
    assert hdf_entry.sha256_sum_hdf5_s == UNVERIFIED_CHECKSUM_SENTINEL
    assert hdf_entry.sha256_sum_hdf5_c == UNVERIFIED_CHECKSUM_SENTINEL
    assert hdf_entry.file_size == 0
    assert hdf_entry.file_size_hdf5_s == 0


def test_verifies_real_size_when_struct_file_is_locally_reachable(
        db_session, make_job, tmp_path):
    job = make_job(model_job_name='j_skip_test_local')
    job.model_name.model_path = str(tmp_path)
    db_session.commit()

    pdrgrid = tmp_path / 'pdrgrid'
    pdrgrid.mkdir()
    struct_name = f'pdrstruct{job.model_job_name}.hdf5'
    (pdrgrid / struct_name).write_bytes(b"y" * 999)

    update_db_pdr_output_entries(job.id, db_session)

    hdf_entry = db_session.query(HDFFile).filter_by(
        parameter_id=job.kosmatau_parameters_id,
        model_name_id=job.model_name_id,
    ).first()
    assert hdf_entry.file_size_hdf5_s == 999
    # Still an unverified sentinel checksum - we never hash the bytes.
    assert hdf_entry.sha256_sum_hdf5_s == UNVERIFIED_CHECKSUM_SENTINEL


def test_updates_existing_entry_paths_and_keeps_sentinel(db_session, make_job, tmp_path):
    job = make_job(model_job_name='j_skip_test_update')
    job.model_name.model_path = str(tmp_path)
    db_session.commit()

    # First call creates the entry.
    update_db_pdr_output_entries(job.id, db_session)
    first = db_session.query(HDFFile).filter_by(
        parameter_id=job.kosmatau_parameters_id,
        model_name_id=job.model_name_id,
    ).first()
    assert first is not None

    # Second call (e.g. a rerun of the "model exists" skip path) must
    # update, not duplicate.
    update_db_pdr_output_entries(job.id, db_session)
    entries = db_session.query(HDFFile).filter_by(
        parameter_id=job.kosmatau_parameters_id,
        model_name_id=job.model_name_id,
    ).all()
    assert len(entries) == 1
