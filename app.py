from flask import (
    Flask, render_template, request, redirect,
    url_for, session, jsonify
)
from werkzeug.security import generate_password_hash, check_password_hash
import sqlite3
from datetime import datetime
import csv
import os
from functools import wraps

DB_PATH = "bechde.db"
CODES_CSV = "codes.csv"   # file in same folder as app.py

app = Flask(__name__)
app.secret_key = os.environ.get("SECRET_KEY", "dev-secret-change-this")


def get_db():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def init_db():
    # create tables if not exist
    with get_db() as db:
        db.executescript(
            """
            CREATE TABLE IF NOT EXISTS players (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT UNIQUE NOT NULL,
                password_hash TEXT NOT NULL,
                score INTEGER DEFAULT 0
            );

            CREATE TABLE IF NOT EXISTS codes (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                code TEXT UNIQUE NOT NULL,
                used_by_player_id INTEGER,
                used_at TEXT,
                FOREIGN KEY(used_by_player_id) REFERENCES players(id)
            );

            CREATE TABLE IF NOT EXISTS scans (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                player_id INTEGER NOT NULL,
                code_id INTEGER NOT NULL,
                scanned_at TEXT NOT NULL,
                FOREIGN KEY(player_id) REFERENCES players(id),
                FOREIGN KEY(code_id) REFERENCES codes(id)
            );
            """
        )

    # load codes from CSV only if table is empty
    with get_db() as db:
        cur = db.execute("SELECT COUNT(*) AS c FROM codes")
        count = cur.fetchone()["c"]
        if count == 0 and os.path.exists(CODES_CSV):
            with open(CODES_CSV, newline="", encoding="utf-8") as f:
                reader = csv.DictReader(f)
                for row in reader:
                    code = row.get("code")
                    if code:
                        code = code.strip()
                        if code:
                            db.execute(
                                "INSERT OR IGNORE INTO codes (code) VALUES (?)",
                                (code,),
                            )
            db.commit()
            print("Loaded codes from CSV.")


def login_required(view):
    @wraps(view)
    def wrapped(*args, **kwargs):
        if "player_id" not in session:
            return redirect(url_for("login"))
        return view(*args, **kwargs)
    return wrapped


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
            with get_db() as db:
                db.execute(
                    "INSERT INTO players (name, password_hash) VALUES (?, ?)",
                    (name, pw_hash),
                )
            return redirect(url_for("login"))
        except sqlite3.IntegrityError:
            return render_template("register.html", error="Name already taken")

    return render_template("register.html")


@app.route("/login", methods=["GET", "POST"])
def login():
    if request.method == "POST":
        name = request.form["name"].strip()
        password = request.form["password"]

        with get_db() as db:
            cur = db.execute("SELECT * FROM players WHERE name = ?", (name,))
            player = cur.fetchone()

        if player and check_password_hash(player["password_hash"], password):
            session["player_id"] = player["id"]
            session["player_name"] = player["name"]
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
    scanned_code = data.get("code", "").strip()
    if not scanned_code:
        return jsonify({"status": "error", "message": "No code detected"})

    player_id = session["player_id"]
    now = datetime.utcnow().isoformat()

    with get_db() as db:
        # find code
        cur = db.execute("SELECT * FROM codes WHERE code = ?", (scanned_code,))
        code_row = cur.fetchone()
        if not code_row:
            return jsonify({"status": "invalid", "message": "Invalid code"})

        if code_row["used_by_player_id"]:
            # already claimed globally
            return jsonify({"status": "used", "message": "⛔ Already claimed!"})

        # mark as used by this player
        db.execute(
            "UPDATE codes SET used_by_player_id = ?, used_at = ? WHERE id = ?",
            (player_id, now, code_row["id"]),
        )
        db.execute(
            "INSERT INTO scans (player_id, code_id, scanned_at) VALUES (?, ?, ?)",
            (player_id, code_row["id"], now),
        )
        db.execute(
            "UPDATE players SET score = score + 1 WHERE id = ?",
            (player_id,),
        )
        db.commit()

        # updated score
        cur2 = db.execute(
            "SELECT score FROM players WHERE id = ?", (player_id,)
        )
        score = cur2.fetchone()["score"]

    return jsonify(
        {
            "status": "ok",
            "message": "🎉 You captured this code!",
            "score": score,
        }
    )


@app.route("/leaderboard")
def leaderboard():
    with get_db() as db:
        cur = db.execute(
            "SELECT name, score FROM players ORDER BY score DESC, name ASC"
        )
        players = cur.fetchall()
    return render_template("leaderboard.html", players=players)


# ---- initialise DB when the app module loads (works on Render & locally) ----
with app.app_context():
    init_db()


if __name__ == "__main__":
    # local run for testing
    app.run(debug=True, host="0.0.0.0", port=5000)
