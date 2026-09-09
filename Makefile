# Pokemon Knowledge Graph - project tasks
# Usage: make <target>   (run from the repository root)

PY := ./venv/bin/python
PIP := ./venv/bin/pip

.DEFAULT_GOAL := help
.PHONY: help venv install up down restart logs status smoke reset clean-graphs extract transform load reason entail split train evaluate embed serve

help:  ## Show this help
	@grep -hE '^[a-zA-Z_-]+:.*?## ' $(MAKEFILE_LIST) | \
	  awk -F':.*?## ' '{printf "  \033[36m%-14s\033[0m %s\n", $$1, $$2}'

venv:  ## Create the virtual environment
	python3 -m venv venv

install: ## Install Python dependencies into ./venv
	$(PIP) install -r requirements.txt

up:    ## Start the Fuseki triplestore (http://localhost:3030)
	docker compose up -d
	@echo "Waiting for Fuseki to become healthy..."
	@$(PY) -c "import sys; sys.path.insert(0,'.'); from pokekg import store; store.wait_until_up(); print('Fuseki is up: http://localhost:3030')"

down:  ## Stop the triplestore, keeping the data
	docker compose down

restart: down up ## Restart the triplestore

logs:  ## Follow the Fuseki container log
	docker compose logs -f fuseki

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
	$(PY) -m uvicorn service.src.api:app --reload --port 8000

status: ## Show endpoint status and triple counts per named graph
	@$(PY) -m pokekg.store

smoke: ## Acceptance test (writes and deletes scratch graphs only)
	$(PY) -m pokekg.smoke

clean-graphs: ## Empty the dataset but keep the container running
	@$(PY) -c "import sys; sys.path.insert(0,'.'); from pokekg import store; store.drop_all(); print('dataset emptied')"

reset: ## Stop the triplestore AND delete the database volume (destructive)
	docker compose down -v
