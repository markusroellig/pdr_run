"""RCloneStorage on an S3-type remote: no mkdir, explicit delete before the
overwrite, size/MD5 verification, size-scaled timeouts, multipart sizing,
key joining, bucket handling. A fake ``rclone`` replaces subprocess.run; no
rclone binary or server is needed."""

import hashlib
import json
import subprocess
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

from pdr_run.storage.remote import RCloneStorage


def _cp(rc=0, out='', err=''):
    return SimpleNamespace(returncode=rc, stdout=out, stderr=err)


class FakeRclone:
    """Dictionary-backed S3: key -> bytes. Records every command."""

    def __init__(self):
        self.objects = {}
        self.calls = []          # (args list, timeout)
        self.fail = {}           # command -> list of results to return first
        self.corrupt_after_put = False
        self.multipart_md5 = True

    def _key(self, arg):
        return arg.split(':', 1)[1]

    def __call__(self, cmd, capture_output=True, text=True, timeout=None, **kw):
        if cmd[1] == 'version':
            return _cp()
        self.calls.append((cmd, timeout))
        name = cmd[1]
        queue = self.fail.get(name)
        if queue:
            r = queue.pop(0)
            if isinstance(r, Exception):
                raise r
            return r
        if name == 'copyto' and ':' in cmd[2] and ':' not in cmd[3]:
            open(cmd[3], 'wb').write(self.objects[self._key(cmd[2])])    # download
            return _cp()
        if name == 'copyto':
            data = open(cmd[2], 'rb').read()
            if self.corrupt_after_put:
                data = data[:-1]
            self.objects[self._key(cmd[3])] = data
            return _cp()
        if name == 'lsjson':
            k = self._key(cmd[3])
            if k not in self.objects:
                return _cp(out='[\n]\n')
            d = self.objects[k]
            return _cp(out=json.dumps([{'Path': 'x', 'Size': len(d), 'IsDir': False,
                                        'Hashes': {'MD5': hashlib.md5(d).hexdigest()}}]))
        if name == 'deletefile':
            if self._key(cmd[2]) not in self.objects:
                return _cp(4, err='object not found')
            del self.objects[self._key(cmd[2])]
            return _cp()
        if name == 'lsf':
            k = self._key(cmd[2])
            if k in self.objects:
                return _cp(out=k.rsplit('/', 1)[-1] + '\n')
            return _cp()
        if name == 'lsd':
            return _cp(out='          -1 2020-01-01 00:00:00        -1 noices\n')
        if name == 'mkdir':
            return _cp()
        raise AssertionError(f"unexpected rclone call {cmd}")

    def names(self):
        return [c[0][1] for c in self.calls]


@pytest.fixture
def fake():
    f = FakeRclone()
    with patch('subprocess.run', f), patch('time.sleep'):
        yield f


def make(remote='kosmatau:noices/grid1', **kw):
    return RCloneStorage({'base_dir': '/tmp', 'rclone_remote': remote,
                          'rclone_remote_type': 's3', **kw})


@pytest.fixture
def src(tmp_path):
    p = tmp_path / 'f.bin'
    p.write_bytes(b'abc' * 1000)
    return p


def test_s3_never_calls_mkdir_and_uses_no_check_bucket(fake, src):
    st = make()
    assert st.store_file(str(src), '/data/m/pdrgrid/f.bin') is True
    assert 'mkdir' not in fake.names()
    copy = [c for c, _ in fake.calls if c[1] == 'copyto'][0]
    assert '--s3-no-check-bucket' in copy
    assert fake.objects['noices/grid1/data/m/pdrgrid/f.bin'] == src.read_bytes()


def test_s3_overwrite_deletes_old_object_first(fake, src):
    st = make()
    fake.objects['noices/grid1/f.bin'] = b'old'
    assert st.store_file(str(src), 'f.bin') is True
    names = fake.names()
    assert names.index('deletefile') < names.index('copyto')
    assert fake.objects['noices/grid1/f.bin'] == src.read_bytes()


def test_s3_fresh_key_is_not_deleted(fake, src):
    make().store_file(str(src), 'f.bin')
    assert 'deletefile' not in fake.names()


def test_s3_size_mismatch_is_detected_and_retried(fake, src):
    st = make(rclone_max_retries=1)
    fake.corrupt_after_put = True
    assert st.store_file(str(src), 'f.bin') is False
    assert 'verification failed' in st.last_error
    assert fake.names().count('copyto') == 2        # one retry
    assert fake.names().count('deletefile') == 1    # the bad object is replaced


