"""Extract the Pokemon dataset from PokeAPI into one raw JSON file.

Writes construction/data/raw/pokeapi_dataset.json with five sections:

    provenance  when and where the data came from
    types       the types and their damage relations (current + past)
    moves       damaging moves learnable in Gen 1-3, with type/power/class
    pokemon     the in-scope Pokemon: stats, types, abilities, moves
    evolution   evolvesInto pairs, input to the recursive reasoning rules

Run:  make extract          (or: ./venv/bin/python construction/src/extract.py)
Re-runs cost nothing, every API response is cached under data/raw/cache/.
"""
from __future__ import annotations

import json
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from pokekg import generations as G
from pokekg import settings as S
from pokekg.pokeapi import BASE_URL, PokeAPIClient, name_from_url

CACHE_DIR = S.RAW_DIR / "cache"
OUTPUT_PATH = S.RAW_DIR / "pokeapi_dataset.json"

NUM_TYPES_CURRENT = 18  # normal..fairy; ids 10001+ are non-battle "unknown"/"shadow"
TARGET_GEN = G.TARGET_GENERATION

# Every projection applied, recorded so the report can show what the Gen 3
# reconstruction actually changed.
AUDIT: dict[str, list[str]] = {
    "pokemon_types": [], "pokemon_abilities": [], "type_chart": [],
    "move_power": [], "move_type": [], "move_class": [], "dropped_post_gen3": [],
}


def extract_types(api: PokeAPIClient) -> dict[str, Any]:
    """The type chart, projected back to the target generation.

    Fairy is dropped (Gen 6) and the remaining types take their damage
    relations from past_damage_relations wherever those differ.
    """
    docs = api.map(
        lambda i: api.get(f"type/{i}"), range(1, NUM_TYPES_CURRENT + 1), "types"
    )
    live = G.types_in_generation(TARGET_GEN)
    chart: dict[str, Any] = {}

    for doc in docs:
        name = doc["name"]
        if name not in live:
            AUDIT["dropped_post_gen3"].append(f"type {name} (introduced in Gen 6)")
            continue

        projected = G.project_damage_relations(doc, TARGET_GEN)
        current = {
            "super_effective_against": sorted(
                x["name"] for x in doc["damage_relations"]["double_damage_to"]
            ),
            "resisted_by": sorted(
                x["name"] for x in doc["damage_relations"]["half_damage_to"]
            ),
            "no_effect_against": sorted(
                x["name"] for x in doc["damage_relations"]["no_damage_to"]
            ),
        }
        if projected != {k: [v for v in vs if v in live] for k, vs in current.items()}:
            AUDIT["type_chart"].append(name)

        chart[name] = {
            "id": doc["id"],
            "damage_class_in_gen3": G.damage_class_for_type(name, TARGET_GEN),
            **projected,
            "current_damage_relations": current,
        }
    return chart


def in_scope_ids() -> list[int]:
    return [
        i
        for i in range(1, S.GEN_MAX_POKEDEX_ID + 1)
        if i not in S.EXCLUDED_POKEMON_IDS
    ]


def extract_pokemon(api: PokeAPIClient, ids: list[int]) -> list[dict[str, Any]]:
    docs = api.map(lambda i: api.get(f"pokemon/{i}"), ids, "pokemon")
    records = []
    for doc in docs:
        stats = {s["stat"]["name"]: s["base_stat"] for s in doc["stats"]}

        current_types = [
            t["type"]["name"] for t in sorted(doc["types"], key=lambda x: x["slot"])
        ]
        types = G.project_pokemon_types(doc, TARGET_GEN)
        if types != current_types:
            AUDIT["pokemon_types"].append(
                f"{doc['name']}: {'/'.join(current_types)} -> {'/'.join(types)}"
            )

        current_abilities = sorted(
            a["ability"]["name"] for a in doc["abilities"] if a.get("ability")
        )
        abilities = G.project_pokemon_abilities(doc, TARGET_GEN)
        nullifying = [a for a in abilities if a in S.TYPE_NULLIFYING_ABILITIES]
        gained = set(nullifying) - set(current_abilities)
        lost = {a for a in current_abilities if a in S.TYPE_NULLIFYING_ABILITIES} - set(
            nullifying
        )
        for ability in sorted(gained):
            AUDIT["pokemon_abilities"].append(f"{doc['name']}: +{ability}")
        for ability in sorted(lost):
            AUDIT["pokemon_abilities"].append(f"{doc['name']}: -{ability}")

        records.append(
            {
                "id": doc["id"],
                "name": doc["name"],
                "types": types,
                "current_types": current_types,
                "stats": {
                    "hp": stats["hp"],
                    "attack": stats["attack"],
                    "defense": stats["defense"],
                    "special_attack": stats["special-attack"],
                    "special_defense": stats["special-defense"],
                    "speed": stats["speed"],
                },
                "abilities": nullifying,
                "move_candidates": sorted(_gen13_level_up_moves(doc)),
            }
        )
    return records


