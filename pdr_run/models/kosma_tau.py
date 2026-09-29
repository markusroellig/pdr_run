"""KOSMA-tau model management."""

import os
import re
import logging
import datetime
import shlex
import signal
import subprocess
import shutil
import tempfile

from pdr_run.config.default_config import (
    PDR_CONFIG, PDR_OUT_DIRS, PDR_INP_DIRS
)
from pdr_run.database.queries import (
    get_or_create, retrieve_job_parameters, update_job_status
)
from pdr_run.io.file_manager import (
    create_dir, copy_dir, move_files, make_tarfile, get_digest
)
from pdr_run.database import get_db_manager
from pdr_run.models.job_status import determine_job_status, SUCCESS_STATUSES
# ... other imports ...

# Removed the direct import of get_session, as it is replaced by get_db_manager().get_session()
from pdr_run.database.models import (
    PDRModelJob, HDFFile, KOSMAtauParameters, ChemicalDatabase
)
from pdr_run.models.parameters import (
    compute_radius, from_string_to_par, from_par_to_string,
    string_to_list, list_to_string, from_par_to_string_log
)

logger = logging.getLogger('dev')

# Define all possible PDRNEW.INP parameters
pdrnew_variable_names=[
    'xnsur' ,  # surface density (cm^⁻3)
    'mass' ,  # clump mass (Msol)
    'rtot' ,  # clump radius (cm)
    'rcore' ,  # core radius fraction (default =0.2)
    'alpha' , # power law index of density law
    'sigd'  ,  # dust UV cross section (cm^2)
    'sint'  ,  #FUV radiation field strength
    'cosray' ,  # cosmic ray ionization rate (s^-1)
    'beta'  ,  # Doppler line width 7.123e4 = 1 km/s FWHM, neg = Larson
    'zmetal' ,  #
    'preshh2'  ,  #
    'preshco'  ,  #
    'ifuvmeth'  , #
    'idustmet'  , #
    'ifuvtype'  , #
    'fuvtemp'   ,
    'fuvstring',
    'inewgam'  , #
    'iscatter'  , #
    'ihtclgas' , # compute Tgas?: default=1
    'tgasc' ,    # constant gas temp (K)
    'ihtcldust'  , #
    'tdustc'  , #
    'ipehmeth' , #
    'indXpeh'  , #
    'ihtclpah'  , #
    'indStr' , #
    'inds' , #
    'indx'  , #
    'd2gratio1', #
    'd2gratio2',  #
    'd2gratio3',  #
    'd2gratio4',  #
    'd2gratio5',  #
    'd2gratio6',  #
    'd2gratio7',  #
    'd2gratio8',  #
    'd2gratio9',  #
    'd2gratio10',  #
    'ih2meth'  , #
    'ih2onpah' , #
    'h2formc',  #
    'ih2shld'  , #
    'h2_structure' , #
    'h2_h_coll_rates'  #
    'h2_h_reactive_colls'  #
    'h2_use_gbar' #
    'h2_quad_a' #
    'ifh2des' #
    'ifcrdes' #
    'ifphdes' #
    'ifthdes' #
    'bindsites' #
    'ifchemheat' #
    'ifheat_alfven' #
    'alfven_velocity' #
    'alfven_column' #
    'temp_start' #
    'itmeth' #
    'ichemeth' #
    'inewtonstep' #
    'omega_neg' #
    'omega_pos' #
    'lambda' #
    'use_conservation' #
    'rescaleQF' #
    'precondLR' #
    'resortQF' #
    'nconv_time' #
    'time_dependent' #
    'use_dlsodes' #
    'use_dlsoda' #
    'use_dvodpk' #
    'first_time_step_yrs' #
    'max_time_yrs' #
    'num_time_steps' #
    'rtol_chem' #
    'atol_chem' #
    'Xhtry' #
    'Niter' #
    'rtol_iter' #
    'step1' #
    'step2' #
    'ihdfout' #
    'dbglvl' #
    'grid' #
    'elfrac4' # He
    'elfrac12' # 12C
    'elfrac13' # 13C
    'elfrac14' # N
    'elfrac16' # 16O
    'elfrac18' # 18O
    'elfrac19' # Fl
    'elfrac23' # Na
    'elfrac24' # Mg
    'elfrac28' # Si
    'elfrac31' # P
    'elfrac32' # S
    'elfrac35' # Cl
    'elfrac56' # Fe
    'species' # long string containing all species separated by whitespace
]

# Create placeholder names for template substitution
pdrnew_placeholder_names = ['KT_VAR{0}_'.format(i) for i in pdrnew_variable_names]

def transform(multilevelDict):
    """Transform a dictionary by prefixing keys with 'KT_VAR' and '_'"""
    return {'KT_VAR'+str(key)+'_' : (transform(value) if isinstance(value, dict) else value) 
            for key, value in multilevelDict.items()}

def open_template(template_name):
    """Open a template file and return its contents.
    
    Args:
        template_name (str): Name or path of the template file
        
    Returns:
        String containing the template content
        
    Raises:
        FileNotFoundError: If the template file cannot be located
    """
    logger.info(f"Looking for template file: '{template_name}'")
    logger.debug(f"Current working directory: {os.getcwd()}")
    
    # Define search paths: current directory first, then PDR_INP_DIRS
    search_dirs = ['.']
    if isinstance(PDR_INP_DIRS, list):
        search_dirs.extend(PDR_INP_DIRS)
    elif isinstance(PDR_INP_DIRS, str):
        search_dirs.append(PDR_INP_DIRS) # Fallback if PDR_INP_DIRS was accidentally a string
    
    attempted_paths = []
    
    for base_dir in search_dirs:
        # Construct the full path
        template_path = os.path.join(base_dir, template_name)
        attempted_paths.append(template_path)
        logger.debug(f"Trying path: {template_path}")
        
        if os.path.exists(template_path):
            logger.info(f"Template found at: {template_path}")
            with open(template_path, "r") as f:
                content = f.read()
            logger.debug(f"Successfully read template ({len(content)} bytes)")
            return content
    
    # If we get here, we couldn't find the template
    error_msg = f"Template file '{template_name}' not found. Attempted paths: {attempted_paths}"
    logger.error(error_msg)
    raise FileNotFoundError(error_msg)

def format_scientific(value):
    """Format a number in scientific notation.
    
    Args:
        value: The number to format
        
    Returns:
        String representation in scientific notation or regular format
    """
    if isinstance(value, (int, float)):
        # For integers, use regular integer format
        if isinstance(value, int):
            return str(value)
        elif abs(value) >= 1000 or abs(value) < 0.1:
            return f"{value:.3e}"
        else:
            return f"{value:.6f}"
    
    # For non-numeric values, return as string
    return str(value)

def create_pdrnew_from_job_id(job_id, session=None, return_content=False):
    """Create a PDRNEW.INP input file for the KOSMA-tau PDR model from a database job ID.
    
    This function retrieves a PDR model job by its ID and generates a PDRNEW.INP file
    by replacing template placeholders with parameter values. The PDRNEW.INP file is the
    primary input file for the KOSMA-tau PDR code that defines all physical and numerical
    parameters needed for the model simulation.
    
    The function performs several key steps:
    1. Retrieves the job and associated parameter records from the database
    2. Loads the PDRNEW.INP template file with placeholder variables
    3. Extracts required parameters from the database record
    4. Transforms parameter names to match template placeholders (adds KT_VAR prefix)
    5. Handles special formatting for different parameter types:
       - Species lists are expanded into multiple SPECIES lines
       - Grid parameters are converted to "*MODEL GRID" flag if enabled
       - Numerical values are formatted in appropriate scientific notation
    6. Writes the processed template to a PDRNEW.INP file in the current directory
    
    Args:
        job_id (int): Database ID of the PDR model job to process
        session (sqlalchemy.orm.Session, optional): Database session. If None, a 
            new session will be created. Defaults to None.
        return_content (bool, optional): Whether to return the generated file content
            in addition to writing the file. Defaults to False.
            
    Returns:
        str or None: If return_content is True, returns the complete content of the 
            generated PDRNEW.INP file as a string. Otherwise returns None.
            
    Raises:
        ValueError: If the job_id does not correspond to a valid PDR model job
        FileNotFoundError: If the PDRNEW.INP.template file cannot be located
        
    Examples:
        # Create PDRNEW.INP file for job ID 123
        create_pdrnew_from_job_id(123)
        
        # Create file and get content
        content = create_pdrnew_from_job_id(123, return_content=True)
        
    Notes:
        - The function expects the current working directory to be the one where
          the PDRNEW.INP file should be written
        - The template file is searched for in the directories specified in PDR_INP_DIRS
        - Template variables have the format KT_VARparameter_name_
    """
    _session = session
    session_created_locally = False
    
    if _session is None:
        _session = get_db_manager().get_session()
        session_created_locally = True
        logger.debug(f"create_pdrnew_from_job_id: Created local session for job {job_id}")

    try:
        job = _session.get(PDRModelJob, job_id)
        model_params = _session.get(KOSMAtauParameters, job.kosmatau_parameters_id)
        
        # Get the template content
        try:
            template_content = open_template("PDRNEW.INP.template")
        except FileNotFoundError:
            logger.warning("PDRNEW.INP.template not found. Skipping PDRNEW.INP creation.")
            if return_content:
                return None
            return None
        
        # Transform parameters directly from model_params
        transformed_params = transform(model_params.__dict__)
        
        # Filter out SQLAlchemy internal attributes
        transformed_params = {k: v for k, v in transformed_params.items() 
                            if not k.startswith('KT_VAR_sa_') and not k.startswith('KT_VAR__')}
        
        # Replace template placeholders with actual values
        output = template_content
        for key, value in transformed_params.items():
            if key == 'KT_VARspecies_':
                species_list = value.split()
                species_lines = []
                for species in species_list:
                    species_lines.append(f"SPECIES  {species}")
                output = output.replace(key, "\n".join(species_lines))
            elif key == 'KT_VARgrid_':
                if value:
                    output = output.replace(key, "*MODEL GRID")
                else:
                    output = output.replace(key, "")
            else:
                # Format numbers in scientific notation
                formatted_value = format_scientific(value)
                output = output.replace(key, formatted_value)
        
        # Write the output file
        with open("PDRNEW.INP", "w") as f:
            f.write(output)
        
        # Log that we created the file
        logger.info(f"Created PDRNEW.INP for job {job_id}")
        
        # Return content if requested
        if return_content:
            return output
        
        return None
    finally:
        if session_created_locally:
            _session.close()
            logger.debug(f"create_pdrnew_from_job_id: Closed local session for job {job_id}")

