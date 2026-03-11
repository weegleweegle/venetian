# NCAAB REFEREE BETTING SYSTEM — MASTER DOCUMENT V3
## Updated March 11, 2026 — Power Conference Rule ELIMINATED
## ALL-TIME RECORD: 9-2

---

## HOW TO USE THIS DOCUMENT
Upload this file + the referee database spreadsheet (NCAAB_Ref_Database_All_1451_Ranked.xlsx) to any new Claude chat. Claude will have full context of the system, rules, results, and methodology. Ask Claude to grade any crew assignment using the 6-layer model below.

---

## SYSTEM OVERVIEW
Pre-game O/U betting system exploiting referee foul-calling tendencies that books don't fully price. 6-layer model requiring 4+ aligned layers before betting. **ALL conferences are eligible — mid-major, power, and everything in between.** Mid-major games retain the primary edge due to books' blind spot on crew assignments, but power conference games are NOT auto-excluded and must be run through all 6 layers.

---

## DATABASE
- **1,451 referees** ranked by career total fouls per game (2010-2026, minimum 5 games)
- File: NCAAB_Ref_Database_All_1451_Ranked.xlsx
- Sheet: "All Refs by Fouls Per Game"
- Columns: Rank, Referee, Total Fouls/Gm, Home Fouls, Away Fouls, Foul Diff, Games, Tier
- Career average IS the grading number — no regression filters, no recent-season adjustments
- Color-coded by tier in spreadsheet

---

## TIER SYSTEM

| Tier | Range | Description |
|------|-------|-------------|
| STRONG HIGH | >36.1 | Top 20% — primary OVER signal |
| HIGH | 34.8-36.1 | Above average |
| NEUTRAL | 33.5-34.8 | No edge — PASS |
| LOW | 32.1-33.5 | Below average |
| STRONG LOW | <32.1 | Bottom 20% — UNDER signal (less reliable) |

**Database stats: Mean 34.09 | Median 34.20 | Std Dev 2.34**

---

## THE 6-LAYER MODEL

### Layer 1 — Referee Pedigree (THE FOUNDATION)
- Look up all 3 refs in database
- Calculate crew average
- Count STRONG HIGH vs LOW anchors
- **36.0+ crew with 2+ STRONG HIGH = strongest OVER signal**
- **Below 33.0 crew with 2+ LOW = UNDER signal (but less reliable)**
- Mixed crew (one high, one low) = NO BET
- NEUTRAL crew average = NO BET

### Layer 2 — Team Foul Profile
- Fouls committed per game (both teams)
- Free throw rate (FTR) and FTA per game
- High FTR + high-foul crew = free throw parade
- Low FTR can be overridden by truly elite crews (36.5+) — see Refinements section
- Check KenPom or team stats for FTR data

### Layer 3 — Pace & Scoring (HARD REQUIREMENT)
- Combined PPG for both teams
- Compare combined PPG to the O/U line
- If combined PPG already EXCEEDS the line = strong over signal
- If combined PPG is BELOW the line by 10+ = caution
- **CRITICAL: Bad offensive teams can have 40+ fouls and still stay under (Wyoming/Air Force lesson: 40 fouls, 128 total). Layer 3 MUST confirm.**

### Layer 4 — Player Matchups
- Dominant inside scorers who draw fouls (check for 18+ PPG players)
- Aggressive ball handlers who attack the rim
- Foul-prone defenders
- Star players shooting high FT% = converts ref's fouls into actual points
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

### Tier 1 (Highest) — Pre-game Over/Under
Based on ref pedigree. Books price totals on team stats but don't fully account for referee pedigree — this is the inefficiency exploited.

### Tier 2 (Good) — 2H Point Spread
Based on halftime box score. Better team shooting below average + opponent foul trouble + competitive game within 8 points.

### Tier 3 (Only if strong signal) — 2H Over/Under
Only when BOTH original full game O/U AND ref crew confirm same direction.

---

## CRITICAL RULES

