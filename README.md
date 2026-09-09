# Pokémon Matchup Advantages Using Knowledge Graphs

Given two Pokémon (stats, types, moves, type-nullifying abilities), determine
and quantify who has the matchup advantage, and predict matchup advantages for
newly added Pokémon using KG embeddings.

* **Logic layer** infers a scored `pk:hasMatchupAdvantageOver` relation with
  declarative SPARQL rules executed inside the triplestore.
* **ML layer** trains KG embeddings on the graph with a subset of those inferred
  edges withheld, then evaluates link prediction on the held-out edges.

## Scope (fixed)

1v1 battles only · Gen 1–3 (≤ #386), legendaries and mythicals excluded ·
all Pokémon at the same level · only type-nullifying abilities (Levitate,
Flash Fire, Volt Absorb, Lightning Rod, Water Absorb) · no held items, status
moves, weather, terrain or team synergy.

## Tech stack

| Layer | Choice | Why |
|---|---|---|
| Graph store | **Apache Jena Fuseki 6.2** (TDB2) in Docker | Real triplestore with a W3C SPARQL 1.1 Query/Update endpoint over HTTP, plus named-graph support and a browser query UI |
| Query language | **SPARQL 1.1** | Standard, and `INSERT … WHERE` lets the matchup rules live in the graph layer rather than in Python |
| Modelling | **RDFS + light OWL**, hand-authored Turtle | Explicit TBox to reason over and validate against |
| Validation | **SHACL** (`pyshacl` + Fuseki's `/shacl` endpoint) | Data-quality evidence for graph construction and evolution |
| Reasoning | SPARQL rules (primary) + `owlrl` RDFS/OWL-RL materialisation (comparison) | Two reasoning styles compared in the write-up |
| Embeddings | **PyKEEN 1.11**, RotatE | Beats TransE, ComplEx and R-GCN on the same split (MRR 0.615 vs 0.151, 0.401 and 0.489) |
| Service | **FastAPI + Uvicorn** over the SPARQL endpoint | Thin API that answers matchup questions from the KG and the trained model |
| Version control | Git / GitHub | |

## Repository layout

```
.
├── construction/        PokéAPI extraction, ontology, RDF generation, loading
│   ├── src/             extract.py, transform.py, load.py
│   └── data/raw/        extracted dataset (committed) + API cache (ignored)
├── embeddings/          KG embeddings, link prediction, predicted matchup edges
├── reasoning/           SPARQL rules, entailment, matchup inference
├── service/             FastAPI query + prediction service
├── pokekg/              shared package used across the project
│   ├── settings.py      IRIs, named graphs, paths, scope constants
│   ├── store.py         SPARQL client (query / update / bulk load)
│   ├── pokeapi.py       cache-backed PokéAPI client
│   ├── generations.py   projection of current data back to Generation 3
│   └── smoke.py         triplestore acceptance test (`make smoke`)
├── fuseki/              dataset-config.ttl, the Fuseki service definition
├── Makefile             every task: up, extract, smoke, status, reset
└── docker-compose.yml   Fuseki 6.2 + a persistent TDB2 volume
```

Three further directories exist locally but are not version-controlled (see
`.gitignore`): `docs/` holds the course reference documents, `scenario/` and
`reflection/` hold drafts of the two prose report sections.

Each tracked folder carries a README saying what belongs there and which
learning outcomes it evidences. Output directories (`data/rdf/`, `results/`,
`models/`) are created by the step that first writes to them rather than being
committed empty.

## Getting started

From a clean checkout, these five commands take you to a populated graph:

```bash
make venv          # once, if ./venv does not exist
make install       # install Python dependencies
make up            # start Fuseki at http://localhost:3030 (admin / admin)
make transform     # build construction/data/rdf/base.ttl from the dataset
make load          # SHACL-validate, then load ontology + shapes + data
```

Then check the result:

```bash
make status        # triple counts per named graph (expect base 7072, ontology 194, shapes 140)
make smoke         # acceptance test: 10 checks, all should pass
```

`make transform` reads the committed dataset at
`construction/data/raw/pokeapi_dataset.json`, so no network access is needed.
The RDF it writes is a generated artefact and is not version-controlled.
`make extract` rebuilds that dataset from PokéAPI directly, which is only needed
to widen the scope and takes several minutes on a cold cache.

Other targets: `make down` (stop, keep data), `make reset` (stop and delete the
database volume), `make logs`, `make clean-graphs`, `make help`.

## Named graphs

Asserted, entailed and evaluation triples are kept apart so that every inferred
edge is attributable and so the ML split cannot leak.

| Graph IRI | Contents | Written by |
|---|---|---|
| `…/graph/ontology` | TBox: classes, properties, axioms | `make load` |
| `…/graph/shapes` | SHACL shapes | `make load` |
| `…/graph/base` | ABox from PokéAPI | `make load` |
| `…/graph/inferred-rdfs` | RDFS / OWL-RL entailments (325, for comparison) | `make entail` |
| `…/graph/inferred-recursive` | evolution closure, `isFullyEvolved` | `make reason` |
| `…/graph/inferred-matchup` | reified `pk:Matchup` nodes + `hasMatchupAdvantageOver` | `make reason` |
| `…/graph/eval-test` | held-out matchup edges | `make split` |
| `…/graph/predicted-matchup` | edges the embedding model adds back (KG completion) | `make evaluate` |

The dataset is configured with `tdb2:unionDefaultGraph true`, so a query without
a `GRAPH` clause sees the union of all named graphs.