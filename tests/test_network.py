"""Protocol and synchronisation tests over the real ASGI app.

These drive the actual FastAPI application and websocket handlers through
Starlette's test transport, so message shapes, host authority, reconnect
behaviour and the snapshot stream are all covered without opening a socket.
"""

from __future__ import annotations

import re

import pytest
from fastapi.testclient import TestClient

from server import constants as C
from server.app import app, manager
from server.breeds import BREEDS
from server.powerups import POWERUPS
from server.tracks import TRACKS


@pytest.fixture()
def client() -> TestClient:
    with TestClient(app) as test_client:
        yield test_client


@pytest.fixture()
def room(client: TestClient) -> dict:
    response = client.post("/api/rooms")
    assert response.status_code == 200
    return response.json()


def wait_for_gates(socket, limit: int = 400) -> None:
    """Block until the engine leaves the countdown — taps before that are ignored."""

    assert drain(socket, "snapshot", limit=limit, where=lambda msg: msg["ph"] == "running")


def drain(socket, wanted: str, limit: int = 400, where=None) -> dict | None:
    """Read frames until one of type ``wanted`` (optionally matching ``where``).

    Broadcasts queue up on a socket, so "the next room_state" is often a frame
    that predates the change under test. Tests that assert on a *result* pass a
    predicate and let this skip the stale ones.
    """

    for _ in range(limit):
        message = socket.receive_json()
        if message.get("t") != wanted:
            continue
        if where is None or where(message):
            return message
    return None


def horse_names(state: dict) -> list[str]:
    return [horse["name"] for horse in state["horses"]]


def collect(socket, count: int) -> list[dict]:
    return [socket.receive_json() for _ in range(count)]


def host_hello(room: dict) -> dict:
    return {"t": "hello", "room": room["code"], "role": "host", "host_token": room["hostToken"]}


def player_hello(room: dict, token: str | None = None) -> dict:
    return {"t": "hello", "room": room["code"], "role": "player", "token": token}


# ---------------------------------------------------------------------------
# HTTP surface
# ---------------------------------------------------------------------------


def test_pages_and_catalogs_are_served(client: TestClient):
    assert "dl-picker" in client.get("/").text
    assert client.get("/play").status_code == 200
    catalogs = client.get("/api/catalogs").json()
    assert len(catalogs["powerups"]) == len(POWERUPS)
    assert len(catalogs["breeds"]) == len(BREEDS)
    assert len(catalogs["tracks"]) == len(TRACKS)
    assert len(catalogs["modes"]) == 6
    assert catalogs["tuning"]["tapCap"] == C.TAP_TPS_CAP


def test_room_creation_returns_a_join_url(room: dict):
    assert re.fullmatch(r"[A-Z2-9]{4}", room["code"])
    assert room["joinUrl"].endswith(f"/play?room={room['code']}")
    assert room["hostToken"]


def test_qr_svg_tracks_the_request_host(client: TestClient, room: dict):
    response = client.get(
        f"/api/rooms/{room['code']}/qr.svg", headers={"x-forwarded-host": "tunnel.example.com"}
    )
    assert response.status_code == 200
    assert response.headers["content-type"].startswith("image/svg")
    assert response.text.lstrip().startswith("<svg")


def test_unknown_room_is_a_404(client: TestClient):
    assert client.get("/api/rooms/ZZZZ").status_code == 404
    assert client.get("/api/rooms/ZZZZ/qr.svg").status_code == 404


# ---------------------------------------------------------------------------
# Joining
# ---------------------------------------------------------------------------


def test_host_receives_catalogs_and_state(client: TestClient, room: dict):
    with client.websocket_connect("/ws") as socket:
        socket.send_json(host_hello(room))
        welcome = socket.receive_json()
        assert welcome["t"] == "welcome"
        assert welcome["isHost"] is True
        assert welcome["state"]["code"] == room["code"]
        assert welcome["catalogs"]["powerups"]