def create_json_from_job_id(job_id, session=None, return_content=False, config=None):
    """Create a pdr_config.json input file for the KOSMA-tau PDR model from a database job ID.
    
    This function retrieves a PDR model job by its ID and generates a JSON config file
    by replacing template placeholders with parameter values. The pdr_config.json file is the
    primary input file for the KOSMA-tau PDR code that defines all physical and numerical
    parameters needed for the model simulation.
    
    The function performs several key steps:
    1. Retrieves the job and associated parameter records from the database
    2. Loads the pdr_config.json template file with placeholder variables
    3. Extracts required parameters from the database record
    4. Transforms parameter names to match template placeholders (adds KT_VAR prefix)
    5. Handles special formatting for different parameter types:
       - Species lists are expanded into multiple SPECIES lines
       - Grid parameters are converted to "*MODEL GRID" flag if enabled
       - Numerical values are formatted in appropriate scientific notation
    6. Writes the processed template to a pdr_config.json file in the current directory
    
    Args:
        job_id (int): Database ID of the PDR model job to process
        session (sqlalchemy.orm.Session, optional): Database session. If None, a 
            new session will be created. Defaults to None.
        return_content (bool, optional): Whether to return the generated file content
            in addition to writing the file. Defaults to False.
        config (dict, optional): Configuration dictionary to retrieve json_template_file.
            Defaults to None.
            
    Returns:
        str or None: If return_content is True, returns the complete content of the 
            generated pdr_config.json file as a string. Otherwise returns None.
            
    Raises:
        ValueError: If the job_id does not correspond to a valid PDR model job
        FileNotFoundError: If the pdr_config.json.template file cannot be located
        
    Examples:
        # Create pdr_config.json file for job ID 123
        create_json_from_job_id(123)
        
        # Create file and get content
        content = create_json_from_job_id(123, return_content=True)
        
    Notes:
        - The function expects the current working directory to be the one where
          the pdr_config.json file should be written
        - The template file is searched for in the directories specified in PDR_INP_DIRS
        - Template variables have the format KT_VARparameter_name_
    """
    _session = session
    session_created_locally = False
    
    if _session is None:
        _session = get_db_manager().get_session()
        session_created_locally = True
        logger.debug(f"create_json_from_job_id: Created local session for job {job_id}")

    try:
        job = _session.get(PDRModelJob, job_id)
        model_params = _session.get(KOSMAtauParameters, job.kosmatau_parameters_id)

        # Retrieve ChemicalDatabase object
        chemical_database = _session.get(ChemicalDatabase, job.chemical_database_id)
        if not chemical_database:
            logger.error(f"ChemicalDatabase not found for job {job_id}, ID: {job.chemical_database_id}")
            raise ValueError(f"ChemicalDatabase not found for job {job_id}")
        
        # Determine which JSON template file to use
        json_template_file_name = PDR_CONFIG['json_template_file']
        if config and 'pdr' in config and 'json_template_file' in config['pdr']:
            json_template_file_name = config['pdr']['json_template_file']
        
        logger.debug(f"Using JSON template file: {json_template_file_name}")

        # Get the template content
        try:
            template_content = open_template(json_template_file_name)
        except FileNotFoundError:
            logger.warning(f"{json_template_file_name} not found. Skipping JSON creation.")
            if return_content:
                return ""
            return None
        
        # Combine model parameters and chemical database file name for substitution
        substitution_params = model_params.__dict__.copy()
        # Add the chemical database file name with a clear placeholder name
        substitution_params['CHEM_DATABASE_FILE'] = chemical_database.chem_rates_file_name

        # Transform combined parameters
        transformed_params = transform(substitution_params)
        
        # Filter out SQLAlchemy internal attributes
        transformed_params = {k: v for k, v in transformed_params.items() 
                            if not k.startswith('KT_VAR_sa_') and not k.startswith('KT_VAR__')}
        
        logger.debug(f"Transformed parameters for JSON: {list(transformed_params.keys())}")
        
        # Replace template placeholders with actual values
        output = template_content
        for key, value in transformed_params.items():
            if key == 'KT_VARspecies_':
                # Handle species list - convert to JSON array format or comma-separated string
                if isinstance(value, str):
                    species_list = string_to_list(value)
                    species_json = '["' + '", "'.join(species_list) + '"]'
                    output = output.replace(key, species_json)
                    logger.debug(f"Replaced {key} with species array: {species_json}")
                else:
                    formatted_value = format_scientific(value)
                    output = output.replace(key, formatted_value)
            elif key == 'KT_VARgrid_':
                # Handle grid parameter
                if value:
                    output = output.replace(key, "true")
                else:
                    output = output.replace(key, "false")
                logger.debug(f"Replaced {key} with boolean: {value}")
            else:
                # Handle regular parameters with proper formatting
                formatted_value = format_scientific(value)
                output = output.replace(key, formatted_value)
                logger.debug(f"Replaced {key} with: {formatted_value}")
        
        # Write the output file
        output_path = "pdr_config.json"
        with open(output_path, "w") as f:
            f.write(output)
        
        # Register the JSON file in the database
        from pdr_run.database.json_handlers import register_json_file
        register_json_file(job_id=job_id, name="pdr_config.json", path=os.path.abspath(output_path), session=_session)

        # Log that we created the file
        logger.info(f"Created pdr_config.json for job {job_id}")
        
        # Return content if requested
        if return_content:
            return output
        
        return None
    finally:
        if session_created_locally:
            _session.close()
            logger.debug(f"create_json_from_job_id: Closed local session for job {job_id}")

def set_gridparam(zmetal, density, cmass, radiation, shieldh2):
    """Set grid parameters for the model.
    
    Args:
        zmetal (str): Metal abundance
        density (str): Density
        cmass (str): Cloud mass
        radiation (str): Radiation field
        shieldh2 (str): H2 shielding
    """
    path = 'GRID_PARAM'
    if os.path.exists(path):
        os.remove(path)
    
    with open(path, 'w') as f:
        f.write('METAL*0.01, 10*(log NSUR), log MASS, 10*(log CHI), log CD(H2)\n')
        f.write(f"{zmetal}\n")
        f.write(f"{density}\n")
        f.write(f"{cmass}\n")
        f.write(f"{radiation}\n")
        f.write(f"{shieldh2}\n")
    
    logger.info(f"Set grid parameters: {zmetal}, {density}, {cmass}, {radiation}, {shieldh2}")

