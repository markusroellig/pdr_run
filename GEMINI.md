# Gemini Code Assistant Context

This document provides context for the PDR (Photo-Dissociation Region) framework, a Python-based application for running astrophysical models.

## Project Overview

The PDR framework is a command-line tool designed to run PDR models, either as single instances or as large parameter grids. It manages the entire workflow, from generating parameter combinations to executing the model and storing the results.

**Key Technologies:**

*   **Language:** Python 3
*   **Core Libraries:**
    *   `sqlalchemy`: For database interaction.
    *   `joblib`, `dask`, `distributed`: For parallel processing.
    *   `pyyaml`: For configuration files.
    *   `paramiko`: For SFTP storage.
    *   `rclone-python`: For rclone-based storage.
    *   `pytest`: For testing.
*   **Database Support:** SQLite (default), MySQL, PostgreSQL.
*   **Storage Support:** Local filesystem, SFTP, rclone.

**Architecture:**

*   **`pdr_run/cli/runner.py`**: The main entry point for the `pdr_run` command-line interface. It handles argument parsing and orchestrates the model runs.
*   **`pdr_run/core/engine.py`**: The core logic of the application. It manages the setup of model directories, creation of database entries, and the execution of model instances, both sequentially and in parallel.
*   **`pdr_run/database/`**: Contains all database-related code, including SQLAlchemy models (`models.py`), database connection management (`db_manager.py`), and queries (`queries.py`).
*   **`pdr_run/storage/`**: Implements different storage backends for model outputs.
*   **`pdr_run/config/`**: Defines the default configuration (`default_config.py`) and logging setup (`logging_config.py`).
*   **`pdr_run/tests/`**: Contains the test suite, with unit, integration, and database tests.

## Building and Running

### Installation

To install the project for development, use the `Makefile`:

```bash
make dev-install
```

This installs the package in editable mode, so changes to the source code are immediately reflected.

### Configuration

The application is configured through a combination of YAML files, environment variables, and command-line arguments. The order of precedence is:

1.  Command-line arguments
2.  Environment variables
3.  YAML configuration file
4.  Default configuration (`pdr_run/config/default_config.py`)

The `README.md` file contains extensive documentation on how to configure the database and storage backends.

### Running the Application

The main command is `pdr_run`. You can see the available options with:

```bash
pdr_run --help
```

**Example Commands:**

*   **Run a single model:**
    ```bash
    pdr_run --single --dens 3.0 --chi 1.0
    ```

*   **Run a parameter grid study:**
    ```bash
    pdr_run --grid --dens 1.0 2.0 --chi 10.0 100.0
    ```

*   **Run in parallel:**
    ```bash
    pdr_run --grid --parallel --workers 4
    ```

## Development Conventions

### Testing

The project uses `pytest` for testing. The tests are located in the `pdr_run/tests/` directory.

To run all tests, use the `Makefile`:

```bash
make test-all
```

This command will set up the sandbox environment (if needed), start the required services (like MySQL), and run the test suite.

To run the tests directly:

```bash
python -m pytest pdr_run/tests/
```

### Sandbox Environment

The project includes a Docker-based sandbox environment for integration testing, especially for MySQL. The `Makefile` provides commands to manage the sandbox:

*   `make setup-sandbox`: Prepares the sandbox directories.
*   `make start-services`: Starts the Docker containers (e.g., MySQL).
*   `make stop-services`: Stops the Docker containers.
*   `make clean-sandbox`: Removes all sandbox data.

### Code Style

The project uses `flake8` and `pylint` for code quality checks. To run the linters:

```bash
make lint
```
