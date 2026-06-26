"""SQLite data layer for the World Cup bracket predictor."""
import sqlite3
import secrets
import string
from contextlib import contextmanager

DB_PATH = "predictor.db"

# Bracket structure: round_order defines progression.
ROUNDS = ["R32", "R16", "QF", "SF", "F"]
ROUND_LABELS = {
    "R32": "Round of 32",
    "R16": "Round of 16",
    "QF": "Quarter-final",
    "SF": "Semi-final",
    "F": "Final",
}
MATCHES_PER_ROUND = {"R32": 16, "R16": 8, "QF": 4, "SF": 2, "F": 1}


@contextmanager
def get_conn():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    try:
        yield conn
        conn.commit()
    finally:
        conn.close()


def init_db():
    with get_conn() as conn:
        conn.executescript("""
        CREATE TABLE IF NOT EXISTS groups (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            code TEXT UNIQUE NOT NULL,
            name TEXT NOT NULL,
            created_at TEXT DEFAULT CURRENT_TIMESTAMP
        );

        CREATE TABLE IF NOT EXISTS players (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            group_id INTEGER NOT NULL REFERENCES groups(id),
            name TEXT NOT NULL,
            UNIQUE(group_id, name)
        );

        CREATE TABLE IF NOT EXISTS matches (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            group_id INTEGER NOT NULL REFERENCES groups(id),
            round TEXT NOT NULL,
            slot INTEGER NOT NULL,           -- position within round, 0-indexed
            team_home TEXT,
            team_away TEXT,
            actual_home INTEGER,             -- NULL until result entered
            actual_away INTEGER,
            match_date TEXT,
            match_time TEXT,
            UNIQUE(group_id, round, slot)
        );

        CREATE TABLE IF NOT EXISTS predictions (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            match_id INTEGER NOT NULL REFERENCES matches(id),
            player_id INTEGER NOT NULL REFERENCES players(id),
            pred_home INTEGER NOT NULL,
            pred_away INTEGER NOT NULL,
            UNIQUE(match_id, player_id)
        );
        """)
    # Migration: add columns to existing databases that predate this schema
    with get_conn() as conn:
        for col in ["match_date TEXT", "match_time TEXT"]:
            try:
                conn.execute(f"ALTER TABLE matches ADD COLUMN {col}")
            except Exception:
                pass  # column already exists


def gen_group_code(length=6):
    alphabet = string.ascii_uppercase + string.digits
    return "".join(secrets.choice(alphabet) for _ in range(length))


def create_group(name):
    code = gen_group_code()
    with get_conn() as conn:
        cur = conn.execute(
            "INSERT INTO groups (code, name) VALUES (?, ?)", (code, name)
        )
        group_id = cur.lastrowid
        # Seed empty bracket: R32 gets placeholder teams, later rounds blank (TBD).
        for rnd in ROUNDS:
            n = MATCHES_PER_ROUND[rnd]
            for slot in range(n):
                if rnd == "R32":
                    home, away = f"Team {slot*2 + 1}", f"Team {slot*2 + 2}"
                else:
                    home, away = None, None
                conn.execute(
                    "INSERT INTO matches (group_id, round, slot, team_home, team_away) "
                    "VALUES (?, ?, ?, ?, ?)",
                    (group_id, rnd, slot, home, away),
                )
    return code, group_id


def get_group_by_code(code):
    with get_conn() as conn:
        row = conn.execute("SELECT * FROM groups WHERE code = ?", (code,)).fetchone()
        return dict(row) if row else None


def get_or_create_player(group_id, name):
    name = name.strip()
    with get_conn() as conn:
        row = conn.execute(
            "SELECT * FROM players WHERE group_id = ? AND name = ?", (group_id, name)
        ).fetchone()
        if row:
            return dict(row)
        cur = conn.execute(
            "INSERT INTO players (group_id, name) VALUES (?, ?)", (group_id, name)
        )
        return {"id": cur.lastrowid, "group_id": group_id, "name": name}


def get_matches(group_id, round_=None):
    with get_conn() as conn:
        if round_:
            rows = conn.execute(
                "SELECT * FROM matches WHERE group_id = ? AND round = ? ORDER BY slot",
                (group_id, round_),
            ).fetchall()
        else:
            rows = conn.execute(
                "SELECT * FROM matches WHERE group_id = ? ORDER BY round, slot",
                (group_id,),
            ).fetchall()
        return [dict(r) for r in rows]


def update_team_names(match_id, team_home, team_away, match_date=None, match_time=None):
    with get_conn() as conn:
        conn.execute(
            "UPDATE matches SET team_home=?, team_away=?, "
            "match_date=COALESCE(?,match_date), match_time=COALESCE(?,match_time) WHERE id=?",
            (team_home, team_away, match_date, match_time, match_id),
        )


