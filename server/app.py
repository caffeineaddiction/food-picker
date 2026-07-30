"""FastAPI application: static hosting, QR codes and the websocket endpoint.

One process serves the display, the phone controller and the realtime protocol
(SPEC.md §8). There is no build step: ``static/`` is shipped as-is.
"""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any

import segno
from fastapi import FastAPI, Request, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse, JSONResponse, Response
from fastapi.staticfiles import StaticFiles

from .protocol import (
    HOST_MESSAGE_TYPES,
    AnswerChallenge,
    Bet,
    ClientRole,
    Hello,
    HostConfig,
    HostKick,
    HostSetBreed,
    HostSetEmoji,
    Join,
    Ping,
    PlayerReady,
    React,
    ServerMessage,
    Tap,
    UsePowerup,
    parse_client_message,
)
from .rooms import Connection, Room, RoomManager, RoomPhase
from .stats import SessionStats
from .tracks import get_track

log = logging.getLogger(__name__)

STATIC_DIR = Path(__file__).resolve().parent.parent / "static"

manager = RoomManager(SessionStats.load())

app = FastAPI(title="dl-picker", docs_url=None, redoc_url=None)


# ---------------------------------------------------------------------------
# Page routes
# ---------------------------------------------------------------------------


@app.get("/")
async def display_page() -> FileResponse:
    """The main display — the hero screen for the office TV."""

    return FileResponse(STATIC_DIR / "display" / "index.html")


@app.get("/play")
async def play_page() -> FileResponse:
    """The phone controller."""

    return FileResponse(STATIC_DIR / "play" / "index.html")


@app.get("/healthz")
async def healthz() -> JSONResponse:
    return JSONResponse({"ok": True, "rooms": len(manager.rooms)})


# ---------------------------------------------------------------------------
# API
# ---------------------------------------------------------------------------


def public_origin(request: Request | WebSocket) -> str:
    """Best guess at the URL phones should use, from the incoming request.

    Honours ``X-Forwarded-*`` so a Cloudflare tunnel "just works" without any
    configuration: whatever host the display was loaded from is what the QR code
    encodes (§7.6).
    """

    headers = request.headers
    forwarded_host = headers.get("x-forwarded-host") or headers.get("host")
    scheme = headers.get("x-forwarded-proto")
    if scheme is None:
        url_scheme = request.url.scheme
        scheme = "https" if url_scheme in ("https", "wss") else "http"
    if not forwarded_host:  # pragma: no cover - defensive
        forwarded_host = f"{request.url.hostname}:{request.url.port or 8000}"
    return f"{scheme}://{forwarded_host.split(',')[0].strip()}"


def join_url(request: Request | WebSocket, code: str) -> str:
    return f"{public_origin(request)}/play?room={code}"


@app.post("/api/rooms")
async def create_room(request: Request) -> JSONResponse:
    """Called by the display when the host starts a new race night."""

    room = manager.create()
    manager.prune()
    return JSONResponse(
        {
            "code": room.code,
            "hostToken": room.host_token,
            "joinUrl": join_url(request, room.code),
        }
    )


@app.get("/api/rooms/active")
async def active_room(request: Request) -> JSONResponse:
    """The room a phone should join when it arrives without a code.

    People type the tunnel URL by hand instead of scanning, and an office runs
    one room at a time — so guessing is right far more often than it is wrong,
    and the phone still offers manual code entry when it isn't.
    """

    room = manager.most_recent()
    if room is None:
        return JSONResponse({"error": "no_active_room"}, status_code=404)
    return JSONResponse(
        {"code": room.code, "phase": room.phase.value, "joinUrl": join_url(request, room.code)}
    )


@app.get("/api/rooms/{code}")
async def room_info(code: str, request: Request) -> JSONResponse:
    room = manager.get(code)
    if room is None:
        return JSONResponse({"error": "no_such_room"}, status_code=404)
    return JSONResponse(
        {
            "code": room.code,
            "phase": room.phase.value,
            "joinUrl": join_url(request, room.code),
            "horses": room.room_state()["horses"],
        }
    )


