"""Tests for history.py: line/score snapshots, box scores, export and endpoints."""
import json
from datetime import datetime, timedelta
from unittest.mock import patch, MagicMock

import pytest

import history
import refmetrics_scraper as rs
from app import app


def _game(db, key, date, away, home, espn_id=None):
    db.execute("""INSERT INTO game_assignments (game_key, game_date, game_time,
                  home_team, away_team, espn_game_id, crew_chief)
                  VALUES (?, ?, '7:00 PM', ?, ?, ?, 'Ref A')""",
               (key, date, home, away, espn_id))


def _odds_event(home, away, commence, books):
    return {"home_team": home, "away_team": away, "commence_time": commence,
            "bookmakers": [{"key": k, "markets": [{"key": "totals", "outcomes": [
                {"name": "Over", "point": line, "price": -110},
                {"name": "Under", "point": line, "price": -110}]}]}
                for k, line in books]}


def _poly(date, a, b, line, start, price=0.5):
    return {"game_date": date, "team_a": a, "team_b": b, "line": line,
            "over_price": price, "volume": 10.0, "start": start}


class TestLineSnapshots:
    def test_records_each_book_and_polymarket(self, db):
        _game(db, "g1", "2026-11-09", "Michigan State", "Duke")
        odds = [_odds_event("Duke Blue Devils", "Michigan State Spartans",
                            "2026-11-10T00:00:00Z", [("draftkings", 149.5), ("fanduel", 150.0)])]
        poly = [_poly("2026-11-09", "michigan state spartans", "duke blue devils",
                      149.5, "2026-11-10T00:00:00Z")]
        assert history.record_line_snapshots(db, odds, poly) == 3
        rows = db.execute("SELECT source, book, line, price_type FROM line_snapshots "
                          "ORDER BY source, book").fetchall()
        assert [tuple(r) for r in rows] == [
            ("odds_api", "draftkings", 149.5, "american"),
            ("odds_api", "fanduel", 150.0, "american"),
            ("polymarket", "", 149.5, "probability")]

    def test_only_adds_rows_when_something_changed(self, db):
        _game(db, "g1", "2026-11-09", "Michigan State", "Duke")
        ev = lambda line: [_odds_event("Duke Blue Devils", "Michigan State Spartans",
                                       "2026-11-10T00:00:00Z", [("draftkings", line)])]
        history.record_line_snapshots(db, ev(149.5), [])
        assert history.record_line_snapshots(db, ev(149.5), []) == 0
        assert history.record_line_snapshots(db, ev(151.0), []) == 1

    def test_ignores_games_we_dont_track_and_wrong_dates(self, db):
        _game(db, "g1", "2026-11-09", "Michigan State", "Duke")
        odds = [_odds_event("Kansas Jayhawks", "Kentucky Wildcats",
                            "2026-11-10T00:00:00Z", [("draftkings", 155.5)]),
                # right teams, but the next day
                _odds_event("Duke Blue Devils", "Michigan State Spartans",
                            "2026-11-11T00:00:00Z", [("draftkings", 149.5)])]
        assert history.record_line_snapshots(db, odds, []) == 0


class TestScoreSnapshots:
    def _ncaa(self, state, period, clock, home, away):
        return [{"home_short": "Duke", "away_short": "Michigan St.", "game_state": state,
                 "period": period, "clock": clock, "home_score": home,
                 "away_score": away, "ncaa_game_id": "9"}]

    def test_records_changes_only_and_skips_pregame(self, db):
        _game(db, "g1", "2026-11-09", "Michigan State", "Duke")
        rec = lambda *a: history.record_score_snapshots(db, "2026-11-09", self._ncaa(*a))
        assert rec("pre", "", "", None, None) == 0
        assert rec("live", "1", "12:00", 10, 8) == 1
        assert rec("live", "1", "12:00", 10, 8) == 0
        assert rec("final", "FINAL", "0:00", 75, 70) == 1
        rows = db.execute("SELECT state, home_score, away_score FROM score_snapshots "
                          "ORDER BY id").fetchall()
        assert [tuple(r) for r in rows] == [("live", 10, 8), ("final", 75, 70)]


ESPN_SUMMARY = {
    "header": {"competitions": [{
        "status": {"type": {"name": "STATUS_FINAL"}},
        "competitors": [
            {"homeAway": "home", "score": "75", "team": {"displayName": "Virginia Cavaliers"}},
            {"homeAway": "away", "score": "72", "team": {"displayName": "Arkansas Razorbacks"}},
        ]}]},
    "boxscore": {"teams": [
        {"homeAway": "away", "statistics": [
            {"name": "fouls", "displayValue": "17"},
            {"name": "totalTechnicalFouls", "displayValue": "1"},
            {"name": "flagrantFouls", "displayValue": "0"},
            {"name": "freeThrowsMade-freeThrowsAttempted", "displayValue": "11-18"}]},
        {"homeAway": "home", "statistics": [
            {"name": "fouls", "displayValue": "16"},
            {"name": "totalTechnicalFouls", "displayValue": "0"},
            {"name": "flagrantFouls", "displayValue": "0"},
            {"name": "freeThrowsMade-freeThrowsAttempted", "displayValue": "15-20"}]},
    ]},
}


