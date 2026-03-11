"""
Venetian — 6-Layer NCAAB Referee Betting Model
================================================
Encapsulates all scoring logic for the V5 system.
- Layer 1 (Ref Pedigree): AUTO — database lookup
- Layer 3 (Pace & Scoring): AUTO when combined PPG is entered
- Layers 2, 4, 5, 6: MANUAL inputs
- Grading engine with seasonal weighting and under asymmetry
"""

import csv
import json
import logging
import os
import re
import sqlite3
from datetime import datetime

log = logging.getLogger(__name__)

CSV_PATH = os.environ.get("CSV_PATH", os.path.join(os.path.dirname(__file__),
                          "refmetrics_ncaa_men_foul_leaders.csv"))

# ── TIER THRESHOLDS (V5) ────────────────────────────────────────────────────
STRONG_HIGH_THRESHOLD = 36.5
STRONG_LOW_THRESHOLD = 32.0


# ── CSV IMPORT ───────────────────────────────────────────────────────────────
def import_csv(conn: sqlite3.Connection, csv_path: str = None):
    """Import referee foul leaders CSV into the database."""
    path = csv_path or CSV_PATH
    if not os.path.exists(path):
        log.warning(f"Referee CSV not found at {path} — Layer 1 will be unavailable.")
        return 0

    imported_at = datetime.now().isoformat(timespec="seconds")
    count = 0

    with open(path, newline="", encoding="utf-8-sig") as f:
        reader = csv.DictReader(f)
        for row in reader:
            name = row.get("Referee", "").strip()
            if not name:
                continue

            # CSV "Total Fouls" column is already fouls per game
            fpg = _parse_float(row.get("Total Fouls"))
            home_fouls = _parse_float(row.get("Home Fouls"))
            away_fouls = _parse_float(row.get("Away Fouls"))
            foul_diff = _parse_float(row.get("Foul Diff"))
            total_games = _parse_int(row.get("Total Games"))

            if fpg is None:
                continue

            tier = _assign_tier(fpg)

            conn.execute("""
                INSERT INTO referee_foul_leaders
                    (referee_name, total_fouls, home_fouls, away_fouls,
                     foul_diff, total_games, fouls_per_game, tier, imported_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(referee_name) DO UPDATE SET
                    total_fouls=excluded.total_fouls,
                    home_fouls=excluded.home_fouls,
                    away_fouls=excluded.away_fouls,
                    foul_diff=excluded.foul_diff,
                    total_games=excluded.total_games,
                    fouls_per_game=excluded.fouls_per_game,
                    tier=excluded.tier,
                    imported_at=excluded.imported_at
            """, (name, fpg, home_fouls, away_fouls, foul_diff,
                  total_games, fpg, tier, imported_at))
            count += 1

    conn.commit()
    log.info(f"Imported {count} referees from CSV.")
    return count


# ── NAME MATCHING ────────────────────────────────────────────────────────────
def _normalize_name(name: str) -> str:
    """Normalize a referee name for matching."""
    n = name.strip().lower()
    # Remove suffixes
    n = re.sub(r'\s+(jr\.?|sr\.?|iii|ii|iv)$', '', n)
    # Collapse whitespace
    n = re.sub(r'\s+', ' ', n)
    return n


def lookup_ref(conn: sqlite3.Connection, name: str) -> dict | None:
    """Look up a referee in the foul leaders database."""
    if not name or not name.strip():
        return None

    normalized = _normalize_name(name)

    # Exact match (case-insensitive)
    row = conn.execute("""
        SELECT * FROM referee_foul_leaders
        WHERE LOWER(referee_name) = ?
    """, (normalized,)).fetchone()

    if row:
        return _row_to_dict(row)

    # Try last-name match (only if unique)
    last_name = normalized.split()[-1] if normalized else ""
    if last_name:
        rows = conn.execute("""
            SELECT * FROM referee_foul_leaders
            WHERE LOWER(referee_name) LIKE ?
        """, (f"% {last_name}",)).fetchall()
        if len(rows) == 1:
            return _row_to_dict(rows[0])

    return None


def _row_to_dict(row) -> dict:
    return {
        "name": row["referee_name"],
        "fouls_per_game": row["fouls_per_game"],
        "tier": row["tier"],
        "total_games": row["total_games"],
        "home_fouls": row["home_fouls"],
        "away_fouls": row["away_fouls"],
        "foul_diff": row["foul_diff"],
    }


