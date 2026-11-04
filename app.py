import re
import json
import streamlit as st
import pathlib
from datetime import date, timedelta
from v3_logic import predict_v4_3, calculate_bankroll_stake_v4_3, parse_match_query, parse_day_query, parse_count, wants_prediction, pick_from_pred, MODEL_VERSION
from data_fetcher_v4_3_2 import (
    fetch_team_data, fetch_v4_3_stats, load_team_cache, fetch_top_5_safe_matches, DEMO_MATCHES,
    log_prediction, log_prediction_v2, log_flagged_match,
    FLAGGED_LOG, PREDICTION_LOG, PREDICTION_LOG_V2, get_secrets,
    fetch_day_fixtures, fetch_fixtures_by_date, fetch_fixtures_between, fetch_fixture_by_id, kickoff_times, update_results_in_log,
    explain_prediction, lagos_today, FINISHED_STATUSES, UPCOMING_STATUSES, get_gemini_key,
)

st.set_page_config(page_title="Iszyodds", page_icon="⚽", layout="wide")

st.title("⚽ Iszyodds")
st.caption("Football match predictions from live fixtures. Strict mode uses verified API data only; lenient (AI-mode) lets the LLM supply the data - always marked AI-mode, never verified.")

api_key, groq_key = get_secrets()
with st.sidebar:
    st.header("Connections")
    if api_key:
        st.success(f"API-Football: connected ({api_key[:4]}...{api_key[-4:]})")
    else:
        st.error("API-Football: not configured (secrets.toml / env)")
    if groq_key:
        st.success(f"Groq LLM: connected ({groq_key[:6]}...)")
    else:
        st.warning("Groq LLM: not configured")
    gemini_key = get_gemini_key()
    if gemini_key:
        st.success(f"Gemini (Google): connected ({gemini_key[:6]}...) - automatic fallback when Groq fails/exhausts its free tier")
    else:
        st.info("Gemini (Google): not configured - add GOOGLE_API_KEY (AIza...) to secrets as an extra fallback")
    try:
        import requests
        r = requests.get("http://localhost:11434/api/tags", timeout=2)
        st.success("Ollama (local): running") if r.status_code == 200 else st.warning("Ollama (local): not running")
    except:
        st.info("Ollama (local): not running - cloud LLMs used")

    st.divider()
    st.header("Mode")
    mode_label = st.radio(
        "Data source",
        ["Strict - verified API data only (default)", "Lenient (AI-mode) - LLM supplies all data"],
        index=0,
        help="Strict: API-Football verified data only, otherwise no prediction. Lenient (AI-mode): the LLM provides fixtures and stats for every feature; estimates are always marked AI-mode, max 0.5% stake, never verified."
    )
    mode = "strict" if mode_label.startswith("Strict") else "lenient"
    lenient = (mode == "lenient")
    st.caption("Lenient mode: every feature (match lists, safest matches, ask, backtest) runs on LLM data when the API is unavailable." if lenient
               else "Strict mode: no genuine API data → no prediction. LLM is used for explanations only.")

    bankroll = st.number_input("Bankroll ($)", min_value=10, value=1000, step=100)
    cache = load_team_cache()
    with st.expander(f"Cached teams ({len(cache)})"):
        st.json(cache)

tab1, tab2, tab3, tab4, tab5, tab6 = st.tabs(["🎯 Match", "🛡️ Safe matches", "💬 Ask", "🔬 Backtest", "📊 Audit", "🗂 Logs"])


