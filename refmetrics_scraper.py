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
from zoneinfo import ZoneInfo

import requests
from bs4 import BeautifulSoup
from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.cron import CronTrigger

# ── CONFIG ─────────────────────────────────────────────────────────────────────
USERNAME       = os.environ.get("REFMETRICS_USERNAME", "")
PASSWORD       = os.environ.get("REFMETRICS_PASSWORD", "")

REFMETRICS_URL = "https://www.refmetrics.com"
LOGIN_URL      = f"{REFMETRICS_URL}/login"
HOME_URL       = REFMETRICS_URL + "/"
TODAYS_GAMES_URL = REFMETRICS_URL + "/ncaa-men/todays-games"

DB_PATH        = os.environ.get("DB_PATH", "/app/data/refmetrics.db")
DISCORD_WEBHOOK = os.environ.get("DISCORD_WEBHOOK", "")
DISPLAY_TZ      = ZoneInfo(os.environ.get("DISPLAY_TZ", "America/Chicago"))
REFMETRICS_TZ   = ZoneInfo("America/New_York")  # RefMetrics times are Eastern

# ESPN public API — no auth required
ESPN_SCOREBOARD = (
    "https://site.api.espn.com/apis/site/v2/sports/basketball/"
    "mens-college-basketball/scoreboard"
)
ESPN_SUMMARY = (
    "https://site.api.espn.com/apis/site/v2/sports/basketball/"
    "mens-college-basketball/summary?event={game_id}"
)

# The Odds API — NCAAB totals
ODDS_API_KEY = os.environ.get("ODDS_API_KEY", "")
ODDS_API_URL = (
    "https://api.the-odds-api.com/v4/sports/basketball_ncaab/odds"
    "?regions=us&markets=totals&oddsFormat=american&apiKey={api_key}"
)

# NCAA API — scoreboard with scores and game state
NCAA_API_SCOREBOARD = "https://ncaa-api.henrygd.me/scoreboard/basketball-men/d1"
# ──────────────────────────────────────────────────────────────────────────────

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(levelname)-8s  %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
log = logging.getLogger(__name__)


# ── DISCORD NOTIFICATIONS ─────────────────────────────────────────────────────
def was_notified(conn: sqlite3.Connection, game_key: str, notif_type: str) -> bool:
    """Check if a notification was already sent for this game+type."""
    row = conn.execute(
        "SELECT 1 FROM notifications_sent WHERE game_key = ? AND notif_type = ?",
        (game_key, notif_type)
    ).fetchone()
    return row is not None


def mark_notified(conn: sqlite3.Connection, game_key: str, notif_type: str):
    """Record that a notification was sent."""
    conn.execute(
        "INSERT OR IGNORE INTO notifications_sent (game_key, notif_type, sent_at) VALUES (?, ?, ?)",
        (game_key, notif_type, datetime.now(DISPLAY_TZ).isoformat(timespec="seconds"))
    )
    conn.commit()


