# NCAAB REFEREE BETTING SYSTEM — MASTER DOCUMENT V5
## Updated March 11, 2026 — Thresholds Tightened via 7-Day Backtesting (722+ games)
## ALL-TIME RECORD: 9-2

---

## HOW TO USE THIS DOCUMENT
Upload this file + the referee database CSV (refmetrics_ncaa_men_foul_leaders.csv) to any new Claude chat. Claude will have full context of the system, rules, results, and methodology. Ask Claude to grade any crew assignment using the 6-layer model below.

---

## SYSTEM OVERVIEW
Pre-game O/U betting system exploiting referee foul-calling tendencies that books don't fully price. 6-layer model requiring 4+ aligned layers before betting. **ALL conferences are eligible — mid-major, power, and everything in between.** Mid-major games retain the primary edge due to books' blind spot on crew assignments, but power conference games are NOT auto-excluded and must be run through all 6 layers.

**Primary edge: OVER signal. STRONG HIGH crews generate a proven, repeatable edge above juice threshold. UNDER signal exists but is significantly less reliable — treat as supporting evidence only, never lead with it.**

---

## DATABASE
- **1,451 referees** ranked by career total fouls per game (2010-2026, minimum 5 games)
- File: refmetrics_ncaa_men_foul_leaders.csv
- Columns: Referee, Total Fouls, Home Fouls, Away Fouls, Foul Diff, Total Games
- Career average IS the grading number — no regression filters, no recent-season adjustments

**Database stats: Mean 35.59 | Median 35.60 | Std Dev 2.54**

---

## TIER SYSTEM (V5 — Thresholds tightened from backtesting)

3-tier system only. No HIGH or LOW mid-categories — a ref is either meaningful or neutral.

| Tier | Range | Signal |
|------|-------|--------|
| STRONG HIGH | > 36.5 | Primary OVER signal — confirmed edge |
| NEUTRAL | 32.0 – 36.5 | No bet — PASS |
| STRONG LOW | < 32.0 | UNDER signal — use with extreme caution |

### Why these thresholds?
**STRONG HIGH >36.5:** 7-day audit confirmed foul correlation holds strongly above 36.5. Crews in the 36.0–36.49 band showed 42% miss rate — not worth the noise. Above 36.5 is where the edge lives.

**STRONG LOW <32.0:** Previous threshold of <34.1 generated 198 under signals with a 39–46% blowup rate in most bands — worse than coin flip after juice. Tightened to <32.0 based on 7-day data. Still carries 24% blowup risk even at this level. Use only with strong Layer 2–6 confirmation.

---

## THE 6-LAYER MODEL

### Layer 1 — Referee Pedigree (THE FOUNDATION)
- Look up all refs in database
- Calculate crew average
- Count STRONG HIGH vs STRONG LOW anchors
- **Crew average 36.5+ with 2+ STRONG HIGH = strongest OVER signal**
- **Crew average below 32.0 with 2+ STRONG LOW = UNDER signal (less reliable — see backtesting)**
- Mixed crew (one STRONG HIGH, one STRONG LOW) = NO BET
- NEUTRAL crew average = NO BET

### Layer 2 — Team Foul Profile
- Fouls committed per game (both teams)
- Free throw rate (FTR) and FTA per game
- High FTR + high-foul crew = free throw parade
- Low FTR can be overridden by truly elite crews (36.5+) — see Refinements
- Check KenPom or team stats for FTR data

### Layer 3 — Pace & Scoring (HARD REQUIREMENT)
- Combined PPG for both teams
- Compare combined PPG to the O/U line
- If combined PPG already EXCEEDS the line = strong over signal
- If combined PPG is BELOW the line by 10+ = caution
- **CRITICAL: Bad offensive teams can have 40+ fouls and still go under (Wyoming/Air Force: 40 fouls, 128 total). Layer 3 MUST confirm.**

### Layer 4 — Player Matchups
- Dominant inside scorers who draw fouls (check for 18+ PPG players)
- Aggressive ball handlers who attack the rim
- Foul-prone defenders
- Star players shooting high FT% = converts ref's fouls into points
- **Three-point override**: Teams with 2.5+ threes per game can shoot OVER any crew. Screen before locking UNDER plays.

### Layer 5 — Coaching Style
- Pressing defense = more fouls
- Attack-the-basket offense = more FTA
- Slow methodical offense = fewer possessions
- Most underrated layer

### Layer 6 — Venue & Situational
- Home teams get favorable calls (check ref home/away foul differential)
- Power ref in mid-major game = unfamiliar dynamic (this IS the edge)
- Senior Day = emotional, more aggressive play
- Late season = more physical
- Tournament implications = more aggressive
- Rivalry games = more intensity

