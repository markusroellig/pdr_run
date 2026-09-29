"""Fixtures shared by pdr_run.models.* tests.

Reuses the ``db_session`` fixture defined in the top-level
``pdr_run/tests/conftest.py`` (in-memory SQLite, ``Base.metadata.create_all``).
"""

import pytest

from pdr_run.database.models import (
    ModelNames, KOSMAtauExecutable, KOSMAtauParameters, ChemicalDatabase,
    PDRModelJob,
)


@pytest.fixture
def make_job(db_session):
    """Factory fixture: build a minimal-but-complete PDRModelJob row graph.

    Returns a callable ``make_job(**overrides)`` -> PDRModelJob, committed
    to ``db_session``. ``overrides`` are passed straight to the
    ``PDRModelJob`` constructor (e.g. ``onion_species='CO'``).
    """
    def _make(**overrides):
        model_name = ModelNames(model_name='testmodel', model_path='/tmp/testmodel')
        exe = KOSMAtauExecutable(executable_file_name='pdrexe')
        chem_db = ChemicalDatabase(chem_rates_file_name='chem_rates.dat')
        db_session.add_all([model_name, exe, chem_db])
        db_session.commit()

        params = KOSMAtauParameters(model_name_id=model_name.id)
        db_session.add(params)
        db_session.commit()

        kwargs = dict(
            model_name_id=model_name.id,
            model_job_name='j001',
            kosmatau_parameters_id=params.id,
            kosmatau_executable_id=exe.id,
            chemical_database_id=chem_db.id,
            onion_species='',
        )
        kwargs.update(overrides)
        job = PDRModelJob(**kwargs)
        db_session.add(job)
        db_session.commit()
        return job
    return _make
