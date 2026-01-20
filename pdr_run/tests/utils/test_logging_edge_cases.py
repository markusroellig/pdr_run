"""
Edge case and security tests for logging utilities.

This test module validates that the sanitization functions handle
various edge cases, bypass attempts, and security scenarios correctly.
"""

import pytest
from pdr_run.utils.logging import (
    sanitize_config,
    sanitize_connection_string,
    sanitize_yaml_content,
)


class TestBypassAttempts:
    """Test attempts to bypass sanitization."""

    def test_uppercase_password_field(self):
        """Test that uppercase PASSWORD is caught."""
        config = {'PASSWORD': 'secret123', 'PASSWD': 'secret456'}
        sanitized = sanitize_config(config)
        assert sanitized['PASSWORD'] == '***'
        assert sanitized['PASSWD'] == '***'

    def test_mixed_case_sensitive_fields(self):
        """Test mixed case variations of sensitive fields."""
        config = {
            'PaSsWoRd': 'secret1',
            'UsErNaMe': 'admin',
            'Api_Key': 'key123',
            'SECRET': 'topsecret'
        }
        sanitized = sanitize_config(config)
        assert sanitized['PaSsWoRd'] == '***'
        assert sanitized['UsErNaMe'] == '***'
        assert sanitized['Api_Key'] == '***'
        assert sanitized['SECRET'] == '***'

    def test_password_in_nested_key_name(self):
        """Test that fields containing 'password' are caught."""
        config = {
            'db_password': 'secret1',
            'admin_password': 'secret2',
            'password_hash': 'secret3',
            'my_password_field': 'secret4'
        }
        sanitized = sanitize_config(config)
        assert sanitized['db_password'] == '***'
        assert sanitized['admin_password'] == '***'
        assert sanitized['password_hash'] == '***'
        assert sanitized['my_password_field'] == '***'

    def test_deeply_nested_passwords(self):
        """Test deeply nested password structures."""
        config = {
            'level1': {
                'level2': {
                    'level3': {
                        'level4': {
                            'password': 'deepSecret'
                        }
                    }
                }
            }
        }
        sanitized = sanitize_config(config)
        assert sanitized['level1']['level2']['level3']['level4']['password'] == '***'

    def test_password_in_list_of_nested_dicts(self):
        """Test passwords in complex nested structures."""
        config = {
            'servers': [
                {
                    'name': 'server1',
                    'auth': {
                        'username': 'user1',
                        'password': 'pass1'
                    }
                },
                {
                    'name': 'server2',
                    'auth': {
                        'username': 'user2',
                        'password': 'pass2'
                    }
                }
            ]
        }
        sanitized = sanitize_config(config)
        assert sanitized['servers'][0]['auth']['password'] == '***'
        assert sanitized['servers'][0]['auth']['username'] == '***'
        assert sanitized['servers'][1]['auth']['password'] == '***'
        assert sanitized['servers'][1]['auth']['username'] == '***'

    def test_special_characters_in_password(self):
        """Test that special characters in passwords are properly masked."""
        config = {
            'password': 'p@$$w0rd!#%^&*()',
            'token': 'tok€n-with-ünïcode'
        }
        sanitized = sanitize_config(config)
        assert sanitized['password'] == '***'
        assert sanitized['token'] == '***'
        # Original should not be modified
        assert config['password'] == 'p@$$w0rd!#%^&*()'


class TestConnectionStringEdgeCases:
    """Test edge cases for connection string sanitization."""

    def test_complex_mysql_url(self):
        """Test complex MySQL URL with options."""
        conn = 'mysql+pymysql://user:p@ssw0rd@db.host.com:3306/database?charset=utf8mb4'
        sanitized = sanitize_connection_string(conn)
        assert 'p@ssw0rd' not in sanitized
        assert '***' in sanitized
        assert 'db.host.com' in sanitized

    def test_postgresql_with_special_chars(self):
        """Test PostgreSQL URL with special characters in password."""
        conn = 'postgresql://user:p@ss:w0rd!@host/db'
        sanitized = sanitize_connection_string(conn)
        assert 'p@ss:w0rd!' not in sanitized
        assert '***' in sanitized

    def test_url_with_port_and_query(self):
        """Test URL with port and query parameters."""
        conn = 'postgresql://admin:secret@localhost:5432/mydb?sslmode=require'
        sanitized = sanitize_connection_string(conn)
        assert 'secret' not in sanitized
        assert '***' in sanitized
        assert 'sslmode=require' in sanitized

    def test_empty_password(self):
        """Test connection string with empty password."""
        conn = 'mysql://user:@localhost/db'
        sanitized = sanitize_connection_string(conn)
        # Should still work, might replace empty string
        assert 'mysql://user' in sanitized

    def test_url_without_password(self):
        """Test URL that doesn't have password section."""
        conn = 'sqlite:///path/to/database.db'
        sanitized = sanitize_connection_string(conn)
        assert sanitized == conn  # Should be unchanged

    def test_multiple_at_symbols(self):
        """Test URL with @ in password (edge case)."""
        conn = 'mysql://user:p@ss@host/db'
        sanitized = sanitize_connection_string(conn)
        # Should mask the password part
        assert 'p@ss' not in sanitized