---

## CONFIDENCE SCORING

| Alignment | Action |
|-----------|--------|
| 5-6 layers aligned | **Full Unit** (strong play) |
| 4 layers aligned | **Half Unit** (medium play) |
| 3 layers aligned | Monitor only, NO BET |
| Mixed/Neutral | Stay away completely |
| 2 or fewer | Stay away completely |

**GOLDEN RULE:** If any layer contradicts another without clear dominant direction = NO BET. Neutral = NO BET always.

---

## PLAY TYPES BY PRIORITY

### Tier 1 (Highest) — Pre-game Over
Based on ref pedigree. Books price totals on team stats but don't fully account for referee pedigree — this is the primary inefficiency exploited.

### Tier 2 (Good) — 2H Point Spread
Based on halftime box score. Better team shooting below average + opponent foul trouble + competitive game within 8 points.

### Tier 3 (Only if strong signal) — Pre-game Under
Only with <32.0 crew + 5-6 layers aligned. Single weakest signal in the system — requires maximum confirmation before betting.

### Tier 4 (Only if strong signal) — 2H Over/Under
Only when BOTH original full game O/U AND ref crew confirm same direction.

---

## CRITICAL RULES

### Mid-Major Blind Spot (PRIMARY EDGE)
Mid-major conferences (Big West, WAC, Summit, Big Sky, SWAC, OVC, WCC non-Gonzaga, etc.) remain the highest-edge environment — books are most blind to crew assignments here. Power conferences (SEC, Big Ten, Big 12, ACC, Big East, A-10, etc.) are fully eligible. Books may partially price in elite crews for high-profile power conf games — note this in Layer 1 — but do NOT auto-pass. Run all 6 layers.

### Crew Size Rule
- **1 ref listed** = incomplete crew → PASS, no analysis
- **2 or 3 refs listed** = valid crew → run full 6-layer analysis
- Calculate crew average from however many refs are assigned

### Always Check Full Game O/U FIRST
Before ANY bet, check what books opened the game at. If original O/U is 160+ stay away from under. Gap between original line and crew tendency = where edge lives.

### Never Bet Incomplete Crews
If third ref is TBD, do not bet. Exception: 2-Ref Rule above.

### Blowout Awareness
Big spreads (15+) can still produce overs IF the favorite scores heavily (see Santa Clara 102-64 = 166 total despite blowout). Garbage time is unpredictable.

---

## STAY AWAY TRIGGERS (ANY of these = NO BET)
- Only 1 ref listed (incomplete crew)
- Original O/U 160+ and considering under
- Blowout game without blowout-proof evidence
- No ref data available
- Mixed signals (neutral layers)
- Live bet without checking original full game line
- Shooting regression alone without crew confirmation
- Under play without strong Layer 2–6 confirmation (crew signal alone is insufficient)

## PULL THE TRIGGER — PRE-GAME OVER
- Two STRONG HIGH refs (>36.5) on same crew
- Crew average 36.5+
- Full game O/U below what that crew typically generates
- Combined team PPG exceeds or equals the line
- Fast pace teams + high FTR + high-foul crew
- 4+ layers all pointing same direction

## PULL THE TRIGGER — PRE-GAME UNDER
- 2+ STRONG LOW refs (<32.0) on same crew
- Crew average below 32.0
- Both teams defensive-minded, slow pace, low combined PPG
- Original O/U is not inflated (check line first)
- Screen for three-point override (Layer 4)
- **5-6 layers required — do not bet at 4 layers for under plays**
- NOTE: 24% blowup rate even at <32.0 threshold over 21 signals

---

## 7-DAY BACKTESTING RESULTS (Dec 6, 2025 — Mar 7, 2026)
*722+ games analyzed across 7 sample days*

### Over Signal Performance (>36.5 crew, 2+ STRONG HIGH)

| Day | Signals | Avg Total | 155+ Hit | <145 Miss |
|-----|---------|-----------|----------|-----------|
| Dec 6, 2025 | 4 | 147.0 | 25% | 25% |
| Jan 3, 2026 | 8 | 158.5 | 62% | 25% |
| Jan 17, 2026 | 4 | 145.2 | 25% | 50% |
| Feb 4, 2026 | 5 | 163.4 | 80% | 0% |
| Feb 21, 2026 | 9 | 164.1 | 78% | 0% |
| Feb 28, 2026 | 8 | 165.6 | 88% | 0% |
| Mar 7, 2026 | 4 | 170.5 | 100% | 0% |
| **TOTAL** | **42** | **160.4** | **69%** | **12%** |

