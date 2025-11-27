.PHONY: help install dev lint format test coverage clean build

help:  ## Show this help message
	@grep -E '^[a-zA-Z_-]+:.*?## .*$$' $(MAKEFILE_LIST) | sort | awk 'BEGIN {FS = ":.*?## "}; {printf "\033[36m%-15s\033[0m %s\n", $$1, $$2}'

install:  ## Install the package
	pip install -e .

dev:  ## Install development dependencies
	pip install -e ".[dev]"

lint:  ## Run linters (ruff + mypy)
	ruff check src/ tests/ examples/
	mypy src/index_maker --ignore-missing-imports

format:  ## Format code with black
	black src/ tests/ examples/

format-check:  ## Check formatting without making changes
	black --check --diff src/ tests/ examples/

test:  ## Run tests
	pytest tests/ -v

coverage:  ## Run tests with coverage report
	pytest tests/ -v --cov=index_maker --cov-report=term-missing --cov-report=html
	@echo "Coverage report generated in htmlcov/index.html"

clean:  ## Clean build artifacts
	rm -rf build/
	rm -rf dist/
	rm -rf *.egg-info/
	rm -rf src/*.egg-info/
	rm -rf .pytest_cache/
	rm -rf .mypy_cache/
	rm -rf .ruff_cache/
	rm -rf htmlcov/
	rm -rf .coverage
	rm -rf coverage.xml
	find . -type d -name __pycache__ -exec rm -rf {} +

build:  ## Build the package
	python -m build

all: format lint test  ## Run format, lint, and test

