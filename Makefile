.PHONY: install clean fmt lint test test-unit test-integration coverage check run-api run-worker db-init db-seed db-reset demo postgres-start postgres-stop

install:
	uv sync

clean:
	rm -rf .venv .coverage src/*.egg-info .pytest_cache .ruff_cache

fmt:
	uv run ruff format src tests

lint:
	uv run ruff check src tests

test:
	uv run pytest

test-unit:
	uv run pytest tests/unit

test-integration:
	uv run pytest tests/integration

coverage:
	uv run coverage run -m pytest
	uv run coverage report

check:
	uv run ruff format --check src tests
	uv run ruff check src tests

run-api:
	uv run uvicorn orders_stock.api:app --host 127.0.0.1 --port 8000

run-worker:
	uv run orders-worker

postgres-start:
	docker compose up -d --wait

postgres-stop:
	docker compose stop

db-init:
	uv run orders-db init

db-seed:
	uv run orders-db seed

db-reset:
	uv run orders-db reset

demo:
	uv run orders-demo