# ── LAYER 1: REF PEDIGREE (AUTO) ────────────────────────────────────────────
def score_layer_1(conn: sqlite3.Connection,
                  crew_chief: str, u1: str, u2: str) -> dict:
    """Auto-score Layer 1 from the referee foul leaders database."""
    refs = []
    for name in [crew_chief, u1, u2]:
        if not name or not name.strip():
            continue
        data = lookup_ref(conn, name)
        if data:
            refs.append(data)
        else:
            refs.append({"name": name, "fouls_per_game": None, "tier": "NOT_FOUND",
                         "total_games": None, "home_fouls": None,
                         "away_fouls": None, "foul_diff": None})

    # Count refs with actual data
    refs_with_data = [r for r in refs if r["fouls_per_game"] is not None]

    if not refs_with_data:
        return {
            "l1_crew_avg": None,
            "l1_tier": "NO_DATA",
            "l1_strong_high_count": 0,
            "l1_strong_low_count": 0,
            "l1_signal": "NEUTRAL",
            "l1_ref_details": json.dumps([_ref_summary(r) for r in refs]),
            "l1_aligned": 0,
        }

    crew_avg = sum(r["fouls_per_game"] for r in refs_with_data) / len(refs_with_data)
    strong_high = sum(1 for r in refs_with_data if r["tier"] == "STRONG_HIGH")
    strong_low = sum(1 for r in refs_with_data if r["tier"] == "STRONG_LOW")

    # Determine crew tier
    crew_tier = _assign_tier(crew_avg)

    # Determine signal
    if strong_high > 0 and strong_low > 0:
        signal = "MIXED"
        aligned = 0
    elif crew_avg > STRONG_HIGH_THRESHOLD and strong_high >= 2:
        signal = "OVER"
        aligned = 1
    elif crew_avg < STRONG_LOW_THRESHOLD and strong_low >= 2:
        signal = "UNDER"
        aligned = 1
    elif crew_avg > STRONG_HIGH_THRESHOLD:
        # Strong high avg but fewer than 2 anchors — still a signal, weaker
        signal = "OVER"
        aligned = 1
    elif crew_avg < STRONG_LOW_THRESHOLD:
        signal = "UNDER"
        aligned = 1
    else:
        signal = "NEUTRAL"
        aligned = 0

    return {
        "l1_crew_avg": round(crew_avg, 2),
        "l1_tier": crew_tier,
        "l1_strong_high_count": strong_high,
        "l1_strong_low_count": strong_low,
        "l1_signal": signal,
        "l1_ref_details": json.dumps([_ref_summary(r) for r in refs]),
        "l1_aligned": aligned,
    }


def _ref_summary(ref: dict) -> dict:
    return {
        "name": ref["name"],
        "fpg": ref["fouls_per_game"],
        "tier": ref["tier"],
        "games": ref["total_games"],
    }


# ── LAYER 3: PACE & SCORING (AUTO) ──────────────────────────────────────────
def score_layer_3(combined_ppg: float, ou_line: float,
                  signal_direction: str) -> dict:
    """Auto-score Layer 3 by comparing combined PPG to the O/U line."""
    if combined_ppg is None or ou_line is None:
        return {"l3_ppg_vs_line": None, "l3_aligned": 0}

    diff = combined_ppg - ou_line

    if diff >= 0:
        ppg_vs_line = "ABOVE"
        # PPG above line supports OVER, contradicts UNDER
        aligned = 1 if signal_direction == "OVER" else 0
    elif diff >= -5:
        ppg_vs_line = "CLOSE"
        aligned = 0
    else:
        ppg_vs_line = "BELOW_10"
        # PPG well below line supports UNDER, contradicts OVER
        aligned = 1 if signal_direction == "UNDER" else 0

    return {"l3_ppg_vs_line": ppg_vs_line, "l3_aligned": aligned}


