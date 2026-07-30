"""WebSocket wire protocol for dl-picker (SPEC.md §7.2).

Client→server messages are validated with pydantic models; unknown message
types are ignored so old clients never hard-fail against a newer server.

Server→client messages are built as plain dicts by :mod:`server.rooms` — they
are on the hot path (20 snapshots/second) and need no validation on the way
out. Message *type* names live here as constants so both directions share one
vocabulary.
"""

from __future__ import annotations

from typing import Annotated, Any, Literal

from pydantic import BaseModel, Field, ValidationError

from .constants import (
    MAX_BACKED_HORSES,
    MAX_PLAYER_NAME_LENGTH,
    MAX_TAPS_PER_MESSAGE,
    ROOM_CODE_LENGTH,
)

# ---------------------------------------------------------------------------
# Server → client message types
# ---------------------------------------------------------------------------


class ServerMessage:
    """Type tags for outbound messages."""

    WELCOME = "welcome"
    ROOM_STATE = "room_state"
    SNAPSHOT = "snapshot"
    PHASE = "phase"
    EVENT = "event"
    NOTIFY = "notify"
    GRANT = "grant"
    UNLOCKED = "unlocked"
    INVENTORY = "inventory"
    YOU = "you"
    RESULT = "result"
    COMMENTARY = "commentary"
    REACTION = "reaction"
    INTEL = "intel"
    ERROR = "error"
    PONG = "pong"
    KICKED = "kicked"


class ClientRole:
    HOST = "host"
    PLAYER = "player"
    SPECTATOR = "spectator"


# ---------------------------------------------------------------------------
# Client → server models
# ---------------------------------------------------------------------------

RoomCode = Annotated[str, Field(min_length=ROOM_CODE_LENGTH, max_length=ROOM_CODE_LENGTH)]


class Hello(BaseModel):
    """First frame on every socket. ``token`` re-attaches a known identity."""

    t: Literal["hello"]
    room: RoomCode
    role: Literal["host", "player", "spectator"] = ClientRole.SPECTATOR
    token: str | None = None
    host_token: str | None = None


class Join(BaseModel):
    """Claim a name and (optionally) some horses.

    No horses at all → spectator. ``horse_ids`` may name up to
    ``MAX_BACKED_HORSES`` options; ``horse_id`` is the one taps should feed.
    """

    t: Literal["join"]
    name: str = Field(min_length=1, max_length=MAX_PLAYER_NAME_LENGTH)
    horse_id: int | None = None
    horse_ids: list[int] | None = Field(default=None, max_length=MAX_BACKED_HORSES)


class Tap(BaseModel):
    """A batch of taps accumulated on the phone since the last send.

    ``horse_id`` names which of the player's horses the taps were for — each
    tap button on the phone reports its own horse.
    """

    t: Literal["tap"]
    n: int = Field(ge=1, le=MAX_TAPS_PER_MESSAGE)
    horse_id: int | None = None


class UsePowerup(BaseModel):
    t: Literal["use_powerup"]
    slot: int = Field(ge=0, le=3)
    target_horse_id: int | None = None


class AnswerChallenge(BaseModel):
    """Unlock a powerup by answering its challenge."""

    t: Literal["answer"]
    slot: int = Field(ge=0, le=3)
    choice: int = Field(ge=0, le=7)


class React(BaseModel):
    t: Literal["react"]
    emoji: str = Field(min_length=1, max_length=8)


class Bet(BaseModel):
    t: Literal["bet"]
    horse_id: int
    amount: int = Field(ge=0)


class HostConfig(BaseModel):
    """Lobby configuration. Every field is optional — send only what changed."""

    t: Literal["host_config"]
    options: list[str] | None = None
    mode: str | None = None
    track: str | None = None
    duration: float | None = None
    powerups_on: bool | None = None
    events_on: bool | None = None
    public_url: str | None = None
    """Origin phones should use (a tunnel address); empty string clears it."""


class HostAction(BaseModel):
    t: Literal["host_start", "host_restart", "host_abort", "host_skip"]


class HostKick(BaseModel):
    t: Literal["host_kick"]
    player_id: str


class HostSetEmoji(BaseModel):
    t: Literal["host_set_emoji"]
    horse_id: int
    emoji: str = Field(min_length=1, max_length=8)


class HostSetBreed(BaseModel):
    """Choose which animal a dinner option runs as."""

    t: Literal["host_set_breed"]
    horse_id: int
    breed: str = Field(min_length=1, max_length=32)


class PlayerReady(BaseModel):
    """Phone toggle telling the host who is up for a rematch."""

    t: Literal["ready"]
    ready: bool = True


class Ping(BaseModel):
    t: Literal["ping"]
    ts: float | None = None


CLIENT_MESSAGE_MODELS: dict[str, type[BaseModel]] = {
    "hello": Hello,
    "join": Join,
    "tap": Tap,
    "use_powerup": UsePowerup,
    "answer": AnswerChallenge,
    "react": React,
    "bet": Bet,
    "ready": PlayerReady,
    "host_config": HostConfig,
    "host_start": HostAction,
    "host_restart": HostAction,
    "host_abort": HostAction,
    "host_skip": HostAction,
    "host_kick": HostKick,
    "host_set_emoji": HostSetEmoji,
    "host_set_breed": HostSetBreed,
    "ping": Ping,
}

HOST_MESSAGE_TYPES = frozenset(
    {
        "host_config",
        "host_start",
        "host_restart",
        "host_abort",
        "host_skip",
        "host_kick",
        "host_set_emoji",
        "host_set_breed",
    }
)


def parse_client_message(raw: Any) -> BaseModel | None:
    """Validate a decoded JSON frame.

    Returns ``None`` for anything unrecognised or malformed; callers simply
    ignore those frames (forward/backward compatibility, §7.2).
    """

    if not isinstance(raw, dict):
        return None
    model = CLIENT_MESSAGE_MODELS.get(raw.get("t"))
    if model is None:
        return None
    try:
        return model.model_validate(raw)
    except ValidationError:
        return None
