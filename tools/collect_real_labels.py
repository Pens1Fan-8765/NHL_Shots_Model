"""
collect_real_labels.py

Completes pending label files by matching actual SOG results from player game logs,
then appends the completed rows to .tmp/real_labels.csv.

Run this early in each day's pipeline, after fetch_player_game_logs.py has run,
so yesterday's actual results are available in the game logs.

How it works:
  1. Finds all pending_labels_YYYY-MM-DD.csv files (except today's — games haven't finished)
  2. Loads player game logs to look up actual SOG for each player/date
  3. Appends completed rows (real line + real SOG) to real_labels.csv
  4. Deletes resolved pending files

Requires:
  .tmp/pending_labels_YYYY-MM-DD.csv  (from save_daily_lines.py)
  .tmp/player_logs_*.json             (from fetch_player_game_logs.py)

Output:
  .tmp/real_labels.csv  (grows over time — used by train_model.py)
  Schema: player_key, game_date, actual_sog, line, book, over_odds
"""

import csv
import glob
import json
import os
from datetime import date

import pandas as pd

TMP_DIR = os.path.join(os.path.dirname(__file__), "..", ".tmp")
REAL_LABELS_PATH = os.path.join(TMP_DIR, "real_labels.csv")
REAL_LABELS_COLS = ["player_key", "game_date", "actual_sog", "line", "book", "over_odds"]


def load_sog_lookup() -> dict:
    """Load all player_logs_*.json and return {player_key: {date_str: sog}}."""
    pattern = os.path.join(TMP_DIR, "player_logs_*.json")
    files = glob.glob(pattern)

    lookup: dict[str, dict[str, float]] = {}
    for filepath in files:
        with open(filepath) as f:
            data = json.load(f)
        for player_key, games in data.items():
            if player_key not in lookup:
                lookup[player_key] = {}
            for game in games:
                game_date = game.get("date")
                sog = game.get("sog")
                if game_date is not None and sog is not None:
                    lookup[player_key][game_date] = sog

    return lookup


def load_existing_keys() -> set:
    """Return set of (player_key, game_date) already recorded in real_labels.csv."""
    if not os.path.exists(REAL_LABELS_PATH):
        return set()
    df = pd.read_csv(REAL_LABELS_PATH)
    return set(zip(df["player_key"], df["game_date"]))


def main():
    today_str = date.today().isoformat()

    # Find all pending files from previous days (not today — games haven't finished)
    pattern = os.path.join(TMP_DIR, "pending_labels_*.csv")
    pending_files = sorted(
        f for f in glob.glob(pattern) if today_str not in f
    )

    if not pending_files:
        print("No pending label files to resolve.")
        return

    print(f"Found {len(pending_files)} pending label file(s) to resolve.")

    sog_lookup = load_sog_lookup()
    existing = load_existing_keys()

    new_rows = []
    unresolved_count = 0
    resolved_files = []

    for pending_path in pending_files:
        pending_df = pd.read_csv(pending_path)
        file_resolved = 0
        file_unresolved = 0

        for _, row in pending_df.iterrows():
            player_key = row["player_key"]
            game_date = row["game_date"]

            if (player_key, game_date) in existing:
                file_resolved += 1
                continue

            actual_sog = sog_lookup.get(player_key, {}).get(game_date)

            if actual_sog is None:
                file_unresolved += 1
                unresolved_count += 1
                continue

            new_rows.append({
                "player_key": player_key,
                "game_date": game_date,
                "actual_sog": actual_sog,
                "line": row["best_line"],
                "book": row["best_book"],
                "over_odds": row["best_over_odds"],
            })
            file_resolved += 1

        resolved_files.append((pending_path, file_resolved, file_unresolved))

    # Append new rows to real_labels.csv
    if new_rows:
        write_header = not os.path.exists(REAL_LABELS_PATH)
        with open(REAL_LABELS_PATH, "a", newline="") as f:
            writer = csv.DictWriter(f, fieldnames=REAL_LABELS_COLS)
            if write_header:
                writer.writeheader()
            writer.writerows(new_rows)
        print(f"Appended {len(new_rows)} real training rows → real_labels.csv")
    else:
        print("No new real labels to append.")

    if unresolved_count:
        print(f"  {unresolved_count} player/date(s) had no matching game log entry (may be postponed games).")

    # Remove resolved pending files
    for pending_path, resolved, unresolved in resolved_files:
        if unresolved == 0:
            os.remove(pending_path)
            print(f"  Removed {os.path.basename(pending_path)}")
        else:
            print(f"  Kept {os.path.basename(pending_path)} ({unresolved} unresolved entries — will retry tomorrow)")

    # Summary
    total_real = 0
    if os.path.exists(REAL_LABELS_PATH):
        total_real = sum(1 for _ in open(REAL_LABELS_PATH)) - 1  # subtract header
    print(f"  real_labels.csv total: {total_real} rows")


if __name__ == "__main__":
    main()
