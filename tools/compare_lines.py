"""
compare_lines.py

Joins today's predictions with sportsbook odds to find:
- Best line per player (highest over line = most favorable)
- Line shopping opportunities (>0.5 SOG spread across books)
- Final confidence score calibrated against actual sportsbook line

Requires:
  .tmp/predictions_YYYY-MM-DD.csv
  .tmp/odds_YYYY-MM-DD.json

Output: .tmp/best_lines_YYYY-MM-DD.csv

Schema: player_key, team, opponent, projected_sog, confidence_score,
        best_line, best_book, odds, line_spread, edge,
        flagged, direction
"""

import csv
import json
import os
from datetime import date

import pandas as pd

TMP_DIR = os.path.join(os.path.dirname(__file__), "..", ".tmp")
CONFIDENCE_THRESHOLD = 60.0


def implied_prob_to_pct(american_odds: float) -> float:
    if american_odds > 0:
        return 100 / (american_odds + 100)
    return abs(american_odds) / (abs(american_odds) + 100)


def confidence_vs_line(projected_sog: float, line: float) -> float:
    """Heuristic confidence when no ML model is available."""
    diff = projected_sog - line
    conf = 50 + diff * 10
    return round(max(10.0, min(90.0, conf)), 1)


def main():
    today_str = date.today().isoformat()
    preds_path = os.path.join(TMP_DIR, f"predictions_{today_str}.csv")
    odds_path = os.path.join(TMP_DIR, f"odds_{today_str}.json")

    if not os.path.exists(preds_path):
        print(f"Predictions file not found: {preds_path}")
        print("Run predict_shots.py first.")
        return

    if not os.path.exists(odds_path):
        print(f"Odds file not found: {odds_path}")
        print("Run fetch_odds.py first.")
        return

    preds_df = pd.read_csv(preds_path)
    with open(odds_path) as f:
        odds = json.load(f)

    results = []
    no_odds_count = 0

    for _, pred in preds_df.iterrows():
        player_key = pred["player_key"]
        projected_sog = float(pred["projected_sog"])
        model_confidence = float(pred["confidence_score"])

        # Try exact match first, then fuzzy match (name without team suffix)
        player_odds = odds.get(player_key, [])

        # Fuzzy fallback: strip team suffix and search
        if not player_odds:
            name_prefix = "_".join(player_key.split("_")[:-1])
            for k, v in odds.items():
                if k.startswith(name_prefix + "_"):
                    player_odds = v
                    break

        if not player_odds:
            no_odds_count += 1
            continue

        # Find best line (highest line = most favorable for over bet)
        # and worst line (for spread calculation)
        best_line = None
        best_book = None
        best_over_odds = None
        best_under_odds = None
        worst_line = None

        for entry in player_odds:
            line = entry.get("line")
            over_odds = entry.get("over_odds")
            under_odds = entry.get("under_odds")
            book = entry.get("book", "")

            if line is None:
                continue

            if worst_line is None or line > worst_line:
                worst_line = line

            # Lower line = better for over bet (easier to hit)
            # Tie-break: when lines are equal, prefer better over odds (less juice)
            is_better_line = best_line is None or line < best_line
            is_tied_better_odds = (
                line == best_line
                and over_odds is not None
                and (best_over_odds is None or over_odds > best_over_odds)
            )
            if is_better_line or is_tied_better_odds:
                best_line = line
                best_book = book
                best_over_odds = over_odds
                best_under_odds = under_odds

        if best_line is None:
            no_odds_count += 1
            continue

        line_spread = round((worst_line - best_line) if worst_line else 0, 1)
        edge = round(projected_sog - best_line, 2)

        # Use model confidence if available, otherwise heuristic
        if pred.get("method") == "logistic_regression":
            final_confidence = model_confidence
        else:
            final_confidence = confidence_vs_line(projected_sog, best_line)

        direction = "OVER" if projected_sog > best_line else "UNDER"
        flagged = "YES" if final_confidence >= CONFIDENCE_THRESHOLD and abs(edge) >= 0.3 else "NO"

        # Flag line shopping opportunities
        shopping_flag = "YES" if line_spread >= 0.5 else "NO"

        # Report the odds for the direction we're actually betting
        reported_odds = best_over_odds if direction == "OVER" else best_under_odds

        results.append({
            "player_key": player_key,
            "team": pred["team"],
            "opponent": pred["opponent"],
            "projected_sog": projected_sog,
            "confidence_score": final_confidence,
            "best_line": best_line,
            "best_book": best_book,
            "odds": reported_odds if reported_odds is not None else "N/A",
            "line_spread": line_spread,
            "edge": edge,
            "direction": direction,
            "flagged": flagged,
            "line_shopping": shopping_flag,
        })

    # Sort by confidence descending
    results.sort(key=lambda x: x["confidence_score"], reverse=True)

    out_path = os.path.join(TMP_DIR, f"best_lines_{today_str}.csv")
    fieldnames = [
        "player_key", "team", "opponent", "projected_sog", "confidence_score",
        "best_line", "best_book", "odds", "line_spread",
        "edge", "direction", "flagged", "line_shopping",
    ]
    with open(out_path, "w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(results)

    flagged_count = sum(1 for r in results if r["flagged"] == "YES")
    shopping_count = sum(1 for r in results if r["line_shopping"] == "YES")

    print(f"Processed {len(results)} players with odds ({no_odds_count} had no lines).")
    print(f"Flagged plays:         {flagged_count}")
    print(f"Line shopping alerts:  {shopping_count}")
    print(f"Saved to {out_path}")


if __name__ == "__main__":
    main()
