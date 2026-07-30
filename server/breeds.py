"""Horse breeds: what a dinner option actually looks like on the track.

Ten horses plus a party parrot. Every breed is the same procedural rig with
different numbers — proportions, gait, markings, and the odd horn or wing — so
adding one costs a dictionary rather than an art pipeline.

The ``render`` block is passed straight through to the display and consumed by
``static/display/horses.js``. Nothing in here affects the simulation: a breed is
pure personality, so picking the party parrot can never be the "fast" choice.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass(frozen=True)
class BreedDef:
    """One selectable look."""

    id: str
    name: str
    icon: str
    blurb: str
    render: dict[str, Any] = field(default_factory=dict)

    def client_meta(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "name": self.name,
            "icon": self.icon,
            "blurb": self.blurb,
            "render": self.render,
        }


#: Render keys, all optional and all with sane defaults on the client:
#:   bodyScale, legLength, legWidth, neckLength, tail ("flow"|"puff"|"fan"|"none"),
#:   mane ("tufts"|"wild"|"mohawk"|"none"), pattern ("none"|"spots"|"patches"|"stripes"),
#:   horn, wings, glow, hop, beak, feathers, rainbow, tint
BREEDS: dict[str, BreedDef] = {
    "thoroughbred": BreedDef(
        id="thoroughbred",
        name="Thoroughbred",
        icon="🐎",
        blurb="Lean, long-legged, born to race.",
        render={"bodyScale": 1.0, "legLength": 1.15, "neckLength": 1.1, "tail": "flow"},
    ),
    "mustang": BreedDef(
        id="mustang",
        name="Mustang",
        icon="🐴",
        blurb="Wild mane, no manners.",
        render={"bodyScale": 1.05, "legLength": 1.0, "mane": "wild", "tail": "flow"},
    ),
    "appaloosa": BreedDef(
        id="appaloosa",
        name="Appaloosa",
        icon="🫎",
        blurb="Freckled all over.",
        render={"bodyScale": 1.02, "pattern": "spots", "tail": "puff"},
    ),
    "pinto": BreedDef(
        id="pinto",
        name="Pinto",
        icon="🐄",
        blurb="Big irregular patches.",
        render={"bodyScale": 1.04, "pattern": "patches", "mane": "tufts"},
    ),
    "clydesdale": BreedDef(
        id="clydesdale",
        name="Clydesdale",
        icon="🐗",
        blurb="Enormous. Feathered hooves. Unbothered.",
        render={
            "bodyScale": 1.22,
            "legLength": 0.95,
            "legWidth": 1.6,
            "feathers": True,
            "mane": "tufts",
            "tail": "puff",
        },
    ),
    "pony": BreedDef(
        id="pony",
        name="Shetland Pony",
        icon="🫏",
        blurb="Small, round, furious.",
        render={
            "bodyScale": 0.78,
            "legLength": 0.7,
            "neckLength": 0.8,
            "mane": "wild",
            "tail": "puff",
        },
    ),
    "unicorn": BreedDef(
        id="unicorn",
        name="Unicorn",
        icon="🦄",
        blurb="A horn, and the confidence to match.",
        render={"bodyScale": 1.0, "legLength": 1.1, "horn": True, "glow": True, "mane": "mohawk"},
    ),
    "pegasus": BreedDef(
        id="pegasus",
        name="Pegasus",
        icon="🕊️",
        blurb="Wings. Technically legal.",
        render={"bodyScale": 1.02, "legLength": 1.05, "wings": True, "tail": "fan"},
    ),
    "zebra": BreedDef(
        id="zebra",
        name="Zebra",
        icon="🦓",
        blurb="Stripes, and an attitude problem.",
        render={"bodyScale": 1.0, "pattern": "stripes", "mane": "mohawk"},
    ),
    "shadow": BreedDef(
        id="shadow",
        name="Shadow Steed",
        icon="🌑",
        blurb="Runs at night. Always.",
        render={"bodyScale": 1.03, "legLength": 1.1, "tint": -0.55, "glow": True, "tail": "flow"},
    ),
    "parrot": BreedDef(
        id="parrot",
        name="Party Parrot",
        icon="🦜",
        blurb="Not a horse. Hops. Rainbow. Undefeated at vibes.",
        render={
            "bodyScale": 0.9,
            "legLength": 0.55,
            "legWidth": 0.7,
            "neckLength": 0.5,
            "mane": "none",
            "tail": "fan",
            "beak": True,
            "hop": True,
            "rainbow": True,
        },
    ),
}

DEFAULT_BREED_ID = "thoroughbred"

#: Rotation used when the host hasn't chosen, so a default field looks varied.
BREED_ROTATION = [
    "thoroughbred",
    "mustang",
    "appaloosa",
    "pinto",
    "unicorn",
    "zebra",
    "clydesdale",
    "pegasus",
    "pony",
    "shadow",
    "parrot",
]


def get_breed(breed_id: str | None) -> BreedDef:
    return BREEDS.get(breed_id or DEFAULT_BREED_ID, BREEDS[DEFAULT_BREED_ID])


def default_breed_for(index: int) -> str:
    return BREED_ROTATION[index % len(BREED_ROTATION)]


def breed_catalog() -> list[dict[str, Any]]:
    return [breed.client_meta() for breed in BREEDS.values()]
