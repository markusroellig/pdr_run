"""Generic model execution interface."""


import logging
from pdr_run.database.connection import get_session

logger = logging.getLogger("dev")

def run_model(job_id, model_type="kosma_tau", **kwargs):
    """Generic model runner that dispatches to specific model implementations.
    
    Args:
        job_id: ID of the model job
        model_type: Type of model to run ('kosma_tau', 'future_model', etc.)
        **kwargs: Additional arguments passed to model-specific runner
        
    Returns:
        bool: Success status
    """
    logger.info(f"Running {model_type} model for job {job_id}")
    
    if model_type == "kosma_tau":
        from pdr_run.models.kosma_tau import run_kosma_tau
        run_kosma_tau(job_id, **kwargs)
        return True
    elif model_type == "future_model":
        # from pdr_run.models.future_model import run_future_model
        # run_future_model(job_id, **kwargs)
        raise NotImplementedError("Future model not implemented")
    else:
        raise ValueError(f"Unknown model type: {model_type}")

# import os
# import tempfile
# import subprocess
# import logging
# from datetime import datetime

# from pdr_run.database.connection import (
#     get_session, prepare_job_json, archive_job_json
# )

# logger = logging.getLogger("dev")

# def run_kosma_tau(job_id, template_id=None, parameters=None, tmp_dir=None, force_onion=False):
#     """Run KOSMA-tau PDR model with JSON parameter file.
    
#     Args:
#         job_id: ID of the PDRModelJob
#         template_id: Optional template ID to use as base
#         parameters: Dictionary of parameters to substitute in the template
#         tmp_dir: Temporary directory for job execution
#         force_onion: If True, run onion even if PDR model was skipped
        
#     Returns:
#         bool: True if successful, False otherwise
#     """
#     from pdr_run.database.models import PDRModelJob, KOSMAtauExecutable
    
#     session = get_session()
#     job = session.get(PDRModelJob, job_id)
    
#     if not job:
#         logger.error(f"Job {job_id} not found")
#         return False
    
#     # Create temp directory if not provided
#     if tmp_dir is None:
#         tmp_dir = tempfile.mkdtemp(prefix=f"pdr_job_{job_id}_")
#     elif not os.path.exists(tmp_dir):
#         os.makedirs(tmp_dir)
    
#     # Update job status
#     job.status = "running"
#     job.start_time = datetime.now()
#     session.commit()
    
#     try:
#         # Prepare JSON parameter file
#         json_path = prepare_job_json(job_id, template_id, parameters, tmp_dir)
#         if not json_path:
#             raise ValueError("Failed to prepare JSON parameter file")
        
#         # Get executable information
#         exe = session.get(KOSMAtauExecutable, job.kosmatau_executable_id)
#         if not exe:
#             raise ValueError(f"Executable with ID {job.kosmatau_executable_id} not found")
        
#         # Set up environment for the PDR code
#         # Copy necessary input files, set up directories, etc.
#         # ...
        
#         # Run the PDR code with the JSON parameter file
#         logger.info(f"Running KOSMA-tau with JSON parameters: {json_path}")
#         process = subprocess.run(
#             [exe.pdr_path, "--json", json_path],
#             cwd=tmp_dir,
#             capture_output=True,
#             text=True
#         )
        
#         if process.returncode != 0:
#             logger.error(f"KOSMA-tau execution failed: {process.stderr}")
#             job.status = "failed"
#             job.error_message = process.stderr
#             session.commit()
#             return False
        
#         # Process outputs
#         output_json = os.path.join(tmp_dir, "output.json")
#         if os.path.exists(output_json):
#             # Archive the output JSON file
#             archive_dir = os.path.join(job.model_output_path, "json")
#             archived_path = archive_job_json(job_id, output_json, archive_dir)
        
#         # Update job status
#         job.status = "completed"
#         job.end_time = datetime.now()
#         session.commit()
        
#         return True
        
#     except Exception as e:
#         logger.exception(f"Error running KOSMA-tau job {job_id}: {e}")
#         job.status = "failed"
#         job.error_message = str(e)
#         session.commit()
#         return False
#     finally:
#         # Cleanup if needed
#         pass
    
  
# def run_kosma_tau_with_json(job_id, template_id=None, parameters=None, tmp_dir='./', force_onion=False):
#     """Run KOSMA-tau with JSON parameters while maintaining compatibility with existing workflow.
    
#     Args:
#         job_id: ID of the PDRModelJob
#         template_id: Optional template ID to use as base
#         parameters: Dictionary of parameters to substitute in the template
#         tmp_dir: Temporary directory for job execution
#         force_onion: If True, run onion even if PDR model was skipped
        
#     Returns:
#         bool: True if successful, False otherwise
#     """
#     from pdr_run.database.connection import prepare_job_json, archive_job_json
    
#     # Prepare JSON file if needed
#     json_path = None
#     if template_id or parameters:
#         json_path = prepare_job_json(job_id, template_id, parameters, tmp_dir)
    
#     # Run the standard KOSMA-tau workflow with force_onion parameter
#     run_kosma_tau(job_id, tmp_dir=tmp_dir, force_onion=force_onion)
    
#     # Archive JSON results if relevant
#     if json_path and os.path.exists(os.path.join(tmp_dir, "output.json")):
#         session = get_session()
#         job = session.get(PDRModelJob, job_id)
#         archive_dir = os.path.join(job.model_name.model_path, "json")
#         archived_path = archive_job_json(job_id, os.path.join(tmp_dir, "output.json"), archive_dir)
        
#     return True

