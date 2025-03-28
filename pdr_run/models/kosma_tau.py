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
    create_dir, copy_dir, move_files, make_tarfile
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
    'bindsites',  #
    'ifchemheat' , #
    'ifheat_alfven' ,#
    'alfven_velocity',  #
    'alfven_column' ,  #
    'temp_start' , #
    'itmeth' , #
    'ichemeth' ,#
    'inewtonstep', #
    'omega_neg', #
    'omega_pos', #
    'lambda', #
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
    'dbglvl', #
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
    # Find the template in the PDR_INP_DIRS locations
    if isinstance(PDR_INP_DIRS, list):
        # Try all possible locations
        for dir_path in PDR_INP_DIRS:
            template_path = os.path.join(dir_path, "templates", template_name)
            if os.path.exists(template_path):
                with open(template_path, 'r') as f:
                    return f.read()
    else:
        # If it's a string, just try that path
        template_path = os.path.join(PDR_INP_DIRS, "templates", template_name)
        if os.path.exists(template_path):
            with open(template_path, 'r') as f:
                return f.read()
    
    # If we get here, we couldn't find the template
    raise FileNotFoundError(f"Template file {template_name} not found in any template directory")

def format_scientific(value):
    """Format a number in scientific notation.
    
    Args:
        value: The number to format
        
    Returns:
        String representation in scientific notation or regular format
    """
    if isinstance(value, (int, float)):
        # For integers between 0 and 1000, use regular integer format
        if value == int(value) and 0 <= value < 1000:
            return str(int(value))
        
        # For very large numbers (>= 1000) or very small numbers (< 0.1), use scientific notation
        elif value >= 1000 or value < 0.1:
            # Use Python's built-in scientific notation formatting
            return f"{value:.1e}"
        
        # For regular floating point numbers, use standard format
        else:
            return str(float(value))
    
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
    template_content = open_template("PDRNEW.INP.template")
    
    # Transform parameters
    param_dict = {
        'xnsur': model_params.xnsur,
        'mass': model_params.mass,
        'rtot': model_params.rtot,
        'species': model_params.species,
        'grid': model_params.grid
    }
    
    # Process the template with the transformed parameters
    transformed_params = transform(param_dict)
    
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
    job = session.get(PDRModelJob,job_id)
    
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

