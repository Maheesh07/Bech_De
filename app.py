# app.py -- Bech De (final, Postgres + SQLite compatible)
from flask import (
    Flask, render_template, request, redirect,
    url_for, session, jsonify
)
from werkzeug.security import generate_password_hash, check_password_hash
from datetime import datetime
import os
import csv
from functools import wraps

# DB config
DATABASE_URL = os.environ.get("DATABASE_URL")  # Render will set this when you add a Postgres DB
SQLITE_PATH = "bechde.db"
CODES_CSV = "codes.csv"

# Flask app
app = Flask(__name__)
app.secret_key = os.environ.get("SECRET_KEY", "dev-secret-change-this")

# detect mode
USE_PG = bool(DATABASE_URL)

# Lazy imports for DB drivers
if USE_PG:
    import psycopg
    from psycopg import errors as pg_errors
else:
    import sqlite3

# --------------------------
# DB helper utilities
# --------------------------
def get_db_conn():
    """
    Return a new DB connection.
    - Postgres: psycopg connection
    - SQLite: sqlite3 connection (row_factory set)
    Caller must close() the connection (or use 'with' context).
    """
    if USE_PG:
        # psycopg.connect accepts the DATABASE_URL string
        return psycopg.connect(DATABASE_URL, autocommit=False)
    else:
        conn = sqlite3.connect(SQLITE_PATH, check_same_thread=False)
        conn.row_factory = sqlite3.Row
        return conn

def _pg_row_to_dict(row, cursor):
    if row is None:
        return None
    cols = [d.name for d in cursor.description]
    return {cols[i]: row[i] for i in range(len(cols))}

def _sqlite_row_to_dict(row):
    if row is None:
        return None
    return dict(row)

def adapt_sql(sql):
    """
    Convert SQL that uses ? placeholders (sqlite style)
    into %s placeholders (postgres style) when needed.
    """
    if USE_PG:
        return sql.replace("?", "%s")
    return sql

def db_execute(sql, params=(), fetchone=False, fetchall=False, commit=False):
    """
    Unified DB execute wrapper.
    - sql: use '?' placeholders for params (we convert to %s for Postgres)
    - params: tuple/list
    - fetchone/fetchall/commit as needed
    Returns fetched rows as dict(s) (or None).
    """
    adapted = adapt_sql(sql)
    conn = get_db_conn()
    try:
        if USE_PG:
            with conn.cursor() as cur:
                cur.execute(adapted, params)
                if commit:
                    conn.commit()
                if fetchone:
                    return _pg_row_to_dict(cur.fetchone(), cur)
                if fetchall:
                    rows = cur.fetchall()
                    return [_pg_row_to_dict(r, cur) for r in rows]
                return None
        else:
            cur = conn.cursor()
            cur.execute(adapted, params)
            if commit:
                conn.commit()
            if fetchone:
                return _sqlite_row_to_dict(cur.fetchone())
            if fetchall:
                rows = cur.fetchall()
                return [dict(r) for r in rows]
            return None
    finally:
        conn.close()