def notify_discord(game: dict, crew_avg: float = None):
    """Send a Discord webhook for a green card game (crew avg >= 35.5)."""
    if not DISCORD_WEBHOOK:
        return

    crew = f"**CC** {game.get('crew_chief', '?')}"
    if game.get("u1"):
        crew += f"  |  **U1** {game['u1']}"
    if game.get("u2"):
        crew += f"  |  **U2** {game['u2']}"

    avg_str = f"**Crew Avg: {crew_avg:.1f}**\n\n" if crew_avg else ""

    embed = {
        "title": f"\U0001f7e2 {game['away']} @ {game['home']}",
        "description": (
            f"**{game.get('game_time_str', '')}**"
            f"{' on ' + game['network'] if game.get('network') else ''}\n\n"
            f"{avg_str}"
            f"{crew}"
        ),
        "color": 0x22c55e,
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
    conn.row_factory = sqlite3.Row

    # Migrate: drop old table if schema is outdated (missing columns)
    try:
        cursor = conn.execute("PRAGMA table_info(game_assignments)")
        cols = {row[1] for row in cursor.fetchall()}
        if cols and ("home_logo" not in cols or "game_key" not in cols):
            log.info("DB schema outdated — recreating tables.")
            conn.execute("DROP TABLE IF EXISTS game_assignments")
            conn.execute("DROP TABLE IF EXISTS referee_stats")
            conn.commit()
        elif cols and "discord_notified" not in cols:
            conn.execute("ALTER TABLE game_assignments ADD COLUMN discord_notified INTEGER DEFAULT 0")
            conn.commit()
    except Exception:
        pass

    # Migrate game_analysis: add result tracking columns
    try:
        cursor = conn.execute("PRAGMA table_info(game_analysis)")
        ga_cols = {row[1] for row in cursor.fetchall()}
        if ga_cols and "actual_total" not in ga_cols:
            conn.execute("ALTER TABLE game_analysis ADD COLUMN actual_total REAL")
            conn.execute("ALTER TABLE game_analysis ADD COLUMN home_score INTEGER")
            conn.execute("ALTER TABLE game_analysis ADD COLUMN away_score INTEGER")
            conn.execute("ALTER TABLE game_analysis ADD COLUMN result TEXT")  # HIT, MISS, PUSH, or NULL
            conn.commit()
            log.info("Added result tracking columns to game_analysis.")
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
            espn_game_id    TEXT,
            game_key        TEXT UNIQUE,  -- espn_game_id or 'away@home' slug
            assignment_src  TEXT,  -- 'espn' or 'refmetrics' or 'none'
            discord_notified INTEGER DEFAULT 0
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
    conn.execute("""
        CREATE TABLE IF NOT EXISTS referee_foul_leaders (
            id              INTEGER PRIMARY KEY AUTOINCREMENT,
            referee_name    TEXT NOT NULL UNIQUE,
            total_fouls     REAL,
            home_fouls      REAL,
            away_fouls      REAL,
            foul_diff       REAL,
            total_games     INTEGER,
            fouls_per_game  REAL,
            tier            TEXT,
            imported_at     TEXT
        )
    """)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS game_analysis (
            id                  INTEGER PRIMARY KEY AUTOINCREMENT,
            game_key            TEXT NOT NULL UNIQUE,
            ou_line             REAL,

            l1_crew_avg         REAL,
            l1_tier             TEXT,
            l1_strong_high_count INTEGER,
            l1_strong_low_count INTEGER,
            l1_signal           TEXT,
            l1_ref_details      TEXT,
            l1_aligned          INTEGER DEFAULT 0,

            l2_home_fouls_pg    REAL,
            l2_away_fouls_pg    REAL,
            l2_home_ftr         REAL,
            l2_away_ftr         REAL,
            l2_home_fta_pg      REAL,
            l2_away_fta_pg      REAL,
            l2_aligned          INTEGER DEFAULT 0,

            l3_combined_ppg     REAL,
            l3_pace_rating      TEXT,
            l3_ppg_vs_line      TEXT,
            l3_aligned          INTEGER DEFAULT 0,

            l4_has_18ppg_scorer     INTEGER DEFAULT 0,
            l4_has_foul_drawer      INTEGER DEFAULT 0,
            l4_has_3pt_volume       INTEGER DEFAULT 0,
            l4_notes                TEXT,
            l4_aligned              INTEGER DEFAULT 0,

            l5_pressing_defense     INTEGER DEFAULT 0,
            l5_attack_basket        INTEGER DEFAULT 0,
            l5_slow_methodical      INTEGER DEFAULT 0,
            l5_notes                TEXT,
            l5_aligned              INTEGER DEFAULT 0,

            l6_senior_day           INTEGER DEFAULT 0,
            l6_rivalry              INTEGER DEFAULT 0,
            l6_tournament_impl      INTEGER DEFAULT 0,
            l6_late_season          INTEGER DEFAULT 0,
            l6_power_ref_midmajor   INTEGER DEFAULT 0,
            l6_notes                TEXT,
            l6_aligned              INTEGER DEFAULT 0,

            layers_aligned      INTEGER DEFAULT 0,
            signal_direction    TEXT,
            grade               TEXT,
            stay_away_reason    TEXT,
            grade_finalized     INTEGER DEFAULT 0,
            finalized_at        TEXT,

            actual_total        REAL,
            home_score          INTEGER,
            away_score          INTEGER,
            result              TEXT,

            created_at          TEXT,
            updated_at          TEXT
        )
    """)
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

    # Import referee CSV if table is empty
    from analysis import import_csv
    count = conn.execute("SELECT COUNT(*) FROM referee_foul_leaders").fetchone()[0]
    if count == 0:
        import_csv(conn)

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
    today_str = datetime.now(DISPLAY_TZ).strftime("%Y%m%d")
    url = f"{ESPN_SCOREBOARD}?dates={today_str}&groups=50&limit=200"

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
                dt_local = dt.astimezone(DISPLAY_TZ)
                tipoff = dt_local
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


# ── REFMETRICS TODAY'S GAMES (primary schedule + refs) ───────────────────────
def fetch_refmetrics_schedule(session: requests.Session) -> list[dict]:
    """Scrape refmetrics.com/ncaa-men/todays-games for the full game schedule.
    This page has all games and is updated before ESPN for ref assignments."""
    try:
        r = session.get(TODAYS_GAMES_URL, timeout=15)
        soup = BeautifulSoup(r.text, "html.parser")
    except requests.RequestException as e:
        log.warning(f"Failed to fetch RefMetrics today's games: {e}")
        return []

    table = soup.find("table")
    if not table:
        log.info("No table found on RefMetrics today's games page.")
        return []

    games = []
    rows = table.find_all("tr")[1:]  # skip header
    for row in rows:
        cols = row.find_all("td")
        if len(cols) < 5:
            continue

        # Columns: Time | Matchup | Crew Chief | Umpire 1 | Umpire 2 | Network | Tournament
        raw_time   = cols[0].get_text(strip=True)
        matchup_td = cols[1]

        # Convert RefMetrics time (Eastern) to display timezone
        time_str = raw_time
        try:
            today = datetime.now(DISPLAY_TZ).date()
            t = datetime.strptime(raw_time, "%I:%M %p").time()
            dt_et = datetime.combine(today, t, tzinfo=REFMETRICS_TZ)
            dt_display = dt_et.astimezone(DISPLAY_TZ)
            time_str = dt_display.strftime("%I:%M %p").lstrip("0")
        except (ValueError, TypeError):
            pass
        cc_name    = cols[2].get_text(strip=True)
        u1_name    = cols[3].get_text(strip=True)
        u2_name    = cols[4].get_text(strip=True)
        network    = cols[5].get_text(strip=True) if len(cols) > 5 else ""

        # Parse matchup: "Away @ Home" (team names are in <strong> tags)
        strong_tags = matchup_td.find_all("strong")
        if len(strong_tags) >= 2:
            rm_away = strong_tags[0].get_text(strip=True)
            rm_home = strong_tags[1].get_text(strip=True)
        else:
            matchup_text = matchup_td.get_text(strip=True)
            parts = matchup_text.split("@")
            if len(parts) != 2:
                continue
            rm_away = parts[0].strip()
            rm_home = parts[1].strip()

        # Collect referee profile links
        ref_links = {}
        for a in row.find_all("a", href=True):
            if "referee-profile" in a["href"]:
                href = a["href"]
                if not href.startswith("http"):
                    href = REFMETRICS_URL + href
                ref_links[a.get_text(strip=True)] = href

        has_cc = cc_name and cc_name.upper() != "TBD"
        games.append({
            "espn_game_id":   None,
            "game_time_str":  time_str,
            "tipoff":         None,
            "home":           rm_home,
            "away":           rm_away,
            "home_logo":      "",
            "away_logo":      "",
            "network":        network,
            "location":       "",
            "crew_chief":     cc_name if has_cc else "",
            "u1":             u1_name if u1_name.upper() != "TBD" else "",
            "u2":             u2_name if u2_name.upper() != "TBD" else "",
            "has_assignment": has_cc,
            "assignment_src": "refmetrics" if has_cc else None,
            "ref_links":      ref_links,
        })

    log.info(f"RefMetrics schedule: {len(games)} games today.")
    return games


def merge_espn_into_games(games: list[dict], espn_games: list[dict]):
    """Merge ESPN data (logos, game IDs, venue) into the RefMetrics game list.
    Also add any ESPN games not found on RefMetrics."""
    matched_espn = set()

    for game in games:
        for i, eg in enumerate(espn_games):
            if i in matched_espn:
                continue
            if fuzzy_match(game["home"], eg["home"]) and fuzzy_match(game["away"], eg["away"]):
                # Merge ESPN metadata
                game["espn_game_id"] = eg["espn_game_id"]
                game["home_logo"]    = eg.get("home_logo", "")
                game["away_logo"]    = eg.get("away_logo", "")
                game["tipoff"]       = eg.get("tipoff")
                if eg.get("location"):
                    game["location"] = eg["location"]
                # Use ESPN time if RefMetrics time is missing
                if not game["game_time_str"] and eg.get("game_time_str"):
                    game["game_time_str"] = eg["game_time_str"]
                matched_espn.add(i)
                break

    # Add ESPN-only games not on RefMetrics
    for i, eg in enumerate(espn_games):
        if i not in matched_espn:
            games.append(eg)
            log.info(f"  ESPN-only game: {eg['away']} @ {eg['home']}")

    log.info(f"Merged ESPN data: {len(matched_espn)} matched, "
             f"{len(espn_games) - len(matched_espn)} ESPN-only added.")


# ── ENRICH WITH OFFICIALS ────────────────────────────────────────────────────
def enrich_with_officials(games: list[dict], session: requests.Session,
                          has_refmetrics: bool) -> list[dict]:
    """Try to get referee assignments for games still missing them."""
    # ESPN officials for any games without refs
    for game in games:
        if not game["has_assignment"] and game.get("espn_game_id"):
            officials = fetch_espn_officials(game["espn_game_id"])
            if officials and officials.get("crew_chief"):
                game.update({
                    "crew_chief":     officials["crew_chief"],
                    "u1":             officials.get("u1", ""),
                    "u2":             officials.get("u2", ""),
                    "has_assignment": True,
                    "assignment_src": "espn",
                })

    espn_assigned = sum(1 for g in games if g.get("assignment_src") == "espn")
    log.info(f"ESPN officials: {espn_assigned} game(s) assigned via ESPN.")

    # RefMetrics home page (login required) for any still missing
    if has_refmetrics:
        missing = [g for g in games if not g["has_assignment"]]
        if missing:
            log.info(f"Checking RefMetrics home for {len(missing)} unassigned game(s)...")
            enrich_from_refmetrics(missing, session)

    return games


def game_key(game: dict) -> str:
    """Unique key for a game: ESPN ID if available, otherwise 'away@home' slug."""
    if game.get("espn_game_id"):
        return str(game["espn_game_id"])
    return f"{game['away']}@{game['home']}".lower().replace(" ", "_")


def fuzzy_match(name_a: str, name_b: str) -> bool:
    """Loose match between team name formats."""
    a = name_a.lower().replace(".", "").replace("-", " ").replace("'", "").strip()
    b = name_b.lower().replace(".", "").replace("-", " ").replace("'", "").strip()
    if a in b or b in a:
        return True
    # Compare first two words (handles "Duke" vs "Duke Blue Devils")
    if a.split()[:2] == b.split()[:2]:
        return True
    # Compare first word only for single-word short names (e.g. "Duke" vs "Duke Blue Devils")
    a_words = a.split()
    b_words = b.split()
    if len(a_words) == 1 and a_words[0] == b_words[0]:
        return True
    if len(b_words) == 1 and b_words[0] == a_words[0]:
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

    # Re-check RefMetrics today's games first
    if not game["has_assignment"]:
        rm_games = fetch_refmetrics_schedule(session)
        for rm in rm_games:
            if rm["has_assignment"] and fuzzy_match(game["home"], rm["home"]) and fuzzy_match(game["away"], rm["away"]):
                game.update({
                    "crew_chief":     rm["crew_chief"],
                    "u1":             rm["u1"],
                    "u2":             rm["u2"],
                    "ref_links":      rm.get("ref_links", {}),
                    "has_assignment": True,
                    "assignment_src": "refmetrics",
                })
                break

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

    scraped_at = datetime.now(DISPLAY_TZ).isoformat(timespec="seconds")
    today_str  = datetime.now(DISPLAY_TZ).strftime("%Y-%m-%d")

    gk = _resolve_game_key(game, conn, today_str)
    conn.execute("""
        INSERT INTO game_assignments
            (scraped_at, league, game_date, game_time, home_team, away_team,
             home_logo, away_logo,
             crew_chief, u1, u2, network, location, espn_game_id, game_key,
             assignment_src)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(game_key) DO UPDATE SET
            scraped_at=excluded.scraped_at,
            home_logo=COALESCE(NULLIF(excluded.home_logo,''), home_logo),
            away_logo=COALESCE(NULLIF(excluded.away_logo,''), away_logo),
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
        game.get("espn_game_id", ""), gk, game.get("assignment_src", "none"),
    ))
    conn.commit()

    # Scrape ref profile stats from RefMetrics if available
    if has_refmetrics:
        for name, url in game.get("ref_links", {}).items():
            try:
                scrape_ref_profile(name, url, session, conn, scraped_at)
            except Exception as e:
                log.warning(f"Ref profile scrape failed for {name}: {e}")

    log.info(
        f"Saved [{game.get('assignment_src','none')}] "
        f"{game.get('crew_chief','?')} / {game.get('u1','?')} / {game.get('u2','?')}"
    )

    # Auto-score Layer 1 and fetch O/U line if refs are assigned
    if game.get("has_assignment"):
        cc = game.get("crew_chief", "")
        u1_name = game.get("u1", "")
        u2_name = game.get("u2", "")
        ref_count = sum(1 for n in [cc, u1_name, u2_name] if n and n.strip())
        if ref_count >= 2:
            existing = conn.execute(
                "SELECT id FROM game_analysis WHERE game_key = ?", (gk,)
            ).fetchone()
            if not existing:
                from analysis import score_layer_1
                l1 = score_layer_1(conn, cc, u1_name, u2_name)
                now_ts = datetime.now(DISPLAY_TZ).isoformat(timespec="seconds")
                conn.execute("""
                    INSERT INTO game_analysis
                        (game_key, l1_crew_avg, l1_tier, l1_strong_high_count,
                         l1_strong_low_count, l1_signal, l1_ref_details, l1_aligned,
                         created_at, updated_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """, (gk, l1["l1_crew_avg"], l1["l1_tier"], l1["l1_strong_high_count"],
                      l1["l1_strong_low_count"], l1["l1_signal"], l1["l1_ref_details"],
                      l1["l1_aligned"], now_ts, now_ts))
                conn.commit()
                log.info(f"Auto-scored Layer 1 for {gk}: crew_avg={l1['l1_crew_avg']}")

                # Notify Discord for green card games — only if game hasn't tipped off
                tipoff = game.get("tipoff")
                game_is_live = tipoff and tipoff <= datetime.now(DISPLAY_TZ)
                if l1["l1_crew_avg"] and l1["l1_crew_avg"] >= 35.5 and not game_is_live:
                    if not was_notified(conn, gk, "discord_refs"):
                        notify_discord(game, crew_avg=l1["l1_crew_avg"])
                        mark_notified(conn, gk, "discord_refs")

            # Fetch O/U line if analysis row exists but line is missing
            try:
                update_odds(conn)
            except Exception as e:
                log.warning(f"Odds fetch failed for {gk}: {e}")


