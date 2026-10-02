"""Command-line interface for running PDR models.

This module provides a command-line interface for configuring and executing 
Photo-Dissociation Region (PDR) model calculations. It allows users to run
either single model instances or parameter grid studies with various configuration 
options.

Features:
- Single model execution with specified parameters
- Parameter grid studies across multiple parameter combinations
- Parallel execution capabilities for grid studies
- Configuration via command-line arguments or YAML config files
- Integrated logging system

Usage Examples:
    # Run a single model with default parameters
    python -m pdr_run.cli.runner --single

    # Run with force-onion option
    python -m pdr_run.cli.runner --single --force-onion

    # Run a parameter grid with specific density and radiation field values
    python -m pdr_run.cli.runner --grid --dens 1e2 1e4 --chi 1.0 10.0 100.0

    # Run with a configuration file
    python -m pdr_run.cli.runner --config path/to/config.yaml

    # Run in parallel mode with 4 workers
    python -m pdr_run.cli.runner --grid --parallel --workers 4

Parameters:
    --config: Path to configuration file (YAML format)
    --model-name: Custom name for the model run (default: timestamp-based)
    --single: Run a single model with specified parameters
    --grid: Run a grid of models with parameter combinations
    --parallel: Enable parallel execution for grid runs
    --workers: Number of worker processes for parallel execution
    --cpus: Number of CPUs to utilize
    --metal: Metal abundance values
    --dens: Density values (cm^-3)
    --mass: Mass values
    --chi: UV radiation field strength values (Draine units)
    --col: Column density values (cm^-2)   temporarily REMOVED
    --species: Chemical species to include in the model
    --random: Generate random parameter sets instead of grid
    --force-onion: Force running onion even if PDR model was skipped
    --json-template: Path to a JSON parameter template file to use for this run
    --check: Preflight check of files, storage, database and post-processing;
             concise PASS/WARN/FAIL report, exit code 1 if anything FAILs
    --check-json: Same, as JSON (see pdr_run/cli/preflight.py)

Environment Variables:
    PDR_STORAGE_TYPE: Storage backend type
    PDR_STORAGE_DIR: Directory for model output storage
    PDR_DB_TYPE: Database type for results
    PDR_DB_FILE: Database file location
    PDR_DB_PASSWORD: Database password

Returns:
    For single runs, returns a job ID
    For grid runs, returns a list of job IDs
"""

import os
import sys
import logging
import logging.config  # Add this import
import argparse
import yaml
import traceback  # Add this import
from datetime import datetime

from pdr_run.core.engine import run_model, run_parameter_grid
from pdr_run.models.job_status import ALL_STATUSES, JOB_SUCCESS_STATES
from pdr_run.config.default_config import (
    DEFAULT_PARAMETERS, non_default_parameters,
    DATABASE_CONFIG, STORAGE_CONFIG, PDR_CONFIG, USER_CONFIG,
    UV_CONTINUUM_CONFIG
)
from pdr_run.config.logging_config import LOGGING_CONFIG
from pdr_run.utils.logging import sanitize_yaml_content, sanitize_config

# Configure logging
logging.config.dictConfig(LOGGING_CONFIG)
logger = logging.getLogger('dev')

# Job states selectable with --rerun (besides 'all' and 'failed').
RERUN_STATES = tuple(sorted(set(ALL_STATUSES) | {
    'failed_storage', 'exception', 'exception_runtime', 'exception_setup_outer',
    'ERROR', 'error', 'problem', 'reset_stale'}))

# pdr_run exit codes
EXIT_OK = 0            # every job ended in a success state
EXIT_JOBS_FAILED = 1   # some jobs failed (or have a post-processing error)
EXIT_RUN_ERROR = 2     # run-level error: no jobs ran / the run itself crashed


def _rerun_argument(text):
    """argparse type for --rerun: comma-separated states -> tuple."""
    states = tuple(x.strip() for x in text.split(',') if x.strip())
    valid = ('all', 'failed') + RERUN_STATES
    bad = [x for x in states if x not in valid]
    if not states or bad:
        raise argparse.ArgumentTypeError(
            f"invalid --rerun state(s) {bad or text!r}; choose from: {', '.join(valid)}")
    return states


