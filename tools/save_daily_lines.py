"""
save_daily_lines.py

Saves today's real sportsbook lines as a pending labels file.
These are the best lines (lowest line = most favorable for over bet)
identified by compare_lines.py.

The pending file is completed the next day by collect_real_labels.py,
which matches each player against their actual SOG from game logs
and appends the completed rows to .tmp/real_labels.csv.

Requires:
  .tmp/best_lines_YYYY-MM-DD.csv (output of compare_lines.py)

Output:
  .tmp/pending_labels_YYYY-MM-DD.csv
  Schema: player_key, game_date, best_line, best_book, best_over_odds
"""

import csv
import os
from datetime import date

import pandas as pd

TMP_DIR = os.path.join(os.path.dirname(__file__), "..", ".tmp")


def main():
    today_str = date.today().isoformat()
    best_lines_path = os.path.join(TMP_DIR, f"best_lines_{today_str}.csv")
    pending_path = os.path.join(TMP_DIR, f"pending_labels_{today_str}.csv")

    if not os.path.exists(best_lines_path):
        print(f"No best_lines file found for {today_str}. Skipping.")
        return

    df = pd.read_csv(best_lines_path)
    df = df[df["best_line"].notna()].copy()

    if df.empty:
        print("No valid lines to save as pending labels.")
        return

    rows = [
        {
            "player_key": row["player_key"],
            "game_date": today_str,
            "best_line": row["best_line"],
            "best_book": row["best_book"],
            "best_over_odds": row["best_over_odds"],
        }
        for _, row in df.iterrows()
    ]

    with open(pending_path, "w", newline="") as f:
        writer = csv.DictWriter(
            f, fieldnames=["player_key", "game_date", "best_line", "best_book", "best_over_odds"]
        )
        writer.writeheader()
        writer.writerows(rows)

    print(f"Saved {len(rows)} pending labels → {os.path.basename(pending_path)}")


if __name__ == "__main__":
    main()
