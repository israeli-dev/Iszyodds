# Iszyodds V4.4.0 - Fixture-Bound, Integrity-First Predictions

Implements the 3-stage audit: (1) live fixture binding, (2) genuine data integrity, (3) anti-leakage backtesting.

## Data modes (sidebar toggle 🧪) - STRICT is the default

- 🔒 **Strict (default):** API-Football genuine data only - the LLM is **never** used for statistics.
  No genuine data → FLAGGED, 0% stake ("no genuine data, no prediction").
- 🎲 **Lenient:** Ollama/Groq may *estimate* stats (`llm-hallucinated`) or a deterministic
  synthetic fallback is used - predictions are always marked **UNVERIFIED, max 0.5% stake,
  never labelled verified**.

## Stage 1 - every prediction is bound to a real fixture
Selected fixture's **ID, league, kickoff in UTC and Africa/Lagos (WAT), status** are shown with the
prediction and stored in `prediction_history_v2.csv` together with model version, as_of, data
sources, data quality, prediction, stake, diff, data snapshots and (for finished matches) the result.

## Stage 2 - no fabricated statistics
API games are `api-football-verified` only when all required per-match stats (shots, SOT,
possession, passes) are genuinely returned by `/fixtures/statistics`. Missing fields stay `None`,
are listed in `missing_fields`, and shown in the UI - never replaced with invented defaults.
Partial games are `api-football-partial` → UNVERIFIED. LLM (Groq/Ollama) is used for **explanations only**.

## Stage 3 - no future-data leakage + backtesting
When a fixture is selected, form data is fetched with `before=<kickoff>` (API `to=` param + hard
cutoff), so past matches are predicted only from pre-kickoff information. The **🔬 Backtest** tab
runs a past date, predicts each finished match that way, and scores picks vs actual results.
The Logs tab can fill in H/D/A results for finished fixtures via `/fixtures?id=`.

## Engine
Genuine 80/20 blend = 80% season form (last 15 venue games) + 20% recent form (last N venue games)
when API data exists, else recent form only. SOT weighting, xG overperformance penalty, rest days,
opponent-adjusted Elo, home bonus 1.5, threshold 5.0 for SURE. Unreproducible accuracy claims removed -
measure real accuracy with the Backtest tab.

Chain: API-Football VERIFIED (strict) | lenient: Ollama/Groq estimates → API-Football → synthetic (all UNVERIFIED)

## Live match sources (no more hardcoded lists; works on any day)

- "Today" is computed in **Africa/Lagos time** (`lagos_today()`) because API-Football dates are UTC - so late-evening Lagos matches never land on the wrong day.
- **🎯 Simple Match (default: 📅 Today's matches)** - shows **all unplayed matches of today, including live ones** (NS/1H/HT/2H/LIVE; finished games excluded) via `/fixtures?date=today`; also Manual entry, 🗓️ Past match (date picker, finished games only), 🔮 Future matches (next N days via `/fixtures?from=&to=`).
- **🛡️ Safe matches** - scans up to **10 matches of the chosen day** (default today; matches of that day that are not finished - for today/future only unplayed + live, finished excluded), predicts each with data as of its kickoff, and ranks by **data quality first** (verified API > partial API > LLM > synthetic = lowest hallucination probability), then by safety. In lenient mode the fixture list itself comes from the LLM when the API is down. If the exact API day is genuinely empty it **auto-widens +2 days**. API errors/quota (HTTP errors or 200-with-`errors`) are reported explicitly instead of "no fixtures found". A clearly labelled **SAMPLE** list is only a fallback when no data source works at all. Fixture responses are cached 5 min to protect the API quota.
- **💬 Ask** - 'Newcastle vs Everton', 'predict Arsenal vs Chelsea?', 'bet on X against Y' → prediction; "today's matches" / 'tomorrow' / '2026-11-05' → that day's fixture list (API in strict, LLM in lenient); **'predict any 3 matches on 2026-11-05' / 'top 5 games tomorrow' → actually runs predictions for the top N matches of that day** (`wants_prediction` + `parse_count`).

All filtered to popular leagues (PL, La Liga, Serie A, Bundesliga, Ligue 1, UCL...). Live data needs `API_FOOTBALL_KEY`; without it, strict mode FLAGs while lenient mode still works end-to-end on LLM data (UNVERIFIED, 0.5% max).

### Local (your PC):
pip install -r requirements.txt --only-binary :all:
ollama run llama3.2  # in separate terminal
streamlit run app.py

### Cloud (Vercel / Streamlit Cloud / Render):
1. Push to GitHub
2. Add secrets in dashboard:
   API_FOOTBALL_KEY = "..."
   GROQ_API_KEY = "gsk_..."
3. Deploy - app auto-detects no Ollama, uses Groq Cloud + API-Football VERIFIED

### Safety:
- Ollama/Groq UNVERIFIED (incl. hallucinated) = 0.5% max stake
- API-Football VERIFIED = 2% SURE
- FLAGGED INADEQUATE (strict mode only) = 0% AVOID - Bankroll Protected

### Files:
- .streamlit/secrets.toml - private keys (in .gitignore, never commit)
- team_ids.json - cache saves API calls
- flagged_matches.csv - auto logs inadequate (strict mode)
- prediction_history.csv - auto logs predictions

### ⚠️ Security note:
An API-Football key was previously committed in this README - rotate it in the API-Football dashboard and keep keys only in secrets/env vars.
