# Logic-based Representation

Declarative SPARQL rules that derive what the extracted data only implies.
Every rule is a SPARQL 1.1 Update that reads the union of the loaded graphs and
writes into an inference graph, so the reasoning happens inside the triplestore
rather than in a Python post-process.

Report section 4 (Logic-based Representation); **LO2** (focus), **LO6**, **LO8**.

```bash
make reason
```

`src/rules.py` is the driver. It empties each inference graph before the rules
that write to it run, which makes the step idempotent. Rule files name their
target graph with a placeholder rather than a literal IRI, so the graph names
stay defined in `pokekg/settings.py` alone.

## `rules/01-evolution.ru` -> `graph/inferred-recursive`

The data states one thing about evolution: `charmander evolvesInto charmeleon`.
Three rules derive the rest.

| rule | technique |
|---|---|
| `evolvesFrom` | the `owl:inverseOf` axiom written as an explicit rule |
| `evolvesIntoEventually` | the `evolvesInto+` property path, SPARQL's own recursion |
| `isFullyEvolved` | `FILTER NOT EXISTS`, negation as failure |

The third is the one that could not be an ontology axiom. RDFS and OWL are
open-world, where a missing fact means unknown; concluding "no successor, so
this line ends here" reads the absence as a fact and needs a rule.

778 triples, well under a second.

## `rules/02-matchup.ru` -> `graph/inferred-matchup`

Answers the project's question, in five passes: type effectiveness into a
scratch graph, each side's best move and the hits it needs, the verdict for the
pair, the move that carried the win, then the scratch graph is dropped.

### The damage model

The real Generation 3 formula, at level 100 with 31 IVs, no EVs and a neutral
nature:

```
((2 x Level / 5 + 2) x power x A / D) / 50 + 2     then x STAB x effectiveness
```

PokéAPI reports base stats, so they are converted to level-100 stats first:
HP is `2 x base + 141`, every other stat `2 x base + 36`. Which stat pair
applies follows the move's *type* rather than the move itself, as it did before
Generation 4. Damage is scaled by 0.925, the mean of the game's 85-100% random
roll, and by the move's accuracy, making it an expected value per turn.

### The verdict

The verdict is decided on knock-outs rather than raw damage. `ceil(HP / damage)`
gives the hits each side needs, and fewer hits wins. Moving first is worth half
a turn, which settles the matchups where both sides need the same number of
hits:

```
effective turns = hits - 0.5 if faster
advantageScore  = (defender - attacker) / (defender + attacker)   in (0, 1)
```

Because the score is derived from the same quantity that decides the winner, an
edge and its magnitude can never disagree, and two Pokémon can never both hold
an advantage. A side that cannot damage the other at all is given 999 hits, so
immunity reads as a hopeless matchup rather than a division by zero.

Each verdict is reified so the arithmetic can be checked by hand:

```turtle
pkr:matchup/charizard-vs-venusaur
    a pk:Matchup ;
    pk:attacker       pkr:charizard ;
    pk:defender       pkr:venusaur ;
    pk:attackerDamage 243.88 ;  # Flamethrower, STAB x1.5, x2 on Grass
    pk:attackerTurns  2 ;       # Venusaur has 301 HP at level 100
    pk:defenderDamage 40.8 ;
    pk:defenderTurns  8 ;
    pk:movesFirst     true ;
    pk:advantageScore 0.6842 ;
    pk:bestMove       pkr:flamethrower .

pkr:charizard pk:hasMatchupAdvantageOver pkr:venusaur .
```

17,663 verdicts over 189 Pokémon, about a minute.

### Multiplying without a PRODUCT aggregate

The type chart is stated type against type, but a matchup needs one figure for
a whole Pokémon, and a dual-typed defender folds two entries into one: Fire
against Venusaur is (x2 for Grass) x (x1 for Poison).

That is a product over a group, and SPARQL has no `PRODUCT` aggregate; the five
it defines are `SUM`, `MIN`, `MAX`, `AVG` and `COUNT`. The rule therefore adds
exponents instead of multiplying factors: super effective is `+1`, resisted
`-1`, neutral `0`, and `math:pow(2, ?total)` converts back. Two defending types
give a sum in `[-2, 2]`, that is x0.25 to x4.

Immunity is not a point on that scale, so it cannot be summed. It rides
alongside as `MIN` over a 0/1 flag, which drops to 0 as soon as any one
defending type, or the defender's ability such as Levitate against Ground,
blanks the move outright.

`math:pow` is not standard SPARQL; it comes from the XPath function library
that Jena exposes. Two alternatives work and were checked against it:
`math:exp(SUM(math:log(?factor)))` is a genuine `PRODUCT` built out of `SUM`
and generalises to any factors, and a plain self-join over the defender's two
types avoids aggregation altogether. All three agree on every case tested.

### Why a scratch graph

Effectiveness is needed twice, once to find the best move and once to name it,
so it is computed once into `graph/scratch` and dropped in the final pass. It is
working space rather than knowledge, and does not survive the run.

## The same job, done declaratively

`make entail` runs `owlrl` over ontology + base data and puts what it derives
into `graph/inferred-rdfs`, so the two reasoning styles can be compared on the
same input. The closure takes 3.3s and yields 325 triples.

| derived | OWL-RL | SPARQL rules |
|---|---|---|
| `pk:evolvesFrom` | 184 | 184, the same 184 |
| `pk:evolvesIntoEventually` | 0 | 229 |
| `pk:isFullyEvolved` | 0 | 365 |
| `pk:hasMatchupAdvantageOver` | 0 | 17,663 |

The first row is the case for ontologies. `pk:evolvesInto owl:inverseOf
pk:evolvesFrom` is one line of Turtle, and a reasoner produces every inverse
edge from it: no rule to write, no rule to keep correct. Rule 01 computes the
identical 184 triples procedurally, and only exists so the graph does not
depend on a reasoner at query time.

The three zeros mark the boundary between the two styles, and each has a
different cause.

**Transitivity derived nothing.** The ontology declares
`pk:evolvesIntoEventually a owl:TransitiveProperty`, which licenses closure over
*asserted* triples of that property, and the base data asserts none, because the
property exists only to hold the closure. The axiom is therefore inert. Getting
it declaratively would mean making `evolvesInto` a subproperty of a transitive
property; the rules instead use SPARQL's `+` path over `evolvesInto`, which
needs no axiom at all.

**`isFullyEvolved` cannot be derived by OWL in principle.** It means *no
successor exists*, and OWL is open-world and monotonic: an absent fact is
unknown, never false. The rule reads absence as evidence, which is negation as
failure, a different logic rather than a better one.

**Matchups need arithmetic.** The damage formula multiplies, divides and takes
a ceiling. OWL has no arithmetic, so no axiom set can express a matchup.

That boundary is why the project uses SPARQL rules as its primary reasoning
and keeps the ontology for what ontologies are good at: saying what is true
once, and letting the inverse edges follow.
