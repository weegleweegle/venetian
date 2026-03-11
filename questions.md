# Questions — Integrating the 6-Layer Model into Venetian
## Based on NCAAB Ref System V5 (March 11, 2026)

## Data Sources

1. **Referee Database**: The system uses `refmetrics_ncaa_men_foul_leaders.csv` with 1,451 refs and their career fouls/game stats (columns: Referee, Total Fouls, Home Fouls, Away Fouls, Foul Diff, Total Games). Should Venetian import this CSV into SQLite on startup, or do you want to manually upload/update it? How often does this file get updated?

2. **Team Stats (Layer 2 & 3)**: The model needs team fouls/game, FTR, FTA, combined PPG, and pace. Where should we pull these from — KenPom (requires subscription/scraping), ESPN team stats, or another source? Or will you provide these manually per game?

3. **Player Matchups (Layer 4)**: Checking for 18+ PPG scorers, foul-drawers, three-point volume (2.5+ threes/game for the three-point override screen) — should the app try to pull this automatically (e.g., from ESPN rosters/stats), or is this a manual input layer?

4. **Coaching Style (Layer 5) & Venue/Situational (Layer 6)**: These seem qualitative. Should these be manual notes you add per game, or do you want some of this automated (e.g., detecting rivalry games, Senior Day, tournament implications)?

## Scoring & Output

5. **Automation Level**: How automated do you want the grading? Options:
   - **Fully manual**: Venetian just shows the ref crew + their V5 tiers (STRONG HIGH >36.5 / NEUTRAL 32.0–36.5 / STRONG LOW <32.0), and you run the model yourself
   - **Semi-auto**: Venetian auto-scores Layers 1-3 (ref pedigree, team foul profiles, pace/scoring) and you manually assess Layers 4-6
   - **Full auto**: Venetian tries to score all 6 layers and gives a confidence rating (FULL UNIT / HALF UNIT / PASS)

6. **O/U Lines**: Where do you get the opening O/U lines? Should the app pull these from somewhere, or will you input them manually?

7. **Over vs Under asymmetry**: V5 backtesting shows the over signal is significantly stronger than under (69% hit rate vs 48%). The system requires 5-6 layers for under plays vs 4+ for over. Should Venetian reflect this asymmetry in the UI (e.g., flag under plays with a caution indicator)?

## Dashboard Integration

8. **Display**: Should the 6-layer analysis appear on the existing game cards in the dashboard, or as a separate "analysis" page? What should it look like — a simple grade (PASS / HALF UNIT / FULL UNIT) on each game card, or a detailed breakdown?

9. **Notifications**: Should Discord/Slack notifications also send the betting analysis (e.g., "FULL UNIT OVER — crew avg 37.2"), or keep notifications strictly about ref assignments?

## Seasonal Consideration

10. **Seasonal weighting**: V5 notes that Dec–Jan over signals are noisier than Feb–Mar (25-62% hit vs 78-100%). Should the app apply stricter layer requirements early season (e.g., require 5+ layers Nov–Jan)?
