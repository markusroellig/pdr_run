# PDR Framework Sandbox Environment

The sandbox provides a complete development and testing environment for the PDR framework, including:
- MySQL database server
- PostgreSQL database server
- SFTP server for remote storage testing
- Redis for caching (optional)
- Mock PDR executables for testing
- Automated test scripts

## Quick Start

1. **Setup the sandbox environment:**
   ```bash
   make setup-sandbox
   ```
   This creates all necessary directories, mock executables, templates, and test scripts.

2. **Start all services:**
   ```bash
   make start-services
   ```
   This starts Docker containers for MySQL, PostgreSQL, SFTP, and Redis.

   **Note:** MySQL database and user are **automatically created** by Docker - no manual setup required!

3. **Run all tests:**
   ```bash
   make test-all
   ```
   This runs unit tests, database connection tests, storage tests, and integration tests.

## What Gets Created

### Directory Structure

When you run `make setup-sandbox`, the following structure is created:

```
sandbox/
├── mysql/
│   └── init/              # MySQL initialization scripts (auto-executed on first start)
├── postgres/
│   └── init/              # PostgreSQL initialization scripts
├── storage/               # Local storage for test model outputs
├── sqlite/                # SQLite database files for testing
├── logs/                  # Log files from test runs
├── pdr_executables/       # Mock PDR executables (mockpdr, mockonion, etc.)
├── templates/             # Test template files
├── configs/               # Test configuration files
├── environments/          # Environment variable files
├── sftp_data/             # SFTP server data directory
├── test_data/             # Additional test data
├── test_db_connections.py # Database connection test script
├── test_storage.py        # Storage backend test script
└── test_integration.py    # Integration test script
```

### Docker Services

The sandbox provides these services via Docker Compose:

- **MySQL 8.0** (port 3306)
  - Database: `pdr_test`
  - User: `pdr_user`
  - Password: `pdr_password`
  - **Auto-created on first start!**

- **PostgreSQL 13** (port 5432)
  - Database: `pdr_test`
  - User: `pdr_user`
  - Password: `pdr_password`

- **SFTP Server** (port 2222)
  - User: `pdr_user`
  - Password: `pdr_password`
  - Data directory: `/home/pdr_user/data`

- **Redis 6** (port 6379)
  - For caching and session management

## Database Auto-Initialization

### MySQL

The MySQL service automatically:
1. Creates the `pdr_test` database
2. Creates the `pdr_user` user with password `pdr_password`
3. Grants all privileges on `pdr_test` to `pdr_user`
4. Executes initialization scripts from `mysql/init/`

**No manual SQL commands are required!** Simply run:
```bash
make start-services
```

### Verifying Database Setup

```bash
# Test database connections
python sandbox/test_db_connections.py

# Or check manually
docker exec -it pdr_mysql mysql -u pdr_user -ppdr_password pdr_test -e "SHOW TABLES;"
```

## Available Make Commands

### Setup and Installation
```bash
make dev-install       # Install PDR framework in development mode
make setup-sandbox     # Create sandbox directory structure and files
make full-dev-setup    # Complete setup: install + sandbox + start services + tests
```

### Service Management
```bash
make start-services    # Start all Docker services
make stop-services     # Stop all Docker services
make restart           # Restart all services (stop + start)
make logs              # View Docker service logs (follow mode)
```

### Testing
```bash
make test-all          # Run all tests (unit + database + storage + integration)
make test-unit         # Run unit tests with pytest
make test-db           # Test database connections (MySQL, SQLite)
make test-storage      # Test storage backends (local, SFTP)
make test-integration  # Run integration tests
```

### Cleanup
```bash
make clean-sandbox     # Remove all sandbox data (databases, volumes, storage files)
```

### Code Quality
```bash
make lint              # Run flake8 and pylint on codebase
```

## Testing Individual Components

### Database Connection Tests

```bash
# Test both SQLite and MySQL connections
python sandbox/test_db_connections.py
```

Expected output:
```
Testing database connections...
✓ SQLite connection successful
✓ MySQL connection successful
✓ MySQL query successful: MySQL sandbox initialized successfully
```

### Storage Tests

```bash
# Test local storage backend
python sandbox/test_storage.py
```

Expected output:
```
Testing storage backends...
✓ Local storage store successful
✓ Local storage retrieve successful
```

### Integration Tests

```bash
# Run a complete workflow test with mock executables
python sandbox/test_integration.py
```

## Environment Variables

The sandbox provides pre-configured environment files in `sandbox/environments/`:

### MySQL Environment (`environments/mysql.env`)
```bash
PDR_DB_TYPE=mysql
PDR_DB_HOST=localhost
PDR_DB_PORT=3306
PDR_DB_DATABASE=pdr_test
PDR_DB_USERNAME=pdr_user
PDR_DB_PASSWORD=pdr_password

PDR_STORAGE_TYPE=local
PDR_STORAGE_DIR=./sandbox/storage
```

