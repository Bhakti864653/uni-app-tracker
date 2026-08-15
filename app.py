from flask import Flask, request, redirect, render_template
import sqlite3
from datetime import date

app = Flask(__name__)

def get_db():
    conn = sqlite3.connect("universities.db")
    conn.row_factory = sqlite3.Row
    return conn

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
    existing = conn.execute("SELECT COUNT(*) FROM universities").fetchone()[0]
    if existing == 0:
        conn.execute("INSERT INTO universities (name, deadline) VALUES (?, ?)", ("Stanford", "2026-11-01"))
        conn.execute("INSERT INTO universities (name, deadline) VALUES (?, ?)", ("UC Berkeley", "2026-11-30"))
    conn.commit()
    conn.close()

STATUS_OPTIONS = ["Not Started", "In Progress", "Submitted"]

def days_remaining_text(deadline_str):
    deadline_date = date.fromisoformat(deadline_str)
    days_left = (deadline_date - date.today()).days
    if days_left < 0:
        return f"{abs(days_left)} days overdue"
    elif days_left == 0:
        return "due today"
    else:
        return f"{days_left} days left"

@app.route("/")
def home():
    conn = get_db()
    rows = conn.execute("SELECT * FROM universities ORDER BY deadline ASC").fetchall()
    conn.close()

    universities = []
    for row in rows:
        universities.append({
            "id": row["id"],
            "name": row["name"],
            "deadline": row["deadline"],
            "status": row["status"],
            "days_text": days_remaining_text(row["deadline"]),
        })

    return render_template("index.html", universities=universities, status_options=STATUS_OPTIONS)

@app.route("/add", methods=["POST"])
def add():
    conn = get_db()
    conn.execute(
        "INSERT INTO universities (name, deadline) VALUES (?, ?)",
        (request.form["name"], request.form["deadline"])
    )
    conn.commit()
    conn.close()
    return redirect("/")

@app.route("/status/<int:university_id>", methods=["POST"])
def update_status(university_id):
    conn = get_db()
    conn.execute(
        "UPDATE universities SET status = ? WHERE id = ?",
        (request.form["status"], university_id)
    )
    conn.commit()
    conn.close()
    return redirect("/")

@app.route("/delete/<int:university_id>")
def delete(university_id):
    conn = get_db()
    conn.execute("DELETE FROM universities WHERE id = ?", (university_id,))
    conn.commit()
    conn.close()
    return redirect("/")

init_db()

if __name__ == "__main__":
    app.run(debug=True)
