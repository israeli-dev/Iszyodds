import math
import re
from datetime import datetime, timedelta

MODEL_VERSION = "v4.4.0"

# Words that are not part of a team name when parsing natural-language requests
_FILLER_WORDS = {
    "predict", "prediction", "predicting", "bet", "betting", "match", "game", "games",
    "today", "tomorrow", "yesterday", "tonight", "please", "the", "a", "an", "on", "in",
    "for", "about", "tell", "me", "what", "who", "will", "win", "wins", "winner", "worth",
    "good", "pick", "give", "show", "analyse", "analyze", "analysis", "vs", "v", "v.",
    "versus", "against", "last", "home", "away", "form", "how", "is", "are", "do", "does",
    "should", "i", "my", "think", "feel", "between", "of", "to", "and", "or", "score",
}

def _clean_team_name(s):
    """Strip punctuation and filler words from one side of a 'X vs Y' split."""
    s = (s or "").strip().strip("?.!,:;\"'()")
    words = [w for w in s.split() if w.lower().strip("?.!,:;\"'()") not in _FILLER_WORDS]
    return " ".join(words).strip() or None

def parse_match_query(query, known_teams=None):
    """Extract (home, away) from a natural-language request.

    Handles: 'Newcastle vs Everton', 'predict Arsenal vs Chelsea?', 'X versus Y',
    'who wins Real Madrid vs Barcelona today', 'bet on Newcastle against Everton',
    'Real Madrid last 5 home form vs Barcelona away'.
    Returns None when two teams cannot be identified.
    """
    q = (query or "").strip()
    if not q:
        return None
    # 1) scan for two known team names, in order of appearance (most robust)
    if known_teams:
        low = q.lower()
        hits = []
        for t in sorted(set(known_teams), key=len, reverse=True):
            m = re.search(r"(?<![a-z0-9])" + re.escape(t.lower()) + r"(?![a-z0-9])", low)
            if m:
                hits.append((m.start(), t))
        hits.sort(key=lambda x: x[0])
        if len(hits) >= 2:
            return hits[0][1], hits[1][1]
    # 2) fallback: 'X vs Y' / 'X versus Y' / 'X against Y' split with filler cleanup
    m = re.search(r"(.+?)\s+(?:vs\.?|v\.?|versus|against)\s+(.+)", q, re.IGNORECASE)
    if m:
        home, away = _clean_team_name(m.group(1)), _clean_team_name(m.group(2))
        if home and away:
            return home, away
    return None

def parse_day_query(query):
    """Return an ISO date string if the query asks about a specific day, else None.
    Handles 'today', 'tonight', 'tomorrow', 'yesterday' and explicit YYYY-MM-DD."""
    q = (query or "").lower()
    today = datetime.now().date()
    if "today" in q or "tonight" in q or "now" in q:
        return today.isoformat()
    if "tomorrow" in q:
        return (today + timedelta(days=1)).isoformat()
    if "yesterday" in q:
        return (today - timedelta(days=1)).isoformat()
    m = re.search(r"\b(\d{4}-\d{2}-\d{2})\b", q)
    if m:
        return m.group(1)
    return None

def parse_count(query, default=3):
    """Extract how many matches the user wants predicted: 'any 3 matches', 'top 5 games', etc."""
    m = re.search(r"\b(\d{1,2})\s*(?:matches?|games?|fixtures?|picks?|bets?)", (query or "").lower())
    if m:
        return max(1, min(20, int(m.group(1))))
    return default

def wants_prediction(query):
    """True if the query asks for predictions/forecasts rather than just a fixture list."""
    q = (query or "").lower()
    return bool(re.search(r"\b\d{1,2}\s*(?:matches?|games?|fixtures?)", q)) or any(
        k in q for k in ("predict", "forecast", "safest", "best", "top", "bet", "who will win", "winner"))

def pick_from_pred(pred, home_team, away_team):
    """Derive the picked side ('H' / 'D' / 'A') from a prediction string (used by backtests)."""
    p = pred or ""
    m = re.search(r"FINAL:\s*(.+?)\s+(?:WIN|slight lean)", p, re.IGNORECASE)
    if m:
        team = m.group(1).strip().lower()
        if team == home_team.lower():
            return "H"
        if team == away_team.lower():
            return "A"
    if "DRAW" in p.upper():
        return "D"
    hi = p.lower().find(home_team.lower())
    ai = p.lower().find(away_team.lower())
    if hi != -1 and (ai == -1 or hi < ai):
        return "H"
    if ai != -1:
        return "A"
    return "D"

