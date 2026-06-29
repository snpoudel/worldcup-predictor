"""SQLite data layer for the World Cup bracket predictor.

Runs against a local SQLite file by default.  When TURSO_URL and TURSO_TOKEN
env vars are set (Streamlit Cloud secrets), all queries are sent to the Turso
database over its HTTP pipeline API so data survives redeployments.
"""
import os
import sqlite3
import secrets
import string
from contextlib import contextmanager

DB_PATH = "predictor.db"
GROUP_LIMIT = 20

ROUNDS = ["R32", "R16", "QF", "SF", "F"]
ROUND_LABELS = {
    "R32": "Round of 32",
    "R16": "Round of 16",
    "QF": "Quarter-final",
    "SF": "Semi-final",
    "F": "Final",
}
MATCHES_PER_ROUND = {"R32": 16, "R16": 8, "QF": 4, "SF": 2, "F": 1}


# ---------------------------------------------------------------------------
# Turso HTTP client — mimics sqlite3's Connection/Cursor interface
# ---------------------------------------------------------------------------

def _encode_arg(v):
    if v is None:
        return {"type": "null"}
    if isinstance(v, int):
        return {"type": "integer", "value": str(v)}
    if isinstance(v, float):
        return {"type": "float", "value": str(v)}
    return {"type": "text", "value": str(v)}


def _decode_val(cell):
    t = cell.get("type")
    if t == "null" or t is None:
        return None
    if t == "integer":
        return int(cell["value"])
    if t == "float":
        return float(cell["value"])
    return cell.get("value")


class _TursoCursor:
    def __init__(self, result):
        cols = [c["name"] for c in result["cols"]]
        self._rows = [
            {col: _decode_val(cell) for col, cell in zip(cols, row)}
            for row in result["rows"]
        ]
        rowid = result.get("last_insert_rowid")
        self.lastrowid = int(rowid) if rowid is not None else None

    def fetchone(self):
        return self._rows[0] if self._rows else None

    def fetchall(self):
        return self._rows


class _TursoConn:
    def __init__(self, url, token):
        # Accept both libsql:// and https:// URL schemes
        self._endpoint = url.replace("libsql://", "https://").rstrip("/") + "/v2/pipeline"
        self._token = token

    def execute(self, sql, params=()):
        import requests as _req
        body = {
            "requests": [
                {"type": "execute", "stmt": {"sql": sql, "args": [_encode_arg(p) for p in params]}},
                {"type": "close"},
            ]
        }
        resp = _req.post(
            self._endpoint,
            json=body,
            headers={"Authorization": f"Bearer {self._token}"},
            timeout=10,
        )
        resp.raise_for_status()
        item = resp.json()["results"][0]
        if item["type"] == "error":
            raise RuntimeError(item["error"]["message"])
        return _TursoCursor(item["response"]["result"])

    def commit(self):
        pass  # Turso auto-commits each statement

    def close(self):
        pass


# ---------------------------------------------------------------------------
# Connection factory
# ---------------------------------------------------------------------------

def _dict_factory(cursor, row):
    return {col[0]: row[idx] for idx, col in enumerate(cursor.description)}


def _connect():
    url = os.environ.get("TURSO_URL", "")
    token = os.environ.get("TURSO_TOKEN", "")
    if url and token:
        return _TursoConn(url, token)
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = _dict_factory
    return conn


@contextmanager
def get_conn():
    conn = _connect()
    if isinstance(conn, sqlite3.Connection):
        conn.execute("PRAGMA foreign_keys = ON")
    try:
        yield conn
        conn.commit()
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# Schema init
# ---------------------------------------------------------------------------

def init_db():
    conn = _connect()
    if isinstance(conn, sqlite3.Connection):
        conn.execute("PRAGMA foreign_keys = ON")

    for ddl in [
        """CREATE TABLE IF NOT EXISTS groups (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            code TEXT UNIQUE NOT NULL,
            name TEXT NOT NULL,
            created_at TEXT DEFAULT CURRENT_TIMESTAMP,
            last_synced_at TEXT
        )""",
        """CREATE TABLE IF NOT EXISTS players (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            group_id INTEGER NOT NULL REFERENCES groups(id),
            name TEXT NOT NULL,
            password_hash TEXT,
            UNIQUE(group_id, name)
        )""",
        """CREATE TABLE IF NOT EXISTS matches (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            group_id INTEGER NOT NULL REFERENCES groups(id),
            round TEXT NOT NULL,
            slot INTEGER NOT NULL,
            team_home TEXT,
            team_away TEXT,
            actual_home INTEGER,
            actual_away INTEGER,
            match_date TEXT,
            match_time TEXT,
            UNIQUE(group_id, round, slot)
        )""",
        """CREATE TABLE IF NOT EXISTS predictions (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            match_id INTEGER NOT NULL REFERENCES matches(id),
            player_id INTEGER NOT NULL REFERENCES players(id),
            pred_home INTEGER NOT NULL,
            pred_away INTEGER NOT NULL,
            UNIQUE(match_id, player_id)
        )""",
    ]:
        conn.execute(ddl)

    conn.commit()
    conn.close()

    # Migration: add columns that didn't exist in older schema versions
    with get_conn() as conn:
        for tbl, col_def in [
            ("matches", "match_date TEXT"),
            ("matches", "match_time TEXT"),
            ("groups",  "last_synced_at TEXT"),
            ("players", "password_hash TEXT"),
        ]:
            try:
                conn.execute(f"ALTER TABLE {tbl} ADD COLUMN {col_def}")
            except Exception:
                pass  # column already exists


