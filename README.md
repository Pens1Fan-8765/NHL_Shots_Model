# NHL Shots on Goal Model

A prediction system that forecasts whether NHL players will go over or under their shots-on-goal prop lines across DraftKings, FanDuel, BetMGM, and Caesars. Built on the WAT framework (Workflows, Agents, Tools).

---

## Team & Branch Ownership

| Person | Layer | Branch |
|--------|-------|--------|
| Friend A | Data Pipeline | `feature/data-pipeline` |
| You | Prediction Model | `feature/prediction-model` |
| Friend B | Sportsbook + Output | `feature/sportsbook-output` |

All branches merge into `main` via pull request after review.

---

## How the System Works

```
NHL API + MoneyPuck          The Odds API
       |                          |
  [Data Pipeline]           [Odds Fetcher]
       |                          |
  .tmp/ data files         .tmp/ odds files
            \                  /
           [Feature Builder]
                   |
           [Prediction Model]
                   |
           [Report Generator]
              /         \
     Terminal Output   Google Sheet
```

**Daily flow:** Fetch today's schedule → pull player stats & team defense → pull odds → build features → run predictions → compare lines → output picks.

---

## Project Structure

```
tools/                          # Python scripts (deterministic execution)
  fetch_nhl_schedule.py         # Layer 1: Today's NHL games
  fetch_player_game_logs.py     # Layer 1: Player SOG history (last 20 games)
  fetch_team_defense_stats.py   # Layer 1: Team shots-against metrics
  fetch_advanced_stats.py       # Layer 1: MoneyPuck advanced stats (xSF, CF%, etc.)
  build_features.py             # Layer 2: Feature engineering
  train_model.py                # Layer 2: Train/retrain prediction model
  predict_shots.py              # Layer 2: Generate daily projections
  backtest.py                   # Layer 2: Historical validation
  fetch_odds.py                 # Layer 3: Pull lines from all 4 sportsbooks
  compare_lines.py              # Layer 3: Find best line per player
  generate_report.py            # Layer 3: Terminal output of ranked picks
  export_to_sheets.py           # Layer 3: Push picks to Google Sheet

workflows/
  daily_picks.md                # Master daily runbook (run this each day)
  data_pipeline.md              # Layer 1 SOP
  model_training.md             # Layer 2 SOP
  sportsbook_scraping.md        # Layer 3 SOP

.tmp/                           # Intermediate files (auto-generated, gitignored)
.env                            # API keys (never commit this)
```

---

## Setup

### 1. Clone and create your branch

```bash
git clone <repo-url>
cd NHL_Shots_Model

# Friend A (data pipeline)
git checkout -b feature/data-pipeline

# You (prediction model)
git checkout -b feature/prediction-model

# Friend B (sportsbook + output)
git checkout -b feature/sportsbook-output
```

### 2. Create a virtual environment

```bash
python3 -m venv venv
source venv/bin/activate       # Mac/Linux
# venv\Scripts\activate        # Windows
pip install -r requirements.txt
```

### 3. Add your API keys to `.env`

```
ODDS_API_KEY=your_key_from_theoddsapi_com
GOOGLE_SHEETS_ID=your_shared_sheet_id
```

- **The Odds API**: Sign up free at theoddsapi.com (500 requests/month on free tier)
- **Google Sheets**: Create a shared Sheet, copy the ID from the URL

### 4. Google Sheets auth (one-time, Friend B only)

```bash
# Follow the workflow: workflows/sportsbook_scraping.md
# You'll need credentials.json from Google Cloud Console
python tools/export_to_sheets.py
# A browser window will open to authorize — token.json is saved locally
```

---

## Running Daily Picks

See `workflows/daily_picks.md` for the full runbook. Quick version:

```bash
python tools/fetch_nhl_schedule.py
python tools/fetch_player_game_logs.py
python tools/fetch_team_defense_stats.py
python tools/fetch_advanced_stats.py
python tools/fetch_odds.py
python tools/build_features.py
python tools/predict_shots.py
python tools/compare_lines.py
python tools/generate_report.py
python tools/export_to_sheets.py
```

---

## Layer Details

### Layer 1 — Data Pipeline (`feature/data-pipeline`)

**Owner: Friend A**

Pulls all raw data and saves to `.tmp/`:

| Tool | Source | Output |
|------|--------|--------|
| `fetch_nhl_schedule.py` | NHL Stats API | Today's games + team IDs |
| `fetch_player_game_logs.py` | NHL Stats API | Last 20 game SOG, TOI, opponent |
| `fetch_team_defense_stats.py` | NHL Stats API | Team SA/game (season + L10) |
| `fetch_advanced_stats.py` | MoneyPuck CSV | xSF, CF%, FF%, iSCF per player |

