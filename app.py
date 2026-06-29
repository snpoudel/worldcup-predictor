"""World Cup Bracket Predictor — minimal Streamlit app.

Run with: streamlit run app.py
"""
import os
import streamlit as st

# Expose Turso credentials from Streamlit secrets as env vars so db.py can read them.
# Must happen before importing db.
try:
    for _k in ("TURSO_URL", "TURSO_TOKEN"):
        if _k in st.secrets and not os.environ.get(_k):
            os.environ[_k] = st.secrets[_k]
except Exception:
    pass

from datetime import datetime, timedelta
import db
import live_scores

st.set_page_config(page_title="World Cup Predictor", page_icon="🏆", layout="wide")

# init_db runs 9 HTTP requests to Turso to check/migrate schema — run once per
# server process, not on every Streamlit rerender.
@st.cache_resource
def _init_db_once():
    db.init_db()

_init_db_once()

# ---------------------------------------------------------------------------
# Cached DB reads — shared across all rerenders and all users.
# Clear explicitly after any write that changes the data.
# ---------------------------------------------------------------------------

@st.cache_data(ttl=30)
def _get_matches(gid):
    """Fetch ALL rounds at once — callers partition by round in Python."""
    return db.get_matches(gid)

@st.cache_data(ttl=60)
def _get_player_preds(player_id):
    return db.get_player_predictions(player_id)

@st.cache_data(ttl=60)
def _compute_leaderboard(gid):
    return db.compute_leaderboard(gid)

@st.cache_data(ttl=60)
def _get_all_match_preds(gid):
    """Fetch predictions for ALL rounds at once — no re-query when round selectbox changes."""
    return db.get_all_match_predictions_all_rounds(gid)

@st.cache_data(ttl=60)
def _get_players_with_passwords(gid):
    return db.get_players_with_passwords(gid)


def _matches_for_round(gid, rnd):
    return [m for m in _get_matches(gid) if m["round"] == rnd]


def _is_locked(m):
    """Return True if predictions should be blocked for this match."""
    if m["actual_home"] is not None:
        return True
    if not m.get("match_date"):
        return False
    try:
        time_raw = (m.get("match_time") or "").replace(" UTC", "").strip()
        # Normalise HH:MM:SS → HH:MM in case feed includes seconds
        if time_raw:
            time_raw = ":".join(time_raw.split(":")[:2])
        if time_raw:
            kick_off = datetime.strptime(f"{m['match_date']} {time_raw}", "%Y-%m-%d %H:%M")
        else:
            kick_off = datetime.strptime(m["match_date"], "%Y-%m-%d")
        return datetime.utcnow() >= kick_off - timedelta(minutes=1)
    except Exception:
        return False


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
    st.write("Enter your name and password to start predicting:")
    pname = st.text_input("Your name", placeholder="e.g. Sandeep")
    ppwd = st.text_input(
        "Password",
        type="password",
        placeholder="Choose a password",
    )
    if st.button("Let's go →", type="primary", use_container_width=True):
        if not pname.strip():
            st.error("Enter your name.")
        elif not ppwd.strip():
            st.error("Enter a password.")
        else:
            player, err = db.get_or_create_player(gid, pname, ppwd)
            if err:
                st.error(err)
            else:
                st.session_state.player = player
                st.rerun()
    st.stop()

# ---------- auto-sync live scores once per hour ----------
# Cache the last-synced time in session_state so we only hit the DB once per
# browser session instead of on every Streamlit rerun (every click/interaction).
if "last_synced_checked" not in st.session_state:
    st.session_state.last_synced_checked = db.get_last_synced(gid)

_last = st.session_state.last_synced_checked
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
        st.session_state.last_synced_checked = datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S")
        _get_matches.clear()
        _compute_leaderboard.clear()
        _get_all_match_preds.clear()  # same function, new signature (gid only)

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
        _get_player_preds.clear()
        _compute_leaderboard.clear()
        _get_all_match_preds.clear()
        _get_players_with_passwords.clear()
        _get_matches.clear()
        st.session_state.group = None
        st.session_state.player = None
        st.session_state.confirm_exit = False
        st.query_params.clear()
        st.rerun()
    if _cn.button("No, stay", use_container_width=True):
        st.session_state.confirm_exit = False
        st.rerun()

# ---------------------------------------------------------------------------
# Tab fragments — each tab is an independent fragment so a button click in one
# tab only rerenders that tab's ~80 widgets, not all 400+ across the whole app.
# ---------------------------------------------------------------------------

