"""
export_to_sheets.py

Pushes today's ranked picks to a shared Google Sheet.

Two worksheets:
  - "Today's Picks":              cleared each run and rewritten with today's predictions only
  - "Historical Picks w/ Hit Rate": summary stats + all completed/graded picks ever

Requires:
  .env with GOOGLE_SHEETS_ID
  service_account.json (Google Cloud Service Account key — gitignored)
  .tmp/best_lines_YYYY-MM-DD.csv

Sheet columns:
  Date | Player | Team | Opp | Proj SOG | Line | Direction | Book | Odds |
  Confidence | Edge | Line Shopping | Result

First-time setup:
  1. Go to Google Cloud Console → Create project → Enable Google Sheets API
  2. Create a Service Account → download JSON key → rename to service_account.json
  3. Place service_account.json in the project root (it's gitignored)
  4. Share your Google Sheet with the service account's client_email (Editor access)
  5. Run this script — no browser pop-up, works permanently
"""

import csv
import glob
import json
import os
import re
from datetime import date, timedelta

import gspread
from dotenv import load_dotenv
from google.oauth2.service_account import Credentials

load_dotenv()

TMP_DIR = os.path.join(os.path.dirname(__file__), "..", ".tmp")
ROOT_DIR = os.path.join(os.path.dirname(__file__), "..")
SERVICE_ACCOUNT_PATH = os.path.join(ROOT_DIR, "service_account.json")
SCOPES = ["https://www.googleapis.com/auth/spreadsheets"]

SHEET_HEADERS = [
    "Date", "Player", "Team", "Opp", "Proj SOG", "Line", "Direction",
    "Book", "Odds", "Confidence %", "Edge", "Line Shopping", "Result",
]


def get_google_creds() -> Credentials:
    if not os.path.exists(SERVICE_ACCOUNT_PATH):
        raise FileNotFoundError(
            "service_account.json not found. Download from Google Cloud Console "
            "(Service Account → Keys → Add Key → JSON) and place in the project root."
        )
    return Credentials.from_service_account_file(SERVICE_ACCOUNT_PATH, scopes=SCOPES)


def get_or_create_worksheet(spreadsheet, title: str):
    """Get an existing worksheet by name, or create it with headers."""
    try:
        ws = spreadsheet.worksheet(title)
    except gspread.WorksheetNotFound:
        ws = spreadsheet.add_worksheet(title=title, rows=1000, cols=20)
        ws.append_row(SHEET_HEADERS)
        print(f"  Created '{title}' worksheet with headers.")
    return ws


def format_player_name(player_key: str) -> str:
    parts = player_key.rsplit("_", 1)[0]
    words = parts.split("_")
    return " ".join(w.capitalize() for w in words)


def format_odds(odds_val: str) -> str:
    try:
        n = int(float(odds_val))
        return f"+{n}" if n > 0 else str(n)
    except (ValueError, TypeError):
        return str(odds_val)


def normalize_name(name: str) -> str:
    """Normalize a display name for matching: 'Nathan MacKinnon' -> 'nathanmackinnon'."""
    name = name.lower().strip()
    name = re.sub(r"[\s\-]+", "", name)
    name = re.sub(r"[^a-z0-9]", "", name)
    return name


def key_to_normalized(player_key: str) -> str:
    """Strip team suffix and normalize: 'nathan_mackinnon_COL' -> 'nathanmackinnon'."""
    prefix = "_".join(player_key.split("_")[:-1])
    return re.sub(r"[^a-z0-9]", "", prefix.lower())


