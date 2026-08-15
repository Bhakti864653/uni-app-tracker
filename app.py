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
from datetime import date, timedelta, datetime, timezone

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
RESET_DEMO_TOKEN = os.environ.get("RESET_DEMO_TOKEN")
AUTO_REMINDER_DAYS = 7

DEFAULT_CHECKLIST_TASKS = ["Essay", "Recommendation Letters", "Transcript"]
STATUS_OPTIONS = ["Not Started", "In Progress", "Submitted"]
TASK_TYPES = ["essay", "recommendation", "document"]
TASK_STATUS_OPTIONS = {
    "essay": ["Not Started", "Drafting", "In Review", "Final"],
    "recommendation": ["Not Requested", "Requested", "Received"],
    "document": ["Not Started", "Requested", "Received", "Submitted"],
}

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

    existing_user_columns = [row["name"] for row in dictrows(conn.execute("PRAGMA table_info(users)"))]
    if "is_demo" not in existing_user_columns:
        conn.execute("ALTER TABLE users ADD COLUMN is_demo INTEGER NOT NULL DEFAULT 0")

    # One-time cleanup: remove the old single shared demo account from
    # before per-visitor demo accounts existed.
    legacy_demo_user = dictrow(conn.execute(
        "SELECT id FROM users WHERE email = 'demo@universitytracker.app'"
    ))
    if legacy_demo_user:
        legacy_university_ids = [row["id"] for row in dictrows(conn.execute(
            "SELECT id FROM universities WHERE user_id = ?", (legacy_demo_user["id"],)
        ))]
        for university_id in legacy_university_ids:
            conn.execute("DELETE FROM tasks WHERE university_id = ?", (university_id,))
        conn.execute("DELETE FROM universities WHERE user_id = ?", (legacy_demo_user["id"],))
        conn.execute("DELETE FROM users WHERE id = ?", (legacy_demo_user["id"],))

    existing_uni_columns = [row["name"] for row in dictrows(conn.execute("PRAGMA table_info(universities)"))]
    if "user_id" not in existing_uni_columns:
        conn.execute("ALTER TABLE universities ADD COLUMN user_id INTEGER")
    if "program" not in existing_uni_columns:
        conn.execute("ALTER TABLE universities ADD COLUMN program TEXT NOT NULL DEFAULT ''")
    if "portal_url" not in existing_uni_columns:
        conn.execute("ALTER TABLE universities ADD COLUMN portal_url TEXT NOT NULL DEFAULT ''")
    if "tuition_cost" not in existing_uni_columns:
        conn.execute("ALTER TABLE universities ADD COLUMN tuition_cost REAL")
    if "financial_aid_estimate" not in existing_uni_columns:
        conn.execute("ALTER TABLE universities ADD COLUMN financial_aid_estimate REAL")

    existing_task_columns = [row["name"] for row in dictrows(conn.execute("PRAGMA table_info(tasks)"))]
    if "prompt" not in existing_task_columns:
        conn.execute("ALTER TABLE tasks ADD COLUMN prompt TEXT")
    if "word_count" not in existing_task_columns:
        conn.execute("ALTER TABLE tasks ADD COLUMN word_count INTEGER")
    if "recommender_name" not in existing_task_columns:
        conn.execute("ALTER TABLE tasks ADD COLUMN recommender_name TEXT")
    if "recommender_email" not in existing_task_columns:
        conn.execute("ALTER TABLE tasks ADD COLUMN recommender_email TEXT")

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

