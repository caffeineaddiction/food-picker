"""The race simulation (SPEC.md §15).

``RaceEngine`` is pure Python: no sockets, no asyncio, no clocks. It is driven
by :meth:`RaceEngine.step` at a fixed 20 Hz, fed player intents through
:meth:`apply_taps` / :meth:`use_powerup`, and read through :meth:`snapshot`.

Because the only randomness comes from ``self.rng`` (seeded per race), the same
config + seed + input trace always produces the same race. That is what makes
the Monte Carlo balance suite possible.
"""

from __future__ import annotations

import math
import random
from collections.abc import Callable, Iterable
from dataclasses import dataclass
from typing import Any

from . import constants as C
from .challenges import generate as generate_challenge
from .effects import (
    Effect,
    EnterEffect,
    HostileOutcome,
    Zone,
    prune,
    sum_effects,
    upsert,
)
from .events import EventDef, pick_event
from .modes import ModeDef, get_mode
from .powerups import (
    POWERUPS,
    CastContext,
    PowerupDef,
    TargetClass,
    Tier,
    is_dead_draw,
    roll_powerup,
    roll_tier,
)
from .state import (
    EngineEvent,
    EngineEventKind,
    Horse,
    HorseSpec,
    RaceConfig,
    RacePhase,
    RacePlayer,
)
from .tracks import TrackDef, get_track


@dataclass
class _ScheduledCall:
    """A deterministic timer: engine-time callback with no wall clock involved."""

    at: float
    fn: Callable[[], None]


@dataclass
class _PendingEvent:
    """A random event picked in advance so Insider Trading has something to leak."""

    definition: EventDef
    fire_at: float
    telegraphed: bool = False
    leaked: bool = False