def scrape_ref_profile(name: str, url: str, session: requests.Session,
                       conn: sqlite3.Connection, scraped_at: str):
    try:
        r    = session.get(url, timeout=8)
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


def fetch_odds_totals() -> dict:
    """Fetch O/U lines from The Odds API. Returns {normalised_team_name: point}."""
    api_key = ODDS_API_KEY
    if not api_key:
        return {}

    try:
        url = ODDS_API_URL.format(api_key=api_key)
        r = requests.get(url, timeout=15)
        data = r.json()
        remaining = r.headers.get("x-requests-remaining", "?")
        log.info(f"Odds API: fetched {len(data)} events ({remaining} requests remaining)")
    except Exception as e:
        log.warning(f"Odds API fetch failed: {e}")
        return {}

    lines = {}
    for event in data:
        home = event.get("home_team", "")
        away = event.get("away_team", "")
        # Use consensus from first bookmaker with a totals market
        for bk in event.get("bookmakers", []):
            for mkt in bk.get("markets", []):
                if mkt.get("key") == "totals":
                    for outcome in mkt.get("outcomes", []):
                        if outcome.get("name") == "Over" and outcome.get("point"):
                            lines[home.lower()] = outcome["point"]
                            lines[away.lower()] = outcome["point"]
                            break
                    break
            if home.lower() in lines:
                break
    return lines