def _kill_process_group(proc, term_timeout=10, kill_timeout=30):
    """Kill *proc*'s whole process group: SIGTERM, escalate to SIGKILL.

    ``proc`` must have been started with ``start_new_session=True`` so it
    is its own process group leader; killing only ``proc.pid`` would (as
    the previous ``shell=True`` implementation did implicitly) leave any
    children - in particular ``pdrexe`` itself when it is invoked via a
    shell - running after the "kill".

    Returns the process's exit code, or ``None`` if it could not be reaped
    within *kill_timeout* seconds of SIGKILL (should not happen for a
    normal process, but this must never hang the driver on a multi-node
    grid run).
    """
    try:
        pgid = os.getpgid(proc.pid)
    except ProcessLookupError:
        return proc.poll()
    try:
        os.killpg(pgid, signal.SIGTERM)
    except ProcessLookupError:
        return proc.poll()
    try:
        return proc.wait(timeout=term_timeout)
    except subprocess.TimeoutExpired:
        logger.warning(f"Process group {pgid} still alive {term_timeout}s "
                       "after SIGTERM, sending SIGKILL")
    try:
        os.killpg(pgid, signal.SIGKILL)
    except ProcessLookupError:
        return proc.poll()
    try:
        return proc.wait(timeout=kill_timeout)
    except subprocess.TimeoutExpired:
        logger.error(f"Process group {pgid} did not die within "
                     f"{kill_timeout}s of SIGKILL; giving up on reaping it")
        return None


def _store_run_status_fields(job, fields):
    """Copy the numeric/text fields from job_status.determine_job_status()
    onto *job*, skipping any column the connected database doesn't have
    yet (see database.db_manager.ensure_additive_columns - this keeps the
    function working against a not-yet-migrated database)."""
    mapping = {
        'converged': 'run_status_converged',
        'global_iterations': 'run_status_global_iterations',
        'eps_final': 'run_status_eps_final',
        'tsearch_flagged_shells': 'run_status_tsearch_flagged_shells',
        'chem_relaxed_calls': 'run_status_chem_relaxed_calls',
        'deferred_iterations': 'run_status_deferred_iterations',
        'code_version': 'run_status_code_version',
        'git_hash': 'run_status_git_hash',
    }
    for key, attr in mapping.items():
        if hasattr(job, attr):
            setattr(job, attr, fields.get(key))
        else:
            logger.debug(f"PDRModelJob has no column '{attr}'; skipping "
                        "(database not migrated, see ensure_additive_columns)")


def run_pdr(job_id, tmp_dir='./', session=None, config=None):
    """Run the PDR model for a given job.

    Args:
        job_id (int): Job ID
        tmp_dir (str): Temporary directory path
        session (sqlalchemy.orm.Session, optional): Database session. If None, a
            new session will be created and closed. Defaults to None.
        config (dict, optional): Configuration dictionary. Reads
            ``config['pdr']['max_walltime_s']`` (seconds; ``None``/absent
            = no cap, i.e. unchanged pre-existing behaviour). On expiry
            the pdrexe process group is killed and the job is classified
            as 'timeout'.

    The exit status is classified by
    ``pdr_run.models.job_status.determine_job_status()`` from
    ``pdroutput/run_status.json`` (preferred) or, failing that, from the
    convergence line in ``pdroutput/TEXTOUT`` - a zero exit code alone no
    longer means the model converged. See that module's docstring.
    """
    _session = session
    session_created_locally = False

    if _session is None:
        _session = get_db_manager().get_session()
        session_created_locally = True
        logger.debug(f"run_pdr: Created local session for job {job_id}")

    max_walltime_s = PDR_CONFIG.get('max_walltime_s')
    if config and 'pdr' in config and 'max_walltime_s' in config['pdr']:
        max_walltime_s = config['pdr']['max_walltime_s']

    try:
        job = _session.get(PDRModelJob, job_id)

        if not job:
            raise ValueError(f"Job with ID {job_id} not found")

        exe = job.executable
        model = job.model_name

        with open(os.path.join('pdroutput', 'TEXTOUT'), 'w') as textout:
            now_start = datetime.datetime.now()
            print('Begin of PDR Job: ' + now_start.strftime("%Y-%m-%d %H:%M:%S"))
            print('Begin of PDR Job: ' + now_start.strftime("%Y-%m-%d %H:%M:%S"), file=textout)
            logger.info('Begin of PDR Job: ' + now_start.strftime("%Y-%m-%d %H:%M:%S"))
            print(' ', file=textout)

            # Update job status in the database
            job.time_of_start = now_start
            update_job_status(job_id, 'running', _session)

            try:
                # Run the PDR model in its own process group. Not
                # shell=True: with shell=True the immediate child is
                # /bin/sh, and killing it on a wall-time timeout leaves
                # pdrexe itself running - see _kill_process_group().
                cmd = shlex.split('./' + exe.executable_file_name)
                timed_out = False
                proc = subprocess.Popen(
                    cmd, stdout=textout, stderr=textout, start_new_session=True
                )
                try:
                    returncode = proc.wait(timeout=max_walltime_s)
                except subprocess.TimeoutExpired:
                    timed_out = True
                    logger.error(
                        f"Job {job_id}: wall-time cap of {max_walltime_s}s "
                        f"exceeded, killing process group (pid {proc.pid})")
                    returncode = _kill_process_group(proc)

                textout.flush()
                status, fields = determine_job_status(
                    returncode, timed_out, os.getcwd())
                job.status = status
                _store_run_status_fields(job, fields)

                if status in SUCCESS_STATUSES:
                    logger.info(
                        f"pdrexe finished with status '{status}' (job {job_id})")
                else:
                    logger.error(
                        f"pdrexe run classified as '{status}' for job {job_id} "
                        f"(returncode={returncode}, timed_out={timed_out})")

                print(' ', file=textout)
                print(f'Output copied to directory {os.path.join(model.model_path, "pdrgrid")}', file=textout)
                logger.info(f'Output copied to directory {os.path.join(model.model_path, "pdrgrid")}')

                now_end = datetime.datetime.now()
                print('End of PDR Job: ' + now_end.strftime("%Y-%m-%d %H:%M:%S"))
                print('End of PDR Job: ' + now_end.strftime("%Y-%m-%d %H:%M:%S"), file=textout)
                logger.info('End of PDR Job: ' + now_end.strftime("%Y-%m-%d %H:%M:%S"))

                job.time_of_finish = now_end
                update_job_status(job_id, job.status, _session)

            except Exception as e:
                logger.error(f"Unexpected error: {str(e)}", exc_info=True)
                job.status = 'ERROR'
                job.time_of_finish = datetime.datetime.now()
                update_job_status(job_id, 'ERROR', _session)
                raise
    finally:
        if session_created_locally:
            _session.close()
            logger.debug(f"run_pdr: Closed local session for job {job_id}")

