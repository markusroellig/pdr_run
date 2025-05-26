"""Test script to verify PDR_DB_PASSWORD environment variable handling."""

import os
import sys
import tempfile
import subprocess
from pathlib import Path

def test_scenario(scenario_name, env_vars=None, config_file=None, expected_password=None):
    """Test a specific scenario for PDR_DB_PASSWORD handling."""
    print(f"\n=== Testing {scenario_name} ===")
    
    # Set up environment
    test_env = os.environ.copy()
    if env_vars:
        test_env.update(env_vars)
    
    # Create minimal test command
    cmd = [
        sys.executable, "-m", "pdr_run.cli.runner",
        "--dry-run",  # Don't actually run models
        "--model-name", "test_password"
    ]
    
    if config_file:
        cmd.extend(["--config", config_file])
    
    try:
        # Run the command and capture output
        result = subprocess.run(
            cmd,
            env=test_env,
            capture_output=True,
            text=True,
            timeout=30
        )
        
        print(f"Return code: {result.returncode}")
        print(f"STDOUT:\n{result.stdout}")
        if result.stderr:
            print(f"STDERR:\n{result.stderr}")
        
        # Check if expected password appears in logs
        if expected_password:
            if f"password: {expected_password}" in result.stdout.lower():
                print(f"✓ Expected password '{expected_password}' found in output")
                return True
            else:
                print(f"✗ Expected password '{expected_password}' NOT found in output")
                return False
        
        return result.returncode == 0
        
    except subprocess.TimeoutExpired:
        print("✗ Test timed out")
        return False
    except Exception as e:
        print(f"✗ Test failed with exception: {e}")
        return False

def create_test_config(password=None):
    """Create a temporary config file for testing."""
    config_content = f"""
database:
  type: mysql
  host: test.example.com
  username: test_user
  database: test_db
"""
    if password:
        config_content += f"  password: {password}\n"
    
    with tempfile.NamedTemporaryFile(mode='w', suffix='.yaml', delete=False) as f:
        f.write(config_content)
        return f.name

def main():
    """Run all test scenarios."""
    print("Testing PDR_DB_PASSWORD environment variable handling")
    print("=" * 60)
    
    results = []
    
    # Test 1: No config file, no environment variable
    results.append(test_scenario(
        "No config, no env var",
        env_vars={'PDR_DB_PASSWORD': ''},  # Ensure it's not set
        expected_password=None
    ))
    
    # Test 2: No config file, with environment variable
    results.append(test_scenario(
        "No config, with env var",
        env_vars={'PDR_DB_PASSWORD': 'env_password123'},
        expected_password='env_password123'
    ))
    
    # Test 3: Config file without password, no environment variable
    config_file_no_pass = create_test_config()
    results.append(test_scenario(
        "Config without password, no env var",
        env_vars={'PDR_DB_PASSWORD': ''},
        config_file=config_file_no_pass,
        expected_password=None
    ))
    
    # Test 4: Config file without password, with environment variable
    results.append(test_scenario(
        "Config without password, with env var",
        env_vars={'PDR_DB_PASSWORD': 'env_override123'},
        config_file=config_file_no_pass,
        expected_password='env_override123'
    ))
    
    # Test 5: Config file with password, no environment variable
    config_file_with_pass = create_test_config('config_password456')
    results.append(test_scenario(
        "Config with password, no env var",
        env_vars={'PDR_DB_PASSWORD': ''},
        config_file=config_file_with_pass,
        expected_password='config_password456'
    ))
    
    # Test 6: Config file with password, with environment variable (should override)
    results.append(test_scenario(
        "Config with password, with env var (override)",
        env_vars={'PDR_DB_PASSWORD': 'env_wins789'},
        config_file=config_file_with_pass,
        expected_password='env_wins789'
    ))
    
    # Clean up
    os.unlink(config_file_no_pass)
    os.unlink(config_file_with_pass)
    
    # Summary
    print("\n" + "=" * 60)
    print("TEST SUMMARY")
    print("=" * 60)
    
    passed = sum(results)
    total = len(results)
    
    print(f"Passed: {passed}/{total}")
    
    if passed == total:
        print("✓ All tests passed! PDR_DB_PASSWORD handling is working correctly.")
        return 0
    else:
        print("✗ Some tests failed. Please check the implementation.")
        return 1

if __name__ == "__main__":
    sys.exit(main())
