"""JSON handling utilities for the PDR framework.

This module provides functionality for working with JSON files in the PDR framework,
including template loading, parameter substitution, file registration, and validation.
It supports the workflow of creating, processing, and archiving JSON configuration files
for PDR modeling jobs.
"""

import json
import os
import logging
import shutil
import hashlib
import re
import copy
import tempfile
from pathlib import Path

logger = logging.getLogger('dev')

# Import this at the function level, not module level to avoid circular imports
def _get_session():
    """Get a database session while avoiding circular imports.
    
    Returns:
        SQLAlchemy Session: An active database session
    """
    from .connection import get_session
    return get_session()

def load_json_template(template_path):
    """Load a JSON template file from disk.
    
    Args:
        template_path (str): Path to the JSON template file
        
    Returns:
        dict: The loaded JSON data as a Python dictionary
        
    Raises:
        FileNotFoundError: If the template file doesn't exist
        json.JSONDecodeError: If the template contains invalid JSON
    """
    with open(template_path, 'r') as f:
        return json.load(f)

def apply_parameters_to_json(template_data, parameters):
    """Apply parameter substitutions to a JSON template.
    
    Replaces placeholders in the format ${parameter_name} with corresponding values
    from the parameters dictionary. Also attempts to convert string values to 
    appropriate numeric types (int or float) where possible.
    
    Args:
        template_data (dict): The JSON template as a Python dictionary
        parameters (dict): Dictionary mapping parameter names to their values
        
    Returns:
        dict: A new dictionary with all parameters substituted
    """
    # Helper function to convert strings to numeric types when possible
    def numeric_or_string(val):
        try:
            return int(val)
        except ValueError:
            try:
                return float(val)
            except ValueError:
                return val

    # Convert template to string for easier substitution
    json_str = json.dumps(template_data)
    
    # Replace each parameter placeholder with its value
    for key, value in parameters.items():
        json_str = json_str.replace(f"${{{key}}}", str(value))

    # Parse the modified JSON string back to a Python object
    parsed = json.loads(json_str, parse_float=float)
    
    # Walk through the object tree to convert string values to numeric types
    def walk(obj):
        if isinstance(obj, dict):
            return {k: walk(v) for k, v in obj.items()}
        if isinstance(obj, list):
            return [walk(x) for x in obj]
        if isinstance(obj, str):
            return numeric_or_string(obj)
        return obj

    return walk(parsed)

def save_json_config(config, output_path):
    """Save a JSON configuration to a file.
    
    Creates any necessary directories in the output path that don't exist.
    
    Args:
        config (dict): The configuration to save as JSON
        output_path (str): Where to save the JSON file
        
    Returns:
        str: The path where the file was saved
        
    Raises:
        PermissionError: If the output location isn't writable
        TypeError: If the config contains objects that can't be serialized to JSON
    """
    # Create parent directories if they don't exist
    os.makedirs(os.path.dirname(output_path), exist_ok=True)
    
    # Write the JSON with nice formatting (2-space indentation)
    with open(output_path, 'w') as f:
        json.dump(config, f, indent=2)
    return output_path

def copy_json_file(src_path, dest_path):
    """Copy a JSON file from one location to another.
    
    Creates any necessary directories in the destination path that don't exist.
    
    Args:
        src_path (str): Source file path
        dest_path (str): Destination file path
        
    Returns:
        str: The destination path where the file was copied
        
    Raises:
        FileNotFoundError: If the source file doesn't exist
        PermissionError: If the destination isn't writable
    """
    # Create parent directories if they don't exist
    os.makedirs(os.path.dirname(dest_path), exist_ok=True)
    
    # Copy the file, preserving metadata
    shutil.copy2(src_path, dest_path)
    return dest_path

def get_json_hash(file_path):
    """Calculate a SHA-256 hash of a JSON file.
    
    Used for detecting duplicate files and changes to existing files.
    
    Args:
        file_path (str): Path to the JSON file
        
    Returns:
        str: Hexadecimal string representation of the SHA-256 hash
        
    Raises:
        FileNotFoundError: If the file doesn't exist
    """
    # Read the file in binary mode to ensure consistent hashing
    with open(file_path, 'rb') as f:
        data = f.read()
    # Return the hexadecimal digest of the SHA-256 hash
    return hashlib.sha256(data).hexdigest()