# --------------------------
# DB initialization & CSV load
# --------------------------
def init_db():
    """
    Create tables if missing. Works for both Postgres and SQLite.
    Also loads codes from CODES_CSV if 'codes' table is empty.
    """
    create_tables_sql_pg = """
    CREATE TABLE IF NOT EXISTS players (
        id SERIAL PRIMARY KEY,
        name TEXT UNIQUE NOT NULL,
        password_hash TEXT NOT NULL,
        score INTEGER DEFAULT 0
    );

    CREATE TABLE IF NOT EXISTS codes (
        id SERIAL PRIMARY KEY,
        code TEXT UNIQUE NOT NULL,
        used_by_player_id INTEGER,
        used_at TEXT
    );

    CREATE TABLE IF NOT EXISTS scans (
        id SERIAL PRIMARY KEY,
        player_id INTEGER NOT NULL,
        code_id INTEGER NOT NULL,
        scanned_at TEXT NOT NULL
    );
    """

    # For SQLite we adjust SERIAL -> INTEGER AUTOINCREMENT
    create_tables_sql_sqlite = create_tables_sql_pg.replace(
        "SERIAL PRIMARY KEY", "INTEGER PRIMARY KEY AUTOINCREMENT"
    )

    if USE_PG:
        # create tables in Postgres
        conn = get_db_conn()
        try:
            with conn.cursor() as cur:
                cur.execute(create_tables_sql_pg)
            conn.commit()
        finally:
            conn.close()
    else:
        conn = get_db_conn()
        try:
            cur = conn.cursor()
            cur.executescript(create_tables_sql_sqlite)
            conn.commit()
        finally:
            conn.close()

    # Load codes from CSV only if table is empty
    # Check count
    try:
        cnt_row = db_execute("SELECT COUNT(*) as c FROM codes", fetchone=True)
        cnt = None
        if cnt_row:
            # depending on driver we may get dict with 'c' key or column name alias
            # For Postgres psycopg, column name likely 'count' or 'c' depending; we used alias 'c' so check.
            if isinstance(cnt_row, dict):
                cnt = list(cnt_row.values())[0]
            else:
                cnt = cnt_row
        if cnt is None:
            cnt = 0
    except Exception:
        cnt = 0

    if cnt == 0 and os.path.exists(CODES_CSV):
        # load codes
        with open(CODES_CSV, newline="", encoding="utf-8") as f:
            reader = csv.DictReader(f)
            codes = [row.get("code").strip() for row in reader if row.get("code") and row.get("code").strip()]
        if codes:
            # insert in batches
            if USE_PG:
                conn = get_db_conn()
                try:
                    with conn.cursor() as cur:
                        for code in codes:
                            # use ON CONFLICT DO NOTHING
                            cur.execute("INSERT INTO codes (code) VALUES (%s) ON CONFLICT DO NOTHING;", (code,))
                    conn.commit()
                finally:
                    conn.close()
            else:
                conn = get_db_conn()
                try:
                    cur = conn.cursor()
                    for code in codes:
                        cur.execute("INSERT OR IGNORE INTO codes (code) VALUES (?)", (code,))
                    conn.commit()
                finally:
                    conn.close()
        print("Loaded codes from CSV.")

# --------------------------
# Helper decorator
# --------------------------
def login_required(view):
    @wraps(view)
    def wrapped(*args, **kwargs):
        if "player_id" not in session:
            return redirect(url_for("login"))
        return view(*args, **kwargs)
    return wrapped

# --------------------------
# Routes (register/login/scan/api/leaderboard)
# --------------------------
@app.route("/")
def index():
    if "player_id" in session:
        return redirect(url_for("scan"))
    return redirect(url_for("login"))

@app.route("/register", methods=["GET", "POST"])
def register():
    if request.method == "POST":
        name = request.form["name"].strip()
        password = request.form["password"]
        if not name or not password:
            return render_template("register.html", error="Name and password required")

        pw_hash = generate_password_hash(password)
        try:
            # Insert new player
            if USE_PG:
                try:
                    db_execute(
                        "INSERT INTO players (name, password_hash) VALUES (?, ?)",
                        (name, pw_hash),
                        commit=True
                    )
                except Exception as e:
                    # Catch unique violation for Postgres
                    if hasattr(e, "sqlstate") or isinstance(e, Exception):
                        # safe fallback: render conflict
                        return render_template("register.html", error="Name already taken")
                    else:
                        raise
            else:
                try:
                    db_execute(
                        "INSERT INTO players (name, password_hash) VALUES (?, ?)",
                        (name, pw_hash),
                        commit=True
                    )
                except sqlite3.IntegrityError:
                    return render_template("register.html", error="Name already taken")

            return redirect(url_for("login"))
        except Exception:
            # generic fallback
            return render_template("register.html", error="Name already taken")

    return render_template("register.html")

@app.route("/login", methods=["GET", "POST"])
def login():
    if request.method == "POST":
        name = request.form["name"].strip()
        password = request.form["password"]

        player = db_execute(
            "SELECT * FROM players WHERE name = ?",
            (name,),
            fetchone=True
        )

        if player and check_password_hash(player.get("password_hash"), password):
            session["player_id"] = player.get("id")
            session["player_name"] = player.get("name")
            return redirect(url_for("scan"))
        else:
            return render_template("login.html", error="Invalid login")

    return render_template("login.html")

@app.route("/logout")
def logout():
    session.clear()
    return redirect(url_for("login"))

@app.route("/scan")
@login_required
def scan():
    return render_template("scan.html", player_name=session.get("player_name"))

