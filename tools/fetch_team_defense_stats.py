"""
fetch_team_defense_stats.py

Combined tool that does two things:

1. Scrapes shotpropz.com for SOG allowed per game by opponent position (C, LW, RW, D),
   capturing both season-to-date (YTD) and last-10-games (Recent) data via headless browser.
   This is the primary matchup signal — weighted heavily by the model.

2. Scrapes dailyfaceoff.com for each team's even-strength line combinations to get accurate
   player positions. DailyFaceoff has final authority over NHL API positions — if they differ,
   DailyFaceoff wins.

Requires: .tmp/schedule_YYYY-MM-DD.json (run fetch_nhl_schedule.py first)
          playwright: pip install playwright && playwright install chromium
Output:   .tmp/team_defense_YYYY-MM-DD.json

Schema:
{
  "team_defense": {
    "COL": {
      "sa_vs_C_season": 8.6,  "sa_vs_C_l10": 7.2,
      "sa_vs_LW_season": 6.4, "sa_vs_LW_l10": 5.8,
      "sa_vs_RW_season": 3.5, "sa_vs_RW_l10": 4.1,
      "sa_vs_D_season": 7.2,  "sa_vs_D_l10": 6.8
    }
  },
  "player_positions": {
    "nathan_mackinnon_COL": "C",
    "gabriel_landeskog_COL": "LW",
    ...
  }
}
"""

import json
import os
import re
import time
from datetime import date

import requests
from bs4 import BeautifulSoup
from playwright.sync_api import sync_playwright

TMP_DIR = os.path.join(os.path.dirname(__file__), "..", ".tmp")
SHOTPROPZ_URL = "https://shotpropz.com/nhl/sog-against-by-position/"
DAILYFACEOFF_URL = "https://www.dailyfaceoff.com/teams/{slug}/line-combinations/"
POSITIONS = ["C", "LW", "RW", "D"]

# Maps NHL API team abbreviations to DailyFaceoff URL slugs
TEAM_SLUGS = {
    "ANA": "anaheim-ducks",
    "BOS": "boston-bruins",
    "BUF": "buffalo-sabres",
    "CAR": "carolina-hurricanes",
    "CBJ": "columbus-blue-jackets",
    "CGY": "calgary-flames",
    "CHI": "chicago-blackhawks",
    "COL": "colorado-avalanche",
    "DAL": "dallas-stars",
    "DET": "detroit-red-wings",
    "EDM": "edmonton-oilers",
    "FLA": "florida-panthers",
    "LAK": "los-angeles-kings",
    "MIN": "minnesota-wild",
    "MTL": "montreal-canadiens",
    "NJD": "new-jersey-devils",
    "NSH": "nashville-predators",
    "NYI": "new-york-islanders",
    "NYR": "new-york-rangers",
    "OTT": "ottawa-senators",
    "PHI": "philadelphia-flyers",
    "PIT": "pittsburgh-penguins",
    "SEA": "seattle-kraken",
    "SJS": "san-jose-sharks",
    "STL": "st-louis-blues",
    "TBL": "tampa-bay-lightning",
    "TOR": "toronto-maple-leafs",
    "UTA": "utah-hockey-club",
    "VAN": "vancouver-canucks",
    "VGK": "vegas-golden-knights",
    "WSH": "washington-capitals",
    "WPG": "winnipeg-jets",
}


# ---------------------------------------------------------------------------
# Shotpropz scraping (playwright — needs JS to toggle the Recent filter)
# ---------------------------------------------------------------------------

def parse_shotpropz_tables(page) -> dict[str, dict]:
    """Parse all 4 position tables from the current page state.
    Tables appear in order: C, LW, RW, D.
    Returns {team_abbr: {position: sa_per_game}}
    """
    tables = page.query_selector_all("table")
    result: dict[str, dict] = {}

    for i, pos in enumerate(POSITIONS):
        if i >= len(tables):
            print(f"  Warning: table for {pos} not found on shotpropz")
            continue
        rows = tables[i].query_selector_all("tbody tr")
        for row in rows:
            cells = row.query_selector_all("td")
            if len(cells) < 4:
                continue
            team = cells[1].inner_text().strip()
            sa_gp_str = cells[3].inner_text().strip()
            try:
                result.setdefault(team, {})[pos] = float(sa_gp_str)
            except ValueError:
                pass

    return result


def scrape_shotpropz() -> tuple[dict, dict]:
    """Returns (ytd_data, l10_data) where each is {team_abbr: {pos: sa_gp}}."""
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        page = browser.new_page()

        print(f"Loading {SHOTPROPZ_URL}...")
        page.goto(SHOTPROPZ_URL, wait_until="networkidle")

        print("  Scraping YTD (season) data...")
        ytd_data = parse_shotpropz_tables(page)

        print("  Switching to Recent (L10) view...")
        page.select_option("select", value="recent")
        page.wait_for_load_state("networkidle")
        page.wait_for_selector("table", timeout=15000)  # wait for tables to re-render
        page.wait_for_timeout(1000)

        print("  Scraping L10 data...")
        l10_data = parse_shotpropz_tables(page)

        browser.close()
        return ytd_data, l10_data


# ---------------------------------------------------------------------------
# DailyFaceoff scraping (requests + BeautifulSoup — parses __NEXT_DATA__ JSON)
# ---------------------------------------------------------------------------

