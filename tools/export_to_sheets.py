"""
export_to_sheets.py

Pushes today's ranked picks to a shared Google Sheet.

Three worksheets:
  - "Today's Picks":   cleared each run and rewritten with today's predictions only
  - "Yesterday's Scorecard":         yesterday's picks (with HIT/MISS filled in) appended here permanently
  - "Historical Picks w/ Hit Rate": rebuilt each run — summary stats + all completed picks ever

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


def move_yesterday_to_results(picks_ws, results_ws, historical_ws) -> None:
    """
    1. Read yesterday's picks from 'Today's Picks' and fill in results.
    2. Append completed rows to 'Historical Picks w/ Hit Rate' (permanent — never cleared).
    3. Clear 'Yesterday's Scorecard' and write only yesterday's completed picks.
    """
    yesterday = (date.today() - timedelta(days=1)).isoformat()
    real_labels_path = os.path.join(TMP_DIR, "real_labels.csv")

    all_rows = picks_ws.get_all_values()
    if len(all_rows) <= 1:
        print("  No picks in Today's Picks to move.")
        results_ws.clear()
        results_ws.append_row(SHEET_HEADERS)
        return

    headers = all_rows[0]
    data_rows = all_rows[1:]

    try:
        date_col = headers.index("Date")
        player_col = headers.index("Player")
        direction_col = headers.index("Direction")
        result_col = headers.index("Result")
    except ValueError:
        print("  Warning: Could not find expected columns — skipping results move.")
        return

    yesterday_rows = [r for r in data_rows if len(r) > date_col and r[date_col] == yesterday]
    if not yesterday_rows:
        print(f"  No rows for {yesterday} in Today's Picks.")
        results_ws.clear()
        results_ws.append_row(SHEET_HEADERS)
        return

    # Build results lookup from real_labels.csv
    results_lookup: dict[str, dict] = {}
    if os.path.exists(real_labels_path):
        with open(real_labels_path, newline="") as f:
            for row in csv.DictReader(f):
                if row.get("game_date") == yesterday:
                    norm = key_to_normalized(row["player_key"])
                    results_lookup[norm] = {
                        "actual_sog": float(row["actual_sog"]),
                        "line": float(row["line"]),
                    }

    # Fill in results
    completed_rows = []
    for row in yesterday_rows:
        result_cell = row[result_col].strip() if len(row) > result_col else ""
        if not result_cell:
            norm = normalize_name(row[player_col])
            result_data = results_lookup.get(norm)
            if result_data:
                actual = result_data["actual_sog"]
                line = result_data["line"]
                direction = row[direction_col] if len(row) > direction_col else "OVER"
                went_over = actual > line
                hit = (went_over and direction == "OVER") or (not went_over and direction == "UNDER")
                result_cell = f"{actual:.1f} SOG ({'HIT' if hit else 'MISS'})"

        completed_row = list(row)
        while len(completed_row) <= result_col:
            completed_row.append("")
        completed_row[result_col] = result_cell
        completed_rows.append(completed_row)

    # Append to Historical Picks w/ Hit Rate — skip if yesterday's date already present (duplicate guard)
    existing_historical = historical_ws.get_all_values()
    existing_dates = set()
    if len(existing_historical) > 1:
        try:
            h_date_col = existing_historical[0].index("Date")
            for r in existing_historical[1:]:
                if len(r) > h_date_col:
                    existing_dates.add(r[h_date_col])
        except (ValueError, IndexError):
            pass

    if yesterday not in existing_dates:
        historical_ws.append_rows(completed_rows, value_input_option="RAW")
        print(f"  Appended {len(completed_rows)} pick(s) to Historical Picks w/ Hit Rate.")
    else:
        print(f"  Historical Picks w/ Hit Rate already has rows for {yesterday} — skipping duplicate append.")

    # Clear Yesterday's Scorecard and write only yesterday's completed picks
    results_ws.clear()
    results_ws.append_row(SHEET_HEADERS)
    results_ws.append_rows(completed_rows, value_input_option="USER_ENTERED")
    print(f"  Yesterday's Scorecard: {len(completed_rows)} completed pick(s) from {yesterday}.")


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
        sheet_rows.append([
            today_str,
            format_player_name(row["player_key"]),
            row.get("team", ""),
            row.get("opponent", ""),
            row.get("projected_sog", ""),
            row.get("best_line", ""),
            row.get("direction", "OVER"),
            row.get("best_book", ""),
            format_odds(row.get("odds", "")),
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
    rows = load_best_lines(today_str)
    sheet_rows = build_sheet_rows(rows, today_str)

    if not sheet_rows:
        print("No flagged plays to export.")
        return

    print("Authenticating with Google Sheets...")
    creds = get_google_creds()
    client = gspread.authorize(creds)
    spreadsheet = client.open_by_key(sheet_id)

    picks_ws = get_or_create_worksheet(spreadsheet, "Today's Picks")
    results_ws = get_or_create_worksheet(spreadsheet, "Yesterday's Scorecard")
    historical_ws = get_or_create_worksheet(spreadsheet, "Historical Picks w/ Hit Rate")

    # Write Historical summary section FIRST (rows 1-8) so append_rows lands data at row 9+
    print("Updating Historical Picks w/ Hit Rate summary...")
    update_historical_sheet(historical_ws)
    _apply_historical_formatting(spreadsheet, historical_ws.id)

    # Fill in results, append to Historical Picks w/ Hit Rate, clear+rewrite Yesterday's Scorecard with yesterday only
    print("Processing yesterday's results...")
    move_yesterday_to_results(picks_ws, results_ws, historical_ws)

    # Apply formatting to Today's Picks and Yesterday's Scorecard
    print("Applying sheet formatting...")
    apply_sheet_formatting(spreadsheet, picks_ws)
    apply_sheet_formatting(spreadsheet, results_ws)

    # Clear Today's Picks and write fresh predictions
    picks_ws.clear()
    picks_ws.append_row(SHEET_HEADERS)
    picks_ws.append_rows(sheet_rows, value_input_option="USER_ENTERED")
    print(f"Exported {len(sheet_rows)} picks to \"Today's Picks\".")
    print(f"Sheet URL: https://docs.google.com/spreadsheets/d/{sheet_id}")


if __name__ == "__main__":
    main()
