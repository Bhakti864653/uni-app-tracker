from flask import Flask, request, redirect, render_template, session
from functools import wraps
from dotenv import load_dotenv
import sqlite3
import os
import secrets
import smtplib
from email.mime.text import MIMEText
from datetime import date

load_dotenv()

app = Flask(__name__)
app.secret_key = os.environ["SECRET_KEY"]
LOGIN_PASSWORD = os.environ["LOGIN_PASSWORD"]
DB_PATH = os.environ.get("DATABASE_PATH", "universities.db")
TURSO_DATABASE_URL = os.environ.get("TURSO_DATABASE_URL")
TURSO_AUTH_TOKEN = os.environ.get("TURSO_AUTH_TOKEN")
EMAIL_ADDRESS = os.environ.get("EMAIL_ADDRESS")
EMAIL_APP_PASSWORD = os.environ.get("EMAIL_APP_PASSWORD")
REMINDER_TOKEN = os.environ.get("REMINDER_TOKEN")
AUTO_REMINDER_DAYS = 7

DEFAULT_CHECKLIST_TASKS = ["Essay", "Recommendation Letters", "Transcript"]
STATUS_OPTIONS = ["Not Started", "In Progress", "Submitted"]

def login_required(view_function):
    @wraps(view_function)
    def wrapper(*args, **kwargs):
        if not session.get("logged_in"):
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

def init_db():
    conn = get_db()
    conn.execute("""
        CREATE TABLE IF NOT EXISTS universities (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL,
            deadline TEXT NOT NULL,
            status TEXT NOT NULL DEFAULT 'Not Started'
        )
    """)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS checklist_items (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            university_id INTEGER NOT NULL,
            task TEXT NOT NULL,
            done INTEGER NOT NULL DEFAULT 0,
            FOREIGN KEY (university_id) REFERENCES universities (id)
        )
    """)

    existing_columns = [row["name"] for row in dictrows(conn.execute("PRAGMA table_info(universities)"))]
    if "notes" not in existing_columns:
        conn.execute("ALTER TABLE universities ADD COLUMN notes TEXT NOT NULL DEFAULT ''")

    existing = conn.execute("SELECT COUNT(*) FROM universities").fetchone()[0]
    if existing == 0:
        add_university(conn, "Stanford", "2026-11-01")
        add_university(conn, "UC Berkeley", "2026-11-30")
    conn.commit()
    conn.close()

def add_university(conn, name, deadline):
    cursor = conn.execute(
        "INSERT INTO universities (name, deadline) VALUES (?, ?)",
        (name, deadline)
    )
    new_id = cursor.lastrowid
    for task in DEFAULT_CHECKLIST_TASKS:
        conn.execute(
            "INSERT INTO checklist_items (university_id, task) VALUES (?, ?)",
            (new_id, task)
        )

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
    message = MIMEText(body)
    message["Subject"] = subject
    message["From"] = EMAIL_ADDRESS
    message["To"] = EMAIL_ADDRESS
    with smtplib.SMTP("smtp.gmail.com", 587, timeout=15) as server:
        server.starttls()
        server.login(EMAIL_ADDRESS, EMAIL_APP_PASSWORD)
        server.send_message(message)

@app.route("/login", methods=["GET", "POST"])
def login():
    error = None
    if request.method == "POST":
        if request.form["password"] == LOGIN_PASSWORD:
            session["logged_in"] = True
            return redirect("/")
        error = "Incorrect password"
    return render_template("login.html", error=error)

@app.route("/logout")
def logout():
    session.pop("logged_in", None)
    return redirect("/login")

@app.route("/")
@login_required
def home():
    conn = get_db()
    rows = dictrows(conn.execute("SELECT * FROM universities ORDER BY deadline ASC"))

    universities = []
    for row in rows:
        checklist = dictrows(conn.execute(
            "SELECT * FROM checklist_items WHERE university_id = ?", (row["id"],)
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
        email_configured=bool(EMAIL_ADDRESS and EMAIL_APP_PASSWORD),
    )

@app.route("/add", methods=["POST"])
@login_required
def add():
    conn = get_db()
    add_university(conn, request.form["name"], request.form["deadline"])
    conn.commit()
    conn.close()
    return redirect("/")

@app.route("/status/<int:university_id>", methods=["POST"])
@login_required
def update_status(university_id):
    conn = get_db()
    conn.execute(
        "UPDATE universities SET status = ? WHERE id = ?",
        (request.form["status"], university_id)
    )
    conn.commit()
    conn.close()
    return redirect("/")

@app.route("/checklist/toggle/<int:item_id>")
@login_required
def toggle_checklist_item(item_id):
    conn = get_db()
    item = dictrow(conn.execute("SELECT done FROM checklist_items WHERE id = ?", (item_id,)))
    new_done = 0 if item["done"] else 1
    conn.execute("UPDATE checklist_items SET done = ? WHERE id = ?", (new_done, item_id))
    conn.commit()
    conn.close()
    return redirect("/")

@app.route("/checklist/add/<int:university_id>", methods=["POST"])
@login_required
def add_checklist_item(university_id):
    conn = get_db()
    conn.execute(
        "INSERT INTO checklist_items (university_id, task) VALUES (?, ?)",
        (university_id, request.form["task"])
    )
    conn.commit()
    conn.close()
    return redirect("/")

@app.route("/checklist/delete/<int:item_id>")
@login_required
def delete_checklist_item(item_id):
    conn = get_db()
    conn.execute("DELETE FROM checklist_items WHERE id = ?", (item_id,))
    conn.commit()
    conn.close()
    return redirect("/")

@app.route("/reminders/send")
@login_required
def send_reminders():
    if not EMAIL_ADDRESS or not EMAIL_APP_PASSWORD:
        return redirect("/?reminder_error=not_configured")

    conn = get_db()
    rows = dictrows(conn.execute("SELECT * FROM universities ORDER BY deadline ASC"))
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
    except (smtplib.SMTPException, OSError):
        return redirect("/?reminder_error=send_failed")

    return redirect("/?reminder_sent=1")

@app.route("/reminders/auto")
def send_auto_reminder():
    if not REMINDER_TOKEN or not secrets.compare_digest(request.args.get("token", ""), REMINDER_TOKEN):
        return "Forbidden", 403

    if not EMAIL_ADDRESS or not EMAIL_APP_PASSWORD:
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
    except (smtplib.SMTPException, OSError):
        return "Send failed", 500

    return "Reminder sent", 200

@app.route("/edit/<int:university_id>")
@login_required
def edit_form(university_id):
    conn = get_db()
    uni = dictrow(conn.execute("SELECT * FROM universities WHERE id = ?", (university_id,)))
    conn.close()
    return render_template("edit.html", uni=uni)

@app.route("/edit/<int:university_id>", methods=["POST"])
@login_required
def edit_submit(university_id):
    conn = get_db()
    conn.execute(
        "UPDATE universities SET name = ?, deadline = ?, notes = ? WHERE id = ?",
        (request.form["name"], request.form["deadline"], request.form["notes"], university_id)
    )
    conn.commit()
    conn.close()
    return redirect("/")

@app.route("/delete/<int:university_id>")
@login_required
def delete(university_id):
    conn = get_db()
    conn.execute("DELETE FROM checklist_items WHERE university_id = ?", (university_id,))
    conn.execute("DELETE FROM universities WHERE id = ?", (university_id,))
    conn.commit()
    conn.close()
    return redirect("/")

init_db()

if __name__ == "__main__":
    app.run(debug=True)