# ── GRADING ENGINE ───────────────────────────────────────────────────────────
def compute_grade(analysis: dict, game_date: str) -> dict:
    """Compute the final confidence grade from all layer alignments."""
    l1_signal = analysis.get("l1_signal", "NEUTRAL")

    # Stay-away triggers → auto-PASS
    stay_away = _check_stay_away(analysis)
    if stay_away:
        return {
            "layers_aligned": 0,
            "signal_direction": None,
            "grade": "PASS",
            "stay_away_reason": stay_away,
        }

    # Count aligned layers
    aligned = sum(1 for i in range(1, 7) if analysis.get(f"l{i}_aligned") == 1)

    # Signal direction from Layer 1
    signal = l1_signal if l1_signal in ("OVER", "UNDER") else None

    if signal is None:
        return {
            "layers_aligned": aligned,
            "signal_direction": None,
            "grade": "PASS",
            "stay_away_reason": "No directional signal from Layer 1",
        }

    # Determine threshold based on season and direction
    threshold = _get_layer_threshold(signal, game_date)

    if aligned >= 5:
        grade = "FULL_UNIT"
    elif aligned >= threshold:
        grade = "HALF_UNIT"
    else:
        grade = "PASS"

    return {
        "layers_aligned": aligned,
        "signal_direction": signal,
        "grade": grade,
        "stay_away_reason": None,
    }


def _check_stay_away(analysis: dict) -> str | None:
    """Check for any stay-away triggers. Returns reason string or None."""
    l1_signal = analysis.get("l1_signal", "NEUTRAL")
    l1_tier = analysis.get("l1_tier", "NEUTRAL")
    ou_line = analysis.get("ou_line")

    # Incomplete crew (only 1 ref or no data)
    ref_details = analysis.get("l1_ref_details", "[]")
    if isinstance(ref_details, str):
        try:
            refs = json.loads(ref_details)
        except (json.JSONDecodeError, TypeError):
            refs = []
    else:
        refs = ref_details
    refs_with_data = [r for r in refs if r.get("fpg") is not None]
    total_refs = [r for r in refs if r.get("tier") != "NOT_FOUND" or r.get("fpg") is not None]
    if len(total_refs) < 2 and len(refs) > 0:
        return "Incomplete crew (fewer than 2 refs with data)"

    # Mixed crew
    if l1_signal == "MIXED":
        return "Mixed crew (STRONG HIGH + STRONG LOW)"

    # Neutral crew
    if l1_signal == "NEUTRAL":
        return "Neutral crew average (32.0-36.5)"

    # O/U 160+ and under signal
    if ou_line and ou_line >= 160 and l1_signal == "UNDER":
        return "O/U line 160+ with UNDER signal"

    # No ref data
    if l1_tier == "NO_DATA":
        return "No referee data available"

    return None


def _get_layer_threshold(signal: str, game_date: str) -> int:
    """Return the minimum layers required for a bet based on signal + season."""
    # Under plays always require 5 layers
    if signal == "UNDER":
        return 5

    # Over plays: seasonal weighting
    try:
        dt = datetime.strptime(game_date, "%Y-%m-%d")
        month = dt.month
        # Nov-Jan = early season (5+ layers), Feb-Mar = late season (4+ layers)
        if month in (11, 12, 1):
            return 5
        else:
            return 4
    except (ValueError, TypeError):
        return 4  # default to late-season threshold


def get_seasonal_mode(game_date: str) -> dict:
    """Return the active seasonal mode for display."""
    try:
        dt = datetime.strptime(game_date, "%Y-%m-%d")
        month = dt.month
        if month in (11, 12, 1):
            return {
                "mode": "EARLY_SEASON",
                "label": "NOV-JAN MODE: 5+ layers for over",
                "over_threshold": 5,
            }
        else:
            return {
                "mode": "LATE_SEASON",
                "label": "FEB-MAR MODE: 4+ layers for over",
                "over_threshold": 4,
            }
    except (ValueError, TypeError):
        return {
            "mode": "LATE_SEASON",
            "label": "FEB-MAR MODE: 4+ layers for over",
            "over_threshold": 4,
        }


# ── HELPERS ──────────────────────────────────────────────────────────────────
def _assign_tier(fpg: float) -> str:
    if fpg > STRONG_HIGH_THRESHOLD:
        return "STRONG_HIGH"
    elif fpg < STRONG_LOW_THRESHOLD:
        return "STRONG_LOW"
    return "NEUTRAL"


def _parse_float(val) -> float | None:
    try:
        return float(val)
    except (ValueError, TypeError):
        return None


def _parse_int(val) -> int | None:
    try:
        return int(val)
    except (ValueError, TypeError):
        return None
