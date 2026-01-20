"""
Unit tests for logging utilities that handle sensitive data sanitization.

This test module ensures that password, API keys, and other sensitive
credentials are properly masked before being written to logs.
"""

import pytest
import logging
from pdr_run.utils.logging import (
    is_sensitive_field,
    sanitize_config,
    sanitize_connection_string,
    sanitize_yaml_content,
    get_password_status,
    log_config_safely,
    DEFAULT_SENSITIVE_FIELDS
)


class TestIsSensitiveField:
    """Test suite for is_sensitive_field function."""

    def test_password_field(self):
        """Test that password field is detected as sensitive."""
        assert is_sensitive_field('password') is True
        assert is_sensitive_field('PASSWORD') is True
        assert is_sensitive_field('Password') is True

    def test_username_field(self):
        """Test that username field is detected as sensitive."""
        assert is_sensitive_field('username') is True
        assert is_sensitive_field('user') is True
        assert is_sensitive_field('USERNAME') is True

    def test_api_key_field(self):
        """Test that API key fields are detected as sensitive."""
        assert is_sensitive_field('api_key') is True
        assert is_sensitive_field('apikey') is True
        assert is_sensitive_field('API_KEY') is True

    def test_secret_field(self):
        """Test that secret fields are detected as sensitive."""
        assert is_sensitive_field('secret') is True
        assert is_sensitive_field('SECRET') is True
        assert is_sensitive_field('client_secret') is True

    def test_token_field(self):
        """Test that token fields are detected as sensitive."""
        assert is_sensitive_field('token') is True
        assert is_sensitive_field('auth_token') is True
        assert is_sensitive_field('TOKEN') is True

    def test_non_sensitive_field(self):
        """Test that non-sensitive fields are not flagged."""
        assert is_sensitive_field('host') is False
        assert is_sensitive_field('port') is False
        assert is_sensitive_field('database') is False
        assert is_sensitive_field('timeout') is False

    def test_custom_sensitive_fields(self):
        """Test using custom list of sensitive fields."""
        custom_fields = ['custom_secret', 'private_key']
        assert is_sensitive_field('custom_secret', custom_fields) is True
        assert is_sensitive_field('private_key', custom_fields) is True
        assert is_sensitive_field('password', custom_fields) is False


class TestSanitizeConfig:
    """Test suite for sanitize_config function."""

    def test_flat_dict_with_password(self):
        """Test sanitization of flat dictionary with password."""
        config = {
            'host': 'localhost',
            'port': 3306,
            'password': 'secret123',
            'database': 'testdb'
        }
        sanitized = sanitize_config(config)
        assert sanitized['host'] == 'localhost'
        assert sanitized['port'] == 3306
        assert sanitized['password'] == '***'
        assert sanitized['database'] == 'testdb'

    def test_flat_dict_with_username(self):
        """Test sanitization of flat dictionary with username."""
        config = {
            'username': 'admin',
            'host': 'localhost'
        }
        sanitized = sanitize_config(config)
        assert sanitized['username'] == '***'
        assert sanitized['host'] == 'localhost'

    def test_nested_dict(self):
        """Test sanitization of nested dictionaries."""
        config = {
            'database': {
                'host': 'localhost',
                'password': 'secret123',
                'port': 3306
            },
            'storage': {
                'type': 'sftp',
                'password': 'storage_pass'
            }
        }
        sanitized = sanitize_config(config)
        assert sanitized['database']['host'] == 'localhost'
        assert sanitized['database']['password'] == '***'
        assert sanitized['database']['port'] == 3306
        assert sanitized['storage']['type'] == 'sftp'
        assert sanitized['storage']['password'] == '***'

    def test_list_of_dicts(self):
        """Test sanitization of lists containing dictionaries."""
        config = {
            'servers': [
                {'host': 'server1', 'password': 'pass1'},
                {'host': 'server2', 'password': 'pass2'}
            ]
        }
        sanitized = sanitize_config(config)
        assert sanitized['servers'][0]['host'] == 'server1'
        assert sanitized['servers'][0]['password'] == '***'
        assert sanitized['servers'][1]['host'] == 'server2'
        assert sanitized['servers'][1]['password'] == '***'

    def test_api_key_sanitization(self):
        """Test that API keys are sanitized."""
        config = {
            'api_key': 'abc123xyz',
            'host': 'api.example.com'
        }
        sanitized = sanitize_config(config)
        assert sanitized['api_key'] == '***'
        assert sanitized['host'] == 'api.example.com'

    def test_custom_mask(self):
        """Test using a custom mask string."""
        config = {'password': 'secret123'}
        sanitized = sanitize_config(config, mask='REDACTED')
        assert sanitized['password'] == 'REDACTED'

    def test_empty_dict(self):
        """Test sanitization of empty dictionary."""
        config = {}
        sanitized = sanitize_config(config)
        assert sanitized == {}

    def test_none_value(self):
        """Test sanitization when None is passed."""
        result = sanitize_config(None)
        assert result is None

    def test_non_dict_value(self):
        """Test sanitization when non-dict is passed."""
        result = sanitize_config("not a dict")
        assert result == "not a dict"

    def test_case_insensitive_matching(self):
        """Test that field matching is case-insensitive."""
        config = {
            'PASSWORD': 'secret1',
            'Password': 'secret2',
            'password': 'secret3',
            'API_KEY': 'key1',
            'apikey': 'key2'
        }
        sanitized = sanitize_config(config)
        assert sanitized['PASSWORD'] == '***'
        assert sanitized['Password'] == '***'
        assert sanitized['password'] == '***'
        assert sanitized['API_KEY'] == '***'
        assert sanitized['apikey'] == '***'