@app.route("/api/scan", methods=["POST"])
@login_required
def api_scan():
    data = request.get_json()
    scanned_code = (data.get("code", "") or "").strip()
    if not scanned_code:
        return jsonify({"status": "error", "message": "No code detected"})

    player_id = session["player_id"]
    now = datetime.utcnow().isoformat()

    # 1) find code
    code_row = db_execute("SELECT * FROM codes WHERE code = ?", (scanned_code,), fetchone=True)
    if not code_row:
        return jsonify({"status": "invalid", "message": "Invalid code"})

    # Depending on DB wrapper, used_by_player_id may be None or null
    used_by = code_row.get("used_by_player_id")
    if used_by:
        return jsonify({"status": "used", "message": "⛔ Already claimed!"})

    # 2) mark as used, insert scan, update player score
    try:
        # update codes
        db_execute(
            "UPDATE codes SET used_by_player_id = ?, used_at = ? WHERE id = ?",
            (player_id, now, code_row.get("id")),
            commit=True
        )

        # insert scan
        db_execute(
            "INSERT INTO scans (player_id, code_id, scanned_at) VALUES (?, ?, ?)",
            (player_id, code_row.get("id"), now),
            commit=True
        )

        # update player score
        db_execute(
            "UPDATE players SET score = score + 1 WHERE id = ?",
            (player_id,),
            commit=True
        )

        # fetch updated score
        row = db_execute("SELECT score FROM players WHERE id = ?", (player_id,), fetchone=True)
        score = row.get("score") if row else 0

        return jsonify({"status": "ok", "message": "🎉 You captured this code!", "score": score})
    except Exception as e:
        # integrity/unique errors or race conditions might occur if two clients try same code at once
        # re-check status
        code_row2 = db_execute("SELECT * FROM codes WHERE code = ?", (scanned_code,), fetchone=True)
        if code_row2 and code_row2.get("used_by_player_id"):
            return jsonify({"status": "used", "message": "⛔ Already claimed!"})
        # else generic error
        return jsonify({"status": "error", "message": "Server error (try again)"}), 500

@app.route("/leaderboard")
def leaderboard():
    players = db_execute("SELECT name, score FROM players ORDER BY score DESC, name ASC", fetchall=True) or []
    return render_template("leaderboard.html", players=players)

# Admin reset route
@app.route("/admin/reset")
def admin_reset():
    # simple protection: require confirm=yes and SECRET_KEY passed as query (optional)
    if request.args.get("confirm") != "yes":
        return "Add ?confirm=yes to confirm reset"

    # optional extra: secret check (uncomment to enforce)
    # if request.args.get("secret") != os.environ.get("ADMIN_RESET_SECRET"):
    #     return "Missing or wrong secret", 403

    # Delete tables / file depending on mode
    if USE_PG:
        try:
            db_execute("DROP TABLE IF EXISTS scans;", commit=True)
            db_execute("DROP TABLE IF EXISTS codes;", commit=True)
            db_execute("DROP TABLE IF EXISTS players;", commit=True)
        except Exception:
            pass
    else:
        if os.path.exists(SQLITE_PATH):
            try:
                os.remove(SQLITE_PATH)
            except Exception:
                pass

    # Re-init DB and reload codes
    init_db()
    return "✔ Reset complete! All data cleared."

# Initialize DB on import/load
with app.app_context():
    init_db()

if __name__ == "__main__":
    app.run(debug=True, host="0.0.0.0", port=5000)

if __name__ == "__main__":
	@app.route("/admin/players")
	def admin_players():
    		# OPTIONAL: protect the page with a simple password
    		admin_pass = request.args.get("pass")
    		expected = os.environ.get("ADMIN_PASS", "bechde123")

    		if admin_pass != expected:
        		return "Unauthorized. Add ?pass=YOUR_PASSWORD", 403

    		rows = db_execute(
        		"SELECT id, name, score FROM players ORDER BY id",
        		fetchall=True
    		) or []

    		# Return a simple HTML table
    		html = """
    		<h2>Registered Players</h2>
    		<table border='1' cellpadding='6' cellspacing='0' style='border-collapse: collapse;'>
        		<tr>
            		<th>ID</th>
            		<th>Name</th>
            		<th>Score</th>
        		</tr>
    		"""
    		for r in rows:
        		html += f"""
        		<tr>
            		<td>{r['id']}</td>
            		<td>{r['name']}</td>
            		<td>{r['score']}</td>
        		</tr>
        		"""

    		html += "</table>"
    		return html

