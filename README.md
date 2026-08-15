# University Application Tracker

A small web app I built to track my own university applications — deadlines, status, and progress — while learning Python and web development from scratch.

**Live demo:** https://uni-app-tracker-b70h.onrender.com/
*(hosted on a free tier — may take ~30s to wake up on first visit)*

## Features

- Add universities with a name and application deadline
- See all applications sorted by soonest deadline, with days remaining calculated automatically
- Track status per application: Not Started, In Progress, Submitted
- Delete applications
- Data is stored persistently in a SQLite database

## Tech stack

- **Backend:** Python, Flask
- **Database:** SQLite
- **Frontend:** HTML/CSS with Jinja templates

## Running it locally

```
python -m venv venv
venv\Scripts\activate
pip install -r requirements.txt
python app.py
```

Then open `http://127.0.0.1:5000` in your browser.

## What I learned building this

This was my first real project after starting to learn to code. It took me through:
- Python fundamentals (variables, functions, loops, dictionaries)
- Building a web server and routes with Flask
- Reading and writing data with SQL and SQLite
- HTML forms and handling user input
- Separating logic (Python) from presentation (HTML templates) and styling with CSS