def register_json_template(name, path, description=None):
    """Register a JSON template in the database.
    
    This function creates a new JSONTemplate record in the database with the 
    provided information. It also calculates a SHA-256 hash of the template file
    for later verification and change detection.
    
    Args:
        name (str): Unique identifier/name for the template
        path (str): File system path to the JSON template file
        description (str, optional): Human-readable description of the template
    
    Returns:
        JSONTemplate: The newly created database record for the template
        
    Note:
        The SHA-256 hash is used for detecting changes and avoiding duplicate templates
    """
    # Import the model class locally to avoid circular imports
    from .models import JSONTemplate
    
    # Get a database session using the helper function
    session = _get_session()
    
    # Create a new template record with the provided information
    template = JSONTemplate(
        name=name,
        path=path,
        description=description,
        sha256_sum=get_json_hash(path)  # Calculate SHA-256 hash of template file
    )
    
    # Add the new template to the session and persist to database
    session.add(template)
    session.commit()
    
    # Return the newly created template record
    return template

def register_json_file(job_id, name, path, template_id=None):
    """Register a JSON file in the database.
    
    Associates a JSON file with a specific job and optionally with a template.
    If a file with the same SHA-256 hash already exists, updates that record
    instead of creating a new one.
    
    Args:
        job_id (int): ID of the job this JSON file belongs to
        name (str): Name/identifier for the file
        path (str): Path to the JSON file on disk
        template_id (int, optional): ID of the template this file was created from
        
    Returns:
        JSONFile: The created or updated database record
        
    Note:
        This function checks for duplicate files by comparing SHA-256 hashes.
    """
    from .models import JSONFile
    
    session = _get_session()
    
    # Calculate hash for deduplication and verification
    file_hash = get_json_hash(path)
    
    # Check if file with same hash exists to avoid duplicates
    existing = session.query(JSONFile).filter_by(sha256_sum=file_hash).first()
    if existing:
        # Update existing record instead of creating a new one
        existing.name = name
        existing.path = path
        existing.job_id = job_id
        existing.template_id = template_id
        session.commit()
        return existing
    
    # Create new record if no existing file found
    json_file = JSONFile(
        job_id=job_id,
        name=name, 
        path=path,
        template_id=template_id,
        sha256_sum=file_hash
    )
    session.add(json_file)
    session.commit()
    return json_file

def process_json_template(template_path, parameters, output_path=None):
    """Process a JSON template with parameters.
    
    Loads a template, applies parameter substitutions, and optionally saves
    the result to a file.
    
    Args:
        template_path (str): Path to the template file
        parameters (dict): Parameter values to substitute
        output_path (str, optional): Where to save the processed template
        
    Returns:
        tuple: (processed_data, output_path)
            - processed_data (dict): The template with parameters applied
            - output_path (str or None): Path where the file was saved, or None
    """
    # Load template from disk
    template = load_json_template(template_path)
    
    # Apply parameters to the template
    processed = apply_parameters_to_json(template, parameters)
    
    # Save to file if output path is provided
    if output_path:
        save_json_config(processed, output_path)
        return processed, output_path
    
    # Otherwise just return the processed data
    return processed, None

