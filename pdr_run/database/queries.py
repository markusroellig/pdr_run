"""Database query utilities for the PDR framework."""

import logging
import time
from datetime import datetime, timedelta
from functools import wraps
from typing import TypeVar, Type, Optional, Any, Callable, List, Dict
from sqlalchemy import and_
from sqlalchemy.orm import Session
from sqlalchemy.exc import (
    OperationalError,
    DisconnectionError,
    TimeoutError as SQLAlchemyTimeoutError,
    InvalidRequestError
)

from pdr_run.database.db_manager import get_db_manager
from pdr_run.database.models import (
    ModelNames, User, KOSMAtauExecutable, ChemicalDatabase,
    KOSMAtauParameters, PDRModelJob, HDFFile
)
from pdr_run.models.job_status import ALL_STATUSES as _JOB_STATUS_ALL_STATUSES

logger = logging.getLogger('dev')

# Status assigned by reset_stale_jobs() to a 'running' job whose
# time_of_start is too old to still be a genuine in-progress run. Kept out
# of job_status.ALL_STATUSES (that module classifies a *completed* pdrexe
# run; this one classifies a row the driver never got to finish writing).
STATUS_RESET_STALE = 'reset_stale'

# Default staleness window used when the caller (CLI/engine) does not have
# a config['pdr']['max_walltime_s'] to derive one from. Deliberately much
# larger than any single grid-1 node is expected to take (hours), so this
# only ever fires for jobs truly abandoned by a crashed/killed driver -
# never for one that is still legitimately running.
DEFAULT_STALE_AFTER_S = 6 * 3600

T = TypeVar('T')


def retry_on_db_error(max_retries: int = 3, initial_delay: float = 1.0, backoff: float = 2.0):
    """Decorator to retry database operations on transient errors.

    This decorator handles common database connection issues during parallel execution,
    such as connection loss, timeouts, and SSL errors. It implements exponential backoff
    to avoid overwhelming the database server.

    Args:
        max_retries: Maximum number of retry attempts (default: 3)
        initial_delay: Initial delay in seconds before first retry (default: 1.0)
        backoff: Multiplier for delay between retries (default: 2.0)

    Returns:
        Decorated function with retry logic
    """
    def decorator(func: Callable) -> Callable:
        @wraps(func)
        def wrapper(*args, **kwargs):
            delay = initial_delay
            last_exception = None

            for attempt in range(max_retries + 1):
                try:
                    return func(*args, **kwargs)
                except (OperationalError, DisconnectionError, SQLAlchemyTimeoutError) as e:
                    last_exception = e
                    error_str = str(e).lower()

                    # Check if this is a retryable error
                    is_retryable = any([
                        'lost connection' in error_str,
                        'connection' in error_str and 'closed' in error_str,
                        'timeout' in error_str,
                        'eof' in error_str,
                        'ssl' in error_str,
                        'broken pipe' in error_str,
                        'connection refused' in error_str,
                        'can\'t connect' in error_str,
                        'gone away' in error_str,
                    ])

                    if not is_retryable or attempt == max_retries:
                        logger.error(
                            f"Database operation failed after {attempt + 1} attempts in {func.__name__}: {e}",
                            exc_info=True
                        )
                        raise

                    logger.warning(
                        f"Database connection error in {func.__name__} (attempt {attempt + 1}/{max_retries + 1}): {e}. "
                        f"Retrying in {delay:.1f}s..."
                    )

                    # Wait before retrying
                    time.sleep(delay)
                    delay *= backoff

                    # Try to clean up any stale session/connection
                    try:
                        if 'session' in kwargs:
                            kwargs['session'].rollback()
                        elif len(args) > 0 and hasattr(args[0], 'rollback'):
                            args[0].rollback()
                    except Exception as cleanup_err:
                        logger.debug(f"Error during session cleanup: {cleanup_err}")

                except InvalidRequestError as e:
                    # Handle session state errors (e.g., trying to use a closed session)
                    if 'session is closed' in str(e).lower() or 'inactive transaction' in str(e).lower():
                        logger.warning(f"Session state error in {func.__name__}: {e}. Creating new session...")
                        # Get a new session and retry once
                        if 'session' in kwargs:
                            # Close old session before replacing to prevent leak
                            old_session = kwargs['session']
                            try:
                                old_session.close()
                                logger.debug("Closed stale session before replacement")
                            except Exception as cleanup_err:
                                logger.debug(f"Error closing stale session: {cleanup_err}")

                            # Create new session and retry
                            kwargs['session'] = get_db_manager().get_session()
                            return func(*args, **kwargs)
                    raise

            # Should never reach here, but just in case
            raise last_exception if last_exception else RuntimeError(f"Failed to execute {func.__name__}")

        return wrapper
    return decorator



