#!/usr/bin/env python3
"""Helper script to run MySQL integration tests with proper setup."""

import os
import sys
import subprocess
import time
from pathlib import Path

def check_docker_mysql():
    """Check if MySQL is running in Docker."""
    try:
        result = subprocess.run(
            ["docker", "ps", "--filter", "name=pdr_mysql", "--format", "{{.Status}}"],
            capture_output=True, text=True, check=True
        )
        
        if "Up" in result.stdout:
            print("✓ MySQL container is running")
            return True
        else:
            print("✗ MySQL container is not running")
            return False
            
    except subprocess.CalledProcessError:
        print("✗ Failed to check Docker MySQL status")
        return False
    except FileNotFoundError:
        print("✗ Docker not found")
        return False

def start_mysql_container():
    """Start MySQL container using docker-compose."""
    try:
        print("Starting MySQL container...")
        result = subprocess.run(
            ["docker-compose", "up", "-d", "mysql"],
            capture_output=True, text=True, check=True,
            cwd=Path(__file__).parent.parent.parent / "sandbox"
        )
        
        print("Waiting for MySQL to be ready...")
        time.sleep(10)  # Give MySQL time to start
        
        # Check if it's ready
        for i in range(30):  # Try for 30 seconds
            try:
                result = subprocess.run([
                    "docker", "exec", "pdr_mysql", 
                    "mysql", "-u", "root", "-prootpassword", 
                    "-e", "SELECT 1"
                ], capture_output=True, text=True, timeout=5)
                
                if result.returncode == 0:
                    print("✓ MySQL is ready")
                    return True
                    
            except subprocess.TimeoutExpired:
                pass
                
            time.sleep(1)
        
        print("✗ MySQL failed to become ready")
        return False
        
    except subprocess.CalledProcessError as e:
        print(f"✗ Failed to start MySQL container: {e}")
        return False

def install_requirements():
    """Install required MySQL connector."""
    try:
        import mysql.connector
        print("✓ mysql-connector-python is available")
        return True
    except ImportError:
        print("Installing mysql-connector-python...")
        try:
            subprocess.run([
                sys.executable, "-m", "pip", "install", "mysql-connector-python"
            ], check=True)
            print("✓ mysql-connector-python installed")
            return True
        except subprocess.CalledProcessError:
            print("✗ Failed to install mysql-connector-python")
            return False

def main():
    """Main function to set up and run MySQL tests."""
    print("MySQL Integration Test Setup")
    print("=" * 40)
    
    # Check requirements
    if not install_requirements():
        sys.exit(1)
    
    # Check/start MySQL
    if not check_docker_mysql():
        if not start_mysql_container():
            print("\nFailed to start MySQL. Please ensure Docker is running and try:")
            print("cd sandbox && docker-compose up -d mysql")
            sys.exit(1)
    
    # Run the tests
    print("\n" + "=" * 40)
    print("Running MySQL Integration Tests")
    print("=" * 40)
    
    test_file = Path(__file__).parent / "test_mysql_integration.py"
    
    try:
        result = subprocess.run([sys.executable, str(test_file)], check=True)
        print("\n🎉 All MySQL integration tests passed!")
        
    except subprocess.CalledProcessError:
        print("\n❌ MySQL integration tests failed!")
        sys.exit(1)

if __name__ == "__main__":
    main()