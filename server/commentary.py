"""Race commentary (SPEC.md §12.3).

A rule-based line picker driven by engine events. Priorities interrupt the
ticker; cooldowns stop any single trigger from monopolising it; nothing repeats
inside a race. The writing carries a lot of the game's personality, so the
tables here are deliberately long.

Placeholders available per template: ``{horse}``, ``{other}``, ``{player}``,
``{item}``, ``{target}``, ``{winner}``.
"""

from __future__ import annotations

import random
from dataclasses import dataclass, field

from .state import EngineEvent, EngineEventKind


class Priority:
    CRITICAL = 0
    HIGH = 1
    MEDIUM = 2
    IDLE = 4


LINES: dict[str, list[str]] = {
    "race_start": [
        "And they're off! Dinner is officially on the line!",
        "The gates fly open — nobody in this office is eating quietly tonight!",
        "They're away! Somewhere out there, a burrito believes in itself.",
        "Hooves down, thumbs up, here we go!",
        "The field breaks clean and the office holds its breath!",
        "It's a beautiful evening for deciding this badly!",
        "Post time! Every option still hungry, every option still hopeful.",
        "We're racing! Please tap responsibly.",
    ],
    "lead_change": [
        "{horse} takes the lead — and takes it personally!",
        "New leader: {horse}! Absolutely no humility on display.",
        "{horse} hits the front! The crowd approves. The other horses do not.",
        "{horse} steals first place while nobody was looking!",
        "Lead change! {horse} would like to be taken seriously now.",
        "{horse} in front — this changes the entire dinner conversation.",
        "Up front now: {horse}, running like the fridge is empty.",
        "{horse} surges past! Someone's about to get very smug.",
    ],
    "powerup": [
        "{player} uses {item} on {target} — HR has been notified!",
        "{item} from {player}! {target} did not consent to that.",
        "Oh! {player} drops {item} on {target}. Cold-blooded.",
        "{player} spends {item} on {target} and immediately looks pleased.",
        "{item}! {player} is playing to win and it shows.",
        "That's {item} from {player} — the strategy nobody asked for.",
        "{player} activates {item}. {target} feels it instantly.",
        "{item} deployed by {player}. This is why we can't have nice things.",
    ],
    "powerup_blocked": [
        "Blocked! {target} shrugs off {item} completely.",
        "Nothing doing — {target} was ready for that one.",
        "{item} fizzles out. {player} is going to hear about this.",
        "Denied! {target} had protection and knew it.",
    ],
    "event": [
        "{headline} — the track has opinions tonight!",
        "{headline}! Nobody planned for this, least of all the horses.",
        "Here comes trouble: {headline}",
        "{headline}. Truly, the world is alive.",
    ],
    "cow": [
        "There is a cow. On the track. Nobody knows why.",
        "That cow has no idea it's in a sporting event.",
        "The cow is not sorry and the cow is not moving.",
    ],
    "second_wind": [
        "{horse} is NOT done! Look at that finish!",
        "Second wind for {horse} — the comeback nobody ordered!",
        "{horse} remembers it has legs!",
    ],
    "photo_finish_incoming": [
        "This is going to be CLOSE!",
        "Two noses, one dinner!",
        "I can't split them! Nobody can split them!",
    ],
    "final_stretch": [
        "FINAL FURLONG! Grip your phones!",
        "Down the stretch they come — tap like your appetite depends on it!",
        "Last chance! Empty the inventory!",
        "This is where dinner is decided!",
    ],
    "idle": [
        "{horse} has been training on a treadmill. A very small treadmill.",
        "Reminder: the loser buys nothing. This is purely about pride.",
        "The crowd is composed entirely of people who should be working.",
        "{horse} looks confident. {other} looks unemployed.",
        "Statistically, one of these will be dinner. Statistically.",
        "I've called nine hundred races and I still don't know what {horse} is.",
        "Somebody in accounting has money on {other}. Allegedly.",
        "{horse} running with the quiet dignity of a reheated lunch.",
        "The pace is honest. The horses are not.",
        "{other} has a plan. {other} has never had a plan.",
    ],
    "eliminated": [
        "{horse} is OUT! Discontinued, delisted, decommissioned!",
        "And {horse} is gone. Pour one out.",
        "{horse} eliminated — its backers are now free agents and very bitter.",
    ],
    "winner": [
        "{winner} WINS IT! Somebody get this horse a menu!",
        "{winner} takes the whole thing — dinner is decided!",
        "It's {winner}! The office has spoken, loudly and badly!",
        "{winner} first past the post! Absolutely no notes!",
    ],
    "photo_finish": [
        "TOO CLOSE TO CALL! Going to the replay booth!",
        "PHOTO FINISH! Nobody breathe!",
        "We need the camera on this one!",
    ],
}

