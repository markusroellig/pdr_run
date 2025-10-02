#!/usr/bin/env python3
"""Sandbox environment setup script."""

from pathlib import Path

def create_directory_structure():
    """Create the sandbox directory structure."""
    base_dir = Path(__file__).parent
    directories = [
        "storage",
        "sqlite",
        "logs",
        "pdr_executables",
        "mysql/init",
        "postgres/init",
        "sftp_data",
        "test_data",
        "configs",
        "environments",
        "templates"
    ]

    for directory in directories:
        dir_path = base_dir / directory
        dir_path.mkdir(parents=True, exist_ok=True)
        print(f"Created directory: {dir_path}")

def create_mock_executables():
    """Create mock PDR executables for testing."""
    base_dir = Path(__file__).parent
    executables = {
        "mockpdr": """#!/bin/bash
echo "Mock PDR executable running..."
sleep 2
touch pdroutput/pdrstruct_s.hdf5
touch pdroutput/pdrchem_c.hdf5
touch pdroutput/pdrout.hdf
touch pdroutput/TEXTOUT
touch pdroutput/CTRL_IND
echo "Mock PDR completed successfully"
""",
        "mockonion": """#!/bin/bash
echo "Mock Onion executable running..."
sleep 1
touch onionoutput/jerg_CO.smli
echo "Mock Onion completed successfully"
""",
        "mockgetctrlind": """#!/bin/bash
echo "Mock GetCtrlInd executable running..."
touch CTRL_IND
echo "Mock GetCtrlInd completed successfully"
""",
        "mockmrt": """#!/bin/bash
echo "Mock MRT executable running..."
sleep 1
touch Out/mock.out
echo "Mock MRT completed successfully"
"""
    }

    exe_dir = base_dir / "pdr_executables"
    for name, content in executables.items():
        exe_path = exe_dir / name
        exe_path.write_text(content)
        exe_path.chmod(0o755)
        print(f"Created mock executable: {exe_path}")

def create_test_templates():
    """Create test templates."""
    base_dir = Path(__file__).parent
    pdrnew_template = """
                 INPUT-DATA for PDR program (SANDBOX VERSION)

I. Physical input parameters

Total H particle density at cloud surface (in 1/cm**3):   XNSUR
KT_VARxnsur_
Cloud mass (in solar masses): MASS
KT_VARmass_
Cloud radius (in cm): RTOT
KT_VARrtot_
Species to track:
KT_VARspecies_
Grid flag:
KT_VARgrid_
"""

    json_template = """{
  "model_name": "sandbox_test",
  "parameters": {
    "xnsur": KT_VARxnsur_,
    "mass": KT_VARmass_,
    "rtot": KT_VARrtot_,
    "species": KT_VARspecies_
  }
}"""

    templates_dir = base_dir / "templates"
    (templates_dir / "PDRNEW.INP.template").write_text(pdrnew_template)
    (templates_dir / "pdr_config.json.template").write_text(json_template)
    print("Created test templates")

def setup_database_init_scripts():
    """Create database initialization scripts."""
    base_dir = Path(__file__).parent

    # Note: MySQL database and user are auto-created by docker-compose environment variables
    # This init script only creates additional test tables
    mysql_init = """
-- MySQL initialization script for PDR sandbox
-- Note: Database 'pdr_test' and user 'pdr_user' are auto-created by Docker

-- Create a test table to verify connection
CREATE TABLE IF NOT EXISTS connection_test (
    id INT AUTO_INCREMENT PRIMARY KEY,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    message VARCHAR(255)
);

INSERT INTO connection_test (message) VALUES ('MySQL sandbox initialized successfully');
"""

    postgres_init = """
-- PostgreSQL initialization script for PDR sandbox
CREATE TABLE IF NOT EXISTS connection_test (
    id SERIAL PRIMARY KEY,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    message VARCHAR(255)
);

INSERT INTO connection_test (message) VALUES ('PostgreSQL sandbox initialized successfully');
"""

    (base_dir / "mysql/init/01-init.sql").write_text(mysql_init)
    (base_dir / "postgres/init/01-init.sql").write_text(postgres_init)
    print("Created database initialization scripts")

