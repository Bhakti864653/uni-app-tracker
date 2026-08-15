# University Application Tracker

A small web app I built to track my own university applications — deadlines, status, and progress — while learning Python and web development from scratch.

**Live demo:** https://uni-app-tracker-b70h.onrender.com/
*(hosted on a free tier — may take ~30s to wake up on first visit)*

## Features

- Add, edit, and delete universities with a name and application deadline
- See all applications sorted by soonest deadline, with days remaining calculated automatically
- Track status per application: Not Started, In Progress, Submitted
- Per-university checklist (essay, recommendation letters, transcript, plus your own custom tasks)
- Progress bar showing % of checklist complete
- Free-text notes per university
- Stats dashboard summarizing status counts and the nearest deadline
- Search, filter by status, and sort the list
- Dark mode
- One-click email reminder summarizing pending deadlines (optional, requires your own email credentials)
- Password-protected login
- Data is stored persistently in a SQLite database

## Tech stack

- **Backend:** Python, Flask
- **Database:** SQLite
- **Frontend:** HTML/CSS with Jinja templates
- **Auth:** Flask sessions with a password stored in an environment variable
- **Tests:** pytest with Flask's test client

## Running it locally

```
python -m venv venv
venv\Scripts\activate
pip install -r requirements.txt
```

Create a `.env` file (see `.env.example`) with your own `SECRET_KEY` and `LOGIN_PASSWORD`. To enable the "Email reminders" button, also add `EMAIL_ADDRESS` and `EMAIL_APP_PASSWORD` (a [Gmail App Password](https://myaccount.google.com/apppasswords), not your regular password) - otherwise leave them out and the button just won't show up. Then:

```
python app.py
```

Then open `http://127.0.0.1:5000` in your browser.

## Running the tests

```
pip install -r requirements-dev.txt
pytest -v
```

Tests run against a separate temporary database, so they never touch your real data.

## What I learned building this

This was my first real project after starting to learn to code. It took me through:
- Python fundamentals (variables, functions, loops, dictionaries)
- Building a web server and routes with Flask
- Reading and writing data with SQL and SQLite
- HTML forms and handling user input
- Separating logic (Python) from presentation (HTML templates) and styling with CSS
