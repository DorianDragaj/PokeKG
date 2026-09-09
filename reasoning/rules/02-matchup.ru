# Matchup rules -> graph/inferred-matchup
#
#   1  effectiveness of every type against every Pokemon      (scratch)
#   2  each side's best move against each opponent            (scratch)
#   3  the verdict, one row per pair
#   4  the move that carried the win
#   5  drop the scratch graph
#
# Only fully evolved Pokemon that know a damaging move; isFullyEvolved comes
# from 01, so the rules chain.
#
# Damage is the Generation 3 formula at level 100, 31 IVs, no EVs, neutral
# nature. Base stats become real stats first: HP is 2 x base + 141, the rest
# 2 x base + 36; the 42 is the level term. Scaled by 0.925, the mean of the
# 85-100% roll, and by accuracy, so it is an expected value per turn. The stat
# pair follows the move's TYPE, not the move, as it did before Generation 4.
#
# The verdict is knock-outs: ceil(HP / damage) hits each, fewer wins, and
# moving first is worth half a turn.


# 1. Effectiveness. A dual-typed defender folds two chart entries into one -
#    Fire on Venusaur is (x2 Grass) x (x1 Poison) - and that is a product over
#    a group, which SPARQL has no aggregate for. So sum exponents instead
#    (+1 super effective, -1 resisted, 0 neutral) and let math:pow raise 2 to
#    the total. Immunity is not on that scale, so it rides alongside as MIN
#    over a 0/1 flag. Passes 2 and 4 both need this, so it is computed once.

INSERT {
    GRAPH <GRAPH_SCRATCH> {
        ?effect pk:effectOfType ?moveType ;
                pk:effectAgainst ?defender ;
                pk:multiplier ?multiplier .
    }
}
WHERE {
    {
        SELECT ?moveType ?defender (SUM(?exponent) AS ?total) (MIN(?connects) AS ?lands)
        WHERE {
            ?moveType a pk:Type .
            ?defender a pk:Pokemon ; pk:hasType ?defendingType .

            BIND(IF(EXISTS { ?moveType pk:superEffectiveAgainst ?defendingType },  1,
                 IF(EXISTS { ?moveType pk:resistedBy ?defendingType },            -1,
                                                                                   0))
                 AS ?exponent)
            BIND(IF(EXISTS { ?moveType pk:noEffectAgainst ?defendingType }
                    || EXISTS { ?defender pk:hasAbility ?ability .
                                ?ability pk:nullifiesType ?moveType },             0,
                                                                                   1)
                 AS ?connects)
        }
        GROUP BY ?moveType ?defender
    }
    # Without the cast the datatype would follow whichever branch ran:
    # math:pow returns a double, the immunity branch a decimal.
    BIND(xsd:decimal(IF(?lands = 0, 0.0, math:pow(2, ?total))) AS ?multiplier)
    BIND(IRI(CONCAT("http://example.org/pokemon-kg/resource/effect/",
                    REPLACE(STR(?moveType), "^.*/", ""), "-vs-",
                    REPLACE(STR(?defender), "^.*/", ""))) AS ?effect)
} ;


# 2. Best damage each way, and the hits it needs. Both directions are
#    different sums, so both are computed - into scratch, since which half of
#    the pair survives is not known yet.
#
#    The defender's moves are tested with FILTER EXISTS rather than matched.
#    Matching would multiply every row by the defender's move count - a million
#    rows instead of 190,000 - to answer a yes/no question.