def finish_run(job_ids):
    """Print the one-line job-state summary of a run and return the exit code."""
    if not job_ids:
        logger.error("No jobs were run")
        return EXIT_RUN_ERROR
    from pdr_run.database.queries import summarize_job_states
    counts, n_postproc = summarize_job_states(job_ids)
    parts = [f"{n} {state}" for state, n in sorted(counts.items(), key=lambda kv: (-kv[1], kv[0]))]
    if not counts.get('failed_storage'):
        parts.append("0 failed_storage")
    if n_postproc:
        parts.append(f"{n_postproc} with post-processing errors")
    line = f"Job states: {', '.join(parts)}"
    print(line)
    logger.info(line)
    n_failed = sum(n for state, n in counts.items() if state not in JOB_SUCCESS_STATES)
    return EXIT_JOBS_FAILED if (n_failed or n_postproc) else EXIT_OK


def parse_arguments():
    """Parse command-line arguments."""
    parser = argparse.ArgumentParser(description='Run PDR model calculations')
    
    # Configuration file
    parser.add_argument('--config', type=str, help='Configuration file path')
    
    # Preflight check: fast, concise, non-destructive verification of files,
    # storage, database and post-processing set-up (see cli/preflight.py).
    parser.add_argument('--check', action='store_true',
                        help='Run the preflight check (config, KOSMA-tau install, templates, '
                             'disk, storage, database, post-processing), print a compact '
                             'PASS/WARN/FAIL report and exit (0 = no FAIL, 1 = FAIL)')
    parser.add_argument('--check-json', '--json', dest='check_json', action='store_true',
                        help='Preflight check with JSON output (implies --check)')
    parser.add_argument('--min-free-gb', type=float, default=20.0,
                        help='Preflight: WARN if a filesystem has less free space (default 20 GB)')
    parser.add_argument('--check-timeout', type=float, default=5.0,
                        help='Preflight: timeout in seconds for each network check (default 5)')

    # Add dry-run option
    parser.add_argument('--dry-run', action='store_true', 
                        help='Display configuration and exit without running models')
    
                            
    # Model name
    parser.add_argument(
        '--model-name',
        type=str,
        default=f"pdr_model_{datetime.now().strftime('%Y%m%d_%H%M%S')}",
        help='Model name'
    )
    
    # Create a group for mutually exclusive execution modes
    execution_group = parser.add_mutually_exclusive_group()
    execution_group.add_argument('--single', action='store_true', help='Run single model')
    execution_group.add_argument('--grid', action='store_true', help='Run parameter grid')
    
    # Parallelization
    parser.add_argument('--parallel', action='store_true', help='Run in parallel')
    parser.add_argument('--workers', type=int, help='Number of worker processes')
    parser.add_argument('--cpus', type=int, help='Number of CPUs to use')
    
    # Add force-onion option
    parser.add_argument('--force-onion', action='store_true', 
                       help='Force running onion even if PDR model was skipped')

    # Add force-simline option (SIMLINE RT post-processing; also runs on
    # existing models when the PDR step is skipped -> RT-only grid reruns)
    parser.add_argument('--force-simline', action='store_true',
                       help='Run SIMLINE post-processing, even if PDR model was skipped')
    
    # Recompute nodes whose result is already stored (default: skip them)
    parser.add_argument('--rerun', type=_rerun_argument, default=None, metavar='STATE[,STATE...]',
                        help="Recompute existing nodes whose stored result has one of these "
                             "job states instead of skipping them: 'all', 'failed' (every "
                             "state that is not finished/finished_relaxed/flagged/skipped) "
                             "or literal states such as not_converged, aborted, timeout, "
                             "failed_storage. Default: existing nodes are skipped.")

    # Add keep-tmp option
    parser.add_argument('--keep-tmp', action='store_true',
                        help='Do not delete temporary directories after run (for debugging)')
    
    
    # Add other parameters
    parser.add_argument(
        '--metal',
        type=str,
        nargs='+',
        help='Metal abundance values'
    )
    
    parser.add_argument(
        '--dens',
        type=str,
        nargs='+',
        help='Density values'
    )
    
    parser.add_argument(
        '--mass',
        type=str,
        nargs='+',
        help='Mass values'
    )
    
    parser.add_argument(
        '--chi',
        type=str,
        nargs='+',
        help='Radiation field values'
    )
    
