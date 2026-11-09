"""
Game history: O/U line movement, live score snapshots and final box scores.

Three append-only tables, written by the scraper every cycle:
  line_snapshots   one row each time a source's line or price for a game changes
  score_snapshots  one row each time a live game's score / period / clock changes
  box_scores       one row per finished game: fouls, free throws, shooting, etc.

All timestamps are UTC ISO strings ("2026-11-09T01:30:00Z") so they compare
correctly as text. Rows are only added when something changed, and every
lookup is indexed, so the tables stay small and fast (unlike referee_stats).
"""
import json
import logging
import sqlite3
from datetime import datetime, timedelta, timezone

import requests

log = logging.getLogger(__name__)

# Preferred sportsbooks for a single "closing line", in order.
PREFERRED_BOOKS = ["draftkings", "fanduel", "betmgm", "caesars", "betrivers"]
BOX_SCORE_LOOKBACK_DAYS = 3
BOX_SCORE_MAX_FETCH = 40   # ESPN summary calls per cycle, at most
BOX_SCORE_TIME_BUDGET = 90 # seconds per cycle, so a slow ESPN can't stall the scraper


def _utc_now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _to_utc_iso(value: str) -> str | None:
    try:
        dt = datetime.fromisoformat(value.replace("Z", "+00:00").replace(" ", "T"))
    except (AttributeError, ValueError):
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


# ── SCHEMA ────────────────────────────────────────────────────────────────────
def ensure_tables(conn: sqlite3.Connection):
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS line_snapshots (
            id            INTEGER PRIMARY KEY AUTOINCREMENT,
            game_key      TEXT NOT NULL,
            source        TEXT NOT NULL,          -- 'odds_api' or 'polymarket'
            book          TEXT NOT NULL DEFAULT '',-- sportsbook key; '' for polymarket
            captured_at   TEXT NOT NULL,          -- UTC
            commence_time TEXT,                   -- tip-off, UTC
            line          REAL,
            over_price    REAL,
            under_price   REAL,
            price_type    TEXT,                   -- 'american' or 'probability'
            volume        REAL
        );
        CREATE INDEX IF NOT EXISTS idx_line_snap_game
            ON line_snapshots (game_key, source, book, captured_at);

        CREATE TABLE IF NOT EXISTS score_snapshots (
            id            INTEGER PRIMARY KEY AUTOINCREMENT,
            game_key      TEXT NOT NULL,
            captured_at   TEXT NOT NULL,          -- UTC
            state         TEXT,                   -- 'live' or 'final'
            period        TEXT,
            clock         TEXT,
            home_score    INTEGER,
            away_score    INTEGER,
            ncaa_game_id  TEXT
        );
        CREATE INDEX IF NOT EXISTS idx_score_snap_game
            ON score_snapshots (game_key, captured_at);

        CREATE TABLE IF NOT EXISTS box_scores (
            game_key        TEXT PRIMARY KEY,
            espn_game_id    TEXT,
            captured_at     TEXT,
            home_team       TEXT,
            away_team       TEXT,
            home_score      INTEGER,
            away_score      INTEGER,
            home_fouls      INTEGER,
            away_fouls      INTEGER,
            home_techs      INTEGER,
            away_techs      INTEGER,
            home_flagrants  INTEGER,
            away_flagrants  INTEGER,
            home_ftm        INTEGER,
            home_fta        INTEGER,
            away_ftm        INTEGER,
            away_fta        INTEGER,
            stats_json      TEXT                  -- every ESPN team stat, both sides
        );
    """)
    conn.commit()


# ── MATCHING ──────────────────────────────────────────────────────────────────
def _games_on(conn, dates) -> dict[str, list]:
    dates = sorted(set(dates))
    if not dates:
        return {}
    rows = conn.execute(
        f"SELECT game_key, game_date, home_team, away_team FROM game_assignments "
        f"WHERE game_date IN ({','.join('?' * len(dates))})", dates
    ).fetchall()
    out: dict[str, list] = {}
    for r in rows:
        out.setdefault(r["game_date"], []).append(r)
    return out


def _find_game(candidates, team_a: str, team_b: str):
    """DB game whose two (short) team names both match the source's full names."""
    from refmetrics_scraper import _team_name_matches as tm
    for g in candidates:
        h, a = g["home_team"] or "", g["away_team"] or ""
        if (tm(h, team_a) and tm(a, team_b)) or (tm(h, team_b) and tm(a, team_a)):
            return g
    return None


