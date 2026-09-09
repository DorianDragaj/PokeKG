"""Train the embedding model on the graph with the held-out matchups removed.

RotatE, on the triples split.py wrote. Training stops early against the
validation edges and the best checkpoint is kept, so the saved model is not
necessarily the last epoch: it is usually an early one, because the model fits
the training matchups quickly and then drifts.

No metric is reported here. evaluate.py judges the saved checkpoints, one pair
at a time, which is the shape the matchup question actually has.

Run:  make train
"""
from __future__ import annotations

import os
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

import torch
from pykeen.pipeline import pipeline
from pykeen.triples import TriplesFactory

from pokekg import settings as S

SEED = 20260906
# Which model to train. TransE and ComplEx are the two LO1 names that share
# this triples factory; RotatE is the default because it wins (see README).
MODEL = os.environ.get("KGE_MODEL", "RotatE")

# R-GCN is a graph neural network rather than a scoring function: it builds an
# entity's vector by passing messages from its neighbours instead of looking it
# up. PyKEEN defaults it to a DistMult decoder, which scores symmetrically and
# so cannot represent an antisymmetric relation at all - hence the override.
EXTRA_KWARGS = {"RGCN": dict(interaction="rotate")}

CONFIG = dict(
    embedding_dim=128,
    num_epochs=300,
    batch_size=512,
    learning_rate=0.005,
    negatives_per_positive=32,
    patience=5,          # early-stopping evaluations without improvement
    frequency=10,        # epochs between them
)


def load_factories() -> tuple[TriplesFactory, TriplesFactory]:
    """Validation reuses the training vocabulary, so the ids line up."""
    train = TriplesFactory.from_path(S.ML_DATA / "train.tsv")
    valid = TriplesFactory.from_path(
        S.ML_DATA / "valid.tsv",
        entity_to_id=train.entity_to_id,
        relation_to_id=train.relation_to_id,
    )
    return train, valid


def main() -> int:
    if not (S.ML_DATA / "train.tsv").exists():
        print("no split yet  ->  make split")
        return 1

    train, valid = load_factories()
    print(f"  model {MODEL}")
    print(f"  {train.num_entities} entities, {train.num_relations} relations, "
          f"{train.num_triples:,} training triples")

    started = time.perf_counter()
    result = pipeline(
        training=train,
        validation=valid,
        testing=valid,          # unused; the pipeline requires a testing set
        model=MODEL,
        model_kwargs=dict(embedding_dim=CONFIG["embedding_dim"],
                          **EXTRA_KWARGS.get(MODEL, {})),
        optimizer_kwargs=dict(lr=CONFIG["learning_rate"]),
        training_kwargs=dict(num_epochs=CONFIG["num_epochs"], batch_size=CONFIG["batch_size"]),
        negative_sampler_kwargs=dict(num_negs_per_pos=CONFIG["negatives_per_positive"]),
        stopper="early",
        stopper_kwargs=dict(
            frequency=CONFIG["frequency"],
            patience=CONFIG["patience"],
            # A ranking metric, used only to decide when to stop. It is not
            # reported: evaluate.py judges the model per pair instead.
            metric="mean_reciprocal_rank",
            relative_delta=0.002,
        ),
        random_seed=SEED,
        device="cpu",
        use_tqdm=False,
        evaluation_kwargs=dict(use_tqdm=False),
    )
    elapsed = time.perf_counter() - started

    S.ML_MODELS.mkdir(parents=True, exist_ok=True)
    torch.save(result.model, S.ML_MODELS / f"{MODEL}.pt")
    print(f"  {MODEL} trained {len(result.losses)} epochs in {elapsed:.1f}s")
    print(f"  saved to {S.ML_MODELS / f'{MODEL}.pt'}  ->  make evaluate")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
