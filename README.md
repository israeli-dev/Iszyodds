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

## Engine (v4.4.0)
Genuine 80/20 blend = 80% season form (last 15 venue games) + 20% recent form (last N venue games)
when API data exists, else recent form only. SOT weighting, xG overperformance penalty, rest days,
opponent-adjusted Elo, home bonus 1.5, threshold 5.0 for SURE. Unreproducible accuracy claims removed -
measure real accuracy with the Backtest tab.

Chain: API-Football VERIFIED (strict) | lenient: Ollama/Groq estimates → API-Football → synthetic (all UNVERIFIED)

## Live match sources (no more hardcoded lists)

- **🎯 Simple Match (default: 📅 Today's matches, live)** - pick from today's real fixtures (incl. in-play games) via `/fixtures?date=today`; also Manual entry, 🗓️ Past match (date picker, finished games), 🔮 Future matches (next N days via `/fixtures?from=&to=`). Needs `API_FOOTBALL_KEY` in secrets.
- **🛡️ Safest 5 (Live)** - scans ALL fixtures for the chosen date (default today), predicts each, ranks by safety (SURE verified → SURE unverified → Double → LEAN by |diff|) and shows the top 5. The old hardcoded 5 are only a labelled **DEMO** fallback when no API key is set.
- **💬 Dynamic Request** - natural language: 'Newcastle vs Everton', 'predict Arsenal vs Chelsea?', 'bet on X against Y' → prediction; "today's matches" / 'tomorrow' / '2026-11-05' → that day's live fixture list.

All filtered to popular leagues (PL, La Liga, Serie A, Bundesliga, Ligue 1, UCL...). Live data needs `API_FOOTBALL_KEY`; without it, lenient mode still predicts on synthetic data (0.5% max, UNVERIFIED).

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
