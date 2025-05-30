"""Tests for SQLAlchemy database models."""

import pytest
import datetime
import tempfile
from unittest.mock import patch, MagicMock
from sqlalchemy import create_engine, text, event
from sqlalchemy.orm import sessionmaker
from sqlalchemy.exc import IntegrityError, OperationalError

from pdr_run.database.models import (
    Base, User, ModelNames, KOSMAtauExecutable, ChemicalDatabase,
    KOSMAtauParameters, PDRModelJob, HDFFile, JSONTemplate, JSONFile
)
from pdr_run.database.queries import get_or_create


class TestDatabaseModels:
    """Test SQLAlchemy model definitions and relationships."""

    @pytest.fixture(autouse=True)
    def setup_database(self):
        """Set up in-memory SQLite database for each test."""
        self.engine = create_engine("sqlite:///:memory:")
        Base.metadata.create_all(self.engine)
        Session = sessionmaker(bind=self.engine)
        self.session = Session()
        yield
        self.session.close()

    def test_user_model_creation(self):
        """Test User model creation and basic attributes."""
        user = User(
            username="test_user",
            email="test@example.com"
        )
        self.session.add(user)
        self.session.commit()

        # Verify user was created
        saved_user = self.session.query(User).filter_by(username="test_user").first()
        assert saved_user is not None
        assert saved_user.username == "test_user"
        assert saved_user.email == "test@example.com"
        assert saved_user.id is not None

    def test_user_model_validation(self):
        """Test User model field validation."""
        # Test required fields
        user = User()  # Missing required fields
        self.session.add(user)
        
        with pytest.raises(IntegrityError):
            self.session.commit()

    def test_model_names_creation(self):
        """Test ModelNames model creation."""
        model = ModelNames(
            model_name="test_model",
            model_path="/test/path",
            model_description="Test model description"
        )
        self.session.add(model)
        self.session.commit()

        saved_model = self.session.query(ModelNames).filter_by(model_name="test_model").first()
        assert saved_model is not None
        assert saved_model.model_name == "test_model"
        assert saved_model.model_path == "/test/path"
        assert saved_model.model_description == "Test model description"

    def test_kosmatau_executable_creation(self):
        """Test KOSMAtauExecutable model creation."""
        exe = KOSMAtauExecutable(
            code_revision="v1.0.0",
            compilation_date=datetime.datetime(2023, 1, 1, 12, 0, 0),
            executable_file_name="pdr_exe",
            executable_full_path="/path/to/exe",
            sha256_sum="abc123def456",
            comment="Test executable"
        )
        self.session.add(exe)
        self.session.commit()

        saved_exe = self.session.query(KOSMAtauExecutable).first()
        assert saved_exe is not None
        assert saved_exe.code_revision == "v1.0.0"
        assert saved_exe.executable_file_name == "pdr_exe"
        assert saved_exe.sha256_sum == "abc123def456"

    def test_chemical_database_creation(self):
        """Test ChemicalDatabase model creation."""
        chem_db = ChemicalDatabase(
            chem_rates_file_name="rates.dat",
            chem_rates_full_path="/path/to/rates.dat",
            database_origin="UDfA12"
        )
        self.session.add(chem_db)
        self.session.commit()

        saved_db = self.session.query(ChemicalDatabase).first()
        assert saved_db is not None
        assert saved_db.chem_rates_file_name == "rates.dat"
        assert saved_db.database_origin == "UDfA12"

    def test_kosmatau_parameters_creation(self):
        """Test KOSMAtauParameters model with comprehensive parameters."""
        # First create a model name (required foreign key)
        model_name = ModelNames(
            model_name="param_test_model",
            model_path="/test/path"
        )
        self.session.add(model_name)
        self.session.flush()

        params = KOSMAtauParameters(
            model_name_id=model_name.id,
            xnsur=1.0e3,
            mass=10.0,
            rtot=1.0e17,
            rcore=0.2,
            alpha=1.5,
            sigd=1.9e-21,
            sint=1.0,
            cosray=2.0e-16,
            beta=-1.0,
            zmetal=1.0,
            preshh2=0.0,
            preshco=0.0,
            species="CO H2 H",
            comments="Test parameters"
        )
        self.session.add(params)
        self.session.commit()

        saved_params = self.session.query(KOSMAtauParameters).first()
        assert saved_params is not None
        assert saved_params.xnsur == 1.0e3
        assert saved_params.mass == 10.0
        assert saved_params.zmetal == 1.0
        assert saved_params.species == "CO H2 H"

    def test_pdr_model_job_creation(self):
        """Test PDRModelJob model with all relationships."""
        # Create prerequisite objects
        user = User(username="job_user", email="job@example.com")
        self.session.add(user)
        self.session.flush()

        model_name = ModelNames(model_name="job_model", model_path="/job/path")
        self.session.add(model_name)
        self.session.flush()

        executable = KOSMAtauExecutable(
            executable_file_name="job_exe",
            executable_full_path="/job/exe/path",
            code_revision="v1.0"
        )
        self.session.add(executable)
        self.session.flush()

        chem_db = ChemicalDatabase(
            chem_rates_file_name="job_rates.dat",
            chem_rates_full_path="/job/rates.dat"
        )
        self.session.add(chem_db)
        self.session.flush()

        params = KOSMAtauParameters(
            model_name_id=model_name.id,
            xnsur=1000.0,
            mass=5.0
        )
        self.session.add(params)
        self.session.flush()

        # Create the job
        job = PDRModelJob(
            model_name_id=model_name.id,
            model_job_name="test_job_001",
            user_id=user.id,
            kosmatau_parameters_id=params.id,
            kosmatau_executable_id=executable.id,
            chemical_database_id=chem_db.id,
            output_directory="/output/path",
            output_hdf4_file="test.hdf",
            pending=True,
            status="pending",
            onion_species="CO H2 H"
        )
        self.session.add(job)
        self.session.commit()

        # Verify job and relationships
        saved_job = self.session.query(PDRModelJob).first()
        assert saved_job is not None
        assert saved_job.model_job_name == "test_job_001"
        assert saved_job.status == "pending"
        assert saved_job.pending is True
        
        # Test relationships
        assert saved_job.user.username == "job_user"
        assert saved_job.model_name.model_name == "job_model"
        assert saved_job.executable.executable_file_name == "job_exe"
        assert saved_job.chemical_database.chem_rates_file_name == "job_rates.dat"
        assert saved_job.parameters.xnsur == 1000.0

    def test_json_template_creation(self):
        """Test JSONTemplate model creation."""
        template = JSONTemplate(
            name="test_template",
            path="/path/to/template.json",
            description="Test JSON template",
            sha256_sum="def456abc789"
        )
        self.session.add(template)
        self.session.commit()

        saved_template = self.session.query(JSONTemplate).first()
        assert saved_template is not None
        assert saved_template.name == "test_template"
        assert saved_template.sha256_sum == "def456abc789"
        assert saved_template.created_at is not None

    def test_json_file_creation(self):
        """Test JSONFile model with template relationship."""
        # Create prerequisite job
        user = User(username="json_user", email="json@example.com")
        model_name = ModelNames(model_name="json_model", model_path="/json/path")
        executable = KOSMAtauExecutable(
            executable_file_name="json_exe",
            executable_full_path="/json/exe"
        )
        params = KOSMAtauParameters(model_name_id=1)  # Will be set properly after flush
        
        self.session.add_all([user, model_name, executable])
        self.session.flush()
        
        params.model_name_id = model_name.id
        self.session.add(params)
        self.session.flush()

        job = PDRModelJob(
            model_name_id=model_name.id,
            model_job_name="json_job",
            user_id=user.id,
            kosmatau_parameters_id=params.id,
            kosmatau_executable_id=executable.id
        )
        self.session.add(job)
        self.session.flush()

        # Create template
        template = JSONTemplate(
            name="json_template",
            path="/template.json",
            sha256_sum="template123"
        )
        self.session.add(template)
        self.session.flush()

        # Create JSON file
        json_file = JSONFile(
            name="config.json",
            path="/config.json",
            sha256_sum="json123",
            template_id=template.id,
            job_id=job.id
        )
        self.session.add(json_file)
        self.session.commit()

        # Verify relationships
        saved_file = self.session.query(JSONFile).first()
        assert saved_file is not None
        assert saved_file.name == "config.json"
        assert saved_file.template.name == "json_template"
        assert saved_file.job.model_job_name == "json_job"

    def test_hdf_file_creation(self):
        """Test HDFFile model creation."""
        # Create prerequisite objects (simplified for HDF test)
        model_name = ModelNames(model_name="hdf_model", model_path="/hdf/path")
        self.session.add(model_name)
        self.session.flush()

        hdf_file = HDFFile(
            job_id=1,  # Assuming job exists
            pdrexe_id=1,  # Assuming executable exists
            parameter_id=1,  # Assuming parameters exist
            model_name_id=model_name.id,
            file_name="test.hdf",
            full_path="/full/path/test.hdf",
            path="/full/path",
            modification_time=datetime.datetime.now(),
            sha256_sum="hdf123",
            file_size=1024,
            corrupt=False,
            # HDF5 structure file
            file_name_hdf5_s="test_struct.hdf5",
            full_path_hdf5_s="/full/path/test_struct.hdf5",
            # HDF5 chemistry file  
            file_name_hdf5_c="test_chem.hdf5",
            full_path_hdf5_c="/full/path/test_chem.hdf5"
        )
        self.session.add(hdf_file)
        self.session.commit()

        saved_hdf = self.session.query(HDFFile).first()
        assert saved_hdf is not None
        assert saved_hdf.file_name == "test.hdf"
        assert saved_hdf.file_size == 1024
        assert saved_hdf.corrupt is False


