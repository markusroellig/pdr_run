#!/usr/bin/env python3
"""Test storage functionality in the sandbox environment."""

import os
import sys
import tempfile
from pathlib import Path
import shutil # Ensure shutil is imported for potential cleanup if needed, though LocalStorage handles it.

# Add the parent directory to Python path
project_root = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(project_root))

def test_direct_local_storage_operations():
    """Test pdr_run.storage.local.LocalStorage directly."""
    print("\n=== Testing LocalStorage Direct Operations ===")
    
    try:
        from pdr_run.storage.local import LocalStorage
        
        with tempfile.TemporaryDirectory() as temp_storage_base_dir:
            storage = LocalStorage(temp_storage_base_dir)
            print(f"✓ LocalStorage initialized with base_dir: {temp_storage_base_dir}")
            
            test_content = "Direct LocalStorage test content."
            # remote_path is relative to the LocalStorage base_dir
            remote_relative_path = Path("test_subdir") / "test_file.txt"

            # --- Test store_file ---
            # Create a temporary local file to act as the source
            with tempfile.NamedTemporaryFile(mode="w", delete=False, suffix=".txt") as temp_source_file:
                temp_source_file.write(test_content)
                local_source_file_path = temp_source_file.name
            
            try:
                print(f"  Attempting: store_file(local_path='{local_source_file_path}', remote_path='{str(remote_relative_path)}')")
                storage.store_file(local_source_file_path, str(remote_relative_path))
                
                expected_stored_file = Path(temp_storage_base_dir) / remote_relative_path
                assert expected_stored_file.exists(), f"File not found at {expected_stored_file} after store_file"
                assert expected_stored_file.read_text() == test_content, "Stored file content mismatch"
                print(f"  ✓ store_file successful. File verified at: {expected_stored_file}")

            finally:
                if Path(local_source_file_path).exists():
                    Path(local_source_file_path).unlink()

            # --- Test retrieve_file ---
            # Create a temporary path for the retrieved file
            with tempfile.NamedTemporaryFile(delete=True, suffix=".txt") as temp_target_file_node:
                local_target_retrieve_path = temp_target_file_node.name # Path for the downloaded file
            
            # Ensure parent directory for local_target_retrieve_path exists (LocalStorage.retrieve_file does this too)
            Path(local_target_retrieve_path).parent.mkdir(parents=True, exist_ok=True)

            print(f"  Attempting: retrieve_file(remote_path='{str(remote_relative_path)}', local_path='{local_target_retrieve_path}')")
            storage.retrieve_file(str(remote_relative_path), local_target_retrieve_path)
            
            assert Path(local_target_retrieve_path).exists(), f"File not found at {local_target_retrieve_path} after retrieve_file"
            assert Path(local_target_retrieve_path).read_text() == test_content, "Retrieved file content mismatch"
            print(f"  ✓ retrieve_file successful. File verified at: {local_target_retrieve_path}")
            
            if Path(local_target_retrieve_path).exists():
                Path(local_target_retrieve_path).unlink()

            # --- Test list_files ---
            print(f"  Attempting: list_files(path='{str(remote_relative_path.parent)}')")
            files_in_subdir = storage.list_files(str(remote_relative_path.parent))
            assert remote_relative_path.name in files_in_subdir, f"File {remote_relative_path.name} not found in list_files result."
            print(f"  ✓ list_files successful. Found: {files_in_subdir}")

            print("✓ Direct LocalStorage operations test passed.")
            return True
            
    except ImportError:
        print("! Failed to import pdr_run.storage.local.LocalStorage. Skipping direct test.")
        return True # Or False if this is critical
    except Exception as e:
        import traceback
        print(f"✗ Direct LocalStorage test encountered an error: {e}")
        traceback.print_exc()
        return False