def test_s3_md5_mismatch_with_equal_size_fails(fake, src):
    st = make(rclone_max_retries=0)

    orig = fake.__call__

    def lsjson_wrong_md5(cmd, **kw):
        r = orig(cmd, **kw)
        if cmd[1] == 'lsjson' and r.stdout.strip() not in ('[\n]', '[]'):
            d = json.loads(r.stdout)
            d[0]['Hashes']['MD5'] = '0' * 32
            return _cp(out=json.dumps(d))
        return r
    with patch('subprocess.run', lsjson_wrong_md5):
        assert st.store_file(str(src), 'f.bin') is False
    assert 'MD5' in st.last_error


def test_s3_no_md5_available_checks_size_only(fake, src):
    st = make(rclone_max_retries=0)
    orig = fake.__call__

    def no_hash(cmd, **kw):
        r = orig(cmd, **kw)
        if cmd[1] == 'lsjson' and 'Hashes' in r.stdout:
            d = json.loads(r.stdout)
            d[0]['Hashes'] = {}
            return _cp(out=json.dumps(d))
        return r
    with patch('subprocess.run', no_hash):
        assert st.store_file(str(src), 'f.bin') is True


def test_every_call_has_hard_timeout_scaled_with_size(fake, tmp_path):
    st = make(rclone_min_rate_mb_s=2.0)
    big = tmp_path / 'big.bin'
    with open(big, 'wb') as fh:
        fh.truncate(400 * 1024 * 1024)          # sparse 400 MiB
    st.verify = False
    assert st.store_file(str(big), 'big.bin') is True
    for cmd, timeout in fake.calls:
        assert timeout is not None and timeout > 0
        assert '--contimeout' in cmd and '--timeout' in cmd
    t = [t for c, t in fake.calls if c[1] == 'copyto'][0]
    assert t == pytest.approx(300 + 200, rel=0.01)   # 300 s base + 400 MiB / 2 MiB/s
    small = [t for c, t in fake.calls if c[1] == 'lsjson'][0]
    assert small <= 120


def test_subprocess_timeout_is_retried_then_reported(fake, src):
    st = make(rclone_max_retries=2)
    fake.fail['copyto'] = [subprocess.TimeoutExpired('rclone', 5)] * 3
    assert st.store_file(str(src), 'f.bin') is False
    assert fake.names().count('copyto') == 3
    assert 'timed out' in st.last_error


def test_hanging_call_then_success(fake, src):
    st = make()
    fake.fail['copyto'] = [subprocess.TimeoutExpired('rclone', 5)]
    assert st.store_file(str(src), 'f.bin') is True


def test_multipart_chunk_size_keeps_part_count_below_limit(fake, tmp_path):
    st = make(rclone_chunk_size_mb=64, rclone_upload_cutoff_mb=256,
              rclone_upload_concurrency=3)
    st.verify = False
    big = tmp_path / 'big.bin'
    with open(big, 'wb') as fh:
        fh.truncate(1500 * 1024 * 1024)
    st.store_file(str(big), 'big.bin')
    cmd = [c for c, _ in fake.calls if c[1] == 'copyto'][0]
    assert cmd[cmd.index('--s3-chunk-size') + 1] == '64M'
    assert cmd[cmd.index('--s3-upload-cutoff') + 1] == '256M'
    assert cmd[cmd.index('--s3-upload-concurrency') + 1] == '3'
    # 1 TiB would need 1 TiB / 9000 = 117 MiB chunks, not 64 MiB
    flags = st._upload_flags(1024 ** 4)
    chunk = int(flags[flags.index('--s3-chunk-size') + 1].rstrip('M'))
    assert 1024 ** 4 / (chunk * 1024 * 1024) <= 9000


def test_permanent_errors_are_not_retried(fake, src):
    st = make()
    fake.fail['copyto'] = [_cp(1, err='AccessDenied: status code: 403')] * 4
    assert st.store_file(str(src), 'f.bin') is False
    assert fake.names().count('copyto') == 1


def test_missing_bucket_message(fake, src):
    st = make()
    fake.fail['copyto'] = [_cp(1, err='NoSuchBucket: The specified bucket does not exist')]
    assert st.store_file(str(src), 'f.bin') is False
    assert 'bucket does not exist' in st.last_error and 'admin' in st.last_error


def test_missing_local_file_is_not_retried(fake, tmp_path):
    st = make()
    assert st.store_file(str(tmp_path / 'nope'), 'f.bin') is False
    assert fake.calls == []


def test_key_longer_than_1024_bytes_is_refused(fake, src):
    st = make()
    assert st.store_file(str(src), 'a/' + 'x' * 1100) is False
    assert fake.calls == []


def test_non_object_remote_still_uses_mkdir_and_leading_slash(src):
    f = FakeRclone()
    st = RCloneStorage({'base_dir': '/t', 'rclone_remote': 'myremote',
                        'rclone_remote_type': 'sftp', 'rclone_verify': False})
    with patch('subprocess.run', f):
        assert st.store_file(str(src), 'a/b/f.bin') is True
    assert f.names() == ['mkdir', 'copyto']
    assert f.calls[0][0][2] == 'myremote:/a/b'


