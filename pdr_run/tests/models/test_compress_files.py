"""Whole-file gzip compression of stored result files (storage.compress_files)."""

import glob
import gzip
import hashlib
import os
from unittest.mock import MagicMock, patch

import pytest

from pdr_run.database.models import HDFFile
from pdr_run.models import kosma_tau
from pdr_run.storage.local import LocalStorage

CHEM = b'chemistry ' * 5000
CHK = b'chemchk line\n' * 5000
GRID_PATTERNS = ['pdrchem*.hdf5', 'chemchk*.out']


@pytest.fixture
def setup(tmp_path, monkeypatch, make_job, db_session):
    """Local storage under tmp_path/store, run output under tmp_path/run."""
    run = tmp_path / 'run'
    (run / 'pdroutput').mkdir(parents=True)
    for name, data in (('pdrout.hdf', b'hdf4'), ('pdrstruct_s.hdf5', b'struct' * 100),
                       ('pdrchem_c.hdf5', CHEM), ('chemchk.out', CHK),
                       ('TEXTOUT', b'log'), ('CTRL_IND', b'ci')):
        (run / 'pdroutput' / name).write_bytes(data)
    monkeypatch.chdir(run)
    storage = LocalStorage(str(tmp_path / 'store'))
    monkeypatch.setattr('pdr_run.storage.base.get_storage_backend',
                        lambda config=None: storage)
    job = make_job(status='finished')
    job.model_name.model_path = str(tmp_path / 'store' / 'm')
    db_session.commit()
    grid = tmp_path / 'store' / 'm' / 'pdrgrid'
    return dict(run=run, storage=storage, job=job, grid=grid, session=db_session)


def _copy(setup, patterns):
    cfg = {'storage': {'compress_files': patterns}} if patterns is not None else {}
    return kosma_tau.copy_pdroutput(setup['job'].id, config=cfg,
                                    session=setup['session'], model_status='finished')


def _hdf(setup):
    return setup['session'].query(HDFFile).one()


def test_default_stores_uncompressed(setup):
    assert _copy(setup, None) is True
    assert (setup['grid'] / 'pdrchemj001.hdf5').read_bytes() == CHEM
    assert (setup['grid'] / 'chemchkj001.out').read_bytes() == CHK
    assert not glob.glob(str(setup['grid'] / '*.gz'))
    assert _hdf(setup).file_name_hdf5_c == 'pdrchemj001.hdf5'


def test_compress_stores_gz_with_checksum_of_stored_file(setup):
    assert _copy(setup, GRID_PATTERNS) is True
    grid = setup['grid']
    gz = grid / 'pdrchemj001.hdf5.gz'
    assert gz.is_file() and not (grid / 'pdrchemj001.hdf5').exists()
    assert gzip.decompress(gz.read_bytes()) == CHEM
    assert gzip.decompress((grid / 'chemchkj001.out.gz').read_bytes()) == CHK
    assert gz.stat().st_size < len(CHEM) / 10
    row = _hdf(setup)
    assert row.file_name_hdf5_c == 'pdrchemj001.hdf5.gz'
    assert row.full_path_hdf5_c.endswith('pdrgrid/pdrchemj001.hdf5.gz')
    assert row.sha256_sum_hdf5_c == hashlib.sha256(gz.read_bytes()).hexdigest()
    assert row.file_size_hdf5_c == gz.stat().st_size
    job = setup['job']
    assert job.output_hdf5_chem_file.endswith('pdrchemj001.hdf5.gz')
    assert job.output_chemchk_file.endswith('chemchkj001.out.gz')
    # pdrstruct, even if matched by a pattern, is never compressed
    assert (grid / 'pdrstructj001.hdf5').is_file()
    assert job.output_hdf5_struct_file.endswith('pdrstructj001.hdf5')


def test_local_name_pattern_also_matches(setup):
    _copy(setup, ['pdrchem_c.hdf5'])
    assert (setup['grid'] / 'pdrchemj001.hdf5.gz').is_file()
    assert (setup['grid'] / 'chemchkj001.out').is_file()


def test_gzip_is_reproducible_and_leaves_no_temp_file(setup):
    _copy(setup, GRID_PATTERNS)
    first = (setup['grid'] / 'pdrchemj001.hdf5.gz').read_bytes()
    _copy(setup, GRID_PATTERNS)
    assert (setup['grid'] / 'pdrchemj001.hdf5.gz').read_bytes() == first
    assert not glob.glob(str(setup['run'] / 'pdroutput' / '*.gz'))
    assert not glob.glob(str(setup['grid'] / '*.part'))


