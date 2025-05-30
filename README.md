# PDR Framework Installation and Testing Guide

This document provides instructions for installing, configuring, and testing the PDR (Photo-Dissociation Region) framework.

## Table of Contents
- [Installation](#installation)
- [Configuration](#configuration)
- [Running a Test Model](#running-a-test-model)
- [Running with JSON Only](#running-with-json-only)
- [Resource Management](#resource-management)
- [Comparing with Example Script](#comparing-with-example-script)
- [Advanced Usage](#advanced-usage)
- [Troubleshooting](#troubleshooting)

## Installation

Install the PDR framework in development mode:

```bash
# Navigate to the package directory
cd pdr_run/

# Install in development mode
pip install -e .
```

This makes the `pdr_run` command available in your path while allowing you to modify the code.

## Configuration

Set up the required environment variables:

```bash
# Database configuration
export PDR_DB_TYPE=sqlite  # Options: sqlite, postgresql, mysql
export PDR_DB_FILE=/path/to/kosma_tau.db  # Only for sqlite
export PDR_DB_PASSWORD=your_db_password   # Set your database password here (required for MySQL/PostgreSQL)
```

> **Note:**  
> The PDR framework will automatically read the database password from the `PDR_DB_PASSWORD` environment variable if it is set.  
> This value will override any password specified in configuration files.  
> If `PDR_DB_PASSWORD` is not set, the password in the config will be used (which defaults to `None`).  
> If neither is set, database authentication will fail for password-protected databases.

# Storage configuration
export PDR_STORAGE_TYPE=local  # Options: local, s3
export PDR_STORAGE_DIR=/path/to/storage/directory  # Only for local storage

# Optional: Executable paths
export PDR_EXEC_PATH=/path/to/pdr/executables
```

## Running a Test Model

Run a simple PDR model:

```bash
# Basic single-point model
pdr_run --model-name test_model --single --dens 3.0 --chi 1.0

```

## Running with JSON Only

You can run the PDR model using only a JSON parameter file. The PDRNEW.INP template is optional. If it is missing, the workflow will proceed as long as a valid JSON template is available.

```bash
# Run with JSON only (no PDRNEW.INP.template required)
pdr_run --model-name test_model --json-template my_config.json.template --dens 3.0 --chi 1.0
```

If `PDRNEW.INP.template` is not found, a warning will be logged, but the model will run using the JSON configuration.

## Resource Management

The PDR framework manages resources from several locations:

### 1. Package Data Directory
The framework includes essential files within the installed package:
- Templates: `pdr_run/templates/`
- Reference data: `pdr_run/reference_data/`

### 2. Environment-Configured Locations
Resources are accessed via environment variables:
- `PDR_STORAGE_DIR`: Storage for model outputs
- `PDR_EXEC_PATH`: Location of executable binaries

### 3. Temporary Working Directory
For each model run:
1. A temporary directory is created
2. Required files are copied from package data
3. Configuration files are generated from templates
4. The model is executed
5. Results are stored in the database and storage directory
6. Temporary files are cleaned up (unless `--keep-tmp` is specified)

## Comparing with Example Script

The `example.py` script offers a simplified approach:
- Sets explicit paths to executables
- Creates temporary directories
- Runs the PDR model directly
- Copies input/output files manually

The `pdr_run` framework provides these advantages:
- Standardized command-line interface
- Parameter management through database
- Automatic file handling and cleanup
- Support for parameter grids
- Parallel execution capabilities
- Consistent logging and error handling

## Advanced Usage

### Parameter Grid Studies
```bash
# Run a grid of models with different density and radiation field values
pdr_run --model-name grid_test --dens 1.0 2.0 3.0 --chi 1.0 10.0 100.0
```

### Parallel Execution
```bash
# Run models in parallel
pdr_run --model-name parallel_test --parallel --workers 4 --dens 1.0 2.0 --chi 1.0 2.0
```

### Configuration File
Create a YAML configuration file (e.g., `config.yaml`):
```yaml
database:
  type: sqlite
  file: kosma_tau.db
storage:
  type: local
  path: /tmp/pdr_storage
model_params:
  metal: ["1.0"]
  dens: ["1.0", "2.0"]
  chi: ["1.0", "10.0"]
```

Run with the configuration:
```bash
pdr_run --config config.yaml
```

### Custom Templates
```bash
# Use a custom input template
pdr_run --model-name custom_model --template my_template.inp --dens 3.0 --chi 1.0
```

## Troubleshooting

### Running Tests
Verify the framework is working correctly:

```bash
# Run the full test suite
cd /home/roellig/pdr/pdr/pdr_run/
python -m pytest pdr_run/tests/

# Run a specific test
python -m pytest pdr_run/tests/test_integration.py
```

### Common Issues
1. **Missing Environment Variables**: Ensure all required environment variables are set
2. **Database Connection Errors**: Verify database configuration
3. **File Permission Issues**: Check storage directory permissions
4. **Missing Templates**: Confirm template files exist in the package data

### Database Password Issues

- If you see errors like `Access denied for user ... using password: YES` or the password in the connection string appears as `None`, it means the password was not set correctly.
- Always set your database password using the `PDR_DB_PASSWORD` environment variable:
  ```bash
  export PDR_DB_PASSWORD=your_db_password
  ```
- The framework will automatically use this value and not the value in the config file.
- For security, avoid hardcoding passwords in configuration files.

### Checking Logs
```bash
# View the last run log
cat $(pdr_run --print-last-log)
```
## Development

### Setting Up the Development Environment

1. Clone the repository:
   ```bash
   git clone <your-repo>
   cd pdr_run```

2. Set up the sandbox environment:
   ```bash
    make setup-sandbox
    make start-services
```

3. Run tests:
   ```bash
   make test-all
```

See SANDBOX_README.md for detailed development instructions.
---

For more details, consult the full documentation or reach out to the development team.