def copy_pdroutput(job_id):
    """Copy PDR output files to the model directory.
    
    Args:
        job_id (int): Job ID
    """
    session = get_session()
    job = session.get(PDRModelJob,job_id)
    
    if not job:
        raise ValueError(f"Job with ID {job_id} not found")
    
    model = job.model_job_name
    model_path = job.model_name.model_path
    
    hdf_out_name = 'pdr' + model + '.hdf'
    hdf5_struct_out_name = 'pdrstruct' + model + '.hdf5'
    hdf5_chem_out_name = 'pdrchem' + model + '.hdf5'
    text_out_name = 'TEXTOUT' + model
    chemchk_out_name = 'chemchk' + model + '.out'
    mrt_out_name = 'MCDRT' + model + '.tar.gz'
    pdrnew_inp_file_name = 'PDRNEW' + model + '.INP'
    ctrl_ind_file_name = 'CTRL_IND' + model
    
    # Copy output files to the model directory
    if os.path.exists(os.path.join('pdroutput', 'TEXTOUT')):
        move_files(
            os.path.join('pdroutput', 'TEXTOUT'),
            os.path.join(model_path, 'pdrgrid', text_out_name)
        )
        job.log_file = os.path.join(model_path, 'pdrgrid', text_out_name)
        job.output_textout_file = os.path.join(model_path, 'pdrgrid', text_out_name)
    
    if os.path.exists(os.path.join('pdroutput', 'pdrout.hdf')):
        move_files(
            os.path.join('pdroutput', 'pdrout.hdf'),
            os.path.join(model_path, 'pdrgrid', hdf_out_name)
        )
        job.output_hdf4_file = os.path.join(model_path, 'pdrgrid', hdf_out_name)
    
    if os.path.exists(os.path.join('pdroutput', 'pdrstruct_s.hdf5')):
        move_files(
            os.path.join('pdroutput', 'pdrstruct_s.hdf5'),
            os.path.join(model_path, 'pdrgrid', hdf5_struct_out_name )
        )
        job.output_hdf5_struct_file = os.path.join(model_path, 'pdrgrid', hdf5_struct_out_name )
    
    if os.path.exists(os.path.join('pdroutput', 'pdrchem_c.hdf5')):
        move_files(
            os.path.join('pdroutput', 'pdrchem_c.hdf5'),
            os.path.join(model_path, 'pdrgrid', hdf5_chem_out_name )
        )
        job.output_hdf5_chem_file = os.path.join(model_path, 'pdrgrid', hdf5_chem_out_name )
    
    if os.path.exists(os.path.join('pdroutput', 'chemchk.out')):
        move_files(
            os.path.join('pdroutput', 'chemchk.out'),
            os.path.join(model_path, 'pdrgrid', chemchk_out_name)
        )
        job.output_chemchk_file = os.path.join(model_path, 'pdrgrid', chemchk_out_name)
    
    if os.path.exists('./Out'):
        make_tarfile(
            os.path.join(model_path, 'pdrgrid', mrt_out_name),
            './Out'
        )
        job.output_mcdrt_zip_file = os.path.join(model_path, 'pdrgrid', mrt_out_name)
    
    if os.path.exists('PDRNEW.INP'):
        shutil.copyfile(
            'PDRNEW.INP',
            os.path.join(model_path, 'pdrgrid', pdrnew_inp_file_name)
        )
        job.input_pdrnew_inp_file = os.path.join(model_path, 'pdrgrid', pdrnew_inp_file_name)
    
    if os.path.exists(os.path.join('pdroutput', 'CTRL_IND')):
        shutil.copyfile(
            os.path.join('pdroutput', 'CTRL_IND'),
            os.path.join(model_path, 'pdrgrid', ctrl_ind_file_name)
        )
        shutil.copyfile(
            os.path.join('pdroutput', 'CTRL_IND'),
            'CTRL_IND'
        )
        job.output_ctrl_ind_file = os.path.join(model_path, 'pdrgrid', ctrl_ind_file_name)
    
    session.commit()
    
    # Create HDFFile entry in the database
    sha_key = get_digest(os.path.join(model_path, 'pdrgrid', hdf_out_name))
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
            modification_time=datetime.datetime.fromtimestamp(
                os.path.getmtime(os.path.join(model_path, 'pdrgrid', hdf_out_name))),
            sha256_sum=sha_key,
            file_size=os.path.getsize(os.path.join(model_path, 'pdrgrid', hdf_out_name)),
            #HDF 5 structure file
            file_name_hdf5_s=hdf5_struct_out_name,
            full_path_hdf5_s=os.path.join(model_path, 'pdrgrid', hdf5_struct_out_name),
            path_hdf5_s=os.path.join(model_path, 'pdrgrid'),
            modification_time_hdf5_s=datetime.datetime.fromtimestamp(
                os.path.getmtime(os.path.join(model_path, 'pdrgrid', hdf5_struct_out_name))),
            sha256_sum_hdf5_s=sha_key,
            file_size_hdf5_s=os.path.getsize(os.path.join(model_path, 'pdrgrid', hdf5_struct_out_name)),
            #hdf5 chemistry file
            file_name_hdf5_c=hdf5_chem_out_name,
            full_path_hdf5_c=os.path.join(model_path, 'pdrgrid', hdf5_chem_out_name),
            path_hdf5_c=os.path.join(model_path, 'pdrgrid'),
            modification_time_hdf5_c=datetime.datetime.fromtimestamp(
                os.path.getmtime(os.path.join(model_path, 'pdrgrid', hdf5_chem_out_name))),
            sha256_sum_hdf5_c=sha_key,
            file_size_hdf5_c=os.path.getsize(os.path.join(model_path, 'pdrgrid', hdf5_chem_out_name))
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

