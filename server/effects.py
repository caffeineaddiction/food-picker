"""Speed modifiers, track zones and traps.

The simulation keeps every timed speed modifier in a single :class:`Effect`
list per horse. Effects are grouped into *categories* (powerup / event / track)
that clamp independently (SPEC.md §15.2) so a pile of buffs from one source can
never dominate the model.

Zones cover everything spatial with one type: mud patches, oil slicks, syrup
pools, banana peels, boost pads and collectible pickups. The engine only has to
walk one list per tick, and the renderer only has to know one shape.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from enum import Enum

from .constants import (
    EVENT_SUM_MAX,
    EVENT_SUM_MIN,
    MAX_VISIBLE_EFFECTS,
    POWERUP_SUM_MAX,
    POWERUP_SUM_MIN,
    TRACK_SUM_MAX,
    TRACK_SUM_MIN,
)


class Category(str, Enum):
    """Independent clamping buckets for speed modifiers."""

    POWERUP = "powerup"
    EVENT = "event"
    TRACK = "track"


CATEGORY_CLAMPS: dict[Category, tuple[float, float]] = {
    Category.POWERUP: (POWERUP_SUM_MIN, POWERUP_SUM_MAX),
    Category.EVENT: (EVENT_SUM_MIN, EVENT_SUM_MAX),
    Category.TRACK: (TRACK_SUM_MIN, TRACK_SUM_MAX),
}


class HostileOutcome(str, Enum):
    """Why a hostile effect did (or did not) land — drives display feedback."""

    APPLIED = "applied"
    SOFTENED = "softened"
    SHIELDED = "shielded"
    GHOSTED = "ghosted"
    IMMUNE = "immune"


@dataclass
class Effect:
    """A timed additive modifier to a horse's speed multiplier.

    ``magnitude`` is a delta on the base multiplier: ``+0.4`` means +40% speed.
    ``dynamic`` names a per-tick recompute rule (currently only Magnet Draft)
    handled by the engine; such effects ignore their stored magnitude.
    """

    id: str
    label: str
    magnitude: float
    expires_at: float
    category: Category = Category.POWERUP
    vfx: str = "boost"
    hostile: bool = False
    tier: str | None = None
    protective: bool = False
    dynamic: str | None = None
    source_player_id: str | None = None
    source_player_name: str | None = None

    def active(self, now: float) -> bool:
        return now < self.expires_at

    @property
    def is_buff(self) -> bool:
        return self.magnitude > 0


@dataclass
class EnterEffect:
    """What happens the instant a horse enters a zone."""

    id: str
    label: str
    magnitude: float = 0.0
    duration: float = 0.0
    vfx: str = "boost"
    category: Category = Category.EVENT
    stumble_seconds: float = 0.0
    hostile: bool = False


@dataclass
class Zone:
    """A stretch of track that does something to horses inside it.

    A zone may apply a continuous multiplier (``magnitude`` while inside),
    a one-shot :class:`EnterEffect`, or both. ``trap`` zones are player-placed
    and therefore ignored by Ghost Horse; weather is not.
    """

    id: int
    kind: str
    start: float
    end: float
    magnitude: float = 0.0
    category: Category = Category.EVENT
    expires_at: float = math.inf
    hostile: bool = False
    trap: bool = False
    consume_on_trigger: bool = False
    once_per_horse: bool = True
    enter: EnterEffect | None = None
    owner_name: str | None = None
    owner_id: str | None = None
    owner_horse_id: int | None = None
    """The thrower's horse, which is immune to its own trap."""
    consumed: bool = False
    triggered: set[int] = field(default_factory=set)

    def contains(self, pos: float) -> bool:
        return self.start <= pos <= self.end

    def alive(self, now: float) -> bool:
        return not self.consumed and now < self.expires_at


def sum_effects(effects: list[Effect], now: float) -> float:
    """Total clamped speed delta from all live effects on a horse."""

    totals: dict[Category, float] = {}
    for effect in effects:
        if effect.active(now):
            totals[effect.category] = totals.get(effect.category, 0.0) + effect.magnitude
    total = 0.0
    for category, raw in totals.items():
        low, high = CATEGORY_CLAMPS[category]
        total += min(high, max(low, raw))
    return total


def prune(effects: list[Effect], now: float) -> list[Effect]:
    """Drop expired effects and enforce the readability cap (§9.0).

    Protective effects (shield/ghost/diamond auras) are never culled — they are
    load-bearing for gameplay reads. Oldest ordinary effect goes first.
    """

    live = [effect for effect in effects if effect.active(now)]
    ordinary = [effect for effect in live if not effect.protective]
    if len(ordinary) > MAX_VISIBLE_EFFECTS:
        doomed = set(id(effect) for effect in ordinary[: len(ordinary) - MAX_VISIBLE_EFFECTS])
        live = [effect for effect in live if id(effect) not in doomed]
    return live


def upsert(effects: list[Effect], effect: Effect) -> None:
    """Add an effect, refreshing rather than stacking the same id (§9.0)."""

    for index, existing in enumerate(effects):
        if existing.id == effect.id:
            effects[index] = effect
            return
    effects.append(effect)
