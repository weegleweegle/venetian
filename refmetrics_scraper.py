"""
RefMetrics Scraper — with ESPN Fallback
========================================
1. Logs into refmetrics.com and pulls today's game schedule + ref assignments
2. For any game missing assignments, falls back to ESPN's public API
3. Schedules a scrape 10 minutes before each tip-off
4. At trigger time: re-checks RefMetrics (NCAA posts late), then ESPN again
5. Saves everything to SQLite (refmetrics.db)

Requirements:
    pip install requests beautifulsoup4 apscheduler

Usage:
    1. Set REFMETRICS_USERNAME and REFMETRICS_PASSWORD env vars.
    2. Run: python refmetrics_scraper.py
    3. Keep it running — jobs fire automatically before each tip.
"""

import json
import logging
import os
import sqlite3
import time
from datetime import datetime, timedelta

import requests
from bs4 import BeautifulSoup
from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.date import DateTrigger

# ── CONFIG ─────────────────────────────────────────────────────────────────────
USERNAME       = os.environ.get("REFMETRICS_USERNAME", "")
PASSWORD       = os.environ.get("REFMETRICS_PASSWORD", "")

BASE_URL       = "https://www.refmetrics.com"
LOGIN_URL      = f"{BASE_URL}/login"
HOME_URL       = BASE_URL + "/"

LEAGUE         = os.environ.get("LEAGUE", "ncaa-men")  # ncaa-men | ncaa-women | nba | wnba | g-league
MINUTES_BEFORE = int(os.environ.get("MINUTES_BEFORE", "10"))
DB_PATH        = os.environ.get("DB_PATH", "/app/data/refmetrics.db")

# ESPN public API — no auth required
ESPN_SCOREBOARD = (
    "https://site.api.espn.com/apis/site/v2/sports/basketball/"
    "mens-college-basketball/scoreboard"
)
ESPN_SUMMARY = (
    "https://site.api.espn.com/apis/site/v2/sports/basketball/"
    "mens-college-basketball/summary?event={game_id}"
)
# ──────────────────────────────────────────────────────────────────────────────

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(levelname)-8s  %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
log = logging.getLogger(__name__)


# ── DATABASE ───────────────────────────────────────────────────────────────────
def init_db(db_path: str) -> sqlite3.Connection:
    os.makedirs(os.path.dirname(db_path) or ".", exist_ok=True)
    conn = sqlite3.connect(db_path, check_same_thread=False)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS game_assignments (
            id              INTEGER PRIMARY KEY AUTOINCREMENT,
            scraped_at      TEXT,
            league          TEXT,
            game_date       TEXT,
            game_time       TEXT,
            home_team       TEXT,
            away_team       TEXT,
            crew_chief      TEXT,
            u1              TEXT,
            u2              TEXT,
            network         TEXT,
            assignment_src  TEXT   -- 'refmetrics' or 'espn' or 'none'
        )
    """)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS referee_stats (
            id              INTEGER PRIMARY KEY AUTOINCREMENT,
            scraped_at      TEXT,
            league          TEXT,
            official_name   TEXT,
            games           TEXT,
            fouls_per_game  TEXT,
            techs           TEXT,
            flagrants       TEXT,
            raw_stats_json  TEXT
        )
    """)
    conn.commit()
    return conn


# ── SESSION / LOGIN ────────────────────────────────────────────────────────────
def create_session() -> requests.Session:
    s = requests.Session()
    s.headers.update({
        "User-Agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/122.0.0.0 Safari/537.36"
        )
    })
    return s


def login(session: requests.Session) -> bool:
    if not USERNAME or not PASSWORD:
        log.error("REFMETRICS_USERNAME and REFMETRICS_PASSWORD env vars must be set.")
        return False

    try:
        r    = session.get(LOGIN_URL, timeout=15)
        soup = BeautifulSoup(r.text, "html.parser")

        payload = {}
        form    = soup.find("form")
        if form:
            inputs = form.find_all("input")
            field_names = [inp.get("name", "") for inp in inputs if inp.get("name")]
            log.info(f"Login form fields: {field_names}")

            for inp in inputs:
                name = inp.get("name")
                if not name:
                    continue
                if inp.get("type") == "hidden":
                    payload[name] = inp.get("value", "")
                elif inp.get("type") == "password":
                    payload[name] = PASSWORD
                elif inp.get("type") in ("text", "email") or name.lower() in ("email", "username", "login"):
                    payload[name] = USERNAME

        # Fallback if form parsing didn't set credentials
        if not any(v == USERNAME for v in payload.values()):
            payload["email"] = USERNAME
            payload["username"] = USERNAME
        if not any(v == PASSWORD for v in payload.values()):
            payload["password"] = PASSWORD

        log.info(f"Logging in as: {USERNAME}")
        log.info(f"POST payload keys: {list(payload.keys())}")
        resp = session.post(LOGIN_URL, data=payload, timeout=15, allow_redirects=True)
        log.info(f"Login response: status={resp.status_code}, url={resp.url}")

        if "/login" in resp.url and resp.status_code == 200:
            log.error("Login failed — still on login page after POST.")
            return False

        log.info("Login successful.")
        return True

    except requests.RequestException as e:
        log.error(f"Login error: {e}")
        return False


