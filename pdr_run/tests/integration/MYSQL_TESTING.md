# MySQL Integration Testing

This directory contains comprehensive MySQL integration tests for the PDR framework.

## Quick Start

### Option 1: Automated Setup and Run
```bash
# Run with automatic MySQL setup
python pdr_run/tests/integration/run_mysql_tests.py
```


### Option 2: Automated Setup and Run
```bash
# 1. Start MySQL service
cd sandbox
docker-compose up -d mysql

# 2. Install MySQL connector
pip install mysql-connector-python

# 3. Run tests directly
python pdr_run/tests/integration/test_mysql_integration.py

# 4. Or run with pytest
pytest pdr_run/tests/integration/test_mysql_integration.py::test_mysql_integration_manual -v -s
```