def run_prediction(home, away, games_played, use_llm, fixture=None):
    """Shared fetch+predict+render. Binds the selected fixture (id, kickoff UTC/Lagos, as_of)
    to the prediction and to the v2 history log. Used by tab1 and tab3."""
    # --- Stage 1: fixture binding ---
    fixture_id, league_name, kick_utc, kick_local, as_of, result = "", "", "", "", None, ""
    if fixture:
        fixture_id = fixture.get("fixture_id") or ""
        league_name = fixture.get("league", "")
        kick_raw = fixture.get("kickoff") or (fixture.get("date", "") + " 00:00")
        kick_utc, kick_local = kickoff_times(kick_raw)
        as_of = fixture.get("kickoff") or None          # Stage 3: no data after kickoff
        if fixture.get("status") in FINISHED_STATUSES and fixture.get("home_goals") is not None:
            hg_, ag_ = fixture["home_goals"], fixture["away_goals"]
            result = "H" if hg_ > ag_ else ("A" if ag_ > hg_ else "D")

    with st.spinner(f"Fetching genuine data ({mode} mode, as_of={str(as_of)[:16] or 'now'})..."):
        h_form, h_season, hs_src, hs_detail, hs_flag = fetch_team_data(home, "home", games_played, use_llm=use_llm, mode=mode, before=as_of)
        a_form, a_season, aw_src, aw_detail, aw_flag = fetch_team_data(away, "away", games_played, use_llm=use_llm, mode=mode, before=as_of)

        if not h_form or not a_form:
            st.error(f"FLAGGED INADEQUATE - AVOID 0% (strict: no genuine data, no prediction)\nHome: {hs_src} - {hs_detail}\nAway: {aw_src} - {aw_detail}")
            log_flagged_match(f"{home} vs {away}" + (f" (fixture {fixture_id})" if fixture_id else ""), "both", games_played,
                              f"Home: {hs_detail} | Away: {aw_detail}", f"{hs_src} | {aw_src}", "FLAGGED - NO GENUINE DATA")
            return

        final_pred, status, diff, diff_enh, is_unverified = predict_v4_3(
            h_form, a_form, home, away, bankroll, lenient=lenient, home_season=h_season, away_season=a_season)
        stake_amount, stake_label = calculate_bankroll_stake_v4_3(status, bankroll)

        # --- v2 history: fixture-bound, timezone-aware, with snapshot + model version ---
        data_quality = "VERIFIED (full API stats)" if not is_unverified else "UNVERIFIED (partial/estimated)"
        log_prediction_v2({
            "fixture_id": fixture_id, "home": home, "away": away, "league": league_name,
            "kickoff_utc": kick_utc, "kickoff_local": kick_local, "as_of": str(as_of or ""),
            "mode": mode, "home_source": hs_src, "away_source": aw_src, "data_quality": data_quality,
            "prediction": final_pred, "status": status, "stake": f"${stake_amount:.0f} ({stake_label})",
            "bankroll": bankroll, "is_unverified": is_unverified, "diff": diff_enh, "result": result,
            "home_snapshot": json.dumps(h_form)[:3500], "away_snapshot": json.dumps(a_form)[:3500],
        })
        if "FLAGGED" in status or "AVOID" in status:
            log_flagged_match(f"{home} vs {away}" + (f" (fixture {fixture_id})" if fixture_id else ""), "both", games_played, final_pred, f"{hs_src} | {aw_src}", status)

        # hallucination badge - estimates are never presented as verified
        src_all = f"{hs_src} | {aw_src}".upper()
        hallucinated = ("HALLUCINATED" in src_all) or ("SYNTHETIC" in src_all)
        badge = " 🤖 AI-mode (estimated data)" if (lenient and hallucinated) else ""

        if fixture:
            st.write(f"📌 **Fixture #{fixture_id}** — {league_name} — kickoff **{kick_utc} UTC** — **{kick_local}** (Lagos) — status {fixture.get('status')}"
                     + (f" — result {result}" if result else ""))
        if "SURE" in status and not is_unverified:
            st.success(f"{final_pred}\n\n{status}\n\nStake: ${stake_amount:.0f} ({stake_label}) | Bankroll: ${bankroll}")
        elif "FLAGGED" in status or "AVOID" in status:
            st.error(f"{final_pred}\n\n{status} - 0% Stake - Bankroll Protected")
        elif is_unverified or "0.5%" in status:
            st.warning(f"{final_pred}{badge}\n\n🟡 {status}\n\nStake: ${stake_amount:.0f} | Bankroll: ${bankroll}\n\nSource: {hs_src} | {aw_src}")
        else:
            st.info(f"{final_pred}{badge}\n\n{status}\n\nStake: ${stake_amount:.0f}")

        with st.expander("📊 Data Sources, Quality & Snapshots"):
            st.write(f"Data quality: {data_quality} | Mode: {mode} | Model: {MODEL_VERSION}")
            st.write(f"Home Source: {hs_src}")
            st.write(f"Away Source: {aw_src}")
            st.write(f"Home detail: {hs_detail}")
            st.write(f"Away detail: {aw_detail}")
            st.write(f"as_of (no data after kickoff): {as_of}")
            st.write(f"**{home} form games:**")
            st.json(h_form)
            if h_season:
                st.write(f"**{home} season games (80% blend source):**")
                st.json(h_season)
            st.write(f"**{away} form games:**")
            st.json(a_form)
        if use_llm:
            expl = explain_prediction(final_pred, status, home, away, diff, groq_key=groq_key)
            if expl:
                st.caption("🤖 LLM explanation (explanations only - not used for statistics): " + expl)


