"""Simulation invariants: determinism, the velocity model, finish resolution."""

from __future__ import annotations

import pytest
from conftest import DEFAULT_OPTIONS, Tapper, simulate

from server import constants as C
from server.challenges import Challenge
from server.engine import RaceEngine
from server.roster import build_horses
from server.state import EngineEventKind, RaceConfig, RacePhase, RacePlayer


def make_engine(**overrides) -> RaceEngine:
    """A six-horse engine with optional config overrides and one player."""

    config_kwargs = {
        "seed": 1,
        "powerups_on": False,
        "events_on": False,
        "duration": C.DEFAULT_RACE_SECONDS,
    }
    players = overrides.pop("players", [RacePlayer(id="p1", name="Cullen", horse_id=0)])
    options = overrides.pop("options", DEFAULT_OPTIONS)
    config_kwargs.update(overrides)
    return RaceEngine(RaceConfig(**config_kwargs), build_horses(options), players)


def run_to_finish(engine: RaceEngine, *, taps_for: str | None = None, tps: float = 0.0) -> int:
    accumulator = 0.0
    ticks = 0
    while engine.phase is not RacePhase.FINISHED and ticks < 8000:
        if taps_for and engine.phase is RacePhase.RUNNING:
            accumulator += tps * C.TICK_DT
            whole = int(accumulator)
            if whole:
                accumulator -= whole
                engine.apply_taps(taps_for, whole)
        engine.step()
        ticks += 1
    return ticks


# ---------------------------------------------------------------------------
# Determinism (§15.10.5)
# ---------------------------------------------------------------------------


def test_same_seed_same_race():
    first = simulate(seed=42, tappers=[Tapper(0, 7.0)], powerups=True, events=True)
    second = simulate(seed=42, tappers=[Tapper(0, 7.0)], powerups=True, events=True)
    assert first.winner_id == second.winner_id
    assert first.order == second.order
    assert first.duration == pytest.approx(second.duration)


def test_different_seeds_diverge():
    outcomes = {
        simulate(seed=seed, tappers=[], powerups=True, events=True).winner_id
        for seed in range(20)
    }
    assert len(outcomes) > 1


# ---------------------------------------------------------------------------
# Phases and finishing
# ---------------------------------------------------------------------------


def test_countdown_precedes_running():
    engine = make_engine()
    assert engine.phase is RacePhase.COUNTDOWN
    assert engine.race_time == pytest.approx(-C.COUNTDOWN_SECONDS)
    for _ in range(int(C.COUNTDOWN_SECONDS * C.TICK_RATE) - 1):
        engine.step()
    assert engine.phase is RacePhase.COUNTDOWN
    assert all(horse.pos == 0.0 for horse in engine.horses)
    engine.step()
    assert engine.phase is RacePhase.RUNNING


def test_every_horse_gets_a_finish_position():
    engine = make_engine()
    run_to_finish(engine)
    assert engine.phase is RacePhase.FINISHED
    ranks = sorted(horse.finish_rank for horse in engine.horses)
    assert ranks == list(range(1, len(engine.horses) + 1))


def test_reported_winner_is_the_simulated_winner():
    """§15.10.4 — presentation may never override the simulation."""

    engine = make_engine()
    run_to_finish(engine, taps_for="p1", tps=10.0)
    results = engine.results()
    fastest = min(
        (horse for horse in engine.horses if horse.finished_at is not None),
        key=lambda horse: horse.finished_at,
    )
    assert results["winner_id"] == fastest.id
    assert results["order"][0]["horse_id"] == fastest.id


def test_finish_time_is_interpolated_not_snapped_to_ticks():
    engine = make_engine()
    run_to_finish(engine, taps_for="p1", tps=6.0)
    winner = engine.finish_order[0]
    assert winner.finished_at is not None
    off_grid = abs((winner.finished_at / C.TICK_DT) - round(winner.finished_at / C.TICK_DT))
    assert off_grid > 1e-9, "crossing time looks quantised to the tick grid"