@retry_on_db_error(max_retries=5, initial_delay=1.0, backoff=2.0)
def get_or_create(session: Session, model: Type[T], **kwargs) -> T:
    """Get an existing database entry or create a new one.

    This function implements retry logic to handle transient database connection
    issues during parallel execution.

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


@retry_on_db_error(max_retries=5, initial_delay=1.0, backoff=2.0)
def get_model_name_id(model_name: str, model_path: str, session: Optional[Session] = None) -> int:
    """Get model name ID from the database with retry logic.

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


@retry_on_db_error(max_retries=5, initial_delay=1.0, backoff=2.0)
def get_model_info_from_job_id(job_id: int, session: Optional[Session] = None) -> tuple:
    """Get model information from job ID with retry logic.

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


@retry_on_db_error(max_retries=5, initial_delay=1.0, backoff=2.0)
def retrieve_job_parameters(job_id: int, session: Optional[Session] = None) -> tuple:
    """Retrieve job parameters from the database with retry logic.

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


@retry_on_db_error(max_retries=5, initial_delay=1.0, backoff=2.0)
def _update_job_status(job_id: int, status: str, session: Session) -> None:
    """Internal function to update job status with retry logic."""
    job = session.get(PDRModelJob, job_id)
    if not job:
        raise ValueError(f"Job with ID {job_id} not found")

    job.status = status
    if status == 'running':
        job.active = True
        job.pending = False
    elif status in ['finished', 'error', 'skipped', 'exception',
                     # legacy PDR-run terminal statuses (models/kosma_tau.py)
                     'problem', 'ERROR', 'failed_storage',
                     # pdr_run.models.job_status.determine_job_status() outcomes
                     *_JOB_STATUS_ALL_STATUSES,
                     # reset_stale_jobs() outcome (crashed-driver recovery)
                     STATUS_RESET_STALE]:
        job.active = False
        job.pending = False

    try:
        session.commit()
        logger.info(f"Updated job {job_id} status to '{status}'")
    except Exception as e:
        logger.error(f"Failed to update job {job_id} status to '{status}': {e}")
        session.rollback()
        raise


def summarize_job_states(job_ids: List[int], session: Optional[Session] = None):
    """Final states of the jobs of a run.

    Returns ``(counts, n_postproc_errors)``: ``counts`` maps job status to
    number of jobs; ``n_postproc_errors`` counts jobs with a recorded
    post-processing error (``postproc_error`` or ``uvcont_error``), which do
    not change the job status.
    """
    _session = session
    created = _session is None
    if created:
        _session = get_db_manager().get_session()
    try:
        counts: Dict[str, int] = {}
        n_postproc = 0
        for job_id in job_ids:
            job = _session.get(PDRModelJob, job_id)
            if job is None:
                continue
            _session.refresh(job)
            counts[job.status] = counts.get(job.status, 0) + 1
            if job.postproc_error or job.uvcont_error:
                n_postproc += 1
        return counts, n_postproc
    finally:
        if created:
            _session.close()


