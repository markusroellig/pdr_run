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
from pdr_run.models.job_status import (
    determine_job_status, SUCCESS_STATUSES, COMPLETE_OUTPUT_STATUSES,
    POSTPROCESS_STATUSES, STATUS_SKIPPED, RUN_STATUS_FILE,
)
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

def locate_template(template_name):
    """Find a template file and return its path.
    
    Args:
        template_name (str): Name or path of the template file
        
    Returns:
        Path of the first match in '.' then PDR_INP_DIRS
        
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
            return template_path
    
    # If we get here, we couldn't find the template
    error_msg = f"Template file '{template_name}' not found. Attempted paths: {attempted_paths}"
    logger.error(error_msg)
    raise FileNotFoundError(error_msg)


def open_template(template_name):
    """Return the contents of a template file (see ``locate_template``).

    Raises:
        FileNotFoundError: If the template file cannot be located
    """
    template_path = locate_template(template_name)
    with open(template_path, "r") as f:
        content = f.read()
    logger.debug(f"Successfully read template ({len(content)} bytes)")
    return content


def _config_provenance(config_text, template_name):
    """Return ``(config_json, template_sha256)`` for a rendered pdr_config.json.

    ``config_json`` is the rendered config parsed to a normalized dict, or None
    (with a WARNING) if it does not parse; ``template_sha256`` is the sha256 of
    the template file (None if the file cannot be hashed). Never raises: the
    provenance record must not make a model run fail.
    """
    import hashlib
    import json
    template_sha256 = None
    try:
        with open(locate_template(template_name), "rb") as fh:
            template_sha256 = hashlib.sha256(fh.read()).hexdigest()
    except Exception as exc:
        logger.warning(f"Could not hash template {template_name}: {exc}")
    parsed = None
    try:
        # Same tolerance as the preflight check and json-fortran: comments
        # (strip_json_comments) and trailing commas.
        from pdr_run.cli.preflight import strip_json_comments
        stripped = strip_json_comments(config_text)
        try:
            parsed = json.loads(stripped)
        except json.JSONDecodeError:
            parsed = json.loads(re.sub(r',(\s*[}\]])', r'\1', stripped))
    except Exception as exc:
        logger.warning(f"Rendered pdr_config.json is not parseable ({exc}); "
                       "config_json stays NULL")
        parsed = None
    return parsed, template_sha256

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
        
        # Full-config provenance on the job row (never fatal; NULL if the
        # database has no such columns yet or the config does not parse).
        if hasattr(job, 'config_json'):
            try:
                job.config_json, job.template_sha256 = _config_provenance(
                    output, json_template_file_name)
                _session.commit()
            except Exception as exc:
                _session.rollback()
                logger.warning(f"Could not store config provenance for job {job_id}: {exc}")

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


def _store(storage, local_path, remote_path):
    """Store one file; True on success, False (logged) on any failure.

    The storage backends signal a persisting failure after their retries
    either by returning False (SFTP/rclone) or by raising (local); both
    become False here so that callers can go on storing the remaining files
    and report one ``failed_storage`` at the end.
    """
    try:
        if storage.store_file(local_path, remote_path) is False:
            logger.error(f"Storing {local_path} as {remote_path} failed after retries")
            return False
        return True
    except Exception as exc:  # noqa: BLE001
        logger.error(f"Storing {local_path} as {remote_path} failed: {exc}")
        return False


GZIP_LEVEL = 6
GZ_SUFFIX = '.gz'


def compress_patterns(config):
    """The ``storage.compress_files`` list of *config* (default: empty = no
    compression). A bare string is accepted as a one-element list."""
    pats = ((config or {}).get('storage') or {}).get('compress_files') or []
    return [pats] if isinstance(pats, str) else list(pats)


def should_compress(patterns, *names):
    """True if any of *names* (stored name, local file name) matches one of
    the fnmatch *patterns*. Files that are already ``.gz`` are never
    compressed again, and the pdrstruct file is never compressed (it is
    compressed internally and read directly by downstream tools)."""
    import fnmatch
    names = [n for n in names if n]
    if not names or names[0].endswith(GZ_SUFFIX) or names[0].startswith('pdrstruct'):
        return False
    return any(fnmatch.fnmatchcase(n, pat) for pat in patterns for n in names)


def gzip_file(src, dst, level=GZIP_LEVEL):
    """Stream *src* into the gzip file *dst* (never loads the file into
    memory). The header carries neither name nor time, so the same input
    always gives the same bytes (stable checksum). *dst* is removed if the
    compression fails."""
    import gzip
    try:
        with open(src, 'rb') as fin, open(dst, 'wb') as raw, \
                gzip.GzipFile(filename='', mode='wb', compresslevel=level,
                              fileobj=raw, mtime=0) as fout:
            shutil.copyfileobj(fin, fout, 1024 * 1024)
    except BaseException:
        if os.path.exists(dst):
            os.remove(dst)
        raise


def gunzip_file(src, dst):
    """Stream the gzip file *src* into *dst*; *dst* is removed on failure."""
    import gzip
    try:
        with gzip.open(src, 'rb') as fin, open(dst, 'wb') as fout:
            shutil.copyfileobj(fin, fout, 1024 * 1024)
    except BaseException:
        if os.path.exists(dst):
            os.remove(dst)
        raise


def _storage_has(storage, path):
    """Existence check through the backend (file_exists, else list_files)."""
    if hasattr(storage, 'file_exists'):
        return bool(storage.file_exists(path))
    try:
        return os.path.basename(path) in storage.list_files(os.path.dirname(path))
    except Exception:  # noqa: BLE001
        return False


def resolve_stored_name(storage, path):
    """Path under which *path* is actually stored: *path* itself, else
    ``path + '.gz'`` (whole-file compression, ``storage.compress_files``),
    else None."""
    if _storage_has(storage, path):
        return path
    if _storage_has(storage, path + GZ_SUFFIX):
        return path + GZ_SUFFIX
    return None


def retrieve_decompressed(storage, remote_path, local_path):
    """Fetch *remote_path* into *local_path* as an uncompressed file, also
    when it is stored as ``remote_path + '.gz'``."""
    if remote_path.endswith(GZ_SUFFIX) or _storage_has(storage, remote_path) \
            or not _storage_has(storage, remote_path + GZ_SUFFIX):
        storage.retrieve_file(remote_path, local_path)
        return
    tmp_gz = local_path + '.gz.part'
    try:
        storage.retrieve_file(remote_path + GZ_SUFFIX, tmp_gz)
        gunzip_file(tmp_gz, local_path)
    finally:
        if os.path.exists(tmp_gz):
            os.remove(tmp_gz)


def _mark_failed_storage(job_id, session, what):
    """Give the job the terminal status 'failed_storage' (results could not
    be stored after all retries). Nothing later in run_kosma_tau overwrites
    it - only a genuinely unexpected exception does."""
    job = session.get(PDRModelJob, job_id)
    logger.error(f"Job {job_id}: storing {what} failed; status "
                 f"'{job.status if job else None}' -> 'failed_storage'")
    update_job_status(job_id, 'failed_storage', session)


def _stored_result_status(session, job):
    """Status of the most recent earlier job that computed this node (rows
    that only skipped it or are still running do not count); None if the DB
    knows no such job."""
    prev = (session.query(PDRModelJob)
            .filter(PDRModelJob.model_name_id == job.model_name_id,
                    PDRModelJob.model_job_name == job.model_job_name,
                    PDRModelJob.id != job.id,
                    PDRModelJob.status.notin_([STATUS_SKIPPED, 'running', 'pending']))
            .order_by(PDRModelJob.id.desc()).first())
    return prev.status if prev else None


def rerun_selects(stored_status, rerun):
    """True if a node whose stored result has status *stored_status* is to be
    recomputed for ``--rerun`` selection *rerun* (iterable of 'all',
    'failed' = every non-success status, or literal job statuses)."""
    from pdr_run.models.job_status import JOB_SUCCESS_STATES
    if not rerun:
        return False
    if 'all' in rerun:
        return True
    if stored_status is None:
        return False
    return stored_status in rerun or (
        'failed' in rerun and stored_status not in JOB_SUCCESS_STATES)


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

def copy_pdroutput(job_id, config=None, session=None, model_status=None):
    """Copy PDR output files to the model directory.
    
    Args:
        job_id (int): Job ID
        config (dict): Configuration dictionary
        session (sqlalchemy.orm.Session, optional): Database session. If None, a 
            new session will be created and closed. Defaults to None.
        model_status (str, optional): the model's own status from the
            KOSMA-tau run (default: the job's current status). Passed by
            run_kosma_tau because a post-processing storage failure may
            already have changed job.status to 'failed_storage'.

    Returns:
        bool: False if storing failed after all retries (the job then has
        status 'failed_storage'), True otherwise. Result files are stored
        only for complete outputs (job_status.COMPLETE_OUTPUT_STATUSES);
        for other statuses only the logs/config are stored.
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
        
        # Store what exists. Diagnostic files (TEXTOUT, run_status.json,
        # pdrexe_error.log, the input config) are always stored so that the
        # cause of a failure can be diagnosed. The result files (HDF4/HDF5,
        # chemchk, MCDRT, CTRL_IND) only if the output is complete and valid
        # (job_status.COMPLETE_OUTPUT_STATUSES): a partial pdrstruct file of a
        # timed-out/aborted run must not look like a finished node.
        model_status = model_status or job.status
        complete = model_status in COMPLETE_OUTPUT_STATUSES
        failures = []
        pdrgrid = os.path.join(model_path, 'pdrgrid')

        patterns = compress_patterns(config)
        uploaded = {}   # remote_name -> what is actually stored (compressed files only)

        def _put(local_source, remote_name, attr=None, label=None):
            """Store one file, as <remote_name>.gz if it matches
            storage.compress_files. The temporary .gz is always removed."""
            gz_tmp = None
            try:
                upload_path, stored_name = local_source, remote_name
                if should_compress(patterns, remote_name, os.path.basename(local_source)):
                    gz_tmp = local_source + GZ_SUFFIX
                    stored_name = remote_name + GZ_SUFFIX
                    try:
                        gzip_file(local_source, gz_tmp)
                    except OSError as exc:
                        logger.error(f"Compressing {local_source} failed: {exc}")
                        gz_tmp = None
                        failures.append(remote_name)
                        return False
                    upload_path = gz_tmp
                remote_dest = os.path.join(pdrgrid, stored_name)
                ok = _store(storage, upload_path, remote_dest)
                if ok and gz_tmp:
                    uploaded[remote_name] = {
                        'name': stored_name,
                        'sha256': get_digest(gz_tmp),
                        'size': os.path.getsize(gz_tmp),
                        'size_uncompressed': os.path.getsize(local_source),
                        'mtime': os.path.getmtime(local_source),
                    }
                    logger.info(f"Compressed {remote_name}: "
                                f"{uploaded[remote_name]['size_uncompressed']} -> "
                                f"{uploaded[remote_name]['size']} bytes (gzip {GZIP_LEVEL})")
            finally:
                if gz_tmp and os.path.exists(gz_tmp):
                    os.remove(gz_tmp)
            if ok:
                if attr:
                    setattr(job, attr, remote_dest)
                logger.info(f"Successfully stored {label or stored_name} for job {job_id}")
                return True
            failures.append(remote_name)
            return False

        try:
            if os.path.exists(os.path.join('pdroutput', 'TEXTOUT')):
                if _put(os.path.join('pdroutput', 'TEXTOUT'), text_out_name,
                        'output_textout_file', 'TEXTOUT'):
                    job.log_file = job.output_textout_file

            if not complete:
                for local_source, remote_name in (
                        (os.path.join('pdroutput', RUN_STATUS_FILE),
                         'run_status' + model + '.json'),
                        ('pdrexe_error.log', 'pdrexe_error' + model + '.log'),
                        (os.path.join('pdroutput', 'pdrexe_error.log'),
                         'pdrexe_error' + model + '.log')):
                    if os.path.exists(local_source):
                        _put(local_source, remote_name)
            else:
                for local_source, remote_name, attr, label in (
                        (os.path.join('pdroutput', 'pdrout.hdf'), hdf_out_name,
                         'output_hdf4_file', 'HDF4 file'),
                        (os.path.join('pdroutput', 'pdrstruct_s.hdf5'), hdf5_struct_out_name,
                         'output_hdf5_struct_file', 'HDF5 struct file'),
                        (os.path.join('pdroutput', 'pdrchem_c.hdf5'), hdf5_chem_out_name,
                         'output_hdf5_chem_file', 'HDF5 chem file'),
                        (os.path.join('pdroutput', 'chemchk.out'), chemchk_out_name,
                         'output_chemchk_file', 'chemchk file')):
                    if os.path.exists(local_source):
                        _put(local_source, remote_name, attr, label)

                if os.path.exists('./Out'):
                    # Create tar file locally first, then upload it
                    local_tar = os.path.join('/tmp', mrt_out_name)
                    make_tarfile(local_tar, './Out')
                    _put(local_tar, mrt_out_name, 'output_mcdrt_zip_file', 'MCDRT output')
                    os.unlink(local_tar)

            if os.path.exists('PDRNEW.INP'):
                _put('PDRNEW.INP', pdrnew_inp_file_name, 'input_pdrnew_inp_file', 'PDRNEW.INP')

            if os.path.exists('pdr_config.json'):
                _put('pdr_config.json', json_file_name, 'input_json_file', 'pdr_config.json')

            if complete and os.path.exists(os.path.join('pdroutput', 'CTRL_IND')):
                if _put(os.path.join('pdroutput', 'CTRL_IND'), ctrl_ind_file_name,
                        None, 'CTRL_IND'):
                    # copy for CTRL_IND onionexe
                    shutil.copyfile(os.path.join('pdroutput', 'CTRL_IND'), 'CTRL_IND')

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
            _mark_failed_storage(job_id, _session, f"output files ({e})")
            return False

        if failures:
            _mark_failed_storage(job_id, _session, "output file(s) " + ", ".join(failures))
            return False

        if not complete:
            logger.info(f"Job {job_id}: model status '{model_status}' - stored logs only, "
                        "no result files")
            return True

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

        chem_stored = uploaded.get(hdf5_chem_out_name)
        if chem_stored:
            # stored as .gz: checksum, size and time describe the stored file
            hdf5_chem_out_name = chem_stored['name']
            sha_key_hdf5_c = chem_stored['sha256']
            local_hdf5_chem_mtime = chem_stored['mtime']
            local_hdf5_chem_size = chem_stored['size']
        elif os.path.exists(local_hdf5_chem_path):
            sha_key_hdf5_c = get_digest(local_hdf5_chem_path)
            local_hdf5_chem_mtime = os.path.getmtime(local_hdf5_chem_path)
            local_hdf5_chem_size = os.path.getsize(local_hdf5_chem_path)
        else:
            logger.error(f"Cannot find local HDF5 chemistry file: {local_hdf5_chem_path}")
            return

        chemchk_out_name = uploaded.get(chemchk_out_name, {}).get('name', chemchk_out_name)

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
        return True
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

    Returns:
        bool: False if any file could not be stored after all retries.
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
        
        ok = True
        for f in onion_files:
            path = os.path.join('onionoutput', f)
            if os.path.exists(path):
                ok &= _store(storage, path, os.path.join(
                    model_path, 'oniongrid', 'ONION' + model + '.' + f))
        ok &= _store(
            storage,
            os.path.join('onionoutput', 'TEXTOUT'),
            os.path.join(model_path, 'oniongrid', 'TEXTOUT' + model + "_" + spec))
        if ok:
            logger.info(f"Successfully copied onion output for species {spec}")
        else:
            logger.error(f"Failed to store onion output files for species {spec}, job {job_id}")
        return ok
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

    Returns False if storing a result file failed after all retries; raises
    if the pipeline itself fails.
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
            retrieve_decompressed(storage, remote_struct, workfile)

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
        ok = True
        for fname in sorted(os.listdir(simline_out)):
            src = os.path.join(simline_out, fname)
            if os.path.isfile(src):
                if _store(storage, src, os.path.join(
                        model_path, 'simlinegrid', f'SIMLINE{model}.{fname}')):
                    stored += 1
                else:
                    ok = False
        ok &= _store(storage, workfile, os.path.join(
            model_path, 'simlinegrid', f'pdrstruct{model}_simline.hdf5'))
        if ok:
            logger.info(
                f"Stored SIMLINE-augmented HDF5 and {stored} output file(s) "
                f"under simlinegrid/ for job {job_id}")
        return ok
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


def run_kosma_tau(job_id, tmp_dir='./', force_onion=False, config=None, force_simline=False,
                  rerun=None):
    """Run the KOSMA-tau model workflow for a job.

    Args:
        job_id (int): Job ID
        tmp_dir (str): Temporary directory path
        force_onion (bool): If True, run onion even if PDR model was skipped
        config (dict): Configuration dictionary
        rerun (iterable of str, optional): recompute a node whose result is
            already stored if the status of its stored result is selected
            (see ``rerun_selects``); default None = skip existing nodes.

    The model's own status (finished, not_converged, aborted, ...) is never
    overwritten by a post-processing failure: ONION/SIMLINE run only for
    ``job_status.POSTPROCESS_STATUSES``, their failures go to
    ``job.postproc_error``, and result storage failures give the terminal
    status 'failed_storage'.
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
        
        # Use storage backend to check file existence (also a stored .gz)
        try:
            model_exists = resolve_stored_name(storage, hdf_storage_path) is not None

            logger.debug(f"Checking for existing model at: {hdf_storage_path}")
            logger.debug(f"Model exists: {model_exists}")
            
        except Exception as e:
            logger.warning(f"Could not check for existing model: {e}")
            model_exists = False
        
        if model_exists and rerun:
            stored_status = _stored_result_status(_session, job)
            if rerun_selects(stored_status, rerun):
                logger.warning(
                    f"Model {model} exists (stored result status: {stored_status}); "
                    f"--rerun {','.join(rerun)} selects it: recomputing, the stored "
                    "files are replaced only when the new ones are written")
                model_exists = False

        if model_exists:
            logger.warning(f"Model {model} exists remotely, skipping PDR execution")
            
            # Update database entries
            update_db_pdr_output_entries(job_id, _session, config=config) # Pass session
    
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

        # Post-processing (ONION, SIMLINE) runs only on a usable model. For
        # aborted/timeout/missing_output there is no valid pdrstruct/CTRL_IND
        # and ONION would only raise and hide the real failure. A failure of
        # a step is recorded in job.postproc_error; it never changes the
        # model's status and never prevents storing the model output.
        model_status = job.status
        usable = pdr_skipped or model_status in POSTPROCESS_STATUSES
        postproc_errors = []

        def _postproc(step, func, *args, **kwargs):
            try:
                return func(*args, **kwargs)
            except Exception as exc:  # noqa: BLE001
                logger.error(f"{step} failed for job {job_id}: {exc}", exc_info=True)
                postproc_errors.append(f"{step}: {exc}")
                return None

        if not usable:
            logger.info(f"Job {job_id}: model status '{job.status}' is not usable; "
                        "skipping ONION/SIMLINE/UV-continuum post-processing, "
                        "storing the logs")
        else:
            # Run onion for each species if PDR was not skipped or force_onion is True
            if not pdr_skipped or force_onion:
                species = string_to_list(job.onion_species)
                for spec in species:
                    logger.info(f"Processing species: {spec}")

                    def _onion(spec=spec):
                        # Set up onion directory, run onion, copy its output
                        set_oniondir(spec)
                        run_onion(spec, job_id, tmp_dir, config=config, session=_session)
                        return copy_onionoutput(spec, job_id, config=config, session=_session)

                    if _postproc(f"ONION {spec}", _onion) is False:
                        _mark_failed_storage(job_id, _session, f"ONION output for {spec}")
            else:
                logger.info(f"Skipping onion runs as PDR was skipped. Use force_onion=True to override.")

            # SIMLINE post-processing (opt-in via config['simline']['enabled'] or
            # force_simline). force_simline also runs it when the PDR step was
            # skipped because the model already exists (RT-only reruns on a grid).
            simline_enabled = force_simline or (config or {}).get('simline', {}).get('enabled', False)
            if simline_enabled:
                if not pdr_skipped or force_onion or force_simline:
                    if _postproc("SIMLINE", run_simline, job_id, tmp_dir,
                                 config=config, session=_session) is False:
                        _mark_failed_storage(job_id, _session, "SIMLINE output")
                else:
                    logger.info("Skipping SIMLINE as PDR was skipped. Use force_simline=True to override.")

        if postproc_errors:
            job.postproc_error = '; '.join(postproc_errors)
            _session.commit()

        # Copy output files - moved after the onion run because ONION modifies the HDF5 file
        # Only copy if we actually ran the PDR model. Runs for every status:
        # logs are stored even for an unusable model (copy_pdroutput decides
        # what to store); a storage failure sets 'failed_storage' there.
        if not pdr_skipped:
            copy_pdroutput(job_id, config=config, session=_session,
                           model_status=model_status)

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


def update_db_pdr_output_entries(job_id, session, config=None):
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
        config (dict, optional): configuration (selects the storage backend)
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

        # Files stored whole-file compressed (storage.compress_files) have
        # the name <name>.gz; register what is actually stored.
        _storage = get_storage_backend(config)
        pdrgrid_dir = os.path.join(model_path, 'pdrgrid')
        hdf5_chem_out_name = os.path.basename(
            resolve_stored_name(_storage, os.path.join(pdrgrid_dir, hdf5_chem_out_name))
            or hdf5_chem_out_name)
        chemchk_out_name = os.path.basename(
            resolve_stored_name(_storage, os.path.join(pdrgrid_dir, chemchk_out_name))
            or chemchk_out_name)
        
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
