"""
backfill_advanced_stats.py

One-time script: fills in xSF_per_60, CF_pct, FF_pct, iSCF_per_60
in historical_features.csv for rows where those values are 0.0.
Downloads current MoneyPuck season data as the source.
"""
import csv
import os
import sys

sys.path.insert(0, os.path.dirname(__file__))
from fetch_advanced_stats import fetch_advanced_stats, process_rows  # reuse existing logic

TMP_DIR = os.path.join(os.path.dirname(__file__), "..", ".tmp")
HIST_PATH = os.path.join(TMP_DIR, "historical_features.csv")
ADV_COLS = ["xSF_per_60", "CF_pct", "FF_pct", "iSCF_per_60"]


def main():
    rows_raw = fetch_advanced_stats()
    moneypuck = process_rows(rows_raw)  # dict: player_key -> {xSF_per_60, CF_pct, FF_pct, iSCF_per_60}
    print(f"MoneyPuck data loaded: {len(moneypuck)} players.")

    with open(HIST_PATH, newline="") as f:
        reader = csv.DictReader(f)
        fieldnames = reader.fieldnames
        rows = list(reader)

    print(f"Loaded {len(rows)} rows from {os.path.basename(HIST_PATH)}.")

    filled = 0
    no_match = 0
    already_set = 0

    for row in rows:
        if float(row.get("xSF_per_60", 0) or 0) == 0.0:
            stats = moneypuck.get(row["player_key"])
            if stats:
                for col in ADV_COLS:
                    row[col] = stats[col]
                filled += 1
            else:
                no_match += 1
        else:
            already_set += 1

    with open(HIST_PATH, "w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)

    print(f"Backfilled {filled} rows.")
    print(f"No MoneyPuck match (stay at 0.0): {no_match} rows.")
    print(f"Already populated (untouched): {already_set} rows.")
    print(f"Saved to {HIST_PATH}")


if __name__ == "__main__":
    main()
