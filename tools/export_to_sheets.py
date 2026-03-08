"""
export_to_sheets.py

Pushes today's ranked picks to a shared Google Sheet.

Requires:
  .env with GOOGLE_SHEETS_ID
  service_account.json (Google Cloud Service Account key — gitignored)
  .tmp/best_lines_YYYY-MM-DD.csv

Sheet columns:
  Date | Player | Team | Opp | Proj SOG | Line | Direction | Book | Odds |
  Confidence | Edge | Line Shopping | Result

Result column is left blank — fill it in the next day with actual SOG.

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


def update_yesterday_results(worksheet) -> None:
    """Fill in the Result column for yesterday's picks using real_labels.csv."""
    yesterday = (date.today() - timedelta(days=1)).isoformat()
    real_labels_path = os.path.join(TMP_DIR, "real_labels.csv")

    if not os.path.exists(real_labels_path):
        return

    # Build lookup: normalized_name -> {actual_sog, line} for yesterday
    results_lookup: dict[str, dict] = {}
    with open(real_labels_path, newline="") as f:
        for row in csv.DictReader(f):
            if row.get("game_date") == yesterday:
                norm = key_to_normalized(row["player_key"])
                results_lookup[norm] = {
                    "actual_sog": float(row["actual_sog"]),
                    "line": float(row["line"]),
                }

    if not results_lookup:
        return

    all_rows = worksheet.get_all_values()
    if not all_rows:
        return

    headers = all_rows[0]
    try:
        date_col = headers.index("Date")
        player_col = headers.index("Player")
        direction_col = headers.index("Direction")
        result_col = headers.index("Result")
    except ValueError:
        print("  Warning: Could not find expected columns in sheet — skipping results update.")
        return

    updates = []
    updated = 0

    for row_idx, row in enumerate(all_rows[1:], start=2):  # row 1 is header; Sheets is 1-indexed
        if len(row) <= result_col:
            continue
        if row[date_col] != yesterday:
            continue
        if row[result_col].strip():  # already filled in
            continue

        norm = normalize_name(row[player_col])
        result_data = results_lookup.get(norm)
        if result_data is None:
            continue

        actual = result_data["actual_sog"]
        line = result_data["line"]
        direction = row[direction_col] if len(row) > direction_col else "OVER"
        went_over = actual > line
        hit = (went_over and direction == "OVER") or (not went_over and direction == "UNDER")
        label = "HIT" if hit else "MISS"
        cell_value = f"{actual:.1f} SOG ({label})"

        # gspread batch_update uses A1 notation
        col_letter = chr(ord("A") + result_col)
        updates.append({"range": f"{col_letter}{row_idx}", "values": [[cell_value]]})
        updated += 1

    if updates:
        worksheet.spreadsheet.values_batch_update({"data": updates, "valueInputOption": "RAW"})
        print(f"  Updated {updated} result(s) for {yesterday} in Google Sheet.")
    else:
        print(f"  No sheet rows matched for {yesterday} results update.")


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
    """Apply all visual formatting to the Picks worksheet. Safe to call on every run."""
    sheet_id = worksheet.id
    _apply_structural_formatting(spreadsheet, sheet_id)
    _apply_conditional_formatting(spreadsheet, sheet_id)


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
            format_odds(row.get("best_over_odds", "")),
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

    print(f"Authenticating with Google Sheets...")
    creds = get_google_creds()
    client = gspread.authorize(creds)

    spreadsheet = client.open_by_key(sheet_id)

    # Use or create a worksheet named "Picks"
    try:
        worksheet = spreadsheet.worksheet("Picks")
    except gspread.WorksheetNotFound:
        worksheet = spreadsheet.add_worksheet(title="Picks", rows=1000, cols=20)
        worksheet.append_row(SHEET_HEADERS)
        print("Created 'Picks' worksheet with headers.")

    # Check if headers exist; add if sheet is empty
    existing = worksheet.get_all_values()
    if not existing:
        worksheet.append_row(SHEET_HEADERS)

    # Apply visual formatting (idempotent — safe every run)
    print("Applying sheet formatting...")
    apply_sheet_formatting(spreadsheet, worksheet)

    # Fill in yesterday's results before appending today's rows
    print("Updating yesterday's results...")
    update_yesterday_results(worksheet)

    worksheet.append_rows(sheet_rows, value_input_option="USER_ENTERED")
    print(f"Exported {len(sheet_rows)} picks to Google Sheet.")
    print(f"Sheet URL: https://docs.google.com/spreadsheets/d/{sheet_id}")


if __name__ == "__main__":
    main()
