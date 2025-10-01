# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Development Commands

### Installation and Setup
```bash
# Install in development mode
make dev-install
# or: pip install -e .

# Set up development environment (includes MySQL/SFTP Docker services)
make setup-sandbox
make start-services

# Complete development setup
make full-dev-setup
```

### Testing
```bash
# Run all tests
make test-all

# Individual test categories
make test-unit        # pytest unit tests
make test-db          # database connection tests
make test-storage     # storage functionality tests
make test-integration # integration tests

# Run specific test with pytest
python -m pytest pdr_run/tests/ -v
python -m pytest pdr_run/tests/database/test_db_manager.py -v
```

### Code Quality
```bash
# Lint code
make lint
# Runs: flake8 and pylint on pdr_run/
```

### Development Services
```bash
# Manage Docker services (MySQL, SFTP)
make start-services   # docker compose up -d
make stop-services    # docker compose down
make restart         # stop then start
make logs            # view service logs

# Clean up development data
make clean-sandbox   # removes volumes, databases, storage files
```

### Running PDR Models
```bash
# Basic model run
pdr_run --model-name test_model --single --dens 3.0 --chi 1.0

# With custom configuration
pdr_run --config my_config.yaml --model-name test_model --single --dens 3.0 --chi 1.0

# Grid runs with multiple parameter values
pdr_run --model-name grid_test --dens 1.0 2.0 3.0 --chi 1.0 10.0 100.0

# Parallel execution
pdr_run --model-name parallel_test --parallel --workers 4 --dens 1.0 2.0 --chi 1.0 2.0
```

## Architecture Overview

### Core Components

**Engine (`pdr_run/core/engine.py`)**: Central execution engine that orchestrates PDR model runs, manages parameter combinations, creates database entries, and handles parallel execution.

**Database Layer (`pdr_run/database/`)**:
- `db_manager.py`: Unified database abstraction supporting SQLite, MySQL, PostgreSQL
- `models.py`: SQLAlchemy ORM models for tracking jobs, parameters, executables
- `queries.py`: Common database operations and utilities
- Password handling via environment variables (never stored in config files)

**Storage Backends (`pdr_run/storage/`)**:
- `base.py`: Abstract storage interface and backend selection logic
- `local.py`: Local filesystem storage
- `remote.py`: SFTP and RClone remote storage with authentication

**Configuration System (`pdr_run/config/`)**:
- Hierarchical precedence: Environment variables > Config file > Defaults
- Database and storage credentials exclusively via environment variables
- Support for multiple backend types with unified configuration schema

### Execution Flow

1. **Parameter Generation**: Configuration parameters are expanded into parameter combinations
2. **Database Setup**: Jobs, parameters, and metadata are recorded in database for tracking
3. **Parallel Execution**: Each parameter set runs in isolated temporary directory with:
   - Executable symlinks from PDR base directory
   - Input file copies and template processing
   - Chemical database configuration
   - Result collection and database status updates

### Key Environment Variables

```bash
# Database Configuration
PDR_DB_TYPE=mysql|postgresql|sqlite
PDR_DB_HOST=localhost
PDR_DB_PASSWORD=secure_password   # Always use env var for passwords

# Storage Configuration
PDR_STORAGE_TYPE=local|sftp|rclone
PDR_STORAGE_PASSWORD=secure_password   # Always use env var for passwords

# Execution Configuration
PDR_BASE_DIR=/path/to/pdr/executables
PDR_STORAGE_DIR=/path/to/model/storage
```

### Database Models

Key models for understanding the data schema:
- `PDRModelJob`: Individual model execution tracking
- `KOSMAtauParameters`: Physical parameter sets
- `ModelNames`: Model name and path registration
- `KOSMAtauExecutable`: Executable version and checksum tracking
- `ChemicalDatabase`: Chemical reaction database metadata

### Package Structure

```
pdr_run/
├── cli/           # Command-line interface
├── config/        # Configuration management
├── core/          # Main execution engine
├── database/      # Database abstraction and models
├── io/           # File operations and utilities
├── models/       # Parameter handling and KOSMA-tau integration
├── storage/      # Storage backends (local, SFTP, RClone)
└── tests/        # Test suite
```

### Important Development Notes

- **Security**: Never commit passwords or sensitive credentials - always use environment variables
- **Database**: The framework auto-creates tables and handles schema migrations
- **Storage**: Remote storage (SFTP/RClone) requires proper authentication setup via environment variables
- **Testing**: Integration tests require Docker services to be running (`make start-services`)
- **Temporary Files**: Use `--keep-tmp` flag for debugging to preserve temporary execution directories