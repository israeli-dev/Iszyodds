# Iszyodds V4.3.2 Cloud-Ready

## Data modes (sidebar toggle 🧪)

- 🎲 **Lenient (default) - hallucination OK:** the chain *always* produces a prediction.
  Ollama/Groq may **estimate/fabricate** plausible stats (marked `llm-hallucinated`),
  API-Football real stats are preferred when the key is set, and if nothing works a
  deterministic **synthetic fallback** is used. Nothing is flagged; predictions on
  estimated data are UNVERIFIED and capped at **0.5% stake**.
- 🔒 **Strict:** old behaviour - Ollama → Groq → API-Football VERIFIED → FLAGGED AVOID 0%
  when data is inadequate.

Chain: Ollama Local → Groq Cloud → API-Football VERIFIED → (lenient: synthetic fallback, never flags)

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
