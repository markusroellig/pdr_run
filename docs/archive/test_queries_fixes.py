#!/usr/bin/env python
"""Test fixes for queries.py commit error handling."""

import os
import sys
from unittest.mock import Mock, patch, MagicMock
import pytest

# Set up test environment
os.environ['PDR_DB_TYPE'] = 'sqlite'
os.environ['PDR_DB_PATH'] = ':memory:'

# Import after environment setup
from pdr_run.database.db_manager import get_db_manager
from pdr_run.database.queries import (
    get_or_create,
    get_model_name_id,
    _update_job_status
)
from pdr_run.database.models import ModelNames, PDRModelJob


def setup_test_database():
    """Set up a test database with required tables."""
    # Reset the database manager to ensure fresh state
    import pdr_run.database.db_manager as dbm
    dbm._db_manager = None  # Reset singleton

    db_manager = get_db_manager()
    db_manager.create_tables()
    return db_manager


def test_get_or_create_success():
    """Test successful get_or_create."""
    print("\n" + "="*80)
    print("TEST 1: get_or_create() - Success (Create New)")
    print("="*80)

    db_manager = setup_test_database()
    session = db_manager.get_session()

    try:
        model = get_or_create(
            session,
            ModelNames,
            model_name='test_model',
            model_path='/tmp/test'
        )

        assert model.model_name == 'test_model'
        assert model.model_path == '/tmp/test'
        assert model.id is not None
        print(f"✅ PASS: Model created successfully (ID: {model.id})")

    finally:
        session.close()

    print("✅ TEST 1 PASSED\n")


def test_get_or_create_existing():
    """Test get_or_create with existing record."""
    print("\n" + "="*80)
    print("TEST 2: get_or_create() - Success (Get Existing)")
    print("="*80)

    db_manager = setup_test_database()
    session = db_manager.get_session()

    try:
        # Create first time
        model1 = get_or_create(
            session,
            ModelNames,
            model_name='test_model',
            model_path='/tmp/test'
        )
        id1 = model1.id

        # Get existing
        model2 = get_or_create(
            session,
            ModelNames,
            model_name='test_model',
            model_path='/tmp/test'
        )
        id2 = model2.id

        assert id1 == id2, "Should return same record"
        print(f"✅ PASS: Existing model returned (ID: {id2})")

    finally:
        session.close()

    print("✅ TEST 2 PASSED\n")


def test_get_or_create_commit_failure():
    """Test get_or_create with commit failure and rollback."""
    print("\n" + "="*80)
    print("TEST 3: get_or_create() - Commit Failure with Rollback")
    print("="*80)

    setup_test_database()

    # Mock session to simulate commit failure
    mock_session = MagicMock()
    mock_session.query.return_value.filter_by.return_value.first.return_value = None
    mock_session.commit.side_effect = Exception("Simulated commit failure")

    with pytest.raises(Exception) as exc_info:
        get_or_create(
            mock_session,
            ModelNames,
            model_name='test_model',
            model_path='/tmp/test'
        )

    assert "Simulated commit failure" in str(exc_info.value)
    assert mock_session.rollback.called, "Rollback should be called on failure"
    print(f"✅ PASS: Commit failure properly handled")
    print(f"✅ PASS: Rollback was called")

    print("✅ TEST 3 PASSED\n")


def test_get_model_name_id_success():
    """Test successful get_model_name_id (create new)."""
    print("\n" + "="*80)
    print("TEST 4: get_model_name_id() - Success (Create New)")
    print("="*80)

    setup_test_database()

    model_id = get_model_name_id('test_model', '/tmp/test')

    assert model_id is not None
    assert isinstance(model_id, int)
    print(f"✅ PASS: Model name created successfully (ID: {model_id})")

    print("✅ TEST 4 PASSED\n")


def test_get_model_name_id_existing():
    """Test get_model_name_id with existing record."""
    print("\n" + "="*80)
    print("TEST 5: get_model_name_id() - Success (Get Existing)")
    print("="*80)

    setup_test_database()

    # Create first time
    id1 = get_model_name_id('test_model', '/tmp/test')

    # Get existing
    id2 = get_model_name_id('test_model', '/tmp/test')

    assert id1 == id2, "Should return same ID"
    print(f"✅ PASS: Existing model ID returned (ID: {id2})")

    print("✅ TEST 5 PASSED\n")


