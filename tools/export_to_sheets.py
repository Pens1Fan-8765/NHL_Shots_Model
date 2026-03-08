"""
export_to_sheets.py

Pushes today's ranked picks to a shared Google Sheet.

Requires:
  .env with GOOGLE_SHEETS_ID
  credentials.json (Google Cloud OAuth client — gitignored)
  .tmp/best_lines_YYYY-MM-DD.csv

Sheet columns:
  Date | Player | Team | Opp | Proj SOG | Line | Direction | Book | Odds |
  Confidence | Edge | Line Shopping | Result

Result column is left blank — fill it in the next day with actual SOG.

First-time setup:
  1. Go to Google Cloud Console → Create project → Enable Google Sheets API
  2. Create OAuth 2.0 Desktop credentials → Download as credentials.json
  3. Place credentials.json in the project root (it's gitignored)
  4. Run this script — a browser window opens to authorize
  5. token.json is saved locally for future runs
"""

import csv
import os
from datetime import date

import gspread
from dotenv import load_dotenv
from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow

load_dotenv()

TMP_DIR = os.path.join(os.path.dirname(__file__), "..", ".tmp")
ROOT_DIR = os.path.join(os.path.dirname(__file__), "..")
CREDENTIALS_PATH = os.path.join(ROOT_DIR, "credentials.json")
TOKEN_PATH = os.path.join(ROOT_DIR, "token.json")
SCOPES = ["https://www.googleapis.com/auth/spreadsheets"]

SHEET_HEADERS = [
    "Date", "Player", "Team", "Opp", "Proj SOG", "Line", "Direction",
    "Book", "Odds", "Confidence %", "Edge", "Line Shopping", "Result",
]


def get_google_creds() -> Credentials:
    creds = None
    if os.path.exists(TOKEN_PATH):
        creds = Credentials.from_authorized_user_file(TOKEN_PATH, SCOPES)

    if not creds or not creds.valid:
        if creds and creds.expired and creds.refresh_token:
            creds.refresh(Request())
        else:
            if not os.path.exists(CREDENTIALS_PATH):
                raise FileNotFoundError(
                    "credentials.json not found. Download from Google Cloud Console "
                    "and place in the project root. See workflows/sportsbook_scraping.md."
                )
            flow = InstalledAppFlow.from_client_secrets_file(CREDENTIALS_PATH, SCOPES)
            creds = flow.run_local_server(port=0)
        with open(TOKEN_PATH, "w") as token:
            token.write(creds.to_json())

    return creds


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
