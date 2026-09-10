# Service

A read-only HTTP API over the knowledge graph, which is the outcome the
scenario asks for. Report sections 1 and 5; **LO5**, **LO11**.

```bash
make serve
```

The interactive docs are then at <http://localhost:8000/docs>.

Needs Fuseki up (`make up`) with the data loaded, the rules run (`make reason`)
and, for predicted matchups, `make embed`.

## Endpoints

| | |
|---|---|
| `GET /health` | triplestore reachable, and the size of every named graph |
| `GET /pokemon/{name}` | types, stats, learnset, win/loss record |
| `GET /matchup/{a}/vs/{b}` | who wins, and on what evidence |
| `GET /counters/{name}?limit=n` | what beats it, hardest first (limit 1–50) |

## What it demonstrates

The graph holds two kinds of answer and the service never mixes them.

A verdict the **rules proved** comes back with its working:

```json
{ "winner": "charizard", "loser": "venusaur", "source": "rules",
  "confidence": null,
  "evidence": { "advantage_score": 0.6842,
                "winner_needs_hits": 2, "loser_needs_hits": 8,
                "winner_damage_per_hit": 243.88,
                "winner_moves_first": true } }
```

A verdict the **embedding guessed** cannot show working, and says so:

```json
{ "winner": "charizard", "loser": "miltank", "source": "embedding",
  "confidence": 0.5162, "evidence": null }
```

Rules are preferred wherever they reached a verdict; the model answers only
where they did not. Of the 2,222 predicted edges, **99** are for pairs the
rules left undecided, and the rest re-derive edges withheld for evaluation.

`/counters` is the recommendation the scenario describes. Asking what beats
Blissey returns Shedinja, Sableye and Gengar: ghosts, which a normal-type
attacker cannot touch at all. Nobody encoded that; it falls out of the type
chart through the damage rules.

## Design

One module, `src/api.py`, in four parts: IRI helpers, Pydantic response models,
the SPARQL query templates, and the endpoints that combine them. Splitting the
queries out keeps each endpoint short enough to read as the shape of its answer.

The service holds no state and no copy of the data: every answer is a SPARQL
query against Fuseki, so it cannot drift from the graph. Reads are scoped to
named graphs by IRI, which is what keeps proved and predicted answers separable.
The response models are what FastAPI turns into the schemas shown at `/docs`.
