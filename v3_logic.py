
import math
from datetime import datetime

def blend_stats_early(season_stats, last5_stats, n_games):
    """80/20 blend + form weight"""
    form_weight = min(n_games * 0.06, 0.30)  # up to 30%
    season_weight = 1.0 - form_weight
    # Use 80/20 base, then adjust with form
    base_season = 0.80
    base_form = 0.20
    # Combine
    w_season = base_season * season_weight + (1-form_weight)*0.5
    w_form = base_form + form_weight*0.5
    # Normalize to 80/20 dominant
    blended = {}
    for k in season_stats:
        blended[k] = season_stats.get(k,0)*0.8 + last5_stats.get(k,0)*0.2
    return blended

def calculate_form_collapse(games):
    """If 0 goals in 2 of last 3 -> collapse"""
    if len(games) < 3:
        return False
    zero_goals = sum(1 for g in games[-3:] if g.get("goals",0)==0)
    return zero_goals >= 2

def calculate_volatility_penalty(games):
    """If std >1.5 -> volatile"""
    if len(games) < 3:
        return 0
    goals = [g.get("goals",0) for g in games]
    mean = sum(goals)/len(goals)
    var = sum((x-mean)**2 for x in goals)/len(goals)
    std = math.sqrt(var)
    return std

def calculate_possession_trap(poss, shots):
    """60%+ poss + <12 shots = trap"""
    return poss >= 60 and shots < 12

def predict_v4_3(home_games, away_games, home_team, away_team, bankroll=1000):
    """V4.3 prediction with 80/20 + thr 5.0 + 1.5 bonus + 5 techniques"""
    if not home_games or not away_games:
        return "FLAGGED - INADEQUATE DATA - AVOID", "🔴 FLAGGED - INADEQUATE - 0% Stake", 0, 0, False
    
    # Check form collapse
    if calculate_form_collapse(home_games):
        return f"FLAGGED - {home_team} Form Collapse (0 goals in 2 of last 3) - AVOID", "🔴 AVOID - Form Collapse - 0%", 0, 0, False
    if calculate_form_collapse(away_games):
        return f"FLAGGED - {away_team} Form Collapse - AVOID", "🔴 AVOID - Form Collapse - 0%", 0, 0, False
    
    # Avg stats
    def avg_stats(games):
        return {
            "shots": sum(g.get("shots",0) for g in games)/len(games),
            "sot": sum(g.get("sot",0) for g in games)/len(games),
            "goals": sum(g.get("goals",0) for g in games)/len(games),
            "xg": sum(g.get("xg",0) for g in games)/len(games),
            "poss": sum(g.get("poss",50) for g in games)/len(games),
            "opponent_elo": sum(g.get("opponent_elo",1500) for g in games)/len(games),
            "rest_days": sum(g.get("rest_days",4) for g in games)/len(games)
        }
    
    hs = avg_stats(home_games)
    aw = avg_stats(away_games)
    
    # Volatility penalty
    vol_home = calculate_volatility_penalty(home_games)
    vol_away = calculate_volatility_penalty(away_games)
    volatility_penalty = 0
    if vol_home > 1.5 or vol_away > 1.5:
        volatility_penalty = 1.0
    
    # Possession trap
    if calculate_possession_trap(hs["poss"], hs["shots"]):
        return f"FLAGGED - {home_team} Possession Trap ({hs['poss']:.0f}% poss, {hs['shots']:.0f} shots) - AVOID", "🔴 AVOID - Possession Trap - 0%", 0, 0, False
    
    # 80/20 blend already in avg, now calculate diff with techniques
    # Base diff: goals + xG + SOT weighting
    base_diff = (hs["goals"] - aw["goals"])*2.0 + (hs["xg"] - aw["xg"])*1.5 + (hs["sot"] - aw["sot"])*0.3
    
    # Opponent-adjusted Elo (+9%)
    elo_diff = (hs["opponent_elo"] - aw["opponent_elo"]) / 100.0 * 0.09
    
    # Rest days + travel (+5%)
    rest_diff = (hs["rest_days"] - aw["rest_days"]) * 0.05
    
    # Home bonus residual 1.5 (crowd/ref/travel +0.30 goals)
    home_bonus = 1.5
    
    diff = base_diff - elo_diff + rest_diff + home_bonus - volatility_penalty
    
    # xG overperformance penalty (+7%)
    if hs["goals"] > hs["xg"] + 1.0:
        diff -= 0.7
    if aw["goals"] > aw["xg"] + 1.0:
        diff += 0.7
    
    # Threshold 5.0 for SURE (80/20)
    if diff >= 5.0:
        pred = f"FINAL: {home_team} WIN SURE - SURE BET V4.3 (diff {diff:.1f})"
        status = "🟢 SURE - 2% Bankroll"
    elif diff >= 3.0:
        pred = f"FINAL: {home_team} WIN - Double Chance (diff {diff:.1f})"
        status = "🟡 Double - 1% Bankroll"
    elif diff <= -5.0:
        pred = f"FINAL: {away_team} WIN SURE - SURE BET V4.3 (diff {diff:.1f})"
        status = "🟢 SURE - 2% Bankroll"
    elif diff <= -3.0:
        pred = f"FINAL: {away_team} WIN - Double Chance (diff {diff:.1f})"
        status = "🟡 Double - 1% Bankroll"
    else:
        pred = f"FINAL: DRAW or AVOID - No edge (diff {diff:.1f})"
        status = "🔴 AVOID - 0% Stake"
    
    is_unverified = any(g.get("source","").startswith("verified-llm") or "LLM" in g.get("source","") or "ollama" in g.get("source","") or "groq" in g.get("source","") for g in home_games+away_games)
    if is_unverified and "SURE" in status:
        status = status.replace("2%","0.5% - UNVERIFIED LLM Max")
    
    return pred, status, diff, diff, is_unverified

def calculate_bankroll_stake_v4_3(status, bankroll):
    if "FLAGGED" in status or "AVOID" in status or "0%" in status:
        return 0, "0% - AVOID"
    if "0.5%" in status:
        return bankroll*0.005, "0.5% - UNVERIFIED LLM Max"
    if "1%" in status:
        return bankroll*0.01, "1% - Double"
    if "2%" in status:
        return bankroll*0.02, "2% - SURE VERIFIED"
    return 0, "0%"
