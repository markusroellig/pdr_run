"""Tests for the preflight check (``pdr_run --check``)."""

import io
import json
import os
import shutil
import sqlite3
import sys
import textwrap

import pytest
import yaml

from pdr_run.cli import preflight
from pdr_run.cli.preflight import run_preflight, strip_json_comments

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(
    os.path.abspath(__file__)))))
TEMPLATE = os.path.join(REPO_ROOT, 'templates', 'pdr_config.json.template')

SECRET_DB = 'S3cretDbPw!zz9'
SECRET_ST = 'S3cretStPw!qq7'
SECRET_USER = 'confidential_user'


@pytest.fixture(autouse=True)
def clean_env(monkeypatch):
    for var in list(os.environ):
        if var.startswith('PDR_'):
            monkeypatch.delenv(var)


@pytest.fixture
def install(tmp_path):
    """A fake KOSMA-tau run directory with mock executables and inputs."""
    base = tmp_path / 'rundir'
    base.mkdir()
    for exe in ('pdrexe', 'onionexe', 'getctrlind', 'mrt.exe'):
        f = base / exe
        f.write_text('#!/bin/sh\n[ "$1" = "--version" ] && echo "Running version: v9.9.9-1-gabcdef0"\nexit 0\n')
        f.chmod(0o755)
    (base / 'pdrinpdata').mkdir()
    (base / 'pdrinpdata' / 'chem_test.dat').write_text('x' * 100)
    (base / 'pdrinpdata' / 'binding_energies.dist').write_text('x' * 100)
    (base / 'onioninpdata').mkdir()
    (base / 'onioninpdata' / 'ONION3.INP.CO').write_text('x')
    (base / 'In').mkdir()
    (base / 'templates').mkdir()
    shutil.copy(TEMPLATE, base / 'templates' / 'pdr_config.json.template')
    return base


def _make_db(path, drop_column=None):
    """Full current schema in a sqlite file, via the real models."""
    from sqlalchemy import create_engine, text
    import pdr_run.database.models  # noqa: F401
    from pdr_run.database.base import Base
    engine = create_engine(f'sqlite:///{path}')
    Base.metadata.create_all(engine)
    if drop_column:
        with engine.begin() as conn:
            conn.execute(text(f'ALTER TABLE pdr_model_jobs DROP COLUMN {drop_column}'))
    engine.dispose()


@pytest.fixture
def config(tmp_path, install):
    db = tmp_path / 'pdr.db'
    _make_db(db)
    store = tmp_path / 'store'
    store.mkdir()
    cfg = {
        'database': {'type': 'sqlite', 'path': str(db)},
        'storage': {'type': 'local', 'base_dir': str(store)},
        'pdr': {'base_dir': str(install), 'pdr_file_name': 'pdrexe',
                'onion_file_name': 'onionexe', 'getctrlind_file_name': 'getctrlind',
                'mrt_file_name': 'mrt.exe', 'chem_database': 'chem_test.dat',
                'json_template_file': 'pdr_config.json.template',
                'max_walltime_s': 3600},
        'model_parameters': {'species': ['CO']},
    }
    return cfg


def _write(tmp_path, cfg):
    path = tmp_path / 'cfg.yaml'
    path.write_text(yaml.safe_dump(cfg))
    return str(path)


def _run(tmp_path, cfg, **kw):
    out = io.StringIO()
    kw.setdefault('min_free_gb', 0.0)
    kw.setdefault('timeout', 1.0)
    rc = run_preflight(config_path=_write(tmp_path, cfg), json_output=True, out=out, **kw)
    data = json.loads(out.getvalue())
    return rc, data, {c['name']: c for c in data['checks']}, out.getvalue()


def test_all_pass(tmp_path, config):
    rc, data, checks, _ = _run(tmp_path, config)
    assert rc == 0, [c for c in data['checks'] if c['status'] == 'FAIL']
    assert data['ok'] is True
    for name in ('config.file', 'kt.exe.pdr', 'kt.exe.onion', 'kt.exe.getctrlind',
                 'kt.exe.mrt', 'kt.pdr_version', 'tpl.json', 'tpl.chem_network',
                 'tpl.binding_energies', 'storage', 'db.connect', 'db.tables',
                 'db.columns', 'db.additive_columns', 'db.rows', 'db.stale_jobs',
                 'db.write_rollback', 'post.onion', 'run.walltime'):
        assert checks[name]['status'] == 'PASS', checks[name]
    assert 'v9.9.9' in checks['kt.pdr_version']['detail']
    assert checks['post.uv_continuum']['status'] == 'SKIP'
    assert checks['post.simline']['status'] == 'SKIP'


