#!/usr/bin/env python3
"""Setup script to create the template directory structure and files."""

import os
import argparse
import sys

# Add parent directory to path to import from pdr_run
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from pdr_run.config.default_config import PDR_INP_DIRS

def setup_template_directories():
    """Create template directories in PDR_INP_DIRS locations."""
    print("Creating template directories...")
    
    # Make sure we have valid directory paths
    if not PDR_INP_DIRS or (isinstance(PDR_INP_DIRS, list) and not PDR_INP_DIRS):
        print("Error: PDR_INP_DIRS is empty. Please check your configuration.")
        return False
    
    # Process directory list or single directory
    dirs_to_create = []
    if isinstance(PDR_INP_DIRS, list):
        for dir_path in PDR_INP_DIRS:
            dirs_to_create.append(os.path.join(dir_path, "templates"))
    else:
        dirs_to_create.append(os.path.join(PDR_INP_DIRS, "templates"))
    
    # Create directories if they don't exist
    for dir_path in dirs_to_create:
        if not os.path.exists(dir_path):
            try:
                os.makedirs(dir_path, exist_ok=True)
                print(f"Created directory: {dir_path}")
            except OSError as e:
                print(f"Error creating directory {dir_path}: {e}")
                continue
        else:
            print(f"Directory already exists: {dir_path}")
    
    return True

def create_template_file(force=False):
    """Create a basic PDRNEW.INP.template file in the first available template directory."""
    template_content = """*********************************
******* PDRNEW.INP  *********
*********************************

**************************************************
***** Reference PDR Model with Clump geometry*****
**************************************************

*** Physical Parameters ***

xnsur = KT_VARxnsur_    # Surface density (cm^-3)
mass = KT_VARmass_      # Clump mass (solar masses)
rtot = KT_VARrtot_      # Cloud radius (cm)

*** Species parameters ***
KT_VARspecies_

*** Grid settings ***
KT_VARgrid_

*** End of template file ***
"""
    
    # Find the first available template directory
    template_dir = None
    if isinstance(PDR_INP_DIRS, list):
        for dir_path in PDR_INP_DIRS:
            temp_dir = os.path.join(dir_path, "templates")
            if os.path.exists(temp_dir):
                template_dir = temp_dir
                break
    else:
        temp_dir = os.path.join(PDR_INP_DIRS, "templates")
        if os.path.exists(temp_dir):
            template_dir = temp_dir
    
    if not template_dir:
        print("Error: No valid template directory found. Run setup_template_directories first.")
        return False
    
    # Create the template file
    template_path = os.path.join(template_dir, "PDRNEW.INP.template")
    if os.path.exists(template_path) and not force:
        print(f"Template file already exists: {template_path}")
        print("Use --force to overwrite it.")
        return False
    
    try:
        with open(template_path, 'w') as f:
            f.write(template_content)
        print(f"Created template file: {template_path}")
        return True
    except IOError as e:
        print(f"Error creating template file: {e}")
        return False

def main():
    """Run the setup script."""
    parser = argparse.ArgumentParser(description="Set up PDR template directories and files")
    parser.add_argument("--force", action="store_true", 
                       help="Force overwrite of existing template files")
    parser.add_argument("--show-dirs", action="store_true",
                       help="Show the configured PDR_INP_DIRS")
    args = parser.parse_args()
    
    if args.show_dirs:
        print("PDR_INP_DIRS configuration:")
        if isinstance(PDR_INP_DIRS, list):
            for i, dir_path in enumerate(PDR_INP_DIRS):
                print(f"{i+1}. {dir_path}")
                template_path = os.path.join(dir_path, "templates", "PDRNEW.INP.template")
                print(f"   Template: {template_path} (Exists: {os.path.exists(template_path)})")
        else:
            print(f"Single path: {PDR_INP_DIRS}")
            template_path = os.path.join(PDR_INP_DIRS, "templates", "PDRNEW.INP.template")
            print(f"   Template: {template_path} (Exists: {os.path.exists(template_path)})")
        return
    
    # Create directories
    if setup_template_directories():
        # Create template file
        create_template_file(args.force)

if __name__ == "__main__":
    main()
