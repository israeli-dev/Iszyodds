import pathlib, json, requests, csv, re, random, hashlib
from datetime import datetime, timedelta, timezone

CACHE_FILE = pathlib.Path("team_ids.json")

# Logging
FLAGGED_LOG = pathlib.Path("flagged_matches.csv")
PREDICTION_LOG = pathlib.Path("prediction_history.csv")          # legacy v1 schema (kept for compatibility)
PREDICTION_LOG_V2 = pathlib.Path("prediction_history_v2.csv")    # v2 schema: fixture-bound, timezone-aware, snapshot, result

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

# API-Football per-match statistic fields that must be genuinely present for a game
# to be labelled "api-football-verified". Anything else is recorded in missing_fields.
REQUIRED_API_FIELDS = ["Shots Total", "Shots on Goal", "Ball Possession", "Total passes"]
OPTIONAL_API_FIELDS = ["expected_goals", "Dangerous attacks"]

# Rough Elo defaults (used when a team has no cached entry)
DEFAULT_ELO = {
    "Real Madrid": 1930, "Barcelona": 1900, "Atletico Madrid": 1840, "Arsenal": 1890,
    "Manchester City": 1920, "Liverpool": 1910, "Chelsea": 1850, "Manchester United": 1830,
    "Tottenham": 1810, "Newcastle": 1800, "Everton": 1750, "Brighton": 1780,
    "Bayern Munich": 1930, "Dortmund": 1860, "PSG": 1890, "Inter": 1900, "AC Milan": 1860,
    "Juventus": 1850, "Real Oviedo": 1600, "Las Palmas": 1620,
}

# Demo fallback (only used when no API_FOOTBALL_KEY is configured - clearly labelled DEMO in the UI)
DEMO_MATCHES = [
    ("Real Madrid", "Barcelona"),
    ("Arsenal", "Liverpool"),
    ("Manchester City", "Chelsea"),
    ("Bayern Munich", "Dortmund"),
    ("PSG", "Inter")
]

# ---------- logging ----------

def log_flagged_match(team, venue, games_played, reason, source, flag_type):
    try:
        file_exists = FLAGGED_LOG.exists()
        with open(FLAGGED_LOG, 'a', newline='', encoding='utf-8') as f:
            writer = csv.writer(f)
            if not file_exists:
                writer.writerow(["timestamp", "team", "venue", "games_requested", "games_found", "reason", "source", "flag_type", "action"])
            writer.writerow([datetime.now().isoformat(), team, venue, games_played, 0, reason, source, flag_type, "AVOID - NO BET"])
    except Exception as e:
        print(f"Logging error: {e}")

def log_prediction(home, away, home_src, away_src, pred, status, stake, bankroll, is_unverified, diff):
    """Legacy v1 logger (kept for backward compatibility). New code uses log_prediction_v2."""
    try:
        file_exists = PREDICTION_LOG.exists()
        with open(PREDICTION_LOG, 'a', newline='', encoding='utf-8') as f:
            writer = csv.writer(f)
            if not file_exists:
                writer.writerow(["timestamp", "home", "away", "home_source", "away_source", "prediction", "status", "stake", "bankroll", "is_unverified", "diff", "profit"])
            writer.writerow([datetime.now().isoformat(), home, away, home_src, away_src, pred, status, stake, bankroll, is_unverified, diff, ""])
    except Exception as e:
        print(f"Pred log error: {e}")

V2_HEADER = ["timestamp", "model_version", "fixture_id", "home", "away", "league",
             "kickoff_utc", "kickoff_local", "as_of", "mode",
             "home_source", "away_source", "data_quality", "prediction", "status",
             "stake", "bankroll", "is_unverified", "diff", "result",
             "home_snapshot", "away_snapshot"]

def log_prediction_v2(row: dict):
    """Append a fixture-bound prediction row (v2 schema) to prediction_history_v2.csv."""
    try:
        from v3_logic import MODEL_VERSION
        row.setdefault("model_version", MODEL_VERSION)
        row.setdefault("timestamp", datetime.now().isoformat())
        file_exists = PREDICTION_LOG_V2.exists()
        with open(PREDICTION_LOG_V2, 'a', newline='', encoding='utf-8') as f:
            writer = csv.writer(f)
            if not file_exists:
                writer.writerow(V2_HEADER)
            writer.writerow([str(row.get(k, ""))[:4000] for k in V2_HEADER])
    except Exception as e:
        print(f"Pred v2 log error: {e}")