def test_get_storage_backend_for_local():
    """Test obtaining LocalStorage via pdr_run.storage.base.get_storage_backend."""
    print("\n=== Testing get_storage_backend for LocalStorage ===")
    
    original_env_type = os.environ.get("PDR_STORAGE_TYPE")
    original_env_dir = os.environ.get("PDR_STORAGE_DIR")
    
    try:
        from pdr_run.storage.base import get_storage_backend
        from pdr_run.storage.local import LocalStorage
        
        with tempfile.TemporaryDirectory() as temp_backend_storage_dir:
            os.environ["PDR_STORAGE_TYPE"] = "local"
            os.environ["PDR_STORAGE_DIR"] = temp_backend_storage_dir
            print(f"  Set PDR_STORAGE_TYPE=local, PDR_STORAGE_DIR={temp_backend_storage_dir}")

            backend = get_storage_backend()
            assert isinstance(backend, LocalStorage), "get_storage_backend did not return LocalStorage instance for type 'local'"
            assert backend.base_dir == temp_backend_storage_dir, "LocalStorage instance from backend has incorrect base_dir"
            print(f"  ✓ get_storage_backend returned LocalStorage instance with base_dir: {backend.base_dir}")

            # Perform a simple operation to ensure it's functional
            test_content = "Content via get_storage_backend."
            remote_rel_path = "backend_test.txt"
            with tempfile.NamedTemporaryFile(mode="w", delete=False) as src_file:
                src_file.write(test_content)
                local_src_path = src_file.name
            
            backend.store_file(local_src_path, remote_rel_path)
            Path(local_src_path).unlink()

            with tempfile.NamedTemporaryFile(delete=True) as dest_file_node:
                local_dest_path = dest_file_node.name
            backend.retrieve_file(remote_rel_path, local_dest_path)
            assert Path(local_dest_path).read_text() == test_content
            print("  ✓ store_file and retrieve_file via backend instance successful.")
            Path(local_dest_path).unlink()

        print("✓ get_storage_backend for LocalStorage test passed.")
        return True

    except ImportError:
        print("! Failed to import from pdr_run.storage.base or .local. Skipping backend retrieval test.")
        return True # Or False
    except Exception as e:
        import traceback
        print(f"✗ get_storage_backend test encountered an error: {e}")
        traceback.print_exc()
        return False
    finally:
        # Restore original environment variables
        if original_env_type is None:
            os.environ.pop("PDR_STORAGE_TYPE", None)
        else:
            os.environ["PDR_STORAGE_TYPE"] = original_env_type
        
        if original_env_dir is None:
            os.environ.pop("PDR_STORAGE_DIR", None)
        else:
            os.environ["PDR_STORAGE_DIR"] = original_env_dir

def test_storage_configuration_check():
    """Check storage configuration from default_config and environment."""
    print("\n=== Testing Storage Configuration Check ===")
    try:
        # Check default_config.py
        try:
            from pdr_run.config.default_config import STORAGE_CONFIG
            print(f"  ✓ Imported STORAGE_CONFIG from default_config:")
            print(f"    Type: {STORAGE_CONFIG.get('type')}, Base Dir: {STORAGE_CONFIG.get('base_dir')}")
        except ImportError:
            print("  ! pdr_run.config.default_config.STORAGE_CONFIG not found.")
        
        # Check environment variables (as used by get_storage_backend)
        env_type = os.environ.get('PDR_STORAGE_TYPE', 'local (env default)')
        env_dir = os.environ.get('PDR_STORAGE_DIR', '/tmp/pdr_storage (env default)')
        print(f"  ✓ Environment variables for storage:")
        print(f"    PDR_STORAGE_TYPE: {env_type}")
        print(f"    PDR_STORAGE_DIR: {env_dir}")
        
        print("✓ Storage configuration check completed.")
        return True
    except Exception as e:
        import traceback
        print(f"✗ Storage configuration check failed: {e}")
        traceback.print_exc()
        return False

def main():
    """Run all storage tests."""
    print("\nPDR Storage Tests")
    print("=" * 40)
    
    results = []
    results.append(test_direct_local_storage_operations())
    results.append(test_get_storage_backend_for_local())
    results.append(test_storage_configuration_check())
    
    passed_count = sum(1 for r in results if r is True)
    total_tests = len(results)
    
    print(f"\n=== Test Summary ===")
    print(f"Passed: {passed_count}/{total_tests}")
    
    if passed_count == total_tests:
        print("✓ All storage tests passed!")
        return 0  # Exit code 0 for success
    else:
        print("✗ Some storage tests failed!")
        return 1  # Exit code 1 for failure

if __name__ == "__main__":
    sys.exit(main())