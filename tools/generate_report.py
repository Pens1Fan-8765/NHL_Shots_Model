"""
generate_report.py

Prints a ranked picks report to the terminal based on today's best lines
and confidence scores.

Requires:
  .tmp/best_lines_YYYY-MM-DD.csv

Filters to plays where: flagged == YES (confidence >= 60%, edge >= 0.3)
"""

import csv
import os
from datetime import date

TMP_DIR = os.path.join(os.path.dirname(__file__), "..", ".tmp")
CONFIDENCE_THRESHOLD = 60.0


def format_player_name(player_key: str) -> str:
    """Convert nathan_mackinnon_COL -> Nathan MacKinnon"""
    parts = player_key.rsplit("_", 1)[0]  # remove team suffix
    words = parts.split("_")
    return " ".join(w.capitalize() for w in words)


def format_odds(odds_val: str) -> str:
    try:
        n = int(float(odds_val))
        return f"+{n}" if n > 0 else str(n)
    except (ValueError, TypeError):
        return str(odds_val)


def main():
    today_str = date.today().isoformat()
    best_lines_path = os.path.join(TMP_DIR, f"best_lines_{today_str}.csv")

    if not os.path.exists(best_lines_path):
        print(f"Best lines file not found: {best_lines_path}")
        print("Run compare_lines.py first.")
        return

    rows = []
    with open(best_lines_path, newline="") as f:
        reader = csv.DictReader(f)
        for row in reader:
            rows.append(row)

    # Filter to flagged plays only, sorted by confidence
    flagged = [r for r in rows if r.get("flagged") == "YES"]
    flagged.sort(key=lambda x: float(x.get("confidence_score", 0)), reverse=True)

    # Also prepare line shopping alerts
    shopping = [r for r in rows if r.get("line_shopping") == "YES"]

    print()
    print("=" * 78)
    print(f"  NHL SHOTS ON GOAL PICKS — {today_str}")
    print("=" * 78)

    if not flagged:
        print("\n  No high-confidence plays found today.")
        print(f"  (Threshold: confidence >= {CONFIDENCE_THRESHOLD}%, edge >= 0.3 SOG)\n")
    else:
        header = f"{'RANK':<5} {'PLAYER':<22} {'TEAM':<5} {'OPP':<5} {'PROJ':>5} {'LINE':>6} {'DIR':<6} {'BOOK':<12} {'ODDS':>6} {'CONF':>6} {'EDGE':>6}"
        print()
        print(header)
        print("-" * 78)

        for rank, row in enumerate(flagged, start=1):
            player = format_player_name(row["player_key"])[:22]
            team = row.get("team", "")[:4]
            opp = row.get("opponent", "")[:4]
            proj = row.get("projected_sog", "")
            line = row.get("best_line", "")
            direction = row.get("direction", "OVER")
            book = row.get("best_book", "")[:12]
            odds = format_odds(row.get("best_over_odds", "N/A"))
            conf = f"{float(row.get('confidence_score', 0)):.0f}%"
            edge = f"{float(row.get('edge', 0)):+.2f}"

            line_str = f"{direction[:1]} {line}"

            print(
                f"{rank:<5} {player:<22} {team:<5} {opp:<5} {float(proj):>5.2f} "
                f"{line_str:>6} {direction:<6} {book:<12} {odds:>6} {conf:>6} {edge:>6}"
            )

    # Line shopping section
    if shopping:
        print()
        print("-" * 78)
        print("  LINE SHOPPING ALERTS (>= 0.5 SOG spread across books)")
        print("-" * 78)
        for row in shopping[:10]:
            player = format_player_name(row["player_key"])
            spread = row.get("line_spread", "")
            best_book = row.get("best_book", "")
            best_line = row.get("best_line", "")
            print(f"  {player:<25} spread: {spread} SOG  |  best: {best_line} @ {best_book}")

    print()
    print(f"  Total flagged plays: {len(flagged)}")
    print(f"  Model method: see .tmp/predictions_{today_str}.csv")
    print("=" * 78)
    print()


if __name__ == "__main__":
    main()