def test_player_join_lands_on_a_horse_and_notifies_the_room(client: TestClient, room: dict):
    with client.websocket_connect("/ws") as host:
        host.send_json(host_hello(room))
        drain(host, "welcome")

        with client.websocket_connect("/ws") as player:
            player.send_json(player_hello(room))
            drain(player, "welcome")
            player.send_json({"t": "join", "name": "Cullen", "horse_id": 0})
            welcome = drain(player, "welcome")
            assert welcome["you"]["name"] == "Cullen"
            assert welcome["you"]["horse_id"] == 0

            state = drain(
                host,
                "room_state",
                where=lambda msg: any(p["name"] == "Cullen" for p in msg["participants"]),
            )
            assert state is not None
            assert state["horses"][0]["backers"] == 1


def test_spectator_has_no_horse(client: TestClient, room: dict):
    with client.websocket_connect("/ws") as spectator:
        spectator.send_json({"t": "hello", "room": room["code"], "role": "spectator"})
        drain(spectator, "welcome")
        spectator.send_json({"t": "join", "name": "Watcher", "horse_id": None})
        welcome = drain(spectator, "welcome")
        assert welcome["you"]["role"] == "spectator"
        assert welcome["you"]["horse_id"] is None


def test_reconnect_with_token_restores_identity(client: TestClient, room: dict):
    with client.websocket_connect("/ws") as player:
        player.send_json(player_hello(room))
        drain(player, "welcome")
        player.send_json({"t": "join", "name": "Dana", "horse_id": 2})
        token = drain(player, "welcome")["token"]

    # New socket, same token: same seat, same horse (§7.4).
    with client.websocket_connect("/ws") as player:
        player.send_json(player_hello(room, token))
        welcome = drain(player, "welcome")
        assert welcome["token"] == token
        mine = [p for p in welcome["state"]["participants"] if p["id"] == token]
        assert mine and mine[0]["horse_id"] == 2
        assert mine[0]["connected"] is True


def test_bad_room_code_is_reported_as_fatal(client: TestClient):
    with client.websocket_connect("/ws") as socket:
        socket.send_json({"t": "hello", "room": "ZZZZ", "role": "player"})
        error = socket.receive_json()
        assert error["t"] == "error"
        assert error["fatal"] is True


# ---------------------------------------------------------------------------
# Host authority (§6.3)
# ---------------------------------------------------------------------------


def test_players_cannot_send_host_messages(client: TestClient, room: dict):
    live_room = manager.get(room["code"])
    original_options = list(live_room.options)
    with client.websocket_connect("/ws") as player:
        player.send_json(player_hello(room))
        drain(player, "welcome")
        player.send_json({"t": "host_config", "options": ["Hacked"]})
        player.send_json({"t": "ping", "ts": 1})
        assert drain(player, "pong") is not None
    assert live_room.options == original_options


def test_host_config_updates_the_room(client: TestClient, room: dict):
    with client.websocket_connect("/ws") as host:
        host.send_json(host_hello(room))
        drain(host, "welcome")
        host.send_json(
            {
                "t": "host_config",
                "options": ["Ramen", "Tacos", "Bagels"],
                "mode": "lightning",
                "track": "neon",
            }
        )
        state = drain(host, "room_state", where=lambda msg: horse_names(msg)[0] == "RAMEN")
        assert horse_names(state) == ["RAMEN", "TACOS", "BAGELS"]
        assert state["config"]["mode"] == "lightning"
        assert state["config"]["durationLocked"] is True


def test_duplicate_and_empty_options_are_cleaned(client: TestClient, room: dict):
    with client.websocket_connect("/ws") as host:
        host.send_json(host_hello(room))
        drain(host, "welcome")
        host.send_json({"t": "host_config", "options": ["Pizza", "pizza", "  ", "Sushi"]})
        state = drain(host, "room_state", where=lambda msg: len(msg["horses"]) == 2)
        assert horse_names(state) == ["PIZZA", "SUSHI"]


