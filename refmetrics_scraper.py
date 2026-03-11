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
from apscheduler.triggers.date import DateTrigger

# ── CONFIG ─────────────────────────────────────────────────────────────────────
USERNAME       = os.environ.get("REFMETRICS_USERNAME", "")
PASSWORD       = os.environ.get("REFMETRICS_PASSWORD", "")

REFMETRICS_URL = "https://www.refmetrics.com"
LOGIN_URL      = f"{REFMETRICS_URL}/login"
HOME_URL       = REFMETRICS_URL + "/"
TODAYS_GAMES_URL = REFMETRICS_URL + "/ncaa-men/todays-games"

MINUTES_BEFORE = int(os.environ.get("MINUTES_BEFORE", "10"))
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
    today_str = datetime.now().strftime("%Y%m%d")
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
            today = datetime.now().date()
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

    scraped_at = datetime.now().isoformat(timespec="seconds")
    today_str  = datetime.now().strftime("%Y-%m-%d")

    gk = game_key(game)
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
            scrape_ref_profile(name, url, session, conn, scraped_at)

    # Notify Discord when refs are assigned (only once per game)
    if game["has_assignment"]:
        already = conn.execute(
            "SELECT discord_notified FROM game_assignments WHERE game_key = ?", (gk,)
        ).fetchone()
        if not already or not already["discord_notified"]:
            notify_discord(game)
            conn.execute(
                "UPDATE game_assignments SET discord_notified = 1 WHERE game_key = ?", (gk,)
            )
            conn.commit()

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


def fetch_espn_final_score(game_id: str) -> dict | None:
    """Fetch final score for a completed ESPN game. Returns None if game not final."""
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


def _backfill_espn_ids(conn: sqlite3.Connection):
    """Find and fill missing ESPN game IDs by searching the scoreboard API."""
    missing = conn.execute("""
        SELECT game_key, game_date, home_team, away_team
        FROM game_assignments
        WHERE (espn_game_id IS NULL OR espn_game_id = '')
          AND game_date IS NOT NULL
    """).fetchall()
    if not missing:
        return

    # Group by date to minimize API calls
    dates = {}
    for row in missing:
        d = row["game_date"].replace("-", "")
        dates.setdefault(d, []).append(row)

    for date_str, games_needing_ids in dates.items():
        try:
            url = f"{ESPN_SCOREBOARD}?dates={date_str}&groups=50&limit=200"
            r = requests.get(url, timeout=15)
            data = r.json()
        except Exception as e:
            log.warning(f"ESPN scoreboard fetch for backfill failed: {e}")
            continue

        # Build lookup: normalised team name -> game_id
        espn_games = {}
        for event in data.get("events", []):
            eid = event.get("id")
            comp = event.get("competitions", [{}])[0]
            for c in comp.get("competitors", []):
                team_name = c["team"]["displayName"].lower()
                espn_games[team_name] = eid

        for row in games_needing_ids:
            home = row["home_team"].lower()
            away = row["away_team"].lower()
            eid = espn_games.get(home) or espn_games.get(away)
            if eid:
                conn.execute(
                    "UPDATE game_assignments SET espn_game_id = ? WHERE game_key = ?",
                    (eid, row["game_key"])
                )
                log.info(f"Backfilled ESPN ID {eid} for {row['game_key']}")

    conn.commit()


def update_game_results(conn: sqlite3.Connection):
    """Fetch final scores for green-card games missing results and compute HIT/MISS."""
    # First, try to backfill any missing ESPN game IDs
    try:
        _backfill_espn_ids(conn)
    except Exception as e:
        log.warning(f"ESPN ID backfill failed: {e}")

    rows = conn.execute("""
        SELECT ga.game_key, ga.ou_line, ga.signal_direction,
               gm.espn_game_id
        FROM game_analysis ga
        JOIN game_assignments gm ON ga.game_key = gm.game_key
        WHERE ga.l1_crew_avg >= 35.5
          AND ga.actual_total IS NULL
          AND gm.espn_game_id IS NOT NULL
          AND gm.espn_game_id != ''
    """).fetchall()

    updated = 0
    for row in rows:
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
              datetime.now().isoformat(timespec="seconds"), row["game_key"]))
        updated += 1

    if updated:
        conn.commit()
        log.info(f"Updated {updated} game result(s).")
    return updated


def save_games_to_db(games: list[dict], conn: sqlite3.Connection):
    """Save all games to DB immediately so the dashboard can display them."""
    scraped_at = datetime.now().isoformat(timespec="seconds")
    today_str  = datetime.now().strftime("%Y-%m-%d")

    for game in games:
        gk = game_key(game)
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
    for game in games:
        if not game.get("has_assignment"):
            continue
        gk = game_key(game)
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
        now = datetime.now().isoformat(timespec="seconds")
        conn.execute("""
            INSERT INTO game_analysis
                (game_key, l1_crew_avg, l1_tier, l1_strong_high_count,
                 l1_strong_low_count, l1_signal, l1_ref_details, l1_aligned,
                 created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (gk, l1["l1_crew_avg"], l1["l1_tier"], l1["l1_strong_high_count"],
              l1["l1_strong_low_count"], l1["l1_signal"], l1["l1_ref_details"],
              l1["l1_aligned"], now, now))
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
    """Run a full scrape cycle and return summary stats.
    Can be called from the Flask app for manual refresh.
    """
    _db_path = db_path or DB_PATH
    conn = init_db(_db_path)
    session = create_session()
    has_refmetrics = login_refmetrics(session)

    games = build_schedule(session)
    if not games:
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

    has_refmetrics = login_refmetrics(session)

    # Build schedule from RefMetrics + ESPN
    games = build_schedule(session)
    if not games:
        log.warning("No games found today. Exiting.")
        return

    # Get referee assignments for any still missing
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
