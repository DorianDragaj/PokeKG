"""Materialise the RDFS / OWL-RL entailments, for comparison with the rules.

The SPARQL rules in reasoning/rules/ are the project's primary reasoning, but
they are procedural: each one says how to compute something. An ontology says
what is true and lets a reasoner work out the rest. Both styles are kept in the
graph so the report can compare them on the same data.

owlrl computes the deductive closure of ontology + base data, and the triples
it adds beyond its input go into graph/inferred-rdfs. What it derives is
exactly what the axioms license: the inverse of evolvesInto, plus class
membership from rdfs:domain and rdfs:range.

The rest of what the rules produce is out of reach for it. A matchup needs
arithmetic (the damage formula) and isFullyEvolved needs negation as failure
(no successor exists). OWL is monotonic and open-world and has neither, so the
absence of a fact is never evidence. Running both makes that boundary visible;
reasoning/README.md tabulates the result.

Run:  make entail
"""
from __future__ import annotations

import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from owlrl import DeductiveClosure, OWLRL_Semantics
from rdflib import Graph, OWL, RDF, URIRef

from pokekg import settings as S
from pokekg import store

ONTOLOGY = S.ONTOLOGY_DIR / "pokemon.ttl"
BASE = S.RDF_DIR / "base.ttl"


def entailments() -> Graph:
    """Closure of ontology + data, minus what was already asserted."""
    asserted = Graph().parse(ONTOLOGY, format="turtle").parse(BASE, format="turtle")
    closed = Graph()
    for triple in asserted:
        closed.add(triple)
    DeductiveClosure(OWLRL_Semantics).expand(closed)

    new = Graph()
    for prefix, namespace in (("pk", S.PK), ("pkr", S.PKR)):
        new.bind(prefix, namespace)
    for subject, predicate, obj in closed:
        if (subject, predicate, obj) in asserted:
            continue
        # OWL-RL derives datatype axioms with literal subjects, which are not
        # legal RDF and which Fuseki rejects; and it restates the W3C
        # vocabularies about themselves. Keep only what it says about ours.
        if not isinstance(subject, URIRef) or not str(subject).startswith(S.BASE):
            continue
        # Trivially true of everything, and true before the reasoner ran.
        if predicate == OWL.sameAs and subject == obj:
            continue
        if predicate == RDF.type and obj in (OWL.Thing, OWL.NamedIndividual):
            continue
        new.add((subject, predicate, obj))
    return new


def summarise(graph: Graph) -> None:
    """Group the derived triples by predicate, most numerous first."""
    counts: dict[str, int] = {}
    for _, predicate, _ in graph:
        key = str(predicate).replace(str(S.PK), "pk:") \
            .replace("http://www.w3.org/1999/02/22-rdf-syntax-ns#", "rdf:") \
            .replace("http://www.w3.org/2000/01/rdf-schema#", "rdfs:") \
            .replace("http://www.w3.org/2002/07/owl#", "owl:")
        counts[key] = counts.get(key, 0) + 1
    for key, count in sorted(counts.items(), key=lambda kv: -kv[1])[:10]:
        print(f"    {count:>7,}  {key}")


def main() -> int:
    if not store.is_up():
        print("Fuseki is not running  ->  make up")
        return 1
    if not BASE.exists():
        print(f"missing {BASE.relative_to(S.ROOT)}  ->  make transform")
        return 1

    started = time.perf_counter()
    derived = entailments()
    elapsed = time.perf_counter() - started

    path = S.RDF_DIR / "entailed.ttl"
    derived.serialize(path, format="turtle")
    store.clear_graph(str(S.G_RDFS))
    store.load_turtle(path, str(S.G_RDFS))

    print(f"  OWL-RL closure in {elapsed:.1f}s -> {len(derived):,} new triples\n")
    summarise(derived)
    print(f"\n  {store.count_triples(str(S.G_RDFS)):,} triples in <{S.G_RDFS}>")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
