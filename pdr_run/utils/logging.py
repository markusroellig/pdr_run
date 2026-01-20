"""
Logging utilities for safely handling sensitive data in log output.

This module provides functions to sanitize configuration dictionaries,
connection strings, and YAML content before logging to prevent exposure
of passwords, API keys, and other sensitive credentials.
"""

import re
import logging
from typing import Dict, Any, List, Optional


# Default list of sensitive field names (case-insensitive matching)
DEFAULT_SENSITIVE_FIELDS = [
    'password',
    'passwd',
    'pwd',
    'api_key',
    'apikey',
    'secret',
    'token',
    'username',
    'user',
]


def is_sensitive_field(field_name: str, sensitive_fields: Optional[List[str]] = None) -> bool:
    """
    Check if a field name is considered sensitive.

    Args:
        field_name: The field name to check
        sensitive_fields: Optional list of sensitive field names. If None, uses DEFAULT_SENSITIVE_FIELDS

    Returns:
        True if the field is sensitive, False otherwise
    """
    if sensitive_fields is None:
        sensitive_fields = DEFAULT_SENSITIVE_FIELDS

    field_lower = field_name.lower()
    return any(sensitive.lower() in field_lower for sensitive in sensitive_fields)


def sanitize_config(
    config_dict: Dict[str, Any],
    fields: Optional[List[str]] = None,
    mask: str = '***'
) -> Dict[str, Any]:
    """
    Recursively sanitize a configuration dictionary by masking sensitive values.

    Args:
        config_dict: Dictionary to sanitize
        fields: Optional list of sensitive field names. If None, uses DEFAULT_SENSITIVE_FIELDS
        mask: String to use for masking sensitive values

    Returns:
        New dictionary with sensitive values masked
    """
    if not isinstance(config_dict, dict):
        return config_dict

    if fields is None:
        fields = DEFAULT_SENSITIVE_FIELDS

    sanitized = {}
    for key, value in config_dict.items():
        if is_sensitive_field(key, fields):
            sanitized[key] = mask
        elif isinstance(value, dict):
            sanitized[key] = sanitize_config(value, fields, mask)
        elif isinstance(value, list):
            sanitized[key] = [
                sanitize_config(item, fields, mask) if isinstance(item, dict) else item
                for item in value
            ]
        else:
            sanitized[key] = value

    return sanitized


def sanitize_connection_string(conn_string: str, mask: str = '***') -> str:
    """
    Sanitize database connection strings by masking passwords.

    Handles various formats:
    - mysql://user:password@host:port/database
    - postgresql://user:password@host:port/database
    - user:password@host

    Args:
        conn_string: Connection string to sanitize
        mask: String to use for masking passwords

    Returns:
        Sanitized connection string
    """
    if not isinstance(conn_string, str):
        return conn_string

    # Pattern to match password in connection strings
    # First try: ://user:password@ format (for mysql://, postgresql://, etc.)
    result = re.sub(r'(://[^:@]+:)[^@]+(@)', r'\1' + mask + r'\2', conn_string)

    # Second try: simple user:password@host format (only if no :// found)
    if '://' not in result:
        result = re.sub(r'^([^:@]+:)[^@]+(@)', r'\1' + mask + r'\2', result)

    return result


def sanitize_yaml_content(
    yaml_string: str,
    fields: Optional[List[str]] = None,
    mask: str = '***'
) -> str:
    """
    Sanitize YAML content by masking sensitive field values.

    Args:
        yaml_string: Raw YAML content as string
        fields: Optional list of sensitive field names. If None, uses DEFAULT_SENSITIVE_FIELDS
        mask: String to use for masking sensitive values

    Returns:
        Sanitized YAML string
    """
    if not isinstance(yaml_string, str):
        return yaml_string

    if fields is None:
        fields = DEFAULT_SENSITIVE_FIELDS

    result = yaml_string
    for field in fields:
        # Match various YAML formats:
        # password: value
        # password : value
        # password: "value"
        # password: 'value'
        patterns = [
            (rf'({field}\s*:\s*)[^\s\n]+', r'\1' + mask),  # password: value
            (rf'({field}\s*:\s*)["\']([^"\']*)["\']', r'\1"' + mask + '"'),  # password: "value"
        ]

        for pattern, replacement in patterns:
            result = re.sub(pattern, replacement, result, flags=re.IGNORECASE)

    return result


def get_password_status(password: Optional[str]) -> str:
    """
    Get a safe status string for a password without exposing the value.

    Args:
        password: Password to check

    Returns:
        Status string like "SET (12 chars)" or "NOT SET"
    """
    if password:
        return f"SET ({len(password)} chars)"
    return "NOT SET"


def log_config_safely(
    logger: logging.Logger,
    config: Dict[str, Any],
    level: int = logging.DEBUG,
    message: Optional[str] = None
) -> None:
    """
    Convenience function to log a configuration dictionary safely.

    Args:
        logger: Logger instance to use
        config: Configuration dictionary to log
        level: Log level (default: DEBUG)
        message: Optional message prefix
    """
    sanitized = sanitize_config(config)
    if message:
        logger.log(level, f"{message}: {sanitized}")
    else:
        logger.log(level, f"Configuration: {sanitized}")