def prepare_job_json(job_id, template_id=None, parameters=None, tmp_dir=None):
    """Prepare JSON data for a job.
    
    Selects an appropriate template, processes it with provided parameters,
    saves it to disk, and registers it in the database.
    
    Args:
        job_id (int): ID of the job to prepare JSON for
        template_id (int, optional): Template to use, default if not specified
        parameters (dict, optional): Parameters to apply to the template
        tmp_dir (str, optional): Directory to store the processed file
        
    Returns:
        str: Path to the created JSON file
        
    Raises:
        ValueError: If the specified template doesn't exist or no default template is found
    """
    from .models import JSONTemplate, JSONFile
    
    session = _get_session()
    
    # Get template from database
    if template_id:
        # Use the specified template if a template_id is provided
        template = session.get(JSONTemplate, template_id)  # Modern SQLAlchemy method
        if not template:
            raise ValueError(f"Template with ID {template_id} not found")
    else:
        # Otherwise use the default template
        template = session.query(JSONTemplate).filter_by(name="default").first()
        if not template:
            raise ValueError("No default template found")
    
    # Create temp directory if needed
    if not tmp_dir:
        # Create a temporary directory that will be automatically cleaned up
        tmp_dir = tempfile.mkdtemp()
    else:
        # Use the provided directory, creating it if needed
        os.makedirs(tmp_dir, exist_ok=True)
    
    # Generate output path for the processed template
    output_filename = f"job_{job_id}_config.json"
    output_path = os.path.join(tmp_dir, output_filename)
    
    # Process template with parameters
    processed, path = process_json_template(template.path, parameters or {}, output_path)
    
    # Register the file in the database
    register_json_file(job_id, output_filename, path, template.id)
    
    return path

def archive_job_json(job_id, tmp_json_path, archive_dir):
    """Archive JSON data for a job.
    
    Copies a temporary JSON file to a permanent archive location and
    updates the database record to reflect the archived path.
    
    Args:
        job_id (int): ID of the job whose JSON to archive
        tmp_json_path (str): Path to the temporary JSON file
        archive_dir (str): Directory where to archive the file
        
    Returns:
        str: Path to the archived JSON file
    """
    from .models import JSONFile
    
    # Create archive directory if it doesn't exist
    os.makedirs(archive_dir, exist_ok=True)
    
    # Determine archive path for the file
    filename = os.path.basename(tmp_json_path)
    archive_path = os.path.join(archive_dir, f"job_{job_id}_{filename}")
    
    # Copy the file to the archive location
    copy_json_file(tmp_json_path, archive_path)
    
    # Update the database record with the archived path
    session = _get_session()
    json_file = session.query(JSONFile).filter_by(job_id=job_id).first()
    if json_file:
        json_file.archived_path = archive_path
        session.commit()
    
    return archive_path

def validate_json(json_path, schema_path=None):
    """Validate JSON data against a schema.
    
    Currently only checks if the file contains valid JSON syntax.
    If schema_path is provided, would validate against that schema
    (not yet implemented).
    
    Args:
        json_path (str): Path to the JSON file to validate
        schema_path (str, optional): Path to JSON schema for validation
        
    Returns:
        bool: True if valid, False if invalid
    """
    try:
        # Try to parse the JSON file
        with open(json_path, 'r') as f:
            json.load(f)
        return True
    except json.JSONDecodeError:
        # Log error and return False if not valid JSON
        logger.error(f"Invalid JSON in {json_path}")
        return False

def get_json_templates():
    """Get all JSON templates from the database.
    
    Returns:
        list: List of JSONTemplate objects from the database
    """
    from .models import JSONTemplate
    
    session = _get_session()
    return session.query(JSONTemplate).all()

def get_job_json_files(job_id):
    """Get JSON files associated with a job.
    
    Args:
        job_id (int): ID of the job to get files for
        
    Returns:
        list: List of JSONFile objects associated with the job
    """
    from .models import JSONFile
    
    session = _get_session()
    return session.query(JSONFile).filter_by(job_id=job_id).all()

def update_job_output_json(job_id, output_path):
    """Update job output JSON.
    
    Registers a JSON file as the output for a specific job and
    updates the job record to reference this output.
    
    Args:
        job_id (int): ID of the job to update
        output_path (str): Path to the output JSON file
        
    Returns:
        JSONFile: The registered output file
        
    Raises:
        ValueError: If the job is not found
    """
    from .models import PDRModelJob
    
    session = _get_session()
    job = session.get(PDRModelJob, job_id)  # Modern SQLAlchemy method
    if not job:
        raise ValueError(f"Job with ID {job_id} not found")
    
    # Register the output file in the database
    filename = os.path.basename(output_path)
    json_file = register_json_file(job_id, filename, output_path)
    
    # Update the job record to point to this output file
    job.output_json_id = json_file.id
    session.commit()
    
    return json_file
