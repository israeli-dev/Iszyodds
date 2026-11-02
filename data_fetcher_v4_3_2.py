import pathlib, json, requests, csv, re, random, hashlib
from datetime import datetime, timedelta

CACHE_FILE = pathlib.Path("team_ids.json")

# Logging
FLAGGED_LOG = pathlib.Path("flagged_matches.csv")
PREDICTION_LOG = pathlib.Path("prediction_history.csv")

API_BASE = "https://v3.football.api-sports.io"

# Popular leagues used by the match picker (today / past / future)
POPULAR_LEAGUES = {
    2: "Champions League",
    3: "Europa League",
    4: "Conference League",
    39: "Premier League",
    78: "Bundesliga",
    135: "Serie A",
    140: "La Liga",
    61: "Ligue 1",
    88: "Eredivisie",
    203: "Süper Lig",
    71: "Brasileirão",
    13: "Libertadores",
}

FINISHED_STATUSES = {"FT", "AET", "PEN"}
UPCOMING_STATUSES = {"NS", "1H", "HT", "2H", "LIVE", "ET"}

# Rough Elo defaults (used when a team has no cached entry)
DEFAULT_ELO = {
    "Real Madrid": 1930, "Barcelona": 1900, "Atletico Madrid": 1840, "Arsenal": 1890,
    "Manchester City": 1920, "Liverpool": 1910, "Chelsea": 1850, "Manchester United": 1830,
    "Tottenham": 1810, "Newcastle": 1800, "Everton": 1750, "Brighton": 1780,
    "Bayern Munich": 1930, "Dortmund": 1860, "PSG": 1890, "Inter": 1900, "AC Milan": 1860,
    "Juventus": 1850, "Real Oviedo": 1600, "Las Palmas": 1620,
}

def log_flagged_match(team, venue, games_played, reason, source, flag_type):
    try:
        file_exists = FLAGGED_LOG.exists()
        with open(FLAGGED_LOG, 'a', newline='', encoding='utf-8') as f:
            writer = csv.writer(f)
            if not file_exists:
                writer.writerow(["timestamp","team","venue","games_requested","games_found","reason","source","flag_type","action"])
            writer.writerow([datetime.now().isoformat(), team, venue, games_played, 0, reason, source, flag_type, "AVOID - NO BET"])
    except Exception as e:
        print(f"Logging error: {e}")

def log_prediction(home, away, home_src, away_src, pred, status, stake, bankroll, is_unverified, diff):
    try:
        file_exists = PREDICTION_LOG.exists()
        with open(PREDICTION_LOG, 'a', newline='', encoding='utf-8') as f:
            writer = csv.writer(f)
            if not file_exists:
                writer.writerow(["timestamp","home","away","home_source","away_source","prediction","status","stake","bankroll","is_unverified","diff","profit"])
            writer.writerow([datetime.now().isoformat(), home, away, home_src, away_src, pred, status, stake, bankroll, is_unverified, diff, ""])
    except Exception as e:
        print(f"Pred log error: {e}")

# Team cache
def load_team_cache():
    try:
        if CACHE_FILE.exists():
            return json.loads(CACHE_FILE.read_text())
    except:
        pass
    return {
        "Real Madrid": 541, "Barcelona": 529, "Atletico Madrid": 530,
        "Arsenal": 42, "Manchester City": 50, "Liverpool": 40,
        "Chelsea": 49, "Manchester United": 33, "Tottenham": 47,
        "Newcastle": 34, "Everton": 45, "Brighton": 51,
        "Bayern Munich": 157, "Dortmund": 165, "PSG": 85,
        "Inter": 505, "AC Milan": 489, "Juventus": 496,
        "Real Oviedo": 727, "Las Palmas": 534
    }

def save_team_cache(cache):
    try:
        CACHE_FILE.write_text(json.dumps(cache, indent=2))
    except:
        pass