def test_baseline_horse_finishes_in_a_sane_time():
    """An ignored horse must remain a real contender (§15.7)."""

    engine = make_engine(players=[])
    run_to_finish(engine)
    times = [horse.finished_at for horse in engine.horses if horse.finished_at]
    assert min(times) > C.DEFAULT_RACE_SECONDS * 0.7
    assert max(times) < C.DEFAULT_RACE_SECONDS * 1.6


# ---------------------------------------------------------------------------
# Velocity model
# ---------------------------------------------------------------------------


def test_tapping_increases_speed_multiplier():
    engine = make_engine()
    for _ in range(int(C.COUNTDOWN_SECONDS * C.TICK_RATE) + 2):
        engine.step()
    for _ in range(20):
        engine.apply_taps("p1", 1)
        engine.step()
    tapped = engine.horses_by_id[0]
    assert tapped.tap_bonus > 0.05
    assert engine.horses_by_id[1].tap_bonus == 0.0


def test_tap_bonus_saturates_below_ceiling():
    engine = make_engine(
        players=[RacePlayer(id=f"p{i}", name=f"P{i}", horse_id=0) for i in range(8)]
    )
    for _ in range(int(C.COUNTDOWN_SECONDS * C.TICK_RATE) + 2):
        engine.step()
    for _ in range(40):
        for index in range(8):
            engine.apply_taps(f"p{index}", 3)
        engine.step()
    assert engine.horses_by_id[0].tap_bonus <= C.TAP_BONUS_MAX + 1e-9


def test_rubber_band_ignores_small_gaps_and_drags_breakaways():
    engine = make_engine()
    horse = engine.horses_by_id[0]
    assert engine._rubber_band(horse, horse.pos) == 0.0
    assert engine._rubber_band(horse, horse.pos + C.RUBBER_BAND_DEADZONE * 0.5) == 0.0
    drag = engine._rubber_band(horse, horse.pos - 200)
    boost = engine._rubber_band(horse, horse.pos + 200)
    assert drag == pytest.approx(C.RUBBER_BAND_MIN)
    assert boost == pytest.approx(C.RUBBER_BAND_MAX)


def test_speed_never_goes_negative_or_infinite():
    engine = make_engine(powerups_on=True, events_on=True, seed=9)
    ticks = 0
    while engine.phase is not RacePhase.FINISHED and ticks < 8000:
        engine.step()
        ticks += 1
        for horse in engine.horses:
            assert horse.speed >= 0.0
            assert horse.pos == horse.pos  # not NaN
            assert horse.pos < C.TRACK_LENGTH * 4


# ---------------------------------------------------------------------------
# Snapshots
# ---------------------------------------------------------------------------


def test_snapshot_describes_every_horse_and_orders_them():
    engine = make_engine()
    run_to_finish(engine)
    snapshot = engine.snapshot()
    assert {"t", "k", "rt", "ph", "h", "o", "z"} <= set(snapshot)
    assert len(snapshot["h"]) == len(engine.horses)
    assert sorted(snapshot["o"]) == sorted(horse.id for horse in engine.horses)


def test_player_hud_tracks_the_backed_horse():
    engine = make_engine()
    for _ in range(int(C.COUNTDOWN_SECONDS * C.TICK_RATE) + 4):
        engine.step()
    engine.apply_taps("p1", 5)
    engine.step()
    hud = engine.player_hud("p1")
    assert hud is not None
    assert hud["horse_id"] == 0, "horse id 0 must survive falsy checks"
    assert hud["taps"] == 5
    assert hud["rank"] in range(1, len(engine.horses) + 1)


# ---------------------------------------------------------------------------
# Modes
# ---------------------------------------------------------------------------


def test_elimination_removes_one_horse_at_a_time():
    engine = make_engine(mode_id="last_bite", duration=60.0)
    seen_remaining: list[int] = []
    ticks = 0
    while engine.phase is not RacePhase.FINISHED and ticks < 8000:
        for event in engine.step():
            if event.kind.value == "eliminated":
                seen_remaining.append(event.payload["remaining"])
        ticks += 1
    assert seen_remaining == sorted(seen_remaining, reverse=True)
    assert seen_remaining[-1] == 1
    assert sum(1 for horse in engine.horses if horse.eliminated_at is not None) == 5


