"""World Cup Bracket Predictor — minimal Streamlit app.

Run with: streamlit run app.py
"""
import streamlit as st
import db
import live_scores

st.set_page_config(page_title="World Cup Predictor", page_icon="🏆", layout="wide")
db.init_db()

# ---------- helpers ----------

def match_label(m):
    home = m["team_home"] or "TBD"
    away = m["team_away"] or "TBD"
    return f"{home} vs {away}"


def result_str(m):
    if m["actual_home"] is None:
        return ""
    return f"({m['actual_home']}-{m['actual_away']})"


def fmt_match_date(m):
    date = m.get("match_date")
    if not date:
        return ""
    try:
        from datetime import datetime
        d = datetime.strptime(date, "%Y-%m-%d")
        s = d.strftime("%b %d, %Y")
    except Exception:
        s = date
    t = m.get("match_time")
    if t:
        s += f"  ·  {t}"
    return s


# ---------- session state ----------
if "group" not in st.session_state:
    st.session_state.group = None
if "player" not in st.session_state:
    st.session_state.player = None

qp = st.query_params
if "g" in qp and st.session_state.group is None:
    g = db.get_group_by_code(qp["g"])
    if g:
        st.session_state.group = g

# ---------- sidebar: entry point ----------
with st.sidebar:
    st.title("🏆 WC Predictor")

    if st.session_state.group is None:
        tab1, tab2 = st.tabs(["Join group", "Create group"])
        with tab1:
            code = st.text_input("Group code", max_chars=6).upper().strip()
            if st.button("Join", key="join_btn"):
                g = db.get_group_by_code(code)
                if g:
                    st.session_state.group = g
                    st.query_params["g"] = code
                    st.rerun()
                else:
                    st.error("No group found with that code.")
        with tab2:
            gname = st.text_input("Group name", placeholder="e.g. Office Pool 2026")
            if st.button("Create", key="create_btn"):
                if gname.strip():
                    code, gid = db.create_group(gname.strip())
                    st.session_state.group = db.get_group_by_code(code)
                    st.query_params["g"] = code
                    st.success(f"Created! Share this code with friends: **{code}**")
                    st.rerun()
                else:
                    st.error("Enter a group name.")
    else:
        group = st.session_state.group
        st.success(f"Group: **{group['name']}**")
        st.code(group["code"], language=None)
        st.caption("Share this code (or the page URL) with friends so they can join.")

        if st.session_state.player is None:
            pname = st.text_input("Your name", key="player_name_input")
            if st.button("Enter"):
                if pname.strip():
                    st.session_state.player = db.get_or_create_player(group["id"], pname)
                    st.rerun()
                else:
                    st.error("Enter your name.")
        else:
            st.info(f"Playing as **{st.session_state.player['name']}**")
            if st.button("Switch player"):
                st.session_state.player = None
                st.rerun()

        st.divider()
        if st.button("Leave group"):
            st.session_state.group = None
            st.session_state.player = None
            st.query_params.clear()
            st.rerun()

# ---------- main area ----------
if st.session_state.group is None:
    st.title("🏆 World Cup Bracket Predictor")
    st.write(
        "Create a group, share the code with friends, and everyone predicts "
        "the knockout bracket — Round of 32 through the Final.\n\n"
        "**Scoring:** 1 point for the correct outcome (win/draw/loss), "
        "+1 bonus point for the exact score (2 points total)."
    )
    st.info("Use the sidebar to create a new group or join an existing one with a code.")
    st.stop()

group = st.session_state.group
gid = group["id"]

tab_predict, tab_bracket, tab_leaderboard, tab_admin = st.tabs(
    ["📝 Predict", "🏆 Bracket", "📊 Leaderboard", "⚙️ Admin (results)"]
)

# ---------- Predict tab ----------
with tab_predict:
    if st.session_state.player is None:
        st.warning("Enter your name in the sidebar to start predicting.")
    else:
        player = st.session_state.player
        round_choice = st.selectbox(
            "Round", db.ROUNDS, format_func=lambda r: db.ROUND_LABELS[r]
        )
        st.markdown(f"### {db.ROUND_LABELS[round_choice]}")
        st.divider()
        matches = db.get_matches(gid, round_choice)
        st.caption("Enter your predicted score for each match. Save updates anytime "
                    "before the actual match kicks off.")
        for m in matches:
            home, away = m["team_home"], m["team_away"]
            if not home or not away:
                st.write(f"Match {m['slot']+1}: TBD vs TBD (waiting on previous round)")
                continue
            cols = st.columns([3, 1, 1, 1])
            cols[0].write(f"**{home}** vs **{away}**")
            date_str = fmt_match_date(m)
            if date_str:
                cols[0].caption(f"📅 {date_str}")
            existing = db.get_prediction(m["id"], player["id"])
            default_h = existing["pred_home"] if existing else 0
            default_a = existing["pred_away"] if existing else 0
            ph = cols[1].number_input("Home", min_value=0, max_value=20, value=default_h,
                                       key=f"ph_{m['id']}", label_visibility="collapsed")
            pa = cols[2].number_input("Away", min_value=0, max_value=20, value=default_a,
                                       key=f"pa_{m['id']}", label_visibility="collapsed")
            if cols[3].button("Save", key=f"save_{m['id']}"):
                db.upsert_prediction(m["id"], player["id"], ph, pa)
                st.toast(f"Saved: {home} {ph}-{pa} {away}")

