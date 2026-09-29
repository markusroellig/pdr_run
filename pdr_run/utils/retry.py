"""Generic bounded retry-with-backoff decorator for transient I/O errors.

Mirrors ``pdr_run.database.queries.retry_on_db_error`` but is I/O-backend
agnostic (subprocess/paramiko/socket errors, not SQLAlchemy ones). Used by
the storage backends (SFTP, rclone) where a single dropped connection or a
momentarily saturated remote endpoint must not fail an entire multi-hour
grid node's result upload/download.

Unlike ``retry_on_db_error``, callers here wrap only the *raising* inner
operation - the storage backends' public methods (``store_file`` etc.) have
an established ``bool`` return contract (``True``/``False``, never an
exception) used throughout ``pdr_run.core.engine`` and
``pdr_run.models.kosma_tau``. Preserving that contract while still retrying
means: decorate an inner function that raises on failure, call it from
inside the existing try/except that converts a persisting failure into
``False``.
"""

import logging
import time
from functools import wraps

logger = logging.getLogger('dev')


def retry_with_backoff(max_retries=3, initial_delay=2.0, backoff=2.0,
                        exceptions=(Exception,)):
    """Retry *func* up to ``max_retries`` times on ``exceptions``, with
    exponential backoff starting at ``initial_delay`` seconds.

    Re-raises the last exception once retries are exhausted, so callers
    that need to convert a persisting failure into a sentinel return value
    (``False``/``[]``) can do so with their own try/except around the call.
    """
    def decorator(func):
        @wraps(func)
        def wrapper(*args, **kwargs):
            delay = initial_delay
            last_exc = None
            for attempt in range(max_retries + 1):
                try:
                    return func(*args, **kwargs)
                except exceptions as exc:
                    last_exc = exc
                    if attempt == max_retries:
                        logger.error(
                            "%s failed after %d attempt(s): %s",
                            getattr(func, '__qualname__', func.__name__),
                            attempt + 1, exc,
                        )
                        raise
                    logger.warning(
                        "%s attempt %d/%d failed: %s. Retrying in %.1fs...",
                        getattr(func, '__qualname__', func.__name__),
                        attempt + 1, max_retries + 1, exc, delay,
                    )
                    time.sleep(delay)
                    delay *= backoff
            # Unreachable (loop always returns or raises), kept for clarity.
            raise last_exc
        return wrapper
    return decorator
