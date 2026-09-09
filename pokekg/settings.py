"""IRIs, named graphs and filesystem paths.

Imported across the project so the ontology, the SPARQL rules, the embedding
pipeline and the service agree on one vocabulary.
"""
from __future__ import annotations

import os
from pathlib import Path

from rdflib import Namespace

ROOT = Path(__file__).resolve().parent.parent

CONSTRUCTION_DIR = ROOT / "construction"
RAW_DIR = CONSTRUCTION_DIR / "data" / "raw"
RDF_DIR = CONSTRUCTION_DIR / "data" / "rdf"
ONTOLOGY_DIR = CONSTRUCTION_DIR / "ontology"

ML_DIR = ROOT / "embeddings"
ML_DATA = ML_DIR / "data"                 # train/valid/test triple files
ML_RESULTS = ML_DIR / "results"
ML_MODELS = ML_DIR / "models"

RULES_DIR = ROOT / "reasoning" / "rules"

# Schema (TBox) and instances (ABox) live in separate namespaces: PK uses hash
# IRIs, PKR slash IRIs.
BASE = "http://example.org/pokemon-kg/"

PK = Namespace(BASE + "ontology#")      # classes + properties
PKR = Namespace(BASE + "resource/")     # individuals
PKG = Namespace(BASE + "graph/")        # named-graph IRIs

# Terms the embedding pipeline derives but never asserts into the KG, such as
# the stat bands that stand in for numeric literals a KGE model cannot read.
PKM = Namespace(BASE + "ml/")           # ML-side relations and band entities

# Turtle/SPARQL prefix header, prepended to every query.
SPARQL_PREFIXES = "\n".join(
    [
        f"PREFIX {p}: <{ns}>"
        for p, ns in (
            ("pk", PK),
            ("pkr", PKR),
            ("pkg", PKG),
            ("pkm", PKM),
            ("rdf", "http://www.w3.org/1999/02/22-rdf-syntax-ns#"),
            ("rdfs", "http://www.w3.org/2000/01/rdf-schema#"),
            ("owl", "http://www.w3.org/2002/07/owl#"),
            ("sh", "http://www.w3.org/ns/shacl#"),
            ("math", "http://www.w3.org/2005/xpath-functions/math#"),
            ("xsd", "http://www.w3.org/2001/XMLSchema#"),
        )
    ]
)

# Asserted, entailed and evaluation triples are kept in separate named graphs so
# that every inferred edge stays attributable to the step that produced it, and
# so the evaluation can withhold test edges without contaminating training.
G_ONTOLOGY = PKG["ontology"]              # TBox: classes, properties, axioms
G_SHAPES = PKG["shapes"]                  # SHACL shapes
G_BASE = PKG["base"]                      # ABox from PokeAPI
G_RDFS = PKG["inferred-rdfs"]             # RDFS / OWL-RL entailments
G_DERIVED = PKG["inferred-recursive"]     # recursive rules: evolution closure,
                                          # isFullyEvolved, counter chains
G_MATCHUP = PKG["inferred-matchup"]       # reified pk:Matchup nodes +
                                          # hasMatchupAdvantageOver edges
G_SCRATCH = PKG["scratch"]                # working space for the rules,
                                          # dropped before they finish
G_EVAL_TEST = PKG["eval-test"]            # held-out matchup edges
G_PREDICTED = PKG["predicted-matchup"]    # edges the embedding model adds back
                                          # into the KG


# Fuseki endpoints
FUSEKI_HOST = os.environ.get("FUSEKI_HOST", "http://localhost:3030")
FUSEKI_DATASET = os.environ.get("FUSEKI_DATASET", "pokemon")
FUSEKI_USER = os.environ.get("FUSEKI_USER", "admin")
FUSEKI_PASSWORD = os.environ.get("FUSEKI_PASSWORD", "admin")

QUERY_ENDPOINT = f"{FUSEKI_HOST}/{FUSEKI_DATASET}/sparql"
UPDATE_ENDPOINT = f"{FUSEKI_HOST}/{FUSEKI_DATASET}/update"
GSP_ENDPOINT = f"{FUSEKI_HOST}/{FUSEKI_DATASET}/data"
PING_ENDPOINT = f"{FUSEKI_HOST}/$/ping"

GEN_MAX_POKEDEX_ID = 386          # Gen 1-3 only

# Legendary / mythical Pokemon excluded from the KG.
EXCLUDED_POKEMON_IDS = frozenset(
    {
        144, 145, 146, 150, 151,                                # Gen 1
        243, 244, 245, 249, 250, 251,                           # Gen 2
        377, 378, 379, 380, 381, 382, 383, 384, 385, 386,       # Gen 3
    }
)

# Version groups belonging to generations 1-3. Learnsets are restricted to
# these, otherwise PokeAPI returns Gen 4-9 level-up moves for Gen 1-3 Pokemon.
GEN13_VERSION_GROUPS = frozenset(
    {
        "red-blue", "yellow",                    # Gen 1
        "gold-silver", "crystal",                # Gen 2
        "ruby-sapphire", "emerald",              # Gen 3
        "firered-leafgreen",
        "colosseum", "xd",                       # Gen 3 side games
    }
)

# The only abilities modeled: those that nullify one attacking type.
TYPE_NULLIFYING_ABILITIES: dict[str, str] = {
    "levitate": "ground",
    "flash-fire": "fire",
    "volt-absorb": "electric",
    "lightning-rod": "electric",
    "water-absorb": "water",
}
