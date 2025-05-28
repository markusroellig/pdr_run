"""Default configuration for the PDR framework.

This module defines the default configuration settings for the Photo-Dissociation Region (PDR)
modeling framework. It provides centralized configuration for database connections, file storage,
model parameters, and runtime environments.

Configuration Sections:
----------------------
DATABASE_CONFIG:
    Configuration for database connections, supporting both SQLite and MySQL backends.
    Controls connection parameters, authentication, and connection pool behavior.

STORAGE_CONFIG:
    Defines how and where model inputs/outputs are stored. Supports local filesystem
    storage as well as remote options like rclone, SFTP, and FTP.

PDR_CONFIG:
    Core configuration for the PDR model execution environment, including:
    - Base directory for model execution
    - Executable filenames for various model components
    - Template files and chemical databases
    - Version and build information

USER_CONFIG:
    Information about the user running the models, used for tracking and notification.

DEFAULT_PARAMETERS:
    Default physical parameters for PDR model runs, including:
    - Metallicity, density, and mass values
    - UV field strength and column densities
    - Chemical species to track
    - Cloud structure parameters (alpha, rcore)
    - Chemistry model selection

PDR_INP_DIRS, PDR_OUT_DIRS:
    Standard directory structures for model inputs and outputs

Usage:
-----
This configuration serves as the baseline for all PDR model runs. Values can be overridden by:
1. Command-line arguments (highest priority)
2. Configuration files specified at runtime
3. Environment variables

Example:
-------
    from pdr_run.config.default_config import DEFAULT_PARAMETERS, PDR_CONFIG
    
    # Use default parameters as a starting point
    params = DEFAULT_PARAMETERS.copy()
    
    # Override specific parameters
    params['chi'] = ['1.0', '10.0', '100.0']
    
    # Access PDR configuration
    model_dir = PDR_CONFIG['base_dir']

Notes:
-----
- All paths should be absolute to ensure consistency across different execution environments
- Sensitive information like passwords should be provided via environment variables rather
  than being hardcoded in this file
"""

import logging
import logging.config  # Add this line
import os

# Database configuration
DATABASE_CONFIG = {
    'type': 'sqlite',
    'path': 'kosma_tau.db',
    'host': 'localhost',
    'port': 3306,
    'database': 'kosma_tau',
    'username': 'root',
    'password': None,              # Set via PDR_DB_PASSWORD environment variable if needed
    'pool_recycle': 280,           # Prevent connection timeout
    'pool_pre_ping': True,         # Check connection health
    'connect_args': {},  # Add for additional connection parameters
}

# File storage configuration
STORAGE_CONFIG = {
    'type': 'local',                # 'local', 'rclone', 'sftp', 'ftp'
    'base_dir': '/home/roellig/pdr/pdr/test_run',
    'rclone_remote': 'kosmatau',    # Rclone remote name if using rclone
    'use_mount': False,             # Set to True if you want to use mount functionality 
    'host': None,                   # For SFTP/FTP
    'port': None,                   # For SFTP/FTP
    'username': None,               # For SFTP/FTP
    'password': None,               # For SFTP/FTP
    'use_local_copy': True,         # Keep local copy when using remote storage
}

# PDR model configuration
PDR_CONFIG = {
    'model_name': 'test1',
    'base_dir': '/home/roellig/pdr/pdr/test_run',  # Use a temporary directory for tests
    'pdr_file_name': 'mockpdr',
    'onion_file_name': 'mockonion',
    'getctrlind_file_name': 'mockgetctrlind',
    'mrt_file_name': 'mockmrt',
    'pdrinp_template_file': 'PDRNEW.INP.template',
    'json_template_file': 'pdr_config.json.template',
    'chem_database': 'chem_rates_2022-10-21-ERS.dat',
    'chem_origin': 'UDfA12',
    'exe_revision': 'dev',
    'compilation_date': '2099-01-01',
}
# PDR_CONFIG = {
#     'model_name': 'symlogtanh_stepper_test',
#     'base_dir': '/home/roellig/pdr/pdr/xxpdr.git/kosma-tau',
#     'pdr_file_name': 'pdrexe.symlogtanh_5_2_0.5',
#     'onion_file_name': 'onionexe.g9f4a',
#     'mrt_file_name': 'mrt.exe',
#     'getctrlind_file_name': 'getctrlind',
#     'pdrinp_template_file': 'PDRNEW.INP.template',
#     'chem_database': 'chem_rates_2017-09-28-incl-Cazaux_et_al_2015.dat',
#     'chem_origin': 'UDfA12',
# }