class TestBoxScores:
    def test_parse(self):
        box = history.parse_espn_box_score(ESPN_SUMMARY)
        assert (box["home_score"], box["away_score"]) == (75, 72)
        assert (box["home_fouls"], box["away_fouls"]) == (16, 17)
        assert (box["away_ftm"], box["away_fta"]) == (11, 18)
        assert box["away_techs"] == 1
        assert json.loads(box["stats_json"])["home"]["fouls"] == "16"

    def test_parse_skips_unfinished(self):
        live = json.loads(json.dumps(ESPN_SUMMARY))
        live["header"]["competitions"][0]["status"]["type"]["name"] = "STATUS_IN_PROGRESS"
        assert history.parse_espn_box_score(live) is None

    def test_store_uses_espn_id_and_skips_stored_games(self, db):
        yesterday = (datetime.now(rs.DISPLAY_TZ) - timedelta(days=1)).strftime("%Y-%m-%d")
        _game(db, "g1", yesterday, "Arkansas", "Virginia", espn_id="401")
        resp = MagicMock()
        resp.json.return_value = ESPN_SUMMARY
        with patch("history.requests.get", return_value=resp) as get:
            assert history.store_box_scores(db) == 1
            assert history.store_box_scores(db) == 0  # already stored
        assert get.call_count == 1
        assert "401" in get.call_args.args[0]

    def test_store_finds_missing_espn_id_on_scoreboard(self, db):
        yesterday = (datetime.now(rs.DISPLAY_TZ) - timedelta(days=1)).strftime("%Y-%m-%d")
        _game(db, "g1", yesterday, "Arkansas", "Virginia")
        board = MagicMock()
        board.json.return_value = {"events": [{"id": "777", "competitions": [{"competitors": [
            {"homeAway": "home", "team": {"displayName": "Virginia Cavaliers"}},
            {"homeAway": "away", "team": {"displayName": "Arkansas Razorbacks"}}]}]}]}
        summary = MagicMock()
        summary.json.return_value = ESPN_SUMMARY
        with patch("history.requests.get", side_effect=[board, summary]):
            assert history.store_box_scores(db) == 1
        assert db.execute("SELECT espn_game_id FROM box_scores").fetchone()[0] == "777"

    def test_future_games_not_fetched(self, db):
        tomorrow = (datetime.now(rs.DISPLAY_TZ) + timedelta(days=1)).strftime("%Y-%m-%d")
        _game(db, "g1", tomorrow, "Arkansas", "Virginia", espn_id="401")
        with patch("history.requests.get") as get:
            assert history.store_box_scores(db) == 0
        get.assert_not_called()


def _snap(db, gk, source, book, captured, line, commence="2026-11-10T00:00:00Z"):
    db.execute("""INSERT INTO line_snapshots (game_key, source, book, captured_at,
                  commence_time, line) VALUES (?, ?, ?, ?, ?, ?)""",
               (gk, source, book, captured, commence, line))


class TestReading:
    def _setup(self, db):
        _game(db, "g1", "2026-11-09", "Michigan State", "Duke")
        _snap(db, "g1", "odds_api", "draftkings", "2026-11-08T12:00:00Z", 147.5)  # opening
        _snap(db, "g1", "odds_api", "betonline", "2026-11-09T23:00:00Z", 150.5)
        _snap(db, "g1", "odds_api", "draftkings", "2026-11-09T22:00:00Z", 149.5)  # close (preferred)
        _snap(db, "g1", "polymarket", "", "2026-11-09T23:30:00Z", 150.0)
        _snap(db, "g1", "odds_api", "draftkings", "2026-11-10T00:30:00Z", 160.5)  # in-game
        db.execute("""INSERT INTO box_scores (game_key, home_score, away_score,
                      home_fouls, away_fouls, home_fta, away_fta, home_techs, away_techs)
                      VALUES ('g1', 80, 75, 16, 17, 20, 18, 0, 1)""")

    def test_opening_and_closing_lines(self, db):
        self._setup(db)
        h = history.game_history(db, "g1")
        assert h["summary"]["opening_line"]["line"] == 147.5
        assert h["summary"]["closing_line_odds_api"]["line"] == 149.5
        assert h["summary"]["closing_line_polymarket"]["line"] == 150.0
        assert len(h["lines"]) == 5

    def test_export_row(self, db):
        self._setup(db)
        [row] = history.export_rows(db, "2026-11-09", "2026-11-09")
        assert row["open_line"] == 147.5
        assert row["close_line_odds_api"] == 149.5 and row["close_book"] == "draftkings"
        assert row["total"] == 155 and row["result_vs_close"] == "OVER"
        assert row["total_fouls"] == 33 and row["total_fta"] == 38 and row["total_techs"] == 1
        assert set(row) == set(history.EXPORT_COLUMNS)


class TestEndpoints:
    @pytest.fixture
    def client(self, db):
        def fake_get_db():
            from flask import g
            if "db" not in g:
                g.db = db
            return g.db
        app.config["TESTING"] = True
        with app.test_client() as c, app.app_context(), patch("app.get_db", fake_get_db):
            yield c

    def test_history_endpoint(self, client, db):
        _game(db, "2026-11-09_a@b", "2026-11-09", "A", "B")
        assert client.get("/api/game/2026-11-09_a@b/history").get_json()["game"]["home_team"] == "B"
        assert client.get("/api/game/nope/history").status_code == 404

    def test_csv_export(self, client, db):
        _game(db, "g1", "2026-11-09", "Michigan State", "Duke")
        r = client.get("/api/export/games.csv?start=2026-11-01&end=2026-11-30")
        assert r.status_code == 200 and r.mimetype == "text/csv"
        lines = r.get_data(as_text=True).strip().splitlines()
        assert lines[0].split(",") == history.EXPORT_COLUMNS
        assert len(lines) == 2

    def test_csv_bad_date(self, client):
        assert client.get("/api/export/games.csv?start=nov").status_code == 400