def test_elimination_frees_backers_of_a_dead_horse():
    players = [RacePlayer(id="p1", name="A", horse_id=0), RacePlayer(id="p2", name="B", horse_id=1)]
    engine = make_engine(mode_id="last_bite", players=players)
    while engine.phase is not RacePhase.FINISHED:
        engine.step()
    survivors = [horse for horse in engine.horses if horse.eliminated_at is None]
    assert len(survivors) == 1
    freed = [player for player in players if player.horse_id is None]
    assert freed, "eliminated horses must release their backers"


# ---------------------------------------------------------------------------
# Backing several horses (one tap button each)
# ---------------------------------------------------------------------------


def arm(player: RacePlayer, slot: int, powerup_id: str) -> None:
    """Put an unlocked item in a slot; the unlock gate is tested separately."""

    held = player.inventory[slot]
    held.clear()
    held.powerup_id = powerup_id
    held.armed = True


def running_engine(**overrides) -> RaceEngine:
    engine = make_engine(**overrides)
    while engine.phase is not RacePhase.RUNNING:
        engine.step()
    return engine


def test_taps_are_credited_to_the_button_that_sent_them():
    player = RacePlayer(id="p1", name="Spread")
    player.set_backing([0, 2])
    engine = running_engine(players=[player])
    engine.apply_taps("p1", 4, 0)
    engine.apply_taps("p1", 6, 2)
    rates = player.tap_rates(engine.race_time)
    assert rates[0] < rates[2], "each button feeds its own horse"
    assert player.taps_total == 10


def test_four_buttons_are_not_four_times_the_tap_power():
    """The cap is on the person, not the button (§15.3)."""

    solo = RacePlayer(id="solo", name="Solo")
    solo.set_backing([0])
    spread = RacePlayer(id="spread", name="Spread")
    spread.set_backing([1, 2, 3, 4])
    engine = running_engine(players=[solo, spread])

    for _ in range(20):
        engine.apply_taps("solo", 3, 0)
        for horse_id in (1, 2, 3, 4):
            engine.apply_taps("spread", 3, horse_id)
        engine.step()

    solo_total = sum(solo.tap_allocation(engine.race_time).values())
    spread_total = sum(spread.tap_allocation(engine.race_time).values())
    assert spread_total == pytest.approx(solo_total, rel=0.02)
    assert spread_total <= C.TAP_TPS_CAP * 1.001


def test_spreading_taps_splits_the_bonus_between_horses():
    player = RacePlayer(id="p1", name="Spread")
    player.set_backing([0, 1])
    engine = running_engine(players=[player])
    for _ in range(20):
        engine.apply_taps("p1", 4, 0)
        engine.apply_taps("p1", 1, 1)
        engine.step()
    bonuses = engine._tap_bonuses()
    assert bonuses[0] > bonuses[1] > 0, "the horse you favour gets more"
    assert bonuses[0] < C.TAP_BONUS_MAX


def test_taps_for_an_unbacked_or_dead_horse_are_dropped():
    player = RacePlayer(id="p1", name="Loyal")
    player.set_backing([0])
    engine = running_engine(players=[player])
    engine.apply_taps("p1", 5, 3)
    assert player.taps_total == 0

    engine.horses_by_id[0].eliminated_at = engine.race_time
    engine.apply_taps("p1", 5, 0)
    assert player.taps_total == 0


def test_a_self_powerup_can_choose_which_of_your_horses_it_buffs():
    player = RacePlayer(id="p1", name="Spread")
    player.set_backing([0, 3])
    engine = running_engine(players=[player], powerups_on=True)
    arm(player, 0, "turbo_boost")
    assert engine.use_powerup("p1", 0, 3)[0]
    assert any(effect.id == "turbo_boost" for effect in engine.horses_by_id[3].effects)
    assert not engine.horses_by_id[0].effects