INSERT {
    GRAPH <GRAPH_SCRATCH> {
        ?score pk:attacker ?attacker ;
               pk:defender ?defender ;
               pk:attackerDamage ?best ;
               pk:attackerTurns ?turns .
    }
}
WHERE {
    {
        SELECT ?attacker ?defender ?defenderHp (MAX(?damage) AS ?best)
        WHERE {
            GRAPH <GRAPH_SCRATCH> {
                ?effect pk:effectOfType ?moveType ;
                        pk:effectAgainst ?defender ;
                        pk:multiplier ?effectiveness .
            }

            ?attacker pk:isFullyEvolved true ; pk:knowsMove ?move ;
                      pk:attack ?baseAttack ; pk:specialAttack ?baseSpecialAttack .
            ?defender pk:isFullyEvolved true ; pk:hp ?baseHp ;
                      pk:defense ?baseDefense ; pk:specialDefense ?baseSpecialDefense .
            FILTER(?attacker != ?defender)
            FILTER EXISTS { ?defender pk:knowsMove ?anyMove }

            ?move pk:moveType ?moveType ; pk:power ?power .
            ?moveType pk:typeDamageClass ?damageClass .
            OPTIONAL { ?move pk:accuracy ?statedAccuracy }
            OPTIONAL { ?attacker pk:hasType ?moveType . BIND(1.5 AS ?matchedType) }

            BIND(2 * ?baseHp + 141 AS ?defenderHp)
            BIND(IF(?damageClass = pkr:physical, 2 * ?baseAttack  + 36,
                                                 2 * ?baseSpecialAttack + 36) AS ?offence)
            BIND(IF(?damageClass = pkr:physical, 2 * ?baseDefense + 36,
                                                 2 * ?baseSpecialDefense + 36) AS ?defence)
            BIND(COALESCE(?matchedType, 1.0)          AS ?stab)
            BIND(COALESCE(?statedAccuracy, 100) / 100 AS ?accuracy)

            BIND((42 * ?power * ?offence / ?defence) / 50 + 2 AS ?rawDamage)
            BIND(ROUND(?rawDamage * ?stab * ?effectiveness * 0.925 * ?accuracy * 100) / 100
                 AS ?damage)
        }
        GROUP BY ?attacker ?defender ?defenderHp
    }
    # A side that cannot land a hit never wins; 999 keeps it comparable.
    BIND(xsd:integer(IF(?best > 0, CEIL(?defenderHp / ?best), 999)) AS ?turns)
    BIND(IRI(CONCAT("http://example.org/pokemon-kg/resource/score/",
                    REPLACE(STR(?attacker), "^.*/", ""), "-vs-",
                    REPLACE(STR(?defender), "^.*/", ""))) AS ?score)
} ;


# 3. The verdict, once per PAIR rather than once per direction: the ordering
#    filter takes one of the two scratch rows and fetches the other by name, so
#    no pair is judged twice and no losing row has to be retracted afterwards.