def _match_odds_line(team_name: str, lines: dict) -> float | None:
    """Match a DB team name against Odds API team names using substring matching.
    Odds API uses full names like 'Rutgers Scarlet Knights' while ESPN uses 'Rutgers'.
    """
    team_lower = team_name.lower()
    # Exact match first
    if team_lower in lines:
        return lines[team_lower]
    # Check if DB name is a prefix of any Odds API name
    for odds_name, point in lines.items():
        if odds_name.startswith(team_lower) or team_lower.startswith(odds_name):
            return point
    # Check if all words in DB name appear in an Odds API name
    team_words = team_lower.split()
    for odds_name, point in lines.items():
        if all(w in odds_name for w in team_words):
            return point
    return None


def update_odds(conn: sqlite3.Connection):
    """Fetch O/U lines and populate game_analysis.ou_line for any games missing lines."""
    lines = fetch_odds_totals()
    if not lines:
        return 0

    rows = conn.execute("""
        SELECT ga.game_key, gm.home_team, gm.away_team
        FROM game_analysis ga
        JOIN game_assignments gm ON ga.game_key = gm.game_key
        WHERE ga.ou_line IS NULL OR ga.ou_line = 0
    """).fetchall()

    updated = 0
    for row in rows:
        point = _match_odds_line(row["home_team"], lines) or _match_odds_line(row["away_team"], lines)
        if point:
            conn.execute(
                "UPDATE game_analysis SET ou_line = ?, updated_at = ? WHERE game_key = ?",
                (point, datetime.now(DISPLAY_TZ).isoformat(timespec="seconds"), row["game_key"])
            )
            log.info(f"Set O/U line {point} for {row['game_key']}")
            updated += 1

    if updated:
        conn.commit()
    return updated