You can source these files:
```bash
source sandbox/environments/mysql.env
pdr_run --model-name test_model --single --dens 3.0 --chi 1.0
```

## Mock Executables

The sandbox includes mock PDR executables for testing without requiring the full PDR codebase:

- **mockpdr**: Simulates the main PDR executable
- **mockonion**: Simulates the Onion model
- **mockgetctrlind**: Simulates GetCtrlInd
- **mockmrt**: Simulates the MRT radiative transfer code

These executables:
- Run quickly (1-2 seconds)
- Create dummy output files
- Allow testing the framework without actual PDR calculations
- Are located in `sandbox/pdr_executables/`

## Troubleshooting

### Port Conflicts

If you see port binding errors:
```
Error: Bind for 0.0.0.0:3306 failed: port is already allocated
```

Stop conflicting services:
```bash
# Check what's using the port
sudo lsof -i :3306

# Stop existing MySQL
sudo systemctl stop mysql

# Or change the port in docker-compose.yml
```

### Database Connection Issues

```bash
# Check if MySQL is running
docker ps | grep mysql

# View MySQL logs
docker logs pdr_mysql

# Restart MySQL service
docker restart pdr_mysql

# Manual connection test
docker exec -it pdr_mysql mysql -u root -prootpassword
```

### Cleaning Up

If things get messy:
```bash
# Complete cleanup and restart
make clean-sandbox
make setup-sandbox
make start-services
make test-all
```

### Viewing Service Logs

```bash
# All services (follow mode)
make logs

# Specific service
cd sandbox && docker compose logs mysql
cd sandbox && docker compose logs postgres
cd sandbox && docker compose logs sftp
```

## Configuration Files

### Docker Compose

The main configuration is in `sandbox/docker-compose.yml`. Key points:

- Volume paths are **relative to the sandbox directory**
- Environment variables auto-create database and user
- Init scripts in `mysql/init/` and `postgres/init/` run on first start
- Data persists in Docker volumes (`mysql_data`, `postgres_data`)

### Test Scripts

Generated test scripts are standalone and can be run independently:

```bash
# Database tests
python sandbox/test_db_connections.py

# Storage tests
python sandbox/test_storage.py

# Integration tests
python sandbox/test_integration.py
```

## Development Workflow

Typical development workflow using the sandbox:

```bash
# 1. Initial setup (once)
make dev-install
make setup-sandbox

# 2. Start services
make start-services

# 3. Make code changes
# ... edit files in pdr_run/ ...

# 4. Run tests
make test-unit           # Quick unit tests
make test-db             # Database tests
make test-all            # Full test suite

# 5. Test with actual model run
pdr_run --model-name dev_test --single --dens 3.0 --chi 1.0

# 6. Check logs
cat sandbox/logs/pdr_run.log

# 7. Clean up when done
make stop-services
```

## Differences from Production

The sandbox environment differs from production in these ways:

| Aspect | Sandbox | Production |
|--------|---------|------------|
| Executables | Mock scripts (fast, dummy output) | Real PDR binaries |
| Database | Local Docker MySQL | Production MySQL server |
| Storage | Local filesystem | SFTP/RClone remote storage |
| Data | Test data, small grids | Real astronomical data, large grids |
| Performance | Fast, lightweight | Compute-intensive |

## Advanced Usage

### Running with Custom Configuration

```bash
# Create a custom config
cat > sandbox/configs/custom.yaml << EOF
database:
  type: mysql
  host: localhost
  port: 3306
  database: pdr_test
  username: pdr_user
  password: null  # Use PDR_DB_PASSWORD env var

storage:
  type: local
  base_dir: ./sandbox/storage

pdr:
  base_dir: ./sandbox/pdr_executables
  pdr_file_name: mockpdr
EOF

# Use it
export PDR_DB_PASSWORD=pdr_password
pdr_run --config sandbox/configs/custom.yaml --model-name test
```

### Testing Parallel Execution

```bash
# Run multiple models in parallel using mock executables
pdr_run \
  --model-name parallel_test \
  --parallel \
  --workers 4 \
  --dens 1.0 2.0 3.0 4.0 \
  --chi 1.0 10.0
```

### Database Inspection

```bash
# Access MySQL directly
docker exec -it pdr_mysql mysql -u pdr_user -ppdr_password pdr_test

# Run queries
mysql> SHOW TABLES;
mysql> SELECT * FROM pdr_model_jobs LIMIT 5;
mysql> SELECT * FROM kosma_tau_parameters LIMIT 5;
```

## See Also

- [Main README](README.md) - Complete framework documentation
- [CLAUDE.md](CLAUDE.md) - Development guidelines for Claude Code
- [Makefile](Makefile) - All available make targets