def _grade_picks_for_date(game_date: str, real_labels_path: str) -> list[list]:
    """
    Load best_lines_{game_date}.csv, look up actual SOG, and return graded sheet rows.
    Returns an empty list if the file doesn't exist or has no flagged picks.
    """
    best_lines_path = os.path.join(TMP_DIR, f"best_lines_{game_date}.csv")
    if not os.path.exists(best_lines_path):
        return []

    raw_rows = []
    with open(best_lines_path, newline="") as f:
        for row in csv.DictReader(f):
            raw_rows.append(row)

    base_rows = build_sheet_rows(raw_rows, game_date)
    if not base_rows:
        return []

    # Column indices (matching SHEET_HEADERS order)
    PLAYER_COL = 1
    LINE_COL = 5
    DIRECTION_COL = 6
    RESULT_COL = 12

    # Build results lookup from real_labels.csv
    results_lookup: dict[str, dict] = {}
    if os.path.exists(real_labels_path):
        with open(real_labels_path, newline="") as f:
            for row in csv.DictReader(f):
                if row.get("game_date") == game_date:
                    norm = key_to_normalized(row["player_key"])
                    results_lookup[norm] = {
                        "actual_sog": float(row["actual_sog"]),
                        "line": float(row["line"]),
                    }

    # Fallback: load actual SOG directly from player_logs_*.json files
    logs_sog_lookup: dict[str, float] = {}
    for log_file in glob.glob(os.path.join(TMP_DIR, "player_logs_*.json")):
        try:
            with open(log_file) as lf:
                log_data = json.load(lf)
            for player_key, games in log_data.items():
                norm = key_to_normalized(player_key)
                for game in games:
                    if game.get("date") == game_date and game.get("sog") is not None:
                        logs_sog_lookup[norm] = float(game["sog"])
        except Exception:
            pass

    if results_lookup:
        print(f"    {len(results_lookup)} result(s) from real_labels.csv")
    elif logs_sog_lookup:
        print(f"    {len(logs_sog_lookup)} result(s) from player_logs fallback")
    else:
        print(f"    Warning: no actual SOG data found — results will be blank")

    # Grade each pick
    completed_rows = []
    for row in base_rows:
        player_name = row[PLAYER_COL] if len(row) > PLAYER_COL else ""
        norm = normalize_name(player_name)
        direction = row[DIRECTION_COL] if len(row) > DIRECTION_COL else "OVER"

        result_data = results_lookup.get(norm)
        if result_data:
            actual = result_data["actual_sog"]
            line = result_data["line"]
        elif norm in logs_sog_lookup:
            actual = logs_sog_lookup[norm]
            try:
                line = float(row[LINE_COL]) if len(row) > LINE_COL and row[LINE_COL] else None
            except (ValueError, TypeError):
                line = None
        else:
            actual = None
            line = None

        result_cell = ""
        if actual is not None and line is not None:
            went_over = actual > line
            hit = (went_over and direction == "OVER") or (not went_over and direction == "UNDER")
            result_cell = f"{actual:.1f} SOG ({'HIT' if hit else 'MISS'})"

        completed_row = list(row)
        while len(completed_row) <= RESULT_COL:
            completed_row.append("")
        completed_row[RESULT_COL] = result_cell
        completed_rows.append(completed_row)

    return completed_rows