**Key finding: Over signal strengthens as season progresses. Dec–Jan noisier (early season, weaker schedules). Feb–Mar signal is dominant — 80–100% hit rate in final 3 sample days.**

### Under Signal Performance (<32.0 crew, 2+ STRONG LOW)
*21 signals across 7 days*

| Result | Count | % |
|--------|-------|---|
| ✅ Hit (<145) | 10 | 48% |
| ➖ Mid (145–154) | 6 | 29% |
| ⚠️ Blowup (155+) | 5 | 24% |
| **Avg total** | | **143.8** |

**Key finding: Under signal generates a below-juice hit rate (48% vs 52.4% needed). The 24% blowup rate means losses are often painful. Under signal is NOT a standalone bet — requires maximum layer confirmation and should be treated as the weakest play type in the system.**

### Under Band Analysis (Current system <34.1 threshold — WHY WE TIGHTENED)
| Band | Games | <145 Hit | Blowup Rate |
|------|-------|----------|-------------|
| < 31.0 | 5 | 60% | 0% |
| 31.0–31.9 | 18 | 39% | 33% |
| 32.0–32.4 | 29 | 52% | 31% |
| 32.5–32.9 | 39 | 38% | **46%** |
| 33.0–33.4 | 48 | 52% | 25% |
| 33.5–34.1 | 59 | 39% | **44%** |

The 32.5–32.9 and 33.5–34.1 bands are the worst in the system — 44–46% blowup rates. These are not bettable. Threshold tightened to <32.0 to eliminate them.

---

## HISTORICAL AUDIT FINDINGS (February 28, 2026 — 133 games)

### Foul Correlation by Crew Average
| Crew Avg | Games | Avg Fouls | Avg Total |
|----------|-------|-----------|-----------|
| 36.5+ (Elite) | 8 | **38.4** | 155.1 |
| 36.0–36.49 | 11 | **37.2** | 151.7 |
| 35.5–35.99 | 20 | 34.6 | 151.6 |
| 35.0–35.49 | 24 | 32.6 | 146.2 |
| 34.5–34.99 | 17 | 32.8 | 152.4 |
| 34.0–34.49 | 17 | 31.6 | 139.2 |
| Below 34.0 | 41 | 32.9 | 147.6 |

**Key findings:**
1. **Foul correlation holds at the top**: 36.5+ crews = 38.4 fouls (+5.2 above baseline). Signal disappears below 35.0.
2. **Total points correlation is weaker**: Only 7-point gap between elite and low crews. Layers 2–6 are essential.
3. **High fouls ≠ always high scoring**: Wyoming/Air Force had 40 fouls but only 128 total. Layer 3 is a HARD filter.
4. **Under signal less reliable**: Bellarmine/Austin Peay had 32.6 crew but produced 208 total and 51 fouls.
5. **Power conference note**: Power conf games average 34.6 crew avg historically — books may price some in, but signal still applies when crew exceeds thresholds.

### Anomalies to Remember
- Elite crew + bad offensive teams = fouls but no points (Wyoming/Air Force: 36.5 crew, 40 fouls, 128 total)
- Low crew + run-and-gun teams = low fouls but high points (Wake Forest/Syracuse: 32.5 crew, 30 fouls, 171 total)
- Extreme outlier: Bellarmine/Austin Peay: 32.6 crew, 51 fouls, 208 total — low crew signal completely overridden

---

## REFINEMENT UNDER REVIEW (Not Official Yet)

**Elite Crew Override**: For crews averaging 36.5+ with 2+ STRONG HIGHs, Layer 1 may override ONE contradicting layer. Evidence: LBSU/CSUB had 36.9 crew, LBSU had lowest FT rate in Big West (Layer 2 said pass), but crew generated 47 fouls and game went 175. Need more data before making this a hard rule.

**Seasonal Weighting**: Over signal data shows Dec–Jan is noticeably noisier than Feb–Mar. Early season games (Nov–Jan) may warrant requiring 5+ layers instead of 4. Under review.

---

## DATABASE CORRECTIONS (Pedigree ≠ Foul Rate)

| Ref | Pedigree Assessment | Actual Database | Correction |
|-----|-------------------|-----------------|------------|
| Randy McCall | "Big 12 elite" | 34.5 NEUTRAL | NOT elevated |
| Evon Burroughs | "Big East, elevated" | 33.8 NEUTRAL | NOT elevated |
| James Breeding | "Major conf, above mid-major" | 35.4 NEUTRAL | NOT elevated |
| Kipp Kissinger | Initially "#5 nationally" | 33.3 NEUTRAL (new threshold) | Always verify in database |

