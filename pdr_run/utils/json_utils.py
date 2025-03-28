"""JSON utility functions for PDR framework (DEPRECATED).

This module is provided for backward compatibility.
All functionality has been moved to pdr_run.database.json_handlers.
"""

import warnings

warnings.warn(
    "pdr_run.utils.json_utils is deprecated. "
    "Use pdr_run.database.json_handlers instead.",
    DeprecationWarning,
    stacklevel=2
)

# Simply import everything from json_handlers
from pdr_run.database.json_handlers import (
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

# Export all these functions
__all__ = [
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
    'update_job_output_json'
]