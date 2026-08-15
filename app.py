from flask import Flask, request, redirect, render_template, session
from functools import wraps
from dotenv import load_dotenv
from werkzeug.security import generate_password_hash, check_password_hash
from werkzeug.middleware.proxy_fix import ProxyFix
import sqlite3
import os
import secrets
import json
import time
import urllib.request
from collections import defaultdict
from datetime import date

load_dotenv()

app = Flask(__name__)
app.wsgi_app = ProxyFix(app.wsgi_app, x_for=1)
app.secret_key = os.environ["SECRET_KEY"]
DB_PATH = os.environ.get("DATABASE_PATH", "universities.db")
TURSO_DATABASE_URL = os.environ.get("TURSO_DATABASE_URL")
TURSO_AUTH_TOKEN = os.environ.get("TURSO_AUTH_TOKEN")
EMAIL_ADDRESS = os.environ.get("EMAIL_ADDRESS")
RESEND_API_KEY = os.environ.get("RESEND_API_KEY")
REMINDER_TOKEN = os.environ.get("REMINDER_TOKEN")
AUTO_REMINDER_DAYS = 7

DEFAULT_CHECKLIST_TASKS = ["Essay", "Recommendation Letters", "Transcript"]
STATUS_OPTIONS = ["Not Started", "In Progress", "Submitted"]

RATE_LIMIT_WINDOW_SECONDS = 900
RATE_LIMIT_MAX_ATTEMPTS = 5
_auth_attempts = defaultdict(list)

def is_rate_limited(key):
    now = time.time()
    attempts = _auth_attempts[key]
    attempts[:] = [t for t in attempts if now - t < RATE_LIMIT_WINDOW_SECONDS]
    if len(attempts) >= RATE_LIMIT_MAX_ATTEMPTS:
        return True
    attempts.append(now)
    return False

def login_required(view_function):
    @wraps(view_function)
    def wrapper(*args, **kwargs):
        if not session.get("user_id"):
            return redirect("/login")
        return view_function(*args, **kwargs)
    return wrapper

def get_db():
    if TURSO_DATABASE_URL and TURSO_AUTH_TOKEN:
        import libsql
        return libsql.connect(database=TURSO_DATABASE_URL, auth_token=TURSO_AUTH_TOKEN)
    return sqlite3.connect(DB_PATH)

def dictrows(cursor):
    columns = [col[0] for col in cursor.description]
    return [dict(zip(columns, row)) for row in cursor.fetchall()]

def dictrow(cursor):
    columns = [col[0] for col in cursor.description]
    row = cursor.fetchone()
    return dict(zip(columns, row)) if row else None

def get_owned_university(conn, university_id, user_id):
    return dictrow(conn.execute(
        "SELECT * FROM universities WHERE id = ? AND user_id = ?",
        (university_id, user_id)
    ))

def get_owned_task(conn, task_id, user_id):
    return dictrow(conn.execute(
        """SELECT tasks.* FROM tasks
           JOIN universities ON universities.id = tasks.university_id
           WHERE tasks.id = ? AND universities.user_id = ?""",
        (task_id, user_id)
    ))

