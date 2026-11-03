import re
import json
import streamlit as st
import pathlib
from datetime import date, timedelta
from v3_logic import predict_v4_3, calculate_bankroll_stake_v4_3, parse_match_query, parse_day_query, pick_from_pred, MODEL_VERSION
from data_fetcher_v4_3_2 import (
    fetch_team_data, fetch_v4_3_stats, load_team_cache, fetch_top_5_safe_matches, DEMO_MATCHES,
    log_prediction, log_prediction_v2, log_flagged_match,
    FLAGGED_LOG, PREDICTION_LOG, PREDICTION_LOG_V2, get_secrets,
    fetch_fixtures_by_date, fetch_fixtures_between, fetch_fixture_by_id, kickoff_times, update_results_in_log,
    explain_prediction, FINISHED_STATUSES, UPCOMING_STATUSES,
)

st.set_page_config(page_title="Iszyodds V4.4 - Fixture-Bound Predictions", page_icon="⚽", layout="wide")

st.title(f"⚽ Iszyodds {MODEL_VERSION} - Fixture-Bound, Integrity-First Predictions")
st.caption("Strict by default: no genuine API-Football data → no prediction (FLAGGED). Lenient allows estimates but NEVER labels them verified (0.5% max). "
           "Every prediction is bound to fixture ID + kickoff (UTC & Lagos time) and logged with a data snapshot. LLM = explanations only, never statistics.")

api_key, groq_key = get_secrets()
with st.sidebar:
    st.header("🔐 Cloud Status")
    if api_key:
        st.success(f"API-Football: ✅ Connected ({api_key[:4]}...{api_key[-4:]})")
    else:
        st.error("API-Football: ❌ Missing - Add to .streamlit/secrets.toml")
    if groq_key:
        st.success(f"Groq Cloud LLM: ✅ Connected ({groq_key[:6]}...) - explanations only")
    else:
        st.warning("Groq: ⚠️ Missing - local Ollama explanations only")
    try:
        import requests
        r = requests.get("http://localhost:11434/api/tags", timeout=2)
        if r.status_code == 200:
            st.success("Ollama Local: ✅ Running")
        else:
            st.warning("Ollama Local: ⚠️ Not running (OK for cloud)")
    except:
        st.info("Ollama Local: ℹ️ Not running - Groq/API-Football on cloud")

    st.divider()
    st.header("🧪 Data Mode")
    mode_label = st.radio(
        "Prediction behaviour",
        ["🔒 Strict - genuine API data only (DEFAULT)", "🎲 Lenient - estimates allowed, never shown as verified"],
        index=0,
        help="Strict: API-Football verified data only; inadequate data → FLAGGED, 0% stake. "
             "Lenient: LLM/synthetic estimates may be used for a prediction, always marked UNVERIFIED (max 0.5%)."
    )
    mode = "strict" if mode_label.startswith("🔒") else "lenient"
    lenient = (mode == "lenient")
    if lenient:
        st.caption("🎲 Lenient: predictions on estimated data are UNVERIFIED, max 0.5% stake, never 'verified'.")
    else:
        st.caption("🔒 Strict (default): no genuine data → no prediction. LLM never supplies statistics.")

    bankroll = st.number_input("Bankroll ($)", min_value=10, value=1000, step=100)
    st.divider()
    st.write("**Deployment:**")
    st.code("Vercel: Set env vars API_FOOTBALL_KEY and GROQ_API_KEY\nStreamlit Cloud: Add to Secrets")
    cache = load_team_cache()
    st.write(f"**Cached Teams:** {len(cache)}")
    st.json(cache)

tab1, tab2, tab3, tab4, tab5, tab6 = st.tabs(["🎯 Simple Match", "🛡️ Safest 5 (Live)", "💬 Dynamic Request", "🔬 Backtest", "📊 Logic Audit", "🚩 Logs"])


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
        badge = " 🎲 estimated data - UNVERIFIED" if (lenient and hallucinated) else ""

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


