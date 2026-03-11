# Venetian — 6-Layer Betting Model Implementation Spec
## Based on NCAAB Ref System V5 + Integration Q&A (March 11, 2026)

---

## 1. Overview

Integrate the V5 6-layer NCAAB referee betting model into the existing Venetian dashboard. The system auto-scores Layer 1 (ref pedigree) and Layer 3 (pace/scoring comparison), accepts manual inputs for Layers 2, 4, 5, and 6, then produces a confidence grade (PASS / HALF UNIT / FULL UNIT) with direction (OVER / UNDER) per game.

**Key decisions from Q&A:**
- Semi-automatic: Layer 1 fully auto, Layer 3 auto when PPG is entered, rest manual
- All data inputs (team stats, player matchups, O/U line) are manual — no external API scraping
- Referee CSV imported into SQLite on startup (static career data, updated once per season)
- Over/under asymmetry enforced in UI and scoring logic
- Seasonal weighting: 5+ layers Nov–Jan, 4+ layers Feb–Mar (over plays only)
- Notifications include betting analysis for HALF UNIT and FULL UNIT plays only

---

## 2. Database Changes

### 2.1 New Table: `referee_foul_leaders`

Sourced from `refmetrics_ncaa_men_foul_leaders.csv`. Imported once on startup, re-imported if CSV is newer than last import.

```sql
CREATE TABLE IF NOT EXISTS referee_foul_leaders (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    referee_name    TEXT NOT NULL,
    total_fouls     REAL,
    home_fouls      REAL,
    away_fouls      REAL,
    foul_diff       REAL,
    total_games     INTEGER,
    fouls_per_game  REAL,       -- computed: total_fouls / total_games (or direct from CSV)
    tier            TEXT,        -- computed: 'STRONG_HIGH' | 'NEUTRAL' | 'STRONG_LOW'
    imported_at     TEXT
);
CREATE UNIQUE INDEX IF NOT EXISTS idx_rfl_name ON referee_foul_leaders(referee_name);
```

**Tier assignment logic:**
- `fouls_per_game > 36.5` → `STRONG_HIGH`
- `fouls_per_game >= 32.0 AND <= 36.5` → `NEUTRAL`
- `fouls_per_game < 32.0` → `STRONG_LOW`

**CSV import notes:**
- CSV columns: `Referee, Total Fouls, Home Fouls, Away Fouls, Foul Diff, Total Games`
- `fouls_per_game` = `Total Fouls / Total Games` (if not directly in CSV)
- Fuzzy name matching needed when looking up crew names against this table (strip suffixes, normalize spacing)
- CSV file location: project root or configurable via `CSV_PATH` env var

### 2.2 New Table: `game_analysis`

Stores the 6-layer analysis per game. One row per game, created when user first opens the analysis panel or when Layer 1 is auto-scored.

```sql
CREATE TABLE IF NOT EXISTS game_analysis (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    game_key        TEXT NOT NULL UNIQUE,

    -- O/U Line (manual input)
    ou_line         REAL,

    -- Layer 1: Ref Pedigree (AUTO)
    l1_crew_avg         REAL,
    l1_tier             TEXT,       -- 'STRONG_HIGH' | 'NEUTRAL' | 'STRONG_LOW'
    l1_strong_high_count INTEGER,
    l1_strong_low_count  INTEGER,
    l1_signal           TEXT,       -- 'OVER' | 'UNDER' | 'NEUTRAL' | 'MIXED'
    l1_ref_details      TEXT,       -- JSON: [{name, fpg, tier}, ...]
    l1_aligned          INTEGER DEFAULT 0,  -- 1 = layer supports signal

    -- Layer 2: Team Foul Profile (MANUAL)
    l2_home_fouls_pg    REAL,
    l2_away_fouls_pg    REAL,
    l2_home_ftr         REAL,
    l2_away_ftr         REAL,
    l2_home_fta_pg      REAL,
    l2_away_fta_pg      REAL,
    l2_aligned          INTEGER DEFAULT 0,

    -- Layer 3: Pace & Scoring (AUTO if PPG entered)
    l3_combined_ppg     REAL,
    l3_pace_rating      TEXT,       -- 'FAST' | 'AVERAGE' | 'SLOW'
    l3_ppg_vs_line      TEXT,       -- 'ABOVE' | 'CLOSE' | 'BELOW_10'
    l3_aligned          INTEGER DEFAULT 0,

    -- Layer 4: Player Matchups (MANUAL)
    l4_has_18ppg_scorer     INTEGER DEFAULT 0,
    l4_has_foul_drawer      INTEGER DEFAULT 0,
    l4_has_3pt_volume       INTEGER DEFAULT 0,  -- 2.5+ threes/game (override screen)
    l4_notes                TEXT,
    l4_aligned              INTEGER DEFAULT 0,

    -- Layer 5: Coaching Style (MANUAL)
    l5_pressing_defense     INTEGER DEFAULT 0,
    l5_attack_basket        INTEGER DEFAULT 0,
    l5_slow_methodical      INTEGER DEFAULT 0,
    l5_notes                TEXT,
    l5_aligned              INTEGER DEFAULT 0,

    -- Layer 6: Venue & Situational (MANUAL)
    l6_senior_day           INTEGER DEFAULT 0,
    l6_rivalry              INTEGER DEFAULT 0,
    l6_tournament_impl      INTEGER DEFAULT 0,
    l6_late_season          INTEGER DEFAULT 0,
    l6_power_ref_midmajor   INTEGER DEFAULT 0,
    l6_notes                TEXT,
    l6_aligned              INTEGER DEFAULT 0,

    -- Computed Grade
    layers_aligned      INTEGER DEFAULT 0,
    signal_direction    TEXT,       -- 'OVER' | 'UNDER' | NULL
    grade               TEXT,       -- 'PASS' | 'HALF_UNIT' | 'FULL_UNIT'
    grade_finalized     INTEGER DEFAULT 0,
    finalized_at        TEXT,

    created_at          TEXT,
    updated_at          TEXT
);
```