def grade_and_update_historical(historical_ws) -> None:
    """
    Scans all past best_lines_*.csv files (not today's), grades each one against
    real_labels.csv / player_logs fallback, then:
      - Appends any new dates to Historical Picks w/ Hit Rate
      - Fills in missing results for dates already in Historical
    Also handles dates that exist in Historical but have no best_lines CSV
    (e.g., pipeline was skipped for a day) by grading directly from player logs.
    """
    today_str = date.today().isoformat()
    real_labels_path = os.path.join(TMP_DIR, "real_labels.csv")
    PLAYER_COL = 1
    LINE_COL = 5
    DIRECTION_COL = 6
    RESULT_COL = 12

    # Find all past best_lines files (sorted oldest→newest, exclude today)
    all_bl_files = sorted(glob.glob(os.path.join(TMP_DIR, "best_lines_*.csv")))
    past_files = [
        f for f in all_bl_files
        if os.path.basename(f) != f"best_lines_{today_str}.csv"
    ]

    if not past_files:
        print("  No past best_lines files found — nothing to grade.")
        return

    # Read the full historical sheet once (avoid repeated API calls)
    all_hist = historical_ws.get_all_values()
    header_row_idx = 7  # row 8 (0-indexed) is the column header row
    for i, r in enumerate(all_hist):
        if r and r[0] == "Date":
            header_row_idx = i
            break

    # Build a map: date -> list of (1-indexed sheet row, row_list) for existing historical data
    hist_by_date: dict[str, list] = {}
    for i, r in enumerate(all_hist):
        if i <= header_row_idx:
            continue
        if len(r) > 0 and r[0]:
            d = r[0]
            hist_by_date.setdefault(d, []).append((i + 1, r))

    for bl_path in past_files:
        # Extract date from filename: best_lines_YYYY-MM-DD.csv
        fname = os.path.basename(bl_path)
        game_date = fname[len("best_lines_"):-len(".csv")]

        print(f"  Grading {game_date}...")
        completed_rows = _grade_picks_for_date(game_date, real_labels_path)

        if not completed_rows:
            print(f"    No flagged picks for {game_date} — skipping.")
            continue

        existing_entries = hist_by_date.get(game_date, [])

        if not existing_entries:
            # New date — append to historical
            historical_ws.append_rows(completed_rows, value_input_option="RAW")
            print(f"    Appended {len(completed_rows)} pick(s) to Historical.")
            # Refresh our local copy so row numbers stay correct on next iteration
            all_hist = historical_ws.get_all_values()
            hist_by_date.clear()
            for i, r in enumerate(all_hist):
                if i <= header_row_idx:
                    continue
                if len(r) > 0 and r[0]:
                    hist_by_date.setdefault(r[0], []).append((i + 1, r))
        else:
            # Rows exist — fill in any missing results
            result_by_player = {
                normalize_name(cr[PLAYER_COL]): cr[RESULT_COL]
                for cr in completed_rows
                if len(cr) > RESULT_COL and cr[RESULT_COL]
            }
            updated_count = 0
            for sheet_row_num, r in existing_entries:
                existing_result = r[RESULT_COL] if len(r) > RESULT_COL else ""
                if not existing_result.strip():
                    player_norm = normalize_name(r[PLAYER_COL]) if len(r) > PLAYER_COL else ""
                    new_result = result_by_player.get(player_norm, "")
                    if new_result:
                        historical_ws.update_cell(sheet_row_num, RESULT_COL + 1, new_result)
                        updated_count += 1
            if updated_count:
                print(f"    Updated {updated_count} result(s) in Historical for {game_date}.")
            elif result_by_player:
                print(f"    Historical already has results for {game_date} — skipping.")
            else:
                print(f"    No result data available yet for {game_date}.")

    # --- Handle dates in Historical that have no best_lines CSV ---
    # (e.g., pipeline was skipped for a day — picks were added by an older run)
    handled_dates = {
        os.path.basename(p)[len("best_lines_"):-len(".csv")]
        for p in past_files
    }

    # Build full SOG lookup from all player logs: {norm_name: {date: sog}}
    logs_sog_by_date: dict[str, dict[str, float]] = {}
    for log_file in glob.glob(os.path.join(TMP_DIR, "player_logs_*.json")):
        try:
            with open(log_file) as lf:
                log_data = json.load(lf)
            for player_key, games in log_data.items():
                norm = key_to_normalized(player_key)
                if norm not in logs_sog_by_date:
                    logs_sog_by_date[norm] = {}
                for game in games:
                    if game.get("date") and game.get("sog") is not None:
                        logs_sog_by_date[norm][game["date"]] = float(game["sog"])
        except Exception:
            pass

    for game_date in sorted(hist_by_date.keys()):
        if game_date == today_str or game_date in handled_dates:
            continue

        print(f"  Grading {game_date} (Historical only — no best_lines file)...")
        existing_entries = hist_by_date.get(game_date, [])

        updated_count = 0

        for sheet_row_num, r in existing_entries:
            existing_result = r[RESULT_COL] if len(r) > RESULT_COL else ""

            player_name = r[PLAYER_COL] if len(r) > PLAYER_COL else ""
            norm = normalize_name(player_name)
            direction = r[DIRECTION_COL] if len(r) > DIRECTION_COL else "OVER"
            try:
                line = float(r[LINE_COL]) if len(r) > LINE_COL and r[LINE_COL] else None
            except (ValueError, TypeError):
                line = None

            if not existing_result.strip():
                actual = logs_sog_by_date.get(norm, {}).get(game_date)
                if actual is not None and line is not None:
                    went_over = actual > line
                    hit = (went_over and direction == "OVER") or (not went_over and direction == "UNDER")
                    result_cell = f"{actual:.1f} SOG ({'HIT' if hit else 'MISS'})"
                    historical_ws.update_cell(sheet_row_num, RESULT_COL + 1, result_cell)
                    updated_count += 1

        if updated_count:
            print(f"    Updated {updated_count} result(s) for {game_date}.")
        else:
            print(f"    No game log data found for {game_date} — results left blank.")


