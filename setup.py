from setuptools import setup, find_packages

setup(
    name="pdr_run",  # Keep this as is
    version="0.1.0",
    packages=find_packages(),  # Explicitly include packages
    install_requires=[
        "sqlalchemy",
        "numpy",
        "requests",
        "paramiko",
        "joblib",
        "pyyaml",
        "dask",
        "distributed",
        "rclone-python",  # Add for remote storage
        "alembic",  # Add for database migrations
    ],
    extras_require={
        'test': [
            'pytest',
            'pytest-cov',
            'pytest-mock',
        ],
    },
    entry_points={
        'console_scripts': [
            'pdr_run=pdr_run.cli.runner:main',  # Add a command-line entry point
        ],
    },
    author="PDR Framework Developers",
    author_email="m.roellig@physikalischer-verein.de",
    description="A framework for running PDR model grids",
    keywords="pdr, astrophysics, modeling",
    url="",
    classifiers=[
        "Development Status :: 3 - Alpha",
        "Intended Audience :: Science/Research",
        "Programming Language :: Python :: 3",
        "Topic :: Scientific/Engineering :: Astronomy",
    ],
    python_requires=">=3.6",
)