def init_db():
    conn = get_db()
    conn.execute("""
        CREATE TABLE IF NOT EXISTS users (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            email TEXT NOT NULL UNIQUE,
            password_hash TEXT NOT NULL,
            created_at TEXT NOT NULL
        )
    """)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS universities (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER,
            name TEXT NOT NULL,
            deadline TEXT NOT NULL,
            status TEXT NOT NULL DEFAULT 'Not Started',
            notes TEXT NOT NULL DEFAULT ''
        )
    """)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS tasks (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            university_id INTEGER NOT NULL,
            task_type TEXT NOT NULL DEFAULT 'checklist',
            title TEXT NOT NULL,
            status TEXT NOT NULL DEFAULT 'Not Started',
            done INTEGER NOT NULL DEFAULT 0,
            due_date TEXT,
            notes TEXT NOT NULL DEFAULT '',
            FOREIGN KEY (university_id) REFERENCES universities (id)
        )
    """)

    existing_uni_columns = [row["name"] for row in dictrows(conn.execute("PRAGMA table_info(universities)"))]
    if "user_id" not in existing_uni_columns:
        conn.execute("ALTER TABLE universities ADD COLUMN user_id INTEGER")

    # One-time migration from the old single-account schema: copy any
    # pre-existing checklist_items into the new unified tasks table.
    existing_tables = [row["name"] for row in dictrows(conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table'"
    ))]
    if "checklist_items" in existing_tables:
        task_count = conn.execute("SELECT COUNT(*) FROM tasks").fetchone()[0]
        if task_count == 0:
            old_items = dictrows(conn.execute("SELECT * FROM checklist_items"))
            for item in old_items:
                conn.execute(
                    "INSERT INTO tasks (university_id, task_type, title, done) VALUES (?, 'checklist', ?, ?)",
                    (item["university_id"], item["task"], item["done"])
                )

    conn.commit()
    conn.close()

def add_university(conn, user_id, name, deadline):
    cursor = conn.execute(
        "INSERT INTO universities (user_id, name, deadline) VALUES (?, ?, ?)",
        (user_id, name, deadline)
    )
    new_id = cursor.lastrowid
    for task_title in DEFAULT_CHECKLIST_TASKS:
        conn.execute(
            "INSERT INTO tasks (university_id, task_type, title) VALUES (?, 'checklist', ?)",
            (new_id, task_title)
        )
    return new_id

def days_left_int(deadline_str):
    deadline_date = date.fromisoformat(deadline_str)
    return (deadline_date - date.today()).days

def days_remaining_text(deadline_str):
    days_left = days_left_int(deadline_str)
    if days_left < 0:
        return f"{abs(days_left)} days overdue"
    elif days_left == 0:
        return "due today"
    else:
        return f"{days_left} days left"

def send_email_message(subject, body):
    payload = json.dumps({
        "from": "University Tracker <onboarding@resend.dev>",
        "to": [EMAIL_ADDRESS],
        "subject": subject,
        "text": body,
    }).encode()
    request_obj = urllib.request.Request(
        "https://api.resend.com/emails",
        data=payload,
        headers={
            "Authorization": f"Bearer {RESEND_API_KEY}",
            "Content-Type": "application/json",
            "User-Agent": "Mozilla/5.0 (compatible; uni-app-tracker/1.0)",
        },
    )
    urllib.request.urlopen(request_obj, timeout=15)

def generate_csrf_token():
    if "csrf_token" not in session:
        session["csrf_token"] = secrets.token_hex(16)
    return session["csrf_token"]

app.jinja_env.globals["csrf_token"] = generate_csrf_token

@app.before_request
def check_csrf():
    if request.method == "POST":
        token = session.get("csrf_token")
        submitted = request.form.get("csrf_token")
        if not token or not submitted or not secrets.compare_digest(token, submitted):
            return "Invalid or missing CSRF token", 400

@app.route("/signup", methods=["GET", "POST"])
def signup():
    error = None
    if request.method == "POST":
        email = request.form["email"].strip().lower()
        password = request.form["password"]
        confirm_password = request.form["confirm_password"]
        client_key = f"signup:{request.remote_addr}"

        if is_rate_limited(client_key):
            error = "Too many attempts. Try again in a few minutes."
        elif len(password) < 8:
            error = "Password must be at least 8 characters."
        elif password != confirm_password:
            error = "Passwords don't match."
        else:
            conn = get_db()
            existing = dictrow(conn.execute("SELECT id FROM users WHERE email = ?", (email,)))
            if existing:
                error = "An account with that email already exists."
                conn.close()
            else:
                cursor = conn.execute(
                    "INSERT INTO users (email, password_hash, created_at) VALUES (?, ?, ?)",
                    (email, generate_password_hash(password), date.today().isoformat())
                )
                user_id = cursor.lastrowid
                # Claim any universities left over from before accounts existed.
                conn.execute("UPDATE universities SET user_id = ? WHERE user_id IS NULL", (user_id,))
                conn.commit()
                conn.close()
                session.clear()
                session["user_id"] = user_id
                return redirect("/")
    return render_template("signup.html", error=error)

