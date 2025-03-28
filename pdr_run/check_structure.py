# check_structure.py

import os

def list_dir_recursively(start_path):
    for root, dirs, files in os.walk(start_path):
        level = root.replace(start_path, '').count(os.sep)
        indent = ' ' * 4 * level
        print(f"{indent}{os.path.basename(root)}/")
        file_indent = ' ' * 4 * (level + 1)
        for f in files:
            print(f"{file_indent}{f}")

print("Package structure:")
list_dir_recursively("/home/roellig/pdr/pdr/pdr_run/pdr_run")
