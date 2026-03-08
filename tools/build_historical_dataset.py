"""
build_historical_dataset.py

Generates historical_features.csv and historical_labels.csv from existing
player_logs files in .tmp/. Uses the 20-game log history already collected
by fetch_player_game_logs.py to build training rows without any manual data entry.

For each player, slides a window through their game log:
  - game i is the "current" game (the label)
  - games 0..i-1 are used to compute rolling features
  - requires at least 5 prior games (MIN_GAMES = 5)

Line estimation: player's 10-game rolling SOG average rounded to nearest 0.5.
This is a reasonable proxy since books typically set lines near expected production.
Replace with real historical lines once available for better model accuracy.

Output:
  .tmp/historical_features.csv  — feature rows (same schema as features_YYYY-MM-DD.csv)
  .tmp/historical_labels.csv    — labels (player_key, game_date, actual_sog, line, book, over_odds)

Usage:
  python tools/build_historical_dataset.py
"""

import json
import os
import glob
from datetime import datetime, timedelta

import pandas as pd

TMP_DIR = os.path.join(os.path.dirname(__file__), "..", ".tmp")
MIN_GAMES = 5  # minimum prior games needed to generate a feature row


def load_all_player_logs() -> dict:
    """Load and merge all player_logs_*.json files in .tmp/.
    If a player appears in multiple files, deduplicate by (player_key, date).
    """
    pattern = os.path.join(TMP_DIR, "player_logs_*.json")
    files = sorted(glob.glob(pattern))

    if not files:
        raise FileNotFoundError(f"No player_logs_*.json files found in {TMP_DIR}")

    print(f"Found {len(files)} player_logs file(s): {[os.path.basename(f) for f in files]}")

    merged: dict[str, dict] = {}  # player_key -> {date_str: game_entry}

    for filepath in files:
        with open(filepath) as f:
            data = json.load(f)
        for player_key, games in data.items():
            if player_key not in merged:
                merged[player_key] = {}
            for game in games:
                merged[player_key][game["date"]] = game

    # Convert back to sorted lists (ascending by date)
    result = {}
    for player_key, game_dict in merged.items():
        result[player_key] = sorted(game_dict.values(), key=lambda g: g["date"])

    return result


def rolling_avg(games: list, field: str, n: int) -> float:
    """Mean of `field` over the last n games in the list."""
    subset = [g[field] for g in games[-n:] if g.get(field) is not None]
    return sum(subset) / len(subset) if subset else 0.0


def is_back_to_back(current_date: str, prior_games: list) -> int:
    """Returns 1 if the most recent prior game was the day before current_date."""
    if not prior_games:
        return 0
    last_date = prior_games[-1]["date"]
    delta = datetime.fromisoformat(current_date) - datetime.fromisoformat(last_date)
    return 1 if delta.days == 1 else 0


def sog_vs_opponent(prior_games: list, opponent: str, fallback: float) -> float:
    """Historical SOG average vs this specific opponent. Falls back to season avg."""
    opp_games = [g["sog"] for g in prior_games if g.get("opponent") == opponent]
    return sum(opp_games) / len(opp_games) if opp_games else fallback


def estimate_line(sog_avg_10: float) -> float:
    """Round 10-game avg to nearest 0.5 as a proxy for the sportsbook line.
    Replace with real historical lines for better accuracy.
    """
    rounded = round(sog_avg_10 * 2) / 2
    return max(1.5, min(5.5, rounded))


def extract_team(player_key: str) -> str:
    """Extract team abbreviation from player_key (e.g. 'nathan_mackinnon_COL' -> 'COL')."""
    return player_key.rsplit("_", 1)[-1]


def build_rows(player_key: str, games: list) -> tuple[list, list]:
    """Build feature and label rows for one player.
    Returns (feature_rows, label_rows).
    """
    feature_rows = []
    label_rows = []
    team = extract_team(player_key)

    for i in range(MIN_GAMES, len(games)):
        current = games[i]
        prior = games[:i]

        sog_avg_5 = rolling_avg(prior, "sog", 5)
        sog_avg_10 = rolling_avg(prior, "sog", 10)
        sog_avg_20 = rolling_avg(prior, "sog", 20)
        toi_avg_5 = rolling_avg(prior, "toi", 5)
        trend_ratio = sog_avg_5 / sog_avg_20 if sog_avg_20 > 0 else 1.0
        sog_opp = sog_vs_opponent(prior, current["opponent"], sog_avg_20)
        b2b = is_back_to_back(current["date"], prior)
        home_flag = 1 if current.get("home") else 0
        line = estimate_line(sog_avg_10)

        feature_rows.append({
            "player_key": player_key,
            "team": team,
            "opponent": current["opponent"],
            "game_date": current["date"],
            "home_flag": home_flag,
            "b2b_flag": b2b,
            "sog_avg_5": round(sog_avg_5, 3),
            "sog_avg_10": round(sog_avg_10, 3),
            "sog_avg_20": round(sog_avg_20, 3),
            "sog_vs_opp": round(sog_opp, 3),
            "toi_avg_5": round(toi_avg_5, 3),
            "opp_sa_per_game_season": 0.0,  # not available historically
            "opp_sa_per_game_l10": 0.0,     # not available historically
            "trend_ratio": round(trend_ratio, 3),
            "xSF_per_60": 0.0,              # not available historically
            "CF_pct": 0.0,
            "FF_pct": 0.0,
            "iSCF_per_60": 0.0,
            "games_in_log": i,
        })

        label_rows.append({
            "player_key": player_key,
            "game_date": current["date"],
            "actual_sog": current["sog"],
            "line": line,
            "book": "estimated",
            "over_odds": -110,
        })

    return feature_rows, label_rows


def main():
    print("Loading player logs...")
    all_logs = load_all_player_logs()
    print(f"Loaded {len(all_logs)} players\n")

    all_features = []
    all_labels = []
    skipped = 0

    for player_key, games in all_logs.items():
        if len(games) < MIN_GAMES + 1:
            skipped += 1
            continue
        features, labels = build_rows(player_key, games)
        all_features.extend(features)
        all_labels.extend(labels)

    features_df = pd.DataFrame(all_features)
    labels_df = pd.DataFrame(all_labels)

    features_path = os.path.join(TMP_DIR, "historical_features.csv")
    labels_path = os.path.join(TMP_DIR, "historical_labels.csv")

    features_df.to_csv(features_path, index=False)
    labels_df.to_csv(labels_path, index=False)

    print(f"Generated {len(features_df)} training rows from {len(all_logs) - skipped} players")
    print(f"Skipped {skipped} players with fewer than {MIN_GAMES + 1} games")
    print(f"\nSaved:")
    print(f"  {features_path}")
    print(f"  {labels_path}")
    print(f"\nNote: opp_sa_per_game and advanced stats (xSF, CF%, etc.) default to 0.0")
    print(f"      because historical values aren't available from game logs alone.")
    print(f"      Line values are estimated from each player's 10-game rolling average.")
    print(f"      Replace with real sportsbook lines for better model accuracy.")


if __name__ == "__main__":
    main()
