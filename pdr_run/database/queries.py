"""Database query utilities for the PDR framework."""

import logging
from sqlalchemy import and_

from pdr_run.database.connection import get_session
from pdr_run.database.models import (
    ModelNames, User, KOSMAtauExecutable, ChemicalDatabase,
    KOSMAtauParameters, PDRModelJob, HDFFile
)

logger = logging.getLogger('dev')

def get_or_create(session, model, **kwargs):
    """Get an existing database entry or create a new one.
    
    Args:
        session: Database session
        model: Database model class
        **kwargs: Model attributes
        
    Returns:
        model: Database model instance
    """
    instance = session.query(model).filter_by(**kwargs).first()
    if instance:
        logger.info(f"DB entry already exists. Fetching {model.__name__}")
        return instance
    else:
        instance = model(**kwargs)
        session.add(instance)
        session.commit()
        return instance

def get_model_name_id(model_name, model_path, session=None):
    """Get model name ID from the database.
    
    Args:
        model_name (str): Model name
        model_path (str): Model path
        session: Database session
        
    Returns:
        int: Model name ID
    """
    if session is None:
        session = get_session()
    
    model = ModelNames()
    model.model_name = model_name
    model.model_path = model_path
    
    query = session.query(ModelNames).filter(and_(
        ModelNames.model_name == model_name,
        ModelNames.model_path == model_path)
    )
    
    if query.count() == 0:
        # Create entry and return ID
        session.add(model)
        session.commit()
        return model.id
    elif query.count() > 1:
        logger.error(f'Multiple identical model_name entries in database: {model_name}')
        raise ValueError('Multiple identical model_name entries in database.')
    else:
        return query.first().id

def get_model_info_from_job_id(job_id, session=None):
    """Get model information from job ID.
    
    Args:
        job_id (int): Job ID
        session: Database session
        
    Returns:
        tuple: (model_name, model_job_name, model_id, parameter_id)
    """
    if session is None:
        session = get_session()
    
    job = session.get(PDRModelJob, job_id)
    if not job:
        raise ValueError(f"Job with ID {job_id} not found")
    
    model_id = job.model_name_id
    model = session.get(ModelNames, model_id)
    
    return (
        model.model_name,
        job.model_job_name,
        model_id,
        job.kosmatau_parameters_id
    )

def retrieve_job_parameters(job_id, session=None):
    """Retrieve job parameters from the database.
    
    Args:
        job_id (int): Job ID
        session: Database session
        
    Returns:
        tuple: (zmetal, density, mass, radiation, shieldh2)
    """
    if session is None:
        session = get_session()
    
    job = session.get(PDRModelJob, job_id)
    if not job:
        raise ValueError(f"Job with ID {job_id} not found")
    
    params = session.get(KOSMAtauParameters, job.kosmatau_parameters_id)
    
    from pdr_run.models.parameters import (
        from_par_to_string, 
        from_par_to_string_log
    )
    
    return (
        str(round(100 * params.zmetal)),
        from_par_to_string(params.xnsur),
        from_par_to_string(params.mass),
        from_par_to_string(params.sint),
        from_par_to_string(params.preshh2)
    )

def update_job_status(job_id, status, session=None):
    """Update job status in the database.
    
    Args:
        job_id (int): Job ID
        status (str): New job status
        session: Database session
    """
    if session is None:
        session = get_session()
    
    job = session.get(PDRModelJob, job_id)
    if not job:
        raise ValueError(f"Job with ID {job_id} not found")
    
    job.status = status
    if status == 'running':
        job.active = True
        job.pending = False
    elif status in ['finished', 'error', 'skipped', 'exception']:
        job.active = False
        job.pending = False
    
    session.commit()
    logger.info(f"Updated job {job_id} status to '{status}'")