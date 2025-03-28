"""Model execution module for KOSMA-tau PDR code.

This module provides functions to execute the KOSMA-tau PDR model with 
various parameter configurations. It handles job preparation, execution,
and result processing.
"""

import os
import tempfile
import subprocess
import logging
from datetime import datetime

from pdr_run.database.connection import (
    get_session, prepare_job_json, archive_job_json
)

logger = logging.getLogger("dev")

def run_kosma_tau(job_id, template_id=None, parameters=None, tmp_dir=None):
    """Run KOSMA-tau PDR model with JSON parameter file.
    
    Args:
        job_id: ID of the PDRModelJob
        template_id: Optional template ID to use as base
        parameters: Dictionary of parameters to substitute in the template
        tmp_dir: Temporary directory for job execution
        
    Returns:
        bool: True if successful, False otherwise
    """
    from pdr_run.database.models import PDRModelJob, KOSMAtauExecutable
    
    session = get_session()
    job = session.query(PDRModelJob).get(job_id)
    
    if not job:
        logger.error(f"Job {job_id} not found")
        return False
    
    # Create temp directory if not provided
    if tmp_dir is None:
        tmp_dir = tempfile.mkdtemp(prefix=f"pdr_job_{job_id}_")
    elif not os.path.exists(tmp_dir):
        os.makedirs(tmp_dir)
    
    # Update job status
    job.status = "running"
    job.start_time = datetime.now()
    session.commit()
    
    try:
        # Prepare JSON parameter file
        json_path = prepare_job_json(job_id, template_id, parameters, tmp_dir)
        if not json_path:
            raise ValueError("Failed to prepare JSON parameter file")
        
        # Get executable information
        exe = session.query(KOSMAtauExecutable).get(job.kosmatau_executable_id)
        if not exe:
            raise ValueError(f"Executable with ID {job.kosmatau_executable_id} not found")
        
        # Set up environment for the PDR code
        # Copy necessary input files, set up directories, etc.
        # ...
        
        # Run the PDR code with the JSON parameter file
        logger.info(f"Running KOSMA-tau with JSON parameters: {json_path}")
        process = subprocess.run(
            [exe.pdr_path, "--json", json_path],
            cwd=tmp_dir,
            capture_output=True,
            text=True
        )
        
        if process.returncode != 0:
            logger.error(f"KOSMA-tau execution failed: {process.stderr}")
            job.status = "failed"
            job.error_message = process.stderr
            session.commit()
            return False
        
        # Process outputs
        output_json = os.path.join(tmp_dir, "output.json")
        if os.path.exists(output_json):
            # Archive the output JSON file
            archive_dir = os.path.join(job.model_output_path, "json")
            archived_path = archive_job_json(job_id, output_json, archive_dir)
        
        # Update job status
        job.status = "completed"
        job.end_time = datetime.now()
        session.commit()
        
        return True
        
    except Exception as e:
        logger.exception(f"Error running KOSMA-tau job {job_id}: {e}")
        job.status = "failed"
        job.error_message = str(e)
        session.commit()
        return False
    finally:
        # Cleanup if needed
        pass
    
  
def run_kosma_tau_with_json(job_id, template_id=None, parameters=None, tmp_dir='./'):
    """Run KOSMA-tau with JSON parameters while maintaining compatibility with existing workflow."""
    from pdr_run.database.connection import prepare_job_json, archive_job_json
    
    # Prepare JSON file if needed
    json_path = None
    if template_id or parameters:
        json_path = prepare_job_json(job_id, template_id, parameters, tmp_dir)
    
    # Run the standard KOSMA-tau workflow
    run_kosma_tau(job_id, tmp_dir)
    
    # Archive JSON results if relevant
    if json_path and os.path.exists(os.path.join(tmp_dir, "output.json")):
        session = get_session()
        job = session.get(PDRModelJob, job_id)
        archive_dir = os.path.join(job.model_name.model_path, "json")
        archived_path = archive_job_json(job_id, os.path.join(tmp_dir, "output.json"), archive_dir)

        