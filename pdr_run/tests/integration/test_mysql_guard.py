"""The destructive MySQL integration suite (DROP DATABASE / DROP USER) must be
unable to touch a production database. These tests never connect to MySQL."""

import pytest

from pdr_run.tests.integration import test_mysql_integration as t


def test_refused_without_opt_in(monkeypatch):
    monkeypatch.delenv(t.ALLOW_DESTRUCTIVE_ENV, raising=False)
    with pytest.raises(RuntimeError, match='PDR_ALLOW_DESTRUCTIVE_DB_TESTS'):
        t.require_destructive_ok('test_pdr_abc')


@pytest.mark.parametrize('name', ['pdr_grid1', 'pdr_test_abc', 'production', ''])
def test_refused_for_non_test_database_even_with_opt_in(monkeypatch, name):
    monkeypatch.setenv(t.ALLOW_DESTRUCTIVE_ENV, '1')
    with pytest.raises(RuntimeError, match='database name'):
        t.require_destructive_ok(name)


def test_refused_for_production_user(monkeypatch):
    monkeypatch.setenv(t.ALLOW_DESTRUCTIVE_ENV, '1')
    with pytest.raises(RuntimeError, match='user name'):
        t.require_destructive_ok('test_pdr_abc', user_name='pdr_user')


def test_allowed_for_test_names_with_opt_in(monkeypatch):
    monkeypatch.setenv(t.ALLOW_DESTRUCTIVE_ENV, '1')
    t.require_destructive_ok('test_pdr_abc')


def test_suite_creation_and_cleanup_are_guarded(monkeypatch):
    monkeypatch.delenv(t.ALLOW_DESTRUCTIVE_ENV, raising=False)
    suite = t.MySQLIntegrationTest()
    assert suite.test_db_name.startswith('test_')
    for call in (suite.create_test_database, suite.cleanup_test_database, suite.run_all_tests):
        with pytest.raises(RuntimeError):
            call()
