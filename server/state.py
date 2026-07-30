"""Race state containers: horses, players, config and engine output events.

Everything here is plain data — no I/O, no asyncio — which is what makes the
simulation deterministic and unit-testable (SPEC.md §18.1).
"""

from __future__ import annotations

from collections import deque
from dataclasses import dataclass, field
from enum import Enum
from typing import Any

from .challenges import Challenge, ChallengeStats
from .constants import (
    INVENTORY_SLOTS,
    MAX_BACKED_HORSES,
    TAP_TPS_CAP,
    TAP_WINDOW_SECONDS,
)
from .effects import Effect


class RacePhase(str, Enum):
    """Phases owned by the simulation itself."""

    COUNTDOWN = "countdown"
    RUNNING = "running"
    FINISHED = "finished"


class HorseVisualState(str, Enum):
    """Coarse animation state the renderer switches rigs on."""

    GATE = "gate"
    RUN = "run"
    BOOST = "boost"
    SLOW = "slow"
    STUMBLE = "stumble"
    TUMBLE = "tumble"
    FROZEN = "frozen"
    FINISHED = "finished"
    ELIMINATED = "eliminated"


@dataclass
class HorseSpec:
    """Immutable identity of a dinner option, assigned in the lobby."""

    id: int
    name: str
    emoji: str
    color: str
    jockey: str
    breed: str = "thoroughbred"


@dataclass
class Horse:
    """Mutable per-race state for one dinner option."""

    spec: HorseSpec
    pos: float = 0.0
    prev_pos: float = 0.0
    speed: float = 0.0
    speed_multiplier: float = 1.0
    tap_bonus: float = 0.0
    rubber_band: float = 0.0
    noise: float = 1.0
    noise_target: float = 1.0
    noise_retarget_at: float = 0.0
    effects: list[Effect] = field(default_factory=list)
    stumble_until: float = 0.0
    stumble_is_tumble: bool = False
    freeze_until: float = 0.0
    freeze_immune_until: float = 0.0
    rug_immune_until: float = 0.0
    shield_charges: int = 0
    ghost_until: float = 0.0
    diamond_until: float = 0.0
    golden_until: float = 0.0
    muddy: bool = False
    backers: int = 0
    rank: int = 1
    next_drop_at: float = 0.0
    """Race time of this horse's next powerup drop; backers share the stream."""
    finished_at: float | None = None
    finish_rank: int = 0
    projected: bool = False
    """True when ``finished_at`` is an extrapolation (race wrapped up first)."""
    eliminated_at: float | None = None
    laps: int = 0

    # -- identity passthrough -------------------------------------------------
    @property
    def id(self) -> int:
        return self.spec.id

    @property
    def name(self) -> str:
        return self.spec.name

    @property
    def emoji(self) -> str:
        return self.spec.emoji

    # -- status helpers -------------------------------------------------------
    @property
    def racing(self) -> bool:
        """Still physically running (not finished, not eliminated)."""

        return self.finished_at is None and self.eliminated_at is None

    def is_ghost(self, now: float) -> bool:
        return now < self.ghost_until

    def is_diamond(self, now: float) -> bool:
        return now < self.diamond_until

    def is_golden(self, now: float) -> bool:
        return now < self.golden_until

    def is_frozen(self, now: float) -> bool:
        return now < self.freeze_until

    def is_stumbling(self, now: float) -> bool:
        return now < self.stumble_until

    def total_distance(self, track_length: float) -> float:
        """Progress including laps, for elimination mode ordering."""

        return self.laps * track_length + self.pos

    def visual_state(self, now: float) -> HorseVisualState:
        if self.eliminated_at is not None:
            return HorseVisualState.ELIMINATED
        if self.finished_at is not None:
            return HorseVisualState.FINISHED
        if self.is_frozen(now):
            return HorseVisualState.FROZEN
        if self.is_stumbling(now):
            return HorseVisualState.TUMBLE if self.stumble_is_tumble else HorseVisualState.STUMBLE
        if self.speed_multiplier >= 1.25:
            return HorseVisualState.BOOST
        if self.speed_multiplier <= 0.85:
            return HorseVisualState.SLOW
        return HorseVisualState.RUN

    def effect_tags(self, now: float) -> list[str]:
        """Visual tags for the renderer, protective auras first."""

        tags: list[str] = []
        if self.shield_charges > 0:
            tags.append("shield")
        if self.is_diamond(now):
            tags.append("diamond")
        if self.is_ghost(now):
            tags.append("ghost")
        if self.is_golden(now):
            tags.append("golden")
        if self.muddy:
            tags.append("muddy")
        for effect in self.effects:
            if effect.active(now) and effect.vfx and effect.vfx not in tags:
                tags.append(effect.vfx)
        return tags


@dataclass
class InventorySlot:
    """One inventory square: an item, and whether it has been unlocked yet.

    Items land locked behind a :class:`Challenge`. ``armed`` is the only thing
    :meth:`RaceEngine.use_powerup` trusts.
    """

    powerup_id: str | None = None
    armed: bool = False
    challenge: Challenge | None = None
    retry_at: float = 0.0
    """Race time before which a wrong answer blocks another attempt."""
    pace_held: float = 0.0
    """Seconds held inside the target band, for pace challenges."""

    @property
    def empty(self) -> bool:
        return self.powerup_id is None

    def clear(self) -> None:
        self.powerup_id = None
        self.armed = False
        self.challenge = None
        self.retry_at = 0.0
        self.pace_held = 0.0

    def client_meta(self, now: float) -> dict[str, Any] | None:
        if self.powerup_id is None:
            return None
        return {
            "powerup_id": self.powerup_id,
            "armed": self.armed,
            "challenge": self.challenge.client_meta() if self.challenge else None,
            "retryIn": max(0.0, round(self.retry_at - now, 1)),
            "paceHeld": round(self.pace_held, 2),
        }


