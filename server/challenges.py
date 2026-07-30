"""Unlock challenges: what a powerup costs you.

A powerup arrives *locked*. To arm it you answer one quick multiple-choice
question, which means taking your thumb off the tap button for a couple of
seconds. That trade — momentum for firepower — is the whole point: it makes
using an item a decision instead of a reflex, and it is why the items in
:mod:`server.powerups` can afford to be strong.

Design constraints, all deliberate:

* **No typing.** Every challenge is three or four big buttons.
* **A few seconds, not a puzzle.** Mental arithmetic a tired office can do.
* **Server-authoritative.** The answer never leaves the server, so the gate
  cannot be skipped by a clever client.
* **Deterministic.** Generated from the engine's seeded RNG, so races still
  replay exactly and the balance suite stays meaningful.
"""

from __future__ import annotations

import random
from dataclasses import dataclass, field
from typing import Any

CHOICE_COUNT = 4

#: Pace challenges: hold a steady rate rather than mashing.
#: The spread covers slow *and* fast styles on purpose — if every band sat below
#: a masher's natural rate, pace unlocks would quietly punish effort.
PACE_TARGETS = (3.0, 4.0, 5.0, 6.0, 7.0, 9.0, 11.0)
PACE_TOLERANCE = 1.6
PACE_HOLD_SECONDS = 2.2


@dataclass
class Challenge:
    """A question with choices, or a tap-pace task to hold.

    ``choices`` is empty for pace challenges — those are answered with your thumb
    instead of a button, and the server judges them from the tap stream it is
    already receiving.
    """

    kind: str
    prompt: str
    choices: list[str] = field(default_factory=list)
    answer_index: int = -1
    hint: str = ""
    target_rate: float = 0.0
    tolerance: float = 0.0
    hold_seconds: float = 0.0

    @property
    def is_pace(self) -> bool:
        return self.kind == "pace"

    def is_correct(self, choice: int) -> bool:
        return not self.is_pace and choice == self.answer_index

    def rate_in_band(self, rate: float) -> bool:
        return abs(rate - self.target_rate) <= self.tolerance

    def client_meta(self) -> dict[str, Any]:
        """Everything the phone needs — and nothing that gives the answer away."""

        return {
            "kind": self.kind,
            "prompt": self.prompt,
            "choices": list(self.choices),
            "hint": self.hint,
            "targetRate": self.target_rate,
            "tolerance": self.tolerance,
            "holdSeconds": self.hold_seconds,
        }


def _shuffled_with_answer(
    rng: random.Random, answer: str, decoys: list[str]
) -> tuple[list[str], int]:
    """Place the answer among decoys, de-duplicated, and report where it landed."""

    options = [answer]
    for decoy in decoys:
        if decoy not in options and len(options) < CHOICE_COUNT:
            options.append(decoy)
    rng.shuffle(options)
    return options, options.index(answer)


def _arithmetic(rng: random.Random) -> Challenge:
    """Times tables and small sums — fast, and nobody argues with the answer."""

    style = rng.choice(["multiply", "add", "subtract"])
    if style == "multiply":
        left, right = rng.randint(3, 9), rng.randint(3, 9)
        answer = left * right
        prompt = f"{left} × {right}"
    elif style == "add":
        left, right = rng.randint(12, 49), rng.randint(12, 49)
        answer = left + right
        prompt = f"{left} + {right}"
    else:
        left = rng.randint(30, 90)
        right = rng.randint(5, left - 5)
        answer = left - right
        prompt = f"{left} − {right}"

    offsets = rng.sample([-12, -9, -6, -3, -2, -1, 1, 2, 3, 6, 9, 12], 6)
    decoys = [str(answer + offset) for offset in offsets if answer + offset > 0]
    choices, index = _shuffled_with_answer(rng, str(answer), decoys)
    return Challenge(kind="math", prompt=prompt, choices=choices, answer_index=index)


def _sequence(rng: random.Random) -> Challenge:
    """"What comes next" — pattern spotting, no arithmetic strain."""

    step = rng.choice([2, 3, 5, 10])
    start = rng.randint(1, 9)
    if rng.random() < 0.4:
        values = [start * (2**index) for index in range(4)]
        answer = start * (2**4)
    else:
        values = [start + step * index for index in range(4)]
        answer = start + step * 4
    prompt = ", ".join(str(value) for value in values) + ", ?"
    decoys = [str(answer + delta) for delta in rng.sample([-step, step, step * 2, 1, -1, 4], 5)]
    choices, index = _shuffled_with_answer(rng, str(answer), decoys)
    return Challenge(kind="sequence", prompt=prompt, choices=choices, answer_index=index)