def run_onion(spec, job_id, tmp_dir='./'):
    """Run the onion model for a species.
    
    Args:
        spec (str): Species name
        job_id (int): Job ID
        tmp_dir (str): Temporary directory path
    """
    session = get_session()
    job = session.query(PDRModelJob).get(job_id)
    
    if not job:
        raise ValueError(f"Job with ID {job_id} not found")
    

    # hdf_name = job.output_hdf4_file
    # hdf5_name = job.output_hdf5_struct_file
    # now linking to the hdf files in the pdroutput directory
    hdf_name = os.path.join(tmp_dir, 'pdroutput', 'pdrout.hdf')
    hdf5_name = os.path.join(tmp_dir, 'pdroutput', 'pdrstruct_s.hdf')
    
    # Create symbolic link to HDF file
    if os.path.exists(os.path.join(tmp_dir, 'pdrout.hdf')):
        os.remove(os.path.join(tmp_dir, 'pdrout.hdf'))
    
    os.symlink(
        hdf_name,
        os.path.join(tmp_dir, 'pdrout.hdf')
    )

   # Create symbolic link to HDF5 file
    if os.path.exists(os.path.join(tmp_dir, 'pdrstruct_s.hdf5')):
        os.remove(os.path.join(tmp_dir, 'pdrstruct_s.hdf5'))
    
    os.symlink(
        hdf5_name,
        os.path.join(tmp_dir, 'pdrstruct_s.hdf5')
    )
    
    
    # Create CTRL_IND file if it doesn't exist
    if not os.path.exists(os.path.join(tmp_dir, 'CTRL_IND')):
        p = subprocess.call(
            ['getctrlind', 'pdrout.hdf'],
            stdout=open(os.path.join('onionoutput', 'TEXTOUT'), 'w'),
            stderr=subprocess.STDOUT,
            shell=True
        )
        
        if p == 0:
            logger.info("CTRL_IND created without problems")
        else:
            logger.error(f"Couldn't create CTRL_IND, subprocess.call returns: {p}")
    
    # Run onion model
    with open(os.path.join('onionoutput', 'TEXTOUT'), 'w') as textout:
        logger.info(f"Running onion for {spec}")
        print(f"Running onion for {spec}", file=textout)
        print("Getting the CTRL_IND file", file=textout)
        
        onion_code = './onionexe'
        try:
            os.system(f"{onion_code} pdrout.hdf >> {textout.name} 2>> {textout.name}")
        except Exception as e:
            logger.error(f"Unexpected error: {str(e)}", exc_info=True)
            raise
        
        print(' ', file=textout)
    
    logger.info(f"Completed onion run for species {spec}")

def copy_onionoutput(spec, job_id):
    """Copy onion output files to the model directory.
    
    Args:
        spec (str): Species name
        job_id (int): Job ID
    """
    session = get_session()
    job = session.query(PDRModelJob).get(job_id)
    
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
            move_files(
                path,
                os.path.join(model_path, 'oniongrid', 'ONION' + model + '.' + f)
            )
    
    shutil.copyfile(
        os.path.join('onionoutput', 'TEXTOUT'),
        os.path.join(model_path, 'oniongrid', 'TEXTOUT' + model + "_" + spec)
    )
    
    logger.info(f"Copied onion output for species {spec}")

def run_kosma_tau(job_id, tmp_dir='./'):
    """Run the KOSMA-tau model workflow for a job.
    
    Args:
        job_id (int): Job ID
        tmp_dir (str): Temporary directory path
    """
    logger.info(f"Running KOSMA-tau model for job {job_id}")
    
    session = get_session()
    job = session.get(PDRModelJob,job_id)
    
    if not job:
        raise ValueError(f"Job with ID {job_id} not found")
    
    model = job.model_job_name
    zmetal, density, cmass, radiation, shieldh2 = retrieve_job_parameters(job_id, session)
    
    logger.info(f"MODEL is {model}")
    hdf5_out_name = 'pdrstruct' + model + '.hdf5' # check if HDF5 file already exists 
    
    # Set grid parameters
    set_gridparam(zmetal, density, cmass, radiation, shieldh2)
    
    # Create PDRNEW.INP
    create_pdrnew_from_job_id(job_id, session)
    
    # Check if model already exists
    hdf_storage_dir = os.path.join(job.model_name.model_path, 'pdrgrid', hdf5_out_name)
    if os.path.isfile(hdf_storage_dir):
        logger.warning(f"Model {model} exists, skipping PDR execution")
        
        # Update database entries
        update_db_pdr_output_entries(job_id, session)
        
        job = session.query(PDRModelJob).get(job_id)
        shutil.copyfile(job.output_ctrl_ind_file, 'CTRL_IND')
        update_job_status(job_id, 'skipped', session)
    else:
        logger.info(f"Model doesn't exist, executing PDR code")
        
        # Run PDR model
        run_pdr(job_id, tmp_dir)
        
    
    # Run onion for each species
    species = string_to_list(job.onion_species)
    for spec in species:
        logger.info(f"Processing species: {spec}")
        
        # Set up onion directory
        set_oniondir(spec)
        
        # Run onion model
        run_onion(spec, job_id, tmp_dir)
        
        # Copy output files
        copy_onionoutput(spec, job_id)
    
    # Copy output files  moved after the onion run because ONION modifies the HDF5 file
    copy_pdroutput(job_id)
    
    logger.info(f"Completed KOSMA-tau model run for job {job_id}")