def test_temp_files_removed_on_storage_failure(setup):
    real = setup['storage'].store_file

    def failing(src, dst):
        if src.endswith('.gz'):
            raise OSError('disk full')
        return real(src, dst)

    setup['storage'].store_file = failing
    assert _copy(setup, GRID_PATTERNS) is False
    assert not glob.glob(str(setup['run'] / 'pdroutput' / '*.gz'))
    assert not (setup['grid'] / 'pdrchemj001.hdf5.gz').exists()


def test_compression_error_is_a_storage_failure_and_cleans_up(setup):
    with patch.object(kosma_tau.gzip_file.__globals__['shutil'], 'copyfileobj',
                      side_effect=OSError('no space')):
        assert _copy(setup, GRID_PATTERNS) is False
    assert not glob.glob(str(setup['run'] / 'pdroutput' / '*.gz'))


def test_rerun_replaces_gz_atomically(setup):
    _copy(setup, GRID_PATTERNS)
    gz = setup['grid'] / 'pdrchemj001.hdf5.gz'
    old = gz.read_bytes()
    # the new store fails half way: the old .gz stays intact, no .part left
    (setup['run'] / 'pdroutput' / 'pdrchem_c.hdf5').write_bytes(b'new' * 1000)
    real = setup['storage'].store_file

    def interrupted(src, dst):
        if src.endswith('pdrchem_c.hdf5.gz'):
            with open(dst + '.part', 'wb') as f:
                f.write(b'half')
            raise OSError('connection lost')
        return real(src, dst)

    setup['storage'].store_file = interrupted
    assert _copy(setup, GRID_PATTERNS) is False
    assert gz.read_bytes() == old
    # a successful store replaces it
    setup['storage'].store_file = real
    assert _copy(setup, GRID_PATTERNS) is True
    assert gzip.decompress(gz.read_bytes()) == b'new' * 1000
    assert not glob.glob(str(setup['grid'] / '*.part'))


# ------------------------------------------------------------- skip / retrieve

def test_resolve_stored_name(tmp_path):
    st = LocalStorage(str(tmp_path))
    (tmp_path / 'a').mkdir()
    assert kosma_tau.resolve_stored_name(st, 'a/x') is None
    (tmp_path / 'a' / 'x.gz').write_bytes(b'')
    assert kosma_tau.resolve_stored_name(st, 'a/x') == 'a/x.gz'
    (tmp_path / 'a' / 'x').write_bytes(b'')
    assert kosma_tau.resolve_stored_name(st, 'a/x') == 'a/x'


def test_skip_registers_gz_names_in_db(setup):
    setup['grid'].mkdir(parents=True)
    gz = gzip.compress(CHEM)
    (setup['grid'] / 'pdrchemj001.hdf5.gz').write_bytes(gz)
    (setup['grid'] / 'chemchkj001.out.gz').write_bytes(gzip.compress(CHK))
    kosma_tau.update_db_pdr_output_entries(setup['job'].id, setup['session'])
    row = _hdf(setup)
    assert row.file_name_hdf5_c == 'pdrchemj001.hdf5.gz'
    assert row.file_size_hdf5_c == len(gz)
    job = setup['session'].get(kosma_tau.PDRModelJob, setup['job'].id)
    assert job.output_chemchk_file.endswith('chemchkj001.out.gz')


def test_retrieve_decompressed(setup, tmp_path):
    setup['grid'].mkdir(parents=True)
    (setup['grid'] / 'f.hdf5.gz').write_bytes(gzip.compress(CHEM))
    (setup['grid'] / 'plain.hdf5').write_bytes(b'plain')
    dst = tmp_path / 'out' / 'f.hdf5'
    dst.parent.mkdir()
    kosma_tau.retrieve_decompressed(setup['storage'], str(setup['grid']) + '/f.hdf5', str(dst))
    assert dst.read_bytes() == CHEM
    assert os.listdir(dst.parent) == ['f.hdf5']
    kosma_tau.retrieve_decompressed(setup['storage'], str(setup['grid']) + '/plain.hdf5',
                                    str(dst.parent / 'plain.hdf5'))
    assert (dst.parent / 'plain.hdf5').read_bytes() == b'plain'


def test_preflight_line(tmp_path):
    from pdr_run.cli import preflight
    ctx = MagicMock()
    ctx.eff = {'storage': {'compress_files': GRID_PATTERNS}}
    status, detail = preflight.check_compression(ctx)
    assert status == preflight.PASS and 'pdrchem*.hdf5' in detail
    ctx.eff = {'storage': {}}
    assert 'none' in preflight.check_compression(ctx)[1]