# ── LINES ─────────────────────────────────────────────────────────────────────
def _insert_line_if_changed(conn, row: dict) -> bool:
    last = conn.execute("""
        SELECT line, over_price, under_price FROM line_snapshots
        WHERE game_key = ? AND source = ? AND book = ?
        ORDER BY captured_at DESC LIMIT 1
    """, (row["game_key"], row["source"], row["book"])).fetchone()
    if last and (last["line"], last["over_price"], last["under_price"]) == (
            row["line"], row["over_price"], row["under_price"]):
        return False
    conn.execute("""
        INSERT INTO line_snapshots (game_key, source, book, captured_at, commence_time,
                                    line, over_price, under_price, price_type, volume)
        VALUES (:game_key, :source, :book, :captured_at, :commence_time,
                :line, :over_price, :under_price, :price_type, :volume)
    """, row)
    return True


def record_line_snapshots(conn: sqlite3.Connection, odds_events: list[dict],
                          poly_events: list[dict]) -> int:
    """Store every Odds API (per sportsbook) and Polymarket line for games we
    track, adding a row only when the line or price changed."""
    from refmetrics_scraper import DISPLAY_TZ
    now = _utc_now()

    odds_rows = []
    for ev in odds_events or []:
        commence = _to_utc_iso(ev.get("commence_time", ""))
        if not commence:
            continue
        date = datetime.fromisoformat(commence.replace("Z", "+00:00")) \
            .astimezone(DISPLAY_TZ).strftime("%Y-%m-%d")
        for bk in ev.get("bookmakers", []):
            for mkt in bk.get("markets", []):
                if mkt.get("key") != "totals":
                    continue
                outs = {o.get("name"): o for o in mkt.get("outcomes", [])}
                over, under = outs.get("Over"), outs.get("Under")
                if not over or over.get("point") is None:
                    continue
                odds_rows.append((date, ev.get("home_team", "").lower(),
                                  ev.get("away_team", "").lower(), {
                    "source": "odds_api", "book": bk.get("key", ""),
                    "commence_time": commence, "line": float(over["point"]),
                    "over_price": over.get("price"),
                    "under_price": under.get("price") if under else None,
                    "price_type": "american", "volume": None,
                }))

    poly_rows = [(e["game_date"], e["team_a"], e["team_b"], {
        "source": "polymarket", "book": "", "commence_time": e.get("start"),
        # Rounded to the cent so tiny price ticks don't each add a row
        "line": e["line"], "over_price": round(e["over_price"], 2),
        "under_price": round(1 - e["over_price"], 2), "price_type": "probability",
        "volume": e.get("volume"),
    }) for e in poly_events or []]

    all_rows = odds_rows + poly_rows
    games_by_date = _games_on(conn, [r[0] for r in all_rows])
    added = 0
    for date, team_a, team_b, data in all_rows:
        game = _find_game(games_by_date.get(date, []), team_a, team_b)
        if not game:
            continue
        added += _insert_line_if_changed(conn, {
            **data, "game_key": game["game_key"], "captured_at": now})
    conn.commit()
    if added:
        log.info(f"Line history: {added} new line snapshots")
    return added