**RULE: Always trust the database number over pedigree/reputation research.**

---

## ALL-TIME LIVE BET RESULTS

### Session 1 — February 26–27, 2026 (5-2)
| # | Game | Bet | Result | Margin | Notes |
|---|------|-----|--------|--------|-------|
| 1 | UVU/Tarleton | Over 141.5 | ✅ WIN | +9.5 (151) | Padilla + Kissinger crew |
| 2 | Missouri St/LA Tech | Over 136.5 | ✅ WIN | +5.5 (142) | Rorke/Shannon crew |
| 3 | NAU/Idaho | Under 77 2H | ✅ WIN | -7 (70) | Low Big Sky crew + regression |
| 4 | Wichita +1 2H | Spread | ✅ WIN | +1 | Memphis foul trouble |
| 5 | Jackson St/Prairie View | Under 148.5 | ✅ WIN | Under | SWAC crew |
| 6 | LBSU/Cal Poly | Under 87 2H | ❌ LOSS | +19 | Ignored 162.5 original line |
| 7 | CSUB/UCSD | Under 78.5 2H | ❌ LOSS | +7.5 | Ignored Ronald Jessamy 40 pts |

### Session 2 — February 28, 2026 (4-0)
| # | Game | Bet | Result | Margin | Fouls | Crew Avg |
|---|------|-----|--------|--------|-------|----------|
| 8 | Weber St @ Idaho St | Over 153.5 | ✅ WIN | +2.5 (156) | 28 | 38.23 |
| 9 | Oregon St @ Santa Clara | Over 153.5 | ✅ WIN | +11.5 (165) | 44 | 35.90 |
| 10 | Oral Roberts @ KC | Over 147.5 | ✅ WIN | +16.5 (164) | 39 | 35.90 |
| 11 | Nevada @ UNLV | Over 150.5 | ✅ WIN | +17.5 (168) | 46 | 36.10 |

**Combined: 9-2 (81.8%)**

### Lessons from Losses
- Both losses came from 2H bets ignoring the original full game O/U line
- LBSU/Cal Poly: opened at 162.5 — way too high-scoring for under
- CSUB/UCSD: dominant inside scorer (Ronald Jessamy, 40 pts) regardless of crew
- Rules established: always check full game O/U first, account for individual star scorers

---

## WORKFLOW FOR EACH GAME

1. Get ref crew assignment from RefMetrics (or user provides)
2. Look up each ref in the database CSV
3. Calculate crew average and count STRONG HIGH / STRONG LOW anchors
4. If NEUTRAL crew (32.0–36.5) or mixed signals → PASS immediately
5. Check full game O/U line
6. Pull team stats: PPG, fouls/game, FTR, pace
7. Check key player matchups (star scorers, foul-drawers)
8. Assess coaching style and venue/situation
9. Run 6-layer model — only bet if 4+ layers align (5+ for under plays)
10. Assign confidence: 5-6 layers = full unit, 4 layers = half unit (over only)
11. Fewer than required layers → PASS regardless of how strong Layer 1 looks

---

## QUICK REFERENCE — WHAT MAKES A PLAY

**STRONGEST OVER SETUP:**
- 2+ STRONG HIGH refs (>36.5) on any game
- Crew average 36.5+
- Both teams combine for PPG at or above the O/U line
- At least one team with high FTR (top 25%)
- Fast pace or aggressive coaching style
- 4+ layers aligned
- Edge is strongest Feb–Mar; Dec–Jan requires more caution

**STRONGEST UNDER SETUP:**
- 2+ STRONG LOW refs (<32.0) on any crew
- Crew average below 32.0
- Both teams defensive-minded, slow pace
- Low combined PPG relative to line
- Screen for three-point override first (Layer 4)
- **5-6 layers required — 4 layers is not enough for under bets**
- 24% blowup rate even at this threshold — size down accordingly

**AUTOMATIC PASS:**
- NEUTRAL crew average (32.0–36.5)
- Mixed crew (one STRONG HIGH + one STRONG LOW)
- Incomplete crew (only 1 ref listed)
- Under play with only 4 layers aligned
- Fewer than 4 layers aligned for any play

---

## INSTRUCTIONS FOR NEW CHAT
1. Upload this document
2. Upload the referee database CSV (refmetrics_ncaa_men_foul_leaders.csv)
3. Tell Claude what crews are assigned to today's games
4. Claude will look up each ref, run the 6-layer model, and identify plays
5. **All conferences eligible — run every game through the full model**
6. Trust the database numbers, not pedigree/reputation
7. Discipline > volume — fewer plays, higher confidence
8. Over signal is the primary edge. Under signal requires maximum confirmation.
