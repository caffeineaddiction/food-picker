"""The unlock gate: challenge generation, arming, cooldowns and the pace task.

The gate is what makes a powerup cost something, so it has to be both fair (any
tapping style can satisfy it) and unskippable (the answer never leaves the
server).
"""

from __future__ import annotations

import random

import pytest
from conftest import DEFAULT_OPTIONS

from server import constants as C
from server.challenges import (
    GENERATORS,
    PACE_TARGETS,
    Challenge,
    ChallengeStats,
    generate,
)
from server.engine import RaceEngine
from server.roster import build_horses
from server.state import RaceConfig, RacePhase, RacePlayer


@pytest.fixture()
def engine() -> RaceEngine:
    player = RacePlayer(id="p1", name="Cullen", horse_id=0)
    engine = RaceEngine(
        RaceConfig(seed=11, powerups_on=True, events_on=False),
        build_horses(DEFAULT_OPTIONS),
        [player],
    )
    while engine.phase is not RacePhase.RUNNING:
        engine.step()
    return engine


def hand_out(engine: RaceEngine, powerup_id: str = "turbo_boost", slot: int = 0) -> None:
    """Put a locked item in a slot with a known multiple-choice challenge."""

    held = engine.players["p1"].inventory[slot]
    held.clear()
    held.powerup_id = powerup_id
    held.challenge = Challenge(
        kind="math", prompt="2 + 2", choices=["3", "4", "5", "6"], answer_index=1
    )


def tap_at(engine: RaceEngine, rate: float, ticks: int, horse_id: int = 0) -> None:
    accumulator = 0.0
    for _ in range(ticks):
        accumulator += rate * C.TICK_DT
        whole = int(accumulator)
        if whole:
            accumulator -= whole
            engine.apply_taps("p1", whole, horse_id)
        engine.step()


# ---------------------------------------------------------------------------
# Generation
# ---------------------------------------------------------------------------


def test_every_generator_produces_an_answerable_challenge():
    rng = random.Random(7)
    for generator, _ in GENERATORS:
        for _ in range(40):
            challenge = generator(rng)
            assert challenge.prompt
            if challenge.is_pace:
                assert challenge.target_rate in PACE_TARGETS
                assert challenge.hold_seconds > 0
                assert not challenge.choices, "pace tasks are answered with a thumb"
            else:
                assert 2 <= len(challenge.choices) <= 4
                assert 0 <= challenge.answer_index < len(challenge.choices)
                assert len(set(challenge.choices)) == len(challenge.choices), "no duplicates"


def test_the_answer_never_reaches_the_client():
    rng = random.Random(3)
    for _ in range(60):
        meta = generate(rng).client_meta()
        assert "answer_index" not in meta
        assert "answerIndex" not in meta


def test_pace_bands_cover_slow_and_fast_styles():
    """If every band sat below a masher's rate, the gate would punish effort."""

    assert min(PACE_TARGETS) <= 4.0
    assert max(PACE_TARGETS) >= 9.0


def test_generation_is_deterministic_for_a_seed():
    first = [generate(random.Random(5)).prompt for _ in range(10)]
    second = [generate(random.Random(5)).prompt for _ in range(10)]
    assert first == second


# ---------------------------------------------------------------------------
# Arming
# ---------------------------------------------------------------------------


def test_a_locked_item_cannot_be_fired(engine):
    hand_out(engine)
    assert engine.use_powerup("p1", 0, None) == (False, "locked")


def test_a_correct_answer_arms_the_item(engine):
    hand_out(engine)
    assert engine.answer_challenge("p1", 0, 1) == (True, None)
    held = engine.players["p1"].inventory[0]
    assert held.armed and held.challenge is None
    assert engine.use_powerup("p1", 0, None)[0]


def test_a_wrong_answer_costs_a_cooldown_and_a_new_question(engine):
    hand_out(engine)
    before = engine.players["p1"].inventory[0].challenge
    assert engine.answer_challenge("p1", 0, 0) == (False, "wrong")
    held = engine.players["p1"].inventory[0]
    assert not held.armed
    assert held.retry_at > engine.race_time
    assert held.challenge is not before, "a fresh question, so guesses can't be eliminated"
    assert engine.answer_challenge("p1", 0, 0) == (False, "cooling_down")


