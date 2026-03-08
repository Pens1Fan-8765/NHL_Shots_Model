# Workflow: Prediction Model

**Owner:** You (`feature/prediction-model` branch)
**Purpose:** Build features from Layer 1 data and generate daily SOG projections
**Depends on:** Data pipeline outputs in `.tmp/`

---

## Objective

Transform raw NHL data into engineered features, train a prediction model on historical labeled data, and produce daily confidence-scored projections for each player.

---

## Two-Phase Model Approach

### Phase 1 — Weighted Average Baseline (start here)

Weighted average of rolling SOG averages:
- `sog_avg_5` × 0.40 (most recent form)
- `sog_avg_10` × 0.35
- `sog_avg_20` × 0.25 (season baseline)

**Pros:** No training data required. Works immediately.
**Cons:** Does not account for matchup, opponent defense, or situational factors.

Run this first. Use it for the first few weeks while collecting labeled data.

### Phase 2 — Logistic Regression (once you have 50+ labeled games)

Predicts P(player SOG > sportsbook line) using all engineered features.
Train on: historical features + actual SOG outcomes + the line that was offered.

**Target metric:** ROC-AUC > 0.58, Hit Rate > 55% on held-out data.

---

## Tools

| Tool | Command | Purpose |
|------|---------|---------|
| `build_features.py` | `python tools/build_features.py` | Build today's feature set |
| `train_model.py` | `python tools/train_model.py --features ... --labels ...` | Train on historical data |
| `predict_shots.py` | `python tools/predict_shots.py` | Generate today's projections |
| `backtest.py` | `python tools/backtest.py --features ... --labels ...` | Validate model accuracy |

---

## Daily Routine (after Layer 1 runs)

```bash
# Step 1: Build features from today's data
python tools/build_features.py

# Step 2: Generate predictions
python tools/predict_shots.py
```

Output: `.tmp/predictions_YYYY-MM-DD.csv`

---

## Model Training (weekly or on-demand)

### What you need first: labeled historical data

You must collect:
1. **Features**: Save a copy of `features_YYYY-MM-DD.csv` every day
2. **Labels**: The next day, record each player's actual SOG and what the line was

Label CSV format:
```
player_key,game_date,actual_sog,line,book,over_odds
nathan_mackinnon_COL,2026-03-07,4,3.5,DraftKings,-115
```

Build a growing historical dataset by concatenating daily files:
```bash
# Concatenate all feature files (skip header after first)
head -1 .tmp/features_2026-03-01.csv > historical_features.csv
tail -n +2 .tmp/features_2026-*.csv >> historical_features.csv
```

### Training the model

Once you have at least 50 labeled rows (target: 500+):

```bash
python tools/train_model.py \
  --features historical_features.csv \
  --labels historical_labels.csv
```

Output: `.tmp/model.pkl` and `.tmp/model_meta.json`

### When to retrain

- After every 50 new labeled games (model improves with data)
- At the start of a new season (player performance resets)
- If hit rate drops significantly over 2+ weeks

---

## Backtesting

```bash
python tools/backtest.py \
  --features historical_features.csv \
  --labels historical_labels.csv \
  [--model .tmp/model.pkl]
```

**What to look for:**
- Baseline hit rate > 52% = marginally useful
- Baseline hit rate > 55% = profitable at -110 juice
- Model ROC-AUC > 0.58 = model outperforms random
- High-confidence picks (>65%) hit rate > 60% = very useful for filtering

---

## Features Reference

| Feature | Source | Why it matters |
|---------|--------|---------------|
| `sog_avg_5` | Player logs | Recent form — most predictive short-term signal |
| `sog_avg_10` | Player logs | Medium-term stability |
| `sog_avg_20` | Player logs | Season baseline |
| `sog_vs_opp` | Player logs (filtered) | Historical performance vs. this specific opponent |
| `toi_avg_5` | Player logs | Ice time = opportunity; tracks line changes |
| `opp_sa_per_game_season` | Team defense | Defense quality drives shot volume |
| `opp_sa_per_game_l10` | Team defense | Recent defensive form |
| `home_flag` | Schedule | Players average slightly more SOG at home |
| `b2b_flag` | Player logs dates | Back-to-backs often suppress performance |
| `trend_ratio` | Computed | >1.0 = hot streak, <1.0 = cold streak |
| `xSF_per_60` | MoneyPuck | Shot quality opportunity — independent of recent results |
| `CF_pct` | MoneyPuck | Possession proxy; high CF% = more shot attempts |
| `FF_pct` | MoneyPuck | Unblocked attempts percentage |
| `iSCF_per_60` | MoneyPuck | Individual scoring chance attempts |

---

## Known Issues & Notes

| Issue | Handling |
|-------|---------|
| Player has < 5 game logs | Filtered out in `build_features.py` (MIN_GAMES = 5) |
| Advanced stats player key mismatch | Name normalization in `fetch_advanced_stats.py` and `fetch_player_game_logs.py` must match exactly |
| Model not yet trained | `predict_shots.py` auto-falls back to Phase 1 baseline |
| Low confidence on all players | Normal early in season when rolling averages are noisy |
| Feature drift (player traded/new line) | TOI will reflect new role within 5 games; model retraining picks this up |

---

## Updating This Workflow

If you discover a feature that significantly improves accuracy (e.g., goalie save%, power play time, shot quality from Natural Stat Trick), add it to `build_features.py`, retrain, and document it here.
