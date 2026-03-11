"""
Venetian — Minimal web frontend for RefMetrics scraper data.
Reads from the same SQLite database the scraper writes to.
"""

import json
import logging
import os
import sqlite3
from datetime import datetime

import requests
import threading

from flask import Flask, g, render_template, jsonify, request

app = Flask(__name__)
DB_PATH = os.environ.get("DB_PATH", "/app/data/refmetrics.db")
SLACK_WEBHOOK = os.environ.get("SLACK_WEBHOOK", "")

log = logging.getLogger(__name__)


def get_db():
    if "db" not in g:
        g.db = sqlite3.connect(DB_PATH)
        g.db.row_factory = sqlite3.Row
    return g.db


@app.teardown_appcontext
def close_db(exc):
    db = g.pop("db", None)
    if db is not None:
        db.close()


@app.route("/")
def index():
    db = get_db()
    today = datetime.now().strftime("%Y-%m-%d")

    games = db.execute("""
        SELECT * FROM game_assignments
        WHERE game_date = ?
        ORDER BY game_time
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

    return render_template("index.html",
        games=games,
        ref_stats=ref_stats,
        today=today,
        total_games=total_games,
        assigned=assigned,
        last_scrape=last_scrape,
    )


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
