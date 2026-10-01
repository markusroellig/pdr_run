"""Tests for ``pdr_run status --json`` (pdr_run/cli/status.py)."""

import hashlib
import json
import os
import shutil
from datetime import datetime, timedelta

import pytest
import yaml

from pdr_run.cli import status, status_physics

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(
    os.path.abspath(__file__)))))
SCHEMA = os.path.join(REPO_ROOT, 'pdr_run', 'schemas', 'status_v1.schema.json')
SECRET_PW = 'S3cretDbPw!zz9'
SECRET_CFG = 'config-json-secret-marker-42'
MODEL = 'gridT'


@pytest.fixture(autouse=True)
def clean_env(monkeypatch):
    for var in list(os.environ):
        if var.startswith('PDR_'):
            monkeypatch.delenv(var)


def _validate(payload):
    jsonschema = pytest.importorskip('jsonschema')
    with open(SCHEMA) as fh:
        schema = json.load(fh)
    jsonschema.Draft7Validator.check_schema(schema)
    jsonschema.validate(json.loads(json.dumps(payload)), schema)


def make_db(path, rows, model=MODEL, model_path=None):
    """Sandbox SQLite with the real models.  rows: dicts with node (name), status,
    n/M/chi (tenth-dex ints as in the job name), start/finish (datetime), extras."""
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    import pdr_run.database.models as m
    from pdr_run.database.base import Base
    engine = create_engine(f'sqlite:///{path}')
    Base.metadata.create_all(engine)
    S = sessionmaker(bind=engine)()
    mn = m.ModelNames(model_name=model, model_path=model_path or '/nonexistent')
    S.add(mn)
    exe = m.KOSMAtauExecutable(executable_file_name='pdrexe', sha256_sum='e' * 64)
    S.add(exe)
    S.flush()
    params = {}
    for r in rows:
        key = (r['n'], r['M'], r['chi'])
        if key not in params:
            p = m.KOSMAtauParameters(model_name_id=mn.id, xnsur=10 ** (0.1 * r['n']),
                                     mass=10 ** (0.1 * r['M']), sint=10 ** (0.1 * r['chi']), zmetal=1.0)
            S.add(p)
            S.flush()
            params[key] = p.id
        extra = {k: v for k, v in r.items() if k not in ('node', 'n', 'M', 'chi', 'status', 'start', 'finish')}
        job = m.PDRModelJob(
            model_name_id=mn.id, model_job_name=r['node'], kosmatau_parameters_id=params[key],
            kosmatau_executable_id=exe.id, status=r['status'], time_of_start=r.get('start'),
            time_of_finish=r.get('finish'), pending=r['status'] == 'pending',
            active=r['status'] == 'running', **extra)
        S.add(job)
    S.commit()
    S.close()
    engine.dispose()


def grid_rows(n_n=3, n_m=2, n_chi=2, now=None):
    """Full small grid, all finished, except as modified by the tests."""
    now = now or datetime.now()
    rows = []
    for i in range(n_n):
        for j in range(n_m):
            for k in range(n_chi):
                t0 = now - timedelta(hours=10 - i - j - k)
                rows.append(dict(node=f'100_{10 * (i + 1)}_{10 * j}_{10 * k}_00', n=10 * (i + 1),
                                 M=10 * j, chi=10 * k, status='finished', start=t0,
                                 finish=t0 + timedelta(hours=1, minutes=i),
                                 run_status_converged='true', run_status_global_iterations=7 + i,
                                 run_status_eps_final=1e-3, template_sha256='t' * 64))
    return rows


def run_status(db, model=MODEL, **kw):
    cfg = {'database': {'type': 'sqlite', 'path': str(db)}, 'pdr': {'max_walltime_s': 4 * 3600}}
    conn, info = status.open_readonly(cfg['database'])
    try:
        return status.build_status(conn, info, model, cfg, kw.pop('registry', {'workers': 2}), **kw)
    finally:
        conn.close()


@pytest.fixture
def db(tmp_path):
    p = tmp_path / 'grid.db'
    make_db(p, grid_rows())
    return p


# ------------------------------------------------------------------ schema