def seed_demo_data(conn, user_id):
    today = date.today()
    demo_schools = [
        {
            "name": "Stanford University", "program": "Computer Science",
            "deadline": (today + timedelta(days=20)).isoformat(),
            "status": "In Progress", "tuition_cost": 56000, "financial_aid_estimate": 30000,
            "portal_url": "https://apply.stanford.edu", "checklist_done": [True, True, False],
            "essay": {"title": "Personal Statement", "prompt": "Describe a challenge you overcame.",
                      "status": "Drafting", "word_count": 380},
            "recommendation": {"title": "Dr. Chen", "recommender_name": "Dr. Chen",
                                "recommender_email": "chen@school.edu", "status": "Requested"},
            "document": {"title": "Official Transcript", "status": "Received"},
        },
        {
            "name": "University of Michigan", "program": "Data Science",
            "deadline": (today + timedelta(days=45)).isoformat(),
            "status": "Not Started", "tuition_cost": 52000, "financial_aid_estimate": 18000,
            "portal_url": "", "checklist_done": [False, False, False],
            "essay": {"title": "Why Michigan", "prompt": "What draws you to our program?",
                      "status": "Not Started", "word_count": None},
            "recommendation": {"title": "Prof. Alvarez", "recommender_name": "Prof. Alvarez",
                                "recommender_email": "alvarez@school.edu", "status": "Not Requested"},
            "document": {"title": "Official Transcript", "status": "Not Started"},
        },
        {
            "name": "NYU", "program": "Business Administration",
            "deadline": (today + timedelta(days=6)).isoformat(),
            "status": "In Progress", "tuition_cost": 60000, "financial_aid_estimate": 22000,
            "portal_url": "https://apply.nyu.edu", "checklist_done": [True, False, False],
            "essay": {"title": "Career Goals Essay", "prompt": "Describe your career goals.",
                      "status": "In Review", "word_count": 512},
            "recommendation": {"title": "Ms. Patel", "recommender_name": "Ms. Patel",
                                "recommender_email": "patel@school.edu", "status": "Received"},
            "document": {"title": "Resume", "status": "Submitted"},
        },
        {
            "name": "State University", "program": "Biology",
            "deadline": (today - timedelta(days=10)).isoformat(),
            "status": "Submitted", "tuition_cost": 28000, "financial_aid_estimate": 15000,
            "portal_url": "", "checklist_done": [True, True, True],
            "essay": {"title": "Research Interest Essay", "prompt": "Describe a research interest.",
                      "status": "Final", "word_count": 620},
            "recommendation": {"title": "Dr. Okafor", "recommender_name": "Dr. Okafor",
                                "recommender_email": "okafor@school.edu", "status": "Received"},
            "document": {"title": "Transcript", "status": "Submitted"},
        },
    ]

    for school in demo_schools:
        new_id = add_university(conn, user_id, school["name"], school["deadline"])
        conn.execute(
            """UPDATE universities
               SET status = ?, program = ?, portal_url = ?, tuition_cost = ?,
                   financial_aid_estimate = ?, notes = ?
               WHERE id = ?""",
            (school["status"], school["program"], school["portal_url"], school["tuition_cost"],
             school["financial_aid_estimate"], "Demo data - feel free to explore and edit!", new_id)
        )

        checklist_items = dictrows(conn.execute(
            "SELECT id FROM tasks WHERE university_id = ? AND task_type = 'checklist' ORDER BY id", (new_id,)
        ))
        for item, done in zip(checklist_items, school["checklist_done"]):
            conn.execute("UPDATE tasks SET done = ? WHERE id = ?", (1 if done else 0, item["id"]))

        essay = school["essay"]
        conn.execute(
            "INSERT INTO tasks (university_id, task_type, title, status, prompt, word_count) VALUES (?, 'essay', ?, ?, ?, ?)",
            (new_id, essay["title"], essay["status"], essay["prompt"], essay["word_count"])
        )
        rec = school["recommendation"]
        conn.execute(
            """INSERT INTO tasks (university_id, task_type, title, status, recommender_name, recommender_email)
               VALUES (?, 'recommendation', ?, ?, ?, ?)""",
            (new_id, rec["title"], rec["status"], rec["recommender_name"], rec["recommender_email"])
        )
        document = school["document"]
        conn.execute(
            "INSERT INTO tasks (university_id, task_type, title, status) VALUES (?, 'document', ?, ?)",
            (new_id, document["title"], document["status"])
        )

def create_demo_user(conn):
    demo_email = f"demo-{secrets.token_hex(6)}@universitytracker.app"
    cursor = conn.execute(
        "INSERT INTO users (email, password_hash, created_at, is_demo) VALUES (?, ?, ?, 1)",
        (demo_email, generate_password_hash(secrets.token_hex(16)), datetime.now(timezone.utc).isoformat())
    )
    user_id = cursor.lastrowid
    seed_demo_data(conn, user_id)
    return user_id

