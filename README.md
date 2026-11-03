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

## Match picker (tab 🎯 Simple Match)

Pick instead of typing:
- 📅 **Today's matches** - live fixtures via `/fixtures?date=today`
- 🗓️ **Past match (date picker)** - finished games on any date (`status=FT/AET/PEN`)
- 🔮 **Future matches (next N days)** - upcoming fixtures via `/fixtures?from=&to=`

All filtered to popular leagues (PL, La Liga, Serie A, Bundesliga, Ligue 1, UCL...). Needs `API_FOOTBALL_KEY`.

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
