"""Read-only HTTP service over the knowledge graph.

Given two Pokemon, who wins and why; given one, what beats it. Every answer is
a SPARQL query against Fuseki, so the service holds no state and no copy of the
data.

The graph carries two kinds of verdict and the service keeps them apart. The
rules proved 17,663 matchups and can show their working: damage per hit, hits
to knock out, who moves first. The embedding model added a further 2,222 for
pairs the rules left undecided, and can only offer a rank. Both are served,
each labelled with its source.

Run:  make serve      then open http://localhost:8000/docs
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from fastapi import FastAPI, HTTPException, Path as PathParam, Query
from pydantic import BaseModel, Field

from pokekg import settings as S
from pokekg import store

MAX_COUNTERS = 50

app = FastAPI(
    title="Pokemon Matchup KG",
    description="Generation 1-3 matchups, proved by rules or predicted by embeddings.",
    version="1.0",
)


# --------------------------------------------------------------------------
# IRI helpers
# --------------------------------------------------------------------------

def to_iri(name: str) -> str:
    """'Charizard' -> the pkr: IRI. Names are lowercase slugs in the graph."""
    return str(S.PKR[name.strip().lower()])


def local_name(iri: str) -> str:
    """'.../resource/charizard' -> 'charizard'."""
    return str(iri).rsplit("/", 1)[-1]


def first(rows: list[dict]) -> dict | None:
    return rows[0] if rows else None


# --------------------------------------------------------------------------
# Response models. FastAPI turns these into the schemas shown at /docs.
# --------------------------------------------------------------------------

class Stats(BaseModel):
    hp: int
    attack: int
    defense: int
    specialAttack: int
    specialDefense: int
    speed: int


class Record(BaseModel):
    wins: int = Field(description="Matchups the rules decided in this Pokemon's favour")
    losses: int


class PokemonView(BaseModel):
    name: str
    types: list[str]
    stats: Stats
    moves: list[str]
    record: Record


class Evidence(BaseModel):
    """The reified verdict behind a rule-proved matchup."""

    advantage_score: float = Field(description="Margin of the win, in (0, 1)")
    winner_needs_hits: int
    loser_needs_hits: int
    winner_damage_per_hit: float
    winner_moves_first: bool


class MatchupView(BaseModel):
    winner: str | None
    loser: str | None
    source: str | None = Field(description="'rules', 'embedding', or null if undecided")
    confidence: float | None = Field(
        default=None,
        description="Rank of the prediction within its batch. Set for embedding "
                    "answers only; it orders predictions rather than calibrating them.",
    )
    evidence: Evidence | None = Field(
        default=None, description="Set for rule-proved answers only"
    )
    note: str | None = None


class Counter(BaseModel):
    counter: str
    score: float
    turns: int = Field(description="Hits the counter needs to knock this Pokemon out")


class CountersView(BaseModel):
    pokemon: str
    counters: list[Counter]


class Health(BaseModel):
    status: str
    graphs: dict[str, int]


# --------------------------------------------------------------------------
# Queries. Each is scoped to named graphs by IRI, which is what keeps proved
# and predicted answers separable.
# --------------------------------------------------------------------------

POKEMON_FACTS = f"""
    SELECT ?label ?hp ?attack ?defense ?specialAttack ?specialDefense ?speed
           (GROUP_CONCAT(DISTINCT ?type; separator=",") AS ?types)
    FROM <{S.G_BASE}> WHERE {{
        <%(pokemon)s> rdfs:label ?label ; pk:hasType ?t ;
            pk:hp ?hp ; pk:attack ?attack ; pk:defense ?defense ;
            pk:specialAttack ?specialAttack ; pk:specialDefense ?specialDefense ;
            pk:speed ?speed .
        BIND(REPLACE(STR(?t), "^.*/", "") AS ?type)
    }} GROUP BY ?label ?hp ?attack ?defense ?specialAttack ?specialDefense ?speed"""

POKEMON_MOVES = f"""
    SELECT (REPLACE(STR(?m), "^.*/", "") AS ?move) FROM <{S.G_BASE}>
    WHERE {{ <%(pokemon)s> pk:knowsMove ?m }} ORDER BY ?m"""

WINS = f"""SELECT (COUNT(*) AS ?n) FROM <{S.G_MATCHUP}>
    WHERE {{ <%(pokemon)s> pk:hasMatchupAdvantageOver ?o }}"""

LOSSES = f"""SELECT (COUNT(*) AS ?n) FROM <{S.G_MATCHUP}>
    WHERE {{ ?o pk:hasMatchupAdvantageOver <%(pokemon)s> }}"""

# Both orderings are offered to VALUES, so one query settles the pair whichever
# way round the rules recorded it.
PROVED_MATCHUP = f"""
    SELECT ?winner ?loser ?score ?winnerTurns ?loserTurns ?winnerDamage ?movesFirst
    FROM <{S.G_MATCHUP}> WHERE {{
        ?m pk:attacker ?winner ; pk:defender ?loser ;
           pk:advantageScore ?score ; pk:attackerTurns ?winnerTurns ;
           pk:defenderTurns ?loserTurns ; pk:attackerDamage ?winnerDamage ;
           pk:movesFirst ?movesFirst .
        VALUES (?winner ?loser) {{ (<%(left)s> <%(right)s>) (<%(right)s> <%(left)s>) }}
    }} LIMIT 1"""

PREDICTED_MATCHUP = f"""
    SELECT ?winner ?loser ?confidence FROM <{S.G_PREDICTED}> WHERE {{
        ?p pk:attacker ?winner ; pk:defender ?loser ; pkm:confidence ?confidence .
        VALUES (?winner ?loser) {{ (<%(left)s> <%(right)s>) (<%(right)s> <%(left)s>) }}
    }} ORDER BY DESC(?confidence) LIMIT 1"""

COUNTERS = f"""
    SELECT (REPLACE(STR(?winner), "^.*/", "") AS ?counter) ?score ?turns
    FROM <{S.G_MATCHUP}> WHERE {{
        ?m pk:attacker ?winner ; pk:defender <%(pokemon)s> ;
           pk:advantageScore ?score ; pk:attackerTurns ?turns .
    }} ORDER BY DESC(?score) LIMIT %(limit)d"""


# --------------------------------------------------------------------------
# Endpoints
# --------------------------------------------------------------------------

@app.get("/health", summary="Is the triplestore reachable?")
def health() -> Health:
    if not store.is_up():
        raise HTTPException(503, "triplestore unavailable")
    return Health(status="ok", graphs=store.graph_sizes())


@app.get("/pokemon/{name}", summary="Everything the graph knows about one Pokemon")
def pokemon(name: str = PathParam(examples=["charizard"])) -> PokemonView:
    subject = to_iri(name)
    facts = first(store.query(POKEMON_FACTS % {"pokemon": subject}))
    if not facts:
        raise HTTPException(404, f"no Pokemon named '{name}'")

    moves = [row["move"] for row in store.query(POKEMON_MOVES % {"pokemon": subject})]
    wins = store.scalar(WINS % {"pokemon": subject}) or 0
    losses = store.scalar(LOSSES % {"pokemon": subject}) or 0

    return PokemonView(
        name=facts["label"],
        types=facts["types"].split(","),
        stats=Stats(**{k: facts[k] for k in Stats.model_fields}),
        moves=moves,
        record=Record(wins=wins, losses=losses),
    )


@app.get("/matchup/{left_name}/vs/{right_name}", summary="Who wins, and on what evidence")
def matchup(
    left_name: str = PathParam(examples=["charizard"]),
    right_name: str = PathParam(examples=["venusaur"]),
) -> MatchupView:
    left, right = to_iri(left_name), to_iri(right_name)
    if left == right:
        raise HTTPException(400, "a Pokemon cannot fight itself")
    binding = {"left": left, "right": right}

    # Rules win wherever they reached a verdict; the model answers only for the
    # pairs they left undecided.
    proved = first(store.query(PROVED_MATCHUP % binding))
    if proved:
        return MatchupView(
            winner=local_name(proved["winner"]),
            loser=local_name(proved["loser"]),
            source="rules",
            evidence=Evidence(
                advantage_score=proved["score"],
                winner_needs_hits=proved["winnerTurns"],
                loser_needs_hits=proved["loserTurns"],
                winner_damage_per_hit=proved["winnerDamage"],
                winner_moves_first=proved["movesFirst"],
            ),
        )

    guessed = first(store.query(PREDICTED_MATCHUP % binding))
    if guessed:
        return MatchupView(
            winner=local_name(guessed["winner"]),
            loser=local_name(guessed["loser"]),
            source="embedding",
            confidence=guessed["confidence"],
        )

    return MatchupView(
        winner=None, loser=None, source=None,
        note="neither the rules nor the model separate these two",
    )


@app.get("/counters/{name}", summary="What beats this Pokemon, hardest first")
def counters(
    name: str = PathParam(examples=["blissey"]),
    limit: int = Query(10, ge=1, le=MAX_COUNTERS),
) -> CountersView:
    rows = store.query(COUNTERS % {"pokemon": to_iri(name), "limit": limit})
    if not rows:
        raise HTTPException(404, f"no matchups recorded against '{name}'")
    return CountersView(
        pokemon=name.strip().lower(),
        counters=[Counter(**row) for row in rows],
    )
