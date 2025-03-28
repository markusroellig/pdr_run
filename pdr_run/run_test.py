#!/usr/bin/env python3
"""Test script for running PDR models."""

import os
import logging
import logging.config
import datetime
import subprocess
from unittest.mock import patch, MagicMock

from pdr_run.core.engine import run_model
from pdr_run.config.default_config import DEFAULT_PARAMETERS
from pdr_run.config.logging_config import LOGGING_CONFIG

# Configure logging
logging.config.dictConfig(LOGGING_CONFIG)
logger = logging.getLogger("dev")

# Test parameters
test_params = DEFAULT_PARAMETERS.copy()
test_params['dens'] = ["3.0"]
test_params['chi'] = ["1.0"]

# Create test directory and mock files
test_dir = '/tmp/pdr_test'
os.makedirs(test_dir, exist_ok=True)

# Create necessary subdirectories
for subdir in ['pdrinpdata', 'pdrinput', 'pdroutput', 'pdrgrid', 'oniongrid']:
    os.makedirs(os.path.join(test_dir, subdir), exist_ok=True)

# Create mock executables
executables = ['pdr', 'onion', 'getctrlind', 'mrt']
for exe in executables:
    with open(os.path.join(test_dir, exe), 'w') as f:
        f.write('#!/bin/sh\necho "Jan 01 2023 at 12:00:00"')
    os.chmod(os.path.join(test_dir, exe), 0o755)

# Create template file
with open(os.path.join(test_dir, 'pdrinp_template.dat'), 'w') as f:
    f.write('# Mock template file\n')

# Mock input file needed by engine.py
os.makedirs(os.path.join(test_dir, 'pdrinpdata'), exist_ok=True)
with open(os.path.join(test_dir, 'pdrinpdata', 'chem_rates_umist.dat'), 'w') as f:
    f.write('# Mock chemical rates file')

# Create mock SQLite database environment
os.environ['PDR_DB_TYPE'] = 'sqlite'
os.environ['PDR_DB_FILE'] = '/tmp/pdr_test/test.db'

# Create mock session that's callable
mock_session = MagicMock()
mock_session_factory = MagicMock(return_value=mock_session)

# Create mock job IDs for database entries
mock_job_ids = ["test_job_123"]
mock_config = {"mock": "config"}

try:
    # Patch at the appropriate level to bypass database operations
    with patch('pdr_run.io.file_manager.get_compilation_date', 
               return_value=datetime.datetime(2023, 1, 1, 12, 0, 0)), \
         patch('pdr_run.io.file_manager.get_code_revision', 
               return_value='test_revision'), \
         patch('pdr_run.models.kosma_tau.run_kosma_tau', 
               return_value={'status': 'success', 'runtime': 0.1}), \
         patch('pdr_run.core.engine.create_database_entries', 
               return_value=(mock_config, mock_job_ids)), \
         patch('os.symlink'):
        
        # Run the model with fixed patches
        job_id = run_model(params=test_params, model_name="test_model")
        print(f"Job ID: {job_id}")
        
except Exception as e:
    print(f"Error: {e}")
    import traceback
    traceback.print_exc()