def copy_pdroutput(job_id, config=None, session=None):
    """Copy PDR output files to the model directory.
    
    Args:
        job_id (int): Job ID
        config (dict): Configuration dictionary
        session (sqlalchemy.orm.Session, optional): Database session. If None, a 
            new session will be created and closed. Defaults to None.
    """
    from pdr_run.storage.base import get_storage_backend
    
    _session = session
    session_created_locally = False

    if _session is None:
        _session = get_db_manager().get_session()
        session_created_locally = True
        logger.debug(f"copy_pdroutput: Created local session for job {job_id}")

    try:
        job = _session.get(PDRModelJob, job_id)
        
        if not job:
            raise ValueError(f"Job with ID {job_id} not found")
        
        # Get the storage backend
        storage = get_storage_backend(config)
        
        model = job.model_job_name
        model_path = job.model_name.model_path
        
        hdf_out_name = 'pdr' + model + '.hdf'
        hdf5_struct_out_name = 'pdrstruct' + model + '.hdf5'
        hdf5_chem_out_name = 'pdrchem' + model + '.hdf5'
        text_out_name = 'TEXTOUT' + model
        chemchk_out_name = 'chemchk' + model + '.out'
        mrt_out_name = 'MCDRT' + model + '.tar.gz'
        pdrnew_inp_file_name = 'PDRNEW' + model + '.INP'
        json_file_name = 'pdr_config' + model + '.json'
        ctrl_ind_file_name = 'CTRL_IND' + model
        
        # Copy output files to the model directory with error handling
        try:
            if os.path.exists(os.path.join('pdroutput', 'TEXTOUT')):
                 # Local source path
                local_source = os.path.join('pdroutput', 'TEXTOUT')

                # Remote destination path (relative to storage base_dir)
                remote_dest = os.path.join(model_path, 'pdrgrid', text_out_name)

                # Store file using backend (works with local, SFTP, S3, etc.)
                storage.store_file(local_source, remote_dest)

                # Update job with storage-aware path
                job.log_file = remote_dest  # Now this path works with any backend
                job.output_textout_file = remote_dest
                logger.info(f"Successfully stored TEXTOUT file for job {job_id}")


            if os.path.exists(os.path.join('pdroutput', 'pdrout.hdf')):
                local_source = os.path.join('pdroutput', 'pdrout.hdf')
                remote_dest  = os.path.join(model_path, 'pdrgrid', hdf_out_name)
                storage.store_file(local_source, remote_dest)
                job.output_hdf4_file = os.path.join(model_path, 'pdrgrid', hdf_out_name)
                logger.info(f"Successfully stored HDF4 file for job {job_id}")

            if os.path.exists(os.path.join('pdroutput', 'pdrstruct_s.hdf5')):
                local_source = os.path.join('pdroutput', 'pdrstruct_s.hdf5')
                remote_dest = os.path.join(model_path, 'pdrgrid', hdf5_struct_out_name)
                storage.store_file(local_source, remote_dest)
                job.output_hdf5_struct_file = os.path.join(model_path, 'pdrgrid', hdf5_struct_out_name)
                logger.info(f"Successfully stored HDF5 struct file for job {job_id}")

            if os.path.exists(os.path.join('pdroutput', 'pdrchem_c.hdf5')):
                local_source = os.path.join('pdroutput', 'pdrchem_c.hdf5')
                remote_dest = os.path.join(model_path, 'pdrgrid', hdf5_chem_out_name)
                storage.store_file(local_source, remote_dest)
                job.output_hdf5_chem_file = os.path.join(model_path, 'pdrgrid', hdf5_chem_out_name)
                logger.info(f"Successfully stored HDF5 chem file for job {job_id}")

            if os.path.exists(os.path.join('pdroutput', 'chemchk.out')):
                local_source = os.path.join('pdroutput', 'chemchk.out')
                remote_dest = os.path.join(model_path, 'pdrgrid', chemchk_out_name)
                storage.store_file(local_source, remote_dest)
                job.output_chemchk_file = os.path.join(model_path, 'pdrgrid', chemchk_out_name)
                logger.info(f"Successfully stored chemchk file for job {job_id}")

            if os.path.exists('./Out'):
                # Create tar file locally first
                local_tar = os.path.join('/tmp', mrt_out_name)
                make_tarfile(local_tar, './Out')

                # Then upload to storage
                remote_dest = os.path.join(model_path, 'pdrgrid', mrt_out_name)
                storage.store_file(local_tar, remote_dest)

                # Clean up local tar file
                os.unlink(local_tar)
                job.output_mcdrt_zip_file = os.path.join(model_path, 'pdrgrid', mrt_out_name)
                logger.info(f"Successfully stored MCDRT output for job {job_id}")

            if os.path.exists('PDRNEW.INP'):
                local_source = os.path.join('PDRNEW.INP')
                remote_dest = os.path.join(model_path, 'pdrgrid', pdrnew_inp_file_name)
                storage.store_file(local_source, remote_dest)
                job.input_pdrnew_inp_file = os.path.join(model_path, 'pdrgrid', pdrnew_inp_file_name)
                logger.info(f"Successfully stored PDRNEW.INP for job {job_id}")

            if os.path.exists('pdr_config.json'):
                local_source = 'pdr_config.json'
                remote_dest = os.path.join(model_path, 'pdrgrid', json_file_name)
                storage.store_file(local_source, remote_dest)
                job.input_json_file = os.path.join(model_path, 'pdrgrid', json_file_name)
                logger.info(f"Successfully stored pdr_config.json for job {job_id}")

            if os.path.exists(os.path.join('pdroutput', 'CTRL_IND')):
                local_source = os.path.join('pdroutput', 'CTRL_IND')
                remote_dest = os.path.join(model_path, 'pdrgrid', ctrl_ind_file_name)
                storage.store_file(local_source, remote_dest)
                # copy for CTRL_IND onionexe
                shutil.copyfile(
                    os.path.join('pdroutput', 'CTRL_IND'),
                    'CTRL_IND'
                )
                logger.info(f"Successfully stored CTRL_IND for job {job_id}")

            # Commit all file storage updates to database
            try:
                _session.commit()
                logger.info(f"Successfully committed storage updates for job {job_id}")
            except Exception as e:
                logger.error(f"Failed to commit storage updates for job {job_id}: {e}")
                _session.rollback()
                raise

        except Exception as e:
            logger.error(f"Storage operation failed for job {job_id}: {e}")
            # Update job status to indicate storage failure
            try:
                job.status = 'failed_storage'
                _session.commit()
                logger.info(f"Updated job {job_id} status to 'failed_storage'")
            except Exception as commit_error:
                logger.error(f"Failed to update job status after storage error: {commit_error}")
                _session.rollback()
            raise
        

        # Calculate SHA256 for local files (before they get cleaned up)
        local_hdf_path = 'pdroutput/pdrout.hdf'
        local_hdf5_path = 'pdroutput/pdrstruct_s.hdf5'
        local_hdf5_chem_path = 'pdroutput/pdrchem_c.hdf5'

        if os.path.exists(local_hdf_path):
            sha_key = get_digest(local_hdf_path)
            local_hdf_mtime = os.path.getmtime(local_hdf_path)
            local_hdf_size = os.path.getsize(local_hdf_path)
        else:
            logger.error(f"Cannot find local HDF file: {local_hdf_path}")
            return

        if os.path.exists(local_hdf5_path):
            sha_key_hdf5 = get_digest(local_hdf5_path)
            local_hdf5_mtime = os.path.getmtime(local_hdf5_path)
            local_hdf5_size = os.path.getsize(local_hdf5_path)
        else:
            logger.error(f"Cannot find local HDF5 file: {local_hdf5_path}")
            return

        if os.path.exists(local_hdf5_chem_path):
            sha_key_hdf5_c = get_digest(local_hdf5_chem_path)
            local_hdf5_chem_mtime = os.path.getmtime(local_hdf5_chem_path)
            local_hdf5_chem_size = os.path.getsize(local_hdf5_chem_path)
        else:
            logger.error(f"Cannot find local HDF5 chemistry file: {local_hdf5_chem_path}")
            return

        # Use _session.query().filter_by().first() instead of _session.query().get() for complex queries
        instance = _session.query(HDFFile).filter_by(sha256_sum=sha_key).first()
        
        if not instance:
            hdf = get_or_create(
                _session,
                HDFFile,
                job_id=job_id,
                pdrexe_id=job.kosmatau_executable_id,
                parameter_id=job.kosmatau_parameters_id,
                model_name_id=job.model_name_id,
                file_name=hdf_out_name,
                full_path=os.path.join(model_path, 'pdrgrid', hdf_out_name),
                path=os.path.join(model_path, 'pdrgrid'),
                modification_time=datetime.datetime.fromtimestamp(local_hdf_mtime),
                sha256_sum=sha_key,
                file_size=local_hdf_size,
                #HDF 5 structure file
                file_name_hdf5_s=hdf5_struct_out_name,
                full_path_hdf5_s=os.path.join(model_path, 'pdrgrid', hdf5_struct_out_name),
                path_hdf5_s=os.path.join(model_path, 'pdrgrid'),
                modification_time_hdf5_s=datetime.datetime.fromtimestamp(local_hdf5_mtime),
                sha256_sum_hdf5_s=sha_key_hdf5,
                file_size_hdf5_s=local_hdf5_size,
                #hdf5 chemistry file
                file_name_hdf5_c=hdf5_chem_out_name,
                full_path_hdf5_c=os.path.join(model_path, 'pdrgrid', hdf5_chem_out_name),
                path_hdf5_c=os.path.join(model_path, 'pdrgrid'),
                modification_time_hdf5_c=datetime.datetime.fromtimestamp(local_hdf5_chem_mtime),
                sha256_sum_hdf5_c=sha_key_hdf5_c,
                file_size_hdf5_c=local_hdf5_chem_size,
                )
        
        # Update job output file paths (only update fields that exist)
        job.output_hdf_file = os.path.join(model_path, 'pdrgrid', hdf_out_name)
        
        # Check if these attributes exist before setting them
        if hasattr(job, 'output_hdf5_struct_file'):
            job.output_hdf5_struct_file = os.path.join(model_path, 'pdrgrid', hdf5_struct_out_name)
        if hasattr(job, 'output_hdf5_chem_file'):
            job.output_hdf5_chem_file = os.path.join(model_path, 'pdrgrid', hdf5_chem_out_name)
        if hasattr(job, 'output_textout_file'):
            job.output_textout_file = os.path.join(model_path, 'pdrgrid', text_out_name)
        if hasattr(job, 'output_chemchk_file'):
            job.output_chemchk_file = os.path.join(model_path, 'pdrgrid', chemchk_out_name)
        if hasattr(job, 'output_mcdrt_zip_file'):
            job.output_mcdrt_zip_file = os.path.join(model_path, 'pdrgrid', mrt_out_name)
        if hasattr(job, 'output_config_file'):
            job.output_config_file = os.path.join(model_path, 'pdrgrid', json_file_name)
        if hasattr(job, 'output_ctrl_ind_file'):
            job.output_ctrl_ind_file = os.path.join(model_path, 'pdrgrid', ctrl_ind_file_name)
        
        try:
            _session.commit()
            logger.info(f"Successfully updated database entries for existing model {model}")
        except Exception as e:
            logger.error(f"Failed to update database entries: {e}")
            _session.rollback()
            raise
    finally:
        if session_created_locally:
            _session.close()
            logger.debug(f"copy_pdroutput: Closed local session for job {job_id}")

