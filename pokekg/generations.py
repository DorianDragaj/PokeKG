"""Project PokeAPI's current game data back to a target generation.

PokeAPI serves Generation 9 values, the project is scoped to Generations 1-3.
The historical values are already in the API responses; this module applies
them. Two different conventions are used for past data:

1. past_types / past_abilities / past_damage_relations carry an inclusive
   generation, i.e. the value held through that generation. Clefairy's
   generation-v -> [normal] means Normal in Gens 1-5, so take the earliest
   entry whose generation is >= the target.

2. past_values on moves carries an exclusive version_group, i.e. the group in
   which the move changed, so the recorded numbers are the ones in force
   strictly before it. Dig's diamond-pearl -> 60 means 60 power up to Gen 3
   and 80 from Gen 4, so take the earliest entry whose version group's
   generation is > the target. Spot-checked against known Gen 3 values:
   Dig 60, Fly 70, Thrash 90, Petal Dance 70, Hydro Pump 120.

The physical/special split did not exist before Generation 4: damage class was
fixed by the move's type, all Fire moves Special, all Rock moves Physical.
PokeAPI reports the modern per-move class, so for Gen <= 3 the class is
derived from the type instead. The damage formula picks Attack or Sp. Attack
from it.
"""
from __future__ import annotations

from typing import Any

TARGET_GENERATION = 3

GENERATION_NUMBER = {
    "generation-i": 1, "generation-ii": 2, "generation-iii": 3,
    "generation-iv": 4, "generation-v": 5, "generation-vi": 6,
    "generation-vii": 7, "generation-viii": 8, "generation-ix": 9,
}

# Types that did not exist yet, by the generation they were introduced.
TYPE_INTRODUCED_IN = {"dark": 2, "steel": 2, "fairy": 6}

# Before Gen 4 the damage class was a property of the type, not the move.
PHYSICAL_TYPES = frozenset(
    {"normal", "fighting", "flying", "ground", "rock", "bug", "ghost", "poison", "steel"}
)
SPECIAL_TYPES = frozenset(
    {"fire", "water", "grass", "electric", "psychic", "ice", "dragon", "dark"}
)


def types_in_generation(generation: int = TARGET_GENERATION) -> set[str]:
    return {
        name
        for name in (PHYSICAL_TYPES | SPECIAL_TYPES | {"fairy"})
        if TYPE_INTRODUCED_IN.get(name, 1) <= generation
    }


def damage_class_for_type(type_name: str, generation: int = TARGET_GENERATION) -> str:
    """Gen 1-3: class follows the type. Gen 4+: caller should use the move's own."""
    if generation >= 4:
        raise ValueError("from Gen 4 the class is per-move, not per-type")
    return "physical" if type_name in PHYSICAL_TYPES else "special"


def _inclusive_past(
    entries: list[dict[str, Any]],
    generation_key: str,
    generation: int,
) -> dict[str, Any] | None:
    """Earliest entry still in force at that generation, None to use the current value."""
    applicable = [
        entry
        for entry in entries
        if GENERATION_NUMBER.get(entry[generation_key]["name"], 99) >= generation
    ]
    if not applicable:
        return None
    return min(
        applicable, key=lambda e: GENERATION_NUMBER[e[generation_key]["name"]]
    )


def project_pokemon_types(
    doc: dict[str, Any], generation: int = TARGET_GENERATION
) -> list[str]:
    current = [t["type"]["name"] for t in sorted(doc["types"], key=lambda x: x["slot"])]
    past = _inclusive_past(doc.get("past_types", []), "generation", generation)
    if past is None:
        return current
    return [t["type"]["name"] for t in sorted(past["types"], key=lambda x: x["slot"])]


def project_pokemon_abilities(
    doc: dict[str, Any], generation: int = TARGET_GENERATION
) -> list[str]:
    """Abilities as of the given generation.

    past_abilities is a per-slot delta, not a replacement list: Bulbasaur's
    generation-iv -> [{slot: 3, ability: null}] says only that slot 3 was empty
    back then and leaves slot 1 (Overgrow) alone, so the delta has to be merged
    into the current slots rather than replace them.

    Hidden abilities (slot 3) are dropped for Gen <= 4, they only exist from
    Gen 5 on.
    """
    slots: dict[int, tuple[str | None, bool]] = {
        entry["slot"]: (
            entry["ability"]["name"] if entry.get("ability") else None,
            entry.get("is_hidden", False),
        )
        for entry in doc["abilities"]
    }

    past = _inclusive_past(doc.get("past_abilities", []), "generation", generation)
    if past is not None:
        for entry in past["abilities"]:
            slots[entry["slot"]] = (
                entry["ability"]["name"] if entry.get("ability") else None,
                entry.get("is_hidden", False),
            )

    hidden_abilities_exist = generation >= 5
    return sorted(
        {
            name
            for slot, (name, is_hidden) in slots.items()
            if name is not None
            and (hidden_abilities_exist or not (is_hidden or slot == 3))
        }
    )


def project_damage_relations(
    doc: dict[str, Any], generation: int = TARGET_GENERATION
) -> dict[str, list[str]]:
    past = _inclusive_past(
        doc.get("past_damage_relations", []), "generation", generation
    )
    source = past["damage_relations"] if past is not None else doc["damage_relations"]
    live = types_in_generation(generation)
    return {
        "super_effective_against": sorted(
            t["name"] for t in source["double_damage_to"] if t["name"] in live
        ),
        "resisted_by": sorted(
            t["name"] for t in source["half_damage_to"] if t["name"] in live
        ),
        "no_effect_against": sorted(
            t["name"] for t in source["no_damage_to"] if t["name"] in live
        ),
    }


def project_move(
    doc: dict[str, Any],
    version_group_generation: dict[str, int],
    generation: int = TARGET_GENERATION,
) -> dict[str, Any]:
    """Power, accuracy and type as they were in that generation, plus the era's class."""
    power, accuracy = doc.get("power"), doc.get("accuracy")
    move_type = doc["type"]["name"]

    later_changes = sorted(
        (
            entry
            for entry in doc.get("past_values", [])
            if version_group_generation.get(entry["version_group"]["name"], 99)
            > generation
        ),
        key=lambda e: version_group_generation[e["version_group"]["name"]],
    )
    if later_changes:
        # The earliest change after the target records the values at the target.
        change = later_changes[0]
        if change.get("power") is not None:
            power = change["power"]
        if change.get("accuracy") is not None:
            accuracy = change["accuracy"]
        if change.get("type") is not None:
            move_type = change["type"]["name"]

    return {
        "type": move_type,
        "power": power,
        "accuracy": accuracy,
        "damage_class": damage_class_for_type(move_type, generation),
    }


def move_existed(doc: dict[str, Any], generation: int = TARGET_GENERATION) -> bool:
    return GENERATION_NUMBER.get(doc["generation"]["name"], 99) <= generation