def test_emoji_override_survives_in_room_state(client: TestClient, room: dict):
    with client.websocket_connect("/ws") as host:
        host.send_json(host_hello(room))
        drain(host, "welcome")
        host.send_json({"t": "host_config", "options": ["Mystery Meal", "Soup"]})
        drain(host, "room_state", where=lambda msg: len(msg["horses"]) == 2)
        host.send_json({"t": "host_set_emoji", "horse_id": 0, "emoji": "🍔"})
        state = drain(host, "room_state", where=lambda msg: msg["horses"][0]["emoji"] == "🍔")
        assert state is not None


# ---------------------------------------------------------------------------
# The race stream (§7.3)
# ---------------------------------------------------------------------------


def test_race_streams_snapshots_and_reaches_running(client: TestClient, room: dict):
    with client.websocket_connect("/ws") as host:
        host.send_json(host_hello(room))
        drain(host, "welcome")
        host.send_json({"t": "host_config", "options": ["Pizza", "Sushi", "Tacos"], "duration": 20})
        drain(host, "room_state", where=lambda msg: len(msg["horses"]) == 3)

        with client.websocket_connect("/ws") as player:
            player.send_json(player_hello(room))
            drain(player, "welcome")
            player.send_json({"t": "join", "name": "Tapper", "horse_id": 1})
            drain(player, "welcome")

            host.send_json({"t": "host_start"})
            phase = drain(host, "phase")
            assert phase["phase"] == "racing"
            assert phase["data"]["track"]["theme"]
            assert phase["data"]["countdown"] == C.COUNTDOWN_SECONDS

            # Collect through the whole countdown (now 9s) and into the race.
            wanted = int((C.COUNTDOWN_SECONDS + 2) * C.SNAPSHOT_RATE)
            snapshots = []
            for _ in range(wanted * 3):
                message = host.receive_json()
                if message.get("t") == "snapshot":
                    snapshots.append(message)
                    if len(snapshots) >= wanted:
                        break

            assert len(snapshots) >= wanted
            first, last = snapshots[0], snapshots[-1]
            assert first["ph"] == "countdown"
            assert last["rt"] > first["rt"]
            assert {"i", "p", "v", "r", "st", "fx"} <= set(last["h"][0])
            assert len(last["o"]) == 3
            assert any(shot["ph"] == "running" for shot in snapshots)

            # Taps are accepted and reported back on the phone HUD channel.
            player.send_json({"t": "tap", "n": 6})
            hud = drain(player, "you", limit=400, where=lambda msg: msg["taps"] >= 6)
            assert hud is not None, "taps never reached the simulation"
            assert hud["horse_id"] == 1

            host.send_json({"t": "host_abort"})
            assert drain(host, "room_state", limit=400) is not None


def test_mid_race_joiners_cannot_claim_a_horse(client: TestClient, room: dict):
    with client.websocket_connect("/ws") as host:
        host.send_json(host_hello(room))
        drain(host, "welcome")
        host.send_json({"t": "host_config", "options": ["Pizza", "Sushi"], "duration": 20})
        drain(host, "room_state", where=lambda msg: len(msg["horses"]) == 2)
        host.send_json({"t": "host_start"})
        drain(host, "phase")

        with client.websocket_connect("/ws") as latecomer:
            latecomer.send_json(player_hello(room))
            drain(latecomer, "welcome")
            latecomer.send_json({"t": "join", "name": "Late", "horse_id": 0})
            welcome = drain(latecomer, "welcome")
            assert welcome["you"]["horse_id"] is None, "no bandwagoning mid-race (§7.5)"
            assert welcome["live"] is not None, "latecomers still get race context"
            assert welcome["live"]["snapshot"]["h"]

        host.send_json({"t": "host_abort"})
        drain(host, "room_state", limit=600, where=lambda msg: msg["phase"] == "lobby")