### Mid-Major Blind Spot (PRIMARY EDGE — still applies, no longer a restriction)
Mid-major conferences (Big West, WAC, Summit, Big Sky, SWAC, OVC, WCC non-Gonzaga games, etc.) remain the highest-edge environment — books are most blind to crew assignments here. Power conferences (SEC, Big Ten, Big 12, ACC, Big East, A-10, etc.) are eligible for full analysis. Books may partially price in elite crews for high-profile power conf games, so note that context in Layer 1 analysis — but do NOT auto-pass. Run all 6 layers and let the model decide.

### 2-Ref Rule
If only 2 of 3 refs assigned: if BOTH grade STRONG HIGH or BOTH STRONG LOW, treat crew as confirmed. Otherwise = incomplete, PASS.

### Always Check Full Game O/U FIRST
Before ANY bet, check what books opened the game at. If original O/U is 160+ stay away from under. Gap between original line and crew tendency = where edge lives.

### Never Bet Incomplete Crews
If third ref is TBD, do not bet. Exception: 2-Ref Rule above.

### Blowout Awareness
Big spreads (15+) can still produce overs IF the favorite scores heavily regardless (see Santa Clara 102-64 = 166 total despite 38-point blowout). But garbage time is unpredictable.

---

## STAY AWAY TRIGGERS (ANY of these = NO BET)
- Only 2 refs listed without 2-Ref Rule applying
- Original O/U 160+ and considering under
- Blowout game without blowout-proof evidence
- No ref data available
- Mixed signals (neutral layers)
- Live bet without checking original full game line
- Shooting regression alone without crew confirmation

## PULL THE TRIGGER — PRE-GAME OVER
- Two elite refs (STRONG HIGH) paired on same crew
- Crew average 36.0+
- Full game O/U below what that crew typically generates
- Combined team PPG exceeds or equals the line
- Fast pace teams + high FTR + high-foul crew
- 4+ layers all pointing same direction

## PULL THE TRIGGER — PRE-GAME UNDER
- Low-pedigree crew (below 33.0, 2+ LOW/STRONG LOW)
- Both teams defensive-minded
- Slow pace + low FTR
- Screen for three-point override (Layer 4)
- 4+ layers all pointing same direction
- NOTE: Under signal is LESS reliable than over signal per 133-game audit

---

## 133-GAME AUDIT FINDINGS (February 28, 2026)

### Foul Correlation by Crew Average
| Crew Avg | Games | Avg Fouls | Avg Total |
|----------|-------|-----------|-----------|
| 36.5+ (Elite) | 8 | **38.4** | 155.1 |
| 36.0-36.49 | 11 | **37.2** | 151.7 |
| 35.5-35.99 | 20 | 34.6 | 151.6 |
| 35.0-35.49 | 24 | 32.6 | 146.2 |
| 34.5-34.99 | 17 | 32.8 | 152.4 |
| 34.0-34.49 | 17 | 31.6 | 139.2 |
| Below 34.0 | 41 | 32.9 | 147.6 |

**Key findings:**
1. **Foul correlation holds at the top**: 36.5+ crews = 38.4 fouls (+5.2 above baseline). Signal disappears below 35.0.
2. **Total points correlation is weaker**: Only 7-point gap between elite and low crews. Layers 2-6 are essential.
3. **High fouls ≠ always high scoring**: Wyoming/Air Force had 40 fouls but only 128 total (bad offensive teams). Layer 3 is a HARD filter.
4. **Under signal less reliable**: Bellarmine/Austin Peay had 32.6 LOW crew but produced 208 total and 51 fouls. Low crews can blow up.
5. **Power conference note**: Power conf games average 34.6 crew avg historically — books may price some of this in, but the signal still applies when crew exceeds thresholds.

### Anomalies to Remember
- Elite crew + bad offensive teams = fouls but no points (Wyoming/Air Force: 36.5 crew, 40 fouls, 128 total)
- Low crew + run-and-gun teams = low fouls but high points (Wake Forest/Syracuse: 32.5 crew, 30 fouls, 171 total)
- Extreme outlier: Bellarmine/Austin Peay: 32.6 crew, 51 fouls (!), 208 total — low crew completely ignored

---

## REFINEMENT UNDER REVIEW (Not Official Yet)

