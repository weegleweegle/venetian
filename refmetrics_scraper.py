"""
Venetian Scraper — ESPN Schedule + RefMetrics Refs
===================================================
1. Pulls today's game schedule from ESPN's public scoreboard API
2. Gets referee assignments from ESPN game summaries
3. Optionally enriches with RefMetrics referee data (if credentials set)
4. Schedules a scrape 10 minutes before each tip-off
5. At trigger time: re-checks for officials, saves to SQLite

Requirements:
    pip install requests beautifulsoup4 apscheduler

Usage:
    1. Optionally set REFMETRICS_USERNAME and REFMETRICS_PASSWORD env vars.
    2. Run: python refmetrics_scraper.py
    3. Keep it running — jobs fire automatically before each tip.
"""

import json
import logging
import os
import sqlite3
import time
from datetime import datetime, timedelta, timezone

import requests
from bs4 import BeautifulSoup
from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.date import DateTrigger

# ── CONFIG ─────────────────────────────────────────────────────────────────────
USERNAME       = os.environ.get("REFMETRICS_USERNAME", "")
PASSWORD       = os.environ.get("REFMETRICS_PASSWORD", "")

REFMETRICS_URL = "https://www.refmetrics.com"
LOGIN_URL      = f"{REFMETRICS_URL}/login"
HOME_URL       = REFMETRICS_URL + "/"

MINUTES_BEFORE = int(os.environ.get("MINUTES_BEFORE", "10"))
DB_PATH        = os.environ.get("DB_PATH", "/app/data/refmetrics.db")
DISCORD_WEBHOOK = os.environ.get("DISCORD_WEBHOOK", "")

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


# ── DISCORD NOTIFICATIONS ─────────────────────────────────────────────────────
def notify_discord(game: dict):
    """Send a Discord webhook when refs are assigned to a game."""
    if not DISCORD_WEBHOOK:
        return

    crew = f"**CC** {game.get('crew_chief', '?')}"
    if game.get("u1"):
        crew += f"  |  **U1** {game['u1']}"
    if game.get("u2"):
        crew += f"  |  **U2** {game['u2']}"

    embed = {
        "title": f"{game['away']} @ {game['home']}",
        "description": (
            f"**{game.get('game_time_str', '')}**"
            f"{' on ' + game['network'] if game.get('network') else ''}\n\n"
            f"{crew}\n\n"
            f"Source: {game.get('assignment_src', 'unknown').upper()}"
        ),
        "color": 0x34d399 if game.get("assignment_src") == "refmetrics" else 0xfbbf24,
    }

    if game.get("location"):
        embed["footer"] = {"text": game["location"]}

    payload = {"embeds": [embed]}

    try:
        r = requests.post(DISCORD_WEBHOOK, json=payload, timeout=10)
        if r.status_code in (200, 204):
            log.info(f"Discord notification sent: {game['away']} @ {game['home']}")
        else:
            log.warning(f"Discord webhook returned {r.status_code}")
    except Exception as e:
        log.warning(f"Discord notification failed: {e}")


