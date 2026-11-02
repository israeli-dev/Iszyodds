
# Iszyodds V4.3.2 Cloud-Ready

## Chain: Ollama Local → Groq Cloud → API-Football VERIFIED → FLAGGED AVOID

### Local (your PC):
pip install -r requirements.txt --only-binary :all:
ollama run llama3.2  # in separate terminal
streamlit run app.py

### Cloud (Vercel / Streamlit Cloud / Render):
1. Push to GitHub
2. Add secrets in dashboard:
   API_FOOTBALL_KEY = "16c80ed1c0c3ed2413ec402568e4b11c"
   GROQ_API_KEY = "gsk_..."
3. Deploy - app auto-detects no Ollama, uses Groq Cloud + API-Football VERIFIED

### Safety:
- Ollama/Groq UNVERIFIED = 0.5% max stake
- API-Football VERIFIED = 2% SURE
- FLAGGED INADEQUATE = 0% AVOID - Bankroll Protected

### Files:
- .streamlit/secrets.toml - private keys (in .gitignore, never commit)
- team_ids.json - cache saves API calls
- flagged_matches.csv - auto logs inadequate
- prediction_history.csv - auto logs predictions
