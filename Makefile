# Pokemon Knowledge Graph - project tasks
# Usage: make <target>   (run from the repository root)
#
# Two execution modes, same targets:
#   DOCKER=1 (default)  every step runs in the `app` container. Needs Docker
#                       and nothing else installed on the machine.
#   DOCKER=0            every step runs in ./venv. Needs a local Python 3.
# Set it per command (`make DOCKER=0 transform`) or export it once.

DOCKER ?= 1
COMPOSE := docker compose

ifeq ($(DOCKER),1)
  # Run as the invoking user so that files written into the bind-mounted
  # working tree are not owned by root.
  RUN := $(COMPOSE) run --rm --user $(shell id -u):$(shell id -g) app
  PY := $(RUN) python
  # --service-ports publishes 8000 for the API; only `serve` needs it.
  PY_SERVE := $(COMPOSE) run --rm --service-ports --user $(shell id -u):$(shell id -g) app python
  SERVE_HOST := 0.0.0.0
else
  PY := ./venv/bin/python
  PIP := ./venv/bin/pip
  PY_SERVE := $(PY)
  SERVE_HOST := 127.0.0.1
endif

.DEFAULT_GOAL := help
.PHONY: help venv install build shell up down restart logs status smoke reset clean-graphs extract transform load reason entail split train evaluate embed serve

help:  ## Show this help
	@grep -hE '^[a-zA-Z_-]+:.*?## ' $(MAKEFILE_LIST) | \
	  awk -F':.*?## ' '{printf "  \033[36m%-14s\033[0m %s\n", $$1, $$2}'
	@echo ""
	@echo "  Mode: DOCKER=$(DOCKER)  (DOCKER=0 runs everything in ./venv instead)"

venv:  ## Create the virtual environment (DOCKER=0 only)
ifeq ($(DOCKER),1)
	@echo "DOCKER=1: no local virtualenv needed. Run 'make install' to build the image."
else
	python3 -m venv venv
endif

install: ## Install dependencies (Docker: build the image; local: pip into ./venv)
ifeq ($(DOCKER),1)
	$(COMPOSE) build app
else
	$(PIP) install -r requirements.txt
endif

build: install ## Alias for install in Docker mode

shell: ## Open a shell in the app container (Docker mode)
	$(RUN) bash

up:    ## Start the Fuseki triplestore (http://localhost:3030)
	$(COMPOSE) up -d --wait fuseki
	@echo "Fuseki is up: http://localhost:3030"

down:  ## Stop the triplestore, keeping the data
	$(COMPOSE) down

restart: down up ## Restart the triplestore

logs:  ## Follow the Fuseki container log
	$(COMPOSE) logs -f fuseki

extract: ## Download the PokeAPI dataset into construction/data/raw
	$(PY) construction/src/extract.py

transform: ## Turn the extracted dataset into RDF
	$(PY) construction/src/transform.py

load:  ## Validate with SHACL and load every graph into Fuseki
	$(PY) construction/src/load.py

reason: ## Run the SPARQL rules and materialise the inference graphs
	$(PY) reasoning/src/rules.py

entail: ## Materialise the RDFS / OWL-RL entailments for comparison
	$(PY) reasoning/src/entail.py

split: ## Withhold a subset of matchup edges, write the triple files
	$(PY) embeddings/src/split.py

train: ## Train the RotatE embedding model
	$(PY) embeddings/src/train.py

evaluate: ## Rank the withheld edges, write examples and predictions
	$(PY) embeddings/src/evaluate.py

embed: split train evaluate ## The whole ML step, end to end

serve: ## Start the read-only API on http://localhost:8000 (docs at /docs)
	$(PY_SERVE) -m uvicorn service.src.api:app --reload --host $(SERVE_HOST) --port 8000

status: ## Show endpoint status and triple counts per named graph
	@$(PY) -m pokekg.store

smoke: ## Acceptance test (writes and deletes scratch graphs only)
	$(PY) -m pokekg.smoke

clean-graphs: ## Empty the dataset but keep the container running
	@$(PY) -c "import sys; sys.path.insert(0,'.'); from pokekg import store; store.drop_all(); print('dataset emptied')"

reset: ## Stop the triplestore AND delete the database volume (destructive)
	$(COMPOSE) down -v