# Bulletproof prompt V4.3.1 (STRICT mode - flags inadequate data)
OLLAMA_SYSTEM_PROMPT_V4_3 = """
You are IszyOdds V4.3 Data Verifier - You are a STRICT JSON API, not a chatbot.

YOUR JOB: Return ONLY valid JSON. No explanation, no text outside JSON.

STEP-BY-STEP TASK:

1. USER WILL ASK: \"Return last 5 HOME games for Team X in 2024-26 season\"

2. YOU MUST CHECK YOUR KNOWLEDGE:
   - Do you have 5 REAL, VERIFIED games for Team X playing at HOME in 2024-26?
   - Can you provide ALL these fields for EACH game: shots, sot, goals, xg, dangerous, passes, clean, scored, poss, match, date, venue, opponent, opponent_elo, rest_days ?
   - Are you 100% certain each game's stats are from a real match that happened?

3. IF YES TO ALL (you have 5 real home games with all fields):
   RETURN THIS EXACT FORMAT (JSON array):
   [
     {
       \"shots\": 18,
       \"sot\": 6,
       \"goals\": 2,
       \"xg\": 1.8,
       \"dangerous\": 68,
       \"passes\": 540,
       \"clean\": 0,
       \"scored\": 2,
       \"poss\": 59,
       \"match\": \"Real Madrid vs Barcelona 2024-10-26\",
       \"date\": \"2024-10-26\",
       \"venue\": \"home\",
       \"opponent\": \"Barcelona\",
       \"opponent_elo\": 1850,
       \"rest_days\": 4,
       \"source\": \"verified-llm\"
     }
   ]

   RULES FOR SUCCESS JSON:
   - shots: total shots, integer 5-30, must be > sot
   - sot: shots on target, integer 1-15, must be <= shots
   - goals: 0-5
   - xg: 0.2-3.5, float
   - passes: 300-700
   - poss: 35-75
   - opponent_elo: 1400-1950
   - rest_days: 2-9
   - date: YYYY-MM-DD format, must be in 2024-2026
   - venue: must match requested venue
   - NEVER use placeholder values: FORBIDDEN values are shots=12, goals=1.5, passes=430, poss=50, dangerous=50 together

4. IF NO TO ANY (you have <3 games, or missing fields, or uncertain, or team is obscure like Falkirk, Elversberg, Real Oviedo Segunda):
   RETURN THIS EXACT FORMAT (JSON object, not array):
   {
     \"error\": \"INADEQUATE_DATA\",
     \"reason\": \"Only 1 home games found for Falkirk in 2024-26, need 3 minimum\",
     \"flag\": \"FLAGGED - INADEQUATE\",
     \"team\": \"Falkirk\",
     \"venue\": \"home\",
     \"games_found\": 1,
     \"games_needed\": 3,
     \"action\": \"AVOID - NO BET\"
   }

5. FINAL CHECK BEFORE RETURNING SUCCESS:
   - Is SOT <= shots? If not, return INADEQUATE_DATA.
   - Is shots=12 AND goals=1.5 AND passes=430 AND poss=50 together? If yes, return INADEQUATE_DATA.
   - Are dates in YYYY-MM-DD and 2024-2026? If not, return INADEQUATE_DATA.
   - Do you have exactly 5? If <3, return INADEQUATE_DATA.

Returning fake data will cause user to lose real money. Returning INADEQUATE_DATA protects bankroll and is CORRECT when uncertain.
You are JSON API. Return ONLY JSON array on success, ONLY JSON object with error on failure.
"""

# LENIENT mode prompt - hallucination / estimation is explicitly allowed.
# The whole point: ALWAYS return a plausible JSON array so a prediction can be made.
LENIENT_SYSTEM_PROMPT = """
You are IszyOdds LENIENT Estimator - a STRICT JSON API, not a chatbot.
MODE: HALLUCINATION / ESTIMATION ALLOWED AND EXPECTED. The user wants a prediction even when you do NOT have verified real data.

TASK: The user asks for the last @N@ @VENUE@ games of a team.
You MUST ALWAYS return a JSON array of exactly @N@ game objects.
Never refuse. Never return an error object. Never say INADEQUATE_DATA. If unsure, ESTIMATE plausible, realistic, internally-consistent numbers.

Each object must have EXACTLY these fields:
{
  "shots": integer 8-22,
  "sot": integer 1-8, must be <= shots,
  "goals": integer 0-4,
  "xg": float 0.4-3.2, roughly shots * 0.08 to 0.12,
  "dangerous": integer 40-80,
  "passes": integer 350-650,
  "clean": 0,
  "scored": same as goals,
  "poss": integer 40-70,
  "match": "Team vs Opponent YYYY-MM-DD",
  "date": "YYYY-MM-DD",
  "venue": "@VENUE@",
  "opponent": "opponent team name",
  "opponent_elo": integer 1450-1920,
  "rest_days": integer 3-7,
  "source": "llm-hallucinated"
}
Dates: realistic, within the last 9 months, about 7 days apart, most recent first.
Consistency: sot <= shots; goals usually within +-1.5 of xg; higher poss usually means more passes.
Return ONLY the JSON array. No text before or after. No explanations.
"""

