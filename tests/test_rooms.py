"""Room orchestration: rosters, betting maths, brackets, free agents."""

from __future__ import annotations

import pytest

from server import constants as C
from server.breeds import BREEDS
from server.powerups import POWERUPS
from server.rooms import Participant, Room, RoomManager, RoomPhase
from server.stats import SessionStats
from server.tracks import TRACKS


@pytest.fixture()
def room(tmp_path) -> Room:
    manager = RoomManager(SessionStats(path=tmp_path / "stats.json"))
    created = manager.create()
    created.options = ["Pizza", "Sushi", "Tacos", "Ramen"]
    return created


def add_player(room: Room, name: str, horse_id: int | None, **kwargs) -> Participant:
    return room.join(
        name=name, horse_id=horse_id, participant_id=None, as_host=False, **kwargs
    )


class ConfigPatch:
    """Stand-in for the HostConfig model: every field optional, default None."""

    FIELDS = ("options", "mode", "track", "duration", "powerups_on", "events_on", "public_url")

    def __init__(self, **overrides):
        for name in self.FIELDS:
            setattr(self, name, overrides.get(name))


def config_patch(**overrides) -> ConfigPatch:
    return ConfigPatch(**overrides)


# ---------------------------------------------------------------------------
# Codes and roster
# ---------------------------------------------------------------------------


def test_room_codes_avoid_ambiguous_characters():
    manager = RoomManager()
    codes = {manager.create().code for _ in range(60)}
    assert len(codes) == 60, "codes must be unique"
    for code in codes:
        assert len(code) == C.ROOM_CODE_LENGTH
        assert not set(code) & set("O01I"), f"{code} contains a lookalike character"


def test_joining_counts_backers_per_horse(room: Room):
    add_player(room, "Cullen", 0)
    add_player(room, "Dana", 0)
    add_player(room, "Sam", 2)
    add_player(room, "Watcher", None)
    horses = {horse["name"]: horse for horse in room.room_state()["horses"]}
    assert horses["PIZZA"]["backers"] == 2
    assert horses["TACOS"]["backers"] == 1
    assert horses["SUSHI"]["backers"] == 0
    assert len(room.players()) == 3


def test_editing_the_menu_drops_stale_horse_picks(room: Room):
    player = add_player(room, "Cullen", 3)
    assert player.horse_id == 3

    room.apply_config(config_patch(options=["Pizza", "Sushi"]))
    assert player.horse_id is None, "a removed horse must not linger on a player"


def test_names_are_trimmed_to_the_limit(room: Room):
    player = add_player(room, "A" * 40, 0)
    assert len(player.name) <= C.MAX_PLAYER_NAME_LENGTH


def test_reusing_a_token_keeps_one_seat(room: Room):
    first = add_player(room, "Cullen", 0)
    again = room.join(name="Cullen", horse_id=1, participant_id=first.id, as_host=False)
    assert again is first
    assert len(room.participants) == 1
    assert again.horse_id == 1


def test_kick_removes_a_participant(room: Room):
    player = add_player(room, "Rude", 0)
    room.kick(player.id)
    assert player.id not in room.participants


# ---------------------------------------------------------------------------
# Horse locking (§7.5, §11.3)
# ---------------------------------------------------------------------------


def test_horses_lock_during_a_race(room: Room):
    room.phase = RoomPhase.RACING
    latecomer = add_player(room, "Late", 0)
    assert latecomer.horse_id is None


def test_seats_reopen_after_a_race(room: Room):
    """Somebody who wanders in after a race must be able to ride the rematch."""

    room.phase = RoomPhase.RESULTS
    latecomer = add_player(room, "Nextround", 1)
    assert latecomer.horse_id == 1
    assert room.can_start(), "the rematch must be startable straight from results"


def test_eliminated_backers_may_re_pick_in_last_bite(room: Room):
    room.mode_id = "last_bite"
    room.phase = RoomPhase.RACING
    freed = Participant(id="free", name="Freed", horse_id=None)
    room.participants[freed.id] = freed
    room.join(name="Freed", horse_id=2, participant_id=freed.id, as_host=False)
    assert freed.horse_id == 2, "free agents are the documented exception (§11.3)"

    still_racing = add_player(room, "Busy", 1)
    still_racing.horse_id = 1
    room.join(name="Busy", horse_id=3, participant_id=still_racing.id, as_host=False)
    assert still_racing.horse_id == 1, "players with a live horse stay put"


