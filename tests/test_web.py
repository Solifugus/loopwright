import pytest
from fastapi.testclient import TestClient

from loopwright.core.model import ProjectStore, Run, RunState
from loopwright.web.app import create_app


@pytest.fixture
def store(tmp_path):
    return ProjectStore(tmp_path / "projects")


@pytest.fixture
def client(store):
    return TestClient(create_app(store))


def test_index_empty(client):
    response = client.get("/")
    assert response.status_code == 200
    assert "No projects yet" in response.text


def test_created_project_appears_in_index(store, client):
    store.create("demo", "/tmp/demo/repo.git")
    response = client.get("/")
    assert response.status_code == 200
    assert "demo" in response.text
    assert "DRAFT" in response.text
    assert '/projects/demo' in response.text


def test_project_detail_shows_metadata_and_state(store, client):
    store.create("demo", "/tmp/demo/repo.git")
    response = client.get("/projects/demo")
    assert response.status_code == 200
    assert "/tmp/demo/repo.git" in response.text
    assert "DRAFT" in response.text
    assert "No transitions yet" in response.text


def test_project_detail_shows_history(store, client):
    store.create("demo", "/tmp/demo/repo.git")
    run = store.load_run("demo")
    run.transition(RunState.READY)
    run.transition(RunState.RUNNING)
    store.save_run("demo", run)

    response = client.get("/projects/demo")
    assert response.status_code == 200
    assert "READY" in response.text
    assert "RUNNING" in response.text


def test_unknown_project_is_404(client):
    assert client.get("/projects/nope").status_code == 404


def test_invalid_project_name_is_404_not_500(client):
    # names failing NAME_RE raise ValueError in the store; must surface as a plain 404
    assert client.get("/projects/NOT-VALID").status_code == 404


def test_htmx_is_served(client):
    response = client.get("/static/htmx.min.js")
    assert response.status_code == 200
    assert "htmx" in response.text[:200]


def test_index_lists_multiple_sorted(store, client):
    store.create("bravo", "/r/b.git")
    store.create("alpha", "/r/a.git")
    response = client.get("/")
    assert response.text.index("alpha") < response.text.index("bravo")


def test_run_states_render_distinct_badges(store, client):
    store.create("demo", "/r/d.git")
    run = Run()
    run.transition(RunState.READY)
    store.save_run("demo", run)
    response = client.get("/")
    assert 'state-READY' in response.text
