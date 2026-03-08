"""
backtest.py

Validates the prediction model against historical data.

Usage:
  python tools/backtest.py --features path/to/historical_features.csv \\
                            --labels path/to/historical_labels.csv \\
                            [--model path/to/model.pkl]

Labels CSV: player_key, game_date, actual_sog, line, book, over_odds

Output: .tmp/backtest_results.csv + printed summary
"""

import argparse
import csv
import os
import sys
from collections import defaultdict

import joblib
import pandas as pd
from sklearn.metrics import accuracy_score, roc_auc_score

TMP_DIR = os.path.join(os.path.dirname(__file__), "..", ".tmp")
DEFAULT_MODEL_PATH = os.path.join(TMP_DIR, "model.pkl")

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


def implied_prob(american_odds: float) -> float:
    if american_odds > 0:
        return 100 / (american_odds + 100)
    return abs(american_odds) / (abs(american_odds) + 100)


def roi_at_odds(correct: bool, odds: float) -> float:
    """Return profit/loss per $1 wagered."""
    if correct:
        if odds > 0:
            return odds / 100
        else:
            return 100 / abs(odds)
    return -1.0


def run_backtest(features_df: pd.DataFrame, labels_df: pd.DataFrame, model=None) -> pd.DataFrame:
    df = features_df.merge(labels_df, on=["player_key", "game_date"], how="inner")
    print(f"Backtesting on {len(df)} labeled games.")

    df["went_over"] = (df["actual_sog"] > df["line"]).astype(int)
    df["baseline_proj"] = baseline_projection(df)
    df["baseline_pred_over"] = (df["baseline_proj"] > df["line"]).astype(int)

    if model is not None:
        X = df[FEATURE_COLS].fillna(0)
        df["model_prob"] = model.predict_proba(X)[:, 1]
        df["model_pred_over"] = (df["model_prob"] >= 0.5).astype(int)
    else:
        df["model_prob"] = None
        df["model_pred_over"] = df["baseline_pred_over"]

    return df


def print_summary(df: pd.DataFrame) -> None:
    total = len(df)
    print(f"\n{'='*60}")
    print(f"BACKTEST RESULTS — {total} games")
    print(f"{'='*60}")

    # Baseline stats
    baseline_correct = (df["baseline_pred_over"] == df["went_over"]).sum()
    baseline_acc = baseline_correct / total
    print(f"\nPhase 1 (Weighted Average Baseline):")
    print(f"  Hit rate:  {baseline_acc:.3f} ({baseline_acc*100:.1f}%)")
    print(f"  Picks hit: {baseline_correct}/{total}")

    # Model stats (if available)
    if df["model_prob"].notna().any():
        model_correct = (df["model_pred_over"] == df["went_over"]).sum()
        model_acc = model_correct / total
        model_auc = roc_auc_score(df["went_over"], df["model_prob"])
        print(f"\nPhase 2 (Logistic Regression):")
        print(f"  Hit rate:  {model_acc:.3f} ({model_acc*100:.1f}%)")
        print(f"  ROC-AUC:   {model_auc:.3f}")
        print(f"  Picks hit: {model_correct}/{total}")

    # ROI calculation (assumes -110 odds unless actual odds provided)
    if "over_odds" in df.columns:
        odds_col = pd.to_numeric(df["over_odds"], errors="coerce").fillna(-110)
    else:
        odds_col = pd.Series([-110.0] * total)

    roi_values = [
        roi_at_odds(row["baseline_pred_over"] == row["went_over"], odds)
        for (_, row), odds in zip(df.iterrows(), odds_col)
    ]
    total_roi = sum(roi_values)
    roi_pct = total_roi / total * 100
    print(f"\nROI (on all baseline picks, -110 assumed if no odds data):")
    print(f"  Total P/L per $1: {total_roi:+.2f}")
    print(f"  ROI %:            {roi_pct:+.1f}%")

    # Confidence-filtered analysis
    if df["model_prob"].notna().any():
        for threshold in [0.60, 0.65, 0.70]:
            high_conf = df[df["model_prob"] >= threshold]
            if len(high_conf) == 0:
                continue
            hc_acc = (high_conf["model_pred_over"] == high_conf["went_over"]).mean()
            print(f"\nFiltered picks (confidence >= {int(threshold*100)}%):")
            print(f"  Count:    {len(high_conf)}")
            print(f"  Hit rate: {hc_acc:.3f} ({hc_acc*100:.1f}%)")

    print(f"\n{'='*60}")
    print("Target: >55% hit rate = model adds value at standard -110 juice")
    print(f"{'='*60}\n")


def main():
    parser = argparse.ArgumentParser(description="Backtest the NHL SOG model")
    parser.add_argument("--features", required=True, help="Historical features CSV")
    parser.add_argument("--labels", required=True, help="Historical labels CSV (player_key, game_date, actual_sog, line)")
    parser.add_argument("--model", default=DEFAULT_MODEL_PATH, help="Path to model.pkl")
    args = parser.parse_args()

    for path in [args.features, args.labels]:
        if not os.path.exists(path):
            print(f"File not found: {path}")
            sys.exit(1)

    model = None
    if os.path.exists(args.model):
        model = joblib.load(args.model)
        print(f"Loaded model from {args.model}")
    else:
        print(f"No model found at {args.model} — running baseline only.")

    features_df = pd.read_csv(args.features)
    labels_df = pd.read_csv(args.labels)

    df = run_backtest(features_df, labels_df, model)
    print_summary(df)

    out_path = os.path.join(TMP_DIR, "backtest_results.csv")
    df.to_csv(out_path, index=False)
    print(f"Full results saved to {out_path}")


if __name__ == "__main__":
    main()