# ── REFMETRICS SCHEDULE PARSER ─────────────────────────────────────────────────
def parse_refmetrics_games(session: requests.Session) -> list[dict]:
    """Pull today's games + ref assignments from RefMetrics home page."""
    try:
        r    = session.get(HOME_URL, timeout=15)
        soup = BeautifulSoup(r.text, "html.parser")
    except requests.RequestException as e:
        log.error(f"Failed to fetch RefMetrics schedule: {e}")
        return []

    today = datetime.now().date()

    league_label_map = {
        "ncaa-men":   "NCAA Men's",
        "ncaa-women": "NCAA Women's",
        "nba":        "NBA",
        "wnba":       "WNBA",
        "g-league":   "G League",
    }
    target_label = league_label_map.get(LEAGUE, "NCAA Men's")

    # Find the <table> under its <h3> heading
    target_table = None
    for h3 in soup.find_all("h3"):
        if target_label.lower() in h3.get_text().lower():
            for sibling in h3.find_next_siblings():
                if sibling.name == "table":
                    target_table = sibling
                    break
                if sibling.name == "h3":
                    break

    if target_table is None:
        log.warning(f"Could not find game table for: {LEAGUE}")
        return []

    games = []
    for row in target_table.find_all("tr")[1:]:
        cols = [td.get_text(strip=True) for td in row.find_all("td")]
        if len(cols) < 6:
            continue

        time_str   = cols[0]
        home       = cols[1]
        away       = cols[2]
        crew_chief = cols[3]
        u1         = cols[4]
        u2         = cols[5]
        network    = cols[6] if len(cols) > 6 else ""

        # Collect ref profile links for stat scraping
        ref_links = {}
        for a in row.find_all("a", href=True):
            if "referee-profile" in a["href"]:
                ref_links[a.get_text(strip=True)] = BASE_URL + a["href"]

        tipoff = parse_tipoff(time_str, today)
        if tipoff is None:
            continue

        has_assignment = bool(crew_chief and crew_chief.strip() not in ("", "-"))

        games.append({
            "game_time_str":  time_str,
            "tipoff":         tipoff,
            "home":           home,
            "away":           away,
            "crew_chief":     crew_chief,
            "u1":             u1,
            "u2":             u2,
            "network":        network,
            "ref_links":      ref_links,
            "has_assignment": has_assignment,
            "assignment_src": "refmetrics" if has_assignment else None,
            "espn_game_id":   None,
        })

    assigned = sum(1 for g in games if g["has_assignment"])
    log.info(f"RefMetrics: {len(games)} games, {assigned} with assignments.")
    return games


# ── ESPN FALLBACK ──────────────────────────────────────────────────────────────
def fetch_espn_scoreboard() -> list[dict]:
    """Pull today's NCAAB scoreboard from ESPN's public API."""
    try:
        r    = requests.get(ESPN_SCOREBOARD, timeout=15)
        data = r.json()
    except Exception as e:
        log.error(f"ESPN scoreboard fetch failed: {e}")
        return []

    espn_games = []
    for event in data.get("events", []):
        game_id = event.get("id")
        comps   = event.get("competitions", [{}])[0]
        teams   = {
            c["homeAway"]: c["team"]["displayName"]
            for c in comps.get("competitors", [])
        }
        espn_games.append({
            "game_id": game_id,
            "home":    teams.get("home", ""),
            "away":    teams.get("away", ""),
        })

    log.info(f"ESPN scoreboard: {len(espn_games)} games.")
    return espn_games


def fetch_espn_officials(game_id: str) -> dict:
    """Pull officials for a specific ESPN game ID."""
    try:
        url  = ESPN_SUMMARY.format(game_id=game_id)
        r    = requests.get(url, timeout=15)
        data = r.json()
    except Exception as e:
        log.warning(f"ESPN summary failed for game {game_id}: {e}")
        return {}

    officials = data.get("gameInfo", {}).get("officials", [])
    if not officials:
        return {}

    names = [o.get("displayName", "") for o in officials if o.get("displayName")]
    return {
        "crew_chief": names[0] if len(names) > 0 else "",
        "u1":         names[1] if len(names) > 1 else "",
        "u2":         names[2] if len(names) > 2 else "",
    }


