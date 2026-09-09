"""Withhold a subset of the matchup edges, 80/10/10 at random.

Everything that is not a matchup edge - types, moves, abilities, evolution,
stat bands - stays in training. Those describe a Pokemon rather than being
the thing predicted.

Two details decide whether the split is sound. The reasoning layer stores each
verdict twice, as a flat edge and as a reified pk:Matchup node carrying
pk:attacker / pk:defender; those restate the edge, so a model trained on them
would read a withheld answer off the node, and they are dropped here. And a KGE
model cannot see that Aerodactyl has speed 130, so each numeric literal becomes
a quintile band entity (pkm:band/speed/q5). The bands live in the pkm:
namespace because they are a representation choice rather than KG truth.

Run:  make split
"""
from __future__ import annotations

import json
import random
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from pokekg import settings as S
from pokekg import store

SEED = 20260906
TEST_SHARE = 0.10
VALID_SHARE = 0.10
BANDS = 5

MATCHUP = str(S.PK.hasMatchupAdvantageOver)

# Read only the graphs holding ground truth. The default graph is the union of
# all of them, so without this a second run would train on its own predictions.
SOURCE = "\n".join(f"FROM <{g}>" for g in (S.G_BASE, S.G_DERIVED, S.G_MATCHUP))

FEATURE_PREDICATES = [
    S.PK.hasType, S.PK.knowsMove, S.PK.hasAbility,
    S.PK.evolvesInto, S.PK.evolvesFrom, S.PK.evolvesIntoEventually,
    S.PK.moveType, S.PK.damageClass,
    S.PK.superEffectiveAgainst, S.PK.resistedBy, S.PK.noEffectAgainst,
    S.PK.nullifiesType, S.PK.typeDamageClass,
]

BANDED = [
    (S.PK.Pokemon, S.PK.hp, "hp"),
    (S.PK.Pokemon, S.PK.attack, "attack"),
    (S.PK.Pokemon, S.PK.defense, "defense"),
    (S.PK.Pokemon, S.PK.specialAttack, "specialAttack"),
    (S.PK.Pokemon, S.PK.specialDefense, "specialDefense"),
    (S.PK.Pokemon, S.PK.speed, "speed"),
    (S.PK.Move, S.PK.power, "power"),
]


def feature_triples() -> list[tuple[str, str, str]]:
    """Structural edges, plus rdf:type for the four classes worth knowing."""
    values = " ".join(f"<{p}>" for p in FEATURE_PREDICATES)
    classes = " ".join(f"<{c}>" for c in (S.PK.Pokemon, S.PK.Move, S.PK.Type, S.PK.Ability))
    rows = store.query(S.SPARQL_PREFIXES + f"""
        SELECT ?s ?p ?o {SOURCE} WHERE {{
            {{ ?s ?p ?o . VALUES ?p {{ {values} }} }}
            UNION
            {{ ?s ?p ?o . VALUES ?p {{ rdf:type }} VALUES ?o {{ {classes} }} }}
        }}""")
    return [(str(r["s"]), str(r["p"]), str(r["o"])) for r in rows]


def band_triples() -> tuple[list[tuple[str, str, str]], dict]:
    """Bin each numeric property into quintiles and emit one edge per subject."""
    triples, boundaries = [], {}
    for cls, prop, name in BANDED:
        rows = store.query(S.SPARQL_PREFIXES + f"""
            SELECT ?s ?v {SOURCE} WHERE {{ ?s a <{cls}> ; <{prop}> ?v }}""")
        pairs = sorted(((float(r["v"]), str(r["s"])) for r in rows))
        if not pairs:
            continue
        size = len(pairs) / BANDS
        cuts = []
        for index, (value, subject) in enumerate(pairs):
            quintile = min(BANDS, int(index / size) + 1)
            triples.append((subject, str(S.PKM[f"{name}Band"]),
                            str(S.PKM[f"band/{name}/q{quintile}"])))
            if quintile > len(cuts):
                cuts.append(value)
        boundaries[name] = {"lowest": pairs[0][0], "band_starts": cuts, "highest": pairs[-1][0]}
    return triples, boundaries


def matchup_edges() -> list[tuple[str, str, str]]:
    rows = store.query(S.SPARQL_PREFIXES + f"""
        SELECT ?s ?o FROM <{S.G_MATCHUP}> WHERE {{ ?s pk:hasMatchupAdvantageOver ?o }}""")
    return sorted((str(r["s"]), MATCHUP, str(r["o"])) for r in rows)


def write_tsv(path: Path, triples) -> None:
    path.write_text("".join(f"{h}\t{r}\t{t}\n" for h, r, t in triples), encoding="utf-8")


def publish_test_graph(test_edges) -> None:
    """Mirror the held-out edges into graph/eval-test.

    They stay in inferred-matchup too - the KG is not damaged by an experiment.
    The named graph records which edges the model was denied, so the evaluation
    is reproducible from the store alone.
    """
    store.clear_graph(str(S.G_EVAL_TEST))
    for start in range(0, len(test_edges), 2000):
        chunk = " ".join(f"<{h}> <{r}> <{t}> ." for h, r, t in test_edges[start:start + 2000])
        store.update(f"INSERT DATA {{ GRAPH <{S.G_EVAL_TEST}> {{ {chunk} }} }}")


def main() -> int:
    if not store.is_up():
        print("Fuseki is not running  ->  make up")
        return 1

    features = feature_triples()
    bands, boundaries = band_triples()
    edges = matchup_edges()
    if not edges:
        print("no matchup edges in the store  ->  make reason")
        return 1

    shuffled = list(edges)
    random.Random(SEED).shuffle(shuffled)
    n_test = round(len(shuffled) * TEST_SHARE)
    n_valid = round(len(shuffled) * VALID_SHARE)
    test = shuffled[:n_test]
    valid = shuffled[n_test:n_test + n_valid]
    train = shuffled[n_test + n_valid:]

    S.ML_DATA.mkdir(parents=True, exist_ok=True)
    write_tsv(S.ML_DATA / "train.tsv", features + bands + train)
    write_tsv(S.ML_DATA / "valid.tsv", valid)
    write_tsv(S.ML_DATA / "test.tsv", test)
    publish_test_graph(test)

    fighters = {e[0] for e in edges} | {e[2] for e in edges}
    summary = {
        "seed": SEED,
        "split": "random over matchup edges",
        "shares": {"train": 1 - TEST_SHARE - VALID_SHARE,
                   "valid": VALID_SHARE, "test": TEST_SHARE},
        "triples": {"feature": len(features), "band": len(bands),
                    "matchup_total": len(edges), "train": len(train),
                    "valid": len(valid), "test": len(test)},
        "pokemon_with_matchups": len(fighters),
        "band_boundaries": boundaries,
    }
    S.ML_RESULTS.mkdir(parents=True, exist_ok=True)
    (S.ML_RESULTS / "split-summary.json").write_text(
        json.dumps(summary, indent=2), encoding="utf-8")

    print(f"  {len(features):,} feature triples   {len(bands):,} stat-band triples")
    print(f"  {len(edges):,} matchup edges over {len(fighters)} Pokemon\n")
    for name, part in (("train", train), ("valid", valid), ("test", test)):
        print(f"  {name:<6} {len(part):>6,} matchup edges  {len(part) / len(edges):5.1%}")
    print(f"\n  test edges mirrored into <{S.G_EVAL_TEST}>")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
