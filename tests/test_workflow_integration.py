"""Integration test for JSON workflow functionality."""

import sys
import os
import tempfile
import json
from pathlib import Path

# Add the project root to Python path  
project_root = Path(__file__).parent.parent
sys.path.insert(0, str(project_root))

def test_workflow_integration():
    """Test that workflow functions can import and use json_handlers without circular imports."""
    print("Testing workflow integration...")
    
    try:
        # This should work without circular import issues
        from pdr_run.workflow.json_workflow import prepare_json_config, archive_json_output
        from pdr_run.database.json_handlers import load_json_template, apply_parameters_to_json
        
        print("✓ Successfully imported workflow and json_handlers modules")
        
        # Create a mock template for testing
        template_data = {
            "model": "test",
            "chi": "${chi}",
            "density": "KT_VARdens_"
        }
        
        with tempfile.NamedTemporaryFile(mode='w', suffix='.json', delete=False) as f:
            json.dump(template_data, f)
            template_path = f.name
        
        try:
            # Test that we can load and process the template
            loaded = load_json_template(template_path)
            processed = apply_parameters_to_json(loaded, {"chi": "10", "dens": "1000"})
            
            if processed["chi"] == 10 and processed["density"] == 1000:
                print("✓ Template processing works correctly")
                return True
            else:
                print(f"✗ Template processing failed: {processed}")
                return False
                
        finally:
            os.unlink(template_path)
            
    except ImportError as e:
        print(f"✗ Import error: {e}")
        return False
    except Exception as e:
        print(f"✗ Unexpected error: {e}")
        return False

if __name__ == "__main__":
    success = test_workflow_integration()
    print(f"Integration test: {'PASSED' if success else 'FAILED'}")
    sys.exit(0 if success else 1)