# ---------- team cache ----------

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

# ---------- secrets ----------

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
    if not api_key:
        import os
        api_key = os.getenv("API_FOOTBALL_KEY")
    if not groq_key:
        import os
        groq_key = os.getenv("GROQ_API_KEY")
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

# ---------- kickoff timezone helper (Stage 1: fixture binding) ----------

def kickoff_times(iso_str):
    """Return (kickoff_utc_iso, kickoff_local_display) in Africa/Lagos (WAT, UTC+1 year-round)."""
    s = (iso_str or "").strip()
    if not s:
        return "", ""
    if "T" not in s:
        s = s.replace(" ", "T", 1)
    try:
        dt = datetime.fromisoformat(s)
    except Exception:
        try:
            dt = datetime.fromisoformat(s[:19])
        except Exception:
            return s, s
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    utc = dt.astimezone(timezone.utc)
    try:
        from zoneinfo import ZoneInfo
        local = dt.astimezone(ZoneInfo("Africa/Lagos"))
        return utc.isoformat(), local.strftime("%Y-%m-%d %H:%M %Z")
    except Exception:
        lagos = utc + timedelta(hours=1)  # Nigeria = WAT (UTC+1), no DST
        return utc.isoformat(), lagos.strftime("%Y-%m-%d %H:%M") + " WAT"

# ---------- STRICT-mode LLM prompts (legacy; strict chain no longer uses LLM for stats) ----------

OLLAMA_SYSTEM_PROMPT_V4_3 = """
You are IszyOdds V4.3 Data Verifier - You are a STRICT JSON API, not a chatbot.
Return ONLY valid JSON. If you do not have REAL VERIFIED data for the requested games,
return {"error": "INADEQUATE_DATA", "reason": "...", "flag": "FLAGGED - INADEQUATE"}.
Never fabricate statistics.
"""

# LENIENT mode prompt - hallucination / estimation is explicitly allowed (NEVER labelled verified).
LENIENT_SYSTEM_PROMPT = """
You are IszyOdds LENIENT Estimator - a STRICT JSON API, not a chatbot.
MODE: HALLUCINATION / ESTIMATION ALLOWED AND EXPECTED. The user wants a prediction even when you do NOT have verified real data.

TASK: The user asks for the last @N@ @VENUE@ games of a team, AS OF @ASOF@ (only use games played BEFORE that date).
You MUST ALWAYS return a JSON array of exactly @N@ game objects.
Never refuse. Never return an error object. If unsure, ESTIMATE plausible, realistic, internally-consistent numbers.

Each object must have EXACTLY these fields:
{
  "shots": integer 8-22, "sot": integer 1-8 (<= shots), "goals": integer 0-4,
  "xg": float 0.4-3.2 (roughly shots*0.08-0.12), "dangerous": integer 40-80,
  "passes": integer 350-650, "clean": 0, "scored": same as goals, "poss": integer 40-70,
  "match": "Team vs Opponent YYYY-MM-DD", "date": "YYYY-MM-DD",
  "venue": "@VENUE@", "opponent": "opponent team name", "opponent_elo": integer 1450-1920,
  "rest_days": integer 3-7, "source": "llm-hallucinated"
}
Dates: realistic, BEFORE @ASOF@, about 7 days apart, most recent first.
Return ONLY the JSON array. No text before or after.
"""

def _lenient_prompt(team, venue, games_played, before=None):
    asof = (before or datetime.now().date().isoformat())[:10]
    sys_p = (LENIENT_SYSTEM_PROMPT
             .replace("@N@", str(games_played))
             .replace("@VENUE@", venue)
             .replace("@ASOF@", asof))
    return sys_p, (f"Task: Return last {games_played} {venue} games for {team} as of {asof} "
                   f"(games BEFORE that date only). Estimation is allowed - ALWAYS return the JSON array. Return ONLY JSON.")

# ---------- lenient LLM calls (hallucination allowed, never verified) ----------

