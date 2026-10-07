"""simline.bundle_outputs: the SIMLINE side files of a node are stored as ONE
archive simlinegrid/SIMLINE<model>.tar.gz; fetch_simline_outputs reads both
the archive and the old file-by-file layout."""

import io
import os
import tarfile
from unittest.mock import MagicMock

import pytest

from pdr_run.models import kosma_tau
from pdr_run.storage.base import LocalCopy
from pdr_run.storage.local import LocalStorage

MODEL = '100_40_-30_60_00'
# what the fake pipeline writes into simlineoutput/ (run_simline adds TEXTOUT_SIMLINE)
OUTPUTS = {f'jtemp_pdrstruct{MODEL}_simline_{sp}.smli': f'{sp} spectrum\n'.encode() * 50
           for sp in ('CO', '13CO', 'C+', 'O')}
OUTPUTS.update({f'cube_{i:03d}.fits': bytes([i % 256]) * 2880 for i in range(20)})

_FAKE_DRIVER = '''\
import json, sys
from pathlib import Path
out = Path('simlineoutput')
out.mkdir(exist_ok=True)
for name, data in json.loads(Path(sys.argv[0]).with_name('outputs.json').read_text()).items():
    (out / name).write_bytes(data.encode('latin-1'))
print('simline done')
'''


class RecordingStorage(LocalStorage):
    """LocalStorage that records every store_file call."""

    def __init__(self, base_dir):
        super().__init__(base_dir)
        self.stored = []
        self.fail_suffix = None

    def store_file(self, local_path, remote_path):
        self.stored.append(remote_path)
        if self.fail_suffix and remote_path.endswith(self.fail_suffix):
            return False
        return super().store_file(local_path, remote_path)


@pytest.fixture
def env(tmp_path, monkeypatch):
    import json
    base = tmp_path / 'simline'
    (base / 'python').mkdir(parents=True)
    (base / 'bin').mkdir()
    (base / 'bin' / 'simline').write_text('#!/bin/sh\n')
    (base / 'molecules').mkdir()
    (base / 'obs.template').write_text('beam\n')
    (base / 'python' / 'run_simline.py').write_text(_FAKE_DRIVER)
    (base / 'python' / 'outputs.json').write_text(
        json.dumps({k: v.decode('latin-1') for k, v in OUTPUTS.items()}))
    (base / 'python' / 'simline_config.json').write_text('{"simline_dir": "x"}\n')

    store_root = tmp_path / 'store'
    storage = RecordingStorage(str(store_root))
    storage.local_copy = LocalCopy(str(tmp_path / 'mirror'), base_dir=str(store_root))
    monkeypatch.setattr('pdr_run.storage.base.get_storage_backend',
                        lambda config=None: storage)

    job = MagicMock()
    job.model_job_name = MODEL
    job.model_name.model_path = str(store_root / 'grid1')
    session = MagicMock()
    session.get.return_value = job

    workdir = tmp_path / 'work'
    (workdir / 'pdroutput').mkdir(parents=True)
    (workdir / 'pdroutput' / 'pdrstruct_s.hdf5').write_bytes(b'hdf5' * 100)

    def run(bundle, patterns=('TEXTOUT*',)):
        cfg = {'simline': {'simline_dir': str(base)},
               'storage': {'compress_files': list(patterns)}}
        if bundle is not None:
            cfg['simline']['bundle_outputs'] = bundle
        return kosma_tau.run_simline(1, tmp_dir=str(workdir), config=cfg, session=session)

    grid_dir = store_root / 'grid1' / 'simlinegrid'
    return dict(run=run, storage=storage, grid_dir=grid_dir, workdir=workdir,
                mirror=tmp_path / 'mirror' / 'grid1' / 'simlinegrid', tmp=tmp_path)


def _expected_names():
    return sorted([f'SIMLINE{MODEL}.{n}' for n in OUTPUTS] + [f'SIMLINE{MODEL}.TEXTOUT_SIMLINE'])