def test_get_model_name_id_commit_failure():
    """Test get_model_name_id with commit failure and rollback."""
    print("\n" + "="*80)
    print("TEST 6: get_model_name_id() - Commit Failure with Rollback")
    print("="*80)

    setup_test_database()

    with patch('pdr_run.database.queries.get_db_manager') as mock_get_db:
        mock_db_manager = MagicMock()
        mock_session = MagicMock()

        # Mock query to return 0 count (new record)
        mock_query = MagicMock()
        mock_query.count.return_value = 0
        mock_session.query.return_value.filter.return_value = mock_query

        # Simulate commit failure
        mock_session.commit.side_effect = Exception("Model name commit failed")

        mock_db_manager.get_session.return_value = mock_session
        mock_get_db.return_value = mock_db_manager

        with pytest.raises(Exception) as exc_info:
            get_model_name_id('test_model', '/tmp/test')

        assert "Model name commit failed" in str(exc_info.value)
        assert mock_session.rollback.called, "Rollback should be called"
        assert mock_session.close.called, "Session should be closed"
        print(f"✅ PASS: Commit failure properly handled")
        print(f"✅ PASS: Rollback was called")
        print(f"✅ PASS: Session was closed")

    print("✅ TEST 6 PASSED\n")


def test_update_job_status_success():
    """Test successful job status update."""
    print("\n" + "="*80)
    print("TEST 7: _update_job_status() - Success")
    print("="*80)

    db_manager = setup_test_database()
    session = db_manager.get_session()

    try:
        # Create a job first
        model_name = ModelNames(model_name='test_model', model_path='/tmp/test')
        session.add(model_name)
        session.flush()

        job = PDRModelJob(model_name_id=model_name.id, model_job_name='test_job')
        session.add(job)
        session.commit()
        job_id = job.id

        # Update status
        _update_job_status(job_id, 'running', session)

        # Verify
        updated_job = session.get(PDRModelJob, job_id)
        assert updated_job.status == 'running'
        assert updated_job.active is True
        assert updated_job.pending is False
        print(f"✅ PASS: Job status updated successfully")

    finally:
        session.close()

    print("✅ TEST 7 PASSED\n")


def test_update_job_status_commit_failure():
    """Test job status update with commit failure and rollback."""
    print("\n" + "="*80)
    print("TEST 8: _update_job_status() - Commit Failure with Rollback")
    print("="*80)

    setup_test_database()

    # Mock session with job
    mock_session = MagicMock()
    mock_job = MagicMock()
    mock_job.id = 123
    mock_job.status = 'pending'
    mock_session.get.return_value = mock_job

    # Simulate commit failure
    mock_session.commit.side_effect = Exception("Status update commit failed")

    with pytest.raises(Exception) as exc_info:
        _update_job_status(123, 'running', mock_session)

    assert "Status update commit failed" in str(exc_info.value)
    assert mock_session.rollback.called, "Rollback should be called"
    print(f"✅ PASS: Commit failure properly handled")
    print(f"✅ PASS: Rollback was called")

    print("✅ TEST 8 PASSED\n")


def test_update_job_status_not_found():
    """Test job status update with non-existent job."""
    print("\n" + "="*80)
    print("TEST 9: _update_job_status() - Job Not Found")
    print("="*80)

    db_manager = setup_test_database()
    session = db_manager.get_session()

    try:
        with pytest.raises(ValueError) as exc_info:
            _update_job_status(99999, 'running', session)

        assert "Job with ID 99999 not found" in str(exc_info.value)
        print(f"✅ PASS: Proper error for non-existent job")

    finally:
        session.close()

    print("✅ TEST 9 PASSED\n")


def main():
    """Run all tests."""
    print("\n" + "="*80)
    print("QUERIES.PY FIX TESTING")
    print("Testing database commit rollback in all queries.py functions")
    print("="*80)

    try:
        test_get_or_create_success()
        test_get_or_create_existing()
        test_get_or_create_commit_failure()
        test_get_model_name_id_success()
        test_get_model_name_id_existing()
        test_get_model_name_id_commit_failure()
        test_update_job_status_success()
        test_update_job_status_commit_failure()
        test_update_job_status_not_found()

        print("\n" + "="*80)
        print("ALL TESTS PASSED ✅")
        print("="*80)
        print("\nSummary:")
        print("✅ get_or_create() with rollback - WORKING")
        print("✅ get_model_name_id() with rollback - WORKING")
        print("✅ _update_job_status() with rollback - WORKING")
        print("\nAll queries.py commits are properly protected!")
        print("="*80 + "\n")

        return 0

    except Exception as e:
        print(f"\n❌ TEST FAILED: {e}")
        import traceback
        traceback.print_exc()
        return 1


if __name__ == '__main__':
    sys.exit(main())
