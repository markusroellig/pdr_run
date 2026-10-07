"""run_simline: a pipeline run that exits 0 but lost species (run_simline.py
exits 0 as soon as one species succeeded) or whose species lack their FITS /
HDF5 outputs is stored completely and then reported as SimlinePartialError,
i.e. postproc_error "SIMLINE: partial: ..." without a status change."""

import json
import os
from unittest.mock import MagicMock

import pytest

from pdr_run.models import kosma_tau
from pdr_run.storage.local import LocalStorage

MODEL = '100_30_30_-10'
STEM = f'pdrstruct{MODEL}_simline'
SPECIES = ['CO', 'C+', '13C+', 'C', 'O']


def textout(species=SPECIES, failed=(), summary=True):
    lines = ['=' * 60, 'SIMLINE HDF5 PIPELINE', '=' * 60,
             f'Species:     {", ".join(species)}', '']
    for sp in species:
        lines += [f'Processing species: {sp}', '    SIMLINE completed successfully.']
        if sp in failed:
            lines.append(f'    No FITS output found for {sp}')
    if summary:
        lines += ['', '=' * 60, 'SUMMARY', '=' * 60, f'Total:      {len(species)}',
                  f'Successful: {len(species) - len(failed)}', f'Failed:     {len(failed)}']
        if failed:
            lines.append(f'Failed species: {", ".join(failed)}')
    return '\n'.join(lines) + '\n'


def write_outputs(out_dir, species):
    os.makedirs(out_dir, exist_ok=True)
    for sp in species:
        for i in (1, 2):
            with open(os.path.join(out_dir, f'{STEM}_{sp}.{i}-{i - 1}.fits'), 'wb') as f:
                f.write(b'\0' * 2880)
        with open(os.path.join(out_dir, f'jtemp_{STEM}_{sp}.smli'), 'w') as f:
            f.write('x\n')


# ------------------------------------------------------------- parsing

def test_parse_textout_full_and_partial():
    sp, summ = kosma_tau.parse_simline_textout(textout())
    assert sp == SPECIES
    assert summ == dict(total=5, successful=5, failed=0, failed_species=[])
    sp, summ = kosma_tau.parse_simline_textout(textout(failed=('C+', '13C+')))
    assert summ['failed'] == 2 and summ['failed_species'] == ['C+', '13C+']


def test_parse_textout_without_summary():
    sp, summ = kosma_tau.parse_simline_textout(textout(summary=False))
    assert sp == SPECIES and summ is None
    assert kosma_tau.parse_simline_textout('') == (None, None)


# ------------------------------------------------------------- output check

def _check(tmp_path, text, have, species=None):
    out = tmp_path / 'simlineoutput'
    write_outputs(str(out), have)
    (out / 'TEXTOUT_SIMLINE').write_text(text)
    work = tmp_path / f'{STEM}.hdf5'
    work.write_bytes(b'not hdf5')            # unreadable -> HDF5 check skipped
    return kosma_tau.check_simline_outputs(str(out / 'TEXTOUT_SIMLINE'), str(out),
                                           str(work), species)


def test_check_complete(tmp_path):
    assert _check(tmp_path, textout(), SPECIES) == ([], [], None)


def test_check_failed_species_from_summary(tmp_path):
    failed = ('C+', '13C+')
    have = [s for s in SPECIES if s not in failed]
    assert _check(tmp_path, textout(failed=failed), have) == (['C+', '13C+'], [], None)


def test_check_species_reported_ok_but_without_fits(tmp_path):
    # the tier-0 'O' case: SUMMARY says all succeeded, no O FITS written
    have = [s for s in SPECIES if s != 'O']
    assert _check(tmp_path, textout(), have) == ([], ['O [fits]'], None)


def test_check_fits_pattern_is_species_exact(tmp_path):
    # 'C' must not be satisfied by the C+ / CO FITS files
    have = [s for s in SPECIES if s != 'C']
    assert _check(tmp_path, textout(), have)[1] == ['C [fits]']


def test_check_missing_summary_is_reported(tmp_path):
    failed, missing, note = _check(tmp_path, textout(summary=False), SPECIES)
    assert (failed, missing) == ([], []) and 'no SUMMARY' in note


def test_check_species_fallback_to_config(tmp_path):
    out = tmp_path / 'simlineoutput'
    write_outputs(str(out), ['CO'])
    # no 'Species:' header -> the configured list is checked
    (out / 'TEXTOUT_SIMLINE').write_text('\nSUMMARY\nTotal: 2\nSuccessful: 2\nFailed: 0\n')
    work = tmp_path / f'{STEM}.hdf5'
    work.write_bytes(b'x')
    assert kosma_tau.check_simline_outputs(str(out / 'TEXTOUT_SIMLINE'), str(out), str(work),
                                           ['CO', 'OH'])[1] == ['OH [fits]']


