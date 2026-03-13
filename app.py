"""
Venetian — Minimal web frontend for RefMetrics scraper data.
Reads from the same SQLite database the scraper writes to.
"""

import json
import logging
import os
import sqlite3
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

import requests
import threading

from flask import Flask, g, render_template, jsonify, request

DISPLAY_TZ = ZoneInfo(os.environ.get("DISPLAY_TZ", "America/Chicago"))

app = Flask(__name__)
DB_PATH = os.environ.get("DB_PATH", "/app/data/refmetrics.db")
SLACK_WEBHOOK = os.environ.get("SLACK_WEBHOOK", "")

log = logging.getLogger(__name__)


def _migrate_db(conn):
    """Ensure result-tracking columns and notification tracking exist."""
    try:
        cursor = conn.execute("PRAGMA table_info(game_analysis)")
        cols = {row[1] for row in cursor.fetchall()}
        if cols and "actual_total" not in cols:
            conn.execute("ALTER TABLE game_analysis ADD COLUMN actual_total REAL")
            conn.execute("ALTER TABLE game_analysis ADD COLUMN home_score INTEGER")
            conn.execute("ALTER TABLE game_analysis ADD COLUMN away_score INTEGER")
            conn.execute("ALTER TABLE game_analysis ADD COLUMN result TEXT")
            conn.commit()
            log.info("Migrated game_analysis: added result tracking columns.")
    except Exception:
        pass
    try:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS notifications_sent (
                id          INTEGER PRIMARY KEY AUTOINCREMENT,
                game_key    TEXT NOT NULL,
                notif_type  TEXT NOT NULL,
                sent_at     TEXT,
                UNIQUE(game_key, notif_type)
            )
        """)
        conn.commit()
    except Exception:
        pass


def get_db():
    if "db" not in g:
        g.db = sqlite3.connect(DB_PATH)
        g.db.row_factory = sqlite3.Row
        _migrate_db(g.db)
    return g.db


@app.teardown_appcontext
def close_db(exc):
    db = g.pop("db", None)
    if db is not None:
        db.close()


@app.route("/")
def index():
    db = get_db()
    today = datetime.now(DISPLAY_TZ).strftime("%Y-%m-%d")

    # Auto-fetch O/U lines and final scores
    from refmetrics_scraper import update_odds, update_game_results
    try:
        update_odds(db)
    except Exception as e:
        log.warning(f"Odds update failed: {e}")
    try:
        update_game_results(db)
    except Exception as e:
        log.warning(f"Result update failed: {e}")

    # Fetch all games, grouped by date (today first, then descending)
    games = db.execute("""
        SELECT * FROM game_assignments
        ORDER BY
            CASE WHEN game_date = ? THEN 0 ELSE 1 END,
            game_date DESC,
            game_time
    """, (today,)).fetchall()

    ref_stats = {}
    for game in games:
        for ref_name in [game["crew_chief"], game["u1"], game["u2"]]:
            if ref_name and ref_name.strip() and ref_name not in ref_stats:
                row = db.execute("""
                    SELECT * FROM referee_stats
                    WHERE official_name = ?
                    ORDER BY scraped_at DESC
                    LIMIT 1
                """, (ref_name,)).fetchone()
                if row:
                    ref_stats[ref_name] = dict(row)

    total_games = db.execute(
        "SELECT COUNT(*) as c FROM game_assignments WHERE game_date = ?", (today,)
    ).fetchone()["c"]

    assigned = db.execute(
        "SELECT COUNT(*) as c FROM game_assignments WHERE game_date = ? AND assignment_src != 'none'",
        (today,)
    ).fetchone()["c"]

    last_scrape = db.execute(
        "SELECT MAX(scraped_at) as t FROM game_assignments"
    ).fetchone()["t"]

    # Load analysis data for each game
    from analysis import get_seasonal_mode
    analyses = {}
    plays_count = 0
    try:
        for game in games:
            gk = game["game_key"]
            row = db.execute(
                "SELECT * FROM game_analysis WHERE game_key = ?", (gk,)
            ).fetchone()
            if row:
                a = dict(row)
                if a.get("l1_ref_details"):
                    try:
                        a["l1_ref_details"] = json.loads(a["l1_ref_details"])
                    except (json.JSONDecodeError, TypeError):
                        pass
                analyses[gk] = a
                if game["game_date"] == today and a.get("grade") in ("HALF_UNIT", "FULL_UNIT"):
                    plays_count += 1
    except Exception:
        pass  # table may not exist yet on first run

    seasonal_mode = get_seasonal_mode(today)

    # Determine which games are finished using NCAA API scoreboard
    # Only fetch NCAA data for today's games (past dates are all completed)
    from refmetrics_scraper import fetch_ncaa_scoreboard, _match_ncaa_game
    ncaa_date = today.replace("-", "/")
    try:
        ncaa_games = fetch_ncaa_scoreboard(ncaa_date)
    except Exception:
        ncaa_games = []

    ncaa_final_teams = set()
    for ng in ncaa_games:
        if ng["game_state"] == "final":
            ncaa_final_teams.add(ng["home_short"].lower())
            ncaa_final_teams.add(ng["away_short"].lower())

    now_local = datetime.now(DISPLAY_TZ)
    completed_keys = set()
    for game in games:
        gk = game["game_key"]
        # Past dates are always completed
        if game["game_date"] < today:
            completed_keys.add(gk)
            continue
        # If we have a final score stored, it's definitely done
        a = analyses.get(gk, {})
        if a and a.get("actual_total"):
            completed_keys.add(gk)
            continue
        # Check NCAA API game state
        home_lower = game["home_team"].lower()
        away_lower = game["away_team"].lower()
        if home_lower in ncaa_final_teams or away_lower in ncaa_final_teams:
            ncaa_match = _match_ncaa_game(game["home_team"], game["away_team"], ncaa_games)
            if ncaa_match and ncaa_match["game_state"] == "final":
                completed_keys.add(gk)
                continue
        # Fallback: estimate from game time (3+ hours after tip)
        gt = game["game_time"]
        if gt:
            try:
                game_dt = datetime.strptime(f"{game['game_date']} {gt}", "%Y-%m-%d %I:%M %p")
                game_dt = game_dt.replace(tzinfo=DISPLAY_TZ)
                if now_local > game_dt + timedelta(hours=3):
                    completed_keys.add(gk)
            except (ValueError, TypeError):
                pass

    # Group games by date for template
    from collections import OrderedDict
    games_by_date = OrderedDict()
    for game in games:
        d = game["game_date"]
        if d not in games_by_date:
            games_by_date[d] = []
        games_by_date[d].append(game)

    return render_template("index.html",
        games_by_date=games_by_date,
        ref_stats=ref_stats,
        analyses=analyses,
        completed_keys=completed_keys,
        seasonal_mode=seasonal_mode,
        plays_count=plays_count,
        today=today,
        total_games=total_games,
        assigned=assigned,
        last_scrape=last_scrape,
    )


@app.route("/results")
def results():
    db = get_db()

    # Auto-fetch O/U lines and final scores
    from refmetrics_scraper import update_game_results, update_odds
    try:
        update_odds(db)
    except Exception as e:
        log.warning(f"Odds update failed: {e}")
    try:
        update_game_results(db)
    except Exception as e:
        log.warning(f"Result update failed: {e}")
        try:
            db.rollback()
        except Exception:
            pass

    # Backfill: auto-score Layer 1 for any games missing analysis rows
    from analysis import score_layer_1
    try:
        unscored = db.execute("""
            SELECT gm.game_key, gm.crew_chief, gm.u1, gm.u2
            FROM game_assignments gm
            LEFT JOIN game_analysis ga ON gm.game_key = ga.game_key
            WHERE ga.id IS NULL
              AND gm.crew_chief IS NOT NULL AND gm.crew_chief != ''
        """).fetchall()
        for row in unscored:
            cc, u1, u2 = row["crew_chief"], row["u1"] or "", row["u2"] or ""
            ref_count = sum(1 for n in [cc, u1, u2] if n and n.strip())
            if ref_count < 2:
                continue
            l1 = score_layer_1(db, cc, u1, u2)
            now_ts = datetime.now(DISPLAY_TZ).isoformat(timespec="seconds")
            db.execute("""
                INSERT INTO game_analysis
                    (game_key, l1_crew_avg, l1_tier, l1_strong_high_count,
                     l1_strong_low_count, l1_signal, l1_ref_details, l1_aligned,
                     created_at, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (row["game_key"], l1["l1_crew_avg"], l1["l1_tier"],
                  l1["l1_strong_high_count"], l1["l1_strong_low_count"],
                  l1["l1_signal"], l1["l1_ref_details"], l1["l1_aligned"],
                  now_ts, now_ts))
        if unscored:
            db.commit()
            log.info(f"Backfilled {len(unscored)} analysis rows for results page.")
    except Exception as e:
        log.warning(f"Analysis backfill failed: {e}")

    # All green-card games (crew avg >= 35.5) across all dates
    games = []
    try:
        rows = db.execute("""
            SELECT ga.*, gm.home_team, gm.away_team, gm.home_logo, gm.away_logo,
                   gm.game_date, gm.game_time, gm.network, gm.espn_game_id,
                   gm.crew_chief, gm.u1, gm.u2
            FROM game_analysis ga
            JOIN game_assignments gm ON ga.game_key = gm.game_key
            WHERE ga.l1_crew_avg >= 35.5
            ORDER BY gm.game_date DESC, gm.game_time DESC
        """).fetchall()

        for row in rows:
            gd = dict(row)
            if gd.get("l1_ref_details"):
                try:
                    gd["l1_ref_details"] = json.loads(gd["l1_ref_details"])
                except (json.JSONDecodeError, TypeError):
                    pass
            games.append(gd)
    except Exception as e:
        log.warning(f"Results query failed: {e}")

    # Summary stats
    total = len(games)
    with_results = [g for g in games if g.get("result")]
    hits = sum(1 for g in with_results if g["result"] == "HIT")
    misses = sum(1 for g in with_results if g["result"] == "MISS")
    pushes = sum(1 for g in with_results if g["result"] == "PUSH")
    pending = total - len(with_results)
    win_pct = (hits / (hits + misses) * 100) if (hits + misses) > 0 else 0

    return render_template("results.html",
        games=games,
        total=total,
        hits=hits,
        misses=misses,
        pushes=pushes,
        pending=pending,
        win_pct=win_pct,
    )