QR_QUIET_ZONE = 4
"""Modules of white border. Four is the QR spec minimum for reliable scanning."""


@app.get("/api/rooms/{code}/qr.svg")
async def room_qr(code: str, request: Request) -> Response:
    """Inline SVG QR for the lobby screen (§18.3).

    segno emits ``width``/``height`` but no ``viewBox``, and an SVG without a
    viewBox does not scale: CSS stretches the viewport while the code stays at
    its intrinsic 33px in the top-left corner. Injecting the viewBox is what
    makes it fill the card — and a code that fills the card is a code that
    scans from across the room.
    """

    room = manager.get(code)
    if room is None:
        return Response(status_code=404)
    qr = segno.make(room.join_url(fallback=join_url(request, room.code)), error="m")
    modules = qr.symbol_size(scale=1, border=QR_QUIET_ZONE)[0]
    svg = qr.svg_inline(
        scale=1,
        border=QR_QUIET_ZONE,
        dark="#101120",
        light="#ffffff",  # scanners need a real quiet zone, not transparency
    )
    svg = svg.replace(
        "<svg ",
        f'<svg viewBox="0 0 {modules} {modules}" preserveAspectRatio="xMidYMid meet" ',
        1,
    )
    return Response(content=svg, media_type="image/svg+xml", headers={"Cache-Control": "no-store"})


@app.get("/api/catalogs")
async def catalogs() -> JSONResponse:
    return JSONResponse(manager.catalogs())


@app.get("/api/stats")
async def stats() -> JSONResponse:
    return JSONResponse(manager.stats.leaderboard(limit=10))


# ---------------------------------------------------------------------------
# WebSocket
# ---------------------------------------------------------------------------


@app.websocket("/ws")
async def websocket_endpoint(socket: WebSocket) -> None:
    """One socket per client. First frame must be ``hello`` (§7.2)."""

    await socket.accept()
    session = ClientSession(socket)
    try:
        await session.run()
    except WebSocketDisconnect:
        pass
    except Exception:  # pragma: no cover - never take the server down
        log.exception("websocket error")
    finally:
        await session.cleanup()