**Elite Crew Override**: For crews averaging 36.5+ with 2+ STRONG HIGHs, Layer 1 may override ONE contradicting layer. Evidence: LBSU/CSUB had 36.9 crew, LBSU had lowest FT rate in Big West (Layer 2 said pass), but crew generated 47 fouls and game went 175. The foul volume was so extreme it overrode the low FT rate. Need more data before making this a hard rule.

---

## DATABASE CORRECTIONS (Pedigree ≠ Foul Rate)
These refs were initially classified by conference pedigree research but their actual database numbers differ:

| Ref | Pedigree Assessment | Actual Database | Correction |
|-----|-------------------|-----------------|------------|
| Randy McCall | "Big 12 elite" | 34.5 NEUTRAL (1,245 gm) | NOT elevated |
| Evon Burroughs | "Big East, elevated" | 33.8 NEUTRAL (862 gm) | NOT elevated |
| James Breeding | "Major conf, above mid-major" | 35.4 HIGH (1,063 gm) | HIGH not STRONG HIGH |
| Kipp Kissinger | Initially "#5 nationally" | 33.3 LOW (varies by source) | Always verify in database |

**RULE: Always trust the database number over pedigree/reputation research.**

---

## ALL-TIME RESULTS

### Session 1 — February 26-27, 2026 (5-2)
| # | Game | Bet | Result | Margin | Notes |
|---|------|-----|--------|--------|-------|
| 1 | UVU/Tarleton | Over 141.5 | ✅ WIN | +9.5 (151) | Padilla + Kissinger crew |
| 2 | Missouri St/LA Tech | Over 136.5 | ✅ WIN | +5.5 (142) | Rorke/Shannon SEC/Pac-12 crew |
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
- Both losses came from 2H bets that ignored the original full game O/U line
- LBSU/Cal Poly: opened at 162.5 — way too high-scoring for under
- CSUB/UCSD: dominant inside scorer (Ronald Jessamy, 40 pts) generated massive FT volume regardless of crew
- Established rules: always check full game O/U first, account for individual star scorers

---

## WORKFLOW FOR EACH GAME

1. Get ref crew assignment from RefMetrics (or user provides)
2. Look up each ref in the 1,451-ref database spreadsheet
3. Calculate crew average and count STRONG HIGH / LOW anchors
4. If NEUTRAL crew or mixed signals → PASS immediately
5. Check full game O/U line
6. Pull team stats: PPG, fouls/game, FTR, pace
7. Check key player matchups (star scorers, foul-drawers)
8. Assess coaching style and venue/situation
9. Run 6-layer model — only bet if 4+ layers align same direction
10. Assign confidence: 5-6 layers = full unit, 4 layers = half unit
11. If fewer than 4 layers align → PASS regardless of how strong Layer 1 looks

---

## QUICK REFERENCE — WHAT MAKES A PLAY

**STRONGEST OVER SETUP:**
- 2+ STRONG HIGH refs on any game (any conference)
- Crew average 36.0+
- Both teams combine for PPG at or above the O/U line
- At least one team with high FTR (top 25%)
- Fast pace or aggressive coaching style
- Example: Nevada @ UNLV — Corral (38.2) + UNLV allows most FTA in nation + Nevada top 5% FTA

**STRONGEST UNDER SETUP:**
- 2+ LOW/STRONG LOW refs
- Crew average below 33.0
- Both teams defensive-minded, slow pace
- Low combined PPG relative to line
- Screen for three-point override first
- NOTE: Under plays are inherently less reliable

**AUTOMATIC PASS:**
- NEUTRAL crew average (33.5-34.8)
- Mixed crew (one STRONG HIGH + one LOW)
- Incomplete crew (TBD third ref without 2-Ref Rule)
- Fewer than 4 layers aligned

---

## INSTRUCTIONS FOR NEW CHAT
1. Upload this document
2. Upload the referee database spreadsheet (NCAAB_Ref_Database_All_1451_Ranked.xlsx)
3. Tell Claude what crews are assigned to today's games
4. Claude will look up each ref, run the 6-layer model, and identify plays
5. **All conferences eligible — run every game through the full model**
6. Trust the database numbers, not pedigree/reputation
7. Discipline > volume — fewer plays, higher confidence
