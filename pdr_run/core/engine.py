"""Core engine for running PDR (Photo-Dissociation Region) models.

This module serves as the central execution engine for the PDR modeling framework,
providing functionality to configure, run, and manage PDR model calculations with 
various parameter combinations.

Key Functionality:
-----------------
1. Model Setup and Configuration:
   - Directory structure creation and management
   - Parameter combination generation from configuration
   - Database entry creation for tracking model runs

2. Execution Management:
   - Single model instance execution
   - Parallel execution of parameter grids
   - CPU resource management
   - Temporary working directory handling

3. Database Integration:
   - Tracking model parameters, jobs, and execution states
   - Recording relationships between models, parameters, and executables
   - Storing information about chemical databases and user details

Main Components:
--------------
- setup_model_directories: Creates required directory structure for model output
- create_database_entries: Records model configuration in database for tracking
- run_instance: Executes a single PDR model with specific parameters
- run_parameter_grid: Runs multiple parameter combinations, optionally in parallel
- run_model: Convenience function for running a single parameter combination

Technical Implementation:
----------------------
The module implements a flexible execution environment that:
- Creates isolated temporary directories for each model run
- Sets up necessary symlinks and input files
- Manages executable paths and chemical database configurations
- Records detailed provenance information (SHA256 checksums, code revisions)
- Handles parallel execution using joblib for efficient resource utilization

Execution Flow:
-------------
1. Parameter combinations are generated from configuration
2. Database entries are created for tracking
3. For each parameter set:
   a. A temporary directory is created with proper structure
   b. Input files and executables are prepared
   c. The PDR model is executed
   d. Results are stored and database is updated

Usage Examples:
-------------
# Run a model with default parameters
job_id = run_model()

# Run a parameter grid in parallel
params = {'chi': ['1.0', '10.0', '100.0'], 'dens': ['3.0', '4.0']}
job_ids = run_parameter_grid(params=params, model_name='uv_field_study')

Dependencies:
-----------
- Database models and connection management
- File and directory utilities
- Parameter handling functions
- KOSMA-tau model execution code
"""

import os
import logging
import tempfile
import shutil
import multiprocessing
import time
import traceback
from datetime import datetime
from joblib import Parallel, delayed

from pdr_run.config.default_config import (
    DEFAULT_PARAMETERS, PDR_CONFIG, PDR_OUT_DIRS, PDR_INP_DIRS
)
from pdr_run.database import get_db_manager
from pdr_run.database.queries import (
    get_or_create, get_model_name_id, update_job_status
)
from pdr_run.database.models import (
    KOSMAtauExecutable, User, ChemicalDatabase, 
    PDRModelJob, KOSMAtauParameters, ModelNames
)
from pdr_run.io.file_manager import (
    create_dir, copy_dir, get_code_revision, 
    get_compilation_date, get_digest
)
from pdr_run.models.parameters import (
    generate_parameter_combinations, list_to_string, compute_radius,
    from_par_to_string, from_string_to_par, from_par_to_string_log
)
from pdr_run.models.kosma_tau import run_kosma_tau
from pdr_run.storage.base import get_storage_backend

# Configure logger with more detail
logger = logging.getLogger('dev')

def setup_model_directories(model_path, config=None):
    """Set up model storage directories."""
    logger.debug(f"Setting up model directories at {model_path}")

    # Check if we're using remote storage
    storage_backend = get_storage_backend(config)
    logger.debug(f"Storage backend type: {type(storage_backend)}")
    logger.debug(f"Storage backend class: {storage_backend.__class__.__name__}")

    if hasattr(storage_backend, 'host'):
        logger.debug("Using remote storage backend")
        logger.debug(f"Remote host: {storage_backend.host}")
        logger.debug(f"Remote user: {storage_backend.user}")
        logger.debug(f"Remote base_dir: {storage_backend.base_dir}")
    else:
        logger.debug("Using local storage backend")
        logger.debug(f"Local base_dir: {storage_backend.base_dir}")

    
    pdrgrid_path = os.path.join(model_path, 'pdrgrid')
    oniongrid_path = os.path.join(model_path, 'oniongrid')
    
    # Log existing directories
    if os.path.exists(pdrgrid_path):
        logger.warning(f"Directory {pdrgrid_path} already exists")
    if os.path.exists(oniongrid_path):
        logger.warning(f"Directory {oniongrid_path} already exists")
    
    create_dir(pdrgrid_path)
    create_dir(oniongrid_path)
    
    logger.debug(f"Model directories setup complete: {pdrgrid_path}, {oniongrid_path}")

