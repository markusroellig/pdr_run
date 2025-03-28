"""Database module for PDR framework."""

# Connection management
from .connection import get_session, get_engine, init_db

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

# JSON handling - add after json_handlers.py is fixed
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

# Migration utilities
from .migration import create_tables

# Legacy compatibility aliases
get_db_connection = get_engine