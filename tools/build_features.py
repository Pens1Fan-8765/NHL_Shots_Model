"""
build_features.py

Reads all Layer 1 output files and engineers features for each player
appearing in today's games.

Requires:
  .tmp/schedule_YYYY-MM-DD.json
  .tmp/player_logs_YYYY-MM-DD.json
  .tmp/team_defense_YYYY-MM-DD.json
  .tmp/advanced_stats_YYYY-MM-DD.csv

Output: .tmp/features_YYYY-MM-DD.csv

Schema (one row per player):
  player_key, team, opponent, home_flag, b2b_flag,
  sog_avg_5, sog_avg_10, sog_avg_20,
  sog_vs_opp, toi_avg_5,
  opp_sa_per_game_season, opp_sa_per_game_l10,
  trend_ratio, xSF_per_60, CF_pct, FF_pct, iSCF_per_60,
  games_in_log
"""

import csv
import json
import os
from datetime import date, datetime, timedelta

TMP_DIR = os.path.join(os.path.dirname(__file__), "..", ".tmp")
MIN_GAMES = 5  # minimum game log entries to include a player


def load_json(path: str) -> dict | list:
    with open(path) as f:
        return json.load(f)


def load_csv_as_dict(path: str, key_field: str) -> dict[str, dict]:
    result = {}
    with open(path, newline="") as f:
        for row in csv.DictReader(f):
            result[row[key_field]] = row
    return result


def rolling_avg(values: list[float], n: int) -> float:
    subset = values[:n]
    if not subset:
        return 0.0
    return round(sum(subset) / len(subset), 3)


def is_back_to_back(game_logs: list[dict]) -> bool:
    """Returns True if the most recent game in logs was yesterday."""
    if not game_logs:
        return False
    today = date.today()
    try:
        last_game_date = datetime.strptime(game_logs[0]["date"], "%Y-%m-%d").date()
        return last_game_date == today - timedelta(days=1)
    except (ValueError, KeyError):
        return False


def build_features(
    schedule: list[dict],
    player_logs: dict[str, list],
    team_defense: dict[str, dict],
    advanced_stats: dict[str, dict],
    today_str: str = "",
) -> list[dict]:
    # Build quick lookup: team -> opponent, home flag
    team_context: dict[str, dict] = {}
    for game in schedule:
        team_context[game["home_team"]] = {"opponent": game["away_team"], "home": 1}
        team_context[game["away_team"]] = {"opponent": game["home_team"], "home": 0}

    rows = []
    for player_key, logs in player_logs.items():
        if len(logs) < MIN_GAMES:
            continue

        # Determine team from player key (last segment)
        parts = player_key.rsplit("_", 1)
        if len(parts) != 2:
            continue
        team = parts[1]

        if team not in team_context:
            continue  # player's team not playing today

        context = team_context[team]
        opponent = context["opponent"]

        # SOG values in chronological order (most recent first from API)
        sog_values = [g["sog"] for g in logs]
        toi_values = [g["toi"] for g in logs]

        sog_avg_5 = rolling_avg(sog_values, 5)
        sog_avg_10 = rolling_avg(sog_values, 10)
        sog_avg_20 = rolling_avg(sog_values, 20)
        toi_avg_5 = rolling_avg(toi_values, 5)

        # SOG vs today's specific opponent
        opp_games = [g["sog"] for g in logs if g.get("opponent") == opponent]
        sog_vs_opp = round(sum(opp_games) / len(opp_games), 3) if opp_games else sog_avg_20

        # Opponent defense
        opp_defense = team_defense.get(opponent, {})
        opp_sa_season = float(opp_defense.get("sa_season", 0) or 0)
        opp_sa_l10 = float(opp_defense.get("sa_l10", 0) or 0)

        # Trend ratio (recent vs baseline)
        trend_ratio = round(sog_avg_5 / sog_avg_20, 3) if sog_avg_20 > 0 else 1.0

        # Back-to-back flag
        b2b = 1 if is_back_to_back(logs) else 0

        # Advanced stats
        adv = advanced_stats.get(player_key, {})
        xsf_per_60 = float(adv.get("xSF_per_60", 0) or 0)
        cf_pct = float(adv.get("CF_pct", 0) or 0)
        ff_pct = float(adv.get("FF_pct", 0) or 0)
        iscf_per_60 = float(adv.get("iSCF_per_60", 0) or 0)

        rows.append({
            "player_key": player_key,
            "team": team,
            "opponent": opponent,
            "game_date": today_str,
            "home_flag": context["home"],
            "b2b_flag": b2b,
            "sog_avg_5": sog_avg_5,
            "sog_avg_10": sog_avg_10,
            "sog_avg_20": sog_avg_20,
            "sog_vs_opp": sog_vs_opp,
            "toi_avg_5": toi_avg_5,
            "opp_sa_per_game_season": opp_sa_season,
            "opp_sa_per_game_l10": opp_sa_l10,
            "trend_ratio": trend_ratio,
            "xSF_per_60": xsf_per_60,
            "CF_pct": cf_pct,
            "FF_pct": ff_pct,
            "iSCF_per_60": iscf_per_60,
            "games_in_log": len(logs),
        })

    return rows


def main():
    today_str = date.today().isoformat()

    schedule = load_json(os.path.join(TMP_DIR, f"schedule_{today_str}.json"))
    player_logs = load_json(os.path.join(TMP_DIR, f"player_logs_{today_str}.json"))
    team_defense = load_json(os.path.join(TMP_DIR, f"team_defense_{today_str}.json"))
    advanced_stats = load_csv_as_dict(
        os.path.join(TMP_DIR, f"advanced_stats_{today_str}.csv"), "player_key"
    )

    print(f"Building features for {len(player_logs)} players...")
    features = build_features(schedule, player_logs, team_defense, advanced_stats, today_str)
    print(f"Generated features for {len(features)} players in today's games.")

    out_path = os.path.join(TMP_DIR, f"features_{today_str}.csv")
    fieldnames = [
        "player_key", "team", "opponent", "game_date", "home_flag", "b2b_flag",
        "sog_avg_5", "sog_avg_10", "sog_avg_20", "sog_vs_opp", "toi_avg_5",
        "opp_sa_per_game_season", "opp_sa_per_game_l10",
        "trend_ratio", "xSF_per_60", "CF_pct", "FF_pct", "iSCF_per_60",
        "games_in_log",
    ]
    with open(out_path, "w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(features)

    print(f"Saved to {out_path}")


if __name__ == "__main__":
    main()
