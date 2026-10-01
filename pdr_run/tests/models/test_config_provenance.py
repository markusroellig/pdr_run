"""Full-config provenance on pdr_model_jobs (config_json, template_sha256)."""

import hashlib
import json
import logging
import os

import pytest
from sqlalchemy import create_engine, inspect, text

from pdr_run.models import kosma_tau
from pdr_run.models.kosma_tau import create_json_from_job_id

# comment, trailing comma and nested sections, as in the shipped templates
TEMPLATE = '''{
  // a comment json-fortran accepts
  "physical_params": {"surface_density": KT_VARxnsur_, "metallicity": KT_VARzmetal_,},
  "h2": {"gas_seed_h3p_shape": 1, "network": "KT_VARCHEM_DATABASE_FILE_"},
  "species": ["CO", "HCO18O+"],
}
'''
NAME = 'prov.template'


@pytest.fixture
def workdir(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    (tmp_path / NAME).write_text(TEMPLATE)
    return tmp_path


def _render(job, db_session):
    from pdr_run.database.models import KOSMAtauParameters
    params = db_session.get(KOSMAtauParameters, job.kosmatau_parameters_id)
    params.xnsur, params.zmetal = 1.0e3, 1.0
    db_session.commit()
    create_json_from_job_id(job.id, session=db_session,
                            config={'pdr': {'json_template_file': NAME}})
    db_session.refresh(job)


def test_columns_added_on_old_schema():
    from pdr_run.database.base import Base
    import pdr_run.database.models  # noqa: F401
    from pdr_run.database.db_manager import ensure_additive_columns
    engine = create_engine('sqlite://')
    Base.metadata.create_all(engine)
    with engine.begin() as conn:
        conn.execute(text('ALTER TABLE pdr_model_jobs DROP COLUMN config_json'))
        conn.execute(text('ALTER TABLE pdr_model_jobs DROP COLUMN template_sha256'))
    ensure_additive_columns(engine)
    cols = {c['name'] for c in inspect(engine).get_columns('pdr_model_jobs')}
    assert {'config_json', 'template_sha256'} <= cols


def test_config_json_equals_parsed_rendered_file(workdir, make_job, db_session):
    job = make_job()
    _render(job, db_session)
    with open(workdir / 'pdr_config.json') as fh:
        rendered = fh.read()
    from pdr_run.cli.preflight import strip_json_comments
    import re
    expected = json.loads(re.sub(r',(\s*[}\]])', r'\1', strip_json_comments(rendered)))
    assert isinstance(job.config_json, dict)
    assert job.config_json == expected
    assert job.config_json['h2']['network'] == 'chem_rates.dat'
    assert 'KT_VAR' not in json.dumps(job.config_json)


def test_template_hash(workdir, make_job, db_session):
    job = make_job()
    _render(job, db_session)
    assert job.template_sha256 == hashlib.sha256(TEMPLATE.encode()).hexdigest()


def test_malformed_config_gives_null_and_warning(workdir, make_job, db_session, caplog):
    (workdir / NAME).write_text('{"a": KT_VARxnsur_ "b": [}')
    job = make_job()
    with caplog.at_level(logging.WARNING, logger='dev'):
        _render(job, db_session)          # must not raise: the run continues
    assert job.config_json is None
    assert job.template_sha256 == hashlib.sha256(b'{"a": KT_VARxnsur_ "b": [}').hexdigest()
    assert any('not parseable' in r.getMessage() and r.levelno == logging.WARNING
               for r in caplog.records)
    assert os.path.isfile(workdir / 'pdr_config.json')


def test_json_query_on_sqlite(workdir, make_job, db_session):
    from pdr_run.database.models import PDRModelJob
    job = make_job()
    _render(job, db_session)
    got = db_session.query(PDRModelJob.id).filter(
        PDRModelJob.config_json['h2']['gas_seed_h3p_shape'].as_integer() == 1).all()
    assert [r[0] for r in got] == [job.id]
    net = db_session.execute(text(
        "select json_extract(config_json, '$.h2.network') from pdr_model_jobs")).scalar()
    assert net == 'chem_rates.dat'