def _apply_structural_formatting(spreadsheet, sheet_id: int) -> None:
    """Apply header, freeze, column widths, and alternating row banding in one batch call."""
    # Delete any existing banding on this sheet first (idempotency)
    meta = spreadsheet.fetch_sheet_metadata()
    requests = []
    for sheet in meta.get("sheets", []):
        if sheet["properties"]["sheetId"] != sheet_id:
            continue
        for band in sheet.get("bandedRanges", []):
            requests.append({"deleteBanding": {"bandedRangeId": band["bandedRangeId"]}})

    # Freeze row 1
    requests.append({
        "updateSheetProperties": {
            "properties": {
                "sheetId": sheet_id,
                "gridProperties": {"frozenRowCount": 1},
            },
            "fields": "gridProperties.frozenRowCount",
        }
    })

    # Header row: dark navy bg, white bold text, centered
    requests.append({
        "repeatCell": {
            "range": {
                "sheetId": sheet_id,
                "startRowIndex": 0, "endRowIndex": 1,
                "startColumnIndex": 0, "endColumnIndex": 13,
            },
            "cell": {
                "userEnteredFormat": {
                    "backgroundColor": {"red": 0.102, "green": 0.153, "blue": 0.267},
                    "horizontalAlignment": "CENTER",
                    "textFormat": {
                        "bold": True,
                        "fontSize": 10,
                        "foregroundColor": {"red": 1.0, "green": 1.0, "blue": 1.0},
                    },
                }
            },
            "fields": "userEnteredFormat(backgroundColor,horizontalAlignment,textFormat)",
        }
    })

    # Column widths (index: pixels)
    col_widths = {0: 100, 1: 160, 2: 60, 3: 60, 4: 85, 5: 65,
                  6: 90, 7: 110, 8: 70, 9: 110, 10: 70, 11: 115, 12: 160}
    for col_idx, px in col_widths.items():
        requests.append({
            "updateDimensionProperties": {
                "range": {
                    "sheetId": sheet_id,
                    "dimension": "COLUMNS",
                    "startIndex": col_idx,
                    "endIndex": col_idx + 1,
                },
                "properties": {"pixelSize": px},
                "fields": "pixelSize",
            }
        })

    # Alternating row banding (rows 2+ only, skip header)
    requests.append({
        "addBanding": {
            "bandedRange": {
                "range": {
                    "sheetId": sheet_id,
                    "startRowIndex": 1, "endRowIndex": 1000,
                    "startColumnIndex": 0, "endColumnIndex": 13,
                },
                "rowProperties": {
                    "firstBandColor": {"red": 1.0, "green": 1.0, "blue": 1.0},
                    "secondBandColor": {"red": 0.953, "green": 0.957, "blue": 0.965},
                },
            }
        }
    })

    spreadsheet.batch_update({"requests": requests})


