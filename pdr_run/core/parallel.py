"""Parallel execution utilities for PDR (Photo-Dissociation Region) models.

This module provides flexible parallel execution capabilities for the PDR modeling framework,
supporting multiple backend solutions for distributing computational workloads across
available CPU resources.

Key Functionality:
-----------------
1. Backend Selection and Configuration:
   - Support for multiple parallel processing backends (joblib, Dask)
   - Automatic worker configuration based on available system resources
   - CPU reservation to ensure system responsiveness during intensive calculations

2. Execution Management:
   - Parallel execution of arbitrary functions across collections of input items
   - Transparent handling of job distribution and result collection
   - Resource allocation optimization based on hardware capabilities

Parallel Backends:
----------------
- joblib: Simple, process-based parallelism for local execution (default)
  * Low overhead, good for CPU-bound tasks
  * Integrated with scikit-learn and other scientific libraries
  
- Dask: Scalable distributed computing framework
  * Includes monitoring dashboard for task visualization
  * More flexible scheduling and greater scalability
  * Better for complex task graphs and memory-intensive operations

Technical Implementation:
----------------------
The module intelligently manages system resources by:
- Calculating optimal worker counts based on available CPU cores
- Reserving CPUs to prevent system overload during computation
- Handling backend-specific configuration and initialization
- Ensuring proper cleanup of resources after task completion

Usage Examples:
-------------
# Basic parallel execution using default backend (joblib)
results = run_parallel(process_function, items_to_process)

# Using Dask backend with 4 worker processes
results = run_parallel(process_function, items_to_process, 
                       backend='dask', n_workers=4)

# Reserve 2 CPU cores for system processes
results = run_parallel(process_function, items_to_process, 
                       reserved_cpus=2)

Integration:
----------
This module is typically used by the PDR model engine to:
- Execute parameter grid studies with multiple model configurations
- Process large datasets in parallel
- Distribute independent calculations across available computing resources

The parallel execution utilities are designed to be generic enough to support
various workloads within the PDR framework while providing consistent interfaces
regardless of the underlying parallel backend.

Dependencies:
-----------
- joblib: For process-based parallelism
- dask.distributed: For scalable distributed computing
"""

import os
import logging
import multiprocessing
from joblib import Parallel, delayed
from dask.distributed import Client, LocalCluster

logger = logging.getLogger('dev')

def get_parallel_backend(backend='joblib', n_workers=None, reserved_cpus=0):
    """Get parallel execution backend.
    
    Args:
        backend (str, optional): Backend to use ('joblib' or 'dask'). Defaults to 'joblib'.
        n_workers (int, optional): Number of worker processes. Defaults to None.
        reserved_cpus (int, optional): Number of CPUs to reserve. Defaults to 0.
        
    Returns:
        object: Parallel execution backend
    """
    # Determine number of workers
    if n_workers is None:
        n_workers = multiprocessing.cpu_count() - reserved_cpus
        n_workers = max(1, n_workers)
    
    logger.info(f"Setting up parallel backend '{backend}' with {n_workers} workers")
    
    if backend == 'dask':
        # Set up Dask client
        cluster = LocalCluster(n_workers=n_workers, threads_per_worker=1)
        client = Client(cluster)
        logger.info(f"Dask dashboard at {client.dashboard_link}")
        return client
    else:
        # Default to joblib
        return None

def run_parallel(func, items, backend='joblib', n_workers=None, reserved_cpus=0):
    """Run function in parallel over items.
    
    Args:
        func (callable): Function to run
        items (list): Items to process
        backend (str, optional): Backend to use ('joblib' or 'dask'). Defaults to 'joblib'.
        n_workers (int, optional): Number of worker processes. Defaults to None.
        reserved_cpus (int, optional): Number of CPUs to reserve. Defaults to 0.
        
    Returns:
        list: Results
    """
    # Determine number of workers
    if n_workers is None:
        n_workers = multiprocessing.cpu_count() - reserved_cpus
        n_workers = max(1, n_workers)
    
    logger.info(f"Running {len(items)} tasks on {n_workers} workers using {backend}")
    
    if backend == 'dask':
        client = get_parallel_backend('dask', n_workers, reserved_cpus)
        futures = client.map(func, items)
        results = client.gather(futures)
        client.close()
        return results
    else:
        # Default to joblib
        return Parallel(n_jobs=n_workers)(
            delayed(func)(item) for item in items
        )