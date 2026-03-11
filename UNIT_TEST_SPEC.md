# Venetian — Unit Test Spec
## Target: `analysis.py` scoring engine

All tests use an in-memory SQLite database with `row_factory = sqlite3.Row`. A shared fixture creates the `referee_foul_leaders` table and seeds it with controlled test data (not the full 1,451-ref CSV). Tests are pure logic — no network, no Flask, no scraping.

**Framework:** `pytest` (add to `requirements.txt`)

---

## 1. Fixtures

### `db` — In-memory SQLite connection
- Creates `:memory:` database with `row_factory = sqlite3.Row`
- Creates `referee_foul_leaders` table (same schema as production)
- Seeds with a controlled set of test referees covering all tiers:

| Name | Fouls/Game | Tier | Games |
|------|-----------|------|-------|
| High Alice | 38.0 | STRONG_HIGH | 100 |
| High Bob | 37.5 | STRONG_HIGH | 80 |
| Neutral Charlie | 35.0 | NEUTRAL | 120 |
| Neutral Dana | 33.0 | NEUTRAL | 90 |
| Low Eddie | 31.0 | STRONG_LOW | 60 |
| Low Fran | 30.5 | STRONG_LOW | 70 |
| Boundary High | 36.5 | NEUTRAL | 50 |
| Boundary Low | 32.0 | NEUTRAL | 50 |
| Edge Over | 36.51 | STRONG_HIGH | 40 |
| Edge Under | 31.99 | STRONG_LOW | 40 |
| Mike Johnson Jr. | 35.5 | NEUTRAL | 30 |

---

## 2. `_assign_tier` Tests

| # | Input (fpg) | Expected Tier | Why |
|---|-------------|---------------|-----|
| 2.1 | 38.0 | STRONG_HIGH | Well above 36.5 |
| 2.2 | 36.51 | STRONG_HIGH | Just above threshold |
| 2.3 | 36.5 | NEUTRAL | Threshold is `> 36.5`, not `>=` |
| 2.4 | 35.0 | NEUTRAL | Mid-range |
| 2.5 | 32.0 | NEUTRAL | Threshold is `< 32.0`, not `<=` |
| 2.6 | 31.99 | STRONG_LOW | Just below threshold |
| 2.7 | 28.0 | STRONG_LOW | Well below 32.0 |

---

## 3. `_normalize_name` Tests

| # | Input | Expected | Why |
|---|-------|----------|-----|
| 3.1 | `"John Smith"` | `"john smith"` | Basic lowercase |
| 3.2 | `"  John  Smith  "` | `"john smith"` | Whitespace collapse + strip |
| 3.3 | `"John Smith Jr."` | `"john smith"` | Strip Jr. suffix |
| 3.4 | `"John Smith Jr"` | `"john smith"` | Strip Jr without period |
| 3.5 | `"John Smith Sr."` | `"john smith"` | Strip Sr. suffix |
| 3.6 | `"John Smith III"` | `"john smith"` | Strip III suffix |
| 3.7 | `"John Smith II"` | `"john smith"` | Strip II suffix |
| 3.8 | `"John Smith IV"` | `"john smith"` | Strip IV suffix |

---

## 4. `import_csv` Tests

| # | Scenario | Assertions |
|---|----------|------------|
| 4.1 | Import a 3-row test CSV | Returns count=3, all 3 rows in DB with correct fpg and tier |
| 4.2 | CSV with empty referee name | Row is skipped, count excludes it |
| 4.3 | CSV with non-numeric Total Fouls | Row is skipped (fpg=None) |
| 4.4 | Re-import same CSV (upsert) | Count still 3, values updated (not duplicated) |
| 4.5 | Missing CSV file | Returns 0, no crash |

---

## 5. `lookup_ref` Tests

| # | Input Name | Expected | Why |
|---|-----------|----------|-----|
| 5.1 | `"High Alice"` | Match (fpg=38.0) | Exact match |
| 5.2 | `"high alice"` | Match (fpg=38.0) | Case-insensitive |
| 5.3 | `"HIGH ALICE"` | Match (fpg=38.0) | All caps |
| 5.4 | `"Mike Johnson Jr."` | Match (fpg=35.5) | Suffix stripped for match |
| 5.5 | `""` | None | Empty string |
| 5.6 | `None` | None | None input |
| 5.7 | `"   "` | None | Whitespace-only |
| 5.8 | `"Nonexistent Ref"` | None | Not in DB |
| 5.9 | `"  High Alice  "` | Match | Leading/trailing whitespace |

