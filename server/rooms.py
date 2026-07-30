"""Rooms, connections and race orchestration (SPEC.md §6, §7).

One :class:`Room` owns one office's race night: the roster, the config, the
betting pools, the tournament bracket, and the asyncio task that drives the
simulation at a fixed 20 Hz and broadcasts snapshots.

Everything mutates on the event loop — the room loop and the socket handlers are
coroutines on the same loop — so there are no locks anywhere in this file.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import random
import secrets
import time
from collections.abc import Iterable
from dataclasses import dataclass, field
from enum import Enum
from typing import Any

from . import constants as C
from .breeds import BREEDS, DEFAULT_BREED_ID, breed_catalog
from .commentary import CommentaryDirector
from .engine import RaceEngine
from .modes import DEFAULT_MODE_ID, ModeDef, get_mode, mode_catalog
from .powerups import POWERUPS, TargetClass, powerup_catalog
from .protocol import ClientRole, ServerMessage
from .roster import build_horses, options_are_raceable, parse_options
from .state import EngineEventKind, HorseSpec, RaceConfig, RacePhase, RacePlayer
from .stats import SessionStats
from .tracks import DEFAULT_TRACK_ID, TRACKS, get_track, track_catalog

log = logging.getLogger(__name__)

DEFAULT_OPTIONS = ["Chipotle", "Sushi", "Pizza", "Taco Bell", "Five Guys", "Panda Express"]
REACTION_EMOJI = ["🦜", "😂", "🔥", "😱", "🍕", "💀"]
PARTY_PARROT = "🦜"


class RoomPhase(str, Enum):
    """Room-level phases. The engine owns countdown/running inside ``RACING``."""

    LOBBY = "lobby"
    BETTING = "betting"
    RACING = "racing"
    PHOTO_FINISH = "photo_finish"
    CEREMONY = "ceremony"
    RESULTS = "results"
    BRACKET = "bracket"


@dataclass
class Connection:
    """One websocket, with just enough identity to route messages."""

    id: str
    socket: Any
    role: str = ClientRole.SPECTATOR
    participant_id: str | None = None

    async def send(self, payload: str) -> bool:
        """Send a pre-encoded frame. Returns False if the socket is gone."""

        try:
            await self.socket.send_text(payload)
            return True
        except Exception:  # pragma: no cover - socket teardown races
            return False


@dataclass
class Participant:
    """A person in the room, surviving reconnects and races (§7.4)."""

    id: str
    name: str
    role: str = ClientRole.PLAYER
    horse_id: int | None = None
    """The horse this person's taps currently feed."""
    horse_ids: list[int] = field(default_factory=list)
    """Every horse they back, up to ``MAX_BACKED_HORSES``."""
    connected: bool = False
    bankroll: int = C.BETTING_STARTING_BANKROLL
    pending_bet: tuple[int, int] | None = None
    """(horse_id, amount) for the current betting window."""
    ready_for_rematch: bool = False
    career_taps: int = 0
    career_wins: int = 0
    last_seen: float = field(default_factory=time.time)

    @property
    def is_player(self) -> bool:
        return self.role == ClientRole.PLAYER

    @property
    def backed_horse_ids(self) -> list[int]:
        if self.horse_ids:
            return self.horse_ids
        return [] if self.horse_id is None else [self.horse_id]

    def backs(self, horse_id: int) -> bool:
        return horse_id in self.backed_horse_ids

    def set_backing(self, horse_ids: list[int], active: int | None = None) -> None:
        unique: list[int] = []
        for horse_id in horse_ids:
            if horse_id not in unique:
                unique.append(horse_id)
        self.horse_ids = unique[: C.MAX_BACKED_HORSES]
        if active is not None and active in self.horse_ids:
            self.horse_id = active
        elif self.horse_id not in self.horse_ids:
            self.horse_id = self.horse_ids[0] if self.horse_ids else None

    def public(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "name": self.name,
            "role": self.role,
            "horse_id": self.horse_id,
            "horse_ids": self.backed_horse_ids,
            "connected": self.connected,
            "bankroll": self.bankroll,
            "ready": self.ready_for_rematch,
        }


@dataclass
class TournamentState:
    """Heats → final bracket for Tournament mode (§11.4)."""

    heats: list[list[str]]
    heat_index: int = 0
    winners: list[str] = field(default_factory=list)
    final: bool = False
    champion: str | None = None

    @property
    def current(self) -> list[str]:
        if self.final:
            return self.winners
        return self.heats[self.heat_index] if self.heat_index < len(self.heats) else []

    def label(self) -> str:
        if self.final:
            return "FINAL"
        return f"HEAT {self.heat_index + 1} of {len(self.heats)}"

    def bracket_view(self) -> dict[str, Any]:
        return {
            "heats": self.heats,
            "heatIndex": self.heat_index,
            "winners": self.winners,
            "final": self.final,
            "champion": self.champion,
            "label": self.label(),
        }