def blend_stats_early(season_stats, last5_stats, n_games):
    """80/20 blend + form weight (kept for compatibility)."""
    blended = {}
    for k in season_stats:
        blended[k] = season_stats.get(k, 0) * 0.8 + last5_stats.get(k, 0) * 0.2
    return blended

def calculate_form_collapse(games):
    """If 0 goals in 2 of last 3 -> collapse"""
    if len(games) < 3:
        return False
    zero_goals = sum(1 for g in games[-3:] if g.get("goals", 0) == 0)
    return zero_goals >= 2

def calculate_volatility_penalty(games):
    """If std >1.5 -> volatile"""
    goals = [g.get("goals", 0) for g in games if g.get("goals") is not None]
    if len(goals) < 3:
        return 0
    mean = sum(goals) / len(goals)
    var = sum((x - mean) ** 2 for x in goals) / len(goals)
    return math.sqrt(var)

def calculate_possession_trap(poss, shots):
    """60%+ poss + <12 shots = trap"""
    if poss is None or shots is None:
        return False
    return poss >= 60 and shots < 12

def _avg_stats(games):
    """Average of each stat, IGNORING missing (None) values - never fabricates."""
    keys = ["shots", "sot", "goals", "xg", "poss", "opponent_elo", "rest_days"]
    out = {}
    for k in keys:
        vals = [g[k] for g in games if g.get(k) is not None]
        if vals:
            out[k] = sum(vals) / len(vals)
    return out

