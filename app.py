import re
import streamlit as st
import pathlib
from datetime import date, timedelta
from v3_logic import predict_v4_3, calculate_bankroll_stake_v4_3, parse_match_query, parse_day_query
from data_fetcher_v4_3_2 import (
    fetch_v4_3_stats, load_team_cache, fetch_top_5_safe_matches, DEMO_MATCHES, log_prediction, log_flagged_match,
    FLAGGED_LOG, PREDICTION_LOG, get_secrets, fetch_fixtures_by_date, fetch_fixtures_between,
    FINISHED_STATUSES, UPCOMING_STATUSES,
)

st.set_page_config(page_title="Iszyodds - Real Money Bankroll Protected", page_icon="⚽", layout="wide")

st.title("⚽ Iszyodds V4.3.2 Cloud-Ready - Real Money Bankroll Protected")
st.caption("Chain: Ollama Local → Groq Cloud → API-Football VERIFIED → (lenient: synthetic fallback) | 80/20 blend | thr 5.0 | home bonus 1.5 | Home 5 + Away 5 filter | 🎲 Lenient mode predicts on ANY data (hallucination allowed)")

# Check secrets status (don't show keys)
api_key, groq_key = get_secrets()
with st.sidebar:
    st.header("🔐 Cloud Status")
    if api_key:
        st.success(f"API-Football: ✅ Connected ({api_key[:4]}...{api_key[-4:]})")
    else:
        st.error("API-Football: ❌ Missing - Add to .streamlit/secrets.toml")
    if groq_key:
        st.success(f"Groq Cloud LLM: ✅ Connected ({groq_key[:6]}...)")
    else:
        st.warning("Groq: ⚠️ Missing - Local Ollama only")

    # Check Ollama local
    try:
        import requests
        r = requests.get("http://localhost:11434/api/tags", timeout=2)
        if r.status_code == 200:
            st.success("Ollama Local: ✅ Running")
        else:
            st.warning("Ollama Local: ⚠️ Not running (OK for cloud)")
    except:
        st.info("Ollama Local: ℹ️ Not running - Using Groq Cloud + API-Football (Perfect for Vercel)")

    st.divider()
    st.header("🧪 Data Mode")
    mode_label = st.radio(
        "How should missing data be handled?",
        ["🎲 Lenient - hallucination OK (ALWAYS predict)", "🔒 Strict - flag inadequate data (AVOID 0%)"],
        index=0,
        help="Lenient: the LLM may estimate/fabricate plausible stats and a prediction is always produced (marked UNVERIFIED, max 0.5% stake). Strict: inadequate data is FLAGGED and skipped."
    )
    mode = "lenient" if mode_label.startswith("🎲") else "strict"
    lenient = (mode == "lenient")
    if lenient:
        st.caption("🎲 Lenient mode: predictions are made even on estimated/hallucinated data — UNVERIFIED, 0.5% max stake. Nothing gets flagged.")
    else:
        st.caption("🔒 Strict mode: inadequate data is FLAGGED → AVOID, 0% stake (bankroll protected).")

    bankroll = st.number_input("Bankroll ($)", min_value=10, value=1000, step=100)
    st.divider()
    st.write("**Deployment:**")
    st.code("Vercel: Set env vars API_FOOTBALL_KEY and GROQ_API_KEY\nStreamlit Cloud: Add to Secrets")

    cache = load_team_cache()
    st.write(f"**Cached Teams:** {len(cache)}")
    st.json(cache)

tab1, tab2, tab3, tab4, tab5 = st.tabs(["🎯 Simple Match", "🛡️ Safest 5 (Live)", "💬 Dynamic Request", "📊 Logic Audit", "🚩 Flagged Log"])


