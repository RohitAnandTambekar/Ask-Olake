# Common tasks. Run `make help` for the list.
.PHONY: help setup db migrate index dev test lint widget eval clean

help:
	@echo "setup    install Python and widget dependencies"
	@echo "db       start Postgres + pgvector (Docker)"
	@echo "migrate  create or upgrade the database schema"
	@echo "index    fetch docs and repos, build chunks, embed and activate an index"
	@echo "dev      run the API and demo page on http://localhost:8000"
	@echo "test     run all Python and widget tests"
	@echo "lint     ruff check + format check"
	@echo "widget   rebuild the widget into apps/api/static/widget.js"
	@echo "eval     evaluate retrieval and answers against eval/golden.yaml"

setup:
	uv sync
	cd apps/widget && npm ci

db:
	docker compose up -d db

migrate:
	uv run olake-ingest migrate

index:
	uv run olake-ingest run

dev:
	uv run olake-api serve --port 8000

test:
	uv run pytest -q
	cd apps/widget && npm test

lint:
	uv run ruff check .
	uv run ruff format --check .

widget:
	cd apps/widget && npm run build

eval:
	uv run olake-api eval --answers

clean:
	rm -rf apps/ingest/out eval/report.json eval/report.md
