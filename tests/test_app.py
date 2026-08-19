import app
from conftest import reset_csrf


def test_landing_page_is_publicly_accessible(client):
    response = client.get("/")
    assert response.status_code == 200
    assert b"Get started free" in response.data


def test_dashboard_redirects_to_login_when_not_logged_in(client):
    response = client.get("/dashboard")
    assert response.status_code == 302
    assert "/login" in response.headers["Location"]


def test_landing_page_redirects_logged_in_users_to_dashboard(logged_in_client):
    response = logged_in_client.get("/")
    assert response.status_code == 302
    assert response.headers["Location"] == "/dashboard"


def test_signup_creates_account_and_logs_in(client, csrf_token):
    response = client.post("/signup", data={
        "email": "new@example.com",
        "password": "testpassword123",
        "confirm_password": "testpassword123",
        "csrf_token": csrf_token,
    })
    assert response.status_code == 302
    assert response.headers["Location"] == "/dashboard"


def test_signup_with_mismatched_passwords_shows_error(client, csrf_token):
    response = client.post("/signup", data={
        "email": "new@example.com",
        "password": "testpassword123",
        "confirm_password": "somethingelse",
        "csrf_token": csrf_token,
    })
    assert b"don&#39;t match" in response.data


def test_login_with_wrong_credentials_shows_error(logged_in_client, csrf_token):
    logged_in_client.post("/logout", data={"csrf_token": csrf_token})
    reset_csrf(logged_in_client)
    response = logged_in_client.post("/login", data={
        "email": "student@example.com",
        "password": "wrong-password",
        "csrf_token": csrf_token,
    })
    assert b"Incorrect email or password" in response.data


def test_login_with_correct_credentials_redirects_home(logged_in_client, csrf_token):
    logged_in_client.post("/logout", data={"csrf_token": csrf_token})
    reset_csrf(logged_in_client)
    response = logged_in_client.post("/login", data={
        "email": "student@example.com",
        "password": "testpassword123",
        "csrf_token": csrf_token,
    })
    assert response.status_code == 302
    assert response.headers["Location"] == "/dashboard"


def test_post_without_csrf_token_is_rejected(logged_in_client):
    response = logged_in_client.post("/add", data={"name": "MIT", "deadline": "2027-01-01"})
    assert response.status_code == 400


def test_home_shows_onboarding_welcome_for_new_user(logged_in_client):
    response = logged_in_client.get("/dashboard")
    assert "Let's set up your first application".encode() in response.data


def test_add_university_appears_on_home_page(logged_in_client, csrf_token):
    logged_in_client.post("/add", data={"name": "MIT", "deadline": "2027-01-01", "csrf_token": csrf_token})
    response = logged_in_client.get("/dashboard")
    assert b"MIT" in response.data


def test_delete_university_removes_it(logged_in_client, csrf_token):
    logged_in_client.post("/add", data={"name": "MIT", "deadline": "2027-01-01", "csrf_token": csrf_token})
    response = logged_in_client.get("/dashboard")
    html = response.data.decode()

    # Find MIT's specific delete form, not just the first one on the page
    mit_section = html.split(">MIT<")[1]
    university_id = mit_section.split('action="/delete/')[1].split('"')[0]

    logged_in_client.post(f"/delete/{university_id}", data={"csrf_token": csrf_token})
    response = logged_in_client.get("/dashboard")
    assert b"MIT" not in response.data


def test_new_university_gets_default_checklist(logged_in_client, csrf_token):
    logged_in_client.post("/add", data={"name": "MIT", "deadline": "2027-01-01", "csrf_token": csrf_token})
    response = logged_in_client.get("/dashboard")
    assert b"Essay" in response.data
    assert b"Recommendation Letters" in response.data
    assert b"Transcript" in response.data


def test_user_cannot_see_another_users_university(client, csrf_token):
    client.post("/signup", data={
        "email": "first@example.com",
        "password": "testpassword123",
        "confirm_password": "testpassword123",
        "csrf_token": csrf_token,
    })
    reset_csrf(client)
    client.post("/add", data={"name": "Private University", "deadline": "2027-01-01", "csrf_token": csrf_token})
    client.post("/logout", data={"csrf_token": csrf_token})
    reset_csrf(client)

    client.post("/signup", data={
        "email": "second@example.com",
        "password": "testpassword123",
        "confirm_password": "testpassword123",
        "csrf_token": csrf_token,
    })
    response = client.get("/dashboard")
    assert b"Private University" not in response.data


def test_send_reminders_without_email_config_redirects_with_error(logged_in_client):
    # EMAIL_ADDRESS/RESEND_API_KEY aren't set in the test environment,
    # so this should redirect back with an error instead of crashing.
    response = logged_in_client.get("/reminders/send")
    assert response.status_code == 302
    assert "reminder_error=not_configured" in response.headers["Location"]


def test_delete_university_can_be_undone(logged_in_client, csrf_token):
    logged_in_client.post("/add", data={"name": "MIT", "deadline": "2027-01-01", "csrf_token": csrf_token})
    response = logged_in_client.get("/dashboard")
    html = response.data.decode()
    mit_section = html.split(">MIT<")[1]
    university_id = mit_section.split('action="/delete/')[1].split('"')[0]

    delete_response = logged_in_client.post(f"/delete/{university_id}", data={"csrf_token": csrf_token})
    assert delete_response.status_code == 302
    assert "deleted_name=MIT" in delete_response.headers["Location"]
    response = logged_in_client.get("/dashboard")
    assert b"MIT" not in response.data

    restore_response = logged_in_client.post(f"/restore/{university_id}", data={"csrf_token": csrf_token})
    assert restore_response.status_code == 302
    response = logged_in_client.get("/dashboard")
    assert b"MIT" in response.data