def _gen13_level_up_moves(doc: dict[str, Any]) -> set[str]:
    """Moves this Pokemon learns by level-up in a Gen 1-3 game.

    Level-up only: egg/tutor/TM learnsets would give nearly every Pokemon
    nearly every move and wash out the matchup signal. Gen 1-3 version groups
    only: otherwise PokeAPI returns Gen 9 learnsets.
    """
    learned = set()
    for entry in doc["moves"]:
        for detail in entry["version_group_details"]:
            if (
                detail["move_learn_method"]["name"] == "level-up"
                and detail["version_group"]["name"] in S.GEN13_VERSION_GROUPS
            ):
                learned.add(entry["move"]["name"])
                break
    return learned


def version_group_generations(api: PokeAPIClient) -> dict[str, int]:
    """version-group name -> generation number, needed to read past_values."""
    index = api.get("version-group?limit=100")
    names = [entry["name"] for entry in index["results"]]
    docs = api.map(lambda n: api.get(f"version-group/{n}"), names, "version groups")
    return {
        doc["name"]: G.GENERATION_NUMBER[doc["generation"]["name"]] for doc in docs
    }


def extract_moves(
    api: PokeAPIClient, move_names: list[str], vg_generations: dict[str, int]
) -> dict[str, Any]:
    """Damaging moves that existed in the target generation, with era-correct stats.

    Status moves are dropped: without base power they cannot win a matchup,
    and the one-pager scopes them out anyway.
    """
    docs = api.map(lambda m: api.get(f"move/{m}"), move_names, "moves")
    moves = {}
    for doc in docs:
        if not G.move_existed(doc, TARGET_GEN):
            AUDIT["dropped_post_gen3"].append(
                f"move {doc['name']} (introduced in {doc['generation']['name']})"
            )
            continue

        projected = G.project_move(doc, vg_generations, TARGET_GEN)
        if not projected["power"]:                 # None (status) or 0
            continue
        if projected["type"] not in G.types_in_generation(TARGET_GEN):
            AUDIT["dropped_post_gen3"].append(f"move {doc['name']} (post-Gen 3 type)")
            continue

        if projected["power"] != doc.get("power"):
            AUDIT["move_power"].append(
                f"{doc['name']}: {doc.get('power')} -> {projected['power']}"
            )
        if projected["type"] != doc["type"]["name"]:
            AUDIT["move_type"].append(
                f"{doc['name']}: {doc['type']['name']} -> {projected['type']}"
            )
        if projected["damage_class"] != doc["damage_class"]["name"]:
            AUDIT["move_class"].append(
                f"{doc['name']}: {doc['damage_class']['name']} -> "
                f"{projected['damage_class']}"
            )

        moves[doc["name"]] = {
            "id": doc["id"],
            "name": doc["name"],
            **projected,
            "generation": doc["generation"]["name"],
            "current": {
                "type": doc["type"]["name"],
                "power": doc.get("power"),
                "damage_class": doc["damage_class"]["name"],
            },
        }
    return moves


