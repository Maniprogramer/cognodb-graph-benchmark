# One-command benchmark. `make all` goes from an empty machine to a report.

PY := .venv/bin/python
PIP := .venv/bin/pip
COMPOSE := docker compose -f docker/docker-compose.yml
export PYTHONPATH := src

.DEFAULT_GOAL := help
.PHONY: help setup up down fresh check dataset bench quick report test clean logs stats

help: ## Show this help
	@grep -hE '^[a-zA-Z_-]+:.*?## ' $(MAKEFILE_LIST) \
	  | awk 'BEGIN{FS=":.*?## "}{printf "  \033[36m%-10s\033[0m %s\n", $$1, $$2}'

setup: ## Create the venv and install pinned dependencies
	python3 -m venv .venv
	$(PIP) install --quiet --upgrade pip
	$(PIP) install -r requirements-dev.txt
	@echo "Setup complete. Copy .env.example to .env and add your CognoDB credentials."

up: ## Start the local platforms, each capped to the parity tier
	$(COMPOSE) up -d
	@echo "Waiting for platforms to accept connections..."
	@$(PY) scripts/wait_for_platforms.py

fresh: ## Recreate the local platforms from empty volumes (recommended before a reporting run)
	$(COMPOSE) down -v
	$(MAKE) up

down: ## Stop and remove the local platforms and their volumes
	$(COMPOSE) down -v

check: ## Verify every configured platform accepts connections (fast credential check)
	$(PY) scripts/wait_for_platforms.py

dataset: ## Download and canonicalise the dataset
	$(PY) -m bench dataset

bench: ## Run the full benchmark against every configured platform
	$(PY) -m bench run --iterations 100 --repeats 3 --concurrency 1,10,40 --concurrency-seconds 20

quick: ## Short smoke run to verify the harness works
	$(PY) -m bench run --quick

report: ## Regenerate tables and charts from the existing results.json
	$(PY) -m bench report

test: ## Run the unit test suite
	$(PY) -m pytest tests/

stats: ## Show live resource usage of the capped containers
	docker stats --no-stream bench-neo4j bench-memgraph bench-arangodb bench-falkordb

logs: ## Tail logs from the local platforms
	$(COMPOSE) logs -f --tail 50

clean: ## Remove generated data and results
	rm -rf data/nodes.csv data/edges.csv data/manifest.json results/results.json results/charts

all: setup up dataset bench ## Full pipeline: setup -> start -> load -> benchmark -> report