INSERT {
    GRAPH <GRAPH_MATCHUP> {
        ?matchup a pk:Matchup ;
            pk:attacker ?winner ;
            pk:defender ?loser ;
            pk:attackerDamage ?winnerDamage ;
            pk:attackerTurns ?winnerTurns ;
            pk:defenderDamage ?loserDamage ;
            pk:defenderTurns ?loserTurns ;
            pk:movesFirst ?winnerMovesFirst ;
            pk:advantageScore ?score .
        ?winner pk:hasMatchupAdvantageOver ?loser .
    }
}
WHERE {
    GRAPH <GRAPH_SCRATCH> {
        ?forward pk:attacker ?one ; pk:defender ?two ;
                 pk:attackerDamage ?oneDamage ; pk:attackerTurns ?oneTurns .
    }
    FILTER(STR(?one) < STR(?two))
    BIND(IRI(CONCAT("http://example.org/pokemon-kg/resource/score/",
                    REPLACE(STR(?two), "^.*/", ""), "-vs-",
                    REPLACE(STR(?one), "^.*/", ""))) AS ?backward)
    GRAPH <GRAPH_SCRATCH> {
        ?backward pk:attackerDamage ?twoDamage ; pk:attackerTurns ?twoTurns .
    }

    ?one pk:speed ?oneSpeed .
    ?two pk:speed ?twoSpeed .
    BIND(?oneSpeed > ?twoSpeed AS ?oneMovesFirst)

    # Half a turn of credit for striking first, computed per side: a single
    # flag would hand a speed tie to whoever was tested as the attacker.
    BIND(?oneTurns - IF(?oneSpeed > ?twoSpeed, 0.5, 0.0) AS ?oneEffective)
    BIND(?twoTurns - IF(?twoSpeed > ?oneSpeed, 0.5, 0.0) AS ?twoEffective)

    BIND(?oneEffective < ?twoEffective && ?oneDamage > 0 AS ?oneWins)
    BIND(?twoEffective < ?oneEffective && ?twoDamage > 0 AS ?twoWins)
    FILTER(?oneWins || ?twoWins)

    BIND(IF(?oneWins, ?one, ?two) AS ?winner)
    BIND(IF(?oneWins, ?two, ?one) AS ?loser)
    BIND(IF(?oneWins, ?oneDamage, ?twoDamage) AS ?winnerDamage)
    BIND(IF(?oneWins, ?twoDamage, ?oneDamage) AS ?loserDamage)
    BIND(IF(?oneWins, ?oneTurns, ?twoTurns) AS ?winnerTurns)
    BIND(IF(?oneWins, ?twoTurns, ?oneTurns) AS ?loserTurns)
    BIND(IF(?oneWins, ?oneEffective, ?twoEffective) AS ?winnerEffective)
    BIND(IF(?oneWins, ?twoEffective, ?oneEffective) AS ?loserEffective)
    BIND(IF(?oneWins, ?oneSpeed > ?twoSpeed, ?twoSpeed > ?oneSpeed) AS ?winnerMovesFirst)

    BIND(ROUND(((?loserEffective - ?winnerEffective)
                / (?loserEffective + ?winnerEffective)) * 10000) / 10000 AS ?score)
    BIND(IRI(CONCAT("http://example.org/pokemon-kg/resource/matchup/",
                    REPLACE(STR(?winner), "^.*/", ""), "-vs-",
                    REPLACE(STR(?loser), "^.*/", ""))) AS ?matchup)
} ;


# 4. The move behind the verdict. Equal-damage moves both qualify.

INSERT { GRAPH <GRAPH_MATCHUP> { ?matchup pk:bestMove ?move } }
WHERE {
    GRAPH <GRAPH_MATCHUP> {
        ?matchup pk:attacker ?attacker ; pk:defender ?defender ;
                 pk:attackerDamage ?best .
    }
    GRAPH <GRAPH_SCRATCH> {
        ?effect pk:effectOfType ?moveType ;
                pk:effectAgainst ?defender ;
                pk:multiplier ?effectiveness .
    }

    ?attacker pk:knowsMove ?move ;
              pk:attack ?baseAttack ; pk:specialAttack ?baseSpecialAttack .
    ?defender pk:defense ?baseDefense ; pk:specialDefense ?baseSpecialDefense .

    ?move pk:moveType ?moveType ; pk:power ?power .
    ?moveType pk:typeDamageClass ?damageClass .
    OPTIONAL { ?move pk:accuracy ?statedAccuracy }
    OPTIONAL { ?attacker pk:hasType ?moveType . BIND(1.5 AS ?matchedType) }

    BIND(IF(?damageClass = pkr:physical, 2 * ?baseAttack  + 36,
                                         2 * ?baseSpecialAttack + 36) AS ?offence)
    BIND(IF(?damageClass = pkr:physical, 2 * ?baseDefense + 36,
                                         2 * ?baseSpecialDefense + 36) AS ?defence)
    BIND(COALESCE(?matchedType, 1.0)          AS ?stab)
    BIND(COALESCE(?statedAccuracy, 100) / 100 AS ?accuracy)

    BIND((42 * ?power * ?offence / ?defence) / 50 + 2 AS ?rawDamage)
    BIND(ROUND(?rawDamage * ?stab * ?effectiveness * 0.925 * ?accuracy * 100) / 100
         AS ?damage)
    FILTER(?damage = ?best)
} ;


DROP SILENT GRAPH <GRAPH_SCRATCH>