def show_source_msg(msg, empty):
    """Show a data-source message. When the cloud LLM quota (HTTP 429 / rate limit) is the
    reason, say so explicitly with what to do (local Ollama / daily reset at 00:00 UTC)."""
    is_err = empty and any(k in msg for k in ("HTTP", "error", "key missing", "429", "rate limit", "rate_limit"))
    m_low = msg.lower()
    is_quota = "429" in msg or "rate_limit" in m_low or ("groq" in m_low and "rate limit" in m_low)
    if is_err and is_quota:
        st.warning(f"Cloud LLM quota reached (Groq free tier is exhaustible - 429 rate limit): {msg} "
                   "Tips: run Ollama locally (`ollama run llama3.2`) for unlimited local AI-mode, "
                   "or retry after the daily reset (00:00 UTC).")
    elif is_err:
        st.error(f"Data source unavailable: {msg}")
    else:
        st.caption(msg)


def render_scan_result(r, i):
    """Render one live-scan result (tab2/tab3) and log it to the v2 history."""
    badge = "" if r["verified"] else " 🤖 AI-mode"
    score = f" | FT {r['score']}" if r.get("score") else ""
    ku, kl = kickoff_times(r.get("kickoff") or r["date"])
    with st.expander(f"#{i} {r['home']} vs {r['away']} — {r['league']} — {r['date']} ({r['status']}){score}", expanded=(i == 1)):
        st.write(f"**{r['pred']}**")
        st.write(f"{r['status_text']}{badge} | Stake **${r['stake']:.0f}** ({r['stake_label']}) | Verified: {r['verified']} | diff {r['diff']:.1f}")
        st.write(f"📌 Fixture #{r['fixture_id']} — kickoff {ku} UTC / {kl}" + (f" — result {r['result']}" if r.get("result") else ""))
        tier_names = {0: "verified API", 1: "partial API", 2: "LLM-supplied (AI-mode)", 3: "synthetic (AI-mode)"}
        st.caption(f"Data: {tier_names.get(r.get('data_tier', 0), '?')} | Home src: {r['home_src']} | Away src: {r['away_src']}")
    log_prediction_v2({
        "fixture_id": r["fixture_id"], "home": r["home"], "away": r["away"], "league": r["league"],
        "kickoff_utc": ku, "kickoff_local": kl, "as_of": str(r.get("kickoff") or ""), "mode": mode,
        "home_source": r["home_src"], "away_source": r["away_src"],
        "data_quality": "VERIFIED (full API stats)" if r["verified"] else "AI-mode (partial/estimated)",
        "prediction": r["pred"], "status": r["status_text"], "stake": f"${r['stake']:.0f} ({r['stake_label']})",
        "bankroll": bankroll, "is_unverified": not r["verified"], "diff": r["diff"], "result": r.get("result", ""),
    })


