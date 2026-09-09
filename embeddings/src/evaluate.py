"""Score the embedding models on the withheld matchup edges.

The model is scored two ways. Ranking puts the true opponent against every
other Pokemon with all known edges filtered out; candidates are restricted to
Pokemon, or the model would get credit for outranking pkr:tackle.
Classification pairs each withheld edge with a drawn non-edge, so chance is 50%
and the number is readable; its threshold is fitted on validation rather than
on the test set it is applied to.

The best model then writes its answers back into the graph, so evaluation and
KG update happen in one step.

Run:  make evaluate
"""
from __future__ import annotations

import json
import random
import sys
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

import torch
from pykeen.evaluation import RankBasedEvaluator
from pykeen.triples import TriplesFactory

from pokekg import settings as S
from pokekg import store

SEED = 20260906
MATCHUP = str(S.PK.hasMatchupAdvantageOver)
METRICS = [("mean_reciprocal_rank", "MRR"), ("hits_at_1", "Hits@1"),
           ("hits_at_3", "Hits@3"), ("hits_at_10", "Hits@10"),
           ("arithmetic_mean_rank", "MR")]


def edges_of(name: str) -> list[tuple[str, str]]:
    """The matchup edges in one split file, as (head, tail)."""
    rows = (line.split("\t") for line in
            (S.ML_DATA / f"{name}.tsv").read_text(encoding="utf-8").splitlines() if line)
    return [(h, t.strip()) for h, r, t in rows if r == MATCHUP]


def load_model(name: str):
    """Whole-module checkpoints, so torch must be told to unpickle them."""
    return torch.load(S.ML_MODELS / f"{name}.pt", map_location="cpu",
                      weights_only=False).eval()


def factories() -> tuple[TriplesFactory, TriplesFactory, TriplesFactory]:
    train = TriplesFactory.from_path(S.ML_DATA / "train.tsv")
    rest = [TriplesFactory.from_path(S.ML_DATA / f"{n}.tsv",
                                     entity_to_id=train.entity_to_id,
                                     relation_to_id=train.relation_to_id)
            for n in ("valid", "test")]
    return train, *rest


def pokemon_ids(train: TriplesFactory) -> list[int]:
    """Plain ints: the evaluator membership-checks these against a set, and
    tensors hash by identity, so a tensor silently matches nothing."""
    type_id = train.relation_to_id[str(S.PK.hasType)]
    heads = train.mapped_triples[train.mapped_triples[:, 1] == type_id][:, 0]
    return sorted(int(i) for i in torch.unique(heads))


def ranking(model, test, filters, candidates) -> dict:
    result = RankBasedEvaluator(filtered=True).evaluate(
        model=model, mapped_triples=test.mapped_triples,
        additional_filter_triples=filters, restrict_entities_to=candidates,
        batch_size=256, device="cpu", use_tqdm=False)
    return {key: result.get_metric(key) for key, _ in METRICS}


def score(model, factory: TriplesFactory, pairs) -> torch.Tensor:
    relation = factory.relation_to_id[MATCHUP]
    ids = torch.tensor([[factory.entity_to_id[h], relation, factory.entity_to_id[t]]
                        for h, t in pairs], dtype=torch.long)
    with torch.no_grad():
        return torch.cat([model.score_hrt(chunk).flatten() for chunk in ids.split(4096)])


def balanced_pairs(positives, known, fighters, rng) -> tuple[list, torch.Tensor]:
    """Every held-out edge, plus an equal number of drawn non-edges."""
    negatives, seen = [], set(positives)
    while len(negatives) < len(positives):
        one, two = rng.choice(fighters), rng.choice(fighters)
        if one != two and (one, two) not in known and (one, two) not in seen:
            seen.add((one, two))
            negatives.append((one, two))
    return positives + negatives, torch.tensor(
        [1.0] * len(positives) + [0.0] * len(negatives))


