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
├── Makefile             every task: install, up, transform, load, embed, serve
├── Dockerfile           the app image: Python 3.12 + CPU torch + requirements
└── docker-compose.yml   Fuseki 6.2 + the app runtime + persistent volumes
```

Three further directories exist locally but are not version-controlled (see
`.gitignore`): `docs/` holds the course reference documents, `scenario/` and
`reflection/` hold drafts of the two prose report sections.

Each tracked folder carries a README saying what belongs there and which
learning outcomes it evidences. Output directories (`data/rdf/`, `results/`,
`models/`) are created by the step that first writes to them rather than being
committed empty.

## Getting started

**The only prerequisite is Docker** (with the Compose plugin). Python, PyTorch
and the rest of the dependencies live inside the `app` image, so a fresh
checkout installs nothing on the host.

Every command below is a `make` target, run from the repository root. The blocks
are deliberately free of trailing comments so they can be pasted straight into a
shell (see the zsh note at the end of this section).

### 1. Set up and populate the graph

```bash
make install
make up
make transform
make load
```

| Step | What it does |
|---|---|
| `make install` | Builds the `app` image from `Dockerfile`. Once per checkout; a few minutes, mostly PyTorch. |
| `make up` | Starts Fuseki at <http://localhost:3030> (admin / admin) and waits until it reports healthy. |
| `make transform` | Builds `construction/data/rdf/base.ttl` from the committed dataset. |
| `make load` | SHACL-validates, then loads the ontology, shapes and base graphs. |

### 2. Check that it worked

```bash
make status
make smoke
```

`make status` prints the triple count per named graph — expect base 7072,
ontology 194, shapes 140. `make smoke` is the acceptance test: 10 checks, all of
which should pass.

### 3. Reasoning, embeddings and the API

```bash
make reason
make entail
make embed
```

| Step | What it does |
|---|---|
| `make reason` | Runs the SPARQL rules, materialising `inferred-recursive` and `inferred-matchup`. |
| `make entail` | Materialises the RDFS / OWL-RL entailments into `inferred-rdfs`, for comparison. |
| `make embed` | The whole ML step: `split` withholds matchup edges, `train` fits RotatE, `evaluate` ranks the held-out edges. Training takes about seven minutes on a CPU. |

With all of that in place, start the read-only API. It runs in the foreground,
so give it a terminal of its own and stop it with `Ctrl-C`:

```bash
make serve
```

The interactive docs are then at <http://localhost:8000/docs>.

## How the two modes work

Each pipeline step is a short-lived container: `make transform` is really
`docker compose run --rm app python construction/src/transform.py`. The
repository is bind-mounted at `/app`, so edits take effect immediately without
rebuilding and generated artefacts (`construction/data/rdf/`,
`embeddings/models/`, `embeddings/results/`) appear in your working tree, owned
by you rather than by root. The container reaches the triplestore over the
Compose network as `http://fuseki:3030`, which `pokekg/settings.py` picks up
from `FUSEKI_HOST`.

If you would rather run the Python locally — for a debugger, or to avoid the
image build — every target also works against a virtualenv with `DOCKER=0`:

```bash
make DOCKER=0 venv
make DOCKER=0 install
make DOCKER=0 transform
```

Export `DOCKER=0` in your shell to make that the default for the session. In
that mode `pip install torch` pulls the CUDA build from PyPI, about 2.5 GB of
NVIDIA runtime libraries the project never uses. To avoid it, install the
CPU-only wheel first, exactly as the `Dockerfile` does:

```bash
./venv/bin/pip install torch --index-url https://download.pytorch.org/whl/cpu
```

### Notes

`make transform` reads the committed dataset at
`construction/data/raw/pokeapi_dataset.json`, so no network access is needed.
The RDF it writes is a generated artefact and is not version-controlled.
`make extract` rebuilds that dataset from PokéAPI directly, which is only needed
to widen the scope and takes several minutes on a cold cache.

Other targets: `make shell` (a shell inside the app container), `make down`
(stop, keep data), `make reset` (stop and delete the database volume),
`make logs`, `make clean-graphs`, `make help`.

**Using zsh?** Do not paste commands with a trailing `# comment`. Interactive
comments are off by default in zsh, so `make up  # start Fuseki` fails with
`zsh: bad pattern: #` and the command never runs. Either paste the bare commands
as printed above, or enable comments once with:

```bash
echo 'setopt interactive_comments' >> ~/.zshrc
```

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