def run_prediction(home, away, games_played, use_llm):
    """Shared fetch+predict+render used by tab1 and tab3."""
    with st.spinner(f"Fetching via chain ({mode} mode): Ollama → Groq → API-Football" + (" → synthetic" if lenient else " → FLAGGED") + "..."):
        home_games, home_raw, hs_src, hs_detail, hs_flag = fetch_v4_3_stats(home, "home", games_played, use_llm=use_llm, mode=mode)
        away_games, away_raw, aw_src, aw_detail, aw_flag = fetch_v4_3_stats(away, "away", games_played, use_llm=use_llm, mode=mode)

        if not home_games or not away_games:
            st.error(f"FLAGGED INADEQUATE - AVOID 0%\nHome: {hs_src} - {hs_detail}\nAway: {aw_src} - {aw_detail}")
            log_flagged_match(f"{home} vs {away}", "both", games_played, f"Home: {hs_detail} | Away: {aw_detail}", f"{hs_src} | {aw_src}", "FLAGGED - ALL SOURCES")
            return

        final_pred, status, diff, diff_enh, is_unverified = predict_v4_3(home_games, away_games, home, away, bankroll, lenient=lenient)
        stake_amount, stake_label = calculate_bankroll_stake_v4_3(status, bankroll)

        log_prediction(home, away, hs_src, aw_src, final_pred, status, f"${stake_amount:.0f} ({stake_label})", bankroll, is_unverified, diff_enh)
        if "FLAGGED" in status or "AVOID" in status:
            log_flagged_match(f"{home} vs {away}", "both", games_played, final_pred, f"{hs_src} | {aw_src}", status)

        # Hallucination badge - makes it obvious when data was estimated
        src_all = f"{hs_src} | {aw_src}"
        hallucinated = ("HALLUCINATED" in src_all.upper()) or ("SYNTHETIC" in src_all.upper())
        badge = " 🎲 estimated/hallucinated data" if (lenient and hallucinated) else ""

        if "SURE" in status and not is_unverified:
            st.success(f"{final_pred}\n\n{status}\n\nStake: ${stake_amount:.0f} ({stake_label}) | Bankroll: ${bankroll}")
        elif "FLAGGED" in status or "AVOID" in status:
            st.error(f"{final_pred}\n\n{status} - 0% Stake - Bankroll Protected")
        elif is_unverified or "0.5%" in status:
            st.warning(f"{final_pred}{badge}\n\n🟡 {status}\n\nStake: ${stake_amount:.0f} | Bankroll: ${bankroll}\n\nSource: {hs_src} | {aw_src}")
        else:
            st.info(f"{final_pred}{badge}\n\n{status}\n\nStake: ${stake_amount:.0f}")

        with st.expander("📊 Data Sources & Raw Games"):
            st.write(f"Home Source: {hs_src}")
            st.write(f"Away Source: {aw_src}")
            st.write(f"Home detail: {hs_detail}")
            st.write(f"Away detail: {aw_detail}")
            st.write(f"Is Unverified: {is_unverified} | Mode: {mode}")
            st.write(f"**{home} {games_played} home games:**")
            st.json(home_games)
            st.write(f"**{away} {games_played} away games:**")
            st.json(away_games)


with tab1:
    st.subheader("Single Match Prediction - pick a match or type one")
    src = st.selectbox(
        "Match source",
        ["📅 Today's matches (live)", "✍️ Manual entry", "🗓️ Past match (pick a date)", "🔮 Future matches (next N days)"],
        index=0,
        key="match_src",
        help="Today / Past / Future pull real fixtures from API-Football (needs API_FOOTBALL_KEY). Predictions use each team's recent home/away form."
    )
    home = away = None

    def manual_inputs(key_suffix):
        c1, c2 = st.columns(2)
        with c1:
            h = st.text_input("Home Team", value="Newcastle", key=f"home_{key_suffix}")
        with c2:
            a = st.text_input("Away Team", value="Everton", key=f"away_{key_suffix}")
        return h, a

    if src == "✍️ Manual entry":
        home, away = manual_inputs("manual")
    else:
        if not api_key:
            st.warning("⚠️ API-Football key missing - set API_FOOTBALL_KEY in .streamlit/secrets.toml (or env) to browse real fixtures. Falling back to manual entry.")
            home, away = manual_inputs("noapi")
        else:
            today = date.today()
            matches, msg = [], ""
            if src.startswith("📅"):  # today's matches (live - includes in-play games)
                matches, msg = fetch_fixtures_by_date(today.isoformat(), api_key)
            elif src == "🗓️ Past match (pick a date)":
                d = st.date_input("Match date", value=today - timedelta(days=1), max_value=today, key="past_date")
                matches, msg = fetch_fixtures_by_date(d.isoformat(), api_key)
                matches = [m for m in matches if m["status"] in FINISHED_STATUSES]
                msg += f" (finished only: {len(matches)})"
            else:  # future
                days = st.slider("How many days ahead?", 1, 14, 7, key="future_days")
                matches, msg = fetch_fixtures_between(today.isoformat(), (today + timedelta(days=days)).isoformat(), api_key)
                matches = [m for m in matches if m["status"] in UPCOMING_STATUSES]
                msg += f" (not started / in play: {len(matches)})"

            st.caption(f"API-Football: {msg}")
            if not matches:
                st.info("No matches found for this selection in popular leagues - try another date/range, or use Manual entry.")
                home, away = manual_inputs("fallback")
            else:
                labels = {}
                for m in matches:
                    score = f" {m['home_goals']}-{m['away_goals']}" if m.get("home_goals") is not None else ""
                    labels[f"{m['home']} vs {m['away']}  •  {m['league']}  •  {m['date']}  •  {m['status']}{score}"] = m
                choice = st.selectbox(f"👇 Pick a match ({len(matches)} found)", list(labels.keys()), key="fixture_pick")
                m = labels[choice]
                home, away = m["home"], m["away"]
                score = f" — FT score {m['home_goals']}-{m['away_goals']}" if m.get("home_goals") is not None else ""
                st.write(f"**Selected: {home} vs {away}** — {m['league']} ({m['country']}) — {m['date']} — status {m['status']}{score}")

    games_played = st.slider("Games per venue (Home 5 + Away 5 filter)", 3, 5, 5)
    use_llm = st.checkbox("Use LLM Primary (Ollama → Groq → API-Football)", value=True, help="Uncheck for API-Football ONLY (best for Vercel, 2% VERIFIED SURE)")

    if st.button("🔮 Predict", width='stretch'):
        if home and away:
            run_prediction(home.strip(), away.strip(), games_played, use_llm)
        else:
            st.warning("Enter or pick both teams first.")