def fetch_ncaa_scoreboard(date_str: str) -> list[dict]:
    """Fetch NCAA scoreboard for a date. Returns list of game dicts with scores and state.
    date_str format: 'YYYY/MM/DD'
    """
    url = f"{NCAA_API_SCOREBOARD}/{date_str}"
    try:
        r = requests.get(url, timeout=15)
        data = r.json()
    except Exception as e:
        log.warning(f"NCAA scoreboard fetch failed: {e}")
        return []

    games = data.get("games", [])
    results = []
    for g in games:
        game = g.get("game", g)  # handle nested or flat structure
        home = game.get("home", {})
        away = game.get("away", {})
        home_names = home.get("names", {})
        away_names = away.get("names", {})

        home_score_str = home.get("score", "")
        away_score_str = away.get("score", "")

        results.append({
            "ncaa_game_id": game.get("gameID", ""),
            "game_state": game.get("gameState", ""),  # "pre", "live", "final"
            "home_short": home_names.get("short", ""),
            "away_short": away_names.get("short", ""),
            "home_seo": home_names.get("seo", ""),
            "away_seo": away_names.get("seo", ""),
            "home_char6": home_names.get("char6", ""),
            "away_char6": away_names.get("char6", ""),
            "home_score": int(home_score_str) if home_score_str else None,
            "away_score": int(away_score_str) if away_score_str else None,
            "start_time": game.get("startTime", ""),
            "network": game.get("network", ""),
        })
    return results


