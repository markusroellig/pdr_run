import unittest
import os
import tempfile
from unittest.mock import patch, MagicMock
from pdr_run.database.connection import init_db
from pdr_run.database.models import User, ModelNames
from pdr_run.database.queries import get_or_create
from pdr_run.database.db_manager import DatabaseManager


class TestDatabase(unittest.TestCase):
    def setUp(self):
        # Create a temporary database for testing
        self.temp_db = tempfile.NamedTemporaryFile(suffix='.db').name
        self.db_config = {
            'type': 'sqlite',
            'location': 'local',
            'path': self.temp_db
        }
        
        # Mock the DatabaseManager to prevent environment variable interference
        with patch.object(DatabaseManager, '_load_config') as mock_load_config:
            # Force the exact config we want, ignoring environment
            mock_load_config.return_value = self.db_config
            self.session, self.engine = init_db(self.db_config)
    
    def tearDown(self):
        # Clean up
        self.session.close()
        if hasattr(self.engine, 'dispose'):
            self.engine.dispose()
        if os.path.exists(self.temp_db):
            os.unlink(self.temp_db)
    
    def test_user_creation(self):
        user = get_or_create(
            self.session, 
            User, 
            username="test_user", 
            email="test@example.com"
        )
        self.session.flush()  # Ensure the entity is flushed to the database
        self.assertEqual(user.username, "test_user")
        
        # Test retrieving the same user
        user2 = get_or_create(
            self.session, 
            User, 
            username="test_user", 
            email="test@example.com"
        )
        self.session.flush()  # Ensure the entity is flushed to the database
        self.assertEqual(user.id, user2.id)
    
    def test_model_name_creation(self):
        model = get_or_create(
            self.session,
            ModelNames,
            model_name="test_model",
            model_path="/path/to/model"
        )
        self.session.flush()  # Ensure the entity is flushed to the database
        self.assertEqual(model.model_name, "test_model")
        
        # Retrieve the same model
        models = self.session.query(ModelNames).filter_by(model_name="test_model").all()
        self.assertEqual(len(models), 1)


if __name__ == '__main__':
    unittest.main()