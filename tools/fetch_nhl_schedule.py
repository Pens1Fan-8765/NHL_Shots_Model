"""
fetch_nhl_schedule.py

Pulls today's NHL game schedule from the NHL Stats API.
Output: .tmp/schedule_YYYY-MM-DD.json

Schema: [{game_id, home_team, away_team, game_time}]
"""

import json
import os
import sys
from datetime import date
import requests

TMP_DIR = os.path.join(os.path.dirname(__file__), "..", ".tmp")
NHL_SCHEDULE_URL = "https://api-web.nhle.com/v1/schedule/now"


def fetch_schedule() -> list[dict]:
    response = requests.get(NHL_SCHEDULE_URL, timeout=10)
    response.raise_for_status()
    data = response.json()

    games = []
    game_week = data.get("gameWeek", [])
    today_str = date.today().isoformat()

    for day in game_week:
        if day.get("date") != today_str:
            continue
        for game in day.get("games", []):
            games.append({
                "game_id": game["id"],
                "home_team": game["homeTeam"]["abbrev"],
                "away_team": game["awayTeam"]["abbrev"],
                "game_time": game.get("startTimeUTC", ""),
            })

    return games


def main():
    os.makedirs(TMP_DIR, exist_ok=True)
    today_str = date.today().isoformat()
    out_path = os.path.join(TMP_DIR, f"schedule_{today_str}.json")

    print(f"Fetching NHL schedule for {today_str}...")
    games = fetch_schedule()

    if not games:
        print("No games found today.")
        sys.exit(0)

    with open(out_path, "w") as f:
        json.dump(games, f, indent=2)

    print(f"Found {len(games)} game(s):")
    for g in games:
        print(f"  {g['away_team']} @ {g['home_team']}  ({g['game_time']})")
    print(f"Saved to {out_path}")


if __name__ == "__main__":
    main()
