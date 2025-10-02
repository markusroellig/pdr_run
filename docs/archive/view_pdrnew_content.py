#!/usr/bin/env python3
"""Utility script to view PDRNEW.INP content for a job."""

import argparse
import os
import sys
from unittest import mock

# Add parent directory to path so we can import our modules
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from pdr_run.database.models import PDRModelJob, KOSMAtauParameters
from pdr_run.models.kosma_tau import create_pdrnew_from_job_id, open_template
from pdr_run.config.default_config import PDR_INP_DIRS


def setup_mock_environment(use_real_template=False):
    """Set up a mock environment for testing.
    
    Args:
        use_real_template: If True, attempt to locate real template
    """
    if not use_real_template:
        # Create a simple template file in the current directory
        with open("PDRNEW.INP.template", "w") as f:
            f.write("xnsur = KT_VARxnsur_\n")
            f.write("mass = KT_VARmass_\n")
            f.write("rtot = KT_VARrtot_\n")
            f.write("KT_VARspecies_\n")
            f.write("KT_VARgrid_\n")
    else:
        # Ensure template directory exists
        os.makedirs(os.path.join("templates"), exist_ok=True)
        
        # Try to copy the real template if it exists
        template_found = False
        if isinstance(PDR_INP_DIRS, list):
            for dir_path in PDR_INP_DIRS:
                template_path = os.path.join(dir_path, "templates", "PDRNEW.INP.template")
                if os.path.exists(template_path):
                    import shutil
                    shutil.copy(template_path, os.path.join("templates", "PDRNEW.INP.template"))
                    template_found = True
                    break
        elif isinstance(PDR_INP_DIRS, str):
            template_path = os.path.join(PDR_INP_DIRS, "templates", "PDRNEW.INP.template")
            if os.path.exists(template_path):
                import shutil
                shutil.copy(template_path, os.path.join("templates", "PDRNEW.INP.template"))
                template_found = True
        
        if not template_found:
            print("Could not find the real template file. Using a simple mock template instead.")
            with open(os.path.join("templates", "PDRNEW.INP.template"), "w") as f:
                f.write("xnsur = KT_VARxnsur_\n")
                f.write("mass = KT_VARmass_\n")
                f.write("rtot = KT_VARrtot_\n")
                f.write("KT_VARspecies_\n")
                f.write("KT_VARgrid_\n")
    
    # Create mock session and objects
    mock_session = mock.MagicMock()
    
    mock_job = mock.MagicMock(spec=PDRModelJob)
    mock_job.model_job_name = "test_model"
    mock_job.kosmatau_parameters_id = 1
    
    mock_params = mock.MagicMock(spec=KOSMAtauParameters)
    mock_params.xnsur = 1.0e3
    mock_params.mass = 10
    mock_params.rtot = 1.0e17
    mock_params.species = "CO H2 H"
    mock_params.grid = True
    
    mock_session.get.side_effect = lambda cls, id: mock_job if cls == PDRModelJob else mock_params
    
    return mock_session


def show_template_locations():
    """Display all possible locations for the template file."""
    print("\nPossible template locations:")
    
    if isinstance(PDR_INP_DIRS, list):
        for dir_path in PDR_INP_DIRS:
            template_path = os.path.join(dir_path, "templates", "PDRNEW.INP.template")
            print(f"- {template_path} (Exists: {os.path.exists(template_path)})")
    else:
        template_path = os.path.join(PDR_INP_DIRS, "templates", "PDRNEW.INP.template")
        print(f"- {template_path} (Exists: {os.path.exists(template_path)})")


def run_test():
    """Run the test_real_template_pdrnew_creation test directly."""
    import unittest
    
    # Add parent directory to path so we can import modules
    sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    
    # Import the test class
    from pdr_run.tests.test_template_replacement import TestTemplateReplacement
    
    # Create a test suite with just the one test
    suite = unittest.TestSuite()
    suite.addTest(TestTemplateReplacement("test_real_template_pdrnew_creation"))
    
    # Run the test
    runner = unittest.TextTestRunner(verbosity=2)
    result = runner.run(suite)
    return result.wasSuccessful()


def main():
    """Run the script."""
    parser = argparse.ArgumentParser(description="View PDRNEW.INP content for a job.")
    parser.add_argument("job_id", type=int, nargs="?", default=1, 
                        help="Job ID to generate PDRNEW.INP for (default: 1)")
    parser.add_argument("--mock", action="store_true", 
                        help="Use mock data instead of connecting to the database")
    parser.add_argument("--real-template", action="store_true",
                        help="Try to use the real template file")
    parser.add_argument("--show-locations", action="store_true",
                        help="Show possible locations for the template file")
    parser.add_argument("--run-test", action="store_true",
                        help="Run the test_real_template_pdrnew_creation test")
    
    args = parser.parse_args()
    
    if args.run_test:
        success = run_test()
        sys.exit(0 if success else 1)
    
    if args.show_locations:
        show_template_locations()
        return
    
    if args.mock:
        print("Using mock data...")
        session = setup_mock_environment(args.real_template)
        
        if args.real_template:
            # If using real template, temporarily modify PDR_INP_DIRS to point to our local copy
            from pdr_run.config import default_config
            original_inp_dirs = default_config.PDR_INP_DIRS
            default_config.PDR_INP_DIRS = os.getcwd()
            
            try:
                content = create_pdrnew_from_job_id(args.job_id, session, return_content=True)
            finally:
                # Restore original setting
                default_config.PDR_INP_DIRS = original_inp_dirs
        else:
            # Use simple mock template
            with mock.patch('pdr_run.models.kosma_tau.open_template') as mock_open_template:
                with open("PDRNEW.INP.template", "r") as f:
                    mock_open_template.return_value = f.read()
                
                content = create_pdrnew_from_job_id(args.job_id, session, return_content=True)
    else:
        # Use real database connection
        content = create_pdrnew_from_job_id(args.job_id, return_content=True)
    
    print("\n=== PDRNEW.INP CONTENT ===")
    print(content)
    print("=========================")


if __name__ == "__main__":
    main()