def extract_evolution(
    api: PokeAPIClient, ids: list[int], in_scope_names: set[str]
) -> list[dict[str, str]]:
    """Direct evolvesInto pairs.

    These feed the recursive rules, where isFullyEvolved and the
    evolution closure are derived rather than hard-coded.
    """
    species_docs = api.map(
        lambda i: api.get(f"pokemon-species/{i}"), ids, "species (for evolution chains)"
    )
    chain_ids = sorted(
        {int(name_from_url(doc["evolution_chain"]["url"])) for doc in species_docs}
    )
    chain_docs = api.map(
        lambda c: api.get(f"evolution-chain/{c}"), chain_ids, "evolution chains"
    )

    pairs: list[dict[str, str]] = []
    dropped_out_of_scope = 0
    for doc in chain_docs:
        for parent, child, trigger in _walk_chain(doc["chain"]):
            if parent in in_scope_names and child in in_scope_names:
                pairs.append({"from": parent, "to": child, "trigger": trigger})
            else:
                dropped_out_of_scope += 1

    pairs.sort(key=lambda p: (p["from"], p["to"]))
    print(
        f"  evolution: {len(pairs)} in-scope edges "
        f"({dropped_out_of_scope} dropped - endpoint outside Gen 1-3 or legendary)"
    )
    return pairs


def _walk_chain(node: dict[str, Any]) -> list[tuple[str, str, str]]:
    """Flatten a possibly branching evolution tree into parent->child edges."""
    edges = []
    parent = node["species"]["name"]
    for child_node in node["evolves_to"]:
        details = child_node.get("evolution_details") or [{}]
        trigger = (details[0].get("trigger") or {}).get("name", "unknown")
        edges.append((parent, child_node["species"]["name"], trigger))
        edges.extend(_walk_chain(child_node))
    return edges


def validate(payload: dict[str, Any]) -> list[str]:
    problems = []
    types, moves, pokemon = payload["types"], payload["moves"], payload["pokemon"]
    type_names = set(types)

    expected_types = G.types_in_generation(TARGET_GEN)
    if type_names != expected_types:
        problems.append(
            f"type set is not the Gen {TARGET_GEN} set: "
            f"missing {sorted(expected_types - type_names)}, "
            f"unexpected {sorted(type_names - expected_types)}"
        )

    expected_count = S.GEN_MAX_POKEDEX_ID - len(S.EXCLUDED_POKEMON_IDS)
    if len(pokemon) != expected_count:
        problems.append(f"expected {expected_count} Pokemon, got {len(pokemon)}")

    for mon in pokemon:
        if not 1 <= mon["id"] <= S.GEN_MAX_POKEDEX_ID:
            problems.append(f"{mon['name']}: id {mon['id']} out of Gen 1-3 range")
        if mon["id"] in S.EXCLUDED_POKEMON_IDS:
            problems.append(f"{mon['name']}: legendary/mythical leaked into the set")
        if not 1 <= len(mon["types"]) <= 2:
            problems.append(f"{mon['name']}: {len(mon['types'])} types")
        for type_name in mon["types"]:
            if type_name not in type_names:
                problems.append(f"{mon['name']}: unknown type {type_name!r}")
        for move_name in mon["moves"]:
            if move_name not in moves:
                problems.append(f"{mon['name']}: dangling move {move_name!r}")
        for stat, value in mon["stats"].items():
            if not 1 <= value <= 255:
                problems.append(f"{mon['name']}: implausible {stat}={value}")

    for move in moves.values():
        if move["type"] not in type_names:
            problems.append(f"move {move['name']}: unknown type {move['type']!r}")
        if move["damage_class"] != G.damage_class_for_type(move["type"], TARGET_GEN):
            problems.append(
                f"move {move['name']}: class {move['damage_class']!r} does not follow "
                f"its type {move['type']!r} (pre-Gen 4 rule)"
            )
        if move["damage_class"] not in {"physical", "special"}:
            problems.append(f"move {move['name']}: class {move['damage_class']!r}")

    names = {mon["name"] for mon in pokemon}
    for edge in payload["evolution"]:
        if edge["from"] not in names or edge["to"] not in names:
            problems.append(f"evolution edge outside the Pokemon set: {edge}")

    return problems