---

## 3. Backend — New Modules & Endpoints

### 3.1 New Module: `analysis.py`

Encapsulates all 6-layer scoring logic, separate from scraping and serving.

**Functions:**

#### `import_csv(db_path, csv_path)`
- Parse CSV, compute `fouls_per_game` and `tier` for each referee
- INSERT OR REPLACE into `referee_foul_leaders`
- Called on app startup (in `init_db` or separate init function)

#### `lookup_ref(conn, name) → dict | None`
- Query `referee_foul_leaders` by name (fuzzy: strip Jr/Sr, normalize whitespace, case-insensitive)
- Return `{name, fouls_per_game, tier, total_games, home_fouls, away_fouls, foul_diff}`

#### `score_layer_1(conn, crew_chief, u1, u2) → dict`
- Look up each ref via `lookup_ref`
- Compute crew average (only from refs found in DB)
- Count `STRONG_HIGH` and `STRONG_LOW` anchors
- Determine tier and signal:
  - Crew avg > 36.5 AND 2+ STRONG_HIGH → signal = `OVER`, aligned = 1
  - Crew avg < 32.0 AND 2+ STRONG_LOW → signal = `UNDER`, aligned = 1
  - Mixed (has both STRONG_HIGH and STRONG_LOW) → signal = `MIXED`, aligned = 0
  - Otherwise → signal = `NEUTRAL`, aligned = 0
- Return full Layer 1 dict for storage

#### `score_layer_3(combined_ppg, ou_line) → dict`
- If `combined_ppg >= ou_line` → ppg_vs_line = `ABOVE`, aligned = 1 (for over)
- If `ou_line - combined_ppg <= 5` → ppg_vs_line = `CLOSE`, aligned = 0
- If `ou_line - combined_ppg > 10` → ppg_vs_line = `BELOW_10`, aligned = 0 (caution)
- Return Layer 3 fields

#### `compute_grade(analysis_row, game_date) → dict`
- Count aligned layers (l1–l6 `_aligned` fields)
- Determine signal direction from Layer 1
- Apply seasonal weighting:
  - `game_date` Nov 1 – Jan 31 → over requires 5+ layers
  - `game_date` Feb 1 – Mar 31 → over requires 4+ layers
- Apply under asymmetry:
  - Under plays ALWAYS require 5–6 layers regardless of season
- Stay-away triggers (auto-PASS):
  - NEUTRAL crew (32.0–36.5)
  - Mixed crew (STRONG_HIGH + STRONG_LOW)
  - Incomplete crew (only 1 ref)
  - O/U line 160+ and signal is UNDER
- Grade assignment:
  - 5–6 layers aligned → `FULL_UNIT`
  - 4 layers aligned (meeting seasonal threshold) → `HALF_UNIT`
  - Below threshold → `PASS`
- Return `{layers_aligned, signal_direction, grade}`

### 3.2 New API Endpoints (app.py)

#### `GET /api/game/<game_key>/analysis`
- Return the `game_analysis` row for this game (JSON)
- If no row exists, auto-run `score_layer_1` from the game's crew and create the row
- Include the active seasonal mode (`EARLY_SEASON` or `LATE_SEASON`) in response

