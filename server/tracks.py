"""Track catalog (SPEC.md §10).

A track is a *theme package* (palette, props, crowd, ambience — consumed by the
renderer) plus **exactly one** light gameplay twist. Twists are position- or
time-symmetric so they add flavour pressure without favouring a lane.

Twists are implemented as small objects with ``on_start``/``on_tick`` hooks and
are instantiated fresh for every race, so they may hold per-race state.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

from .effects import Category, Effect, EnterEffect, Zone

if TYPE_CHECKING:  # pragma: no cover - typing only
    from .engine import RaceEngine


class TrackTwist:
    """Base class: a track twist that does nothing."""

    #: Short text shown on the display when the twist first acts.
    label: str = ""

    def on_start(self, engine: RaceEngine) -> None:
        """Called once when the race is created (gates still closed)."""

    def on_tick(self, engine: RaceEngine) -> None:
        """Called every simulation tick while the race is running."""


class BoostPadTwist(TrackTwist):
    """Neon Circuit: three fixed boost pads — pure common knowledge."""

    label = "BOOST PADS"
    FRACTIONS = (0.25, 0.50, 0.75)
    PAD_WIDTH = 26.0

    def on_start(self, engine: RaceEngine) -> None:
        for fraction in self.FRACTIONS:
            centre = engine.config.track_length * fraction
            engine.add_zone(
                Zone(
                    id=engine.next_zone_id(),
                    kind="pad",
                    start=centre - self.PAD_WIDTH / 2,
                    end=centre + self.PAD_WIDTH / 2,
                    category=Category.TRACK,
                    enter=EnterEffect(
                        id="pad_boost",
                        label="Boost Pad",
                        magnitude=0.20,
                        duration=1.5,
                        vfx="boost",
                        category=Category.TRACK,
                    ),
                )
            )


class MarketRegimeTwist(TrackTwist):
    """Wall Street: bull/bear regimes flip every 10s, hitting everyone equally."""

    label = "MARKET REGIME"
    PERIOD = 10.0
    MAGNITUDE = 0.08

    def __init__(self) -> None:
        self.next_flip_at = self.PERIOD
        self.bull = True

    def on_tick(self, engine: RaceEngine) -> None:
        if engine.race_time < self.next_flip_at:
            return
        self.next_flip_at += self.PERIOD
        self.bull = not self.bull
        magnitude = self.MAGNITUDE if self.bull else -self.MAGNITUDE
        for horse in engine.racing_horses():
            engine.add_effect(
                horse,
                Effect(
                    id="market_regime",
                    label="Bull Market" if self.bull else "Bear Market",
                    magnitude=magnitude,
                    expires_at=engine.race_time + self.PERIOD,
                    category=Category.TRACK,
                    vfx="bull" if self.bull else "bear",
                ),
            )
        engine.emit_track_moment(
            kind="market_regime",
            headline="BULL MARKET" if self.bull else "BEAR MARKET",
            emoji="📈" if self.bull else "📉",
            params={"bull": self.bull},
        )


class LowGravityTwist(TrackTwist):
    """Lunar Colony: longer, floatier flails that hurt less. Comedy-neutral."""

    label = "LOW GRAVITY"
    HOP_PERIOD = 8.0

    def __init__(self) -> None:
        self.next_hop_at = self.HOP_PERIOD

    def on_tick(self, engine: RaceEngine) -> None:
        if engine.race_time < self.next_hop_at:
            return
        self.next_hop_at += self.HOP_PERIOD
        engine.emit_track_moment(kind="moon_hop", headline="", emoji="🌕", params={})


class CandyTwist(TrackTwist):
    """Candy Canyon: two fixed syrup pools plus one mid-race sugar cube."""

    label = "SYRUP POOLS"
    POOL_FRACTIONS = (0.35, 0.65)
    POOL_WIDTH = 70.0

    def __init__(self) -> None:
        self.cube_spawned = False

    def on_start(self, engine: RaceEngine) -> None:
        for fraction in self.POOL_FRACTIONS:
            centre = engine.config.track_length * fraction
            engine.add_zone(
                Zone(
                    id=engine.next_zone_id(),
                    kind="syrup",
                    start=centre - self.POOL_WIDTH / 2,
                    end=centre + self.POOL_WIDTH / 2,
                    magnitude=-0.15,
                    category=Category.TRACK,
                    hostile=True,
                )
            )

    def on_tick(self, engine: RaceEngine) -> None:
        if self.cube_spawned or engine.race_progress() < 0.30:
            return
        self.cube_spawned = True
        leader = engine.leader()
        base = leader.pos if leader else 0.0
        spawn = min(engine.config.track_length * 0.95, base + engine.rng.uniform(80, 220))
        engine.add_zone(
            Zone(
                id=engine.next_zone_id(),
                kind="sugar_cube",
                start=spawn - 10,
                end=spawn + 10,
                category=Category.TRACK,
                consume_on_trigger=True,
                enter=EnterEffect(
                    id="sugar_rush_cube",
                    label="Sugar Cube",
                    magnitude=0.15,
                    duration=2.0,
                    vfx="boost",
                    category=Category.TRACK,
                ),
            )
        )
        engine.emit_track_moment(
            kind="sugar_cube", headline="SUGAR CUBE!", emoji="🍬", params={"pos": spawn}
        )


class OfficeTwist(TrackTwist):
    """The Office: a door opens and yanks a mid-pack horse into a quick sync."""

    label = "MEETING PULL"
    PERIOD = 15.0
    HOLD_SECONDS = 0.8

    def __init__(self) -> None:
        self.next_pull_at = self.PERIOD

    def on_tick(self, engine: RaceEngine) -> None:
        if engine.race_time < self.next_pull_at:
            return
        self.next_pull_at += self.PERIOD
        ordered = engine.standings()
        midpack = [horse for horse in ordered[1:-1] if horse.racing]
        if not midpack:
            return
        victim = engine.rng.choice(midpack)
        engine.freeze_horse(victim, self.HOLD_SECONDS, tag="meeting")
        engine.emit_track_moment(
            kind="meeting_pull",
            headline="QUICK SYNC",
            emoji="👔",
            params={"horse_id": victim.id, "horse": victim.name},
        )


class PartyParrotTwist(TrackTwist):
    """Party Parrot Paradise: the whole field surges together on the beat.

    Deliberately symmetric — everyone gets the same pulse at the same moment, so
    it changes the *rhythm* of the race (and looks ridiculous) without handing
    anybody an edge.
    """

    label = "BEAT DROP"
    PERIOD = 8.0
    MAGNITUDE = 0.12
    DURATION = 1.0

    def __init__(self) -> None:
        self.next_drop_at = self.PERIOD
        self.drops = 0

    def on_tick(self, engine: RaceEngine) -> None:
        if engine.race_time < self.next_drop_at:
            return
        self.next_drop_at += self.PERIOD
        self.drops += 1
        for horse in engine.racing_horses():
            engine.add_effect(
                horse,
                Effect(
                    id="beat_drop",
                    label="Beat Drop",
                    magnitude=self.MAGNITUDE,
                    expires_at=engine.race_time + self.DURATION,
                    category=Category.TRACK,
                    vfx="party",
                ),
            )
        engine.emit_track_moment(
            kind="beat_drop",
            headline="BEAT DROP!",
            emoji="🦜",
            params={"drop": self.drops},
        )


@dataclass
class TrackDef:
    """Static description of a track plus its rendering theme."""

    id: str
    name: str
    tagline: str
    twist_label: str
    theme: dict[str, Any]
    twist_factory: Callable[[], TrackTwist] = TrackTwist
    weather_linger: float = 1.0
    """Multiplier on weather/mud event durations (turf holds water)."""
    stumble_duration_multiplier: float = 1.0
    stumble_speed_scale: float | None = None
    """Override for how slow a stumble is (higher = gentler)."""
    powerup_theme_bonus: dict[str, int] = field(default_factory=dict)
    event_tag_bonus: dict[str, int] = field(default_factory=dict)

    def client_meta(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "name": self.name,
            "tagline": self.tagline,
            "twist": self.twist_label,
            "theme": self.theme,
        }


TRACKS: dict[str, TrackDef] = {
    "churchill": TrackDef(
        id="churchill",
        name="Churchill Yowns",
        tagline="Roses, straw hats, and a bugle at dusk.",
        twist_label="Pure turf — no gimmicks",
        weather_linger=1.25,
        theme={
            "skyTop": "#F9C97A",
            "skyBottom": "#FDE9C8",
            "sunColor": "#FFE9A8",
            "sunY": 0.30,
            "hills": ["#8FBF6A", "#6FA555"],
            "ground": "#7FB25E",
            "groundDark": "#6B9C4E",
            "lane": "#8CC06B",
            "laneAlt": "#84B865",
            "rail": "#FFFFFF",
            "railPost": "#E9E4D5",
            "crowd": ["🎩", "👒", "🥂", "🎺", "👏", "🌹"],
            "props": ["🌳", "🏛️", "🌳", "🚩"],
            "ambient": "dust",
            "ambientColor": "#F6E3B8",
            "fog": "rgba(255, 226, 168, 0.14)",
            "finishArch": "#D64545",
            "accent": "#D64545",
            "music": "derby",
            "vignette": "rgba(90, 50, 0, 0.30)",
        },
    ),
    "neon": TrackDef(
        id="neon",
        name="Neon Circuit",
        tagline="Tron horses. Fixed boost pads. No excuses.",
        twist_label="3 boost pads at 25 / 50 / 75%",
        twist_factory=BoostPadTwist,
        theme={
            "skyTop": "#0B0420",
            "skyBottom": "#2A0A4A",
            "sunColor": "#FF3CAC",
            "sunY": 0.26,
            "hills": ["#1B0838", "#2C0F55"],
            "ground": "#150733",
            "groundDark": "#0E0424",
            "lane": "#1B0B3D",
            "laneAlt": "#170936",
            "rail": "#00E5FF",
            "railPost": "#FF3CAC",
            "crowd": ["🤖", "👾", "🛸", "💜", "🎮", "⚡"],
            "props": ["🏙️", "📡", "🛰️", "🏙️"],
            "ambient": "grid",
            "ambientColor": "#00E5FF",
            "fog": "rgba(0, 229, 255, 0.10)",
            "finishArch": "#00E5FF",
            "accent": "#FF3CAC",
            "music": "synth",
            "vignette": "rgba(0, 0, 30, 0.55)",
        },
    ),
    "wallst": TrackDef(
        id="wallst",
        name="Wall Street",
        tagline="Ticker tape, opening bells, and regime change.",
        twist_label="Bull/bear regimes flip every 10s",
        twist_factory=MarketRegimeTwist,
        powerup_theme_bonus={"finance": 10},
        theme={
            "skyTop": "#1B2A44",
            "skyBottom": "#41618F",
            "sunColor": "#DCE8FF",
            "sunY": 0.22,
            "hills": ["#16233A", "#1F3252"],
            "ground": "#2E2E38",
            "groundDark": "#24242C",
            "lane": "#37373F",
            "laneAlt": "#31313A",
            "rail": "#C9A227",
            "railPost": "#8C7318",
            "crowd": ["🤵", "💼", "📈", "📉", "☎️", "🗞️"],
            "props": ["🏦", "🏢", "🗽", "🏢"],
            "ambient": "ticker",
            "ambientColor": "#E8E3D0",
            "fog": "rgba(200, 220, 255, 0.08)",
            "finishArch": "#C9A227",
            "accent": "#3EDC81",
            "music": "market",
            "vignette": "rgba(5, 12, 30, 0.45)",
        },
    ),
    "lunar": TrackDef(
        id="lunar",
        name="Lunar Colony",
        tagline="One small step. One giant burrito.",
        twist_label="Low gravity: longer, gentler flails",
        twist_factory=LowGravityTwist,
        stumble_duration_multiplier=1.4,
        stumble_speed_scale=0.5,
        theme={
            "skyTop": "#03040F",
            "skyBottom": "#0B1030",
            "sunColor": "#7FB4FF",
            "sunY": 0.24,
            "hills": ["#3A3A47", "#4A4A58"],
            "ground": "#6E6E7C",
            "groundDark": "#5A5A66",
            "lane": "#7A7A88",
            "laneAlt": "#727280",
            "rail": "#D7D7E3",
            "railPost": "#9A9AA8",
            "crowd": ["👨‍🚀", "🛸", "🌎", "⭐", "📡", "👩‍🚀"],
            "props": ["🛰️", "🏗️", "🌑", "🚀"],
            "ambient": "stars",
            "ambientColor": "#FFFFFF",
            "fog": "rgba(120, 160, 255, 0.06)",
            "finishArch": "#9AD8FF",
            "accent": "#7FB4FF",
            "music": "space",
            "vignette": "rgba(0, 0, 20, 0.60)",
        },
    ),
    "candy": TrackDef(
        id="candy",
        name="Candy Canyon",
        tagline="Licorice rails and a suspicious amount of syrup.",
        twist_label="Syrup pools + a mid-race sugar cube",
        twist_factory=CandyTwist,
        theme={
            "skyTop": "#FFB8DE",
            "skyBottom": "#FFE3F3",
            "sunColor": "#FFF3B0",
            "sunY": 0.28,
            "hills": ["#F58FC2", "#E86FAE"],
            "ground": "#FFC8E4",
            "groundDark": "#F5A9D2",
            "lane": "#FFD5EC",
            "laneAlt": "#FFCBE6",
            "rail": "#5A2A3C",
            "railPost": "#7A3A52",
            "crowd": ["🍬", "🧁", "🍭", "🍩", "🎀", "🐻"],
            "props": ["🍦", "🍫", "🧃", "🍬"],
            "ambient": "sprinkles",
            "ambientColor": "#FFFFFF",
            "fog": "rgba(255, 200, 235, 0.16)",
            "finishArch": "#FF4D9D",
            "accent": "#FF4D9D",
            "music": "candy",
            "vignette": "rgba(120, 30, 80, 0.22)",
        },
    ),
    "parrot": TrackDef(
        id="parrot",
        name="Party Parrot Paradise",
        tagline="A rainbow, a beat, and far too many parrots.",
        twist_label="Beat drop: the whole field surges together",
        twist_factory=PartyParrotTwist,
        theme={
            "skyTop": "#ff4d6d",
            "skyBottom": "#ffd166",
            "sunColor": "#ffffff",
            "sunY": 0.24,
            "hills": ["#8f2bff", "#2bd9ff"],
            "ground": "#5b2bff",
            "groundDark": "#3d1bb0",
            "lane": "#6f3bff",
            "laneAlt": "#8a2be2",
            "rail": "#ffffff",
            "railPost": "#ffd166",
            "crowd": ["🦜", "🌈", "🎉", "🪩", "🥳", "🎊"],
            "props": ["🌈", "🪩", "🦜", "🎪"],
            "ambient": "confetti",
            "ambientColor": "#ffffff",
            "fog": "rgba(255, 255, 255, 0.06)",
            "finishArch": "#ffffff",
            "accent": "#ff4d6d",
            "music": "party",
            "vignette": "rgba(40, 0, 60, 0.35)",
            # The renderer cycles hue for anything colour-coded on this track.
            "rainbow": True,
        },
    ),
    "office": TrackDef(
        id="office",
        name="The Office",
        tagline="Carpet tile straightaway. Finish line is the elevator.",
        twist_label="Meeting pull grabs a mid-pack horse",
        twist_factory=OfficeTwist,
        event_tag_bonus={"office": 15},
        theme={
            "skyTop": "#DCE3E8",
            "skyBottom": "#EFF3F6",
            "sunColor": "#FFFFFF",
            "sunY": 0.18,
            "hills": ["#C3CCD4", "#B2BCC6"],
            "ground": "#8E9AA6",
            "groundDark": "#7C8894",
            "lane": "#98A4B0",
            "laneAlt": "#909CA8",
            "rail": "#5C6670",
            "railPost": "#49525A",
            "crowd": ["🧑‍💻", "☕", "🖨️", "📎", "🪴", "😐"],
            "props": ["🚪", "🪴", "🖨️", "🚪"],
            "ambient": "paper",
            "ambientColor": "#FFFFFF",
            "fog": "rgba(255, 255, 255, 0.10)",
            "finishArch": "#5C6670",
            "accent": "#4EA8FF",
            "music": "office",
            "vignette": "rgba(30, 40, 50, 0.25)",
        },
    ),
}

DEFAULT_TRACK_ID = "churchill"


def get_track(track_id: str | None) -> TrackDef:
    """Resolve a track id, falling back to the default rather than failing."""

    return TRACKS.get(track_id or DEFAULT_TRACK_ID, TRACKS[DEFAULT_TRACK_ID])


def track_catalog() -> list[dict[str, Any]]:
    return [track.client_meta() for track in TRACKS.values()]