class TestSanitizeConnectionString:
    """Test suite for sanitize_connection_string function."""

    def test_mysql_connection_string(self):
        """Test sanitization of MySQL connection string."""
        conn = 'mysql://user:secret123@localhost:3306/database'
        sanitized = sanitize_connection_string(conn)
        assert 'secret123' not in sanitized
        assert '***' in sanitized
        assert 'mysql://user:***@localhost:3306/database' == sanitized

    def test_postgresql_connection_string(self):
        """Test sanitization of PostgreSQL connection string."""
        conn = 'postgresql://admin:mypassword@db.example.com:5432/mydb'
        sanitized = sanitize_connection_string(conn)
        assert 'mypassword' not in sanitized
        assert '***' in sanitized
        assert 'postgresql://admin:***@db.example.com:5432/mydb' == sanitized

    def test_simple_user_password_host(self):
        """Test sanitization of simple user:password@host format."""
        conn = 'user:password@host'
        sanitized = sanitize_connection_string(conn)
        assert 'password' not in sanitized or sanitized == 'user:***@host'
        assert '***' in sanitized

    def test_custom_mask(self):
        """Test using a custom mask string."""
        conn = 'mysql://user:secret@localhost/db'
        sanitized = sanitize_connection_string(conn, mask='HIDDEN')
        assert 'secret' not in sanitized
        assert 'HIDDEN' in sanitized

    def test_none_value(self):
        """Test sanitization when None is passed."""
        result = sanitize_connection_string(None)
        assert result is None

    def test_non_string_value(self):
        """Test sanitization when non-string is passed."""
        result = sanitize_connection_string(12345)
        assert result == 12345

    def test_connection_string_without_password(self):
        """Test connection string that doesn't contain a password."""
        conn = 'sqlite:///path/to/database.db'
        sanitized = sanitize_connection_string(conn)
        assert sanitized == conn