def main() -> int:
    S.RAW_DIR.mkdir(parents=True, exist_ok=True)
    api = PokeAPIClient(CACHE_DIR)
    ids = in_scope_ids()

    print(f"Extracting Gen 1-3 Pokemon (ids 1-{S.GEN_MAX_POKEDEX_ID}, "
          f"{len(S.EXCLUDED_POKEMON_IDS)} legendary/mythical excluded)\n")

    types = extract_types(api)
    pokemon = extract_pokemon(api, ids)

    vg_generations = version_group_generations(api)
    candidate_moves = sorted({m for mon in pokemon for m in mon["move_candidates"]})
    moves = extract_moves(api, candidate_moves, vg_generations)

    # Keep only the moves that survived the damaging-move filter.
    for mon in pokemon:
        mon["moves"] = [m for m in mon.pop("move_candidates") if m in moves]

    in_scope_names = {mon["name"] for mon in pokemon}
    evolution = extract_evolution(api, ids, in_scope_names)

    payload = {
        "provenance": {
            "source": "PokeAPI v2",
            "source_url": BASE_URL,
            "license": "PokeAPI data is freely available; Pokemon is (c) Nintendo/Game Freak",
            "extracted_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "scope": {
                "generations": "1-3",
                "max_pokedex_id": S.GEN_MAX_POKEDEX_ID,
                "excluded_legendary_ids": sorted(S.EXCLUDED_POKEMON_IDS),
                "move_learn_method": "level-up",
                "move_version_groups": sorted(S.GEN13_VERSION_GROUPS),
                "abilities_modelled": S.TYPE_NULLIFYING_ABILITIES,
                "target_generation": TARGET_GEN,
                "type_chart": (
                    f"projected back to Generation {TARGET_GEN}; current values "
                    f"retained alongside under 'current_*' keys"
                ),
            },
        },
        "projection": {
            "target_generation": TARGET_GEN,
            "summary": {key: len(values) for key, values in AUDIT.items()},
            "changes": {key: sorted(values) for key, values in AUDIT.items()},
        },
        "types": types,
        "moves": moves,
        "pokemon": pokemon,
        "evolution": evolution,
    }

    problems = validate(payload)
    print(f"\n{api.stats()}")

    if problems:
        print(f"\nVALIDATION FAILED - {len(problems)} problem(s):")
        for problem in problems[:20]:
            print(f"  - {problem}")
        if len(problems) > 20:
            print(f"  ... and {len(problems) - 20} more")
        return 1

    OUTPUT_PATH.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    _summarise(payload)
    return 0


def _summarise(payload: dict[str, Any]) -> None:
    pokemon, moves = payload["pokemon"], payload["moves"]
    with_ability = [p for p in pokemon if p["abilities"]]
    movecounts = sorted(len(p["moves"]) for p in pokemon)
    dual = [p for p in pokemon if len(p["types"]) == 2]

    print("\nValidation passed.\n")
    print(f"  Pokemon              {len(pokemon)}")
    print(f"    dual-typed         {len(dual)}")
    print(f"    with a nullifying ability {len(with_ability)}")
    print(f"  Types                {len(payload['types'])}")
    print(f"  Damaging moves       {len(moves)}")
    print(f"    moves per Pokemon  min {movecounts[0]}, "
          f"median {movecounts[len(movecounts) // 2]}, max {movecounts[-1]}")
    print(f"  Evolution edges      {len(payload['evolution'])}")

    audit = payload["projection"]["changes"]
    print(f"\n  Gen {TARGET_GEN} reconstruction:")
    print(f"    Pokemon re-typed         {len(audit['pokemon_types'])}")
    print(f"    Ability changes          {len(audit['pokemon_abilities'])}")
    print(f"    Types with a changed chart {len(audit['type_chart'])}")
    print(f"    Moves re-powered         {len(audit['move_power'])}")
    print(f"    Moves re-typed           {len(audit['move_type'])}")
    print(f"    Moves re-classed         {len(audit['move_class'])}  "
          f"(physical/special split did not exist before Gen 4)")
    print(f"    Dropped as post-Gen 3    {len(audit['dropped_post_gen3'])}")
    size_mb = OUTPUT_PATH.stat().st_size / 1_000_000
    print(f"\nWrote {OUTPUT_PATH.relative_to(S.ROOT)}  ({size_mb:.1f} MB)")


if __name__ == "__main__":
    raise SystemExit(main())