def test_schema_valid_and_axes(db):
    p = run_status(db)
    _validate(p)
    assert p['schema'] == 'pdr_run.status/1'
    assert p['summary']['total'] == 12 and p['summary']['by_class']['ok'] == 12
    keys = {a['key']: a['values'] for a in p['grid']['axes']}
    assert keys['n'] == [1.0, 2.0, 3.0] and keys['M'] == [0.0, 1.0] and keys['chi'] == [0.0, 1.0]
    assert 'Z' not in keys                       # constant metallicity is not an axis
    assert p['grid']['facet_axis'] == 'M'
    node = next(n for n in p['nodes'] if n['id'] == '100_30_10_10_00')
    assert node['idx'] == [2, 1, 1] and node['exec_s'] == pytest.approx(3720.0)
    assert node['converged'] is True and node['global_it'] == 9


def test_schema_rejects_bad_payload(db):
    jsonschema = pytest.importorskip('jsonschema')
    p = run_status(db)
    p['nodes'][0]['class'] = 'purple'
    with pytest.raises(jsonschema.ValidationError):
        _validate(p)


# -------------------------------------------------------------- node state

def test_node_state_is_latest_non_skipped(tmp_path):
    now = datetime.now()
    rows = [
        dict(node='A', n=10, M=0, chi=0, status='not_converged', start=now - timedelta(hours=9), finish=now - timedelta(hours=8)),
        dict(node='A', n=10, M=0, chi=0, status='finished', start=now - timedelta(hours=5), finish=now - timedelta(hours=4)),
        dict(node='A', n=10, M=0, chi=0, status='skipped', start=now - timedelta(hours=1), finish=now - timedelta(hours=1)),
        dict(node='B', n=20, M=0, chi=0, status='finished', start=now - timedelta(hours=9), finish=now - timedelta(hours=8)),
        dict(node='B', n=20, M=0, chi=0, status='running', start=now - timedelta(hours=1)),
        dict(node='C', n=30, M=0, chi=0, status='skipped', start=now, finish=now),
        dict(node='D', n=40, M=0, chi=0, status='pending'),
        dict(node='E', n=50, M=0, chi=0, status='timeout', start=now - timedelta(hours=6), finish=now - timedelta(hours=2)),
        dict(node='F', n=60, M=0, chi=0, status='finished', start=now - timedelta(hours=3), finish=now - timedelta(hours=2),
             postproc_error='SIMLINE failed'),
    ]
    db = tmp_path / 's.db'
    make_db(db, rows)
    p = run_status(db)
    _validate(p)
    st = {n['id']: n for n in p['nodes']}
    assert st['A']['status'] == 'finished' and st['A']['reruns'] == 1 and st['A']['class'] == 'ok'
    assert st['B']['status'] == 'running' and st['B']['reruns'] == 1 and st['B']['class'] == 'run'
    assert st['C']['status'] == 'skipped' and st['C']['class'] == 'ok'
    assert st['D']['class'] == 'pending' and st['E']['class'] == 'bad'
    assert st['F']['class'] == 'warn' and st['F']['postproc_error'] == 'SIMLINE failed'
    assert p['summary']['total'] == 6                      # nodes, not rows
    assert p['summary']['n_rows'] == 9


# ---------------------------------------------------------- running / ETA

def test_running_elapsed_and_cap(tmp_path):
    now = datetime.now()
    rows = grid_rows()[:4]
    rows.append(dict(node='R1', n=70, M=0, chi=0, status='running', start=now - timedelta(hours=2)))
    rows.append(dict(node='R2', n=80, M=0, chi=0, status='running', start=now - timedelta(hours=7)))   # > 1.5 * 4 h
    db = tmp_path / 'r.db'
    make_db(db, rows)
    p = run_status(db)
    _validate(p)
    r = {x['node']: x for x in p['running']}
    assert r['R1']['elapsed_s'] == pytest.approx(7200, abs=60)
    assert r['R1']['cap_s'] == 4 * 3600 and r['R1']['frac_cap'] == pytest.approx(0.5, abs=0.01)
    assert any(u['kind'] == 'stale_running' and 'R2' in u['text'] for u in p['urgent'])
    assert not any('R1' in u['text'] for u in p['urgent'])
    assert p['summary']['by_class']['run'] == 2
    assert p['eta_inputs']['workers'] == 2 and len(p['eta_inputs']['samples']) == 4
    assert p['summary']['eta']['n_fit'] == 4 and p['summary']['eta']['p50'] is not None


