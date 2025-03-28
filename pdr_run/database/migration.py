"""Database migration utilities for the PDR framework."""

import logging
from sqlalchemy import inspect
from .connection import get_engine
from .models import Base

logger = logging.getLogger('dev')

def create_tables(engine):
    """Create all database tables defined in models."""
    # Force import all models to ensure they're registered with Base
    from .models import PDRModelJob, JSONTemplate, JSONFile
    
    # Log tables about to be created
    logger.debug(f"Tables to create: {[t.name for t in Base.metadata.sorted_tables]}")
    
    # Create all tables
    Base.metadata.create_all(engine)
    
    # Verify tables were created
    inspector = inspect(engine)
    actual_tables = inspector.get_table_names()
    logger.info(f"Created tables: {actual_tables}")