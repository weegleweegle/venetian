# Venetian — 6-Layer Model Implementation Tasks

## Phase 1: Foundation — Database & Layer 1 Auto-Scoring
*Goal: Import referee CSV, auto-score Layer 1, display crew avg + tier on game cards*

- [x] **1.1** Create `analysis.py` module with `import_csv()` function
- [x] **1.2** Add `referee_foul_leaders` table schema to `init_db()` in scraper
- [x] **1.3** Implement CSV parsing and import (compute fouls_per_game, assign tiers)
- [x] **1.4** Implement `lookup_ref()` with name normalization (lowercase, strip suffixes)
- [x] **1.5** Implement `score_layer_1()` — crew avg, anchor counts, tier, signal direction
- [x] **1.6** Add `game_analysis` table schema to `init_db()`
- [x] **1.7** Auto-run Layer 1 scoring after games are saved (startup + refresh)
- [x] **1.8** Add `GET /api/game/<game_key>/analysis` endpoint to app.py
- [x] **1.9** Update game cards in index.html — add crew analysis bar (crew avg + tier badge)
- [ ] **1.10** Test with sample data: verify tier assignment, crew avg math, badge display

## Phase 2: Analysis Panel & Manual Inputs
*Goal: Expandable analysis panel with input forms for all 6 layers*

- [x] **2.1** Build analysis panel HTML/CSS (collapsed by default, toggle on click)
- [x] **2.2** Add O/U line input field at top of panel
- [x] **2.3** Layer 1 section — read-only display of auto-scored ref data
- [x] **2.4** Layer 2 section — input fields for fouls/game, FTR, FTA (both teams)
- [x] **2.5** Layer 3 section — combined PPG input + pace dropdown, auto-compute PPG vs line
- [x] **2.6** Layer 4 section — checkboxes (18+ PPG, foul-drawer, 3PT volume) + notes
- [x] **2.7** Layer 5 section — checkboxes (pressing D, attack basket, slow offense) + notes
- [x] **2.8** Layer 6 section — checkboxes (Senior Day, rivalry, tourney, late season, power ref mid-major) + notes
- [x] **2.9** Add "Layer supports signal" toggle for manual layers (2, 4, 5, 6)
- [x] **2.10** Add `POST /api/game/<game_key>/analysis` endpoint — save all fields
- [x] **2.11** Implement `score_layer_3()` auto-computation on save
- [x] **2.12** Wire up debounced auto-save (500ms) on every field change in JS
- [x] **2.13** Load existing analysis data when panel opens (populate fields from GET)

## Phase 3: Grading Engine & Grade Display
*Goal: Compute confidence grades, display on cards, enforce rules*

- [x] **3.1** Implement `compute_grade()` — count aligned layers, determine grade
- [x] **3.2** Implement seasonal weighting logic (Nov–Jan: 5+ layers, Feb–Mar: 4+ layers)
- [x] **3.3** Implement under asymmetry (5–6 layers required year-round for under plays)
- [x] **3.4** Implement stay-away triggers (auto-PASS conditions)
- [x] **3.5** Add grade badge to game cards (PASS grey / HALF UNIT green / FULL UNIT dark green / UNDER yellow)
- [x] **3.6** Add summary section to analysis panel (layer count, seasonal mode, grade)
- [x] **3.7** Add seasonal mode indicator to dashboard header
- [x] **3.8** Add under confirmation step — explicit layer-by-layer confirmation before finalize
- [x] **3.9** Re-compute grade on every analysis save, update badge in real-time
- [x] **3.10** Add "Plays: X" counter to stats bar (count of HALF/FULL UNIT games today)

## Phase 4: Finalize & Notifications
*Goal: Lock grades, send betting analysis via Discord/Slack*

- [x] **4.1** Add `POST /api/game/<game_key>/finalize` endpoint
- [x] **4.2** Implement finalize logic — set grade_finalized, lock analysis as read-only
- [x] **4.3** Add "Finalize & Notify" button to analysis panel (disabled unless HALF/FULL UNIT)
- [x] **4.4** Create `notify_discord_analysis(game, analysis)` — formatted betting embed
- [x] **4.5** Create `notify_slack_analysis(game, analysis)` — formatted betting message
- [x] **4.6** Wire notifications to finalize endpoint (only HALF/FULL UNIT plays)
- [x] **4.7** Disable analysis panel editing after finalize (read-only state)
- [ ] **4.8** Test full flow: input → grade → finalize → notification received

## Phase 5: Polish & Edge Cases
*Goal: Handle edge cases, improve UX, final testing*

- [ ] **5.1** Handle missing CSV gracefully (warning state, manual layers still work)
- [ ] **5.2** Handle refs not found in CSV (exclude from avg, show "not in DB" indicator)
- [ ] **5.3** Handle 1-ref crew (auto-PASS, no analysis bar)
- [ ] **5.4** Add stay-away trigger tooltips on PASS badges (explain why)
- [ ] **5.5** Add ref fouls_per_game + tier to existing ref popup modal
- [ ] **5.6** Verify name matching accuracy across RefMetrics/ESPN names vs CSV names
- [ ] **5.7** Test seasonal mode cutover (Jan 31 → Feb 1 boundary)
- [ ] **5.8** Test under flow end-to-end (caution badge, confirmation step, 5+ layers)
- [ ] **5.9** Mobile responsiveness for analysis panel
- [ ] **5.10** Final integration test: full game day simulation with multiple games

## Phase 6: NCAA API Integration — Scores & Game State
*Goal: Replace per-game ESPN score fetching with NCAA API scoreboard for reliable final scores and game state detection*

- [x] **6.1** Add NCAA API constants and `fetch_ncaa_scoreboard(date_str)` function to scraper
- [x] **6.2** Replace `update_game_results()` — use single NCAA scoreboard call instead of per-game ESPN summary fetches
- [x] **6.3** Match NCAA scoreboard games to DB games by team name (fuzzy matching)
- [x] **6.4** Use `gameState == "final"` for reliable completed game detection on home page (replace 3-hour time estimate)
- [ ] **6.5** Store NCAA game ID in `game_assignments` for future per-game lookups
- [x] **6.6** Update home page completed game logic in `app.py` to use `gameState` from NCAA API
- [x] **6.7** Backfill scores for results page games that were missing ESPN IDs (no longer needed — NCAA API matches by team name, no ESPN ID required)
- [x] **6.8** Add fallback: if NCAA API fails, fall back to ESPN summary API
- [ ] **6.9** Test with live data: verify scores, game states, and HIT/MISS/PUSH computation

---

## Notes
- CSV file (`refmetrics_ncaa_men_foul_leaders.csv`) is present in project root
- Phases 1-4 implemented in initial build — Phase 5 is polish/testing
- Mark tasks `[x]` as completed, add date if helpful
