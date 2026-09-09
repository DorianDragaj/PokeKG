"""Turn the extracted dataset into RDF.

Reads construction/data/raw/pokeapi_dataset.json and writes
construction/data/rdf/base.ttl using the vocabulary declared in the ontology.
Only Generation 3 values are emitted; the current_* fields stay in the JSON as
the audit record.

Inverse and transitive edges (evolvesFrom, evolvesIntoEventually) are left out
on purpose so the reasoning rules can derive them.

Run:  make transform
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from rdflib import Graph, Literal, RDF, RDFS, XSD

from pokekg import settings as S

INPUT_PATH = S.RAW_DIR / "pokeapi_dataset.json"
OUTPUT_PATH = S.RDF_DIR / "base.ttl"

PK, PKR = S.PK, S.PKR

STAT_PROPERTY = {
    "hp": PK.hp,
    "attack": PK.attack,
    "defense": PK.defense,
    "special_attack": PK.specialAttack,
    "special_defense": PK.specialDefense,
    "speed": PK.speed,
}

CHART_PROPERTY = {
    "super_effective_against": PK.superEffectiveAgainst,
    "resisted_by": PK.resistedBy,
    "no_effect_against": PK.noEffectAgainst,
}


def build(payload: dict) -> Graph:
    g = Graph()
    g.bind("pk", PK)
    g.bind("pkr", PKR)

    add_types(g, payload["types"])
    add_moves(g, payload["moves"])
    add_pokemon(g, payload["pokemon"])
    add_evolution(g, payload["evolution"])
    return g


def add_types(g: Graph, types: dict) -> None:
    for name, doc in types.items():
        node = PKR[name]
        g.add((node, RDF.type, PK.Type))
        g.add((node, RDFS.label, Literal(name)))
        g.add((node, PK.typeDamageClass, PKR[doc["damage_class_in_gen3"]]))
        for key, prop in CHART_PROPERTY.items():
            for other in doc[key]:
                g.add((node, prop, PKR[other]))


def add_moves(g: Graph, moves: dict) -> None:
    for name, doc in moves.items():
        node = PKR[name]
        g.add((node, RDF.type, PK.Move))
        g.add((node, RDFS.label, Literal(name)))
        g.add((node, PK.moveType, PKR[doc["type"]]))
        g.add((node, PK.power, Literal(doc["power"], datatype=XSD.integer)))
        g.add((node, PK.damageClass, PKR[doc["damage_class"]]))
        if doc["accuracy"] is not None:
            g.add((node, PK.accuracy, Literal(doc["accuracy"], datatype=XSD.integer)))


def add_pokemon(g: Graph, pokemon: list) -> None:
    for doc in pokemon:
        node = PKR[doc["name"]]
        g.add((node, RDF.type, PK.Pokemon))
        g.add((node, RDFS.label, Literal(doc["name"])))
        g.add((node, PK.pokedexId, Literal(doc["id"], datatype=XSD.integer)))
        for type_name in doc["types"]:
            g.add((node, PK.hasType, PKR[type_name]))
        for stat, value in doc["stats"].items():
            g.add((node, STAT_PROPERTY[stat], Literal(value, datatype=XSD.integer)))
        for move_name in doc["moves"]:
            g.add((node, PK.knowsMove, PKR[move_name]))
        for ability in doc["abilities"]:
            g.add((node, PK.hasAbility, PKR[ability]))


def add_evolution(g: Graph, edges: list) -> None:
    for edge in edges:
        g.add((PKR[edge["from"]], PK.evolvesInto, PKR[edge["to"]]))


def main() -> int:
    if not INPUT_PATH.exists():
        print(f"missing {INPUT_PATH}  ->  run: make extract")
        return 1

    payload = json.loads(INPUT_PATH.read_text(encoding="utf-8"))
    graph = build(payload)

    S.RDF_DIR.mkdir(parents=True, exist_ok=True)
    graph.serialize(destination=OUTPUT_PATH, format="turtle")

    counts = {
        "Pokemon": (RDF.type, PK.Pokemon),
        "Type": (RDF.type, PK.Type),
        "Move": (RDF.type, PK.Move),
    }
    print(f"Wrote {OUTPUT_PATH.relative_to(S.ROOT)}\n")
    print(f"  triples          {len(graph)}")
    for label, (prop, obj) in counts.items():
        print(f"  {label:<16} {len(list(graph.subjects(prop, obj)))}")
    print(f"  {'evolvesInto':<16} {len(list(graph.subject_objects(PK.evolvesInto)))}")
    print(f"  {'knowsMove':<16} {len(list(graph.subject_objects(PK.knowsMove)))}")
    print(f"  {'hasAbility':<16} {len(list(graph.subject_objects(PK.hasAbility)))}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
