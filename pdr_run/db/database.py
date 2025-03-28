"""Database utilities for PDR framework."""

import os
import sqlite3
import logging
import mysql.connector
import warnings

logger = logging.getLogger("dev")

"""
Deprecated database utility functions.
Use pdr_run.database module instead.
"""

def get_db_connection():
    """Get database connection (engine).
    
    Deprecated: Use pdr_run.database.get_engine() instead.
    """
    warnings.warn(
        "get_db_connection() is deprecated, use get_engine() from pdr_run.database instead",
        DeprecationWarning, 
        stacklevel=2
    )
    from pdr_run.database import get_engine
    return get_engine()

def create_tables(conn):
    """Create database tables.
    
    Deprecated: Use pdr_run.database.create_tables() instead.
    """
    warnings.warn(
        "create_tables() is deprecated, use create_tables() from pdr_run.database instead",
        DeprecationWarning,
        stacklevel=2
    )
    from pdr_run.database import create_tables
    create_tables(conn)

class ModelRun:
    """Model run data class."""
    
    def __init__(self, name, parameters, status="pending", runtime_seconds=None):
        self.name = name
        self.parameters = parameters
        self.status = status
        self.runtime_seconds = runtime_seconds
    
    def save(self, conn):
        """Save model run to database."""
        cursor = conn.cursor()
        
        # Insert into model_runs
        cursor.execute(
            "INSERT INTO model_runs (name, status, runtime_seconds) VALUES (?, ?, ?)",
            (self.name, self.status, self.runtime_seconds)
        )
        run_id = cursor.lastrowid
        
        # Insert parameters
        for name, value in self.parameters.items():
            cursor.execute(
                "INSERT INTO model_results (run_id, parameter_name, parameter_value) VALUES (?, ?, ?)",
                (run_id, name, str(value))
            )
        
        conn.commit()
        return run_id