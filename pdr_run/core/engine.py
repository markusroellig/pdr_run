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

logger = logging.getLogger('dev')

def setup_model_directories(model_path):
    """Set up model storage directories.
    
    Args:
        model_path (str): Base model storage path
    """
    create_dir(os.path.join(model_path, 'pdrgrid'))
    create_dir(os.path.join(model_path, 'oniongrid'))

def create_database_entries(model_name, model_path, param_combinations, config=None):
    """Create database entries for model runs.
    
    Args:
        model_name (str): Model name
        model_path (str): Model path
        param_combinations (list): List of parameter combinations
        config (dict, optional): Configuration. Defaults to None.
        
    Returns:
        tuple: (parameter_ids, job_ids)
    """
    session = get_session()
    
    # Get configuration
    if config is None:
        config = {
            'pdr': PDR_CONFIG,
            'parameters': DEFAULT_PARAMETERS
        }
    
    # Get executable info
    pdr_file_name = config['pdr'].get('pdr_file_name', PDR_CONFIG['pdr_file_name'])
    pdr_dir = config['pdr'].get('base_dir', PDR_CONFIG['base_dir'])
    
    # Create executable entry
    code_revision = get_code_revision(os.path.join(pdr_dir, pdr_file_name))
    compilation_date = get_compilation_date(os.path.join(pdr_dir, pdr_file_name))
    sha256_sum = get_digest(os.path.join(pdr_dir, pdr_file_name))
    
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
    
    alpha = config['parameters'].get('alpha', DEFAULT_PARAMETERS['alpha'])
    rcore = config['parameters'].get('rcore', DEFAULT_PARAMETERS['rcore'])
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
            'preshh2': from_string_to_par(p[4]),
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
        model = f"{p[0]}_{p[1]}_{p[2]}_{p[3]}_{p[4]}"
        
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

def run_instance(job_id, config=None):
    """Run a single PDR model instance.
    
    Args:
        job_id (int): Job ID
        config (dict, optional): Configuration. Defaults to None.
        
    Returns:
        list: Output log lines
    """
    session = get_session()
    # Replace deprecated query.get() with session.get()
    job = session.get(PDRModelJob, job_id)
    
    if not job:
        logger.error(f"Job with ID {job_id} not found")
        return []
    
    if config is None:
        config = {'pdr': PDR_CONFIG}
    
    pdr_dir = config['pdr'].get('base_dir', PDR_CONFIG['base_dir'])
    
    # Save current directory
    current_dir = os.getcwd()
    
    try:
        # Change to PDR directory
        os.chdir(pdr_dir)
        
        # Create temporary directory
        with tempfile.TemporaryDirectory(prefix='pdr-') as tmp_dir:
            logger.info(f"Created temporary directory: {tmp_dir}")
            
            # Set up temporary directory structure
            pdr_tmp_outdirs = [os.path.join(tmp_dir, d) for d in PDR_OUT_DIRS]
            pdr_tmp_inpdirs = [os.path.join(tmp_dir, d) for d in PDR_INP_DIRS]
            
            # Create output directories
            for d in pdr_tmp_outdirs:
                create_dir(d)
            
            # Copy input directories
            for src, dst in zip(PDR_INP_DIRS, pdr_tmp_inpdirs):
                copy_dir(os.path.join(pdr_dir, src), dst)
            
            # Set up necessary symlinks and files
            pdr_file_name = config['pdr'].get('pdr_file_name', PDR_CONFIG['pdr_file_name'])
            onion_file_name = config['pdr'].get('onion_file_name', PDR_CONFIG['onion_file_name'])
            getctrlind_file_name = config['pdr'].get('getctrlind_file_name', PDR_CONFIG['getctrlind_file_name'])
            mrt_file_name = config['pdr'].get('mrt_file_name', PDR_CONFIG['mrt_file_name'])
            pdrinp_template_file = config['pdr'].get('pdrinp_template_file', PDR_CONFIG['pdrinp_template_file'])
            chem_database = config['pdr'].get('chem_database', PDR_CONFIG['chem_database'])
            
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
            
            # Copy template file
            os.symlink(os.path.join(pdr_dir, pdrinp_template_file), os.path.join(tmp_dir, pdrinp_template_file))
            
            # Change to temporary directory
            os.chdir(tmp_dir)
            
            # Create log file
            with open("out.log", "w") as log_file:
                log_file.write(f"Running instance for job ID: {job_id}\n")
            
            # Run KOSMA-tau
            run_kosma_tau(job_id, tmp_dir)
            
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

def run_instance_wrapper(job_id, config=None):
    """Wrapper function for run_instance to handle exceptions.
    
    Args:
        job_id (int): Job ID
        config (dict, optional): Configuration. Defaults to None.
    """
    try:
        output = run_instance(job_id, config)
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

def run_parameter_grid(params=None, model_name=None, config=None, parallel=True, n_workers=None):
    """Run a grid of PDR models with different parameters.
    
    Args:
        params (dict, optional): Parameter configuration. Defaults to None.
        model_name (str, optional): Model name. Defaults to None.
        config (dict, optional): Framework configuration. Defaults to None.
        parallel (bool, optional): Whether to run in parallel. Defaults to True.
        n_workers (int, optional): Number of worker processes. Defaults to None.
        
    Returns:
        list: List of job IDs
    """
    if params is None:
        params = DEFAULT_PARAMETERS
    
    if model_name is None:
        model_name = "default_model"
    
    if config is None:
        config = {
            'pdr': PDR_CONFIG,
            'parameters': params
        }
    
    # Make sure we have a model path
    pdr_dir = config['pdr'].get('base_dir', PDR_CONFIG['base_dir'])
    model_path = os.path.join(pdr_dir, model_name)
    
    # Set up model directories
    setup_model_directories(model_path)
    
    # Generate parameter combinations
    param_combinations = generate_parameter_combinations(params)
    
    # Create database entries
    _, job_ids = create_database_entries(
        model_name, 
        model_path, 
        param_combinations, 
        config
    )
    
    # Determine number of workers
    if n_workers is None:
        n_workers = _calculate_cpu_count(reserved_cpus=params.get('reserved_cpus', 2))
    
    logger.info(f"Running on {n_workers} workers")
    
    # Run jobs
    if parallel:
        Parallel(n_jobs=n_workers)(
            delayed(run_instance_wrapper)(job_id, config)
            for job_id in job_ids
        )
    else:
        for job_id in job_ids:
            run_instance_wrapper(job_id, config)
    
    return job_ids

# Also make this accessible from run_parameter_grid
run_parameter_grid._calculate_cpu_count = _calculate_cpu_count

def run_model(params=None, model_name=None, config=None):
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
    for key in ['metal', 'dens', 'mass', 'chi', 'col']:
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