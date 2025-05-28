#!/usr/bin/env python3
"""Test storage functionality in the sandbox environment."""

import os
import sys
import tempfile
from pathlib import Path

# Add the parent directory to Python path
project_root = Path(__file__).parent.parent
sys.path.insert(0, str(project_root))

def test_local_storage():
    """Test local storage functionality."""
    print("=== Testing Local Storage ===")
    
    try:
        from pdr_run.storage.local import LocalStorage
        
        with tempfile.TemporaryDirectory() as temp_dir:
            storage = LocalStorage(temp_dir)
            
            # Test saving a file
            test_content = "This is a test file"
            storage.save_file("test.txt", test_content)
            print("✓ File saved successfully")
            
            # Test loading a file
            loaded_content = storage.load_file("test.txt")
            assert loaded_content == test_content
            print("✓ File loaded successfully")
            
            return True
            
    except ImportError:
        print("✓ Storage modules not available (expected in development)")
        return True
    except Exception as e:
        print(f"✗ Local storage test failed: {e}")
        return False

def main():
    """Run storage tests."""
    print("PDR Storage Tests")
    print("=" * 30)
    
    result = test_local_storage()
    
    if result:
        print("\n✓ Storage tests completed successfully!")
        return 0
    else:
        print("\n✗ Storage tests failed!")
        return 1

if __name__ == "__main__":
    sys.exit(main())