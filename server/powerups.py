"""Powerup catalog and drop economy (SPEC.md §9).

Each powerup is a :class:`PowerupDef` with a small ``apply`` function that acts
on the engine through a narrow surface (``add_effect``, ``apply_hostile``,
``add_zone``, ``swap_positions``…). All the shared rules — shields, ghosting,
diamond immunity, the mercy rule, the final-stretch freeze lockout — live in the
engine so no single item can get them wrong.

Rarity rolling, pity timers and the leader tax live here too (§9.3).
"""

from __future__ import annotations

import random
from collections.abc import Callable
from dataclasses import dataclass, field
from enum import Enum
from typing import TYPE_CHECKING, Any

from . import constants as C
from .effects import Category, Effect, EnterEffect, HostileOutcome, Zone
from .state import Horse, RacePlayer

if TYPE_CHECKING:  # pragma: no cover - typing only
    from .engine import RaceEngine


class TargetClass(str, Enum):
    """How a powerup picks its victim, which drives the phone's fire flow."""

    SELF = "self"
    TARGET = "target"
    """Player chooses a rival from a sheet."""
    LEADER = "leader"
    """Auto-targets first place — one tap, no menu."""
    GLOBAL = "global"
    TRAP = "trap"
    """Placed on the track behind the caster."""


class Polarity(str, Enum):
    """What an item *does* to whoever it lands on.

    The phone shows this as an icon on the slot, so a player can tell "point this
    at my horse" from "point this at somebody else" without reading the blurb.
    """

    GOOD = "good"
    BAD = "bad"
    PROTECT = "protect"
    CHAOS = "chaos"


class Scope(str, Enum):
    """Who an item lands on, spelled out for the same reason."""

    MINE = "mine"
    RIVAL = "rival"
    LEADER = "leader"
    EVERYONE = "everyone"
    TRACK = "track"


POLARITY_ICON = {
    Polarity.GOOD: "⬆️",
    Polarity.BAD: "⬇️",
    Polarity.PROTECT: "🛡️",
    Polarity.CHAOS: "🎲",
}

SCOPE_LABEL = {
    Scope.MINE: "YOUR HORSE",
    Scope.RIVAL: "PICK A RIVAL",
    Scope.LEADER: "THE LEADER",
    Scope.EVERYONE: "EVERY HORSE",
    Scope.TRACK: "THE TRACK AHEAD",
}


class Tier(str, Enum):
    COMMON = "common"
    UNCOMMON = "uncommon"
    RARE = "rare"
    EPIC = "epic"


@dataclass
class CastContext:
    """Everything an ``apply`` function is allowed to touch."""

    engine: RaceEngine
    player: RacePlayer
    horse: Horse
    target: Horse | None
    now: float


@dataclass
class CastResult:
    """Outcome of a cast, used to build the display notification."""

    ok: bool = True
    target_label: str | None = None
    target_horse_id: int | None = None
    outcome: HostileOutcome = HostileOutcome.APPLIED
    extra: dict[str, Any] = field(default_factory=dict)
    reason: str | None = None

    @classmethod
    def fail(cls, reason: str) -> CastResult:
        return cls(ok=False, reason=reason)


@dataclass(frozen=True)
class PowerupDef:
    """Static definition of one powerup."""

    id: str
    name: str
    emoji: str
    tier: Tier
    target: TargetClass
    blurb: str
    apply: Callable[[CastContext], CastResult]
    polarity: Polarity = Polarity.GOOD
    scope: Scope = Scope.MINE
    duration: float = 0.0
    themes: tuple[str, ...] = ()
    """Flavour tags used for per-track drop weighting (e.g. ``finance``)."""
    sound: str = "cast"

    def client_meta(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "name": self.name,
            "emoji": self.emoji,
            "tier": self.tier.value,
            "target": self.target.value,
            "blurb": self.blurb,
            "duration": self.duration,
            "polarity": self.polarity.value,
            "polarityIcon": POLARITY_ICON[self.polarity],
            "scope": self.scope.value,
            "scopeLabel": SCOPE_LABEL[self.scope],
        }


# ---------------------------------------------------------------------------
# Effect builders — keep the individual items to one expressive line each
# ---------------------------------------------------------------------------


def _buff(
    powerup_id: str,
    label: str,
    magnitude: float,
    duration: float,
    vfx: str = "boost",
) -> Callable[[CastContext], CastResult]:
    """Self-targeted timed speed buff."""

    def apply(ctx: CastContext) -> CastResult:
        ctx.engine.add_effect(
            ctx.horse,
            Effect(
                id=powerup_id,
                label=label,
                magnitude=magnitude,
                expires_at=ctx.now + duration,
                vfx=vfx,
                source_player_id=ctx.player.id,
                source_player_name=ctx.player.name,
            ),
        )
        return CastResult(target_label=ctx.horse.name, target_horse_id=ctx.horse.id)

    return apply