def set_oniondir(spec):
    """Set up the onion directory for a species.
    
    Args:
        spec (str): Species name
    """
    onion_files = [
        'jerg_' + spec + '.smli',
        'jerg_' + spec + '.srli',
        'jtemp_' + spec + '.smli',
        'jtemp_' + spec + '.smlc',
        'linebt_' + spec + '.out',
        'ONION3_' + spec + '.OUT'
    ]
    
    for f in onion_files:
        path = os.path.join('onionoutput', f)
        if os.path.exists(path):
            os.remove(path)
    
    shutil.copyfile(
        os.path.join('onioninpdata', 'ONION3.INP.' + spec),
        'ONION3.INP'
    )
    
    logger.info(f"Set up onion directory for species {spec}")

def run_onion(spec, job_id, tmp_dir='./', config=None, session=None):
    """Run the onion model for a species.
    
    Args:
        spec (str): Species name
        job_id (int): Job ID
        tmp_dir (str): Temporary directory path
        config (dict): Configuration dictionary containing executable name
        session (sqlalchemy.orm.Session, optional): Database session. If None, a 
            new session will be created and closed. Defaults to None.
    """
    from pdr_run.storage.base import get_storage_backend

    _session = session
    session_created_locally = False

    if _session is None:
        _session = get_db_manager().get_session()
        session_created_locally = True
        logger.debug(f"run_onion: Created local session for job {job_id}")

    try:
        storage = get_storage_backend(config)
        job =  _session.get(PDRModelJob, job_id)
        
        if not job:
            raise ValueError(f"Job with ID {job_id} not found")

           # Get the onion executable name from config or fall back to default
        if config and 'pdr' in config:
            onion_file_name = config['pdr'].get('onion_file_name', PDR_CONFIG['onion_file_name'])
        else:
            onion_file_name = PDR_CONFIG['onion_file_name']
     

        # hdf_name = job.output_hdf4_file
        # hdf5_name = job.output_hdf5_struct_file
        # now linking to the hdf files in the pdroutput directory
    #    hdf_name = os.path.join(tmp_dir, 'pdroutput', 'pdrout.hdf')
        hdf5_name = os.path.join(tmp_dir, 'pdroutput', 'pdrstruct_s.hdf5')
        
    #    # Create symbolic link to HDF file
    #    if os.path.exists(os.path.join(tmp_dir, 'pdrout.hdf')):
    #        os.remove(os.path.join(tmp_dir, 'pdrout.hdf'))
    #    
    #    os.symlink(
    #        hdf_name,
    #        os.path.join(tmp_dir, 'pdrout.hdf')
    #    )

    #   # Create symbolic link to HDF5 file
    #    if os.path.exists(os.path.join(tmp_dir, 'pdrstruct_s.hdf5')):
    #        os.remove(os.path.join(tmp_dir, 'pdrstruct_s.hdf5'))
    #    
    #    os.symlink(
    #        hdf5_name,
    #        os.path.join(tmp_dir, 'pdrstruct_s.hdf5')
    #    )
        
        
        # Create CTRL_IND file if it doesn't exist
    #    if not os.path.exists(os.path.join(tmp_dir, 'CTRL_IND')):
    #        p = subprocess.call(
    #            ['getctrlind', 'pdrout.hdf'],
    #            stdout=open(os.path.join('onionoutput', 'TEXTOUT'), 'w'),
    #            stderr=subprocess.STDOUT,
    #            shell=True
    #        )
    #        
    #        if p == 0:
    #            logger.info("CTRL_IND created without problems")
    #        else:
    #            logger.error(f"Couldn't create CTRL_IND, subprocess.call returns: {p}")
        
        # Run onion model
        with open(os.path.join('onionoutput', 'TEXTOUT'), 'w') as textout:
            logger.info(f"Running onion for {spec}")
            print(f"Running onion for {spec}", file=textout)
            #print("Getting the CTRL_IND file", file=textout)

            # moving CTRL_IND to the tmp_dir for onionexe
            shutil.copyfile(os.path.join(tmp_dir, 'pdroutput', 'CTRL_IND'), os.path.join(tmp_dir, 'CTRL_IND'))

            onion_code = './' + onion_file_name
            logger.info(f"Running onion code {onion_code}")
            try:
                #os.system(f"{onion_code} pdrout.hdf >> {textout.name} 2>> {textout.name}")
                os.system(f"{onion_code} {hdf5_name} >> {textout.name} 2>> {textout.name}")
            except Exception as e:
                logger.error(f"Unexpected error: {str(e)}", exc_info=True)
                raise
            
            print(' ', file=textout)
        
        logger.info(f"Completed onion run for species {spec}")
    finally:
        if session_created_locally:
            _session.close()
            logger.debug(f"run_onion: Closed local session for job {job_id}")

def copy_onionoutput(spec, job_id, config=None, session=None):
    """Copy onion output files to the model directory.
    
    Args:
        spec (str): Species name
        job_id (int): Job ID
        config (dict): Configuration dictionary
        session (sqlalchemy.orm.Session, optional): Database session. If None, a 
            new session will be created and closed. Defaults to None.
    """
    from pdr_run.storage.base import get_storage_backend

    _session = session
    session_created_locally = False

    if _session is None:
        _session = get_db_manager().get_session()
        session_created_locally = True
        logger.debug(f"copy_onionoutput: Created local session for job {job_id}")

    try:
        storage = get_storage_backend(config)
        job = _session.get(PDRModelJob, job_id)

        if not job:
            raise ValueError(f"Job with ID {job_id} not found")
        
        model = job.model_job_name
        model_path = job.model_name.model_path
        
        onion_files = [
            'jerg_' + spec + '.smli',
            'jerg_' + spec + '.srli',
            'jtemp_' + spec + '.smli',
            'jtemp_' + spec + '.smlc',
            'linebt_' + spec + '.out',
            'ONION3_' + spec + '.OUT'
        ]
        
        try:
            for f in onion_files:
                path = os.path.join('onionoutput', f)
                if os.path.exists(path):
                    remote_dest = os.path.join(model_path, 'oniongrid', 'ONION' + model + '.' + f)
                    storage.store_file(path, remote_dest)
                    logger.debug(f"Stored onion file {f} for job {job_id}")

            storage.store_file(
                os.path.join('onionoutput', 'TEXTOUT'),
                os.path.join(model_path, 'oniongrid', 'TEXTOUT' + model + "_" + spec)
            )
            logger.info(f"Successfully copied onion output for species {spec}")

        except Exception as e:
            logger.error(f"Failed to store onion output files for species {spec}, job {job_id}: {e}")
            raise
    finally:
        if session_created_locally:
            _session.close()
            logger.debug(f"copy_onionoutput: Closed local session for job {job_id}")