def test_eta_needs_workers_and_samples(db):
    p = run_status(db, registry={})
    assert p['summary']['eta']['p50'] is None


def test_timeout_is_censored_sample(tmp_path):
    now = datetime.now()
    rows = [dict(node='E', n=10, M=0, chi=0, status='timeout', start=now - timedelta(hours=6),
                 finish=now - timedelta(hours=2))]
    db = tmp_path / 't.db'
    make_db(db, rows)
    p = run_status(db)
    assert p['eta_inputs']['samples'][0]['censored'] is True
    assert p['eta_inputs']['samples'][0]['exec_s'] == 4 * 3600


# ------------------------------------------------------------------ --since

def test_since_filters_nodes_and_events(tmp_path):
    now = datetime.now()
    rows = [
        dict(node='old', n=10, M=0, chi=0, status='finished', start=now - timedelta(hours=30),
             finish=now - timedelta(hours=29)),
        dict(node='new', n=20, M=0, chi=0, status='finished', start=now - timedelta(minutes=50),
             finish=now - timedelta(minutes=10)),
        dict(node='bad', n=30, M=0, chi=0, status='aborted', start=now - timedelta(minutes=40),
             finish=now - timedelta(minutes=20)),
        dict(node='run', n=40, M=0, chi=0, status='running', start=now - timedelta(hours=3)),
    ]
    db = tmp_path / 'e.db'
    make_db(db, rows)
    full = run_status(db)
    assert {n['id'] for n in full['nodes']} == {'old', 'new', 'bad', 'run'} and full['delta'] is False
    # default window is 24 h: 'old' (finished 29 h ago) has no event
    assert {e['node'] for e in full['events']} == {'new', 'bad', 'run'}

    p = run_status(db, since=now - timedelta(minutes=30))
    _validate(p)
    assert p['delta'] is True and p['since'] is not None
    assert {n['id'] for n in p['nodes']} == {'new', 'run'} or {n['id'] for n in p['nodes']} >= {'run'}
    ids = {n['id'] for n in p['nodes']}
    assert 'old' not in ids and 'run' in ids
    kinds = {(e['node'], e['kind']) for e in p['events']}
    assert ('new', 'job_finished') in kinds and ('bad', 'job_finished') in kinds
    assert ('old', 'job_finished') not in kinds and ('new', 'job_started') not in kinds
    assert next(e for e in p['events'] if e['node'] == 'bad')['sev'] == 'error'
    assert p['summary']['total'] == 4                      # summary stays complete


def test_since_parser():
    assert status._parse_ts('2026-10-01T12:00:00') == datetime(2026, 10, 1, 12, 0)
    assert isinstance(status._parse_ts('2026-10-01T12:00:00Z'), datetime)
    with pytest.raises(status.StatusError):
        status._parse_ts('yesterday')


# ----------------------------------------------------------------- physics

