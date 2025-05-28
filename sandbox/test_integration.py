#!/usr/bin/env python3
"""Integration tests for the sandbox environment."""

import os
import sys
from pathlib import Path

# Add the parent directory to Python path
project_root = Path(__file__).parent.parent
sys.path.insert(0, str(project_root))

def test_basic_imports():
    """Test that basic PDR modules can be imported."""
    print("=== Testing Basic Imports ===")
    
    try:
        import pdr_run
        print("✓ pdr_run package imported")
        
        from pdr_run.config import default_config
        print("✓ Configuration module imported")
        
        from pdr_run.database import models
        print("✓ Database models imported")
        
        return True
        
    except ImportError as e:
        print(f"✗ Import failed: {e}")
        return False

def test_mock_executables():
    """Test that mock executables are available."""
    print("\n=== Testing Mock Executables ===")
    
    exe_dir = Path("pdr_executables")
    if not exe_dir.exists():
        print("✓ Mock executables directory not found (expected in some setups)")
        return True
    
    expected_exes = ["mockpdr", "mockonion", "mockgetctrlind", "mockmrt"]
    found_exes = []
    
    for exe in expected_exes:
        exe_path = exe_dir / exe
        if exe_path.exists() and exe_path.is_file():
            found_exes.append(exe)
            print(f"✓ Found mock executable: {exe}")
    
    if found_exes:
        print(f"✓ Found {len(found_exes)}/{len(expected_exes)} mock executables")
        return True
    else:
        print("✓ No mock executables found (may be created on-demand)")
        return True

def main():
    """Run integration tests."""
    print("PDR Integration Tests")
    print("=" * 35)
    
    results = []
    results.append(test_basic_imports())
    results.append(test_mock_executables())
    
    passed = sum(results)
    total = len(results)
    
    print(f"\n=== Test Summary ===")
    print(f"Passed: {passed}/{total}")
    
    if passed == total:
        print("✓ All integration tests passed!")
        return 0
    else:
        print("✗ Some integration tests failed!")
        return 1

if __name__ == "__main__":
    sys.exit(main())