# ── LIVE SCORES ───────────────────────────────────────────────────────────────
def record_score_snapshots(conn: sqlite3.Connection, game_date: str,
                           ncaa_games: list[dict]) -> int:
    """Store the score of each tracked game that is live or final, adding a
    row only when score, period, clock or state changed."""
    from refmetrics_scraper import _match_ncaa_game
    now = _utc_now()
    added = 0
    for g in _games_on(conn, [game_date]).get(game_date, []):
        m = _match_ncaa_game(g["home_team"] or "", g["away_team"] or "", ncaa_games)
        if not m or m.get("game_state") not in ("live", "final"):
            continue
        snap = (m.get("game_state"), m.get("period"), m.get("clock"),
                m.get("home_score"), m.get("away_score"))
        last = conn.execute("""
            SELECT state, period, clock, home_score, away_score FROM score_snapshots
            WHERE game_key = ? ORDER BY captured_at DESC LIMIT 1
        """, (g["game_key"],)).fetchone()
        if last and tuple(last) == snap:
            continue
        conn.execute("""
            INSERT INTO score_snapshots (game_key, captured_at, state, period, clock,
                                         home_score, away_score, ncaa_game_id)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """, (g["game_key"], now, *snap, m.get("ncaa_game_id")))
        added += 1
    conn.commit()
    return added


def update_score_history(conn: sqlite3.Connection) -> int:
    """Snapshot today's games, plus yesterday's for games still going past midnight."""
    from refmetrics_scraper import DISPLAY_TZ, fetch_ncaa_scoreboard
    today = datetime.now(DISPLAY_TZ)
    added = 0
    for d in (today - timedelta(days=1), today):
        date_str = d.strftime("%Y-%m-%d")
        games = fetch_ncaa_scoreboard(d.strftime("%Y/%m/%d"))
        if games:
            added += record_score_snapshots(conn, date_str, games)
    if added:
        log.info(f"Score history: {added} new score snapshots")
    return added


# ── BOX SCORES ────────────────────────────────────────────────────────────────
def _int(v):
    try:
        return int(str(v).strip())
    except (TypeError, ValueError):
        return None


def parse_espn_box_score(summary: dict) -> dict | None:
    """Pull final score and team stats out of an ESPN game summary.
    Returns None unless the game is final."""
    comps = summary.get("header", {}).get("competitions") or [{}]
    comp = comps[0]
    if comp.get("status", {}).get("type", {}).get("name") != "STATUS_FINAL":
        return None
    out = {}
    for c in comp.get("competitors", []):
        side = c.get("homeAway")
        if side in ("home", "away"):
            out[f"{side}_team"] = c.get("team", {}).get("displayName", "")
            out[f"{side}_score"] = _int(c.get("score"))
    stats = {}
    for t in summary.get("boxscore", {}).get("teams", []):
        side = t.get("homeAway")
        if side not in ("home", "away"):
            continue
        s = {x.get("name"): x.get("displayValue") for x in t.get("statistics", [])}
        stats[side] = s
        out[f"{side}_fouls"] = _int(s.get("fouls"))
        out[f"{side}_techs"] = _int(s.get("totalTechnicalFouls", s.get("technicalFouls")))
        out[f"{side}_flagrants"] = _int(s.get("flagrantFouls"))
        ft = (s.get("freeThrowsMade-freeThrowsAttempted") or "").split("-")
        out[f"{side}_ftm"] = _int(ft[0]) if len(ft) == 2 else None
        out[f"{side}_fta"] = _int(ft[1]) if len(ft) == 2 else None
    if not stats:
        return None
    out["stats_json"] = json.dumps(stats)
    return out


def _espn_ids_for_date(date_str: str) -> list[dict]:
    """ESPN games on a date as [{id, home, away}] for looking up missing IDs."""
    from refmetrics_scraper import ESPN_SCOREBOARD
    try:
        r = requests.get(f"{ESPN_SCOREBOARD}?dates={date_str.replace('-', '')}"
                         f"&groups=50&limit=300", timeout=15)
        events = r.json().get("events", [])
    except Exception as e:
        log.warning(f"ESPN scoreboard fetch failed for {date_str}: {e}")
        return []
    out = []
    for ev in events:
        teams = {c.get("homeAway"): c.get("team", {}).get("displayName", "")
                 for c in (ev.get("competitions") or [{}])[0].get("competitors", [])}
        out.append({"id": ev.get("id"), "home": teams.get("home", ""),
                    "away": teams.get("away", "")})
    return out


