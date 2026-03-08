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
run_step "Team defense stats" fetch_team_defense_stats.py
run_step "Advanced stats"     fetch_advanced_stats.py

# Layer 2: Prediction model (your scripts)
run_step "Build features"     build_features.py
run_step "Predict shots"      predict_shots.py

echo ""
echo "========================================"
echo "Done — $(date '+%Y-%m-%d %H:%M:%S')"
echo "Output: .tmp/predictions_$(date +%Y-%m-%d).csv"
echo "========================================"
