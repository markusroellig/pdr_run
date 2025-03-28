# test_pkg.py

import sys
print("\nPython path:")
for path in sys.path:
    print("  -", path)

import importlib.util
package_name = "pdr_run"
spec = importlib.util.find_spec(package_name)
if spec is None:
    print(f"\nThe {package_name} package is NOT found in sys.path")
else:
    print(f"\nThe {package_name} package is found at: {spec.origin}")
    print(f"The parent package is: {spec.parent}")
    print(f"Submodules are: {spec.submodule_search_locations}")

print("\nTrying to import directly from installation path:")
import os
site_packages_dir = "/home/roellig/pdr/pdr/pdr_run/pdr_run"
if os.path.exists(site_packages_dir):
    print(f"Directory exists: {site_packages_dir}")
    print("Contents:")
    for item in os.listdir(site_packages_dir):
        print(f"  - {item}")
    
    # Add the directory to Python's path
    sys.path.insert(0, site_packages_dir)
    try:
        import pdr_run
        print("\nSuccessfully imported the module after adding to path!")
        print(f"Module location: {pdr_run.__file__}")
    except ImportError as e:
        print(f"\nFailed to import after adding to path: {e}")
else:
    print(f"Directory does not exist: {site_packages_dir}")