class TestSanitizeYamlContent:
    """Test suite for sanitize_yaml_content function."""

    def test_simple_yaml_password(self):
        """Test sanitization of simple YAML password field."""
        yaml_content = """
database:
  host: localhost
  password: secret123
  port: 3306
"""
        sanitized = sanitize_yaml_content(yaml_content)
        assert 'secret123' not in sanitized
        assert '***' in sanitized

    def test_quoted_password(self):
        """Test sanitization of quoted YAML password."""
        yaml_content = 'password: "secret123"'
        sanitized = sanitize_yaml_content(yaml_content)
        assert 'secret123' not in sanitized
        assert '***' in sanitized

    def test_single_quoted_password(self):
        """Test sanitization of single-quoted YAML password."""
        yaml_content = "password: 'secret123'"
        sanitized = sanitize_yaml_content(yaml_content)
        assert 'secret123' not in sanitized
        assert '***' in sanitized

    def test_api_key_in_yaml(self):
        """Test sanitization of API key in YAML."""
        yaml_content = """
api:
  api_key: abc123xyz
  endpoint: https://api.example.com
"""
        sanitized = sanitize_yaml_content(yaml_content)
        assert 'abc123xyz' not in sanitized
        assert '***' in sanitized

    def test_multiple_sensitive_fields(self):
        """Test sanitization of multiple sensitive fields."""
        yaml_content = """
database:
  username: admin
  password: secret123
storage:
  password: storage_pass
  token: auth_token_123
"""
        sanitized = sanitize_yaml_content(yaml_content)
        assert 'secret123' not in sanitized
        assert 'storage_pass' not in sanitized
        assert 'auth_token_123' not in sanitized
        assert sanitized.count('***') >= 3  # At least username, 2 passwords

    def test_custom_mask(self):
        """Test using a custom mask string."""
        yaml_content = 'password: secret123'
        sanitized = sanitize_yaml_content(yaml_content, mask='REDACTED')
        assert 'secret123' not in sanitized
        assert 'REDACTED' in sanitized

    def test_case_insensitive_yaml(self):
        """Test case-insensitive matching in YAML."""
        yaml_content = """
PASSWORD: secret1
Password: secret2
password: secret3
"""
        sanitized = sanitize_yaml_content(yaml_content)
        assert 'secret1' not in sanitized
        assert 'secret2' not in sanitized
        assert 'secret3' not in sanitized

    def test_none_value(self):
        """Test sanitization when None is passed."""
        result = sanitize_yaml_content(None)
        assert result is None

    def test_non_string_value(self):
        """Test sanitization when non-string is passed."""
        result = sanitize_yaml_content(12345)
        assert result == 12345


class TestGetPasswordStatus:
    """Test suite for get_password_status function."""

    def test_password_set(self):
        """Test status for a set password."""
        status = get_password_status('secret123')
        assert status == 'SET (9 chars)'

    def test_password_not_set(self):
        """Test status for None password."""
        status = get_password_status(None)
        assert status == 'NOT SET'

    def test_empty_string_password(self):
        """Test status for empty string password."""
        status = get_password_status('')
        assert status == 'NOT SET'

    def test_long_password(self):
        """Test status for a long password."""
        status = get_password_status('a' * 100)
        assert status == 'SET (100 chars)'

    def test_short_password(self):
        """Test status for a very short password."""
        status = get_password_status('x')
        assert status == 'SET (1 chars)'


class TestLogConfigSafely:
    """Test suite for log_config_safely function."""

    def test_basic_logging(self, caplog):
        """Test basic safe logging functionality."""
        logger = logging.getLogger('test')
        config = {
            'host': 'localhost',
            'password': 'secret123'
        }

        with caplog.at_level(logging.DEBUG, logger='test'):
            log_config_safely(logger, config, level=logging.DEBUG)

        # Check that password is masked in log
        assert 'secret123' not in caplog.text
        assert '***' in caplog.text
        assert 'localhost' in caplog.text

    def test_custom_message(self, caplog):
        """Test logging with custom message prefix."""
        logger = logging.getLogger('test')
        config = {'password': 'secret'}

        with caplog.at_level(logging.DEBUG, logger='test'):
            log_config_safely(logger, config, level=logging.DEBUG, message="Database config")

        assert 'Database config' in caplog.text
        assert '***' in caplog.text

    def test_different_log_levels(self, caplog):
        """Test logging at different levels."""
        logger = logging.getLogger('test')
        config = {'password': 'secret'}

        # Test INFO level
        with caplog.at_level(logging.INFO, logger='test'):
            log_config_safely(logger, config, level=logging.INFO)
            assert '***' in caplog.text

    def test_nested_config(self, caplog):
        """Test logging of nested configuration."""
        logger = logging.getLogger('test')
        config = {
            'database': {
                'password': 'db_secret'
            },
            'storage': {
                'password': 'storage_secret'
            }
        }

        with caplog.at_level(logging.DEBUG, logger='test'):
            log_config_safely(logger, config, level=logging.DEBUG)

        assert 'db_secret' not in caplog.text
        assert 'storage_secret' not in caplog.text
        assert '***' in caplog.text