class RaceEngine:
    """Authoritative simulation for a single race."""

    def __init__(
        self,
        config: RaceConfig,
        horse_specs: Iterable[HorseSpec],
        players: Iterable[RacePlayer],
    ) -> None:
        self.config = config
        self.mode: ModeDef = get_mode(config.mode_id)
        self.track: TrackDef = get_track(config.track_id)
        self.twist = self.track.twist_factory()
        self.rng = random.Random(config.seed)

        self.horses: list[Horse] = [Horse(spec=spec) for spec in horse_specs]
        self.horses_by_id: dict[int, Horse] = {horse.id: horse for horse in self.horses}
        self.players: dict[str, RacePlayer] = {player.id: player for player in players}

        self.phase: RacePhase = RacePhase.COUNTDOWN
        self.race_time: float = -C.COUNTDOWN_SECONDS
        self.tick: int = 0
        self.base_speed: float = (
            config.track_length / max(1.0, config.duration) * C.BASE_SPEED_CALIBRATION
        )

        self.zones: list[Zone] = []
        self.finish_order: list[Horse] = []
        self.photo_finish: bool = False
        self.first_finish_at: float | None = None

        self._zone_seq: int = 0
        self._scheduled: list[_ScheduledCall] = []
        self._out: list[EngineEvent] = []
        self._global_cooldowns: dict[str, float] = {}
        self._intel_players: set[str] = set()
        self._used_event_ids: set[str] = set()
        self._pending_events: list[_PendingEvent] = []
        self._tap_efficiency: float = 1.0
        self._tap_efficiency_until: float = 0.0
        self._lead_horse_id: int | None = None
        self._next_elimination_at: float = self.mode.elimination_interval

        self._init_horses()
        self._schedule_first_event()
        self._grant_starting_powerups()
        self.twist.on_start(self)

    # ------------------------------------------------------------------
    # Setup
    # ------------------------------------------------------------------

    def _init_horses(self) -> None:
        low, high = self.mode.noise_range
        for index, horse in enumerate(self.horses):
            horse.noise = self.rng.uniform(low, high)
            horse.noise_target = self.rng.uniform(low, high)
            horse.noise_retarget_at = self.rng.uniform(
                C.NOISE_RETARGET_MIN_S, C.NOISE_RETARGET_MAX_S
            )
            horse.rank = index + 1
        self.recount_backers()

    def recount_backers(self) -> None:
        """Refresh per-horse backer counts (display pips + team feel)."""

        counts: dict[int, int] = {horse.id: 0 for horse in self.horses}
        for player in self.players.values():
            for horse_id in player.backed_horse_ids:
                if horse_id in counts:
                    counts[horse_id] += 1
        for horse in self.horses:
            horse.backers = counts[horse.id]

    def _grant_starting_powerups(self) -> None:
        if not self.config.powerups_on:
            return
        for horse in self.horses:
            horse.next_drop_at = C.FIRST_DROP_AT_S * self.mode.drop_interval_multiplier
        for player in self.players.values():
            for _ in range(self.mode.starting_powerups):
                if player.is_player:
                    self._grant_powerup(player)

    # ------------------------------------------------------------------
    # Public simulation surface
    # ------------------------------------------------------------------

    def drain_events(self) -> list[EngineEvent]:
        """Take everything emitted since the last drain.

        Client intents (a cast, a challenge answer) arrive *between* ticks, and
        they emit too. If the queue were only ever read by :meth:`step`, those
        events would land in a list the room had already finished dispatching and
        vanish on the next tick — which silently swallowed powerup notifications,
        the game's single most important piece of feedback. Anything that lets a
        player change the race must drain afterwards.
        """

        events = self._out
        self._out = []
        return events

    def step(self) -> list[EngineEvent]:
        """Advance one tick and return everything that happened."""

        self.tick += 1
        # Derived, not accumulated: repeated += TICK_DT drifts by ~1e-16 per tick,
        # which is enough to miss an exact `>= 0.0` phase boundary.
        self.race_time = self.tick * C.TICK_DT - C.COUNTDOWN_SECONDS

        if self.phase is RacePhase.COUNTDOWN:
            if self.race_time >= 0.0:
                self.phase = RacePhase.RUNNING
            else:
                return self.drain_events()

        if self.phase is RacePhase.FINISHED:
            return self.drain_events()

        self._run_scheduled()
        self._update_noise()
        self._maybe_grant_powerups()
        self._update_pace_challenges()
        self._maybe_run_events()
        self.twist.on_tick(self)
        self._integrate()
        self._resolve_zones()
        self._update_standings()
        self._maybe_eliminate()
        self._check_race_end()
        return self.drain_events()

    def horse_for(self, player: RacePlayer) -> Horse | None:
        """The horse a player backs, if any. (Horse id 0 is valid — no truthiness.)"""

        if player.horse_id is None:
            return None
        return self.horses_by_id.get(player.horse_id)

    def answer_challenge(self, player_id: str, slot: int, choice: int) -> tuple[bool, str | None]:
        """Attempt a multiple-choice unlock. Returns ``(correct, reason)``."""

        player = self.players.get(player_id)
        if player is None or not 0 <= slot < len(player.inventory):
            return False, "bad_slot"
        held = player.inventory[slot]
        if held.powerup_id is None or held.challenge is None:
            return False, "nothing_to_unlock"
        if self.race_time < held.retry_at:
            return False, "cooling_down"
        if held.challenge.is_pace:
            return False, "pace_challenge"

        if held.challenge.is_correct(choice):
            self._arm(player, slot)
            return True, None

        player.challenge_stats.record_failed()
        held.retry_at = self.race_time + C.CHALLENGE_RETRY_SECONDS
        # A fresh question, so a wrong guess can't be walked through by
        # elimination — but never swap them onto a pace task mid-attempt.
        held.challenge = generate_challenge(self.rng, allow_pace=False)
        self._emit_inventory(player)
        return False, "wrong"

    def _arm(self, player: RacePlayer, slot: int) -> None:
        held = player.inventory[slot]
        held.armed = True
        held.challenge = None
        held.retry_at = 0.0
        held.pace_held = 0.0
        player.challenge_stats.record_solved(slot, self.race_time)
        self._emit(
            EngineEventKind.CHALLENGE_SOLVED,
            {"slot": slot, "powerup_id": held.powerup_id},
            to_player=player.id,
        )
        self._emit_inventory(player)

    def _update_pace_challenges(self) -> None:
        """Judge "hold this rate" unlocks from the tap stream we already have."""

        for player in self.players.values():
            rate = player.taps_per_second(self.race_time)
            for slot_index, held in enumerate(player.inventory):
                challenge = held.challenge
                if challenge is None or not challenge.is_pace or held.armed:
                    continue
                if challenge.rate_in_band(rate):
                    held.pace_held += C.TICK_DT
                    if held.pace_held >= challenge.hold_seconds:
                        self._arm(player, slot_index)
                elif held.pace_held > 0:
                    # Drifting out of the band loses ground rather than resetting:
                    # one stray tap shouldn't wipe two seconds of careful thumbing.
                    held.pace_held = max(0.0, held.pace_held - C.TICK_DT * 1.5)

    def apply_taps(self, player_id: str, count: int, horse_id: int | None = None) -> None:
        """Credit taps to one of the player's horses (their primary by default)."""

        player = self.players.get(player_id)
        if player is None or not player.is_player:
            return
        if self.phase is not RacePhase.RUNNING:
            return
        target = player.horse_id if horse_id is None else horse_id
        if target is None or not player.backs(target):
            return
        horse = self.horses_by_id.get(target)
        if horse is None or not horse.racing:
            return
        player.credit_taps(self.race_time, count, target)
        player.peak_tps = max(player.peak_tps, player.taps_per_second(self.race_time))

    def use_powerup(
        self, player_id: str, slot: int, target_horse_id: int | None
    ) -> tuple[bool, str | None]:
        """Fire an inventory item. Returns ``(consumed, failure_reason)``."""

        player = self.players.get(player_id)
        if player is None or not player.is_player:
            return False, "not_a_player"
        if self.phase is not RacePhase.RUNNING:
            return False, "race_not_running"
        if not 0 <= slot < len(player.inventory):
            return False, "bad_slot"
        held = player.inventory[slot]
        powerup_id = held.powerup_id
        if powerup_id is None:
            return False, "empty_slot"
        if not held.armed:
            return False, "locked"
        powerup = POWERUPS.get(powerup_id)
        horse = self.horse_for(player)
        if powerup is None or horse is None or not horse.racing:
            return False, "unavailable"

        # A multi-horse backer chooses which of *their* horses a self-buff or a
        # dropped trap applies to; single-horse players never see the question.
        if (
            powerup.target in (TargetClass.SELF, TargetClass.TRAP)
            and target_horse_id is not None
            and player.backs(target_horse_id)
        ):
            chosen = self.horses_by_id.get(target_horse_id)
            if chosen is not None and chosen.racing:
                horse = chosen

        target = self._resolve_target(powerup, horse, target_horse_id)
        if powerup.target is TargetClass.TARGET and target is None:
            return False, "needs_target"

        result = powerup.apply(
            CastContext(engine=self, player=player, horse=horse, target=target, now=self.race_time)
        )
        if not result.ok:
            return False, result.reason

        held.clear()
        player.powerups_used += 1
        if result.outcome is HostileOutcome.APPLIED and powerup.target in (
            TargetClass.TARGET,
            TargetClass.LEADER,
        ):
            player.hits_landed += 1

        self._emit(
            EngineEventKind.POWERUP_CAST,
            {
                "player": player.name,
                "player_id": player.id,
                "powerup_id": powerup.id,
                "powerup": powerup.name,
                "emoji": powerup.emoji,
                "tier": powerup.tier.value,
                "sound": powerup.sound,
                "target": result.target_label,
                "target_horse_id": result.target_horse_id,
                "caster_horse_id": horse.id,
                "caster_horse": horse.name,
                "outcome": result.outcome.value,
                **result.extra,
            },
        )
        self._emit_inventory(player)
        return True, None

    def _resolve_target(
        self, powerup: PowerupDef, caster_horse: Horse, requested_id: int | None
    ) -> Horse | None:
        if powerup.target is TargetClass.LEADER:
            leader = self.leader()
            if leader is not None and leader.id == caster_horse.id:
                # Never let an auto-target item hit its own caster.
                standings = [h for h in self.standings() if h.racing and h.id != caster_horse.id]
                return standings[0] if standings else None
            return leader
        if powerup.target is TargetClass.TARGET:
            target = self.horses_by_id.get(requested_id if requested_id is not None else -1)
            if target is None or target.id == caster_horse.id or not target.racing:
                return None
            return target
        return None

    # ------------------------------------------------------------------
    # Simulation internals
    # ------------------------------------------------------------------

    def _run_scheduled(self) -> None:
        if not self._scheduled:
            return
        due = [call for call in self._scheduled if call.at <= self.race_time]
        if not due:
            return
        self._scheduled = [call for call in self._scheduled if call.at > self.race_time]
        for call in due:
            call.fn()

    def _update_noise(self) -> None:
        low, high = self.mode.noise_range
        blend = min(1.0, C.TICK_DT / C.NOISE_LERP_SECONDS)
        for horse in self.horses:
            if self.race_time >= horse.noise_retarget_at:
                horse.noise_target = self.rng.uniform(low, high)
                horse.noise_retarget_at = self.race_time + self.rng.uniform(
                    C.NOISE_RETARGET_MIN_S, C.NOISE_RETARGET_MAX_S
                )
            horse.noise += (horse.noise_target - horse.noise) * blend

    def _integrate(self) -> None:
        """Advance every horse by one tick of the velocity model (§15.2)."""

        mean = self._mean_distance()
        tap_bonuses = self._tap_bonuses()
        for horse in self.horses:
            horse.prev_pos = horse.pos
            horse.effects = prune(horse.effects, self.race_time)
            if horse.stumble_is_tumble and not horse.is_stumbling(self.race_time):
                # Clear the flag when the fall ends, or every later trip — even a
                # gentle one — would keep spinning the horse.
                horse.stumble_is_tumble = False

            if horse.eliminated_at is not None:
                horse.speed = 0.0
                continue

            if horse.finished_at is not None:
                # Winners coast past the line and slow to a victory trot.
                horse.speed *= 0.97
                horse.pos += horse.speed * C.TICK_DT
                continue

            horse.tap_bonus = tap_bonuses.get(horse.id, 0.0)
            horse.rubber_band = self._rubber_band(horse, mean)
            multiplier = 1.0 + horse.tap_bonus + horse.rubber_band
            multiplier += self._dynamic_effect_total(horse)
            multiplier += self._zone_multiplier(horse)
            multiplier = max(C.SPEED_MULTIPLIER_FLOOR, multiplier)

            if horse.is_frozen(self.race_time):
                multiplier = C.FREEZE_SPEED_SCALE
            elif horse.is_stumbling(self.race_time):
                scale = self.track.stumble_speed_scale or C.STUMBLE_SPEED_SCALE
                multiplier *= scale

            horse.speed_multiplier = multiplier
            horse.speed = self.base_speed * horse.noise * multiplier
            new_pos = horse.pos + horse.speed * C.TICK_DT
            self._advance(horse, new_pos)

    def _advance(self, horse: Horse, new_pos: float) -> None:
        """Move a horse, handling lap wrap (elimination) or finish crossing."""

        length = self.config.track_length
        if self.mode.elimination:
            horse.pos = new_pos
            if horse.pos >= length:
                horse.pos -= length
                horse.laps += 1
            return

        if new_pos >= length and horse.finished_at is None:
            travelled = new_pos - horse.pos
            fraction = 1.0 if travelled <= 0 else (length - horse.pos) / travelled
            horse.finished_at = self.race_time + fraction * C.TICK_DT
            horse.finish_rank = len(self.finish_order) + 1
            self.finish_order.append(horse)
            if self.first_finish_at is None:
                self.first_finish_at = horse.finished_at
                self._evaluate_photo_finish(horse)
            self._emit(
                EngineEventKind.HORSE_FINISHED,
                {
                    "horse_id": horse.id,
                    "horse": horse.name,
                    "rank": horse.finish_rank,
                    "time": round(horse.finished_at, 3),
                },
            )
        horse.pos = new_pos

    def _mean_distance(self) -> float:
        racing = [horse for horse in self.horses if horse.racing]
        if not racing:
            return 0.0
        return sum(h.total_distance(self.config.track_length) for h in racing) / len(racing)

    def _rubber_band(self, horse: Horse, mean: float) -> float:
        """Catch-up force with a deadzone (§15.5).

        Inside the deadzone the band does nothing at all, so the pack shuffles
        freely on its own noise — that shuffling is what keeps every dinner
        option live. Outside it the force ramps hard, so a genuine breakaway
        gets reeled in. A band without a deadzone suppresses the pack's spread
        as well as the leader's lead, which makes any constant advantage (a
        sweaty tapper) mathematically uncatchable.
        """

        delta = mean - horse.total_distance(self.config.track_length)
        if abs(delta) <= C.RUBBER_BAND_DEADZONE:
            return 0.0
        slack = delta - math.copysign(C.RUBBER_BAND_DEADZONE, delta)
        raw = C.RUBBER_BAND_GAIN * slack * self.mode.rubber_band_multiplier
        return min(C.RUBBER_BAND_MAX, max(C.RUBBER_BAND_MIN, raw))

    def _tap_bonuses(self) -> dict[int, float]:
        """Combined tap bonus T per horse (§15.3)."""

        combined: dict[int, float] = {}
        for player in self.players.values():
            for horse_id, rate in player.tap_allocation(self.race_time).items():
                if rate > 0:
                    combined[horse_id] = combined.get(horse_id, 0.0) + rate

        efficiency = self._tap_efficiency if self.race_time < self._tap_efficiency_until else 1.0
        bonuses: dict[int, float] = {}
        for horse_id, total in combined.items():
            horse = self.horses_by_id.get(horse_id)
            if horse is None:
                continue
            ceiling = (
                C.TAP_BONUS_MAX_GOLDEN if horse.is_golden(self.race_time) else C.TAP_BONUS_MAX
            )
            bonuses[horse_id] = (
                ceiling * (1.0 - math.exp(-(total * efficiency) / C.TAP_BONUS_SCALE))
            )
        return bonuses

    def _dynamic_effect_total(self, horse: Horse) -> float:
        """Effect sum, resolving per-tick dynamic magnitudes first."""

        for effect in horse.effects:
            if effect.dynamic == "magnet_draft" and effect.active(self.race_time):
                ahead = self.horse_ahead_of(horse)
                if ahead is None:
                    effect.magnitude = 0.10
                else:
                    gap = ahead.total_distance(self.config.track_length) - horse.total_distance(
                        self.config.track_length
                    )
                    effect.magnitude = min(0.45, max(0.05, 0.006 * gap))
        return sum_effects(horse.effects, self.race_time)

    def _zone_hits(self, zone: Zone, horse: Horse) -> bool:
        """Can this zone affect this horse right now?"""

        if not zone.alive(self.race_time) or not zone.contains(horse.pos):
            return False
        if zone.trap:
            # You never trip over the trap you threw — a forward throw would
            # otherwise catch its own caster on the way past.
            if zone.owner_horse_id == horse.id:
                return False
            if horse.is_ghost(self.race_time):
                return False
        return True

    def _zone_multiplier(self, horse: Horse) -> float:
        total = 0.0
        for zone in self.zones:
            if not self._zone_hits(zone, horse):
                continue
            if zone.magnitude:
                if zone.hostile and horse.is_diamond(self.race_time):
                    continue
                total += zone.magnitude
        return min(C.ZONE_SUM_MAX, max(C.ZONE_SUM_MIN, total))

    def _resolve_zones(self) -> None:
        """Trigger one-shot zone entries (traps, pads, pickups) and drop dead zones."""

        for zone in list(self.zones):
            if not zone.alive(self.race_time):
                continue
            for horse in self.horses:
                if not horse.racing or not self._zone_hits(zone, horse):
                    continue
                if zone.kind == "mud":
                    horse.muddy = True
                if zone.enter is None:
                    continue
                if zone.once_per_horse and horse.id in zone.triggered:
                    continue
                zone.triggered.add(horse.id)
                self._apply_enter_effect(zone, horse)
                if zone.consume_on_trigger:
                    zone.consumed = True
                    break
        self.zones = [zone for zone in self.zones if zone.alive(self.race_time)]

    def _apply_enter_effect(self, zone: Zone, horse: Horse) -> None:
        enter: EnterEffect = zone.enter  # type: ignore[assignment]
        if enter.hostile:
            if horse.is_diamond(self.race_time) or horse.shield_charges > 0:
                if horse.shield_charges > 0 and not horse.is_diamond(self.race_time):
                    horse.shield_charges -= 1
                self._emit(
                    EngineEventKind.PICKUP,
                    {
                        "kind": zone.kind,
                        "horse_id": horse.id,
                        "horse": horse.name,
                        "blocked": True,
                    },
                )
                return
        if enter.stumble_seconds:
            self.stumble_horse(horse, enter.stumble_seconds, tumble=True)
        if enter.magnitude and enter.duration:
            self.add_effect(
                horse,
                Effect(
                    id=enter.id,
                    label=enter.label,
                    magnitude=enter.magnitude,
                    expires_at=self.race_time + enter.duration,
                    category=enter.category,
                    vfx=enter.vfx,
                    hostile=enter.hostile,
                ),
            )
        self._emit(
            EngineEventKind.PICKUP,
            {
                "kind": zone.kind,
                "label": enter.label,
                "horse_id": horse.id,
                "horse": horse.name,
                "owner": zone.owner_name,
                "pos": round(horse.pos, 1),
            },
        )

    def _update_standings(self) -> None:
        for rank, horse in enumerate(self.standings(), start=1):
            horse.rank = rank
        leader = self.leader()
        if leader is not None and leader.id != self._lead_horse_id:
            previous = self._lead_horse_id
            self._lead_horse_id = leader.id
            if previous is not None and self.phase is RacePhase.RUNNING:
                self._emit(
                    EngineEventKind.LEAD_CHANGE,
                    {"horse_id": leader.id, "horse": leader.name},
                )

    def _maybe_eliminate(self) -> None:
        if not self.mode.elimination or self.phase is not RacePhase.RUNNING:
            return
        survivors = [horse for horse in self.horses if horse.racing]
        if len(survivors) <= 1 or self.race_time < self._next_elimination_at:
            return
        self._next_elimination_at += self.mode.elimination_interval
        victim = min(survivors, key=lambda h: h.total_distance(self.config.track_length))
        victim.eliminated_at = self.race_time
        freed = [
            player.name for player in self.players.values() if player.backs(victim.id)
        ]
        for player in self.players.values():
            if player.backs(victim.id):
                player.drop_horse(victim.id)
        self.recount_backers()
        self._emit(
            EngineEventKind.ELIMINATED,
            {
                "horse_id": victim.id,
                "horse": victim.name,
                "remaining": len(survivors) - 1,
                "free_agents": freed,
            },
        )

    def _evaluate_photo_finish(self, winner: Horse) -> None:
        """Was second place within a nose? (§15.8)"""

        window = C.PHOTO_FINISH_WINDOW_S * self.mode.photo_finish_multiplier
        best_gap = math.inf
        for horse in self.horses:
            if horse.id == winner.id or not horse.racing:
                continue
            remaining = self.config.track_length - horse.pos
            if horse.speed <= 0:
                continue
            projected = self.race_time + remaining / horse.speed
            best_gap = min(best_gap, projected - (winner.finished_at or self.race_time))
        self.photo_finish = best_gap <= window

    def _check_race_end(self) -> None:
        if self.phase is not RacePhase.RUNNING:
            return
        if self.mode.elimination:
            survivors = [horse for horse in self.horses if horse.racing]
            if len(survivors) <= 1:
                if survivors:
                    survivor = survivors[0]
                    survivor.finished_at = self.race_time
                    survivor.finish_rank = 1
                    self.finish_order.insert(0, survivor)
                self._finish_race()
            return

        everyone_home = all(horse.finished_at is not None for horse in self.horses)
        wrapped_up = (
            self.first_finish_at is not None
            and self.race_time >= self.first_finish_at + C.RACE_WRAPUP_SECONDS
        )
        timed_out = self.race_time > self.config.duration * C.RACE_HARD_TIMEOUT_MULTIPLIER
        if everyone_home or wrapped_up or timed_out:
            self._finish_race()

    def _finish_race(self) -> None:
        """Rank any stragglers by distance and close the race.

        Stragglers get a *projected* finishing time from their current pace so
        the results board reads like a real race card instead of six horses
        mysteriously tying.
        """

        remaining = [horse for horse in self.horses if horse.finished_at is None]
        # Still-running horses rank by distance; eliminated ones by how long they
        # survived (the last option discontinued finished second, not last).
        remaining.sort(
            key=lambda h: (
                h.eliminated_at is None,
                h.eliminated_at
                if h.eliminated_at is not None
                else h.total_distance(self.config.track_length),
            ),
            reverse=True,
        )
        for horse in remaining:
            gap = max(0.0, self.config.track_length - horse.pos)
            horse.projected = True
            horse.finished_at = self.race_time + (gap / horse.speed if horse.speed > 0.1 else 0.0)
            horse.finish_rank = len(self.finish_order) + 1
            self.finish_order.append(horse)
        self.phase = RacePhase.FINISHED
        winner = self.finish_order[0] if self.finish_order else None
        self._emit(
            EngineEventKind.RACE_FINISHED,
            {
                "winner_id": winner.id if winner else None,
                "winner": winner.name if winner else None,
                "photo_finish": self.photo_finish,
                "order": [horse.id for horse in self.finish_order],
            },
        )

    # ------------------------------------------------------------------
    # Powerup drops
    # ------------------------------------------------------------------

    def _maybe_grant_powerups(self) -> None:
        """Run the per-horse drop clocks (§15.4).

        The *horse* earns items and its backers share the stream. Scheduling per
        player instead would multiply a popular option's item power by its
        backer count, which pushes every other dinner option below the §2.3
        floor — the whole point is that stacking a bandwagon must not decide
        dinner. A crowded horse still gets somewhat more (see
        ``BACKER_DROP_BONUS``), just not linearly more.
        """

        if not self.config.powerups_on:
            return
        for horse in self.horses:
            if self.race_time < horse.next_drop_at:
                continue
            if not horse.racing or horse.backers == 0:
                horse.next_drop_at = self.race_time + 2.0
                continue
            self._schedule_next_drop(horse)
            recipient = self._pick_drop_recipient(horse)
            if recipient is not None:
                self._grant_powerup(recipient)

    def _pick_drop_recipient(self, horse: Horse) -> RacePlayer | None:
        """Choose which backer receives this horse's item (needs a free slot)."""

        eligible = [
            player
            for player in self.players.values()
            if player.backs(horse.id) and player.free_slot() is not None
        ]
        if not eligible:
            return None
        # Fewest grants first keeps the stream fair inside a group of backers.
        fewest = min(player.grants for player in eligible)
        contenders = [player for player in eligible if player.grants == fewest]
        return self.rng.choice(contenders)

    def _schedule_next_drop(self, horse: Horse) -> None:
        interval = self.rng.uniform(C.DROP_INTERVAL_MIN_S, C.DROP_INTERVAL_MAX_S)
        interval *= self.mode.drop_interval_multiplier
        interval /= 1.0 + C.BACKER_DROP_BONUS * max(0, horse.backers - 1)
        if self.race_progress() >= C.DROP_FINAL_RAMP_FROM:
            interval *= C.DROP_FINAL_RAMP_MULTIPLIER
        if horse.rank == 1:
            interval *= C.DROP_LEADER_TAX_MULTIPLIER
        elif self.is_last_place(horse):
            interval *= C.DROP_LAST_PLACE_MULTIPLIER
        horse.next_drop_at = self.race_time + max(self.mode.drop_interval_floor, interval)

    def _grant_powerup(self, player: RacePlayer) -> None:
        slot = player.free_slot()
        horse = self.horse_for(player)
        if slot is None or horse is None:
            return
        tier = roll_tier(
            self.rng,
            self.mode.rarity_weights(),
            common_streak=player.common_streak,
            last_place=self.is_last_place(horse),
        )
        powerup = self._roll_valid_powerup(tier, horse, player)
        held = player.inventory[slot]
        held.clear()
        held.powerup_id = powerup.id
        held.challenge = generate_challenge(self.rng)
        player.grants += 1
        player.last_granted_id = powerup.id
        player.common_streak = 0 if powerup.tier is not Tier.COMMON else player.common_streak + 1
        player.challenge_stats.issued(slot, self.race_time)
        self._emit(
            EngineEventKind.POWERUP_GRANT,
            {
                "slot": slot,
                "powerup_id": powerup.id,
                "tier": powerup.tier.value,
                "challenge": held.challenge.client_meta(),
            },
            to_player=player.id,
        )
        self._emit_inventory(player)

    def _roll_valid_powerup(self, tier: Tier, horse: Horse, player: RacePlayer) -> PowerupDef:
        is_leader = horse.rank == 1
        for _ in range(8):
            powerup = roll_powerup(
                self.rng, tier=tier, theme_bonus=self.track.powerup_theme_bonus
            )
            if not is_dead_draw(
                powerup,
                horse,
                now=self.race_time,
                is_leader=is_leader,
                last_granted_id=player.last_granted_id,
            ):
                return powerup
        return POWERUPS["turbo_boost"]

    def _emit_inventory(self, player: RacePlayer) -> None:
        self._emit(
            EngineEventKind.INVENTORY,
            {"inventory": player.inventory_view(self.race_time)},
            to_player=player.id,
        )

    # ------------------------------------------------------------------
    # Random events
    # ------------------------------------------------------------------

    def _schedule_first_event(self) -> None:
        if not self.config.events_on:
            return
        self._queue_event(C.EVENT_FIRST_AT_S * self._event_time_scale())

    def _event_time_scale(self) -> float:
        """Compress the event schedule for short races so pacing still lands."""

        if not self.config.duration:
            return 1.0
        return min(1.0, self.config.duration / C.DEFAULT_RACE_SECONDS)

    def _queue_event(self, fire_at: float) -> None:
        definition = pick_event(
            self.rng, exclude=self._used_event_ids, tag_bonus=self.track.event_tag_bonus
        )
        if definition is None:
            self._used_event_ids.clear()
            definition = pick_event(self.rng, exclude=set(), tag_bonus=self.track.event_tag_bonus)
        if definition is None:
            return
        self._used_event_ids.add(definition.id)
        self._pending_events.append(_PendingEvent(definition=definition, fire_at=fire_at))

    def _maybe_run_events(self) -> None:
        if not self.config.events_on:
            return
        for pending in list(self._pending_events):
            lead = pending.fire_at - self.race_time
            if not pending.leaked and lead <= C.INSIDER_LEAK_LEAD_SECONDS:
                pending.leaked = True
                self._leak_event(pending)
            if not pending.telegraphed and lead <= C.EVENT_TELEGRAPH_SECONDS:
                pending.telegraphed = True
                self._emit(
                    EngineEventKind.EVENT_TELEGRAPH,
                    {
                        "event_id": pending.definition.id,
                        "headline": pending.definition.telegraph,
                        "emoji": pending.definition.emoji,
                    },
                )
            if self.race_time >= pending.fire_at:
                self._pending_events.remove(pending)
                self._fire_event(pending.definition)

    def _leak_event(self, pending: _PendingEvent) -> None:
        if not self._intel_players:
            return
        eta = max(0.0, pending.fire_at - self.race_time)
        for player_id in self._intel_players:
            self._emit(
                EngineEventKind.INTEL,
                {
                    "text": f"{pending.definition.name.upper()} in {eta:.1f}s",
                    "emoji": pending.definition.emoji,
                },
                to_player=player_id,
            )

    def _fire_event(self, definition: EventDef) -> None:
        too_late = self.race_progress() >= C.EVENT_LAST_CALL_FRACTION
        if not too_late and self.phase is RacePhase.RUNNING:
            payload = definition.apply(self)
            self._emit(
                EngineEventKind.EVENT_FIRED,
                {
                    "event_id": definition.id,
                    "name": definition.name,
                    "emoji": definition.emoji,
                    "shake": definition.shake,
                    **payload,
                },
            )
        low, high = self.mode.event_interval
        scale = self._event_time_scale()
        next_at = self.race_time + self.rng.uniform(low, high) * scale
        if next_at < self.config.duration * 1.1:
            self._queue_event(next_at)

    # ------------------------------------------------------------------
    # Engine surface used by powerups / events / track twists
    # ------------------------------------------------------------------

    def add_effect(self, horse: Horse, effect: Effect) -> None:
        """Add or refresh an effect (never double-stacks the same id)."""

        upsert(horse.effects, effect)
        horse.effects = prune(horse.effects, self.race_time)

    def clear_debuffs(self, horse: Horse) -> None:
        """Diamond Hands wipes what is already sticking to you."""

        horse.effects = [effect for effect in horse.effects if not effect.hostile]
        horse.stumble_until = 0.0
        horse.freeze_until = 0.0

    def apply_hostile(
        self,
        horse: Horse,
        effect: Effect,
        *,
        duration: float,
        stumble_seconds: float = 0.0,
        tumble: bool = False,
        ignore_mercy: bool = False,
    ) -> HostileOutcome:
        """Route a hostile effect through every protection rule (§9.0)."""

        if not horse.racing:
            return HostileOutcome.IMMUNE
        if horse.is_ghost(self.race_time):
            return HostileOutcome.GHOSTED
        if horse.is_diamond(self.race_time):
            return HostileOutcome.IMMUNE
        if horse.is_golden(self.race_time) and effect.tier == Tier.COMMON.value:
            return HostileOutcome.IMMUNE
        if horse.shield_charges > 0:
            horse.shield_charges -= 1
            return HostileOutcome.SHIELDED

        scale = 1.0
        if not ignore_mercy and self.is_last_place(horse):
            scale = C.MERCY_RULE_DURATION_MULTIPLIER
        effect.expires_at = self.race_time + duration * scale
        self.add_effect(horse, effect)
        if stumble_seconds:
            self.stumble_horse(horse, stumble_seconds * scale, tumble=tumble)
        return HostileOutcome.APPLIED

    def apply_freeze(
        self,
        horse: Horse,
        *,
        seconds: float,
        tag: str,
        source_player_id: str | None = None,
        source_player_name: str | None = None,
    ) -> HostileOutcome:
        """Hard stop with the final-stretch lockout and re-freeze immunity."""

        if not horse.racing:
            return HostileOutcome.IMMUNE
        if horse.pos / self.config.track_length >= C.FINAL_STRETCH_FRACTION:
            self.apply_hostile(
                horse,
                Effect(
                    id="halted_soft",
                    label="Trading Halted",
                    magnitude=C.FREEZE_SOFTENED_MAGNITUDE,
                    expires_at=self.race_time + C.FREEZE_SOFTENED_DURATION_S,
                    vfx="halt",
                    hostile=True,
                    source_player_id=source_player_id,
                    source_player_name=source_player_name,
                ),
                duration=C.FREEZE_SOFTENED_DURATION_S,
            )
            return HostileOutcome.SOFTENED
        if self.race_time < horse.freeze_immune_until:
            return HostileOutcome.IMMUNE
        if horse.is_ghost(self.race_time):
            return HostileOutcome.GHOSTED
        if horse.is_diamond(self.race_time):
            return HostileOutcome.IMMUNE
        if horse.shield_charges > 0:
            horse.shield_charges -= 1
            return HostileOutcome.SHIELDED
        self.freeze_horse(horse, seconds, tag=tag)
        horse.freeze_immune_until = self.race_time + C.FREEZE_REAPPLY_IMMUNITY_S
        return HostileOutcome.APPLIED

    def freeze_horse(self, horse: Horse, seconds: float, *, tag: str = "freeze") -> None:
        """Unconditional hard stop (world events and track twists use this)."""

        horse.freeze_until = max(horse.freeze_until, self.race_time + seconds)
        self.add_effect(
            horse,
            Effect(
                id=f"freeze_{tag}",
                label=tag.replace("_", " ").title(),
                magnitude=0.0,
                expires_at=horse.freeze_until,
                vfx="freeze",
                hostile=True,
            ),
        )

    def stumble_horse(self, horse: Horse, seconds: float, *, tumble: bool = False) -> None:
        seconds *= self.track.stumble_duration_multiplier
        horse.stumble_until = max(horse.stumble_until, self.race_time + seconds)
        horse.stumble_is_tumble = tumble or horse.stumble_is_tumble

    def swap_positions(self, first: Horse, second: Horse) -> None:
        first.pos, second.pos = second.pos, first.pos
        first.laps, second.laps = second.laps, first.laps

    def trap_placement(self, caster: Horse, *, behind_offset: float, radius: float) -> float:
        """Where a thrown trap should land.

        Traps are thrown **forward**, landing just in front of the nearest horse
        ahead so they run onto it. A trap that deploys behind the caster can only
        ever punish the horses already losing, which makes it a leader's tool; a
        trap thrown forward is how somebody in the pack takes a place off the
        horses in front of them.

        A leader has nobody ahead, so theirs drops behind as a rear guard.
        Either way the caster is immune to their own trap (see
        :meth:`_zone_hits`), so the throw never backfires.
        """

        ahead = self.horse_ahead_of(caster)
        if ahead is not None:
            return min(self.config.track_length - 4.0, ahead.pos + C.TRAP_FORWARD_LEAD)
        # Out in front: drop it behind as a rear guard for the chasing pack.
        return max(4.0, caster.pos + behind_offset - radius)

    def add_zone(self, zone: Zone) -> None:
        self.zones.append(zone)

    def add_trap_zone(self, zone: Zone) -> None:
        """Add a player-placed trap, retiring the oldest beyond the cap."""

        traps = [existing for existing in self.zones if existing.trap]
        while len(traps) >= C.MAX_LIVE_TRAPS:
            oldest = traps.pop(0)
            oldest.consumed = True
        self.zones = [existing for existing in self.zones if not existing.consumed]
        self.zones.append(zone)

    def next_zone_id(self) -> int:
        self._zone_seq += 1
        return self._zone_seq

    def schedule(self, delay: float, fn: Callable[[], None]) -> None:
        """Deterministic delayed callback in race time."""

        self._scheduled.append(_ScheduledCall(at=self.race_time + delay, fn=fn))

    def claim_global_cooldown(self, key: str) -> bool:
        if not self.mode.respect_global_cooldowns:
            return True
        if self.race_time < self._global_cooldowns.get(key, -1.0):
            return False
        self._global_cooldowns[key] = self.race_time + C.GLOBAL_POWERUP_COOLDOWNS_S.get(key, 5.0)
        return True

    def set_tap_efficiency(self, multiplier: float, duration: float) -> None:
        self._tap_efficiency = multiplier
        self._tap_efficiency_until = self.race_time + duration

    def grant_intel(self, player_id: str) -> None:
        self._intel_players.add(player_id)

    def emit_track_moment(
        self, *, kind: str, headline: str, emoji: str, params: dict[str, Any]
    ) -> None:
        self._emit(
            EngineEventKind.TRACK_MOMENT,
            {"kind": kind, "headline": headline, "emoji": emoji, **params},
        )

    def _emit(
        self, kind: EngineEventKind, payload: dict[str, Any], to_player: str | None = None
    ) -> None:
        self._out.append(EngineEvent(kind=kind, payload=payload, to_player=to_player))

    # ------------------------------------------------------------------
    # Queries
    # ------------------------------------------------------------------

    def racing_horses(self) -> list[Horse]:
        return [horse for horse in self.horses if horse.racing]

    def standings(self) -> list[Horse]:
        """Horses ordered 1st → last: finishers by rank, then by distance."""

        finished = sorted(
            (h for h in self.horses if h.finished_at is not None),
            key=lambda h: h.finish_rank,
        )
        running = sorted(
            (h for h in self.horses if h.finished_at is None and h.eliminated_at is None),
            key=lambda h: h.total_distance(self.config.track_length),
            reverse=True,
        )
        eliminated = sorted(
            (h for h in self.horses if h.eliminated_at is not None),
            key=lambda h: h.eliminated_at or 0.0,
            reverse=True,
        )
        return finished + running + eliminated

    def leader(self) -> Horse | None:
        running = [horse for horse in self.horses if horse.racing]
        if not running:
            return None
        return max(running, key=lambda h: h.total_distance(self.config.track_length))

    def last_place(self) -> Horse | None:
        running = [horse for horse in self.horses if horse.racing]
        if not running:
            return None
        return min(running, key=lambda h: h.total_distance(self.config.track_length))

    def is_last_place(self, horse: Horse) -> bool:
        last = self.last_place()
        return last is not None and last.id == horse.id and len(self.racing_horses()) > 1

    def horse_ahead_of(self, horse: Horse) -> Horse | None:
        """Nearest horse in front, by distance travelled."""

        mine = horse.total_distance(self.config.track_length)
        ahead = [
            other
            for other in self.horses
            if other.racing
            and other.id != horse.id
            and other.total_distance(self.config.track_length) > mine
        ]
        if not ahead:
            return None
        return min(ahead, key=lambda h: h.total_distance(self.config.track_length))

    def race_progress(self) -> float:
        """Leader's fraction of the track (elimination mode uses time instead)."""

        if self.mode.elimination:
            return min(1.0, max(0.0, self.race_time / max(1.0, self.config.duration)))
        leader = self.leader()
        if leader is None:
            return 1.0
        return min(1.0, leader.pos / self.config.track_length)

    # ------------------------------------------------------------------
    # Serialisation
    # ------------------------------------------------------------------

    def snapshot(self) -> dict[str, Any]:
        """Full authoritative visual state (§18.3). Short keys: sent 20×/second."""

        now = self.race_time
        return {
            "t": "snapshot",
            "k": self.tick,
            "rt": round(now, 3),
            "ph": self.phase.value,
            "h": [
                {
                    "i": horse.id,
                    "p": round(horse.pos, 2),
                    "l": horse.laps,
                    "v": round(horse.speed_multiplier, 3),
                    "r": horse.rank,
                    "b": horse.backers,
                    "st": horse.visual_state(now).value,
                    "fx": horse.effect_tags(now),
                }
                for horse in self.horses
            ],
            "o": [horse.id for horse in self.standings()],
            "z": [
                {
                    "i": zone.id,
                    "k": zone.kind,
                    "s": round(zone.start, 1),
                    "e": round(zone.end, 1),
                }
                for zone in self.zones
                if zone.alive(now)
            ],
        }

    def player_hud(self, player_id: str) -> dict[str, Any] | None:
        """Per-player phone HUD payload (§7.2 ``you``)."""

        player = self.players.get(player_id)
        if player is None:
            return None
        horse = self.horse_for(player)
        return {
            "t": "you",
            "taps": player.taps_total,
            "tps": round(player.taps_per_second(self.race_time), 1),
            "horse_ids": list(player.backed_horse_ids),
            "rates": {
                str(horse_id): round(rate, 1)
                for horse_id, rate in player.tap_rates(self.race_time).items()
            },
            "maxed": player.taps_per_second(self.race_time) >= C.TAP_TPS_CAP,
            "horse_id": horse.id if horse else None,
            "rank": horse.rank if horse else None,
            "field": len([h for h in self.horses if h.eliminated_at is None]),
            "inventory": player.inventory_view(self.race_time),
            "powerups_used": player.powerups_used,
            "hits": player.hits_landed,
        }

    def results(self) -> dict[str, Any]:
        """Final standings and per-player stats for the ceremony screen."""

        order = self.finish_order or self.standings()
        return {
            "winner_id": order[0].id if order else None,
            "winner": order[0].name if order else None,
            "winner_emoji": order[0].emoji if order else None,
            "photo_finish": self.photo_finish,
            "order": [
                {
                    "horse_id": horse.id,
                    "name": horse.name,
                    "emoji": horse.emoji,
                    "rank": horse.finish_rank or horse.rank,
                    "time": round(horse.finished_at, 2) if horse.finished_at else None,
                    "projected": horse.projected,
                    "eliminated": horse.eliminated_at is not None,
                    "backers": horse.backers,
                }
                for horse in order
            ],
            "players": [
                {
                    "player_id": player.id,
                    "name": player.name,
                    "horse_id": player.horse_id,
                    "taps": player.taps_total,
                    "peak_tps": round(player.peak_tps, 1),
                    "powerups_used": player.powerups_used,
                    "hits": player.hits_landed,
                    "unlocks": player.challenge_stats.solved,
                    "fumbles": player.challenge_stats.failed,
                    "fastest_unlock": (
                        round(player.challenge_stats.fastest_seconds, 1)
                        if player.challenge_stats.fastest_seconds is not None
                        else None
                    ),
                }
                for player in self.players.values()
                if player.taps_total or player.powerups_used or player.challenge_stats.solved
            ],
        }