with tab2:
    st.subheader("🛡️ Safest 5 Matches - LIVE (not hardcoded)")
    d = st.date_input("Match date (default = today, live)", value=date.today(), key="bulk_date")
    if st.button("🔴 Fetch Safest 5 (live scan)", width='stretch'):
        with st.spinner(f"Scanning live {d.isoformat()} fixtures in popular leagues and predicting each..."):
            top, msg = fetch_top_5_safe_matches(api_key=api_key, date_str=d.isoformat(), games_played=5, mode=mode, use_llm=use_llm, bankroll=bankroll)
        st.caption(f"API-Football: {msg}")
        if not top:
            st.warning("⚠️ No live data - set API_FOOTBALL_KEY in secrets/env for real matches. Showing DEMO matches below (predictions still run in the selected mode).")
            for h, a in DEMO_MATCHES:
                with st.spinner(f"DEMO {h} vs {a}..."):
                    hg, _, hs_src, _, _ = fetch_v4_3_stats(h, "home", 5, use_llm=use_llm, mode=mode)
                    ag, _, aw_src, _, _ = fetch_v4_3_stats(a, "away", 5, use_llm=use_llm, mode=mode)
                    if hg and ag:
                        pred, status, diff, _, is_unv = predict_v4_3(hg, ag, h, a, bankroll, lenient=lenient)
                        stake, label = calculate_bankroll_stake_v4_3(status, bankroll)
                        st.write(f"🧪 DEMO **{h} vs {a}** | {pred} | {status} | Stake ${stake:.0f} | Verified: {not is_unv}")
                    else:
                        st.write(f"🧪 DEMO **{h} vs {a}** | FLAGGED ({hs_src} | {aw_src})")
        else:
            for i, r in enumerate(top, 1):
                badge = "" if r["verified"] else " 🎲 UNVERIFIED"
                score = f" | FT {r['score']}" if r["score"] else ""
                with st.expander(f"#{i} {r['home']} vs {r['away']} — {r['league']} — {r['date']} ({r['status']}){score}", expanded=(i == 1)):
                    st.write(f"**{r['pred']}**")
                    st.write(f"{r['status_text']}{badge} | Stake **${r['stake']:.0f}** ({r['stake_label']}) | Verified: {r['verified']} | diff {r['diff']:.1f}")
                    st.caption(f"Home src: {r['home_src']} | Away src: {r['away_src']}")

with tab3:
    st.subheader("Dynamic Request - Natural Language")
    query = st.text_input("Ask: 'Newcastle vs Everton' | 'predict Arsenal vs Chelsea?' | \"today's matches\" | 'tomorrow matches' | 'matches on 2026-11-05'", key="dyn")
    if st.button("Ask", width='stretch') and query:
        parsed = parse_match_query(query, known_teams=list(load_team_cache().keys()))
        if parsed:
            h, a = parsed
            st.caption(f"Parsed as: **{h}** (home) vs **{a}** (away)")
            run_prediction(h, a, 5, use_llm=True)
        else:
            day = parse_day_query(query)
            if day and api_key:
                matches, msg = fetch_fixtures_by_date(day, api_key)
                st.caption(f"{msg} — {day}")
                if matches:
                    for m in matches[:25]:
                        score = f" {m['home_goals']}-{m['away_goals']}" if m.get("home_goals") is not None else ""
                        st.write(f"- **{m['home']} vs {m['away']}** — {m['league']} — {m['date']} ({m['status']}){score}")
                else:
                    st.info("No matches found that day in popular leagues.")
            elif day:
                st.info("Set API_FOOTBALL_KEY in secrets to list live matches by day (e.g. \"today's matches\").")
            else:
                st.info("Couldn't find two teams or a day in that - try 'Newcastle vs Everton' or \"today's matches\".")

