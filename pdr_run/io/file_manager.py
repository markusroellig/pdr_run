"""File management utilities for the PDR framework."""

import os
import shutil
import tarfile
import hashlib
import logging
import tempfile
import subprocess

from pdr_run.config.default_config import STORAGE_CONFIG

logger = logging.getLogger('dev')

def create_dir(path):
    """Create a directory if it doesn't exist.
    
    Args:
        path (str): Directory path
    """
    try:
        os.makedirs(path)
        logger.info(f"Successfully created the directory {path}")
    except FileExistsError:
        logger.warning(f"Directory {path} already exists")

def copy_dir(source, target):
    """Copy a directory to another location.
    
    Args:
        source (str): Source directory path
        target (str): Target directory path
    """
    try:
        shutil.copytree(source, target)
        logger.info(f"Successfully copied the directory {target}")
    except FileExistsError:
        logger.warning(f"Directory {target} already exists")

def move_files(src_path, destination_path):
    """Move a file from one location to another.
    
    Args:
        src_path (str): Source file path
        destination_path (str): Destination file path
    """
    try:
        shutil.move(src_path, destination_path)
        logger.info(f"Moved file from {src_path} to {destination_path}")
    except FileNotFoundError:
        logger.error(f"Could not find the file: {src_path}")
    except Exception as e:
        logger.error(f"Error moving file {src_path}: {str(e)}", exc_info=True)

def make_tarfile(output_filename, source_dir):
    """Create a compressed tarfile from a directory.
    
    Args:
        output_filename (str): Output tarfile path
        source_dir (str): Source directory path
    """
    with tarfile.open(output_filename, "w:gz") as tar:
        tar.add(source_dir, arcname=os.path.basename(source_dir))
    logger.info(f"Created tarfile {output_filename} from {source_dir}")

def get_digest(file_path):
    """Calculate SHA-256 hash of a file.
    
    Args:
        file_path (str): File path
        
    Returns:
        str: SHA-256 hash
    """
    h = hashlib.sha256()
    with open(file_path, 'rb') as file:
        while True:
            chunk = file.read(h.block_size)
            if not chunk:
                break
            h.update(chunk)
    return h.hexdigest()

def get_code_revision(exe_path):
    """Get the revision information from an executable.
    
    Extracts revision information from the executable's version output by:
    1. Looking for a line containing 'Revision:'
    2. Falling back to alternative parsing methods if needed
    
    Args:
        exe_path (str): Path to the executable
        
    Returns:
        str: The revision identifier or a default value
    """
    try:
        out = subprocess.check_output([exe_path, "--version"]).decode("utf-8")
        
        # First approach: Look for a line containing "Revision:"
        for line in out.split("\n"):
            if "revision:" in line.lower():
                # Extract the part after the colon and strip whitespace
                return line.split(":", 1)[1].strip()
        
        # Second approach: Look for the word "revision" anywhere
        if "revision" in out.lower():
            # Original method
            revision_part = out.strip().split("revision")[1].strip()
            # Just take the first word to avoid trailing text
            return revision_part.split()[0]
            
        # Third approach: Second-to-last line, last word (previously used method)
        if len(out.split("\n")) > 1:
            return out.split("\n")[-2].split()[-1]
            
        return "unknown_revision"
    except (subprocess.SubprocessError, FileNotFoundError):
        logger.warning(f"Could not get revision for {exe_path}. Using default value.")
        return "test_revision"

def get_compilation_date(exe_path):
    """Get compilation date from executable.
    
    Args:
        exe_path (str): Executable path
        
    Returns:
        datetime: Compilation date
    """
    import datetime
    out = subprocess.check_output([exe_path, "--version"]).decode("utf-8")
    strg = list(filter(None, out.split("\n")))[-1].lstrip(' Binary compiled the ')
    return datetime.datetime.strptime(strg, '%b %d %Y at %X')

def create_temp_dir(prefix='pdr-'):
    """Create a temporary directory.
    
    Args:
        prefix (str): Directory name prefix
        
    Returns:
        str: Temporary directory path
    """
    return tempfile.mkdtemp(prefix=prefix)

def setup_model_directories(model_path):
    """Set up model directories.
    
    Args:
        model_path (str): Base model path
    """
    create_dir(os.path.join(model_path, 'pdrgrid'))
    create_dir(os.path.join(model_path, 'oniongrid'))