class TestDefaultSensitiveFields:
    """Test that DEFAULT_SENSITIVE_FIELDS contains expected values."""

    def test_contains_password(self):
        """Test that default fields include password variants."""
        assert 'password' in DEFAULT_SENSITIVE_FIELDS
        assert 'passwd' in DEFAULT_SENSITIVE_FIELDS or 'pwd' in DEFAULT_SENSITIVE_FIELDS

    def test_contains_api_key(self):
        """Test that default fields include API key variants."""
        assert 'api_key' in DEFAULT_SENSITIVE_FIELDS or 'apikey' in DEFAULT_SENSITIVE_FIELDS

    def test_contains_secret(self):
        """Test that default fields include secret."""
        assert 'secret' in DEFAULT_SENSITIVE_FIELDS

    def test_contains_token(self):
        """Test that default fields include token."""
        assert 'token' in DEFAULT_SENSITIVE_FIELDS

    def test_contains_username(self):
        """Test that default fields include username variants."""
        assert 'username' in DEFAULT_SENSITIVE_FIELDS or 'user' in DEFAULT_SENSITIVE_FIELDS


class TestIntegrationScenarios:
    """Integration tests for real-world scenarios."""

    def test_database_config_scenario(self):
        """Test complete database configuration sanitization."""
        config = {
            'database': {
                'type': 'mysql',
                'host': 'db.example.com',
                'port': 3306,
                'username': 'admin',
                'password': 'super_secret_password',
                'database': 'production_db'
            }
        }
        sanitized = sanitize_config(config)

        # Check that sensitive data is masked
        assert sanitized['database']['password'] == '***'
        assert sanitized['database']['username'] == '***'

        # Check that non-sensitive data is preserved
        assert sanitized['database']['type'] == 'mysql'
        assert sanitized['database']['host'] == 'db.example.com'
        assert sanitized['database']['port'] == 3306
        assert sanitized['database']['database'] == 'production_db'

    def test_storage_config_scenario(self):
        """Test complete storage configuration sanitization."""
        config = {
            'storage': {
                'type': 'sftp',
                'host': 'storage.example.com',
                'username': 'storage_user',
                'password': 'storage_password_123',
                'base_dir': '/data/storage'
            }
        }
        sanitized = sanitize_config(config)

        # Check that sensitive data is masked
        assert sanitized['storage']['password'] == '***'
        assert sanitized['storage']['username'] == '***'

        # Check that non-sensitive data is preserved
        assert sanitized['storage']['type'] == 'sftp'
        assert sanitized['storage']['host'] == 'storage.example.com'
        assert sanitized['storage']['base_dir'] == '/data/storage'

    def test_full_yaml_config_scenario(self):
        """Test sanitization of complete YAML configuration file."""
        yaml_content = """
database:
  type: postgresql
  host: db.example.com
  port: 5432
  username: dbuser
  password: db_secret_123
  database: myapp

storage:
  type: sftp
  host: storage.example.com
  username: storageuser
  password: storage_secret_456
  base_dir: /data

api:
  endpoint: https://api.example.com
  api_key: abc123xyz789
  timeout: 30
"""
        sanitized = sanitize_yaml_content(yaml_content)

        # Check that all passwords and keys are masked
        assert 'db_secret_123' not in sanitized
        assert 'storage_secret_456' not in sanitized
        assert 'abc123xyz789' not in sanitized

        # Check that non-sensitive data is preserved
        assert 'db.example.com' in sanitized
        assert 'storage.example.com' in sanitized
        assert 'https://api.example.com' in sanitized