def make_h5(path, tweak=None):
    """Synthetic pdrstruct with known answers (layout of KOSMA-tau rc2 output)."""
    h5py = pytest.importorskip('h5py')
    import numpy as np
    n = 8
    av = np.array([0.0, 1e-4, 0.2, 0.5, 1.0, 2.0, 4.0, 8.0])
    gas = np.zeros((n, 6))
    gas[:, 0] = 1e4
    gas[:, 2] = [500, 400, 300, 200, 100, 50, 30, 20]          # T_gas; row 0 is unfilled (av = 0)
    pos = np.zeros((n, 5))
    pos[:, 0] = av
    pos[:, 1] = av * 1.1
    h = np.array([5000, 4000, 3000, 2000, 400, 100, 10, 1.0])    # n(H)
    h2 = (1e4 - h) / 2.0
    dens = np.stack([np.full(n, 1.0), h, h2], axis=1)          # ELECTR, H, H2
    meta = np.array([[b'/L', b'Densities', str(i).encode(), b'x', lab, b'', b'', b'', b'', b'', b'', b'', b'']
                     for i, lab in enumerate([b'n(ELECTR)', b'n(H)', b'n(H2)'])], dtype='S40')
    ir = np.zeros((10, 6))
    ir[:, 4] = 2.0                                              # wavelength column must NOT be summed
    ir[:, 5] = 1e-5
    levx = np.array([[0, 0, 1, 0.], [1, 0, 9, 118.], [2, 0, 5, 354.], [3, 0, 21, 705.]])
    levcol = np.zeros((n, 3 + 4))
    levcol[-1, -4:] = [4.0, 6.0, 1.0, 1.0]                      # even J: 5, odd J: 7
    with h5py.File(path, 'w') as f:
        f['Local quantities/Gas state'] = gas
        f['Local quantities/Positions'] = pos
        f['Local quantities/Densities/Densities'] = dens
        f['Metadata/Metadata'] = meta
        f['Integrated quantities/Spectrum/IR Lines/Spectrum IR small'] = ir
        f['Integrated quantities/Excitation/H2 ortho-para all levels'] = np.array([[1.4]])
        f['Local quantities/Auxiliary/Excitation/Level column densities'] = levcol
        f['Parameters/H2 energy levels X'] = levx
        f['Local quantities/Auxiliary/Molecular fraction'] = np.linspace(0, 0.9, n).reshape(n, 1)
        if tweak:
            tweak(f)


def test_physics_extraction_known_values(tmp_path):
    pytest.importorskip('h5py')
    p = tmp_path / 'pdrstructX.hdf5'
    make_h5(p)
    q, flags = status_physics.extract_quantities(str(p))
    assert q['log_Tsurf'] == pytest.approx(2.60206, abs=1e-5)       # row 0 (av=0) skipped -> 400 K
    assert q['log_Tdeep'] == pytest.approx(1.30103, abs=1e-5)       # 20 K
    assert q['log_H2IR_tot'] == pytest.approx(-4.0, abs=1e-9)       # 10 * 1e-5 (column 5)
    assert q['op_col'] == pytest.approx(1.4)
    # x = 2n(H2)/(n(H)+2n(H2)) = 1 - n(H)/1e4: 0.6 (av=0.2), 0.8 (av=0.5) -> 0.5 not reached at
    # the first filled zone (av=1e-4, x=0.6): front is at the surface
    assert q['AV_HH2'] == pytest.approx(1e-4) and flags['AV_HH2'] == 'surface'


def test_physics_front_interpolation_and_not_reached(tmp_path):
    pytest.importorskip('h5py')
    import numpy as np

    def tweak(f):
        h = np.array([9000, 8000, 7000, 6000, 4000, 1000, 100, 1.0])  # x = .1 .2 .3 .4 .6 .9 ...
        d = f['Local quantities/Densities/Densities'][()]
        d[:, 1] = h
        d[:, 2] = (1e4 - h) / 2
        f['Local quantities/Densities/Densities'][...] = d
    p = tmp_path / 'a.hdf5'
    make_h5(p, tweak)
    q, flags = status_physics.extract_quantities(str(p))
    # filled zones av = 1e-4, .2, .5, 1, 2 ...; x = .2,.3,.4,.6,... -> 0.5 between av=.5 (x=.4) and av=1 (x=.6)
    assert q['AV_HH2'] == pytest.approx(0.75, abs=1e-6) and flags['AV_HH2'] == 'ok'

    def tweak2(f):
        d = f['Local quantities/Densities/Densities'][()]
        d[:, 1] = 1e4
        d[:, 2] = 0.0
        f['Local quantities/Densities/Densities'][...] = d
    p2 = tmp_path / 'b.hdf5'
    make_h5(p2, tweak2)
    q2, f2 = status_physics.extract_quantities(str(p2))
    assert q2['AV_HH2'] is None and f2['AV_HH2'] == 'not_reached'