def try_ollama_lenient(team, venue, games_played, before=None):
    try:
        sys_p, user_prompt = _lenient_prompt(team, venue, games_played, before)
        r = requests.post("http://localhost:11434/api/chat", json={
            "model": "llama3.2",
            "messages": [{"role": "system", "content": sys_p}, {"role": "user", "content": user_prompt}],
            "stream": False,
            "options": {"temperature": 0.4, "top_p": 0.9, "num_predict": 2500}
        }, timeout=25)
        if r.status_code != 200:
            return None, [], f"Ollama HTTP {r.status_code}", "", True
        content = r.json().get("message", {}).get("content", "").strip()
        return parse_llm_lenient(content, team, venue, games_played, "ollama-local", before=before)
    except Exception as e:
        return None, [], f"Ollama not available: {e}", "", True

def try_groq_lenient(team, venue, games_played, groq_key, before=None):
    if not groq_key:
        return None, [], "Groq key missing", "", True
    try:
        sys_p, user_prompt = _lenient_prompt(team, venue, games_played, before)
        headers = {"Authorization": f"Bearer {groq_key}", "Content-Type": "application/json"}
        data = {
            "model": "llama-3.3-70b-versatile",
            "messages": [{"role": "system", "content": sys_p}, {"role": "user", "content": user_prompt}],
            "temperature": 0.4, "top_p": 0.9, "max_tokens": 3000
        }
        r = requests.post("https://api.groq.com/openai/v1/chat/completions", json=data, headers=headers, timeout=30)
        if r.status_code != 200:
            return None, [], f"Groq HTTP {r.status_code}: {r.text[:200]}", "", True
        content = r.json()["choices"][0]["message"]["content"].strip()
        return parse_llm_lenient(content, team, venue, games_played, "groq-cloud", before=before)
    except Exception as e:
        return None, [], f"Groq error: {e}", "", True

def _clamp_num(v, lo, hi, default, integer=False):
    try:
        x = float(v)
    except (TypeError, ValueError):
        x = float(default)
    x = max(lo, min(hi, x))
    return int(round(x)) if integer else round(x, 2)

def normalize_game_lenient(g, venue, idx=0, before=None):
    """Fill missing fields and clamp ranges for a hallucinated game. Never rejects.
    Missing/unknown fields are recorded in missing_fields so they stay visible."""
    if not isinstance(g, dict):
        g = {}
    before_d = None
    try:
        before_d = datetime.fromisoformat((before or "")[:10]).date() if before else None
    except Exception:
        before_d = None
    base = before_d or datetime.now().date()
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
        date = (base - timedelta(days=7 * (idx + 1))).isoformat()
    opponent = str(g.get("opponent") or "Unknown Opponent")
    match = str(g.get("match") or f"{opponent} {date}")
    return {
        "shots": shots, "sot": sot, "goals": goals, "xg": xg, "dangerous": dangerous,
        "passes": passes, "clean": 0, "scored": goals, "poss": poss,
        "match": match, "date": date, "venue": venue, "opponent": opponent,
        "opponent_elo": elo, "rest_days": rest, "source": "llm-hallucinated",
        "missing_fields": [],  # hallucinated data is complete by construction, but UNVERIFIED
    }

def parse_llm_lenient(content, team, venue, games_played, source_label, before=None):
    """Parser for LENIENT LLM output: accepts >=1 games, fills gaps, NEVER flags, NEVER verified."""
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
        games = [normalize_game_lenient(g, venue, i, before=before) for i, g in enumerate(data) if isinstance(g, dict)]
        if not games:
            return None, [], f"{source_label} no usable games in array", text[:200], True
        msg = f"{source_label} HALLUCINATED - {team} {venue} - {len(games)} estimated games (as of {(before or 'now')[:10]})"
        return games, games, msg, f"{source_label} lenient estimate accepted (hallucination mode - UNVERIFIED, never verified)", False
    except Exception as e:
        return None, [], f"{source_label} parse error: {e}", (content or "")[:300], True

