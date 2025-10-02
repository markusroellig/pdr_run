#!/usr/bin/env python3
"""Integration test for PDR framework."""

import os
import sys
import tempfile
sys.path.insert(0, '.')

def test_full_workflow():
    # Set up environment
    os.environ.update({
        'PDR_DB_TYPE': 'sqlite',
        'PDR_DB_FILE': './sandbox/sqlite/integration_test.db',
        'PDR_STORAGE_TYPE': 'local',
        'PDR_STORAGE_DIR': './sandbox/storage'
    })
    
    try:
        from pdr_run.core.engine import run_model
        from pdr_run.config.default_config import DEFAULT_PARAMETERS
        
        # Set up test parameters
        test_params = DEFAULT_PARAMETERS.copy()
        test_params['dens'] = ["30"]
        test_params['chi'] = ["10"]
        test_params['mass'] = ["-10"]
        test_params['metal'] = ["100"]
        
        # Configuration for sandbox
        config = {
            'pdr': {
                'base_dir': './sandbox/pdr_executables',
                'pdr_file_name': 'mockpdr',
                'onion_file_name': 'mockonion',
                'getctrlind_file_name': 'mockgetctrlind',
                'mrt_file_name': 'mockmrt',
                'pdrinp_template_file': 'PDRNEW.INP.template',
                'json_template_file': 'pdr_config.json.template'
            },
            'parameters': test_params
        }
        
        # Run model
        job_id = run_model(
            params=test_params,
            model_name="sandbox_test",
            config=config
        )
        
        print(f"✓ Integration test successful - Job ID: {job_id}")
        
    except Exception as e:
        print(f"✗ Integration test failed: {e}")
        import traceback
        traceback.print_exc()

if __name__ == "__main__":
    print("Running integration test...")
    test_full_workflow()