def run_simline(job_id, tmp_dir='./', config=None, session=None):
    """Run SIMLINE radiative-transfer post-processing for a job.

    Wraps the KOSMA-tau simline pipeline (simline/python/run_simline.py):
    pdrstruct HDF5 -> converter -> SIMLINE binary -> FITS -> HDF5 + ASCII.
    Species list and observation settings come from the pipeline's own
    simline_config.json in <kosma-tau>/simline/python/, overridable via
    config['simline'] = {
        'enabled':     bool  (checked by run_kosma_tau, not here),
        'species':     list  (optional --species override),
        'simline_dir': str   (optional; default <pdr base_dir>/simline),
        'config_file': str   (optional pipeline config JSON),
        'timeout':     int   (seconds, default 3600),
    }

    Unlike ONION, which modifies the grid pdrstruct in place, SIMLINE
    results are written into a separate working copy that is stored under
    simlinegrid/pdrstruct<model>_simline.hdf5, so the ONION intensities in
    pdrgrid/ remain untouched.
    """
    import sys as _sys

    from pdr_run.storage.base import get_storage_backend

    _session = session
    session_created_locally = False
    if _session is None:
        _session = get_db_manager().get_session()
        session_created_locally = True
        logger.debug(f"run_simline: Created local session for job {job_id}")

    try:
        storage = get_storage_backend(config)
        job = _session.get(PDRModelJob, job_id)
        if not job:
            raise ValueError(f"Job with ID {job_id} not found")

        model = job.model_job_name
        model_path = job.model_name.model_path

        simline_cfg = (config or {}).get('simline', {}) or {}
        pdr_base = (config or {}).get('pdr', {}).get('base_dir', PDR_CONFIG['base_dir'])
        simline_dir = simline_cfg.get('simline_dir') or os.path.join(pdr_base, 'simline')
        driver = os.path.join(simline_dir, 'python', 'run_simline.py')
        if not os.path.isfile(driver):
            raise FileNotFoundError(f"SIMLINE driver not found: {driver}")

        # --- obtain the pdrstruct file (local run output, or fetch from storage)
        workdir = os.path.abspath(tmp_dir)
        outdir = os.path.join(workdir, 'pdroutput')
        os.makedirs(outdir, exist_ok=True)
        workfile = os.path.join(outdir, f'pdrstruct{model}_simline.hdf5')
        local_struct = os.path.join(outdir, 'pdrstruct_s.hdf5')
        if os.path.exists(local_struct):
            shutil.copyfile(local_struct, workfile)
        else:
            remote_struct = os.path.join(model_path, 'pdrgrid', f'pdrstruct{model}.hdf5')
            logger.info(f"run_simline: fetching {remote_struct} from storage")
            storage.retrieve_file(remote_struct, workfile)

        # --- per-job pipeline config: base config with an absolute simline_dir
        base_cfg_path = simline_cfg.get('config_file') or os.path.join(
            simline_dir, 'python', 'simline_config.json')
        with open(base_cfg_path) as f:
            cfg_text = f.read()
        cfg_text = re.sub(r'"simline_dir"\s*:\s*"[^"]*"',
                          f'"simline_dir": "{simline_dir}"', cfg_text)
        job_cfg_path = os.path.join(workdir, 'simline_config_job.json')
        with open(job_cfg_path, 'w') as f:
            f.write(cfg_text)

        cmd = [_sys.executable, driver, os.path.relpath(workfile, workdir),
               '--config', job_cfg_path]
        species = simline_cfg.get('species')
        if species:
            cmd += ['--species'] + list(species)

        simline_out = os.path.join(workdir, 'simlineoutput')
        os.makedirs(simline_out, exist_ok=True)
        logger.info(f"Running SIMLINE pipeline for job {job_id}: {' '.join(cmd)}")
        with open(os.path.join(simline_out, 'TEXTOUT_SIMLINE'), 'w') as textout:
            proc = subprocess.run(cmd, cwd=workdir, stdout=textout,
                                  stderr=subprocess.STDOUT,
                                  timeout=simline_cfg.get('timeout', 3600))
        if proc.returncode != 0:
            raise RuntimeError(
                f"SIMLINE pipeline exited with {proc.returncode} for job {job_id} "
                f"(see simlineoutput/TEXTOUT_SIMLINE)")

        # --- store results under simlinegrid/
        stored = 0
        for fname in sorted(os.listdir(simline_out)):
            src = os.path.join(simline_out, fname)
            if os.path.isfile(src):
                storage.store_file(
                    src, os.path.join(model_path, 'simlinegrid',
                                      f'SIMLINE{model}.{fname}'))
                stored += 1
        storage.store_file(
            workfile, os.path.join(model_path, 'simlinegrid',
                                   f'pdrstruct{model}_simline.hdf5'))
        logger.info(
            f"Stored SIMLINE-augmented HDF5 and {stored} output file(s) "
            f"under simlinegrid/ for job {job_id}")
    finally:
        if session_created_locally:
            _session.close()
            logger.debug(f"run_simline: Closed local session for job {job_id}")


UV_CONTINUUM_TOOL_RELPATH = os.path.join('h2py', 'postprocess_uv_continuum.py')
UV_CONTINUUM_CLOSURE_EXIT = 3


def run_uv_continuum(job_id, tmp_dir='./', config=None, session=None):
    """Append the H2 UV dissociation continuum to the local pdrstruct HDF5.

    Wraps ``<kosma-tau checkout>/h2py/postprocess_uv_continuum.py``, which
    appends 'Integrated quantities/Spectrum/UV Continuum/...' and a
    provenance line under 'Parameters/Postprocessing' to
    ``pdroutput/pdrstruct_s.hdf5`` IN PLACE (~5 s, ~0.7 GB RSS on a
    grid-1 model). Called by ``run_kosma_tau`` right after ``run_pdr``,
    i.e. before the onion loop, SIMLINE, and ``copy_pdroutput`` - all of
    which read or copy that same file - so the stored/post-processed file
    carries the continuum.

    A photon-closure gate failure (tool exit code 3) leaves the HDF5 file
    UNCHANGED (the tool's own contract, see its ``--help``) and is NOT
    treated as an error here: the return value is False and
    ``job.uvcont_closure_ok``/``job.uvcont_applied`` are set to False, but
    no exception is raised. Any other non-zero exit is a genuine tool
    error and raises ``RuntimeError``; the caller (``run_kosma_tau``)
    decides whether that should fail the job (by default it does not -
    see the try/except around this call there).

    config['uv_continuum'] = {
        'enabled':            bool (default False; checked by the caller),
        'kosma_tau_dir':      str  (checkout containing h2py/), required,
        'python_executable':  str  (default: the interpreter running pdr_run),
        'timeout':            int  seconds (default 300),
        'force':              bool (default False; passes --force),
        'extra_args':         list[str] (optional passthrough args),
    }

    Returns:
        bool: True if the continuum was written (closure within the
        tool's gate), False if the closure gate rejected the write.

    Raises:
        ValueError: uv_continuum.kosma_tau_dir is not configured.
        FileNotFoundError: the tool script or the model HDF5 is missing.
        RuntimeError: the tool exited with a code other than 0 or 3.
        subprocess.TimeoutExpired: the tool exceeded uv_continuum.timeout.
    """
    import sys as _sys

    _session = session
    session_created_locally = False
    if _session is None:
        _session = get_db_manager().get_session()
        session_created_locally = True
        logger.debug(f"run_uv_continuum: Created local session for job {job_id}")

    try:
        job = _session.get(PDRModelJob, job_id)
        if not job:
            raise ValueError(f"Job with ID {job_id} not found")

        uv_cfg = (config or {}).get('uv_continuum', {}) or {}
        kosma_tau_dir = uv_cfg.get('kosma_tau_dir')
        if not kosma_tau_dir:
            raise ValueError(
                "uv_continuum.enabled is set but uv_continuum.kosma_tau_dir "
                "is not configured")

        tool = os.path.join(kosma_tau_dir, UV_CONTINUUM_TOOL_RELPATH)
        if not os.path.isfile(tool):
            raise FileNotFoundError(f"UV continuum tool not found: {tool}")

        workdir = os.path.abspath(tmp_dir)
        hdf5_path = os.path.join(workdir, 'pdroutput', 'pdrstruct_s.hdf5')
        if not os.path.isfile(hdf5_path):
            raise FileNotFoundError(
                f"UV continuum step: model HDF5 not found: {hdf5_path}")

        python_exe = uv_cfg.get('python_executable') or _sys.executable
        cmd = [python_exe, tool, hdf5_path]
        if uv_cfg.get('force'):
            cmd.append('--force')
        extra_args = uv_cfg.get('extra_args')
        if extra_args:
            cmd += list(extra_args)

        # h2py must be importable by the tool's own subprocess - the
        # driver's environment may not have it on PYTHONPATH.
        env = os.environ.copy()
        h2py_dir = os.path.join(kosma_tau_dir, 'h2py')
        env['PYTHONPATH'] = os.pathsep.join(
            [h2py_dir] + ([env['PYTHONPATH']] if env.get('PYTHONPATH') else []))

        log_path = os.path.join(workdir, 'pdroutput', 'TEXTOUT_UVCONT')
        logger.info(f"Running UV continuum post-processing for job {job_id}: "
                   f"{' '.join(cmd)}")
        with open(log_path, 'w') as textout:
            proc = subprocess.run(
                cmd, cwd=workdir, stdout=textout, stderr=subprocess.STDOUT,
                env=env, timeout=uv_cfg.get('timeout', 300))

        closure_ok = True
        if proc.returncode == UV_CONTINUUM_CLOSURE_EXIT:
            closure_ok = False
            logger.warning(
                f"UV continuum photon-closure gate failed for job {job_id} "
                f"(exit {UV_CONTINUUM_CLOSURE_EXIT}, file left unmodified, "
                f"see {log_path}); model is NOT marked as failed")
        elif proc.returncode != 0:
            raise RuntimeError(
                f"UV continuum post-processing exited with {proc.returncode} "
                f"for job {job_id} (see {log_path})")
        else:
            logger.info(
                f"UV continuum post-processing succeeded for job {job_id}")

        if hasattr(job, 'uvcont_applied'):
            job.uvcont_applied = closure_ok
        if hasattr(job, 'uvcont_closure_ok'):
            job.uvcont_closure_ok = closure_ok
        if hasattr(job, 'uvcont_error'):
            job.uvcont_error = None
        _session.commit()

        return closure_ok
    finally:
        if session_created_locally:
            _session.close()
            logger.debug(f"run_uv_continuum: Closed local session for job {job_id}")