---

## 6. `score_layer_1` Tests

### 6.1 — Strong OVER signal (ideal setup)
- Crew: High Alice (38.0), High Bob (37.5), Neutral Charlie (35.0)
- Expected: crew_avg ≈ 36.83, tier=STRONG_HIGH, strong_high=2, signal=OVER, aligned=1

### 6.2 — Strong UNDER signal
- Crew: Low Eddie (31.0), Low Fran (30.5), Neutral Dana (33.0)
- Expected: crew_avg ≈ 31.5, tier=STRONG_LOW, strong_low=2, signal=UNDER, aligned=1

### 6.3 — Neutral crew (all neutral refs)
- Crew: Neutral Charlie (35.0), Neutral Dana (33.0), Boundary High (36.5)
- Expected: crew_avg ≈ 34.83, signal=NEUTRAL, aligned=0

### 6.4 — Mixed crew (one high + one low)
- Crew: High Alice (38.0), Low Eddie (31.0), Neutral Charlie (35.0)
- Expected: signal=MIXED, aligned=0

### 6.5 — High avg with only 1 anchor
- Crew: High Alice (38.0), Neutral Charlie (35.0), Edge Over (36.51)
- Expected: crew_avg ≈ 36.50, signal depends on avg vs threshold; with avg=36.50 that's NOT > 36.5, so NEUTRAL

### 6.6 — Two-ref crew (valid)
- Crew: High Alice (38.0), High Bob (37.5), u2=""
- Expected: crew_avg=37.75, strong_high=2, signal=OVER, aligned=1

### 6.7 — One ref not in DB
- Crew: High Alice (38.0), High Bob (37.5), "Unknown Ref"
- Expected: crew_avg=(38.0+37.5)/2=37.75 (Unknown excluded from avg), ref_details includes NOT_FOUND entry

### 6.8 — All refs not in DB
- Crew: "Unknown A", "Unknown B", "Unknown C"
- Expected: l1_tier=NO_DATA, signal=NEUTRAL, aligned=0

### 6.9 — Empty crew (no refs at all)
- Crew: "", "", ""
- Expected: NO_DATA, no refs in details

### 6.10 — High avg, one anchor (weaker over signal)
- Crew: High Alice (38.0), Neutral Charlie (35.0), Boundary High (36.5)
- Expected: crew_avg ≈ 36.5, which is NOT > 36.5, so signal=NEUTRAL

---

## 7. `score_layer_3` Tests

| # | PPG | Line | Signal Dir | Expected ppg_vs_line | Expected aligned |
|---|-----|------|-----------|---------------------|-----------------|
| 7.1 | 150.0 | 145.0 | OVER | ABOVE | 1 |
| 7.2 | 150.0 | 145.0 | UNDER | ABOVE | 0 |
| 7.3 | 142.0 | 145.0 | OVER | CLOSE | 0 |
| 7.4 | 140.5 | 145.0 | OVER | CLOSE | 0 |
| 7.5 | 134.0 | 145.0 | OVER | BELOW_10 | 0 |
| 7.6 | 134.0 | 145.0 | UNDER | BELOW_10 | 1 |
| 7.7 | 145.0 | 145.0 | OVER | ABOVE | 1 (exact match = above) |
| 7.8 | None | 145.0 | OVER | None | 0 |
| 7.9 | 150.0 | None | OVER | None | 0 |
| 7.10 | 140.0 | 145.0 | NEUTRAL | CLOSE | 0 |

---

## 8. `_check_stay_away` Tests

| # | Scenario | Expected Reason |
|---|----------|----------------|
| 8.1 | Mixed signal | "Mixed crew (STRONG HIGH + STRONG LOW)" |
| 8.2 | Neutral signal | "Neutral crew average (32.0-36.5)" |
| 8.3 | Under signal + O/U line 165 | "O/U line 160+ with UNDER signal" |
| 8.4 | Under signal + O/U line 155 | None (no trigger) |
| 8.5 | Over signal + O/U line 165 | None (only triggers for under) |
| 8.6 | NO_DATA tier | "No referee data available" |
| 8.7 | Only 1 ref in details with data | "Incomplete crew (fewer than 2 refs with data)" |
| 8.8 | Valid OVER signal, 2+ refs, normal line | None |

---

## 9. `_get_layer_threshold` Tests

