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
from datetime import date, timedelta

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


def show_yesterday_results():
    """Print yesterday's flagged picks and their actual SOG results."""
    yesterday = (date.today() - timedelta(days=1)).isoformat()
    best_lines_path = os.path.join(TMP_DIR, f"best_lines_{yesterday}.csv")
    real_labels_path = os.path.join(TMP_DIR, "real_labels.csv")

    if not os.path.exists(best_lines_path) or not os.path.exists(real_labels_path):
        return

    # Load yesterday's flagged picks
    flagged = {}
    with open(best_lines_path, newline="") as f:
        for row in csv.DictReader(f):
            if row.get("flagged") == "YES":
                flagged[row["player_key"]] = row

    if not flagged:
        return

    # Load actual results from real_labels.csv filtered to yesterday
    results = {}
    with open(real_labels_path, newline="") as f:
        for row in csv.DictReader(f):
            if row.get("game_date") == yesterday:
                results[row["player_key"]] = row

    print()
    print("=" * 78)
    print(f"  YESTERDAY'S RESULTS — {yesterday}")
    print("=" * 78)
    header = f"  {'PLAYER':<24} {'LINE':>5} {'ACTUAL':>7} {'RESULT':<8} {'CONF':>5}"
    print(header)
    print("  " + "-" * 54)

    hits = 0
    resolved = 0

    for player_key, pick in flagged.items():
        result = results.get(player_key)

        # Fuzzy fallback: match on name prefix without team suffix
        if result is None:
            name_prefix = "_".join(player_key.split("_")[:-1])
            for k, v in results.items():
                if "_".join(k.split("_")[:-1]) == name_prefix:
                    result = v
                    break

        player = format_player_name(player_key)[:24]
        line = float(pick.get("best_line", 0))
        conf = f"{float(pick.get('confidence_score', 0)):.0f}%"
        direction = pick.get("direction", "OVER")

        if result:
            actual = float(result["actual_sog"])
            went_over = actual > line
            hit = (went_over and direction == "OVER") or (not went_over and direction == "UNDER")
            result_str = "HIT" if hit else "MISS"
            if hit:
                hits += 1
            resolved += 1
            print(f"  {player:<24} {line:>5.1f} {actual:>7.1f} {result_str:<8} {conf:>5}")
        else:
            print(f"  {player:<24} {line:>5.1f} {'?':>7} {'No result':<8} {conf:>5}")

    print()
    if resolved > 0:
        print(f"  Record: {hits}/{resolved} ({hits/resolved*100:.0f}%)")
    print("=" * 78)


def main():
    show_yesterday_results()

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