def fuzzy_match(rm_name: str, espn_name: str) -> bool:
    """Loose match between RefMetrics and ESPN team name formats."""
    a = rm_name.lower().replace(".", "").replace("-", " ").strip()
    b = espn_name.lower().replace(".", "").replace("-", " ").strip()
    if a in b or b in a:
        return True
    # Match on first two words (handles 'Kansas State' vs 'Kansas St Wildcats')
    if a.split()[:2] == b.split()[:2]:
        return True
    return False


def enrich_with_espn(games: list[dict]) -> list[dict]:
    """For games missing assignments, try ESPN as a fallback."""
    missing = [g for g in games if not g["has_assignment"]]
    if not missing:
        log.info("All games have RefMetrics assignments — ESPN fallback skipped.")
        return games

    log.info(f"Checking ESPN for {len(missing)} unassigned game(s)...")
    espn_games = fetch_espn_scoreboard()

    for game in missing:
        matched = next(
            (eg for eg in espn_games
             if fuzzy_match(game["home"], eg["home"])
             and fuzzy_match(game["away"], eg["away"])),
            None
        )

        if not matched:
            log.info(f"  No ESPN match: {game['away']} @ {game['home']}")
            continue

        game["espn_game_id"] = matched["game_id"]
        officials = fetch_espn_officials(matched["game_id"])

        if officials and officials.get("crew_chief"):
            game.update({
                "crew_chief":     officials["crew_chief"],
                "u1":             officials.get("u1", ""),
                "u2":             officials.get("u2", ""),
                "has_assignment": True,
                "assignment_src": "espn",
            })
            log.info(
                f"  ESPN: {game['away']} @ {game['home']} -> "
                f"{officials['crew_chief']} / {officials.get('u1','')} / {officials.get('u2','')}"
            )
        else:
            log.info(f"  ESPN has no officials yet: {game['away']} @ {game['home']}")

    return games


# ── TIME PARSING ───────────────────────────────────────────────────────────────
def parse_tipoff(time_str: str, date) -> datetime | None:
    clean = (
        time_str.upper()
        .replace(" PT", "").replace(" ET", "")
        .replace(" CT", "").replace(" MT", "")
        .strip()
    )
    for fmt in ("%I:%M %p", "%I:%M%p"):
        try:
            t = datetime.strptime(clean, fmt)
            return datetime.combine(date, t.time())
        except ValueError:
            continue
    return None


# ── SCRAPE JOB ─────────────────────────────────────────────────────────────────
def scrape_game(game: dict, session: requests.Session, conn: sqlite3.Connection):
    """
    Fires 10 min before tip.
    Re-checks RefMetrics (assignments post late), then ESPN if still missing.
    Saves assignment row + ref profile stats to DB.
    """
    log.info(f">> {game['away']} @ {game['home']}  ({game['game_time_str']})")

    # Re-check RefMetrics — NCAA assignments often post within 30 min of tip
    fresh_games = parse_refmetrics_games(session)
    for fg in fresh_games:
        if fuzzy_match(game["home"], fg["home"]) and fuzzy_match(game["away"], fg["away"]):
            if fg["has_assignment"]:
                log.info("  RefMetrics assignment now posted — using fresh data.")
                game.update({
                    "crew_chief":     fg["crew_chief"],
                    "u1":             fg["u1"],
                    "u2":             fg["u2"],
                    "ref_links":      fg["ref_links"],
                    "has_assignment": True,
                    "assignment_src": "refmetrics",
                })
            break

    # Still missing — one more ESPN check
    if not game["has_assignment"]:
        log.info("  Still no assignment — trying ESPN...")
        espn_id = game.get("espn_game_id")
        if not espn_id:
            # Try to find game_id now
            for eg in fetch_espn_scoreboard():
                if fuzzy_match(game["home"], eg["home"]) and fuzzy_match(game["away"], eg["away"]):
                    espn_id = eg["game_id"]
                    game["espn_game_id"] = espn_id
                    break

        if espn_id:
            officials = fetch_espn_officials(espn_id)
            if officials and officials.get("crew_chief"):
                game.update({
                    "crew_chief":     officials["crew_chief"],
                    "u1":             officials.get("u1", ""),
                    "u2":             officials.get("u2", ""),
                    "has_assignment": True,
                    "assignment_src": "espn",
                })

    scraped_at = datetime.now().isoformat(timespec="seconds")
    today_str  = datetime.now().strftime("%Y-%m-%d")

    conn.execute("""
        INSERT INTO game_assignments
            (scraped_at, league, game_date, game_time, home_team, away_team,
             crew_chief, u1, u2, network, assignment_src)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
    """, (
        scraped_at, LEAGUE, today_str, game["game_time_str"],
        game["home"], game["away"],
        game.get("crew_chief", ""), game.get("u1", ""), game.get("u2", ""),
        game.get("network", ""), game.get("assignment_src", "none"),
    ))
    conn.commit()

    # Scrape ref profile stats from RefMetrics
    for name, url in game.get("ref_links", {}).items():
        scrape_ref_profile(name, url, session, conn, scraped_at)

    log.info(
        f"Saved [{game.get('assignment_src','none')}] "
        f"{game.get('crew_chief','?')} / {game.get('u1','?')} / {game.get('u2','?')}"
    )