@app.route("/api/backfill-lines", methods=["POST"])
def backfill_lines():
    """One-shot backfill of O/U lines and scores for 2026-03-12 games."""
    db = get_db()
    games = [
        {"away": "Rhode Island",  "home": "Duquesne",    "ou_line": 135.5, "away_score": 61, "home_score": 67},
        {"away": "UCF",           "home": "Arizona",     "ou_line": 136.0, "away_score": 59, "home_score": 81},
        {"away": "Auburn",        "home": "Tennessee",   "ou_line": 135.5, "away_score": 62, "home_score": 72},
        {"away": "Kentucky",      "home": "Missouri",    "ou_line": 135.5, "away_score": 78, "home_score": 72},
        {"away": "Iowa",          "home": "Ohio State",  "ou_line": 135.5, "away_score": 69, "home_score": 72},
        {"away": "Massachusetts", "home": "Miami (OH)",  "ou_line": 136.0, "away_score": 87, "home_score": 83},
    ]
    results = []
    for g in games:
        actual_total = g["away_score"] + g["home_score"]
        row = db.execute("""
            SELECT ga.game_key, ga.signal_direction
            FROM game_analysis ga
            JOIN game_assignments gm ON ga.game_key = gm.game_key
            WHERE gm.game_date = '2026-03-12'
              AND LOWER(gm.home_team) LIKE ?
              AND LOWER(gm.away_team) LIKE ?
        """, (f"%{g['home'].lower()}%", f"%{g['away'].lower()}%")).fetchone()

        if not row:
            results.append(f"NOT FOUND: {g['away']} @ {g['home']}")
            continue

        signal = row["signal_direction"]
        result = None
        if g["ou_line"] and signal:
            if actual_total == g["ou_line"]:
                result = "PUSH"
            elif signal == "OVER":
                result = "HIT" if actual_total > g["ou_line"] else "MISS"
            elif signal == "UNDER":
                result = "HIT" if actual_total < g["ou_line"] else "MISS"

        db.execute("""
            UPDATE game_analysis
            SET ou_line = ?, home_score = ?, away_score = ?,
                actual_total = ?, result = ?
            WHERE game_key = ?
        """, (g["ou_line"], g["home_score"], g["away_score"],
              actual_total, result, row["game_key"]))
        results.append(f"OK: {g['away']} @ {g['home']} | O/U {g['ou_line']} | Total {actual_total} | {result or 'NO_SIGNAL'}")

    db.commit()
    return jsonify({"updated": len([r for r in results if r.startswith("OK")]), "details": results})


