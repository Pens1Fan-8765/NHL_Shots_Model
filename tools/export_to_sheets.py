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
from datetime import date

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

    worksheet.append_rows(sheet_rows)
    print(f"Exported {len(sheet_rows)} picks to Google Sheet.")
    print(f"Sheet URL: https://docs.google.com/spreadsheets/d/{sheet_id}")


if __name__ == "__main__":
    main()
