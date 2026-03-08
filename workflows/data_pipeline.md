# Workflow: Data Pipeline

**Owner:** Friend A (`feature/data-pipeline` branch)
**Purpose:** Pull all raw NHL data needed for today's predictions and save to `.tmp/`
**Run before:** Feature building (`build_features.py`)

---

## Objective

Collect today's game schedule, player shot histories, team defensive metrics, and advanced stats from MoneyPuck. All outputs land in `.tmp/` and are consumed by Layer 2 (prediction model).

---

## Required Inputs

- None (all data pulled from public APIs)
- `.env` is not required for this layer

---

## Tools & Sequence

Run these scripts **in order**. Steps 2–4 can be parallelized once Step 1 is done.

### Step 1 — Fetch today's schedule

```bash
python tools/fetch_nhl_schedule.py
```

**What it does:** Hits the NHL Stats API and pulls all games scheduled for today.
**Output:** `.tmp/schedule_YYYY-MM-DD.json`
**Check:** Printed game list should match tonight's slate on NHL.com.
**If no games:** Script exits cleanly. Do not run subsequent steps.

---

### Step 2 — Fetch player game logs (run after Step 1)

```bash
python tools/fetch_player_game_logs.py
```

**What it does:**
1. Reads the schedule to find which teams are playing
2. Fetches rosters for each team from the NHL API
3. For each skater (forwards + defensemen), pulls their last 20 game logs
4. Extracts: date, shots on goal, TOI, opponent, home/away flag, goals, assists

**Output:** `.tmp/player_logs_YYYY-MM-DD.json`
**Expected runtime:** ~3–5 minutes (rate-limited at 0.2s per request)
**Check:** Open the file and verify a known player's recent SOG totals against NHL.com game logs

---

### Step 3 — Fetch team defense stats (can run alongside Step 2)

```bash
python tools/fetch_team_defense_stats.py
```

**What it does:** Pulls season-level shots-against per game from the NHL standings endpoint for all teams playing today.
**Output:** `.tmp/team_defense_YYYY-MM-DD.json`
**Note:** The standings API gives season SA totals, not L10. If you want L10 precision, compute it manually from `player_logs` by averaging the opponent's SA from each game. The model currently uses `sa_season` for both `sa_season` and `sa_l10` as a fallback.

---

### Step 4 — Fetch advanced stats from MoneyPuck (can run alongside Step 2)

```bash
python tools/fetch_advanced_stats.py
```

**What it does:** Downloads the full-season skater CSV from MoneyPuck, filters to `situation=all`, and extracts:
- `xSF_per_60` — expected shot attempts per 60 min (shot quality proxy)
- `CF_pct` — Corsi For % (possession metric)
- `FF_pct` — Fenwick For % (unblocked shot attempts)
- `iSCF_per_60` — individual scoring chance attempts per 60

**Output:** `.tmp/advanced_stats_YYYY-MM-DD.csv`
**Note:** MoneyPuck updates daily — no rate limiting concerns.

---

## Expected Outputs Checklist

After running all four scripts, verify these files exist in `.tmp/`:

- [ ] `schedule_YYYY-MM-DD.json` — list of today's games
- [ ] `player_logs_YYYY-MM-DD.json` — dict of player game logs
- [ ] `team_defense_YYYY-MM-DD.json` — dict of team SA stats
- [ ] `advanced_stats_YYYY-MM-DD.csv` — CSV of advanced metrics

---

## Known Issues & Edge Cases

| Scenario | Handling |
|----------|----------|
| No games today | `fetch_nhl_schedule.py` prints "No games found today" and exits — stop here |
| API rate limits | Scripts use `time.sleep()` between requests. If you get 429 errors, increase the sleep interval |
| Player name mismatches | Names are normalized to `first_last_TEAM` format. Check `fetch_player_game_logs.py::make_player_key()` if a player is missing downstream |
| Suspended/injured players | Their logs will still be fetched — Layer 2 filters based on whether they appear in today's lineup |
| New players (rookies) | Will have fewer than 20 game logs — Layer 2 handles this with minimum game thresholds |
| MoneyPuck season rollover | `fetch_advanced_stats.py::current_season_id()` uses October as the season start cutoff |

---

## Verification

After running, spot-check one player:

```bash
python -c "
import json
with open('.tmp/player_logs_$(date +%Y-%m-%d).json') as f:
    logs = json.load(f)
# Check Nathan MacKinnon
key = 'nathan_mackinnon_COL'
if key in logs:
    for game in logs[key][:5]:
        print(game)
"
```

Compare the SOG values to NHL.com's game-by-game stats for that player.

---

## Updating This Workflow

If you discover new edge cases, rate limit behavior, or field name changes in the NHL API response, update this document. Document the change, the date you found it, and how you resolved it.
