"""Turning typed dinner options into horses.

Emoji assignment is a small but load-bearing bit of polish: "🌯 CHIPOTLE" reads
instantly from across the room, while "OPTION 3" does not. Keyword hints cover
the office regulars; anything unrecognised falls back to a stable rotation.
"""

from __future__ import annotations

import unicodedata

from .breeds import default_breed_for, get_breed
from .constants import (
    FALLBACK_FOOD_EMOJI,
    FOOD_EMOJI_HINTS,
    HORSE_COLORS,
    JOCKEY_EMOJI,
    MAX_OPTION_NAME_LENGTH,
    MAX_OPTIONS,
    MIN_OPTIONS,
)
from .state import HorseSpec


def _has_emoji_prefix(text: str) -> str | None:
    """Return a leading emoji the host typed themselves, if any."""

    if not text:
        return None
    first = text[0]
    if unicodedata.category(first) == "So" or ord(first) > 0x2500:
        return first
    return None


def guess_emoji(name: str, index: int) -> str:
    """Pick an emoji for a dinner option from keyword hints."""

    lowered = name.lower()
    for keyword, emoji in FOOD_EMOJI_HINTS.items():
        if keyword in lowered:
            return emoji
    return FALLBACK_FOOD_EMOJI[index % len(FALLBACK_FOOD_EMOJI)]


def clean_option(raw: str) -> str:
    """Normalise one typed option: trim, collapse spaces, cap length."""

    collapsed = " ".join(raw.split())
    return collapsed[:MAX_OPTION_NAME_LENGTH].strip()


def parse_options(raw_options: list[str]) -> list[str]:
    """Clean, de-duplicate (case-insensitively) and cap a list of options."""

    seen: set[str] = set()
    options: list[str] = []
    for raw in raw_options:
        name = clean_option(raw)
        if not name:
            continue
        key = name.lower()
        if key in seen:
            continue
        seen.add(key)
        options.append(name)
        if len(options) >= MAX_OPTIONS:
            break
    return options


def build_horses(
    options: list[str],
    existing: dict[str, str] | None = None,
    breeds: dict[str, str] | None = None,
) -> list[HorseSpec]:
    """Build horse specs for the given option names.

    ``existing`` maps option name → emoji and ``breeds`` maps option name → breed
    id, so host tweaks survive both a rematch and an edit to the option list.
    Unchosen options get breeds from a rotation, which makes a default field look
    like a field rather than ten clones.
    """

    horses: list[HorseSpec] = []
    for index, name in enumerate(options):
        typed = _has_emoji_prefix(name)
        display_name = name[1:].strip() if typed else name
        emoji = (existing or {}).get(name) or typed or guess_emoji(display_name, index)
        chosen = (breeds or {}).get(name)
        breed = get_breed(chosen).id if chosen else default_breed_for(index)
        horses.append(
            HorseSpec(
                id=index,
                name=display_name.upper() or f"OPTION {index + 1}",
                emoji=emoji,
                color=HORSE_COLORS[index % len(HORSE_COLORS)],
                jockey=JOCKEY_EMOJI[index % len(JOCKEY_EMOJI)],
                breed=breed,
            )
        )
    return horses


def options_are_raceable(options: list[str]) -> bool:
    return MIN_OPTIONS <= len(options) <= MAX_OPTIONS
