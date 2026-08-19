# Prosper Challenge — run everything from the repo root.

VENV := backend/.venv
PYTHON := $(VENV)/bin/python

.PHONY: help install run api frontend test-backend clean

help: ## Show available targets
	@grep -E '^[a-zA-Z_-]+:.*?## .*$$' $(MAKEFILE_LIST) | \
		awk 'BEGIN {FS = ":.*?## "}; {printf "  \033[36m%-10s\033[0m %s\n", $$1, $$2}'

install: ## Create the venv and install dependencies
	python3 -m venv $(VENV)
	$(PYTHON) -m pip install --upgrade pip
	$(PYTHON) -m pip install -r backend/requirements.txt

run: ## Run the voice agent (then open http://localhost:7860/client)
	$(PYTHON) backend/bot.py

api: ## Run the local text scheduling API on http://localhost:8000
	cd backend && .venv/bin/python -m uvicorn api:app --reload --port 8000

frontend: ## Run the Agent Studio frontend
	cd frontend && npm run dev

test-backend: ## Run deterministic scheduling and conversation tests
	$(PYTHON) -m unittest discover -s backend/tests -v

clean: ## Remove the venv and Python caches
	rm -rf $(VENV)
	find backend -type d -name __pycache__ -prune -exec rm -rf {} +