def store_box_scores(conn: sqlite3.Connection,
                     max_fetch: int = BOX_SCORE_MAX_FETCH) -> int:
    """Fetch and store box scores for recently finished games that don't have one."""
    from refmetrics_scraper import DISPLAY_TZ, ESPN_SUMMARY, _same_matchup
    today = datetime.now(DISPLAY_TZ).strftime("%Y-%m-%d")
    since = (datetime.now(DISPLAY_TZ) - timedelta(days=BOX_SCORE_LOOKBACK_DAYS)).strftime("%Y-%m-%d")
    rows = conn.execute("""
        SELECT gm.game_key, gm.game_date, gm.home_team, gm.away_team, gm.espn_game_id
        FROM game_assignments gm
        LEFT JOIN box_scores bs ON bs.game_key = gm.game_key
        LEFT JOIN game_analysis ga ON ga.game_key = gm.game_key
        WHERE bs.game_key IS NULL
          AND gm.game_date BETWEEN ? AND ?
          AND (gm.game_date < ?
               OR ga.actual_total IS NOT NULL
               OR EXISTS (SELECT 1 FROM score_snapshots ss
                          WHERE ss.game_key = gm.game_key AND ss.state = 'final'))
        ORDER BY gm.game_date DESC
    """, (since, today, today)).fetchall()

    import time
    deadline = time.monotonic() + BOX_SCORE_TIME_BUDGET
    espn_by_date: dict[str, list] = {}
    stored = fetched = 0
    for g in rows:
        if fetched >= max_fetch or time.monotonic() > deadline:
            break
        espn_id = (g["espn_game_id"] or "").strip()
        if not espn_id:
            if g["game_date"] not in espn_by_date:
                espn_by_date[g["game_date"]] = _espn_ids_for_date(g["game_date"])
            hit = next((e for e in espn_by_date[g["game_date"]]
                        if _same_matchup(g["home_team"] or "", g["away_team"] or "",
                                         e["home"], e["away"])), None)
            if not hit:
                continue
            espn_id = hit["id"]
        fetched += 1
        try:
            summary = requests.get(ESPN_SUMMARY.format(game_id=espn_id), timeout=8).json()
        except Exception as e:
            log.warning(f"ESPN summary fetch failed for {espn_id}: {e}")
            continue
        box = parse_espn_box_score(summary)
        if not box:
            continue
        cols = ["game_key", "espn_game_id", "captured_at", *box.keys()]
        conn.execute(
            f"INSERT OR REPLACE INTO box_scores ({','.join(cols)}) "
            f"VALUES ({','.join('?' * len(cols))})",
            (g["game_key"], espn_id, _utc_now(), *box.values()))
        stored += 1
    conn.commit()
    if stored:
        log.info(f"Box scores: stored {stored} games")
    return stored


# ── READING ───────────────────────────────────────────────────────────────────
def _pregame_lines(conn, game_key: str) -> list:
    """Line snapshots taken before tip-off, oldest first."""
    return conn.execute("""
        SELECT * FROM line_snapshots
        WHERE game_key = ? AND (commence_time IS NULL OR captured_at < commence_time)
        ORDER BY captured_at
    """, (game_key,)).fetchall()


def _closing(snaps, source: str):
    """Last pregame line from a source, preferring the main sportsbooks."""
    picks = [s for s in snaps if s["source"] == source]
    if not picks:
        return None
    if source == "odds_api":
        for book in PREFERRED_BOOKS:
            booked = [s for s in picks if s["book"] == book]
            if booked:
                return booked[-1]
    return picks[-1]


