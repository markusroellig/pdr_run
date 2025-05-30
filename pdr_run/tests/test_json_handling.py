"""Tests for JSON file handling in PDR framework."""

import os
import json
import tempfile
import unittest
import uuid
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
from pdr_run.database.models import JSONTemplate, PDRModelJob, ModelNames, KOSMAtauParameters, KOSMAtauExecutable

class TestJSONHandling(unittest.TestCase):
    """Test cases for JSON file handling."""
    
    def setUp(self):
        """Set up test environment."""
        # Create unique test database for each test instance
        self.test_db_path = f'test_db_{uuid.uuid4().hex[:8]}.sqlite'
        self.config = {
            'type': 'sqlite',
            'path': self.test_db_path
        }
        self.session, self.engine = init_db(self.config)
        
        # Create required foreign key records first
        # Create a model name (note: model_path is required)
        model_name = ModelNames(
            model_name="Test Model", 
            model_path="/test/path/model",  # This field is required
            model_description="Test model for testing"
        )
        self.session.add(model_name)
        self.session.flush()  # Get the ID without committing
        
        # Create KOSMATAU parameters (note: using model_name_id, not model_name)
        kosmatau_params = KOSMAtauParameters(
            model_name_id=model_name.id,  # Foreign key reference
            comments="Test parameters for testing"
        )
        self.session.add(kosmatau_params)
        self.session.flush()
        
        # Create KOSMATAU executable (note: correct class name is KOSMAtauExecutable)
        kosmatau_exec = KOSMAtauExecutable(
            executable_file_name="Test Executable",
            executable_full_path="/test/path/kosmatau",
            code_revision="1.0.0"
        )
        self.session.add(kosmatau_exec)
        self.session.flush()
        
        # Now commit all the prerequisite records
        self.session.commit()
        
        # Create a test template
        self.template_dir = tempfile.mkdtemp()
        self.template_path = os.path.join(self.template_dir, 'test_template.json')
        
        # Make template content unique for each test to avoid SHA256 conflicts
        unique_id = uuid.uuid4().hex[:8]
        self.template_data = {  # Add this attribute
            "model": {
                "name": "${model_name}",
                "unique_id": unique_id,  # Make each template unique
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
        
        # Create a test job with valid foreign key references
        self.job = PDRModelJob(
            model_job_name="test_job",
            model_name_id=model_name.id,
            kosmatau_parameters_id=kosmatau_params.id,
            kosmatau_executable_id=kosmatau_exec.id,
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
            name=f"Test Template {uuid.uuid4().hex[:8]}",  # Make name unique
            path=self.template_path,
            description="A test template"
        )
        
        self.assertIsNotNone(template)
        self.assertIn("Test Template", template.name)
        
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
        # First register template with unique name
        template = register_json_template(
            name=f"Test Template {uuid.uuid4().hex[:8]}",
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