See `workflows/data_pipeline.md` for full SOP.

---

### Layer 2 — Prediction Model (`feature/prediction-model`)

**Owner: You**

Features engineered per player:

| Feature | Description |
|---------|-------------|
| `sog_avg_5/10/20` | Rolling SOG averages |
| `sog_vs_opp` | Avg SOG against today's opponent |
| `opp_sa_per_game_season` | How many shots opp allows per game |
| `opp_sa_per_game_l10` | Opponent's last 10 game SA/game |
| `toi_avg_5` | Avg ice time last 5 games |
| `home_flag` | 1 = home, 0 = away |
| `b2b_flag` | 1 = back-to-back game |
| `trend_ratio` | sog_avg_5 / sog_avg_20 (hot/cold streak) |
| `xSF_per_60` | Expected shot attempts per 60 min |

**Model approach:**
- Phase 1: Weighted average baseline (5-game: 40%, 10-game: 35%, 20-game: 25%)
- Phase 2: Logistic regression — predicts P(SOG > line)
- Confidence score: 0–100% probability of going over the line

See `workflows/model_training.md` for training/retraining SOP.

---

### Layer 3 — Sportsbook + Output (`feature/sportsbook-output`)

**Owner: Friend B**

| Tool | What it does |
|------|-------------|
| `fetch_odds.py` | Pulls SOG prop lines from DK, FD, BetMGM, Caesars via The Odds API |
| `compare_lines.py` | Finds best line per player, flags line shopping opportunities |
| `generate_report.py` | Prints ranked picks to terminal |
| `export_to_sheets.py` | Pushes picks to shared Google Sheet |

**Terminal output format:**
```
=== NHL SHOTS ON GOAL PICKS — 2026-03-07 ===

RANK  PLAYER              TEAM  OPP   PROJ  LINE   BOOK        CONF   EDGE
1     Nathan MacKinnon    COL   EDM   4.8   O 3.5  DraftKings  78%    +1.3
2     David Pastrnak      BOS   TBL   3.9   O 3.5  FanDuel     71%    +0.4
3     Auston Matthews     TOR   MTL   4.2   O 3.5  BetMGM      67%    +0.7
```

**Google Sheet columns:**
`Date | Player | Team | Opp | Proj SOG | Line | O/U | Book | Odds | Confidence | Edge | Result`

Result is filled in the next day with actual SOG for tracking accuracy.

See `workflows/sportsbook_scraping.md` for full SOP.

---

## Shared Data Interfaces

All tools share these conventions so branches integrate cleanly:

- **Player key**: `{first_last}_{team_abbr}` — e.g., `nathan_mackinnon_COL`
- **Date key**: `YYYY-MM-DD`
- **All intermediate files**: `.tmp/` directory

| File | Schema |
|------|--------|
| `.tmp/schedule_YYYY-MM-DD.json` | `[{game_id, home_team, away_team, game_time}]` |
| `.tmp/player_logs_YYYY-MM-DD.json` | `{player_key: [{date, sog, toi, opponent, home}]}` |
| `.tmp/team_defense_YYYY-MM-DD.json` | `{team_abbr: {sa_season, sa_l10}}` |
| `.tmp/advanced_stats_YYYY-MM-DD.csv` | `player_key, xSF_per_60, CF_pct, FF_pct, iSCF` |
| `.tmp/features_YYYY-MM-DD.csv` | One row per player, all engineered features |
| `.tmp/predictions_YYYY-MM-DD.csv` | `player_key, game_date, projected_sog, confidence_score` |
| `.tmp/odds_YYYY-MM-DD.json` | `{player_key: [{book, line, over_odds, under_odds}]}` |
| `.tmp/best_lines_YYYY-MM-DD.csv` | `player_key, best_line, best_book, best_over_odds, line_spread` |

---

## Verification Checklist

- [ ] **Data**: Run `fetch_player_game_logs.py` for MacKinnon, manually verify SOG values match NHL.com
- [ ] **Model**: Run `backtest.py` on prior season — target >55% hit rate minimum
- [ ] **Odds**: Run `fetch_odds.py` on a game day, spot-check lines against DraftKings app
- [ ] **Integration**: Run full daily flow, verify Google Sheet populates correctly
- [ ] **Accuracy tracking**: Fill in Result column next-day, track rolling hit rate in Sheet

---

## Git Workflow

```bash
# Work on your branch
git add tools/your_file.py
git commit -m "feat: add fetch_player_game_logs tool"
git push origin feature/your-branch

# When ready to merge, open a pull request to main on GitHub
# At least one other person should review before merging
```

Commit message convention: `feat:`, `fix:`, `refactor:`, `docs:`