def game_history(conn: sqlite3.Connection, game_key: str) -> dict | None:
    game = conn.execute("SELECT * FROM game_assignments WHERE game_key = ?",
                        (game_key,)).fetchone()
    if not game:
        return None
    lines = [dict(r) for r in conn.execute(
        "SELECT * FROM line_snapshots WHERE game_key = ? ORDER BY captured_at",
        (game_key,))]
    scores = [dict(r) for r in conn.execute(
        "SELECT * FROM score_snapshots WHERE game_key = ? ORDER BY captured_at",
        (game_key,))]
    box = conn.execute("SELECT * FROM box_scores WHERE game_key = ?", (game_key,)).fetchone()
    box = dict(box) if box else None
    if box and box.get("stats_json"):
        box["stats"] = json.loads(box.pop("stats_json"))
    pre = _pregame_lines(conn, game_key)
    summary = {
        "opening_line": dict(pre[0]) if pre else None,
        "closing_line_odds_api": dict(c) if (c := _closing(pre, "odds_api")) else None,
        "closing_line_polymarket": dict(c) if (c := _closing(pre, "polymarket")) else None,
    }
    return {"game": dict(game), "summary": summary, "lines": lines,
            "scores": scores, "box_score": box}


EXPORT_COLUMNS = [
    "game_date", "game_time", "away_team", "home_team", "crew_chief", "u1", "u2",
    "crew_avg", "signal", "grade", "stored_line",
    "open_line", "open_source", "close_line_odds_api", "close_book",
    "close_line_polymarket", "away_score", "home_score", "total", "result_vs_close",
    "away_fouls", "home_fouls", "total_fouls", "away_fta", "home_fta", "total_fta",
    "total_techs", "line_snapshots", "game_key",
]


def export_rows(conn: sqlite3.Connection, start: str, end: str) -> list[dict]:
    """One row per game between two dates (inclusive) with lines, score and fouls."""
    games = conn.execute("""
        SELECT gm.*, ga.l1_crew_avg, ga.l1_signal, ga.signal_direction, ga.grade,
               ga.ou_line, ga.actual_total, ga.home_score AS ga_home, ga.away_score AS ga_away
        FROM game_assignments gm
        LEFT JOIN game_analysis ga ON ga.game_key = gm.game_key
        WHERE gm.game_date BETWEEN ? AND ?
        ORDER BY gm.game_date, gm.game_time
    """, (start, end)).fetchall()
    out = []
    for g in games:
        gk = g["game_key"]
        pre = _pregame_lines(conn, gk)
        close_o, close_p = _closing(pre, "odds_api"), _closing(pre, "polymarket")
        box = conn.execute("SELECT * FROM box_scores WHERE game_key = ?", (gk,)).fetchone()
        n_snaps = conn.execute("SELECT COUNT(*) FROM line_snapshots WHERE game_key = ?",
                               (gk,)).fetchone()[0]
        away = box["away_score"] if box else g["ga_away"]
        home = box["home_score"] if box else g["ga_home"]
        total = (away + home) if away is not None and home is not None else g["actual_total"]
        close = (close_o or close_p)
        result = None
        if total is not None and close:
            result = "OVER" if total > close["line"] else "UNDER" if total < close["line"] else "PUSH"

        def both(a, b):
            return (a + b) if a is not None and b is not None else None
        out.append({
            "game_date": g["game_date"], "game_time": g["game_time"],
            "away_team": g["away_team"], "home_team": g["home_team"],
            "crew_chief": g["crew_chief"], "u1": g["u1"], "u2": g["u2"],
            "crew_avg": g["l1_crew_avg"], "signal": g["signal_direction"] or g["l1_signal"],
            "grade": g["grade"], "stored_line": g["ou_line"],
            "open_line": pre[0]["line"] if pre else None,
            "open_source": pre[0]["source"] if pre else None,
            "close_line_odds_api": close_o["line"] if close_o else None,
            "close_book": close_o["book"] if close_o else None,
            "close_line_polymarket": close_p["line"] if close_p else None,
            "away_score": away, "home_score": home, "total": total,
            "result_vs_close": result,
            "away_fouls": box["away_fouls"] if box else None,
            "home_fouls": box["home_fouls"] if box else None,
            "total_fouls": both(box["away_fouls"], box["home_fouls"]) if box else None,
            "away_fta": box["away_fta"] if box else None,
            "home_fta": box["home_fta"] if box else None,
            "total_fta": both(box["away_fta"], box["home_fta"]) if box else None,
            "total_techs": both(box["away_techs"], box["home_techs"]) if box else None,
            "line_snapshots": n_snaps, "game_key": gk,
        })
    return out