def test_the_cooldown_expires(engine):
    hand_out(engine)
    engine.answer_challenge("p1", 0, 0)
    for _ in range(int(C.CHALLENGE_RETRY_SECONDS * C.TICK_RATE) + 2):
        engine.step()
    held = engine.players["p1"].inventory[0]
    correct = held.challenge.answer_index
    assert engine.answer_challenge("p1", 0, correct) == (True, None)


def test_answering_an_empty_slot_is_rejected(engine):
    assert engine.answer_challenge("p1", 0, 0) == (False, "nothing_to_unlock")
    assert engine.answer_challenge("p1", 9, 0) == (False, "bad_slot")
    assert engine.answer_challenge("nobody", 0, 0) == (False, "bad_slot")


def test_granting_an_item_always_attaches_a_challenge(engine):
    player = engine.players["p1"]
    for _ in range(2000):
        engine.step()
        for held in player.inventory:
            if held.powerup_id and not held.armed:
                assert held.challenge is not None, "an item must never arrive pre-armed"
    assert player.grants > 0, "the race should have handed out something"


# ---------------------------------------------------------------------------
# The pace task
# ---------------------------------------------------------------------------


def pace_slot(engine: RaceEngine, target: float, slot: int = 0):
    held = engine.players["p1"].inventory[slot]
    held.clear()
    held.powerup_id = "turbo_boost"
    held.challenge = Challenge(
        kind="pace",
        prompt=f"HOLD {target:.0f}",
        target_rate=target,
        tolerance=1.6,
        hold_seconds=2.2,
    )
    return held


def test_holding_the_pace_arms_the_item(engine):
    held = pace_slot(engine, 5.0)
    tap_at(engine, 5.0, 70)
    assert held.armed


def test_mashing_does_not_satisfy_a_pace_task(engine):
    held = pace_slot(engine, 4.0)
    tap_at(engine, 12.0, 70)
    assert not held.armed, "the tax here is control, not effort"


def test_not_tapping_at_all_does_not_satisfy_it(engine):
    held = pace_slot(engine, 5.0)
    for _ in range(70):
        engine.step()
    assert not held.armed


def test_a_fast_player_can_still_satisfy_a_fast_band(engine):
    held = pace_slot(engine, 11.0)
    tap_at(engine, 11.0, 70)
    assert held.armed


def test_drifting_out_of_the_band_loses_ground_without_resetting(engine):
    held = pace_slot(engine, 5.0)
    tap_at(engine, 5.0, 40)  # just under the hold time, so it banks without arming
    banked = held.pace_held
    assert 0 < banked < held.challenge.hold_seconds
    # The rate is a 1s rolling average, so the excursion has to last long enough
    # to actually push the average out of the band.
    tap_at(engine, 13.0, 22)
    assert 0 < held.pace_held < banked, "a wobble should cost progress, not all of it"
    assert not held.armed


def test_a_pace_task_ignores_button_answers(engine):
    pace_slot(engine, 5.0)
    assert engine.answer_challenge("p1", 0, 0) == (False, "pace_challenge")


# ---------------------------------------------------------------------------
# Stats
# ---------------------------------------------------------------------------


def test_stats_track_solves_fumbles_and_best_time():
    stats = ChallengeStats()
    stats.issued(0, 10.0)
    stats.record_solved(0, 12.5)
    stats.record_failed()
    stats.issued(1, 20.0)
    stats.record_solved(1, 20.8)
    assert stats.solved == 2
    assert stats.failed == 1
    assert stats.fastest_seconds == pytest.approx(0.8)


def test_results_report_unlock_performance(engine):
    hand_out(engine)
    engine.answer_challenge("p1", 0, 1)
    engine.players["p1"].challenge_stats.record_failed()
    row = next(row for row in engine.results()["players"] if row["name"] == "Cullen")
    assert row["unlocks"] == 1
    assert row["fumbles"] == 1
