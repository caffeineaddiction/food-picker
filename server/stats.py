"""Session statistics, persisted to a single JSON file.

Deliberately not a database (SPEC.md §8): one small file, written after each
race, is everything an office needs to argue about who won last Thursday.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

log = logging.getLogger(__name__)

DEFAULT_PATH = Path("data/session_stats.json")


@dataclass
class SessionStats:
    """Aggregate history across every race this installation has run."""

    path: Path = DEFAULT_PATH
    races: int = 0
    food_wins: dict[str, int] = field(default_factory=dict)
    food_runs: dict[str, int] = field(default_factory=dict)
    player_taps: dict[str, int] = field(default_factory=dict)
    player_wins: dict[str, int] = field(default_factory=dict)
    player_powerups: dict[str, int] = field(default_factory=dict)
    history: list[dict[str, Any]] = field(default_factory=list)

    # -- persistence ---------------------------------------------------------
    @classmethod
    def load(cls, path: Path = DEFAULT_PATH) -> SessionStats:
        stats = cls(path=path)
        try:
            raw = json.loads(path.read_text())
        except (OSError, json.JSONDecodeError):
            return stats
        stats.races = raw.get("races", 0)
        stats.food_wins = raw.get("food_wins", {})
        stats.food_runs = raw.get("food_runs", {})
        stats.player_taps = raw.get("player_taps", {})
        stats.player_wins = raw.get("player_wins", {})
        stats.player_powerups = raw.get("player_powerups", {})
        stats.history = raw.get("history", [])
        return stats

    def save(self) -> None:
        """Best-effort write; a stats failure must never break a race night."""

        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            self.path.write_text(
                json.dumps(
                    {
                        "races": self.races,
                        "food_wins": self.food_wins,
                        "food_runs": self.food_runs,
                        "player_taps": self.player_taps,
                        "player_wins": self.player_wins,
                        "player_powerups": self.player_powerups,
                        "history": self.history[-100:],
                    },
                    indent=2,
                )
            )
        except OSError:  # pragma: no cover - disk problems shouldn't crash the game
            log.warning("could not persist session stats to %s", self.path)

    # -- recording -----------------------------------------------------------
    def record_race(self, results: dict[str, Any], *, mode: str, track: str) -> None:
        self.races += 1
        winner = results.get("winner")
        for row in results.get("order", []):
            self.food_runs[row["name"]] = self.food_runs.get(row["name"], 0) + 1
        if winner:
            self.food_wins[winner] = self.food_wins.get(winner, 0) + 1
        winning_horse = results.get("winner_id")
        for player in results.get("players", []):
            name = player["name"]
            self.player_taps[name] = self.player_taps.get(name, 0) + player["taps"]
            self.player_powerups[name] = self.player_powerups.get(name, 0) + player["powerups_used"]
            if player["horse_id"] == winning_horse:
                self.player_wins[name] = self.player_wins.get(name, 0) + 1
        self.history.append({"winner": winner, "mode": mode, "track": track})
        self.save()

    # -- reading -------------------------------------------------------------
    def leaderboard(self, limit: int = 5) -> dict[str, Any]:
        """Small digest shown on the lobby and results screens."""

        def top(counter: dict[str, int]) -> list[dict[str, Any]]:
            ordered = sorted(counter.items(), key=lambda item: item[1], reverse=True)
            return [{"name": name, "value": value} for name, value in ordered[:limit]]

        return {
            "races": self.races,
            "topFoods": top(self.food_wins),
            "topTappers": top(self.player_taps),
            "topWinners": top(self.player_wins),
        }