def create_database_entries(model_name, model_path, param_combinations, config=None):
    """Create database entries for model runs."""
    start_time = time.time()
    logger.info(f"Creating database entries for model '{model_name}' at '{model_path}'")
    logger.debug(f"Parameter combinations: {len(param_combinations)} total")
    
    # Extract database config if available
    db_config = None
    if config and 'database' in config:
        db_config = config['database']
        logger.debug(f"Using database config from provided config: {db_config}")
        logger.debug(f"Database config type: {type(db_config)}")
        logger.debug(f"Database config items: {list(db_config.items()) if isinstance(db_config, dict) else 'Not a dict'}")

    
    db_manager = get_db_manager(db_config)  # Pass the database config
    
    # CREATE TABLES BEFORE GETTING SESSION
    try:
        db_manager.create_tables()
        logger.info("Database tables created successfully")
    except Exception as e:
        logger.error(f"Failed to create database tables: {e}")
        raise
    
    session = db_manager.get_session()
    logger.debug(f"Database session established")
    
    # Get configuration
    if config is None:
        logger.info("No config provided, using defaults")
        config = {
            'pdr': PDR_CONFIG,
            'parameters': DEFAULT_PARAMETERS
        }
    
    # Map model_params to parameters for backward compatibility
    if 'model_params' in config and 'parameters' not in config:
        logger.info("Mapping 'model_params' to 'parameters' for compatibility")
        config['parameters'] = config['model_params']
    
    # Log configuration details
    logger.debug("Configuration details:")
    for section_name, section in config.items():
        if isinstance(section, dict):
            logger.debug(f"  {section_name}:")
            for key, value in section.items():
                logger.debug(f"    {key}: {value}")
        else:
            logger.debug(f"  {section_name}: {section}")
    
    # Get executable info
    pdr_file_name = config['pdr'].get('pdr_file_name', PDR_CONFIG['pdr_file_name'])
    pdr_dir = config['pdr'].get('base_dir', PDR_CONFIG['base_dir'])
    
    full_pdr_path = os.path.join(pdr_dir, pdr_file_name)
    logger.info(f"Using PDR executable: {full_pdr_path}")
    
    # Check executable exists
    if not os.path.exists(full_pdr_path):
        logger.error(f"PDR executable not found at {full_pdr_path}")
        logger.debug(f"Directory contents of {pdr_dir}: {os.listdir(pdr_dir) if os.path.exists(pdr_dir) else 'directory not found'}")
    
    # Create executable entry
    logger.debug(f"Getting code revision for {full_pdr_path}")
    code_revision = get_code_revision(full_pdr_path)
    compilation_date = get_compilation_date(full_pdr_path)
    sha256_sum = get_digest(full_pdr_path)
    
    logger.info(f"Executable: {pdr_file_name}")
    logger.info(f"Code revision: {code_revision}")
    logger.info(f"Compiled at: {compilation_date.strftime('%c')}")
    logger.info(f"SHA-256 hash: {sha256_sum}")
    
    exe = get_or_create(
        session,
        KOSMAtauExecutable,
        code_revision=code_revision,
        compilation_date=compilation_date,
        executable_file_name=pdr_file_name,
        executable_full_path=pdr_dir,
        sha256_sum=sha256_sum
    )
    
    # Create user entry
    username = config.get('user', {}).get('username', 'Default User')
    email = config.get('user', {}).get('email', 'user@example.com')
    
    logger.info(f"User: {username}, Email: {email}")
    
    user = get_or_create(
        session,
        User,
        username=username,
        email=email
    )
    
    # Create model name entry
    model_name_id = get_model_name_id(model_name, model_path, session)
    
    logger.info(f"Model name: {model_name}")
    logger.info(f"Model ID: {model_name_id}")
    
    # Create chemical database entry
    chem_database = config['pdr'].get(
        'chem_database', 
        PDR_CONFIG['chem_database']
    )
    chem_origin = config['pdr'].get(
        'chem_origin', 
        PDR_CONFIG['chem_origin']
    )
    
    chem = get_or_create(
        session,
        ChemicalDatabase,
        chem_rates_file_name=chem_database,
        chem_rates_full_path=os.path.join(pdr_dir, 'pdrinpdata', chem_database),
        database_origin=chem_origin
    )
    
    logger.info(f"Chemical database: {chem_database}")
    logger.info(f"Chemical database ID: {chem.id}")
    
    # Create parameter and job entries
    parameter_ids = []
    job_ids = []
    
    # Fix here: Extract single values for alpha and rcore
    alpha_param = config['parameters'].get('alpha', DEFAULT_PARAMETERS['alpha'])
    rcore_param = config['parameters'].get('rcore', DEFAULT_PARAMETERS['rcore'])
    
    # Convert to numeric if list
    alpha = alpha_param[0] if isinstance(alpha_param, list) else alpha_param
    rcore = rcore_param[0] if isinstance(rcore_param, list) else rcore_param
 
    logger.debug(f"Using alpha={alpha} (from {alpha_param}) and rcore={rcore} (from {rcore_param})")
    
    species = config['parameters'].get('species', DEFAULT_PARAMETERS['species'])
    chemistry = config['parameters'].get('chemistry', DEFAULT_PARAMETERS.get('chemistry', ['umist']))
    
    for p in param_combinations:
        logger.info(f"Creating entry for parameters: {p}")
        
        # Calculate radius
        rtot = compute_radius(
            from_string_to_par(p[2]),  # mass
            from_string_to_par(p[1]),  # density
            alpha,
            rcore
        )
        
        # Create parameter entry
        param_dict = {
            'zmetal': eval(p[0]) * 0.01,
            'xnsur': from_string_to_par(p[1]),
            'alpha': alpha,
            'rcore': rcore,
            'rtot': rtot,
            'mass': from_string_to_par(p[2]),
            'sint': from_string_to_par(p[3]),
            'model_name_id': model_name_id,
            'species': list_to_string(chemistry) if isinstance(chemistry, list) else chemistry
        }
        
        # Add non-default parameters
        non_default_params = config.get('non_default_parameters', {})
        param_dict.update(non_default_params)
        
        # Ensure all list parameters are properly converted to strings
        for key, value in param_dict.items():
            if isinstance(value, list):
                param_dict[key] = list_to_string(value)
        
        param = get_or_create(session, KOSMAtauParameters, **param_dict)
        parameter_ids.append(param.id)
        
        # Create job entry
        model = f"{p[0]}_{p[1]}_{p[2]}_{p[3]}_00"
        
        job = get_or_create(
            session,
            PDRModelJob,
            model_name_id=model_name_id,
            model_job_name=model,
            user_id=user.id,
            kosmatau_parameters_id=param.id,
            kosmatau_executable_id=exe.id,
            output_directory=model_path,
            output_hdf4_file=f'pdr{model}.hdf',
            pending=True,
            onion_species=list_to_string(species),
            chemical_database_id=chem.id
        )
        
        job_ids.append(job.id)
    
    logger.info(f"Created {len(parameter_ids)} parameter sets")
    logger.info(f"Created {len(job_ids)} job entries")
    
    return parameter_ids, job_ids