class TestModelRelationships:
    """Test model relationships and cascades."""

    @pytest.fixture(autouse=True)
    def setup_database(self):
        """Set up in-memory SQLite database for each test."""
        self.engine = create_engine("sqlite:///:memory:")
        Base.metadata.create_all(self.engine)
        Session = sessionmaker(bind=self.engine)
        self.session = Session()
        yield
        self.session.close()

    def test_user_job_relationship(self):
        """Test User to PDRModelJob relationship."""
        user = User(username="rel_user", email="rel@example.com")
        self.session.add(user)
        self.session.flush()

        # Create minimal job
        model_name = ModelNames(model_name="rel_model", model_path="/rel")
        self.session.add(model_name)
        self.session.flush()

        job = PDRModelJob(
            model_name_id=model_name.id,
            model_job_name="rel_job",
            user_id=user.id
        )
        self.session.add(job)
        self.session.commit()

        # Test relationship access
        assert len(user.jobs) == 1
        assert user.jobs[0].model_job_name == "rel_job"
        assert job.user.username == "rel_user"

    def test_model_name_relationships(self):
        """Test ModelNames relationships to other entities."""
        model_name = ModelNames(model_name="multi_rel_model", model_path="/multi")
        self.session.add(model_name)
        self.session.flush()

        # Create parameters linked to this model
        params = KOSMAtauParameters(
            model_name_id=model_name.id,
            xnsur=500.0
        )
        self.session.add(params)
        self.session.commit()

        # Test that parameters are accessible through model_name
        assert params.model_name.model_name == "multi_rel_model"