def _debuff(
    powerup_id: str,
    label: str,
    magnitude: float,
    duration: float,
    vfx: str = "slow",
    tier: Tier = Tier.COMMON,
    stumble: float = 0.0,
) -> Callable[[CastContext], CastResult]:
    """Hostile timed slow, routed through the engine's protection rules."""

    def apply(ctx: CastContext) -> CastResult:
        target = ctx.target
        if target is None:
            return CastResult.fail("no_target")
        outcome = ctx.engine.apply_hostile(
            target,
            Effect(
                id=powerup_id,
                label=label,
                magnitude=magnitude,
                expires_at=ctx.now + duration,
                vfx=vfx,
                hostile=True,
                tier=tier.value,
                source_player_id=ctx.player.id,
                source_player_name=ctx.player.name,
            ),
            duration=duration,
            stumble_seconds=stumble,
        )
        return CastResult(target_label=target.name, target_horse_id=target.id, outcome=outcome)

    return apply


# ---------------------------------------------------------------------------
# Bespoke item behaviours
# ---------------------------------------------------------------------------


def _shield(ctx: CastContext) -> CastResult:
    ctx.horse.shield_charges = 1
    return CastResult(target_label=ctx.horse.name, target_horse_id=ctx.horse.id)


def _banana(ctx: CastContext) -> CastResult:
    """Hurl a peel in front of the horse ahead (or drop it behind if leading)."""

    engine = ctx.engine
    pos = engine.trap_placement(
        ctx.horse, behind_offset=C.BANANA_PLACEMENT_OFFSET, radius=C.BANANA_CATCH_RADIUS
    )
    engine.add_trap_zone(
        Zone(
            id=engine.next_zone_id(),
            kind="banana",
            start=pos - C.BANANA_CATCH_RADIUS,
            end=pos + C.BANANA_CATCH_RADIUS,
            expires_at=ctx.now + C.TRAP_LIFETIME_S,
            hostile=True,
            trap=True,
            consume_on_trigger=True,
            owner_id=ctx.player.id,
            owner_name=ctx.player.name,
            owner_horse_id=ctx.horse.id,
            enter=EnterEffect(
                id="banana_slip",
                label="Banana Peel",
                stumble_seconds=1.2,
                vfx="slip",
                hostile=True,
            ),
        )
    )
    return CastResult(target_label="the track", extra={"pos": pos})


def _diamond_hands(ctx: CastContext) -> CastResult:
    ctx.horse.diamond_until = max(ctx.horse.diamond_until, ctx.now + 6.0)
    ctx.engine.clear_debuffs(ctx.horse)
    return CastResult(target_label=ctx.horse.name, target_horse_id=ctx.horse.id)


def _reply_all_storm(ctx: CastContext) -> CastResult:
    engine = ctx.engine
    if not engine.claim_global_cooldown("reply_all_storm"):
        return CastResult.fail("cooldown")
    for horse in engine.racing_horses():
        if horse.id == ctx.horse.id:
            continue
        engine.add_effect(
            horse,
            Effect(
                id="reply_all",
                label="Reply-All Storm",
                magnitude=-0.15,
                expires_at=ctx.now + 2.0,
                category=Category.EVENT,
                vfx="mail",
                hostile=True,
                source_player_id=ctx.player.id,
                source_player_name=ctx.player.name,
            ),
        )
    return CastResult(target_label="the whole field")


def _circuit_breaker(ctx: CastContext) -> CastResult:
    target = ctx.target
    if target is None:
        return CastResult.fail("no_target")
    outcome = ctx.engine.apply_freeze(
        target,
        seconds=1.2,
        tag="halted",
        source_player_id=ctx.player.id,
        source_player_name=ctx.player.name,
    )
    return CastResult(target_label=target.name, target_horse_id=target.id, outcome=outcome)


def _fed_rate_cut(ctx: CastContext) -> CastResult:
    engine = ctx.engine
    for horse in engine.racing_horses():
        magnitude = 0.45 if horse.id == ctx.horse.id else 0.25
        engine.add_effect(
            horse,
            Effect(
                id="fed_rate_cut",
                label="Fed Rate Cut",
                magnitude=magnitude,
                expires_at=ctx.now + 3.0,
                category=Category.EVENT,
                vfx="money",
                source_player_id=ctx.player.id,
                source_player_name=ctx.player.name,
            ),
        )
    return CastResult(target_label="everyone (but mostly " + ctx.horse.name + ")")