class TestYAMLEdgeCases:
    """Test edge cases for YAML content sanitization."""

    def test_yaml_with_colons_in_password(self):
        """Test YAML where password value contains colons."""
        yaml_content = '''
database:
  password: "pass:with:colons"
  host: localhost
'''
        sanitized = sanitize_yaml_content(yaml_content)
        assert 'pass:with:colons' not in sanitized
        assert '***' in sanitized

    def test_yaml_with_quotes_in_password(self):
        """Test YAML with quotes in password."""
        yaml_content = '''password: "pass\\"word"'''
        sanitized = sanitize_yaml_content(yaml_content)
        assert 'pass\\"word' not in sanitized

    def test_yaml_multiline_config(self):
        """Test multiline YAML with multiple passwords."""
        yaml_content = '''
database:
  type: mysql
  host: db.server.com
  username: dbadmin
  password: db_secret_123
  port: 3306

storage:
  type: sftp
  host: storage.server.com
  username: storageuser
  password: storage_secret_456

api:
  endpoint: https://api.example.com
  api_key: api_secret_789
  timeout: 30
'''
        sanitized = sanitize_yaml_content(yaml_content)
        assert 'db_secret_123' not in sanitized
        assert 'storage_secret_456' not in sanitized
        assert 'api_secret_789' not in sanitized
        assert sanitized.count('***') >= 3

    def test_yaml_inline_dict(self):
        """Test YAML inline dictionary format."""
        yaml_content = 'auth: {username: admin, password: secret}'
        sanitized = sanitize_yaml_content(yaml_content)
        # This might not be fully sanitized due to inline format
        # but password field should still be caught
        assert 'password: secret' not in sanitized or 'password: ***' in sanitized


class TestSecurityScenarios:
    """Test real-world security scenarios."""

    def test_config_with_sql_injection_attempt(self):
        """Test that SQL injection attempts in passwords are masked."""
        config = {
            'password': "'; DROP TABLE users; --",
            'host': 'localhost'
        }
        sanitized = sanitize_config(config)
        assert sanitized['password'] == '***'
        assert '; DROP TABLE users; --' not in str(sanitized)

    def test_config_with_path_traversal_attempt(self):
        """Test that path traversal attempts in passwords are masked."""
        config = {
            'password': '../../../etc/passwd',
            'path': '/safe/path'
        }
        sanitized = sanitize_config(config)
        assert sanitized['password'] == '***'
        assert '../../../etc/passwd' not in str(sanitized)

    def test_config_original_not_modified(self):
        """Verify that sanitization doesn't modify the original config."""
        original = {
            'password': 'secret123',
            'database': {
                'password': 'nested_secret'
            }
        }
        import copy
        original_copy = copy.deepcopy(original)

        sanitized = sanitize_config(original)

        # Original should be unchanged
        assert original == original_copy
        assert original['password'] == 'secret123'
        assert original['database']['password'] == 'nested_secret'

        # Sanitized should be different
        assert sanitized['password'] == '***'
        assert sanitized['database']['password'] == '***'

    def test_logging_with_very_long_password(self):
        """Test sanitization of very long passwords."""
        config = {
            'password': 'a' * 10000,  # Very long password
            'host': 'localhost'
        }
        sanitized = sanitize_config(config)
        assert sanitized['password'] == '***'
        assert len(str(sanitized)) < 100  # Should be short

    def test_config_with_unicode_password(self):
        """Test Unicode characters in passwords."""
        config = {
            'password': '密码🔐パスワード',
            'host': 'localhost'
        }
        sanitized = sanitize_config(config)
        assert sanitized['password'] == '***'

    def test_config_with_null_bytes(self):
        """Test passwords with null bytes."""
        config = {
            'password': 'pass\x00word',
            'host': 'localhost'
        }
        sanitized = sanitize_config(config)
        assert sanitized['password'] == '***'


class TestDataIntegrity:
    """Test that non-sensitive data is preserved correctly."""

    def test_port_numbers_preserved(self):
        """Test that port numbers are not sanitized."""
        config = {
            'host': 'localhost',
            'port': 3306,
            'password': 'secret'
        }
        sanitized = sanitize_config(config)
        assert sanitized['port'] == 3306
        assert sanitized['host'] == 'localhost'

    def test_boolean_values_preserved(self):
        """Test that boolean values are preserved."""
        config = {
            'use_ssl': True,
            'verify_cert': False,
            'password': 'secret'
        }
        sanitized = sanitize_config(config)
        assert sanitized['use_ssl'] is True
        assert sanitized['verify_cert'] is False

    def test_none_values_preserved(self):
        """Test that None values are handled correctly."""
        config = {
            'password': None,
            'host': 'localhost',
            'api_key': None
        }
        sanitized = sanitize_config(config)
        assert sanitized['password'] == '***'  # None is still masked
        assert sanitized['host'] == 'localhost'
        assert sanitized['api_key'] == '***'

    def test_numeric_values_preserved(self):
        """Test that numeric values are not affected."""
        config = {
            'timeout': 30,
            'max_connections': 100,
            'retry_delay': 1.5,
            'password': 'secret'
        }
        sanitized = sanitize_config(config)
        assert sanitized['timeout'] == 30
        assert sanitized['max_connections'] == 100
        assert sanitized['retry_delay'] == 1.5

    def test_lists_of_non_dicts_preserved(self):
        """Test that lists of non-dict values are preserved."""
        config = {
            'allowed_hosts': ['host1', 'host2', 'host3'],
            'ports': [3306, 5432, 27017],
            'password': 'secret'
        }
        sanitized = sanitize_config(config)
        assert sanitized['allowed_hosts'] == ['host1', 'host2', 'host3']
        assert sanitized['ports'] == [3306, 5432, 27017]
