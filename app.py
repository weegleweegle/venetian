"""
Venetian — Minimal web frontend for RefMetrics scraper data.
Reads from the same SQLite database the scraper writes to.
"""

import json
import os
import sqlite3
from datetime import datetime

from flask import Flask, g, render_template, jsonify

app = Flask(__name__)
DB_PATH = os.environ.get("DB_PATH", "/app/data/refmetrics.db")


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


if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port)