class ClientSession:
    """Handles one websocket: identity, routing and teardown."""

    def __init__(self, socket: WebSocket) -> None:
        self.socket = socket
        self.room: Room | None = None
        self.connection: Connection | None = None
        self.participant_id: str | None = None
        self.is_host = False

    async def run(self) -> None:
        while True:
            raw = await self.socket.receive_text()
            try:
                decoded = json.loads(raw)
            except json.JSONDecodeError:
                continue
            message = parse_client_message(decoded)
            if message is None:
                continue
            await self.handle(message)

    async def cleanup(self) -> None:
        if self.room and self.connection:
            await self.room.detach(self.connection.id)

    # ------------------------------------------------------------------

    async def handle(self, message: Any) -> None:
        if isinstance(message, Hello):
            await self.handle_hello(message)
            return
        if self.room is None or self.connection is None:
            return
        room = self.room

        if isinstance(message, Ping):
            await self.send({"t": ServerMessage.PONG, "ts": message.ts})
            return

        message_type = getattr(message, "t", None)
        if message_type in HOST_MESSAGE_TYPES and not self.is_host:
            return

        if isinstance(message, Join):
            await self.handle_join(message)
        elif isinstance(message, Tap):
            if self.participant_id:
                room.apply_taps(self.participant_id, message.n, message.horse_id)
        elif isinstance(message, UsePowerup):
            if self.participant_id:
                await room.use_powerup(self.participant_id, message.slot, message.target_horse_id)
        elif isinstance(message, AnswerChallenge):
            if self.participant_id:
                await room.answer_challenge(self.participant_id, message.slot, message.choice)
        elif isinstance(message, React):
            if self.participant_id:
                await room.react(self.participant_id, message.emoji)
        elif isinstance(message, Bet):
            if self.participant_id and room.place_bet(
                self.participant_id, message.horse_id, message.amount
            ):
                await room.broadcast_room_state()
        elif isinstance(message, PlayerReady):
            if self.participant_id:
                room.mark_ready(self.participant_id, message.ready)
                await room.broadcast_room_state()
        elif isinstance(message, HostConfig):
            room.apply_config(message)
            await room.broadcast_room_state()
        elif isinstance(message, HostSetEmoji):
            room.set_emoji(message.horse_id, message.emoji)
            await room.broadcast_room_state()
        elif isinstance(message, HostSetBreed):
            room.set_breed(message.horse_id, message.breed)
            await room.broadcast_room_state()
        elif isinstance(message, HostKick):
            room.kick(message.player_id)
            await room.send_to_participant(message.player_id, {"t": ServerMessage.KICKED})
            await room.broadcast_room_state()
        elif message_type == "host_start":
            await room.start_race()
        elif message_type == "host_restart":
            await room.abort_race()
            await room.start_race()
        elif message_type == "host_abort":
            await room.abort_race()
        elif message_type == "host_skip":
            await room.skip_ahead()

    async def handle_hello(self, message: Hello) -> None:
        room = manager.get(message.room)
        if room is None:
            await self.send({"t": ServerMessage.ERROR, "message": "no_such_room", "fatal": True})
            return
        self.room = room
        self.is_host = bool(message.host_token) and message.host_token == room.host_token
        role = ClientRole.HOST if self.is_host else message.role
        self.connection = Connection(
            id=f"c{id(self.socket)}", socket=self.socket, role=role
        )
        room.attach(self.connection)

        # A known token re-attaches an existing identity (§7.4).
        if message.token and message.token in room.participants:
            participant = room.participants[message.token]
            participant.connected = True
            self.participant_id = participant.id
            self.connection.participant_id = participant.id

        await self.send(
            {
                "t": ServerMessage.WELCOME,
                "code": room.code,
                "role": role,
                "token": self.participant_id,
                "isHost": self.is_host,
                "catalogs": manager.catalogs(),
                "state": room.room_state(),
                "live": await self.live_race_payload(room),
            }
        )
        await room.broadcast_room_state()

    async def live_race_payload(self, room: Room) -> dict[str, Any] | None:
        """Enough context for a client that arrives mid-race (§7.5)."""

        if room.engine is None or room.phase not in (
            RoomPhase.RACING,
            RoomPhase.PHOTO_FINISH,
            RoomPhase.CEREMONY,
        ):
            return None

        engine = room.engine
        return {
            "track": get_track(engine.config.track_id).client_meta(),
            "mode": room.mode().client_meta(),
            "duration": engine.config.duration,
            "trackLength": engine.config.track_length,
            "label": engine.config.label,
            "snapshot": engine.snapshot(),
            "raceNumber": room.race_number,
        }

    async def handle_join(self, message: Join) -> None:
        room = self.room
        if room is None or self.connection is None:
            return
        participant = room.join(
            name=message.name,
            horse_id=message.horse_id,
            horse_ids=message.horse_ids,
            participant_id=self.participant_id,
            as_host=self.is_host and message.horse_id is None and not message.horse_ids,
        )
        self.participant_id = participant.id
        self.connection.participant_id = participant.id
        if room.engine is not None:
            room.engine.recount_backers()
        await self.send(
            {
                "t": ServerMessage.WELCOME,
                "code": room.code,
                "role": participant.role,
                "token": participant.id,
                "isHost": self.is_host,
                "you": participant.public(),
                "state": room.room_state(),
                "catalogs": manager.catalogs(),
                "live": await self.live_race_payload(room),
            }
        )
        await room.broadcast_room_state()

    async def send(self, message: dict[str, Any]) -> None:
        try:
            await self.socket.send_text(json.dumps(message, separators=(",", ":")))
        except Exception:  # pragma: no cover - socket teardown races
            pass


# Mounted last so the explicit routes above win.
app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")
