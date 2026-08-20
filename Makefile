# Prosper Challenge — run everything from the repo root.

VENV := backend/.venv
PYTHON := $(VENV)/bin/python

.PHONY: help install run api frontend logs logs-once logs-errors db-up db-migrate db-status test-backend clean

help: ## Show available targets
	@grep -E '^[a-zA-Z_-]+:.*?## .*$$' $(MAKEFILE_LIST) | \
		awk 'BEGIN {FS = ":.*?## "}; {printf "  \033[36m%-10s\033[0m %s\n", $$1, $$2}'

install: ## Create the venv and install dependencies
	python3 -m venv $(VENV)
	$(PYTHON) -m pip install --upgrade pip
	$(PYTHON) -m pip install -r backend/requirements.txt

run: ## Run the voice agent used by the embedded frontend call tester
	$(PYTHON) backend/bot.py

api: ## Run the local text scheduling API on http://localhost:8000
	cd backend && .venv/bin/python -m uvicorn api:app --reload --port 8000

frontend: ## Run the Agent Studio frontend
	cd frontend && npm run dev

logs: ## Follow structured API and voice logs
	$(PYTHON) backend/show_logs.py --follow --limit 100

logs-once: ## Print the latest 200 structured events
	$(PYTHON) backend/show_logs.py --limit 200

logs-errors: ## Follow errors from every backend process
	$(PYTHON) backend/show_logs.py --follow --limit 100 --level ERROR

db-up: ## Start local PostgreSQL in Docker
	docker compose up -d postgres

db-migrate: ## Apply PostgreSQL migrations using backend/.env
	$(PYTHON) backend/manage_db.py migrate

db-status: ## Verify PostgreSQL connectivity and schema
	$(PYTHON) backend/manage_db.py status

test-backend: ## Run deterministic scheduling and conversation tests
	$(PYTHON) -m unittest discover -s backend/tests -v

clean: ## Remove the venv and Python caches
	rm -rf $(VENV)
	find backend -type d -name __pycache__ -prune -exec rm -rf {} +