def synthetic_games(team, venue, n=5, before=None):
    """Deterministic fallback for lenient mode: plausible estimated games, all dated BEFORE
    `before` (anti-leakage). Seeded by team+venue+before. Marked synthetic-fallback (UNVERIFIED)."""
    before_d = None
    try:
        before_d = datetime.fromisoformat((before or "")[:10]).date() if before else None
    except Exception:
        before_d = None
    base = before_d or datetime.now().date()
    seed = int(hashlib.md5(f"{team}|{venue}|{base}".encode()).hexdigest()[:8], 16)
    rng = random.Random(seed)
    try:
        opponents_pool = [t for t in load_team_cache().keys() if t.lower() != team.lower()]
    except Exception:
        opponents_pool = []
    if not opponents_pool:
        opponents_pool = ["Athletic Bilbao", "Real Sociedad", "Villarreal", "Roma", "Napoli"]
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
        d = base - timedelta(days=7 * (i + 1) + rng.randint(0, 2))
        games.append({
            "shots": shots, "sot": sot, "goals": goals, "xg": xg, "dangerous": dangerous,
            "passes": passes, "clean": 0, "scored": goals, "poss": poss,
            "match": f"{team} vs {opp} {d.isoformat()}" if venue == "home" else f"{opp} vs {team} {d.isoformat()}",
            "date": d.isoformat(), "venue": venue, "opponent": opp,
            "opponent_elo": elo, "rest_days": rest, "source": "synthetic-fallback",
            "missing_fields": ["shots", "sot", "goals", "xg", "poss", "passes", "dangerous"],
        })
    return games

# ---------- API-Football (genuine data only; missing fields are exposed, never fabricated) ----------

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
    """Map one API-Football fixture (+ per-team statistics) to a game dict.

    Stage 2 integrity: match identity/date/venue/goals come from the fixture (always real).
    Statistic fields are taken from the API ONLY - when missing they stay None and are
    listed in missing_fields (never replaced with invented defaults). The game is labelled
    'api-football-verified' only when ALL required stat fields are genuinely present;
    otherwise 'api-football-partial'.
    """
    teams = f.get("teams", {})
    fx = f.get("fixture", {})
    goals_block = f.get("goals", {}) or {}
    is_home = teams.get("home", {}).get("id") == team_id
    opponent = (teams.get("away") or {}).get("name") if is_home else (teams.get("home") or {}).get("name") or "Unknown"
    team_goals = goals_block.get("home") if is_home else goals_block.get("away")
    goals = _to_float(team_goals, 0) or 0

    stats = stats or {}
    shots = _to_float(stats.get("Shots Total"))          # None if not returned - NOT fabricated
    sot_raw = _to_float(stats.get("Shots on Goal"))
    sot = min(sot_raw, shots) if (sot_raw is not None and shots is not None) else sot_raw
    poss = _to_float(stats.get("Ball Possession"))
    passes = _to_float(stats.get("Total passes"))
    dangerous = _to_float(stats.get("Dangerous attacks"))
    xg = _to_float(stats.get("expected_goals"))          # may be None - engine ignores None

    missing = [fld for fld in REQUIRED_API_FIELDS + OPTIONAL_API_FIELDS
               if stats.get(fld) is None]
    verified = bool(stats) and all(stats.get(fld) is not None for fld in REQUIRED_API_FIELDS)

    date_str = (fx.get("date") or "")[:10] or datetime.now().date().isoformat()
    rest_days = 4
    if prev_date:
        try:
            rest_days = max(1, min(14, abs((datetime.fromisoformat(date_str) - datetime.fromisoformat(prev_date[:10])).days)))
        except Exception:
            rest_days = 4
    return {
        "shots": int(shots) if shots is not None else None,
        "sot": int(sot) if sot is not None else None,
        "goals": int(goals), "xg": round(float(xg), 2) if xg is not None else None,
        "dangerous": int(dangerous) if dangerous is not None else None,
        "passes": int(passes) if passes is not None else None,
        "clean": None, "scored": int(goals), "poss": int(poss) if poss is not None else None,
        "match": f"{teams.get('home', {}).get('name', '?')} vs {teams.get('away', {}).get('name', '?')} {date_str}",
        "date": date_str, "venue": venue, "opponent": opponent,
        "opponent_elo": DEFAULT_ELO.get(opponent, 1600),
        "rest_days": rest_days,
        "source": "api-football-verified" if verified else "api-football-partial",
        "missing_fields": missing,
    }

