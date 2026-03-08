"""
train_model.py

Trains the shots-on-goal prediction model on historical feature data.

Phase 1: Weighted average baseline projection (no ML required to run this)
Phase 2: Logistic regression — predicts P(SOG > line) given engineered features

Usage:
  python tools/train_model.py --features path/to/historical_features.csv \\
                               --labels path/to/historical_labels.csv

Historical features CSV: same schema as features_YYYY-MM-DD.csv
Historical labels CSV: player_key, game_date, actual_sog, line

Output: .tmp/model.pkl (scikit-learn pipeline)

NOTE: You need historical data to train. Collect several weeks of
      features + actual results before running this. See workflows/model_training.md.
"""

import argparse
import csv
import json
import os
import sys

import joblib
import pandas as pd
from sklearn.linear_model import LogisticRegression
from sklearn.metrics import roc_auc_score, accuracy_score
from sklearn.model_selection import cross_val_score
from sklearn.pipeline import Pipeline
from sklearn.preprocessing import StandardScaler

TMP_DIR = os.path.join(os.path.dirname(__file__), "..", ".tmp")
MODEL_PATH = os.path.join(TMP_DIR, "model.pkl")

FEATURE_COLS = [
    "sog_avg_5", "sog_avg_10", "sog_avg_20",
    "sog_vs_opp", "toi_avg_5",
    "opp_sa_per_game_season", "opp_sa_per_game_l10",
    "trend_ratio", "xSF_per_60", "CF_pct", "FF_pct", "iSCF_per_60",
    "home_flag", "b2b_flag",
]

# Phase 1 weights for baseline projection
BASELINE_WEIGHTS = {"sog_avg_5": 0.40, "sog_avg_10": 0.35, "sog_avg_20": 0.25}


def baseline_projection(df: pd.DataFrame) -> pd.Series:
    """Weighted average baseline — no ML required."""
    return (
        df["sog_avg_5"] * BASELINE_WEIGHTS["sog_avg_5"]
        + df["sog_avg_10"] * BASELINE_WEIGHTS["sog_avg_10"]
        + df["sog_avg_20"] * BASELINE_WEIGHTS["sog_avg_20"]
    )


def load_labels(labels_path: str) -> pd.DataFrame:
    """Load labels, merging real sportsbook lines over estimated ones where available.

    real_labels.csv is built daily by collect_real_labels.py and contains actual
    sportsbook lines. Where a (player_key, game_date) exists in both files, the
    real line is used instead of the estimated proxy.
    """
    base = pd.read_csv(labels_path)
    real_path = os.path.join(TMP_DIR, "real_labels.csv")

    if not os.path.exists(real_path):
        print("No real_labels.csv found — using estimated lines only.")
        return base

    real = pd.read_csv(real_path)
    real_count = len(real)

    # Tag sources so we can report the split
    base["_source"] = "estimated"
    real["_source"] = "real"

    # Merge: keep all rows from base, replace with real where (player_key, game_date) matches
    merged = pd.concat([base, real], ignore_index=True)
    # Sort so real rows come last, then deduplicate keeping last (= real)
    merged = merged.sort_values("_source").drop_duplicates(
        subset=["player_key", "game_date"], keep="last"
    ).drop(columns=["_source"])

    real_used = len(merged[merged.index.isin(real.index)])
    print(f"Labels: {len(base)} estimated + {real_count} real → {len(merged)} total ({real_count} real lines used)")
    return merged


def train(features_path: str, labels_path: str) -> None:
    os.makedirs(TMP_DIR, exist_ok=True)

    # Load and merge
    features_df = pd.read_csv(features_path)
    labels_df = load_labels(labels_path)

    df = features_df.merge(labels_df, on=["player_key", "game_date"], how="inner")
    print(f"Training on {len(df)} labeled examples.")

    if len(df) < 50:
        print("Warning: fewer than 50 training examples. Consider collecting more data before trusting the model.")

    # Target: 1 = went over the line, 0 = under
    df["went_over"] = (df["actual_sog"] > df["line"]).astype(int)

    X = df[FEATURE_COLS].fillna(0)
    y = df["went_over"]

    # --- Phase 1: Baseline projection evaluation ---
    df["baseline_proj"] = baseline_projection(df)
    df["baseline_pred"] = (df["baseline_proj"] > df["line"]).astype(int)
    baseline_acc = accuracy_score(y, df["baseline_pred"])
    print(f"\nPhase 1 Baseline accuracy: {baseline_acc:.3f} ({baseline_acc*100:.1f}%)")

    # --- Phase 2: Logistic regression ---
    pipeline = Pipeline([
        ("scaler", StandardScaler()),
        ("clf", LogisticRegression(max_iter=1000, C=1.0, class_weight="balanced")),
    ])

    cv_scores = cross_val_score(pipeline, X, y, cv=5, scoring="roc_auc")
    print(f"\nPhase 2 Logistic Regression (5-fold CV):")
    print(f"  ROC-AUC scores: {[round(s, 3) for s in cv_scores]}")
    print(f"  Mean ROC-AUC:   {cv_scores.mean():.3f} (+/- {cv_scores.std():.3f})")

    # Fit on full dataset
    pipeline.fit(X, y)

    # Full-set accuracy
    y_pred = pipeline.predict(X)
    y_prob = pipeline.predict_proba(X)[:, 1]
    full_acc = accuracy_score(y, y_pred)
    full_auc = roc_auc_score(y, y_prob)
    print(f"  Full-set accuracy: {full_acc:.3f}")
    print(f"  Full-set ROC-AUC:  {full_auc:.3f}")

    # Save
    joblib.dump(pipeline, MODEL_PATH)
    print(f"\nModel saved to {MODEL_PATH}")

    # Save metadata
    meta = {
        "trained_on": len(df),
        "baseline_accuracy": round(baseline_acc, 4),
        "cv_roc_auc_mean": round(float(cv_scores.mean()), 4),
        "cv_roc_auc_std": round(float(cv_scores.std()), 4),
        "full_accuracy": round(full_acc, 4),
        "full_roc_auc": round(full_auc, 4),
        "feature_cols": FEATURE_COLS,
    }
    meta_path = os.path.join(TMP_DIR, "model_meta.json")
    with open(meta_path, "w") as f:
        json.dump(meta, f, indent=2)
    print(f"Metadata saved to {meta_path}")


def main():
    parser = argparse.ArgumentParser(description="Train the NHL SOG prediction model")
    parser.add_argument("--features", required=True, help="Path to historical features CSV")
    parser.add_argument("--labels", required=True, help="Path to historical labels CSV (player_key, game_date, actual_sog, line)")
    args = parser.parse_args()

    if not os.path.exists(args.features):
        print(f"Features file not found: {args.features}")
        sys.exit(1)
    if not os.path.exists(args.labels):
        print(f"Labels file not found: {args.labels}")
        sys.exit(1)

    train(args.features, args.labels)


if __name__ == "__main__":
    main()