#    parser.add_argument(
#        '--col',
#        type=str,
#        nargs='+',
#        help='Column density values'
#    )
    
    parser.add_argument(
        '--species',
        type=str,
        nargs='+',
        help='Species to consider'
    )
    
    parser.add_argument(
        '--random',
        action='store_true',
        help='Generate random parameter sets'
    )
    
    parser.add_argument(
        '--json-template',
        type=str,
        help='Path to a JSON parameter template file to use for this run'
    )

    # Stale-job recovery: jobs left in status 'running' by a driver process
    # that crashed or was killed before pdrexe itself finished/timed out.
    # See pdr_run.database.queries.reset_stale_jobs(). A utility action -
    # runs and exits, does not launch any model.
    parser.add_argument(
        '--reset-stale-jobs', action='store_true',
        help="Mark jobs stuck in status 'running' (time_of_start older than "
             "--stale-after-hours) and never-started 'pending' rows "
             "(time_created older than that) as 'reset_stale' "
             "(active and pending cleared) and exit, without "
             "running any model. Combine with --dry-run to only report "
             "what would be reset."
    )
    parser.add_argument(
        '--stale-after-hours', type=float, default=None,
        help='Age threshold (hours) for --reset-stale-jobs. Default: '
             '1.5x config[pdr][max_walltime_s] if set, else 6 hours. Rows '
             'of a driver killed less than that ago are only reset with a '
             'smaller value (0 = every running/pending row); use it only '
             'when no pdr_run of that database is running.'
    )

    return parser.parse_args()

def load_config(config_file):
    """Load configuration from file."""
    if not config_file or not os.path.exists(config_file):
        logger.warning(f"Config file not found: {config_file}")
        return None
    
    config_content = None  # Initialize to avoid unbound variable
    try:
        with open(config_file, 'r') as f:
            config_content = f.read()
            logger.debug(f"Raw config content:\n{sanitize_yaml_content(config_content)}")
            config = yaml.safe_load(config_content)
        logger.info(f"Loaded configuration from {config_file}")
        # Log top-level structure for debugging
        logger.debug(f"Configuration structure: {list(config.keys()) if config else None}")
        return config
    except yaml.YAMLError as e:
        # Enhanced YAML error reporting
        logger.error(f"YAML parsing error in {config_file}: {e}")
        if hasattr(e, 'problem_mark') and e.problem_mark is not None and config_content is not None:
            mark = e.problem_mark
            # Ensure mark has the required attributes
            if hasattr(mark, 'line') and hasattr(mark, 'column'):
                # Print detailed position information
                logger.error(f"Error position: line {mark.line + 1}, column {mark.column + 1}")
                # Show the problematic line with a marker
                config_lines = config_content.splitlines()
                if 0 <= mark.line < len(config_lines):
                    problem_line = config_lines[mark.line]
                    logger.error(f"Problem line: {problem_line}")
                    logger.error(f"              {' ' * mark.column}^")
        return None
    except Exception as e:
        logger.error(f"Error loading config file: {e}", exc_info=True)
        return None

# Define aliases for backward compatibility
SECTION_NAME_ALIASES = {
    'model_params': 'model_parameters',
    'non_default_params': 'non_default_parameters'
}

