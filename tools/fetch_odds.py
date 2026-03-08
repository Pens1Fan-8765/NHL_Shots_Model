"""
fetch_odds.py

Pulls NHL shots-on-goal player prop lines from The Odds API
for DraftKings, FanDuel, BetMGM, and Caesars.

Requires:
  .env with ODDS_API_KEY
  .tmp/schedule_YYYY-MM-DD.json (to get today's game IDs)

Output: .tmp/odds_YYYY-MM-DD.json

Schema: {player_key: [{book, line, over_odds, under_odds}]}
Player key format: {first_last}_{team_abbr}
"""

import json
import os
import re
import time
from datetime import date

import requests
from dotenv import load_dotenv

load_dotenv()

TMP_DIR = os.path.join(os.path.dirname(__file__), "..", ".tmp")
ODDS_API_BASE = "https://api.the-odds-api.com/v4"
SPORT = "icehockey_nhl"
BOOKS = ["draftkings", "fanduel", "betmgm", "caesars"]
MARKET = "player_shots_on_goal"


def normalize_name(name: str) -> str:
    """Normalize player name to match our player_key format."""
    name = name.lower().strip()
    name = re.sub(r"[\s\-]+", "_", name)
    name = re.sub(r"[^a-z0-9_]", "", name)
    return name


def name_to_key(player_name: str, team_abbr: str) -> str:
    parts = player_name.strip().split(" ", 1)
    if len(parts) == 2:
        first, last = parts
    else:
        first, last = parts[0], parts[0]
    return f"{normalize_name(first)}_{normalize_name(last)}_{team_abbr.upper()}"


def fetch_event_ids(api_key: str) -> list[dict]:
    """Get today's NHL event IDs from The Odds API."""
    url = f"{ODDS_API_BASE}/sports/{SPORT}/events"
    resp = requests.get(url, params={"apiKey": api_key}, timeout=10)
    resp.raise_for_status()
    return resp.json()


def fetch_player_props(api_key: str, event_id: str) -> dict:
    """Fetch player prop lines for a single game."""
    url = f"{ODDS_API_BASE}/sports/{SPORT}/events/{event_id}/odds"
    params = {
        "apiKey": api_key,
        "regions": "us",
        "markets": MARKET,
        "bookmakers": ",".join(BOOKS),
        "oddsFormat": "american",
    }
    resp = requests.get(url, params=params, timeout=10)
    if resp.status_code == 404:
        return {}  # market not available for this game
    resp.raise_for_status()
    return resp.json()


def parse_props(event_data: dict) -> dict[str, list]:
    """Extract per-player lines from an event's odds data."""
    player_odds: dict[str, list] = {}

    home_team = event_data.get("home_team", "")
    away_team = event_data.get("away_team", "")

    for bookmaker in event_data.get("bookmakers", []):
        book_key = bookmaker.get("key", "")
        for market in bookmaker.get("markets", []):
            if market.get("key") != MARKET:
                continue
            for outcome in market.get("outcomes", []):
                player_name = outcome.get("description", "") or outcome.get("name", "")
                point = outcome.get("point")  # the line (e.g. 3.5)
                price = outcome.get("price")  # american odds
                name_type = outcome.get("name", "")  # "Over" or "Under"

                if not player_name or point is None:
                    continue

                # Best-effort team assignment — The Odds API doesn't always include team
                # Downstream compare_lines.py joins on player_key from predictions
                # Try both home and away team as suffix
                for team_abbr in [home_team[:3].upper(), away_team[:3].upper()]:
                    key = name_to_key(player_name, team_abbr)
                    if key not in player_odds:
                        player_odds[key] = []

                    # Check if we already have an entry for this book
                    existing = next(
                        (e for e in player_odds[key] if e["book"] == book_key),
                        None,
                    )
                    if existing is None:
                        existing = {"book": book_key, "line": point, "over_odds": None, "under_odds": None}
                        player_odds[key].append(existing)

                    if name_type.lower() == "over":
                        existing["over_odds"] = price
                        existing["line"] = point
                    elif name_type.lower() == "under":
                        existing["under_odds"] = price

    return player_odds


def main():
    api_key = os.getenv("ODDS_API_KEY")
    if not api_key:
        print("ERROR: ODDS_API_KEY not set in .env")
        return

    today_str = date.today().isoformat()
    os.makedirs(TMP_DIR, exist_ok=True)

    print("Fetching today's NHL events from The Odds API...")
    events = fetch_event_ids(api_key)
    print(f"Found {len(events)} NHL events.")

    all_player_odds: dict[str, list] = {}
    requests_used = 0

    for event in events:
        event_id = event["id"]
        home = event.get("home_team", "")
        away = event.get("away_team", "")
        print(f"  Fetching props: {away} @ {home} (event {event_id})")

        try:
            event_data = fetch_player_props(api_key, event_id)
            requests_used += 1
            if not event_data:
                print(f"    No {MARKET} market found.")
                continue
            props = parse_props(event_data)
            print(f"    Found {len(props)} players with lines.")
            all_player_odds.update(props)
        except Exception as e:
            print(f"    ERROR: {e}")

        time.sleep(0.5)  # stay within rate limits

    out_path = os.path.join(TMP_DIR, f"odds_{today_str}.json")
    with open(out_path, "w") as f:
        json.dump(all_player_odds, f, indent=2)

    print(f"\nTotal players with odds: {len(all_player_odds)}")
    print(f"API requests used: {requests_used}")
    print(f"Saved to {out_path}")
    print("\nNote: Free tier = 500 requests/month. Each game uses 1 request.")


if __name__ == "__main__":
    main()