def update_db_pdr_output_entries(job_id, session=None):
    """Update database entries for PDR output files.
    
    Args:
        job_id (int): Job ID
        session: Database session
    """
    if session is None:
        session = get_session()
    
    job = session.query(PDRModelJob).get(job_id)
    
    if not job:
        raise ValueError(f"Job with ID {job_id} not found")
    
    model = job.model_job_name
    model_path = job.model_name.model_path
    
    hdf_out_name = 'pdr' + model + '.hdf'
    hdf5_struct_out_name = 'pdrstruct' + model + '.hdf5'
    hdf5_chem_out_name = 'pdrchem' + model + '.hdf5'
    text_out_name = 'TEXTOUT' + model
    chemchk_out_name = 'chemchk' + model + '.out'
    mrt_out_name = 'MCDRT' + model + '.tar.gz'
    pdrnew_inp_file_name = 'PDRNEW' + model + '.INP'
    ctrl_ind_file_name = 'CTRL_IND' + model
    
    # Update job file paths
    if os.path.exists(os.path.join(model_path, 'pdrgrid', text_out_name)):
        job.log_file = os.path.join(model_path, 'pdrgrid', text_out_name)
        job.output_textout_file = os.path.join(model_path, 'pdrgrid', text_out_name)
    
    if os.path.exists(os.path.join(model_path, 'pdrgrid', hdf_out_name)):
        job.output_hdf4_file = os.path.join(model_path, 'pdrgrid', hdf_out_name)
    
    if os.path.exists(os.path.join(model_path, 'pdrgrid', hdf5_struct_out_name)):
        job.output_hdf5_struct_file = os.path.join(model_path, 'pdrgrid', hdf5_struct_out_name)

    if os.path.exists(os.path.join(model_path, 'pdrgrid', hdf5_chem_out_name)):
        job.output_hdf5_chem_file = os.path.join(model_path, 'pdrgrid', hdf5_chem_out_name)

    if os.path.exists(os.path.join(model_path, 'pdrgrid', chemchk_out_name)):
        job.output_chemchk_file = os.path.join(model_path, 'pdrgrid', chemchk_out_name)
    
    if os.path.exists(os.path.join(model_path, 'pdrgrid', mrt_out_name)):
        job.output_mcdrt_zip_file = os.path.join(model_path, 'pdrgrid', mrt_out_name)
    
    if os.path.exists(os.path.join(model_path, 'pdrgrid', pdrnew_inp_file_name)):
        job.input_pdrnew_inp_file = os.path.join(model_path, 'pdrgrid', pdrnew_inp_file_name)
    
    if os.path.exists(os.path.join(model_path, 'pdrgrid', ctrl_ind_file_name)):
        job.output_ctrl_ind_file = os.path.join(model_path, 'pdrgrid', ctrl_ind_file_name)
    
    session.commit()
    
    # Create HDFFile entry in the database
    from pdr_run.io.file_manager import get_digest
    
    sha_key = get_digest(os.path.join(model_path, 'pdrgrid', hdf_out_name))
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
            modification_time=datetime.datetime.fromtimestamp(
                os.path.getmtime(os.path.join(model_path, 'pdrgrid', hdf_out_name))),
            sha256_sum=sha_key,
            file_size=os.path.getsize(os.path.join(model_path, 'pdrgrid', hdf_out_name)),
            #HDF 5 structure file
            file_name_hdf5_s=hdf5_struct_out_name,
            full_path_hdf5_s=os.path.join(model_path, 'pdrgrid', hdf5_struct_out_name),
            path_hdf5_s=os.path.join(model_path, 'pdrgrid'),
            modification_time_hdf5_s=datetime.datetime.fromtimestamp(
                os.path.getmtime(os.path.join(model_path, 'pdrgrid', hdf5_struct_out_name))),
            sha256_sum_hdf5_s=sha_key,
            file_size_hdf5_s=os.path.getsize(os.path.join(model_path, 'pdrgrid', hdf5_struct_out_name)),
            #hdf5 chemistry file
            file_name_hdf5_c=hdf5_chem_out_name,
            full_path_hdf5_c=os.path.join(model_path, 'pdrgrid', hdf5_chem_out_name),
            path_hdf5_c=os.path.join(model_path, 'pdrgrid'),
            modification_time_hdf5_c=datetime.datetime.fromtimestamp(
                os.path.getmtime(os.path.join(model_path, 'pdrgrid', hdf5_chem_out_name))),
            sha256_sum_hdf5_c=sha_key,
            file_size_hdf5_c=os.path.getsize(os.path.join(model_path, 'pdrgrid', hdf5_chem_out_name))

        )
    else:
        logger.info(f'HDF file entry already exists. ID: {instance.id}')