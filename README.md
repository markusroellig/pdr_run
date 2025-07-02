# PDR Framework Installation and Testing Guide

This document provides instructions for installing, configuring, and testing the PDR (Photo-Dissociation Region) framework.

## Table of Contents
- [Installation](#installation)
- [Configuration](#configuration)
  - [Configuration Precedence](#configuration-precedence)
  - [Database Configuration](#database-configuration)
  - [Storage Configuration](#storage-configuration)
- [MySQL Setup](#mysql-setup)
- [SFTP Storage Setup](#sftp-storage-setup)
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

## Installation Validation

After installation, verify everything is working:

```bash
# Check if pdr_run command is available
which pdr_run

# Test basic functionality
pdr_run --help

# Verify package installation
python -c "import pdr_run; print(f'PDR Framework version: {pdr_run.__version__ if hasattr(pdr_run, \"__version__\") else \"installed\"}')"

# Test imports
python -c "
from pdr_run.database.db_manager import get_db_manager
from pdr_run.storage.base import get_storage_backend
print('All core modules imported successfully')
"
```

## Quick Start Verification

Test the complete workflow:

```bash
# 1. Install and setup
make dev-install
make setup-sandbox
make start-services

# 2. Run tests
make test-all

# 3. Test a simple model (if PDR executables are available)
pdr_run --model-name test_install --dry-run --single --dens 3.0 --chi 1.0

# 4. Check logs
ls -la logs/
```

## Configuration

The PDR framework supports multiple configuration methods with a clear precedence hierarchy. Configuration can be provided through environment variables, YAML configuration files, or a combination of both.

### Configuration Precedence

The framework follows this precedence order (highest to lowest priority):

1. **Environment Variables** (highest priority)
2. **Configuration File Settings** (YAML file)
3. **Default Configuration** (lowest priority)

This means:
- Environment variables always override config file settings
- Config file settings override default values
- If a setting is not specified in any location, the default value is used

### Database Configuration

#### SQLite (Default)
```bash
# Environment variables
export PDR_DB_TYPE=sqlite
export PDR_DB_FILE=/path/to/kosma_tau.db  # or ":memory:" for in-memory database
```

Or in your `config.yaml`:
```yaml
database:
  type: sqlite
  path: /path/to/kosma_tau.db  # or ":memory:" for in-memory database
```

#### MySQL Configuration
```bash
# Environment variables (recommended for passwords)
export PDR_DB_TYPE=mysql
export PDR_DB_HOST=localhost
export PDR_DB_PORT=3306
export PDR_DB_DATABASE=pdr_test
export PDR_DB_USERNAME=pdr_user
export PDR_DB_PASSWORD=your_secure_password  # Always use environment variable for passwords
```

Or in your `config.yaml`:
```yaml
database:
  type: mysql
  host: localhost
  port: 3306
  database: pdr_test
  username: pdr_user
  password: null  # Leave null - use PDR_DB_PASSWORD environment variable
  pool_recycle: 3600
  pool_pre_ping: true
  connect_args: {}
```

#### PostgreSQL Configuration
```bash
# Environment variables
export PDR_DB_TYPE=postgresql
export PDR_DB_HOST=localhost
export PDR_DB_PORT=5432
export PDR_DB_DATABASE=pdr_test
export PDR_DB_USERNAME=pdr_user
export PDR_DB_PASSWORD=your_secure_password
```

### Storage Configuration

#### Local Storage (Default)
```bash
export PDR_STORAGE_TYPE=local
export PDR_STORAGE_DIR=/path/to/storage/directory
```

Or in `config.yaml`:
```yaml
storage:
  type: local
  base_dir: /path/to/storage/directory
```

#### SFTP Storage
```bash
# Environment variables
export PDR_STORAGE_TYPE=sftp
export PDR_STORAGE_HOST=your-sftp-server.com
export PDR_STORAGE_USER=your_username
export PDR_STORAGE_PASSWORD=your_password  # Use environment variable for security
export PDR_STORAGE_DIR=/remote/path/to/storage
```

Or in `config.yaml`:
```yaml
storage:
  type: sftp
  host: your-sftp-server.com
  username: your_username
  password: null  # Leave null - use PDR_STORAGE_PASSWORD environment variable
  base_dir: /remote/path/to/storage
```

#### RClone Storage
```bash
export PDR_STORAGE_TYPE=rclone
export PDR_STORAGE_RCLONE_REMOTE=your_remote_name
export PDR_STORAGE_DIR=/path/to/local/mount/point
# Optional: Set a prefix to trim from remote paths to create cleaner directory structures.
export PDR_STORAGE_REMOTE_PATH_PREFIX=/path/to/trim
```

Or in `config.yaml`:
```yaml
storage:
  type: rclone
  rclone_remote: your_remote_name
  base_dir: /path/to/mount/point
  use_mount: false
  # Optional: Specify a path prefix to remove from the remote destination path.
  # This is useful for creating cleaner remote directory structures by removing
  # absolute local path components.
  remote_path_prefix: /path/to/trim/from/remote/destination
```

## MySQL Setup

### Prerequisites

1. **Install MySQL connector**:
   ```bash
   pip install mysql-connector-python
   ```

2. **Start MySQL service** (using Docker for development):
   ```bash
   cd sandbox
   docker compose up -d mysql
   ```

### Database Setup

1. **Create database and user** (if not using sandbox):
   ```sql
   CREATE DATABASE pdr_test;
   CREATE USER 'pdr_user'@'%' IDENTIFIED BY 'your_password';
   GRANT ALL PRIVILEGES ON pdr_test.* TO 'pdr_user'@'%';
   FLUSH PRIVILEGES;
   ```

2. **Configure environment**:
   ```bash
   export PDR_DB_TYPE=mysql
   export PDR_DB_HOST=localhost
   export PDR_DB_PORT=3306
   export PDR_DB_DATABASE=pdr_test
   export PDR_DB_USERNAME=pdr_user
   export PDR_DB_PASSWORD=your_password
   ```

3. **Test connection**:
   ```bash
   python sandbox/test_db_connections.py
   ```

### Running MySQL Integration Tests

```bash
# Automated setup and testing
python pdr_run/tests/integration/run_mysql_tests.py

# Or manually
cd sandbox && docker compose up -d mysql
python pdr_run/tests/integration/test_mysql_integration.py
```

## SFTP Storage Setup

### Prerequisites

1. **Install paramiko** (usually included):
   ```bash
   pip install paramiko
   ```

2. **SFTP Server Access**: Ensure you have:
   - Hostname/IP address
   - Username and password (or SSH key)
   - Remote directory path with write permissions

### Configuration

1. **Environment variables** (recommended):
   ```bash
   export PDR_STORAGE_TYPE=sftp
   export PDR_STORAGE_HOST=hera.ph1.uni-koeln.de
   export PDR_STORAGE_USER=your_username
   export PDR_STORAGE_PASSWORD=your_password
   export PDR_STORAGE_DIR=/remote/path/to/pdr/storage
   ```

2. **Configuration file** (for non-sensitive settings):
   ```yaml
   storage:
     type: sftp
     host: hera.ph1.uni-koeln.de
     username: your_username
     password: null  # Use PDR_STORAGE_PASSWORD environment variable
     base_dir: /remote/path/to/pdr/storage
   ```

### Testing SFTP Connection

```bash
# Test storage functionality (if file exists)
python sandbox/test_storage.py

# Alternative: use integration tests
python pdr_run/tests/integration/test_storage.py

# Check configuration
python -c "
from pdr_run.storage.base import get_storage_backend
storage = get_storage_backend()
print(f'Storage type: {type(storage).__name__}')
print('Connection test passed!' if hasattr(storage, 'host') else 'Using local storage')
"
```

### SSH Key Authentication

For key-based authentication, ensure your SSH key is available:

```bash
# Add your key to ssh-agent
ssh-add ~/.ssh/your_private_key

# Or use environment variable for key path
export PDR_STORAGE_SSH_KEY=/path/to/your/private/key
```

## Configuration File Examples

### Complete MySQL + SFTP Configuration

Create `my_config.yaml`:
```yaml
# Database Configuration
database:
  type: mysql
  host: localhost
  port: 3306
  database: pdr_test
  username: pdr_user
  password: null  # Use PDR_DB_PASSWORD environment variable

# Storage Configuration
storage:
  type: sftp
  host: your-server.com
  username: your_username
  password: null  # Use PDR_STORAGE_PASSWORD environment variable
  base_dir: /remote/pdr/storage

# Model Configuration
pdr:
  model_name: production_run
  base_dir: /home/user/pdr/production

# Model Parameters
model_params:
  metal: ["100"]
  dens: ["30", "40", "50"]
  mass: ["5", "6", "7"]
  chi: ["1", "10", "100"]
  species:
    - CO
    - C+
    - C
    - O
```

### Environment Variables for Production

```bash
#!/bin/bash
# production_env.sh - Source this file for production environment

# Database (MySQL)
export PDR_DB_TYPE=mysql
export PDR_DB_HOST=production-db.company.com
export PDR_DB_PORT=3306
export PDR_DB_DATABASE=pdr_production
export PDR_DB_USERNAME=pdr_service
export PDR_DB_PASSWORD="$(cat /etc/pdr/db_password)"  # Read from secure file

# Storage (SFTP)
export PDR_STORAGE_TYPE=sftp
export PDR_STORAGE_HOST=storage.company.com
export PDR_STORAGE_USER=pdr_service
export PDR_STORAGE_PASSWORD="$(cat /etc/pdr/storage_password)"
export PDR_STORAGE_DIR=/data/pdr/models

# Optional: Additional settings
export PDR_EXEC_PATH=/opt/pdr/bin
```

Usage:
```bash
source production_env.sh
pdr_run --config production_config.yaml --grid
```

## Running a Test Model

Run a simple PDR model:

```bash
# Basic single-point model
pdr_run --model-name test_model --single --dens 3.0 --chi 1.0

# With specific configuration
pdr_run --config my_config.yaml --model-name test_model --single --dens 3.0 --chi 1.0
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

### Configuration File Override Examples
```bash
# Environment overrides config file
export PDR_DB_PASSWORD=production_password
pdr_run --config config_with_different_password.yaml  # Uses production_password

# Mix of config file and command line
pdr_run --config base_config.yaml --model-name override_name --dens 5.0
```

### Switching Between Environments
```bash
# Development (SQLite + Local)
export PDR_DB_TYPE=sqlite PDR_STORAGE_TYPE=local
pdr_run --model-name dev_test

# Production (MySQL + SFTP)
export PDR_DB_TYPE=mysql PDR_STORAGE_TYPE=sftp
pdr_run --config production.yaml --model-name prod_run
```

## Troubleshooting

### Running Tests
Verify the framework is working correctly:

```bash
# Run the full test suite
cd /home/roellig/pdr/pdr/pdr_run/
python -m pytest pdr_run/tests/

# Run database-specific tests
make test-db

# Run MySQL integration tests
python pdr_run/tests/integration/run_mysql_tests.py

# Run storage tests
make test-storage
```

### Common Issues

#### Database Issues

1. **MySQL Connection Errors**:
   ```bash
   # Check if MySQL is running
   docker ps | grep mysql
   
   # Start MySQL service
   cd sandbox && docker compose up -d mysql
   
   # Test connection manually
   mysql -h localhost -u pdr_user -p pdr_test
   ```

2. **Password Authentication Failures**:
   - Always use `PDR_DB_PASSWORD` environment variable
   - Never hardcode passwords in config files
   - Check password contains no special characters that need escaping

3. **Missing MySQL Driver**:
   ```bash
   pip install mysql-connector-python
   ```

#### SFTP Storage Issues

1. **Authentication Failures**:
   ```bash
   # Test SFTP connection manually
   sftp your_username@your-server.com
   
   # Check environment variables
   echo $PDR_STORAGE_PASSWORD
   ```

2. **Permission Denied**:
   - Ensure remote directory exists and is writable
   - Check SSH key permissions (600 for private keys)
   - Verify user has access to the specified base directory

3. **Network/Firewall Issues**:
   ```bash
   # Test network connectivity
   ping your-server.com
   telnet your-server.com 22
   ```

#### Configuration Issues

1. **Environment Variable Not Recognized**:
   ```bash
   # Check current environment
   env | grep PDR_
   
   # Verify precedence
   python -c "
   from pdr_run.database.db_manager import DatabaseManager
   manager = DatabaseManager()
   print(f'Database type: {manager.config[\"type\"]}')
   print(f'Password source: {\"env\" if manager.config[\"password\"] else \"config\"}')"
   ```

2. **Config File Parsing Errors**:
   ```bash
   # Validate YAML syntax
   python -c "import yaml; yaml.safe_load(open('my_config.yaml'))"
   ```

### Database Password Issues

- If you see errors like `Access denied for user ... using password: YES` or the password in the connection string appears as `None`, it means the password was not set correctly.
- Always set your database password using the `PDR_DB_PASSWORD` environment variable:
  ```bash
  export PDR_DB_PASSWORD=your_db_password
  ```
- The framework will automatically use this value and override any value in the config file.
- For security, avoid hardcoding passwords in configuration files.

### SFTP Connection Issues

- For `[Errno 2] No such file or directory: ''` errors, check that local directory paths are properly specified
- Use `PDR_STORAGE_PASSWORD` environment variable for SFTP passwords
- Check SFTP server logs for authentication issues
- Verify network connectivity and firewall settings

### Checking Logs
```bash
# View the last run log (if it exists)
ls -la logs/ && cat logs/pdr_run.log 2>/dev/null || echo "No log file found yet"

# View paramiko (SFTP) logs (if SFTP is used)
cat logs/paramiko.log 2>/dev/null || echo "No SFTP log file found"

# Check Docker service logs
cd sandbox && docker compose logs mysql

# List all available logs
find . -name "*.log" -type f 2>/dev/null || echo "No log files found"
```

### Configuration Debugging

```bash
# Print current configuration
pdr_run --model-name debug_config --dry-run

# Check storage backend
python -c "
from pdr_run.storage.base import get_storage_backend
storage = get_storage_backend()
print(f'Storage: {type(storage).__name__}')
if hasattr(storage, 'host'):
    print(f'Host: {storage.host}')
    print(f'User: {storage.user}')
    print(f'Base dir: {storage.base_dir}')
"

# Check database configuration
python -c "
from pdr_run.database.db_manager import get_db_manager
manager = get_db_manager()
print(f'DB Type: {manager.config.get(\"type\")}')
print(f'DB Host: {manager.config.get(\"host\", \"N/A\")}')
print(f'DB Name: {manager.config.get(\"database\", manager.config.get(\"path\", \"N/A\"))}')
"
```

## Development

### Setting Up the Development Environment

1. Clone the repository and navigate to the PDR framework:
   ```bash
   cd /home/roellig/pdr/pdr/pdr_run/
   ```

2. Install in development mode:
   ```bash
   make dev-install
   ```

3. Set up the sandbox environment:
   ```bash
   make setup-sandbox
   make start-services
   ```

4. Run tests:
   ```bash
   make test-all
   ```

### Sandbox Environment

The sandbox provides MySQL and SFTP services for development:

```bash
# Start all services
make start-services

# Reset and clean environment
make clean-sandbox

# Test services individually
make test-db
make test-storage
make test-integration

# View service logs
make logs

# Restart services
make restart

# Complete development setup
make full-dev-setup
```

See SANDBOX_README.md for detailed development instructions.

---

For more details, consult the full documentation or reach out to the development team.