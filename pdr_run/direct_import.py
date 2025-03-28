# direct_import.py

import sys
import os

# Full path to your package
package_path = "/home/roellig/pdr/pdr/pdr_run/pdr_run"
if package_path not in sys.path:
    sys.path.insert(0, package_path)

try:
    import pdr_run
    print(f"Successfully imported pdr_run from {pdr_run.__file__}")
    
    # Try to import a specific module
    try:
        from pdr_run.cli import runner
        print("Successfully imported runner module")
    except ImportError as e:
        print(f"Failed to import runner module: {e}")
        
except ImportError as e:
    print(f"Failed to import pdr_run: {e}")