# Define a comprehensive valid configuration structure for validation
VALID_CONFIG_STRUCTURE = {
    'database': set(DATABASE_CONFIG.keys()),
    'storage': set(STORAGE_CONFIG.keys()),
    'pdr': set(PDR_CONFIG.keys()),
    'user': set(USER_CONFIG.keys()),
    'model_parameters': set(DEFAULT_PARAMETERS.keys()), # Canonical name
    'non_default_parameters': set(non_default_parameters.keys()), # Canonical name
    'directories': {'pdr_out_dirs', 'pdr_inp_dirs'}, # Assuming 'directories' is a valid top-level section
    # SIMLINE RT post-processing (models/kosma_tau.py:run_simline). This was
    # missing from VALID_CONFIG_STRUCTURE since it was introduced (4246aca),
    # so a config.yaml with a top-level 'simline:' section would previously
    # abort validation - added here together with 'uv_continuum' below.
    'simline': {'enabled', 'species', 'simline_dir', 'config_file', 'timeout'},
    'uv_continuum': set(UV_CONTINUUM_CONFIG.keys()),
}

def validate_config(config_to_validate):
    """Validate the loaded configuration dictionary against a predefined structure.

    Behavior:
    - Unknown top-level sections abort the run (these are almost always typos
      and the framework cannot route them anywhere).
    - Unknown parameter keys inside a known section emit a warning but do NOT
      abort. The default_config.py dicts intentionally don't list every key
      the framework consumes (e.g. storage.remote_path_prefix is read in
      storage/base.py and remote.py); rejecting legitimate keys broke valid
      configs (issue #17). A warning still surfaces real typos in the logs.

    Handles section name aliases (e.g. ``non_default_params`` →
    ``non_default_parameters``) for backward compatibility.
    """
    logger.info("Starting configuration validation...")

    unknown_param_count = 0
    for section_name in config_to_validate.keys():
        canonical_section_name = SECTION_NAME_ALIASES.get(section_name, section_name)

        if canonical_section_name not in VALID_CONFIG_STRUCTURE:
            logger.critical(f"Unknown top-level section '{section_name}' found in configuration. Aborting.")
            sys.exit(1)

        # Validate parameters within each known section (using canonical name for structure lookup)
        if isinstance(config_to_validate[section_name], dict):
            for param_name in config_to_validate[section_name].keys():
                if param_name not in VALID_CONFIG_STRUCTURE[canonical_section_name]:
                    logger.warning(
                        "Unknown parameter '%s' in section '%s' — not listed in defaults. "
                        "Check for typos; if this key is consumed elsewhere in the framework, "
                        "it will still be passed through.",
                        param_name, section_name,
                    )
                    unknown_param_count += 1
        elif isinstance(config_to_validate[section_name], list):
            # For list-based sections (like directories.pdr_out_dirs), no further key validation
            pass
        else:
            # Handle other types if necessary, or just skip if they are not expected to contain parameters
            pass

    if unknown_param_count:
        logger.info("Configuration validated with %d unknown parameter(s) (warnings above).", unknown_param_count)
    else:
        logger.info("Configuration validated successfully.")

def print_configuration(params, model_name, config, parallel=False, n_workers=None):
    """Print the full configuration that would be used for a run.
    
    Args:
        params (dict): Parameter configuration
        model_name (str): Model name
        config (dict): Framework configuration
        parallel (bool): Whether parallel execution is enabled
        n_workers (int): Number of worker processes
    """
    print("\n=== PDR RUN CONFIGURATION ===\n")
    
    # Print general settings
    print(f"Model name: {model_name}")
    print(f"Execution mode: {'Parallel' if parallel else 'Sequential'}")
    if parallel and n_workers:
        print(f"Worker processes: {n_workers}")
    
    # Print environment settings
    print("\n--- Environment Variables ---")
    env_vars = {
        'PDR_STORAGE_TYPE': os.environ.get('PDR_STORAGE_TYPE', 'Not set'),
        'PDR_STORAGE_DIR': os.environ.get('PDR_STORAGE_DIR', 'Not set'),
        'PDR_DB_TYPE': os.environ.get('PDR_DB_TYPE', 'Not set'),
        'PDR_DB_FILE': os.environ.get('PDR_DB_FILE', 'Not set'),
        'PDR_EXEC_PATH': os.environ.get('PDR_EXEC_PATH', 'Not set')
    }
    for key, value in env_vars.items():
        print(f"{key}: {value}")
    
    # Print model parameters
    print("\n--- Model Parameters ---")
    for key, value in sorted(params.items()):
        print(f"{key}: {value}")
    
    # Print configuration details if available
    if config:
        print("\n--- Additional Configuration ---")
        # Sanitize config before printing to prevent password leaks
        sanitized_config = sanitize_config(config) if isinstance(config, dict) else config
        for section, settings in sorted(sanitized_config.items()):
            print(f"\n{section}:")
            if isinstance(settings, dict):
                for key, value in sorted(settings.items()):
                    print(f"  {key}: {value}")
            else:
                print(f"  {settings}")
    
    print("\n=== END CONFIGURATION ===\n")