def test_reactions_are_broadcast_and_rate_limited(client: TestClient, room: dict):
    with client.websocket_connect("/ws") as host:
        host.send_json(host_hello(room))
        drain(host, "welcome")
        with client.websocket_connect("/ws") as player:
            player.send_json(player_hello(room))
            drain(player, "welcome")
            player.send_json({"t": "join", "name": "Loud", "horse_id": 0})
            drain(player, "welcome")
            drain(host, "room_state", where=lambda msg: bool(msg["participants"]))

            # Two reactions in quick succession: the second is rate limited away,
            # so the host sees exactly one before its own pong comes back.
            player.send_json({"t": "react", "emoji": "😂"})
            player.send_json({"t": "react", "emoji": "🔥"})
            host.send_json({"t": "ping", "ts": 2})
            frames = []
            for _ in range(20):
                message = host.receive_json()
                frames.append(message)
                if message.get("t") == "pong":
                    break
            reactions = [frame for frame in frames if frame.get("t") == "reaction"]
            assert [frame["emoji"] for frame in reactions] == ["😂"]


def test_unknown_message_types_are_ignored(client: TestClient, room: dict):
    with client.websocket_connect("/ws") as socket:
        socket.send_json(host_hello(room))
        drain(socket, "welcome")
        socket.send_json({"t": "from_the_future", "payload": 1})
        socket.send_json("not even an object")
        socket.send_json({"t": "ping", "ts": 3})
        assert drain(socket, "pong") is not None


# ---------------------------------------------------------------------------
# Backing several horses, and the tunnel URL
# ---------------------------------------------------------------------------


def test_a_player_can_back_several_horses_and_tap_each_of_them(
    client: TestClient, room: dict
):
    with client.websocket_connect("/ws") as host:
        host.send_json(host_hello(room))
        drain(host, "welcome")
        host.send_json({"t": "host_config", "options": ["Pizza", "Sushi", "Tacos", "Ramen"]})
        drain(host, "room_state", where=lambda msg: len(msg["horses"]) == 4)

        with client.websocket_connect("/ws") as player:
            player.send_json(player_hello(room))
            drain(player, "welcome")
            player.send_json({"t": "join", "name": "Spread", "horse_ids": [0, 2, 3]})
            welcome = drain(player, "welcome")
            assert welcome["you"]["horse_ids"] == [0, 2, 3]
            assert welcome["you"]["horse_id"] == 0

            state = drain(
                host, "room_state", where=lambda msg: msg["horses"][2]["backers"] == 1
            )
            backers = {horse["id"]: horse["backers"] for horse in state["horses"]}
            assert backers == {0: 1, 1: 0, 2: 1, 3: 1}

            host.send_json({"t": "host_start"})
            drain(host, "phase")
            wait_for_gates(player)

            # One button per horse: taps carry the horse they were meant for.
            player.send_json({"t": "tap", "n": 5, "horse_id": 2})
            player.send_json({"t": "tap", "n": 3, "horse_id": 3})
            hud = drain(player, "you", limit=500, where=lambda msg: msg["taps"] >= 8)
            assert hud is not None, "per-horse taps never reached the simulation"
            assert hud["horse_ids"] == [0, 2, 3]
            assert set(hud["rates"]) <= {"0", "2", "3"}

            host.send_json({"t": "host_abort"})
            drain(host, "room_state", limit=600, where=lambda msg: msg["phase"] == "lobby")


def test_backing_is_capped_by_the_server(client: TestClient, room: dict):
    with client.websocket_connect("/ws") as host:
        host.send_json(host_hello(room))
        drain(host, "welcome")
        host.send_json({"t": "host_config", "options": [f"Option {i}" for i in range(8)]})
        drain(host, "room_state", where=lambda msg: len(msg["horses"]) == 8)

        with client.websocket_connect("/ws") as player:
            player.send_json(player_hello(room))
            drain(player, "welcome")
            # Over the cap: pydantic rejects the frame outright, so nothing changes.
            player.send_json({"t": "join", "name": "Greedy", "horse_ids": [0, 1, 2, 3, 4]})
            player.send_json({"t": "join", "name": "Greedy", "horse_ids": [0, 1, 2, 3]})
            welcome = drain(player, "welcome")
            assert len(welcome["you"]["horse_ids"]) <= 4


