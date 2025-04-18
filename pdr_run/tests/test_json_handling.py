"""Tests for JSON file handling in PDR framework."""

import os
import json
import tempfile
import unittest
from pathlib import Path

from pdr_run.database.json_handlers import (
    load_json_template,
    apply_parameters_to_json,
    save_json_config,
    get_json_hash,
    register_json_template,process_json_template,
    prepare_job_json, validate_json
    # other functions...
)
from pdr_run.workflow.json_workflow import (
    prepare_json_config,
    archive_json_output
)
from pdr_run.database.connection import (
    init_db
    # , register_json_template, process_json_template, 
    # prepare_job_json, validate_json
)
from pdr_run.database.models import JSONTemplate, PDRModelJob

class TestJSONHandling(unittest.TestCase):
    """Test cases for JSON file handling."""
    
    def setUp(self):
        """Set up test environment."""
        # Initialize test database
        self.test_db_path = 'test_db.sqlite'
        self.config = {
            'type': 'sqlite',
            'path': self.test_db_path
        }
        self.session, self.engine = init_db(self.config)
        
        # Create a test template
        self.template_dir = tempfile.mkdtemp()
        self.template_path = os.path.join(self.template_dir, 'test_template.json')
        
        self.template_data = {  # Add this attribute
            "model": {
                "name": "${model_name}",
                "parameters": {
                    "density": "${density}",
                    "temperature": "${temperature}",
                    "radiation_field": "${radiation_field}"
                }
            },
            "outputs": {
                "species": ["CO", "C", "C+"]
            }
        }
        
        with open(self.template_path, 'w') as f:
            json.dump(self.template_data, f, indent=2)
        
        # Create a test job
        self.job = PDRModelJob(
            model_job_name="test_job",
            model_name_id=1,
            kosmatau_parameters_id=1,
            kosmatau_executable_id=1,
            status="pending"
        )
        self.session.add(self.job)
        self.session.commit()
    
    def tearDown(self):
        """Clean up after tests."""
        # Clean up test database
        self.session.close()
        if os.path.exists(self.test_db_path):
            os.remove(self.test_db_path)
        
        # Clean up template directory
        if os.path.exists(self.template_dir):
            import shutil
            shutil.rmtree(self.template_dir)
    
    def test_load_template(self):
        """Test loading a JSON template."""
        data = load_json_template(self.template_path)
        self.assertEqual(data["model"]["name"], "${model_name}")
        self.assertEqual(len(data["outputs"]["species"]), 3)
    
    def test_apply_parameters(self):
        """Test applying parameters to JSON template."""
        params = {
            "model_name": "test_model",
            "density": 1000,
            "temperature": 100,
            "radiation_field": 1.0
        }
        
        data = load_json_template(self.template_path)
        modified = apply_parameters_to_json(data, params)
        
        self.assertEqual(modified["model"]["name"], "test_model")
        self.assertEqual(modified["model"]["parameters"]["density"], 1000)
    
    def test_save_config(self):
        """Test saving JSON configuration."""
        output_path = os.path.join(self.template_dir, "output.json")
        save_json_config(self.template_data, output_path)
        
        self.assertTrue(os.path.exists(output_path))
        with open(output_path, 'r') as f:
            saved_data = json.load(f)
        
        self.assertEqual(saved_data["model"]["name"], "${model_name}")
    
    def test_json_hash(self):
        """Test calculating JSON file hash."""
        hash1 = get_json_hash(self.template_path)
        
        # Create a different file
        different_path = os.path.join(self.template_dir, "different.json")
        different_data = self.template_data.copy()
        different_data["model"]["name"] = "different_model"
        with open(different_path, 'w') as f:
            json.dump(different_data, f)
        
        hash2 = get_json_hash(different_path)
        
        self.assertNotEqual(hash1, hash2)
    
    def test_workflow_integration(self):
        """Test JSON workflow integration."""
        # Mock job ID
        job_id = 1
        
        # Prepare parameters
        params = {
            "model_name": "test_model",
            "density": 1000,
            "temperature": 100,
            "radiation_field": 1.0
        }
        
        # Prepare JSON config
        config_path = prepare_json_config(job_id, self.template_path, params, self.template_dir)
        self.assertTrue(os.path.exists(config_path))
        
        # Create a mock output file
        output_filename = "results.json"
        output_path = os.path.join(self.template_dir, output_filename)
        output_data = {"results": {"success": True, "values": [1, 2, 3]}}
        with open(output_path, 'w') as f:
            json.dump(output_data, f)
        
        # Archive output
        archive_dir = os.path.join(self.template_dir, "archive")
        archived_path = archive_json_output(job_id, self.template_dir, output_filename, archive_dir)
        
        self.assertTrue(os.path.exists(archived_path))
    
    def test_register_template(self):
        template = register_json_template(
            name="Test Template",
            path=self.template_path,
            description="A test template"
        )
        
        self.assertIsNotNone(template)
        self.assertEqual(template.name, "Test Template")
        
    def test_process_template(self):
        params = {
            "model_name": "test_model",
            "density": 1000,
            "temperature": 100,
            "radiation_field": 1.0
        }
        
        output_path = os.path.join(self.template_dir, "processed.json")
        processed, path = process_json_template(
            self.template_path, params, output_path
        )
        
        self.assertTrue(os.path.exists(path))
        self.assertEqual(processed["model"]["name"], "test_model")
        self.assertEqual(processed["model"]["parameters"]["density"], 1000)
        
    def test_prepare_job_json(self):
        # First register template
        template = register_json_template(
            name="Test Template",
            path=self.template_path
        )
        
        # Prepare job JSON
        params = {
            "model_name": "test_model",
            "density": 1000,
            "temperature": 100,
            "radiation_field": 1.0
        }
        
        job_json_path = prepare_job_json(
            job_id=self.job.id,
            template_id=template.id,
            parameters=params
        )
        
        self.assertTrue(os.path.exists(job_json_path))
        
        # Validate the content
        with open(job_json_path, 'r') as f:
            content = json.load(f)
            
        self.assertEqual(content["model"]["name"], "test_model")
        
    def test_validate_json(self):
        # Create a valid JSON file
        valid_json_path = os.path.join(self.template_dir, "valid.json")
        with open(valid_json_path, 'w') as f:
            json.dump({"test": "data"}, f)
            
        # Create an invalid JSON file
        invalid_json_path = os.path.join(self.template_dir, "invalid.json")
        with open(invalid_json_path, 'w') as f:
            f.write('{"test": "data"')  # Missing closing bracket
            
        self.assertTrue(validate_json(valid_json_path))
        self.assertFalse(validate_json(invalid_json_path))

if __name__ == "__main__":
    unittest.main()