def _rug_pull(ctx: CastContext) -> CastResult:
    target = ctx.target
    if target is None:
        return CastResult.fail("no_target")
    if ctx.now < target.rug_immune_until:
        return CastResult(
            target_label=target.name, target_horse_id=target.id, outcome=HostileOutcome.IMMUNE
        )
    outcome = ctx.engine.apply_hostile(
        target,
        Effect(
            id="rug_pull",
            label="Rug Pull",
            magnitude=-0.30,
            expires_at=ctx.now + 3.0,
            vfx="rug",
            hostile=True,
            tier=Tier.EPIC.value,
            source_player_id=ctx.player.id,
            source_player_name=ctx.player.name,
        ),
        duration=3.0,
        stumble_seconds=1.0,
        tumble=True,
        ignore_mercy=True,
    )
    if outcome is HostileOutcome.APPLIED:
        target.rug_immune_until = ctx.now + C.RUG_PULL_IMMUNITY_S
    return CastResult(target_label=target.name, target_horse_id=target.id, outcome=outcome)


def _golden_carrot(ctx: CastContext) -> CastResult:
    ctx.horse.golden_until = max(ctx.horse.golden_until, ctx.now + 5.0)
    ctx.engine.add_effect(
        ctx.horse,
        Effect(
            id="golden_carrot",
            label="Golden Carrot",
            magnitude=0.35,
            expires_at=ctx.now + 5.0,
            vfx="golden",
            source_player_id=ctx.player.id,
            source_player_name=ctx.player.name,
        ),
    )
    return CastResult(target_label=ctx.horse.name, target_horse_id=ctx.horse.id)


# ---------------------------------------------------------------------------
# The catalog
# ---------------------------------------------------------------------------

POWERUPS: dict[str, PowerupDef] = {}


def _register(powerup: PowerupDef) -> PowerupDef:
    POWERUPS[powerup.id] = powerup
    return powerup


# The catalog is deliberately short. Twelve items that each read instantly from
# a phone beat twenty-six that need explaining — and every one has to fit on the
# countdown primer card. Magnitudes are generous because a powerup now costs you
# a few seconds of not tapping to unlock (see `Challenge`).
# fmt: off
_register(PowerupDef("turbo_boost", "Turbo Boost", "🚀", Tier.COMMON, TargetClass.SELF,
    "+55% speed for 3s.", _buff("turbo_boost", "Turbo Boost", 0.55, 3.0),
    polarity=Polarity.GOOD, scope=Scope.MINE, duration=3.0))
_register(PowerupDef("tailwind", "Tailwind", "🍃", Tier.COMMON, TargetClass.SELF,
    "+22% speed for a long 8s.", _buff("tailwind", "Tailwind", 0.22, 8.0, vfx="leaves"),
    polarity=Polarity.GOOD, scope=Scope.MINE, duration=8.0))
_register(PowerupDef("headwind", "Headwind", "🌬️", Tier.COMMON, TargetClass.LEADER,
    "Leader −28% for 4s.", _debuff("headwind", "Headwind", -0.28, 4.0, vfx="wind"),
    polarity=Polarity.BAD, scope=Scope.LEADER, duration=4.0))
_register(PowerupDef("shield", "Shield", "🛡️", Tier.COMMON, TargetClass.SELF,
    "Blocks the next hostile effect.", _shield,
    polarity=Polarity.PROTECT, scope=Scope.MINE, sound="shield"))

_register(PowerupDef("rocket_horseshoes", "Rocket Horseshoes", "🎇", Tier.UNCOMMON, TargetClass.SELF,
    "+95% speed for 2s. Photo-finish fuel.",
    _buff("rocket_horseshoes", "Rocket Horseshoes", 0.95, 2.0, vfx="rocket"),
    polarity=Polarity.GOOD, scope=Scope.MINE, duration=2.0))
_register(PowerupDef("short_seller", "Short Seller", "📉", Tier.UNCOMMON, TargetClass.TARGET,
    "Pick a rival: −40% for 4s.",
    _debuff("short_seller", "Short Seller", -0.40, 4.0, vfx="short", tier=Tier.UNCOMMON),
    polarity=Polarity.BAD, scope=Scope.RIVAL, duration=4.0, themes=("finance",)))
