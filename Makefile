## Enhanced Makefile

Create this `Makefile` at the project root:

```makefile
.PHONY: help install dev-install setup-sandbox start-services stop-services test-all clean-sandbox

help:
	@echo "PDR Framework Development Commands"
	@echo "================================="
	@echo "install        - Install package for production use"
	@echo "dev-install    - Install package in development mode"
	@echo "setup-sandbox  - Set up development sandbox"
	@echo "start-services - Start all development services (Docker)"
	@echo "stop-services  - Stop all development services"
	@echo "test-all       - Run all tests"
	@echo "clean-sandbox  - Clean up sandbox data"
	@echo "lint          - Run code quality checks"

install:
	pip install .

dev-install:
	pip install -e .

setup-sandbox:
	python sandbox/setup_sandbox.py

start-services:
	cd sandbox && docker-compose up -d
	@echo "Waiting for services to start..."
	@sleep 10

stop-services:
	cd sandbox && docker-compose down

test-db:
	python sandbox/test_db_connections.py

test-storage:
	python sandbox/test_storage.py

test-integration:
	python sandbox/test_integration.py

test-unit:
	python -m pytest pdr_run/tests/ -v

test-all: test-unit test-db test-storage test-integration

clean-sandbox:
	cd sandbox && docker-compose down -v
	rm -rf sandbox/storage/*
	rm -rf sandbox/sqlite/*.db
	rm -rf sandbox/logs/*
	find sandbox/ -name "*.pyc" -delete

lint:
	python -m flake8 pdr_run/
	python -m pylint pdr_run/

logs:
	cd sandbox && docker-compose logs -f

restart: stop-services start-services

full-dev-setup: dev-install setup-sandbox start-services test-all