@st.fragment
def _predict_tab(gid, player_id):
    player = st.session_state.player
    round_choice = st.selectbox(
        "Round", db.ROUNDS, format_func=lambda r: db.ROUND_LABELS[r]
    )
    st.markdown(f"### {db.ROUND_LABELS[round_choice]}")
    st.divider()
    matches = _matches_for_round(gid, round_choice)
    player_preds = _get_player_preds(player_id)
    st.caption("Enter your predicted score for each match. Save updates anytime before kick-off.")
    for m in matches:
        home, away = m["team_home"], m["team_away"]
        if not home or not away:
            st.write(f"Match {m['slot']+1}: TBD vs TBD (waiting on previous round)")
            continue

        locked = _is_locked(m)

        cols = st.columns([3, 1, 1])
        cols[0].write(f"**{home}** vs **{away}**")
        date_str = fmt_match_date(m)
        if date_str:
            cols[0].caption(f"📅 {date_str}")
        existing = player_preds.get(m["id"])
        default_h = existing["pred_home"] if existing else 0
        default_a = existing["pred_away"] if existing else 0
        ph = cols[1].number_input("Home", min_value=0, max_value=20, value=default_h,
                                   key=f"ph_{m['id']}", label_visibility="collapsed",
                                   disabled=locked)
        pa = cols[2].number_input("Away", min_value=0, max_value=20, value=default_a,
                                   key=f"pa_{m['id']}", label_visibility="collapsed",
                                   disabled=locked)
        if locked:
            st.caption("🔒 Predictions locked.")
        elif st.button("Save", key=f"save_{m['id']}", use_container_width=True):
            # Re-verify with a fresh DB read — bypasses cache so stale UI
            # state can never sneak a prediction through for a locked game.
            fresh = db.get_match_by_id(m["id"])
            if fresh and _is_locked(fresh):
                _get_matches.clear()
                st.warning("🔒 This match just locked. Refresh to see the updated state.")
            else:
                db.upsert_prediction(m["id"], player["id"], ph, pa)
                _get_player_preds.clear()
                _compute_leaderboard.clear()
                st.toast(f"Saved: {home} {ph}-{pa} {away}")


@st.fragment
def _bracket_tab(gid):
    all_matches = _get_matches(gid)
    matches_by_round = {}
    for m in all_matches:
        matches_by_round.setdefault(m["round"], []).append(m)
    round_tabs = st.tabs([db.ROUND_LABELS[r] for r in db.ROUNDS])
    for i, rnd in enumerate(db.ROUNDS):
        with round_tabs[i]:
            for m in matches_by_round.get(rnd, []):
                home = m["team_home"] or "TBD"
                away = m["team_away"] or "TBD"
                res = result_str(m)
                st.markdown(f"**{home}** vs **{away}** {res}")
                st.divider()


@st.fragment
def _leaderboard_tab(gid):
    lb = _compute_leaderboard(gid)
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


@st.fragment
def _predictions_tab(gid):
    round_choice = st.selectbox(
        "Round", db.ROUNDS, format_func=lambda r: db.ROUND_LABELS[r], key="preds_round"
    )
    st.markdown(f"### {db.ROUND_LABELS[round_choice]}")
    st.divider()
    all_preds = _get_all_match_preds(gid)
    for m in _matches_for_round(gid, round_choice):
        home = m["team_home"] or "TBD"
        away = m["team_away"] or "TBD"
        header = f"**{home} vs {away}**"
        if m["actual_home"] is not None:
            header += f"  —  result: {m['actual_home']}-{m['actual_away']}"
        st.markdown(header)
        date_str = fmt_match_date(m)
        if date_str:
            st.caption(f"📅 {date_str}")
        preds = all_preds.get(m["id"], [])
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


@st.fragment
def _admin_tab(gid):
    st.caption(
        "Anyone can access this tab — there's no separate admin login. "
        "Use it to enter real match results as they happen; later rounds auto-fill with winners."
    )

    with st.expander("👥 Players & passwords"):
        all_players = _get_players_with_passwords(gid)
        if all_players:
            st.table([{"Name": p["name"], "Password": p["password"] or "(none)"} for p in all_players])
        else:
            st.caption("No players yet.")

    st.divider()

    if st.button("🔄 Refresh real scores", type="primary"):
        with st.spinner("Fetching live scores..."):
            updated, draws, err = live_scores.sync_results(gid, db)
            db.set_last_synced(gid)
            st.session_state.last_synced_checked = datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S")
            _get_matches.clear()
            _compute_leaderboard.clear()
            _get_all_match_preds.clear()
        if err:
            st.error(err)
        else:
            msg = f"Updated {updated} match(es)."
            if draws:
                msg += f" {draws} draw(s) found — set the penalty winner below."
            st.success(msg)
        st.rerun(scope="app")
    _ls = st.session_state.last_synced_checked
    if _ls:
        st.caption(f"Last synced: {_ls} UTC  ·  Auto-syncs every hour")

    st.divider()
    round_choice2 = st.selectbox(
        "Round to edit", db.ROUNDS, format_func=lambda r: db.ROUND_LABELS[r], key="admin_round"
    )
    matches2 = _matches_for_round(gid, round_choice2)
    for m in matches2:
        with st.expander(f"Match {m['slot']+1}: {match_label(m)} {result_str(m)}"):
            c1, c2 = st.columns(2)
            new_home = c1.text_input("Home team", value=m["team_home"] or "", key=f"th_{m['id']}")
            new_away = c2.text_input("Away team", value=m["team_away"] or "", key=f"ta_{m['id']}")
            if st.button("Update team names", key=f"upd_{m['id']}"):
                db.update_team_names(m["id"], new_home, new_away)
                _get_matches.clear()
                st.rerun(scope="app")

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
                _get_matches.clear()
                _compute_leaderboard.clear()
                _get_all_match_preds.clear()
                st.rerun(scope="app")

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
                    _get_matches.clear()
                    st.rerun(scope="app")


# ---------------------------------------------------------------------------
# Render tabs
# ---------------------------------------------------------------------------

tab_predict, tab_bracket, tab_leaderboard, tab_all_preds, tab_admin = st.tabs(
    ["📝 Predict", "🏆 Bracket", "📊 Leaderboard", "👀 Predictions", "⚙️ Admin (results)"]
)

with tab_predict:
    _predict_tab(gid, st.session_state.player["id"])
with tab_bracket:
    _bracket_tab(gid)
with tab_leaderboard:
    _leaderboard_tab(gid)
with tab_all_preds:
    _predictions_tab(gid)
with tab_admin:
    _admin_tab(gid)
