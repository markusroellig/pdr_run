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
    
    Replaces placeholders in the format ${parameter_name} or KT_VARparameter_name_ 
    with corresponding values from the parameters dictionary. Also attempts to convert 
    string values to appropriate numeric types (int or float) where possible.
    
    Args:
        template_data (dict): The JSON template as a Python dictionary
        parameters (dict): Dictionary mapping parameter names to their values
        
    Returns:
        dict: A new dictionary with all parameters substituted
    """
    import json
    import re
    
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
    if isinstance(template_data, dict):
        json_str = json.dumps(template_data)
    else:
        json_str = str(template_data)
    
    logger.debug(f"Starting JSON parameter substitution with {len(parameters)} parameters")
    
    # Replace each parameter placeholder with its value
    # Support both ${parameter} and KT_VARparameter_ formats
    substitution_count = 0
    for key, value in parameters.items():
        # Format value properly
        if isinstance(value, (int, float)):
            if isinstance(value, int):
                str_value = str(value)
            elif abs(value) >= 1000 or abs(value) < 0.1:
                str_value = f"{value:.3e}"
            else:
                str_value = f"{value:.6f}"
        else:
            str_value = str(value)
        
        # Try ${parameter} format first
        pattern1 = f"${{{key}}}"
        if pattern1 in json_str:
            json_str = json_str.replace(pattern1, str_value)
            substitution_count += 1
            logger.debug(f"Replaced ${{{key}}} with {str_value}")
        
        # Try KT_VARparameter_ format
        pattern2 = f"KT_VAR{key}_"
        if pattern2 in json_str:
            json_str = json_str.replace(pattern2, str_value)
            substitution_count += 1
            logger.debug(f"Replaced KT_VAR{key}_ with {str_value}")
        
        # Also try without KT_VAR prefix (for backward compatibility)
        if key.startswith('KT_VAR') and key.endswith('_'):
            clean_key = key[6:-1]  # Remove KT_VAR and trailing _
            pattern3 = f"${{{clean_key}}}"
            if pattern3 in json_str:
                json_str = json_str.replace(pattern3, str_value)
                substitution_count += 1
                logger.debug(f"Replaced ${{{clean_key}}} with {str_value}")

    logger.debug(f"Made {substitution_count} parameter substitutions")
    
    # Check for unreplaced placeholders
    unreplaced_kt = re.findall(r'KT_VAR\w+_', json_str)
    unreplaced_dollar = re.findall(r'\$\{\w+\}', json_str)
    
    if unreplaced_kt or unreplaced_dollar:
        logger.warning(f"Found unreplaced placeholders: KT_VAR format: {unreplaced_kt}, dollar format: {unreplaced_dollar}")

    try:
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

        result = walk(parsed)
        logger.debug("Successfully parsed and processed JSON template")
        return result
        
    except json.JSONDecodeError as e:
        logger.error(f"Failed to parse JSON after parameter substitution: {e}")
        logger.debug(f"Problematic JSON string: {json_str[:500]}...")
        raise

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

def initialize_default_templates(template_dir=None):
    """Initialize default JSON templates in the database.
    
    Scans the provided template directory (or default templates directory)
    and registers any templates that don't already exist in the database.
    
    Args:
        template_dir (str, optional): Directory containing template files
            If None, uses the default templates directory from config
            
    Returns:
        list: List of registered template objects
    """
    from pdr_run.config.default_config import get_config
    import glob
    
    # Get templates directory from config if not provided
    if template_dir is None:
        config = get_config()
        template_dir = config.get('templates_dir', os.path.join(os.path.dirname(__file__), '../templates'))
    
    # Ensure the directory exists
    if not os.path.exists(template_dir):
        logger.warning(f"Templates directory {template_dir} does not exist. Creating it.")
        os.makedirs(template_dir, exist_ok=True)
        return []
    
    # Find all JSON template files in the directory
    template_files = glob.glob(os.path.join(template_dir, "*.json.template"))
    if not template_files:
        logger.warning(f"No template files found in {template_dir}")
        return []
    
    # Register each template
    registered_templates = []
    session = _get_session()
    
    for template_path in template_files:
        template_name = os.path.basename(template_path).replace(".json.template", "")
        
        # Check if template with this hash already exists
        template_hash = get_json_hash(template_path)
        existing = session.query(JSONTemplate).filter_by(sha256_sum=template_hash).first()
        
        if existing:
            logger.info(f"Template {template_name} already registered with hash {template_hash[:8]}...")
            registered_templates.append(existing)
            continue
            
        # Create a short description from the first few lines
        description = None
        try:
            with open(template_path, 'r') as f:
                content = f.read(1000)  # Read first 1000 chars
                # If there's a comment at the top, use it as description
                if content.strip().startswith(('/*', '//', '#')):
                    description = content.split('\n', 1)[0].strip('/* \t\n')
        except Exception as e:
            logger.warning(f"Couldn't read template {template_path}: {e}")
            
        # Register the template
        template = register_json_template(
            name=template_name,
            path=template_path,
            description=description
        )
        registered_templates.append(template)
        logger.info(f"Registered template {template_name} from {template_path}")
    
    # Create a default template if none exists
    if not session.query(JSONTemplate).filter_by(name="default").first():
        # If we have any templates, set the first one as default
        if registered_templates:
            default = registered_templates[0]
            default_copy = register_json_template(
                name="default",
                path=default.path,
                description=f"Default template (copy of {default.name})"
            )
            logger.info(f"Created default template based on {default.name}")
            registered_templates.append(default_copy)
            
    return registered_templates

def update_json_template(template_id, name=None, path=None, description=None):
    """Update an existing JSON template in the database.
    
    Args:
        template_id (int): ID of the template to update
        name (str, optional): New name for the template
        path (str, optional): New path for the template file
        description (str, optional): New description for the template
        
    Returns:
        JSONTemplate: The updated template object, or None if not found
        
    Raises:
        ValueError: If the template doesn't exist
    """
    from .models import JSONTemplate
    session = _get_session()
    
    template = session.get(JSONTemplate, template_id)
    if not template:
        raise ValueError(f"Template with ID {template_id} not found")
    
    # Update fields if provided
    if name:
        template.name = name
    if path:
        template.path = path
        # Update hash if path changed
        template.sha256_sum = get_json_hash(path)
    if description:
        template.description = description
        
    session.commit()
    return template

def delete_json_template(template_id, force=False):
    """Delete a JSON template from the database.
    
    Args:
        template_id (int): ID of the template to delete
        force (bool, optional): If True, delete even if template has instances
            Default is False, which raises an error if template has instances
            
    Returns:
        bool: True if deleted successfully
        
    Raises:
        ValueError: If the template doesn't exist or has instances and force=False
    """
    from .models import JSONTemplate
    session = _get_session()
    
    template = session.get(JSONTemplate, template_id)
    if not template:
        raise ValueError(f"Template with ID {template_id} not found")
    
    # Check if template has instances
    if not force and template.instances:
        raise ValueError(
            f"Template {template.name} has {len(template.instances)} instances. "
            f"Use force=True to delete anyway."
        )
    
    # If force is True and template has instances, update them to no longer
    # reference this template
    if force and template.instances:
        for instance in template.instances:
            instance.template_id = None
        session.commit()
    
    # Delete the template
    session.delete(template)
    session.commit()
    return True

def get_all_json_files():
    """Get all JSON files from the database.
    
    Returns:
        list: List of JSONFile objects from the database
    """
    from .models import JSONFile
    session = _get_session()
    return session.query(JSONFile).all()

def find_orphaned_json_files():
    """Find JSON files in the database that no longer exist on disk.
    
    Returns:
        list: List of JSONFile objects that don't exist on disk
    """
    from .models import JSONFile
    session = _get_session()
    all_files = session.query(JSONFile).all()
    
    orphaned = []
    for json_file in all_files:
        if not os.path.exists(json_file.path) and (
            json_file.archived_path is None or not os.path.exists(json_file.archived_path)
        ):
            orphaned.append(json_file)
            
    return orphaned

def cleanup_orphaned_json_files(delete=False):
    """Find and optionally delete orphaned JSON files from the database.
    
    Args:
        delete (bool): If True, delete orphaned files from database
            If False, just return the list without deleting
            
    Returns:
        list: List of orphaned JSON file objects
    """
    orphaned = find_orphaned_json_files()
    
    if delete and orphaned:
        session = _get_session()
        for json_file in orphaned:
            logger.info(f"Deleting orphaned JSON file: {json_file.name} (ID: {json_file.id})")
            session.delete(json_file)
        session.commit()
    
    return orphaned
