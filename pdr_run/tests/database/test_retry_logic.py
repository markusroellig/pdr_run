"""Tests for database retry logic to handle connection failures during parallel execution."""

import pytest
from unittest.mock import Mock, patch, MagicMock
from sqlalchemy.exc import OperationalError, DisconnectionError
from sqlalchemy.orm import Session

from pdr_run.database.queries import (
    retry_on_db_error,
    get_or_create,
    update_job_status,
    get_model_name_id,
    get_model_info_from_job_id,
    retrieve_job_parameters
)
from pdr_run.database.models import PDRModelJob, ModelNames, KOSMAtauParameters


class TestRetryDecorator:
    """Test the retry_on_db_error decorator."""

    def test_retry_on_connection_lost(self):
        """Test that functions retry when connection is lost."""
        call_count = 0

        @retry_on_db_error(max_retries=3, initial_delay=0.01, backoff=1.5)
        def mock_db_operation():
            nonlocal call_count
            call_count += 1
            if call_count < 3:
                raise OperationalError("Lost connection to MySQL server", None, None)
            return "success"

        result = mock_db_operation()
        assert result == "success"
        assert call_count == 3  # Failed twice, succeeded on third attempt

    def test_retry_on_ssl_error(self):
        """Test that functions retry on SSL/EOF errors."""
        call_count = 0

        @retry_on_db_error(max_retries=3, initial_delay=0.01, backoff=1.5)
        def mock_db_operation():
            nonlocal call_count
            call_count += 1
            if call_count < 2:
                raise OperationalError("SSL connection error: EOF", None, None)
            return "success"

        result = mock_db_operation()
        assert result == "success"
        assert call_count == 2  # Failed once, succeeded on second attempt

    def test_retry_exhausted_raises_error(self):
        """Test that after max retries, the error is raised."""
        call_count = 0

        @retry_on_db_error(max_retries=2, initial_delay=0.01, backoff=1.5)
        def mock_db_operation():
            nonlocal call_count
            call_count += 1
            raise OperationalError("Lost connection to MySQL server", None, None)

        with pytest.raises(OperationalError):
            mock_db_operation()

        assert call_count == 3  # Initial attempt + 2 retries

    def test_no_retry_on_non_retryable_error(self):
        """Test that non-retryable errors are not retried."""
        call_count = 0

        @retry_on_db_error(max_retries=3, initial_delay=0.01, backoff=1.5)
        def mock_db_operation():
            nonlocal call_count
            call_count += 1
            raise ValueError("Invalid parameter")

        with pytest.raises(ValueError):
            mock_db_operation()

        assert call_count == 1  # No retries for ValueError

    def test_immediate_success_no_retry(self):
        """Test that successful operations don't retry."""
        call_count = 0

        @retry_on_db_error(max_retries=3, initial_delay=0.01, backoff=1.5)
        def mock_db_operation():
            nonlocal call_count
            call_count += 1
            return "success"

        result = mock_db_operation()
        assert result == "success"
        assert call_count == 1  # No retries needed


class TestUpdateJobStatusRetry:
    """Test retry logic for update_job_status function."""

    @patch('pdr_run.database.queries.get_db_manager')
    def test_update_job_status_retries_on_connection_loss(self, mock_get_db_manager):
        """Test that update_job_status retries on connection loss."""
        # Create mock session
        mock_session = MagicMock(spec=Session)
        mock_job = Mock()
        mock_job.id = 1

        # Set up the session to fail twice then succeed
        call_count = 0

        def mock_get(*args, **kwargs):
            nonlocal call_count
            call_count += 1
            if call_count < 3:
                raise OperationalError("Lost connection to MySQL server", None, None)
            return mock_job

        mock_session.get.side_effect = mock_get

        # Mock the database manager
        mock_db = Mock()
        mock_db.get_session.return_value = mock_session
        mock_get_db_manager.return_value = mock_db

        # This should succeed after retries
        update_job_status(1, "running", session=mock_session)

        # Verify retries occurred
        assert call_count == 3


class TestGetOrCreateRetry:
    """Test retry logic for get_or_create function."""

    def test_get_or_create_retries_on_disconnection(self):
        """Test that get_or_create retries on disconnection errors."""
        mock_session = MagicMock(spec=Session)
        mock_query = Mock()

        call_count = 0

        def mock_query_filter_by(**kwargs):
            nonlocal call_count
            call_count += 1
            if call_count < 2:
                raise DisconnectionError("Connection closed by peer", None, None, None)
            return mock_query

        mock_session.query.return_value.filter_by = mock_query_filter_by
        mock_query.first.return_value = Mock(id=1)

        # Mock the model
        MockModel = Mock()
        MockModel.__name__ = "TestModel"

        result = get_or_create(mock_session, MockModel, test_field="value")

        # Verify retries occurred
        assert call_count == 2
        assert result is not None


class TestQueryFunctionsRetry:
    """Test retry logic for query functions."""

    @patch('pdr_run.database.queries.get_db_manager')
    def test_get_model_name_id_retries(self, mock_get_db_manager):
        """Test that get_model_name_id retries on connection errors."""
        mock_session = MagicMock(spec=Session)
        mock_query = Mock()

        call_count = 0

        def mock_query_filter(*args, **kwargs):
            nonlocal call_count
            call_count += 1
            if call_count < 2:
                raise OperationalError("Lost connection", None, None)
            return mock_query

        mock_session.query.return_value.filter = mock_query_filter
        mock_query.count.return_value = 1
        mock_query.first.return_value = Mock(id=42)

        # Mock database manager
        mock_db = Mock()
        mock_db.get_session.return_value = mock_session
        mock_get_db_manager.return_value = mock_db

        result = get_model_name_id("test_model", "/path/to/model")

        assert call_count == 2
        assert result == 42


if __name__ == '__main__':
    pytest.main([__file__, '-v'])