def _match_ncaa_game(db_home: str, db_away: str, ncaa_games: list[dict]) -> dict | None:
    """Match a DB game to an NCAA scoreboard entry by team name."""
    home_lower = db_home.lower()
    away_lower = db_away.lower()

    for g in ncaa_games:
        ncaa_home = g["home_short"].lower()
        ncaa_away = g["away_short"].lower()

        # Exact match
        if (home_lower == ncaa_home and away_lower == ncaa_away):
            return g

        # Substring match (handles "LSU" vs "LSU Tigers" etc)
        home_match = (home_lower in ncaa_home or ncaa_home in home_lower or
                      home_lower.startswith(ncaa_home.split()[0]) or
                      ncaa_home.startswith(home_lower.split()[0]))
        away_match = (away_lower in ncaa_away or ncaa_away in away_lower or
                      away_lower.startswith(ncaa_away.split()[0]) or
                      ncaa_away.startswith(away_lower.split()[0]))

        if home_match and away_match:
            return g

    return None


def fetch_espn_final_score(game_id: str) -> dict | None:
    """Fetch final score for a completed ESPN game. Fallback if NCAA API fails."""
    try:
        url = ESPN_SUMMARY.format(game_id=game_id)
        r = requests.get(url, timeout=15)
        data = r.json()
    except Exception as e:
        log.warning(f"ESPN score fetch failed for {game_id}: {e}")
        return None

    header = data.get("header", {})
    competitions = header.get("competitions", [{}])
    if not competitions:
        return None

    comp = competitions[0]
    status = comp.get("status", {}).get("type", {}).get("name", "")
    if status != "STATUS_FINAL":
        return None

    scores = {}
    for c in comp.get("competitors", []):
        side = c.get("homeAway", "")
        scores[side] = int(c.get("score", 0))

    home_score = scores.get("home", 0)
    away_score = scores.get("away", 0)
    return {
        "home_score": home_score,
        "away_score": away_score,
        "actual_total": home_score + away_score,
    }


def update_game_results(conn: sqlite3.Connection):
    """Fetch final scores using NCAA API scoreboard and compute HIT/MISS/PUSH."""
    # Get all green-card games missing results
    rows = conn.execute("""
        SELECT ga.game_key, ga.ou_line, ga.signal_direction,
               gm.espn_game_id, gm.home_team, gm.away_team, gm.game_date
        FROM game_analysis ga
        JOIN game_assignments gm ON ga.game_key = gm.game_key
        WHERE ga.l1_crew_avg >= 35.5
          AND ga.actual_total IS NULL
    """).fetchall()

    if not rows:
        return 0

    # Group by date and fetch NCAA scoreboard once per date
    dates = {}
    for row in rows:
        d = row["game_date"]
        dates.setdefault(d, []).append(row)

    updated = 0
    for game_date, games_for_date in dates.items():
        # Fetch NCAA scoreboard for this date
        ncaa_date = game_date.replace("-", "/")  # "2026-03-12" -> "2026/03/12"
        ncaa_games = fetch_ncaa_scoreboard(ncaa_date)

        for row in games_for_date:
            # Try NCAA API first
            match = _match_ncaa_game(row["home_team"], row["away_team"], ncaa_games) if ncaa_games else None

            score = None
            if match and match["game_state"] == "final" and match["home_score"] is not None:
                score = {
                    "home_score": match["home_score"],
                    "away_score": match["away_score"],
                    "actual_total": match["home_score"] + match["away_score"],
                }
            elif row["espn_game_id"]:
                # Fallback to ESPN summary API
                score = fetch_espn_final_score(row["espn_game_id"])

            if not score:
                continue

            actual = score["actual_total"]
            ou_line = row["ou_line"]
            signal = row["signal_direction"]

            # Determine result
            result = None
            if ou_line is not None and signal:
                if actual == ou_line:
                    result = "PUSH"
                elif signal == "OVER":
                    result = "HIT" if actual > ou_line else "MISS"
                elif signal == "UNDER":
                    result = "HIT" if actual < ou_line else "MISS"

            conn.execute("""
                UPDATE game_analysis
                SET actual_total = ?, home_score = ?, away_score = ?, result = ?,
                    updated_at = ?
                WHERE game_key = ?
            """, (actual, score["home_score"], score["away_score"], result,
                  datetime.now(DISPLAY_TZ).isoformat(timespec="seconds"), row["game_key"]))
            updated += 1

    if updated:
        conn.commit()
        log.info(f"Updated {updated} game result(s) via NCAA API.")
    return updated