@dataclass
class RacePlayer:
    """Per-race player state: tap history, inventory and drop schedule."""

    id: str
    name: str
    horse_id: int | None = None
    """The horse currently receiving this player's taps."""
    horse_ids: list[int] = field(default_factory=list)
    """Every horse this player backs (up to :data:`MAX_BACKED_HORSES`).

    Empty means "just the active one" — the single-horse case, which is most of
    them, needs no extra bookkeeping.
    """
    taps_total: int = 0
    tap_history: deque[tuple[float, int, int | None]] = field(default_factory=deque)
    """(race time, tap count, horse id) — taps are attributed per horse."""
    inventory: list[InventorySlot] = field(
        default_factory=lambda: [InventorySlot() for _ in range(INVENTORY_SLOTS)]
    )
    challenge_stats: ChallengeStats = field(default_factory=ChallengeStats)
    common_streak: int = 0
    last_granted_id: str | None = None
    grants: int = 0
    powerups_used: int = 0
    hits_landed: int = 0
    peak_tps: float = 0.0

    @property
    def is_player(self) -> bool:
        return self.horse_id is not None

    @property
    def backed_horse_ids(self) -> list[int]:
        """Every horse supported, falling back to the active one."""

        if self.horse_ids:
            return self.horse_ids
        return [] if self.horse_id is None else [self.horse_id]

    def backs(self, horse_id: int) -> bool:
        return horse_id in self.backed_horse_ids

    def set_backing(self, horse_ids: list[int], active: int | None = None) -> None:
        """Replace the backed set, keeping ``horse_id`` pointing inside it."""

        unique: list[int] = []
        for horse_id in horse_ids:
            if horse_id not in unique:
                unique.append(horse_id)
        self.horse_ids = unique[:MAX_BACKED_HORSES]
        if active is not None and active in self.horse_ids:
            self.horse_id = active
        elif self.horse_id not in self.horse_ids:
            self.horse_id = self.horse_ids[0] if self.horse_ids else None

    def drop_horse(self, horse_id: int) -> None:
        """Stop backing one horse (it was eliminated), keeping the rest."""

        remaining = [existing for existing in self.backed_horse_ids if existing != horse_id]
        self.set_backing(remaining)

    def credit_taps(self, now: float, count: int, horse_id: int | None = None) -> None:
        self.tap_history.append((now, count, horse_id if horse_id is not None else self.horse_id))
        self.taps_total += count

    def _trim(self, now: float) -> None:
        cutoff = now - TAP_WINDOW_SECONDS
        history = self.tap_history
        while history and history[0][0] < cutoff:
            history.popleft()

    def taps_per_second(self, now: float) -> float:
        """Total taps/sec over the rolling window, across every horse."""

        self._trim(now)
        return sum(count for _, count, _ in self.tap_history) / TAP_WINDOW_SECONDS

    def tap_rates(self, now: float) -> dict[int, float]:
        """Taps/sec per horse, for the phone's per-button feedback."""

        self._trim(now)
        rates: dict[int, float] = {}
        for _, count, horse_id in self.tap_history:
            if horse_id is None:
                continue
            rates[horse_id] = rates.get(horse_id, 0.0) + count / TAP_WINDOW_SECONDS
        return rates

    def effective_tps(self, now: float) -> float:
        """Tap rate after the honesty cap."""

        return min(self.taps_per_second(now), TAP_TPS_CAP)

    def tap_allocation(self, now: float) -> dict[int, float]:
        """Split this player's capped tap rate across the horses they tapped.

        The cap is on the *person*, not the button: someone with four buttons is
        worth exactly as much total influence as someone with one, they just get
        to choose how to spread it. Without this, four buttons would be four
        times the tap power and the balance envelopes in §2.3 would collapse.
        """

        rates = self.tap_rates(now)
        total = sum(rates.values())
        if total <= 0:
            return {}
        budget = self.effective_tps(now)
        return {horse_id: budget * (rate / total) for horse_id, rate in rates.items()}

    def free_slot(self) -> int | None:
        for index, slot in enumerate(self.inventory):
            if slot.empty:
                return index
        return None

    def inventory_view(self, now: float) -> list[dict[str, Any] | None]:
        return [slot.client_meta(now) for slot in self.inventory]


@dataclass
class RaceConfig:
    """Everything the engine needs to run one race."""

    mode_id: str = "classic"
    track_id: str = "churchill"
    duration: float = 60.0
    track_length: float = 1000.0
    powerups_on: bool = True
    events_on: bool = True
    seed: int = 0
    label: str | None = None
    """Optional heat label used by Tournament mode ("Heat 2", "FINAL")."""


class EngineEventKind(str, Enum):
    """Outbound simulation events; the room turns these into WS frames."""

    POWERUP_CAST = "powerup_cast"
    POWERUP_GRANT = "powerup_grant"
    CHALLENGE_SOLVED = "challenge_solved"
    INVENTORY = "inventory"
    EVENT_TELEGRAPH = "event_telegraph"
    EVENT_FIRED = "event_fired"
    PICKUP = "pickup"
    LEAD_CHANGE = "lead_change"
    ELIMINATED = "eliminated"
    HORSE_FINISHED = "horse_finished"
    RACE_FINISHED = "race_finished"
    INTEL = "intel"
    TRACK_MOMENT = "track_moment"


@dataclass
class EngineEvent:
    """One thing that happened in the sim.

    ``to_player`` marks private events (item grants, Insider Trading leaks);
    everything else is broadcast.
    """

    kind: EngineEventKind
    payload: dict[str, Any] = field(default_factory=dict)
    to_player: str | None = None