def test_text_report_is_compact(tmp_path, config):
    out = io.StringIO()
    rc = run_preflight(config_path=_write(tmp_path, config), out=out, min_free_gb=0.0, timeout=1.0)
    lines = out.getvalue().rstrip('\n').splitlines()
    assert rc == 0
    assert 20 <= len(lines) <= 45
    assert lines[0].startswith('[PASS]')
    assert any(l.startswith('Summary:') and '0 FAIL' in l for l in lines)
    assert 'detailed log' in lines[-1]


def test_no_side_effects(tmp_path, config, install):
    def snapshot():
        return sorted(str(p.relative_to(tmp_path)) for p in tmp_path.rglob('*')
                      if 'cfg.yaml' not in p.name)
    before = snapshot()
    db = config['database']['path']
    size_before = os.path.getsize(db)
    _run(tmp_path, config)
    assert snapshot() == before          # probe files/dirs removed
    assert os.path.getsize(db) == size_before
    con = sqlite3.connect(db)
    assert con.execute('select count(*) from users').fetchone()[0] == 0   # insert rolled back
    con.close()


def test_missing_executable(tmp_path, config, install):
    (install / 'onionexe').unlink()
    rc, _, checks, _ = _run(tmp_path, config)
    assert rc == 1
    assert checks['kt.exe.onion']['status'] == 'FAIL'
    assert 'not found' in checks['kt.exe.onion']['detail']
    # isolation: everything else still ran
    assert checks['db.connect']['status'] == 'PASS'
    assert checks['kt.exe.pdr']['status'] == 'PASS'


def test_non_executable_file(tmp_path, config, install):
    (install / 'mrt.exe').chmod(0o644)
    rc, _, checks, _ = _run(tmp_path, config)
    assert rc == 1
    assert 'not executable' in checks['kt.exe.mrt']['detail']


@pytest.mark.skipif(os.geteuid() == 0, reason='root ignores directory permissions')
def test_unwritable_directories(tmp_path, config, install):
    store = tmp_path / 'store'
    os.chmod(store, 0o555)
    os.chmod(install, 0o555)
    try:
        rc, _, checks, _ = _run(tmp_path, config)
    finally:
        os.chmod(store, 0o755)
        os.chmod(install, 0o755)
    assert rc == 1
    assert checks['storage']['status'] == 'FAIL'
    assert checks['kt.rundir_write']['status'] == 'WARN'


def test_broken_symlink_input_dir(tmp_path, config, install):
    shutil.rmtree(install / 'pdrinpdata')
    os.symlink(tmp_path / 'nowhere', install / 'pdrinpdata')
    rc, _, checks, _ = _run(tmp_path, config)
    assert rc == 1
    assert 'broken symlink' in checks['kt.input_dirs']['detail']
    assert checks['tpl.chem_network']['status'] == 'FAIL'


def test_bad_database_url(tmp_path, config):
    config['database'] = {'type': 'mysql', 'host': '127.0.0.1', 'port': 1,
                          'database': 'nodb', 'username': 'nobody'}
    os.environ['PDR_DB_PASSWORD'] = 'irrelevant-pw-1'
    try:
        rc, _, checks, _ = _run(tmp_path, config, timeout=2.0)
    finally:
        del os.environ['PDR_DB_PASSWORD']
    assert rc == 1
    assert checks['db.connect']['status'] == 'FAIL'
    for dep in ('db.tables', 'db.columns', 'db.additive_columns', 'db.rows',
                'db.stale_jobs', 'db.write_rollback'):
        assert checks[dep]['status'] == 'SKIP'
    assert checks['storage']['status'] == 'PASS'      # unrelated checks still run


def test_unsupported_and_incomplete_database(tmp_path, config):
    config['database'] = {'type': 'oracle'}
    rc, _, checks, _ = _run(tmp_path, config)
    assert rc == 1 and 'Unsupported database type' in checks['db.connect']['detail']
    config['database'] = {'type': 'mysql'}
    rc, _, checks, _ = _run(tmp_path, config)
    assert rc == 1 and 'password' in checks['db.connect']['detail']