with tab1:
    st.subheader("Match prediction")
    src = st.selectbox(
        "Match source",
        ["📅 Today's matches", "✍️ Manual entry", "🗓️ Past match (pick a date)", "🔮 Future matches (next N days)"],
        index=0,
        key="match_src",
        help="Fixtures come from API-Football (strict) or the LLM (lenient). Form data is always limited to before the kickoff."
    )
    home = away = None
    selected_fixture = None

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
        today = lagos_today()  # 'today' in Lagos time, not UTC (API-Football dates are UTC)
        matches, msg = [], ""
        if src.startswith("📅"):
            target = today.isoformat()
            matches, msg, fsrc = fetch_day_fixtures(target, api_key=api_key, groq_key=groq_key, mode=mode, n=30)
            # today's matches = ALL unplayed matches of the day, including live ones; exclude finished
            matches = [m for m in matches if m["status"] in UPCOMING_STATUSES]
            msg += f" (today {target}, unplayed + live: {len(matches)})"
        elif src == "🗓️ Past match (pick a date)":
            d = st.date_input("Match date", value=today - timedelta(days=1), max_value=today, key="past_date")
            matches, msg, fsrc = fetch_day_fixtures(d.isoformat(), api_key=api_key, groq_key=groq_key, mode=mode, n=30)
            if fsrc == "api":
                matches = [m for m in matches if m["status"] in FINISHED_STATUSES]
                msg += f" (finished only: {len(matches)})"
        else:
            days = st.slider("How many days ahead?", 1, 14, 7, key="future_days")
            if api_key:
                matches, msg = fetch_fixtures_between(today.isoformat(), (today + timedelta(days=days)).isoformat(), api_key)
                matches = [m for m in matches if m["status"] in UPCOMING_STATUSES]
                msg += f" (not started / in play: {len(matches)})"
            else:
                matches, msg, fsrc = fetch_day_fixtures((today + timedelta(days=days)).isoformat(), api_key=None, groq_key=groq_key, mode=mode, n=30)
                msg += " (approximate day via LLM)"
        if not api_key and mode == "strict":
            st.warning("API-Football key missing and strict mode is on - using manual entry. Switch to lenient to list matches via the LLM.")
            home, away = manual_inputs("noapi")
            matches = []
        st.caption(msg)
        if not matches:
            st.info("No matches found for this selection - try another date/range, or use Manual entry.")
            home, away = manual_inputs("fallback")
        else:
            labels = {}
            for m in matches:
                score = f" {m['home_goals']}-{m['away_goals']}" if m.get("home_goals") is not None else ""
                labels[f"{m['home']} vs {m['away']}  •  {m['league']}  •  {m['date']}  •  {m['status']}{score}"] = m
            choice = st.selectbox(f"👇 Pick a match ({len(matches)} found)", list(labels.keys()), key="fixture_pick")
            m = labels[choice]
            home, away, selected_fixture = m["home"], m["away"], m
            score = f" — FT score {m['home_goals']}-{m['away_goals']}" if m.get("home_goals") is not None else ""
            ku, kl = kickoff_times(m.get("kickoff") or m["date"])
            st.write(f"**Selected: {home} vs {away}** — {m['league']} ({m['country']}) — kickoff {ku} UTC / {kl} — status {m['status']}{score}")

    games_played = st.slider("Games per venue (recent form)", 3, 5, 5)
    use_llm = st.checkbox("Use LLM (explanations in lenient mode; never statistics in strict mode)", value=True)

    if st.button("🔮 Predict", width='stretch'):
        if home and away:
            run_prediction(home.strip(), away.strip(), games_played, use_llm, fixture=selected_fixture)
        else:
            st.warning("Enter or pick both teams first.")

