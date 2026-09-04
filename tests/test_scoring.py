import os

os.environ.setdefault("SECRET_KEY", "test-secret-key")

from datetime import date, timedelta

from app import build_suggestions, compute_readiness_score, days_left_int


def make_university(
    id=1,
    name="MIT",
    status="In Progress",
    deadline="2027-01-01",
    checklist=(),
    essays=(),
    recommendations=(),
    documents=(),
    interviews=(),
):
    return {
        "id": id,
        "name": name,
        "status": status,
        "deadline": deadline,
        "checklist": list(checklist),
        "essays": list(essays),
        "recommendations": list(recommendations),
        "documents": list(documents),
        "interviews": list(interviews),
    }


# --- compute_readiness_score ---


def test_no_items_at_all_scores_zero():
    assert compute_readiness_score([], [], [], []) == 0


def test_empty_checklist_and_no_tasks_scores_zero():
    assert compute_readiness_score([], [], [], [], interviews=[]) == 0


def test_all_checklist_items_done_scores_100():
    checklist = [{"title": "Essay", "done": True}, {"title": "Transcript", "done": True}]
    assert compute_readiness_score(checklist, [], [], []) == 100


def test_all_checklist_items_incomplete_scores_zero():
    checklist = [{"title": "Essay", "done": False}, {"title": "Transcript", "done": False}]
    assert compute_readiness_score(checklist, [], [], []) == 0


def test_half_checklist_done_scores_50():
    checklist = [{"title": "Essay", "done": True}, {"title": "Transcript", "done": False}]
    assert compute_readiness_score(checklist, [], [], []) == 50


def test_essay_status_maps_to_progress_percent():
    essays = [{"title": "Common App", "status": "Drafting"}]
    assert compute_readiness_score([], essays, [], []) == 33


def test_unknown_status_counts_as_zero_progress():
    essays = [{"title": "Common App", "status": "Some Unexpected Status"}]
    assert compute_readiness_score([], essays, [], []) == 0


def test_multiple_components_are_averaged_evenly():
    # checklist 100%, essays 0%, recommendations 100%, documents 0% -> avg 50%
    checklist = [{"title": "Essay", "done": True}]
    essays = [{"title": "Common App", "status": "Not Started"}]
    recommendations = [{"title": "Teacher rec", "status": "Received"}]
    documents = [{"title": "Transcript", "status": "Not Started"}]
    score = compute_readiness_score(checklist, essays, recommendations, documents)
    assert score == 50


def test_interviews_default_to_empty_without_crashing():
    checklist = [{"title": "Essay", "done": True}]
    assert compute_readiness_score(checklist, [], [], []) == 100


def test_interviews_component_included_when_provided():
    interviews = [{"title": "Alumni interview", "status": "Scheduled"}]
    assert compute_readiness_score([], [], [], [], interviews=interviews) == 50


# --- build_suggestions ---


def test_no_universities_returns_no_suggestions():
    assert build_suggestions([]) == []


def test_submitted_university_produces_no_suggestions():
    uni = make_university(
        status="Submitted",
        checklist=[{"title": "Essay", "done": False}],
    )
    assert build_suggestions([uni]) == []


def test_fully_complete_university_produces_no_suggestions():
    uni = make_university(
        checklist=[{"title": "Essay", "done": True}],
        essays=[{"title": "Common App", "status": "Final"}],
        recommendations=[{"title": "Teacher rec", "status": "Received"}],
        documents=[{"title": "Transcript", "status": "Submitted"}],
        interviews=[{"title": "Alumni interview", "status": "Completed"}],
    )
    assert build_suggestions([uni]) == []


def test_incomplete_checklist_item_produces_a_suggestion():
    uni = make_university(checklist=[{"title": "Essay", "done": False}])
    suggestions = build_suggestions([uni])
    assert len(suggestions) == 1
    assert "Essay" in suggestions[0]["text"]
    assert suggestions[0]["university_id"] == uni["id"]


def test_each_incomplete_task_type_produces_its_own_suggestion():
    uni = make_university(
        essays=[{"title": "Common App", "status": "Drafting"}],
        recommendations=[{"title": "Teacher rec", "status": "Requested"}],
        documents=[{"title": "Transcript", "status": "Requested"}],
        interviews=[{"title": "Alumni interview", "status": "Scheduled"}],
    )
    suggestions = build_suggestions([uni])
    assert len(suggestions) == 4


def test_suggestions_sorted_by_soonest_deadline_first():
    soon = make_university(
        id=1, deadline=(date.today() + timedelta(days=2)).isoformat(),
        checklist=[{"title": "Essay", "done": False}],
    )
    later = make_university(
        id=2, deadline=(date.today() + timedelta(days=30)).isoformat(),
        checklist=[{"title": "Essay", "done": False}],
    )
    suggestions = build_suggestions([later, soon])
    assert [s["university_id"] for s in suggestions] == [1, 2]
    assert suggestions[0]["days_left"] == days_left_int(soon["deadline"])


def test_respects_limit_parameter():
    uni = make_university(
        checklist=[{"title": f"Task {i}", "done": False} for i in range(10)]
    )
    suggestions = build_suggestions([uni], limit=3)
    assert len(suggestions) == 3
