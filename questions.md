# Questions — Integrating the 6-Layer Model into Venetian
## Based on NCAAB Ref System V5 (March 11, 2026)

## Data Sources

1. **Referee Database**: The system uses `refmetrics_ncaa_men_foul_leaders.csv` with 1,451 refs and their career fouls/game stats (columns: Referee, Total Fouls, Home Fouls, Away Fouls, Foul Diff, Total Games). Should Venetian import this CSV into SQLite on startup, or do you want to manually upload/update it? How often does this file get updated?

   > **Answer:** Import CSV into SQLite on startup. This is a static career-average file, not a live feed — no constant refreshing needed. Update manually at the end of each season when RefMetrics publishes new data. One import, query fast. Tier logic: STRONG HIGH >36.5 | NEUTRAL 32.0-36.5 | STRONG LOW <32.0.

2. **Team Stats (Layer 2 & 3)**: The model needs team fouls/game, FTR, FTA, combined PPG, and pace. Where should we pull these from — KenPom (requires subscription/scraping), ESPN team stats, or another source? Or will you provide these manually per game?

   > **Answer:** Manual input per game. KenPom scraping is fragile and requires a subscription. ESPN's API is inconsistent. With only 3-8 plays per day, pulling stats for 6-16 teams manually takes ~5 minutes. Build a clean input form per game with fields: Combined PPG (both teams), Fouls/game (both teams), FTR / FTA per game (both teams), Pace rating. Do not chase automation on this layer yet.

3. **Player Matchups (Layer 4)**: Checking for 18+ PPG scorers, foul-drawers, three-point volume (2.5+ threes/game for the three-point override screen) — should the app try to pull this automatically (e.g., from ESPN rosters/stats), or is this a manual input layer?

   > **Answer:** Manual input. The 18+ PPG check and three-point override screen (2.5+ threes/game) are quick lookups on ESPN or KenPom (~30 seconds per team). Automating this requires reliable roster and stats scraping that breaks constantly. Not worth the maintenance overhead.

4. **Coaching Style (Layer 5) & Venue/Situational (Layer 6)**: These seem qualitative. Should these be manual notes you add per game, or do you want some of this automated (e.g., detecting rivalry games, Senior Day, tournament implications)?

   > **Answer:** Manual notes and checkboxes. Coaching style is a one-time lookup per team that becomes internalized quickly. For situational flags, build a simple checkbox UI: Senior Day, Rivalry game, Tournament implications, Late season (Feb-Mar) — more physical, Power ref in mid-major game. Do not automate these — the qualitative judgment is part of the edge.

## Scoring & Output

5. **Automation Level**: How automated do you want the grading? Options:
   - **Fully manual**: Venetian just shows the ref crew + their V5 tiers (STRONG HIGH >36.5 / NEUTRAL 32.0–36.5 / STRONG LOW <32.0), and you run the model yourself
   - **Semi-auto**: Venetian auto-scores Layers 1-3 (ref pedigree, team foul profiles, pace/scoring) and you manually assess Layers 4-6
   - **Full auto**: Venetian tries to score all 6 layers and gives a confidence rating (FULL UNIT / HALF UNIT / PASS)

   > **Answer:** Semi-automatic. Auto-score Layer 1 completely (pure DB logic). Auto-score Layer 3 if user has input PPG (compare combined PPG to the line). Layers 2, 4, 5, and 6 are manual inputs. This delivers time savings where it matters without brittle scraping dependencies.
   >
   > | Layer | Auto/Manual | Notes |
   > |---|---|---|
   > | Layer 1 — Ref Pedigree | AUTO | DB lookup, crew avg, tier, signal direction |
   > | Layer 2 — Team Foul Profile | MANUAL | Fouls/game, FTR, FTA input form |
   > | Layer 3 — Pace & Scoring | AUTO (if PPG input) | Compare combined PPG to line |
   > | Layer 4 — Player Matchups | MANUAL | 18+ PPG, 3PT override check |
   > | Layer 5 — Coaching Style | MANUAL | Notes field |
   > | Layer 6 — Venue & Situational | MANUAL | Checkbox UI |

6. **O/U Lines**: Where do you get the opening O/U lines? Should the app pull these from somewhere, or will you input them manually?

   > **Answer:** Manual input for now. User pulls lines from their sportsbook and enters them directly into the game card. If the app scales, DraftKings and FanDuel both have semi-public odds endpoints worth exploring later.

7. **Over vs Under asymmetry**: V5 backtesting shows the over signal is significantly stronger than under (69% hit rate vs 48%). The system requires 5-6 layers for under plays vs 4+ for over. Should Venetian reflect this asymmetry in the UI (e.g., flag under plays with a caution indicator)?

   > **Answer:** Yes — reflect asymmetry explicitly in the UI. Backtesting (7-day sample, 722+ games): Over 18-6 (75.0%, +22.6% edge vs juice), Under 10-10 (50.0%, -2.4% at <32.0 threshold). Over signals display normally (HALF UNIT / FULL UNIT). Under signals always show a yellow caution badge and require 5-6 layers confirmed before grading as bettable. Require explicit confirmation of each layer before the system will grade an under. Under threshold (<32.0) is still under review with ongoing backtesting.

## Dashboard Integration

8. **Display**: Should the 6-layer analysis appear on the existing game cards in the dashboard, or as a separate "analysis" page? What should it look like — a simple grade (PASS / HALF UNIT / FULL UNIT) on each game card, or a detailed breakdown?

   > **Answer:** Simple grade on each game card (PASS / HALF UNIT / FULL UNIT + direction) visible at a glance. Tap/click to expand the full 6-layer breakdown. Game card layout:
   > - **Top line:** Teams + tip time
   > - **Second line:** Crew avg + tier badge (STRONG HIGH / NEUTRAL / STRONG LOW)
   > - **Third line:** Grade badge — PASS (grey) / HALF UNIT OVER (green) / FULL UNIT OVER (dark green) / UNDER plays with yellow caution
   > - **Expanded view:** Full 6-layer checklist with user inputs and auto-scored fields

9. **Notifications**: Should Discord/Slack notifications also send the betting analysis (e.g., "FULL UNIT OVER — crew avg 37.2"), or keep notifications strictly about ref assignments?

   > **Answer:** Include betting analysis in notifications — but only for HALF UNIT and FULL UNIT plays. PASS games should never trigger a notification. Format: `FULL UNIT OVER — crew avg 37.2 | line 141.5 | 5/6 layers aligned`. One message per play, sent when the user finalizes the grade. Discord and/or Slack.

## Seasonal Consideration

10. **Seasonal weighting**: V5 notes that Dec–Jan over signals are noisier than Feb–Mar (25-62% hit vs 78-100%). Should the app apply stricter layer requirements early season (e.g., require 5+ layers Nov–Jan)?

    > **Answer:** Yes — implement date-based layer requirements. Nov 1 - Jan 31: 5+ layers required for over plays (25-62% hit rate, noisier early season). Feb 1 - Mar 31: 4+ layers standard threshold (78-100% hit rate, signal dominant). Simple date check on game date. Surface the active mode in the UI so the user always knows which requirement is in effect. Under plays require 5-6 layers year-round regardless of season — seasonal weighting only affects over play requirements.