# ---------------------------------------------------------------------------
# Betting (§11.5)
# ---------------------------------------------------------------------------


def test_bets_only_land_during_the_window(room: Room):
    punter = add_player(room, "Punter", 0)
    assert room.place_bet(punter.id, 0, 200) is False
    room.mode_id = "punters"
    room.phase = RoomPhase.BETTING
    assert room.place_bet(punter.id, 0, 200) is True
    assert punter.bankroll == C.BETTING_STARTING_BANKROLL - 200


def test_changing_a_bet_refunds_the_previous_stake(room: Room):
    room.mode_id = "punters"
    room.phase = RoomPhase.BETTING
    punter = add_player(room, "Punter", 0)
    room.place_bet(punter.id, 0, 300)
    room.place_bet(punter.id, 1, 100)
    assert punter.pending_bet == (1, 100)
    assert punter.bankroll == C.BETTING_STARTING_BANKROLL - 100
    assert room.betting_pool == {0: 0, 1: 100}


def test_bets_are_capped_by_the_bankroll_and_a_minimum(room: Room):
    room.mode_id = "punters"
    room.phase = RoomPhase.BETTING
    punter = add_player(room, "Punter", 0)
    assert room.place_bet(punter.id, 0, C.BETTING_MIN_BET - 1) is False
    assert room.place_bet(punter.id, 0, 99_999) is True
    assert punter.bankroll == 0
    assert punter.pending_bet == (0, C.BETTING_STARTING_BANKROLL)


def test_pari_mutuel_payout_splits_the_whole_pool(room: Room):
    room.mode_id = "punters"
    room.phase = RoomPhase.BETTING
    winner = add_player(room, "Lucky", 0)
    loser_a = add_player(room, "Unlucky", 1)
    loser_b = add_player(room, "Alsobad", 2)
    room.place_bet(winner.id, 0, 100)
    room.place_bet(loser_a.id, 1, 200)
    room.place_bet(loser_b.id, 2, 100)

    payouts = room.settle_bets(0)
    # 400 in the pool, one 100 stake on the winner → the lot comes back.
    assert winner.bankroll == C.BETTING_STARTING_BANKROLL - 100 + 400
    assert loser_a.bankroll == C.BETTING_STARTING_BANKROLL - 200
    assert payouts[0]["hit"] is True
    assert room.betting_pool == {}


def test_nobody_backed_the_winner_means_no_payout(room: Room):
    room.mode_id = "punters"
    room.phase = RoomPhase.BETTING
    punter = add_player(room, "Punter", 1)
    room.place_bet(punter.id, 1, 500)
    room.settle_bets(0)
    assert punter.bankroll == C.BETTING_STARTING_BANKROLL - 500


def test_odds_are_published_for_every_horse(room: Room):
    room.mode_id = "punters"
    room.phase = RoomPhase.BETTING
    punter = add_player(room, "Punter", 0)
    room.place_bet(punter.id, 0, 500)
    view = room.betting_view()
    assert view is not None
    assert set(view["odds"]) == {"0", "1", "2", "3"}
    assert view["odds"]["0"] >= 1.0
    assert view["total"] == 500


def test_non_betting_modes_have_no_betting_view(room: Room):
    assert room.betting_view() is None


# ---------------------------------------------------------------------------
# Tournament brackets (§11.4)
# ---------------------------------------------------------------------------


def test_small_fields_run_a_single_heat(room: Room):
    room.mode_id = "tournament"
    room.options = ["Pizza", "Sushi", "Tacos"]
    bracket = room._build_bracket()
    assert len(bracket.heats) == 1
    assert bracket.label() == "HEAT 1 of 1"


def test_large_fields_split_into_balanced_heats(room: Room):
    room.mode_id = "tournament"
    room.options = [f"Option {index}" for index in range(12)]
    bracket = room._build_bracket()
    assert len(bracket.heats) == 3
    assert sorted(len(heat) for heat in bracket.heats) == [4, 4, 4]
    assert sum(len(heat) for heat in bracket.heats) == 12


