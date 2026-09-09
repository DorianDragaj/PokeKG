"""Thin client for the Fuseki triplestore.

Everything goes over the SPARQL 1.1 Protocol and the Graph Store Protocol via
plain HTTP: query() for SELECT/ASK, update() for INSERT/DELETE, load_turtle()
to bulk load a named graph.
"""
from __future__ import annotations

import time
from pathlib import Path
from typing import Any

import requests

from pokekg import settings as S


class StoreError(RuntimeError):
    """Raised when Fuseki answers with a non-2xx status."""


def _auth() -> tuple[str, str]:
    return (S.FUSEKI_USER, S.FUSEKI_PASSWORD)


def is_up(timeout: float = 2.0) -> bool:
    try:
        return requests.get(S.PING_ENDPOINT, timeout=timeout).ok
    except requests.RequestException:
        return False


def wait_until_up(timeout: float = 90.0, interval: float = 2.0) -> None:
    """Block until Fuseki answers /$/ping, or raise."""
    deadline = time.time() + timeout
    while time.time() < deadline:
        if is_up():
            return
        time.sleep(interval)
    raise StoreError(
        f"Fuseki did not become ready at {S.FUSEKI_HOST} within {timeout:.0f}s. "
        f"Is it running?  ->  make up"
    )


def query(sparql: str, timeout: float = 300.0) -> list[dict[str, Any]]:
    """Run a SELECT/ASK query and return the rows as dicts of Python values."""
    resp = requests.post(
        S.QUERY_ENDPOINT,
        data=(S.SPARQL_PREFIXES + "\n" + sparql).encode("utf-8"),
        headers={
            "Content-Type": "application/sparql-query; charset=utf-8",
            "Accept": "application/sparql-results+json",
        },
        auth=_auth(),
        timeout=timeout,
    )
    if not resp.ok:
        raise StoreError(f"SPARQL query failed ({resp.status_code}): {resp.text[:800]}")
    payload = resp.json()
    if "boolean" in payload:                      # ASK
        return [{"boolean": payload["boolean"]}]
    return [
        {var: _term_to_python(binding[var]) for var in binding}
        for binding in payload["results"]["bindings"]
    ]


def ask(sparql: str) -> bool:
    return bool(query(sparql)[0]["boolean"])


def scalar(sparql: str, var: str = "n") -> Any:
    """Single-value queries such as COUNT."""
    rows = query(sparql)
    return rows[0][var] if rows else None


_NUMERIC = {
    "http://www.w3.org/2001/XMLSchema#integer": int,
    "http://www.w3.org/2001/XMLSchema#int": int,
    "http://www.w3.org/2001/XMLSchema#long": int,
    "http://www.w3.org/2001/XMLSchema#decimal": float,
    "http://www.w3.org/2001/XMLSchema#double": float,
    "http://www.w3.org/2001/XMLSchema#float": float,
    "http://www.w3.org/2001/XMLSchema#boolean": lambda v: v == "true",
}


def _term_to_python(term: dict[str, str]) -> Any:
    value = term["value"]
    caster = _NUMERIC.get(term.get("datatype", ""))
    return caster(value) if caster else value


def update(sparql: str, timeout: float = 600.0) -> None:
    """Run a SPARQL 1.1 Update (INSERT/DELETE/CLEAR/...)."""
    resp = requests.post(
        S.UPDATE_ENDPOINT,
        data=(S.SPARQL_PREFIXES + "\n" + sparql).encode("utf-8"),
        headers={"Content-Type": "application/sparql-update; charset=utf-8"},
        auth=_auth(),
        timeout=timeout,
    )
    if not resp.ok:
        raise StoreError(f"SPARQL update failed ({resp.status_code}): {resp.text[:800]}")


def clear_graph(graph_iri: str) -> None:
    update(f"CLEAR SILENT GRAPH <{graph_iri}>")


def drop_all() -> None:
    """Empty the whole dataset (all named graphs + default graph)."""
    update("DROP SILENT ALL")


def load_turtle(path: str | Path, graph_iri: str, replace: bool = True) -> None:
    """Upload a Turtle file into one named graph. PUT replaces it, POST appends."""
    path = Path(path)
    with path.open("rb") as handle:
        resp = requests.request(
            "PUT" if replace else "POST",
            S.GSP_ENDPOINT,
            params={"graph": graph_iri},
            data=handle,
            headers={"Content-Type": "text/turtle; charset=utf-8"},
            auth=_auth(),
            timeout=600,
        )
    if not resp.ok:
        raise StoreError(
            f"Graph Store upload of {path.name} -> <{graph_iri}> failed "
            f"({resp.status_code}): {resp.text[:800]}"
        )


def count_triples(graph_iri: str | None = None) -> int:
    if graph_iri is None:
        return int(scalar("SELECT (COUNT(*) AS ?n) WHERE { ?s ?p ?o }"))
    return int(
        scalar(
            f"SELECT (COUNT(*) AS ?n) WHERE {{ GRAPH <{graph_iri}> {{ ?s ?p ?o }} }}"
        )
    )


def graph_sizes() -> dict[str, int]:
    """Triple count per named graph, largest first."""
    rows = query(
        "SELECT ?g (COUNT(*) AS ?n) WHERE { GRAPH ?g { ?s ?p ?o } } "
        "GROUP BY ?g ORDER BY DESC(?n)"
    )
    return {row["g"]: int(row["n"]) for row in rows}


def describe_store() -> str:
    """Status block printed by `make status`."""
    lines = [f"Fuseki      : {S.FUSEKI_HOST}  (dataset '{S.FUSEKI_DATASET}')"]
    if not is_up():
        lines.append("Status      : DOWN  -> run `make up`")
        return "\n".join(lines)
    sizes = graph_sizes()
    lines.append("Status      : UP")
    lines.append(f"Triples     : {count_triples():,} total across {len(sizes)} named graph(s)")
    for iri, n in sizes.items():
        lines.append(f"  {n:>8,}  <{iri}>")
    if not sizes:
        lines.append("  (empty - nothing loaded yet)")
    return "\n".join(lines)


if __name__ == "__main__":
    print(describe_store())