def test_physics_op_fallback_and_dataset_source(tmp_path):
    pytest.importorskip('h5py')

    def tweak(f):
        del f['Integrated quantities/Excitation/H2 ortho-para all levels']
    p = tmp_path / 'c.hdf5'
    make_h5(p, tweak)
    q, _ = status_physics.extract_quantities(str(p))
    assert q['op_col'] == pytest.approx(7.0 / 5.0)               # odd J / even J, last row
    qd, fd = status_physics.extract_quantities(str(p), molfrac_source='dataset')
    assert fd['molfrac_source'] == 'dataset' and qd['AV_HH2'] is not None


def test_physics_missing_datasets_give_none(tmp_path):
    h5py = pytest.importorskip('h5py')
    import numpy as np
    p = tmp_path / 'min.hdf5'
    with h5py.File(p, 'w') as f:
        f['Local quantities/Gas state'] = np.array([[1e4, 1e4, 50., 20., 0., 0.]] * 3)
        f['Local quantities/Positions'] = np.array([[0.1, 0, 0, 0, 0], [0.2, 0, 0, 0, 0], [0.3, 0, 0, 0, 0]])
    q, flags = status_physics.extract_quantities(str(p))
    assert q['log_Tsurf'] == pytest.approx(1.69897, abs=1e-5)
    assert q['log_H2IR_tot'] is None and q['op_col'] is None and q['AV_HH2'] is None


def _physics_db(tmp_path, with_files=True):
    store = tmp_path / 'store' / MODEL / 'pdrgrid'
    store.mkdir(parents=True)
    rows = grid_rows(2, 1, 1)
    db = tmp_path / 'p.db'
    make_db(db, rows, model_path=str(tmp_path / 'store' / MODEL))
    if with_files:
        make_h5(store / f'pdrstruct{rows[0]["node"]}.hdf5')
    return db, store, rows


def test_physics_in_payload_and_cache(tmp_path):
    pytest.importorskip('h5py')
    db, store, rows = _physics_db(tmp_path)
    cache = tmp_path / 'cache' / 'phys.json'
    p = run_status(db, with_physics=True, cache_path=str(cache))
    _validate(p)
    assert p['physics_meta']['computed'] == 1 and p['physics_meta']['no_file'] == 1
    assert p['physics'][0]['node'] == rows[0]['node']
    assert p['physics'][0]['q']['log_H2IR_tot'] == pytest.approx(-4.0)
    assert cache.exists()

    # second call: from the cache, the HDF5 file is not opened again
    import h5py
    real = h5py.File
    calls = []
    status_physics.h5py.File = lambda *a, **k: calls.append(a) or real(*a, **k)
    try:
        p2 = run_status(db, with_physics=True, cache_path=str(cache))
    finally:
        status_physics.h5py.File = real
    assert p2['physics_meta']['cached'] == 1 and p2['physics_meta']['computed'] == 0 and not calls
    assert p2['physics'] == p['physics']

    # touching the file (size/mtime change) invalidates the entry
    f = store / f'pdrstruct{rows[0]["node"]}.hdf5'
    st = f.stat()
    os.utime(f, ns=(st.st_atime_ns, st.st_mtime_ns + 10_000_000_000))
    p3 = run_status(db, with_physics=True, cache_path=str(cache))
    assert p3['physics_meta']['computed'] == 1


def test_physics_corrupt_cache_and_bad_file(tmp_path):
    pytest.importorskip('h5py')
    db, store, rows = _physics_db(tmp_path)
    cache = tmp_path / 'phys.json'
    cache.write_text('{not json')
    (store / f'pdrstruct{rows[1]["node"]}.hdf5').write_bytes(b'not an hdf5 file')
    p = run_status(db, with_physics=True, cache_path=str(cache))
    _validate(p)
    assert p['physics_meta']['computed'] == 1 and p['physics_meta']['failed'] == 1
    bad = next(r for r in p['physics'] if r['node'] == rows[1]['node'])
    assert 'error' in bad['flags'] and all(v is None for v in bad['q'].values())


def test_physics_budget_defers(tmp_path):
    pytest.importorskip('h5py')
    db, store, rows = _physics_db(tmp_path)
    make_h5(store / f'pdrstruct{rows[1]["node"]}.hdf5')
    p = run_status(db, with_physics=True, cache_path=str(tmp_path / 'c.json'), physics_budget_s=-1)
    assert p['physics_meta']['deferred'] == 2 and p['physics_meta']['computed'] == 0


