"""Database module for PDR framework."""

import warnings

# New database manager
from .db_manager import DatabaseManager, get_db_manager

# Connection management (deprecated but kept for compatibility)
# Import these with warning suppression to avoid warnings during normal import
with warnings.catch_warnings():
    warnings.simplefilter("ignore", DeprecationWarning)
    from .connection import get_session, get_engine, init_db, get_database_config

# Database models
from .models import (
    ModelNames, User, KOSMAtauExecutable, ChemicalDatabase,
    KOSMAtauParameters, PDRModelJob, HDFFile, JSONTemplate, JSONFile,
    ModelRun
)

# Query utilities
from .queries import (
    get_or_create,
    get_model_name_id,
    get_model_info_from_job_id,
    retrieve_job_parameters,
    update_job_status
)

# JSON handling
from .json_handlers import (
    load_json_template,
    apply_parameters_to_json,
    save_json_config,
    copy_json_file,
    get_json_hash,
    register_json_template,
    register_json_file,
    process_json_template,
    prepare_job_json,
    archive_job_json,
    validate_json,
    get_json_templates,
    get_job_json_files,
    update_job_output_json
)

# Expose the new recommended way
__all__ = [
    # New API
    'DatabaseManager',
    'get_db_manager',
    
    # Legacy API (deprecated)
    'get_session',
    'get_engine', 
    'init_db',
    'get_database_config',
    
    # Models
    'ModelNames',
    'User',
    'KOSMAtauExecutable',
    'ChemicalDatabase',
    'KOSMAtauParameters',
    'PDRModelJob',
    'HDFFile',
    'JSONTemplate',
    'JSONFile',
    'ModelRun',
    
    # Queries
    'get_or_create',
    'get_model_name_id',
    'get_model_info_from_job_id',
    'retrieve_job_parameters',
    'update_job_status',
    
    # JSON handling
    'load_json_template',
    'apply_parameters_to_json',
    'save_json_config',
    'copy_json_file',
    'get_json_hash',
    'register_json_template',
    'register_json_file',
    'process_json_template',
    'prepare_job_json',
    'archive_job_json',
    'validate_json',
    'get_json_templates',
    'get_job_json_files',
    'update_job_output_json',
]