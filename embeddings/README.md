# ML-based Representation

KG embeddings trained with a subset of the inferred matchup edges withheld,
evaluated on their ability to predict them back. Report section 3;
**LO1** (focus), LO3, LO6, LO8.

```bash
make embed          # split -> train -> evaluate
```

Needs Fuseki up (`make up`) and the rules run (`make reason`). Training takes
about seven minutes on a CPU.

## The split

`split.py` withholds **10% of the 17,663 `hasMatchupAdvantageOver` edges** for
testing and another 10% for validation, drawn at random. Every Pokémon keeps
most of its record and the model fills the gaps.

Everything that is not a matchup edge (types, moves, abilities, evolution, stat
bands) stays in training. Those describe a Pokémon rather than being the thing
predicted.

| | edges |
|---|---|
| train | 14,131 |
| validation | 1,766 |
| test | 1,766 |

Test edges are mirrored into `graph/eval-test`, so which edges were withheld is
recoverable from the store rather than only from a file.

Two things need care:

**The reified verdicts are removed.** Each matchup is stored twice: as a flat
edge and as a `pk:Matchup` node carrying `pk:attacker`/`pk:defender`. Those
restate the edge, so a model trained on them would read a withheld answer off
the node.

**Numeric literals become entities.** A KGE model scores triples between
entities and cannot see that Aerodactyl has speed 130. Each stat is binned into
a quintile band (`pkr:aerodactyl pkm:speedBand pkm:band/speed/q5`). The bands
live in the `pkm:` namespace and are never asserted into the KG, since they are
a representation choice rather than a fact about the domain.

## Results

Ranking is filtered, with candidates restricted to Pokémon; otherwise the model
gets credit for ranking a Pokémon above `pkr:tackle`. Accuracy is a balanced
yes/no task (each withheld edge against a drawn non-edge), so chance is 0.500.

| | MRR | Hits@1 | Hits@10 | MR | accuracy | ROC-AUC |
|---|---|---|---|---|---|---|
| **RotatE** | **0.615** | **0.468** | **0.907** | **4.2** | **0.902** | **0.963** |
| R-GCN | 0.489 | 0.352 | 0.779 | 8.5 | 0.874 | 0.945 |
| ComplEx | 0.401 | 0.245 | 0.758 | 8.7 | 0.842 | 0.916 |
| TransE | 0.151 | 0.000 | 0.480 | 18.4 | 0.746 | 0.828 |

RotatE is the model that ships. The true opponent lands in the top 10 for
**91%** of withheld edges, with a mean rank of 4.2 out of 365 candidates.

The ordering follows from the relation's algebra. `hasMatchupAdvantageOver` is
strictly antisymmetric: of 17,663 edges, **zero** pairs hold in both directions.
DistMult scores `Σ hᵢrᵢtᵢ`, which is symmetric, so it cannot represent the
relation at all and was not run. TransE and RotatE both can; ComplEx can in
principle. `KGE_MODEL=TransE make train` switches models.

## The GNN

The other three models learn one vector per entity, looked up from a table.
**R-GCN computes** it instead, by passing messages along the entity's own edges:

    h(aggron) = sigma( sum over neighbours of W_r * h(neighbour) )
                       rock, steel, iron-tail, band/defense/q5, ...

The `W_r` are shared by every entity, so the graph's structure is the shape of
the network, which is what LO3 describes. PyKEEN defaults R-GCN to a DistMult
decoder; that scores symmetrically and so cannot represent this relation, so
`train.py` overrides it to a rotation.

It reaches MRR 0.489, second of the four, and does not beat RotatE. That is the
expected result for this split, which leaves every Pokémon 80% of its edges in
training. Composing an entity from its neighbours has least to add over a
well-fitted lookup precisely there. A GNN earns its keep on entities with few or
no edges, the cold-start case this split does not test and the direction named
under Limitations.

## How it evolves the KG

Completion rather than correction. `evaluate.py` scores every ordered pair
absent from training and validation (**19,635** of them: the withheld edges plus
every pair the rules gave no verdict for) and writes those above its threshold
into `graph/predicted-matchup`: **2,222 edges, 62.2% correct**, recovering 1,383
of the 1,766 withheld. Nothing already asserted is changed or removed.

The publication threshold is calibrated separately from the accuracy figures
above. Those come from a balanced task; completion is only ~9% positive, so a
cut chosen for accuracy floods the graph with false edges. It is picked by F1
on validation candidates built exactly like the completion candidates.

Predictions land beside the rule-derived `graph/inferred-matchup` rather than
inside it, so a consumer can ask for verdicts the rules proved, verdicts the
model guessed, or both. Each carries a `pkm:confidence`, its rank in the batch,
which is an ordering rather than a probability. Provenance (`pkm:producedBy`,
`pkm:generatedOn`) is asserted once about the graph.

`results/examples.md` shows five entities as the model holds them, plus a
recovered edge and a pair the model endorses that the graph does not assert.
That second one is worth reading closely: an unasserted pair can mean the rules
gave the win to the other side, or that they named no winner at all, and the
file says which. A tie in knock-out turns produces no edge in either direction,
so the model can be marked wrong on a matchup the rules never judged.

## Limitations

**Volume.** Training is linear in edges, but the target relation is quadratic
in Pokémon: 189 give 35,532 ordered pairs, a full national dex would give over
a million. Scoring all candidates at completion time grows the same way.

**Veracity.** The labels are themselves inferred. The model imitates the
Generation 3 damage formula as the rules apply it, and inherits every
simplification in them: no items, no switching, no status moves.

**Variety.** Adding a relation is cheap; adding a literal that matters is not,
because it needs a binning decision, and each is a judgement about resolution.

**Little training data.** Every entity here appears in training, so each has a
trained vector. A genuinely new Pokémon, absent from training altogether, would
have no embedding and could not be scored at all. Supporting that needs an
inductive model that composes an entity from its neighbours. R-GCN is the
encoder for it, but PyKEEN trains it transductively here, so the entity must
still be present at training time.

## Files

| | |
|---|---|
| `src/split.py` | withholds the edges, writes the triple files and `eval-test` |
| `src/train.py` | trains one model, saves the checkpoint |
| `src/evaluate.py` | ranks the withheld edges, writes examples and predictions |
| `data/` | `train.tsv`, `valid.tsv`, `test.tsv` (generated) |
| `results/` | split summary, metrics, `examples.md` |
| `models/` | trained checkpoints (generated) |
