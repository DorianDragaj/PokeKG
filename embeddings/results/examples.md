# How RotatE represents this graph

Every entity is a point in 128-dimensional space and every relation is a
transformation between such points. The model never sees an IRI, a label or a
number - only which triples exist.

## Five things, as the model holds them

1. `pkr:charizard` -> `[+0.216+0.418i, -0.548-0.427i, -0.055+0.205i, -0.222+0.986i, ... ] (128 values)`
   A Pokemon, learned from its types, moves, bands and 80% of its matchups

2. `pkr:fire` -> `[-0.080-0.563i, -0.042+0.089i, -0.009-0.140i, +0.219+1.019i, ... ] (128 values)`
   A type, learned from the type chart and every Pokemon carrying it

3. `pkr:flamethrower` -> `[-0.490-0.387i, +0.069-0.012i, -0.163+0.236i, +0.225-0.869i, ... ] (128 values)`
   A move, learned from its type, damage class and who knows it

4. `pkm:band/speed/q5` -> `[-0.683-0.540i, +0.065-0.487i, -0.409+0.807i, -0.399+0.790i, ... ] (128 values)`
   A stat band - the literal speed 130 became an entity

5. `pkr:levitate` -> `[+0.087+0.220i, +0.330-0.387i, +0.103+0.096i, -0.657+0.520i, ... ] (128 values)`
   An ability, tied to the type it nullifies

The edge `pk:hasMatchupAdvantageOver` is not stored per pair. It is one transformation
`[-0.231+0.973i, -0.232+0.973i, -0.279-0.960i, -0.401+0.916i, ... ]` applied to whichever attacker vector it is given; the
prediction is how close the result lands to the defender vector.

## A withheld edge the model recovers (true positive)

`pkr:aerodactyl pk:hasMatchupAdvantageOver pkr:vileplume`  score -7.312 (threshold -8.855) - scored as a win, and the rules agree.

- pkr:aerodactyl: flying/rock, speed 130
- pkr:vileplume: grass/poison, speed 50

## A pair the model endorses that the rules left undecided

`pkr:machamp pk:hasMatchupAdvantageOver pkr:ursaring`  score -7.862 (threshold -8.855) - scored as a win, but the rules named no winner.

- pkr:machamp: fighting, speed 55
- pkr:ursaring: normal, speed 55

Neither side knocks the other out in fewer turns and neither is faster, so
`02-matchup.ru` writes no edge either way. The pair still scores against the
model: the negative class is every pair the rules left unasserted, the ones
they decided against and the ones they never decided alike.