with tab1:
    st.subheader("Single Match Prediction - pick a real fixture or type teams")
    src = st.selectbox(
        "Match source",
        ["📅 Today's matches (live)", "✍️ Manual entry", "🗓️ Past match (pick a date)", "🔮 Future matches (next N days)"],
        index=0,
        key="match_src",
        help="Today / Past / Future pull real fixtures from API-Football (needs API_FOOTBALL_KEY). Predictions use form data BEFORE the kickoff only."
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
        if not api_key:
            st.warning("⚠️ API-Football key missing - set API_FOOTBALL_KEY in secrets/env to browse real fixtures. Falling back to manual entry.")
            home, away = manual_inputs("noapi")
        else:
            today = date.today()
            matches, msg = [], ""
            if src.startswith("📅"):
                matches, msg = fetch_fixtures_by_date(today.isoformat(), api_key)
            elif src == "🗓️ Past match (pick a date)":
                d = st.date_input("Match date", value=today - timedelta(days=1), max_value=today, key="past_date")
                matches, msg = fetch_fixtures_by_date(d.isoformat(), api_key)
                matches = [m for m in matches if m["status"] in FINISHED_STATUSES]
                msg += f" (finished only: {len(matches)})"
            else:
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
    st.subheader("🛡️ Safest 5 Matches - LIVE scan (not hardcoded)")
    d = st.date_input("Match date (default = today, live)", value=date.today(), key="bulk_date")
    if st.button("🔴 Fetch Safest 5 (live scan)", width='stretch'):
        with st.spinner(f"Scanning live {d.isoformat()} fixtures in popular leagues and predicting each (data as of each kickoff)..."):
            top, msg = fetch_top_5_safe_matches(api_key=api_key, date_str=d.isoformat(), games_played=5, mode=mode, use_llm=use_llm, bankroll=bankroll)
        st.caption(f"API-Football: {msg}")
        if not top:
            st.warning("⚠️ No live data - set API_FOOTBALL_KEY in secrets/env for real matches. Showing DEMO matches below (clearly labelled).")
            for h, a in DEMO_MATCHES:
                with st.spinner(f"DEMO {h} vs {a}..."):
                    hf, hsn, hs_src, _, _ = fetch_team_data(h, "home", 5, use_llm=use_llm, mode=mode)
                    af, asn, aw_src, _, _ = fetch_team_data(a, "away", 5, use_llm=use_llm, mode=mode)
                    if hf and af:
                        pred, status, diff, _, is_unv = predict_v4_3(hf, af, h, a, bankroll, lenient=lenient, home_season=hsn, away_season=asn)
                        stake, label = calculate_bankroll_stake_v4_3(status, bankroll)
                        st.write(f"🧪 DEMO **{h} vs {a}** | {pred} | {status} | Stake ${stake:.0f} | Verified: {not is_unv}")
                    else:
                        st.write(f"🧪 DEMO **{h} vs {a}** | FLAGGED ({hs_src} | {aw_src})")
        else:
            for i, r in enumerate(top, 1):
                badge = "" if r["verified"] else " 🎲 UNVERIFIED"
                score = f" | FT {r['score']}" if r["score"] else ""
                ku, kl = kickoff_times(r.get("kickoff") or r["date"])
                with st.expander(f"#{i} {r['home']} vs {r['away']} — {r['league']} — {r['date']} ({r['status']}){score}", expanded=(i == 1)):
                    st.write(f"**{r['pred']}**")
                    st.write(f"{r['status_text']}{badge} | Stake **${r['stake']:.0f}** ({r['stake_label']}) | Verified: {r['verified']} | diff {r['diff']:.1f}")
                    st.write(f"📌 Fixture #{r['fixture_id']} — kickoff {ku} UTC / {kl}" + (f" — result {r['result']}" if r.get("result") else ""))
                    st.caption(f"Home src: {r['home_src']} | Away src: {r['away_src']}")
                log_prediction_v2({
                    "fixture_id": r["fixture_id"], "home": r["home"], "away": r["away"], "league": r["league"],
                    "kickoff_utc": ku, "kickoff_local": kl, "as_of": str(r.get("kickoff") or ""), "mode": mode,
                    "home_source": r["home_src"], "away_source": r["away_src"],
                    "data_quality": "VERIFIED (full API stats)" if r["verified"] else "UNVERIFIED (partial/estimated)",
                    "prediction": r["pred"], "status": r["status_text"], "stake": f"${r['stake']:.0f} ({r['stake_label']})",
                    "bankroll": bankroll, "is_unverified": not r["verified"], "diff": r["diff"], "result": r.get("result", ""),
                })

with tab3:
    st.subheader("Dynamic Request - Natural Language")
    query = st.text_input("Ask: 'Newcastle vs Everton' | 'predict Arsenal vs Chelsea?' | \"today's matches\" | 'tomorrow matches' | 'matches on 2026-11-05'", key="dyn")
    if st.button("Ask", width='stretch') and query:
        parsed = parse_match_query(query, known_teams=list(load_team_cache().keys()))
        if parsed:
            h, a = parsed
            st.caption(f"Parsed as: **{h}** (home) vs **{a}** (away)")
            run_prediction(h, a, 5, use_llm=True, fixture=None)
        else:
            day = parse_day_query(query)
            if day and api_key:
                matches, msg = fetch_fixtures_by_date(day, api_key)
                st.caption(f"{msg} — {day}")
                if matches:
                    for m in matches[:25]:
                        score = f" {m['home_goals']}-{m['away_goals']}" if m.get("home_goals") is not None else ""
                        ku, kl = kickoff_times(m.get("kickoff") or m["date"])
                        st.write(f"- **{m['home']} vs {m['away']}** — {m['league']} — {kl} ({m['status']}){score}")
                else:
                    st.info("No matches found that day in popular leagues.")
            elif day:
                st.info("Set API_FOOTBALL_KEY in secrets to list live matches by day (e.g. \"today's matches\").")
            else:
                st.info("Couldn't find two teams or a day in that - try 'Newcastle vs Everton' or \"today's matches\".")

with tab4:
    st.subheader("🔬 Backtest a past date (no future-data leakage)")
    st.caption("Predicts finished matches of a past date using ONLY team data before each kickoff (`as_of`), then scores the pick against the actual result. Uses genuine API data (strict).")
    d = st.date_input("Backtest date", value=date.today() - timedelta(days=1), key="bt_date")
    maxn = st.slider("Max matches to test", 3, 20, 10, key="bt_max")
    if st.button("▶ Run backtest", width='stretch'):
        if not api_key:
            st.error("Backtest needs API_FOOTBALL_KEY in secrets.")
        else:
            with st.spinner(f"Backtesting {d.isoformat()}..."):
                matches, msg = fetch_fixtures_by_date(d.isoformat(), api_key)
                finished = [m for m in matches if m["status"] in FINISHED_STATUSES][:maxn]
            st.caption(f"{msg} — {len(finished)} finished matches tested")
            total = correct = skipped = 0
            for m in finished:
                as_of = m.get("kickoff") or m["date"]
                hf, hsn, hs_, _, _ = fetch_team_data(m["home"], "home", 5, use_llm=False, mode="strict", before=as_of)
                af, asn, as_, _, _ = fetch_team_data(m["away"], "away", 5, use_llm=False, mode="strict", before=as_of)
                hg_, ag_ = m.get("home_goals"), m.get("away_goals")
                actual = "H" if hg_ > ag_ else ("A" if ag_ > hg_ else "D")
                ku, kl = kickoff_times(m.get("kickoff") or m["date"])
                if not hf or not af:
                    skipped += 1
                    st.write(f"— **{m['home']} vs {m['away']}** {hg_}-{ag_} | NO DATA (skipped)")
                    continue
                pred, status, diff, _, unv = predict_v4_3(hf, af, m["home"], m["away"], bankroll, lenient=False,
                                                         home_season=hsn, away_season=asn)
                pick = pick_from_pred(pred, m["home"], m["away"])
                decided = "AVOID" not in status and "FLAGGED" not in status
                ok = (pick == actual) if decided else None
                total += 1 if decided else 0
                correct += 1 if ok else 0
                log_prediction_v2({
                    "fixture_id": m.get("fixture_id"), "home": m["home"], "away": m["away"], "league": m["league"],
                    "kickoff_utc": ku, "kickoff_local": kl, "as_of": str(as_of), "mode": "backtest",
                    "home_source": hs_, "away_source": as_,
                    "data_quality": "VERIFIED (full API stats)" if not unv else "UNVERIFIED (partial)",
                    "prediction": pred, "status": status, "stake": "", "bankroll": bankroll,
                    "is_unverified": unv, "diff": diff, "result": actual,
                    "home_snapshot": json.dumps(hf)[:3500], "away_snapshot": json.dumps(af)[:3500],
                })
                mark = "✅" if ok else ("❌" if ok is not None else "—")
                st.write(f"{mark} **{m['home']} vs {m['away']}** {hg_}-{ag_} ({m['league']}) | pick **{pick}** | actual **{actual}** | {status.split('|')[0].strip()} | verified={not unv}")
            if total:
                st.success(f"Backtest accuracy on decided predictions: **{correct}/{total} = {100 * correct / total:.0f}%** ({skipped} skipped for no data)")
            else:
                st.warning("No decided predictions (all AVOID / no genuine data) - that is the strict-mode guarantee.")

with tab5:
    st.subheader("📊 Logic Audit " + MODEL_VERSION)
    st.markdown("""
    **Stage 1 - Fixture binding:** every prediction is tied to the selected fixture: `fixture_id`, league, kickoff in **UTC and Africa/Lagos (WAT)**, status, and stored in `prediction_history_v2.csv` with model version, data snapshot, data-quality and result.

    **Stage 2 - Data integrity:**
    - Strict (DEFAULT): API-Football only; the LLM is never used for statistics. No genuine data → FLAGGED, 0% stake.
    - API games are `api-football-verified` only when all required per-match stats (shots, SOT, possession, passes) are genuinely returned; otherwise `api-football-partial` and every missing field is listed in `missing_fields` and shown in the UI. Missing values stay `None` (engine ignores them) - never replaced with invented numbers.
    - Lenient: LLM/synthetic estimates allowed, always UNVERIFIED, max 0.5% stake, never labelled verified.
    - LLM (Groq/Ollama) is used for **explanations only**.

    **Stage 3 - Anti-leakage & backtesting:** when a fixture is selected, team form is fetched with `before=<kickoff>` (API `to=` param + hard date cutoff), so a past match is predicted only from information available before it kicked off. Tab 🔬 Backtest runs a past date and scores picks vs actual results.

    **Engine:** genuine 80/20 blend = 80% season form (last 15 venue games) + 20% recent form (last N venue games) when API data exists, else recent form only. SOT weighting, xG overperformance penalty, rest days, opponent-adjusted Elo, home bonus 1.5, threshold 5.0 for SURE. Safety checks: form collapse, volatility, possession trap (missing stats are skipped, not guessed).

    **Honesty note:** previous "85% accuracy vs 57%" claims were not reproducible and have been removed - use the 🔬 Backtest tab to measure real accuracy on past dates.

    **Live sources:** tab 🎯 defaults to today's real fixtures (live incl. in-play); tab 🛡️ scans all fixtures of the chosen day and ranks the safest 5 (no hardcoded list; DEMO only as labelled fallback without API key); tab 💬 parses natural language and day queries.
    """)

with tab6:
    st.subheader("🚩 Logs - flagged + prediction history (v2)")
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
                st.write(f"**{len(df2)} predictions (v2, fixture-bound):**")
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