def test_restore_requires_ownership(client, csrf_token):
    client.post("/signup", data={
        "email": "owner@example.com", "password": "testpassword123",
        "confirm_password": "testpassword123", "csrf_token": csrf_token,
    })
    reset_csrf(client)
    client.post("/add", data={"name": "Private University", "deadline": "2027-01-01", "csrf_token": csrf_token})
    response = client.get("/dashboard")
    html = response.data.decode()
    section = html.split(">Private University<")[1]
    university_id = section.split('action="/delete/')[1].split('"')[0]
    client.post(f"/delete/{university_id}", data={"csrf_token": csrf_token})
    client.post("/logout", data={"csrf_token": csrf_token})
    reset_csrf(client)

    client.post("/signup", data={
        "email": "intruder@example.com", "password": "testpassword123",
        "confirm_password": "testpassword123", "csrf_token": csrf_token,
    })
    reset_csrf(client)
    response = client.post(f"/restore/{university_id}", data={"csrf_token": csrf_token})
    assert response.status_code == 404


def test_duplicate_university_copies_checklist(logged_in_client, csrf_token):
    logged_in_client.post("/add", data={"name": "MIT", "deadline": "2027-01-01", "csrf_token": csrf_token})
    response = logged_in_client.get("/dashboard")
    html = response.data.decode()
    mit_section = html.split(">MIT<")[1]
    university_id = mit_section.split('action="/duplicate/')[1].split('"')[0]

    duplicate_response = logged_in_client.post(f"/duplicate/{university_id}", data={"csrf_token": csrf_token})
    assert duplicate_response.status_code == 302

    response = logged_in_client.get("/dashboard")
    assert b"MIT (Copy)" in response.data
    assert b"Essay" in response.data


def test_export_csv_contains_university_name(logged_in_client, csrf_token):
    logged_in_client.post("/add", data={"name": "MIT", "deadline": "2027-01-01", "csrf_token": csrf_token})
    response = logged_in_client.get("/export/csv")
    assert response.status_code == 200
    assert response.mimetype == "text/csv"
    assert b"MIT" in response.data


def test_export_calendar_contains_deadline_event(logged_in_client, csrf_token):
    logged_in_client.post("/add", data={"name": "MIT", "deadline": "2027-01-01", "csrf_token": csrf_token})
    response = logged_in_client.get("/export/calendar")
    assert response.status_code == 200
    assert response.mimetype == "text/calendar"
    assert b"BEGIN:VEVENT" in response.data
    assert b"MIT" in response.data
    assert b"DTSTART;VALUE=DATE:20270101" in response.data


def test_export_csv_neutralizes_formula_injection(logged_in_client, csrf_token):
    logged_in_client.post("/add", data={
        "name": "=cmd|'/c calc'!A1", "deadline": "2027-01-01", "csrf_token": csrf_token,
    })
    response = logged_in_client.get("/export/csv")
    assert b"\r\n'=cmd" in response.data or b"\n'=cmd" in response.data


def test_export_calendar_escapes_embedded_newlines(logged_in_client, csrf_token):
    conn = app.get_db()
    cursor = conn.execute(
        "INSERT INTO universities (user_id, name, deadline) VALUES (?, ?, ?)",
        (1, "Evil\r\nEND:VEVENT\r\nBEGIN:VALARM", "2027-01-01"),
    )
    conn.commit()
    conn.close()

    response = logged_in_client.get("/export/calendar")
    text = response.data.decode()
    # The injected content must stay inert text inside one SUMMARY line
    # (escaped as literal "\n"), never a real CRLF-delimited ICS line.
    assert "\r\nEND:VEVENT\r\nBEGIN:VALARM" not in text
    assert "\\nEND:VEVENT\\nBEGIN:VALARM" in text


def test_export_calendar_skips_malformed_deadline(logged_in_client, csrf_token):
    conn = app.get_db()
    conn.execute(
        "INSERT INTO universities (user_id, name, deadline) VALUES (?, ?, ?)",
        (1, "Malformed", "2027-01-01\r\nEND:VEVENT\r\nBEGIN:VALARM"),
    )
    conn.commit()
    conn.close()

    response = logged_in_client.get("/export/calendar")
    text = response.data.decode()
    assert "BEGIN:VALARM" not in text
    assert "Malformed" not in text


def test_deleted_university_excluded_from_exports(logged_in_client, csrf_token):
    logged_in_client.post("/add", data={"name": "MIT", "deadline": "2027-01-01", "csrf_token": csrf_token})
    response = logged_in_client.get("/dashboard")
    html = response.data.decode()
    mit_section = html.split(">MIT<")[1]
    university_id = mit_section.split('action="/delete/')[1].split('"')[0]
    logged_in_client.post(f"/delete/{university_id}", data={"csrf_token": csrf_token})

    csv_response = logged_in_client.get("/export/csv")
    assert b"MIT" not in csv_response.data
    calendar_response = logged_in_client.get("/export/calendar")
    assert b"MIT" not in calendar_response.data