with tab2:
    st.subheader("Safe matches - scan the day (up to 10)")
    c1, c2 = st.columns(2)
    with c1:
        d = st.date_input("Match date", value=lagos_today(), key="bulk_date")
    with c2:
        max_scan = st.slider("Matches to scan", 5, 15, 10, key="bulk_scan")
    st.caption("Matches of that day that are not finished (today/future) are scanned; ranked by data quality first (verified API > partial API > LLM > synthetic = lowest hallucination probability), then by safety.")
    if st.button("🔍 Fetch safe matches", width='stretch'):
        with st.spinner(f"Scanning up to {max_scan} matches of {d.isoformat()} and predicting each..."):
            top, msg = fetch_top_5_safe_matches(api_key=api_key, groq_key=groq_key, date_str=d.isoformat(), games_played=5, mode=mode,
                                               use_llm=use_llm, bankroll=bankroll, top_n=10, max_scan=max_scan)
        show_source_msg(msg, not top)
        if not top:
            st.warning("No matches could be predicted for that day. Showing sample matches below (labelled; estimates are AI-mode in lenient, FLAGGED in strict).")
            for h, a in DEMO_MATCHES:
                with st.spinner(f"Sample: {h} vs {a}..."):
                    hf, hsn, hs_src, _, _ = fetch_team_data(h, "home", 5, use_llm=use_llm, mode=mode)
                    af, asn, aw_src, _, _ = fetch_team_data(a, "away", 5, use_llm=use_llm, mode=mode)
                    if hf and af:
                        pred, status, diff, _, is_unv = predict_v4_3(hf, af, h, a, bankroll, lenient=lenient, home_season=hsn, away_season=asn)
                        stake, label = calculate_bankroll_stake_v4_3(status, bankroll)
                        st.write(f"SAMPLE **{h} vs {a}** | {pred} | {status} | Stake ${stake:.0f} | Verified: {not is_unv}")
                    else:
                        st.write(f"SAMPLE **{h} vs {a}** | no genuine data ({hs_src} | {aw_src})")
        else:
            for i, r in enumerate(top, 1):
                render_scan_result(r, i)

with tab3:
    st.subheader("Ask in natural language")
    query = st.text_input("Ask: 'Newcastle vs Everton' | 'predict Arsenal vs Chelsea?' | \"today's matches\" | 'predict any 3 matches on 2026-11-05' | 'top 5 games tomorrow'", key="dyn")
    if st.button("Ask", width='stretch') and query:
        parsed = parse_match_query(query, known_teams=list(load_team_cache().keys()))
        if parsed:
            h, a = parsed
            st.caption(f"Match: **{h}** (home) vs **{a}** (away)")
            run_prediction(h, a, 5, use_llm=True, fixture=None)
        else:
            day = parse_day_query(query)
            if not day:
                st.info("Couldn't find two teams or a day in that - try 'Newcastle vs Everton', \"today's matches\" or 'predict any 3 matches tomorrow'.")
            elif wants_prediction(query):
                # day + prediction intent -> RUN predictions for the top N matches of that day
                n = parse_count(query, default=3)
                with st.spinner(f"Predicting the top {n} matches of {day}..."):
                    top, msg = fetch_top_5_safe_matches(api_key=api_key, groq_key=groq_key, date_str=day, top_n=n, max_scan=max(10, n), mode=mode, use_llm=use_llm, bankroll=bankroll)
                show_source_msg(msg, not top)
                if not top:
                    st.warning(f"No predictable matches found for {day}.")
                else:
                    for i, r in enumerate(top, 1):
                        render_scan_result(r, i)
            else:
                matches, msg, fsrc = fetch_day_fixtures(day, api_key=api_key, groq_key=groq_key, mode=mode, n=25)
                show_source_msg(f"{msg} — {day}" if matches else msg, not matches)
                if matches:
                    for m in matches[:25]:
                        score = f" {m['home_goals']}-{m['away_goals']}" if m.get("home_goals") is not None else ""
                        ku, kl = kickoff_times(m.get("kickoff") or m["date"])
                        st.write(f"- **{m['home']} vs {m['away']}** — {m['league']} — {kl} ({m['status']}){score}")
                else:
                    st.info("No matches found for that day.")

