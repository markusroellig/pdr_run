"""SQLAlchemy models for the PDR framework."""

import datetime
from sqlalchemy import Column, Integer, String, Float, Boolean, DateTime, ForeignKey, Text, Interval
from sqlalchemy.sql import func
from sqlalchemy.orm import relationship

from pdr_run.database.base import Base

class ModelNames(Base):
    """Model names table."""
    
    __tablename__ = 'model_names'
    
    id = Column(Integer, primary_key=True)
    model_name = Column(String(255), nullable=False)
    model_path = Column(String(255), nullable=False)
    model_description = Column(String(1000))
    
    def __repr__(self):
        return f"<ModelNames(id={self.id}, model_name='{self.model_name}')>"

class User(Base):
    """User table."""
    
    __tablename__ = 'users'
    
    id = Column(Integer, primary_key=True)
    username = Column(String(255), nullable=False)
    email = Column(String(255), nullable=False)
    
    jobs = relationship("PDRModelJob", back_populates="user")
    
    def __repr__(self):
        return f"<User(id={self.id}, username='{self.username}')>"

class KOSMAtauExecutable(Base):
    """KOSMA-tau executable table."""
    
    __tablename__ = 'kosmatau_executables'
    
    id = Column(Integer, primary_key=True)
    code_revision = Column(String(50))
    compilation_date = Column(DateTime)
    executable_file_name = Column(String(255), nullable=False)
    executable_full_path = Column(String(255))
    sha256_sum = Column(String(64))
    comment = Column(String(250), default=None)
    
    jobs = relationship("PDRModelJob", back_populates="executable")
    
    def __repr__(self):
        return f"<KOSMAtauExecutable(id={self.id}, file_name='{self.executable_file_name}')>"

class ChemicalDatabase(Base):
    """Chemical database table."""
    
    __tablename__ = 'chemical_databases'
    
    id = Column(Integer, primary_key=True)
    chem_rates_file_name = Column(String(255), nullable=False)
    chem_rates_full_path = Column(String(255))
    database_origin = Column(String(50))
    
    jobs = relationship("PDRModelJob", back_populates="chemical_database")
    
    def __repr__(self):
        return f"<ChemicalDatabase(id={self.id}, file_name='{self.chem_rates_file_name}')>"