def run_kosma_tau(job_id, tmp_dir='./', force_onion=False, config=None, force_simline=False):
    """Run the KOSMA-tau model workflow for a job.

    Args:
        job_id (int): Job ID
        tmp_dir (str): Temporary directory path
        force_onion (bool): If True, run onion even if PDR model was skipped
        config (dict): Configuration dictionary
    """
    logger.info(f"Running KOSMA-tau model for job {job_id}")

    # Import storage backend here to avoid circular imports
    from pdr_run.storage.base import get_storage_backend

    _session = get_db_manager().get_session() # acquire session for this workflow
    try:
        job = _session.get(PDRModelJob, job_id)

        if not job:
            raise ValueError(f"Job with ID {job_id} not found")
    
        model = job.model_job_name
        zmetal, density, cmass, radiation, shieldh2 = retrieve_job_parameters(job_id, _session) # Pass session
        
        logger.info(f"MODEL is {model}")
        hdf5_out_name = 'pdrstruct' + model + '.hdf5' # check if HDF5 file already exists 
        
        # Get storage backend to check for existing files
        storage = get_storage_backend(config)
        
        # Primary workflow: Create JSON config (always)
        create_json_from_job_id(job_id, session=_session, config=config) # Pass session and config
        
        # Legacy support: Create PDRNEW.INP only if template exists
        try:
            create_pdrnew_from_job_id(job_id, session=_session) # Pass session
            logger.info("Created PDRNEW.INP for legacy compatibility")
        except FileNotFoundError:
            logger.info("PDRNEW.INP.template not found - using JSON-only workflow")
        
        # Flag to track if PDR execution was skipped
        pdr_skipped = False
        
        # Check if model already exists using storage backend
        hdf_storage_path = os.path.join(job.model_name.model_path, 'pdrgrid', hdf5_out_name)
        
        # Use storage backend to check file existence
        try:
            if hasattr(storage, 'file_exists'):
                # If storage backend has a file_exists method, use it
                model_exists = storage.file_exists(hdf_storage_path)
            else:
                # Fallback: try to get file info (this will raise an exception if file doesn't exist)
                try:
                    # This is a simple check - try to list the directory and see if our file is there
                    parent_dir = os.path.dirname(hdf_storage_path)
                    files = storage.list_files(parent_dir)
                    model_exists = os.path.basename(hdf_storage_path) in files
                except:
                    # If we can't list files or any other error, assume file doesn't exist
                    model_exists = False
                    
            logger.debug(f"Checking for existing model at: {hdf_storage_path}")
            logger.debug(f"Model exists: {model_exists}")
            
        except Exception as e:
            logger.warning(f"Could not check for existing model: {e}")
            model_exists = False
        
        if model_exists:
            logger.warning(f"Model {model} exists remotely, skipping PDR execution")
            
            # Update database entries
            update_db_pdr_output_entries(job_id, _session) # Pass session
    
            job = _session.get(PDRModelJob, job_id)
            
            # Download CTRL_IND file for onion processing if it exists
            ctrl_ind_remote_path = os.path.join(job.model_name.model_path, 'pdrgrid', f'CTRL_IND{model}')
            ctrl_ind_downloaded = False
            try:
                logger.info(f"Attempting to download CTRL_IND file from remote storage at: {ctrl_ind_remote_path}")
                
                # Check if the source file exists before attempting to retrieve
                if os.path.exists(ctrl_ind_remote_path):
                    # Create absolute path for destination to avoid path resolution issues
                    ctrl_ind_dest = os.path.abspath('CTRL_IND')
                    logger.info(f"Source file exists, downloading to: {ctrl_ind_dest}")
                    
                    storage.retrieve_file(ctrl_ind_remote_path, ctrl_ind_dest)
                    logger.info(f"Downloaded CTRL_IND file from remote storage")
                    ctrl_ind_downloaded = True
                else:
                    logger.warning(f"CTRL_IND file does not exist at: {ctrl_ind_remote_path}")
                    
            except Exception as e:
                logger.warning(f"Could not download CTRL_IND file: {e}")
                logger.debug(f"Error details: {type(e).__name__}: {str(e)}")
                
            # If download failed, check if we have it in pdroutput directory (fallback)
            if not ctrl_ind_downloaded and os.path.exists(os.path.join('pdroutput', 'CTRL_IND')):
                try:
                    import shutil
                    shutil.copy2(os.path.join('pdroutput', 'CTRL_IND'), 'CTRL_IND')
                    logger.info(f"Copied CTRL_IND from pdroutput directory as fallback")
                    ctrl_ind_downloaded = True
                except Exception as e:
                    logger.warning(f"Could not copy CTRL_IND from pdroutput: {e}")
        
            update_job_status(job_id, 'skipped', _session) # Pass session
            pdr_skipped = True
        else:
            logger.info(f"Model doesn't exist, executing PDR code")

            # Run PDR model
            run_pdr(job_id, tmp_dir, session=_session, config=config) # Pass session

            # UV H2 dissociation continuum post-processing (opt-in via
            # config['uv_continuum']['enabled']). Runs right after run_pdr,
            # i.e. before the onion loop, SIMLINE, and copy_pdroutput below,
            # so the stored/post-processed HDF5 carries the continuum. Only
            # attempted for a run that actually produced a usable model
            # (see job_status.SUCCESS_STATUSES).
            uvcont_enabled = (config or {}).get('uv_continuum', {}).get('enabled', False)
            if uvcont_enabled and job.status in SUCCESS_STATUSES:
                try:
                    run_uv_continuum(job_id, tmp_dir, config=config, session=_session)
                except Exception as e:
                    # A broken/misconfigured post-processing step must not
                    # turn an already-successful physics model into a
                    # failed grid node - log it and keep going. A closure
                    # *gate* failure never reaches this except (see
                    # run_uv_continuum docstring); only genuine tool
                    # errors (missing deps, bad config, timeout) do.
                    logger.error(
                        f"UV continuum post-processing failed for job {job_id}: {e}",
                        exc_info=True)
                    if hasattr(job, 'uvcont_error'):
                        job.uvcont_error = str(e)
                        _session.commit()
            elif uvcont_enabled:
                logger.info(
                    f"Skipping UV continuum post-processing for job {job_id}: "
                    f"model status '{job.status}' is not a success status")

        # Run onion for each species if PDR was not skipped or force_onion is True
        if not pdr_skipped or force_onion:
            species = string_to_list(job.onion_species)
            for spec in species:
                logger.info(f"Processing species: {spec}")
                
                # Set up onion directory
                set_oniondir(spec)
                
                # Run onion model
                run_onion(spec, job_id, tmp_dir, config=config, session=_session) # Pass session
                # Copy output files
                copy_onionoutput(spec, job_id, config=config, session=_session) # Pass session
        else:
            logger.info(f"Skipping onion runs as PDR was skipped. Use force_onion=True to override.")

        # SIMLINE post-processing (opt-in via config['simline']['enabled'] or
        # force_simline). force_simline also runs it when the PDR step was
        # skipped because the model already exists (RT-only reruns on a grid).
        simline_enabled = force_simline or (config or {}).get('simline', {}).get('enabled', False)
        if simline_enabled:
            if not pdr_skipped or force_onion or force_simline:
                run_simline(job_id, tmp_dir, config=config, session=_session)
            else:
                logger.info("Skipping SIMLINE as PDR was skipped. Use force_simline=True to override.")
        
        # Copy output files - moved after the onion run because ONION modifies the HDF5 file
        # Only copy if we actually ran the PDR model
        if not pdr_skipped:
            copy_pdroutput(job_id, config=config, session=_session) # Pass session
    
        logger.info(f"Completed KOSMA-tau model run for job {job_id}")

    finally:
        # CRITICAL FIX: Always close the session to prevent connection leaks
        _session.close() # Close the session acquired at the beginning of run_kosma_tau
        logger.debug(f"Database session closed for job {job_id} in run_kosma_tau")

# Sentinel written to HDFFile.sha256_sum* when a model is skipped because it
# already exists in storage (update_db_pdr_output_entries below): we never
# downloaded/hashed the actual bytes, so there is no real checksum to store.
# Deliberately NOT a valid hex string (a real sha256_sum is 64 lowercase hex
# chars) so any code or query naively treating this column as a real digest
# fails loudly/obviously instead of silently comparing against a fake hash.
UNVERIFIED_CHECKSUM_SENTINEL = "UNVERIFIED:no-local-hash-model-skipped-exists-remotely"