def run_instance(job_id, config=None, force_onion=False, json_template=None, keep_tmp=False):
    """Run a single PDR model instance.
    
    This function handles the infrastructure setup (directories, executables, templates)
    and delegates the actual model execution to the model-specific implementation.
        Args:
        job_id (int): Job ID
        config (dict, optional): Configuration. Defaults to None.
        force_onion (bool): If True, run onion even if PDR model was skipped.
        json_template (str, optional): Path to a user-supplied JSON template. Defaults to None.
        keep_tmp (bool): If True, do not delete temporary directory after run.
    Returns:
        list: Log lines or error messages.
    """
    #start_time = time.time()
    start_time_instance = time.time()
    logger.info(f"Starting job {job_id} at {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    logger.info(f"keep_tmp flag for job {job_id}: {keep_tmp}")

    
    db_manager = get_db_manager()
    session = db_manager.get_session()
    job = session.get(PDRModelJob, job_id)
    
    if not job:
        logger.error(f"Job with ID {job_id} not found in database")
        return []
    
    if config is None:
        logger.debug(f"No config provided for job {job_id}, using defaults")
        config = {'pdr': PDR_CONFIG}
    
    pdr_dir = config['pdr'].get('base_dir', PDR_CONFIG['base_dir'])
    logger.debug(f"PDR base directory: {pdr_dir}")
    
    if not os.path.exists(pdr_dir):
        logger.error(f"PDR directory does not exist: {pdr_dir}")
        return [f"Error: PDR directory {pdr_dir} does not exist"]
    
    # Save current directory
    current_dir = os.getcwd()
    logger.debug(f"Current working directory: {current_dir}")
    
    
    def _execute_in_tmp_dir(tmp_dir_path_local):
        original_cwd_for_execute = os.getcwd() 
        try:
            _setup_execution_environment(tmp_dir_path_local, pdr_dir, config, json_template)
            os.chdir(tmp_dir_path_local)
            logger.info(f"Job {job_id}: Changed working directory to temporary directory: {tmp_dir_path_local}")
            
            run_kosma_tau(job_id, tmp_dir_path_local, force_onion=force_onion, config=config)
            
            return [f"Job {job_id}: Execution in {tmp_dir_path_local} completed. Check logs for details."]
        except Exception as exec_err:
            logger.error(f"Job {job_id}: Error during execution in {tmp_dir_path_local}: {exec_err}", exc_info=True)
            err_session_exec = get_db_manager().get_session()
            try:
                update_job_status(job_id, "exception_runtime", err_session_exec) 
            finally:
                err_session_exec.close()
            raise 
        finally:
            os.chdir(original_cwd_for_execute)
            logger.info(f"Job {job_id}: Restored working directory from temp to: {original_cwd_for_execute}")

    output_lines_result = []
    try:
        os.chdir(pdr_dir)
        logger.debug(f"Job {job_id}: Changed working directory to PDR base: {pdr_dir}")

        if keep_tmp:
            persistent_tmp_dir = tempfile.mkdtemp(prefix=f'pdr-job{job_id}-')
            logger.warning(f"Job {job_id}: Temporary directory {persistent_tmp_dir} will be kept due to --keep-tmp flag.")
            logger.warning(f"Job {job_id}: Please ensure you manually clean up this directory after debugging: {persistent_tmp_dir}")
            try:
                output_lines_result = _execute_in_tmp_dir(persistent_tmp_dir)
            except Exception as e_persistent:
                output_lines_result.extend([f"Error in persistent temp dir: {str(e_persistent)}", traceback.format_exc()])
        else:
            with tempfile.TemporaryDirectory(prefix=f'pdr-job{job_id}-') as tmp_dir_context:
                logger.info(f"Job {job_id}: Created temporary directory: {tmp_dir_context}")
                try:
                    output_lines_result = _execute_in_tmp_dir(tmp_dir_context)
                except Exception as e_context:
                    output_lines_result.extend([f"Error in temp dir: {str(e_context)}", traceback.format_exc()])
        
    except Exception as e_outer:
        logger.error(f"Job {job_id}: Outer error during setup or execution: {str(e_outer)}", exc_info=True)
        err_session_outer = get_db_manager().get_session()
        try:
            update_job_status(job_id, "exception_setup_outer", err_session_outer)
        finally:
            err_session_outer.close()
        output_lines_result.extend([f"Outer Error: {str(e_outer)}", traceback.format_exc()])
    finally:
        os.chdir(current_dir)
        logger.debug(f"Job {job_id}: Restored original working directory: {current_dir}")

    end_time_instance = time.time()
    logger.info(f"Job {job_id} finished processing in {end_time_instance - start_time_instance:.2f} seconds.")
    return output_lines_result

def _setup_execution_environment(tmp_dir, pdr_dir, config, json_template=None):
    """Set up the execution environment for PDR model runs.
    
    This function handles all the infrastructure setup that's common to all model types.
    """
    # Set up temporary directory structure
    pdr_tmp_outdirs = [os.path.join(tmp_dir, d) for d in PDR_OUT_DIRS]
    pdr_tmp_inpdirs = [os.path.join(tmp_dir, d) for d in PDR_INP_DIRS]
    
    # Create output directories
    for d in pdr_tmp_outdirs:
        create_dir(d)
        logger.debug(f"Created output directory: {d}")
    
    # Copy input directories
    for src, dst in zip(PDR_INP_DIRS, pdr_tmp_inpdirs):
        src_path = os.path.join(pdr_dir, src)
        if not os.path.exists(src_path):
            logger.warning(f"Source directory does not exist: {src_path}")
            continue
        copy_dir(src_path, dst)
        logger.debug(f"Copied input directory: {src_path} -> {dst}")
    
    # Get executable names
    pdr_file_name = config['pdr'].get('pdr_file_name', PDR_CONFIG['pdr_file_name'])
    onion_file_name = config['pdr'].get('onion_file_name', PDR_CONFIG['onion_file_name'])
    getctrlind_file_name = config['pdr'].get('getctrlind_file_name', PDR_CONFIG['getctrlind_file_name'])
    mrt_file_name = config['pdr'].get('mrt_file_name', PDR_CONFIG['mrt_file_name'])
    
    logger.debug(f"Using executables: PDR={pdr_file_name}, Onion={onion_file_name}, "
                 f"GetCtrlInd={getctrlind_file_name}, MRT={mrt_file_name}")
    
    # Check if executables exist and create symlinks
    for exe in [pdr_file_name, onion_file_name, getctrlind_file_name, mrt_file_name]:
        exe_path = os.path.join(pdr_dir, exe)
        if not os.path.exists(exe_path):
            logger.error(f"Executable not found: {exe_path}")
            raise FileNotFoundError(f"Executable {exe_path} not found")
        os.symlink(exe_path, os.path.join(tmp_dir, exe))
    
    # Set up chemical database
    chem_database = config['pdr'].get('chem_database', PDR_CONFIG['chem_database'])
    chem_rates_path = os.path.join(tmp_dir, 'pdrinpdata', 'chem_rates.dat')
    if os.path.exists(chem_rates_path):
        os.remove(chem_rates_path)
    os.symlink(
        os.path.join(tmp_dir, 'pdrinpdata', chem_database),
        chem_rates_path
    )
    
    # Set up template files
    _setup_template_files(tmp_dir, pdr_dir, config, json_template)

def _setup_template_files(tmp_dir, pdr_dir, config, json_template=None):
    """Set up template files for model execution."""
    pdrinp_template_file = config['pdr'].get('pdrinp_template_file', PDR_CONFIG['pdrinp_template_file'])
    json_template_file = config['pdr'].get('json_template_file', PDR_CONFIG['json_template_file'])
    
    logger.debug(f"Setting up templates: PDRINP={pdrinp_template_file}, JSON={json_template_file}")

    # Find and link template files
    template_dirs = ['templates', 'pdrinpdata/templates', '.']
    pdrinp_found = False
    json_found = False
    
    for template_dir in template_dirs:
        # Check for PDRNEW.INP.template
        if not pdrinp_found:
            src_path = os.path.join(pdr_dir, template_dir, pdrinp_template_file)
            if os.path.exists(src_path):
                tmp_template_path = os.path.join(tmp_dir, template_dir, pdrinp_template_file)
                if os.path.exists(tmp_template_path):
                    os.symlink(tmp_template_path, os.path.join(tmp_dir, pdrinp_template_file))
                else:
                    os.symlink(src_path, os.path.join(tmp_dir, pdrinp_template_file))
                pdrinp_found = True
                logger.debug(f"Found PDRNEW template at: {src_path}")
        
        # Check for JSON template
        if not json_found:
            src_path = os.path.join(pdr_dir, template_dir, json_template_file)
            if os.path.exists(src_path):
                tmp_template_path = os.path.join(tmp_dir, template_dir, json_template_file)
                if os.path.exists(tmp_template_path):
                    os.symlink(tmp_template_path, os.path.join(tmp_dir, json_template_file))
                else:
                    os.symlink(src_path, os.path.join(tmp_dir, json_template_file))
                json_found = True
                logger.debug(f"Found JSON template at: {src_path}")
    
    # Handle user-supplied JSON template
    if json_template:
        import shutil
        dest_path = os.path.join(tmp_dir, "pdr_config.json.template")
        shutil.copy(json_template, dest_path)
        logger.info(f"Using user-supplied JSON template: {json_template}")
        json_found = True
    
    # Log warnings for missing templates
    if not pdrinp_found:
        logger.warning(f"PDRNEW template file {pdrinp_template_file} not found in standard locations")
    if not json_found:
        logger.warning(f"JSON template file {json_template_file} not found in standard locations")


def run_instance_wrapper(job_id, config=None, force_onion=False, json_template=None, keep_tmp=False):
    """Wrapper function for run_instance to handle exceptions.
    
    Args:
        job_id (int): Job ID
        config (dict, optional): Configuration. Defaults to None.
        force_onion (bool): If True, run onion even if PDR model was skipped
        json_template (str, optional): Path to a user-supplied JSON template. Defaults to None.
        keep_tmp (bool): If True, do not delete temporary directory after run.
    """
    try:
        output = run_instance(job_id, config, force_onion=force_onion, json_template=json_template, keep_tmp=keep_tmp)
        for line in output:
            logger.info(f"[Job {job_id}]: {line}")
    except Exception as e: # This catches errors re-raised from run_instance or new ones here
        logger.error(f"Critical error in run_instance_wrapper for job {job_id}: {str(e)}", exc_info=True)
        
        # Update job status
        db_manager = get_db_manager()
        session = db_manager.get_session()
        # Replace deprecated query.get() with session.get()
        job = session.get(PDRModelJob, job_id)
        if job:
            job.pending = False
            job.status = "exception"
            session.commit()

def _calculate_cpu_count(requested_cpus=0, reserved_cpus=2):
    """Calculate the number of CPUs to use.
    
    Args:
        requested_cpus: Number of CPUs requested (0 for auto)
        reserved_cpus: Number of CPUs to reserve for other tasks
        
    Returns:
        int: Number of CPUs to use
    """
    import multiprocessing
    
    available_cpus = multiprocessing.cpu_count()
    
    if requested_cpus > 0:
        return min(requested_cpus, available_cpus)
    else:
        return max(1, available_cpus - reserved_cpus)

def _build_default_config(params=None):
    """Build default configuration from default_config.py with environment variable overrides."""
    from pdr_run.config.default_config import (
        DATABASE_CONFIG, STORAGE_CONFIG, USER_CONFIG, PDR_CONFIG, DEFAULT_PARAMETERS
    )
    
    # Start with default configurations
    config = {
        'database': DATABASE_CONFIG.copy(),
        'storage': STORAGE_CONFIG.copy(),
        'user': USER_CONFIG.copy(),
        'pdr': PDR_CONFIG.copy(),
        'parameters': params or DEFAULT_PARAMETERS.copy()
    }
    
    # Override with environment variables
    # Database overrides
    if os.environ.get('PDR_DB_TYPE'):
        config['database']['type'] = os.environ['PDR_DB_TYPE']
    if os.environ.get('PDR_DB_HOST'):
        config['database']['host'] = os.environ['PDR_DB_HOST']
    if os.environ.get('PDR_DB_PORT'):
        config['database']['port'] = int(os.environ['PDR_DB_PORT'])
    if os.environ.get('PDR_DB_DATABASE'):
        config['database']['database'] = os.environ['PDR_DB_DATABASE']
    if os.environ.get('PDR_DB_USERNAME'):
        config['database']['username'] = os.environ['PDR_DB_USERNAME']
    if os.environ.get('PDR_DB_PASSWORD'):
        config['database']['password'] = os.environ['PDR_DB_PASSWORD']
    if os.environ.get('PDR_DB_FILE'):
        config['database']['path'] = os.environ['PDR_DB_FILE']
    
    # Storage overrides
    if os.environ.get('PDR_STORAGE_TYPE'):
        config['storage']['type'] = os.environ['PDR_STORAGE_TYPE']
    if os.environ.get('PDR_STORAGE_DIR'):
        config['storage']['base_dir'] = os.environ['PDR_STORAGE_DIR']
    if os.environ.get('PDR_STORAGE_HOST'):
        config['storage']['host'] = os.environ['PDR_STORAGE_HOST']
    if os.environ.get('PDR_STORAGE_USER'):
        config['storage']['username'] = os.environ['PDR_STORAGE_USER']
    if os.environ.get('PDR_STORAGE_PASSWORD'):
        config['storage']['password'] = os.environ['PDR_STORAGE_PASSWORD']
    
    # PDR overrides
    if os.environ.get('PDR_BASE_DIR'):
        config['pdr']['base_dir'] = os.environ['PDR_BASE_DIR']
    if os.environ.get('PDR_EXEC_PATH'):
        config['pdr']['base_dir'] = os.environ['PDR_EXEC_PATH']
    
    return config

def run_parameter_grid(params=None, model_name=None, config=None, parallel=True, n_workers=None, force_onion=False, json_template=None, keep_tmp=False):
    """Run a grid of PDR models with different parameters."""
    start_time = time.time()
    logger.info(f"Starting parameter grid execution for model '{model_name}'")
    logger.info(f"Parallel execution: {'enabled' if parallel else 'disabled'}")
    logger.info(f"keep_tmp flag for grid run '{model_name}': {keep_tmp}")
    
    if params is None:
        params = DEFAULT_PARAMETERS
        logger.info("Using default parameters")
    else:
        logger.info("Using custom parameters")
    
    logger.debug("Parameter grid configuration:")
    for param_name, param_values in params.items():
        if isinstance(param_values, list):
            logger.debug(f"  {param_name}: {param_values} ({len(param_values)} values)")
        else:
            logger.debug(f"  {param_name}: {param_values}")
    
    if model_name is None:
        model_name = "default_model"
        logger.info(f"No model name provided, using default: {model_name}")
    
    if config is None:
        config = _build_default_config(params)
        logger.info("No configuration provided, using default configuration with environment overrides")
    
    # Ensure config has required sections
    if 'pdr' not in config:
        logger.error("Configuration missing 'pdr' section")
        config['pdr'] = PDR_CONFIG.copy()
    
    # Get storage base directory and PDR execution directory
    pdr_dir = config['pdr'].get('base_dir', PDR_CONFIG['base_dir'])
    
    # Check if we have a separate storage directory configured
    storage_base_dir = config.get('storage', {}).get('base_dir')  # Changed from 'path' to 'base_dir'
    if storage_base_dir is None:
        # Fall back to PDR_STORAGE_DIR environment variable
        storage_base_dir = os.environ.get('PDR_STORAGE_DIR')
    if storage_base_dir is None:
        # Final fallback: use PDR base directory
        storage_base_dir = pdr_dir
        logger.info(f"No storage directory configured, using PDR base directory: {storage_base_dir}")
    else:
        logger.info(f"Using configured storage directory: {storage_base_dir}")
    
    # Model path should be in the storage directory
    model_path = os.path.join(storage_base_dir, model_name)
    logger.info(f"Model storage path: {model_path}")
    logger.info(f"PDR execution directory: {pdr_dir}")
    
    # Check if PDR directory exists
    if not os.path.exists(pdr_dir):
        logger.error(f"PDR directory does not exist: {pdr_dir}")
        logger.debug(f"Environment variables: PDR_BASE_DIR={os.environ.get('PDR_BASE_DIR', 'not set')}")
        return []
    
    # Create storage base directory if it doesn't exist
    if not os.path.exists(storage_base_dir):
        logger.info(f"Creating storage base directory: {storage_base_dir}")
        create_dir(storage_base_dir)
    
    # Set up model directories
    setup_model_directories(model_path, config)
    
    # Generate parameter combinations
    param_combinations = generate_parameter_combinations(params)
    logger.info(f"Generated {len(param_combinations)} parameter combinations")
    if len(param_combinations) <= 10:
        logger.debug(f"Parameter combinations: {param_combinations}")
    else:
        logger.debug(f"First 5 parameter combinations: {param_combinations[:5]}")
        logger.debug(f"Last 5 parameter combinations: {param_combinations[-5:]}")
    
    # Create database entries
    creation_start = time.time()
    logger.info("Creating database entries for parameter combinations")
    _, job_ids = create_database_entries(
        model_name, 
        model_path, 
        param_combinations, 
        config
    )
    logger.info(f"Database entries created in {time.time() - creation_start:.2f} seconds")
    
    # Determine number of workers
    if n_workers is None:
        n_workers = _calculate_cpu_count(reserved_cpus=params.get('reserved_cpus', 2))
    
    logger.info(f"Running on {n_workers} workers (total CPUs: {multiprocessing.cpu_count()})")
    
    # Run jobs
    if parallel:
        Parallel(n_jobs=n_workers)(
            delayed(run_instance_wrapper)(job_id, config, force_onion=force_onion, json_template=json_template, keep_tmp=keep_tmp)
            for job_id in job_ids
        )
    else:
        for job_id in job_ids:
            run_instance_wrapper(job_id, config, force_onion=force_onion, json_template=json_template, keep_tmp=keep_tmp)
    end_time = time.time()
    logger.info(f"Parameter grid execution for model '{model_name}' completed in {end_time - start_time:.2f} seconds.")
    return job_ids

# Remove this problematic line that tries to attach the function as an attribute
# run_parameter_grid._calculate_cpu_count = _calculate_cpu_count

def run_model(params=None, model_name=None, config=None, force_onion=False, json_template=None, keep_tmp=False):
    """Run a single PDR model.
    
    Args:
        params (dict, optional): Parameter configuration. Defaults to None.
        model_name (str, optional): Model name. Defaults to None.
        config (dict, optional): Framework configuration. Defaults to None.
        force_onion (bool): If True, run onion even if PDR model was skipped.
        json_template (str, optional): Path to a user-supplied JSON template. Defaults to None.
        keep_tmp (bool): If True, do not delete temporary directory after run.
        
    Returns:
        int: Job ID or None if no jobs were created/run.
    """
    if params is None:
        params = DEFAULT_PARAMETERS
    
    # Take just the first combination of parameters
    for key in ['metal', 'dens', 'mass', 'chi']:
        if isinstance(params[key], list) and len(params[key]) > 1:
            params[key] = [params[key][0]]
    
    job_ids = run_parameter_grid(
        params=params,
        model_name=model_name,
        config=config,
        parallel=False,
        json_template=json_template,
        keep_tmp=keep_tmp # Pass keep_tmp
    )
    
    if job_ids:
        return job_ids[0]
    return None