def best_threshold(values: torch.Tensor, labels: torch.Tensor, by: str = "accuracy") -> float:
    """Cut that maximises accuracy on a balanced set, or F1 on a skewed one.

    Accuracy is the right target when half the pairs are true, because always
    saying yes then scores 0.5 and has to be beaten. It is the wrong target
    when only 9% are true - always saying no would score 0.91 - so the
    publication cut is chosen by F1 instead.
    """
    order = values.argsort(descending=True)
    positives = labels.sum()
    taken = torch.arange(1, len(order) + 1, dtype=torch.float)
    hits = labels[order].cumsum(0)
    if by == "f1":
        objective = 2 * hits / (taken + positives)
    else:
        objective = (hits + (len(order) - positives) - (taken - hits)) / len(order)
    return float(values[order][int(objective.argmax())])


def classify(values: torch.Tensor, labels: torch.Tensor, cut: float) -> dict:
    predicted, actual = values >= cut, labels.bool()
    tp = int((predicted & actual).sum())
    fp = int((predicted & ~actual).sum())
    fn = int((~predicted & actual).sum())
    precision = tp / (tp + fp) if tp + fp else 0.0
    recall = tp / (tp + fn) if tp + fn else 0.0
    ranks = values.argsort().argsort().float() + 1
    pos, neg = labels.sum(), len(labels) - labels.sum()
    return {
        "accuracy": round(float((predicted == actual).float().mean()), 4),
        "precision": round(precision, 4), "recall": round(recall, 4),
        "roc_auc": round(float((ranks[actual].sum() - pos * (pos + 1) / 2) / (pos * neg)), 4),
        "threshold": round(cut, 4),
    }


def short(iri: str) -> str:
    for prefix, namespace in (("pk:", S.PK), ("pkr:", S.PKR), ("pkm:", S.PKM)):
        if iri.startswith(str(namespace)):
            return prefix + iri[len(str(namespace)):]
    return iri


def facts(pokemon: str) -> str:
    rows = store.query(S.SPARQL_PREFIXES + f"""
        SELECT (GROUP_CONCAT(DISTINCT ?t; separator="/") AS ?types) ?speed
        FROM <{S.G_BASE}> WHERE {{
            <{pokemon}> pk:hasType ?ty ; pk:speed ?speed .
            BIND(REPLACE(STR(?ty), "^.*/", "") AS ?t) }} GROUP BY ?speed""")
    return f"{rows[0]['types']}, speed {rows[0]['speed']}" if rows else "?"


