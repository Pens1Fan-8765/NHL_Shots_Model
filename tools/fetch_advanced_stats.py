"""
fetch_advanced_stats.py

Downloads the current-season player-level CSV from MoneyPuck and extracts
advanced shot metrics (xSF, CF%, FF%, iSCF) for all skaters.

Output: .tmp/advanced_stats_YYYY-MM-DD.csv

Schema: player_key, xSF_per_60, CF_pct, FF_pct, iSCF
Player key format: {first_last}_{team_abbr}
"""

import csv
import io
import json
import os
import re
from datetime import date
import requests

TMP_DIR = os.path.join(os.path.dirname(__file__), "..", ".tmp")

# MoneyPuck provides downloadable CSVs for all skaters — current season, all situations
MONEYPUCK_URL = (
    "https://moneypuck.com/moneypuck/playerData/seasonSummary/"
    "{season}/regular/skaters.csv"
)


def current_season_id() -> str:
    """Returns the MoneyPuck season string, e.g. '2025' for the 2025-26 season."""
    today = date.today()
    # NHL season starts in October; if before October, we're in the prior season
    year = today.year if today.month >= 10 else today.year - 1
    return str(year)


def normalize_name(name: str) -> str:
    """Lowercase, replace spaces/hyphens/apostrophes with underscores."""
    name = name.lower().strip()
    name = re.sub(r"[\s\-]+", "_", name)
    name = re.sub(r"[^a-z0-9_]", "", name)
    return name


def make_player_key(first: str, last: str, team: str) -> str:
    return f"{normalize_name(first)}_{normalize_name(last)}_{team.upper()}"


def fetch_advanced_stats() -> list[dict]:
    season = current_season_id()
    url = MONEYPUCK_URL.format(season=season)
    print(f"Downloading MoneyPuck data for {season} season...")

    response = requests.get(url, timeout=30)
    response.raise_for_status()

    reader = csv.DictReader(io.StringIO(response.text))
    rows = list(reader)
    print(f"Downloaded {len(rows)} player-season rows from MoneyPuck.")
    return rows


def process_rows(rows: list[dict]) -> dict[str, dict]:
    """
    Aggregates all-situations stats per player.
    MoneyPuck has one row per player per situation (all, 5on5, PP, PK, etc.).
    We use the 'all' situation row for overall metrics.
    """
    player_stats: dict[str, dict] = {}

    for row in rows:
        if row.get("situation") != "all":
            continue

        # MoneyPuck uses a single "name" column (e.g. "Alexander Wennberg")
        full_name = row.get("name", "")
        team = row.get("team", "")
        parts = full_name.strip().split(" ", 1)

        if len(parts) < 2 or not team:
            continue

        first, last = parts[0], parts[1]

        key = make_player_key(first, last, team)

        try:
            toi = float(row.get("icetime", 0) or 0)
            xsf = float(row.get("I_F_xOnGoal", 0) or 0)
            cf = float(row.get("OnIce_F_shotAttempts", 0) or 0)
            ca = float(row.get("OnIce_A_shotAttempts", 0) or 0)
            ff = float(row.get("OnIce_F_unblockedShotAttempts", 0) or 0)
            fa = float(row.get("OnIce_A_unblockedShotAttempts", 0) or 0)
            iscf = float(row.get("I_F_shotAttempts", 0) or 0)

            toi_60 = toi / 60 if toi > 0 else 1
            cf_pct = round(cf / (cf + ca) * 100, 2) if (cf + ca) > 0 else 0.0
            ff_pct = round(ff / (ff + fa) * 100, 2) if (ff + fa) > 0 else 0.0
            xsf_per_60 = round(xsf / toi_60, 2)
            iscf_per_60 = round(iscf / toi_60, 2)
        except (ValueError, ZeroDivisionError):
            continue

        player_stats[key] = {
            "player_key": key,
            "xSF_per_60": xsf_per_60,
            "CF_pct": cf_pct,
            "FF_pct": ff_pct,
            "iSCF_per_60": iscf_per_60,
        }

    return player_stats


def main():
    os.makedirs(TMP_DIR, exist_ok=True)
    today_str = date.today().isoformat()
    out_path = os.path.join(TMP_DIR, f"advanced_stats_{today_str}.csv")

    # Load the filtered player set from fetch_player_game_logs.py
    logs_path = os.path.join(TMP_DIR, f"player_logs_{today_str}.json")
    if not os.path.exists(logs_path):
        print(f"player_logs file not found: {logs_path}")
        print("Run fetch_player_game_logs.py first.")
        return
    with open(logs_path) as f:
        kept_players = set(json.load(f).keys())
    print(f"Loaded {len(kept_players)} kept players from player_logs.")

    rows = fetch_advanced_stats()
    all_stats = process_rows(rows)

    # Only keep stats for players that passed the TOI filter
    player_stats = {k: v for k, v in all_stats.items() if k in kept_players}
    skipped = len(all_stats) - len(player_stats)

    fieldnames = ["player_key", "xSF_per_60", "CF_pct", "FF_pct", "iSCF_per_60"]
    with open(out_path, "w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(player_stats.values())

    print(f"Skipped {skipped} players below TOI threshold.")
    print(f"Saved advanced stats for {len(player_stats)} players to {out_path}")


if __name__ == "__main__":
    main()
