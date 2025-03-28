"""Utility functions for PDR framework.

This module provides essential utility functions used throughout the PDR (Photo-Dissociation Region) 
framework. PDR models describe regions where far-ultraviolet photons dominate the chemistry and
heating of the gas.

The utilities in this file include:
    - Function decorators for capturing standard output and errors (std_wrapper)
    - File integrity verification through hash calculations (get_digest)
    - Data handling utilities for species management (insert_species_to_list)
    - Timestamp formatting and parsing for logging operations (format_timestamp, parse_timestamp)

These utilities help standardize common operations throughout the codebase, improving
maintainability and reducing code duplication. The functions are designed to be lightweight,
reusable and handle error conditions gracefully.

Typical usage:
    from pdr_run.core.utils import std_wrapper, get_digest
    
    @std_wrapper
    def my_function():
        # Function implementation
        pass
        
    file_hash = get_digest('/path/to/file')
"""

import os
import logging
import hashlib
import functools

logger = logging.getLogger('dev')

def std_wrapper(func):
    """Wrapper function to capture stdout and stderr.
    
    Args:
        func (callable): Function to wrap
        
    Returns:
        callable: Wrapped function
    """
    @functools.wraps(func)
    def caller(*args, **kwargs):
        try:
            from io import StringIO
        except ImportError:
            from StringIO import StringIO
        import sys
        
        # Redirect stdout and stderr
        sys.stdout, sys.stderr = StringIO(), StringIO()
        
        # Call the function
        response = None
        try:
            response = func(*args, **kwargs)
        except Exception as e:
            print(e)
        
        # Get captured output
        sys.stdout.seek(0)
        sys.stderr.seek(0)
        stdout = sys.stdout.read()
        stderr = sys.stderr.read()
        
        return stdout, stderr, response
    
    return caller

def get_digest(file_path):
    """Calculate SHA-256 hash of a file.
    
    Args:
        file_path (str): File path
        
    Returns:
        str: SHA-256 hash
    """
    h = hashlib.sha256()
    
    with open(file_path, 'rb') as file:
        while True:
            chunk = file.read(h.block_size)
            if not chunk:
                break
            h.update(chunk)
    
    return h.hexdigest()

def insert_species_to_list(spec, liststrg):
    """Insert a species to a list if not already present.
    
    Args:
        spec (str): Species to insert
        liststrg (str): List as string
        
    Returns:
        str: Updated list as string
    """
    from pdr_run.models.parameters import string_to_list, list_to_string
    
    list_obj = string_to_list(liststrg)
    if spec.strip() not in list_obj:
        list_obj.append(spec.strip())
        return list_to_string(list_obj)
    return liststrg

def format_timestamp(timestamp=None):
    """Format a timestamp for logging.
    
    Args:
        timestamp (datetime, optional): Timestamp to format. Defaults to None (current time).
        
    Returns:
        str: Formatted timestamp
    """
    if timestamp is None:
        timestamp = datetime.datetime.now()
    
    return timestamp.strftime("%Y-%m-%d %H:%M:%S")

def parse_timestamp(timestamp_str):
    """Parse a timestamp string.
    
    Args:
        timestamp_str (str): Timestamp string
        
    Returns:
        datetime: Parsed timestamp
    """
    try:
        return datetime.datetime.strptime(timestamp_str, "%Y-%m-%d %H:%M:%S")
    except ValueError:
        logger.error(f"Invalid timestamp format: {timestamp_str}")
        return None