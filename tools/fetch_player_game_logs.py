"""
fetch_player_game_logs.py

For each player on today's rosters, pulls their last 20 game logs
from the NHL Stats API.

Requires: .tmp/schedule_YYYY-MM-DD.json (run fetch_nhl_schedule.py first)
Output:   .tmp/player_logs_YYYY-MM-DD.json

Schema: {player_key: [{date, sog, toi, opponent, home, goals, assists}]}
Player key format: {first_last}_{team_abbr} e.g. nathan_mackinnon_COL
"""

import json
import os
import time
from datetime import date
import requests

TMP_DIR = os.path.join(os.path.dirname(__file__), "..", ".tmp")
NHL_ROSTER_URL = "https://api-web.nhle.com/v1/roster/{team}/current"
NHL_GAME_LOG_URL = "https://api-web.nhle.com/v1/player/{player_id}/game-log/now"


def make_player_key(first: str, last: str, team: str) -> str:
    name = f"{first}_{last}".lower().replace(" ", "_").replace("-", "_").replace("'", "")
    return f"{name}_{team.upper()}"


def fetch_roster(team_abbr: str) -> list[dict]:
    url = NHL_ROSTER_URL.format(team=team_abbr)
    response = requests.get(url, timeout=10)
    response.raise_for_status()
    data = response.json()

    players = []
    for position_group in ["forwards", "defensemen"]:
        for p in data.get(position_group, []):
            players.append({
                "player_id": p["id"],
                "first_name": p["firstName"]["default"],
                "last_name": p["lastName"]["default"],
                "team": team_abbr,
                "position": p.get("positionCode", ""),
            })
    return players


def fetch_game_log(player_id: int, n_games: int = 20) -> list[dict]:
    url = NHL_GAME_LOG_URL.format(player_id=player_id)
    response = requests.get(url, timeout=10)
    response.raise_for_status()
    data = response.json()

    logs = []
    for game in data.get("gameLog", [])[:n_games]:
        toi_str = game.get("toi", "0:00")
        toi_minutes = _toi_to_minutes(toi_str)
        logs.append({
            "date": game.get("gameDate", ""),
            "sog": game.get("shots", 0),
            "toi": toi_minutes,
            "opponent": game.get("opponentAbbrev", ""),
            "home": game.get("homeRoadFlag", "H") == "H",
            "goals": game.get("goals", 0),
            "assists": game.get("assists", 0),
        })
    return logs


def _toi_to_minutes(toi_str: str) -> float:
    """Convert 'MM:SS' string to decimal minutes."""
    try:
        parts = toi_str.split(":")
        return int(parts[0]) + int(parts[1]) / 60
    except Exception:
        return 0.0


def main():
    today_str = date.today().isoformat()
    schedule_path = os.path.join(TMP_DIR, f"schedule_{today_str}.json")

    if not os.path.exists(schedule_path):
        print(f"Schedule file not found: {schedule_path}")
        print("Run fetch_nhl_schedule.py first.")
        return

    with open(schedule_path) as f:
        games = json.load(f)

    teams = set()
    for game in games:
        teams.add(game["home_team"])
        teams.add(game["away_team"])

    print(f"Fetching rosters for {len(teams)} teams: {', '.join(sorted(teams))}")

    all_players: list[dict] = []
    for team in sorted(teams):
        try:
            players = fetch_roster(team)
            all_players.extend(players)
            print(f"  {team}: {len(players)} players")
        except Exception as e:
            print(f"  {team}: ERROR — {e}")
        time.sleep(0.3)  # be polite to the API

    print(f"\nFetching game logs for {len(all_players)} players...")
    player_logs: dict[str, list] = {}

    for i, player in enumerate(all_players):
        key = make_player_key(player["first_name"], player["last_name"], player["team"])
        try:
            logs = fetch_game_log(player["player_id"])
            player_logs[key] = logs
            if (i + 1) % 20 == 0:
                print(f"  {i + 1}/{len(all_players)} done...")
        except Exception as e:
            print(f"  {key}: ERROR — {e}")
            player_logs[key] = []
        time.sleep(0.2)

    out_path = os.path.join(TMP_DIR, f"player_logs_{today_str}.json")
    with open(out_path, "w") as f:
        json.dump(player_logs, f, indent=2)

    print(f"\nSaved logs for {len(player_logs)} players to {out_path}")


if __name__ == "__main__":
    main()
