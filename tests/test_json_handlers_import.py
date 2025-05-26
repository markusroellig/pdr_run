"""Test script to verify json_handlers module can be imported without circular import issues."""

import sys
import os
import tempfile
import json
import traceback
from pathlib import Path

# Add the project root to Python path
project_root = Path(__file__).parent.parent
sys.path.insert(0, str(project_root))

def test_basic_import():
    """Test that json_handlers can be imported without circular import errors."""
    print("Testing basic import of json_handlers...")
    try:
        from pdr_run.database import json_handlers
        print("✓ Successfully imported json_handlers")
        return True
    except ImportError as e:
        print(f"✗ Import failed: {e}")
        traceback.print_exc()
        return False

def test_function_availability():
    """Test that all expected functions are available."""
    print("\nTesting function availability...")
    try:
        from pdr_run.database import json_handlers
        
        expected_functions = [
            'load_json_template',
            'apply_parameters_to_json', 
            'save_json_config',
            'process_json_template',
            'register_json_template',
            'register_json_file',
            'prepare_job_json'
        ]
        
        missing_functions = []
        for func_name in expected_functions:
            if not hasattr(json_handlers, func_name):
                missing_functions.append(func_name)
        
        if missing_functions:
            print(f"✗ Missing functions: {missing_functions}")
            return False
        else:
            print("✓ All expected functions are available")
            return True
            
    except Exception as e:
        print(f"✗ Error checking functions: {e}")
        return False

def test_parameter_substitution():
    """Test the parameter substitution functionality."""
    print("\nTesting parameter substitution...")
    try:
        from pdr_run.database.json_handlers import apply_parameters_to_json
        
        # Test template with both placeholder formats
        template = {
            "model": "PDR",
            "chi": "${chi}",
            "density": "KT_VARdens_",
            "temperature": "${temp}",
            "nested": {
                "value1": "KT_VARvalue1_",
                "value2": "${value2}"
            }
        }
        
        parameters = {
            "chi": "10.0",
            "dens": "1e4", 
            "temp": "100",
            "value1": "42",
            "value2": "3.14159"
        }
        
        result = apply_parameters_to_json(template, parameters)
        
        # Check substitutions worked
        if (result["chi"] == 10.0 and 
            result["density"] == 1e4 and
            result["temperature"] == 100 and
            result["nested"]["value1"] == 42 and
            result["nested"]["value2"] == 3.14159):
            print("✓ Parameter substitution working correctly")
            return True
        else:
            print(f"✗ Parameter substitution failed. Result: {result}")
            return False
            
    except Exception as e:
        print(f"✗ Error in parameter substitution test: {e}")
        traceback.print_exc()
        return False

def test_json_template_processing():
    """Test loading and processing JSON templates."""
    print("\nTesting JSON template processing...")
    try:
        from pdr_run.database.json_handlers import process_json_template
        
        # Create a temporary template file
        template_data = {
            "model_name": "test_model",
            "parameters": {
                "chi": "${chi}",
                "density": "KT_VARdens_",
                "temperature": 100
            }
        }
        
        with tempfile.NamedTemporaryFile(mode='w', suffix='.json', delete=False) as f:
            json.dump(template_data, f)
            template_path = f.name
        
        try:
            parameters = {"chi": "5.0", "dens": "1000"}
            
            # Test processing without saving
            result, output_path = process_json_template(template_path, parameters)
            
            if (result["parameters"]["chi"] == 5.0 and 
                result["parameters"]["density"] == 1000 and
                output_path is None):
                print("✓ JSON template processing working correctly")
                return True
            else:
                print(f"✗ JSON template processing failed. Result: {result}")
                return False
                
        finally:
            # Clean up temp file
            os.unlink(template_path)
            
    except Exception as e:
        print(f"✗ Error in JSON template processing test: {e}")
        traceback.print_exc()
        return False

def test_database_session_helper():
    """Test that the database session helper works without circular imports."""
    print("\nTesting database session helper...")
    try:
        from pdr_run.database.json_handlers import _get_session
        
        # This will fail if database isn't set up, but should not fail due to circular imports
        try:
            session = _get_session()
            print("✓ Database session helper accessible")
            # Don't actually use the session since DB might not be initialized
            return True
        except Exception as e:
            # Database connection errors are expected in test environment
            if "circular" in str(e).lower() or "import" in str(e).lower():
                print(f"✗ Circular import detected: {e}")
                return False
            else:
                print("✓ Database session helper accessible (DB connection expected to fail in test)")
                return True
                
    except ImportError as e:
        print(f"✗ Import error in session helper: {e}")
        return False

def run_all_tests():
    """Run all tests and report results."""
    print("=" * 60)
    print("TESTING JSON HANDLERS CIRCULAR IMPORT RESOLUTION")
    print("=" * 60)
    
    tests = [
        test_basic_import,
        test_function_availability,
        test_parameter_substitution,
        test_json_template_processing,
        test_database_session_helper
    ]
    
    results = []
    for test in tests:
        results.append(test())
    
    print("\n" + "=" * 60)
    print("TEST RESULTS SUMMARY")
    print("=" * 60)
    
    passed = sum(results)
    total = len(results)
    
    for i, (test, result) in enumerate(zip(tests, results)):
        status = "PASS" if result else "FAIL"
        print(f"{test.__name__}: {status}")
    
    print(f"\nOverall: {passed}/{total} tests passed")
    
    if passed == total:
        print("🎉 All tests passed! Circular import issue appears to be resolved.")
        return True
    else:
        print("❌ Some tests failed. There may still be issues to resolve.")
        return False

if __name__ == "__main__":
    success = run_all_tests()
    sys.exit(0 if success else 1)