# ── DATABASE ───────────────────────────────────────────────────────────────────
def init_db(db_path: str) -> sqlite3.Connection:
    os.makedirs(os.path.dirname(db_path) or ".", exist_ok=True)
    conn = sqlite3.connect(db_path, check_same_thread=False)

    # Migrate: drop old table if schema is outdated (missing columns)
    try:
        cursor = conn.execute("PRAGMA table_info(game_assignments)")
        cols = {row[1] for row in cursor.fetchall()}
        if cols and "home_logo" not in cols:
            log.info("DB schema outdated — recreating tables.")
            conn.execute("DROP TABLE IF EXISTS game_assignments")
            conn.execute("DROP TABLE IF EXISTS referee_stats")
            conn.commit()
    except Exception:
        pass

    conn.execute("""
        CREATE TABLE IF NOT EXISTS game_assignments (
            id              INTEGER PRIMARY KEY AUTOINCREMENT,
            scraped_at      TEXT,
            league          TEXT,
            game_date       TEXT,
            game_time       TEXT,
            home_team       TEXT,
            away_team       TEXT,
            home_logo       TEXT,
            away_logo       TEXT,
            crew_chief      TEXT,
            u1              TEXT,
            u2              TEXT,
            network         TEXT,
            location        TEXT,
            espn_game_id    TEXT UNIQUE,
            assignment_src  TEXT   -- 'espn' or 'refmetrics' or 'none'
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


# ── SESSION ───────────────────────────────────────────────────────────────────
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


# ── REFMETRICS LOGIN (optional) ──────────────────────────────────────────────
def login_refmetrics(session: requests.Session) -> bool:
    if not USERNAME or not PASSWORD:
        log.info("No RefMetrics credentials — skipping RefMetrics login.")
        return False

    try:
        r    = session.get(LOGIN_URL, timeout=15)
        soup = BeautifulSoup(r.text, "html.parser")

        payload = {}
        form    = soup.find("form")
        if form:
            for inp in form.find_all("input"):
                name = inp.get("name")
                if not name:
                    continue
                if inp.get("type") == "hidden":
                    payload[name] = inp.get("value", "")
                elif inp.get("type") == "password":
                    payload[name] = PASSWORD
                elif inp.get("type") in ("text", "email") or name.lower() in ("email", "username", "login"):
                    payload[name] = USERNAME

        if not any(v == USERNAME for v in payload.values()):
            payload["username_or_email"] = USERNAME
        if not any(v == PASSWORD for v in payload.values()):
            payload["password"] = PASSWORD

        resp = session.post(LOGIN_URL, data=payload, timeout=15, allow_redirects=True)

        if "/login" in resp.url and resp.status_code == 200:
            log.warning("RefMetrics login failed — will use ESPN only for officials.")
            return False

        log.info("RefMetrics login successful.")
        return True

    except requests.RequestException as e:
        log.warning(f"RefMetrics login error: {e}")
        return False


# ── ESPN SCHEDULE (primary source) ───────────────────────────────────────────
def fetch_espn_schedule() -> list[dict]:
    """Pull today's NCAAB schedule from ESPN's public scoreboard API."""
    today_str = datetime.now().strftime("%Y%m%d")
    url = f"{ESPN_SCOREBOARD}?dates={today_str}"

    try:
        r    = requests.get(url, timeout=15)
        data = r.json()
    except Exception as e:
        log.error(f"ESPN scoreboard fetch failed: {e}")
        return []

    games = []
    for event in data.get("events", []):
        game_id = event.get("id")
        comp    = event.get("competitions", [{}])[0]

        teams = {}
        logos = {}
        for c in comp.get("competitors", []):
            side = c["homeAway"]
            teams[side] = c["team"]["displayName"]
            logo = c["team"].get("logo", "")
            if not logo:
                # Try logos array
                team_logos = c["team"].get("logos", [])
                if team_logos:
                    logo = team_logos[0].get("href", "")
            logos[side] = logo

        # Parse game time from ISO date string
        date_str = comp.get("date", event.get("date", ""))
        tipoff = None
        time_str = ""
        if date_str:
            try:
                dt = datetime.fromisoformat(date_str.replace("Z", "+00:00"))
                dt_local = dt.astimezone()
                tipoff = dt_local.replace(tzinfo=None)
                time_str = dt_local.strftime("%-I:%M %p")
            except (ValueError, OSError):
                try:
                    dt = datetime.fromisoformat(date_str.replace("Z", "+00:00"))
                    dt_local = dt.astimezone()
                    tipoff = dt_local.replace(tzinfo=None)
                    time_str = dt_local.strftime("%I:%M %p").lstrip("0")
                except Exception:
                    pass

        # TV network
        network = ""
        for broadcast in comp.get("broadcasts", []):
            names = broadcast.get("names", [])
            if names:
                network = names[0]
                break

        # Venue
        venue = comp.get("venue", {})
        location = venue.get("fullName", "")
        city = venue.get("address", {}).get("city", "")
        state = venue.get("address", {}).get("state", "")
        if city and state:
            location = f"{location}, {city}, {state}" if location else f"{city}, {state}"

        games.append({
            "espn_game_id":   game_id,
            "game_time_str":  time_str,
            "tipoff":         tipoff,
            "home":           teams.get("home", ""),
            "away":           teams.get("away", ""),
            "home_logo":      logos.get("home", ""),
            "away_logo":      logos.get("away", ""),
            "network":        network,
            "location":       location,
            "crew_chief":     "",
            "u1":             "",
            "u2":             "",
            "has_assignment": False,
            "assignment_src": None,
            "ref_links":      {},
        })

    log.info(f"ESPN schedule: {len(games)} games today.")
    return games


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


# ── ENRICH WITH OFFICIALS ────────────────────────────────────────────────────
def enrich_with_officials(games: list[dict], session: requests.Session,
                          has_refmetrics: bool) -> list[dict]:
    """Try to get referee assignments for each game."""
    # First pass: ESPN officials
    for game in games:
        if game["espn_game_id"]:
            officials = fetch_espn_officials(game["espn_game_id"])
            if officials and officials.get("crew_chief"):
                game.update({
                    "crew_chief":     officials["crew_chief"],
                    "u1":             officials.get("u1", ""),
                    "u2":             officials.get("u2", ""),
                    "has_assignment": True,
                    "assignment_src": "espn",
                })

    espn_assigned = sum(1 for g in games if g["has_assignment"])
    log.info(f"ESPN officials: {espn_assigned}/{len(games)} games have refs assigned.")

    # Second pass: RefMetrics for any still missing
    if has_refmetrics:
        missing = [g for g in games if not g["has_assignment"]]
        if missing:
            log.info(f"Checking RefMetrics for {len(missing)} unassigned game(s)...")
            enrich_from_refmetrics(missing, session)

    return games


def fuzzy_match(name_a: str, name_b: str) -> bool:
    """Loose match between team name formats."""
    a = name_a.lower().replace(".", "").replace("-", " ").strip()
    b = name_b.lower().replace(".", "").replace("-", " ").strip()
    if a in b or b in a:
        return True
    if a.split()[:2] == b.split()[:2]:
        return True
    return False


def enrich_from_refmetrics(games: list[dict], session: requests.Session):
    """Check RefMetrics for referee assignments for the given games."""
    try:
        r    = session.get(HOME_URL, timeout=15)
        soup = BeautifulSoup(r.text, "html.parser")
    except requests.RequestException as e:
        log.warning(f"Failed to fetch RefMetrics: {e}")
        return

    # Find NCAA Men's table
    target_table = None
    for h3 in soup.find_all("h3"):
        if "ncaa men" in h3.get_text().lower():
            for sibling in h3.find_next_siblings():
                if sibling.name == "table":
                    target_table = sibling
                    break
                if sibling.name == "h3":
                    break

    if target_table is None:
        log.info("No RefMetrics game table found.")
        return

    for row in target_table.find_all("tr")[1:]:
        cols = [td.get_text(strip=True) for td in row.find_all("td")]
        if len(cols) < 6:
            continue

        rm_home       = cols[1]
        rm_away       = cols[2]
        rm_crew_chief = cols[3]
        rm_u1         = cols[4]
        rm_u2         = cols[5]

        if not rm_crew_chief or rm_crew_chief.strip() in ("", "-"):
            continue

        # Match to our ESPN games
        for game in games:
            if game["has_assignment"]:
                continue
            if fuzzy_match(game["home"], rm_home) and fuzzy_match(game["away"], rm_away):
                ref_links = {}
                for a in row.find_all("a", href=True):
                    if "referee-profile" in a["href"]:
                        ref_links[a.get_text(strip=True)] = REFMETRICS_URL + a["href"]

                game.update({
                    "crew_chief":     rm_crew_chief,
                    "u1":             rm_u1,
                    "u2":             rm_u2,
                    "ref_links":      ref_links,
                    "has_assignment": True,
                    "assignment_src": "refmetrics",
                })
                log.info(f"  RefMetrics: {game['away']} @ {game['home']} -> {rm_crew_chief}")
                break


# ── SCRAPE JOB ─────────────────────────────────────────────────────────────────
def scrape_game(game: dict, session: requests.Session, conn: sqlite3.Connection,
                has_refmetrics: bool):
    """
    Fires 10 min before tip.
    Re-checks ESPN for officials, then RefMetrics if still missing.
    Saves assignment row + ref profile stats to DB.
    """
    log.info(f">> {game['away']} @ {game['home']}  ({game['game_time_str']})")

    # Re-check ESPN officials
    if not game["has_assignment"] and game["espn_game_id"]:
        officials = fetch_espn_officials(game["espn_game_id"])
        if officials and officials.get("crew_chief"):
            game.update({
                "crew_chief":     officials["crew_chief"],
                "u1":             officials.get("u1", ""),
                "u2":             officials.get("u2", ""),
                "has_assignment": True,
                "assignment_src": "espn",
            })

    # Re-check RefMetrics
    if not game["has_assignment"] and has_refmetrics:
        enrich_from_refmetrics([game], session)

    scraped_at = datetime.now().isoformat(timespec="seconds")
    today_str  = datetime.now().strftime("%Y-%m-%d")

    conn.execute("""
        INSERT INTO game_assignments
            (scraped_at, league, game_date, game_time, home_team, away_team,
             home_logo, away_logo,
             crew_chief, u1, u2, network, location, espn_game_id, assignment_src)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(espn_game_id) DO UPDATE SET
            scraped_at=excluded.scraped_at,
            crew_chief=excluded.crew_chief,
            u1=excluded.u1,
            u2=excluded.u2,
            assignment_src=excluded.assignment_src
    """, (
        scraped_at, "ncaa-men", today_str, game["game_time_str"],
        game["home"], game["away"],
        game.get("home_logo", ""), game.get("away_logo", ""),
        game.get("crew_chief", ""), game.get("u1", ""), game.get("u2", ""),
        game.get("network", ""), game.get("location", ""),
        game.get("espn_game_id", ""), game.get("assignment_src", "none"),
    ))
    conn.commit()

    # Scrape ref profile stats from RefMetrics if available
    if has_refmetrics:
        for name, url in game.get("ref_links", {}).items():
            scrape_ref_profile(name, url, session, conn, scraped_at)

    # Notify Discord when refs are assigned
    if game["has_assignment"]:
        notify_discord(game)

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
        scraped_at, "ncaa-men", name,
        stats.get("Games", stats.get("Total Games", "")),
        stats.get("Fouls/Game", stats.get("Avg Fouls", "")),
        stats.get("Technicals", stats.get("Tech Fouls", "")),
        stats.get("Flagrants", ""),
        json.dumps(stats, ensure_ascii=False),
    ))
    conn.commit()
    log.info(f"  -> {name}  fouls/g: {stats.get('Fouls/Game', 'n/a')}")


def save_games_to_db(games: list[dict], conn: sqlite3.Connection):
    """Save all games to DB immediately so the dashboard can display them."""
    scraped_at = datetime.now().isoformat(timespec="seconds")
    today_str  = datetime.now().strftime("%Y-%m-%d")

    for game in games:
        conn.execute("""
            INSERT INTO game_assignments
                (scraped_at, league, game_date, game_time, home_team, away_team,
                 home_logo, away_logo,
                 crew_chief, u1, u2, network, location, espn_game_id, assignment_src)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(espn_game_id) DO UPDATE SET
                scraped_at=excluded.scraped_at,
                crew_chief=excluded.crew_chief,
                u1=excluded.u1,
                u2=excluded.u2,
                assignment_src=excluded.assignment_src
        """, (
            scraped_at, "ncaa-men", today_str, game["game_time_str"],
            game["home"], game["away"],
            game.get("home_logo", ""), game.get("away_logo", ""),
            game.get("crew_chief", ""), game.get("u1", ""), game.get("u2", ""),
            game.get("network", ""), game.get("location", ""),
            game.get("espn_game_id", ""), game.get("assignment_src", "none"),
        ))
    conn.commit()
    log.info(f"Saved {len(games)} games to DB.")


# ── MAIN ───────────────────────────────────────────────────────────────────────
def run_refresh(db_path: str = None) -> dict:
    """Run a full scrape cycle and return summary stats.
    Can be called from the Flask app for manual refresh.
    """
    _db_path = db_path or DB_PATH
    conn = init_db(_db_path)
    session = create_session()
    has_refmetrics = login_refmetrics(session)

    games = fetch_espn_schedule()
    if not games:
        log.warning("No games on ESPN schedule today.")
        return {"games": 0, "assigned": 0}

    games = enrich_with_officials(games, session, has_refmetrics)
    save_games_to_db(games, conn)

    # Run immediate scrape for all games (re-check officials, save stats)
    for game in games:
        scrape_game(game, session, conn, has_refmetrics)

    assigned = sum(1 for g in games if g["has_assignment"])
    conn.close()
    log.info(f"Manual refresh complete: {len(games)} games, {assigned} assigned.")
    return {"games": len(games), "assigned": assigned}


def main():
    log.info("=" * 60)
    log.info("Venetian — NCAA MBB Referee Tracker")
    log.info(f"Schedule: ESPN  |  Trigger: T-{MINUTES_BEFORE} min")
    log.info("=" * 60)

    conn    = init_db(DB_PATH)
    session = create_session()

    # RefMetrics login is optional — ESPN is the primary source
    has_refmetrics = login_refmetrics(session)

    # Get today's schedule from ESPN
    games = fetch_espn_schedule()
    if not games:
        log.warning("No games on ESPN schedule today. Exiting.")
        return

    # Get referee assignments
    games = enrich_with_officials(games, session, has_refmetrics)

    # Save all games to DB immediately so the dashboard shows them
    save_games_to_db(games, conn)

    scheduler = BackgroundScheduler()
    scheduler.start()

    now       = datetime.now()
    scheduled = 0

    for game in games:
        if game["tipoff"] is None:
            log.info(f"  (no tipoff time) {game['away']} @ {game['home']}")
            scrape_game(game, session, conn, has_refmetrics)
            continue

        trigger_time = game["tipoff"] - timedelta(minutes=MINUTES_BEFORE)

        if trigger_time <= now:
            log.info(f"  (immediate) {game['away']} @ {game['home']}")
            scrape_game(game, session, conn, has_refmetrics)
        else:
            scheduler.add_job(
                scrape_game,
                trigger=DateTrigger(run_date=trigger_time),
                args=[game, session, conn, has_refmetrics],
                id=f"{game['home']}_{game['away']}".replace(" ", "_"),
                replace_existing=True,
            )
            src = "ESPN" if game["has_assignment"] and game["assignment_src"] == "espn" \
                  else "RM" if game["has_assignment"] else "TBD"
            log.info(
                f"  [{src:6}] {game['away']} @ {game['home']}"
                f"  -> fire at {trigger_time.strftime('%I:%M %p')}"
            )
            scheduled += 1

    assigned = sum(1 for g in games if g["has_assignment"])
    log.info(f"\n{len(games)} games, {assigned} with officials, {scheduled} scheduled.")
    log.info("Waiting for scheduled jobs...\n")

    try:
        while True:
            time.sleep(60)
    except KeyboardInterrupt:
        log.info("Shutting down.")
        scheduler.shutdown()
        conn.close()


if __name__ == "__main__":
    main()