def _api_team_games(team, venue, api_key, games_played=5, min_games=3, before=None, season_games=None):
    """Fetch genuine venue-filtered finished fixtures for a team.

    before: ISO string - only fixtures BEFORE this datetime are returned (Stage 3:
    prevents future-data leakage when predicting/backtesting a past fixture).
    Returns (form_games, season_games, msg, detail, flag).
    """
    if not api_key:
        return None, None, "API-Football key missing", "", True
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
                return None, None, f"API-Football team {team} not found", "", True

        params = {"team": team_id}
        if before:
            params["to"] = (before or "")[:10]          # only matches before this date
        else:
            params["last"] = max(games_played * 4, 20)
        r = requests.get(f"{API_BASE}/fixtures", headers={"x-apisports-key": api_key}, params=params, timeout=15)
        if r.status_code != 200:
            return None, None, f"API-Football HTTP {r.status_code}", "", True
        fixtures = r.json().get("response", [])
        # most recent first
        fixtures.sort(key=lambda f: f.get("fixture", {}).get("date") or "", reverse=True)
        venue_fixtures = []
        for f in fixtures:
            status = f.get("fixture", {}).get("status", {}).get("short", "")
            if status not in FINISHED_STATUSES:
                continue
            is_home = f.get("teams", {}).get("home", {}).get("id") == team_id
            if (venue == "home") == is_home:
                venue_fixtures.append(f)
        if before:  # hard guarantee: nothing after the as-of kickoff leaks in
            cutoff = (before or "")[:10]
            venue_fixtures = [f for f in venue_fixtures if (f.get("fixture", {}).get("date") or "")[:10] <= cutoff]
        if len(venue_fixtures) < min_games:
            return None, None, f"API-Football only {len(venue_fixtures)} {venue} games for {team} before {(before or 'now')[:10]} (need {min_games})", "venue-filtered fixtures", True

        take = season_games if season_games else games_played
        all_games = []
        prev_date = None
        for f in venue_fixtures[:take]:
            fid = f.get("fixture", {}).get("id")
            stats = fetch_fixture_team_stats(fid, team_id, api_key) if fid else None
            all_games.append(build_game_from_fixture(f, team_id, venue, stats, prev_date))
            prev_date = (f.get("fixture", {}).get("date") or "")[:10]
        form = all_games[:games_played]
        season = all_games if season_games else None
        n_verified = sum(1 for g in form if g["source"] == "api-football-verified")
        msg = f"API-Football - {team} {venue} - {len(form)} form games ({n_verified} fully verified)"
        if season:
            msg += f" + {len(season)} season games (80/20 blend source)"
        return form, season, msg, "API-Football genuine fixtures (missing fields exposed, never fabricated)", False
    except Exception as e:
        return None, None, f"API-Football error: {e}", "", True

def try_api_football_v4_3(team, venue, games_played, api_key, min_games=3, before=None):
    """Backward-compatible wrapper around _api_team_games (returns form games only)."""
    form, _season, msg, detail, flag = _api_team_games(team, venue, api_key, games_played, min_games, before=before)
    return form, form, msg, detail, flag

# ---------- fixture browser: today / past (date) / future (range) ----------

def _fixture_to_dict(f):
    kickoff_raw = f.get("fixture", {}).get("date") or ""
    return {
        "fixture_id": f.get("fixture", {}).get("id"),
        "kickoff": kickoff_raw,  # full ISO with timezone - used for kickoff_utc / kickoff_local / as_of
        "league": f.get("league", {}).get("name", "?"),
        "league_id": f.get("league", {}).get("id"),
        "country": f.get("league", {}).get("country", ""),
        "date": kickoff_raw[:16].replace("T", " "),
        "status": f.get("fixture", {}).get("status", {}).get("short", "?"),
        "home": f.get("teams", {}).get("home", {}).get("name", "?"),
        "away": f.get("teams", {}).get("away", {}).get("name", "?"),
        "home_id": f.get("teams", {}).get("home", {}).get("id"),
        "away_id": f.get("teams", {}).get("away", {}).get("id"),
        "home_goals": (f.get("goals") or {}).get("home"),
        "away_goals": (f.get("goals") or {}).get("away"),
    }

