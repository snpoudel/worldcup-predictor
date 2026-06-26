"""World Cup Bracket Predictor — minimal Streamlit app.

Run with: streamlit run app.py
"""
import streamlit as st
from datetime import datetime, timedelta
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

# ---------- sidebar: group info + leave (always accessible on desktop) ----------
with st.sidebar:
    st.title("🏆 WC Predictor")
    if st.session_state.group is not None:
        group = st.session_state.group
        st.success(f"Group: **{group['name']}**")
        st.code(group["code"], language=None)
        st.caption("Share this code (or the page URL) with friends.")
        if st.session_state.player:
            st.info(f"Playing as **{st.session_state.player['name']}**")
            if st.button("Switch player"):
                st.session_state.player = None
                st.rerun()
        st.divider()
        if st.button("← Back to home"):
            st.session_state.group = None
            st.session_state.player = None
            st.query_params.clear()
            st.rerun()

# ---------- home page: join / create (in main area — works on mobile) ----------
if st.session_state.group is None:
    st.title("🏆 World Cup Predictor 2026")
    st.write(
        "Predict knockout scores with friends — Round of 32 through the Final.  \n"
        "No login or signup needed. **Scoring:** 1 pt for correct outcome, +1 for exact score."
    )
    st.write("")

    tab_join, tab_create = st.tabs(["🔑 Join with code", "➕ Create a group"])

    with tab_join:
        code_input = st.text_input("Group code", max_chars=6, placeholder="e.g. ABC123")
        if st.button("Join →", type="primary", use_container_width=True, key="join_btn"):
            code_clean = code_input.upper().strip()
            g = db.get_group_by_code(code_clean)
            if g:
                st.session_state.group = g
                st.query_params["g"] = code_clean
                st.rerun()
            else:
                st.error("No group found with that code — check it and try again.")

    with tab_create:
        group_count = db.get_group_count()
        if group_count >= db.GROUP_LIMIT:
            st.warning(
                f"Group capacity reached ({group_count}/{db.GROUP_LIMIT}). "
                "No new groups can be created on this app instance."
            )
        else:
            st.caption(f"Groups: {group_count}/{db.GROUP_LIMIT}")
            gname = st.text_input("Group name", placeholder="e.g. Office Pool 2026")
            if st.button("Create group →", type="primary", use_container_width=True, key="create_btn"):
                if gname.strip():
                    code, _gid = db.create_group(gname.strip())
                    st.session_state.group = db.get_group_by_code(code)
                    st.query_params["g"] = code
                    st.rerun()
                else:
                    st.error("Enter a group name.")

    # Existing groups — click to jump straight in
    all_groups = db.get_all_groups()
    if all_groups:
        st.divider()
        st.write("**Existing groups — click to join:**")
        for g in all_groups:
            if st.button(g["name"], key=f"grp_{g['id']}", use_container_width=True):
                st.session_state.group = g
                st.query_params["g"] = g["code"]
                st.rerun()

    # Admin panel — password-protected, only visible to you
    st.divider()
    with st.expander("🔧 Admin"):
        try:
            _admin_pwd = st.secrets.get("ADMIN_PASSWORD", "")
        except Exception:
            _admin_pwd = ""

        if not _admin_pwd:
            st.caption("Set `ADMIN_PASSWORD` in Streamlit Cloud → Settings → Secrets to enable.")
        elif not st.session_state.get("admin_unlocked"):
            _entered = st.text_input("Admin password", type="password", key="admin_pwd_input")
            if st.button("Unlock", key="admin_unlock_btn"):
                if _entered == _admin_pwd:
                    st.session_state.admin_unlocked = True
                    st.rerun()
                else:
                    st.error("Wrong password.")
        else:
            col_hd, col_lock = st.columns([5, 1])
            col_hd.write("**Delete a group — permanent, cannot be undone.**")
            if col_lock.button("Lock", key="admin_lock_btn"):
                st.session_state.admin_unlocked = False
                st.session_state.pop("delete_confirm_gid", None)
                st.rerun()

            _confirm_id = st.session_state.get("delete_confirm_gid")
            for g in db.get_all_groups():
                c1, c2 = st.columns([5, 1])
                c1.write(g["name"])
                if _confirm_id == g["id"]:
                    st.warning(
                        f"Delete **{g['name']}** and all its players, predictions, and "
                        "matches? This cannot be undone."
                    )
                    cy, cn = st.columns(2)
                    if cy.button("Yes, delete", key=f"yes_del_{g['id']}",
                                 type="primary", use_container_width=True):
                        db.delete_group(g["id"])
                        st.session_state.pop("delete_confirm_gid", None)
                        st.rerun()
                    if cn.button("Cancel", key=f"no_del_{g['id']}", use_container_width=True):
                        st.session_state.pop("delete_confirm_gid", None)
                        st.rerun()
                else:
                    if c2.button("🗑️", key=f"del_{g['id']}"):
                        st.session_state.delete_confirm_gid = g["id"]
                        st.rerun()

    st.stop()