def find_stale_jobs(session: Optional[Session] = None,
                     stale_after_s: float = DEFAULT_STALE_AFTER_S) -> List[PDRModelJob]:
    """Find jobs stuck in status ``'running'`` that are almost certainly
    abandoned by a crashed or killed driver process rather than still
    genuinely executing.

    A pdrexe run that is actually still in progress always has a bounded
    lifetime: either it finishes and ``run_pdr()`` writes a terminal status
    (see ``pdr_run.models.job_status``), or it hits
    ``config['pdr']['max_walltime_s']`` and ``_kill_process_group()`` /
    ``determine_job_status()`` write ``'timeout'``. A row that is still
    ``'running'`` long after any plausible wall-time cap was never given
    the chance to reach either of those - the driver process itself (not
    pdrexe) died. This function only *detects* such rows; call
    ``reset_stale_jobs()`` to mark them so job-status bookkeeping
    (``active``/``pending``) is no longer wrong.

    Args:
        session: Database session (optional; a local one is used if None).
        stale_after_s: Age of ``time_of_start``, in seconds, beyond which a
            still-'running' job is considered abandoned. Pass e.g.
            ``config['pdr']['max_walltime_s'] * 1.5`` when a wall-time cap
            is configured for a tighter, run-specific threshold.

    Returns:
        List of stale ``PDRModelJob`` rows (may be attached to a locally
        created session - read their attributes before it is closed, or
        pass in your own session to keep them usable).
    """
    _session = session
    session_created_locally = False
    if _session is None:
        _session = get_db_manager().get_session()
        session_created_locally = True

    try:
        cutoff = datetime.now() - timedelta(seconds=stale_after_s)
        stale = (
            _session.query(PDRModelJob)
            .filter(PDRModelJob.status == 'running')
            .filter(PDRModelJob.time_of_start.isnot(None))
            .filter(PDRModelJob.time_of_start < cutoff)
            .all()
        )
        if stale:
            logger.warning(
                "Found %d job(s) stuck in status 'running' with time_of_start "
                "older than %.0fs (likely abandoned by a crashed driver): %s",
                len(stale), stale_after_s,
                [(j.id, j.model_job_name, j.time_of_start) for j in stale],
            )
        return stale
    finally:
        if session_created_locally:
            # Caller only reads plain attributes already loaded above;
            # closing here is safe and avoids leaking a connection.
            _session.close()


def reset_stale_jobs(session: Optional[Session] = None,
                      stale_after_s: float = DEFAULT_STALE_AFTER_S,
                      dry_run: bool = False) -> List[int]:
    """Mark jobs found by ``find_stale_jobs()`` as ``'reset_stale'`` so they
    are no longer counted as ``active``/``pending`` and a human reviewing
    the grid table can immediately see which nodes need a manual rerun.

    This does NOT create a replacement job - job creation happens once in
    ``pdr_run.core.engine.create_database_entries()``; re-running the same
    grid invocation will create a fresh job row for the same parameters
    (the old, now-'reset_stale' row is left in place for provenance, same
    pattern as the retired-row convention used elsewhere in this project).

    Args:
        session: Database session (optional).
        stale_after_s: See ``find_stale_jobs()``.
        dry_run: If True, only log/report what would be reset; make no
            database changes. Intended for a pre-flight check before a
            production grid run.

    Returns:
        List of job IDs that were (or, in dry_run mode, would be) reset.
    """
    _session = session
    session_created_locally = False
    if _session is None:
        _session = get_db_manager().get_session()
        session_created_locally = True

    try:
        stale = find_stale_jobs(_session, stale_after_s=stale_after_s)
        job_ids = [j.id for j in stale]
        if not job_ids:
            return []

        if dry_run:
            logger.warning(
                "[dry-run] Would reset %d stale job(s) to status '%s': %s",
                len(job_ids), STATUS_RESET_STALE, job_ids,
            )
            return job_ids

        for job in stale:
            age_s = (datetime.now() - job.time_of_start).total_seconds()
            logger.warning(
                "Resetting stale job %d (%s), running since %s (%.0fs ago), "
                "to status '%s'",
                job.id, job.model_job_name, job.time_of_start, age_s,
                STATUS_RESET_STALE,
            )
            job.status = STATUS_RESET_STALE
            job.active = False
            job.pending = False

        try:
            _session.commit()
        except Exception as e:
            logger.error(f"Failed to commit reset of stale jobs {job_ids}: {e}")
            _session.rollback()
            raise
        return job_ids
    finally:
        if session_created_locally:
            _session.close()


def get_session() -> Session:
    """Get a database session using the database manager.
    
    This function provides backward compatibility for tests and code
    that expects to patch 'pdr_run.database.queries.get_session'.
    
    Returns:
        Session: SQLAlchemy session object
    """
    db_manager = get_db_manager()
    return db_manager.get_session()