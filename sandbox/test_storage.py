#!/usr/bin/env python3
"""Test storage backends."""

import os
import sys
import tempfile
import json
sys.path.insert(0, '.')

def test_local_storage():
    os.environ.update({
        'PDR_STORAGE_TYPE': 'local',
        'PDR_STORAGE_DIR': './sandbox/storage'
    })
    
    try:
        from pdr_run.storage.base import get_storage_backend
        storage = get_storage_backend()
        
        # Create test file
        test_data = {"test": "data", "timestamp": "2023-01-01"}
        with tempfile.NamedTemporaryFile(mode='w', delete=False) as f:
            json.dump(test_data, f)
            temp_file = f.name
        
        # Store file
        storage.store_file(temp_file, "test/data.json")
        print("✓ Local storage store successful")
        
        # Retrieve file
        with tempfile.NamedTemporaryFile(mode='w', delete=False) as f:
            retrieve_file = f.name
        
        storage.retrieve_file("test/data.json", retrieve_file)
        
        # Verify content
        with open(retrieve_file, 'r') as f:
            retrieved_data = json.load(f)
        
        if retrieved_data == test_data:
            print("✓ Local storage retrieve successful")
        else:
            print("✗ Local storage data mismatch")
            
        # Cleanup
        os.unlink(temp_file)
        os.unlink(retrieve_file)
        
    except Exception as e:
        print(f"✗ Local storage test failed: {e}")

if __name__ == "__main__":
    print("Testing storage backends...")
    test_local_storage()