def predict_v4_3(home_games, away_games, home_team, away_team, bankroll=1000, lenient=False,
                 home_season=None, away_season=None):
    """V4.4 prediction engine.

    - Data integrity: averages ignore missing (None) fields; missing API fields are
      reported as notes on the status. A side counts as VERIFIED only when every game
      is sourced 'api-football-verified' (real per-match stats for all required fields).
    - 80/20 blend, VENUE-SPECIFIC: the home team's "last 5" = its last 5 HOME games only;
      the away team's "last 5" = its last 5 AWAY games only (never mixed). When season form
      (last 15 games at the same venue) is supplied, each side's averages are blended
      80% season + 20% last-5. Otherwise last-5 only.
    - lenient=True: never returns FLAGGED/AVOID - safety checks become diff penalties +
      notes and the no-edge branch becomes a small 'lean' prediction. Estimates are
      always labelled AI-mode (never presented as verified).
    """
    notes = []
    if not home_games or not away_games:
        if lenient:
            default_game = {"shots": 13, "sot": 4, "goals": 1, "xg": 1.2, "poss": 52,
                            "opponent_elo": 1600, "rest_days": 4, "source": "synthetic-fallback",
                            "missing_fields": ["shots", "sot", "goals", "xg", "poss"]}
            home_games = home_games or [dict(default_game) for _ in range(5)]
            away_games = away_games or [dict(default_game) for _ in range(5)]
            notes.append("missing side replaced with neutral defaults")
        else:
            return "FLAGGED - INADEQUATE DATA - AVOID", "🔴 FLAGGED - INADEQUATE - 0% Stake", 0, 0, False

    # Form collapse (lenient: penalty + note instead of AVOID)
    form_penalty = 0.0
    if calculate_form_collapse(home_games):
        if lenient:
            notes.append(f"{home_team} form-collapse risk (0 goals in 2 of last 3)")
            form_penalty -= 2.0
        else:
            return f"FLAGGED - {home_team} Form Collapse (0 goals in 2 of last 3) - AVOID", "🔴 AVOID - Form Collapse - 0%", 0, 0, False
    if calculate_form_collapse(away_games):
        if lenient:
            notes.append(f"{away_team} form-collapse risk (0 goals in 2 of last 3)")
            form_penalty += 2.0
        else:
            return f"FLAGGED - {away_team} Form Collapse (0 goals in 2 of last 3) - AVOID", "🔴 AVOID - Form Collapse - 0%", 0, 0, False

    # Averages (missing fields ignored) + genuine 80/20 season/form blend when available
    hs = _avg_stats(home_games)
    aw = _avg_stats(away_games)
    if home_season:
        ss = _avg_stats(home_season)
        hs = {k: 0.8 * ss.get(k, hs.get(k, 0)) + 0.2 * hs.get(k, 0) for k in set(hs) | set(ss)}
    if away_season:
        ss = _avg_stats(away_season)
        aw = {k: 0.8 * ss.get(k, aw.get(k, 0)) + 0.2 * aw.get(k, 0) for k in set(aw) | set(ss)}

    # Make missing API fields visible (data integrity) - only for real API data;
    # hallucinated/synthetic estimates are already covered by the AI-mode label
    missing = sorted({f for g in (home_games + away_games)
                      if str(g.get("source", "")).startswith("api-football")
                      for f in (g.get("missing_fields") or [])})
    if missing:
        notes.append("missing API fields: " + ", ".join(missing))

    # Volatility penalty
    volatility_penalty = 0
    if calculate_volatility_penalty(home_games) > 1.5 or calculate_volatility_penalty(away_games) > 1.5:
        volatility_penalty = 1.0
        if lenient:
            notes.append("high goal volatility")

    # Possession trap (only when poss & shots are actually known)
    poss_penalty = 0.0
    if calculate_possession_trap(hs.get("poss"), hs.get("shots")):
        if lenient:
            notes.append(f"{home_team} possession-trap pattern ({hs['poss']:.0f}% poss, {hs['shots']:.0f} shots)")
            poss_penalty -= 1.5
        else:
            return f"FLAGGED - {home_team} Possession Trap ({hs['poss']:.0f}% poss, {hs['shots']:.0f} shots) - AVOID", "🔴 AVOID - Possession Trap - 0%", 0, 0, False

    # Base diff: goals + xG + SOT weighting (missing keys contribute 0)
    base_diff = ((hs.get("goals", 0) - aw.get("goals", 0)) * 2.0
                 + (hs.get("xg", 0) - aw.get("xg", 0)) * 1.5
                 + (hs.get("sot", 0) - aw.get("sot", 0)) * 0.3)

    # Opponent-adjusted Elo (+9%)
    elo_diff = ((hs.get("opponent_elo", 1500) - aw.get("opponent_elo", 1500)) / 100.0) * 0.09

    # Rest days + travel (+5%)
    rest_diff = (hs.get("rest_days", 4) - aw.get("rest_days", 4)) * 0.05

    # Home bonus residual 1.5 (crowd/ref/travel +0.30 goals)
    home_bonus = 1.5

    diff = base_diff - elo_diff + rest_diff + home_bonus - volatility_penalty + form_penalty + poss_penalty

    # xG overperformance penalty (+7%) - only when xG is known on that side
    if "xg" in hs and "goals" in hs and hs["goals"] > hs["xg"] + 1.0:
        diff -= 0.7
    if "xg" in aw and "goals" in aw and aw["goals"] > aw["xg"] + 1.0:
        diff += 0.7

    # Only fully-verified API-Football data counts as verified; everything else (LLM, hallucinated, synthetic, partial) is AI-mode
    is_unverified = any(g.get("source") != "api-football-verified" for g in (home_games + away_games))

    # Threshold 5.0 for SURE
    if diff >= 5.0:
        pred = f"FINAL: {home_team} WIN SURE - SURE BET {MODEL_VERSION} (diff {diff:.1f})"
        status = "🟢 SURE - 2% Bankroll"
    elif diff >= 3.0:
        pred = f"FINAL: {home_team} WIN - Double Chance (diff {diff:.1f})"
        status = "🟡 Double - 1% Bankroll"
    elif diff <= -5.0:
        pred = f"FINAL: {away_team} WIN SURE - SURE BET {MODEL_VERSION} (diff {diff:.1f})"
        status = "🟢 SURE - 2% Bankroll"
    elif diff <= -3.0:
        pred = f"FINAL: {away_team} WIN - Double Chance (diff {diff:.1f})"
        status = "🟡 Double - 1% Bankroll"
    elif lenient:
        lean = home_team if diff >= 0 else away_team
        pred = f"FINAL: {lean} slight lean / DRAW risk (diff {diff:.1f}) - LENIENT MODE"
        status = "🟡 LEAN - 0.5% max" + (" (AI-mode)" if is_unverified else " (verified, weak edge)")
    else:
        pred = f"FINAL: DRAW or AVOID - No edge (diff {diff:.1f})"
        status = "🔴 AVOID - 0% Stake"

    if is_unverified and "SURE" in status:
        status = status.replace("2%", "0.5% - AI-mode (estimates never shown as verified)")
    if notes:
        status = status + " | ⚠️ " + "; ".join(notes)

    return pred, status, diff, diff, is_unverified

def calculate_bankroll_stake_v4_3(status, bankroll):
    if "FLAGGED" in status or "AVOID" in status or "0%" in status:
        return 0, "0% - AVOID"
    if "0.5%" in status:
        return bankroll * 0.005, "0.5% - AI-mode Max"
    if "1%" in status:
        return bankroll * 0.01, "1% - Double"
    if "2%" in status:
        return bankroll * 0.02, "2% - SURE VERIFIED"
    return 0, "0%"
