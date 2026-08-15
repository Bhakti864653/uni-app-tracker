from conftest import reset_csrf


def test_home_redirects_to_login_when_not_logged_in(client):
    response = client.get("/")
    assert response.status_code == 302
    assert "/login" in response.headers["Location"]


def test_signup_creates_account_and_logs_in(client, csrf_token):
    response = client.post("/signup", data={
        "email": "new@example.com",
        "password": "testpassword123",
        "confirm_password": "testpassword123",
        "csrf_token": csrf_token,
    })
    assert response.status_code == 302
    assert response.headers["Location"] == "/"


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
    assert response.headers["Location"] == "/"


def test_post_without_csrf_token_is_rejected(logged_in_client):
    response = logged_in_client.post("/add", data={"name": "MIT", "deadline": "2027-01-01"})
    assert response.status_code == 400


def test_home_shows_empty_state_for_new_user(logged_in_client):
    response = logged_in_client.get("/")
    assert b"No universities yet" in response.data


def test_add_university_appears_on_home_page(logged_in_client, csrf_token):
    logged_in_client.post("/add", data={"name": "MIT", "deadline": "2027-01-01", "csrf_token": csrf_token})
    response = logged_in_client.get("/")
    assert b"MIT" in response.data


def test_delete_university_removes_it(logged_in_client, csrf_token):
    logged_in_client.post("/add", data={"name": "MIT", "deadline": "2027-01-01", "csrf_token": csrf_token})
    response = logged_in_client.get("/")
    html = response.data.decode()

    # Find MIT's specific delete form, not just the first one on the page
    mit_section = html.split(">MIT<")[1]
    university_id = mit_section.split('action="/delete/')[1].split('"')[0]

    logged_in_client.post(f"/delete/{university_id}", data={"csrf_token": csrf_token})
    response = logged_in_client.get("/")
    assert b"MIT" not in response.data


def test_new_university_gets_default_checklist(logged_in_client, csrf_token):
    logged_in_client.post("/add", data={"name": "MIT", "deadline": "2027-01-01", "csrf_token": csrf_token})
    response = logged_in_client.get("/")
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
    response = client.get("/")
    assert b"Private University" not in response.data


def test_send_reminders_without_email_config_redirects_with_error(logged_in_client):
    # EMAIL_ADDRESS/RESEND_API_KEY aren't set in the test environment,
    # so this should redirect back with an error instead of crashing.
    response = logged_in_client.get("/reminders/send")
    assert response.status_code == 302
    assert "reminder_error=not_configured" in response.headers["Location"]