@app.route("/login", methods=["GET", "POST"])
def login():
    error = None
    if request.method == "POST":
        email = request.form["email"].strip().lower()
        password = request.form["password"]
        client_key = f"login:{request.remote_addr}"

        if is_rate_limited(client_key):
            error = "Too many attempts. Try again in a few minutes."
        else:
            conn = get_db()
            user = dictrow(conn.execute("SELECT * FROM users WHERE email = ?", (email,)))
            conn.close()
            if user and check_password_hash(user["password_hash"], password):
                session.clear()
                session["user_id"] = user["id"]
                return redirect("/")
            error = "Incorrect email or password"
    return render_template("login.html", error=error)

@app.route("/logout", methods=["POST"])
def logout():
    session.clear()
    return redirect("/login")

@app.route("/")
@login_required
def home():
    user_id = session["user_id"]
    conn = get_db()
    rows = dictrows(conn.execute(
        "SELECT * FROM universities WHERE user_id = ? ORDER BY deadline ASC", (user_id,)
    ))

    universities = []
    for row in rows:
        checklist = dictrows(conn.execute(
            "SELECT * FROM tasks WHERE university_id = ? AND task_type = 'checklist'", (row["id"],)
        ))

        done_count = sum(1 for item in checklist if item["done"])
        total_count = len(checklist)
        progress_percent = round(100 * done_count / total_count) if total_count else 0

        universities.append({
            "id": row["id"],
            "name": row["name"],
            "deadline": row["deadline"],
            "status": row["status"],
            "days_text": days_remaining_text(row["deadline"]),
            "checklist": checklist,
            "progress_percent": progress_percent,
            "notes": row["notes"],
        })
    conn.close()

    status_counts = {option: 0 for option in STATUS_OPTIONS}
    for uni in universities:
        status_counts[uni["status"]] += 1
    next_deadline = universities[0] if universities else None

    return render_template(
        "index.html",
        universities=universities,
        status_options=STATUS_OPTIONS,
        status_counts=status_counts,
        next_deadline=next_deadline,
        reminder_sent=request.args.get("reminder_sent"),
        reminder_error=request.args.get("reminder_error"),
        email_configured=bool(EMAIL_ADDRESS and RESEND_API_KEY),
    )

@app.route("/add", methods=["POST"])
@login_required
def add():
    conn = get_db()
    add_university(conn, session["user_id"], request.form["name"], request.form["deadline"])
    conn.commit()
    conn.close()
    return redirect("/")

@app.route("/status/<int:university_id>", methods=["POST"])
@login_required
def update_status(university_id):
    conn = get_db()
    if not get_owned_university(conn, university_id, session["user_id"]):
        conn.close()
        return "Not found", 404
    conn.execute(
        "UPDATE universities SET status = ? WHERE id = ?",
        (request.form["status"], university_id)
    )
    conn.commit()
    conn.close()
    return redirect("/")

@app.route("/checklist/toggle/<int:item_id>", methods=["POST"])
@login_required
def toggle_checklist_item(item_id):
    conn = get_db()
    item = get_owned_task(conn, item_id, session["user_id"])
    if not item:
        conn.close()
        return "Not found", 404
    new_done = 0 if item["done"] else 1
    conn.execute("UPDATE tasks SET done = ? WHERE id = ?", (new_done, item_id))
    conn.commit()
    conn.close()
    return redirect("/")

@app.route("/checklist/add/<int:university_id>", methods=["POST"])
@login_required
def add_checklist_item(university_id):
    conn = get_db()
    if not get_owned_university(conn, university_id, session["user_id"]):
        conn.close()
        return "Not found", 404
    conn.execute(
        "INSERT INTO tasks (university_id, task_type, title) VALUES (?, 'checklist', ?)",
        (university_id, request.form["task"])
    )
    conn.commit()
    conn.close()
    return redirect("/")

