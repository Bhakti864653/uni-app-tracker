def test_home_redirects_to_login_when_not_logged_in(client):
    response = client.get("/")
    assert response.status_code == 302
    assert "/login" in response.headers["Location"]


def test_login_with_wrong_password_shows_error(client):
    response = client.post("/login", data={"password": "wrong-password"})
    assert b"Incorrect password" in response.data


def test_login_with_correct_password_redirects_home(client):
    response = client.post("/login", data={"password": "testpassword"})
    assert response.status_code == 302
    assert response.headers["Location"] == "/"


def test_home_shows_seeded_universities(logged_in_client):
    response = logged_in_client.get("/")
    assert b"Stanford" in response.data
    assert b"UC Berkeley" in response.data


def test_add_university_appears_on_home_page(logged_in_client):
    logged_in_client.post("/add", data={"name": "MIT", "deadline": "2027-01-01"})
    response = logged_in_client.get("/")
    assert b"MIT" in response.data


def test_delete_university_removes_it(logged_in_client):
    logged_in_client.post("/add", data={"name": "MIT", "deadline": "2027-01-01"})
    response = logged_in_client.get("/")
    html = response.data.decode()

    # Find MIT's specific delete link, not just the first one on the page
    mit_section = html.split(">MIT<")[1]
    university_id = mit_section.split('href="/delete/')[1].split('"')[0]

    logged_in_client.get(f"/delete/{university_id}")
    response = logged_in_client.get("/")
    assert b"MIT" not in response.data


def test_new_university_gets_default_checklist(logged_in_client):
    logged_in_client.post("/add", data={"name": "MIT", "deadline": "2027-01-01"})
    response = logged_in_client.get("/")
    assert b"Essay" in response.data
    assert b"Recommendation Letters" in response.data
    assert b"Transcript" in response.data