class TestModelValidationAndConstraints:
    """Test model validation and database constraints."""

    @pytest.fixture(autouse=True)
    def setup_database(self):
        """Set up in-memory SQLite database for each test."""
        self.engine = create_engine("sqlite:///:memory:")
        
        # Enable foreign key constraints for SQLite
        @event.listens_for(self.engine, "connect")
        def set_sqlite_pragma(dbapi_connection, connection_record):
            cursor = dbapi_connection.cursor()
            cursor.execute("PRAGMA foreign_keys=ON")
            cursor.close()
        
        Base.metadata.create_all(self.engine)
        Session = sessionmaker(bind=self.engine)
        self.session = Session()
        yield
        self.session.close()

    def test_unique_constraints(self):
        """Test unique constraints on models."""
        # Test JSONTemplate unique sha256_sum
        template1 = JSONTemplate(
            name="template1",
            path="/path1.json",
            sha256_sum="unique_hash_123"
        )
        template2 = JSONTemplate(
            name="template2", 
            path="/path2.json",
            sha256_sum="unique_hash_123"  # Same hash
        )
        
        self.session.add(template1)
        self.session.commit()
        
        self.session.add(template2)
        with pytest.raises(IntegrityError):
            self.session.commit()

    def test_foreign_key_constraints(self):
        """Test foreign key constraint enforcement."""
        # Try to create job with non-existent user_id
        job = PDRModelJob(
            model_name_id=999,  # Non-existent
            model_job_name="orphan_job", 
            user_id=999  # Non-existent
        )
        self.session.add(job)
        
        # SQLite raises OperationalError for foreign key violations
        with pytest.raises(OperationalError) as exc_info:
            self.session.commit()
        
        # Verify it's the expected foreign key error
        assert "FOREIGN KEY constraint failed" in str(exc_info.value)

    def test_datetime_defaults(self):
        """Test datetime field defaults."""
        template = JSONTemplate(
            name="datetime_test",
            path="/datetime.json",
            sha256_sum="datetime123"
        )
        self.session.add(template)
        self.session.commit()

        # created_at should be automatically set
        assert template.created_at is not None
        assert isinstance(template.created_at, datetime.datetime)

    def test_get_or_create_functionality(self):
        """Test the get_or_create query utility with models."""
        # First call should create
        user1 = get_or_create(
            self.session,
            User,
            username="get_create_user",
            email="get_create@example.com"
        )
        assert user1.id is not None
        
        # Second call should retrieve existing
        user2 = get_or_create(
            self.session,
            User,
            username="get_create_user",
            email="get_create@example.com"
        )
        
        # Should be the same object
        assert user1.id == user2.id
        assert user1.username == user2.username