def test_check_hdf5_by_species_source(tmp_path):
    h5py = pytest.importorskip('h5py')
    out = tmp_path / 'simlineoutput'
    write_outputs(str(out), SPECIES)
    (out / 'TEXTOUT_SIMLINE').write_text(textout())
    work = tmp_path / f'{STEM}.hdf5'
    with h5py.File(work, 'w') as f:
        g = f.create_group(kosma_tau.SIMLINE_BY_SPECIES)
        for sp in SPECIES:
            g.create_dataset(sp, data=[[0.0, 0.0, 0.0]])
            # O only carries ONION's dataset (SIMLINE did not overwrite it)
            g[sp].attrs['source'] = 'ONION' if sp == 'O' else 'SIMLINE'
    assert kosma_tau.check_simline_outputs(str(out / 'TEXTOUT_SIMLINE'), str(out),
                                           str(work)) == ([], ['O [hdf5]'], None)


def test_error_message_format():
    e = kosma_tau.SimlinePartialError(['C+', '13C+'], ['O [fits]'])
    assert str(e) == 'partial: failed species C+, 13C+; missing outputs O [fits]'
    assert e.failed_species == ['C+', '13C+'] and e.missing_outputs == ['O [fits]']


# ------------------------------------------------------- run_simline end-to-end

_FAKE_DRIVER = '''\
import json, sys
from pathlib import Path
spec = json.loads(Path(sys.argv[0]).with_name('spec.json').read_text())
out = Path('simlineoutput')
out.mkdir(exist_ok=True)
for name in spec['files']:
    (out / name).write_bytes(b'0' * 100)
print(spec['textout'])
sys.exit(spec['rc'])
'''


@pytest.fixture
def env(tmp_path, monkeypatch):
    base = tmp_path / 'simline'
    (base / 'python').mkdir(parents=True)
    for d in ('bin', 'molecules'):
        (base / d).mkdir()
    (base / 'obs.template').write_text('beam\n')
    (base / 'python' / 'run_simline.py').write_text(_FAKE_DRIVER)
    (base / 'python' / 'simline_config.json').write_text('{"simline_dir": "x"}\n')

    store_root = tmp_path / 'store'

    class Storage(LocalStorage):
        fail = False

        def store_file(self, local_path, remote_path):
            return False if self.fail else super().store_file(local_path, remote_path)

    storage = Storage(str(store_root))
    monkeypatch.setattr('pdr_run.storage.base.get_storage_backend', lambda config=None: storage)
    job = MagicMock()
    job.model_job_name = MODEL
    job.model_name.model_path = str(store_root / 'grid1')
    session = MagicMock()
    session.get.return_value = job
    workdir = tmp_path / 'work'
    (workdir / 'pdroutput').mkdir(parents=True)
    (workdir / 'pdroutput' / 'pdrstruct_s.hdf5').write_bytes(b'hdf5' * 100)

    def run(text, have, rc=0, bundle=True):
        files = [f'{STEM}_{sp}.{i}-{i - 1}.fits' for sp in have for i in (1, 2)]
        (base / 'python' / 'spec.json').write_text(json.dumps(dict(textout=text, files=files, rc=rc)))
        cfg = {'simline': {'simline_dir': str(base), 'bundle_outputs': bundle}}
        return kosma_tau.run_simline(1, tmp_dir=str(workdir), config=cfg, session=session)

    return dict(run=run, storage=storage, grid_dir=store_root / 'grid1' / 'simlinegrid')


def test_run_simline_full_success(env):
    assert env['run'](textout(), SPECIES) is True


def test_run_simline_partial_stores_everything_then_raises(env):
    failed = ('C+', '13C+', 'C')
    have = [s for s in SPECIES if s not in failed and s != 'O']
    with pytest.raises(kosma_tau.SimlinePartialError) as ei:
        env['run'](textout(failed=failed), have, bundle=True)
    assert str(ei.value) == ('partial: failed species C+, 13C+, C; '
                             'missing outputs O [fits]')
    assert sorted(os.listdir(env['grid_dir'])) == sorted(
        [f'SIMLINE{MODEL}.tar.gz', f'pdrstruct{MODEL}_simline.hdf5'])


def test_run_simline_partial_file_by_file_layout(env):
    with pytest.raises(kosma_tau.SimlinePartialError):
        env['run'](textout(failed=('O',)), SPECIES[:-1], bundle=False)
    names = os.listdir(env['grid_dir'])
    assert f'pdrstruct{MODEL}_simline.hdf5' in names
    assert any(n.startswith(f'SIMLINE{MODEL}.TEXTOUT_SIMLINE') for n in names)


def test_run_simline_partial_with_storage_failure_returns_false(env):
    env['storage'].fail = True
    assert env['run'](textout(failed=('O',)), SPECIES[:-1]) is False


def test_run_simline_full_failure_unchanged(env):
    with pytest.raises(RuntimeError, match='exited with 1') as ei:
        env['run'](textout(failed=tuple(SPECIES)), [], rc=1)
    assert not isinstance(ei.value, kosma_tau.SimlinePartialError)