ODD_ONE_OUT = [
    ("Which is NOT food?", ["🍕", "🌮", "🍣", "🛞"], "🛞"),
    ("Which is NOT food?", ["🍔", "🍜", "🥗", "🧦"], "🧦"),
    ("Which is NOT an animal?", ["🐴", "🦜", "🐄", "🚀"], "🚀"),
    ("Which one is a horse?", ["🐴", "🦜", "🐄", "🍕"], "🐴"),
    ("Which one is a bird?", ["🦜", "🐴", "🐟", "🌮"], "🦜"),
    ("Which is a drink?", ["🧋", "🍟", "🥨", "🧀"], "🧋"),
    ("Which is spicy?", ["🌶️", "🍦", "🥛", "🍞"], "🌶️"),
    ("Which is the biggest?", ["🐘", "🐁", "🐜", "🐝"], "🐘"),
]


def _odd_one_out(rng: random.Random) -> Challenge:
    """Pure recognition: the fastest possible unlock for a quick thumb."""

    prompt, options, answer = rng.choice(ODD_ONE_OUT)
    shuffled = list(options)
    rng.shuffle(shuffled)
    return Challenge(
        kind="pick",
        prompt=prompt,
        choices=shuffled,
        answer_index=shuffled.index(answer),
    )


def _biggest_number(rng: random.Random) -> Challenge:
    values = rng.sample(range(11, 99), CHOICE_COUNT)
    answer = str(max(values) if rng.random() < 0.5 else min(values))
    wanted = "BIGGEST" if answer == str(max(values)) else "SMALLEST"
    choices = [str(value) for value in values]
    rng.shuffle(choices)
    return Challenge(
        kind="compare",
        prompt=f"Tap the {wanted}",
        choices=choices,
        answer_index=choices.index(answer),
    )


def _pace(rng: random.Random) -> Challenge:
    """Hold a steady rate, shown live on the tap button.

    The tax here isn't stopping — it's *control*. Mashing overshoots the band, so
    a player has to deliberately back off the throttle, which is a different and
    funnier kind of hard than arithmetic.
    """

    target = rng.choice(PACE_TARGETS)
    return Challenge(
        kind="pace",
        prompt=f"HOLD {target:.0f} TAPS/SEC",
        hint=f"Keep the needle in the band for {PACE_HOLD_SECONDS:.0f}s",
        target_rate=target,
        tolerance=PACE_TOLERANCE,
        hold_seconds=PACE_HOLD_SECONDS,
    )


#: Weighted so most unlocks are near-instant recognition, with some arithmetic
#: and a regular dose of thumb control.
GENERATORS: list[tuple[Any, int]] = [
    (_odd_one_out, 27),
    (_arithmetic, 24),
    (_pace, 22),
    (_biggest_number, 16),
    (_sequence, 11),
]


def generate(rng: random.Random, *, allow_pace: bool = True) -> Challenge:
    """Roll one unlock challenge.

    ``allow_pace=False`` is used when re-rolling after a wrong answer: a maths
    question that turns into a rhythm task mid-attempt is disorienting, so a slot
    keeps the kind of challenge it started with.
    """

    pool = [
        (generator, weight)
        for generator, weight in GENERATORS
        if allow_pace or generator is not _pace
    ]
    generators = [generator for generator, _ in pool]
    weights = [weight for _, weight in pool]
    return rng.choices(generators, weights=weights, k=1)[0](rng)


@dataclass
class ChallengeStats:
    """Per-player unlock record, shown on the results card."""

    solved: int = 0
    failed: int = 0
    fastest_seconds: float | None = None
    _pending_since: dict[int, float] = field(default_factory=dict)

    def issued(self, slot: int, now: float) -> None:
        self._pending_since[slot] = now

    def record_solved(self, slot: int, now: float) -> None:
        self.solved += 1
        started = self._pending_since.pop(slot, None)
        if started is not None:
            elapsed = max(0.0, now - started)
            if self.fastest_seconds is None or elapsed < self.fastest_seconds:
                self.fastest_seconds = elapsed

    def record_failed(self) -> None:
        self.failed += 1
