"""Test for remote_path_prefix functionality in RCloneStorage.

This test verifies that the remote_path_prefix configuration option
properly strips the specified prefix from remote paths, addressing
GitHub issue #7.
"""

import os
import tempfile
import pytest
from unittest.mock import patch
from pdr_run.storage.remote import RCloneStorage


def test_remote_path_prefix_stripping():
    """Test that remote_path_prefix correctly strips the prefix from remote paths."""

    # Create a mock rclone configuration
    config = {
        'base_dir': '/tmp/test',
        'rclone_remote': 'test_remote:/remote/base',
        'use_mount': False,
        'remote_path_prefix': '/home/ossk/kosma-tau/kosma-tau/rundir'
    }

    # Create RCloneStorage instance
    storage = RCloneStorage(config)

    # Test cases
    test_cases = [
        {
            'input': '/home/ossk/kosma-tau/kosma-tau/rundir/lowUV-C18O/oniongrid/file.txt',
            'expected': 'test_remote:/remote/base/lowUV-C18O/oniongrid/file.txt',
            'description': 'Full path with prefix should be stripped'
        },
        {
            'input': 'lowUV-C18O/oniongrid/file.txt',
            'expected': 'test_remote:/remote/base/lowUV-C18O/oniongrid/file.txt',
            'description': 'Relative path without prefix should work'
        },
        {
            'input': '/other/path/file.txt',
            'expected': 'test_remote:/remote/base/other/path/file.txt',
            'description': 'Path not starting with prefix should be left as-is'
        }
    ]

    for case in test_cases:
        result = storage._get_full_remote_path(case['input'])
        # Normalize slashes for comparison
        result = result.replace('\\', '/')
        expected = case['expected'].replace('\\', '/')

        assert result == expected, (
            f"{case['description']}\n"
            f"Input: {case['input']}\n"
            f"Expected: {expected}\n"
            f"Got: {result}"
        )

    print("✓ All remote_path_prefix tests passed!")


def test_remote_path_prefix_none():
    """Test behavior when remote_path_prefix is None."""

    config = {
        'base_dir': '/tmp/test',
        'rclone_remote': 'test_remote:/remote/base',
        'use_mount': False,
        'remote_path_prefix': None
    }

    storage = RCloneStorage(config)

    # When prefix is None, paths should be used as-is
    test_path = '/full/path/to/file.txt'
    result = storage._get_full_remote_path(test_path)
    expected = 'test_remote:/remote/base/full/path/to/file.txt'

    result = result.replace('\\', '/')
    expected = expected.replace('\\', '/')

    assert result == expected, (
        f"When remote_path_prefix is None, path should be used as-is\n"
        f"Expected: {expected}\n"
        f"Got: {result}"
    )

    print("✓ remote_path_prefix=None test passed!")


def test_remote_path_prefix_from_config():
    """Test that remote_path_prefix is properly loaded from config."""
    from pdr_run.storage.base import get_storage_backend

    # Test with config file
    config = {
        'storage': {
            'type': 'rclone',
            'base_dir': '/tmp/test',
            'rclone_remote': 'test_remote:/remote/base',
            'remote_path_prefix': '/home/user/prefix'
        }
    }

    storage = get_storage_backend(config)

    assert isinstance(storage, RCloneStorage), "Should create RCloneStorage instance"
    assert storage.remote_path_prefix == '/home/user/prefix', (
        f"remote_path_prefix should be loaded from config\n"
        f"Expected: /home/user/prefix\n"
        f"Got: {storage.remote_path_prefix}"
    )

    print("✓ Config loading test passed!")


def test_remote_path_prefix_from_env():
    """Test that remote_path_prefix can be set via environment variable."""
    from pdr_run.storage.base import get_storage_backend

    # Set environment variable
    os.environ['PDR_STORAGE_TYPE'] = 'rclone'
    os.environ['PDR_STORAGE_REMOTE_PATH_PREFIX'] = '/env/prefix/path'
    os.environ['PDR_STORAGE_RCLONE_REMOTE'] = 'test_remote:/base'

    try:
        storage = get_storage_backend()

        assert isinstance(storage, RCloneStorage), "Should create RCloneStorage instance"
        assert storage.remote_path_prefix == '/env/prefix/path', (
            f"remote_path_prefix should be loaded from environment\n"
            f"Expected: /env/prefix/path\n"
            f"Got: {storage.remote_path_prefix}"
        )

        print("✓ Environment variable test passed!")
    finally:
        # Clean up environment
        os.environ.pop('PDR_STORAGE_TYPE', None)
        os.environ.pop('PDR_STORAGE_REMOTE_PATH_PREFIX', None)
        os.environ.pop('PDR_STORAGE_RCLONE_REMOTE', None)


def _issue7_storage(remote_type):
    """RCloneStorage with the issue-7 config and a fixed remote type, so that
    the result does not depend on the host's rclone configuration."""
    config = {
        'base_dir': '/tmp/test',
        'rclone_remote': 'kosmatau:',
        'rclone_remote_type': remote_type,      # no `rclone listremotes` lookup
        'use_mount': False,
        'remote_path_prefix': '/home/ossk/kosma-tau/kosma-tau/rundir'
    }
    with patch('pdr_run.storage.remote.subprocess.run'):   # `rclone version` check
        return RCloneStorage(config)


ISSUE7_INPUT = ('/home/ossk/kosma-tau/kosma-tau/rundir/lowUV-C18O/oniongrid/'
                'ONION100_3.0_1.0_1.0_00.jerg_CO.smli')


def test_github_issue_7_example():
    """GitHub issue #7: the prefix is stripped (ordinary remote: absolute path)."""
    result = _issue7_storage('sftp')._get_full_remote_path(ISSUE7_INPUT)
    assert result == 'kosmatau:/lowUV-C18O/oniongrid/ONION100_3.0_1.0_1.0_00.jerg_CO.smli'


def test_github_issue_7_example_s3_has_no_leading_slash():
    """On an S3 remote the first component is the bucket and a leading slash is
    dropped (S3 hardening); the prefix is stripped just the same."""
    result = _issue7_storage('s3')._get_full_remote_path(ISSUE7_INPUT)
    assert result == 'kosmatau:lowUV-C18O/oniongrid/ONION100_3.0_1.0_1.0_00.jerg_CO.smli'


if __name__ == '__main__':
    print("Running remote_path_prefix tests...\n")

    test_remote_path_prefix_stripping()
    test_remote_path_prefix_none()
    test_remote_path_prefix_from_config()
    test_remote_path_prefix_from_env()
    test_github_issue_7_example()

    print("\n✅ All tests passed!")
