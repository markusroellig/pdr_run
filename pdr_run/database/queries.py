"""Database query utilities for the PDR framework."""

import logging
from typing import TypeVar, Type, Optional, Any
from sqlalchemy import and_
from sqlalchemy.orm import Session

from pdr_run.database.db_manager import get_db_manager
from pdr_run.database.models import (
    ModelNames, User, KOSMAtauExecutable, ChemicalDatabase,
    KOSMAtauParameters, PDRModelJob, HDFFile
)

logger = logging.getLogger('dev')

T = TypeVar('T')



def get_or_create(session: Session, model: Type[T], **kwargs) -> T:
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
        logger.debug(f"Found existing {model.__name__} with {kwargs}")
        return instance
    else:
        logger.debug(f"Creating new {model.__name__} with {kwargs}")
        instance = model(**kwargs)
        session.add(instance)
        try:
            session.commit()
            logger.debug(f"Successfully created {model.__name__} with ID {instance.id}")
        except Exception as e:
            logger.error(f"Failed to create {model.__name__}: {e}")
            session.rollback()
            raise
        return instance


def get_model_name_id(model_name: str, model_path: str, session: Optional[Session] = None) -> int:
    """Get model name ID from the database.
    
    Args:
        model_name: Model name
        model_path: Model path
        session: Database session (optional)
        
    Returns:
        int: Model name ID
    """
    if session is None:
        db_manager = get_db_manager()
        session = db_manager.get_session()
        should_close = True
    else:
        should_close = False
    
    try:
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
            try:
                session.commit()
                logger.debug(f"Created model name: {model_name} (ID: {model.id})")
            except Exception as e:
                logger.error(f"Failed to create model name {model_name}: {e}")
                session.rollback()
                raise
            return model.id
        elif query.count() > 1:
            logger.error(f'Multiple identical model_name entries in database: {model_name}')
            raise ValueError('Multiple identical model_name entries in database.')
        else:
            return query.first().id
    finally:
        if should_close:
            session.close()


def get_model_info_from_job_id(job_id: int, session: Optional[Session] = None) -> tuple:
    """Get model information from job ID.
    
    Args:
        job_id: Job ID
        session: Database session (optional)
        
    Returns:
        tuple: (model_name, model_job_name, model_id, parameter_id)
    """
    if session is None:
        db_manager = get_db_manager()
        session = db_manager.get_session()
        should_close = True
    else:
        should_close = False
    
    try:
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
    finally:
        if should_close:
            session.close()


def retrieve_job_parameters(job_id: int, session: Optional[Session] = None) -> tuple:
    """Retrieve job parameters from the database.
    
    Args:
        job_id: Job ID
        session: Database session (optional)
        
    Returns:
        tuple: (zmetal, density, mass, radiation, shieldh2)
    """
    if session is None:
        db_manager = get_db_manager()
        session = db_manager.get_session()
        should_close = True
    else:
        should_close = False
    
    try:
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
    finally:
        if should_close:
            session.close()


def update_job_status(job_id: int, status: str, session: Optional[Session] = None) -> None:
    """Update job status in the database.
    
    Args:
        job_id: Job ID
        status: New job status
        session: Database session (optional)
    """
    if session is None:
        db_manager = get_db_manager()
        with db_manager.session_scope() as session:
            _update_job_status(job_id, status, session)
    else:
        _update_job_status(job_id, status, session)


def _update_job_status(job_id: int, status: str, session: Session) -> None:
    """Internal function to update job status."""
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

    try:
        session.commit()
        logger.info(f"Updated job {job_id} status to '{status}'")
    except Exception as e:
        logger.error(f"Failed to update job {job_id} status to '{status}': {e}")
        session.rollback()
        raise


def get_session() -> Session:
    """Get a database session using the database manager.
    
    This function provides backward compatibility for tests and code
    that expects to patch 'pdr_run.database.queries.get_session'.
    
    Returns:
        Session: SQLAlchemy session object
    """
    db_manager = get_db_manager()
    return db_manager.get_session()