def update_match_date(match_id, match_date, match_time):
    with get_conn() as conn:
        conn.execute(
            "UPDATE matches SET match_date=COALESCE(?,match_date), match_time=COALESCE(?,match_time) WHERE id=?",
            (match_date, match_time, match_id),
        )


def set_result(match_id, home, away):
    with get_conn() as conn:
        conn.execute(
            "UPDATE matches SET actual_home = ?, actual_away = ? WHERE id = ?",
            (home, away, match_id),
        )
        match = conn.execute(
            "SELECT * FROM matches WHERE id = ?", (match_id,)
        ).fetchone()
        match = dict(match)
    _propagate_winner(match)


def _winner_name(match):
    """Return winner team name, or None if no result yet. Draws need manual
    advance via set_winner_on_draw since this is knockout (no penalty data)."""
    if match["actual_home"] is None or match["actual_away"] is None:
        return None
    if match["actual_home"] > match["actual_away"]:
        return match["team_home"]
    if match["actual_away"] > match["actual_home"]:
        return match["team_away"]
    return None  # draw -> needs penalty shootout winner, set separately


def set_draw_winner(match_id, winner_team_name):
    """For knockout draws decided on penalties: explicitly set who advances."""
    with get_conn() as conn:
        match = dict(conn.execute(
            "SELECT * FROM matches WHERE id = ?", (match_id,)
        ).fetchone())
    _propagate_winner(match, forced_winner=winner_team_name)


def _propagate_winner(match, forced_winner=None):
    winner = forced_winner or _winner_name(match)
    if not winner:
        return
    next_round_map = {"R32": "R16", "R16": "QF", "QF": "SF", "SF": "F"}
    rnd = match["round"]
    if rnd not in next_round_map:
        return  # Final has no next round
    next_round = next_round_map[rnd]
    next_slot = match["slot"] // 2
    is_home = (match["slot"] % 2 == 0)
    with get_conn() as conn:
        next_match = conn.execute(
            "SELECT * FROM matches WHERE group_id = ? AND round = ? AND slot = ?",
            (match["group_id"], next_round, next_slot),
        ).fetchone()
        if not next_match:
            return
        col = "team_home" if is_home else "team_away"
        conn.execute(
            f"UPDATE matches SET {col} = ? WHERE id = ?",
            (winner, next_match["id"]),
        )


def get_prediction(match_id, player_id):
    with get_conn() as conn:
        row = conn.execute(
            "SELECT * FROM predictions WHERE match_id = ? AND player_id = ?",
            (match_id, player_id),
        ).fetchone()
        return dict(row) if row else None


def upsert_prediction(match_id, player_id, pred_home, pred_away):
    with get_conn() as conn:
        conn.execute(
            """INSERT INTO predictions (match_id, player_id, pred_home, pred_away)
               VALUES (?, ?, ?, ?)
               ON CONFLICT(match_id, player_id)
               DO UPDATE SET pred_home = excluded.pred_home, pred_away = excluded.pred_away""",
            (match_id, player_id, pred_home, pred_away),
        )


def get_players(group_id):
    with get_conn() as conn:
        rows = conn.execute(
            "SELECT * FROM players WHERE group_id = ? ORDER BY name", (group_id,)
        ).fetchall()
        return [dict(r) for r in rows]


def compute_leaderboard(group_id):
    """1 point for correct outcome (win/draw/loss direction), +1 bonus (2 total)
    for exact scoreline. Only matches with a result count."""
    matches = [m for m in get_matches(group_id) if m["actual_home"] is not None]
    players = get_players(group_id)
    scores = {p["id"]: {"name": p["name"], "points": 0, "exact": 0, "correct": 0} for p in players}

    with get_conn() as conn:
        for m in matches:
            preds = conn.execute(
                "SELECT * FROM predictions WHERE match_id = ?", (m["id"],)
            ).fetchall()
            actual_outcome = _outcome(m["actual_home"], m["actual_away"])
            for p in preds:
                p = dict(p)
                if p["player_id"] not in scores:
                    continue
                pred_outcome = _outcome(p["pred_home"], p["pred_away"])
                pts = 0
                if pred_outcome == actual_outcome:
                    pts += 1
                    scores[p["player_id"]]["correct"] += 1
                if p["pred_home"] == m["actual_home"] and p["pred_away"] == m["actual_away"]:
                    pts += 1
                    scores[p["player_id"]]["exact"] += 1
                scores[p["player_id"]]["points"] += pts

    leaderboard = sorted(scores.values(), key=lambda s: -s["points"])
    return leaderboard


def _outcome(home, away):
    if home > away:
        return "H"
    if away > home:
        return "A"
    return "D"