with tab4:
    st.subheader("Backtest a past date")
    st.caption("Predicts matches of a past date using ONLY data available before each kickoff (`as_of` - no future-data leakage), then scores the pick against the actual result. Runs in the selected mode: strict uses genuine API data; lenient also works when the API is down (LLM/synthetic as-of data, marked AI-mode).")
    d = st.date_input("Backtest date", value=lagos_today() - timedelta(days=1), key="bt_date")
    maxn = st.slider("Max matches to test", 3, 20, 10, key="bt_max")
    if st.button("▶ Run backtest", width='stretch'):
        if not api_key and mode == "strict":
            st.error("Strict backtest needs API_FOOTBALL_KEY in secrets - or switch to lenient mode to backtest on LLM data.")
        else:
            with st.spinner(f"Backtesting {d.isoformat()} ({mode} mode)..."):
                matches, msg, fsrc = fetch_day_fixtures(d.isoformat(), api_key=api_key, groq_key=groq_key, mode=mode, n=maxn)
                finished = [m for m in matches if m["status"] in FINISHED_STATUSES][:maxn] if fsrc == "api" else matches[:maxn]
            st.caption(f"{msg} — {len(finished)} matches tested")
            total = correct = skipped = 0
            for m in finished:
                as_of = m.get("kickoff") or m["date"]
                hf, hsn, hs_, _, _ = fetch_team_data(m["home"], "home", 5, use_llm=use_llm, mode=mode, before=as_of)
                af, asn, as_, _, _ = fetch_team_data(m["away"], "away", 5, use_llm=use_llm, mode=mode, before=as_of)
                hg_, ag_ = m.get("home_goals"), m.get("away_goals")
                actual = ("H" if hg_ > ag_ else "A" if ag_ > hg_ else "D") if (hg_ is not None and ag_ is not None) else "?"
                ku, kl = kickoff_times(m.get("kickoff") or m["date"])
                if not hf or not af:
                    skipped += 1
                    st.write(f"— **{m['home']} vs {m['away']}** {hg_}-{ag_} | no data (skipped)")
                    continue
                pred, status, diff, _, unv = predict_v4_3(hf, af, m["home"], m["away"], bankroll, lenient=lenient,
                                                         home_season=hsn, away_season=asn)
                pick = pick_from_pred(pred, m["home"], m["away"])
                decided = "AVOID" not in status and "FLAGGED" not in status
                ok = (pick == actual) if (decided and actual != "?") else None
                total += 1 if decided else 0
                correct += 1 if ok else 0
                log_prediction_v2({
                    "fixture_id": m.get("fixture_id"), "home": m["home"], "away": m["away"], "league": m["league"],
                    "kickoff_utc": ku, "kickoff_local": kl, "as_of": str(as_of), "mode": f"backtest-{mode}",
                    "home_source": hs_, "away_source": as_,
                    "data_quality": "VERIFIED (full API stats)" if not unv else "AI-mode (partial)",
                    "prediction": pred, "status": status, "stake": "", "bankroll": bankroll,
                    "is_unverified": unv, "diff": diff, "result": actual,
                    "home_snapshot": json.dumps(hf)[:3500], "away_snapshot": json.dumps(af)[:3500],
                })
                mark = "✅" if ok else ("❌" if ok is not None else "—")
                score_disp = f"{hg_}-{ag_}" if hg_ is not None else "?"
                st.write(f"{mark} **{m['home']} vs {m['away']}** {score_disp} ({m['league']}) | pick **{pick}** | actual **{actual}** | {status.split('|')[0].strip()} | verified={not unv}")
            if total:
                st.success(f"Backtest accuracy on decided predictions: **{correct}/{total} = {100 * correct / total:.0f}%** ({skipped} skipped for no data)")
            else:
                st.warning("No decided predictions (all AVOID / no genuine data) - that is the strict-mode guarantee.")