def test_bundle_one_store_call_for_all_side_files(env):
    assert env['run'](True) is True
    st = env['storage'].stored
    bundle = os.path.join('simlinegrid', f'SIMLINE{MODEL}.tar.gz')
    hdf = os.path.join('simlinegrid', f'pdrstruct{MODEL}_simline.hdf5')
    assert len(st) == 2 and st[0].endswith(bundle) and st[1].endswith(hdf)
    assert sorted(os.listdir(env['grid_dir'])) == sorted(
        [f'SIMLINE{MODEL}.tar.gz', f'pdrstruct{MODEL}_simline.hdf5'])
    with tarfile.open(env['grid_dir'] / f'SIMLINE{MODEL}.tar.gz') as tar:
        members = tar.getmembers()
        assert sorted(m.name for m in members) == _expected_names()
        assert all(m.isfile() for m in members)
        for name, data in OUTPUTS.items():
            assert tar.extractfile(f'SIMLINE{MODEL}.{name}').read() == data
        # TEXTOUT is inside the (gzipped) archive, not compressed again
        assert b'simline done' in tar.extractfile(f'SIMLINE{MODEL}.TEXTOUT_SIMLINE').read()
    # the temporary archive is gone from the work directory
    assert not list(env['workdir'].glob('*.tar.gz'))


def test_bundle_local_copy_holds_archive_as_stored(env):
    assert env['run'](True) is True
    stored = env['grid_dir'] / f'SIMLINE{MODEL}.tar.gz'
    mirrored = env['mirror'] / f'SIMLINE{MODEL}.tar.gz'
    assert mirrored.read_bytes() == stored.read_bytes()
    assert sorted(os.listdir(env['mirror'])) == sorted(os.listdir(env['grid_dir']))


@pytest.mark.parametrize('flag', [None, False])
def test_default_keeps_file_by_file_layout(env, flag):
    assert env['run'](flag) is True
    names = sorted(os.listdir(env['grid_dir']))
    assert f'SIMLINE{MODEL}.tar.gz' not in names
    assert f'SIMLINE{MODEL}.TEXTOUT_SIMLINE.gz' in names        # compress_files
    assert len(env['storage'].stored) == len(OUTPUTS) + 2      # + TEXTOUT + HDF5


def test_bundle_store_failure_returns_false_and_cleans_up(env):
    env['storage'].fail_suffix = '.tar.gz'
    assert env['run'](True) is False
    assert not list(env['workdir'].glob('*.tar.gz'))
    # the HDF5 is still attempted (same as the per-file path)
    assert env['storage'].stored[-1].endswith(f'pdrstruct{MODEL}_simline.hdf5')


def test_fetch_reads_both_layouts_identically(env):
    model_path = str(env['tmp'] / 'store' / 'grid1')
    env['run'](False)
    old = env['tmp'] / 'old'
    names_old = kosma_tau.fetch_simline_outputs(env['storage'], model_path, MODEL, str(old))

    # same node re-stored in the bundled layout under another model path
    import shutil
    shutil.rmtree(env['grid_dir'])
    env['run'](True)
    new = env['tmp'] / 'new'
    names_new = kosma_tau.fetch_simline_outputs(env['storage'], model_path, MODEL, str(new))

    assert names_old == names_new == _expected_names()
    for n in names_new:
        assert (old / n).read_bytes() == (new / n).read_bytes()
    assert sorted(os.listdir(new)) == names_new          # no .part left behind


def test_fetch_old_layout_ignores_other_models(env):
    model_path = str(env['tmp'] / 'store' / 'grid1')
    env['run'](False)
    other = env['grid_dir'] / f'SIMLINE{MODEL}1.jtemp_x.smli'   # prefix of another node
    other.write_text('other node')
    names = kosma_tau.fetch_simline_outputs(env['storage'], model_path, MODEL,
                                            str(env['tmp'] / 'out'))
    assert names == _expected_names()


def test_fetch_rejects_member_with_path(env, tmp_path):
    grid = env['grid_dir']
    grid.mkdir(parents=True)
    with tarfile.open(grid / f'SIMLINE{MODEL}.tar.gz', 'w:gz') as tar:
        info = tarfile.TarInfo(f'../SIMLINE{MODEL}.evil')
        data = b'x'
        info.size = len(data)
        tar.addfile(info, io.BytesIO(data))
    with pytest.raises(ValueError, match='unexpected member'):
        kosma_tau.fetch_simline_outputs(env['storage'], str(tmp_path / 'store' / 'grid1'),
                                        MODEL, str(tmp_path / 'out'))
    assert not (tmp_path / f'SIMLINE{MODEL}.evil').exists()
    assert not list((tmp_path / 'out').glob('*.part'))


def test_runner_accepts_bundle_key():
    from pdr_run.cli.runner import VALID_CONFIG_STRUCTURE
    assert 'bundle_outputs' in VALID_CONFIG_STRUCTURE['simline']