def _resolve_game_key(game: dict, conn: sqlite3.Connection, today_str: str) -> str:
    """Resolve a stable game key, checking DB for existing entries with same teams.
    Prevents duplicates when a game is saved once with ESPN ID and again with slug.
    """
    gk = game_key(game)
    # If we already have an ESPN ID, use it
    if game.get("espn_game_id"):
        # Check if a slug-based entry already exists for this matchup
        slug = f"{game['away']}@{game['home']}".lower().replace(" ", "_")
        existing = conn.execute(
            "SELECT game_key FROM game_assignments WHERE game_key = ? AND game_date = ?",
            (slug, today_str)
        ).fetchone()
        if existing:
            # Migrate the old slug entry to use the ESPN ID
            conn.execute("UPDATE game_assignments SET game_key = ?, espn_game_id = ? WHERE game_key = ?",
                         (gk, game["espn_game_id"], slug))
            conn.execute("UPDATE game_analysis SET game_key = ? WHERE game_key = ?", (gk, slug))
            conn.execute("UPDATE notifications_sent SET game_key = ? WHERE game_key = ?", (gk, slug))
            conn.commit()
            log.info(f"Migrated game key {slug} -> {gk}")
        return gk
    else:
        # No ESPN ID — check if an ESPN-ID-based entry exists for these teams
        home_lower = game["home"].lower()
        away_lower = game["away"].lower()
        existing = conn.execute(
            "SELECT game_key FROM game_assignments WHERE game_date = ? AND LOWER(home_team) = ? AND LOWER(away_team) = ?",
            (today_str, home_lower, away_lower)
        ).fetchone()
        if existing:
            return existing["game_key"]  # reuse existing key (ESPN ID)
        return gk