def test_a_self_powerup_defaults_to_the_primary_horse():
    player = RacePlayer(id="p1", name="Spread")
    player.set_backing([2, 4])
    engine = running_engine(players=[player], powerups_on=True)
    arm(player, 0, "turbo_boost")
    assert engine.use_powerup("p1", 0, None)[0]
    assert any(effect.id == "turbo_boost" for effect in engine.horses_by_id[2].effects)


def test_elimination_only_frees_the_dead_horse():
    player = RacePlayer(id="p1", name="Spread")
    player.set_backing([0, 1, 2])
    engine = make_engine(mode_id="last_bite", players=[player])
    while engine.phase is not RacePhase.FINISHED:
        engine.step()
        if len(player.backed_horse_ids) < 3:
            break
    assert len(player.backed_horse_ids) == 2, "only the eliminated option is released"
    assert player.horse_id in player.backed_horse_ids


# ---------------------------------------------------------------------------
# Party Parrot Paradise
# ---------------------------------------------------------------------------


def test_the_beat_drop_lifts_the_whole_field_equally():
    """A track twist must change the rhythm, never the odds."""

    engine = make_engine(track_id="parrot")
    seen = []
    while engine.phase is not RacePhase.FINISHED:
        for event in engine.step():
            if event.payload.get("kind") == "beat_drop":
                boosted = [
                    horse.id
                    for horse in engine.horses
                    if any(effect.id == "beat_drop" for effect in horse.effects)
                ]
                seen.append(boosted)
    assert seen, "the beat should drop at least once in a 60s race"
    for boosted in seen:
        assert len(boosted) == len(engine.horses), "every lane rides the same beat"


def test_the_parrot_track_is_still_a_fair_lottery():
    from conftest import win_rates

    outcomes = [simulate(seed=seed, tappers=[], track="parrot") for seed in range(120)]
    rates = win_rates(outcomes, 6)
    for horse_id, rate in rates.items():
        assert abs(rate - 1 / 6) <= 0.08, f"horse {horse_id} at {rate:.1%}"


# ---------------------------------------------------------------------------
# Event delivery for intents that arrive between ticks
# ---------------------------------------------------------------------------


def test_events_from_a_cast_between_ticks_are_not_lost():
    """The room dispatches a tick's events, then sleeps — and casts arrive then.

    `step()` used to return its queue by reference, so anything emitted after the
    room finished dispatching landed in an already-walked list and disappeared on
    the next tick. That silently swallowed powerup notifications.
    """

    player = RacePlayer(id="p1", name="Cullen", horse_id=0)
    engine = running_engine(players=[player], powerups_on=True)
    arm(player, 0, "turbo_boost")

    engine.step()  # the room dispatches this and then awaits its sleep
    assert engine.use_powerup("p1", 0, None)[0]

    flushed = [event.kind for event in engine.drain_events()]
    assert EngineEventKind.POWERUP_CAST in flushed, "the TV must hear about every cast"
    assert EngineEventKind.INVENTORY in flushed, "the phone must see the slot empty"

    # And draining must not double-deliver on the following tick.
    assert EngineEventKind.POWERUP_CAST not in [e.kind for e in engine.step()]


def test_events_from_an_unlock_between_ticks_are_not_lost():
    player = RacePlayer(id="p1", name="Cullen", horse_id=0)
    engine = running_engine(players=[player], powerups_on=True)
    held = player.inventory[0]
    held.clear()
    held.powerup_id = "turbo_boost"
    held.challenge = Challenge(
        kind="math", prompt="1 + 1", choices=["2", "3"], answer_index=0
    )

    engine.step()
    assert engine.answer_challenge("p1", 0, 0) == (True, None)
    flushed = [event.kind for event in engine.drain_events()]
    assert EngineEventKind.CHALLENGE_SOLVED in flushed
    assert EngineEventKind.INVENTORY in flushed


def test_a_drain_is_empty_when_nothing_happened():
    engine = running_engine()
    engine.step()
    assert engine.drain_events() == []