def test_taps_for_a_horse_you_do_not_back_are_ignored(client: TestClient, room: dict):
    with client.websocket_connect("/ws") as host:
        host.send_json(host_hello(room))
        drain(host, "welcome")
        host.send_json({"t": "host_config", "options": ["Pizza", "Sushi", "Tacos"], "duration": 20})
        drain(host, "room_state", where=lambda msg: len(msg["horses"]) == 3)

        with client.websocket_connect("/ws") as player:
            player.send_json(player_hello(room))
            drain(player, "welcome")
            player.send_json({"t": "join", "name": "Cheeky", "horse_ids": [0]})
            drain(player, "welcome")
            host.send_json({"t": "host_start"})
            drain(host, "phase")
            wait_for_gates(player)

            player.send_json({"t": "tap", "n": 9, "horse_id": 2})
            player.send_json({"t": "ping", "ts": 9})
            assert drain(player, "pong", limit=500) is not None
            live_room = manager.get(room["code"])
            racer = live_room.engine.players[
                [p.id for p in live_room.participants.values() if p.name == "Cheeky"][0]
            ]
            assert racer.taps_total == 0, "taps for an unbacked horse must not count"

            host.send_json({"t": "host_abort"})
            drain(host, "room_state", limit=600, where=lambda msg: msg["phase"] == "lobby")


def test_qr_follows_the_host_supplied_tunnel_url(client: TestClient, room: dict):
    with client.websocket_connect("/ws") as host:
        host.send_json(host_hello(room))
        drain(host, "welcome")
        host.send_json({"t": "host_config", "public_url": "tunnel.example.com"})
        state = drain(host, "room_state", where=lambda msg: msg["config"]["publicUrl"])
        assert state["config"]["publicUrl"] == "tunnel.example.com"

    live_room = manager.get(room["code"])
    encoded = live_room.join_url(fallback="http://localhost:8000/play?room=XXXX")
    assert encoded == f"https://tunnel.example.com/play?room={room['code']}"
    assert client.get(f"/api/rooms/{room['code']}/qr.svg").status_code == 200


def test_qr_scales_because_it_carries_a_viewbox(client: TestClient, room: dict):
    """Without a viewBox the code renders tiny in the corner of the card."""

    svg = client.get(f"/api/rooms/{room['code']}/qr.svg").text
    assert "viewBox=" in svg
    assert 'light="' not in svg or "#ffffff" in svg


def test_a_phone_with_no_code_is_pointed_at_the_live_room(client: TestClient, room: dict):
    active = client.get("/api/rooms/active").json()
    assert active["code"] == room["code"]


# ---------------------------------------------------------------------------
# The unlock gate over the wire
# ---------------------------------------------------------------------------


