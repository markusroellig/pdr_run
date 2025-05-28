"""File management utilities for the PDR framework."""

import os
import time
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
    logger.debug(f"Creating directory: {os.path.abspath(path)}")
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
    if not os.path.exists(file_path):
        logger.error(f"Cannot calculate digest: File not found at {file_path}")
        return "file_not_found"
        
    try:
        file_size = os.path.getsize(file_path)
        logger.debug(f"Calculating SHA-256 hash of file: {file_path} (size: {file_size/1024:.2f} KB)")
        
        start_time = time.time()
        h = hashlib.sha256()
        read_bytes = 0
        
        with open(file_path, 'rb') as file:
            while True:
                chunk = file.read(h.block_size)
                if not chunk:
                    break
                read_bytes += len(chunk)
                h.update(chunk)
                
        digest = h.hexdigest()
        duration = time.time() - start_time
        logger.debug(f"SHA-256 digest calculated: {digest[:8]}...{digest[-8:]} ({read_bytes} bytes processed in {duration:.3f}s)")
        return digest
    except Exception as e:
        logger.error(f"Error calculating digest of {file_path}: {str(e)}", exc_info=True)
        return "error_calculating_digest"

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
    if not os.path.exists(exe_path):
        logger.error(f"Cannot get revision: Executable not found at {exe_path}")
        logger.debug(f"Current directory: {os.getcwd()}")
        logger.debug(f"Executable directory exists: {os.path.exists(os.path.dirname(exe_path))}")
        return "executable_not_found"

    try:
        logger.debug(f"Getting code revision for: {exe_path}")
        start_time = time.time()

        cmd = [exe_path, "--version"]
        logger.debug(f"Executing command: {' '.join(cmd)}")

        result = subprocess.run(cmd, capture_output=True, text=True, timeout=10)
        duration = time.time() - start_time

        if result.returncode != 0:
            logger.warning(f"Command {cmd} exited with non-zero code: {result.returncode}")
            if result.stderr:
                logger.debug(f"Command stderr: {result.stderr}")
            return "error_getting_revision"

        out = result.stdout
        logger.debug(f"Command completed in {duration:.3f}s with {len(out)} bytes output")
        logger.debug(f"Version output: {out.strip()}")

        # Ensure out is a string, not bytes
        if isinstance(out, bytes):
            out = out.decode('utf-8', errors='replace')

        # First approach: Look for a line containing "Revision:"
        for line in out.split("\n"):
            if "revision:" in line.lower():
                revision = line.split(":", 1)[1].strip()
                logger.debug(f"Found revision marker, extracted: '{revision}'")
                return revision
        
        # Second approach: Look for the word "revision" anywhere
        if "revision" in out.lower():
            revision_part = out.strip().split("revision")[1].strip()
            revision = revision_part.split()[0]
            logger.debug(f"Found 'revision' keyword, extracted: '{revision}'")
            return revision
            
        # Third approach: Second-to-last line, last word
        if len(out.split("\n")) > 1:
            out_lines = out.split("\n")
            revision = out_lines[-2].split()[-1]
            logger.debug(f"Used fallback method (second-to-last line), extracted: '{revision}'")
            return revision
        
        logger.warning(f"Could not extract revision using standard methods from: {out}")
        return "unknown_revision"
    except subprocess.TimeoutExpired:
        logger.error(f"Command timed out after 10s: {exe_path} --version")
        return "command_timeout"
    except (subprocess.SubprocessError, FileNotFoundError) as e:
        logger.warning(f"Could not get revision for {exe_path}: {str(e)}")
        logger.debug(f"Exception details:", exc_info=True)
        return "test_revision"

def get_compilation_date(exe_path):
    """Get compilation date from executable.
    
    Tries to extract the compilation date from the executable's version output.
    Returns a default date if extraction fails.
    
    Args:
        exe_path (str): Executable path
        
    Returns:
        datetime: Compilation date or default date if extraction fails
    """
    import datetime
    
    # Default date if extraction fails (Jan 1, 2000)
    default_date = datetime.datetime(2000, 1, 1)
    
    if not os.path.exists(exe_path):
        logger.error(f"Cannot get compilation date: Executable not found at {exe_path}")
        return default_date
        
    try:
        logger.debug(f"Getting compilation date for: {exe_path}")
        start_time = time.time()
        
        cmd = [exe_path, "--version"]
        logger.debug(f"Executing command: {' '.join(cmd)}")
        
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=10)
        duration = time.time() - start_time
        
        if result.returncode != 0:
            logger.warning(f"Command {cmd} exited with non-zero code: {result.returncode}")
            return default_date
            
        out = result.stdout
        logger.debug(f"Command completed in {duration:.3f}s with {len(out)} bytes output")
        logger.debug(f"Version output: {out.strip()}")
        
        # Extract the compilation date line
        lines = list(filter(None, out.split("\n")))
        if not lines:
            logger.warning("No output lines found from version command")
            return default_date
            
        date_line = lines[-1]
        logger.debug(f"Attempting to parse date from: '{date_line}'")
        
        # Try to find the compilation date indicator
        if "compiled the " in date_line.lower():
            date_part = date_line.split("compiled the ", 1)[1].strip()
            logger.debug(f"Extracted date string: '{date_part}'")
            
            # Try to parse with expected format
            try:
                compile_date = datetime.datetime.strptime(date_part, '%b %d %Y at %X')
                logger.debug(f"Parsed compilation date: {compile_date}")
                return compile_date
            except ValueError as e:
                logger.warning(f"Failed to parse date string '{date_part}': {e}")
                
        # Alternative date formats if the first attempt fails
        date_formats = [
            '%Y-%m-%d %H:%M:%S',
            '%d %b %Y %H:%M:%S',
            '%B %d, %Y',
            '%d.%m.%Y %H:%M:%S'
        ]
        
        for date_format in date_formats:
            try:
                # Try to find a date anywhere in the string
                for word in date_line.split():
                    if len(word) >= 8:  # Most date formats have at least 8 chars
                        try:
                            compile_date = datetime.datetime.strptime(word, date_format)
                            logger.debug(f"Found date using format '{date_format}': {compile_date}")
                            return compile_date
                        except ValueError:
                            pass
            except Exception:
                pass
                
        logger.warning(f"Could not extract compilation date from: {out}")
        return default_date
        
    except subprocess.TimeoutExpired:
        logger.error(f"Command timed out after 10s: {exe_path} --version")
        return default_date
    except Exception as e:
        logger.error(f"Error getting compilation date for {exe_path}: {str(e)}", exc_info=True)
        return default_date

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