"""Tests for bounded retry-with-backoff on storage transfers (SFTP/rclone).

All network I/O is mocked (subprocess.run / paramiko.SSHClient) - no real
rclone binary or SSH server is used or required.
"""

import os
import subprocess
from unittest.mock import MagicMock, patch

import paramiko
import pytest

from pdr_run.storage.remote import RCloneStorage, SFTPStorage
from pdr_run.utils.retry import retry_with_backoff


# ---------------------------------------------------------------------------
# Generic retry_with_backoff decorator
# ---------------------------------------------------------------------------

def test_retry_with_backoff_retries_then_succeeds():
    calls = {'n': 0}

    @retry_with_backoff(max_retries=3, initial_delay=0.001, backoff=1.0,
                         exceptions=(RuntimeError,))
    def flaky():
        calls['n'] += 1
        if calls['n'] < 3:
            raise RuntimeError("transient")
        return "ok"

    assert flaky() == "ok"
    assert calls['n'] == 3


def test_retry_with_backoff_raises_after_exhausting_retries():
    calls = {'n': 0}

    @retry_with_backoff(max_retries=2, initial_delay=0.001, backoff=1.0,
                         exceptions=(RuntimeError,))
    def always_fails():
        calls['n'] += 1
        raise RuntimeError("permanent")

    with pytest.raises(RuntimeError):
        always_fails()
    # 1 initial attempt + 2 retries = 3 calls total.
    assert calls['n'] == 3


def test_retry_with_backoff_does_not_retry_unlisted_exceptions():
    calls = {'n': 0}

    @retry_with_backoff(max_retries=3, initial_delay=0.001, backoff=1.0,
                         exceptions=(RuntimeError,))
    def wrong_error():
        calls['n'] += 1
        raise ValueError("not retryable")

    with pytest.raises(ValueError):
        wrong_error()
    assert calls['n'] == 1


# ---------------------------------------------------------------------------
# RCloneStorage
# ---------------------------------------------------------------------------

@pytest.fixture
def rclone_storage():
    with patch('subprocess.run') as mock_run:
        mock_run.return_value = MagicMock(returncode=0, stdout='', stderr='')
        storage = RCloneStorage({'base_dir': '/tmp', 'rclone_remote': 'testremote',
                                 'rclone_remote_type': 'sftp', 'rclone_verify': False})
    storage.logger = MagicMock()
    return storage


def test_rclone_store_file_retries_on_transient_failure_then_succeeds(rclone_storage, tmp_path):
    local_file = tmp_path / "model.hdf5"
    local_file.write_text("data")

    fail_result = MagicMock(returncode=1, stdout='', stderr='rclone: connection reset')
    ok_result = MagicMock(returncode=0, stdout='', stderr='')

    with patch('subprocess.run', side_effect=[
        MagicMock(returncode=0),  # mkdir
        fail_result,              # copyto attempt 1 -> fails
        MagicMock(returncode=0),  # mkdir (retry)
        ok_result,                # copyto attempt 2 -> succeeds
    ]) as mock_run:
        with patch('time.sleep'):
            result = rclone_storage.store_file(str(local_file), 'grid/model.hdf5')

    assert result is True
    assert mock_run.call_count == 4


def test_rclone_store_file_gives_up_after_bounded_retries(rclone_storage, tmp_path):
    local_file = tmp_path / "model.hdf5"
    local_file.write_text("data")

    fail_result = MagicMock(returncode=1, stdout='', stderr='rclone: connection reset')

    with patch('subprocess.run', return_value=fail_result) as mock_run:
        with patch('time.sleep'):
            result = rclone_storage.store_file(str(local_file), 'grid/model.hdf5')

    assert result is False
    # 4 attempts (1 + 3 retries); the (failing) mkdir stops each attempt = 4 calls.
    assert mock_run.call_count == 4


def test_rclone_file_exists_does_not_retry_ordinary_not_found(rclone_storage):
    """The common case - a node that has not been computed yet - must not
    pay retry latency; only stderr that looks like a transport error is
    retried (rclone exit 3 / 'directory not found')."""
    not_found = MagicMock(returncode=3, stdout='', stderr='directory not found')

    with patch('subprocess.run', return_value=not_found) as mock_run:
        with patch('time.sleep') as mock_sleep:
            result = rclone_storage.file_exists('grid/does_not_exist.hdf5')

    assert result is False
    assert mock_run.call_count == 1
    mock_sleep.assert_not_called()


def test_rclone_file_exists_retries_transport_error(rclone_storage):
    timeout_result = MagicMock(returncode=1, stdout='', stderr='rclone: connection timed out')
    ok_result = MagicMock(returncode=0, stdout='model.hdf5\n', stderr='')

    with patch('subprocess.run', side_effect=[timeout_result, ok_result]) as mock_run:
        with patch('time.sleep'):
            result = rclone_storage.file_exists('grid/model.hdf5')

    assert result is True
    assert mock_run.call_count == 2


def test_rclone_file_exists_true_when_file_present(rclone_storage):
    ok_result = MagicMock(returncode=0, stdout='model.hdf5\n', stderr='')
    with patch('subprocess.run', return_value=ok_result):
        assert rclone_storage.file_exists('grid/model.hdf5') is True