_register(PowerupDef("banana", "Banana Peel", "🍌", Tier.UNCOMMON, TargetClass.TRAP,
    "Hurl it at the horse ahead. Somebody eats it.", _banana,
    polarity=Polarity.BAD, scope=Scope.TRACK, duration=15.0))
_register(PowerupDef("diamond_hands", "Diamond Hands", "💎", Tier.UNCOMMON, TargetClass.SELF,
    "Immune to slows for 7s.", _diamond_hands,
    polarity=Polarity.PROTECT, scope=Scope.MINE, duration=7.0,
    themes=("finance",), sound="diamond"))

_register(PowerupDef("circuit_breaker", "Circuit Breaker", "🛑", Tier.RARE, TargetClass.TARGET,
    "TRADING HALTED: a rival stops dead for 1.4s.", _circuit_breaker,
    polarity=Polarity.BAD, scope=Scope.RIVAL, duration=1.4,
    themes=("finance",), sound="freeze"))
_register(PowerupDef("fed_rate_cut", "Fed Rate Cut", "🏦", Tier.RARE, TargetClass.GLOBAL,
    "EVERY horse +25%, yours +60%, for 3s.", _fed_rate_cut,
    polarity=Polarity.CHAOS, scope=Scope.EVERYONE, duration=3.0, themes=("finance",)))
_register(PowerupDef("reply_all", "Reply-All Storm", "📧", Tier.RARE, TargetClass.GLOBAL,
    "EVERY other horse −22% for 2s.", _reply_all_storm,
    polarity=Polarity.BAD, scope=Scope.EVERYONE, duration=2.0, themes=("office",)))

_register(PowerupDef("rug_pull", "Rug Pull", "🧻", Tier.EPIC, TargetClass.LEADER,
    "Yank the rug: the leader tumbles, then −35%.", _rug_pull,
    polarity=Polarity.BAD, scope=Scope.LEADER, duration=3.0,
    themes=("finance",), sound="epic"))
_register(PowerupDef("golden_carrot", "Golden Carrot", "🥕", Tier.EPIC, TargetClass.SELF,
    "+45%, higher tap cap, shrugs off small hits. 5s.", _golden_carrot,
    polarity=Polarity.GOOD, scope=Scope.MINE, duration=5.0, sound="epic"))
# fmt: on


def powerup_catalog() -> list[dict[str, Any]]:
    """Client-facing catalog, sent once in ``welcome`` (§18.3)."""

    return [powerup.client_meta() for powerup in POWERUPS.values()]


BY_TIER: dict[Tier, list[PowerupDef]] = {tier: [] for tier in Tier}
for _powerup in POWERUPS.values():
    BY_TIER[_powerup.tier].append(_powerup)


# ---------------------------------------------------------------------------
# Drop economy (§9.3, §15.4)
# ---------------------------------------------------------------------------


def roll_tier(
    rng: random.Random,
    weights: dict[str, int],
    *,
    common_streak: int,
    last_place: bool,
) -> Tier:
    """Pick a rarity tier, honouring the pity timer and last-place charity."""

    table = dict(weights)
    if last_place:
        table["rare"] = table.get("rare", 0) + C.LAST_PLACE_RARE_BONUS
    if common_streak >= C.PITY_COMMON_STREAK:
        table.pop("common", None)
    tiers = list(table.keys())
    picks = [max(0, table[tier]) for tier in tiers]
    if sum(picks) <= 0:
        return Tier.COMMON
    return Tier(rng.choices(tiers, weights=picks, k=1)[0])


def roll_powerup(
    rng: random.Random,
    *,
    tier: Tier,
    theme_bonus: dict[str, int] | None = None,
) -> PowerupDef:
    """Pick an item within a tier, applying per-track theme weighting (§10.3)."""

    pool = BY_TIER[tier]
    weights: list[float] = []
    for powerup in pool:
        weight = 100.0
        for theme, bonus in (theme_bonus or {}).items():
            if theme in powerup.themes:
                weight += bonus
        weights.append(weight)
    return rng.choices(pool, weights=weights, k=1)[0]


def is_dead_draw(
    powerup: PowerupDef,
    horse: Horse,
    *,
    now: float,
    is_leader: bool = False,
    last_granted_id: str | None = None,
) -> bool:
    """Grant-time reroll rules (§9.3) — never hand out a useless item."""

    if powerup.id == "shield" and horse.shield_charges > 0:
        return True
    if powerup.id == "diamond_hands" and horse.is_diamond(now):
        return True
    if powerup.id == last_granted_id and powerup.tier is Tier.COMMON:
        # Two identical commons back to back feels like the game stopped rolling.
        return True
    return False