def write_examples(name, model, factory, known, pairs, values, labels, cut) -> None:
    """The five representations and the two predictions the guide asks for."""
    entity = model.entity_representations[0]
    relation = model.relation_representations[0]

    def numbers(values_, count=4):
        if values_.is_complex():
            return ", ".join(f"{v.real:+.3f}{v.imag:+.3f}i" for v in values_[:count])
        return ", ".join(f"{float(v):+.3f}" for v in values_[:count])

    def vector(iri):
        v = entity(indices=torch.tensor([factory.entity_to_id[iri]])).detach().flatten()
        return f"[{numbers(v)}, ... ] ({v.numel()} values)"

    ranked = sorted(zip(pairs, values.tolist(), labels.tolist()), key=lambda r: -r[1])
    positive = next(r for r in ranked if r[2] == 1.0)
    negative = next(r for r in ranked if r[2] == 0.0)
    dim = entity(indices=None).shape[-1]

    lines = [f"# How {name} represents this graph", "",
             f"Every entity is a point in {dim}-dimensional space and every relation is a",
             "transformation between such points. The model never sees an IRI, a label or a",
             "number - only which triples exist.", "",
             "## Five things, as the model holds them", ""]
    for index, (key, why) in enumerate([
        ("charizard", "A Pokemon, learned from its types, moves, bands and 80% of its matchups"),
        ("fire", "A type, learned from the type chart and every Pokemon carrying it"),
        ("flamethrower", "A move, learned from its type, damage class and who knows it"),
        ("band/speed/q5", "A stat band - the literal speed 130 became an entity"),
        ("levitate", "An ability, tied to the type it nullifies"),
    ], 1):
        iri = str(S.PKM[key] if key.startswith("band/") else S.PKR[key])
        if iri in factory.entity_to_id:
            lines += [f"{index}. `{short(iri)}` -> `{vector(iri)}`", f"   {why}", ""]

    rel = relation(indices=torch.tensor([factory.relation_to_id[MATCHUP]])).detach().flatten()
    lines += [
        f"The edge `{short(MATCHUP)}` is not stored per pair. It is one transformation",
        f"`[{numbers(rel)}, ... ]` applied to whichever attacker vector it is given; the",
        "prediction is how close the result lands to the defender vector.", "",
        "## A withheld edge the model recovers (true positive)", ""]

    # The negative class is "no asserted edge", and that covers two different
    # cases: the rules gave the win to the other side, or they named no winner
    # at all. Equal knock-out turns at equal speed leaves no edge in either
    # direction, and calling that a false positive marks the model wrong on a
    # question the rules declined to answer. So the heading says which case
    # this example is.
    contested = (negative[0][1], negative[0][0]) in known
    heading = ("## A pair the model wrongly endorses (false positive)" if contested else
               "## A pair the model endorses that the rules left undecided")
    for label, (pair, value, _), note in (
            ("tp", positive, "and the rules agree"),
            ("fp", negative, "but the rules give the win to the other side" if contested
             else "but the rules named no winner")):
        head, tail = pair
        if label == "fp":
            lines += [heading, ""]
        lines += [f"`{short(head)} {short(MATCHUP)} {short(tail)}`  score {value:+.3f} "
                  f"(threshold {cut:+.3f}) - scored as a win, {note}.", "",
                  f"- {short(head)}: {facts(head)}", f"- {short(tail)}: {facts(tail)}", ""]
    if not contested:
        lines += [
            "Neither side knocks the other out in fewer turns and neither is faster, so",
            "`02-matchup.ru` writes no edge either way. The pair still scores against the",
            "model: the negative class is every pair the rules left unasserted, the ones",
            "they decided against and the ones they never decided alike.", ""]
    (S.ML_RESULTS / "examples.md").write_text("\n".join(lines), encoding="utf-8")


def publish(name, model, factory, seen, truth, fighters, cut) -> tuple[int, int, int]:
    """Completion: score every pair the model was not told about, keep the wins.

    Candidates are all ordered pairs absent from training and validation - the
    withheld edges plus every pair the rules gave no verdict for. Scoring only
    the *unasserted* pairs would be meaningless: at 49.7% density an unasserted
    pair almost always means the reverse edge holds, so each such prediction
    would contradict the rules.

    Predictions land in their own graph beside the rule-derived one. Confidence
    is the prediction's rank within the batch, so it orders predictions rather
    than calibrating them.
    """
    candidates = [(one, two) for one in fighters for two in fighters
                  if one != two and (one, two) not in seen]
    values = score(model, factory, candidates)
    chosen = sorted(((pair, float(v)) for pair, v in zip(candidates, values) if v >= cut),
                    key=lambda row: row[1])
    store.clear_graph(str(S.G_PREDICTED))
    triples = [f'<{S.G_PREDICTED}> <{S.PKM.producedBy}> "{name}" .',
               f'<{S.G_PREDICTED}> <{S.PKM.generatedOn}> '
               f'"{date.today().isoformat()}"^^<http://www.w3.org/2001/XMLSchema#date> .']
    for index, ((head, tail), _) in enumerate(chosen, start=1):
        node = S.PKM[f"prediction/{head.rsplit('/', 1)[-1]}-vs-{tail.rsplit('/', 1)[-1]}"]
        triples += [
            f"<{head}> <{S.PK.hasMatchupAdvantageOver}> <{tail}> .",
            f"<{node}> <{S.PK.attacker}> <{head}> .",
            f"<{node}> <{S.PK.defender}> <{tail}> .",
            f'<{node}> <{S.PKM.confidence}> "{round(index / len(chosen), 4)}"'
            f'^^<http://www.w3.org/2001/XMLSchema#decimal> .']
    for start in range(0, len(triples), 2000):
        store.update(f"INSERT DATA {{ GRAPH <{S.G_PREDICTED}> "
                     f"{{ {' '.join(triples[start:start + 2000])} }} }}")
    correct = sum(1 for pair, _ in chosen if pair in truth)
    return len(candidates), len(chosen), correct