def create_test_scripts():
    """Create comprehensive test scripts."""
    base_dir = Path(__file__).parent

    # Database connection test
    db_test = """#!/usr/bin/env python3
\"\"\"Test database connections.\"\"\"

import os
import sys
sys.path.insert(0, '.')

def test_mysql():
    # Clear cached modules to ensure fresh connection
    for module in list(sys.modules.keys()):
        if module.startswith('pdr_run.database'):
            del sys.modules[module]

    os.environ.update({
        'PDR_DB_TYPE': 'mysql',
        'PDR_DB_HOST': 'localhost',
        'PDR_DB_PORT': '3306',
        'PDR_DB_DATABASE': 'pdr_test',
        'PDR_DB_USERNAME': 'pdr_user',
        'PDR_DB_PASSWORD': 'pdr_password'
    })

    try:
        from pdr_run.database.connection import init_db
        from sqlalchemy import text
        session, engine = init_db()
        print("✓ MySQL connection successful")

        # Test query
        result = session.execute(text("SELECT message FROM connection_test LIMIT 1"))
        message = result.fetchone()[0]
        print(f"✓ MySQL query successful: {message}")
        session.close()

    except Exception as e:
        print(f"✗ MySQL connection failed: {e}")

def test_sqlite():
    os.environ.update({
        'PDR_DB_TYPE': 'sqlite',
        'PDR_DB_FILE': './sandbox/sqlite/test.db'
    })
    
    try:
        from pdr_run.database.connection import init_db
        session, engine = init_db()
        print("✓ SQLite connection successful")
        session.close()
        
    except Exception as e:
        print(f"✗ SQLite connection failed: {e}")

if __name__ == "__main__":
    print("Testing database connections...")
    test_sqlite()
    test_mysql()
"""
    
    # Storage test
    storage_test = """#!/usr/bin/env python3
\"\"\"Test storage backends.\"\"\"

import os
import sys
import tempfile
import json
sys.path.insert(0, '.')

def test_local_storage():
    os.environ.update({
        'PDR_STORAGE_TYPE': 'local',
        'PDR_STORAGE_DIR': './sandbox/storage'
    })
    
    try:
        from pdr_run.storage.base import get_storage_backend
        storage = get_storage_backend()
        
        # Create test file
        test_data = {"test": "data", "timestamp": "2023-01-01"}
        with tempfile.NamedTemporaryFile(mode='w', delete=False) as f:
            json.dump(test_data, f)
            temp_file = f.name
        
        # Store file
        storage.store_file(temp_file, "test/data.json")
        print("✓ Local storage store successful")
        
        # Retrieve file
        with tempfile.NamedTemporaryFile(mode='w', delete=False) as f:
            retrieve_file = f.name
        
        storage.retrieve_file("test/data.json", retrieve_file)
        
        # Verify content
        with open(retrieve_file, 'r') as f:
            retrieved_data = json.load(f)
        
        if retrieved_data == test_data:
            print("✓ Local storage retrieve successful")
        else:
            print("✗ Local storage data mismatch")
            
        # Cleanup
        os.unlink(temp_file)
        os.unlink(retrieve_file)
        
    except Exception as e:
        print(f"✗ Local storage test failed: {e}")

if __name__ == "__main__":
    print("Testing storage backends...")
    test_local_storage()
"""
    
    # Integration test
    integration_test = """#!/usr/bin/env python3
\"\"\"Integration test for PDR framework.\"\"\"

import os
import sys
import tempfile
sys.path.insert(0, '.')

def test_full_workflow():
    # Set up environment
    os.environ.update({
        'PDR_DB_TYPE': 'sqlite',
        'PDR_DB_FILE': './sandbox/sqlite/integration_test.db',
        'PDR_STORAGE_TYPE': 'local',
        'PDR_STORAGE_DIR': './sandbox/storage'
    })
    
    try:
        from pdr_run.core.engine import run_model
        from pdr_run.config.default_config import DEFAULT_PARAMETERS
        
        # Set up test parameters
        test_params = DEFAULT_PARAMETERS.copy()
        test_params['dens'] = ["30"]
        test_params['chi'] = ["10"]
        test_params['mass'] = ["-10"]
        test_params['metal'] = ["100"]
        
        # Configuration for sandbox
        config = {
            'pdr': {
                'base_dir': './sandbox/pdr_executables',
                'pdr_file_name': 'mockpdr',
                'onion_file_name': 'mockonion',
                'getctrlind_file_name': 'mockgetctrlind',
                'mrt_file_name': 'mockmrt',
                'pdrinp_template_file': 'PDRNEW.INP.template',
                'json_template_file': 'pdr_config.json.template'
            },
            'parameters': test_params
        }
        
        # Run model
        job_id = run_model(
            params=test_params,
            model_name="sandbox_test",
            config=config
        )
        
        print(f"✓ Integration test successful - Job ID: {job_id}")
        
    except Exception as e:
        print(f"✗ Integration test failed: {e}")
        import traceback
        traceback.print_exc()

if __name__ == "__main__":
    print("Running integration test...")
    test_full_workflow()
"""
    
    (base_dir / "test_db_connections.py").write_text(db_test)
    (base_dir / "test_storage.py").write_text(storage_test)
    (base_dir / "test_integration.py").write_text(integration_test)

    # Make scripts executable
    for script in ["test_db_connections.py", "test_storage.py", "test_integration.py"]:
        (base_dir / script).chmod(0o755)

    print("Created test scripts")

def main():
    """Set up the complete sandbox environment."""
    print("Setting up PDR Framework sandbox environment...")

    create_directory_structure()
    create_mock_executables()
    create_test_templates()
    setup_database_init_scripts()
    create_test_scripts()

    print("\nSandbox setup complete!")
    print("\nNext steps:")
    print("1. Start services: make start-services")
    print("   (or: cd sandbox && docker compose up -d)")
    print("2. Test database connections: python sandbox/test_db_connections.py")
    print("3. Test storage: python sandbox/test_storage.py")
    print("4. Run integration test: python sandbox/test_integration.py")
    print("\nNote: MySQL database and user are auto-created by Docker.")
    print("No manual database setup is required!")

if __name__ == "__main__":
    main()