@app.route("/api/ref/<name>")
def ref_detail(name):
    db = get_db()
    row = db.execute("""
        SELECT * FROM referee_stats
        WHERE official_name = ?
        ORDER BY scraped_at DESC
        LIMIT 1
    """, (name,)).fetchone()

    if not row:
        return jsonify({"error": "not found"}), 404

    data = dict(row)
    if data.get("raw_stats_json"):
        data["raw_stats"] = json.loads(data["raw_stats_json"])
    return jsonify(data)


# ── ANALYSIS ENDPOINTS ────────────────────────────────────────────────────────

@app.route("/api/game/<path:game_key>/analysis", methods=["GET"])
def get_analysis(game_key):
    from analysis import score_layer_1, get_seasonal_mode, compute_grade
    db = get_db()
    today = datetime.now(DISPLAY_TZ).strftime("%Y-%m-%d")

    row = db.execute(
        "SELECT * FROM game_analysis WHERE game_key = ?", (game_key,)
    ).fetchone()

    if not row:
        # Auto-create by scoring Layer 1
        game = db.execute(
            "SELECT * FROM game_assignments WHERE game_key = ?", (game_key,)
        ).fetchone()
        if not game:
            return jsonify({"error": "game not found"}), 404

        l1 = score_layer_1(db, game["crew_chief"] or "", game["u1"] or "", game["u2"] or "")
        now = datetime.now().isoformat(timespec="seconds")
        db.execute("""
            INSERT INTO game_analysis
                (game_key, l1_crew_avg, l1_tier, l1_strong_high_count,
                 l1_strong_low_count, l1_signal, l1_ref_details, l1_aligned,
                 created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (game_key, l1["l1_crew_avg"], l1["l1_tier"], l1["l1_strong_high_count"],
              l1["l1_strong_low_count"], l1["l1_signal"], l1["l1_ref_details"],
              l1["l1_aligned"], now, now))
        db.commit()
        row = db.execute(
            "SELECT * FROM game_analysis WHERE game_key = ?", (game_key,)
        ).fetchone()

    data = dict(row)
    # Parse ref details JSON
    if data.get("l1_ref_details"):
        try:
            data["l1_ref_details"] = json.loads(data["l1_ref_details"])
        except (json.JSONDecodeError, TypeError):
            pass

    data["seasonal_mode"] = get_seasonal_mode(today)
    return jsonify(data)


@app.route("/api/game/<path:game_key>/analysis", methods=["POST"])
def save_analysis(game_key):
    from analysis import score_layer_3, compute_grade
    db = get_db()
    today = datetime.now(DISPLAY_TZ).strftime("%Y-%m-%d")

    # Ensure row exists
    existing = db.execute(
        "SELECT id FROM game_analysis WHERE game_key = ?", (game_key,)
    ).fetchone()
    if not existing:
        return jsonify({"error": "analysis not found, GET first to create"}), 404

    body = request.get_json(force=True)
    now = datetime.now().isoformat(timespec="seconds")

    # Updatable fields
    fields = [
        "ou_line",
        "l2_home_fouls_pg", "l2_away_fouls_pg", "l2_home_ftr", "l2_away_ftr",
        "l2_home_fta_pg", "l2_away_fta_pg", "l2_aligned",
        "l3_combined_ppg", "l3_pace_rating", "l3_aligned",
        "l4_has_18ppg_scorer", "l4_has_foul_drawer", "l4_has_3pt_volume",
        "l4_notes", "l4_aligned",
        "l5_pressing_defense", "l5_attack_basket", "l5_slow_methodical",
        "l5_notes", "l5_aligned",
        "l6_senior_day", "l6_rivalry", "l6_tournament_impl",
        "l6_late_season", "l6_power_ref_midmajor", "l6_notes", "l6_aligned",
    ]

    updates = []
    values = []
    for f in fields:
        if f in body:
            updates.append(f"{f} = ?")
            values.append(body[f])

    if updates:
        updates.append("updated_at = ?")
        values.append(now)
        values.append(game_key)
        db.execute(
            f"UPDATE game_analysis SET {', '.join(updates)} WHERE game_key = ?",
            values
        )
        db.commit()

    # Re-read the full row
    row = dict(db.execute(
        "SELECT * FROM game_analysis WHERE game_key = ?", (game_key,)
    ).fetchone())

    # Auto-score Layer 3 if both PPG and O/U line are present
    if row.get("l3_combined_ppg") and row.get("ou_line"):
        l3 = score_layer_3(row["l3_combined_ppg"], row["ou_line"],
                           row.get("l1_signal", "NEUTRAL"))
        db.execute("""
            UPDATE game_analysis
            SET l3_ppg_vs_line = ?, l3_aligned = ?, updated_at = ?
            WHERE game_key = ?
        """, (l3["l3_ppg_vs_line"], l3["l3_aligned"], now, game_key))
        db.commit()
        row.update(l3)

    # Recompute grade
    grade_result = compute_grade(row, today)
    db.execute("""
        UPDATE game_analysis
        SET layers_aligned = ?, signal_direction = ?, grade = ?,
            stay_away_reason = ?, updated_at = ?
        WHERE game_key = ?
    """, (grade_result["layers_aligned"], grade_result["signal_direction"],
          grade_result["grade"], grade_result.get("stay_away_reason"),
          now, game_key))
    db.commit()

    # Return updated row
    final = dict(db.execute(
        "SELECT * FROM game_analysis WHERE game_key = ?", (game_key,)
    ).fetchone())
    if final.get("l1_ref_details"):
        try:
            final["l1_ref_details"] = json.loads(final["l1_ref_details"])
        except (json.JSONDecodeError, TypeError):
            pass
    from analysis import get_seasonal_mode
    final["seasonal_mode"] = get_seasonal_mode(today)
    return jsonify(final)


@app.route("/api/game/<path:game_key>/finalize", methods=["POST"])
def finalize_analysis(game_key):
    from analysis import compute_grade
    db = get_db()
    today = datetime.now(DISPLAY_TZ).strftime("%Y-%m-%d")

    row = db.execute(
        "SELECT * FROM game_analysis WHERE game_key = ?", (game_key,)
    ).fetchone()
    if not row:
        return jsonify({"error": "analysis not found"}), 404

    analysis = dict(row)
    if analysis.get("grade_finalized"):
        return jsonify({"error": "already finalized"}), 409

    grade = analysis.get("grade", "PASS")
    signal = analysis.get("signal_direction")

    now = datetime.now().isoformat(timespec="seconds")
    db.execute("""
        UPDATE game_analysis
        SET grade_finalized = 1, finalized_at = ?
        WHERE game_key = ?
    """, (now, game_key))
    db.commit()

    # Send notifications for actionable grades (only once per game)
    if grade in ("HALF_UNIT", "FULL_UNIT"):
        from refmetrics_scraper import was_notified, mark_notified
        if not was_notified(db, game_key, "discord_analysis"):
            game = db.execute(
                "SELECT * FROM game_assignments WHERE game_key = ?", (game_key,)
            ).fetchone()
            if game:
                game_dict = dict(game)
                _notify_analysis(game_dict, analysis)
                mark_notified(db, game_key, "discord_analysis")

    return jsonify({"status": "finalized", "grade": grade,
                    "signal_direction": signal})


def _notify_analysis(game: dict, analysis: dict):
    """Send Discord + Slack notifications for a finalized betting play."""
    grade_label = analysis.get("grade", "").replace("_", " ")
    signal = analysis.get("signal_direction", "")
    crew_avg = analysis.get("l1_crew_avg", "?")
    ou_line = analysis.get("ou_line", "?")
    layers = analysis.get("layers_aligned", 0)

    title = f"{grade_label} {signal}"
    detail = f"crew avg {crew_avg} | line {ou_line} | {layers}/6 layers aligned"
    matchup = f"{game.get('away_team', '?')} @ {game.get('home_team', '?')}"
    time_net = f"{game.get('game_time', '')}"
    if game.get("network"):
        time_net += f" | {game['network']}"

    # Discord
    discord_url = os.environ.get("DISCORD_WEBHOOK", "")
    if discord_url:
        color = 0x22c55e  # dark green for FULL UNIT OVER
        if signal == "UNDER":
            color = 0xfbbf24  # yellow for under
        elif "HALF" in grade_label:
            color = 0x34d399  # green for HALF UNIT

        embed = {
            "title": f"{title} — {detail}",
            "description": f"{matchup}\n{time_net}",
            "color": color,
        }
        try:
            requests.post(discord_url, json={"embeds": [embed]}, timeout=10)
        except Exception as e:
            log.warning(f"Discord analysis notification failed: {e}")

    # Slack
    if SLACK_WEBHOOK:
        text = f":basketball: *{title}* — {detail}\n{matchup} | {time_net}"
        try:
            requests.post(SLACK_WEBHOOK, json={"text": text}, timeout=10)
        except Exception as e:
            log.warning(f"Slack analysis notification failed: {e}")


_refresh_lock = threading.Lock()


def notify_slack(result: dict):
    """Send a Slack message summarizing the refresh results."""
    if not SLACK_WEBHOOK:
        return

    games = result.get("games", 0)
    assigned = result.get("assigned", 0)
    pending = games - assigned

    text = (
        f":basketball: *Venetian Refresh Complete*\n"
        f"• *{games}* games today\n"
        f"• *{assigned}* with refs assigned\n"
        f"• *{pending}* pending"
    )

    try:
        r = requests.post(SLACK_WEBHOOK, json={"text": text}, timeout=10)
        if r.status_code == 200:
            log.info("Slack notification sent")
        else:
            log.warning(f"Slack webhook returned {r.status_code}")
    except Exception as e:
        log.warning(f"Slack notification failed: {e}")


@app.route("/api/refresh", methods=["POST"])
def refresh():
    if not _refresh_lock.acquire(blocking=False):
        return jsonify({"status": "already_running"}), 409

    try:
        from refmetrics_scraper import run_refresh
        result = run_refresh(DB_PATH)
        notify_slack(result)
        return jsonify({"status": "ok", **result})
    except Exception as e:
        return jsonify({"status": "error", "message": str(e)}), 500
    finally:
        _refresh_lock.release()


if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port)