def test_physics_without_h5py(tmp_path, monkeypatch):
    db, store, rows = _physics_db(tmp_path)
    monkeypatch.setattr(status_physics, 'h5py', None)
    p = run_status(db, with_physics=True, cache_path=str(tmp_path / 'c.json'))
    _validate(p)
    assert p['physics'] == [] and p['physics_meta']['h5py'] is False
    assert 'h5py' in p['physics_meta']['skipped']
    assert not (tmp_path / 'c.json').exists()
    assert p['summary']['total'] == 2                     # the rest of the payload is intact


def test_physics_gz_file(tmp_path):
    pytest.importorskip('h5py')
    import gzip
    db, store, rows = _physics_db(tmp_path, with_files=False)
    plain = tmp_path / 'tmp.hdf5'
    make_h5(plain)
    with open(plain, 'rb') as a, gzip.open(store / f'pdrstruct{rows[0]["node"]}.hdf5.gz', 'wb') as b:
        shutil.copyfileobj(a, b)
    p = run_status(db, with_physics=True, cache_path=str(tmp_path / 'c.json'))
    assert p['physics_meta']['computed'] == 1


# ------------------------------------------------- read-only and credentials

def _fingerprint(path):
    st = os.stat(path)
    return st.st_mtime_ns, st.st_size, hashlib.sha256(open(path, 'rb').read()).hexdigest()


def test_read_only_no_db_or_file_writes(tmp_path, capsys):
    rows = grid_rows()
    rows[0]['status'] = 'running'
    d = tmp_path / 'sandbox'
    d.mkdir()
    db = d / 'ro.db'
    make_db(db, rows)
    cfg = d / 'cfg.yaml'
    cfg.write_text(yaml.safe_dump({'database': {'type': 'sqlite', 'path': str(db)},
                                   'pdr': {'model_name': MODEL, 'max_walltime_s': 3600}}))
    before, listing = _fingerprint(db), sorted(os.listdir(d))
    cwd = os.getcwd()
    os.chdir(d)
    try:
        assert status.main(['--config', str(cfg), '--json', '--with-physics', '--with-local',
                            '--physics-cache', str(d / 'phys.json')]) == 0
        assert status.main(['--config', str(cfg)]) == 0                     # human table
    finally:
        os.chdir(cwd)
    assert _fingerprint(db) == before
    # no journal, no log directory, no cache (no files with physics output), nothing else
    assert sorted(os.listdir(d)) == listing
    out = capsys.readouterr().out
    assert json.loads(out.splitlines()[0])['schema'] == 'pdr_run.status/1'


def test_sqlite_connection_cannot_write(db):
    from sqlalchemy import text
    from sqlalchemy.exc import OperationalError
    conn, _ = status.open_readonly({'type': 'sqlite', 'path': str(db)})
    try:
        with pytest.raises(OperationalError):
            conn.execute(text("UPDATE pdr_model_jobs SET status='x'"))
    finally:
        conn.close()


def test_out_file_is_the_only_write(tmp_path):
    db = tmp_path / 'o.db'
    make_db(db, grid_rows())
    cfg = tmp_path / 'cfg.yaml'
    cfg.write_text(yaml.safe_dump({'database': {'type': 'sqlite', 'path': str(db)}}))
    out = tmp_path / 'snap.json'
    before = sorted(os.listdir(tmp_path))
    assert status.main(['--config', str(cfg), '--model-name', MODEL, '--json', '--out', str(out)]) == 0
    assert sorted(os.listdir(tmp_path)) == sorted(before + ['snap.json'])
    _validate(json.loads(out.read_text()))


