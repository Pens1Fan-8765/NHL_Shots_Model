# Workflow: Data Pipeline

**Owner:** Friend A (`feature/data-pipeline` branch)
**Purpose:** Pull all raw NHL data needed for today's predictions and save to `.tmp/`
**Run before:** Feature building (`build_features.py`)

---

## Objective

Collect today's game schedule, player shot histories, team defensive metrics, and advanced stats from MoneyPuck. All outputs land in `.tmp/` and are consumed by Layer 2 (prediction model).

---

## Required Inputs

- None (all data pulled from public APIs/websites)
- `.env` is not required for this layer
- Playwright must be installed: `pip install playwright && playwright install chromium` (one-time setup)

---

## Tools & Sequence

Run these scripts **in order**. Steps 2 and 3 can run in parallel after Step 1. Step 4 must wait for Step 2.

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
3. For each skater, pulls their last 20 game logs
4. Extracts: date, shots on goal, TOI, power play TOI, opponent, home/away flag, goals, assists
5. Filters out low-usage players: forwards averaging ≤14 min TOI and defensemen averaging ≤18 min TOI are excluded

**Output:** `.tmp/player_logs_YYYY-MM-DD.json` (only includes TOI-qualifying players)
**Expected runtime:** ~3–5 minutes (rate-limited at 0.2s per request)
**Check:** Open the file and verify a known player's recent SOG totals against NHL.com game logs

---

### Step 3 — Fetch team defense stats + player positions (can run alongside Step 2)

```bash
python tools/fetch_team_defense_stats.py
```

**What it does:** Two data sources combined into one output:

1. **shotpropz.com** (via Playwright headless browser) — scrapes SOG allowed per game broken down by opposing player position (C, LW, RW, D). Captures both season-to-date and last-10-games data by clicking the JS "Recent" filter. Only scrapes teams playing today.

2. **dailyfaceoff.com** (via requests + BeautifulSoup) — parses the `__NEXT_DATA__` Next.js JSON embedded in each team's line combinations page. Extracts even-strength (EV) player-to-position assignments. **DailyFaceoff is the final authority on player positions** — if it conflicts with the NHL API position, DailyFaceoff wins.

**Output:** `.tmp/team_defense_YYYY-MM-DD.json`
```json
{
  "team_defense": {
    "COL": {
      "sa_vs_C_season": 8.6, "sa_vs_C_l10": 7.2,
      "sa_vs_LW_season": 6.4, "sa_vs_LW_l10": 5.8,
      "sa_vs_RW_season": 3.5, "sa_vs_RW_l10": 4.1,
      "sa_vs_D_season": 7.2,  "sa_vs_D_l10": 6.8
    }
  },
  "player_positions": {
    "nathan_mackinnon_COL": "C",
    "gabriel_landeskog_COL": "LW"
  }
}
```
**Expected runtime:** ~30–60 seconds (playwright ~10–15s + per-team HTTP requests)
**Note for Layer 2:** Use `player_positions` to look up the correct `sa_vs_{pos}` column for each player's matchup. L10 data should be weighted more heavily than season-to-date.

---

### Step 4 — Fetch advanced stats from MoneyPuck (must run after Step 2)

```bash
python tools/fetch_advanced_stats.py
```

**What it does:** Downloads the full-season skater CSV from MoneyPuck, filters to `situation=all`, and extracts:
- `xSF_per_60` — expected shot attempts per 60 min (shot quality proxy)
- `CF_pct` — Corsi For % (possession metric)
- `FF_pct` — Fenwick For % (unblocked shot attempts)
- `iSCF_per_60` — individual scoring chance attempts per 60

Only saves stats for players that appear in `player_logs_YYYY-MM-DD.json` (i.e., those who passed the TOI filter in Step 2).

**Output:** `.tmp/advanced_stats_YYYY-MM-DD.csv`
**Depends on:** Step 2 must complete first (reads `player_logs` to filter players)
**Note:** MoneyPuck updates daily — no rate limiting concerns.

---

## Expected Outputs Checklist

After running all four scripts, verify these files exist in `.tmp/`:

- [ ] `schedule_YYYY-MM-DD.json` — list of today's games
- [ ] `player_logs_YYYY-MM-DD.json` — dict of player game logs (TOI-filtered)
- [ ] `team_defense_YYYY-MM-DD.json` — team SA stats by position + player position overrides
- [ ] `advanced_stats_YYYY-MM-DD.csv` — CSV of advanced metrics (TOI-filtered)

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
| shotpropz "Recent" button selector | Uses `page.click("text=Recent")` — if the JS filter label changes, update the selector in `scrape_shotpropz()` |
| Utah Hockey Club (UTA) slug | DailyFaceoff slug is `utah-hockey-club` — verify this on first run; script warns and skips gracefully if wrong |
| DailyFaceoff page structure change | Data is parsed from `__NEXT_DATA__` JSON at path `props.pageProps.combinations.players[]` — if Next.js version changes, this path may shift |

---

## Handoff Notes for Layer 2 (build_features.py)

Two new fields exist that the feature builder needs to consume:

- **`pp_toi`** — power play ice time per game (in `player_logs`). Use as a feature; more PP time = more shot opportunities.
- **`player_positions`** — position overrides from DailyFaceoff (in `team_defense`). Use this to determine which `sa_vs_{pos}` column applies to each player's matchup against their opponent.

---

## Verification

After running, spot-check one player:

```bash
python -c "
import json
with open('.tmp/player_logs_$(date +%Y-%m-%d).json') as f:
    logs = json.load(f)
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
