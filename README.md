# PDR Framework Installation and Testing Guide

This document provides instructions for installing, configuring, and testing the PDR (Photo-Dissociation Region) framework.

## Table of Contents
- [Installation](#installation)
- [Preflight Check](#preflight-check)
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
- [Run Status and Exit Handling](#run-status-and-exit-handling)
  - [Job states](#job-states)
  - [Database columns](#database-columns)
- [Wall-Time Cap](#wall-time-cap)
- [Stale-Job Recovery](#stale-job-recovery)
- [Storage Retries](#storage-retries)
  - [Placeholder checksums](#placeholder-checksums)
- [Post-Processing Steps](#post-processing-steps)
  - [UV continuum](#uv-continuum)
  - [ONION](#onion)
  - [SIMLINE](#simline)
- [Production Grid Run](#production-grid-run)
- [Troubleshooting](#troubleshooting)
  - [Known Limitations](#known-limitations)

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

## Preflight Check

`pdr_run --check` verifies the whole set-up of a production run in a few seconds and prints one
line per check. It is the compact alternative to `--dry-run`, which dumps the full configuration
and log.

```bash
pdr_run --check --config my_config.yaml                    # human-readable report
pdr_run --check-json --config my_config.yaml               # same results as JSON (for scripts)
pdr_run --check --config my_config.yaml --min-free-gb 50   # disk-space WARN threshold (default 20)
pdr_run --check --config my_config.yaml --check-timeout 10 # per network check (default 5 s)
```

`python -m pdr_run.cli.runner --check ...` is equivalent (`pdr_run` is the console script).
`--workers`, `--cpus`, `--species`, `--json-template` and `--force-simline` (and `--rerun`, shown in the report) are honoured so that
the check sees what the real run would see.

What is checked (`name` as shown in the report):

| Group | Checks |
|---|---|
| Configuration | `config.file` (loaded? no `pdr:` section means the runner discards the file), `config.sections` (unknown sections abort a run), `config.env` (which `PDR_*` variables override the file, and which the code silently IGNORES because the file defines that section), `config.secrets` (password set/not set; values are never printed), `python.imports` |
| KOSMA-tau install | `kt.base_dir`, `kt.rundir_write` (probe file), `kt.exe.pdr/onion/getctrlind/mrt` (exist + executable), `kt.pdr_version` (`pdrexe --version`, `-dirty` is a WARN), `kt.input_dirs` (symlinks resolve) |
| Templates and inputs | `tpl.json` (found, placeholders substituted as in a real job, parsed with json-fortran-style comments), `tpl.provenance` (config provenance is recorded; template sha256), `tpl.chem_network`, `tpl.binding_energies`, `tpl.fuv_file` (only if `ifuvtype` 5/6) |
| Scratch and disk | `tmp.dir` (writable), `disk.free` (per filesystem, WARN below `--min-free-gb`) |
| Storage | `storage` (local, sftp or rclone: write probe, read back, compare, delete; latency) |
| Database | `db.connect` (+ server version), `db.tables`, `db.columns`, `db.additive_columns` (the 13 run-status / UV-continuum / config-provenance columns), `db.rows`, `db.stale_jobs`, `db.write_rollback` (INSERT rolled back) |
| Post-processing | `post.onion` (`ONION3.INP.<species>` for every species), `post.uv_continuum` (`kosma_h2` importable, data files), `post.simline` (driver, binary, molecules, config) |
| Resources | `run.walltime` (WARN if `pdr.max_walltime_s` is unset), `run.workers` (workers, CPUs, RAM, MySQL `max_connections`) |

`--check` is read-only: it never calls `create_tables()` or `ensure_additive_columns()` and never
creates a missing database file or storage directory. A missing column is a FAIL with the hint
that a normal run would add it. The only side effects are probe files / one rolled-back INSERT,
which are removed even when a check fails. Each check is isolated (an exception becomes a FAIL
line for that check only) and every network check has a timeout. During the check the console log
is suppressed; the detailed log still goes to `logs/pdr_run.log` (the footer prints the path).

Example (sandbox-like set-up with an old database that lacks a column):

```
[PASS] config.file           /tmp/ex/cfg.yaml (4 sections)
[PASS] config.env            no PDR_* environment overrides set
[PASS] config.secrets        DB password NOT set (values never printed); storage password not set
[PASS] kt.exe.pdr            mockpdr
[PASS] kt.pdr_version        Mock PDR executable running...
[PASS] tpl.json              /tmp/ex/rundir/templates/pdr_config.json.template (placeholders substituted…
[PASS] storage               local /tmp/ex/store: write/read/compare/delete OK (0 ms)
[PASS] disk.free             run dir+temp dir+storage+sqlite db+log dir 80 GB free (min 20 GB)
[PASS] db.connect            sqlite 3.49.1 at /tmp/ex/pdr.db (1 ms)
[PASS] db.tables             all 9 expected tables present
[FAIL] db.additive_columns   1/11 additive column(s) missing on pdr_model_jobs: uvcont_error - a normal run adds them (ensure_additive_columns); --check does not
[PASS] db.write_rollback     INSERT + rollback OK, no residue (1 ms)
[SKIP] post.uv_continuum     uv_continuum.enabled is false
[PASS] run.walltime          max_walltime_s=21600 s (6.0 h); stale threshold 9.0 h
  ... (about 32 lines in total)

Summary: 27 PASS, 1 WARN, 1 FAIL, 3 SKIP in 2.1 s -> NOT READY
Config: /tmp/ex/cfg.yaml | detailed log: /tmp/ex/logs/pdr_run.log | 0 WARNING+ log message(s) suppressed on screen
```

Exit codes: `0` nothing FAILed (WARN and SKIP are fine), `1` at least one FAIL. With `--check-json`
the object has `ok`, `summary` (counts), `checks` (`name`, `status`, `detail`, `elapsed_s`),
`config_file`, `log_files`.

## Querying the configuration of a job

The `kosmatau_parameters` columns are only the old PDRNEW.INP parameters; everything else (h2.*,
radiative_transfer.*, numerical_params.*, dust.*, the network file, species, abundances) is fixed in
the JSON template. To make it queryable, every job stores, when its `pdr_config.json` is rendered,

* `pdr_model_jobs.config_json` - the complete resolved config the job ran with (placeholders
  substituted), parsed to a normalized JSON object (comments and trailing commas, which json-fortran
  accepts, are removed; `JSON` on MySQL, text with the JSON functions on SQLite);
* `pdr_model_jobs.template_sha256` - sha256 of the template file it was rendered from.

The record belongs to the job (not to `json_files`, whose rows are deduplicated by file hash and
shared between jobs). A job that is skipped because its result already exists is rendered all the
same, so it carries the config it would have run with (a `--rerun` job gets the new one). If the
config does not parse, `config_json` is NULL and a WARNING is logged; the run is not affected.
Old databases get the columns automatically (`ensure_additive_columns`); jobs created before have NULL.

```sql
-- jobs with h2.gas_seed_h3p_shape = 1
SELECT id, model_job_name FROM pdr_model_jobs
 WHERE config_json->>'$.h2.gas_seed_h3p_shape' = '1';

-- the network file of a job
SELECT config_json->>'$.chemical_network_file' FROM pdr_model_jobs WHERE id = 123;

-- jobs whose species list contains "HCO18O+"
SELECT id FROM pdr_model_jobs
 WHERE JSON_CONTAINS(config_json->'$.species', '"HCO18O+"');

-- jobs rendered from a given template
SELECT COUNT(*) FROM pdr_model_jobs WHERE template_sha256 = '<sha256 from pdr_run --check>';
```

For a frequently used key, a generated column plus an index (MySQL 5.7+):

```sql
ALTER TABLE pdr_model_jobs
  ADD COLUMN h3p_shape INT GENERATED ALWAYS AS (config_json->>'$.h2.gas_seed_h3p_shape') VIRTUAL,
  ADD INDEX idx_h3p_shape (h3p_shape);
```

SQLite supports `json_extract(config_json, '$.h2.gas_seed_h3p_shape')` (and `json_each` for lists);
in SQLAlchemy use `PDRModelJob.config_json['h2']['gas_seed_h3p_shape'].as_integer()`.

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
   make start-services
   # or manually: cd sandbox && docker compose up -d mysql
   ```

### Database Setup

#### Using Sandbox (Recommended for Development)

The sandbox environment **automatically creates** the database and user when MySQL starts:

```bash
# 1. Setup sandbox directories
make setup-sandbox

# 2. Start services (MySQL auto-creates database and user)
make start-services

# 3. Test connection
python sandbox/test_db_connections.py
```

**Note:** No manual database creation required! Docker automatically creates:
- Database: `pdr_test`
- User: `pdr_user` with password `pdr_password`

#### Manual Setup (Production/Non-Docker)

If not using the sandbox, create the database and user manually:

```sql
CREATE DATABASE pdr_test;
CREATE USER 'pdr_user'@'%' IDENTIFIED BY 'your_password';
GRANT ALL PRIVILEGES ON pdr_test.* TO 'pdr_user'@'%';
FLUSH PRIVILEGES;
```

#### Configure Environment

```bash
export PDR_DB_TYPE=mysql
export PDR_DB_HOST=localhost
export PDR_DB_PORT=3306
export PDR_DB_DATABASE=pdr_test
export PDR_DB_USERNAME=pdr_user
export PDR_DB_PASSWORD=your_password
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

## Run Status and Exit Handling

`pdrexe` exits with status 0 both when the global iteration converged and when it did not (many aborts
are a plain Fortran `STOP`). pdr_run therefore does not trust the exit code alone.
`pdr_run.models.job_status.determine_job_status()` classifies every run, in this order:

1. The wall-time cap fired (see [Wall-Time Cap](#wall-time-cap)) -> `timeout`.
2. `pdrexe` returned a non-zero exit code (any value, also a negative one from a signal) -> `aborted`.
3. `pdroutput/pdrstruct_s.hdf5` does not exist -> `missing_output`.
4. Otherwise the convergence outcome is read from **`pdroutput/run_status.json`** if that file exists.
   If it is absent or unparsable (a warning is logged, the run is not aborted), the fallback is one of
   three fixed lines in `pdroutput/TEXTOUT` (the captured screen output):
   `Model CONVERGED in N iterations (eps=...)`,
   `Model CONVERGED in N iterations (relaxed eps=..., strict eps=...)`,
   `Model NOT CONVERGED after N iterations (eps=...)`.
   If neither source gives a result (e.g. an older `pdrexe`), the job is `finished`, i.e. the legacy
   exit-code-only behaviour, and a warning is logged.

pdr_run reads only `run_status.json` and `TEXTOUT`. It does not open the HDF5 group
`/Parameters/Run status` of the model file.

Fields understood in `run_status.json` (all optional): `converged`, `global_iterations`, `eps_final`,
`tsearch_flagged_shells`, `chem_relaxed_calls`, `deferred_iterations`, `code_version`, `git_hash`.
`converged` must be one of the strings `strict`, `relaxed` or `no` (the values the TEXTOUT fallback
produces); any other value is logged and treated as `finished`.

### Job states

| `status` | Meaning | Post-processing (UV continuum, ONION, SIMLINE) | Result files stored |
|---|---|---|---|
| `finished` | converged (`strict`), no flagged shells (or legacy fallback, see above) | yes | yes |
| `finished_relaxed` | converged only under the relaxed criterion (`converged = relaxed`) | yes | yes |
| `flagged` | converged, but `tsearch_flagged_shells > 0` (temperature search flagged shells) | yes | yes |
| `not_converged` | `converged = no`; wins over `flagged` | no | yes |
| `aborted` | `pdrexe` exit code != 0 | no | logs only |
| `missing_output` | exit code 0, but no `pdrstruct_s.hdf5` | no | logs only |
| `timeout` | wall-time cap exceeded, process group killed | no | logs only |

Other values written by the framework: `running` (`active` = true), `skipped` (model already exists in
storage, see below), `ERROR` (exception while running `pdrexe`), `exception` and `exception_runtime` /
`exception_setup_outer` (exception in the worker; `exception_runtime` only for a genuinely unexpected
exception, never for a failed post-processing step), `failed_storage` (results could not be stored after all
retries, see [Storage Retries](#storage-retries)),
`reset_stale` (see [Stale-Job Recovery](#stale-job-recovery)) and the legacy `problem`. Every one of these
except `running` clears the `active` and `pending` flags of the job row.

### Database columns

`pdr_model_jobs` carries 12 additive, nullable columns (list `_PDR_MODEL_JOB_ADDITIVE_COLUMNS` in
`pdr_run/database/db_manager.py`, same columns as `PDRModelJob` in `pdr_run/database/models.py`):

| Column | Type | Meaning |
|---|---|---|
| `run_status_converged` | VARCHAR(20) | `strict`, `relaxed` or `no` |
| `run_status_global_iterations` | INTEGER | number of global iterations |
| `run_status_eps_final` | FLOAT | final convergence measure (TEXTOUT fallback: the strict eps in the relaxed case) |
| `run_status_tsearch_flagged_shells` | INTEGER | shells flagged by the temperature search |
| `run_status_chem_relaxed_calls` | INTEGER | chemistry calls accepted under the relaxed criterion |
| `run_status_deferred_iterations` | INTEGER | deferred iterations (from `run_status.json` only) |
| `run_status_code_version` | VARCHAR(100) | `pdrexe` version string (JSON only) |
| `run_status_git_hash` | VARCHAR(64) | `pdrexe` git hash (JSON only) |
| `uvcont_applied` | BOOLEAN | UV continuum was written to the model file |
| `uvcont_closure_ok` | BOOLEAN | photon-closure gate of the UV continuum tool passed |
| `uvcont_error` | TEXT | error message of the UV continuum step, if it failed |
| `postproc_error` | TEXT | error messages of failed ONION / SIMLINE steps (`ONION <species>: ...; SIMLINE: ...`); the job status is not changed by them |

The TEXTOUT fallback fills only `converged`, `global_iterations` and `eps_final`; the other `run_status_*`
columns stay NULL. For `aborted`, `missing_output` and `timeout` all `run_status_*` columns are NULL.

`ensure_additive_columns(engine)` adds any of these columns that is missing to an existing table with
`ALTER TABLE ... ADD COLUMN` (portable across SQLite, MySQL and PostgreSQL). It is called at the end of
`create_tables()`, so both a normal run and `--reset-stale-jobs` migrate an old database automatically; a
fresh database gets the columns from `create_all()`. It never raises: on failure a warning is logged and
the columns stay unpopulated. `pdr_run --check` does not migrate; it reports the missing columns
(`db.additive_columns`).

### Post-processing and result storage per status

The model's own status is kept. `run_kosma_tau` runs UV continuum, ONION and SIMLINE only for a usable
model: `finished`, `finished_relaxed`, `flagged` (`job_status.POSTPROCESS_STATUSES`). For every other status
this is skipped and logged, and the classification stays (`aborted`, `timeout`, `missing_output`,
`not_converged`).

What is stored (`copy_pdroutput`):

- Always: the screen log `TEXTOUT<model>`, `pdr_config<model>.json` and `PDRNEW<model>.INP` if present.
- For `aborted`, `timeout` and `missing_output` additionally `run_status<model>.json` (if the model wrote
  one) and `pdrexe_error<model>.log` (from `pdrexe_error.log`), but **no result files**: the output of such a
  run is partial, and a stored `pdrstruct<model>.hdf5` would be taken for a finished node by the
  skip-existing logic.
- For `finished`, `finished_relaxed`, `flagged` and `not_converged` (complete output,
  `job_status.COMPLETE_OUTPUT_STATUSES`) all result files: HDF4/HDF5, `chemchk`, `MCDRT`, `CTRL_IND`. A
  `not_converged` model is stored for inspection but not post-processed.

A failing ONION or SIMLINE step is logged, written to `postproc_error` and does not change the status or
prevent the storage of the model output. A failing UV continuum step goes to `uvcont_error` as before.
A file that cannot be stored after all retries gives the job the terminal status `failed_storage`; the other
files are still tried and nothing overwrites that status later. (The earlier status is then only in the log.)

### Exit code and summary line

At the end of a `--single` or grid run pdr_run prints one line, e.g.

```
Job states: 12 finished, 1 not_converged, 1 aborted, 0 failed_storage
```

(actual `status` values, most frequent first; `N with post-processing errors` is appended if any job has a
`postproc_error` or `uvcont_error`) and exits with

| Exit code | Meaning |
|---|---|
| 0 | every job ended in `finished`, `finished_relaxed`, `flagged` or `skipped` and no job has a post-processing error |
| 1 | some jobs failed: any other status (`not_converged`, `aborted`, `timeout`, `missing_output`, `failed_storage`, `exception*`, ...) or a recorded post-processing error |
| 2 | run-level error: the run raised an exception, or no job was run (e.g. missing PDR directory) |

Other errors that end the program before any job runs (unknown config section, failed `--check`) exit 1
as before. The outcome per node is also in the database; the columns to look at are `status`, `active`,
`pending`, `run_status_*`, `uvcont_*`, `postproc_error`, `time_of_start`, `time_of_finish`:

```sql
-- overview of a grid
SELECT status, COUNT(*) FROM pdr_model_jobs GROUP BY status;

-- everything that needs attention
SELECT id, model_job_name, status, run_status_converged, run_status_global_iterations,
       run_status_eps_final, uvcont_error, postproc_error
FROM pdr_model_jobs
WHERE status NOT IN ('finished', 'finished_relaxed', 'flagged', 'skipped') OR postproc_error IS NOT NULL;
```

Nodes are never retried automatically; use `--rerun` ([step 8](#8-rerun-failed-nodes)).

## Wall-Time Cap

`pdr.max_walltime_s` (seconds, default `None` = no cap) limits the run time of each `pdrexe`. The value is
read by `run_pdr()` from the `pdr` section of the config file (default from `PDR_CONFIG`). `pdrexe` is
started in its own session (`start_new_session=True`, no shell), and `Popen.wait(timeout=max_walltime_s)`
is used. On expiry the whole process group is sent `SIGTERM`; if it is still alive after 10 s it gets
`SIGKILL`, and pdr_run waits up to 30 s more to reap it. The run is then classified `timeout` (no other
source is consulted), the error `wall-time cap of ...s exceeded, killing process group` is logged, and the
worker moves on. Note the caveat under
[How a failed or non-converged model shows up](#how-a-failed-or-non-converged-model-shows-up): the final
status of a timed-out job may be `exception_runtime`. The cap applies to `pdrexe` only, not to ONION, SIMLINE (own
`simline.timeout`, default 3600 s) or the UV continuum (own `uv_continuum.timeout`, default 300 s).

```yaml
pdr:
  max_walltime_s: 21600   # 6 h per model
```

Choose the cap from the slowest node of the grid, not the average. `pdr_run --check` warns if it is unset
(`run.walltime`). The cap also sets the default stale threshold (below).

## Stale-Job Recovery

A job that is `running` but whose driver process died (crash, reboot or power loss of the host, `kill -9`)
never reaches a terminal status: `pdrexe` either finishes or hits the wall-time cap while the driver lives,
so a `running` row long after any possible run time is abandoned. Such rows keep `active = true`.

```bash
pdr_run --reset-stale-jobs --dry-run --config my_config.yaml   # only report
pdr_run --reset-stale-jobs --config my_config.yaml             # set status 'reset_stale', active/pending = false
pdr_run --reset-stale-jobs --stale-after-hours 12 --config my_config.yaml
```

- `find_stale_jobs()` (`pdr_run/database/queries.py`) selects rows with `status = 'running'` and
  `time_of_start` older than the threshold. Rows with `time_of_start` NULL are never selected.
- Threshold, in order: `--stale-after-hours` (hours); otherwise `1.5 * pdr.max_walltime_s`; otherwise
  `DEFAULT_STALE_AFTER_S` = 6 h. `--stale-after-hours` accepts fractions.
- `reset_stale_jobs()` only marks the rows (`reset_stale`, `active = false`, `pending = false`). It creates
  no replacement job and leaves the old row in place. The action runs and exits; no model is started.
- Every grid run also calls `find_stale_jobs()` after creating its job entries and only logs a warning
  (never fails); resetting is always an explicit operator action, so a job of a concurrent, unrelated
  pdr_run invocation is not touched behind your back.
- Use it after a crash or reboot of the host, **when no pdr_run of that database is still running**. If
  another driver runs on the same database with long nodes, set the threshold above the longest node.
- To recompute the reset nodes, run the same grid command again (see
  [Rerun failed nodes](#8-rerun-failed-nodes)).

## Storage Retries

Remote storage backends retry transient errors with bounded exponential backoff
(`pdr_run/utils/retry.py`, `retry_with_backoff(max_retries=3, initial_delay=2.0, backoff=2.0)`): 1 initial
attempt plus 3 retries, waiting 2 s, 4 s and 8 s. Each failed attempt is logged as a WARNING, the final
failure as an ERROR.

| Backend | Retried operations | Retried errors |
|---|---|---|
| SFTP | `store_file`, `retrieve_file`, `list_files`, `file_exists` | `paramiko.SSHException`, socket errors and timeouts, `ConnectionError`, `OSError`, `EOFError`; **not** retried: `FileNotFoundError` and `paramiko.AuthenticationException` (permanent) |
| rclone | `store_file` (mkdir + copyto), `retrieve_file`, `list_files`, `sync_directory`, `file_exists` | `SubprocessError`, `RuntimeError`, `OSError` |

For rclone `file_exists`, only a stderr that looks like a transport problem (`timeout`, `connection
refused`, `no such host`, ...) is retried; an ordinary "path not found" is the normal answer for a node not
yet computed and is not retried. The local backend does not retry.

When the retries are exhausted, `store_file` keeps its old contract and **returns `False`** instead of
raising (`file_exists` returns `False`, treating the file as absent). Callers in `pdr_run.models.kosma_tau` (`copy_pdroutput`, `copy_onionoutput`, `run_simline`) check this
(a local-backend exception counts as a failure as well): the remaining files are still tried, and the job
gets the status `failed_storage`, which is not overwritten later. The reason is in the log
(`Storing ... failed after retries`).

Files are replaced safely: SFTP uploads to `<name>.part` and renames it over the target (`posix_rename`), the
local backend copies to `<name>.part` and uses `os.replace`; an interrupted transfer leaves an existing file
intact (rclone `copyto` already writes to a temporary name). A leftover `.part` file is removed on failure.

### Whole-file compression of stored results

`storage.compress_files` is a list of `fnmatch` patterns (default `[]` = nothing is compressed, behaviour
unchanged). A result file whose **stored name** (e.g. `pdrchem<model>.hdf5`, `chemchk<model>.out`) or whose
**local name** (`pdrchem_c.hdf5`, `chemchk.out`) matches a pattern is stored as `<name>.gz`:

```yaml
storage:
  compress_files: ["pdrchem*.hdf5", "chemchk*.out"]   # recommended for grid 1
```

- Measured on a (5,0,0) node: `pdrchem_c.hdf5` 65 MB -> 21 MB, `chemchk.out` 29 MB -> 3 MB. Per-dataset HDF5
  compression does not help (the size is per-object overhead of 1000 small datasets).
- The file is compressed by streaming (gzip level 6, no file name or time in the header, so the same input
  gives the same bytes) into a temporary `<local file>.gz` next to the local output, uploaded with the normal
  atomic `.part` logic and retries, and removed afterwards, also on failure. A compression error counts like
  a storage failure (`failed_storage`).
- The database refers to what is stored: `HDFFile.file_name_hdf5_c`, `full_path_hdf5_c` and
  `PDRModelJob.output_hdf5_chem_file` / `output_chemchk_file` carry the `.gz` name, `sha256_sum_hdf5_c` and
  `file_size_hdf5_c` are checksum and size of the `.gz`. The uncompressed size is only logged
  (`Compressed ...: N -> M bytes`), there is no column for it.
- `pdrstruct<model>.hdf5` is never compressed (compressed internally, read directly by downstream tools),
  even if a pattern matches it. TEXTOUT, CTRL_IND and the other files are compressed only if matched.
- Skip-existing and `--rerun` look for `pdrstruct<model>.hdf5` and accept `pdrstruct<model>.hdf5.gz`; the
  skip path registers `.gz` names if only those are stored. `kosma_tau.retrieve_decompressed()` fetches a
  stored file uncompressed whether it is stored plain or as `.gz` (used for the SIMLINE input).
- Switching the setting for an already stored node: a new store writes the other variant next to the old one
  (`pdr_run` has no delete operation); remove the old file by hand. Read the `.gz` with `gunzip -k`, `zcat` or
  `h5py.File(io.BytesIO(gzip.open(f).read()))`.
- `pdr_run --check` shows the setting in the line `run.compression`.

### Placeholder checksums

If a node's `pdrstruct<model>.hdf5` already exists in storage, the PDR step is skipped (status `skipped`)
and `update_db_pdr_output_entries()` registers the file in table `hdf_files` (new entries; an existing entry only gets its paths updated) without
downloading it. The
`sha256_sum`, `sha256_sum_hdf5_s` and `sha256_sum_hdf5_c` columns then contain the constant
`UNVERIFIED:no-local-hash-model-skipped-exists-remotely` (`UNVERIFIED_CHECKSUM_SENTINEL` in
`pdr_run/models/kosma_tau.py`), never a hash. A real digest is 64 lowercase hex characters, so the
placeholder cannot be mistaken for one and any comparison against it fails visibly. The `file_size*`
columns are the real size for local storage and `0` for SFTP/rclone (logged as "without a verified size or
checksum"). Find such rows with:

```sql
SELECT id, file_name FROM hdf_files WHERE sha256_sum LIKE 'UNVERIFIED:%';
```

## Post-Processing Steps

Order inside one job (`run_kosma_tau`): `pdrexe` -> UV continuum (optional) -> ONION per species ->
SIMLINE (optional) -> result storage (`copy_pdroutput`). UV continuum, ONION and SIMLINE run only for a
usable model (`finished`, `finished_relaxed`, `flagged`; see
[Post-processing and result storage per status](#post-processing-and-result-storage-per-status)); their
failures are recorded in `postproc_error` / `uvcont_error`. ONION and SIMLINE read the local
`pdroutput/pdrstruct_s.hdf5`, so the file stored under `pdrgrid/` already contains their additions. If the
model already exists in storage (`skipped`), nothing of this runs unless forced.

### UV continuum

Adds the H2 dissociation continuum to the model file in place (about 5 s and 0.7 GB RSS per model, see the
`kosma_h2` documentation in the KOSMA-tau repository). Opt-in:

```yaml
uv_continuum:
  enabled: true
  kosma_tau_dir: /path/to/kosma-tau      # checkout containing h2py/; required
  python_executable: /path/to/python     # default: the interpreter running pdr_run
  timeout: 300                           # seconds (default)
  force: false                           # pass --force to the tool
  extra_args: []                         # extra CLI arguments, appended
```

- The tool is `<kosma_tau_dir>/h2py/postprocess_uv_continuum.py <workdir>/pdroutput/pdrstruct_s.hdf5`. It runs
  with `PYTHONPATH=<kosma_tau_dir>/h2py:$PYTHONPATH`, so `kosma_h2` is importable without changing the driver
  environment. The chosen `python_executable` needs `numpy` and `h5py`.
- Only jobs in a usable state (`finished`, `finished_relaxed`, `flagged`) are processed.
- Output: the datasets `Integrated quantities/Spectrum/UV Continuum/...` and a provenance line under
  `Parameters/Postprocessing` inside `pdrstruct_s.hdf5` (stored as `pdrgrid/pdrstruct<model>.hdf5`); the
  tool log is `pdroutput/TEXTOUT_UVCONT` in the temporary job directory (removed with it unless `--keep-tmp`).
- Exit code 0 -> `uvcont_applied = uvcont_closure_ok = true`. Exit code 3 (photon-closure gate rejected the
  write; file unchanged) -> both `false`, the job is **not** failed, a warning is logged. Any other exit code,
  a missing tool or model file, a missing `kosma_tau_dir` or a timeout -> the exception text is written to
  `uvcont_error`, the model stays in its physics status and the grid continues. `uvcont_error` is reset to
  NULL on success.

### ONION

Runs for every species of the job (`--species`, `model_parameters.species`; default list in
`pdr_run/config/default_config.py`). For each species `set_oniondir()` copies
`<pdr.base_dir>/onioninpdata/ONION3.INP.<species>` to `ONION3.INP`; a missing file makes that species fail
(text in `postproc_error`, status unchanged, the other species and the model storage continue), after
`pdrexe` has already run. Check the species list against `onioninpdata/`
before starting (`pdr_run --check`, `post.onion`). Results go to
`<model_path>/oniongrid/ONION<model>.<file>` for `jerg_`, `jtemp_`, `linebt_` and `ONION3_<species>.OUT`
files plus `TEXTOUT<model>_<species>`. The ONION exit status is not evaluated.
`--force-onion` also runs ONION for a skipped node.

### SIMLINE

Radiative-transfer post-processing through `<simline_dir>/python/run_simline.py`. Opt-in per config or per
run:

```yaml
simline:
  enabled: true
  species: [CO, 13CO]        # optional; default: the pipeline's own simline_config.json
  simline_dir: /path/to/kosma-tau/simline   # default: <pdr.base_dir>/simline
  config_file: /path/to/simline_config.json # default: <simline_dir>/python/simline_config.json
  timeout: 3600              # seconds (default)
```

```bash
pdr_run --grid --force-simline --config my_config.yaml   # also for nodes whose PDR step is skipped
```

- `--force-simline` runs SIMLINE even if `simline.enabled` is false and even if the model already exists in
  storage; in that case the model file is fetched from storage (`pdrgrid/pdrstruct<model>.hdf5`), which makes
  RT-only re-runs of an existing grid possible. Without the flag, a skipped node is not processed.
- Input: a working copy `pdroutput/pdrstruct<model>_simline.hdf5` of the model file; the pipeline writes to
  `simlineoutput/` (log: `TEXTOUT_SIMLINE`). The ONION results in `pdrgrid/` are not modified.
- Output in storage: `<model_path>/simlinegrid/SIMLINE<model>.<file>` for every file in `simlineoutput/` and
  `<model_path>/simlinegrid/pdrstruct<model>_simline.hdf5`.
- A non-zero exit of the pipeline is recorded as `SIMLINE: ...` in `postproc_error`; the status of the job
  stays and `copy_pdroutput` still stores the model files. `pdr_run --check` verifies driver, binary,
  `obs.template`, `molecules/` and the config (`post.simline`).

## Production Grid Run

Walkthrough for a tiered grid on one compute host with a MySQL database. Hostnames, paths and passwords
below are placeholders.

### 1. Deploy the branch

```bash
git clone <repository-url> pdr_run && cd pdr_run
git checkout <production-branch>
python3 -m venv venv && . venv/bin/activate
pip install -e .            # installs the pdr_run console script
pdr_run --help
```

### 2. Set the database password

```bash
export PDR_DB_PASSWORD='...'    # from a secrets store; never in the config file or the shell history
```

The variable overrides `database.password` of the config file (see [Database Configuration](#database-configuration)).

### 3. Freeze the template and the binary

Every job runs in a fresh temporary directory in which `pdrexe` is a **symlink** to
`<pdr.base_dir>/<pdr.pdr_file_name>`, and `pdrinpdata/`, `onioninpdata/`, `In/` and `templates/` are copied
from `pdr.base_dir` per job. A rebuild of `pdrexe` or an edit of the template during a multi-day grid would
therefore change the model halfway. Freeze both before the start:

```bash
cd /path/to/pdr.base_dir
cp /path/to/kosma-tau/pdrsrc/bin/main/pdrexe pdrexe_grid1_20260930   # pinned copy, unique name
sha256sum pdrexe_grid1_20260930
cp /path/to/frozen/pdr_config.json.template templates/pdr_config.json.template
sha256sum templates/pdr_config.json.template
chmod a-w pdrexe_grid1_20260930 templates/pdr_config.json.template
```

The `KOSMAtauExecutable` table records the file name, code revision, compilation date and SHA-256 of the
executable, so the run stays traceable. Use `pdr.pdr_file_name` for the pinned copy; per-grid templates can
also be passed with `--json-template`. Keep the hashes in your run notes.

### 4. Run `pdr_run --check` until READY

```bash
pdr_run --check --config grid1.yaml --workers 8
```

Fix every FAIL and read every WARN (see [Preflight Check](#preflight-check)); repeat until the summary reads
READY. Exit code 0 = no FAIL. Repeat the check after every change of config, template or binary.

### 5. Start the grid

```yaml
# grid1.yaml
database:
  type: mysql
  host: localhost
  port: 3306
  database: pdr_production
  username: pdr_service
  password: null              # PDR_DB_PASSWORD
  pool_size: 5
  max_overflow: 5

storage:
  type: local
  base_dir: /data/pdr/models  # results: <base_dir>/<model_name>/pdrgrid, oniongrid, simlinegrid
  compress_files: ["pdrchem*.hdf5", "chemchk*.out"]   # stored as .gz, ~3x (HDF5) / ~10x (ASCII) smaller

pdr:
  model_name: grid1_tier0
  base_dir: /path/to/pdr.base_dir
  pdr_file_name: pdrexe_grid1_20260930
  onion_file_name: onionexe
  getctrlind_file_name: getctrlind
  mrt_file_name: mrt.exe
  json_template_file: pdr_config.json.template
  chem_database: chem_rates_grid.dat
  max_walltime_s: 21600

uv_continuum:
  enabled: true
  kosma_tau_dir: /path/to/kosma-tau

simline:
  enabled: false

model_parameters:
  metal: ["100"]
  dens: ["30", "40", "50"]
  mass: ["5", "6"]
  chi: ["1", "10", "100"]
  species: [CO, C+, C]
```

```bash
nohup pdr_run --grid --parallel --workers 8 --config grid1.yaml > grid1_tier0.out 2>&1 &
```

`--workers` is the number of worker processes (one `pdrexe` each); it is only used with `--parallel`. Without
`--workers`, the count is the number of CPUs minus `reserved_cpus` (a `model_parameters` key, default 2);
`--cpus` is accepted but only used by `--check`, not by a run. Size the workers to CPUs and RAM
(`pdrexe` plus the ~0.7 GB of the UV continuum), and check that `workers * (pool_size + max_overflow)` does not
exceed the MySQL `max_connections` (`run.workers` reports it). `--model-name` on the command line overrides
`pdr.model_name`. `pdr_run` creates all job rows first (`pending = true`), then runs them.

### 6. Monitor

```bash
tail -f logs/pdr_run.log                                    # detailed log (PDR_LOG_DIR changes the directory)
mysql -u pdr_service -p pdr_production -e \
  "SELECT status, COUNT(*) FROM pdr_model_jobs GROUP BY status"
```

Watch `running` (should not exceed `--workers`), the `timeout`, `aborted`, `not_converged`, `failed_storage` and `exception*`
counts, and `time_of_start` of the oldest `running` job against `max_walltime_s`. The stored `TEXTOUT<model>`
of a node (`pdrgrid/`) is the screen output of that model.

### 7. Recover stale jobs after interruptions

After a crash or reboot of the host, first make sure no `pdr_run` and no `pdrexe` of the grid is left
(`pgrep -af pdr_run`, `pgrep -af pdrexe_grid1`), then:

```bash
pdr_run --reset-stale-jobs --dry-run --config grid1.yaml
pdr_run --reset-stale-jobs --config grid1.yaml
```

See [Stale-Job Recovery](#stale-job-recovery). Then restart the same grid command (step 8).

### 8. Rerun failed nodes

Rerunning the same command (same `model_name` and parameter lists) creates new job rows for every node whose
earlier row is no longer `pending` and, by default, skips (status `skipped`) every node whose
`<model_path>/pdrgrid/pdrstruct<model>.hdf5` already exists in storage. Consequently:

- Nodes that never produced a file (`aborted`, `timeout`, `missing_output`, `reset_stale`, exceptions before
  storage) are recomputed by simply restarting the command (their partial output is not stored, see above).
- Nodes whose result is stored but should be recomputed (`not_converged`, `flagged`, `failed_storage`, or
  after a change of binary/template) need **`--rerun STATE[,STATE...]`**:

  ```bash
  pdr_run --grid --config grid1.yaml --rerun not_converged        # only these
  pdr_run --grid --config grid1.yaml --rerun failed               # every non-success state
  pdr_run --grid --config grid1.yaml --rerun all                  # everything, also converged nodes
  ```

  `STATE` is `all`, `failed` (every state except `finished`, `finished_relaxed`, `flagged`, `skipped`) or a
  literal job status (`not_converged`, `aborted`, `timeout`, `failed_storage`, ...). The state of a node is
  the status of the most recent earlier job of that node in the database that computed it (`skipped` rows
  are ignored); a stored file without such a row is recomputed only by `all`. Without `--rerun` the behaviour
  is unchanged. The new job is recorded as a new row, and the stored files are replaced only when the
  new ones are written (write, then rename); if the recomputation fails, the old stored files stay and the
  new job row carries the failure. `--check` accepts `--rerun` and shows it in the report.
- Skipped nodes get `UNVERIFIED:` placeholder checksums in `hdf_files` (see above).
- To re-run a restricted set, restrict the parameter lists on the command line (`--dens`, `--chi`, ...
  override the config file); `--single` runs one node.
- A changed binary or template needs a new `pdr_file_name` / template and a new `pdr_run --check`.

### 9. Where results and logs are

| What | Where |
|---|---|
| Model files | `<storage.base_dir>/<model_name>/pdrgrid/`: `pdrstruct<model>.hdf5`, `pdrchem<model>.hdf5`, `pdr<model>.hdf` (if written), `TEXTOUT<model>`, `chemchk<model>.out`, `pdr_config<model>.json`, `CTRL_IND<model>`, `MCDRT<model>.tar.gz` |
| ONION | `<model_name>/oniongrid/ONION<model>.*`, `TEXTOUT<model>_<species>` |
| SIMLINE | `<model_name>/simlinegrid/SIMLINE<model>.*`, `pdrstruct<model>_simline.hdf5` |
| Job table | `pdr_model_jobs` (status, run-status and UV continuum columns, file paths); `hdf_files` (paths, checksums, sizes) |
| Driver log | `logs/pdr_run.log` (directory `PDR_LOG_DIR`, default `logs/`, relative to the working directory) and the redirected stdout |
| Temporary job directories | `pdr-job<ID>-*` in the system temp directory, removed after the job unless `--keep-tmp` |

`<model>` is `<metal>_<dens>_<mass>_<chi>_00`. With SFTP or rclone storage the same layout lives below the
remote `base_dir`.

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

### Known Limitations

Behaviour of the existing code that was found while writing the preflight check, and what could not be
verified without the production host, is listed in
[`docs/PREFLIGHT_FINDINGS_2026-09-30.md`](docs/PREFLIGHT_FINDINGS_2026-09-30.md). Also note:

- `--cpus` and `--random` are accepted by the argument parser but have no effect on a run (`--cpus` is used
  by `--check` only).
- A `--rerun` of a node that fails again keeps the old stored files (only the new job row shows the failure).

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