def _apply_conditional_formatting(spreadsheet, sheet_id: int) -> None:
    """Clear all existing conditional format rules and re-add them (idempotent)."""
    meta = spreadsheet.fetch_sheet_metadata()
    existing_count = 0
    for sheet in meta.get("sheets", []):
        if sheet["properties"]["sheetId"] == sheet_id:
            existing_count = len(sheet.get("conditionalFormats", []))
            break

    if existing_count > 0:
        delete_requests = [
            {"deleteConditionalFormatRule": {"sheetId": sheet_id, "index": i}}
            for i in range(existing_count - 1, -1, -1)
        ]
        spreadsheet.batch_update({"requests": delete_requests})

    def text_contains_rule(col: int, text: str, bg: dict, bold: bool = False) -> dict:
        fmt: dict = {"backgroundColor": bg}
        if bold:
            fmt["textFormat"] = {"bold": True}
        return {
            "addConditionalFormatRule": {
                "rule": {
                    "ranges": [{
                        "sheetId": sheet_id,
                        "startRowIndex": 1, "endRowIndex": 1000,
                        "startColumnIndex": col, "endColumnIndex": col + 1,
                    }],
                    "booleanRule": {
                        "condition": {
                            "type": "TEXT_CONTAINS",
                            "values": [{"userEnteredValue": text}],
                        },
                        "format": fmt,
                    },
                },
                "index": 0,
            }
        }

    def number_gte_rule(col: int, threshold: float, bg: dict, bold: bool = False) -> dict:
        fmt: dict = {"backgroundColor": bg}
        if bold:
            fmt["textFormat"] = {"bold": True}
        return {
            "addConditionalFormatRule": {
                "rule": {
                    "ranges": [{
                        "sheetId": sheet_id,
                        "startRowIndex": 1, "endRowIndex": 1000,
                        "startColumnIndex": col, "endColumnIndex": col + 1,
                    }],
                    "booleanRule": {
                        "condition": {
                            "type": "NUMBER_GREATER_THAN_EQ",
                            "values": [{"userEnteredValue": str(threshold)}],
                        },
                        "format": fmt,
                    },
                },
                "index": 0,
            }
        }

    add_requests = [
        text_contains_rule(12, "HIT",   {"red": 0.776, "green": 0.937, "blue": 0.808}),
        text_contains_rule(12, "MISS",  {"red": 1.0,   "green": 0.800, "blue": 0.800}),
        text_contains_rule(6,  "OVER",  {"red": 0.851, "green": 0.918, "blue": 0.827}),
        text_contains_rule(6,  "UNDER", {"red": 0.812, "green": 0.886, "blue": 0.953}),
        text_contains_rule(11, "YES",   {"red": 1.0,   "green": 0.949, "blue": 0.800}),
        number_gte_rule(9, 70.0,        {"red": 0.918, "green": 0.957, "blue": 0.918}, bold=True),
    ]
    spreadsheet.batch_update({"requests": add_requests})


def apply_sheet_formatting(spreadsheet, worksheet) -> None:
    """Apply all visual formatting to a worksheet. Safe to call on every run."""
    sheet_id = worksheet.id
    _apply_structural_formatting(spreadsheet, sheet_id)
    _apply_conditional_formatting(spreadsheet, sheet_id)