class Room:
    """One race night."""

    def __init__(self, code: str, manager: RoomManager) -> None:
        self.code = code
        self.manager = manager
        self.host_token = secrets.token_urlsafe(12)
        self.created_at = time.time()

        self.phase: RoomPhase = RoomPhase.LOBBY
        self.options: list[str] = list(DEFAULT_OPTIONS)
        self.emoji_overrides: dict[str, str] = {}
        self.breed_overrides: dict[str, str] = {}
        self.mode_id: str = DEFAULT_MODE_ID
        self.track_id: str = DEFAULT_TRACK_ID
        self.duration: float = C.DEFAULT_RACE_SECONDS
        self.powerups_on: bool = True
        self.events_on: bool = True
        self.random_track: bool = True
        self.public_url: str | None = os.environ.get("PUBLIC_URL") or None
        """Origin phones should use, when it isn't the one the TV loaded from.

        Opening the display on ``localhost`` while phones come in over a tunnel
        is the normal case, and a QR pointing at localhost is useless to them —
        so the host can set this (or export ``PUBLIC_URL``) and the QR follows.
        """

        self.connections: dict[str, Connection] = {}
        self.participants: dict[str, Participant] = {}

        self.engine: RaceEngine | None = None
        self.commentary: CommentaryDirector | None = None
        self.race_task: asyncio.Task | None = None
        self.race_number: int = 0
        self.last_results: dict[str, Any] | None = None
        self.tournament: TournamentState | None = None
        self.betting_closes_at: float | None = None
        self.betting_pool: dict[int, int] = {}
        self.stats = manager.stats
        self._rng = random.Random()
        self._last_reaction: dict[str, float] = {}
        self._aborting = False
        self._skip = asyncio.Event()

    # ------------------------------------------------------------------
    # Connections
    # ------------------------------------------------------------------

    def attach(self, connection: Connection) -> None:
        self.connections[connection.id] = connection

    async def detach(self, connection_id: str) -> None:
        connection = self.connections.pop(connection_id, None)
        if connection is None:
            return
        participant = self.participants.get(connection.participant_id or "")
        if participant is not None and not self._has_live_connection(participant.id):
            participant.connected = False
            await self.broadcast_room_state()

    def _has_live_connection(self, participant_id: str) -> bool:
        return any(
            connection.participant_id == participant_id
            for connection in self.connections.values()
        )

    async def broadcast(self, message: dict[str, Any]) -> None:
        """Encode once, fan out to every socket (§16.6)."""

        payload = json.dumps(message, separators=(",", ":"))
        await self._send_raw(payload, list(self.connections.values()))

    async def send_to_participant(self, participant_id: str, message: dict[str, Any]) -> None:
        targets = [
            connection
            for connection in self.connections.values()
            if connection.participant_id == participant_id
        ]
        if targets:
            await self._send_raw(json.dumps(message, separators=(",", ":")), targets)

    async def _send_raw(self, payload: str, targets: Iterable[Connection]) -> None:
        dead: list[str] = []
        for connection in targets:
            if not await connection.send(payload):
                dead.append(connection.id)
        for connection_id in dead:
            self.connections.pop(connection_id, None)

    # ------------------------------------------------------------------
    # Roster / lobby
    # ------------------------------------------------------------------

    def join_url(self, *, fallback: str) -> str:
        """The URL the QR code should encode.

        ``fallback`` is derived from the incoming request, which is right when
        the TV is already on the public URL. An explicit ``public_url`` wins,
        because only the host knows the tunnel address.
        """

        if not self.public_url:
            return fallback
        origin = self.public_url.strip().rstrip("/")
        if not origin.startswith(("http://", "https://")):
            origin = f"https://{origin}"
        return f"{origin}/play?room={self.code}"

    def set_public_url(self, value: str | None) -> None:
        cleaned = (value or "").strip()
        self.public_url = cleaned or None

    def horse_specs(self) -> list[HorseSpec]:
        names = self.tournament.current if self.tournament else self.options
        return build_horses(names, self.emoji_overrides, self.breed_overrides)

    def mode(self) -> ModeDef:
        return get_mode(self.mode_id)

    def join(
        self,
        *,
        name: str,
        horse_id: int | None,
        participant_id: str | None,
        as_host: bool,
        horse_ids: list[int] | None = None,
    ) -> Participant:
        """Create or update a participant. Existing tokens keep their identity.

        ``horse_ids`` is the full set the player wants to back (capped at
        ``MAX_BACKED_HORSES``); ``horse_id`` is the one their taps should feed and
        is also the whole set for the ordinary single-horse case.
        """

        participant = self.participants.get(participant_id or "")
        if participant is None:
            participant = Participant(id=participant_id or secrets.token_urlsafe(12), name=name)
            self.participants[participant.id] = participant
        participant.name = name[: C.MAX_PLAYER_NAME_LENGTH].strip() or participant.name
        participant.connected = True
        participant.last_seen = time.time()

        wanted = [
            candidate
            for candidate in (horse_ids if horse_ids is not None else [])
            if self._horse_exists(candidate)
        ]
        if horse_id is not None and self._horse_exists(horse_id) and horse_id not in wanted:
            wanted.insert(0, horse_id)

        # Asking for horses makes you a player; asking for none makes you a
        # spectator. Saying nothing at all leaves an existing seat alone, so a
        # client re-asserting its name can't silently throw away its horses.
        if as_host:
            participant.role = ClientRole.HOST
        elif wanted:
            participant.role = ClientRole.PLAYER
        elif horse_ids == [] or not participant.backed_horse_ids:
            participant.role = ClientRole.SPECTATOR
            participant.set_backing([])

        if wanted and self._can_pick_horse(participant):
            self.assign_horses(participant, wanted, active=horse_id)
        return participant

    def _horse_exists(self, horse_id: int) -> bool:
        return 0 <= horse_id < len(self.horse_specs())

    def _can_pick_horse(self, participant: Participant | None = None) -> bool:
        """Horses may only be claimed outside a live race (§7.5).

        The one exception is Last Bite: when your option is eliminated you become
        a free agent and may back somebody else mid-race (§11.3). Everyone else
        is locked out so nobody can jump on the leader at t=50s.
        """

        open_phases = (RoomPhase.LOBBY, RoomPhase.BETTING, RoomPhase.RESULTS, RoomPhase.BRACKET)
        if self.phase in open_phases:
            return True
        if self.phase is RoomPhase.RACING and self.mode().elimination:
            return participant is not None and not participant.backed_horse_ids
        return False

    def assign_horses(
        self, participant: Participant, horse_ids: list[int], active: int | None = None
    ) -> None:
        """Set which horses a participant backs, keeping the live race in sync."""

        participant.set_backing(horse_ids, active=active)
        engine = self.engine
        if engine is not None:
            racer = engine.players.get(participant.id)
            if racer is not None:
                racer.set_backing(participant.backed_horse_ids, active=participant.horse_id)
            engine.recount_backers()

    def players(self) -> list[Participant]:
        return [p for p in self.participants.values() if p.is_player and p.backed_horse_ids]

    def can_start(self) -> bool:
        return options_are_raceable(self.horse_specs_names()) and self.phase in (
            RoomPhase.LOBBY,
            RoomPhase.RESULTS,
            RoomPhase.BRACKET,
        )

    def horse_specs_names(self) -> list[str]:
        return [spec.name for spec in self.horse_specs()]

    # ------------------------------------------------------------------
    # Host configuration
    # ------------------------------------------------------------------

    def apply_config(self, payload: Any) -> None:
        if payload.options is not None:
            options = parse_options(payload.options)
            if options:
                self.options = options
                self._prune_horse_selections()
        if payload.mode is not None and payload.mode in [m["id"] for m in mode_catalog()]:
            self.mode_id = payload.mode
            mode = self.mode()
            if mode.duration_locked:
                self.duration = mode.default_duration
        if payload.track is not None:
            if payload.track == "random":
                self.random_track = True
            elif payload.track in TRACKS:
                self.random_track = False
                self.track_id = payload.track
        if payload.duration is not None and not self.mode().duration_locked:
            self.duration = min(C.MAX_RACE_SECONDS, max(C.MIN_RACE_SECONDS, payload.duration))
        if payload.powerups_on is not None:
            self.powerups_on = payload.powerups_on
        if payload.events_on is not None:
            self.events_on = payload.events_on
        if payload.public_url is not None:
            self.set_public_url(payload.public_url)

    def set_emoji(self, horse_id: int, emoji: str) -> None:
        specs = self.horse_specs()
        if 0 <= horse_id < len(specs):
            names = self.tournament.current if self.tournament else self.options
            self.emoji_overrides[names[horse_id]] = emoji[:8]

    def set_breed(self, horse_id: int, breed_id: str) -> None:
        """Choose which animal a dinner option runs as. Cosmetic only."""

        specs = self.horse_specs()
        if not 0 <= horse_id < len(specs) or breed_id not in BREEDS:
            return
        names = self.tournament.current if self.tournament else self.options
        self.breed_overrides[names[horse_id]] = breed_id

    def _prune_horse_selections(self) -> None:
        """Drop selections that no longer exist after an option-list edit."""

        count = len(self.horse_specs())
        for participant in self.participants.values():
            surviving = [
                horse_id for horse_id in participant.backed_horse_ids if horse_id < count
            ]
            if surviving != participant.backed_horse_ids:
                participant.set_backing(surviving)

    def kick(self, participant_id: str) -> None:
        self.participants.pop(participant_id, None)

    # ------------------------------------------------------------------
    # Serialisation
    # ------------------------------------------------------------------

    def room_state(self) -> dict[str, Any]:
        specs = self.horse_specs()
        mode = self.mode()
        return {
            "t": ServerMessage.ROOM_STATE,
            "code": self.code,
            "phase": self.phase.value,
            "config": {
                "mode": self.mode_id,
                "track": "random" if self.random_track else self.track_id,
                "resolvedTrack": self.track_id,
                "duration": self.duration,
                "durationLocked": mode.duration_locked,
                "powerups": self.powerups_on,
                "events": self.events_on,
                "options": list(self.options),
                "publicUrl": self.public_url or "",
            },
            "horses": [
                {
                    "id": spec.id,
                    "name": spec.name,
                    "emoji": spec.emoji,
                    "color": spec.color,
                    "jockey": spec.jockey,
                    "breed": spec.breed,
                    "backers": sum(
                        1
                        for person in self.participants.values()
                        if person.is_player and person.backs(spec.id)
                    ),
                }
                for spec in specs
            ],
            "participants": [p.public() for p in self.participants.values()],
            "canStart": self.can_start(),
            "raceNumber": self.race_number,
            "tournament": self.tournament.bracket_view() if self.tournament else None,
            "betting": self.betting_view(),
            "stats": self.stats.leaderboard(),
        }

    def betting_view(self) -> dict[str, Any] | None:
        """Live pari-mutuel odds from the current pool (§11.5)."""

        if not self.mode().betting:
            return None
        total = sum(self.betting_pool.values())
        odds: dict[str, float] = {}
        for spec in self.horse_specs():
            staked = self.betting_pool.get(spec.id, 0)
            payout = (total / staked) if staked else float(len(self.horse_specs()))
            odds[str(spec.id)] = round(max(1.05, payout * (1 - C.BETTING_TAKEOUT)), 2)
        return {
            "open": self.phase is RoomPhase.BETTING,
            "closesIn": max(0.0, round((self.betting_closes_at or 0) - time.time(), 1))
            if self.betting_closes_at
            else 0.0,
            "pool": {str(k): v for k, v in self.betting_pool.items()},
            "total": total,
            "odds": odds,
        }

    async def broadcast_room_state(self) -> None:
        await self.broadcast(self.room_state())

    async def broadcast_phase(self, extra: dict[str, Any] | None = None) -> None:
        await self.broadcast(
            {"t": ServerMessage.PHASE, "phase": self.phase.value, "data": extra or {}}
        )

    # ------------------------------------------------------------------
    # Betting
    # ------------------------------------------------------------------

    def place_bet(self, participant_id: str, horse_id: int, amount: int) -> bool:
        if self.phase is not RoomPhase.BETTING:
            return False
        participant = self.participants.get(participant_id)
        if participant is None or not self._horse_exists(horse_id):
            return False
        if participant.pending_bet:
            previous_horse, previous_amount = participant.pending_bet
            self.betting_pool[previous_horse] = max(
                0, self.betting_pool.get(previous_horse, 0) - previous_amount
            )
            participant.bankroll += previous_amount
            participant.pending_bet = None
        amount = max(0, min(amount, participant.bankroll))
        if amount < C.BETTING_MIN_BET:
            return False
        participant.bankroll -= amount
        participant.pending_bet = (horse_id, amount)
        self.betting_pool[horse_id] = self.betting_pool.get(horse_id, 0) + amount
        return True

    def settle_bets(self, winning_horse_id: int | None) -> list[dict[str, Any]]:
        """Pay out the pool and clear pending bets. Returns a payout summary."""

        total = sum(self.betting_pool.values())
        # Horse id 0 is a real horse: never let it fall through a truthiness check.
        winning_stake = (
            self.betting_pool.get(winning_horse_id, 0) if winning_horse_id is not None else 0
        )
        payouts: list[dict[str, Any]] = []
        for participant in self.participants.values():
            if not participant.pending_bet:
                continue
            horse_id, amount = participant.pending_bet
            participant.pending_bet = None
            if horse_id == winning_horse_id and winning_stake > 0:
                won = int(amount * (total / winning_stake) * (1 - C.BETTING_TAKEOUT))
                participant.bankroll += won
                payouts.append(
                    {"name": participant.name, "won": won, "staked": amount, "hit": True}
                )
            else:
                payouts.append({"name": participant.name, "won": 0, "staked": amount, "hit": False})
        self.betting_pool = {}
        payouts.sort(key=lambda row: row["won"], reverse=True)
        return payouts

    # ------------------------------------------------------------------
    # Reactions
    # ------------------------------------------------------------------

    async def react(self, participant_id: str, emoji: str) -> None:
        now = time.time()
        if now - self._last_reaction.get(participant_id, 0.0) < C.REACTION_MIN_INTERVAL_S:
            return
        self._last_reaction[participant_id] = now
        if emoji not in REACTION_EMOJI:
            emoji = REACTION_EMOJI[0]
        await self.broadcast({"t": ServerMessage.REACTION, "emoji": emoji})

    # ------------------------------------------------------------------
    # Race lifecycle
    # ------------------------------------------------------------------

    async def start_race(self) -> None:
        """Host pressed START (or a rematch/next heat begins)."""

        if self.race_task and not self.race_task.done():
            return
        if not options_are_raceable(self.horse_specs_names()):
            await self.broadcast({"t": ServerMessage.ERROR, "message": "Need 2–12 options"})
            return
        mode = self.mode()
        if mode.tournament and self.tournament is None:
            self.tournament = self._build_bracket()
        self._aborting = False
        # Everything (including the betting window) runs inside the task so the
        # host's socket keeps processing messages while the room waits.
        self.race_task = asyncio.create_task(self._race_lifecycle())

    def _build_bracket(self) -> TournamentState:
        """Split options into heats of at most ``heat_size`` (§11.4)."""

        options = list(self.options)
        size = self.mode().heat_size
        if len(options) <= size:
            heats = [options]
        else:
            heat_count = max(2, (len(options) + size - 1) // size)
            heats = [options[index::heat_count] for index in range(heat_count)]
        return TournamentState(heats=[heat for heat in heats if heat])

    async def _run_betting_window(self) -> None:
        self.phase = RoomPhase.BETTING
        self.betting_pool = {}
        self.betting_closes_at = time.time() + C.BETTING_WINDOW_SECONDS
        await self.broadcast_room_state()
        await self.broadcast_phase({"seconds": C.BETTING_WINDOW_SECONDS})
        while time.time() < (self.betting_closes_at or 0) and not self._aborting:
            await asyncio.sleep(1.0)
            await self.broadcast_room_state()
        self.betting_closes_at = None

    def _resolve_track(self) -> str:
        if self.random_track:
            candidates = [track for track in TRACKS if track != self.track_id] or list(TRACKS)
            self.track_id = self._rng.choice(candidates)
        return self.track_id

    def _build_engine(self) -> RaceEngine:
        mode = self.mode()
        specs = self.horse_specs()
        duration = mode.duration_for(len(specs), self.duration)
        if mode.tournament and self.tournament:
            duration = mode.final_duration if self.tournament.final else mode.heat_duration
        config = RaceConfig(
            mode_id=self.mode_id,
            track_id=self._resolve_track(),
            duration=duration,
            track_length=mode.track_length,
            powerups_on=self.powerups_on,
            events_on=self.events_on,
            seed=self._rng.randrange(1, 2**31),
            label=self.tournament.label() if self.tournament else None,
        )
        race_players = []
        for participant in self.players():
            racer = RacePlayer(id=participant.id, name=participant.name)
            racer.set_backing(participant.backed_horse_ids, active=participant.horse_id)
            race_players.append(racer)
        return RaceEngine(config, specs, race_players)

    async def _race_lifecycle(self) -> None:
        """Countdown → race loop → photo finish → ceremony → results."""

        try:
            if self.mode().betting:
                await self._run_betting_window()
                if self._aborting:
                    return
            self.race_number += 1
            engine = self._build_engine()
            self.engine = engine
            self.commentary = CommentaryDirector(
                rng=random.Random(engine.config.seed),
                horse_names=[horse.name for horse in engine.horses],
            )
            for participant in self.participants.values():
                participant.ready_for_rematch = False

            self.phase = RoomPhase.RACING
            await self.broadcast_room_state()
            await self.broadcast_phase(
                {
                    "countdown": C.COUNTDOWN_SECONDS,
                    "track": get_track(engine.config.track_id).client_meta(),
                    "mode": self.mode().client_meta(),
                    "duration": engine.config.duration,
                    "trackLength": engine.config.track_length,
                    "label": engine.config.label,
                    "raceNumber": self.race_number,
                }
            )
            await self._run_race_loop(engine)
            if self._aborting:
                return
            results = engine.results()
            self.last_results = results
            await self._present_finish(engine, results)
        except asyncio.CancelledError:  # pragma: no cover - abort path
            raise
        except Exception:  # pragma: no cover - keep the room alive on bugs
            log.exception("race loop crashed in room %s", self.code)
            self.phase = RoomPhase.LOBBY
            await self.broadcast_room_state()

    async def _run_race_loop(self, engine: RaceEngine) -> None:
        """Fixed-timestep loop: step, translate events, broadcast, sleep."""

        loop = asyncio.get_running_loop()
        started_at = loop.time()
        hud_interval = max(1, C.TICK_RATE // C.PLAYER_HUD_RATE)
        announced_start = False

        while engine.phase is not RacePhase.FINISHED and not self._aborting:
            events = engine.step()
            await self._dispatch_engine_events(events)

            if engine.phase is RacePhase.RUNNING and not announced_start:
                announced_start = True
                await self._say(self.commentary.race_start(engine.race_time))

            await self.broadcast(engine.snapshot())

            if engine.tick % hud_interval == 0:
                await self._send_player_huds(engine)
            if engine.phase is RacePhase.RUNNING:
                await self._say(
                    self.commentary.tick(
                        engine.race_time,
                        progress=engine.race_progress(),
                        top_gap=self._top_gap(engine),
                    )
                )

            target = started_at + engine.tick * C.TICK_DT
            delay = target - loop.time()
            if delay > 0:
                await asyncio.sleep(delay)
            elif delay < -1.0:  # pragma: no cover - overloaded host
                started_at = loop.time() - engine.tick * C.TICK_DT

    def _top_gap(self, engine: RaceEngine) -> float:
        standings = [horse for horse in engine.standings() if horse.racing]
        if len(standings) < 2:
            return 999.0
        return abs(
            standings[0].total_distance(engine.config.track_length)
            - standings[1].total_distance(engine.config.track_length)
        )

    async def _dispatch_engine_events(self, events: list) -> None:
        for event in events:
            await self._dispatch_engine_event(event)
            if self.commentary is not None and self.engine is not None:
                await self._say(
                    self.commentary.from_engine_event(event, self.engine.race_time)
                )

    async def _dispatch_engine_event(self, event) -> None:
        kind = event.kind
        payload = event.payload

        if event.to_player:
            if kind is EngineEventKind.POWERUP_GRANT:
                powerup = POWERUPS.get(payload["powerup_id"])
                await self.send_to_participant(
                    event.to_player,
                    {
                        "t": ServerMessage.GRANT,
                        "slot": payload["slot"],
                        "powerup": powerup.client_meta() if powerup else None,
                        # The unlock challenge rides along with the item, so the
                        # phone can open it without waiting for an inventory frame.
                        "challenge": payload.get("challenge"),
                    },
                )
            elif kind is EngineEventKind.CHALLENGE_SOLVED:
                await self.send_to_participant(
                    event.to_player,
                    {"t": ServerMessage.UNLOCKED, **payload},
                )
            elif kind is EngineEventKind.INVENTORY:
                await self.send_to_participant(
                    event.to_player,
                    {"t": ServerMessage.INVENTORY, "inventory": payload["inventory"]},
                )
            elif kind is EngineEventKind.INTEL:
                await self.send_to_participant(
                    event.to_player, {"t": ServerMessage.INTEL, **payload}
                )
            return

        if kind is EngineEventKind.ELIMINATED:
            # Free agents (§11.3): release the seat so the phone offers a re-pick.
            dead_horse = payload.get("horse_id")
            for participant in self.participants.values():
                if dead_horse is not None and participant.backs(dead_horse):
                    remaining = [
                        horse_id
                        for horse_id in participant.backed_horse_ids
                        if horse_id != dead_horse
                    ]
                    participant.set_backing(remaining)
            await self.broadcast_room_state()

        if kind is EngineEventKind.POWERUP_CAST:
            await self.broadcast({"t": ServerMessage.NOTIFY, **payload})
        elif kind in (
            EngineEventKind.EVENT_TELEGRAPH,
            EngineEventKind.EVENT_FIRED,
            EngineEventKind.PICKUP,
            EngineEventKind.LEAD_CHANGE,
            EngineEventKind.ELIMINATED,
            EngineEventKind.HORSE_FINISHED,
            EngineEventKind.TRACK_MOMENT,
        ):
            await self.broadcast({"t": ServerMessage.EVENT, "kind": kind.value, **payload})
        elif kind is EngineEventKind.RACE_FINISHED:
            await self.broadcast({"t": ServerMessage.EVENT, "kind": kind.value, **payload})

    async def _send_player_huds(self, engine: RaceEngine) -> None:
        for participant in self.participants.values():
            hud = engine.player_hud(participant.id)
            if hud is not None:
                await self.send_to_participant(participant.id, hud)

    async def _say(self, line) -> None:
        if line is None:
            return
        await self.broadcast(
            {"t": ServerMessage.COMMENTARY, "text": line.text, "priority": line.priority}
        )

    async def _present_finish(self, engine: RaceEngine, results: dict[str, Any]) -> None:
        """Photo finish replay (if earned) → ceremony → results."""

        if results.get("photo_finish"):
            self._skip.clear()
            self.phase = RoomPhase.PHOTO_FINISH
            await self.broadcast_phase({"results": results})
            await self._sleep_or_skip(C.PHOTO_FINISH_PRESENTATION_SECONDS)

        payouts = self.settle_bets(results.get("winner_id")) if self.mode().betting else []
        self._record_stats(engine, results)

        self._skip.clear()
        self.phase = RoomPhase.CEREMONY
        await self.broadcast_phase(
            {
                "results": results,
                "payouts": payouts,
                "stats": self.stats.leaderboard(),
                "tournament": self.tournament.bracket_view() if self.tournament else None,
            }
        )
        await self.broadcast(
            {
                "t": ServerMessage.RESULT,
                **results,
                "payouts": payouts,
                "mode": self.mode_id,
                "track": self.track_id,
            }
        )
        await self._sleep_or_skip(C.CEREMONY_SECONDS)
        if self._aborting:
            return

        if self.tournament is not None:
            await self._advance_tournament(results)
            return

        self.phase = RoomPhase.RESULTS
        await self.broadcast_room_state()
        await self.broadcast_phase({"results": results})

    def _record_stats(self, engine: RaceEngine, results: dict[str, Any]) -> None:
        winning_horse = results.get("winner_id")
        for row in results.get("players", []):
            participant = self.participants.get(row["player_id"])
            if participant is None:
                continue
            participant.career_taps += row["taps"]
            if row["horse_id"] == winning_horse:
                participant.career_wins += 1
        self.stats.record_race(results, mode=self.mode_id, track=engine.config.track_id)

    async def _advance_tournament(self, results: dict[str, Any]) -> None:
        """Record a heat winner and either run the next heat or the final."""

        bracket = self.tournament
        if bracket is None:
            return
        winner = results.get("winner")
        # A field small enough for a single heat has no final to run: that heat
        # decided dinner, so crown it rather than staging a one-horse race.
        if bracket.final or len(bracket.heats) <= 1:
            bracket.champion = winner
            self.phase = RoomPhase.RESULTS
            await self.broadcast_room_state()
            await self.broadcast_phase({"results": results, "champion": winner})
            self.tournament = None
            return

        if winner:
            bracket.winners.append(winner)
        bracket.heat_index += 1
        if bracket.heat_index >= len(bracket.heats):
            bracket.final = True
        for participant in self.participants.values():
            participant.set_backing([])

        self.phase = RoomPhase.BRACKET
        await self.broadcast_room_state()
        await self.broadcast_phase({"bracket": bracket.bracket_view()})

    async def abort_race(self) -> None:
        self._aborting = True
        if self.race_task and not self.race_task.done():
            self.race_task.cancel()
            try:
                await self.race_task
            except (asyncio.CancelledError, Exception):
                pass
        self.race_task = None
        self.engine = None
        self.tournament = None
        self.phase = RoomPhase.LOBBY
        await self.broadcast_room_state()
        await self.broadcast_phase({})

    async def _sleep_or_skip(self, seconds: float) -> None:
        """Wait, unless the host skips ahead."""

        try:
            await asyncio.wait_for(self._skip.wait(), timeout=seconds)
        except TimeoutError:
            pass

    async def skip_ahead(self) -> None:
        """Host wants the ceremony over with; cut the current wait short."""

        if self.phase in (RoomPhase.CEREMONY, RoomPhase.PHOTO_FINISH):
            self._skip.set()

    # ------------------------------------------------------------------
    # Player intents
    # ------------------------------------------------------------------

    def apply_taps(self, participant_id: str, count: int, horse_id: int | None = None) -> None:
        if self.engine is not None:
            self.engine.apply_taps(
                participant_id, min(count, C.MAX_TAPS_PER_MESSAGE), horse_id
            )

    async def answer_challenge(self, participant_id: str, slot: int, choice: int) -> None:
        if self.engine is None:
            return
        correct, reason = self.engine.answer_challenge(participant_id, slot, choice)
        # Intents land between ticks, so their events must be flushed here.
        await self._dispatch_engine_events(self.engine.drain_events())
        if not correct and reason == "wrong":
            await self.send_to_participant(
                participant_id, {"t": ServerMessage.ERROR, "message": "wrong", "soft": True}
            )

    async def use_powerup(self, participant_id: str, slot: int, target: int | None) -> None:
        if self.engine is None:
            return
        ok, reason = self.engine.use_powerup(participant_id, slot, target)
        # The cast notification is emitted here, not inside a tick — flush it now
        # or the TV never hears about it.
        await self._dispatch_engine_events(self.engine.drain_events())
        if not ok and reason not in ("empty_slot", "race_not_running"):
            await self.send_to_participant(
                participant_id, {"t": ServerMessage.ERROR, "message": reason, "soft": True}
            )

    def mark_ready(self, participant_id: str, ready: bool) -> None:
        participant = self.participants.get(participant_id)
        if participant:
            participant.ready_for_rematch = ready


class RoomManager:
    """Creates and finds rooms; owns the shared session stats file."""

    def __init__(self, stats: SessionStats | None = None) -> None:
        self.rooms: dict[str, Room] = {}
        self.stats = stats or SessionStats.load()
        self._rng = random.SystemRandom()

    def create(self) -> Room:
        code = self._unique_code()
        room = Room(code, self)
        self.rooms[code] = room
        log.info("created room %s", code)
        return room

    def get(self, code: str) -> Room | None:
        return self.rooms.get(code.upper())

    def most_recent(self) -> Room | None:
        """Newest room, preferring one that still has people connected."""

        if not self.rooms:
            return None
        live = [room for room in self.rooms.values() if room.connections]
        pool = live or list(self.rooms.values())
        return max(pool, key=lambda room: room.created_at)

    def get_or_create(self, code: str | None) -> Room:
        if code:
            existing = self.get(code)
            if existing:
                return existing
        return self.create()

    def _unique_code(self) -> str:
        while True:
            code = "".join(
                self._rng.choice(C.ROOM_CODE_ALPHABET) for _ in range(C.ROOM_CODE_LENGTH)
            )
            if code not in self.rooms:
                return code

    def catalogs(self) -> dict[str, Any]:
        """Static reference data sent once per client in ``welcome`` (§18.3)."""

        return {
            "powerups": powerup_catalog(),
            "breeds": breed_catalog(),
            "tracks": track_catalog(),
            "modes": mode_catalog(),
            "reactions": REACTION_EMOJI,
            "targetClasses": {cls.value: cls.name for cls in TargetClass},
            "tuning": {
                "tapCap": C.TAP_TPS_CAP,
                "tapBatchMs": C.TAP_BATCH_INTERVAL_MS,
                "interpDelayMs": C.CLIENT_INTERPOLATION_DELAY_MS,
                "inventorySlots": C.INVENTORY_SLOTS,
                "snapshotRate": C.SNAPSHOT_RATE,
                "minOptions": C.MIN_OPTIONS,
                "maxOptions": C.MAX_OPTIONS,
                "minRaceSeconds": C.MIN_RACE_SECONDS,
                "maxRaceSeconds": C.MAX_RACE_SECONDS,
                "defaultRaceSeconds": C.DEFAULT_RACE_SECONDS,
                "minBet": C.BETTING_MIN_BET,
                "maxBackedHorses": C.MAX_BACKED_HORSES,
                "defaultBreed": DEFAULT_BREED_ID,
                "countdownSeconds": C.COUNTDOWN_SECONDS,
                "countdownNumbersSeconds": C.COUNTDOWN_NUMBERS_SECONDS,
                "challengeRetrySeconds": C.CHALLENGE_RETRY_SECONDS,
            },
        }

    def prune(self) -> None:
        """Drop rooms nobody has touched for hours."""

        cutoff = time.time() - C.ROOM_IDLE_TIMEOUT_S
        for code, room in list(self.rooms.items()):
            if room.created_at < cutoff and not room.connections:
                self.rooms.pop(code, None)
