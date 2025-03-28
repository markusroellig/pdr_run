#!/usr/bin/env python3
"""Simple script to run specific tests in the PDR framework."""

import os
import sys
import unittest
import argparse


def main():
    """Run tests based on command line arguments."""
    parser = argparse.ArgumentParser(description="Run PDR framework tests")
    parser.add_argument("test_name", nargs="?", default=None,
                        help="Specific test to run (e.g., 'tests.test_template_replacement')")
    parser.add_argument("--real-template", action="store_true",
                        help="Run test_real_template_pdrnew_creation test")
    parser.add_argument("--list", action="store_true",
                        help="List available test modules")
    
    args = parser.parse_args()
    
    # Add the parent directory to the path so we can import pdr_run
    sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    
    if args.list:
        print("Available test modules:")
        test_dir = os.path.join(os.path.dirname(__file__), "tests")
        for file in os.listdir(test_dir):
            if file.startswith("test_") and file.endswith(".py"):
                print(f"  - tests.{file[:-3]}")
        return
        
    if args.real_template:
        # Directly import and run the specific test
        from pdr_run.tests.test_template_replacement import TestTemplateReplacement
        suite = unittest.TestSuite()
        suite.addTest(TestTemplateReplacement("test_real_template_pdrnew_creation"))
        runner = unittest.TextTestRunner(verbosity=2)
        result = runner.run(suite)
        sys.exit(0 if result.wasSuccessful() else 1)
        
    if args.test_name:
        # Run a specific test module or test
        test_path = f"pdr_run.{args.test_name}"
        suite = unittest.defaultTestLoader.loadTestsFromName(test_path)
        runner = unittest.TextTestRunner(verbosity=2)
        result = runner.run(suite)
        sys.exit(0 if result.wasSuccessful() else 1)
    else:
        # Run all tests
        suite = unittest.defaultTestLoader.discover("pdr_run.tests")
        runner = unittest.TextTestRunner(verbosity=2)
        result = runner.run(suite)
        sys.exit(0 if result.wasSuccessful() else 1)


if __name__ == "__main__":
    main()