#### `POST /api/game/<game_key>/analysis`
- Accept JSON body with any combination of layer fields + `ou_line`
- Update the `game_analysis` row
- Re-run `score_layer_3` if `l3_combined_ppg` and `ou_line` are both present
- Re-run `compute_grade` after every save
- Return updated analysis

#### `POST /api/game/<game_key>/finalize`
- Set `grade_finalized = 1`, `finalized_at = now`
- If grade is `HALF_UNIT` or `FULL_UNIT`:
  - Send Discord notification with betting analysis
  - Send Slack notification with betting analysis
- If signal is `UNDER`, require all aligned layers to be explicitly confirmed (checked via `l1_aligned` through `l6_aligned` — at least 5 must be 1)
- Return final grade

### 3.3 Startup Changes

In `init_db()` or a new `init_analysis()`:
1. Create `referee_foul_leaders` table if not exists
2. Create `game_analysis` table if not exists
3. Import CSV if file exists and table is empty (or file is newer)
4. After games are saved to `game_assignments`, auto-run `score_layer_1` for any game with 2+ refs assigned and no existing `game_analysis` row

### 3.4 Notification Updates

**Discord (`notify_discord` in refmetrics_scraper.py):**
- Current: sends ref assignment embed on crew discovery
- Add: new function `notify_discord_analysis(game, analysis)` for finalized plays
- Format:
  ```
  FULL UNIT OVER — crew avg 37.2 | line 141.5 | 5/6 layers aligned
  Away @ Home | 7:00 PM CT | ESPN
  ```
- Only fires on finalize, only for HALF_UNIT or FULL_UNIT grades

**Slack (`notify_slack` in app.py):**
- Current: sends refresh summary
- Add: new function `notify_slack_analysis(game, analysis)` for finalized plays
- Same format as Discord
- Only fires on finalize, only for HALF_UNIT or FULL_UNIT grades

---

## 4. Frontend Changes (templates/index.html)

### 4.1 Game Card Layout Update

Current card structure:
```
.game-card
  .game-header (matchup + meta)
  .location
  .crew (refs)
```

New card structure:
```
.game-card
  .game-header (matchup + meta)
  .location
  .crew (refs)
  .crew-analysis-bar          ← NEW: crew avg + tier badge
  .grade-badge                ← NEW: PASS / HALF UNIT / FULL UNIT badge
  .analysis-panel (collapsed) ← NEW: expandable 6-layer breakdown
```

### 4.2 Crew Analysis Bar

Displayed below the crew section on every card with 2+ refs in the foul leaders DB.

```
Crew Avg: 37.2 · STRONG HIGH        [Analyze ▼]
```