def fetch_fixture_by_id(fixture_id, api_key):
    """GET /fixtures?id=ID -> one fixture dict (used to update results in history)."""
    if not api_key or not fixture_id:
        return None
    try:
        r = requests.get(f"{API_BASE}/fixtures", headers={"x-apisports-key": api_key}, params={"id": fixture_id}, timeout=20)
        if r.status_code == 200 and r.json().get("response"):
            return _fixture_to_dict(r.json()["response"][0])
    except Exception:
        pass
    return None

def lagos_today():
    """Today's date in Africa/Lagos (WAT). API-Football `date=` is UTC, so 'today' for the
    user must be computed in Lagos time - otherwise late-evening matches land on the wrong day."""
    try:
        from zoneinfo import ZoneInfo
        return datetime.now(ZoneInfo("Africa/Lagos")).date()
    except Exception:
        return (datetime.now(timezone.utc) + timedelta(hours=1)).date()

# Short TTL cache so repeated clicks across tabs don't burn the API-Football quota
_FIXTURE_CACHE = {}          # params_key -> (timestamp, (matches, msg))
FIXTURE_CACHE_TTL = 300      # seconds

def _fetch_fixtures(params, api_key, league_ids, label):
    if not api_key:
        return [], "API-Football key missing"
    key = json.dumps(params, sort_keys=True, default=str)
    now = datetime.now()
    if key in _FIXTURE_CACHE:
        ts, cached = _FIXTURE_CACHE[key]
        if (now - ts).total_seconds() < FIXTURE_CACHE_TTL:
            return cached
    try:
        r = requests.get(f"{API_BASE}/fixtures", headers={"x-apisports-key": api_key}, params=params, timeout=25)
        if r.status_code != 200:
            return [], f"API-Football HTTP {r.status_code}: {r.text[:150]}"
        payload = r.json()
        errors = payload.get("errors")
        if errors:  # API-Football often returns HTTP 200 with errors on quota/rate-limit
            return [], f"API-Football error: {str(errors)[:200]} (quota or rate limit reached?)"
        out = []
        for f in payload.get("response", []):
            d = _fixture_to_dict(f)
            if league_ids and d["league_id"] not in league_ids:
                continue
            out.append(d)
        out.sort(key=lambda d: (d["league_id"] or 0, d["kickoff"]))
        result = (out, f"{len(out)} fixtures {label}")
        _FIXTURE_CACHE[key] = (now, result)
        return result
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

# ---------- main data chains ----------

def fetch_v4_3_stats(team, venue, games_played=5, use_llm=True, mode="strict", before=None):
    """Backward-compatible form fetch. Returns (games, raw, msg, detail, flag).

    mode="strict" (DEFAULT): API-Football genuine data only - the LLM is NEVER used for
    statistics in strict mode. No genuine data -> FLAGGED.
    mode="lenient": lenient LLM (as-of aware) -> API-Football -> synthetic fallback.
    Estimates are never labelled verified.
    """
    form, _season, msg, detail, flag = fetch_team_data(team, venue, games_played, use_llm=use_llm,
                                                       mode=mode, before=before, season_games=None)
    return form, form, msg, detail, flag

