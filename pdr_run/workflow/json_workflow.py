"""JSON workflow integration for PDR model execution."""

import os
import logging
from pathlib import Path

# Import everything directly from json_handlers
from pdr_run.database.json_handlers import (
    load_json_template,
    apply_parameters_to_json,
    save_json_config,
    copy_json_file,
    get_json_hash,
    register_json_template,
    process_json_template,
    prepare_job_json, 
    archive_job_json,
    register_json_file,
    update_job_output_json
)

logger = logging.getLogger("dev")

def prepare_json_config(job_id, template_path, parameters, tmp_dir):
    """Prepare JSON configuration for a PDR model job.
    
    Args:
        job_id: ID of the PDRModelJob
        template_path: Path to the JSON template file
        parameters: Dict of parameters to apply
        tmp_dir: Temporary directory for model execution
        
    Returns:
        str: Path to the prepared JSON config file
    """
    # Load template
    template_data = load_json_template(template_path)
    
    # Register template in database
    template_name = os.path.basename(template_path)
    template = register_json_template(template_name, template_path)
    
    # Apply parameters
    config_data = apply_parameters_to_json(template_data, parameters)
    
    # Save to temporary directory
    config_filename = f"config_{job_id}.json"
    config_path = os.path.join(tmp_dir, config_filename)
    save_json_config(config_data, config_path)
    
    # Register in database
    register_json_file(job_id, config_filename, config_path, template.id)
    
    return config_path

def archive_json_output(job_id, tmp_dir, output_filename, archive_dir):
    """Archive JSON output files to the model directory.
    
    Args:
        job_id: ID of the PDRModelJob
        tmp_dir: Temporary directory containing the output
        output_filename: Name of the JSON output file
        archive_dir: Directory where to archive the file
        
    Returns:
        str: Path to the archived file
    """
    tmp_path = os.path.join(tmp_dir, output_filename)
    
    if not os.path.exists(tmp_path):
        logger.warning(f"JSON output file not found: {tmp_path}")
        return None
    
    # Archive the file
    archived_path = archive_job_json(job_id, tmp_path, archive_dir)
    
    # Update job record with output JSON reference
    update_job_output_json(job_id, archived_path)
    
    logger.info(f"Archived JSON output for job {job_id}: {archived_path}")
    return archived_path