def normalize_player_key(full_name: str, team_abbr: str) -> str | None:
    """Convert 'Nathan MacKinnon' + 'COL' to 'nathan_mackinnon_COL'.
    Matches the same format used in fetch_player_game_logs.py::make_player_key().
    """
    parts = full_name.strip().split(" ", 1)
    if len(parts) < 2:
        return None
    first, last = parts[0], parts[1]
    combined = f"{first}_{last}".lower().replace(" ", "_").replace("-", "_").replace("'", "")
    return f"{combined}_{team_abbr.upper()}"


def normalize_position(pos_identifier: str) -> str:
    """Map DailyFaceoff position identifiers to our standard positions."""
    mapping = {
        "lw": "LW",
        "c": "C",
        "rw": "RW",
        "ld": "D",
        "rd": "D",
    }
    return mapping.get(pos_identifier.lower(), pos_identifier.upper())


def scrape_dailyfaceoff_team(team_abbr: str) -> dict[str, str]:
    """Scrapes even-strength line combinations for one team.
    Returns {player_key: position} e.g. {"nathan_mackinnon_COL": "C"}
    DailyFaceoff is the final authority on player positions.
    """
    slug = TEAM_SLUGS.get(team_abbr)
    if not slug:
        print(f"  {team_abbr}: no DailyFaceoff slug defined, skipping")
        return {}

    url = DAILYFACEOFF_URL.format(slug=slug)
    try:
        response = requests.get(url, timeout=10, headers={"User-Agent": "Mozilla/5.0"})
        response.raise_for_status()
    except Exception as e:
        print(f"  {team_abbr}: DailyFaceoff fetch failed — {e}")
        return {}

    soup = BeautifulSoup(response.text, "html.parser")

    # DailyFaceoff is a Next.js app — all player data is in __NEXT_DATA__ JSON
    script_tag = soup.find("script", {"id": "__NEXT_DATA__"})
    if not script_tag:
        print(f"  {team_abbr}: __NEXT_DATA__ not found on DailyFaceoff page")
        return {}

    try:
        next_data = json.loads(script_tag.string)
        players = (
            next_data["props"]["pageProps"]["combinations"]["players"]
        )
    except (KeyError, json.JSONDecodeError) as e:
        print(f"  {team_abbr}: failed to parse DailyFaceoff JSON — {e}")
        return {}

    player_positions: dict[str, str] = {}
    for player in players:
        # Only use even-strength lines — this is the true positional assignment
        if player.get("categoryIdentifier") != "ev":
            continue
        # Skip goalies
        if player.get("positionIdentifier") == "g":
            continue

        name = player.get("name", "")
        pos_id = player.get("positionIdentifier", "")

        if not name or not pos_id:
            continue

        key = normalize_player_key(name, team_abbr)
        if key:
            player_positions[key] = normalize_position(pos_id)

    return player_positions


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

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

    # --- Step 1: Shotpropz position-based defense stats (primary signal) ---
    print("\n=== Scraping shotpropz.com (position-based defense stats) ===")
    ytd_data, l10_data = scrape_shotpropz()

    team_defense: dict[str, dict] = {}
    missing_defense = []

    for team in sorted(today_teams):
        if team not in ytd_data and team not in l10_data:
            missing_defense.append(team)
            continue
        team_defense[team] = {}
        for pos in POSITIONS:
            team_defense[team][f"sa_vs_{pos}_season"] = ytd_data.get(team, {}).get(pos, 0.0)
            team_defense[team][f"sa_vs_{pos}_l10"] = l10_data.get(team, {}).get(pos, 0.0)

    if missing_defense:
        print(f"Warning: no shotpropz data for: {missing_defense}")

    print("\nTeam defense (YTD / L10 per position):")
    for team, stats in sorted(team_defense.items()):
        print(
            f"  {team}: "
            f"C={stats['sa_vs_C_season']}/{stats['sa_vs_C_l10']}  "
            f"LW={stats['sa_vs_LW_season']}/{stats['sa_vs_LW_l10']}  "
            f"RW={stats['sa_vs_RW_season']}/{stats['sa_vs_RW_l10']}  "
            f"D={stats['sa_vs_D_season']}/{stats['sa_vs_D_l10']}  "
            f"(YTD/L10)"
        )

    # --- Step 2: DailyFaceoff player positions (final authority on positions) ---
    print(f"\n=== Scraping dailyfaceoff.com (line combinations for {len(today_teams)} teams) ===")
    player_positions: dict[str, str] = {}

    for team in sorted(today_teams):
        positions = scrape_dailyfaceoff_team(team)
        player_positions.update(positions)
        print(f"  {team}: {len(positions)} players mapped")
        time.sleep(0.5)  # polite delay between requests

    # --- Save combined output ---
    output = {
        "team_defense": team_defense,
        "player_positions": player_positions,
    }

    out_path = os.path.join(TMP_DIR, f"team_defense_{today_str}.json")
    with open(out_path, "w") as f:
        json.dump(output, f, indent=2)

    print(f"\nSaved:")
    print(f"  {len(team_defense)} teams' defense stats")
    print(f"  {len(player_positions)} player position overrides")
    print(f"  → {out_path}")


if __name__ == "__main__":
    main()