def fetch_team_data(team, venue, games_played=5, use_llm=True, mode="strict", before=None, season_games=15):
    """Main chain. Returns (form_games, season_games, msg, detail, flag).

    strict (default): API-Football only (genuine, venue-filtered, before=as_of) -> else FLAGGED.
    lenient: lenient LLM -> API-Football -> synthetic fallback. Always returns form games.
    season_games: when API data is used, also returns up to season_games venue games for the
    genuine 80/20 season/form blend (None for LLM/synthetic sources).
    """
    api_key, groq_key = get_secrets()
    lenient = (mode == "lenient")

    if lenient:
        if use_llm:
            data, raw, msg, detail, is_flagged = try_ollama_lenient(team, venue, games_played, before=before)
            if data:
                return data, None, f"LLM LOCAL HALLUCINATED - {msg}", detail, False
        if use_llm and groq_key:
            data, raw, msg, detail, is_flagged = try_groq_lenient(team, venue, games_played, groq_key, before=before)
            if data:
                return data, None, f"LLM GROQ HALLUCINATED - {msg}", detail, False
        if api_key:
            form, season, msg, detail, is_flagged = _api_team_games(team, venue, api_key, games_played, min_games=1, before=before, season_games=season_games)
            if form:
                return form, season, f"API-Football - {msg}", detail, False
        data = synthetic_games(team, venue, games_played, before=before)
        return data, None, f"SYNTHETIC FALLBACK - estimated {games_played} {venue} games for {team} before {(before or 'now')[:10]} (UNVERIFIED)", "synthetic fallback - UNVERIFIED", False

    # strict: genuine API-Football data ONLY (LLM never supplies stats in strict mode)
    if api_key:
        form, season, msg, detail, is_flagged = _api_team_games(team, venue, api_key, games_played, min_games=3, before=before, season_games=season_games)
        if form:
            return form, season, f"API-Football VERIFIED - {msg}", detail, False
        reason = msg
    else:
        reason = "API-Football key missing - strict mode requires genuine API data"
    log_flagged_match(team, venue, games_played, reason, "strict-api-only", "FLAGGED - NO GENUINE DATA")
    return None, None, f"FLAGGED - {reason}", "strict mode: no genuine data, no prediction", True

# ---------- LLM explanation (explanations only - never statistics) ----------

def explain_prediction(pred, status, home, away, diff, groq_key=None):
    """Short LLM explanation of an already-computed prediction. Never used for stats.
    Returns text or None. Non-blocking."""
    user = (f"A football prediction engine computed: {pred} | status: {status} | diff {diff:.1f} "
            f"for {home} (home) vs {away} (away). Explain in 2-3 sentences WHY, referencing only "
            f"typical factors (form, home advantage, shot quality). Do NOT invent any specific statistics.")
    sys_p = "You are a football analyst. Explain predictions briefly and honestly. Never fabricate statistics."
    try:
        if groq_key:
            r = requests.post("https://api.groq.com/openai/v1/chat/completions",
                              json={"model": "llama-3.3-70b-versatile",
                                    "messages": [{"role": "system", "content": sys_p}, {"role": "user", "content": user}],
                                    "temperature": 0.3, "max_tokens": 220},
                              headers={"Authorization": f"Bearer {groq_key}", "Content-Type": "application/json"}, timeout=20)
            if r.status_code == 200:
                return r.json()["choices"][0]["message"]["content"].strip()
    except Exception:
        pass
    try:
        r = requests.post("http://localhost:11434/api/chat", json={
            "model": "llama3.2",
            "messages": [{"role": "system", "content": sys_p}, {"role": "user", "content": user}],
            "stream": False, "options": {"temperature": 0.3, "num_predict": 220}
        }, timeout=15)
        if r.status_code == 200:
            return r.json().get("message", {}).get("content", "").strip()
    except Exception:
        pass
    return None

# ---------- live safest-5 scan ----------

def _safety_rank(status_text, diff):
    """Lower tuple = safer. SURE verified > SURE unverified > Double > LEAN (by |diff|) > rest."""
    s = (status_text or "").upper()
    if "FLAGGED" in s or "AVOID" in s:
        return (9, 0)
    if "SURE" in s and "0.5%" not in s:
        return (0, -abs(diff))
    if "SURE" in s:
        return (1, -abs(diff))
    if "DOUBLE" in s or "1%" in s:
        return (2, -abs(diff))
    if "LEAN" in s:
        return (3, -abs(diff))
    return (5, -abs(diff))