def get_secrets():
    """Load secrets from .streamlit/secrets.toml or env - works on Streamlit Cloud, Vercel, local"""
    api_key = None
    groq_key = None
    try:
        import streamlit as st
        api_key = st.secrets.get("API_FOOTBALL_KEY")
        groq_key = st.secrets.get("GROQ_API_KEY")
    except:
        pass
    # Try env vars (Vercel)
    if not api_key:
        import os
        api_key = os.getenv("API_FOOTBALL_KEY")
    if not groq_key:
        import os
        groq_key = os.getenv("GROQ_API_KEY")
    # Try file directly
    if not api_key or not groq_key:
        try:
            p = pathlib.Path(".streamlit/secrets.toml")
            if p.exists():
                txt = p.read_text()
                if not api_key:
                    m = re.search(r'API_FOOTBALL_KEY\s*=\s*"([^"]+)"', txt)
                    if m: api_key = m.group(1)
                if not groq_key:
                    m = re.search(r'GROQ_API_KEY\s*=\s*"([^"]+)"', txt)
                    if m: groq_key = m.group(1)
        except:
            pass
    return api_key, groq_key

# ---------- STRICT LLM path (flags inadequate data) ----------

def try_ollama_v4_3(team, venue, games_played):
    """Level 1 (strict): Local Ollama"""
    try:
        user_prompt = f"Task: Return last {games_played} {venue} games for {team} in 2024-26 season. Follow SYSTEM instructions exactly. If you have 5 real games return JSON array, if <3 return JSON object with error INADEQUATE_DATA. Return ONLY JSON."
        r = requests.post("http://localhost:11434/api/chat", json={
            "model": "llama3.2",
            "messages": [
                {"role": "system", "content": OLLAMA_SYSTEM_PROMPT_V4_3},
                {"role": "user", "content": user_prompt}
            ],
            "stream": False,
            "options": {"temperature": 0.0, "top_p": 0.1, "num_predict": 2000}
        }, timeout=20)
        if r.status_code != 200:
            return None, [], f"Ollama HTTP {r.status_code}", "", True
        content = r.json().get("message", {}).get("content", "").strip()
        return parse_llm_response(content, team, venue, games_played, "ollama-local")
    except Exception as e:
        return None, [], f"Ollama not available: {e}", "", True

def try_groq_v4_3(team, venue, games_played, groq_key):
    """Level 2 (strict): Groq Cloud LLM"""
    if not groq_key:
        return None, [], "Groq key missing", "", True
    try:
        headers = {"Authorization": f"Bearer {groq_key}", "Content-Type": "application/json"}
        user_prompt = f"Task: Return last {games_played} {venue} games for {team} in 2024-26 season. Follow SYSTEM instructions exactly. If you have 5 real games return JSON array, if <3 return JSON object with error INADEQUATE_DATA. Return ONLY JSON."
        data = {
            "model": "llama-3.3-70b-versatile",
            "messages": [
                {"role": "system", "content": OLLAMA_SYSTEM_PROMPT_V4_3},
                {"role": "user", "content": user_prompt}
            ],
            "temperature": 0.0,
            "top_p": 0.1,
            "max_tokens": 2500
        }
        r = requests.post("https://api.groq.com/openai/v1/chat/completions", json=data, headers=headers, timeout=25)
        if r.status_code != 200:
            return None, [], f"Groq HTTP {r.status_code}: {r.text[:200]}", "", True
        content = r.json()["choices"][0]["message"]["content"].strip()
        return parse_llm_response(content, team, venue, games_played, "groq-cloud")
    except Exception as e:
        return None, [], f"Groq error: {e}", "", True

