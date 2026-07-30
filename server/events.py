"""Random world events (SPEC.md §12).

Every event is telegraphed 1.5s before it lands, fires through the engine's
public surface, and returns a payload the display uses to stage its spectacle.
Some events (Photo Drone) deliberately have no gameplay effect — pacing beats
matter as much as swings.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from .effects import Category, Effect, EnterEffect, Zone

if TYPE_CHECKING:  # pragma: no cover - typing only
    from .engine import RaceEngine

Payload = dict[str, Any]


@dataclass(frozen=True)
class EventDef:
    """One entry in the random-event table."""

    id: str
    name: str
    emoji: str
    telegraph: str
    weight: int
    apply: Callable[[RaceEngine], Payload]
    tags: tuple[str, ...] = ()
    shake: float = 0.0
    """Screen-shake strength hint for the renderer (0–1)."""


def _event_effect(
    engine: RaceEngine,
    horse,
    *,
    effect_id: str,
    label: str,
    magnitude: float,
    duration: float,
    vfx: str,
    hostile: bool = False,
) -> None:
    engine.add_effect(
        horse,
        Effect(
            id=effect_id,
            label=label,
            magnitude=magnitude,
            expires_at=engine.race_time + duration,
            category=Category.EVENT,
            vfx=vfx,
            hostile=hostile,
        ),
    )


# ---------------------------------------------------------------------------
# Event implementations
# ---------------------------------------------------------------------------


def _crowd_wave(engine: RaceEngine) -> Payload:
    horse = engine.rng.choice(engine.racing_horses())
    _event_effect(
        engine, horse, effect_id="crowd_wave", label="Crowd Wave",
        magnitude=0.20, duration=3.0, vfx="cheer",
    )
    return {"horse_id": horse.id, "horse": horse.name, "headline": f"THE CROWD LOVES {horse.name}!"}


def _rain(engine: RaceEngine) -> Payload:
    duration = 8.0 * engine.track.weather_linger
    for horse in engine.racing_horses():
        _event_effect(
            engine, horse, effect_id="rain", label="Rain",
            magnitude=-0.10, duration=duration, vfx="rain", hostile=True,
        )
    engine.set_tap_efficiency(1.20, duration)
    return {"headline": "RAIN!", "duration": duration, "weather": "rain"}


def _mud_patch(engine: RaceEngine) -> Payload:
    leader = engine.leader()
    start = (leader.pos if leader else 0.0) + 60
    start = min(start, engine.config.track_length - 120)
    zone = Zone(
        id=engine.next_zone_id(),
        kind="mud",
        start=max(0.0, start),
        end=max(80.0, start + 80),
        magnitude=-0.20,
        expires_at=engine.race_time + 30.0 * engine.track.weather_linger,
        hostile=True,
        once_per_horse=False,
    )
    engine.add_zone(zone)
    return {"headline": "MUD PATCH!", "start": zone.start, "end": zone.end, "weather": "mud"}


def _tailwind_gust(engine: RaceEngine) -> Payload:
    standings = engine.standings()
    trailing = standings[len(standings) // 2 :]
    for horse in trailing:
        _event_effect(
            engine, horse, effect_id="gust", label="Tailwind Gust",
            magnitude=0.15, duration=4.0, vfx="leaves",
        )
    return {"headline": "TAILWIND GUST!", "horse_ids": [h.id for h in trailing]}


def _loose_cow(engine: RaceEngine) -> Payload:
    horses = engine.racing_horses()
    anchor = engine.rng.choice(horses)
    victims = sorted(horses, key=lambda h: abs(h.pos - anchor.pos))[:3]
    for horse in victims:
        engine.stumble_horse(horse, 0.8, tumble=False)
    return {
        "headline": "LOOSE COW ON THE TRACK",
        "pos": anchor.pos,
        "horse_ids": [h.id for h in victims],
    }


def _meteor(engine: RaceEngine) -> Payload:
    horses = engine.racing_horses()
    anchor = engine.rng.choice(horses)
    impact = anchor.pos + engine.rng.uniform(-30, 60)
    victims = [h for h in horses if abs(h.pos - impact) <= 40]
    for horse in victims:
        engine.stumble_horse(horse, 0.6, tumble=True)
    engine.add_zone(
        Zone(
            id=engine.next_zone_id(),
            kind="crater",
            start=impact - 18,
            end=impact + 18,
            magnitude=-0.05,
            expires_at=engine.race_time + 45.0,
            once_per_horse=False,
        )
    )
    return {"headline": "METEOR!", "pos": impact, "horse_ids": [h.id for h in victims]}


def _pigeon_flock(engine: RaceEngine) -> Payload:
    leader = engine.leader()
    if leader is None:
        return {"headline": "PIGEONS!"}
    _event_effect(
        engine, leader, effect_id="pigeons", label="Pigeon Flock",
        magnitude=-0.15, duration=3.0, vfx="birds", hostile=True,
    )
    return {"headline": "PIGEONS HARASS THE LEADER", "horse_id": leader.id, "horse": leader.name}


def _second_wind(engine: RaceEngine) -> Payload:
    trailing = engine.last_place()
    if trailing is None:
        return {"headline": "SECOND WIND!"}
    _event_effect(
        engine, trailing, effect_id="second_wind", label="Second Wind",
        magnitude=0.35, duration=3.0, vfx="hero",
    )
    return {
        "headline": f"SECOND WIND FOR {trailing.name}!",
        "horse_id": trailing.id,
        "horse": trailing.name,
        "hero": True,
    }


def _excited_horse(engine: RaceEngine) -> Payload:
    horse = engine.rng.choice(engine.racing_horses())
    _event_effect(
        engine, horse, effect_id="excited", label="Excited",
        magnitude=0.25, duration=2.0, vfx="hearts",
    )
    return {"headline": f"{horse.name} IS VERY EXCITED", "horse_id": horse.id, "horse": horse.name}


def _false_finish(engine: RaceEngine) -> Payload:
    leader = engine.leader()
    pos = (leader.pos if leader else 0.0) + engine.config.track_length * 0.12
    return {"headline": "FINISH LINE?!", "pos": min(pos, engine.config.track_length * 0.9)}


def _office_manager(engine: RaceEngine) -> Payload:
    for horse in engine.racing_horses():
        engine.freeze_horse(horse, 1.0, tag="announcement")

    def resume() -> None:
        for horse in engine.racing_horses():
            _event_effect(
                engine, horse, effect_id="make_up_time", label="Making Up Time",
                magnitude=0.10, duration=2.0, vfx="boost",
            )

    engine.schedule(1.0, resume)
    return {"headline": "QUICK ANNOUNCEMENT", "freeze": 1.0}


def _photo_drone(engine: RaceEngine) -> Payload:
    return {"headline": "DRONE SWEEP", "cinematic": True, "duration": 2.0}


def _jockey_swap(engine: RaceEngine) -> Payload:
    horses = engine.racing_horses()
    if len(horses) < 2:
        return {"headline": "JOCKEY SWAP"}
    first, second = engine.rng.sample(horses, 2)
    first.spec.jockey, second.spec.jockey = second.spec.jockey, first.spec.jockey
    for horse in (first, second):
        _event_effect(
            engine, horse, effect_id="jockey_swap", label="New Jockey",
            magnitude=0.05, duration=2.0, vfx="sparkle",
        )
    return {
        "headline": "JOCKEY SWAP!",
        "horse_ids": [first.id, second.id],
        "jockeys": {str(first.id): first.spec.jockey, str(second.id): second.spec.jockey},
    }


def _golden_apple(engine: RaceEngine) -> Payload:
    leader = engine.leader()
    spawn = min((leader.pos if leader else 0.0) + 150, engine.config.track_length * 0.95)
    engine.add_zone(
        Zone(
            id=engine.next_zone_id(),
            kind="apple",
            start=spawn - 10,
            end=spawn + 10,
            consume_on_trigger=True,
            expires_at=engine.race_time + 40.0,
            enter=EnterEffect(
                id="golden_apple",
                label="Golden Apple",
                magnitude=0.30,
                duration=3.0,
                vfx="apple",
            ),
        )
    )
    return {"headline": "GOLDEN APPLE!", "pos": spawn}


def _earthquake(engine: RaceEngine) -> Payload:
    for horse in engine.racing_horses():
        magnitude = engine.rng.uniform(-0.08, 0.08)
        _event_effect(
            engine, horse, effect_id="quake", label="Earthquake",
            magnitude=magnitude, duration=4.0, vfx="quake", hostile=magnitude < 0,
        )
    return {"headline": "EARTHQUAKE!", "duration": 4.0}


# ---------------------------------------------------------------------------
# The table (§12.2)
# ---------------------------------------------------------------------------

EVENTS: dict[str, EventDef] = {}


def _register(event: EventDef) -> None:
    EVENTS[event.id] = event


# fmt: off
_register(EventDef("crowd_wave", "Crowd Wave", "👏", "THE CROWD IS RISING", 10, _crowd_wave))
_register(EventDef("rain", "Rain", "🌧️", "RAIN INCOMING", 9, _rain))
_register(EventDef("mud", "Mud Patch", "🟤", "MUD AHEAD", 8, _mud_patch))
_register(EventDef("gust", "Tailwind Gust", "🍃", "GUST BUILDING", 8, _tailwind_gust))
_register(EventDef("cow", "Loose Cow", "🐄", "IS THAT A COW?", 7, _loose_cow, shake=0.3))
_register(EventDef("meteor", "Meteor", "☄️", "SOMETHING IN THE SKY", 5, _meteor, shake=1.0))
_register(EventDef("pigeons", "Pigeon Flock", "🐦", "BIRDS INBOUND", 7, _pigeon_flock))
_register(EventDef("second_wind", "Second Wind", "💫", "SOMEONE'S NOT DONE YET", 8, _second_wind))
_register(EventDef("excited", "Horse Gets Excited", "💗", "SOMEBODY'S EXCITED", 7, _excited_horse))
_register(EventDef("false_finish", "False Finish", "🎪", "FINISH LINE SPOTTED?", 3, _false_finish))
_register(EventDef("office_manager", "Office Manager", "👔", "INCOMING ANNOUNCEMENT", 5,
                   _office_manager, tags=("office",), shake=0.2))
_register(EventDef("drone", "Photo Drone", "🚁", "DRONE INBOUND", 6, _photo_drone))
_register(EventDef("jockey_swap", "Jockey Swap", "🤾", "JOCKEYS ARE RESTLESS", 4, _jockey_swap))
_register(EventDef("apple", "Golden Apple", "🍎", "SOMETHING SHINY AHEAD", 6, _golden_apple))
_register(EventDef("earthquake", "Earthquake", "🌋", "THE GROUND IS SHAKING", 4, _earthquake,
                   shake=0.8))
# fmt: on


def pick_event(
    rng,
    *,
    exclude: set[str],
    tag_bonus: dict[str, int] | None = None,
) -> EventDef | None:
    """Weighted pick from the event table, skipping already-used events."""

    pool = [event for event in EVENTS.values() if event.id not in exclude]
    if not pool:
        return None
    weights = []
    for event in pool:
        weight = float(event.weight)
        for tag, bonus in (tag_bonus or {}).items():
            if tag in event.tags:
                weight += bonus
        weights.append(weight)
    return rng.choices(pool, weights=weights, k=1)[0]