- Crew avg displayed to one decimal
- Tier badge colors:
  - `STRONG_HIGH`: green (#34d399) background
  - `NEUTRAL`: grey (#555) background
  - `STRONG_LOW`: red/orange (#f87171) background
- "Analyze" button toggles the analysis panel

### 4.3 Grade Badge

Displayed on the card when a grade has been computed:

| Grade | Color | Label |
|-------|-------|-------|
| PASS | Grey (#555 on #1a1a1a) | `PASS` |
| HALF_UNIT + OVER | Green (#34d399 on #0d3320) | `HALF UNIT OVER` |
| FULL_UNIT + OVER | Dark green (#22c55e on #052e16) | `FULL UNIT OVER` |
| HALF_UNIT + UNDER | Yellow (#fbbf24 on #332b0d) | `⚠ HALF UNIT UNDER` |
| FULL_UNIT + UNDER | Yellow (#fbbf24 on #332b0d) | `⚠ FULL UNIT UNDER` |

Under grades always show the caution indicator (⚠).

### 4.4 Analysis Panel (Expandable)

Toggled by the "Analyze" button. Contains:

**O/U Line Input:**
- Single number field for the opening over/under line

**Layer 1 — Ref Pedigree (AUTO, read-only):**
- Each ref: name, fouls/game, tier badge
- Crew average, anchor counts
- Signal direction
- Green check or grey X for aligned status

**Layer 2 — Team Foul Profile (MANUAL):**
- Input fields: Home fouls/game, Away fouls/game, Home FTR, Away FTR, Home FTA/game, Away FTA/game
- Toggle: "Layer supports signal" (sets `l2_aligned`)

**Layer 3 — Pace & Scoring (AUTO when PPG entered):**
- Input fields: Combined PPG, Pace rating (dropdown: Fast / Average / Slow)
- Auto-computed: PPG vs line comparison
- Auto-aligned based on PPG vs line logic

**Layer 4 — Player Matchups (MANUAL):**
- Checkboxes: 18+ PPG scorer present, Foul-drawer present, 3PT volume (2.5+/game) — this is the override screen
- Notes text field
- Toggle: "Layer supports signal"

**Layer 5 — Coaching Style (MANUAL):**
- Checkboxes: Pressing defense, Attack-the-basket offense, Slow methodical offense
- Notes text field
- Toggle: "Layer supports signal"

**Layer 6 — Venue & Situational (MANUAL):**
- Checkboxes: Senior Day, Rivalry game, Tournament implications, Late season (Feb–Mar), Power ref in mid-major game
- Notes text field
- Toggle: "Layer supports signal"

**Summary Section:**
- Layer alignment count: `4/6 layers aligned`
- Active seasonal mode indicator: `LATE SEASON — 4+ layers required for over`
- Computed grade badge (large)
- "Finalize & Notify" button (disabled until grade is HALF_UNIT or FULL_UNIT, or enabled as PASS to dismiss)
- For UNDER plays: explicit confirmation step — "This is an UNDER play. Under signals carry a 24% blowup rate. Confirm all layers:" with individual layer checkboxes before finalize is enabled

**Auto-save behavior:**
- Every field change triggers a debounced (500ms) POST to `/api/game/<key>/analysis`
- Grade recomputes on every save
- No manual "save" button needed

### 4.5 Stats Bar Update

Current: `Total games | Assigned | Pending | Last scrape`

Add: `| Plays: X` — count of games graded HALF_UNIT or FULL_UNIT today

### 4.6 Seasonal Mode Indicator

Small badge in the header area showing:
- `NOV–JAN MODE: 5+ layers for over` (orange)
- `FEB–MAR MODE: 4+ layers for over` (green)

Based on today's date.

---

## 5. Referee Name Matching

Matching crew names (from RefMetrics/ESPN) to `referee_foul_leaders` CSV names is critical. Strategy:

1. Normalize: lowercase, strip whitespace, remove Jr/Sr/III suffixes
2. Exact match first
3. If no match: try last-name-only match (if unique in DB)
4. If no match: try Levenshtein distance ≤ 2 (optional, add `python-Levenshtein` to requirements)
5. Store the matched `referee_foul_leaders.id` on the analysis row for auditing

Log warnings for unmatched refs so they can be manually reviewed.

---

## 6. File Structure (New/Modified)

```
venetian/
├── app.py                          ← MODIFIED: new endpoints, init_analysis
├── analysis.py                     ← NEW: 6-layer scoring engine
├── refmetrics_scraper.py           ← MODIFIED: analysis notification functions
├── templates/
│   └── index.html                  ← MODIFIED: game card upgrade, analysis panel
├── refmetrics_ncaa_men_foul_leaders.csv  ← REQUIRED: user must provide
├── requirements.txt                ← MODIFIED: add python-Levenshtein (optional)
├── IMPLEMENTATION_SPEC.md          ← THIS FILE
├── tasks.md                        ← Task tracking
└── questions.md                    ← Answered Q&A
```

---

## 7. Environment Variables (New)

| Variable | Default | Description |
|----------|---------|-------------|
| `CSV_PATH` | `./refmetrics_ncaa_men_foul_leaders.csv` | Path to referee foul leaders CSV |

No other new env vars required.

---

## 8. Stay-Away Triggers (Enforced in Code)

These conditions auto-set grade to PASS regardless of layer count:

1. Only 1 ref listed (incomplete crew)
2. O/U line 160+ and signal direction is UNDER
3. Mixed crew (has both STRONG_HIGH and STRONG_LOW anchors)
4. NEUTRAL crew average (32.0–36.5)
5. No ref data available for any crew member
6. Under play with fewer than 5 layers aligned

When a stay-away trigger fires, the grade badge shows PASS with a tooltip explaining which trigger activated.

---

## 9. Edge Cases & Constraints

- **CSV not present:** Layer 1 returns "no data" state; analysis panel shows warning but still allows manual layer inputs
- **Ref not in CSV:** That ref is excluded from crew average calculation; if all refs missing, Layer 1 = no data
- **2-ref crew:** Valid — calculate average from 2 refs, still require 2+ STRONG_HIGH/LOW for signal
- **1-ref crew:** Auto-PASS per crew size rule
- **Game with no refs yet:** No analysis bar shown; card displays normally as today
- **Multiple saves:** Debounced auto-save prevents race conditions; last write wins
- **Finalize is one-way:** Once finalized, analysis is read-only (prevents accidental re-notification)