def save_games_to_db(games: list[dict], conn: sqlite3.Connection):
    """Save all games to DB immediately so the dashboard can display them."""
    scraped_at = datetime.now(DISPLAY_TZ).isoformat(timespec="seconds")
    today_str  = datetime.now(DISPLAY_TZ).strftime("%Y-%m-%d")

    for game in games:
        gk = _resolve_game_key(game, conn, today_str)
        conn.execute("""
            INSERT INTO game_assignments
                (scraped_at, league, game_date, game_time, home_team, away_team,
                 home_logo, away_logo,
                 crew_chief, u1, u2, network, location, espn_game_id, game_key,
                 assignment_src)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(game_key) DO UPDATE SET
                scraped_at=excluded.scraped_at,
                home_logo=COALESCE(NULLIF(excluded.home_logo,''), home_logo),
                away_logo=COALESCE(NULLIF(excluded.away_logo,''), away_logo),
                location=COALESCE(NULLIF(excluded.location,''), location),
                espn_game_id=COALESCE(NULLIF(excluded.espn_game_id,''), espn_game_id),
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
            game.get("espn_game_id", ""), gk, game.get("assignment_src", "none"),
        ))
    conn.commit()
    log.info(f"Saved {len(games)} games to DB.")

    # Auto-score Layer 1 for games with 2+ refs assigned
    from analysis import score_layer_1
    now_dt = datetime.now(DISPLAY_TZ)
    for game in games:
        if not game.get("has_assignment"):
            continue
        gk = _resolve_game_key(game, conn, today_str)
        existing = conn.execute(
            "SELECT id FROM game_analysis WHERE game_key = ?", (gk,)
        ).fetchone()
        if existing:
            continue
        cc = game.get("crew_chief", "")
        u1_name = game.get("u1", "")
        u2_name = game.get("u2", "")
        ref_count = sum(1 for n in [cc, u1_name, u2_name] if n and n.strip())
        if ref_count < 2:
            continue
        l1 = score_layer_1(conn, cc, u1_name, u2_name)
        now = now_dt.isoformat(timespec="seconds")
        conn.execute("""
            INSERT INTO game_analysis
                (game_key, l1_crew_avg, l1_tier, l1_strong_high_count,
                 l1_strong_low_count, l1_signal, l1_ref_details, l1_aligned,
                 created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (gk, l1["l1_crew_avg"], l1["l1_tier"], l1["l1_strong_high_count"],
              l1["l1_strong_low_count"], l1["l1_signal"], l1["l1_ref_details"],
              l1["l1_aligned"], now, now))

        # Notify Discord for green card games — only if game hasn't tipped off yet
        tipoff = game.get("tipoff")
        game_is_live = tipoff and tipoff <= now_dt
        if l1["l1_crew_avg"] and l1["l1_crew_avg"] >= 35.5 and not game_is_live:
            already = was_notified(conn, gk, "discord_refs")
            log.info(f"Green card {gk}: crew_avg={l1['l1_crew_avg']:.1f}, already_notified={already}")
            if not already:
                notify_discord(game, crew_avg=l1["l1_crew_avg"])
                mark_notified(conn, gk, "discord_refs")
    conn.commit()


# ── MAIN ───────────────────────────────────────────────────────────────────────
def build_schedule(session: requests.Session) -> list[dict]:
    """Build the full game schedule from RefMetrics + ESPN."""
    # Primary: RefMetrics today's games (has all games)
    games = fetch_refmetrics_schedule(session)

    # Merge ESPN data (logos, game IDs, venue info)
    espn_games = fetch_espn_schedule()
    merge_espn_into_games(games, espn_games)

    if not games:
        log.warning("No games found from any source today.")
    else:
        log.info(f"Total schedule: {len(games)} games.")

    return games


def run_refresh(db_path: str = None) -> dict:
    """Run a lightweight refresh: fetch schedule + officials, save to DB.
    Skips heavy ref profile scraping (the background worker handles that).
    """
    _db_path = db_path or DB_PATH
    conn = init_db(_db_path)
    session = create_session()
    has_refmetrics = login_refmetrics(session)

    games = build_schedule(session)
    if not games:
        conn.close()
        return {"games": 0, "assigned": 0}

    games = enrich_with_officials(games, session, has_refmetrics)
    save_games_to_db(games, conn)

    assigned = sum(1 for g in games if g["has_assignment"])
    conn.close()
    log.info(f"Manual refresh complete: {len(games)} games, {assigned} assigned.")
    return {"games": len(games), "assigned": assigned}


def scrape_cycle():
    """Run a full scrape cycle: fetch schedule, enrich officials, save, fetch odds & scores."""
    log.info("-" * 40)
    log.info(f"Scrape cycle starting at {datetime.now(DISPLAY_TZ).strftime('%I:%M %p %Z')}")

    conn = init_db(DB_PATH)
    session = create_session()
    has_refmetrics = login_refmetrics(session)

    # Build schedule from RefMetrics + ESPN
    games = build_schedule(session)
    if not games:
        log.warning("No games found today.")
        conn.close()
        return

    # Get referee assignments
    games = enrich_with_officials(games, session, has_refmetrics)

    # Save all games to DB
    save_games_to_db(games, conn)

    # Scrape ref profiles for games with assignments
    for game in games:
        if not game.get("has_assignment") or not has_refmetrics:
            continue
        for name, url in game.get("ref_links", {}).items():
            try:
                scraped_at = datetime.now(DISPLAY_TZ).isoformat(timespec="seconds")
                scrape_ref_profile(name, url, session, conn, scraped_at)
            except Exception as e:
                log.warning(f"Ref profile scrape failed for {name}: {e}")

    # Fetch O/U lines
    try:
        odds_count = update_odds(conn)
        if odds_count:
            log.info(f"Captured {odds_count} O/U lines.")
    except Exception as e:
        log.warning(f"Odds fetch failed: {e}")

    # Fetch final scores
    try:
        update_game_results(conn)
    except Exception as e:
        log.warning(f"Score update failed: {e}")

    assigned = sum(1 for g in games if g["has_assignment"])
    log.info(f"Cycle complete: {len(games)} games, {assigned} with officials.")
    conn.close()


def main():
    log.info("=" * 60)
    log.info("Venetian — NCAA MBB Referee Tracker")
    log.info("Schedule: every hour at :21 and :51")
    log.info("=" * 60)

    # Run once immediately on startup
    try:
        scrape_cycle()
    except Exception as e:
        log.error(f"Initial scrape cycle failed: {e}")

    # Schedule recurring runs at :21 and :51
    scheduler = BackgroundScheduler()
    scheduler.add_job(
        scrape_cycle,
        CronTrigger(minute="21,51", timezone=DISPLAY_TZ),
        id="scrape_cycle",
        replace_existing=True,
    )
    scheduler.start()
    log.info("Scheduler running — scrape at :21 and :51 past each hour.\n")

    try:
        while True:
            time.sleep(60)
    except KeyboardInterrupt:
        log.info("Shutting down.")
        scheduler.shutdown()


if __name__ == "__main__":
    main()