with tab4:
    st.subheader("Logic Audit V4.3.2")
    st.markdown("""
    **Data modes:**
    - 🎲 **Lenient (default)** - hallucination allowed: Ollama/Groq may *estimate* stats (marked `llm-hallucinated`); API-Football real stats preferred; if nothing works, a deterministic **synthetic fallback** is used. A prediction is ALWAYS produced (UNVERIFIED, max 0.5% stake). Nothing is flagged.
    - 🔒 **Strict** - old behaviour: Ollama → Groq → API-Football VERIFIED → FLAGGED AVOID 0% when data is inadequate.

    **Match picker (tab 🎯, default = today):** Today's matches (live, incl. in-play) / Manual / Past match (date picker) / Future matches (next N days) pulled live from API-Football (`/fixtures?date=` and `/fixtures?from=&to=`), filtered to popular leagues.

    **Safest 5 (tab 🛡️):** LIVE scan - fetches all fixtures for the chosen date (default today), predicts each, ranks by safety (SURE verified → SURE unverified → Double → LEAN) and shows the top 5. No more hardcoded list; the old static list is only a clearly-labelled DEMO fallback when no API key is set.

    **Dynamic Request (tab 💬):** parses natural language via `parse_match_query` ('Newcastle vs Everton', 'predict Arsenal vs Chelsea?', 'bet on X against Y'...) and predicts; day queries ('today's matches', 'tomorrow', '2026-11-05') list that day's live fixtures via `parse_day_query`.

    **Blend:** 80% Season + 20% Last5 Home/Away filtered

    **Threshold:** 5.0 for SURE (was 3.5) → fewer SURE, 85% accuracy vs 57%

    **Home Bonus:** 1.5 residual (crowd/ref/travel +0.30 goals) after filtering

    **5 Techniques:**
    1. SOT weighting (not just shots)
    2. xG Overperformance Penalty (+7%)
    3. Rest Days + Travel (+5%)
    4. Opponent-Adjusted Elo weighting (+9%)
    5. Odds filter (coming)

    **Safety:**
    - Strict mode: Form Collapse: 0 goals in 2 of last 3 → AVOID
    - Strict mode: Volatility: std>1.5 → penalty
    - Strict mode: Possession Trap: 60%+ poss + <12 shots → AVOID
    - Strict mode: SOT > shots → FLAGGED
    - Strict mode: Placeholder 12/1.5/430 → FLAGGED FAKE
    - Lenient mode: the same checks become diff penalties + ⚠️ notes on the status (prediction still shown)

    **API-Football verified path:** fixtures are venue-filtered (home/away) and enriched with real per-match statistics (`/fixtures/statistics`) - shots, SOT, xG, possession, passes, dangerous attacks, real goals & dates.

    **Cloud-Ready:** Works on Vercel, Streamlit Cloud, Render - auto-detects Ollama, falls back to Groq + API-Football
    """)

with tab5:
    st.subheader("🚩 Flagged Log - Auto Logging (strict mode only)")
    col1, col2 = st.columns(2)
    with col1:
        if FLAGGED_LOG.exists():
            try:
                import pandas as pd
                df = pd.read_csv(FLAGGED_LOG)
                st.write(f"**{len(df)} flagged:**")
                st.dataframe(df.tail(20), width='stretch')
                st.download_button("Download flagged_matches.csv", df.to_csv(index=False), "flagged_matches.csv", "text/csv")
            except Exception as e:
                st.error(f"Error: {e}")
                st.text(FLAGGED_LOG.read_text()[:2000])
        else:
            st.info("No flagged yet - in lenient mode nothing is flagged; flags appear in strict mode when sources fail")
    with col2:
        if PREDICTION_LOG.exists():
            try:
                import pandas as pd
                df2 = pd.read_csv(PREDICTION_LOG)
                st.write(f"**{len(df2)} predictions:**")
                st.dataframe(df2.tail(20), width='stretch')
                st.download_button("Download prediction_history.csv", df2.to_csv(index=False), "prediction_history.csv", "text/csv")
            except Exception as e:
                st.error(f"Error: {e}")
        else:
            st.info("No predictions logged yet")
    if st.button("Clear Logs"):
        try:
            if FLAGGED_LOG.exists():
                FLAGGED_LOG.unlink()
            if PREDICTION_LOG.exists():
                PREDICTION_LOG.unlink()
            st.success("Logs cleared")
            st.rerun()
        except Exception as e:
            st.error(f"Error: {e}")