def _apply_historical_formatting(spreadsheet, sheet_id: int) -> None:
    """
    Format the Historical Picks w/ Hit Rate sheet:
      - Rows 1-7: summary section (title + stats + progress bar)
      - Row 8: column headers (navy, frozen)
      - Row 9+: data rows with alternating banding + conditional formatting
    """
    meta = spreadsheet.fetch_sheet_metadata()
    requests = []

    # Remove existing banding
    for sheet in meta.get("sheets", []):
        if sheet["properties"]["sheetId"] != sheet_id:
            continue
        for band in sheet.get("bandedRanges", []):
            requests.append({"deleteBanding": {"bandedRangeId": band["bandedRangeId"]}})

    # Freeze row 8 (the column headers row)
    requests.append({
        "updateSheetProperties": {
            "properties": {
                "sheetId": sheet_id,
                "gridProperties": {"frozenRowCount": 8},
            },
            "fields": "gridProperties.frozenRowCount",
        }
    })

    # Row 1: title — large, navy, bold, merged across A-M
    requests.append({
        "mergeCells": {
            "range": {
                "sheetId": sheet_id,
                "startRowIndex": 0, "endRowIndex": 1,
                "startColumnIndex": 0, "endColumnIndex": 13,
            },
            "mergeType": "MERGE_ALL",
        }
    })
    requests.append({
        "repeatCell": {
            "range": {
                "sheetId": sheet_id,
                "startRowIndex": 0, "endRowIndex": 1,
                "startColumnIndex": 0, "endColumnIndex": 13,
            },
            "cell": {
                "userEnteredFormat": {
                    "backgroundColor": {"red": 0.102, "green": 0.153, "blue": 0.267},
                    "horizontalAlignment": "CENTER",
                    "verticalAlignment": "MIDDLE",
                    "textFormat": {
                        "bold": True,
                        "fontSize": 14,
                        "foregroundColor": {"red": 1.0, "green": 1.0, "blue": 1.0},
                    },
                }
            },
            "fields": "userEnteredFormat(backgroundColor,horizontalAlignment,verticalAlignment,textFormat)",
        }
    })

    # Row 3: stat label cells — Total Picks (blue), Hits (green), Misses (red), Hit Rate (gold)
    stat_colors = [
        {"red": 0.812, "green": 0.886, "blue": 0.953},  # blue  — Total Picks
        {"red": 0.776, "green": 0.937, "blue": 0.808},  # green — Hits
        {"red": 1.0,   "green": 0.800, "blue": 0.800},  # red   — Misses
        {"red": 1.0,   "green": 0.949, "blue": 0.800},  # gold  — Hit Rate
    ]
    for col_idx, bg in enumerate(stat_colors):
        for row_idx in [2, 3]:  # rows 3 and 4 (0-indexed 2 and 3)
            requests.append({
                "repeatCell": {
                    "range": {
                        "sheetId": sheet_id,
                        "startRowIndex": row_idx, "endRowIndex": row_idx + 1,
                        "startColumnIndex": col_idx, "endColumnIndex": col_idx + 1,
                    },
                    "cell": {
                        "userEnteredFormat": {
                            "backgroundColor": bg,
                            "horizontalAlignment": "CENTER",
                            "textFormat": {"bold": True, "fontSize": 11},
                        }
                    },
                    "fields": "userEnteredFormat(backgroundColor,horizontalAlignment,textFormat)",
                }
            })

    # Row 8: column headers — same navy style as other sheets
    requests.append({
        "repeatCell": {
            "range": {
                "sheetId": sheet_id,
                "startRowIndex": 7, "endRowIndex": 8,
                "startColumnIndex": 0, "endColumnIndex": 13,
            },
            "cell": {
                "userEnteredFormat": {
                    "backgroundColor": {"red": 0.102, "green": 0.153, "blue": 0.267},
                    "horizontalAlignment": "CENTER",
                    "textFormat": {
                        "bold": True,
                        "fontSize": 10,
                        "foregroundColor": {"red": 1.0, "green": 1.0, "blue": 1.0},
                    },
                }
            },
            "fields": "userEnteredFormat(backgroundColor,horizontalAlignment,textFormat)",
        }
    })

    # Column widths
    col_widths = {0: 100, 1: 160, 2: 60, 3: 60, 4: 85, 5: 65,
                  6: 90, 7: 110, 8: 70, 9: 110, 10: 70, 11: 115, 12: 160}
    for col_idx, px in col_widths.items():
        requests.append({
            "updateDimensionProperties": {
                "range": {
                    "sheetId": sheet_id,
                    "dimension": "COLUMNS",
                    "startIndex": col_idx,
                    "endIndex": col_idx + 1,
                },
                "properties": {"pixelSize": px},
                "fields": "pixelSize",
            }
        })

    # Alternating row banding for data rows (row 9+)
    requests.append({
        "addBanding": {
            "bandedRange": {
                "range": {
                    "sheetId": sheet_id,
                    "startRowIndex": 8, "endRowIndex": 1000,
                    "startColumnIndex": 0, "endColumnIndex": 13,
                },
                "rowProperties": {
                    "firstBandColor": {"red": 1.0, "green": 1.0, "blue": 1.0},
                    "secondBandColor": {"red": 0.953, "green": 0.957, "blue": 0.965},
                },
            }
        }
    })

    spreadsheet.batch_update({"requests": requests})

    # Remove any existing charts on this sheet (idempotent)
    meta_charts = spreadsheet.fetch_sheet_metadata()
    chart_ids = []
    for sheet in meta_charts.get("sheets", []):
        if sheet["properties"]["sheetId"] == sheet_id:
            for chart in sheet.get("charts", []):
                chart_ids.append(chart["chartId"])
    if chart_ids:
        spreadsheet.batch_update({
            "requests": [{"deleteEmbeddedObject": {"objectId": cid}} for cid in chart_ids]
        })

    # Add donut chart (Hits vs Misses) — data source is N3:O4, chart floats over E1:M7
    spreadsheet.batch_update({
        "requests": [{
            "addChart": {
                "chart": {
                    "spec": {
                        "title": "Overall Hit Rate",
                        "titleTextFormat": {"bold": True, "fontSize": 13},
                        "backgroundColor": {"red": 1.0, "green": 1.0, "blue": 1.0},
                        "pieChart": {
                            "legendPosition": "LABELED_LEGEND",
                            "pieHole": 0.5,
                            "domain": {
                                "sourceRange": {
                                    "sources": [{
                                        "sheetId": sheet_id,
                                        "startRowIndex": 2,   # row 3 (0-indexed)
                                        "endRowIndex": 4,
                                        "startColumnIndex": 13,  # col N — labels
                                        "endColumnIndex": 14,
                                    }]
                                }
                            },
                            "series": {
                                "sourceRange": {
                                    "sources": [{
                                        "sheetId": sheet_id,
                                        "startRowIndex": 2,
                                        "endRowIndex": 4,
                                        "startColumnIndex": 14,  # col O — values
                                        "endColumnIndex": 15,
                                    }]
                                }
                            },
                        },
                    },
                    "position": {
                        "overlayPosition": {
                            "anchorCell": {
                                "sheetId": sheet_id,
                                "rowIndex": 0,    # row 1
                                "columnIndex": 4, # column E
                            },
                            "offsetXPixels": 10,
                            "offsetYPixels": 5,
                            "widthPixels": 380,
                            "heightPixels": 210,
                        }
                    },
                }
            }
        }]
    })

    # Conditional formatting for data rows (HIT/MISS/OVER/UNDER)
    existing_count = 0
    meta2 = spreadsheet.fetch_sheet_metadata()
    for sheet in meta2.get("sheets", []):
        if sheet["properties"]["sheetId"] == sheet_id:
            existing_count = len(sheet.get("conditionalFormats", []))
            break
    if existing_count > 0:
        delete_reqs = [
            {"deleteConditionalFormatRule": {"sheetId": sheet_id, "index": i}}
            for i in range(existing_count - 1, -1, -1)
        ]
        spreadsheet.batch_update({"requests": delete_reqs})

    def text_rule(col, text, bg, bold=False):
        fmt = {"backgroundColor": bg}
        if bold:
            fmt["textFormat"] = {"bold": True}
        return {
            "addConditionalFormatRule": {
                "rule": {
                    "ranges": [{
                        "sheetId": sheet_id,
                        "startRowIndex": 8, "endRowIndex": 1000,
                        "startColumnIndex": col, "endColumnIndex": col + 1,
                    }],
                    "booleanRule": {
                        "condition": {"type": "TEXT_CONTAINS", "values": [{"userEnteredValue": text}]},
                        "format": fmt,
                    },
                },
                "index": 0,
            }
        }

    spreadsheet.batch_update({"requests": [
        text_rule(12, "HIT",   {"red": 0.776, "green": 0.937, "blue": 0.808}),
        text_rule(12, "MISS",  {"red": 1.0,   "green": 0.800, "blue": 0.800}),
        text_rule(6,  "OVER",  {"red": 0.851, "green": 0.918, "blue": 0.827}),
        text_rule(6,  "UNDER", {"red": 0.812, "green": 0.886, "blue": 0.953}),
    ]})


