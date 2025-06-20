"""KOSMA-tau model management."""

import os
import logging
import datetime
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
from pdr_run.database.connection import get_session
from pdr_run.database.models import (
    PDRModelJob, HDFFile, KOSMAtauParameters
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
    'h2_h_coll_rates'  , #
    'h2_h_reactive_colls'  , #
    'h2_use_gbar' , #
    'h2_quad_a' , #
    'ifh2des' , #
    'ifcrdes'  ,#
    'ifphdes' , #
    'ifthdes' , #
    'bindsites' , #
    'ifchemheat' , #
    'ifheat_alfven' ,#
    'alfven_velocity',  #
    'alfven_column' ,  #
    'temp_start' , #
    'itmeth' , #
    'ichemeth' ,#
    'inewtonstep' , #
    'omega_neg' , #
    'omega_pos' , #
    'lambda' , #
    'use_conservation' , #
    'rescaleQF' , #
    'precondLR' , #
    'resortQF' , #
    'nconv_time' ,  #
    'time_dependent' , #
    'use_dlsodes' , #
    'use_dlsoda' , #
    'use_dvodpk' , #
    'first_time_step_yrs' ,  #
    'max_time_yrs' ,  #
    'num_time_steps' , #
    'rtol_chem' ,  #
    'atol_chem' , #
    'Xhtry' ,  #
    'Niter' ,#
    'rtol_iter' ,  #
    'step1' ,  #
    'step2' ,  #
    'ihdfout' , #
    'dbglvl' , #
    'grid'  , #
    'elfrac4' ,   # He
    'elfrac12' ,  # 12C
    'elfrac13' ,  # 13C
    'elfrac14' ,  # N
    'elfrac16' ,  # 16O
    'elfrac18' ,  # 18O
    'elfrac19' ,  # Fl
    'elfrac23' ,  # Na
    'elfrac24',  # Mg
    'elfrac28' ,  # Si
    'elfrac31' , # P
    'elfrac32' ,  # S
    'elfrac35' ,  # Cl
    'elfrac56' ,   # Fe
    'species'        # long string containing all species separated by whitespace
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
        template_name: Name of the template file
        
    Returns:
        String containing the template content
    """
    logger.info(f"Looking for template file: '{template_name}'")
    logger.debug(f"Current working directory: {os.getcwd()}")
    
    # Keep track of all attempted paths for error reporting
    attempted_paths = []
    
    # Find the template in the PDR_INP_DIRS locations
    if isinstance(PDR_INP_DIRS, list):
        # Try all possible locations
        logger.debug(f"Searching across {len(PDR_INP_DIRS)} potential template directories")
        for dir_path in PDR_INP_DIRS:
            template_path = os.path.join(dir_path, template_name)
            attempted_paths.append(template_path)
            logger.debug(f"Trying path: {template_path}")
            
            if os.path.exists(template_path):
                logger.info(f"Template found at: {template_path}")
                with open(template_path, "r") as f:
                    content = f.read()
                logger.debug(f"Successfully read template ({len(content)} bytes)")
                return content
    else:
        # If it's a string, just try that path
        #template_path = os.path.join(PDR_INP_DIRS, "templates", template_name)
        template_path = template_name
        attempted_paths.append(template_path)
        logger.debug(f"Trying single path: {template_path}")
        
        if os.path.exists(template_path):
            logger.info(f"Template found at: {template_path}")
            with open(template_path, "r") as f:
                content = f.read()
            logger.debug(f"Successfully read template ({len(content)} bytes)")
            return content
        else:
            logger.debug(f"Template not found at: {template_path}")
    
    # If we get here, we couldn't find the template
    error_msg = f"Template file '{template_name}' not found in any template directory. Attempted paths: {attempted_paths}"
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
    if session is None:
        session = get_session()
    
    job = session.get(PDRModelJob, job_id)
    model_params = session.get(KOSMAtauParameters, job.kosmatau_parameters_id)
    
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

def create_json_from_job_id(job_id, session=None, return_content=False):
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
    if session is None:
        session = get_session()
    
    job = session.get(PDRModelJob, job_id)
    model_params = session.get(KOSMAtauParameters, job.kosmatau_parameters_id)
    
    # Get the template content
    try:
        template_content = open_template("pdr_config.json.template")
    except FileNotFoundError:
        logger.warning("pdr_config.json.template not found. Skipping JSON creation.")
        if return_content:
            return ""
        return None
    
    # Transform parameters directly from model_params
    transformed_params = transform(model_params.__dict__)
    
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
    register_json_file(job_id=job_id, name="pdr_config.json", path=os.path.abspath(output_path))

    # Log that we created the file
    logger.info(f"Created pdr_config.json for job {job_id}")
    
    # Return content if requested
    if return_content:
        return output
    
    return None

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

def run_pdr(job_id, tmp_dir='./'):
    """Run the PDR model for a given job.
    
    Args:
        job_id (int): Job ID
        tmp_dir (str): Temporary directory path
    """
    session = get_session()
    job = session.get(PDRModelJob, job_id)
    
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
        update_job_status(job_id, 'running', session)
        
        try:
            # Run the PDR model
            p = subprocess.call(
                './' + exe.executable_file_name,
                stdout=textout,
                stderr=textout,
                shell=True
            )
            
            if p == 0:
                logger.info("pdrexe finished without problems")
                job.status = 'finished'
            else:
                logger.error(f"There was a problem, subprocess.call returns: {p}")
                job.status = 'problem'
            
            print(' ', file=textout)
            print(f'Output copied to directory {os.path.join(model.model_path, "pdrgrid")}', file=textout)
            logger.info(f'Output copied to directory {os.path.join(model.model_path, "pdrgrid")}')
            
            now_end = datetime.datetime.now()
            print('End of PDR Job: ' + now_end.strftime("%Y-%m-%d %H:%M:%S"))
            print('End of PDR Job: ' + now_end.strftime("%Y-%m-%d %H:%M:%S"), file=textout)
            logger.info('End of PDR Job: ' + now_end.strftime("%Y-%m-%d %H:%M:%S"))
            
            job.time_of_finish = now_end
            update_job_status(job_id, job.status, session)
            
        except Exception as e:
            logger.error(f"Unexpected error: {str(e)}", exc_info=True)
            job.status = 'ERROR'
            job.time_of_finish = datetime.datetime.now()
            update_job_status(job_id, 'ERROR', session)
            raise

def copy_pdroutput(job_id, config=None):
    """Copy PDR output files to the model directory.
    
    Args:
        job_id (int): Job ID
    """
    from pdr_run.storage.base import get_storage_backend
    session = get_session()
    job = session.get(PDRModelJob, job_id)
    
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
    
    # Copy output files to the model directory
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

    
    if os.path.exists(os.path.join('pdroutput', 'pdrout.hdf')):
        local_source = os.path.join('pdroutput', 'pdrout.hdf')
        remote_dest  = os.path.join(model_path, 'pdrgrid', hdf_out_name)
        storage.store_file(local_source, remote_dest)
        job.output_hdf4_file = os.path.join(model_path, 'pdrgrid', hdf_out_name)
    
    if os.path.exists(os.path.join('pdroutput', 'pdrstruct_s.hdf5')):
        local_source = os.path.join('pdroutput', 'pdrstruct_s.hdf5')
        remote_dest = os.path.join(model_path, 'pdrgrid', hdf5_struct_out_name)
        storage.store_file(local_source, remote_dest)
        job.output_hdf5_struct_file = os.path.join(model_path, 'pdrgrid', hdf5_struct_out_name)

    if os.path.exists(os.path.join('pdroutput', 'pdrchem_c.hdf5')):
        local_source = os.path.join('pdroutput', 'pdrchem_c.hdf5')
        remote_dest = os.path.join(model_path, 'pdrgrid', hdf5_chem_out_name)
        storage.store_file(local_source, remote_dest)
        job.output_hdf5_chem_file = os.path.join(model_path, 'pdrgrid', hdf5_chem_out_name)

    if os.path.exists(os.path.join('pdroutput', 'chemchk.out')):
        local_source = os.path.join('pdroutput', 'chemchk.out')
        remote_dest = os.path.join(model_path, 'pdrgrid', chemchk_out_name)
        storage.store_file(local_source, remote_dest)
        job.output_chemchk_file = os.path.join(model_path, 'pdrgrid', chemchk_out_name)
    
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
    
    if os.path.exists('PDRNEW.INP'):
        local_source = os.path.join('PDRNEW.INP')
        remote_dest = os.path.join(model_path, 'pdrgrid', pdrnew_inp_file_name)
        storage.store_file(local_source, remote_dest)

        job.input_pdrnew_inp_file = os.path.join(model_path, 'pdrgrid', pdrnew_inp_file_name)
    
    if os.path.exists('pdr_config.json'):
        local_source = 'pdr_config.json'
        remote_dest = os.path.join(model_path, 'pdrgrid', json_file_name)
        storage.store_file(local_source, remote_dest)
        job.input_json_file = os.path.join(model_path, 'pdrgrid', json_file_name)

    if os.path.exists(os.path.join('pdroutput', 'CTRL_IND')):
        local_source = os.path.join('pdroutput', 'CTRL_IND')
        remote_dest = os.path.join(model_path, 'pdrgrid', ctrl_ind_file_name)
        storage.store_file(local_source, remote_dest)
        # copy for CTRL_IND onionexe
        shutil.copyfile(
            os.path.join('pdroutput', 'CTRL_IND'),
            'CTRL_IND'
        )
       
    session.commit()
    

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

    # Use session.query().filter_by().first() instead of session.query().get() for complex queries
    instance = session.query(HDFFile).filter_by(sha256_sum=sha_key).first()
    
    if not instance:
        hdf = get_or_create(
            session,
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

def run_onion(spec, job_id, tmp_dir='./', config=None):
    """Run the onion model for a species.
    
    Args:
        spec (str): Species name
        job_id (int): Job ID
        tmp_dir (str): Temporary directory path
        config (dict): Configuration dictionary containing executable name
    """
    from pdr_run.storage.base import get_storage_backend

    storage = get_storage_backend(config)
    session = get_session()
    job =  session.get(PDRModelJob, job_id)
    
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

def copy_onionoutput(spec, job_id, config=None):
    """Copy onion output files to the model directory.
    
    Args:
        spec (str): Species name
        job_id (int): Job ID
    """
    from pdr_run.storage.base import get_storage_backend

    storage = get_storage_backend(config)
    session = get_session()
    job = session.get(PDRModelJob, job_id)

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
    
    for f in onion_files:
        path = os.path.join('onionoutput', f)
        if os.path.exists(path):
            remote_dest = os.path.join(model_path, 'oniongrid', 'ONION' + model + '.' + f)
            storage.store_file(path, remote_dest)
    
    storage.store_file(
        os.path.join('onionoutput', 'TEXTOUT'),
        os.path.join(model_path, 'oniongrid', 'TEXTOUT' + model + "_" + spec)
    )
    
    logger.info(f"Copied onion output for species {spec}")

def run_kosma_tau(job_id, tmp_dir='./', force_onion=False, config=None):
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
    
    session = get_session()
    job = session.get(PDRModelJob, job_id)
    
    if not job:
        raise ValueError(f"Job with ID {job_id} not found")
    
    model = job.model_job_name
    zmetal, density, cmass, radiation, shieldh2 = retrieve_job_parameters(job_id, session)
    
    logger.info(f"MODEL is {model}")
    hdf5_out_name = 'pdrstruct' + model + '.hdf5' # check if HDF5 file already exists 
    
    # Get storage backend to check for existing files
    storage = get_storage_backend(config)
    
    # Primary workflow: Create JSON config (always)
    create_json_from_job_id(job_id, session)
    
    # Legacy support: Create PDRNEW.INP only if template exists
    try:
        create_pdrnew_from_job_id(job_id, session)
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
        update_db_pdr_output_entries(job_id, session)

        job = session.get(PDRModelJob, job_id)
        
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
    
        update_job_status(job_id, 'skipped', session)
        pdr_skipped = True
    else:
        logger.info(f"Model doesn't exist, executing PDR code")
        
        # Run PDR model
        run_pdr(job_id, tmp_dir)
    
    # Run onion for each species if PDR was not skipped or force_onion is True
    if not pdr_skipped or force_onion:
        species = string_to_list(job.onion_species)
        for spec in species:
            logger.info(f"Processing species: {spec}")
            
            # Set up onion directory
            set_oniondir(spec)
            
            # Run onion model
            run_onion(spec, job_id, tmp_dir, config=config)
            # Copy output files
            copy_onionoutput(spec, job_id, config=config)
    else:
        logger.info(f"Skipping onion runs as PDR was skipped. Use force_onion=True to override.")
    
    # Copy output files - moved after the onion run because ONION modifies the HDF5 file
    # Only copy if we actually ran the PDR model
    if not pdr_skipped:
        copy_pdroutput(job_id, config=config)

    logger.info(f"Completed KOSMA-tau model run for job {job_id}")

def update_db_pdr_output_entries(job_id, session):
    """Update database entries for PDR output files when skipping execution.
    
    This function is called when a model already exists remotely and we're
    skipping the PDR execution. It creates database entries with placeholder
    values since we can't access the remote files directly.
    
    Args:
        job_id (int): Job ID
        session: Database session
    """
    from pdr_run.storage.base import get_storage_backend
    
    job = session.get(PDRModelJob, job_id)
    if not job:
        logger.error(f"Job with ID {job_id} not found")
        return
        
    model = job.model_job_name
    model_path = job.model_name.model_path
    
    # Define file names
    hdf_out_name = f'pdr{model}.hdf'
    hdf5_struct_out_name = f'pdrstruct{model}.hdf5'
    hdf5_chem_out_name = f'pdrchem{model}.hdf5'
    text_out_name = f'TEXTOUT{model}'
    chemchk_out_name = f'chemchk{model}.out'
    mrt_out_name = f'MCDRT{model}.tar.gz'
    pdrnew_inp_file_name = f'PDRNEW{model}.INP'
    json_file_name = f'pdr_config{model}.json'
    ctrl_ind_file_name = f'CTRL_IND{model}'
    
    logger.info(f"Creating database entries for existing remote model {model}")
    
    # Use current timestamp as placeholder
    import datetime
    current_time = datetime.datetime.now()
    
    # Check if HDF file entry already exists
    existing_hdf = session.query(HDFFile).filter_by(
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
        
        # Create a minimal HDFFile entry with only the required/known fields
        hdf_file_args = {
            'parameter_id': job.kosmatau_parameters_id,
            'model_name_id': job.model_name_id,
            'file_name': hdf_out_name,
            'full_path': os.path.join(model_path, 'pdrgrid', hdf_out_name),
            'path': os.path.join(model_path, 'pdrgrid'),
            'modification_time': current_time,
            'sha256_sum': "remote_file_no_local_hash",
            'file_size': 0,  # Placeholder value
            # HDF5 structure file fields (these seem to exist based on copy_pdroutput)
            'file_name_hdf5_s': hdf5_struct_out_name,
            'full_path_hdf5_s': os.path.join(model_path, 'pdrgrid', hdf5_struct_out_name),
            'path_hdf5_s': os.path.join(model_path, 'pdrgrid'),
            'modification_time_hdf5_s': current_time,
            'sha256_sum_hdf5_s': "remote_file_no_local_hash",
            'file_size_hdf5_s': 0,  # Placeholder value
            # HDF5 chemistry file fields (these seem to exist based on copy_pdroutput)
            'file_name_hdf5_c': hdf5_chem_out_name,
            'full_path_hdf5_c': os.path.join(model_path, 'pdrgrid', hdf5_chem_out_name),
            'path_hdf5_c': os.path.join(model_path, 'pdrgrid'),
            'modification_time_hdf5_c': current_time,
            'sha256_sum_hdf5_c': "remote_file_no_local_hash",
            'file_size_hdf5_c': 0,  # Placeholder value
        }
        
        # Try to create the HDFFile with these arguments
        try:
            hdf_file = HDFFile(**hdf_file_args)
            session.add(hdf_file)
        except TypeError as e:
            logger.error(f"Error creating HDFFile: {e}")
            # Create with minimal required fields only
            minimal_args = {
                'parameter_id': job.kosmatau_parameters_id,
                'model_name_id': job.model_name_id,
                'file_name': hdf_out_name,
                'full_path': os.path.join(model_path, 'pdrgrid', hdf_out_name),
                'path': os.path.join(model_path, 'pdrgrid'),
                'modification_time': current_time,
                'sha256_sum': "remote_file_no_local_hash",
                'file_size': 0,
            }
            hdf_file = HDFFile(**minimal_args)
            session.add(hdf_file)
    
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
        session.commit()
        logger.info(f"Successfully updated database entries for existing model {model}")
    except Exception as e:
        logger.error(f"Failed to update database entries: {e}")
        session.rollback()
        raise

def replace_template_variables(template_content, parameters):
    """Replace variables in template with parameter values.
    
    Args:
        template_content: Template content string
        parameters: Dictionary of parameters
        
    Returns:
        String with replaced variables
    """
    logger.info(f"Replacing template variables with {len(parameters)} parameters")
    result = template_content
    
    for var_name, var_value in parameters.items():
        placeholder = f"KT_VAR{var_name}_"
        if placeholder in result:
            # Format the value based on its type
            if isinstance(var_value, int):
                # Format integers without decimal point or scientific notation
                formatted_value = str(var_value)
                logger.debug(f"Replacing {placeholder} with integer value: {formatted_value}")
            elif isinstance(var_value, float):
                # Format floats with scientific notation
                formatted_value = f"{var_value:.4e}"
                logger.debug(f"Replacing {placeholder} with float value: {formatted_value}")
            else:
                # Convert other types to string
                formatted_value = str(var_value)
                logger.debug(f"Replacing {placeholder} with string value: {formatted_value}")
                
            # Perform the replacement
            result = result.replace(placeholder, formatted_value)
            
    # Check for any unreplaced variables (debugging)
    unreplaced = []
    for line in result.split('\n'):
        if "KT_VAR" in line:
            unreplaced.append(line.strip())
    
    if unreplaced:
        logger.warning(f"Found {len(unreplaced)} unreplaced template variables:")
        for line in unreplaced[:5]:  # Only log the first 5 unreplaced variables
            logger.warning(f"  {line}")
        if len(unreplaced) > 5:
            logger.warning(f"  ...and {len(unreplaced) - 5} more")
            
    return result