def scrape_ref_profile(name: str, url: str, session: requests.Session,
                       conn: sqlite3.Connection, scraped_at: str):
    try:
        r    = session.get(url, timeout=15)
        soup = BeautifulSoup(r.text, "html.parser")
    except requests.RequestException as e:
        log.warning(f"Profile fetch failed for {name}: {e}")
        return

    stats = {}

    for block in soup.find_all(class_=lambda c: c and "stat" in c.lower()):
        label = block.find(class_=lambda c: c and "label" in c.lower())
        value = block.find(class_=lambda c: c and "value" in c.lower())
        if label and value:
            stats[label.get_text(strip=True)] = value.get_text(strip=True)

    for table in soup.find_all("table"):
        headers = [th.get_text(strip=True) for th in table.find_all("th")]
        for row in table.find_all("tr")[1:]:
            vals = [td.get_text(strip=True) for td in row.find_all("td")]
            for h, v in zip(headers, vals):
                if h:
                    stats[h] = v

    conn.execute("""
        INSERT INTO referee_stats
            (scraped_at, league, official_name, games, fouls_per_game,
             techs, flagrants, raw_stats_json)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?)
    """, (
        scraped_at, LEAGUE, name,
        stats.get("Games", stats.get("Total Games", "")),
        stats.get("Fouls/Game", stats.get("Avg Fouls", "")),
        stats.get("Technicals", stats.get("Tech Fouls", "")),
        stats.get("Flagrants", ""),
        json.dumps(stats, ensure_ascii=False),
    ))
    conn.commit()
    log.info(f"  -> {name}  fouls/g: {stats.get('Fouls/Game', 'n/a')}")


# ── MAIN ───────────────────────────────────────────────────────────────────────
def main():
    log.info("=" * 60)
    log.info("RefMetrics Scraper  (ESPN fallback enabled)")
    log.info(f"League: {LEAGUE}  |  Trigger: T-{MINUTES_BEFORE} min")
    log.info("=" * 60)

    conn    = init_db(DB_PATH)
    session = create_session()

    if not login(session):
        return

    games = parse_refmetrics_games(session)
    if not games:
        log.warning("No games found. Exiting.")
        return

    # Fill missing assignments from ESPN
    games = enrich_with_espn(games)

    scheduler = BackgroundScheduler()
    scheduler.start()

    now       = datetime.now()
    scheduled = 0

    for game in games:
        trigger_time = game["tipoff"] - timedelta(minutes=MINUTES_BEFORE)

        if trigger_time <= now:
            log.info(f"  (immediate) {game['away']} @ {game['home']}")
            scrape_game(game, session, conn)
        else:
            scheduler.add_job(
                scrape_game,
                trigger=DateTrigger(run_date=trigger_time),
                args=[game, session, conn],
                id=f"{game['home']}_{game['away']}".replace(" ", "_"),
                replace_existing=True,
            )
            src = "RM" if game["has_assignment"] and game["assignment_src"] == "refmetrics" \
                  else "ESPN" if game["has_assignment"] else "TBD"
            log.info(
                f"  [{src:6}] {game['away']} @ {game['home']}"
                f"  -> fire at {trigger_time.strftime('%I:%M %p')}"
            )
            scheduled += 1

    log.info(f"\n{scheduled} games scheduled. Press Ctrl+C to stop.\n")

    try:
        while True:
            time.sleep(60)
    except KeyboardInterrupt:
        log.info("Shutting down.")
        scheduler.shutdown()
        conn.close()


if __name__ == "__main__":
    main()