def parse_llm_response(content, team, venue, games_played, source_label):
    """Shared parser for STRICT Ollama/Groq JSON (flags inadequate data)"""
    try:
        if "```" in content:
            m = re.search(r"```(?:json)?\s*(\[.*?\]|\{.*?\})\s*```", content, re.DOTALL)
            if m:
                content = m.group(1)
        content = content.strip()
        if content.startswith("["):
            data = json.loads(content)
            if not isinstance(data, list) or len(data) < 3:
                log_flagged_match(team, venue, games_played, f"Only {len(data) if isinstance(data,list) else 0} games found need 3 min", source_label, "FLAGGED - INADEQUATE")
                return None, [], f"FLAGGED - Only {len(data)} games", f"LLM {source_label} FLAGGED INADEQUATE - Only {len(data)} games for {team} - AVOID", True
            for g in data:
                required = ["shots","sot","goals","xg","passes","poss","opponent","opponent_elo","rest_days","date","venue"]
                missing = [k for k in required if k not in g]
                if missing:
                    log_flagged_match(team, venue, games_played, f"Missing {missing}", source_label, "FLAGGED - INCOMPLETE")
                    return None, [], f"FLAGGED - Missing {missing}", f"LLM missing fields {missing} - FLAGGED", True
                if g.get("sot",0) > g.get("shots",0):
                    log_flagged_match(team, venue, games_played, f"SOT {g['sot']} > shots {g['shots']}", source_label, "FLAGGED - INVALID SOT")
                    return None, [], "FLAGGED - SOT > shots", f"LLM invalid SOT>shots - FLAGGED", True
                if g.get("shots")==12 and g.get("passes")==430:
                    log_flagged_match(team, venue, games_played, "Fake placeholder 12/430", source_label, "FLAGGED - FAKE DATA")
                    return None, [], "FLAGGED - Fake placeholder", f"LLM fake placeholder - FLAGGED REJECTED", True
            return data, data, f"{source_label} - {team} {venue} - {len(data)} games", f"{source_label} success", False
        else:
            data = json.loads(content)
            if data.get("error") == "INADEQUATE_DATA":
                reason = data.get("reason","INADEQUATE")
                flag = data.get("flag","FLAGGED - INADEQUATE")
                log_flagged_match(team, venue, games_played, reason, source_label, flag)
                return None, [], f"FLAGGED - {reason}", f"LLM {source_label} FLAGGED: {reason} - AVOID - {flag}", True
            return None, [], f"Unknown LLM response: {content[:200]}", f"LLM {source_label} unknown", True
    except Exception as e:
        return None, [], f"Parse error {source_label}: {e} - Content: {content[:300]}", f"Parse error {source_label}", True

# ---------- LENIENT LLM path (hallucination allowed - never flags) ----------

def _lenient_prompt(team, venue, games_played):
    sys_p = LENIENT_SYSTEM_PROMPT.replace("@N@", str(games_played)).replace("@VENUE@", venue)
    return sys_p, f"Task: Return last {games_played} {venue} games for {team}. Estimation is allowed - ALWAYS return the JSON array of {games_played} games. Return ONLY JSON."

def try_ollama_lenient(team, venue, games_played):
    """Lenient Level 1: Local Ollama, hallucination allowed"""
    try:
        sys_p, user_prompt = _lenient_prompt(team, venue, games_played)
        r = requests.post("http://localhost:11434/api/chat", json={
            "model": "llama3.2",
            "messages": [
                {"role": "system", "content": sys_p},
                {"role": "user", "content": user_prompt}
            ],
            "stream": False,
            "options": {"temperature": 0.4, "top_p": 0.9, "num_predict": 2500}
        }, timeout=25)
        if r.status_code != 200:
            return None, [], f"Ollama HTTP {r.status_code}", "", True
        content = r.json().get("message", {}).get("content", "").strip()
        return parse_llm_lenient(content, team, venue, games_played, "ollama-local")
    except Exception as e:
        return None, [], f"Ollama not available: {e}", "", True

def try_groq_lenient(team, venue, games_played, groq_key):
    """Lenient Level 2: Groq Cloud, hallucination allowed"""
    if not groq_key:
        return None, [], "Groq key missing", "", True
    try:
        sys_p, user_prompt = _lenient_prompt(team, venue, games_played)
        headers = {"Authorization": f"Bearer {groq_key}", "Content-Type": "application/json"}
        data = {
            "model": "llama-3.3-70b-versatile",
            "messages": [
                {"role": "system", "content": sys_p},
                {"role": "user", "content": user_prompt}
            ],
            "temperature": 0.4,
            "top_p": 0.9,
            "max_tokens": 3000
        }
        r = requests.post("https://api.groq.com/openai/v1/chat/completions", json=data, headers=headers, timeout=30)
        if r.status_code != 200:
            return None, [], f"Groq HTTP {r.status_code}: {r.text[:200]}", "", True
        content = r.json()["choices"][0]["message"]["content"].strip()
        return parse_llm_lenient(content, team, venue, games_played, "groq-cloud")
    except Exception as e:
        return None, [], f"Groq error: {e}", "", True

