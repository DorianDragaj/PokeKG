"""Run the SPARQL rules against the triplestore.

Every rule file is a SPARQL 1.1 Update that reads the union of the loaded
graphs and writes into one inference graph. The rules run inside Fuseki, so
the reasoning is part of the graph layer rather than a Python post-process.

Rule files name their target with a placeholder (<GRAPH_DERIVED>) instead of a
literal IRI, so the graph names stay defined in one place, pokekg/settings.py.
Each target graph is emptied before its rules run, which makes the whole step
idempotent: running it twice leaves the same triples behind.
"""
from __future__ import annotations

import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from pokekg import settings as S
from pokekg import store

PLACEHOLDERS = {
    "GRAPH_RDFS": S.G_RDFS,
    "GRAPH_DERIVED": S.G_DERIVED,
    "GRAPH_MATCHUP": S.G_MATCHUP,
    "GRAPH_SCRATCH": S.G_SCRATCH,
}


def rule_files() -> list[Path]:
    files = sorted(S.RULES_DIR.glob("*.ru"))
    if not files:
        raise SystemExit(f"no rule files in {S.RULES_DIR}")
    return files


def resolve(text: str) -> tuple[str, set[str]]:
    """Replace the graph placeholders and report which ones a rule file writes."""
    targets = set()
    for name, iri in PLACEHOLDERS.items():
        token = f"<{name}>"
        if token in text:
            text = text.replace(token, f"<{iri}>")
            targets.add(str(iri))
    return text, targets


def main() -> int:
    if not store.is_up():
        print("Fuseki is not running  ->  make up")
        return 1

    files = rule_files()
    resolved = [(path, *resolve(path.read_text(encoding="utf-8"))) for path in files]

    written_to = sorted({iri for _, _, targets in resolved for iri in targets})
    for iri in written_to:
        store.clear_graph(iri)
    print(f"cleared {len(written_to)} inference graph(s)\n")

    for path, sparql, targets in resolved:
        before = sum(store.count_triples(iri) for iri in targets)
        started = time.perf_counter()
        store.update(sparql)
        elapsed = time.perf_counter() - started
        after = sum(store.count_triples(iri) for iri in targets)
        print(f"  {path.name:<24} {after - before:>7,} triples   {elapsed:6.1f}s")

    print()
    for iri in written_to:
        print(f"  {store.count_triples(iri):>8,}  <{iri}>")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