@app.route("/checklist/delete/<int:item_id>", methods=["POST"])
@login_required
def delete_checklist_item(item_id):
    conn = get_db()
    if not get_owned_task(conn, item_id, session["user_id"]):
        conn.close()
        return "Not found", 404
    conn.execute("DELETE FROM tasks WHERE id = ?", (item_id,))
    conn.commit()
    conn.close()
    return redirect("/")

@app.route("/reminders/send")
@login_required
def send_reminders():
    if not EMAIL_ADDRESS or not RESEND_API_KEY:
        return redirect("/?reminder_error=not_configured")

    conn = get_db()
    rows = dictrows(conn.execute(
        "SELECT * FROM universities WHERE user_id = ? ORDER BY deadline ASC", (session["user_id"],)
    ))
    conn.close()

    pending = [row for row in rows if row["status"] != "Submitted"]
    if pending:
        lines = [
            f"- {row['name']}: due {row['deadline']} ({days_remaining_text(row['deadline'])}) - {row['status']}"
            for row in pending
        ]
        body = "Your upcoming university application deadlines:\n\n" + "\n".join(lines)
    else:
        body = "Nothing pending - every application is marked Submitted!"

    try:
        send_email_message("University Application Reminders", body)
    except OSError:
        return redirect("/?reminder_error=send_failed")

    return redirect("/?reminder_sent=1")

@app.route("/reminders/auto")
def send_auto_reminder():
    if not REMINDER_TOKEN or not secrets.compare_digest(request.args.get("token", ""), REMINDER_TOKEN):
        return "Forbidden", 403

    if not EMAIL_ADDRESS or not RESEND_API_KEY:
        return "Email not configured", 200

    conn = get_db()
    rows = dictrows(conn.execute("SELECT * FROM universities ORDER BY deadline ASC"))
    conn.close()

    urgent = [
        row for row in rows
        if row["status"] != "Submitted" and days_left_int(row["deadline"]) <= AUTO_REMINDER_DAYS
    ]
    if not urgent:
        return "No urgent deadlines, nothing sent", 200

    lines = [
        f"- {row['name']}: due {row['deadline']} ({days_remaining_text(row['deadline'])}) - {row['status']}"
        for row in urgent
    ]
    body = f"You have deadlines within {AUTO_REMINDER_DAYS} days:\n\n" + "\n".join(lines)

    try:
        send_email_message("University Application Deadline Reminder", body)
    except OSError:
        return "Send failed", 500

    return "Reminder sent", 200

@app.route("/edit/<int:university_id>")
@login_required
def edit_form(university_id):
    conn = get_db()
    uni = get_owned_university(conn, university_id, session["user_id"])
    conn.close()
    if not uni:
        return "Not found", 404
    return render_template("edit.html", uni=uni)

@app.route("/edit/<int:university_id>", methods=["POST"])
@login_required
def edit_submit(university_id):
    conn = get_db()
    if not get_owned_university(conn, university_id, session["user_id"]):
        conn.close()
        return "Not found", 404
    conn.execute(
        "UPDATE universities SET name = ?, deadline = ?, notes = ? WHERE id = ?",
        (request.form["name"], request.form["deadline"], request.form["notes"], university_id)
    )
    conn.commit()
    conn.close()
    return redirect("/")

@app.route("/delete/<int:university_id>", methods=["POST"])
@login_required
def delete(university_id):
    conn = get_db()
    if not get_owned_university(conn, university_id, session["user_id"]):
        conn.close()
        return "Not found", 404
    conn.execute("DELETE FROM tasks WHERE university_id = ?", (university_id,))
    conn.execute("DELETE FROM universities WHERE id = ?", (university_id,))
    conn.commit()
    conn.close()
    return redirect("/")

_db_initialized = False

@app.before_request
def ensure_db_initialized():
    global _db_initialized
    if not _db_initialized:
        init_db()
        _db_initialized = True

if __name__ == "__main__":
    app.run(debug=True)