class KOSMAtauParameters(Base):
    """KOSMA-tau parameters table."""
    
    __tablename__ = 'kosmatau_parameters'
    
    id = Column(Integer, primary_key=True)
    model_name_id = Column(Integer, ForeignKey('model_names.id'))
    time_created = Column(DateTime(timezone=True), server_default=func.now())
    xnsur  = Column(Float, default=None)  # surface density (cm^⁻3)
    mass   = Column(Float, default=None)  # clump mass (Msol)
    rtot   = Column(Float, default=None)  # clump radius (cm)
    rcore  = Column(Float, default=0.2)  # core radius fraction (default =0.2)
    alpha  = Column(Float, default=1.5)  # power law index of density law
    sigd   = Column(Float, default=1.9e-21)  # dust UV cross section (cm^2)
    sint   = Column(Float, default=None)  #FUV radiation field strength
    cosray = Column(Float, default=2.0e-16)  # cosmic ray ionization rate (s^-1)
    beta   = Column(Float, default=-1.0e0)  # Doppler line width 7.123e4 = 1 km/s FWHM, neg = Larson
    zmetal = Column(Float, default=1.0)  #
    preshh2  = Column(Float, default=0.0)  #
    preshco  = Column(Float, default=0.0)  #
    ifuvmeth  = Column(Integer, default=1) #
    idustmet  = Column(Integer, default=1) #
    ifuvtype  = Column(Integer, default=2) #
    fuvtemp   = Column(Float, default=2.0e4)
    fuvstring = Column(String,default='Lyalpha.fuv')
    inewgam  = Column(Integer, default=0) #
    iscatter  = Column(Integer, default=-1) #
    ihtclgas = Column(Integer, default=1) # compute Tgas?: default=1
    tgasc =Column(Float, default=50.0)    # constant gas temp (K)
    ihtcldust  = Column(Integer, default=1) #
    tdustc  = Column(Float, default=20.0) #
    ipehmeth  = Column(Integer, default=1) #
    indxpeh  = Column(Integer, default=4) #
    ihtclpah  = Column(Integer, default=1) #
    indstr = Column(Integer, default=1) #
    inds = Column(Integer, default=4) #
    indx  = Column(Integer, default=7) #
    d2gratio1= Column(Float, default=8.27485e-3) #
    d2gratio2= Column(Float, default=2.27746e-3)  #
    d2gratio3= Column(Float, default=3.75578e-4)  #
    d2gratio4= Column(Float, default=3.75578e-4)  #
    d2gratio5= Column(Float, default=0.0)  #
    d2gratio6= Column(Float, default=0.0)  #
    d2gratio7= Column(Float, default=0.0)  #
    d2gratio8= Column(Float, default=0.0)  #
    d2gratio9= Column(Float, default=0.0)  #
    d2gratio10= Column(Float, default=0.0)  #
    ih2meth  = Column(Integer, default=1) #
    ih2onpah = Column(Integer, default=0) #
    h2formc= Column(Float, default=2.121e-17)  #
    ih2shld  = Column(Integer, default=0) #
    h2_structure = Column(Integer, default=0) #
    h2_h_coll_rates  = Column(Integer, default=3) #
    h2_h_reactive_colls  = Column(Integer, default=1) #
    h2_use_gbar = Column(Integer, default=1) #
    h2_quad_a = Column(Integer, default=1) #
    ## Surface chemistry
    ifh2des = Column(Integer, default=1) #
    ifcrdes = Column(Integer, default=2) #
    ifphdes = Column(Integer, default=1) #
    ifthdes = Column(Integer, default=1) #
    bindsites= Column(Float, default=1.5e15)  #
    ifchemheat = Column(Integer, default=0) #
    ifheat_alfven = Column(Integer, default=0) #
    alfven_velocity = Column(Float, default=3.3e5)  #
    alfven_column = Column(Float, default=4.0e20)  #
    ## numerical parameters
    temp_start = Column(Float, default=0.0)  #
    itmeth = Column(Integer, default=2) #
    ichemeth = Column(Integer, default=0) #
    inewtonstep = Column(Integer, default=1) #
    omega_neg = Column(Float, default=5.0e0) #
    omega_pos = Column(Float, default=2.0e0) #
    lambda_newt = Column(Float ,default=0.5e0) #
    use_conservation = Column(Integer, default=1) #
    rescaleQF = Column(Integer, default=0) #
    precondLR = Column(Integer, default=0) #
    resortQF = Column(Integer, default=0) #
    nconv_time = Column(Integer, default=0)  #
    time_dependent = Column(Integer, default=0) #
    use_dlsodes = Column(Integer, default=1) #
    use_dlsoda = Column(Integer, default=0) #
    use_dvodpk = Column(Integer, default=0) #
    first_time_step_yrs = Column(Float, default=1.0e-10)  #
    max_time_yrs = Column(Float, default=1.0e7)  #
    num_time_steps = Column(Integer, default=0) #
    rtol_chem = Column(Float, default=1.0e-2)  #
    atol_chem = Column(Float, default=1.0e-10)  #
    Xhtry = Column(Float, default=1.0e6)  #
    Niter = Column(Integer, default=60) #
    rtol_iter = Column(Float, default=3.0e-2)  #
    step1 =Column(Float, default=100.0)  #
    step2 =Column(Float, default=200.0)  #
    ihdfout = Column(Integer, default=4) #
    dbglvl = Column(Integer, default=0) #
    grid  = Column(Integer, default=0) #
    elfrac4 = Column(Float, default=8.51e-2)   # He
    elfrac12 = Column(Float, default=2.34e-4)  # 12C
    elfrac13 = Column(Float, default=3.52e-6)  # 13C
    elfrac14 = Column(Float, default=8.32e-5)  # N
    elfrac16 = Column(Float, default=4.47e-4)  # 16O
    elfrac18 = Column(Float, default=8.93e-7)  # 18O
    elfrac19 = Column(Float, default=6.68e-9)  # Fl
    elfrac20 = Column(Float, default=6.9e-5)   # Ne
    elfrac23 = Column(Float, default=2.0e-7)   # Na
    elfrac24 = Column(Float, default=3.2e-6)   # Mg
    elfrac27 = Column(Float, default=2.8e-6)   # Al
    elfrac28 = Column(Float, default=3.17e-6)  # Si
    elfrac31 = Column(Float, default=1.17e-7)  # P
    elfrac32 = Column(Float, default=7.41e-6)  # S
    elfrac35 = Column(Float, default=1.0e-7)   # Cl
    elfrac39 = Column(Float, default=1.35e-7)   # K
    elfrac40 = Column(Float, default=3.29e-6)   # Ar
    elfrac41 = Column(Float, default=2.2e-6)   # Ca
    elfrac56 = Column(Float, default=1.0e-6)   # Fe
    species=Column(Text,default = "H+ H2+ H3+ HE+ HE C CH CO C+ CH+ CH2+ CO+ HCO+ 13C 13CH 13CO 13C+ 13CH+ 13CH2+"\
                                  +" 13CO+ H13CO+ O OH H2O O2 O+ OH+ H2O+ H3O+ S S+ HS HS+ H2S+ CS HCS+ SO SO+"\
                                  +" CH2 CH3+")        # long string containing all species separated by whitespace
    comments = Column(Text,default = None)
    
    model_name = relationship("ModelNames")
    jobs = relationship("PDRModelJob", back_populates="parameters")
    
    def __repr__(self):
        return f"<KOSMAtauParameters(id={self.id}, mass={self.mass})>"

