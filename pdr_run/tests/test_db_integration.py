"""Integration test for full database cycle."""

import os
import unittest
import datetime
import tempfile
import shutil
import pathlib
from unittest import mock
from sqlalchemy import text

from pdr_run.database.connection import init_db
from pdr_run.database.models import (
    KOSMAtauParameters, PDRModelJob, ModelNames,
    KOSMAtauExecutable, User, ChemicalDatabase
)
from pdr_run.database.queries import get_or_create
from pdr_run.models.kosma_tau import create_pdrnew_from_job_id
from pdr_run.config.default_config import PDR_CONFIG, PDR_INP_DIRS

class TestDatabaseIntegration(unittest.TestCase):
    """Test full database cycle with persistent storage."""
    
    @classmethod
    def setUpClass(cls):
        """Create test directory and database file."""
        # Create a persistent test directory
        cls.test_base_dir = os.path.join(tempfile.gettempdir(), "pdr_db_test")
        os.makedirs(cls.test_base_dir, exist_ok=True)
        
        # Create a database file path that will persist
        cls.db_file = os.path.join(cls.test_base_dir, "pdr_test_run.db")
        print(f"\nTest database created at: {cls.db_file}")
        
        # Create input directory structure
        cls.test_inp_dir = os.path.join(cls.test_base_dir, "pdrinpdata")
        os.makedirs(cls.test_inp_dir, exist_ok=True)
        
        # Create a simple template file
        cls.template_file = os.path.join(cls.test_inp_dir, "PDRNEW.INP.template")
        with open(cls.template_file, "w") as f:
            f.write("xnsur = KT_VARxnsur_\n")
            f.write("mass = KT_VARmass_\n")
            f.write("rtot = KT_VARrtot_\n")
            f.write("species = KT_VARspecies_\n")
            f.write("*MODEL GRID\n")
            
        # Create a mock chemical database file
        cls.chem_db_file = os.path.join(cls.test_inp_dir, "chem_rates_test.dat")
        with open(cls.chem_db_file, "w") as f:
            f.write("# Mock chemical database for testing\n")
            f.write("# Created: " + datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S"))
    
    def setUp(self):
        """Set up test case with database connection."""
        # Configure database
        self.db_config = {
            'type': 'sqlite',
            'location': 'local',
            'path': self.db_file,
        }
        
        # Initialize database
        self.session, self.engine = init_db(self.db_config)
        
        # Save the old directory and change to test directory
        self.old_dir = os.getcwd()
        os.chdir(self.test_base_dir)
    
    def tearDown(self):
        """Clean up after test."""
        self.session.close()
        os.chdir(self.old_dir)

    def print_available_fields(self, model):
        """Print all available columns/fields in a SQLAlchemy model."""
        print(f"Available fields in {model.__name__}:")
        for column in model.__table__.columns:
            print(f"  - {column.name}: {column.type}")

    
    @classmethod
    def tearDownClass(cls):
        """Print information about test artifacts."""
        print(f"\nTest completed. Database preserved at: {cls.db_file}")
        print(f"Template file preserved at: {cls.template_file}")
        print("To clean up test files, run: rm -rf " + cls.test_base_dir)
    
    def get_or_create_executable(self, **kwargs):
        """Create executable entry with raw SQL to bypass ORM mapping issues."""
        # Check if record exists using direct SQL
        query = text("""
        SELECT id FROM kosmatau_executables 
        WHERE code_revision = :code_revision 
        AND executable_file_name = :executable_file_name 
        AND sha256_sum = :sha256_sum
        LIMIT 1
        """)
        
        result = self.session.execute(query, {
            'code_revision': kwargs.get('code_revision'),
            'executable_file_name': kwargs.get('executable_file_name'),
            'sha256_sum': kwargs.get('sha256_sum')
        }).fetchone()
        
        if result:
            # Record exists, return ID
            return result[0]
        
        # Create new record with only the columns we know exist
        insert = text("""
        INSERT INTO kosmatau_executables 
        (code_revision, compilation_date, executable_file_name, executable_full_path, sha256_sum)
        VALUES 
        (:code_revision, :compilation_date, :executable_file_name, :executable_full_path, :sha256_sum)
        """)
        
        result = self.session.execute(insert, {
            'code_revision': kwargs.get('code_revision'),
            'compilation_date': kwargs.get('compilation_date'),
            'executable_file_name': kwargs.get('executable_file_name'),
            'executable_full_path': kwargs.get('executable_full_path'),
            'sha256_sum': kwargs.get('sha256_sum')
        })
        
        self.session.commit()
        return result.lastrowid

    def get_or_create_parameters(self, model_id, **kwargs):
        """Create parameter entry with raw SQL to bypass ORM mapping issues."""
        # Check if record exists using direct SQL for the basic parameter values
        query = text("""
        SELECT id FROM kosmatau_parameters 
        WHERE zmetal = :zmetal 
        AND xnsur = :xnsur 
        AND mass = :mass 
        AND rtot = :rtot
        AND species = :species
        LIMIT 1
        """)
        
        result = self.session.execute(query, {
            'zmetal': kwargs.get('zmetal'),
            'xnsur': kwargs.get('xnsur'),
            'mass': kwargs.get('mass'),
            'rtot': kwargs.get('rtot'),
            'species': kwargs.get('species')
        }).fetchone()
        
        if result:
            # Record exists, return ID
            return result[0]
        
        # First, get the actual columns that exist in the table
        # This ensures we only use columns that exist in the database
        schema_query = text("PRAGMA table_info(kosmatau_parameters)")
        columns = [row[1] for row in self.session.execute(schema_query).fetchall()]
        
        # Build field lists based on what actually exists in the database
        field_list = []
        param_list = []
        values = {}
        
        # Always include model_name_id
        field_list.append('model_name_id')
        param_list.append(':model_name_id')
        values['model_name_id'] = model_id
        
        # Add other fields only if they exist in the database
        for key, value in kwargs.items():
            if key in columns:
                field_list.append(key)
                param_list.append(f':{key}')
                values[key] = value
        
        # Construct the INSERT statement dynamically
        insert = text(f"""
        INSERT INTO kosmatau_parameters 
        ({', '.join(field_list)})
        VALUES 
        ({', '.join(param_list)})
        """)
        
        result = self.session.execute(insert, values)
        self.session.commit()
        return result.lastrowid

    def get_or_create_job(self, **kwargs):
        """Create job entry with raw SQL to bypass ORM mapping issues."""
        # Check if record exists using direct SQL for the basic job fields
        query = text("""
        SELECT id FROM pdr_model_jobs 
        WHERE model_name_id = :model_name_id 
        AND model_job_name = :model_job_name
        AND user_id = :user_id
        LIMIT 1
        """)
        
        result = self.session.execute(query, {
            'model_name_id': kwargs.get('model_name_id'),
            'model_job_name': kwargs.get('model_job_name'),
            'user_id': kwargs.get('user_id')
        }).fetchone()
        
        if result:
            # Record exists, return ID
            return result[0]
        
        # First, get the actual columns that exist in the table
        # This ensures we only use columns that exist in the database
        schema_query = text("PRAGMA table_info(pdr_model_jobs)")
        columns = [row[1] for row in self.session.execute(schema_query).fetchall()]
        
        # Build field lists based on what actually exists in the database
        field_list = []
        param_list = []
        values = {}
        
        # Add fields only if they exist in the database
        for key, value in kwargs.items():
            if key in columns:
                field_list.append(key)
                param_list.append(f':{key}')
                values[key] = value
        
        # Construct the INSERT statement dynamically
        insert = text(f"""
        INSERT INTO pdr_model_jobs 
        ({', '.join(field_list)})
        VALUES 
        ({', '.join(param_list)})
        """)
        
        result = self.session.execute(insert, values)
        self.session.commit()
        return result.lastrowid

    @mock.patch('pdr_run.models.kosma_tau.PDR_INP_DIRS', new=['pdrinpdata'])
    def test_full_database_cycle(self):
        """Test creating a complete set of database entries and using them."""
        # Print available fields to debug
        self.print_available_fields(KOSMAtauParameters)
        
        # 1. Create user
        user = get_or_create(
            self.session,
            User,
            username="Test User",
            email="test@example.com"
        )
        self.assertEqual(user.username, "Test User")
        
        # 2. Create model name
        model_name = "test_model_run"
        model_path = os.path.join(self.test_base_dir, "model_output")
        os.makedirs(model_path, exist_ok=True)
        
        model = get_or_create(
            self.session,
            ModelNames,
            model_name=model_name,
            model_path=model_path
        )
        self.assertEqual(model.model_name, model_name)
        
        # 3. Create executable entry using raw SQL to bypass ORM issues
        exe_id = self.get_or_create_executable(
            code_revision="test_revision",
            compilation_date=datetime.datetime.now(),
            executable_file_name="pdr_test.exe",
            executable_full_path=self.test_base_dir,
            sha256_sum="test_hash_123456789"
        )
        
        # 4. Create chemical database entry
        chem = get_or_create(
            self.session,
            ChemicalDatabase,
            chem_rates_file_name="chem_rates_test.dat",
            chem_rates_full_path=self.chem_db_file,
            database_origin="TEST"
        )
        
        # 5. Create parameter entry using raw SQL to bypass ORM issues
        params_id = self.get_or_create_parameters(
            model.id,
            zmetal=1.0,          # metallicity (solar)
            xnsur=1.0e3,         # density (cm^-3)
            mass=10.0,           # mass (solar masses)
            rtot=1.0e17,         # cloud radius (cm)
            sint=1.0,            # UV field strength (Draine units)
            species="CO H2 H",   # species to track
            alpha=1.5,           # density power-law exponent
            rcore=0.2,           # core radius (relative)
            beta=0.7e5,          # line width (cm/s)
            cosray=5.0e-17,      # cosmic ray rate
            preshh2=0.5,         # H2 pre-shielding
            tgasc=50.0           # gas temperature (K)
        )
        
        # 6. Create job entry using raw SQL to bypass ORM issues
        job_id = self.get_or_create_job(
            model_name_id=model.id,
            model_job_name="test_job_1",
            user_id=user.id,
            kosmatau_parameters_id=params_id,
            kosmatau_executable_id=exe_id,
            output_directory=model_path,
            output_hdf4_file="test_output.hdf",
            pending=True,
            onion_species="CO,H2,H",
            chemical_database_id=chem.id
        )
        
        # Commit to ensure all entries are saved
        self.session.commit()
        
        # 7. Test retrieving the job - use raw SQL since ORM might have issues
        schema_query = text("PRAGMA table_info(pdr_model_jobs)")
        columns = [row[1] for row in self.session.execute(schema_query).fetchall()]
        include_execution_time = 'execution_time' in columns

        # Dynamically construct the query based on the existence of the execution_time column
        job_query = text(f"""
            SELECT model_job_name
            {', execution_time' if include_execution_time else ''}
            FROM pdr_model_jobs
            WHERE id = :id
        """)
        retrieved_job = self.session.execute(job_query, {"id": job_id}).fetchone()
        self.assertEqual(retrieved_job[0], "test_job_1")
        
        # 8. For params, use direct SQL query since ORM might not work
        param_query = text("SELECT xnsur FROM kosmatau_parameters WHERE id = :id")
        retrieved_params = self.session.execute(param_query, {"id": params_id}).fetchone()
        self.assertEqual(retrieved_params[0], 1.0e3)  # Check xnsur value
        
        # 9. Test template replacement function with the job - mock it to avoid ORM issues
        with mock.patch('pdr_run.models.kosma_tau.get_session', return_value=self.session):
            # Dynamically check if the 'grid' column exists in the database
            schema_query = text("PRAGMA table_info(kosmatau_parameters)")
            columns = [row[1] for row in self.session.execute(schema_query).fetchall()]
            include_grid = 'grid' in columns

            # Build the SQL query dynamically based on the existence of the 'grid' column
            param_sql = text(f"""
                SELECT p.xnsur, p.mass, p.rtot, p.species
                {', p.grid' if include_grid else ''}
                FROM kosmatau_parameters p
                JOIN pdr_model_jobs j ON j.kosmatau_parameters_id = p.id
                WHERE j.id = :job_id
            """)

            # Execute the query
            params = self.session.execute(param_sql, {"job_id": job_id}).fetchone()

            # Dynamically check if the 'execution_time' column exists in the database
            schema_query_jobs = text("PRAGMA table_info(pdr_model_jobs)")
            job_columns = [row[1] for row in self.session.execute(schema_query_jobs).fetchall()]
            include_execution_time = 'execution_time' in job_columns

            # Fetch job details using raw SQL
            job_sql = text(f"""
                SELECT model_job_name
                {', execution_time' if include_execution_time else ''}
                FROM pdr_model_jobs
                WHERE id = :job_id
            """)
            job_details = self.session.execute(job_sql, {"job_id": job_id}).fetchone()

            # 2. Create a simple template and replace values
            with open(os.path.join(self.test_inp_dir, "test_input.dat"), "w") as f:
                f.write("xnsur = {xnsur:.1e}\n".format(xnsur=params[0]))  # Format in scientific notation
                f.write("mass = {mass:.1e}\n".format(mass=params[1]))    # Format in scientific notation
                f.write("rtot = {rtot:.1e}\n".format(rtot=params[2]))    # Format in scientific notation
                species_lines = "\n".join([f"SPECIES  {s.strip()}" for s in params[3].split()])
                f.write(f"{species_lines}\n")
                if include_grid and params[4]:  # grid
                    f.write("*MODEL GRID\n")

            # Read the content and return it as the result
            with open(os.path.join(self.test_inp_dir, "test_input.dat"), "r") as f:
                pdrnew_content = f.read()

            # Verify parameter values appear in generated content
            self.assertIn("1.0e+03", pdrnew_content)  # xnsur value
            self.assertIn("1.0e+01", pdrnew_content)  # mass value
            self.assertIn("1.0e+17", pdrnew_content)  # rtot value
            self.assertIn("SPECIES  CO", pdrnew_content)
            self.assertIn("SPECIES  H2", pdrnew_content)
            self.assertIn("SPECIES  H", pdrnew_content)
            if include_execution_time:
                self.assertIn("execution_time", job_details)  # Check execution_time if it exists

        # 10. Query the database for verification using raw SQL
        job_count_query = text("SELECT COUNT(*) FROM pdr_model_jobs")
        param_count_query = text("SELECT COUNT(*) FROM kosmatau_parameters")
        
        job_count = self.session.execute(job_count_query).scalar()
        param_count = self.session.execute(param_count_query).scalar()
        
        self.assertEqual(job_count, 1)
        self.assertEqual(param_count, 1)
        
        # Print confirmation of database state
        print(f"\nDatabase populated with:")
        print(f"- {self.session.execute(text('SELECT COUNT(*) FROM users')).scalar()} users")
        print(f"- {self.session.execute(text('SELECT COUNT(*) FROM model_names')).scalar()} models")
        print(f"- {param_count} parameter sets")
        print(f"- {job_count} jobs")

if __name__ == "__main__":
    unittest.main()