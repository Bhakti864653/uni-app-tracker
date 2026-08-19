# Development Log

A running record of real problems hit while building this, the decisions made, and what I learned. Written as I went, not reconstructed after the fact.

## Data loss on every deploy

**Problem:** After adding checklists, status tracking, and a login system, I redeployed the app and came back to find my test data gone — reset to the two default schools from a fresh install.

**Cause:** Render's free web services have an ephemeral filesystem. My SQLite database was just a file sitting next to the app code, and every deploy (or even a restart after the free tier spins down from inactivity) wipes the whole filesystem clean.

**Fix:** Migrated to [Turso](https://turso.tech), a hosted SQLite-compatible database, in production - while keeping plain local SQLite for development (`get_db()` branches on whether `TURSO_DATABASE_URL` is set). No code outside that one function needed to change, since both are accessed through the same `sqlite3`-style connection interface.

**What I learned:** "It works when I test it" isn't the same as "it survives a deploy." Any app with real persistence needs to be tested across a redeploy, not just across requests in one running process.

## A native dependency that couldn't build on my machine

**Problem:** `pip install libsql` failed on Windows with a Rust compiler error (`couldn't determine visual studio generator`).

**Cause:** `libsql` ships prebuilt wheels for Linux, but not for Windows + Python 3.14 at the time - so `pip` fell back to compiling it from source, which needs a Rust toolchain and MSVC build tools I didn't have installed.

**Fix:** Made the `import libsql` lazy - it only happens inside `get_db()`, and only when Turso credentials are actually configured. Locally (no Turso env vars set) the import never runs, so Windows dev never needs the package at all. In `requirements.txt`, marked it `libsql==0.1.11; sys_platform != "win32"` so `pip install` doesn't even attempt it on Windows, while Render's Linux build gets the real prebuilt wheel.

**What I learned:** Be wary of native/compiled dependencies for a small project - they can work perfectly in CI/production and fail entirely on a contributor's laptop for reasons that have nothing to do with the code.

## A deadlock that only happened in production

**Problem:** After fixing the Windows install issue and deploying, the live app returned 500s and the logs showed workers being SIGKILL'd with `thread 'tokio-runtime-worker' panicked ... Resource deadlock avoided`.

**Cause:** `init_db()` ran once at module import time, before Gunicorn forked its worker processes. `libsql`'s Rust runtime (tokio) had already spun up background OS threads by the time the fork happened - and `fork()` only duplicates the calling thread, so the child process inherited a half-initialized runtime with threads that could never be joined.

**Fix:** Deferred database initialization to the first actual HTTP request (`@app.before_request`, guarded by a flag) instead of running it at import time. That guarantees it only runs inside an already-forked worker process, never in the master.

**What I learned:** "Works locally" for a database library doesn't rule out fork-related bugs, because local dev (`python app.py`) never forks. This only showed up under Gunicorn's process model in production - a good reminder to actually test the production server command locally when something touches native code.

## Outbound email silently timing out

**Problem:** The "send reminder" button worked in theory but always failed in production with a connection timeout to Gmail's SMTP server.

**Cause:** Render's free tier blocks outbound traffic to SMTP ports (25, 465, 587) as an anti-spam measure, as of a 2025 policy change. No code fix could work around a network-level block.

**Fix:** Replaced raw SMTP with [Resend](https://resend.com), which sends email over a normal HTTPS API call - not blocked, since blocking port 443 would break the app as a web server entirely. Implemented with the standard library's `urllib.request` rather than adding a new dependency.

**Follow-up bug:** The very first live request to Resend's API failed with a Cloudflare "error code 1010" - not an error from Resend at all, but Cloudflare's bot-protection blocking the request based on Python's default `urllib` User-Agent string. Fixed by setting an explicit `User-Agent` header.

**What I learned:** A clean timeout with no error detail is almost always an infrastructure-level block, not an application bug - worth checking the hosting provider's changelog before spending time debugging code that already looks correct.

## Multi-user accounts and a unified task model

**Decision:** The app started with a single shared password and no real accounts. Adding "user accounts," "demo account," and "onboarding" to the roadmap all depend on real per-user data, so this became the first thing rebuilt rather than the last.

At the same time, four separate planned features - checklist items, essays, recommendation letters, and documents - are structurally the same thing: a task belonging to a school, with a status. Instead of four database tables and four sets of CRUD routes, I built one `tasks` table with a `task_type` column and a handful of nullable type-specific columns (`prompt`, `word_count` for essays; `recommender_name`, `recommender_email` for recommendations). One set of ownership-checked routes covers all four categories.

**What I learned:** Spotting when several "different" features share an underlying shape is one of the highest-leverage design decisions available - it turned what looked like 4x the work into roughly 1.3x the work.

## Shared demo account vs. per-visitor sandboxes

**Problem:** A single shared demo account meant one visitor's edits (or deletions) were visible to the next visitor - a stranger could land on a demo that looked broken because someone before them deleted everything.

**Fix:** Every "Try the demo" click now creates a fresh, randomly-named account seeded with the same sample data, fully isolated via the same per-user ownership checks that protect real accounts. A scheduled cleanup job (GitHub Actions, same pattern as the deadline-reminder job) removes demo accounts older than 24 hours so they don't accumulate.

**What I learned:** "Isolated from real user data" and "isolated from other visitors" are two different safety properties - I'd solved the first one by default (per-user ownership checks) but had to deliberately design for the second.

## Choosing not to build a real AI feature

**Decision:** "AI-powered application assistant" was on the original feature wishlist. Rather than integrate a paid LLM API, I built a rule-based "Smart Suggestions" engine that ranks incomplete tasks across all schools by deadline urgency.

**Why:** A real LLM integration adds an ongoing API cost, a new external dependency, and meaningfully more surface area (prompt design, error handling for a third-party API, rate limits) for a feature whose core value - "tell me what to work on" - a simple sort by urgency already delivers. The rule-based version shipped in under an hour instead of becoming its own multi-day feature.

**What I learned:** "Add AI" is often really "add a ranked recommendation," and it's worth checking whether the non-AI version of a feature already gets 90% of the value before reaching for an API.

## Soft delete instead of a second confirmation dialog

**Decision:** Delete already had a `confirm()` dialog ("Delete MIT? This cannot be undone."), but confirm dialogs get reflexively clicked through - they don't actually prevent the mistake they're guarding against. Added real soft-delete: `deleted_at` gets set instead of a hard `DELETE`, every listing query filters `WHERE deleted_at IS NULL`, and the dashboard shows a "deleted. Undo" banner backed by a `/restore/<id>` route.

**Follow-up:** Old soft-deleted rows aren't kept forever - a `purge_old_deleted_universities()` sweep (rows deleted more than 30 days ago) runs inside the existing token-protected `/demo/cleanup` endpoint, reusing the scheduled GitHub Actions job instead of standing up a second cron.

**Bug caught while building it:** the undo banner's `<form>` was nested inside a `<p class="banner">`. `<form>` isn't valid content inside `<p>` per the HTML spec, so the browser's parser silently auto-closes the `<p>` the moment it hits the `<form>` tag - the Undo link rendered outside the styled banner entirely, as a bare underlined link floating below it. Fixed by using a `<div>` instead. A good reminder that invalid nesting doesn't throw an error, it just silently reshapes the DOM in a way that's easy to miss without actually looking at the rendered page.

**What I learned:** A second confirmation dialog on top of an existing one doesn't fix a data-loss problem - only real reversibility does. Also: "it looks right in the HTML" isn't the same as "the browser will parse it the way I wrote it."

## Two real vulnerabilities in the new export features

**Problem 1 - CSV formula injection:** The new "Export CSV" button writes each university's name straight into a cell. A name like `=cmd|'/c calc'!A1` (settable through the existing add/edit forms - no client-side restriction stops it) would be interpreted as a live formula the moment the exported file is opened in Excel or Google Sheets, by the student or by anyone they share the export with, like a counselor or parent. This is a well-known, named vulnerability class (CSV injection, CWE-1236), not a hypothetical.

**Problem 2 - ICS content injection:** The new "Calendar" export escaped commas/semicolons in the university name for the `.ics` file but not embedded `\r\n`. A name containing a real newline (reachable by posting form data directly, bypassing the browser's own newline-stripping on a text `<input>`) could inject extra lines into the generated calendar file - splicing in a fake `VALARM` or `VEVENT` block that a calendar app would parse as genuine.

**Fix:** Cells starting with `=`, `+`, `-`, `@`, tab, or a stray CR now get a leading `'` prefix before being written to CSV, neutralizing formula interpretation. The `.ics` escaper now also converts any `\r\n` / `\n` / `\r` into a literal `\n` escape sequence per the iCalendar spec, so embedded newlines stay inert text inside one field instead of becoming real line breaks. Both are covered by regression tests now.

**What I learned:** Any feature whose whole point is "produce a file meant to be opened in a different program" inherits that other program's parsing rules as part of its own attack surface - the vulnerability isn't in this app's code path, it's in what Excel or a calendar client does with the file afterward.

## What an automated code review caught (and one thing I overruled)

Ran a second, independent review pass over the same diff. It found one more real bug of the exact same shape as the two above: `export_calendar()` escaped the university `name` field but wrote `deadline` straight into the `DTSTART` line unescaped and unvalidated - a `deadline` containing embedded `\r\n` (there was never any server-side validation that a stored deadline is actually a real date) could inject fake ICS lines the same way the `name` field could before it was fixed. Fixed by parsing `deadline` through `date.fromisoformat()` and skipping the row if it isn't a real date, instead of trusting the raw string.

Also caught: the delete confirmation dialog still read "This cannot be undone" after delete became an undoable soft-delete - directly contradicting the feature it was warning about. Fixed the copy. And: the `deleted_at IS NULL` filter was hand-copied into 7 different SQL strings across the file, an easy thing to forget on the next new feature - pulled into two shared helpers (`get_user_universities`, `get_all_active_universities`) instead.

**Where I disagreed:** the review also flagged that `duplicate()` doesn't copy a task's `notes`, `due_date`, or `word_count` into the new copy, calling it silent data loss. I don't think it is one - the original task (and its notes) are untouched; only the *new* copy starts fresh. Those three fields are progress state tied to the old application's history (a note like "emailed recommender 1/15", a word count on a draft), not reusable template data like the recommender's name or an essay prompt. Copying them would mean a "fresh start" duplicate opens with someone else's old progress log already checked off, which is worse, not better. Kept the reset behavior.

**What I learned:** A second review pass is worth running even right after finishing one, since it can catch the exact class of bug the first pass just fixed elsewhere in the same diff. But not every finding is correct just because it came from a review - the right response is to actually think about the suggested fix, not apply it mechanically.

## Miscellaneous fixes worth noting

- **Sorting `None` and numbers together:** The cost-comparison table crashed with `TypeError: '<' not supported between instances of 'float' and 'NoneType'` the first time a school had no cost data yet while another did. Fixed by giving missing values a sort key of `float("inf")` instead of comparing `None` directly.
- **`datetime.utcnow()` deprecation:** Python 3.14 warns on the naive `utcnow()` call used for demo account timestamps; switched to `datetime.now(timezone.utc)`.
- **CSRF token vs. session.clear():** Login and signup call `session.clear()` for security (avoids session fixation) - which also wipes any CSRF token set earlier in the same session. Correct behavior for real users (a fresh page render issues a new token), but it meant test fixtures had to explicitly re-seed a known CSRF token after simulating a login, rather than assuming one primed value would survive the whole test.
