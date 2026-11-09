"""Tests for the Polymarket O/U fallback in refmetrics_scraper."""
import json
from datetime import datetime, timedelta, timezone
from unittest.mock import patch, MagicMock

import refmetrics_scraper as rs


def _market(line, over_price, closed=False, start="2026-11-09 23:00:00+00"):
    return {
        "sportsMarketType": "totals", "line": line, "closed": closed,
        "outcomes": json.dumps(["Over", "Under"]),
        "outcomePrices": json.dumps([str(over_price), str(round(1 - over_price, 4))]),
        "gameStartTime": start,
    }


def _event(title, markets):
    return {"title": title, "markets": markets}


class TestMainLine:
    def test_picks_line_closest_to_even(self):
        ms = [_market(149.5, 0.62), _market(150.5, 0.51), _market(151.5, 0.40)]
        assert rs._polymarket_main_line(ms) == 150.5

    def test_ignores_closed_and_non_totals(self):
        ms = [_market(150.5, 0.50, closed=True), _market(152.5, 0.55),
              {"sportsMarketType": "spreads", "line": -3.5,
               "outcomes": '["Over","Under"]', "outcomePrices": '["0.5","0.5"]'}]
        assert rs._polymarket_main_line(ms) == 152.5

    def test_rejects_lopsided_lines(self):
        assert rs._polymarket_main_line([_market(144.5, 0.9)]) is None


class TestTeamMatching:
    games = [("texas tech red raiders", "kansas jayhawks", 162.5),
             ("hawaii rainbow warriors", "ut arlington mavericks", 146.5)]

    def test_short_and_full_names_match(self):
        assert rs._match_polymarket_game("Kansas", "Texas Tech", self.games) == 162.5
        assert rs._match_polymarket_game("Kansas Jayhawks", "Texas Tech Red Raiders",
                                         self.games) == 162.5

    def test_needs_both_teams(self):
        # "Texas" prefix-matches "texas tech..." but the opponent doesn't match
        assert rs._match_polymarket_game("Texas", "Rice", self.games) is None

    def test_punctuation_ignored(self):
        assert rs._match_polymarket_game("Hawai'i", "UT Arlington", self.games) == 146.5


class TestFetch:
    def _fetch(self, events, now):
        resp = MagicMock()
        resp.json.return_value = events
        with patch("refmetrics_scraper.requests.get", return_value=resp):
            return rs.fetch_polymarket_totals(now=now)

    def test_skips_games_already_started(self):
        now = datetime(2026, 11, 9, 23, 30, tzinfo=timezone.utc)
        out = self._fetch([_event("A vs. B", [_market(150.5, 0.5)])], now)
        assert out == {}

    def test_groups_by_local_game_date(self):
        # 02:00 UTC on the 10th is the evening of the 9th in Chicago
        now = datetime(2026, 11, 9, 12, 0, tzinfo=timezone.utc)
        ev = _event("Duke Blue Devils vs. Michigan State Spartans",
                    [_market(149.5, 0.48, start="2026-11-10 02:00:00+00")])
        out = self._fetch([ev], now)
        assert out == {"2026-11-09": [("duke blue devils", "michigan state spartans", 149.5)]}


class TestUpdateOdds:
    def _seed(self, db, key, date, home, away, ou=None):
        db.execute("""INSERT INTO game_assignments (game_key, game_date, game_time,
                      home_team, away_team) VALUES (?, ?, '7:00 PM', ?, ?)""",
                   (key, date, home, away))
        if ou is not None:
            db.execute("""INSERT INTO game_analysis (game_key, ou_line, created_at,
                          updated_at) VALUES (?, ?, '', '')""", (key, ou))

    def test_fills_missing_line_without_overwriting(self, db):
        today = datetime.now(rs.DISPLAY_TZ).strftime("%Y-%m-%d")
        self._seed(db, "g1", today, "Kansas", "Texas Tech")
        self._seed(db, "g2", today, "Duke", "Michigan State", ou=148.0)
        poly = {today: [("texas tech red raiders", "kansas jayhawks", 162.5),
                        ("duke blue devils", "michigan state spartans", 149.5)]}
        with patch("refmetrics_scraper.fetch_odds_totals", return_value={}), \
             patch("refmetrics_scraper.fetch_polymarket_totals", return_value=poly):
            rs.update_odds(db)
        lines = dict(db.execute("SELECT game_key, ou_line FROM game_analysis").fetchall())
        assert lines == {"g1": 162.5, "g2": 148.0}

    def test_odds_api_wins_over_polymarket(self, db):
        today = datetime.now(rs.DISPLAY_TZ).strftime("%Y-%m-%d")
        self._seed(db, "g1", today, "Kansas", "Texas Tech")
        poly = {today: [("texas tech red raiders", "kansas jayhawks", 162.5)]}
        with patch("refmetrics_scraper.fetch_odds_totals",
                   return_value={"kansas jayhawks": 160.0}), \
             patch("refmetrics_scraper.fetch_polymarket_totals", return_value=poly) as pm:
            rs.update_odds(db)
        assert db.execute("SELECT ou_line FROM game_analysis").fetchone()[0] == 160.0
        pm.assert_not_called()  # nothing left missing, so no Polymarket call
