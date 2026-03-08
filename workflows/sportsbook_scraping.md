# Workflow: Sportsbook + Output

**Owner:** Friend B (`feature/sportsbook-output` branch)
**Purpose:** Pull SOG prop lines from all 4 sportsbooks, compare, and push picks to terminal + Google Sheet
**Depends on:** `.tmp/predictions_YYYY-MM-DD.csv` from Layer 2

---

## Objective

Fetch today's shots-on-goal prop lines from DraftKings, FanDuel, BetMGM, and Caesars via The Odds API. Join them with model predictions to find the best line per player, then output ranked picks to the terminal and Google Sheet.

---

## Setup (One-Time)

### 1. Get The Odds API key

1. Go to [theoddsapi.com](https://the-odds-api.com) and sign up (free)
2. Copy your API key from the dashboard
3. Add to `.env`:
   ```
   ODDS_API_KEY=your_key_here
   ```
4. Free tier: **500 requests/month**. Each game = 1 request. On a 15-game night = 15 requests.

### 2. Set up Google Sheets

1. Create a new Google Sheet at [sheets.google.com](https://sheets.google.com)
2. Share it with all three team members (Editor access)
3. Copy the Sheet ID from the URL:
   ```
   https://docs.google.com/spreadsheets/d/THIS_IS_THE_ID/edit
   ```
4. Add to `.env`:
   ```
   GOOGLE_SHEETS_ID=your_sheet_id_here
   ```

### 3. Set up Google Sheets API (one-time)

1. Go to [Google Cloud Console](https://console.cloud.google.com)
2. Create a new project (e.g. "NHL Shots Model")
3. Enable the **Google Sheets API**
4. Create credentials: APIs & Services → Credentials → Create Credentials → OAuth 2.0 Client ID
   - Application type: Desktop app
5. Download the JSON file → rename it to `credentials.json` → place in project root
6. Run `export_to_sheets.py` once — a browser window opens to authorize
7. Click Allow — `token.json` is saved. Future runs are automatic.

**Note:** Both `credentials.json` and `token.json` are gitignored. Each teammate needs to complete this auth step on their own machine.

---

## Tools & Sequence

### Step 1 — Fetch odds

```bash
python tools/fetch_odds.py
```

**What it does:**
1. Calls The Odds API to get today's NHL event IDs
2. For each game, fetches `player_shots_on_goal` market from DK, FD, BetMGM, Caesars
3. Parses player names, lines, and American odds
4. Saves normalized output keyed by player

**Output:** `.tmp/odds_YYYY-MM-DD.json`
**Run after:** `fetch_nhl_schedule.py` (to know which games are today)
**Rate limit watch:** Each event = 1 API request. Check remaining quota in the response headers.

---

### Step 2 — Compare lines

```bash
python tools/compare_lines.py
```

**What it does:**
- Joins predictions with odds on `player_key`
- Finds the best (lowest) line per player across all books
- Calculates: edge = projected_sog − best_line
- Flags plays: confidence >= 60% AND edge >= 0.3 SOG
- Flags line shopping: >= 0.5 SOG spread across books

**Output:** `.tmp/best_lines_YYYY-MM-DD.csv`

---

### Step 3 — Generate terminal report

```bash
python tools/generate_report.py
```

**Output format:**
```
======================================================================
  NHL SHOTS ON GOAL PICKS — 2026-03-07
======================================================================

RANK  PLAYER                 TEAM  OPP   PROJ   LINE  DIR    BOOK         ODDS    CONF   EDGE
1     Nathan MacKinnon       COL   EDM   4.80  O 3.5  OVER   draftkings   -115    78%   +1.30
2     David Pastrnak         BOS   TBL   3.90  O 3.5  OVER   fanduel      -110    71%   +0.40

  LINE SHOPPING ALERTS (>= 0.5 SOG spread across books)
  Connor McDavid               spread: 0.5  |  best: 3.5 @ draftkings
======================================================================
```

---

### Step 4 — Export to Google Sheet

```bash
python tools/export_to_sheets.py
```

**Sheet structure (auto-created "Picks" tab):**

| Date | Player | Team | Opp | Proj SOG | Line | Direction | Book | Odds | Confidence % | Edge | Line Shopping | Result |
|------|--------|------|-----|----------|------|-----------|------|------|-------------|------|--------------|--------|
| 2026-03-07 | Nathan MacKinnon | COL | EDM | 4.80 | 3.5 | OVER | draftkings | -115 | 78 | +1.30 | NO | _(fill in next day)_ |

**Result column:** Fill in the player's actual SOG the next day. This builds your accuracy tracking dataset.

---

## Tracking Results & Building Labels

After each game night:
1. Open the Google Sheet → "Picks" tab
2. Fill in the **Result** column with each player's actual SOG (from NHL.com box scores)
3. Export the labeled rows to feed into `train_model.py`:

From the Sheet, download as CSV and format the labels file:
```
player_key,game_date,actual_sog,line,book,over_odds
nathan_mackinnon_COL,2026-03-07,4,3.5,draftkings,-115
```

Over time this dataset trains the Phase 2 model — the more data, the better the accuracy.

---

## Known Issues & Edge Cases

| Scenario | Handling |
|----------|---------|
| Player name mismatch | `fetch_odds.py` normalizes to `first_last_TEAM` format. If a player is missing from odds output, check their name spelling vs. the sportsbook's display name |
| Market not available | Some games won't have SOG props. Script prints "No market found" and skips |
| Odds API quota exceeded | Monitor your remaining requests in the API dashboard. On the free tier (500/month), prioritize fetching only when there are games |
| Google auth expired | Delete `token.json` and re-run `export_to_sheets.py` to re-authorize |
| Sheet tab missing | Script auto-creates "Picks" tab on first run |
| `best_over_odds` is N/A | The book listed the line but not the price yet — happens early in the day. Re-run `fetch_odds.py` closer to game time |

---

## Updating This Workflow

If The Odds API changes the market key for SOG props, update `MARKET` in `fetch_odds.py`. If a new sportsbook is added, add its key to the `BOOKS` list. Document changes and dates here.