def test_a_powerup_arrives_locked_and_unlocks_by_answering(client: TestClient, room: dict):
    """The full loop: grant → challenge → answer → armed → fire."""

    live_room = manager.get(room["code"])
    with client.websocket_connect("/ws") as host:
        host.send_json(host_hello(room))
        drain(host, "welcome")
        host.send_json({"t": "host_config", "options": ["Pizza", "Sushi", "Tacos"], "duration": 30})
        drain(host, "room_state", where=lambda msg: len(msg["horses"]) == 3)

        with client.websocket_connect("/ws") as player:
            player.send_json(player_hello(room))
            drain(player, "welcome")
            player.send_json({"t": "join", "name": "Gated", "horse_ids": [0]})
            drain(player, "welcome")
            host.send_json({"t": "host_start"})
            drain(host, "phase")
            wait_for_gates(player)

            grant = drain(player, "grant", limit=1200)
            assert grant is not None, "no item ever dropped"
            assert grant["challenge"] is not None, "items must arrive locked"
            assert "answer_index" not in grant["challenge"]

            slot = grant["slot"]
            # Firing a locked item does nothing but tell the phone why.
            player.send_json({"t": "use_powerup", "slot": slot})
            racer = live_room.engine.players[
                [p.id for p in live_room.participants.values() if p.name == "Gated"][0]
            ]
            assert racer.inventory[slot].powerup_id is not None, "a locked item is not consumed"

            challenge = racer.inventory[slot].challenge
            if challenge.is_pace:
                # Pace tasks are judged from the tap stream, not a button.
                for _ in range(60):
                    player.send_json({"t": "tap", "n": 1, "horse_id": 0})
                unlocked = drain(
                    player,
                    "inventory",
                    limit=1500,
                    where=lambda msg: bool(msg["inventory"][slot])
                    and msg["inventory"][slot]["armed"],
                )
            else:
                player.send_json(
                    {"t": "answer", "slot": slot, "choice": challenge.answer_index}
                )
                unlocked = drain(
                    player,
                    "inventory",
                    limit=1200,
                    where=lambda msg: bool(msg["inventory"][slot])
                    and msg["inventory"][slot]["armed"],
                )
            assert unlocked is not None, "a correct answer must arm the item"

            host.send_json({"t": "host_abort"})
            drain(host, "room_state", limit=900, where=lambda msg: msg["phase"] == "lobby")


def test_catalogs_tell_the_client_what_each_item_does(client: TestClient):
    catalogs = client.get("/api/catalogs").json()
    for powerup in catalogs["powerups"]:
        assert powerup["polarityIcon"], powerup["id"]
        assert powerup["scopeLabel"], powerup["id"]
        assert powerup["polarity"] in {"good", "bad", "protect", "chaos"}
    tuning = catalogs["tuning"]
    assert tuning["countdownSeconds"] >= 6, "the primer needs time to be read"
    assert tuning["countdownNumbersSeconds"] < tuning["countdownSeconds"]
    assert tuning["challengeRetrySeconds"] > 0


def test_a_cast_reaches_the_display_as_a_notification(client: TestClient, room: dict):
    """"Cullen used Turbo Boost on PIZZA" is the game's main social hook.

    It is emitted by an intent that arrives between ticks, which is exactly the
    path that used to drop its events on the floor.
    """

    live_room = manager.get(room["code"])
    with client.websocket_connect("/ws") as host:
        host.send_json(host_hello(room))
        drain(host, "welcome")
        host.send_json({"t": "host_config", "options": ["Pizza", "Sushi", "Tacos"], "duration": 30})
        drain(host, "room_state", where=lambda msg: len(msg["horses"]) == 3)

        with client.websocket_connect("/ws") as player:
            player.send_json(player_hello(room))
            drain(player, "welcome")
            player.send_json({"t": "join", "name": "Caster", "horse_ids": [0]})
            drain(player, "welcome")
            host.send_json({"t": "host_start"})
            drain(host, "phase")
            wait_for_gates(host)

            # Hand the player an unlocked item directly: the gate has its own tests.
            racer = live_room.engine.players[
                [p.id for p in live_room.participants.values() if p.name == "Caster"][0]
            ]
            held = racer.inventory[0]
            held.clear()
            held.powerup_id = "turbo_boost"
            held.armed = True

            player.send_json({"t": "use_powerup", "slot": 0})
            notify = drain(host, "notify", limit=900)
            assert notify is not None, "the cast never reached the display"
            assert notify["player"] == "Caster"
            assert notify["powerup"] == "Turbo Boost"
            assert notify["polarity"] if "polarity" in notify else True

            host.send_json({"t": "host_abort"})
            drain(host, "room_state", limit=900, where=lambda msg: msg["phase"] == "lobby")
