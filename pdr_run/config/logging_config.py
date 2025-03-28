"""Logging configuration for the PDR (Photo-Dissociation Region) framework.

This module configures the logging system used throughout the PDR modeling framework.
It sets up standardized logging formats, output destinations, and verbosity levels
to provide consistent diagnostic information during model execution.

Configuration Components:
------------------------
- Formatters: Define how log messages are structured and displayed
  - standard: Basic format for console output
  - detailed: Extended format with file and line information for debugging

- Handlers: Define where log messages are sent
  - console: Prints INFO and higher messages to stdout with standard formatting
  - file: Writes DEBUG and higher messages to log file with detailed formatting

- Loggers: Named loggers for different parts of the application
  - dev: Development logger with both console and file output
  - production: Production logger with file output only

Environment Variables:
--------------------
PDR_LOG_DIR: Override the default log directory (default: "logs")

Usage:
-----
To use this logging configuration in other modules:

    import logging
    from pdr_run.config.logging_config import LOGGING_CONFIG
    import logging.config
    
    # Configure logging
    logging.config.dictConfig(LOGGING_CONFIG)
    
    # Get a named logger
    logger = logging.getLogger('dev')  # For development
    # OR
    logger = logging.getLogger('production')  # For production
    
    # Use the logger
    logger.debug("Detailed information for debugging")
    logger.info("General information about program execution")
    logger.warning("Warning about potential issues")
    logger.error("Error that doesn't prevent execution")
    logger.critical("Critical error that may prevent execution")

Note:
----
Log files are automatically created in the LOG_DIR directory. Each log entry includes
a timestamp, log level, logger name, and message. The detailed formatter also includes
the source file and line number to aid in debugging.
"""

import os

# Define log directory
LOG_DIR = os.environ.get("PDR_LOG_DIR", "logs")
os.makedirs(LOG_DIR, exist_ok=True)

LOGGING_CONFIG = {
    'version': 1,
    'disable_existing_loggers': False,
    'formatters': {
        'standard': {
            'format': '%(asctime)s [%(levelname)s] %(name)s: %(message)s'
        },
        'detailed': {
            'format': '%(asctime)s [%(levelname)s] %(name)s (%(filename)s:%(lineno)d): %(message)s'
        },
    },
    'handlers': {
        'console': {
            'class': 'logging.StreamHandler',
            'level': 'INFO',
            'formatter': 'standard',
            'stream': 'ext://sys.stdout',
        },
        'file': {
            'class': 'logging.FileHandler',
            'level': 'DEBUG',
            'formatter': 'detailed',
            'filename': os.path.join(LOG_DIR, 'pdr_run.log'),
            'mode': 'a',
        },
    },
    'loggers': {
        'dev': {
            'handlers': ['console', 'file'],
            'level': 'DEBUG',
            'propagate': True
        },
        'production': {
            'handlers': ['file'],
            'level': 'INFO',
            'propagate': True
        },
    }
}