def cleanup_expired_demo_users(conn, max_age_hours=24):
    cutoff = (datetime.now(timezone.utc) - timedelta(hours=max_age_hours)).isoformat()
    expired_users = dictrows(conn.execute(
        "SELECT id FROM users WHERE is_demo = 1 AND created_at < ?", (cutoff,)
    ))
    for user in expired_users:
        university_ids = [row["id"] for row in dictrows(conn.execute(
            "SELECT id FROM universities WHERE user_id = ?", (user["id"],)
        ))]
        for university_id in university_ids:
            conn.execute("DELETE FROM tasks WHERE university_id = ?", (university_id,))
        conn.execute("DELETE FROM universities WHERE user_id = ?", (user["id"],))
        conn.execute("DELETE FROM users WHERE id = ?", (user["id"],))
    return len(expired_users)

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

@app.route("/demo-login")
def demo_login():
    conn = get_db()
    demo_user_id = create_demo_user(conn)
    conn.commit()
    conn.close()
    session.clear()
    session["user_id"] = demo_user_id
    session["is_demo"] = True
    return redirect("/")

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
        is_demo=session.get("is_demo", False),
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

@app.route("/demo/cleanup")
def cleanup_demo():
    if not RESET_DEMO_TOKEN or not secrets.compare_digest(request.args.get("token", ""), RESET_DEMO_TOKEN):
        return "Forbidden", 403
    conn = get_db()
    removed_count = cleanup_expired_demo_users(conn)
    conn.commit()
    conn.close()
    return f"Cleaned up {removed_count} expired demo account(s)", 200

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

def parse_optional_float(value):
    value = (value or "").strip()
    return float(value) if value else None

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
        """UPDATE universities
           SET name = ?, deadline = ?, notes = ?, program = ?, portal_url = ?,
               tuition_cost = ?, financial_aid_estimate = ?
           WHERE id = ?""",
        (
            request.form["name"], request.form["deadline"], request.form["notes"],
            request.form.get("program", ""), request.form.get("portal_url", ""),
            parse_optional_float(request.form.get("tuition_cost")),
            parse_optional_float(request.form.get("financial_aid_estimate")),
            university_id,
        )
    )
    conn.commit()
    conn.close()
    return redirect(f"/university/{university_id}")

@app.route("/university/<int:university_id>")
@login_required
def university_profile(university_id):
    conn = get_db()
    uni = get_owned_university(conn, university_id, session["user_id"])
    if not uni:
        conn.close()
        return "Not found", 404
    tasks_by_type = {
        task_type: dictrows(conn.execute(
            "SELECT * FROM tasks WHERE university_id = ? AND task_type = ? ORDER BY id",
            (university_id, task_type)
        ))
        for task_type in TASK_TYPES
    }
    conn.close()

    net_cost = None
    if uni["tuition_cost"] is not None and uni["financial_aid_estimate"] is not None:
        net_cost = uni["tuition_cost"] - uni["financial_aid_estimate"]

    return render_template(
        "profile.html",
        uni=uni,
        days_text=days_remaining_text(uni["deadline"]),
        net_cost=net_cost,
        tasks_by_type=tasks_by_type,
        task_status_options=TASK_STATUS_OPTIONS,
    )