@pytest.mark.parametrize('remote,prefix,path,expected', [
    ('kosmatau:noices/grid1', None, '/m/pdrgrid/f', 'kosmatau:noices/grid1/m/pdrgrid/f'),
    ('kosmatau:noices/grid1/', None, 'm//pdrgrid/./f', 'kosmatau:noices/grid1/m/pdrgrid/f'),
    ('kosmatau:noices', '/home/u/runs', '/home/u/runs/m/f', 'kosmatau:noices/m/f'),
    ('kosmatau:noices', '/home/u/runs', '/home/u/runs2/m/f', 'kosmatau:noices/home/u/runs2/m/f'),
    ('kosmatau:noices', '/home/u/runs/', '/home/u/runs/m/f', 'kosmatau:noices/m/f'),
    ('kosmatau', None, '/noices/m/f', 'kosmatau:noices/m/f'),
])
def test_s3_key_joining(remote, prefix, path, expected):
    st = make(remote, remote_path_prefix=prefix)
    assert st._get_full_remote_path(path) == expected
    assert '//' not in expected.split(':', 1)[1]


def test_dotdot_is_refused():
    with pytest.raises(ValueError):
        make()._get_full_remote_path('a/../../b')


def test_file_exists_distinguishes_absent_from_failure(fake):
    st = make(rclone_max_retries=1)
    assert st.file_exists('nope') is False                       # rc 0, empty
    fake.fail['lsf'] = [_cp(3, err='directory not found')]
    assert st.file_exists('nope') is False
    assert fake.names().count('lsf') == 2                        # not retried
    fake.fail['lsf'] = [_cp(1, err='connection refused')] * 2    # persists
    assert st.file_exists('x') is False
    assert 'connection refused' in st.last_error
    assert fake.names().count('lsf') == 4                        # retried once


def test_file_exists_true(fake, src):
    st = make()
    st.store_file(str(src), 'f.bin')
    assert st.file_exists('f.bin') is True


def test_list_files_missing_dir_is_empty_without_retry(fake):
    st = make()
    fake.fail['lsf'] = [_cp(3, err='directory not found')]
    assert st.list_files('nodir') == []
    assert fake.names().count('lsf') == 1


def test_delete_file_absent_is_success(fake):
    assert make().delete_file('nope') is True


def test_bucket_helpers(fake):
    st = make('kosmatau:noices/grid1')
    assert st.bucket_of_remote() == 'noices'
    assert st.bucket_exists('noices') is True
    assert st.bucket_exists('other') is False
    assert make('kosmatau').bucket_of_remote() is None
    fake.fail['lsd'] = [_cp(1, err='Forbidden')]
    assert st.bucket_exists('noices') is None


def test_remote_type_detection_from_listremotes():
    st = RCloneStorage.__new__(RCloneStorage)
    with patch('subprocess.run', return_value=_cp(0, 'a: sftp\nkosmatau: s3\n')):
        st.__init__({'rclone_remote': 'kosmatau:noices'})
        assert st._detect_remote_type() == 's3'
        assert st._is_object_store()


# ------------------------------------------------------------- preflight probe

def _ctx(remote):
    ctx = MagicMock()
    ctx.timeout = 5
    return ctx


def _st(remote):
    return {'type': 'rclone', 'base_dir': '/tmp', 'rclone_remote': remote, 'use_mount': False,
            'remote_path_prefix': None, 'rclone_opts': {'rclone_remote_type': 's3'}}


def test_preflight_probe_writes_overwrites_reads_deletes(fake):
    from pdr_run.cli import preflight
    status, detail = preflight._storage_rclone(_ctx('x'), _st('kosmatau:noices/grid1'))
    assert status == preflight.PASS and 'overwrite' in detail
    assert fake.objects == {}                      # probe object deleted again
    assert 'mkdir' not in fake.names()
    assert fake.names().count('copyto') == 3       # write, overwrite, read-back


def test_preflight_probe_reports_missing_bucket_without_creating_it(fake):
    from pdr_run.cli import preflight
    with pytest.raises(preflight.CheckOutcome) as ei:
        preflight._storage_rclone(_ctx('x'), _st('kosmatau:nobucket/grid1'))
    assert 'missing' in str(ei.value) and 'admin' in str(ei.value)
    assert 'copyto' not in fake.names() and 'mkdir' not in fake.names()


def test_preflight_probe_warns_for_s3_remote_without_bucket(fake):
    from pdr_run.cli import preflight
    status, detail = preflight._storage_rclone(_ctx('x'), _st('kosmatau'))
    assert status == preflight.WARN and 'bucket' in detail
    assert 'copyto' not in fake.names()