def test_missing_additive_column_is_fail_and_not_repaired(tmp_path, config):
    db = config['database']['path']
    os.remove(db)
    _make_db(db, drop_column='uvcont_error')
    rc, _, checks, _ = _run(tmp_path, config)
    assert rc == 1
    assert checks['db.additive_columns']['status'] == 'FAIL'
    assert 'uvcont_error' in checks['db.additive_columns']['detail']
    assert 'ensure_additive_columns' in checks['db.additive_columns']['detail']
    assert checks['db.columns']['status'] == 'PASS'   # generic columns are fine
    con = sqlite3.connect(db)                          # --check must not have ALTERed
    cols = [r[1] for r in con.execute('pragma table_info(pdr_model_jobs)')]
    con.close()
    assert 'uvcont_error' not in cols


def test_missing_table_and_generic_column(tmp_path, config):
    db = config['database']['path']
    con = sqlite3.connect(db)
    con.execute('drop table json_files')
    con.close()
    rc, _, checks, _ = _run(tmp_path, config)
    assert rc == 1
    assert 'json_files' in checks['db.tables']['detail']


def test_stale_running_job_is_reported(tmp_path, config):
    db = config['database']['path']
    con = sqlite3.connect(db)
    con.execute("insert into pdr_model_jobs (model_job_name, status, time_of_start) "
                "values ('old', 'running', '2020-01-01 00:00:00')")
    con.commit()
    con.close()
    rc, _, checks, _ = _run(tmp_path, config)
    assert rc == 0
    assert checks['db.stale_jobs']['status'] == 'WARN'
    assert 'stale(' in checks['db.stale_jobs']['detail']
    assert '--reset-stale-jobs' in checks['db.stale_jobs']['detail']


def test_in_memory_sqlite_is_warning(tmp_path, config):
    config['database'] = {'type': 'sqlite', 'path': ':memory:'}
    rc, _, checks, _ = _run(tmp_path, config)
    assert rc == 0
    assert checks['db.connect']['status'] == 'WARN'
    assert checks['db.tables']['status'] == 'SKIP'


def test_walltime_unset_is_warning(tmp_path, config):
    del config['pdr']['max_walltime_s']
    rc, _, checks, _ = _run(tmp_path, config)
    assert rc == 0
    assert checks['run.walltime']['status'] == 'WARN'


def test_disk_threshold_warns(tmp_path, config):
    rc, _, checks, _ = _run(tmp_path, config, min_free_gb=1e9)
    assert rc == 0
    assert checks['disk.free']['status'] == 'WARN'


def test_dirty_version_is_warning(tmp_path, config, install):
    (install / 'pdrexe').write_text('#!/bin/sh\necho "Running version: v1-2-gabc-dirty"\n')
    rc, _, checks, _ = _run(tmp_path, config)
    assert rc == 0
    assert checks['kt.pdr_version']['status'] == 'WARN'
    assert 'dirty' in checks['kt.pdr_version']['detail'].lower()


def test_secrets_never_printed(tmp_path, config, monkeypatch):
    config['database'] = {'type': 'mysql', 'host': '127.0.0.1', 'port': 1, 'database': 'nodb',
                          'username': SECRET_USER, 'password': SECRET_DB}
    config['storage'] = {'type': 'sftp', 'host': '127.0.0.1', 'port': 1,
                         'username': SECRET_USER, 'password': SECRET_ST, 'base_dir': '/tmp'}
    monkeypatch.setenv('PDR_DB_PASSWORD', SECRET_DB + '-env')
    monkeypatch.setenv('PDR_STORAGE_PASSWORD', SECRET_ST + '-env')
    monkeypatch.setenv('PDR_DB_USERNAME', SECRET_USER)
    for as_json in (False, True):
        out = io.StringIO()
        rc = run_preflight(config_path=_write(tmp_path, config), json_output=as_json, out=out,
                           min_free_gb=0.0, timeout=1.0)
        text = out.getvalue()
        assert rc == 1
        for secret in (SECRET_DB, SECRET_ST, SECRET_USER):
            assert secret not in text
        assert 'DB password set (env)' in text
        assert '(values never printed)' in text