@app.route("/tasks/add/<int:university_id>/<task_type>", methods=["POST"])
@login_required
def add_task(university_id, task_type):
    conn = get_db()
    if not get_owned_university(conn, university_id, session["user_id"]):
        conn.close()
        return "Not found", 404
    if task_type not in TASK_TYPES:
        conn.close()
        return "Invalid task type", 400
    conn.execute(
        """INSERT INTO tasks
           (university_id, task_type, title, status, due_date, prompt, recommender_name, recommender_email)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
        (
            university_id, task_type,
            request.form.get("title", "").strip(),
            TASK_STATUS_OPTIONS[task_type][0],
            request.form.get("due_date") or None,
            request.form.get("prompt") or None,
            request.form.get("recommender_name") or None,
            request.form.get("recommender_email") or None,
        )
    )
    conn.commit()
    conn.close()
    return redirect(f"/university/{university_id}")

@app.route("/tasks/update/<int:task_id>", methods=["POST"])
@login_required
def update_task(task_id):
    conn = get_db()
    task = get_owned_task(conn, task_id, session["user_id"])
    if not task:
        conn.close()
        return "Not found", 404
    word_count = request.form.get("word_count") or None
    conn.execute(
        "UPDATE tasks SET status = ?, notes = ?, word_count = ? WHERE id = ?",
        (
            request.form.get("status", task["status"]),
            request.form.get("notes", task["notes"]),
            int(word_count) if word_count else None,
            task_id,
        )
    )
    conn.commit()
    conn.close()
    return redirect(f"/university/{task['university_id']}")

@app.route("/tasks/delete/<int:task_id>", methods=["POST"])
@login_required
def delete_task(task_id):
    conn = get_db()
    task = get_owned_task(conn, task_id, session["user_id"])
    if not task:
        conn.close()
        return "Not found", 404
    university_id = task["university_id"]
    conn.execute("DELETE FROM tasks WHERE id = ?", (task_id,))
    conn.commit()
    conn.close()
    return redirect(f"/university/{university_id}")

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

@app.route("/pipeline")
@login_required
def pipeline():
    conn = get_db()
    rows = dictrows(conn.execute(
        "SELECT * FROM universities WHERE user_id = ? ORDER BY deadline ASC", (session["user_id"],)
    ))
    conn.close()

    next_status = {
        STATUS_OPTIONS[i]: STATUS_OPTIONS[i + 1]
        for i in range(len(STATUS_OPTIONS) - 1)
    }
    columns = {option: [] for option in STATUS_OPTIONS}
    for row in rows:
        row["days_text"] = days_remaining_text(row["deadline"])
        columns[row["status"]].append(row)

    return render_template(
        "pipeline.html",
        columns=columns,
        status_options=STATUS_OPTIONS,
        next_status=next_status,
    )

@app.route("/analytics")
@login_required
def analytics():
    conn = get_db()
    rows = dictrows(conn.execute(
        "SELECT * FROM universities WHERE user_id = ? ORDER BY deadline ASC", (session["user_id"],)
    ))

    university_data = []
    status_counts = {option: 0 for option in STATUS_OPTIONS}
    timeline_events = []

    for row in rows:
        status_counts[row["status"]] += 1

        checklist = dictrows(conn.execute(
            "SELECT done FROM tasks WHERE university_id = ? AND task_type = 'checklist'", (row["id"],)
        ))
        done_count = sum(1 for item in checklist if item["done"])
        progress_percent = round(100 * done_count / len(checklist)) if checklist else 0

        net_cost = None
        if row["tuition_cost"] is not None and row["financial_aid_estimate"] is not None:
            net_cost = row["tuition_cost"] - row["financial_aid_estimate"]

        university_data.append({
            "name": row["name"],
            "status": row["status"],
            "deadline": row["deadline"],
            "progress_percent": progress_percent,
            "tuition_cost": row["tuition_cost"],
            "financial_aid_estimate": row["financial_aid_estimate"],
            "net_cost": net_cost,
        })

        timeline_events.append({"date": row["deadline"], "label": f"{row['name']} - application deadline"})
        due_tasks = dictrows(conn.execute(
            "SELECT title, task_type, due_date FROM tasks WHERE university_id = ? AND due_date IS NOT NULL",
            (row["id"],)
        ))
        for task in due_tasks:
            timeline_events.append({
                "date": task["due_date"],
                "label": f"{row['name']} - {task['title']} ({task['task_type']})",
            })

    conn.close()
    timeline_events.sort(key=lambda event: event["date"])
    has_cost_data = any(u["tuition_cost"] is not None for u in university_data)
    cost_ranked_universities = sorted(
        university_data, key=lambda u: u["net_cost"] if u["net_cost"] is not None else float("inf")
    )

    return render_template(
        "analytics.html",
        universities=university_data,
        cost_ranked_universities=cost_ranked_universities,
        status_counts=status_counts,
        status_options=STATUS_OPTIONS,
        timeline_events=timeline_events,
        has_cost_data=has_cost_data,
    )

_db_initialized = False

@app.before_request
def ensure_db_initialized():
    global _db_initialized
    if not _db_initialized:
        init_db()
        _db_initialized = True

if __name__ == "__main__":
    app.run(debug=True)
