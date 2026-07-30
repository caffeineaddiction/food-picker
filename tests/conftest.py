"""Headless race harness shared by the test suite.

The engine has no I/O, so a race is just a loop. These helpers script tap input
and (optionally) random powerup casts so tests can measure outcome
distributions across hundreds of seeded races.
"""

from __future__ import annotations

import random
from dataclasses import dataclass, field

from server import constants as C
from server.engine import RaceEngine
from server.roster import build_horses
from server.state import RaceConfig, RacePhase, RacePlayer

DEFAULT_OPTIONS = [
    "Chipotle",
    "Sushi",
    "Pizza",
    "Taco Bell",
    "Five Guys",
    "Panda Express",
]


@dataclass
class Tapper:
    """A scripted player: taps at a constant rate on one horse.

    ``unlock_delay`` models the seconds a human spends reading a challenge before
    answering it, and ``unlock_skill`` the chance they get it right. Items are
    gated behind those challenges, so a harness that ignores them would measure a
    game with no powerups in it at all.
    """

    horse_id: int
    tps: float
    name: str = "Bot"
    fire_powerups: bool = True
    unlock_delay: float = 1.6
    unlock_skill: float = 0.9


@dataclass
class RaceOutcome:
    winner_id: int
    duration: float
    photo_finish: bool
    order: list[int]
    ticks: int
    results: dict = field(default_factory=dict)


def simulate(
    *,
    seed: int,
    tappers: list[Tapper] | None = None,
    options: list[str] | None = None,
    mode: str = "classic",
    track: str = "churchill",
    duration: float = C.DEFAULT_RACE_SECONDS,
    powerups: bool = False,
    events: bool = False,
    max_ticks: int = 8000,
) -> RaceOutcome:
    """Run one complete race and return its outcome.

    Scripted tappers emit whole taps at their nominal rate and fire powerups as
    soon as they receive them (targeting the leader when a target is required),
    which is a reasonable proxy for an engaged human.
    """

    names = options or DEFAULT_OPTIONS
    specs = build_horses(names)
    scripted = tappers or []
    players = [
        RacePlayer(id=f"p{index}", name=f"{tapper.name}{index}", horse_id=tapper.horse_id)
        for index, tapper in enumerate(scripted)
    ]
    mode_def = __import__("server.modes", fromlist=["get_mode"]).get_mode(mode)
    config = RaceConfig(
        mode_id=mode,
        track_id=track,
        duration=mode_def.duration_for(len(specs), duration),
        track_length=mode_def.track_length,
        powerups_on=powerups,
        events_on=events,
        seed=seed,
    )
    engine = RaceEngine(config, specs, players)
    rng = random.Random(seed ^ 0xC0FFEE)

    tap_accumulator = [0.0 for _ in scripted]
    # When each bot will get around to answering the challenge in each slot.
    answer_at: list[dict[int, float]] = [{} for _ in scripted]
    ticks = 0
    while engine.phase is not RacePhase.FINISHED and ticks < max_ticks:
        if engine.phase is RacePhase.RUNNING:
            for index, tapper in enumerate(scripted):
                tap_accumulator[index] += tapper.tps * C.TICK_DT
                whole = int(tap_accumulator[index])
                if whole:
                    tap_accumulator[index] -= whole
                    engine.apply_taps(players[index].id, whole)
        events_out = engine.step()
        ticks += 1
        if not powerups:
            continue
        for index, tapper in enumerate(scripted):
            if not tapper.fire_powerups:
                continue
            player = players[index]
            _work_the_challenges(engine, player, tapper, answer_at[index], rng)
            for slot, held in enumerate(player.inventory):
                if held.powerup_id is None or not held.armed or rng.random() > 0.25:
                    continue
                leader = engine.leader()
                target = None
                if leader is not None and leader.id != player.horse_id:
                    target = leader.id
                else:
                    rivals = [h.id for h in engine.racing_horses() if h.id != player.horse_id]
                    target = rng.choice(rivals) if rivals else None
                engine.use_powerup(player.id, slot, target)
        del events_out

    results = engine.results()
    return RaceOutcome(
        winner_id=results["winner_id"],
        duration=engine.race_time,
        photo_finish=results["photo_finish"],
        order=[row["horse_id"] for row in results["order"]],
        ticks=ticks,
        results=results,
    )


def _work_the_challenges(engine, player, tapper: Tapper, answer_at: dict[int, float], rng) -> None:
    """Answer any pending unlock challenges the way a competent human would.

    Pace challenges need no action here: the bot's steady tapping either sits in
    the band or it doesn't, exactly as a player's would.
    """

    now = engine.race_time
    for slot, held in enumerate(player.inventory):
        challenge = held.challenge
        if held.powerup_id is None or held.armed or challenge is None or challenge.is_pace:
            answer_at.pop(slot, None)
            continue
        if now < held.retry_at:
            answer_at.pop(slot, None)
            continue
        due = answer_at.get(slot)
        if due is None:
            answer_at[slot] = now + tapper.unlock_delay
            continue
        if now < due:
            continue
        answer_at.pop(slot, None)
        choice = challenge.answer_index
        if rng.random() > tapper.unlock_skill:
            choice = (choice + 1) % max(1, len(challenge.choices))
        engine.answer_challenge(player.id, slot, choice)


def win_rates(outcomes: list[RaceOutcome], horse_count: int) -> dict[int, float]:
    """Fraction of races won by each horse id."""

    counts = {horse_id: 0 for horse_id in range(horse_count)}
    for outcome in outcomes:
        if outcome.winner_id is not None:
            counts[outcome.winner_id] += 1
    total = max(1, len(outcomes))
    return {horse_id: count / total for horse_id, count in counts.items()}
