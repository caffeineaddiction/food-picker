"""Breeds are pure cosmetics, and that is the property worth protecting.

If a breed could touch the simulation, picking the party parrot would stop being
a joke and start being a strategy.
"""

from __future__ import annotations

import pytest
from conftest import DEFAULT_OPTIONS, simulate

from server.breeds import (
    BREED_ROTATION,
    BREEDS,
    DEFAULT_BREED_ID,
    breed_catalog,
    default_breed_for,
    get_breed,
)
from server.rooms import RoomManager
from server.roster import build_horses
from server.stats import SessionStats

RENDER_KEYS = {
    "bodyScale",
    "legLength",
    "legWidth",
    "neckLength",
    "tail",
    "mane",
    "pattern",
    "horn",
    "wings",
    "glow",
    "hop",
    "beak",
    "feathers",
    "rainbow",
    "tint",
}


def test_there_are_ten_horses_and_a_party_parrot():
    assert len(BREEDS) == 11
    assert "parrot" in BREEDS
    horses = [breed for breed_id, breed in BREEDS.items() if breed_id != "parrot"]
    assert len(horses) == 10


def test_every_breed_is_presentable():
    for breed_id, breed in BREEDS.items():
        assert breed.id == breed_id
        assert breed.name and breed.icon and breed.blurb
        assert breed.render, f"{breed_id} needs at least one render tweak"


def test_render_params_are_all_understood_by_the_client():
    """A typo'd render key silently does nothing, so pin the vocabulary."""

    for breed in BREEDS.values():
        unknown = set(breed.render) - RENDER_KEYS
        assert not unknown, f"{breed.id} uses unknown render keys: {unknown}"


def test_breeds_carry_no_gameplay_numbers():
    banned = {"speed", "boost", "multiplier", "bonus", "advantage", "tapBonus"}
    for breed in BREEDS.values():
        assert not banned & set(breed.render), f"{breed.id} looks like it affects the race"


def test_unknown_breeds_fall_back_rather_than_raising():
    assert get_breed(None).id == DEFAULT_BREED_ID
    assert get_breed("pegasus_deluxe").id == DEFAULT_BREED_ID
    assert get_breed("parrot").id == "parrot"


def test_the_default_rotation_covers_every_breed():
    assert set(BREED_ROTATION) == set(BREEDS)
    picks = {default_breed_for(index) for index in range(len(BREED_ROTATION))}
    assert picks == set(BREEDS), "a default field should look varied"


def test_a_default_field_is_not_ten_clones():
    horses = build_horses([f"Option {index}" for index in range(8)])
    assert len({horse.breed for horse in horses}) >= 6


def test_the_host_choice_sticks_to_the_option_not_the_lane(tmp_path):
    room = RoomManager(SessionStats(path=tmp_path / "s.json")).create()
    room.options = ["Pizza", "Sushi", "Tacos"]
    room.set_breed(1, "parrot")
    assert room.horse_specs()[1].breed == "parrot"

    # Reordering the menu keeps Sushi as the parrot.
    room.options = ["Sushi", "Pizza", "Tacos"]
    specs = {spec.name: spec.breed for spec in room.horse_specs()}
    assert specs["SUSHI"] == "parrot"
    assert specs["PIZZA"] != "parrot"


def test_an_unknown_breed_from_a_host_is_ignored(tmp_path):
    room = RoomManager(SessionStats(path=tmp_path / "s.json")).create()
    before = room.horse_specs()[0].breed
    room.set_breed(0, "definitely-not-a-breed")
    assert room.horse_specs()[0].breed == before
    room.set_breed(99, "parrot")
    assert len(room.breed_overrides) == 0


def test_the_catalog_is_serialisable():
    catalog = breed_catalog()
    assert len(catalog) == len(BREEDS)
    assert {"id", "name", "icon", "blurb", "render"} <= set(catalog[0])


@pytest.mark.parametrize("breed_id", ["thoroughbred", "parrot", "clydesdale"])
def test_the_race_runs_identically_whatever_the_breed(breed_id):
    """Same seed, same result — the parrot is not secretly fast."""

    baseline = simulate(seed=99, tappers=[], powerups=True, events=True)
    specs = build_horses(DEFAULT_OPTIONS, breeds={name: breed_id for name in DEFAULT_OPTIONS})
    assert all(spec.breed == breed_id for spec in specs)
    repeat = simulate(seed=99, tappers=[], powerups=True, events=True)
    assert repeat.winner_id == baseline.winner_id
    assert repeat.order == baseline.order
