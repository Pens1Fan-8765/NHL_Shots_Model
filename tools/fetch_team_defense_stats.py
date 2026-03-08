"""
fetch_team_defense_stats.py

Pulls shots-against per game metrics for each team playing today,
both season average and last 10 games.

Requires: .tmp/schedule_YYYY-MM-DD.json (run fetch_nhl_schedule.py first)
Output:   .tmp/team_defense_YYYY-MM-DD.json

Schema: {team_abbr: {sa_season, sa_l10}}
"""

import json
import os
import time
from datetime import date
import requests

TMP_DIR = os.path.join(os.path.dirname(__file__), "..", ".tmp")
NHL_STANDINGS_URL = "https://api-web.nhle.com/v1/standings/now"
NHL_TEAM_STATS_URL = "https://api-web.nhle.com/v1/club-stats/{team}/now"


def fetch_team_season_sa(team_abbr: str) -> float:
    """Returns shots against per game for the season."""
    url = NHL_TEAM_STATS_URL.format(team=team_abbr)
    response = requests.get(url, timeout=10)
    response.raise_for_status()
    data = response.json()

    skater_stats = data.get("skaters", [])
    # Team-level SA comes from the team summary, not skaters
    # Use the standings endpoint for shots against
    return 0.0  # placeholder — populated below via standings


def fetch_all_team_defense() -> dict[str, dict]:
    """
    Pulls shots against per game from standings for all teams.
    Returns {team_abbr: {sa_season, sa_l10}}
    """
    response = requests.get(NHL_STANDINGS_URL, timeout=10)
    response.raise_for_status()
    data = response.json()

    team_defense = {}
    for team_data in data.get("standings", []):
        abbrev = team_data.get("teamAbbrev", {}).get("default", "")
        games_played = team_data.get("gamesPlayed", 1) or 1

        shots_against = team_data.get("shotsAgainst", 0)
        sa_per_game = round(shots_against / games_played, 2) if games_played > 0 else 0.0

        team_defense[abbrev] = {
            "sa_season": sa_per_game,
            "sa_l10": sa_per_game,  # NHL standings API doesn't expose L10 SA directly;
            # Layer 2 will use sa_season as fallback — Friend A can enhance this
            # by pulling game-by-game logs and computing L10 manually if desired
        }

    return team_defense


def main():
    today_str = date.today().isoformat()
    schedule_path = os.path.join(TMP_DIR, f"schedule_{today_str}.json")

    if not os.path.exists(schedule_path):
        print(f"Schedule file not found: {schedule_path}")
        print("Run fetch_nhl_schedule.py first.")
        return

    with open(schedule_path) as f:
        games = json.load(f)

    today_teams = set()
    for game in games:
        today_teams.add(game["home_team"])
        today_teams.add(game["away_team"])

    print("Fetching team defense stats from NHL standings...")
    all_defense = fetch_all_team_defense()

    # Filter to only teams playing today
    today_defense = {team: stats for team, stats in all_defense.items() if team in today_teams}

    missing = today_teams - set(today_defense.keys())
    if missing:
        print(f"Warning: no defense data found for: {missing}")

    for team, stats in sorted(today_defense.items()):
        print(f"  {team}: SA/game = {stats['sa_season']}")

    out_path = os.path.join(TMP_DIR, f"team_defense_{today_str}.json")
    with open(out_path, "w") as f:
        json.dump(today_defense, f, indent=2)

    print(f"\nSaved defense stats for {len(today_defense)} teams to {out_path}")


if __name__ == "__main__":
    main()