def _clamp_num(v, lo, hi, default, integer=False):
    try:
        x = float(v)
    except (TypeError, ValueError):
        x = float(default)
    x = max(lo, min(hi, x))
    return int(round(x)) if integer else round(x, 2)

def normalize_game_lenient(g, venue, idx=0):
    """Fill missing fields and clamp ranges for a hallucinated game. Never rejects."""
    if not isinstance(g, dict):
        g = {}
    shots = _clamp_num(g.get("shots"), 8, 22, 13, integer=True)
    sot = _clamp_num(g.get("sot"), 1, min(8, shots), max(1, shots // 3), integer=True)
    goals = _clamp_num(g.get("goals"), 0, 4, 1, integer=True)
    xg = _clamp_num(g.get("xg"), 0.4, 3.2, round(shots * 0.09, 2))
    passes = _clamp_num(g.get("passes"), 350, 650, 480, integer=True)
    poss = _clamp_num(g.get("poss"), 40, 70, 52, integer=True)
    dangerous = _clamp_num(g.get("dangerous"), 40, 80, 55, integer=True)
    elo = _clamp_num(g.get("opponent_elo"), 1450, 1920, 1650, integer=True)
    rest = _clamp_num(g.get("rest_days"), 3, 7, 4, integer=True)
    date = str(g.get("date") or "")
    if not re.match(r"^\d{4}-\d{2}-\d{2}$", date):
        date = (datetime.now().date() - timedelta(days=7 * (idx + 1))).isoformat()
    opponent = str(g.get("opponent") or "Unknown Opponent")
    match = str(g.get("match") or f"{opponent} {date}")
    return {
        "shots": shots, "sot": sot, "goals": goals, "xg": xg, "dangerous": dangerous,
        "passes": passes, "clean": 0, "scored": goals, "poss": poss,
        "match": match, "date": date, "venue": venue, "opponent": opponent,
        "opponent_elo": elo, "rest_days": rest, "source": "llm-hallucinated",
    }

def parse_llm_lenient(content, team, venue, games_played, source_label):
    """Parser for LENIENT LLM output: accepts >=1 games, fills gaps, NEVER flags."""
    try:
        text = (content or "").strip()
        if "```" in text:
            m = re.search(r"```(?:json)?\s*(\[.*\])\s*```", text, re.DOTALL)
            if m:
                text = m.group(1)
        if not text.startswith("["):
            i = text.find("[")
            j = text.rfind("]")
            if i == -1 or j <= i:
                return None, [], f"{source_label} no JSON array in response", f"content: {text[:200]}", True
            text = text[i:j + 1]
        data = json.loads(text)
        if isinstance(data, dict) and "shots" in data:
            data = [data]
        if not isinstance(data, list):
            return None, [], f"{source_label} JSON is not a list", text[:200], True
        games = [normalize_game_lenient(g, venue, i) for i, g in enumerate(data) if isinstance(g, dict)]
        if not games:
            return None, [], f"{source_label} no usable games in array", text[:200], True
        msg = f"{source_label} HALLUCINATED - {team} {venue} - {len(games)} estimated games"
        return games, games, msg, f"{source_label} lenient estimate accepted (hallucination mode - UNVERIFIED)", False
    except Exception as e:
        return None, [], f"{source_label} parse error: {e}", (content or "")[:300], True

def synthetic_games(team, venue, n=5):
    """Deterministic fallback: plausible estimated games so a prediction can ALWAYS be made.
    Seeded by team+venue so repeated calls are consistent. Marked synthetic-fallback."""
    seed = int(hashlib.md5(f"{team}|{venue}".encode()).hexdigest()[:8], 16)
    rng = random.Random(seed)
    try:
        opponents_pool = [t for t in load_team_cache().keys() if t.lower() != team.lower()]
    except Exception:
        opponents_pool = []
    if not opponents_pool:
        opponents_pool = ["Athletic Bilbao", "Real Sociedad", "Villarreal", "Roma", "Napoli"]
    today = datetime.now().date()
    games = []
    for i in range(n):
        poss = rng.randint(42, 68)
        shots = max(8, min(22, int(rng.randint(9, 18) + (poss - 50) * 0.18)))
        sot = max(1, min(shots, int(shots * rng.uniform(0.25, 0.45))))
        xg = round(shots * rng.uniform(0.06, 0.11), 2)
        goals = max(0, min(4, int(round(xg + rng.uniform(-1.2, 1.2)))))
        passes = int(rng.randint(360, 640))
        dangerous = int(rng.randint(42, 78))
        elo = rng.randint(1450, 1900)
        rest = rng.randint(3, 7)
        opp = rng.choice(opponents_pool)
        d = today - timedelta(days=7 * (i + 1) + rng.randint(0, 2))
        games.append({
            "shots": shots, "sot": sot, "goals": goals, "xg": xg, "dangerous": dangerous,
            "passes": passes, "clean": 0, "scored": goals, "poss": poss,
            "match": f"{team} vs {opp} {d.isoformat()}" if venue == "home" else f"{opp} vs {team} {d.isoformat()}",
            "date": d.isoformat(), "venue": venue, "opponent": opp,
            "opponent_elo": elo, "rest_days": rest, "source": "synthetic-fallback",
        })
    return games

# ---------- API-Football (verified) ----------

def _to_float(v, default=None):
    if v is None:
        return default
    if isinstance(v, (int, float)):
        return float(v)
    s = str(v).replace("%", "").replace(",", "").strip()
    try:
        return float(s)
    except ValueError:
        return default

def _current_season():
    now = datetime.now()
    return now.year if now.month >= 7 else now.year - 1

def fetch_fixture_team_stats(fixture_id, team_id, api_key):
    """GET /fixtures/statistics?fixture=ID -> flat {stat_type: value} dict for one team, or None"""
    try:
        r = requests.get(f"{API_BASE}/fixtures/statistics",
                         headers={"x-apisports-key": api_key},
                         params={"fixture": fixture_id}, timeout=20)
        if r.status_code != 200:
            return None
        for entry in r.json().get("response", []):
            if entry.get("team", {}).get("id") == team_id:
                return {s.get("type"): s.get("value") for s in entry.get("statistics", [])}
    except Exception:
        pass
    return None

def build_game_from_fixture(f, team_id, venue, stats, prev_date=None):
    """Map one API-Football fixture (+ optional per-team statistics) to a game dict."""
    teams = f.get("teams", {})
    fx = f.get("fixture", {})
    goals_block = f.get("goals", {}) or {}
    is_home = teams.get("home", {}).get("id") == team_id
    opponent = (teams.get("away") or {}).get("name") if is_home else (teams.get("home") or {}).get("name") or "Unknown"
    team_goals = goals_block.get("home") if is_home else goals_block.get("away")
    goals = _to_float(team_goals, 0) or 0
    shots = _to_float((stats or {}).get("Shots Total"), round(8 + (55 - 50) * 0.2 + 5)) or 13
    sot = _to_float((stats or {}).get("Shots on Goal"), max(1, int(shots * 0.33)))
    sot = min(sot, shots)
    poss = _to_float((stats or {}).get("Ball Possession"), 52) or 52
    passes = _to_float((stats or {}).get("Total passes"), 480) or 480
    dangerous = _to_float((stats or {}).get("Dangerous attacks"), 55) or 55
    xg = _to_float((stats or {}).get("expected_goals"), round(shots * 0.085, 2))
    date_str = (fx.get("date") or "")[:10] or datetime.now().date().isoformat()
    rest_days = 4
    if prev_date:
        try:
            d1 = datetime.fromisoformat(date_str)
            d2 = datetime.fromisoformat(prev_date[:10])
            rest_days = max(1, min(14, abs((d1 - d2).days)))
        except Exception:
            rest_days = 4
    verified = bool(stats)  # real per-match stats fetched
    return {
        "shots": int(shots), "sot": int(sot), "goals": int(goals), "xg": round(float(xg), 2),
        "dangerous": int(dangerous), "passes": int(passes), "clean": 0, "scored": int(goals),
        "poss": int(poss),
        "match": f"{teams.get('home',{}).get('name','?')} vs {teams.get('away',{}).get('name','?')} {date_str}",
        "date": date_str, "venue": venue, "opponent": opponent,
        "opponent_elo": DEFAULT_ELO.get(opponent, 1600),
        "rest_days": rest_days,
        "source": "api-football-verified" if verified else "api-football-partial",
    }

def try_api_football_v4_3(team, venue, games_played, api_key, min_games=3):
    """Level 3: API-Football verified - real fixtures, venue-filtered, real per-match stats when available"""
    if not api_key:
        return None, [], "API-Football key missing", "", True
    try:
        cache = load_team_cache()
        team_id = cache.get(team)
        if not team_id:
            r = requests.get(f"{API_BASE}/teams", headers={"x-apisports-key": api_key}, params={"search": team}, timeout=15)
            if r.status_code == 200 and r.json().get("response"):
                team_id = r.json()["response"][0]["team"]["id"]
                cache[team] = team_id
                save_team_cache(cache)
            else:
                return None, [], f"API-Football team {team} not found", "", True

        # Fetch recent fixtures and filter by the requested venue + finished status
        params = {"team": team_id, "last": max(games_played * 4, 20)}
        r = requests.get(f"{API_BASE}/fixtures", headers={"x-apisports-key": api_key}, params=params, timeout=15)
        if r.status_code != 200:
            return None, [], f"API-Football HTTP {r.status_code}", "", True
        fixtures = r.json().get("response", [])
        venue_fixtures = []
        for f in fixtures:
            status = f.get("fixture", {}).get("status", {}).get("short", "")
            if status not in FINISHED_STATUSES:
                continue
            is_home = f.get("teams", {}).get("home", {}).get("id") == team_id
            if (venue == "home") == is_home:
                venue_fixtures.append(f)
        # API returns most recent first; keep that order
        venue_fixtures = venue_fixtures[:games_played] if len(venue_fixtures) >= games_played else venue_fixtures
        if len(venue_fixtures) < min_games:
            return None, [], f"API-Football only {len(venue_fixtures)} {venue} games for {team} (need {min_games})", f"venue-filtered fixtures for {team}", True

        games = []
        prev_date = None
        for f in venue_fixtures:
            fid = f.get("fixture", {}).get("id")
            stats = fetch_fixture_team_stats(fid, team_id, api_key) if fid else None
            games.append(build_game_from_fixture(f, team_id, venue, stats, prev_date))
            prev_date = (f.get("fixture", {}).get("date") or "")[:10]
        n_verified = sum(1 for g in games if g["source"] == "api-football-verified")
        return games, games, f"API-Football - {team} {venue} - {len(games)} games ({n_verified} with full stats)", "API-Football verified fixtures", False
    except Exception as e:
        return None, [], f"API-Football error: {e}", "", True

# ---------- Match picker: today / past (date) / future (range) ----------

def _fixture_to_dict(f):
    return {
        "fixture_id": f.get("fixture", {}).get("id"),
        "league": f.get("league", {}).get("name", "?"),
        "league_id": f.get("league", {}).get("id"),
        "country": f.get("league", {}).get("country", ""),
        "date": (f.get("fixture", {}).get("date") or "")[:16].replace("T", " "),
        "status": f.get("fixture", {}).get("status", {}).get("short", "?"),
        "home": f.get("teams", {}).get("home", {}).get("name", "?"),
        "away": f.get("teams", {}).get("away", {}).get("name", "?"),
        "home_id": f.get("teams", {}).get("home", {}).get("id"),
        "away_id": f.get("teams", {}).get("away", {}).get("id"),
        "home_goals": (f.get("goals") or {}).get("home"),
        "away_goals": (f.get("goals") or {}).get("away"),
    }

def _fetch_fixtures(params, api_key, league_ids, label):
    if not api_key:
        return [], "API-Football key missing"
    try:
        r = requests.get(f"{API_BASE}/fixtures", headers={"x-apisports-key": api_key}, params=params, timeout=25)
        if r.status_code != 200:
            return [], f"API-Football HTTP {r.status_code}: {r.text[:150]}"
        out = []
        for f in r.json().get("response", []):
            d = _fixture_to_dict(f)
            if league_ids and d["league_id"] not in league_ids:
                continue
            out.append(d)
        out.sort(key=lambda d: (d["league_id"] or 0, d["date"]))
        return out, f"{len(out)} fixtures {label}"
    except Exception as e:
        return [], f"API-Football error: {e}"

def fetch_fixtures_by_date(date_str, api_key, league_ids=None):
    """All fixtures on one date (YYYY-MM-DD). Defaults to popular leagues."""
    if league_ids is None:
        league_ids = list(POPULAR_LEAGUES.keys())
    return _fetch_fixtures({"date": date_str}, api_key, league_ids, f"on {date_str}")

def fetch_fixtures_between(from_str, to_str, api_key, league_ids=None):
    """Fixtures in a date range (YYYY-MM-DD .. YYYY-MM-DD) - used for future matches."""
    if league_ids is None:
        league_ids = list(POPULAR_LEAGUES.keys())
    return _fetch_fixtures({"from": from_str, "to": to_str}, api_key, league_ids, f"between {from_str} and {to_str}")

def fetch_fixtures_for_league(league_id, api_key, next_n=10, season=None):
    """Next N fixtures of one league (alternative future-matches source)."""
    params = {"league": league_id, "next": next_n}
    if season:
        params["season"] = season
    return _fetch_fixtures(params, api_key, None, f"next {next_n} in league {league_id}")

# ---------- Main chain ----------

def fetch_v4_3_stats(team, venue, games_played=5, use_llm=True, mode="strict"):
    """Main chain.

    mode="strict":  Ollama strict -> Groq strict -> API-Football verified -> FLAGGED AVOID (old behaviour)
    mode="lenient": Ollama lenient (hallucination OK) -> Groq lenient -> API-Football verified
                    -> synthetic fallback. ALWAYS returns data so a prediction can be made.
    """
    api_key, groq_key = get_secrets()
    lenient = (mode == "lenient")

    if lenient:
        # Level 1: Local Ollama, estimation allowed
        if use_llm:
            data, raw, msg, detail, is_flagged = try_ollama_lenient(team, venue, games_played)
            if data:
                return data, raw, f"LLM LOCAL HALLUCINATED - {msg}", detail, False
        # Level 2: Groq Cloud, estimation allowed
        if use_llm and groq_key:
            data, raw, msg, detail, is_flagged = try_groq_lenient(team, venue, games_played, groq_key)
            if data:
                return data, raw, f"LLM GROQ HALLUCINATED - {msg}", detail, False
        # Level 3: API-Football verified real data (best)
        if api_key:
            data, raw, msg, detail, is_flagged = try_api_football_v4_3(team, venue, games_played, api_key, min_games=1)
            if data:
                return data, raw, f"API-Football VERIFIED - {msg}", detail, False
        # Level 4: synthetic fallback - NEVER flag, ALWAYS produce a prediction
        data = synthetic_games(team, venue, games_played)
        return data, data, f"SYNTHETIC FALLBACK - estimated {games_played} {venue} games for {team} (no external data available)", "synthetic fallback - UNVERIFIED", False

    # ---- strict mode (original behaviour) ----
    if use_llm:
        data, raw, msg, detail, is_flagged = try_ollama_v4_3(team, venue, games_played)
        if data and not is_flagged:
            return data, raw, f"LLM LOCAL UNVERIFIED - {msg}", detail, True
    if use_llm and groq_key:
        data, raw, msg, detail, is_flagged = try_groq_v4_3(team, venue, games_played, groq_key)
        if data and not is_flagged:
            return data, raw, f"LLM GROQ CLOUD UNVERIFIED - {msg}", detail, True
    if api_key:
        data, raw, msg, detail, is_flagged = try_api_football_v4_3(team, venue, games_played, api_key)
        if data:
            return data, raw, f"API-Football VERIFIED - {msg}", detail, False

    reason = f"All sources failed for {team} {venue} - No data"
    log_flagged_match(team, venue, games_played, reason, "all-sources", "FLAGGED - ALL SOURCES FAILED")
    return None, [], f"FLAGGED - {reason}", f"All sources failed - AVOID 0% - {reason}", True

def fetch_top_5_safe_matches():
    """Return 5 safe matches with high API coverage"""
    return [
        ("Real Madrid", "Barcelona"),
        ("Arsenal", "Liverpool"),
        ("Manchester City", "Chelsea"),
        ("Bayern Munich", "Dortmund"),
        ("PSG", "Inter")
    ]
