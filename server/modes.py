"""Game modes (SPEC.md §11).

A mode is a set of constant overrides plus at most a couple of behaviour flags.
Four of the six modes are *pure* constant overrides; only Last Bite
(elimination), Tournament and The Punters' Club need orchestration, and that
lives in :mod:`server.rooms` rather than in a plugin system.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from . import constants as C


@dataclass(frozen=True)
class ModeDef:
    """Declarative description of one game mode."""

    id: str
    name: str
    emoji: str
    tagline: str
    blurb: str
    influence_note: str

    # -- duration ------------------------------------------------------------
    default_duration: float = C.DEFAULT_RACE_SECONDS
    duration_locked: bool = False
    """True when the mode derives its own duration (host slider is hidden)."""
    track_length: float = C.TRACK_LENGTH

    # -- simulation overrides ------------------------------------------------
    noise_range: tuple[float, float] = (C.NOISE_MIN, C.NOISE_MAX)
    rubber_band_multiplier: float = 1.0
    photo_finish_multiplier: float = 1.0

    # -- powerup economy -----------------------------------------------------
    drop_interval_multiplier: float = 1.0
    drop_interval_floor: float = 4.0
    rarity_weight_overrides: dict[str, int] = field(default_factory=dict)
    starting_powerups: int = 0
    respect_global_cooldowns: bool = True

    # -- world events --------------------------------------------------------
    event_interval: tuple[float, float] = (C.EVENT_INTERVAL_MIN_S, C.EVENT_INTERVAL_MAX_S)
    max_concurrent_events: int = 1

    # -- orchestration flags -------------------------------------------------
    elimination: bool = False
    betting: bool = False
    tournament: bool = False
    chaos_visuals: bool = False

    # -- mode-specific knobs -------------------------------------------------
    elimination_interval: float = C.ELIMINATION_INTERVAL_S
    heat_size: int = 4
    heat_duration: float = 40.0
    final_duration: float = 60.0

    def duration_for(self, horse_count: int, requested: float) -> float:
        """Resolve the race length for a field of ``horse_count`` horses."""

        if self.elimination:
            return max(self.elimination_interval * max(1, horse_count - 1), 12.0)
        if self.duration_locked:
            return self.default_duration
        return min(C.MAX_RACE_SECONDS, max(C.MIN_RACE_SECONDS, requested))

    def rarity_weights(self) -> dict[str, int]:
        weights = dict(C.RARITY_WEIGHTS)
        weights.update(self.rarity_weight_overrides)
        return weights

    def client_meta(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "name": self.name,
            "emoji": self.emoji,
            "tagline": self.tagline,
            "blurb": self.blurb,
            "influence": self.influence_note,
            "durationLocked": self.duration_locked,
            "defaultDuration": self.default_duration,
            "betting": self.betting,
            "tournament": self.tournament,
            "elimination": self.elimination,
        }


MODES: dict[str, ModeDef] = {
    "classic": ModeDef(
        id="classic",
        name="Classic Derby",
        emoji="🏇",
        tagline="One race. Winner feeds the office.",
        blurb="Full powerups, full events, 60 seconds of dignity on the line.",
        influence_note="~50% you / ~50% chaos",
    ),
    "chaos": ModeDef(
        id="chaos",
        name="Chaos Buffet",
        emoji="🌪️",
        tagline="Everything, everywhere, all at once.",
        blurb="Double the items, double the events, wider swings, permanent screen shake.",
        influence_note="~35% you / ~65% chaos (stated up front!)",
        noise_range=(0.62, 1.38),
        drop_interval_multiplier=0.5,
        rarity_weight_overrides={"epic": 6},
        event_interval=(7.0, 12.0),
        max_concurrent_events=2,
        respect_global_cooldowns=False,
        chaos_visuals=True,
    ),
    "last_bite": ModeDef(
        id="last_bite",
        name="Last Bite",
        emoji="🪓",
        tagline="Last place gets discontinued. Repeatedly.",
        blurb="The track loops. Every 12 seconds the trailing option is eliminated "
        "and its backers become free agents.",
        influence_note="~55% you / ~45% chaos — survival is a skill",
        duration_locked=True,
        elimination=True,
        rubber_band_multiplier=0.8,
    ),
    "tournament": ModeDef(
        id="tournament",
        name="Tournament",
        emoji="🏆",
        tagline="Heats, then a final. Bracket included.",
        blurb="Options split into heats of up to four. Heat winners advance to a "
        "60-second final with double Epic odds.",
        influence_note="~50% you / ~50% chaos, three times over",
        duration_locked=True,
        default_duration=40.0,
        tournament=True,
    ),
    "punters": ModeDef(
        id="punters",
        name="The Punters' Club",
        emoji="🎰",
        tagline="Classic race. Everyone bets. Spectators finally matter.",
        blurb="A 20-second betting window with live pari-mutuel odds, then a "
        "Classic race. Bankrolls persist all night.",
        influence_note="~50% you / ~50% chaos — bets never touch the sim",
        betting=True,
    ),
    "lightning": ModeDef(
        id="lightning",
        name="Lightning Round",
        emoji="⚡",
        tagline="Twenty seconds. Knife fight. Go.",
        blurb="Short track, everyone starts armed, items every five seconds and a "
        "rubber band cranked for photo finishes.",
        influence_note="~45% you / ~55% chaos, compressed",
        default_duration=20.0,
        duration_locked=True,
        track_length=350.0,
        drop_interval_multiplier=0.45,
        drop_interval_floor=3.0,
        starting_powerups=1,
        rubber_band_multiplier=1.5,
        photo_finish_multiplier=2.0,
        event_interval=(6.0, 10.0),
    ),
}

DEFAULT_MODE_ID = "classic"


def get_mode(mode_id: str | None) -> ModeDef:
    """Resolve a mode id, falling back to Classic rather than failing."""

    return MODES.get(mode_id or DEFAULT_MODE_ID, MODES[DEFAULT_MODE_ID])


def mode_catalog() -> list[dict[str, Any]]:
    return [mode.client_meta() for mode in MODES.values()]