def update_historical_sheet(historical_ws) -> None:
    """
    Write the summary section (rows 1-7) and column headers (row 8).
    Does NOT clear or touch data rows (row 9+) — those are appended permanently
    by move_yesterday_to_results() and must never be wiped.
    """
    # Row 1: title
    historical_ws.update([["NHL SHOTS MODEL — HISTORICAL PERFORMANCE"]], "A1", value_input_option="RAW")

    # Row 3-4: stat labels + formula values (auto-update as data rows grow)
    historical_ws.update([["Total Picks", "Hits", "Misses", "Hit Rate"]], "A3:D3", value_input_option="RAW")
    historical_ws.update(
        [[
            '=COUNTA(A9:A1000)',
            '=COUNTIF(M9:M1000,"*HIT*")',
            '=COUNTIF(M9:M1000,"*MISS*")',
            '=IF(A4=0,"—",TEXT(B4/A4,"0.0%"))',
        ]],
        "A4:D4",
        value_input_option="USER_ENTERED",
    )

    # Chart data source in N3:O4 (used by the donut chart — auto-updates)
    historical_ws.update(
        [
            ["Hits",   '=COUNTIF(M9:M1000,"*HIT*")'],
            ["Misses", '=COUNTIF(M9:M1000,"*MISS*")'],
        ],
        "N3:O4",
        value_input_option="USER_ENTERED",
    )

    # Row 8: column headers (only write if missing)
    existing = historical_ws.get_all_values()
    if len(existing) < 8 or existing[7] != SHEET_HEADERS:
        historical_ws.update([SHEET_HEADERS], "A8", value_input_option="RAW")

    total = max(0, len(existing) - 8) if len(existing) > 8 else 0
    print(f"  Historical Picks w/ Hit Rate: summary updated ({total} total completed pick(s)).")


