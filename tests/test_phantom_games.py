"""Tests for the after-midnight phantom duplicate fix in refmetrics_scraper."""
from datetime import datetime, timedelta

import pytest

import refmetrics_scraper as rs


def _seed(db, key, date, away, home, actual_total=None, analysis=True):
    db.execute("""INSERT INTO game_assignments (game_key, game_date, game_time,
                  home_team, away_team) VALUES (?, ?, '9:00 PM', ?, ?)""",
               (key, date, home, away))
    if analysis:
        db.execute("""INSERT INTO game_analysis (game_key, l1_crew_avg, actual_total,
                      created_at, updated_at) VALUES (?, 36.0, ?, '', '')""",
                   (key, actual_total))


def _keys(db, table="game_assignments"):
    return {r[0] for r in db.execute(f"SELECT game_key FROM {table}")}


class TestSameMatchup:
    def test_short_vs_full_names(self):
        assert rs._same_matchup("Seattle U", "Portland St",
                                "Seattle U Redhawks", "Portland State Vikings")

    def test_swapped_home_away(self):
        assert rs._same_matchup("Drake", "Southern", "Southern Jaguars", "Drake Bulldogs")

    def test_one_team_in_common_is_not_a_match(self):
        assert not rs._same_matchup("Drake", "Southern", "Drake Bulldogs", "Iowa Hawkeyes")

    def test_blank_names(self):
        assert not rs._same_matchup("", "", "Drake", "Southern")


class TestCleanup:
    def test_removes_next_day_copy_and_keeps_real_game(self, db):
        _seed(db, "401", "2026-11-07", "Portland State Vikings", "Seattle U Redhawks",
              actual_total=139)
        _seed(db, "2026-11-08_portland_st@seattle_u", "2026-11-08",
              "Portland St", "Seattle U")
        assert rs.remove_phantom_duplicates(db) == 1
        assert _keys(db) == {"401"}
        assert _keys(db, "game_analysis") == {"401"}

    def test_moves_analysis_to_real_game_when_it_has_none(self, db):
        _seed(db, "401", "2026-11-07", "Portland State", "Seattle U", analysis=False)
        _seed(db, "phantom", "2026-11-08", "Portland St", "Seattle U")
        rs.remove_phantom_duplicates(db)
        assert _keys(db) == {"401"}
        assert _keys(db, "game_analysis") == {"401"}

    def test_keeps_scored_rows_and_unrelated_games(self, db):
        _seed(db, "a", "2026-11-07", "Drake", "Iowa", actual_total=140)
        _seed(db, "b", "2026-11-08", "Drake", "Creighton")       # different opponent
        _seed(db, "c", "2026-11-09", "Drake", "Creighton", actual_total=150)  # has a score
        _seed(db, "d", "2026-11-11", "Drake", "Iowa")            # not the next day
        assert rs.remove_phantom_duplicates(db) == 0
        assert _keys(db) == {"a", "b", "c", "d"}

    def test_second_run_is_a_no_op(self, db):
        _seed(db, "401", "2026-11-07", "Portland State", "Seattle U", actual_total=139)
        _seed(db, "phantom", "2026-11-08", "Portland St", "Seattle U")
        rs.remove_phantom_duplicates(db)
        assert rs.remove_phantom_duplicates(db) == 0


class TestSaveSkipsYesterday:
    @pytest.fixture
    def db(self):
        conn = rs.init_db(":memory:")  # real schema, which save_games_to_db writes
        yield conn
        conn.close()

    def _game(self, away, home, espn_id=None):
        return {"away": away, "home": home, "game_time_str": "9:00 PM",
                "espn_game_id": espn_id, "has_assignment": False}

    def test_last_nights_game_not_saved_again(self, db):
        yesterday = (datetime.now(rs.DISPLAY_TZ) - timedelta(days=1)).strftime("%Y-%m-%d")
        today = datetime.now(rs.DISPLAY_TZ).strftime("%Y-%m-%d")
        _seed(db, "401", yesterday, "Portland State Vikings", "Seattle U Redhawks")
        rs.save_games_to_db([self._game("Portland St", "Seattle U"),
                             self._game("Duke", "Kansas")], db)
        rows = {(r["away_team"], r["game_date"]) for r in
                db.execute("SELECT away_team, game_date FROM game_assignments")}
        assert rows == {("Portland State Vikings", yesterday), ("Duke", today)}

    def test_espn_id_match_does_not_move_game_to_today(self, db):
        yesterday = (datetime.now(rs.DISPLAY_TZ) - timedelta(days=1)).strftime("%Y-%m-%d")
        _seed(db, "401", yesterday, "Portland State", "Seattle U")
        rs.save_games_to_db([self._game("Portland State", "Seattle U", espn_id="401")], db)
        assert db.execute("SELECT game_date FROM game_assignments WHERE game_key='401'"
                          ).fetchone()[0] == yesterday