# ---------- Bracket tab (read-only overview) ----------
with tab_bracket:
    cols = st.columns(len(db.ROUNDS))
    for i, rnd in enumerate(db.ROUNDS):
        with cols[i]:
            st.markdown(f"**{db.ROUND_LABELS[rnd]}**")
            for m in db.get_matches(gid, rnd):
                home = m["team_home"] or "TBD"
                away = m["team_away"] or "TBD"
                res = result_str(m)
                st.markdown(f"{home}\n\nvs {away} {res}")
                st.markdown("---")

# ---------- Leaderboard tab ----------
with tab_leaderboard:
    lb = db.compute_leaderboard(gid)
    if not lb:
        st.write("No results entered yet — leaderboard will populate as matches are scored.")
    else:
        st.table(
            [
                {
                    "Rank": i + 1,
                    "Player": row["name"],
                    "Points": row["points"],
                    "Exact scores": row["exact"],
                    "Correct outcomes": row["correct"],
                }
                for i, row in enumerate(lb)
            ]
        )

# ---------- Admin tab ----------
with tab_admin:
    st.caption(
        "Anyone can access this tab — there's no separate admin login. "
        "Use it to set team names for Round of 32 and enter real match results "
        "as they happen; later rounds auto-fill with winners."
    )

    col_a, col_b = st.columns([1, 3])
    with col_a:
        if st.button("🔄 Refresh real scores", type="primary"):
            with st.spinner("Fetching live scores..."):
                updated, draws, err = live_scores.sync_results(gid, db)
            if err:
                st.error(err)
            else:
                msg = f"Updated {updated} match(es)."
                if draws:
                    msg += f" {draws} draw(s) found — set the penalty winner below."
                st.success(msg)
    with col_b:
        st.caption(
            "Pulls real results from a free, community-maintained World Cup "
            "data feed (openfootball/worldcup.json on GitHub). Updates are "
            "by-hand on their end, so expect some delay after a match ends — "
            "not true live/in-play scores. Draws need a manual penalty-winner "
            "pick below since shootout results aren't in the feed."
        )

    st.divider()
    round_choice2 = st.selectbox(
        "Round to edit", db.ROUNDS, format_func=lambda r: db.ROUND_LABELS[r], key="admin_round"
    )
    matches2 = db.get_matches(gid, round_choice2)
    for m in matches2:
        with st.expander(f"Match {m['slot']+1}: {match_label(m)} {result_str(m)}"):
            c1, c2 = st.columns(2)
            new_home = c1.text_input("Home team", value=m["team_home"] or "", key=f"th_{m['id']}")
            new_away = c2.text_input("Away team", value=m["team_away"] or "", key=f"ta_{m['id']}")
            if st.button("Update team names", key=f"upd_{m['id']}"):
                db.update_team_names(m["id"], new_home, new_away)
                st.rerun()

            st.write("Enter result:")
            c3, c4, c5 = st.columns(3)
            rh = c3.number_input("Home score", min_value=0, max_value=20,
                                  value=m["actual_home"] if m["actual_home"] is not None else 0,
                                  key=f"rh_{m['id']}")
            ra = c4.number_input("Away score", min_value=0, max_value=20,
                                  value=m["actual_away"] if m["actual_away"] is not None else 0,
                                  key=f"ra_{m['id']}")
            if c5.button("Save result", key=f"saveres_{m['id']}"):
                db.set_result(m["id"], rh, ra)
                st.rerun()

            is_saved_draw = (
                m["actual_home"] is not None
                and m["actual_home"] == m["actual_away"]
                and m["team_home"] and m["team_away"]
            )
            if is_saved_draw:
                st.caption("Draw — knockout needs a winner (penalties). Pick who advances:")
                winner = st.radio(
                    "Advances on penalties",
                    [m["team_home"], m["team_away"]],
                    key=f"pen_{m['id']}",
                    horizontal=True,
                )
                if st.button("Confirm penalty winner", key=f"penbtn_{m['id']}"):
                    db.set_draw_winner(m["id"], winner)
                    st.rerun()