def _placeholder_file_stat(full_path):
    """Best-effort (file_size, warned) for a file we are registering in the
    database without downloading/hashing it (see
    ``update_db_pdr_output_entries``). For local storage, ``full_path`` is
    already a real filesystem path, so a cheap ``os.path.getsize()`` gives
    an accurate size at negligible cost - much cheaper than a full sha256
    of a multi-hundred-MB HDF5 file, but still catches the common
    "existing file is 0 bytes / truncated" case. For a genuinely remote
    backend (SFTP/rclone) this path is not locally readable and the size
    falls back to 0, logged as such - callers must not mistake the 0 for a
    verified empty file.
    """
    try:
        if os.path.isfile(full_path):
            return os.path.getsize(full_path), False
    except OSError as exc:
        logger.warning(f"Could not stat {full_path} for size verification: {exc}")
    logger.warning(
        f"Registering {full_path} in the database without a verified size "
        "or checksum (file not locally reachable - remote storage backend). "
        f"sha256_sum will be the sentinel '{UNVERIFIED_CHECKSUM_SENTINEL}'.")
    return 0, True


def update_db_pdr_output_entries(job_id, session):
    """Update database entries for PDR output files when skipping execution.

    This function is called when a model already exists remotely and we're
    skipping the PDR execution. It creates database entries with placeholder
    values since we can't access the remote files directly.

    The size is verified cheaply where possible (local storage backend);
    the checksum is always the ``UNVERIFIED_CHECKSUM_SENTINEL`` sentinel -
    see ``_placeholder_file_stat`` - since computing a real sha256 would
    require downloading the full file, defeating the point of skipping.

    Args:
        job_id (int): Job ID
        session: Database session
    """
    from pdr_run.storage.base import get_storage_backend
    
    _session = session
    session_created_locally = False

    if _session is None:
        _session = get_db_manager().get_session()
        session_created_locally = True
        logger.debug(f"update_db_pdr_output_entries: Created local session for job {job_id}")

    try:
        job = _session.get(PDRModelJob, job_id)
        if not job:
            logger.error(f"Job with ID {job_id} not found")
            return
            
        model = job.model_job_name
        model_path = job.model_name.model_path
        
        # Define file names
        hdf_out_name = f'pdr{model}.hdf'
        hdf5_struct_out_name = f'pdrstruct{model}.hdf5'
        hdf5_chem_out_name = f'pdrchem{model}.hdf5'
        text_out_name = f'TEXTOUT' + model
        chemchk_out_name = f'chemchk{model}.out'
        mrt_out_name = f'MCDRT{model}.tar.gz'
        pdrnew_inp_file_name = f'PDRNEW{model}.INP'
        json_file_name = f'pdr_config{model}.json'
        ctrl_ind_file_name = f'CTRL_IND' + model
        
        logger.info(f"Creating database entries for existing remote model {model}")
        
        # Use current timestamp as placeholder
        import datetime
        current_time = datetime.datetime.now()
        
        # Check if HDF file entry already exists
        existing_hdf = _session.query(HDFFile).filter_by(
            parameter_id=job.kosmatau_parameters_id,
            model_name_id=job.model_name_id
        ).first()
        
        if existing_hdf:
            logger.info(f"Database entry for model {model} already exists, updating paths")
            # Update the existing entry with current paths
            existing_hdf.full_path = os.path.join(model_path, 'pdrgrid', hdf_out_name)
            existing_hdf.full_path_hdf5_s = os.path.join(model_path, 'pdrgrid', hdf5_struct_out_name)
            existing_hdf.full_path_hdf5_c = os.path.join(model_path, 'pdrgrid', hdf5_chem_out_name)
            
            # Update other paths if they exist as fields
            if hasattr(existing_hdf, 'full_path_textout'):
                existing_hdf.full_path_textout = os.path.join(model_path, 'pdrgrid', text_out_name)
            if hasattr(existing_hdf, 'full_path_chemchk'):
                existing_hdf.full_path_chemchk = os.path.join(model_path, 'pdrgrid', chemchk_out_name)
            if hasattr(existing_hdf, 'full_path_mcdrt'):
                existing_hdf.full_path_mcdrt = os.path.join(model_path, 'pdrgrid', mrt_out_name)
            if hasattr(existing_hdf, 'full_path_config'):
                existing_hdf.full_path_config = os.path.join(model_path, 'pdrgrid', json_file_name)
            if hasattr(existing_hdf, 'full_path_ctrl_ind'):
                existing_hdf.full_path_ctrl_ind = os.path.join(model_path, 'pdrgrid', ctrl_ind_file_name)
        else:
            logger.info(f"Creating new database entry for model {model}")

            full_path = os.path.join(model_path, 'pdrgrid', hdf_out_name)
            full_path_hdf5_s = os.path.join(model_path, 'pdrgrid', hdf5_struct_out_name)
            full_path_hdf5_c = os.path.join(model_path, 'pdrgrid', hdf5_chem_out_name)
            file_size, _ = _placeholder_file_stat(full_path)
            file_size_hdf5_s, _ = _placeholder_file_stat(full_path_hdf5_s)
            file_size_hdf5_c, _ = _placeholder_file_stat(full_path_hdf5_c)

            # Create a minimal HDFFile entry with only the required/known fields
            hdf_file_args = {
                'parameter_id': job.kosmatau_parameters_id,
                'model_name_id': job.model_name_id,
                'file_name': hdf_out_name,
                'full_path': full_path,
                'path': os.path.join(model_path, 'pdrgrid'),
                'modification_time': current_time,
                'sha256_sum': UNVERIFIED_CHECKSUM_SENTINEL,
                'file_size': file_size,
                # HDF5 structure file fields (these seem to exist based on copy_pdroutput)
                'file_name_hdf5_s': hdf5_struct_out_name,
                'full_path_hdf5_s': full_path_hdf5_s,
                'path_hdf5_s': os.path.join(model_path, 'pdrgrid'),
                'modification_time_hdf5_s': current_time,
                'sha256_sum_hdf5_s': UNVERIFIED_CHECKSUM_SENTINEL,
                'file_size_hdf5_s': file_size_hdf5_s,
                # HDF5 chemistry file fields (these seem to exist based on copy_pdroutput)
                'file_name_hdf5_c': hdf5_chem_out_name,
                'full_path_hdf5_c': full_path_hdf5_c,
                'path_hdf5_c': os.path.join(model_path, 'pdrgrid'),
                'modification_time_hdf5_c': current_time,
                'sha256_sum_hdf5_c': UNVERIFIED_CHECKSUM_SENTINEL,
                'file_size_hdf5_c': file_size_hdf5_c,
            }

            # Try to create the HDFFile with these arguments
            try:
                hdf_file = HDFFile(**hdf_file_args)
                _session.add(hdf_file)
            except TypeError as e:
                logger.error(f"Error creating HDFFile: {e}")
                # Create with minimal required fields only
                minimal_args = {
                    'parameter_id': job.kosmatau_parameters_id,
                    'model_name_id': job.model_name_id,
                    'file_name': hdf_out_name,
                    'full_path': full_path,
                    'path': os.path.join(model_path, 'pdrgrid'),
                    'modification_time': current_time,
                    'sha256_sum': UNVERIFIED_CHECKSUM_SENTINEL,
                    'file_size': file_size,
                }
                hdf_file = HDFFile(**minimal_args)
                _session.add(hdf_file)
        
        # Update job output file paths (only update fields that exist)
        job.output_hdf_file = os.path.join(model_path, 'pdrgrid', hdf_out_name)
        
        # Check if these attributes exist before setting them
        if hasattr(job, 'output_hdf5_struct_file'):
            job.output_hdf5_struct_file = os.path.join(model_path, 'pdrgrid', hdf5_struct_out_name)
        if hasattr(job, 'output_hdf5_chem_file'):
            job.output_hdf5_chem_file = os.path.join(model_path, 'pdrgrid', hdf5_chem_out_name)
        if hasattr(job, 'output_textout_file'):
            job.output_textout_file = os.path.join(model_path, 'pdrgrid', text_out_name)
        if hasattr(job, 'output_chemchk_file'):
            job.output_chemchk_file = os.path.join(model_path, 'pdrgrid', chemchk_out_name)
        if hasattr(job, 'output_mcdrt_zip_file'):
            job.output_mcdrt_zip_file = os.path.join(model_path, 'pdrgrid', mrt_out_name)
        if hasattr(job, 'output_config_file'):
            job.output_config_file = os.path.join(model_path, 'pdrgrid', json_file_name)
        if hasattr(job, 'output_ctrl_ind_file'):
            job.output_ctrl_ind_file = os.path.join(model_path, 'pdrgrid', ctrl_ind_file_name)
        
        try:
            _session.commit()
            logger.info(f"Successfully updated database entries for existing model {model}")
        except Exception as e:
            logger.error(f"Failed to update database entries: {e}")
            _session.rollback()
            raise
    finally:
        if session_created_locally:
            _session.close()
            logger.debug(f"update_db_pdr_output_entries: Closed local session for job {job_id}")