def test_secret_in_exception_is_scrubbed(tmp_path, config, monkeypatch):
    def boom(ctx):
        raise RuntimeError(f"connect failed for mysql://u:{SECRET_DB}@host/db")
    monkeypatch.setattr(preflight, 'check_walltime', boom)
    monkeypatch.setattr(preflight, 'CHECKS', [(n, boom if n == 'run.walltime' else f)
                                              for n, f in preflight.CHECKS])
    config['database'] = {'type': 'sqlite', 'path': config['database']['path'], 'password': SECRET_DB}
    rc, _, checks, text = _run(tmp_path, config)
    assert checks['run.walltime']['status'] == 'FAIL'
    assert 'check crashed' in checks['run.walltime']['detail']
    assert SECRET_DB not in text
    assert checks['db.connect']['status'] == 'PASS'   # isolation


def test_env_override_ignored_note(tmp_path, config, monkeypatch):
    monkeypatch.setenv('PDR_STORAGE_DIR', '/somewhere/else')
    monkeypatch.setenv('PDR_DB_TYPE', 'sqlite')
    rc, _, checks, _ = _run(tmp_path, config)
    assert checks['config.env']['status'] == 'WARN'
    assert 'PDR_STORAGE_DIR' in checks['config.env']['detail'].split('IGNORED')[1]
    assert 'PDR_DB_TYPE' in checks['config.env']['detail'].split('IGNORED')[0]


def test_config_file_errors(tmp_path):
    out = io.StringIO()
    rc = run_preflight(config_path=str(tmp_path / 'nope.yaml'), json_output=True, out=out)
    assert rc == 1 and json.loads(out.getvalue())['checks'][0]['status'] == 'FAIL'
    bad = tmp_path / 'bad.yaml'
    bad.write_text('database: [unclosed\n')
    out = io.StringIO()
    rc = run_preflight(config_path=str(bad), json_output=True, out=out)
    assert rc == 1 and 'YAML error' in json.loads(out.getvalue())['checks'][0]['detail']


def test_unknown_section_fails(tmp_path, config):
    config['databse'] = {}
    rc, _, checks, _ = _run(tmp_path, config)
    assert rc == 1 and checks['config.sections']['status'] == 'FAIL'


def test_template_trailing_comma_and_unresolved_placeholder(tmp_path, config, install):
    tpl = install / 'templates' / 'pdr_config.json.template'
    text = tpl.read_text()
    tpl.write_text(text.replace('"metallicity"', '"x": 1,\n  "metallicity"', 1)
                   .replace('KT_VARzmetal_', 'KT_VARzmetal_,'))
    rc, _, checks, _ = _run(tmp_path, config)
    assert checks['tpl.json']['status'] == 'PASS' and 'trailing comma' in checks['tpl.json']['detail']
    tpl.write_text(text.replace('KT_VARzmetal_', 'KT_VARnosuchparam_'))
    rc, _, checks, _ = _run(tmp_path, config)
    assert rc == 1 and 'KT_VARnosuchparam_' in checks['tpl.json']['detail']
    tpl.write_text('{ "a": KT_VARzmetal_ ')          # broken JSON
    rc, _, checks, _ = _run(tmp_path, config)
    assert rc == 1 and 'not parseable' in checks['tpl.json']['detail']


def test_strip_json_comments():
    src = '{"a": 1, // c1\n "b": "x//y", / c2\n "c": 2 }\n'
    assert json.loads(strip_json_comments(src)) == {'a': 1, 'b': 'x//y', 'c': 2}
    assert json.loads(strip_json_comments('{"a": 1 ! bang\n}', bang=True)) == {'a': 1}


def test_missing_data_files(tmp_path, config, install):
    (install / 'pdrinpdata' / 'binding_energies.dist').unlink()
    (install / 'pdrinpdata' / 'chem_test.dat').unlink()
    (install / 'onioninpdata' / 'ONION3.INP.CO').unlink()
    rc, _, checks, _ = _run(tmp_path, config)
    assert rc == 1
    for name in ('tpl.binding_energies', 'tpl.chem_network', 'post.onion'):
        assert checks[name]['status'] == 'FAIL'


