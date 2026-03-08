#!/bin/bash
# run_daily.sh — NHL Shots Model daily pipeline
# Runs all scripts in order. Stops immediately if any script fails.

set -e  # exit on first error

PROJECT_DIR="$(cd "$(dirname "$0")" && pwd)"
PYTHON="$PROJECT_DIR/venv/bin/python3"
LOG_DIR="$PROJECT_DIR/.tmp/logs"
LOG_FILE="$LOG_DIR/run_$(date +%Y-%m-%d).log"

mkdir -p "$LOG_DIR"

echo "========================================"
echo "NHL Shots Model — $(date '+%Y-%m-%d %H:%M:%S')"
echo "========================================"

run_step() {
    local label="$1"
    local script="$2"
    echo ""
    echo "--- $label ---"
    "$PYTHON" "$PROJECT_DIR/tools/$script"
}

# Layer 1: Data collection (Friend A's scripts)
run_step "Schedule"           fetch_nhl_schedule.py
run_step "Player game logs"   fetch_player_game_logs.py
run_step "Collect real labels" collect_real_labels.py   # completes yesterday's pending lines with actual SOG
run_step "Team defense stats" fetch_team_defense_stats.py
run_step "Advanced stats"     fetch_advanced_stats.py

# Layer 2: Prediction model (your scripts)
run_step "Build features"     build_features.py
run_step "Predict shots"      predict_shots.py

# Layer 3: Lines comparison + output (Friend B's scripts)
run_step "Fetch odds"         fetch_odds.py
run_step "Compare lines"      compare_lines.py
run_step "Save daily lines"   save_daily_lines.py       # saves today's real lines as pending labels for tomorrow
run_step "Generate report"    generate_report.py
run_step "Export to sheets"   export_to_sheets.py

echo ""
echo "========================================"
echo "Done — $(date '+%Y-%m-%d %H:%M:%S')"
echo "Output: .tmp/best_lines_$(date +%Y-%m-%d).csv"
echo "========================================"
