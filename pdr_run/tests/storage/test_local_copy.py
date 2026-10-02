"""storage.use_local_copy: a verified upload to a remote backend is mirrored
under a local root with the same relative key layout."""

import gzip
import os
from unittest.mock import MagicMock, patch

import pytest

from pdr_run.models import kosma_tau
from pdr_run.storage.base import LocalCopy, get_storage_backend, resolve_local_copy

PREFIX = '/grid1_store'


class FakeRemote:
    """Stands in for RCloneStorage/SFTPStorage: remembers uploads."""

    def __init__(self, ok=True, local_copy=None):
        self.ok = ok
        self.uploads = {}
        self.local_copy = local_copy

    def store_file(self, local_path, remote_path):
        if not self.ok:
            return False
        with open(local_path, 'rb') as fh:
            self.uploads[remote_path] = fh.read()
        return True


def _src(tmp_path, name='a.bin', data=b'payload'):
    p = tmp_path / name
    p.write_bytes(data)
    return str(p)


def test_copy_lands_at_same_relative_key(tmp_path):
    root = tmp_path / 'mirror'
    lc = LocalCopy(str(root), prefix=PREFIX, base_dir=PREFIX)
    remote = FakeRemote(local_copy=lc)
    key = f'{PREFIX}/grid1_tier0/pdrgrid/pdrstruct100_30_0_10_00.hdf5'
    assert kosma_tau._store(remote, _src(tmp_path), key) is True
    dest = root / 'grid1_tier0' / 'pdrgrid' / 'pdrstruct100_30_0_10_00.hdf5'
    assert dest.read_bytes() == b'payload'
    assert not list(root.rglob('*.part'))


def test_default_root_is_base_dir_and_original_path_when_prefix_equals_base_dir(tmp_path):
    base = tmp_path / 'grid1_store'
    sc = {'type': 'rclone', 'base_dir': str(base), 'remote_path_prefix': str(base),
          'use_local_copy': True}
    lc = resolve_local_copy(sc, 'rclone')
    full = str(base / 'm' / 'pdrgrid' / 'x.hdf5')
    assert lc.root == str(base)
    assert lc.local_path(full) == full          # lands where the model_path file is


def test_prefix_missing_falls_back_to_base_dir_strip(tmp_path):
    lc = LocalCopy('/mirror', prefix=None, base_dir='/store')
    assert lc.local_path('/store/m/pdrgrid/x') == '/mirror/m/pdrgrid/x'


def test_local_copy_dir_overrides_base_dir():
    lc = resolve_local_copy({'base_dir': '/b', 'remote_path_prefix': '/b',
                             'local_copy_dir': '/other'}, 'rclone')
    assert lc.root == '/other'


def test_sftp_needs_explicit_local_copy_dir():
    assert resolve_local_copy({'base_dir': '/server/path'}, 'sftp') is None
    assert resolve_local_copy({'base_dir': '/server/path', 'local_copy_dir': '/l'},
                              'sftp').root == '/l'


def test_disabled_and_local_storage_give_none():
    assert resolve_local_copy({'base_dir': '/b', 'use_local_copy': False}, 'rclone') is None
    assert resolve_local_copy({'base_dir': '/b'}, 'local') is None


def test_gz_file_is_copied_exactly_as_stored(tmp_path):
    root = tmp_path / 'mirror'
    remote = FakeRemote(local_copy=LocalCopy(str(root), PREFIX, PREFIX))
    patterns = ['TEXTOUT*']
    src = _src(tmp_path, 'TEXTOUT_x', b'screen log ' * 100)
    assert kosma_tau._store_maybe_gz(remote, src, f'{PREFIX}/m/pdrgrid/TEXTOUT_x', patterns)
    dest = root / 'm' / 'pdrgrid' / 'TEXTOUT_x.gz'
    assert dest.read_bytes() == remote.uploads[f'{PREFIX}/m/pdrgrid/TEXTOUT_x.gz']
    assert gzip.decompress(dest.read_bytes()) == b'screen log ' * 100
    assert not (root / 'm' / 'pdrgrid' / 'TEXTOUT_x').exists()


def test_failed_upload_keeps_no_copy(tmp_path):
    root = tmp_path / 'mirror'
    remote = FakeRemote(ok=False, local_copy=LocalCopy(str(root), PREFIX, PREFIX))
    assert kosma_tau._store(remote, _src(tmp_path), f'{PREFIX}/m/x') is False
    assert not root.exists()


def test_local_copy_failure_is_warning_and_does_not_fail_job(tmp_path, caplog):
    blocker = tmp_path / 'blocker'
    blocker.write_text('a file where the root directory should be')
    remote = FakeRemote(local_copy=LocalCopy(str(blocker / 'root'), PREFIX, PREFIX))
    with caplog.at_level('WARNING', logger='dev'):
        assert kosma_tau._store(remote, _src(tmp_path), f'{PREFIX}/m/x') is True
    assert f'{PREFIX}/m/x' in remote.uploads
    assert any(r.levelname == 'WARNING' and 'Local copy' in r.getMessage() for r in caplog.records)


def test_part_file_removed_when_copy_is_interrupted(tmp_path):
    root = tmp_path / 'mirror'
    lc = LocalCopy(str(root), PREFIX, PREFIX)
    with patch('pdr_run.storage.base.shutil.copyfile', side_effect=OSError('disk full')):
        assert lc.store(_src(tmp_path), f'{PREFIX}/m/x') is False
    assert not list(root.rglob('*.part'))
    assert not (root / 'm' / 'x').exists()


def test_rerun_replaces_the_local_copy(tmp_path):
    root = tmp_path / 'mirror'
    remote = FakeRemote(local_copy=LocalCopy(str(root), PREFIX, PREFIX))
    key = f'{PREFIX}/m/pdrgrid/pdrstruct_a.hdf5'
    kosma_tau._store(remote, _src(tmp_path, 'a', b'old'), key)
    kosma_tau._store(remote, _src(tmp_path, 'b', b'new result'), key)
    assert (root / 'm' / 'pdrgrid' / 'pdrstruct_a.hdf5').read_bytes() == b'new result'


def test_dotdot_key_is_refused_without_failing(tmp_path):
    lc = LocalCopy(str(tmp_path / 'mirror'), PREFIX, PREFIX)
    assert lc.store(_src(tmp_path), f'{PREFIX}/../../etc/x') is False
    assert not (tmp_path / 'etc').exists()


def test_store_without_local_copy_attribute_and_with_mock_storage(tmp_path):
    storage = MagicMock()                   # MagicMock().local_copy is a MagicMock, not a LocalCopy
    storage.store_file.return_value = True
    assert kosma_tau._store(storage, _src(tmp_path), 'k') is True


def test_get_storage_backend_attaches_local_copy(tmp_path):
    cfg = {'storage': {'type': 'rclone', 'rclone_remote': 'r:b', 'base_dir': str(tmp_path),
                       'remote_path_prefix': str(tmp_path), 'use_local_copy': True}}
    with patch('pdr_run.storage.remote.subprocess.run'):
        backend = get_storage_backend(cfg)
    assert isinstance(backend.local_copy, LocalCopy)
    assert backend.local_copy.root == str(tmp_path)
    cfg['storage']['use_local_copy'] = False
    with patch('pdr_run.storage.remote.subprocess.run'):
        assert get_storage_backend(cfg).local_copy is None