group = st.session_state.group
gid = group["id"]

# ---------- player name (in main area — works on mobile) ----------
if st.session_state.player is None:
    st.title(f"🏆 {group['name']}")
    st.write("Enter your name to start predicting:")
    pname = st.text_input("Your name", placeholder="e.g. Sandeep")
    ppwd = st.text_input(
        "Password (optional)",
        type="password",
        placeholder="Set one to protect your account",
    )
    if st.button("Let's go →", type="primary", use_container_width=True):
        if pname.strip():
            player, err = db.get_or_create_player(gid, pname, ppwd or None)
            if err:
                st.error(err)
            else:
                st.session_state.player = player
                st.rerun()
        else:
            st.error("Enter your name.")
    st.stop()

# ---------- auto-sync live scores once per hour ----------
_last = db.get_last_synced(gid)
_needs_sync = True
if _last:
    try:
        _age = datetime.utcnow() - datetime.strptime(_last, "%Y-%m-%d %H:%M:%S")
        _needs_sync = _age > timedelta(hours=1)
    except Exception:
        pass
if _needs_sync:
    with st.spinner("Syncing live scores…"):
        live_scores.sync_results(gid, db)
        db.set_last_synced(gid)

# Status bar — visible on mobile without opening the sidebar
if "confirm_exit" not in st.session_state:
    st.session_state.confirm_exit = False

# Red Exit button — targets the status bar row uniquely via :has() on the caption
st.markdown("""<style>
[data-testid="stHorizontalBlock"]:has(
    > [data-testid="stColumn"]:first-child [data-testid="stCaptionContainer"]
) [data-testid="stColumn"]:last-child button {
    background-color: #ff4b4b;
    color: white;
    border: 1px solid #dc3545;
}
[data-testid="stHorizontalBlock"]:has(
    > [data-testid="stColumn"]:first-child [data-testid="stCaptionContainer"]
) [data-testid="stColumn"]:last-child button:hover {
    background-color: #cc0000;
    border-color: #cc0000;
    color: white;
}
</style>""", unsafe_allow_html=True)

_col_who, _col_exit = st.columns([5, 1])
_col_who.caption(f"👤 **{st.session_state.player['name']}**  ·  {group['name']}")
if _col_exit.button("Exit"):
    st.session_state.confirm_exit = True

if st.session_state.confirm_exit:
    st.warning(
        "This will remove you from the group and **delete all your predictions**. "
        "Are you sure?"
    )
    _cy, _cn = st.columns(2)
    if _cy.button("Yes, exit", type="primary", use_container_width=True):
        db.remove_player(st.session_state.player["id"])
        st.session_state.group = None
        st.session_state.player = None
        st.session_state.confirm_exit = False
        st.query_params.clear()
        st.rerun()
    if _cn.button("No, stay", use_container_width=True):
        st.session_state.confirm_exit = False
        st.rerun()

tab_predict, tab_bracket, tab_leaderboard, tab_all_preds, tab_admin = st.tabs(
    ["📝 Predict", "🏆 Bracket", "📊 Leaderboard", "👀 Predictions", "⚙️ Admin (results)"]
)