# ---------------------------------------------------------------------------
# Group helpers
# ---------------------------------------------------------------------------

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
        for rnd in ROUNDS:
            n = MATCHES_PER_ROUND[rnd]
            for slot in range(n):
                home = f"Team {slot*2 + 1}" if rnd == "R32" else None
                away = f"Team {slot*2 + 2}" if rnd == "R32" else None
                conn.execute(
                    "INSERT INTO matches (group_id, round, slot, team_home, team_away) "
                    "VALUES (?, ?, ?, ?, ?)",
                    (group_id, rnd, slot, home, away),
                )
    return code, group_id


def delete_group(group_id):
    with get_conn() as conn:
        conn.execute(
            "DELETE FROM predictions WHERE match_id IN "
            "(SELECT id FROM matches WHERE group_id=?)", (group_id,)
        )
        conn.execute("DELETE FROM matches WHERE group_id=?", (group_id,))
        conn.execute("DELETE FROM players WHERE group_id=?", (group_id,))
        conn.execute("DELETE FROM groups WHERE id=?", (group_id,))


def get_all_groups():
    with get_conn() as conn:
        rows = conn.execute(
            "SELECT * FROM groups ORDER BY created_at DESC"
        ).fetchall()
        return [dict(r) for r in rows]


def get_group_count():
    with get_conn() as conn:
        return conn.execute("SELECT COUNT(*) as cnt FROM groups").fetchone()["cnt"]


def get_group_by_code(code):
    with get_conn() as conn:
        row = conn.execute("SELECT * FROM groups WHERE code = ?", (code,)).fetchone()
        return dict(row) if row else None


# ---------------------------------------------------------------------------
# Player helpers
# ---------------------------------------------------------------------------

def get_or_create_player(group_id, name, password=None):
    """Returns (player_dict, error_str). error_str is None on success."""
    import hashlib
    name = name.strip()
    pw_hash = hashlib.sha256(password.encode()).hexdigest() if password else None

    with get_conn() as conn:
        row = conn.execute(
            "SELECT * FROM players WHERE group_id = ? AND name = ?", (group_id, name)
        ).fetchone()
        if row:
            player = dict(row)
            if player.get("password_hash"):
                if not password:
                    return None, "This name is password-protected — enter the password."
                if pw_hash != player["password_hash"]:
                    return None, "Wrong password."
            return player, None
        cur = conn.execute(
            "INSERT INTO players (group_id, name, password_hash) VALUES (?, ?, ?)",
            (group_id, name, pw_hash),
        )
        return {"id": cur.lastrowid, "group_id": group_id, "name": name, "password_hash": pw_hash}, None


def get_players(group_id):
    with get_conn() as conn:
        rows = conn.execute(
            "SELECT * FROM players WHERE group_id = ? ORDER BY name", (group_id,)
        ).fetchall()
        return [dict(r) for r in rows]


def remove_player(player_id):
    with get_conn() as conn:
        conn.execute("DELETE FROM predictions WHERE player_id=?", (player_id,))
        conn.execute("DELETE FROM players WHERE id=?", (player_id,))


# ---------------------------------------------------------------------------
# Match helpers
# ---------------------------------------------------------------------------

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
        match = dict(conn.execute(
            "SELECT * FROM matches WHERE id = ?", (match_id,)
        ).fetchone())
    _propagate_winner(match)


def _winner_name(match):
    if match["actual_home"] is None or match["actual_away"] is None:
        return None
    if match["actual_home"] > match["actual_away"]:
        return match["team_home"]
    if match["actual_away"] > match["actual_home"]:
        return match["team_away"]
    return None


def set_draw_winner(match_id, winner_team_name):
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
        return
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


# ---------------------------------------------------------------------------
# Prediction helpers
# ---------------------------------------------------------------------------

def get_match_predictions(match_id):
    with get_conn() as conn:
        rows = conn.execute(
            """SELECT p.name, pr.pred_home, pr.pred_away
               FROM predictions pr JOIN players p ON p.id = pr.player_id
               WHERE pr.match_id = ?
               ORDER BY p.name""",
            (match_id,),
        ).fetchall()
        return [dict(r) for r in rows]


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


# ---------------------------------------------------------------------------
# Live-score sync tracking
# ---------------------------------------------------------------------------

def get_last_synced(group_id):
    with get_conn() as conn:
        row = conn.execute(
            "SELECT last_synced_at FROM groups WHERE id=?", (group_id,)
        ).fetchone()
        return row["last_synced_at"] if row else None


def set_last_synced(group_id):
    with get_conn() as conn:
        conn.execute(
            "UPDATE groups SET last_synced_at=datetime('now') WHERE id=?",
            (group_id,),
        )


# ---------------------------------------------------------------------------
# Leaderboard
# ---------------------------------------------------------------------------

def compute_leaderboard(group_id):
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

    return sorted(scores.values(), key=lambda s: -s["points"])


def _outcome(home, away):
    if home > away:
        return "H"
    if away > home:
        return "A"
    return "D"