def fetch_top_5_safe_matches(api_key=None, date_str=None, games_played=5, mode="strict", use_llm=True, max_scan=12, bankroll=1000, top_n=5, window_days=2):
    """LIVE: fetch real fixtures for a date (default today in Lagos time), predict each, return the safest top_n.

    Returns (results, message). Each result dict carries fixture_id/kickoff/league/date/status/
    pred/status_text/diff/stake/stake_label/verified/home_src/away_src/result.
    If the exact date has no fixtures (off-day, or UTC/Lagos date edge) the search widens to
    date..date+window_days. Empty list + an explicit message when there is no live data or the
    API errored (message says which) - caller falls back to DEMO_MATCHES.
    """
    if api_key is None:
        api_key, _ = get_secrets()
    if date_str is None:
        date_str = lagos_today().isoformat()
    if not api_key:
        return [], "API-Football key missing - set API_FOOTBALL_KEY in secrets for live matches (DEMO list used instead)"
    note = ""
    matches, msg = fetch_fixtures_by_date(date_str, api_key)
    looks_like_error = any(k in msg for k in ("HTTP", "error", "key missing"))
    if not matches and not looks_like_error:
        # genuinely empty day -> widen the window so "matches of the day" still works
        to = (datetime.fromisoformat(date_str).date() + timedelta(days=window_days)).isoformat()
        matches, msg2 = fetch_fixtures_between(date_str, to, api_key)
        note = f" (0 fixtures exactly on {date_str}; widened to {date_str}..{to})"
    if not matches:
        return [], msg  # msg carries the real reason: API error/quota, or genuinely no fixtures
    from v3_logic import predict_v4_3, calculate_bankroll_stake_v4_3  # lazy: avoid import cycle
    results = []
    for m in matches[:max_scan]:
        h, a = m["home"], m["away"]
        as_of = m.get("kickoff") or None  # never use data after kickoff
        hg, h_season, hs, _, _ = fetch_team_data(h, "home", games_played, use_llm=use_llm, mode=mode, before=as_of)
        ag, a_season, asrc, _, _ = fetch_team_data(a, "away", games_played, use_llm=use_llm, mode=mode, before=as_of)
        if not hg or not ag:
            continue
        pred, status, diff, _, unv = predict_v4_3(hg, ag, h, a, bankroll, lenient=(mode == "lenient"),
                                                 home_season=h_season, away_season=a_season)
        if "FLAGGED" in status or "AVOID" in status:
            continue
        stake, label = calculate_bankroll_stake_v4_3(status, bankroll)
        hg_, ag_ = m.get("home_goals"), m.get("away_goals")
        result = ("H" if hg_ > ag_ else "A" if ag_ > hg_ else "D") if (m.get("status") in FINISHED_STATUSES and hg_ is not None and ag_ is not None) else ""
        results.append({
            "fixture_id": m.get("fixture_id"), "kickoff": m.get("kickoff"),
            "home": h, "away": a, "league": m["league"], "date": m["date"], "status": m["status"],
            "score": f"{hg_}-{ag_}" if hg_ is not None else "", "result": result,
            "pred": pred, "status_text": status, "diff": diff, "stake": stake, "stake_label": label,
            "verified": not unv, "home_src": hs, "away_src": asrc,
            "_rank": _safety_rank(status, diff),
        })
    results.sort(key=lambda r: r["_rank"])
    top = results[:top_n]
    for r in top:
        r.pop("_rank", None)
    return top, f"{len(top)} safest of {len(matches)} live matches on {date_str}{note}"

# ---------- backtest support: fill in results in the v2 history ----------

def update_results_in_log(api_key, log_path=None):
    """For v2 history rows with a fixture_id and empty result, fetch the fixture and store H/D/A."""
    log_path = log_path or PREDICTION_LOG_V2
    if not api_key or not log_path.exists():
        return 0, "nothing to update (need API key and existing v2 log)"
    with open(log_path, newline="", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        rows = list(reader)
        header = reader.fieldnames
    if not rows or not header:
        return 0, "log empty"
    updated = 0
    for r in rows:
        if r.get("result") or not r.get("fixture_id"):
            continue
        try:
            fx = fetch_fixture_by_id(int(float(r["fixture_id"])), api_key)
        except Exception:
            fx = None
        if not fx:
            continue
        hg, ag = fx.get("home_goals"), fx.get("away_goals")
        if fx.get("status") in FINISHED_STATUSES and hg is not None and ag is not None:
            r["result"] = "H" if hg > ag else ("A" if ag > hg else "D")
            updated += 1
    if updated:
        with open(log_path, "w", newline="", encoding="utf-8") as f:
            w = csv.DictWriter(f, fieldnames=header)
            w.writeheader()
            w.writerows(rows)
    return updated, f"{updated} results updated from API-Football"