| # | Signal | Game Date | Expected Threshold |
|---|--------|-----------|-------------------|
| 9.1 | OVER | 2026-01-15 | 5 (early season) |
| 9.2 | OVER | 2026-01-31 | 5 (last day of early) |
| 9.3 | OVER | 2026-02-01 | 4 (first day of late) |
| 9.4 | OVER | 2026-03-15 | 4 (late season) |
| 9.5 | OVER | 2026-11-15 | 5 (Nov = early) |
| 9.6 | OVER | 2026-12-20 | 5 (Dec = early) |
| 9.7 | UNDER | 2026-01-15 | 5 (under always 5) |
| 9.8 | UNDER | 2026-02-15 | 5 (under always 5, even late season) |
| 9.9 | UNDER | 2026-03-15 | 5 (under always 5) |
| 9.10 | OVER | invalid date | 4 (default fallback) |

---

## 10. `compute_grade` Tests

### 10.1 — FULL UNIT OVER (5 layers, late season)
- Analysis: OVER signal, l1-l5 aligned, game_date=2026-03-01
- Expected: grade=FULL_UNIT, signal_direction=OVER, layers_aligned=5

### 10.2 — FULL UNIT OVER (6 layers)
- Analysis: OVER signal, all 6 aligned, game_date=2026-03-01
- Expected: grade=FULL_UNIT, layers_aligned=6

### 10.3 — HALF UNIT OVER (4 layers, late season)
- Analysis: OVER signal, l1-l4 aligned, game_date=2026-03-01
- Expected: grade=HALF_UNIT, layers_aligned=4

### 10.4 — PASS (3 layers, late season)
- Analysis: OVER signal, l1-l3 aligned, game_date=2026-03-01
- Expected: grade=PASS, layers_aligned=3

### 10.5 — Early season: 4 layers = PASS (not enough)
- Analysis: OVER signal, l1-l4 aligned, game_date=2026-01-15
- Expected: grade=PASS (needs 5 in early season)

### 10.6 — Early season: 5 layers = HALF UNIT
- Analysis: OVER signal, l1-l5 aligned, game_date=2026-01-15
- Expected: grade=HALF_UNIT

### 10.7 — UNDER: 4 layers = PASS (always needs 5)
- Analysis: UNDER signal, l1-l4 aligned, game_date=2026-03-01
- Expected: grade=PASS

### 10.8 — UNDER: 5 layers = HALF UNIT
- Analysis: UNDER signal, l1-l5 aligned, game_date=2026-03-01
- Expected: grade=HALF_UNIT

### 10.9 — UNDER: 6 layers = FULL UNIT
- Analysis: UNDER signal, all 6 aligned, game_date=2026-03-01
- Expected: grade=FULL_UNIT

### 10.10 — Stay-away trigger overrides layers
- Analysis: MIXED signal, all 6 layers aligned
- Expected: grade=PASS, stay_away_reason contains "Mixed"

### 10.11 — Neutral signal = PASS regardless of layers
- Analysis: NEUTRAL signal, all 6 layers aligned
- Expected: grade=PASS

### 10.12 — NO_DATA = PASS
- Analysis: l1_tier=NO_DATA, l1_signal=NEUTRAL
- Expected: grade=PASS, stay_away_reason contains "No referee data"

---

## 11. `get_seasonal_mode` Tests

| # | Date | Expected Mode | Expected Threshold |
|---|------|--------------|-------------------|
| 11.1 | 2026-01-15 | EARLY_SEASON | 5 |
| 11.2 | 2026-02-01 | LATE_SEASON | 4 |
| 11.3 | 2026-11-20 | EARLY_SEASON | 5 |
| 11.4 | 2026-12-01 | EARLY_SEASON | 5 |
| 11.5 | 2026-03-15 | LATE_SEASON | 4 |
| 11.6 | invalid | LATE_SEASON | 4 (default) |

---

## File Structure

```
venetian/
├── tests/
│   ├── __init__.py
│   ├── conftest.py        ← db fixture, test ref seeding
│   └── test_analysis.py   ← all tests above
├── requirements.txt       ← add pytest
```

## Running

```bash
pytest tests/ -v
```

---

## What's NOT Tested Here

- **Flask endpoints** (app.py) — these are integration tests, not unit tests. Could add later with Flask test client.
- **Scraper logic** (refmetrics_scraper.py) — requires network mocking, lower priority.
- **Frontend JS** — would need browser-level testing (Playwright/Selenium), very low priority.
- **Notification delivery** — would need webhook mocking.

The scoring engine in `analysis.py` is where logic bugs cause real money consequences. That's where tests matter most.