def test_bracket_view_is_serialisable(room: Room):
    room.mode_id = "tournament"
    room.options = [f"Option {index}" for index in range(8)]
    view = room._build_bracket().bracket_view()
    assert {"heats", "heatIndex", "winners", "final", "label"} <= set(view)


def test_tournament_horses_come_from_the_current_heat(room: Room):
    room.mode_id = "tournament"
    room.options = [f"Option {index}" for index in range(8)]
    room.tournament = room._build_bracket()
    names = room.horse_specs_names()
    assert len(names) == len(room.tournament.heats[0])


# ---------------------------------------------------------------------------
# Reference data
# ---------------------------------------------------------------------------


def test_catalogs_expose_everything_the_clients_need():
    catalogs = RoomManager().catalogs()
    assert len(catalogs["powerups"]) == len(POWERUPS)
    assert len(catalogs["breeds"]) == len(BREEDS)
    assert len(catalogs["tracks"]) == len(TRACKS)
    assert len(catalogs["modes"]) == 6
    assert catalogs["reactions"]
    assert catalogs["tuning"]["inventorySlots"] == C.INVENTORY_SLOTS


def test_stats_round_trip(tmp_path):
    stats = SessionStats(path=tmp_path / "stats.json")
    stats.record_race(
        {
            "winner": "PIZZA",
            "winner_id": 0,
            "order": [{"name": "PIZZA"}, {"name": "SUSHI"}],
            "players": [
                {
                    "player_id": "p1",
                    "name": "Cullen",
                    "horse_id": 0,
                    "taps": 120,
                    "powerups_used": 3,
                }
            ],
        },
        mode="classic",
        track="churchill",
    )
    reloaded = SessionStats.load(tmp_path / "stats.json")
    assert reloaded.races == 1
    assert reloaded.food_wins["PIZZA"] == 1
    assert reloaded.player_taps["Cullen"] == 120
    assert reloaded.player_wins["Cullen"] == 1
    board = reloaded.leaderboard()
    assert board["topFoods"][0]["name"] == "PIZZA"


# ---------------------------------------------------------------------------
# Backing several horses at once
# ---------------------------------------------------------------------------


def test_a_player_may_back_up_to_four_horses(room: Room):
    room.options = [f"Option {index}" for index in range(8)]
    player = add_player(room, "Greedy", None, horse_ids=[0, 1, 2, 3, 4, 5])
    assert player.backed_horse_ids == [0, 1, 2, 3], "capped at MAX_BACKED_HORSES"
    assert player.horse_id == 0, "taps default to the first pick"
    assert player.is_player


def test_every_backed_horse_counts_a_backer(room: Room):
    add_player(room, "Spread", None, horse_ids=[0, 2])
    add_player(room, "Loyal", 2)
    horses = {horse["id"]: horse["backers"] for horse in room.room_state()["horses"]}
    assert horses[0] == 1
    assert horses[2] == 2
    assert horses[1] == 0


def test_duplicate_and_unknown_horses_are_ignored(room: Room):
    player = add_player(room, "Sloppy", None, horse_ids=[1, 1, 99, 2])
    assert player.backed_horse_ids == [1, 2]


def test_the_first_pick_is_the_default_horse(room: Room):
    """Taps and self-buffs land here unless the phone names another button."""

    player = add_player(room, "Spread", None, horse_ids=[2, 0, 3])
    assert player.horse_id == 2
    assert player.backed_horse_ids == [2, 0, 3]


def test_rejoining_without_a_selection_keeps_your_horses(room: Room):
    """A client re-asserting its name must not lose its seat."""

    player = add_player(room, "Backer", None, horse_ids=[1, 3])
    again = room.join(name="Backer", horse_id=None, participant_id=player.id, as_host=False)
    assert again.backed_horse_ids == [1, 3]
    assert again.role == "player"


def test_explicitly_choosing_nothing_is_a_spectator(room: Room):
    watcher = add_player(room, "Watcher", None, horse_ids=[])
    assert watcher.role == "spectator"
    assert watcher.backed_horse_ids == []

    # And a player can step back to spectating on purpose.
    player = add_player(room, "Quitter", 2)
    room.join(name="Quitter", horse_id=None, horse_ids=[], participant_id=player.id, as_host=False)
    assert player.role == "spectator"
    assert player.backed_horse_ids == []
