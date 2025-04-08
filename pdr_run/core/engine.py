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
import multiprocessing
import time
from datetime import datetime
from joblib import Parallel, delayed

from pdr_run.config.default_config import (
    DEFAULT_PARAMETERS, PDR_CONFIG, PDR_OUT_DIRS, PDR_INP_DIRS
)
from pdr_run.database.connection import get_session
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

# Configure logger with more detail
logger = logging.getLogger('dev')

def setup_model_directories(model_path):
    """Set up model storage directories."""
    logger.debug(f"Setting up model directories at {model_path}")
    
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
    
    session = get_session()
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
    #alpha = from_string_to_par(alpha_param[0]) if isinstance(alpha_param, list) else from_string_to_par(alpha_param)
    #rcore = from_string_to_par(rcore_param[0]) if isinstance(rcore_param, list) else from_string_to_par(rcore_param)
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
            #'preshh2': from_string_to_par(p[4]),
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
        #model = f"{p[0]}_{p[1]}_{p[2]}_{p[3]}_{p[4]}"
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

def run_instance(job_id, config=None, force_onion=False):
    """Run a single PDR model instance.
    
    Args:
        job_id (int): Job ID
        config (dict, optional): Configuration. Defaults to None.
        force_onion (bool): If True, run onion even if PDR model was skipped
    """
    start_time = time.time()
    logger.info(f"Starting job {job_id} at {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    
    session = get_session()
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
    
    try:
        # Change to PDR directory
        os.chdir(pdr_dir)
        logger.debug(f"Changed working directory to: {pdr_dir}")
        
        # Create temporary directory
        with tempfile.TemporaryDirectory(prefix='pdr-') as tmp_dir:
            logger.info(f"Created temporary directory: {tmp_dir}")
            
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
            
            # Detailed executable info
            pdr_file_name = config['pdr'].get('pdr_file_name', PDR_CONFIG['pdr_file_name'])
            onion_file_name = config['pdr'].get('onion_file_name', PDR_CONFIG['onion_file_name'])
            getctrlind_file_name = config['pdr'].get('getctrlind_file_name', PDR_CONFIG['getctrlind_file_name'])
            mrt_file_name = config['pdr'].get('mrt_file_name', PDR_CONFIG['mrt_file_name'])
            
            logger.debug(f"Using executables: PDR={pdr_file_name}, Onion={onion_file_name}, "
                         f"GetCtrlInd={getctrlind_file_name}, MRT={mrt_file_name}")
            
            # Check if executables exist
            for exe in [pdr_file_name, onion_file_name, getctrlind_file_name, mrt_file_name]:
                exe_path = os.path.join(pdr_dir, exe)
                if not os.path.exists(exe_path):
                    logger.error(f"Executable not found: {exe_path}")
                    return [f"Error: Executable {exe_path} not found"]
            
            # Set up necessary symlinks and files
            pdrinp_template_file = config['pdr'].get('pdrinp_template_file', PDR_CONFIG['pdrinp_template_file'])
            json_template_file = config['pdr'].get('json_template_file', PDR_CONFIG['json_template_file'])
            chem_database = config['pdr'].get('chem_database', PDR_CONFIG['chem_database'])
            
            logger.debug(f"Using template file: PDRINP={pdrinp_template_file}, JSON={json_template_file}, ChemRates={chem_database}")

            # Remove existing chem_rates.dat and create symlink
            chem_rates_path = os.path.join(tmp_dir, 'pdrinpdata', 'chem_rates.dat')
            if os.path.exists(chem_rates_path):
                os.remove(chem_rates_path)
            
            os.symlink(
                os.path.join(tmp_dir, 'pdrinpdata', chem_database),
                chem_rates_path
            )
            
            # Copy executables
            for exe in [pdr_file_name, onion_file_name, getctrlind_file_name, mrt_file_name]:
                os.symlink(os.path.join(pdr_dir, exe), os.path.join(tmp_dir, exe))
            
            # Find and link template files correctly - check likely locations
            template_dirs = ['templates', 'pdrinpdata/templates', '.']
            pdrinp_found = False
            json_found = False
            
            # Try to find templates in standard locations
            for template_dir in template_dirs:
                # Check for PDRNEW.INP.template
                if not pdrinp_found:
                    src_path = os.path.join(pdr_dir, template_dir, pdrinp_template_file)
                    if os.path.exists(src_path):
                        # If template exists in tmp_dir, use it directly
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
                        # If template exists in tmp_dir, use it directly
                        tmp_template_path = os.path.join(tmp_dir, template_dir, json_template_file)
                        if os.path.exists(tmp_template_path):
                            os.symlink(tmp_template_path, os.path.join(tmp_dir, json_template_file))
                        else:
                            os.symlink(src_path, os.path.join(tmp_dir, json_template_file))
                        json_found = True
                        logger.debug(f"Found JSON template at: {src_path}")
            
            # Log warning if templates not found
            if not pdrinp_found:
                logger.warning(f"PDRNEW template file {pdrinp_template_file} not found in standard locations")
            
            if not json_found:
                logger.warning(f"JSON template file {json_template_file} not found in standard locations")
            
            # Change to temporary directory
            os.chdir(tmp_dir)

                    
            # Pre-generate input files from templates for debugging
            from pdr_run.models.kosma_tau import create_pdrnew_from_job_id, create_json_from_job_id
            session = get_session()
            logger.info(f"Pre-generating input files from templates...")
            create_pdrnew_from_job_id(job_id, session)
            create_json_from_job_id(job_id, session)

            # Now add the debugging pause
            #logger.info(f"DEBUGGING: Temporary directory at {tmp_dir}")
            #logger.info(f"DEBUGGING: Pausing for 300 seconds to allow inspection")
            #logger.info(f"DEBUGGING: Generated PDRNEW.INP and pdr_config.json are now available")
            #time.sleep(300)  # Pause for 5 minutes
            
            # Create log file
            with open("out.log", "w") as log_file:
                log_file.write(f"Running instance for job ID: {job_id}\n")
            
            # Run KOSMA-tau
            run_kosma_tau(job_id, tmp_dir, force_onion=force_onion)
            
            # Read log file
            with open("out.log", "r") as log_file:
                output_lines = log_file.read().splitlines()
            
            # Return to original directory
            os.chdir(current_dir)
            
            return output_lines
    
    except Exception as e:
        logger.error(f"Error running job {job_id}: {str(e)}", exc_info=True)
        
        # Update job status
        update_job_status(job_id, "exception")
        
        # Return to original directory
        os.chdir(current_dir)
        
        return [f"Error: {str(e)}"]

def run_instance_wrapper(job_id, config=None, force_onion=False):
    """Wrapper function for run_instance to handle exceptions.
    
    Args:
        job_id (int): Job ID
        config (dict, optional): Configuration. Defaults to None.
        force_onion (bool): If True, run onion even if PDR model was skipped
    """
    try:
        output = run_instance(job_id, config, force_onion=force_onion)
        for line in output:
            logger.info(f"[Job {job_id}]: {line}")
    except Exception as e:
        logger.error(f"Error in job {job_id}: {str(e)}", exc_info=True)
        
        # Update job status
        session = get_session()
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

def run_parameter_grid(params=None, model_name=None, config=None, parallel=True, n_workers=None, force_onion=False):
    """Run a grid of PDR models with different parameters."""
    start_time = time.time()
    logger.info(f"Starting parameter grid execution for model '{model_name}'")
    logger.info(f"Parallel execution: {'enabled' if parallel else 'disabled'}")
    
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
        config = {
            'pdr': PDR_CONFIG,
            'parameters': params
        }
        logger.info("No configuration provided, using default configuration")
    
    # Make sure we have a model path
    pdr_dir = config['pdr'].get('base_dir', PDR_CONFIG['base_dir'])
    model_path = os.path.join(pdr_dir, model_name)
    logger.info(f"Model path: {model_path}")
    
    # Check if PDR directory exists
    if not os.path.exists(pdr_dir):
        logger.error(f"PDR directory does not exist: {pdr_dir}")
        logger.debug(f"Environment variables: PDR_BASE_DIR={os.environ.get('PDR_BASE_DIR', 'not set')}")
        return []
    
    # Set up model directories
    setup_model_directories(model_path)
    
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
            delayed(run_instance_wrapper)(job_id, config, force_onion=force_onion)
            for job_id in job_ids
        )
    else:
        for job_id in job_ids:
            run_instance_wrapper(job_id, config, force_onion=force_onion)
    
    return job_ids

# Also make this accessible from run_parameter_grid
run_parameter_grid._calculate_cpu_count = _calculate_cpu_count

def run_model(params=None, model_name=None, config=None, force_onion=False):
    """Run a single PDR model.
    
    Args:
        params (dict, optional): Parameter configuration. Defaults to None.
        model_name (str, optional): Model name. Defaults to None.
        config (dict, optional): Framework configuration. Defaults to None.
        
    Returns:
        int: Job ID
    """
    if params is None:
        params = DEFAULT_PARAMETERS
    
    # Take just the first combination of parameters
    #for key in ['metal', 'dens', 'mass', 'chi', 'col']:
    for key in ['metal', 'dens', 'mass', 'chi']:
        if isinstance(params[key], list) and len(params[key]) > 1:
            params[key] = [params[key][0]]
    
    job_ids = run_parameter_grid(
        params=params,
        model_name=model_name,
        config=config,
        parallel=False
    )
    
    if job_ids:
        return job_ids[0]
    return None