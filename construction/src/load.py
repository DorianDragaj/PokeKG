"""Validate the RDF and load it into Fuseki.

Loads three named graphs: the ontology, the SHACL shapes, and the base data
produced by transform.py. SHACL validation runs first, so data that violates
a shape never reaches the store.

Run:  make load
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from pyshacl import validate
from rdflib import Graph

from pokekg import settings as S
from pokekg import store

ONTOLOGY = S.ONTOLOGY_DIR / "pokemon.ttl"
SHAPES = S.ONTOLOGY_DIR / "shapes.ttl"
BASE = S.RDF_DIR / "base.ttl"

GRAPHS = [
    (ONTOLOGY, S.G_ONTOLOGY, "ontology"),
    (SHAPES, S.G_SHAPES, "shapes"),
    (BASE, S.G_BASE, "base data"),
]

MAX_REPORTED = 15


def run_validation() -> bool:
    """Validate ontology + data against the shapes. True when it conforms."""
    data = Graph().parse(ONTOLOGY, format="turtle").parse(BASE, format="turtle")
    shapes = Graph().parse(SHAPES, format="turtle")
    conforms, report, text = validate(data, shacl_graph=shapes, inference="none")

    if conforms:
        print(f"SHACL: conforms ({len(data)} triples checked)")
        return True

    violations = [
        line.strip()
        for line in text.splitlines()
        if line.strip().startswith(("Focus Node:", "Message:", "Value Node:"))
    ]
    print("SHACL: FAILED\n")
    for line in violations[: MAX_REPORTED * 3]:
        print(f"  {line}")
    if len(violations) > MAX_REPORTED * 3:
        print("  ... and more")
    return False


def main() -> int:
    missing = [path for path, _, _ in GRAPHS if not path.exists()]
    if missing:
        for path in missing:
            print(f"missing: {path.relative_to(S.ROOT)}")
        print("\nrun:  make extract && make transform")
        return 1

    if not run_validation():
        print("\nNothing was loaded.")
        return 1

    store.wait_until_up()
    print()
    for path, graph_iri, label in GRAPHS:
        store.load_turtle(path, str(graph_iri))
        print(f"  {label:<12} {store.count_triples(str(graph_iri)):>6} triples")

    print()
    print(store.describe_store())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