def main() -> int:
    if not store.is_up():
        print("Fuseki is not running  ->  make up")
        return 1
    checkpoints = sorted(p.stem for p in S.ML_MODELS.glob("*.pt"))
    if not checkpoints:
        print("no trained model  ->  make train")
        return 1

    train, valid, test = factories()
    candidates = pokemon_ids(train)
    edges = {name: edges_of(name) for name in ("train", "valid", "test")}
    known = {(h, t) for part in edges.values() for h, t in part}
    fighters = sorted({p for pair in known for p in pair})

    rng = random.Random(SEED)
    pairs, labels = {}, {}
    for name in ("valid", "test"):
        pairs[name], labels[name] = balanced_pairs(edges[name], known, fighters, rng)

    print(f"  {train.num_entities} entities, {train.num_relations} relations, "
          f"{train.num_triples:,} training triples")
    print(f"  {test.num_triples:,} withheld edges, ranked against {len(candidates)} Pokemon\n")

    models, report = {}, {}
    for name in checkpoints:
        models[name] = load_model(name)
        cut = best_threshold(score(models[name], train, pairs["valid"]), labels["valid"])
        values = score(models[name], train, pairs["test"])
        report[name] = {
            **{k: round(v, 4) for k, v in
               ranking(models[name], test, [train.mapped_triples, valid.mapped_triples],
                       candidates).items()},
            **classify(values, labels["test"], cut),
        }

    head = f"  {'model':<12}" + "".join(f"{lbl:>9}" for _, lbl in METRICS) + f"{'acc':>8}{'AUC':>8}"
    print(head + "\n  " + "-" * (len(head) - 2))
    for name, values in report.items():
        row = f"  {name:<12}"
        for key, _ in METRICS:
            row += (f"{values[key]:>9.1f}" if key == "arithmetic_mean_rank"
                    else f"{values[key]:>9.3f}")
        print(row + f"{values['accuracy']:>8.3f}{values['roc_auc']:>8.3f}")
    print("\n  chance on the balanced task: 0.500 accuracy")

    best = max(report, key=lambda n: report[n]["accuracy"])
    model = models[best]
    write_examples(best, model, train, known, pairs["test"],
                   score(model, train, pairs["test"]), labels["test"],
                   report[best]["threshold"])

    # The publication cut is calibrated on the task it is used for. Validation
    # candidates are built like the completion candidates - every pair absent
    # from training - so the class balance matches (about 9% true) rather than
    # the balanced 50% the accuracy column is measured on.
    train_edges = set(edges["train"])
    valid_candidates = [(one, two) for one in fighters for two in fighters
                        if one != two and (one, two) not in train_edges]
    valid_truth = torch.tensor([float(pair in known) for pair in valid_candidates])
    publish_cut = best_threshold(score(model, train, valid_candidates), valid_truth, by="f1")

    considered, written, correct = publish(
        best, model, train, train_edges | set(edges["valid"]), known, fighters, publish_cut)
    report[best]["edges_written"] = written
    report[best]["edges_correct"] = correct
    print(f"\n  {best} judged {considered:,} pairs it was never shown and added "
          f"{written:,} edges to <{S.G_PREDICTED}>, {correct:,} correct "
          f"({correct / written:.1%})")

    (S.ML_RESULTS / "link-prediction.json").write_text(
        json.dumps({"seed": SEED, "results": report}, indent=2), encoding="utf-8")
    print(f"  metrics   -> {S.ML_RESULTS / 'link-prediction.json'}")
    print(f"  examples  -> {S.ML_RESULTS / 'examples.md'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