with tab5:
    st.subheader("Audit - how predictions are made")
    st.markdown("""
    **Fixture binding:** every prediction is tied to a specific fixture - league, kickoff in **UTC and Lagos time (WAT)**, status - and logged with a data snapshot, data sources and result.

    **Data integrity:**
    - **Strict (default):** API-Football only; the LLM is never used for statistics. No genuine data → no prediction, 0% stake.
    - API games count as verified only when all required per-match stats (shots, on-target, possession, passes) are genuinely returned; otherwise they are partial and the missing fields are listed. Missing values stay empty - never replaced with invented numbers.
    - **Lenient:** the LLM supplies fixtures and stats when the API is unavailable (Ollama locally, Groq in the cloud with Gemini Flash as fallback); everything it produces is marked **AI-mode**, max 0.5% stake, never labelled verified.

    **No future-data leakage:** when a fixture is selected, team form is fetched with `before=<kickoff>`, so a past match is predicted only from information available before it kicked off. The Backtest tab runs a past date and scores picks against actual results - in strict mode on API data, in lenient mode on LLM data.

    **Engine:** 80/20 blend = 80% season form (last 15 venue games) + 20% recent form (last N venue games) when API data exists, else recent form only. Shots-on-target weighting, xG overperformance penalty, rest days, opponent-adjusted Elo, home advantage 1.5, threshold 5.0 for a strong pick. Safety checks: form collapse, volatility, missing stats (skipped, not guessed).

    **Honest accuracy:** earlier "85% accuracy" claims were not reproducible and were removed - measure real accuracy with the Backtest tab on past dates.

    **Where the data comes from:** "today" is the current date in Lagos time (API dates are UTC); today's list = all unplayed + live matches of that day, finished excluded. Safe matches scans up to 10 matches of the chosen day (auto-widens +2 days if the API day is genuinely empty) and ranks by data quality first (verified API > partial API > LLM > synthetic), then by safety. The Ask tab understands day queries ('today's matches', 'predict any 3 matches on 2026-11-05', 'top 5 games tomorrow') and runs predictions for that day's top matches. Fixture responses are cached 5 min to protect the API quota. API errors/quota are reported explicitly instead of showing "no fixtures".
    """)

with tab6:
    st.subheader("Logs")
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
        else:
            st.info("No flagged entries yet - strict mode flags only when genuine data is missing.")
    with col2:
        if PREDICTION_LOG_V2.exists():
            try:
                import pandas as pd
                df2 = pd.read_csv(PREDICTION_LOG_V2)
                st.write(f"**{len(df2)} predictions:**")
                show = df2.drop(columns=[c for c in ("home_snapshot", "away_snapshot") if c in df2.columns])
                st.dataframe(show.tail(20), width='stretch')
                st.download_button("Download prediction_history_v2.csv", df2.to_csv(index=False), "prediction_history_v2.csv", "text/csv")
            except Exception as e:
                st.error(f"Error: {e}")
        elif PREDICTION_LOG.exists():
            st.info("Only legacy v1 history exists (prediction_history.csv) - new predictions use the v2 schema.")
        else:
            st.info("No predictions logged yet")
    c1, c2 = st.columns(2)
    with c1:
        if st.button("🔄 Update results from API (fill H/D/A for finished fixtures)"):
            n, mmsg = update_results_in_log(api_key)
            st.info(mmsg)
            if n:
                st.rerun()
    with c2:
        if st.button("Clear Logs"):
            try:
                for p in (FLAGGED_LOG, PREDICTION_LOG, PREDICTION_LOG_V2):
                    if p.exists():
                        p.unlink()
                st.success("Logs cleared")
                st.rerun()
            except Exception as e:
                st.error(f"Error: {e}")