def main():
    """Main entry point for the PDR run CLI."""
    # Preflight check: parse first and dispatch before anything is logged,
    # so that the report is not mixed with start-up log lines.
    args = parse_arguments()
    if args.check or args.check_json:
        from pdr_run.cli.preflight import run_preflight
        sys.exit(run_preflight(
            config_path=args.config, json_output=args.check_json,
            min_free_gb=args.min_free_gb, timeout=args.check_timeout,
            json_template=args.json_template, workers=args.workers, cpus=args.cpus,
            force_simline=args.force_simline, species=args.species,
            rerun=args.rerun))

    start_time = datetime.now()
    logger.info(f"========== PDR RUN STARTED AT {start_time.strftime('%Y-%m-%d %H:%M:%S')} ==========")
    logger.info(f"Python version: {sys.version}")
    logger.info(f"Working directory: {os.getcwd()}")
    
    logger.info(f"Command-line arguments: {vars(args)}")
    
    # Load config if provided
    config = None
    if args.config:
        logger.info(f"Loading configuration from: {os.path.abspath(args.config)}")
        config = load_config(args.config)
        if not config:
            logger.error(f"Failed to load configuration from {args.config}. Using defaults.")
        else:
            logger.info(f"Config loaded successfully with {len(config)} top-level sections")
    else:
        logger.info("No configuration file specified, using defaults and command-line arguments")
    
    # Always initialize config structure if it doesn't exist
    if config is None:
        config = {}
    
    # Ensure database section exists
    if 'database' not in config:
        config['database'] = {}
    
    # Always check for environment variables and update config
    db_password_env = os.environ.get('PDR_DB_PASSWORD')
    if db_password_env:
        logger.info("Found PDR_DB_PASSWORD in environment, using it for database configuration")
        config['database']['password'] = db_password_env

    # Validate the loaded configuration thoroughly
    if config:
        validate_config(config) # This will abort if any unknown params are found

    # ===== MODEL NAME PRECEDENCE LOGIC =====
    # Priority: 1. Command line, 2. Config file, 3. Default
    model_name = None
    model_name_source = "default"
    
    # 1. Check command line first (highest priority)
    if hasattr(args, 'model_name') and args.model_name and args.model_name != f"pdr_model_{start_time.strftime('%Y%m%d_%H%M%S')}":
        # User explicitly provided a model name via command line
        model_name = args.model_name
        model_name_source = "command-line"
    
    # 2. Check config file (medium priority)
    elif config and 'pdr' in config and 'model_name' in config['pdr'] and config['pdr']['model_name']:
        model_name = config['pdr']['model_name']
        model_name_source = "config-file"
    
    # 3. Use default (lowest priority)
    else:
        model_name = f"pdr_model_{start_time.strftime('%Y%m%d_%H%M%S')}"
        model_name_source = "default"
    
    # Update args.model_name with the determined model name
    args.model_name = model_name
    logger.info(f"Model name: '{model_name}' (source: {model_name_source})")
    
    # Prepare parameters: start with defaults, then apply config file overrides
    # All parameters in config are guaranteed to be valid due to validate_config call
    params = DEFAULT_PARAMETERS.copy()
    params.update(non_default_parameters) # Add all known non_default_parameters

    logger.debug(f"Combined default and non-default parameters for initial params: {params}")

    # Track parameter sources for debugging
    param_sources = {key: "default" for key in params.keys()}

    # Apply config file parameters for model_params/model_parameters
    model_params_data = {}
    if config and 'model_params' in config: # Alias
        model_params_data.update(config['model_params'])
    if config and 'model_parameters' in config: # Canonical (takes precedence)
        model_params_data.update(config['model_parameters'])
    
    if model_params_data:
        for key, value in model_params_data.items():
            params[key] = value
            param_sources[key] = "config-file"
        logger.info(f"Applied {len(model_params_data)} parameters from 'model_params'/'model_parameters' section of config file.")

    # Apply config file parameters for non_default_params/non_default_parameters
    non_default_params_data = {}
    if config and 'non_default_params' in config: # Alias
        non_default_params_data.update(config['non_default_params'])
    if config and 'non_default_parameters' in config: # Canonical (takes precedence)
        non_default_params_data.update(config['non_default_parameters'])
    
    if non_default_params_data:
        for key, value in non_default_params_data.items():
            params[key] = value
            param_sources[key] = "config-file"
        logger.info(f"Applied {len(non_default_params_data)} parameters from 'non_default_params'/'non_default_parameters' section of config file.")
    
    # Override with command-line arguments (highest priority)
    for param in ['metal', 'dens', 'mass', 'chi', 'species']:
        if hasattr(args, param) and getattr(args, param) is not None:
            value = getattr(args, param)
            logger.info(f"Overriding parameter '{param}' with CLI value: {value}")
            params[param] = value
            param_sources[param] = "command-line"
    
    # Log final parameter configuration with sources
    logger.info("Final parameter configuration:")
    for key, value in params.items():
        if isinstance(value, list):
            logger.info(f"  {key}: {value} (source: {param_sources.get(key, 'unknown')}, count: {len(value)})")
        else:
            logger.info(f"  {key}: {value} (source: {param_sources.get(key, 'unknown')})")
    
    # Log execution environment
    logger.info(f"Execution environment:")
    logger.info(f"  Model name: {args.model_name}")
    logger.info(f"  Parallel execution: {'enabled' if args.parallel else 'disabled'}")
    if args.parallel:
        logger.info(f"  Worker count: {args.workers or 'auto'}")
    
    # Check system resources
    try:
        import psutil
        memory = psutil.virtual_memory()
        disk = psutil.disk_usage(os.getcwd())
        logger.info(f"System resources:")
        logger.info(f"  CPU cores: {psutil.cpu_count(logical=False)} physical, {psutil.cpu_count()} logical")
        logger.info(f"  Memory: {memory.total / (1024**3):.1f} GB total, {memory.available / (1024**3):.1f} GB available")
        logger.info(f"  Disk: {disk.total / (1024**3):.1f} GB total, {disk.free / (1024**3):.1f} GB free")
    except ImportError:
        logger.debug("psutil not available, skipping system resource information")
    except Exception as e:
        logger.warning(f"Failed to get system resource information: {e}")
    
    # Check if required executables and paths exist
    if config and 'pdr' in config:
        pdr_config = config['pdr']
        base_dir = pdr_config.get('base_dir', None)
        pdr_file = pdr_config.get('pdr_file_name', None)
        
        if base_dir and pdr_file:
            full_path = os.path.join(base_dir, pdr_file)
            logger.info(f"PDR executable configuration:")
            logger.info(f"  Base directory: {base_dir} (exists: {os.path.exists(base_dir)})")
            logger.info(f"  Executable file: {pdr_file}")
            logger.info(f"  Full path: {full_path} (exists: {os.path.exists(full_path)})")
            
            if not os.path.exists(full_path):
                logger.error(f"PDR executable not found at {full_path}")
                if os.path.exists(base_dir):
                    logger.info(f"Directory content of {base_dir}: {os.listdir(base_dir)}")
    
    # Stale-job recovery utility action: runs and exits, no model execution.
    if getattr(args, 'reset_stale_jobs', False):
        from pdr_run.database.queries import reset_stale_jobs, DEFAULT_STALE_AFTER_S
        from pdr_run.database.db_manager import get_db_manager
        # This can be the first thing run against a database (e.g. right
        # after deploying to a new host) - ensure the schema exists (and
        # any additive columns are patched in) before querying it, exactly
        # like a normal grid run's create_database_entries() would. Safe
        # to call repeatedly (see DatabaseManager.create_tables()).
        get_db_manager(config.get('database') if config else None).create_tables()
        if args.stale_after_hours is not None:
            stale_after_s = args.stale_after_hours * 3600
        else:
            max_walltime_s = (config.get('pdr') or {}).get('max_walltime_s') if config else None
            stale_after_s = max_walltime_s * 1.5 if max_walltime_s else DEFAULT_STALE_AFTER_S
        reset_job_ids = reset_stale_jobs(
            stale_after_s=stale_after_s,
            dry_run=bool(getattr(args, 'dry_run', False)),
        )
        if reset_job_ids:
            logger.info(f"Stale-job reset: {len(reset_job_ids)} job(s) -> {reset_job_ids}")
        else:
            logger.info("Stale-job reset: no stale jobs found")
        return

    # Check for dry run mode
    if hasattr(args, 'dry_run') and args.dry_run:
        print_configuration(
            params=params,
            model_name=args.model_name,
            config=config,
            parallel=args.parallel,
            n_workers=args.workers
        )
        logger.info("Dry run completed, exiting without running models")
        return
    
    # Execute models
    exit_code = EXIT_RUN_ERROR   # stays 2 if the run itself raises
    try:
        if args.single:
            logger.info(f"Executing single model: {model_name}")
            logger.debug(f"Model parameters: {params}")
            
            # Build configuration if none provided
            if config is None or 'pdr' not in config:
                from pdr_run.core.engine import _build_default_config
                config = _build_default_config(params)
                logger.info("Built default configuration with environment overrides")
            
            job_id = run_model(
                params=params,
                model_name=model_name,
                config=config,
                force_onion=args.force_onion,
                force_simline=args.force_simline,
                json_template=args.json_template,
                keep_tmp=args.keep_tmp,
                rerun=args.rerun
            )
            logger.info(f"Single model execution completed. Job ID: {job_id}")
            exit_code = finish_run([job_id] if job_id is not None else [])
        else:
            # Grid execution
            if config is None or 'pdr' not in config:
                from pdr_run.core.engine import _build_default_config
                config = _build_default_config(params)
                logger.info("Built default configuration with environment overrides")
                
            job_ids = run_parameter_grid(
                params=params,
                model_name=model_name,
                config=config,
                parallel=args.parallel,
                n_workers=args.workers,
                force_onion=args.force_onion,
                force_simline=args.force_simline,
                json_template=args.json_template,
                keep_tmp=args.keep_tmp,
                rerun=args.rerun
            )
            logger.info(f"Parameter grid execution completed. Job IDs: {job_ids}")
            exit_code = finish_run(job_ids)
            
    except Exception as e:
        logger.error(f"Error running model: {e}")
        logger.error(f"Error details: {type(e).__name__}")
        logger.error(f"Traceback: {traceback.format_exc()}")
        
        # Log additional context that might help debugging
        if 'config' in locals() and config:
            logger.debug("Last known configuration state:")
            for section in config:
                if isinstance(config[section], dict):
                    for key, value in config[section].items():
                        logger.debug(f"  {section}.{key}: {value}")
    finally:
        end_time = datetime.now()
        total_run_time = (end_time - start_time).total_seconds()
        logger.info(f"========== PDR RUN COMPLETED AT {end_time.strftime('%Y-%m-%d %H:%M:%S')} ==========")
        logger.info(f"Total execution time: {total_run_time:.2f} seconds")

    if exit_code != EXIT_OK:
        sys.exit(exit_code)

if __name__ == '__main__':
    main()