# ---------------------------------------------------------------------------
# SFTPStorage
# ---------------------------------------------------------------------------

@pytest.fixture
def sftp_storage():
    with patch.object(SFTPStorage, '_test_connection', return_value=None):
        storage = SFTPStorage('example.org', 'user', 'pw', '/remote/base')
    return storage


def test_sftp_store_file_retries_on_ssh_exception_then_succeeds(sftp_storage, tmp_path):
    local_file = tmp_path / "model.hdf5"
    local_file.write_text("data")

    good_client = MagicMock()
    good_sftp = MagicMock()
    good_sftp.stat.return_value = MagicMock(st_size=len("data"))
    good_client.open_sftp.return_value = good_sftp

    call_count = {'n': 0}

    def connect_side_effect(*args, **kwargs):
        call_count['n'] += 1
        if call_count['n'] == 1:
            raise paramiko.SSHException("connection dropped")
        return None

    with patch('paramiko.SSHClient') as mock_ssh_cls:
        bad_client = MagicMock()
        bad_client.connect.side_effect = paramiko.SSHException("connection dropped")
        mock_ssh_cls.side_effect = [bad_client, good_client]

        with patch('time.sleep'):
            result = sftp_storage.store_file(str(local_file), 'grid/model.hdf5')

    assert result is True
    good_client.open_sftp.assert_called_once()


def test_sftp_store_file_gives_up_after_bounded_retries(sftp_storage, tmp_path):
    local_file = tmp_path / "model.hdf5"
    local_file.write_text("data")

    with patch('paramiko.SSHClient') as mock_ssh_cls:
        bad_client = MagicMock()
        bad_client.connect.side_effect = paramiko.SSHException("connection dropped")
        mock_ssh_cls.return_value = bad_client

        with patch('time.sleep'):
            result = sftp_storage.store_file(str(local_file), 'grid/model.hdf5')

    assert result is False
    # 1 initial attempt + 3 retries = 4 connection attempts.
    assert mock_ssh_cls.call_count == 4


def test_sftp_store_file_no_retry_for_missing_local_file(sftp_storage, tmp_path):
    missing = tmp_path / "does_not_exist.hdf5"
    with patch('paramiko.SSHClient') as mock_ssh_cls:
        with pytest.raises(FileNotFoundError):
            sftp_storage.store_file(str(missing), 'grid/model.hdf5')
    # Never even attempted a connection - local precondition checked first.
    mock_ssh_cls.assert_not_called()


def test_sftp_retrieve_missing_remote_file_is_not_retried(sftp_storage, tmp_path):
    """FileNotFoundError is an OSError but permanent: exactly one attempt."""
    client = MagicMock()
    client.open_sftp.return_value.get.side_effect = FileNotFoundError("no such file")
    with patch('paramiko.SSHClient', return_value=client) as mock_ssh_cls:
        with patch('time.sleep') as mock_sleep:
            with pytest.raises(FileNotFoundError):
                sftp_storage.retrieve_file('grid/missing.hdf5', str(tmp_path / 'x'))
    assert mock_ssh_cls.call_count == 1
    mock_sleep.assert_not_called()


def test_sftp_other_oserror_is_still_retried(sftp_storage, tmp_path):
    client = MagicMock()
    client.open_sftp.return_value.get.side_effect = OSError("socket closed")
    with patch('paramiko.SSHClient', return_value=client) as mock_ssh_cls:
        with patch('time.sleep'):
            with pytest.raises(OSError):
                sftp_storage.retrieve_file('grid/x.hdf5', str(tmp_path / 'x'))
    assert mock_ssh_cls.call_count == 4


def test_retry_giveup_exceptions_are_raised_immediately():
    calls = {'n': 0}

    @retry_with_backoff(max_retries=3, initial_delay=0.001, backoff=1.0,
                         exceptions=(OSError,), giveup=(FileNotFoundError,))
    def f():
        calls['n'] += 1
        raise FileNotFoundError("gone")

    with pytest.raises(FileNotFoundError):
        f()
    assert calls['n'] == 1


def test_sftp_store_writes_part_file_then_renames(sftp_storage, tmp_path):
    local_file = tmp_path / "model.hdf5"
    local_file.write_text("data")
    client = MagicMock()
    sftp = client.open_sftp.return_value
    sftp.stat.return_value = MagicMock(st_size=4)
    with patch('paramiko.SSHClient', return_value=client):
        assert sftp_storage.store_file(str(local_file), 'grid/model.hdf5') is True
    target = '/remote/base/grid/model.hdf5'
    sftp.put.assert_called_once_with(str(local_file), target + '.part')
    sftp.posix_rename.assert_called_once_with(target + '.part', target)


def test_local_store_replaces_existing_file_atomically(tmp_path):
    from pdr_run.storage.local import LocalStorage
    store = LocalStorage(str(tmp_path / 'store'))
    src = tmp_path / 'new.txt'
    src.write_text('new')
    store.store_file(str(src), 'a/b.txt')
    (tmp_path / 'store' / 'a' / 'b.txt').write_text('old')
    store.store_file(str(src), 'a/b.txt')
    assert (tmp_path / 'store' / 'a' / 'b.txt').read_text() == 'new'
    assert sorted(os.listdir(tmp_path / 'store' / 'a')) == ['b.txt']   # no .part left