def test_fuv_file_required_for_type_6(tmp_path, config, install):
    config['model_parameters']['ifuvtype'] = 6
    config['model_parameters']['fuvstring'] = 'nofile.fuv'
    rc, _, checks, _ = _run(tmp_path, config)
    assert rc == 1 and checks['tpl.fuv_file']['status'] == 'FAIL'
    (install / 'pdrinpdata' / 'nofile.fuv').write_text('x')
    rc, _, checks, _ = _run(tmp_path, config)
    assert checks['tpl.fuv_file']['status'] == 'PASS'


def test_uv_continuum_and_simline_enabled_but_missing(tmp_path, config):
    config['uv_continuum'] = {'enabled': True, 'kosma_tau_dir': str(tmp_path / 'nokt')}
    config['simline'] = {'enabled': True, 'simline_dir': str(tmp_path / 'nosim')}
    rc, _, checks, _ = _run(tmp_path, config)
    assert rc == 1
    assert checks['post.uv_continuum']['status'] == 'FAIL'
    assert checks['post.simline']['status'] == 'FAIL'


def test_uv_continuum_ok_with_fake_package(tmp_path, config):
    pytest.importorskip('h5py')
    pytest.importorskip('numpy')
    kt = tmp_path / 'kt'
    pkg = kt / 'h2py' / 'kosma_h2'
    pkg.mkdir(parents=True)
    (kt / 'h2py' / 'postprocess_uv_continuum.py').write_text('')
    (pkg / '__init__.py').write_text('')
    (pkg / 'spectra.py').write_text('')
    (pkg / 'atomic_data.py').write_text(
        f"def get_data_dir():\n    return {str(kt / 'h2py' / 'data')!r}\n")
    (kt / 'h2py' / 'data').mkdir()
    config['uv_continuum'] = {'enabled': True, 'kosma_tau_dir': str(kt),
                              'python_executable': sys.executable}
    rc, _, checks, _ = _run(tmp_path, config)
    assert checks['post.uv_continuum']['status'] == 'FAIL'
    assert 'uvh2b29.dat' in checks['post.uv_continuum']['detail']
    (kt / 'h2py' / 'data' / 'uvh2b29.dat').write_text('x')
    (kt / 'h2py' / 'data' / 'uvh2c29.dat').write_text('x')
    rc, _, checks, _ = _run(tmp_path, config)
    assert checks['post.uv_continuum']['status'] == 'PASS', checks['post.uv_continuum']


def test_simline_ok(tmp_path, config):
    sim = tmp_path / 'simline'
    (sim / 'python').mkdir(parents=True)
    (sim / 'bin').mkdir()
    (sim / 'molecules').mkdir()
    (sim / 'python' / 'run_simline.py').write_text('')
    (sim / 'python' / 'simline_config.json').write_text('{ // c\n "species": ["CO"] ! c2\n}')
    (sim / 'obs.template').write_text('')
    exe = sim / 'bin' / 'simline'
    exe.write_text('#!/bin/sh\n')
    exe.chmod(0o755)
    config['simline'] = {'enabled': True, 'simline_dir': str(sim)}
    rc, _, checks, _ = _run(tmp_path, config)
    assert checks['post.simline']['status'] == 'PASS', checks['post.simline']


def test_cli_entry_point(tmp_path, config, monkeypatch, capsys):
    from pdr_run.cli import runner
    monkeypatch.setattr(sys, 'argv', ['pdr_run', '--check', '--config', _write(tmp_path, config),
                                      '--min-free-gb', '0', '--check-timeout', '1'])
    with pytest.raises(SystemExit) as exc:
        runner.main()
    assert exc.value.code == 0
    out = capsys.readouterr().out
    assert out.startswith('[') and 'Summary:' in out
    assert 'PDR RUN STARTED' not in out               # no start-up log flood


def test_cli_check_json_exit_code(tmp_path, config, install, monkeypatch, capsys):
    from pdr_run.cli import runner
    (install / 'pdrexe').unlink()
    monkeypatch.setattr(sys, 'argv', ['pdr_run', '--check-json', '--config', _write(tmp_path, config),
                                      '--min-free-gb', '0'])
    with pytest.raises(SystemExit) as exc:
        runner.main()
    assert exc.value.code == 1
    data = json.loads(capsys.readouterr().out)
    assert data['ok'] is False and data['summary']['FAIL'] >= 1
