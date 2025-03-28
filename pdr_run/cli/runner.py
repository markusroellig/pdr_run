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
    --col: Column density values (cm^-2)
    --species: Chemical species to include in the model
    --random: Generate random parameter sets instead of grid

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
    
    parser.add_argument(
        '--col',
        type=str,
        nargs='+',
        help='Column density values'
    )
    
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
    
    return parser.parse_args()

def load_config(config_file):
    """Load configuration from file."""
    if not config_file or not os.path.exists(config_file):
        logger.warning(f"Config file not found: {config_file}")
        return None
    
    try:
        with open(config_file, 'r') as f:
            config = yaml.safe_load(f)
        logger.info(f"Loaded configuration from {config_file}")
        return config
    except Exception as e:
        logger.error(f"Error loading config file: {e}")
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
    args = parse_arguments()
    
    # Load config if provided
    config = None
    if args.config:
        config = load_config(args.config)
    
    # Prepare parameters
    params = DEFAULT_PARAMETERS.copy()
    
    # Override with command-line arguments
    for param in ['metal', 'dens', 'mass', 'chi', 'col', 'species']:
        if hasattr(args, param) and getattr(args, param) is not None:
            params[param] = getattr(args, param)
    
    # Override with config if present
    if config and 'model_params' in config:
        for key, value in config['model_params'].items():
            params[key] = value
    
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
    
    try:
        # Run model
        if args.single:
            logger.info(f"Running single model: {args.model_name}")
            job_id = run_model(params=params, model_name=args.model_name, config=config)
            logger.info(f"Completed job {job_id}")
        else:
            logger.info(f"Running parameter grid: {args.model_name}")
            job_ids = run_parameter_grid(
                params=params,
                model_name=args.model_name,
                config=config,
                parallel=args.parallel,
                n_workers=args.workers
            )
            logger.info(f"Completed {len(job_ids)} jobs")
    except Exception as e:
        logger.error(f"Error running model: {e}")

if __name__ == '__main__':
    main()