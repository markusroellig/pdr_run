"""scripts/consolidate_simline.py: merge the stored SIMLINE HDF5 into the grid
pdrstruct file (stubbed local storage, synthetic HDF5 files)."""

import gzip
import importlib.util
import os

import numpy as np
import pytest

from pdr_run.storage.base import LocalCopy
from pdr_run.storage.local import LocalStorage

h5py = pytest.importorskip('h5py')

_SCRIPT = os.path.join(os.path.dirname(__file__), '..', '..', '..', 'scripts',
                       'consolidate_simline.py')
_spec = importlib.util.spec_from_file_location('consolidate_simline', _SCRIPT)
cs = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(cs)

MODEL = '100_30_30_-10'
BY = 'Integrated quantities/Intensities/By species'


def make_h5(path, simline, extra=None, struct=1.0):
    with h5py.File(path, 'w') as f:
        f.create_dataset('Local quantities/Gas/T', data=np.full(5, struct))
        f.create_dataset('Metadata/Metadata', data=np.arange(3.0 + simline))
        d = f.create_dataset(f'{BY}/CO', data=np.full((2, 3), 7.0 if simline else 1.0))
        d.attrs['source'] = 'SIMLINE' if simline else 'ONION'
        if simline:
            f.create_dataset('SIMLINE output/co/x', data=np.ones(4))
        for k, v in (extra or {}).items():
            f.create_dataset(k, data=v)


@pytest.fixture
def env(tmp_path):
    store = tmp_path / 'store'
    storage = LocalStorage(str(store))
    storage.local_copy = LocalCopy(str(tmp_path / 'mirror'), base_dir=str(store))
    mp = str(store / 'grid1')
    (store / 'grid1' / 'pdrgrid').mkdir(parents=True)
    (store / 'grid1' / 'simlinegrid').mkdir(parents=True)
    tmp = tmp_path / 'tmp'
    tmp.mkdir()
    return dict(storage=storage, mp=mp, store=store, tmp=str(tmp), mirror=tmp_path / 'mirror')


def put(env, main_kw, sim_kw, gz=False):
    main = env['store'] / 'grid1' / 'pdrgrid' / f'pdrstruct{MODEL}.hdf5'
    sim = env['store'] / 'grid1' / 'simlinegrid' / f'pdrstruct{MODEL}_simline.hdf5'
    make_h5(sim, True, **sim_kw)
    make_h5(main, False, **main_kw)
    if gz:
        main.write_bytes(gzip.compress(main.read_bytes()))
        main.rename(str(main) + '.gz')
    return main, sim


def run(env, **kw):
    return cs.consolidate_node(env['storage'], env['mp'], MODEL, env['tmp'], **kw)


def test_consolidates_with_attic_and_overwrite(env):
    main, sim = put(env, {}, {})
    sim_bytes = sim.read_bytes()
    old_bytes = main.read_bytes()
    res, _ = run(env)
    assert res == 'consolidated'
    assert main.read_bytes() == sim_bytes
    assert (main.parent / cs.ATTIC_DIR / main.name).read_bytes() == old_bytes
    assert sim.exists()                                   # no purge by default
    assert (env['mirror'] / 'grid1' / 'pdrgrid' / main.name).read_bytes() == sim_bytes
    # second run: grid file now carries SIMLINE species
    assert run(env)[0] == 'already'


def test_gz_grid_object_keeps_its_name(env):
    main, sim = put(env, {}, {}, gz=True)
    gz = main.parent / (main.name + '.gz')
    assert run(env)[0] == 'consolidated'
    assert sorted(os.listdir(main.parent)) == [cs.ATTIC_DIR, gz.name]
    assert gzip.decompress(gz.read_bytes()) == sim.read_bytes()
    assert (main.parent / cs.ATTIC_DIR / gz.name).exists()


def test_refused_when_common_dataset_differs(env):
    main, sim = put(env, {}, {})
    with h5py.File(sim, 'a') as f:
        f['Local quantities/Gas/T'][0] = 99.0
    before = main.read_bytes()
    res, detail = run(env)
    assert res == 'refused' and 'Local quantities/Gas/T' in detail
    assert main.read_bytes() == before and not (main.parent / cs.ATTIC_DIR).exists()


def test_refused_when_sim_is_not_superset(env):
    main, sim = put(env, dict(extra={'Extra/only_in_main': [1, 2]}), {})
    res, detail = run(env)
    assert res == 'refused' and 'only_in_main' in detail


def test_already_without_sim_object_and_dry_run(env):
    main, sim = put(env, {}, {})
    assert run(env, dry_run=True)[0] == 'consolidated'
    assert not (main.parent / cs.ATTIC_DIR).exists()
    sim.unlink()
    assert run(env)[0] == 'already'


def test_purge_deletes_sim_object_when_backend_can(env):
    main, sim = put(env, {}, {})
    env['storage'].delete_file = lambda path: os.remove(os.path.join(env['store'], path)) or True
    assert run(env, purge=True)[0] == 'consolidated'
    assert not sim.exists()


def test_purge_only_for_already_consolidated_node(env):
    main, sim = put(env, {}, {})
    make_h5(main, True)                                   # grid file consolidated earlier
    env['storage'].delete_file = lambda path: os.remove(os.path.join(env['store'], path)) or True
    assert run(env)[0] == 'already'                       # without --purge nothing happens
    assert sim.exists()
    assert run(env, purge=True)[0] == 'purged'
    assert not sim.exists() and main.exists()
    assert not (main.parent / cs.ATTIC_DIR).exists()      # no attic for a node that was not replaced


def test_selects_flagged_and_relaxed_nodes():
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from pdr_run.database.models import Base, ModelNames, PDRModelJob
    engine = create_engine('sqlite://')
    Base.metadata.create_all(engine)
    session = sessionmaker(bind=engine)()
    mn = ModelNames(model_name='grid1', model_path='/p')
    session.add(mn)
    session.flush()
    for status in ('finished', 'finished_relaxed', 'flagged', 'timeout', 'pending'):
        session.add(PDRModelJob(model_name_id=mn.id, model_job_name=f'100_30_30_{status}',
                                status=status))
    session.commit()
    names = [n for _, n, _ in cs.select_nodes(session, 'grid1')]
    assert names == ['100_30_30_finished', '100_30_30_finished_relaxed', '100_30_30_flagged']
    session.close()