def test_credentials_and_config_json_never_in_payload(tmp_path, monkeypatch):
    rows = grid_rows(1, 1, 1)
    db = tmp_path / 'c.db'
    make_db(db, rows)
    # a job whose config_json carries a secret, and a postproc_error that echoes the password
    from sqlalchemy import create_engine, text
    eng = create_engine(f'sqlite:///{db}')
    with eng.begin() as c:
        c.execute(text("UPDATE pdr_model_jobs SET config_json=:j, postproc_error=:e"),
                  {'j': json.dumps({'k': SECRET_CFG}), 'e': f'connect failed password={SECRET_PW}'})
    eng.dispose()
    cfg = tmp_path / 'cfg.yaml'
    cfg.write_text(yaml.safe_dump({'database': {'type': 'sqlite', 'path': str(db), 'password': SECRET_PW,
                                                'username': 'confidential_user'},
                                   'pdr': {'model_name': MODEL}}))
    monkeypatch.setenv('PDR_STORAGE_PASSWORD', 'S3cretStPw!qq7')
    out = tmp_path / 'o.json'
    assert status.main(['--config', str(cfg), '--json', '--out', str(out), '--with-local']) == 0
    text_ = out.read_text()
    for needle in (SECRET_PW, SECRET_CFG, 'confidential_user', 'S3cretStPw!qq7', 'config_json'):
        assert needle not in text_, needle
    assert '***' in text_                                  # the echoed password was scrubbed, not dropped


def test_missing_model_and_db_errors(tmp_path, capsys):
    db = tmp_path / 'm.db'
    make_db(db, grid_rows(1, 1, 1))
    cfg = tmp_path / 'cfg.yaml'
    cfg.write_text(yaml.safe_dump({'database': {'type': 'sqlite', 'path': str(db)}}))
    assert status.main(['--config', str(cfg), '--json']) == 2                         # no model name
    assert status.main(['--config', str(cfg), '--json', '--model-name', 'nope']) == 2
    assert 'recent' in capsys.readouterr().err
    cfg.write_text(yaml.safe_dump({'database': {'type': 'sqlite', 'path': str(tmp_path / 'absent.db')}}))
    assert status.main(['--config', str(cfg), '--json', '--model-name', MODEL]) == 2
    assert not (tmp_path / 'absent.db').exists()                                      # never created


def test_env_credentials_like_rest_of_pdr_run(tmp_path, monkeypatch):
    db = tmp_path / 'e.db'
    make_db(db, grid_rows(1, 1, 1))
    monkeypatch.setenv('PDR_DB_TYPE', 'sqlite')
    monkeypatch.setenv('PDR_DB_FILE', str(db))
    assert status.main(['--model-name', MODEL, '--json']) == 0


def test_older_database_without_additive_columns(tmp_path):
    """A database from before the run_status columns: status still works, nothing is ALTERed."""
    from sqlalchemy import create_engine, text
    db = tmp_path / 'old.db'
    make_db(db, grid_rows(1, 1, 1))
    eng = create_engine(f'sqlite:///{db}')
    with eng.begin() as c:
        for col in ('run_status_eps_final', 'postproc_error', 'uvcont_error'):
            c.execute(text(f'ALTER TABLE pdr_model_jobs DROP COLUMN {col}'))
    eng.dispose()
    p = run_status(db)
    _validate(p)
    assert p['summary']['total'] == 1 and 'eps' not in p['nodes'][0]


def test_local_probe_parses_textout():
    t = ("*** current shell #: 143 *** current iteration step: 4\nAV = 2.31E+00\n"
         "T(gas): 48.2 T(dust): 20.1 stable T: T\nBrent fallback\nMAXIT reached\n")
    r = status.parse_textout_tail(t)
    assert r['shell'] == 143 and r['global_it'] == 4 and r['av'] == pytest.approx(2.31)
    assert r['t_gas'] == pytest.approx(48.2) and r['brent_fallbacks'] == 1 and r['maxit'] == 1


def test_entry_dispatch_does_not_import_runner(monkeypatch):
    """``pdr_run status`` must not import the runner (it creates logs/ on import)."""
    import subprocess
    import sys
    code = ("import sys; sys.argv=['pdr_run','status','--help']\n"
            "from pdr_run.cli import entry\n"
            "try:\n entry.main()\nexcept SystemExit: pass\n"
            "print('RUNNER' if 'pdr_run.cli.runner' in sys.modules else 'CLEAN')\n")
    r = subprocess.run([sys.executable, '-c', code], capture_output=True, text=True, cwd=REPO_ROOT)
    assert r.stdout.strip().endswith('CLEAN'), r.stdout + r.stderr
