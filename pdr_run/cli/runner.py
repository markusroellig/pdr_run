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
from datetime import datetime

from pdr_run.core.engine import run_model, run_parameter_grid
from pdr_run.config.default_config import DEFAULT_PARAMETERS
from pdr_run.config.logging_config import LOGGING_CONFIG

# Configure logging
logging.config.dictConfig(LOGGING_CONFIG)
logger = logging.getLogger('dev')

def parse_arguments():
    """Parse command-line arguments."""
    parser = argparse.ArgumentParser(description='Run PDR model calculations')
    
    # Configuration file
    parser.add_argument('--config', type=str, help='Configuration file path')
    
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
    
    return parser.parse_args()

def load_config(config_file):
    """Load configuration from file."""
    if not config_file or not os.path.exists(config_file):
        logger.warning(f"Config file not found: {config_file}")
        return None
    
    try:
        with open(config_file, 'r') as f:
            config_content = f.read()
            logger.debug(f"Raw config content:\n{config_content}")
            config = yaml.safe_load(config_content)
        logger.info(f"Loaded configuration from {config_file}")
        # Log top-level structure for debugging
        logger.debug(f"Configuration structure: {list(config.keys()) if config else None}")
        return config
    except yaml.YAMLError as e:
        # Enhanced YAML error reporting
        logger.error(f"YAML parsing error in {config_file}: {e}")
        if hasattr(e, 'problem_mark'):
            mark = e.problem_mark
            # Print detailed position information
            logger.error(f"Error position: line {mark.line + 1}, column {mark.column + 1}")
            # Show the problematic line with a marker
            if mark.line < len(config_content.splitlines()):
                problem_line = config_content.splitlines()[mark.line]
                logger.error(f"Problem line: {problem_line}")
                logger.error(f"              {' ' * mark.column}^")
        return None
    except Exception as e:
        logger.error(f"Error loading config file: {e}", exc_info=True)
        return None

def configure_from_args(args):
    """Configure environment from command-line arguments."""
    # Set environment variables based on args
    if hasattr(args, 'storage_type') and args.storage_type:
        os.environ['PDR_STORAGE_TYPE'] = args.storage_type
    if hasattr(args, 'storage_dir') and args.storage_dir:
        os.environ['PDR_STORAGE_DIR'] = args.storage_dir
    if hasattr(args, 'db_type') and args.db_type:
        os.environ['PDR_DB_TYPE'] = args.db_type
    if hasattr(args, 'db_file') and args.db_file:
        os.environ['PDR_DB_FILE'] = args.db_file
    if hasattr(args, 'db_password') and args.db_password:
        os.environ['PDR_DB_PASSWORD'] = args.db_password
    
    # Return config dict for compatibility
    return {}

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
        for section, settings in sorted(config.items()):
            print(f"\n{section}:")
            if isinstance(settings, dict):
                for key, value in sorted(settings.items()):
                    print(f"  {key}: {value}")
            else:
                print(f"  {settings}")
    
    print("\n=== END CONFIGURATION ===\n")

def main():
    """Main entry point."""
    start_time = datetime.now()
    logger.info(f"========== PDR RUN STARTED AT {start_time.strftime('%Y-%m-%d %H:%M:%S')} ==========")
    logger.info(f"Python version: {sys.version}")
    logger.info(f"Working directory: {os.getcwd()}")
    
    # Parse arguments
    args = parse_arguments()
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
    
    # Prepare parameters
    params = DEFAULT_PARAMETERS.copy()
    logger.debug(f"Default parameters: {params}")
    
    # Track parameter sources for debugging
    param_sources = {key: "default" for key in params.keys()}
    
    # Override with command-line arguments
    #for param in ['metal', 'dens', 'mass', 'chi', 'col', 'species']:
    for param in ['metal', 'dens', 'mass', 'chi', 'species']:
        if hasattr(args, param) and getattr(args, param) is not None:
            value = getattr(args, param)
            logger.info(f"Overriding parameter '{param}' with CLI value: {value}")
            params[param] = value
            param_sources[param] = "command-line"
    
    # Override with config if present
    if config and 'model_params' in config:
        logger.info("Applying model parameters from configuration file")
        for key, value in config['model_params'].items():
            if key in params:
                logger.info(f"Overriding parameter '{key}' with config value: {value}")
                params[key] = value
                param_sources[key] = "config-file"
            else:
                logger.warning(f"Unknown parameter '{key}' in configuration file")
    
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
    try:
        execution_start = datetime.now()
        logger.info(f"Starting model execution at {execution_start.strftime('%Y-%m-%d %H:%M:%S')}")
        
        # Run model
        if args.single:
            logger.info(f"Executing single model: {args.model_name}")
            logger.debug(f"Model parameters: {params}")
            if args.force_onion:
                logger.info("Force onion mode enabled")
            job_id = run_model(
                params=params, 
                model_name=args.model_name, 
                config=config,
                force_onion=args.force_onion,
                json_template=args.json_template
            )
            logger.info(f"Completed job {job_id}")
        else:
            # Calculate number of parameter combinations
            param_counts = {}
            #for param in ['metal', 'dens', 'mass', 'chi', 'col']:
            for param in ['metal', 'dens', 'mass', 'chi']:
                if param in params and isinstance(params[param], list):
                    param_counts[param] = len(params[param])
            
            if param_counts:
                total_combinations = 1
                for count in param_counts.values():
                    total_combinations *= count
                logger.info(f"Parameter grid will generate {total_combinations} combinations")
                logger.debug(f"Parameter grid dimensions: {param_counts}")
            
            logger.info(f"Executing parameter grid for model: {args.model_name}")
            logger.info(f"Parallel execution: {'enabled' if args.parallel else 'disabled'}")
            if args.force_onion:
                logger.info("Force onion mode enabled")
            
            job_ids = run_parameter_grid(
                params=params,
                model_name=args.model_name,
                config=config,
                parallel=args.parallel,
                n_workers=args.workers,
                force_onion=args.force_onion,
                json_template=args.json_template
            )
            
            execution_time = datetime.now() - execution_start
            logger.info(f"Grid execution completed: {len(job_ids)} jobs in {execution_time.total_seconds():.1f} seconds")
            logger.info(f"Average time per job: {execution_time.total_seconds() / max(len(job_ids), 1):.3f} seconds")
            
    except Exception as e:
        import traceback
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

if __name__ == '__main__':
    main()