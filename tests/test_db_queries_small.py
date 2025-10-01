#!/usr/bin/env python
"""Quick database query test with detailed analysis."""

import os
import sys
import time

# Set up environment for MySQL
os.environ['PDR_DB_TYPE'] = 'mysql'
os.environ['PDR_DB_HOST'] = 'localhost'
os.environ['PDR_DB_PORT'] = '3306'
os.environ['PDR_DB_DATABASE'] = 'pdr_test'
os.environ['PDR_DB_USERNAME'] = 'pdr_user'
os.environ['PDR_DB_PASSWORD'] = 'pdr_password'
os.environ['PDR_STORAGE_TYPE'] = 'local'
os.environ['PDR_STORAGE_DIR'] = '/tmp/pdr_test_queries'

from pdr_run.core.engine import run_parameter_grid

print("\n" + "="*80)
print("SMALL DATABASE QUERY TEST")
print("="*80 + "\n")

# Define minimal test grid (2 jobs)
params = {
    'metal': ['100'],
    'dens': ['3.0'],
    'mass': ['5.0'],
    'chi': ['1.0', '10.0'],  # Just 2 values = 2 jobs
    'species': ['CO'],
    'chemistry': ['umist']
}

print(f"🧪 Test: 2 jobs, single worker, sequential execution\n")

start_time = time.time()

try:
    job_ids = run_parameter_grid(
        params=params,
        model_name='query_test',
        config=None,
        parallel=False,  # Sequential to make analysis clearer
        n_workers=1
    )
    execution_time = time.time() - start_time

    print(f"\n✅ Completed in {execution_time:.2f} seconds")
    print(f"📋 Jobs created: {job_ids}\n")

except Exception as e:
    print(f"\n❌ Failed: {e}")
    import traceback
    traceback.print_exc()

print("="*80)