class PDRModelJob(Base):
    """Model for PDR model jobs."""
    
    __tablename__ = "pdr_model_jobs"
    
    id = Column(Integer, primary_key=True)
    model_name_id = Column(Integer, ForeignKey('model_names.id'))
    model_job_name = Column(String(255), nullable=False)
    user_id = Column(Integer, ForeignKey('users.id'))
    kosmatau_parameters_id = Column(Integer, ForeignKey('kosmatau_parameters.id'))
    kosmatau_executable_id = Column(Integer, ForeignKey('kosmatau_executables.id'))
    chemical_database_id = Column(Integer, ForeignKey('chemical_databases.id'))
    chemical_database = relationship("ChemicalDatabase", back_populates="pdrmodel_jobs")
    onion_species=Column(Text,default = None)

    # Job status
    pending = Column(Boolean, default=True)
    active = Column(Boolean, default=False)
    status = Column(String(50), default='pending')
    time_of_start = Column(DateTime)
    time_of_finish = Column(DateTime)
    execution_time =  Column(Interval, nullable=True, default=None)
    token = Column(String(250), nullable=True)
    
    # Output files
    output_directory = Column(String(255))
    output_hdf4_file = Column(String(255))
    output_textout_file = Column(String(255))
    output_chemchk_file = Column(String(255))
    output_mcdrt_zip_file = Column(String(255))
    output_ctrl_ind_file = Column(String(255))
    output_hdf5_file = Column(String(250), nullable=True)
    input_pdrnew_inp_file = Column(String(255))
    input_json_file = Column(String(512))
    output_json_file = Column(String(512))
    log_file = Column(String(255))
    onion_species = Column(String(255))
    time_created = Column(DateTime(timezone=True), server_default=func.now())
    time_updated = Column(DateTime(timezone=True), onupdate=func.now())

    
    # Relationships
    model_name = relationship("ModelNames")
    user = relationship("User", back_populates="jobs")
    parameters = relationship("KOSMAtauParameters", back_populates="jobs")
    executable = relationship("KOSMAtauExecutable", back_populates="jobs")
    chemical_database = relationship("ChemicalDatabase", back_populates="jobs")
    hdf_files = relationship("HDFFile", back_populates="job")
    json_files = relationship("JSONFile", back_populates="job")
    
    def __repr__(self):
        return f"<PDRModelJob(id={self.id}, name='{self.model_job_name}', status='{self.status}')>"