def load_best_lines(today_str: str) -> list[dict]:
    path = os.path.join(TMP_DIR, f"best_lines_{today_str}.csv")
    if not os.path.exists(path):
        raise FileNotFoundError(f"Best lines file not found: {path}. Run compare_lines.py first.")
    rows = []
    with open(path, newline="") as f:
        for row in csv.DictReader(f):
            rows.append(row)
    return rows


def build_sheet_rows(rows: list[dict], today_str: str) -> list[list]:
    sheet_rows = []
    flagged = [r for r in rows if r.get("flagged") == "YES"]
    flagged.sort(key=lambda x: float(x.get("confidence_score", 0)), reverse=True)

    for row in flagged:
        direction = row.get("direction", "OVER")
        odds_val = row.get("best_under_odds", "") if direction == "UNDER" else row.get("best_over_odds", "")
        sheet_rows.append([
            today_str,
            format_player_name(row["player_key"]),
            row.get("team", ""),
            row.get("opponent", ""),
            row.get("projected_sog", ""),
            row.get("best_line", ""),
            direction,
            row.get("best_book", ""),
            format_odds(odds_val),
            row.get("confidence_score", ""),
            row.get("edge", ""),
            row.get("line_shopping", "NO"),
            "",  # Result — filled in next day
        ])

    return sheet_rows


def main():
    sheet_id = os.getenv("GOOGLE_SHEETS_ID")
    if not sheet_id:
        print("ERROR: GOOGLE_SHEETS_ID not set in .env")
        return

    today_str = date.today().isoformat()

    # Load today's picks — may be empty or file may not exist yet
    try:
        rows = load_best_lines(today_str)
        sheet_rows = build_sheet_rows(rows, today_str)
    except FileNotFoundError:
        rows = []
        sheet_rows = []

    print("Authenticating with Google Sheets...")
    creds = get_google_creds()
    client = gspread.authorize(creds)
    spreadsheet = client.open_by_key(sheet_id)

    historical_ws = get_or_create_worksheet(spreadsheet, "Historical Picks w/ Hit Rate")

    # Delete legacy "Yesterday's" tabs if they exist
    for stale_name in ("Yesterday's Scorecard", "Yesterday's Picks"):
        try:
            stale_ws = spreadsheet.worksheet(stale_name)
            spreadsheet.del_worksheet(stale_ws)
            print(f"  Deleted '{stale_name}' worksheet.")
        except gspread.WorksheetNotFound:
            pass

    # Write Historical summary section FIRST (rows 1-8) so append_rows lands data at row 9+
    print("Updating Historical Picks w/ Hit Rate summary...")
    update_historical_sheet(historical_ws)
    _apply_historical_formatting(spreadsheet, historical_ws.id)

    # Grade yesterday's picks and append to Historical Picks w/ Hit Rate
    print("Processing yesterday's results...")
    grade_and_update_historical(historical_ws)

    if not sheet_rows:
        print("No flagged plays today — grading complete.")
        print(f"Sheet URL: https://docs.google.com/spreadsheets/d/{sheet_id}")
        return

    # Apply formatting to Today's Picks and write predictions
    picks_ws = get_or_create_worksheet(spreadsheet, "Today's Picks")
    print("Applying sheet formatting...")
    apply_sheet_formatting(spreadsheet, picks_ws)

    picks_ws.clear()
    picks_ws.append_row(SHEET_HEADERS)
    picks_ws.append_rows(sheet_rows, value_input_option="USER_ENTERED")
    print(f"Exported {len(sheet_rows)} picks to \"Today's Picks\".")
    print(f"Sheet URL: https://docs.google.com/spreadsheets/d/{sheet_id}")


if __name__ == "__main__":
    main()