# ---------- Predict tab ----------
with tab_predict:
    player = st.session_state.player
    round_choice = st.selectbox(
        "Round", db.ROUNDS, format_func=lambda r: db.ROUND_LABELS[r]
    )
    st.markdown(f"### {db.ROUND_LABELS[round_choice]}")
    st.divider()
    matches = db.get_matches(gid, round_choice)
    st.caption("Enter your predicted score for each match. Save updates anytime before kick-off.")
    for m in matches:
        home, away = m["team_home"], m["team_away"]
        if not home or not away:
            st.write(f"Match {m['slot']+1}: TBD vs TBD (waiting on previous round)")
            continue

        # Lock predictions 1 hour before kick-off (or if result already entered)
        locked = False
        if m.get("match_date") and m.get("match_time"):
            try:
                kick_off = datetime.strptime(
                    f"{m['match_date']} {m['match_time']}", "%Y-%m-%d %H:%M"
                )
                locked = datetime.utcnow() >= kick_off - timedelta(hours=1)
            except Exception:
                pass
        elif m["actual_home"] is not None:
            locked = True

        cols = st.columns([3, 1, 1])
        cols[0].write(f"**{home}** vs **{away}**")
        date_str = fmt_match_date(m)
        if date_str:
            cols[0].caption(f"📅 {date_str}")
        existing = db.get_prediction(m["id"], player["id"])
        default_h = existing["pred_home"] if existing else 0
        default_a = existing["pred_away"] if existing else 0
        ph = cols[1].number_input("Home", min_value=0, max_value=20, value=default_h,
                                   key=f"ph_{m['id']}", label_visibility="collapsed",
                                   disabled=locked)
        pa = cols[2].number_input("Away", min_value=0, max_value=20, value=default_a,
                                   key=f"pa_{m['id']}", label_visibility="collapsed",
                                   disabled=locked)
        if locked:
            st.caption("🔒 Predictions locked — less than 1 hour to kick-off.")
        elif st.button("Save", key=f"save_{m['id']}", use_container_width=True):
            db.upsert_prediction(m["id"], player["id"], ph, pa)
            st.toast(f"Saved: {home} {ph}-{pa} {away}")

# ---------- Bracket tab — tabs per round (mobile-friendly) ----------
with tab_bracket:
    round_tabs = st.tabs([db.ROUND_LABELS[r] for r in db.ROUNDS])
    for i, rnd in enumerate(db.ROUNDS):
        with round_tabs[i]:
            for m in db.get_matches(gid, rnd):
                home = m["team_home"] or "TBD"
                away = m["team_away"] or "TBD"
                res = result_str(m)
                st.markdown(f"**{home}** vs **{away}** {res}")
                st.divider()

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
                    "Total Points": row["points"],
                    "Correct Goal Score": row["exact"],
                    "Correct outcomes": row["correct"],
                }
                for i, row in enumerate(lb)
            ]
        )

# ---------- Predictions tab ----------
with tab_all_preds:
    round_choice3 = st.selectbox(
        "Round", db.ROUNDS, format_func=lambda r: db.ROUND_LABELS[r], key="preds_round"
    )
    st.markdown(f"### {db.ROUND_LABELS[round_choice3]}")
    st.divider()
    for m in db.get_matches(gid, round_choice3):
        home = m["team_home"] or "TBD"
        away = m["team_away"] or "TBD"
        header = f"**{home} vs {away}**"
        if m["actual_home"] is not None:
            header += f"  —  result: {m['actual_home']}-{m['actual_away']}"
        st.markdown(header)
        date_str = fmt_match_date(m)
        if date_str:
            st.caption(f"📅 {date_str}")

        preds = db.get_match_predictions(m["id"])
        if not preds:
            st.caption("No predictions yet.")
        else:
            for p in preds:
                score = f"{p['pred_home']}-{p['pred_away']}"
                icon = ""
                if m["actual_home"] is not None:
                    if p["pred_home"] == m["actual_home"] and p["pred_away"] == m["actual_away"]:
                        icon = " ⭐"
                    else:
                        a_out = "H" if m["actual_home"] > m["actual_away"] else ("A" if m["actual_away"] > m["actual_home"] else "D")
                        p_out = "H" if p["pred_home"] > p["pred_away"] else ("A" if p["pred_away"] > p["pred_home"] else "D")
                        if a_out == p_out:
                            icon = " ✅"
                st.write(f"- {p['name']}: **{score}**{icon}")
        st.divider()

# ---------- Admin tab ----------
with tab_admin:
    st.caption(
        "Anyone can access this tab — there's no separate admin login. "
        "Use it to enter real match results as they happen; later rounds auto-fill with winners."
    )

    if st.button("🔄 Refresh real scores", type="primary"):
        with st.spinner("Fetching live scores..."):
            updated, draws, err = live_scores.sync_results(gid, db)
            db.set_last_synced(gid)
        if err:
            st.error(err)
        else:
            msg = f"Updated {updated} match(es)."
            if draws:
                msg += f" {draws} draw(s) found — set the penalty winner below."
            st.success(msg)
    _ls = db.get_last_synced(gid)
    if _ls:
        st.caption(f"Last synced: {_ls} UTC  ·  Auto-syncs every hour")

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