# User configuration
USER_CONFIG = {
    'username': 'Markus Roellig',
    'email': 'm.roellig@physikalischer-verein.de',
}

# Default model parameters
DEFAULT_PARAMETERS = {
    'metal': ["100"],    # Metallicity relative to solar
    'dens': ["30"],     # Log density (cm^-3)
    'mass': ["-10"],     # Log mass (solar masses)
    'chi': ["10"],      # Log UV field strength
    #'col': ["00"],     # Column density (cm^-2)
    'species':  ["CO","C+", "C", "O", "13C+", "13C", "13CO", "OH", "CH+", "HCO+", "H13CO+"],  # Species to compute radiative transfer
    #'alpha': ["1.5"],    # Cloud density profile parameter
    #'rcore': ["0.2"],    # Core radius parametergrep
    'alpha' : 1.5,
    'rcore' : 0.2,
    'default_species' :  "H+ H2+ H3+ HE+ HE C CH C+ CH+ CH2+ CH2 CH3+ CH3 CH4+ CH4 CH5+ "\
                  +" "+" CN CN+ HCN HCN+ CO CO+ HCO+ HCO CO2 CO2+ H2CO H2CO+ 13C 13CH 13CO 13C+ 13CH+ 13CH2+ "\
                  +" "+" 13CO+ H13CO+ N N2 N2+ N2H+ NO NO+ O O+ OH OH+ H2O H2O+ H3O+ O2 O2+ "\
                  +" "+" 18O C18O 13C18O O18O O18O+ 18OH 18OH+ H218O H218O+ H318O+ "\
                  +" "+" SO SO+ SO2 SO2+ HSO2+ S S+ "\
                  +" "+" SI SI+ SIH SIH+ SIH2+ SIO SIO+ SIOH+ CH3OH+ CH3O CH3OH H2O2 "\
                  +" "+" JH JC JCH JCH2 JCH3 JCH4 JCN JHCN JCO JHCO JCO2 JH2CO JCH3O JCH3OH "\
                  +" "+" JN JN2 JNO JO JOH JH2O JH2O2 JO2 JSO JSO2 JS",
    'chemistry': "HE+ HE H+ H2+ H3+ C CH C+ CH+ CH2+ CH2 CH3+ CH3 " \
                +" "+" CH4+ CH4 CH5+ CN CN+ HCN HCN+ CO CO+ HCO+ HCO CO2 CO2+" \
                +" "+" H2CO H2CO+ "\
                +" "+" 13C 13CH 13CO 13C+ 13CH+ " \
                +" "+" 13CH2+ 13CO+ H13CO+ N N2 N2+ N2H+ NO NO+ O " \
                +" "+" O+ OH OH+ H2O H2O+ H3O+ "\
                +" "+" O2 O2+ 18O "\
                +" "+" C18O 13C18O O18O O18O+ 18OH 18OH+ H218O H218O+ H318O+ SO "\
                +" "+" SO+ SO2 SO2+ HSO2+ S S+ SI SI+ SIH SIH+ "\
                +" "+" SIH2+ SIO SIO+ SIOH+ "\
                +" "+" CH3OH+ CH3O CH3OH H2O2 JH JC JCH JCH2 JCH3 JCH4 "\
                +" "+" JCN JHCN JCO JHCO JCO2 JH2CO JCH3O JCH3OH JN JN2 "\
                +" "+" JNO JO JOH JH2O JH2O2 JO2 JSO JSO2 JS "
                }

non_default_parameters={"ihtclgas":1, "tgasc":50.,
                        "ihtcldust":1, "tdustc":20,
                        "idustmet":1,
                        "ih2meth":0, "cosray":1.0e-16,
                        "beta":1.0000e5,
                        "ifuvtype":2, 
                        "ifuvmeth":1, "inewgam":0,
                        "ipehmeth":1, "inds":4,
                        "indstr":1, "ih2shld":0,
                        "indxpeh":4, "indx":7,
                        "ih2onpah":0, "step1":100,
                        "step2":200, "nconv_time":0,
                        "temp_start":0.0,
                        "atol_chem":1.0e-10,
                        "h2_structure":0,
                        "itmeth":2, 
                        "rtol_iter":3.0e-2}

# Input and output directories

PDR_OUT_DIRS=['pdroutput','onionoutput','Out']
PDR_INP_DIRS=['pdrinpdata','onioninpdata','In','templates']

def get_database_config():
    """Get database configuration with environment variable overrides."""
    config = DATABASE_CONFIG.copy()
    
    # Override with environment variables if they exist
    db_password = os.environ.get('PDR_DB_PASSWORD')
    if db_password:
        config['password'] = db_password
    
    return config