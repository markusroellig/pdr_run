"""Base module for database models to avoid circular imports.

This module provides a central location for the SQLAlchemy declarative base class
that all ORM models in the application will inherit from. By isolating the Base
class in its own module, we prevent circular import issues that commonly occur
when models depend on each other.

Usage:
    from pdr_run.database.base import Base
    
    class MyModel(Base):
        __tablename__ = 'my_table'
        # Define columns here
"""

# Import the declarative_base function from SQLAlchemy ORM
# This is the factory function that creates a base class for declarative class definitions
from sqlalchemy.orm import declarative_base

# Create base class for SQLAlchemy models
# All model classes will inherit from this Base class to gain ORM functionality
# The Base class contains a MetaData object where all Table objects are collected
Base = declarative_base()