class HDFFile(Base):
    """HDF file table."""
    
    __tablename__ = 'hdf_files'
    
    id = Column(Integer, primary_key=True)
    job_id = Column(Integer, ForeignKey('pdr_model_jobs.id'))
    pdrexe_id = Column(Integer, ForeignKey('kosmatau_executables.id'))
    parameter_id = Column(Integer, ForeignKey('kosmatau_parameters.id'))
    model_name_id = Column(Integer, ForeignKey('model_names.id'))
    
    file_name = Column(String(255), nullable=False)
    full_path = Column(String(255))
    path = Column(String(255))
    modification_time = Column(DateTime)
    sha256_sum = Column(String(64))
    file_size = Column(Integer)
    corrupt = Column(Boolean, default=False)
    comments = Column(Text,default = None)

    file_name_hdf5_s = Column(String(255), nullable=False)
    full_path_hdf5_s = Column(String(255))
    path_hdf5_s = Column(String(255))
    modification_time_hdf5_s = Column(DateTime)
    sha256_sum_hdf5_s = Column(String(64))
    file_size_hdf5_s = Column(Integer)

    file_name_hdf5_c = Column(String(255), nullable=False)
    full_path_hdf5_c = Column(String(255))
    path_hdf5_c = Column(String(255))
    modification_time_hdf5_c = Column(DateTime)
    sha256_sum_hdf5_c = Column(String(64))
    file_size_hdf5_c = Column(Integer)
    
    job = relationship("PDRModelJob", back_populates="hdf_files")
    
    def __repr__(self):
        return f"<HDFFile(id={self.id}, file_name='{self.file_name}')>"

class JSONTemplate(Base):
    """Model for storing JSON template files."""
    
    __tablename__ = "json_templates"
    
    id = Column(Integer, primary_key=True)
    name = Column(String(255), nullable=False)
    path = Column(String(512), nullable=False)
    description = Column(Text)
    hash = Column(String(64))  # Add this column
    sha256_sum = Column(String(64), unique=True)
    created_at = Column(DateTime, default=datetime.datetime.utcnow)
    
    # Relationships
    instances = relationship("JSONFile", back_populates="template")
    
    def __repr__(self):
        return f"<JSONTemplate(name='{self.name}', path='{self.path}')>"

class JSONFile(Base):
    """Model for storing JSON configuration files."""
    
    __tablename__ = "json_files"
    
    id = Column(Integer, primary_key=True)
    name = Column(String(255), nullable=False)
    path = Column(String(512), nullable=False)
    archived_path = Column(String(512))  # Add this column for archived file path
    sha256_sum = Column(String(64), unique=True)
    created_at = Column(DateTime, default=datetime.datetime.utcnow)
    
    # Foreign keys
    template_id = Column(Integer, ForeignKey("json_templates.id"))
    job_id = Column(Integer, ForeignKey("pdr_model_jobs.id"))
    
    # Relationships
    template = relationship("JSONTemplate", back_populates="instances")
    job = relationship("PDRModelJob", back_populates="json_files")
    
    def __repr__(self):
        return f"<JSONFile(name='{self.name}', path='{self.path}')>"

class ModelRun:
    """Model run data class for direct SQL operations.
    
    Note: Consider migrating to SQLAlchemy models for consistency.
    """
    
    def __init__(self, name, parameters, status="pending", runtime_seconds=None):
        self.name = name
        self.parameters = parameters
        self.status = status
        self.runtime_seconds = runtime_seconds
    
    def save(self, conn):
        """Save model run to database."""
        cursor = conn.cursor()
        
        # Insert into model_runs
        cursor.execute(
            "INSERT INTO model_runs (name, status, runtime_seconds) VALUES (?, ?, ?)",
            (self.name, self.status, self.runtime_seconds)
        )
        run_id = cursor.lastrowid
        
        # Insert parameters
        for name, value in self.parameters.items():
            cursor.execute(
                "INSERT INTO model_results (run_id, parameter_name, parameter_value) VALUES (?, ?, ?)",
                (run_id, name, str(value))
            )
        
        conn.commit()
        return run_id