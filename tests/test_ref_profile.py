"""Tests for the RefMetrics referee profile parser and its daily refresh."""
import json
from datetime import datetime, timedelta
from unittest.mock import MagicMock

import pytest

import refmetrics_scraper as rs

# Shaped like the fields seen on a live profile: a one-row summary table,
# a per-season table and a game log whose cells contain nested elements.
PROFILE_HTML = """
<html><body>
<table>
  <tr><th>Games</th><th>CC Games</th><th>U1/U2 Games</th><th>Tier</th></tr>
  <tr><td>23</td><td>2</td><td>21</td><td>Tier 3</td></tr>
</table>
<table>
  <tr><th>Season</th><th>2024-25</th><th>2025-26</th><th>Total</th></tr>
  <tr><td>Games</td><td>0</td><td>1</td><td>1</td></tr>
</table>
<table>
  <tr><th>Date</th><th>Home</th><th>Away</th><th>Score</th><th>Fouls</th>
      <th>Line</th><th>O/U</th><th>Crew Chief</th><th>U1</th><th>U2</th></tr>
  <tr><td>2026-1-10</td><td>Creighton</td><td>St John's</td>
      <td><span>73</span> <span>-</span> <span>90</span></td>
      <td><span>18</span>
          <span>-</span>
          <span>20</span></td>
      <td><span>Favorite +15.5</span><span>St. John's -1.5</span></td>
      <td><span>Over +10.5</span><span>152.5</span></td>
      <td>James Breeding</td><td>Tony Chiazza</td><td>Michael Palau</td></tr>
  <tr><td>2026-1-03</td><td>Xavier</td><td>Butler</td>
      <td>70 - 64</td><td>15 - 17</td><td>Xavier -4.5</td>
      <td><span>Under -6.5</span><span>140.5</span></td>
      <td>Pat Adams</td><td>Tony Chiazza</td><td>Bo Boroski</td></tr>
</table>
</body></html>
"""


class TestParse:
    def test_game_log_rows_are_kept_separately(self):
        summary, log, headers = rs.parse_ref_profile(PROFILE_HTML)
        assert len(log) == 2
        assert log[0]["Fouls"] == "18 - 20"
        assert log[0]["O/U"] == "Over +10.5 152.5"
        assert log[1]["Home"] == "Xavier"

    def test_computed_summary(self):
        summary, _, _ = rs.parse_ref_profile(PROFILE_HTML)
        assert summary["Fouls/Game"] == "35.0"          # (38 + 32) / 2
        assert summary["Logged Games"] == "2"
        assert summary["O/U Record (logged)"] == "1 over / 1 under"

    def test_summary_table_kept_and_season_columns_skipped(self):
        summary, _, _ = rs.parse_ref_profile(PROFILE_HTML)
        assert summary["Games"] == "23"
        assert summary["Tier"] == "Tier 3"
        assert "2025-26" not in summary
        # game-log values don't leak into the summary any more
        assert "Home" not in summary and "Date" not in summary

    def test_unknown_layout_reports_headers(self):
        summary, log, headers = rs.parse_ref_profile(
            "<table><tr><th>Foo</th></tr><tr><td>1</td></tr></table>")
        assert log == [] and headers == [["Foo"]] and "Fouls/Game" not in summary


class TestScrape:
    @pytest.fixture
    def conn(self):
        c = rs.init_db(":memory:")
        yield c
        c.close()

    def _session(self):
        s = MagicMock()
        s.get.return_value.text = PROFILE_HTML
        return s

    def test_stores_parsed_profile(self, conn):
        now = datetime.now(rs.DISPLAY_TZ).isoformat(timespec="seconds")
        rs.scrape_ref_profile("Tony Chiazza", "http://x", self._session(), conn, now)
        row = conn.execute("SELECT * FROM referee_stats").fetchone()
        assert row["fouls_per_game"] == "35.0" and row["games"] == "23"
        assert len(json.loads(row["game_log_json"])) == 2

    def test_skips_ref_scraped_recently(self, conn):
        session = self._session()
        now = datetime.now(rs.DISPLAY_TZ).isoformat(timespec="seconds")
        rs.scrape_ref_profile("Tony Chiazza", "http://x", session, conn, now)
        rs.scrape_ref_profile("Tony Chiazza", "http://x", session, conn, now)
        assert session.get.call_count == 1
        assert conn.execute("SELECT COUNT(*) FROM referee_stats").fetchone()[0] == 1

    def test_rescrapes_old_format_and_stale_rows(self, conn):
        session = self._session()
        now = datetime.now(rs.DISPLAY_TZ)
        # an old-parser row from just now (no game_log_json) doesn't count
        conn.execute("INSERT INTO referee_stats (scraped_at, official_name) VALUES (?, ?)",
                     (now.isoformat(timespec="seconds"), "Tony Chiazza"))
        # a new-parser row from two days ago is stale
        conn.execute("INSERT INTO referee_stats (scraped_at, official_name, game_log_json) "
                     "VALUES (?, ?, '[]')",
                     ((now - timedelta(days=2)).isoformat(timespec="seconds"), "Tony Chiazza"))
        rs.scrape_ref_profile("Tony Chiazza", "http://x", session, conn,
                              now.isoformat(timespec="seconds"))
        assert session.get.call_count == 1
