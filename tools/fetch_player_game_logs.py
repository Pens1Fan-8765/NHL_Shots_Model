"""
fetch_player_game_logs.py

For each player on today's rosters, pulls their last 20 game logs
from the NHL Stats API.

Requires: .tmp/schedule_YYYY-MM-DD.json (run fetch_nhl_schedule.py first)
Output:   .tmp/player_logs_YYYY-MM-DD.json

Schema: {player_key: [{date, sog, toi, pp_toi, opponent, home, goals, assists}]}
Player key format: {first_last}_{team_abbr} e.g. nathan_mackinnon_COL

Filters: Forwards averaging <= 14 min TOI excluded. Defensemen averaging <= 18 min TOI excluded.
"""

import csv
import glob
import json
import os
import time
from datetime import date, timedelta
import requests

TMP_DIR = os.path.join(os.path.dirname(__file__), "..", ".tmp")
NHL_ROSTER_URL = "https://api-web.nhle.com/v1/roster/{team}/current"
NHL_GAME_LOG_URL = "https://api-web.nhle.com/v1/player/{player_id}/game-log/now"

MIN_TOI_FORWARD = 14.0    # Centers, wings excluded if avg TOI <= this
MIN_TOI_DEFENSE = 18.0    # Defensemen excluded if avg TOI < this


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
        pp_toi_str = game.get("powerPlayToi", "0:00")
        logs.append({
            "date": game.get("gameDate", ""),
            "sog": game.get("shots", 0),
            "toi": _toi_to_minutes(toi_str),
            "pp_toi": _toi_to_minutes(pp_toi_str),
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

    # Also include teams of any players with unresolved pending labels (for grading)
    # This is more reliable than relying on old schedule files existing in .tmp/
    pending_teams: set[str] = set()
    for pending_file in sorted(glob.glob(os.path.join(TMP_DIR, "pending_labels_*.csv"))):
        if today_str in pending_file:
            continue  # today's games haven't finished yet
        try:
            with open(pending_file, newline="") as f:
                for row in csv.DictReader(f):
                    player_key = row.get("player_key", "")
                    if player_key:
                        team = player_key.rsplit("_", 1)[-1].upper()
                        pending_teams.add(team)
        except Exception:
            pass
    new_teams = pending_teams - teams
    if new_teams:
        print(f"Also fetching {len(new_teams)} team(s) from pending labels (for grading): {', '.join(sorted(new_teams))}")
        teams.update(new_teams)

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
    filtered_forwards = 0
    filtered_defense = 0

    for i, player in enumerate(all_players):
        key = make_player_key(player["first_name"], player["last_name"], player["team"])
        try:
            logs = fetch_game_log(player["player_id"])
            if logs:
                avg_toi = sum(g["toi"] for g in logs) / len(logs)
                is_defense = player["position"] == "D"
                threshold = MIN_TOI_DEFENSE if is_defense else MIN_TOI_FORWARD
                if avg_toi <= threshold:
                    if is_defense:
                        filtered_defense += 1
                    else:
                        filtered_forwards += 1
                    if (i + 1) % 20 == 0:
                        print(f"  {i + 1}/{len(all_players)} done...")
                    time.sleep(0.2)
                    continue
            player_logs[key] = logs
            if (i + 1) % 20 == 0:
                print(f"  {i + 1}/{len(all_players)} done...")
        except Exception as e:
            print(f"  {key}: ERROR — {e}")
        time.sleep(0.2)

    out_path = os.path.join(TMP_DIR, f"player_logs_{today_str}.json")
    with open(out_path, "w") as f:
        json.dump(player_logs, f, indent=2)

    print(f"\nFiltered out {filtered_forwards + filtered_defense} players below TOI thresholds:")
    print(f"  Forwards excluded (avg TOI <= {MIN_TOI_FORWARD} min): {filtered_forwards}")
    print(f"  Defensemen excluded (avg TOI <= {MIN_TOI_DEFENSE} min): {filtered_defense}")
    print(f"Saved logs for {len(player_logs)} players to {out_path}")


if __name__ == "__main__":
    main()
