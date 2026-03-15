"""Shared fixtures for Venetian test suite."""
import sqlite3
import pytest


@pytest.fixture
def db():
    """In-memory SQLite with production schema and controlled test referees."""
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row

    conn.executescript("""
        CREATE TABLE referee_foul_leaders (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            referee_name TEXT UNIQUE NOT NULL,
            total_fouls REAL,
            home_fouls REAL,
            away_fouls REAL,
            foul_diff REAL,
            total_games INTEGER,
            fouls_per_game REAL,
            tier TEXT,
            imported_at TEXT
        );

        CREATE TABLE game_assignments (
            game_key TEXT PRIMARY KEY,
            game_date TEXT,
            game_time TEXT,
            away_team TEXT,
            home_team TEXT,
            crew_chief TEXT,
            u1 TEXT,
            u2 TEXT,
            espn_id TEXT,
            espn_game_id TEXT,
            home_score INTEGER,
            away_score INTEGER,
            game_state TEXT DEFAULT 'pre',
            assignment_src TEXT DEFAULT 'none',
            home_logo TEXT DEFAULT '',
            away_logo TEXT DEFAULT '',
            network TEXT DEFAULT '',
            scraped_at TEXT
        );

        CREATE TABLE game_analysis (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            game_key TEXT UNIQUE NOT NULL,
            ou_line REAL,
            l1_crew_avg REAL,
            l1_tier TEXT,
            l1_strong_high_count INTEGER DEFAULT 0,
            l1_strong_low_count INTEGER DEFAULT 0,
            l1_signal TEXT,
            l1_ref_details TEXT,
            l1_aligned INTEGER DEFAULT 0,
            l3_ppg_vs_line TEXT,
            l3_aligned INTEGER DEFAULT 0,
            l2_aligned INTEGER DEFAULT 0,
            l4_aligned INTEGER DEFAULT 0,
            l5_aligned INTEGER DEFAULT 0,
            l6_aligned INTEGER DEFAULT 0,
            grade TEXT,
            signal_direction TEXT,
            layers_aligned INTEGER DEFAULT 0,
            stay_away_reason TEXT,
            actual_total REAL,
            home_score INTEGER,
            away_score INTEGER,
            result TEXT,
            created_at TEXT,
            updated_at TEXT
        );

        CREATE TABLE notifications_sent (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            game_key TEXT NOT NULL,
            notification_type TEXT NOT NULL,
            sent_at TEXT,
            UNIQUE(game_key, notification_type)
        );
    """)

    # Seed test referees covering all tiers
    refs = [
        ("High Alice",     38.0,  19.0, 19.0, 0.0, 100, 38.0,  "STRONG_HIGH"),
        ("High Bob",       37.5,  18.5, 19.0, 0.5,  80, 37.5,  "STRONG_HIGH"),
        ("Neutral Charlie", 35.0, 17.5, 17.5, 0.0, 120, 35.0,  "NEUTRAL"),
        ("Neutral Dana",   33.0,  16.5, 16.5, 0.0,  90, 33.0,  "NEUTRAL"),
        ("Low Eddie",      31.0,  15.5, 15.5, 0.0,  60, 31.0,  "STRONG_LOW"),
        ("Low Fran",       30.5,  15.0, 15.5, 0.5,  70, 30.5,  "STRONG_LOW"),
        ("Boundary High",  36.5,  18.0, 18.5, 0.5,  50, 36.5,  "NEUTRAL"),
        ("Boundary Low",   32.0,  16.0, 16.0, 0.0,  50, 32.0,  "NEUTRAL"),
        ("Edge Over",      36.51, 18.0, 18.51, 0.51, 40, 36.51, "STRONG_HIGH"),
        ("Edge Under",     31.99, 16.0, 15.99, 0.01, 40, 31.99, "STRONG_LOW"),
        ("Mike Johnson Jr.", 35.5, 17.5, 18.0, 0.5,  30, 35.5,  "NEUTRAL"),
    ]
    conn.executemany("""
        INSERT INTO referee_foul_leaders
            (referee_name, total_fouls, home_fouls, away_fouls,
             foul_diff, total_games, fouls_per_game, tier)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?)
    """, refs)
    conn.commit()

    yield conn
    conn.close()
