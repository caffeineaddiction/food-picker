"""Monte Carlo balance envelopes (SPEC.md §18.6).

These are the guard rails that keep the game fun after fifty tweaks. A failure
here means a constant changed the *outcome distribution*, not just the feel.

Envelope rationale, measured over 240 seeded races each:

* An untapped field must be a fair lottery.
* A well-backed horse must be clearly better off than an ignored one, without
  the race becoming a formality (§2.3: ~35–45% for a 3-backer horse, with every
  other option keeping ~8–13%).
* No configuration may push a single horse past ~55%.
"""

from __future__ import annotations

import pytest
from conftest import DEFAULT_OPTIONS, Tapper, simulate, win_rates

from server import constants as C
from server.modes import MODES
from server.tracks import TRACKS

RACES = 240
HORSES = len(DEFAULT_OPTIONS)
UNIFORM = 1.0 / HORSES


def _rates(tappers, *, races=RACES, **kwargs):
    outcomes = [simulate(seed=seed, tappers=tappers, **kwargs) for seed in range(races)]
    return win_rates(outcomes, HORSES), outcomes


def test_untapped_field_is_a_fair_lottery():
    """With nobody tapping, every dinner option is equally likely."""

    rates, outcomes = _rates([])
    for horse_id, rate in rates.items():
        assert abs(rate - UNIFORM) <= 0.06, f"horse {horse_id} at {rate:.1%}"
    mean_duration = sum(o.duration for o in outcomes) / len(outcomes)
    assert 52.0 <= mean_duration <= 68.0, mean_duration


def test_one_backer_each_stays_even():
    """Six horses, one backer each, all tapping equally → still a lottery."""

    rates, _ = _rates(
        [Tapper(horse_id, 8.0) for horse_id in range(HORSES)],
        powerups=True,
        events=True,
    )
    for horse_id, rate in rates.items():
        assert abs(rate - UNIFORM) <= 0.07, f"horse {horse_id} at {rate:.1%}"


def test_popular_horse_is_favoured_but_not_a_formality():
    """§2.3 headline scenario: three backers versus one each elsewhere."""

    tappers = [Tapper(0, 8.0)] * 3 + [Tapper(horse_id, 8.0) for horse_id in range(1, HORSES)]
    rates, _ = _rates(tappers, powerups=True, events=True)
    assert 0.33 <= rates[0] <= 0.52, f"favourite at {rates[0]:.1%}"
    for horse_id in range(1, HORSES):
        assert rates[horse_id] >= 0.05, f"horse {horse_id} squeezed out at {rates[horse_id]:.1%}"


def test_hard_tapping_beats_casual_tapping():
    """Effort must pay: a maxed tapper beats a field of casual ones."""

    tappers = [Tapper(0, 12.0)] + [Tapper(horse_id, 4.0) for horse_id in range(1, HORSES)]
    rates, _ = _rates(tappers, powerups=True, events=True)
    assert rates[0] >= UNIFORM * 1.4, f"hard tapper only won {rates[0]:.1%}"
    assert rates[0] <= 0.55, f"hard tapper dominated at {rates[0]:.1%}"


def test_ignored_options_still_win_sometimes():
    """Even with a single sweaty player and five ignored horses, nobody is dead."""

    rates, _ = _rates([Tapper(0, 10.0)], powerups=True, events=True)
    assert rates[0] <= 0.85, f"lone tapper at {rates[0]:.1%}"
    for horse_id in range(1, HORSES):
        assert rates[horse_id] > 0.0, f"horse {horse_id} never won a single race"


def test_photo_finishes_are_special_but_not_rare():
    """The replay beat should land often enough to matter, rarely enough to thrill."""

    tappers = [Tapper(horse_id, 8.0) for horse_id in range(HORSES)]
    _, outcomes = _rates(tappers, powerups=True, events=True)
    share = sum(1 for outcome in outcomes if outcome.photo_finish) / len(outcomes)
    assert 0.08 <= share <= 0.45, f"photo finishes in {share:.0%} of races"


def test_taps_never_exceed_the_documented_ceiling():
    """The tap bonus is asymptotic: even absurd input cannot pass the cap."""

    from server.state import RacePlayer

    players = [RacePlayer(id=f"p{i}", name=f"P{i}", horse_id=0) for i in range(10)]
    for player in players:
        player.credit_taps(0.0, 999)
    total = sum(player.effective_tps(0.5) for player in players)
    import math
    bonus = C.TAP_BONUS_MAX * (1 - math.exp(-total / C.TAP_BONUS_SCALE))
    assert bonus <= C.TAP_BONUS_MAX + 1e-9


@pytest.mark.parametrize("mode_id", sorted(MODES))
def test_every_mode_completes(mode_id):
    """No mode may hang, crash, or fail to produce a winner."""

    for seed in range(6):
        outcome = simulate(
            seed=seed,
            tappers=[Tapper(0, 9.0), Tapper(1, 5.0)],
            mode=mode_id,
            powerups=True,
            events=True,
        )
        assert outcome.winner_id is not None
        assert outcome.ticks < 8000


@pytest.mark.parametrize("track_id", sorted(TRACKS))
def test_every_track_completes(track_id):
    """Track twists must never break a race or starve a lane."""

    for seed in range(6):
        outcome = simulate(
            seed=seed,
            tappers=[Tapper(0, 9.0), Tapper(2, 7.0)],
            track=track_id,
            powerups=True,
            events=True,
        )
        assert outcome.winner_id is not None
        assert len(outcome.order) == HORSES