#: Minimum seconds between two lines from the same trigger.
COOLDOWNS: dict[str, float] = {
    "lead_change": 4.0,
    "powerup": 2.0,
    "powerup_blocked": 4.0,
    "event": 3.0,
    "idle": 8.0,
    "eliminated": 1.0,
}

IDLE_INTERVAL = 8.0


@dataclass
class CommentaryLine:
    text: str
    priority: int = Priority.MEDIUM
    emoji: str = ""


@dataclass
class CommentaryDirector:
    """Turns engine events into commentary lines, with anti-repetition."""

    rng: random.Random
    horse_names: list[str] = field(default_factory=list)
    _used: dict[str, set[str]] = field(default_factory=dict)
    _last_at: dict[str, float] = field(default_factory=dict)
    _last_any_at: float = -99.0
    _final_stretch_called: bool = False
    _photo_warned: bool = False

    def _pick(self, trigger: str, now: float, **fmt: object) -> CommentaryLine | None:
        pool = LINES.get(trigger)
        if not pool:
            return None
        cooldown = COOLDOWNS.get(trigger, 0.0)
        if now - self._last_at.get(trigger, -99.0) < cooldown:
            return None
        used = self._used.setdefault(trigger, set())
        available = [line for line in pool if line not in used]
        if not available:
            used.clear()
            available = list(pool)
        template = self.rng.choice(available)
        used.add(template)
        self._last_at[trigger] = now
        self._last_any_at = now
        defaults = {
            "horse": fmt.get("horse", self._random_horse()),
            "other": fmt.get("other", self._random_horse()),
            "player": fmt.get("player", "Somebody"),
            "item": fmt.get("item", "something"),
            "target": fmt.get("target", "the field"),
            "winner": fmt.get("winner", "Nobody"),
            "headline": fmt.get("headline", "Something happens"),
        }
        priority = {
            "race_start": Priority.HIGH,
            "winner": Priority.CRITICAL,
            "photo_finish": Priority.CRITICAL,
            "photo_finish_incoming": Priority.HIGH,
            "final_stretch": Priority.HIGH,
            "lead_change": Priority.HIGH,
            "powerup": Priority.HIGH,
            "idle": Priority.IDLE,
        }.get(trigger, Priority.MEDIUM)
        try:
            text = template.format(**defaults)
        except (KeyError, IndexError):  # pragma: no cover - defensive
            text = template
        return CommentaryLine(text=text, priority=priority)

    def _random_horse(self) -> str:
        return self.rng.choice(self.horse_names) if self.horse_names else "the favourite"

    # ------------------------------------------------------------------

    def race_start(self, now: float) -> CommentaryLine | None:
        return self._pick("race_start", now)

    def from_engine_event(self, event: EngineEvent, now: float) -> CommentaryLine | None:
        """Map one engine event to at most one line."""

        payload = event.payload
        kind = event.kind
        if kind is EngineEventKind.LEAD_CHANGE:
            return self._pick("lead_change", now, horse=payload.get("horse"))
        if kind is EngineEventKind.POWERUP_CAST:
            blocked = payload.get("outcome") not in (None, "applied", "softened")
            trigger = "powerup_blocked" if blocked else "powerup"
            return self._pick(
                trigger,
                now,
                player=payload.get("player"),
                item=payload.get("powerup"),
                target=payload.get("target"),
            )
        if kind is EngineEventKind.EVENT_FIRED:
            if payload.get("event_id") == "cow":
                return self._pick("cow", now)
            if payload.get("event_id") == "second_wind":
                return self._pick("second_wind", now, horse=payload.get("horse"))
            return self._pick("event", now, headline=payload.get("headline", payload.get("name")))
        if kind is EngineEventKind.ELIMINATED:
            return self._pick("eliminated", now, horse=payload.get("horse"))
        if kind is EngineEventKind.RACE_FINISHED:
            if payload.get("photo_finish"):
                return self._pick("photo_finish", now)
            return self._pick("winner", now, winner=payload.get("winner"))
        return None

    def tick(self, now: float, *, progress: float, top_gap: float) -> CommentaryLine | None:
        """Pacing lines: final stretch, close-finish warning, idle colour."""

        if progress >= 0.75 and not self._final_stretch_called:
            self._final_stretch_called = True
            return self._pick("final_stretch", now)
        if progress >= 0.85 and top_gap < 25 and not self._photo_warned:
            self._photo_warned = True
            return self._pick("photo_finish_incoming", now)
        if now - self._last_any_at >= IDLE_INTERVAL:
            return self._pick("idle", now)
        return None
