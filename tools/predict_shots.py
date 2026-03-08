"""
predict_shots.py

Generates today's shots-on-goal projections for all players in today's games.

If a trained model (.tmp/model.pkl) exists, uses Phase 2 logistic regression
for confidence scores. Otherwise falls back to Phase 1 weighted average baseline.

Requires:
  .tmp/features_YYYY-MM-DD.csv
  .tmp/model.pkl (optional — falls back to baseline if missing)
  .tmp/odds_YYYY-MM-DD.json (optional — used to compare projection vs. lines)

Output: .tmp/predictions_YYYY-MM-DD.csv

Schema: player_key, game_date, projected_sog, confidence_score, method
"""

import csv
import json
import os
from datetime import date

import pandas as pd

TMP_DIR = os.path.join(os.path.dirname(__file__), "..", ".tmp")
MODEL_PATH = os.path.join(TMP_DIR, "model.pkl")
CONFIDENCE_THRESHOLD = 60.0  # minimum confidence to flag as a recommended play

FEATURE_COLS = [
    "sog_avg_5", "sog_avg_10", "sog_avg_20",
    "sog_vs_opp", "toi_avg_5",
    "opp_sa_per_game_season", "opp_sa_per_game_l10",
    "trend_ratio", "xSF_per_60", "CF_pct", "FF_pct", "iSCF_per_60",
    "home_flag", "b2b_flag",
]

BASELINE_WEIGHTS = {"sog_avg_5": 0.40, "sog_avg_10": 0.35, "sog_avg_20": 0.25}


def baseline_projection(df: pd.DataFrame) -> pd.Series:
    return (
        df["sog_avg_5"] * BASELINE_WEIGHTS["sog_avg_5"]
        + df["sog_avg_10"] * BASELINE_WEIGHTS["sog_avg_10"]
        + df["sog_avg_20"] * BASELINE_WEIGHTS["sog_avg_20"]
    )


def load_model():
    if not os.path.exists(MODEL_PATH):
        return None
    try:
        import joblib
        return joblib.load(MODEL_PATH)
    except Exception as e:
        print(f"Warning: could not load model ({e}). Using baseline.")
        return None


def load_odds(today_str: str) -> dict[str, list]:
    odds_path = os.path.join(TMP_DIR, f"odds_{today_str}.json")
    if not os.path.exists(odds_path):
        return {}
    with open(odds_path) as f:
        return json.load(f)


def american_to_implied_prob(odds: int) -> float:
    if odds > 0:
        return 100 / (odds + 100)
    return abs(odds) / (abs(odds) + 100)


def main():
    today_str = date.today().isoformat()
    features_path = os.path.join(TMP_DIR, f"features_{today_str}.csv")

    df = pd.read_csv(features_path)
    print(f"Loaded features for {len(df)} players.")

    model = load_model()
    odds = load_odds(today_str)

    predictions = []

    if model is not None:
        print("Using Phase 2: Logistic Regression model.")
        X = df[FEATURE_COLS].fillna(0)
        probs = model.predict_proba(X)[:, 1]  # P(went over)
        proj = baseline_projection(df)  # still use weighted avg for the SOG projection
        method = "logistic_regression"
    else:
        print("No model found — using Phase 1: Weighted Average baseline.")
        proj = baseline_projection(df)
        probs = None
        method = "baseline"

    for i, row in df.iterrows():
        player_key = row["player_key"]
        projected_sog = round(float(proj.iloc[i]), 2)

        if probs is not None:
            # Model confidence: P(over line)
            # We use P(SOG > avg line) as a proxy since we don't know the line yet
            confidence = round(float(probs[i]) * 100, 1)
        else:
            # Baseline confidence heuristic:
            # Higher rolling average relative to a typical line (3.5) = higher confidence
            # This is a rough proxy until lines are loaded
            avg_line = 3.5  # will be refined in compare_lines.py
            if projected_sog > avg_line:
                edge = projected_sog - avg_line
                confidence = min(round(50 + edge * 10, 1), 90.0)
            else:
                edge = avg_line - projected_sog
                confidence = max(round(50 - edge * 10, 1), 10.0)

        # Check against actual book line if available
        player_odds = odds.get(player_key, [])
        line_comparison = []
        for book_entry in player_odds:
            line = book_entry.get("line", None)
            over_odds = book_entry.get("over_odds", None)
            book = book_entry.get("book", "")
            if line is not None:
                edge_vs_line = round(projected_sog - line, 2)
                # Recalculate confidence against actual line if model is available
                if probs is not None:
                    conf_vs_line = confidence  # already model-based
                else:
                    if projected_sog > line:
                        e = projected_sog - line
                        conf_vs_line = min(round(50 + e * 10, 1), 90.0)
                    else:
                        e = line - projected_sog
                        conf_vs_line = max(round(50 - e * 10, 1), 10.0)
                line_comparison.append({
                    "book": book, "line": line, "over_odds": over_odds,
                    "edge": edge_vs_line, "confidence": conf_vs_line,
                })

        predictions.append({
            "player_key": player_key,
            "team": row["team"],
            "opponent": row["opponent"],
            "game_date": today_str,
            "projected_sog": projected_sog,
            "confidence_score": confidence,
            "method": method,
            "flagged": "YES" if confidence >= CONFIDENCE_THRESHOLD else "NO",
        })

    out_path = os.path.join(TMP_DIR, f"predictions_{today_str}.csv")
    fieldnames = [
        "player_key", "team", "opponent", "game_date",
        "projected_sog", "confidence_score", "method", "flagged",
    ]
    with open(out_path, "w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(predictions)

    flagged = [p for p in predictions if p["flagged"] == "YES"]
    print(f"\nPredictions: {len(predictions)} players, {len(flagged)} flagged (confidence >= {CONFIDENCE_THRESHOLD}%)")
    print(f"Saved to {out_path}")


if __name__ == "__main__":
    main()
