"""Acceptance test for the triplestore: update, query, bulk load, isolation.

Fixtures use a pk:SmokeTestPokemon class and smoke- prefixed IRIs, so the
counts hold whether or not the real dataset is loaded.

Run:  make smoke      (or: python -m pokekg.smoke)
"""
from __future__ import annotations

import tempfile
from pathlib import Path

from pokekg import settings as S
from pokekg import store

SCRATCH_A = f"{S.PKG}smoke-test-a"
SCRATCH_B = f"{S.PKG}smoke-test-b"

TURTLE_FIXTURE = f"""\
@prefix pk:  <{S.PK}> .
@prefix pkr: <{S.PKR}> .

pkr:smoke-pikachu  a pk:SmokeTestPokemon ; pk:hasType pkr:electric ; pk:speed 90 .
pkr:smoke-geodude  a pk:SmokeTestPokemon ; pk:hasType pkr:rock, pkr:ground ; pk:speed 20 .
pkr:electric a pk:Type .
pkr:rock     a pk:Type .
pkr:ground   a pk:Type .
"""

_passed = 0
_failed = 0


def check(label: str, actual, expected) -> None:
    global _passed, _failed
    ok = actual == expected
    _passed, _failed = _passed + ok, _failed + (not ok)
    mark = "PASS" if ok else "FAIL"
    detail = f"{actual!r}" if ok else f"got {actual!r}, expected {expected!r}"
    print(f"  [{mark}] {label:<46} {detail}")


def main() -> int:
    print(f"\nConnecting to {S.QUERY_ENDPOINT}")
    store.wait_until_up(timeout=90)
    print("Fuseki is up.\n")

    # Only the two scratch graphs are touched.
    store.clear_graph(SCRATCH_A)
    store.clear_graph(SCRATCH_B)

    print("1. SPARQL Update + Query")
    store.update(
        f"INSERT DATA {{ GRAPH <{SCRATCH_A}> {{ "
        f"  pkr:smoke-charizard a pk:SmokeTestPokemon ; pk:hasType pkr:fire, pkr:flying . "
        f"}} }}"
    )
    check("triples written to scratch graph A", store.count_triples(SCRATCH_A), 3)
    types = store.query(
        f"SELECT ?t WHERE {{ GRAPH <{SCRATCH_A}> {{ pkr:smoke-charizard pk:hasType ?t }} }} "
        f"ORDER BY ?t"
    )
    check(
        "charizard's types read back",
        [row["t"].rsplit("/", 1)[-1] for row in types],
        ["fire", "flying"],
    )
    check(
        "ASK finds the typed individual",
        store.ask(f"ASK {{ GRAPH <{SCRATCH_A}> {{ ?p a pk:SmokeTestPokemon }} }}"),
        True,
    )

    print("\n2. Graph Store Protocol bulk load")
    with tempfile.NamedTemporaryFile("w", suffix=".ttl", delete=False) as handle:
        handle.write(TURTLE_FIXTURE)
        fixture_path = Path(handle.name)
    try:
        store.load_turtle(fixture_path, SCRATCH_B)
        check("triples loaded into scratch graph B", store.count_triples(SCRATCH_B), 10)
        # PUT is idempotent, loading twice must not double the graph.
        store.load_turtle(fixture_path, SCRATCH_B)
        check("re-load replaces instead of appending", store.count_triples(SCRATCH_B), 10)
    finally:
        fixture_path.unlink(missing_ok=True)

    print("\n3. Named-graph isolation and the union default graph")
    check("graph A unaffected by graph B", store.count_triples(SCRATCH_A), 3)
    check(
        "GRAPH ?g sees both scratch graphs",
        sorted(g for g in store.graph_sizes() if "smoke-test" in g),
        [SCRATCH_A, SCRATCH_B],
    )
    check(
        "union default graph spans both",
        store.scalar(
            "SELECT (COUNT(*) AS ?n) WHERE { ?s a pk:SmokeTestPokemon }"
        ),
        3,  # the three fixtures, spread over two graphs
    )

    print("\n4. A real query: fastest Pokemon per type in graph B")
    rows = store.query(
        f"SELECT ?name ?speed WHERE {{ GRAPH <{SCRATCH_B}> {{ "
        f"  ?p a pk:SmokeTestPokemon ; pk:speed ?speed . BIND(STRAFTER(STR(?p), STR(pkr:)) AS ?name) "
        f"}} }} ORDER BY DESC(?speed)"
    )
    for row in rows:
        print(f"       {row['name']:<10} speed={row['speed']}")
    check("speed literal came back as a Python int", isinstance(rows[0]["speed"], int), True)

    print("\n5. Cleanup")
    store.clear_graph(SCRATCH_A)
    store.clear_graph(SCRATCH_B)
    check("scratch graphs removed", store.count_triples(SCRATCH_A) + store.count_triples(SCRATCH_B), 0)

    print(f"\n{'-' * 72}")
    print(f"{_passed} passed, {_failed} failed")
    print(store.describe_store())
    print(f"{'-' * 72}\n")
    return 1 if _failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
