"""Both 'model_params' (canonical) and 'model_parameters' (README alias) carry
the grid parameters into engine.create_database_entries (smoke-test finding:
KeyError 'parameters' with model_parameters)."""

import datetime
from unittest.mock import MagicMock, patch

import pytest

from pdr_run.core.engine import create_database_entries


def _run(config):
    db = MagicMock()
    db.get_session.return_value = MagicMock()
    ids = [MagicMock(id=1) for _ in range(4)] + [MagicMock(id=42)]
    with patch('pdr_run.core.engine.get_db_manager', return_value=db), \
         patch('pdr_run.core.engine.get_or_create', side_effect=ids), \
         patch('pdr_run.core.engine.get_model_name_id', return_value=1), \
         patch('pdr_run.core.engine.get_code_revision', return_value='rev'), \
         patch('pdr_run.core.engine.get_compilation_date',
               return_value=datetime.datetime.now()), \
         patch('pdr_run.core.engine.get_digest', return_value='deadbeef'), \
         patch('os.path.exists', return_value=True):
        return create_database_entries(
            model_name='m', model_path='/tmp/m',
            param_combinations=[('100', '3.0', '1.0', '1.0')], config=config)


@pytest.mark.parametrize('key', ['model_params', 'model_parameters'])
def test_both_keys_are_accepted(key):
    config = {'pdr': {'base_dir': '/tmp'}, key: {'alpha': 1.5, 'rcore': 0.2, 'species': ['CO']}}
    _run(config)
    assert config['parameters']['alpha'] == 1.5


def test_model_params_wins_over_alias():
    config = {'pdr': {'base_dir': '/tmp'}, 'model_params': {'alpha': 1.5},
              'model_parameters': {'alpha': 9.0}}
    _run(config)
    assert config['parameters']['alpha'] == 1.5
