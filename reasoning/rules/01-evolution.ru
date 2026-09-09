# Evolution rules -> graph/inferred-recursive
# The data asserts only the single step charmander -> charmeleon.


# owl:inverseOf as a rule, so the edge exists without an OWL reasoner.

INSERT { GRAPH <GRAPH_DERIVED> { ?child pk:evolvesFrom ?parent } }
WHERE  { ?parent pk:evolvesInto ?child } ;


# Transitive closure. The + path is SPARQL's own recursion - no fixpoint loop.

INSERT { GRAPH <GRAPH_DERIVED> { ?early pk:evolvesIntoEventually ?late } }
WHERE  { ?early pk:evolvesInto+ ?late } ;


# Negation as failure: no successor means the line ends here. Not an RDFS/OWL
# entailment - an open-world axiom cannot read absence as a fact. 02 filters on it.

INSERT { GRAPH <GRAPH_DERIVED> { ?p pk:isFullyEvolved true } }
WHERE  {
    ?p a pk:Pokemon .
    FILTER NOT EXISTS { ?p pk:evolvesInto ?any }
} ;

INSERT { GRAPH <GRAPH_DERIVED> { ?p pk:isFullyEvolved false } }
WHERE  { ?p pk:evolvesInto ?any }