class TestModelMethods:
    """Test any custom methods on models."""

    @pytest.fixture(autouse=True)
    def setup_database(self):
        """Set up in-memory SQLite database for each test."""
        self.engine = create_engine("sqlite:///:memory:")
        Base.metadata.create_all(self.engine)
        Session = sessionmaker(bind=self.engine)
        self.session = Session()
        yield
        self.session.close()

    def test_model_repr_methods(self):
        """Test string representations of models."""
        user = User(username="repr_user", email="repr@example.com")
        self.session.add(user)
        self.session.flush()
        
        repr_str = repr(user)
        assert "repr_user" in repr_str
        assert "User" in repr_str
        assert str(user.id) in repr_str

    def test_model_name_repr(self):
        """Test ModelNames repr method."""
        model = ModelNames(model_name="repr_model", model_path="/repr")
        self.session.add(model)
        self.session.flush()
        
        repr_str = repr(model)
        assert "repr_model" in repr_str
        assert "ModelNames" in repr_str

    def test_pdr_job_repr(self):
        """Test PDRModelJob repr method."""
        # Create minimal job for repr test
        user = User(username="job_repr_user", email="job_repr@example.com")
        model_name = ModelNames(model_name="job_repr_model", model_path="/job_repr")
        self.session.add_all([user, model_name])
        self.session.flush()

        job = PDRModelJob(
            model_name_id=model_name.id,
            model_job_name="repr_job_001",
            user_id=user.id,
            status="testing"
        )
        self.session.add(job)
        self.session.flush()
        
        repr_str = repr(job)
        assert "repr_job_001" in repr_str
        assert "testing" in repr_str
        assert "PDRModelJob" in repr_str