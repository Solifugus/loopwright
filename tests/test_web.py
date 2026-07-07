import pytest
from fastapi.testclient import TestClient

from loopwright import service
from loopwright.core.model import ProjectStore, Run, RunState
from loopwright.gitctl.repo import ProjectRepo
from loopwright.notify.ntfy import Event, NullNotifier
from loopwright.web.app import create_app


@pytest.fixture
def store(tmp_path):
    return ProjectStore(tmp_path / "projects")


@pytest.fixture
def notifier():
    return NullNotifier()


@pytest.fixture
def client(store, notifier):
    return TestClient(create_app(store, notifier=notifier))


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


# --- wizard and packet editor (task 5.2) ---


def test_new_project_form_renders(client):
    response = client.get("/projects/new")
    assert response.status_code == 200
    assert 'name="name"' in response.text


def test_wizard_creates_project_and_redirects_to_editor(store, client):
    response = client.post("/projects", data={"name": "demo"}, follow_redirects=False)
    assert response.status_code == 303
    assert response.headers["location"] == "/projects/demo/packet"
    assert (store.project_dir("demo") / "project.yaml").is_file()
    assert ProjectRepo(store.project_dir("demo") / "repo.git").branches()  # git repo exists

    editor = client.get("/projects/demo/packet")
    assert "DESIGN.md" in editor.text
    assert "demo — Design" in editor.text


def test_wizard_prepopulates_from_doctrine(store, notifier, tmp_path):
    base = tmp_path / "doctrine"
    (base / "templates").mkdir(parents=True)
    (base / "templates" / "DESIGN.md").write_text("# {{PROJECT}} via doctrine\n")
    client = TestClient(create_app(store, notifier=notifier, doctrine_dir=base))

    client.post("/projects", data={"name": "demo"})
    editor = client.get("/projects/demo/packet")
    assert "# demo via doctrine" in editor.text


def test_wizard_rejects_duplicate_name(store, client):
    service.create_project(store, "demo")
    response = client.post("/projects", data={"name": "demo"})
    assert response.status_code == 400
    assert "already exists" in response.text


def test_wizard_rejects_invalid_name(client):
    response = client.post("/projects", data={"name": "Bad Name!"})
    assert response.status_code == 400
    assert "invalid project name" in response.text


def test_save_draft_persists_without_committing(store, client):
    service.create_project(store, "demo")
    repo = ProjectRepo(store.project_dir("demo") / "repo.git")
    head_before = repo.head_of("design/main")

    response = client.post(
        "/projects/demo/packet/save",
        data={"design": "# my design", "devplan": "# my plan", "testplan": "# my tests"},
        follow_redirects=False,
    )
    assert response.status_code == 303

    files = service.load_packet(store, "demo")
    assert files["DESIGN.md"] == "# my design"
    assert repo.head_of("design/main") == head_before  # saving never commits

    editor = client.get("/projects/demo/packet?saved=1")
    assert "Draft saved" in editor.text


def test_approve_commits_and_marks_ready(store, client):
    service.create_project(store, "demo")
    repo = ProjectRepo(store.project_dir("demo") / "repo.git")
    head_before = repo.head_of("design/main")

    response = client.post(
        "/projects/demo/packet/approve",
        data={"design": "# final design", "devplan": "# plan", "testplan": "# tests"},
        follow_redirects=False,
    )
    assert response.status_code == 303
    assert response.headers["location"] == "/projects/demo"
    assert repo.head_of("design/main") != head_before
    assert store.load_run("demo").state is RunState.READY


def test_approve_blocked_while_running_shows_error(store, client):
    service.create_project(store, "demo")
    run = store.load_run("demo")
    run.transition(RunState.READY)
    run.transition(RunState.RUNNING)
    store.save_run("demo", run)

    response = client.post(
        "/projects/demo/packet/approve",
        data={"design": "x", "devplan": "y", "testplan": "z"},
        follow_redirects=False,
    )
    assert response.status_code == 303
    assert "error=" in response.headers["location"]

    editor = client.get(response.headers["location"])
    assert "RUNNING" in editor.text
    assert store.load_run("demo").state is RunState.RUNNING


# --- run controls and dashboard (task 5.3) ---


def ready_project(store, name="demo"):
    store.create(name, "/nowhere/repo.git")
    run = store.load_run(name)
    run.transition(RunState.READY)
    store.save_run(name, run)


def test_detail_page_polls_dashboard(store, client):
    ready_project(store)
    response = client.get("/projects/demo")
    assert 'hx-get="/projects/demo/dashboard"' in response.text
    assert 'hx-trigger="every 5s"' in response.text


def test_dashboard_shows_state_appropriate_buttons(store, client):
    ready_project(store)
    response = client.get("/projects/demo/dashboard")
    assert response.status_code == 200
    assert "/run/start" in response.text
    assert "/run/stop" in response.text
    assert "/run/pause" not in response.text


def test_start_button_transitions_and_notifies(store, client, notifier):
    ready_project(store)
    response = client.post("/projects/demo/run/start")
    assert response.status_code == 200
    assert "state-RUNNING" in response.text
    assert "/run/pause" in response.text  # buttons refreshed for the new state
    assert store.load_run("demo").state is RunState.RUNNING
    assert notifier.events == [(Event.RUN_STARTED, "Run started for demo", "demo")]


def test_pause_and_stop_do_not_notify(store, client, notifier):
    ready_project(store)
    client.post("/projects/demo/run/start")
    client.post("/projects/demo/run/pause")
    client.post("/projects/demo/run/stop")
    assert store.load_run("demo").state is RunState.STOPPED
    assert [event for event, _, _ in notifier.events] == [Event.RUN_STARTED]


def test_illegal_action_shows_error_and_preserves_state(store, client):
    store.create("demo", "/nowhere/repo.git")  # still DRAFT
    response = client.post("/projects/demo/run/start")
    assert response.status_code == 200
    assert "cannot start" in response.text
    assert store.load_run("demo").state is RunState.DRAFT


def test_unknown_action_shows_error(store, client):
    ready_project(store)
    response = client.post("/projects/demo/run/explode")
    assert "unknown run action" in response.text
    assert store.load_run("demo").state is RunState.READY


def test_dashboard_lists_checkpoints(store, client):
    service.create_project(store, "demo")
    repo = ProjectRepo(store.project_dir("demo") / "repo.git")
    repo.tag_checkpoint("hello-world")
    response = client.get("/projects/demo/dashboard")
    assert "checkpoint/0001-hello-world" in response.text


# --- rollback from the UI (task 6.5) ---


def rollback_ready(store):
    service.create_project(store, "demo")
    repo = ProjectRepo(store.project_dir("demo") / "repo.git")
    tag = repo.tag_checkpoint("good")
    old_head = repo.head_of("agent/work")
    repo.commit_packet({"DESIGN.md": "# v2\n"}, message="advance")
    repo.reset_branch("agent/work", "design/main")
    run = store.load_run("demo")
    run.transition(RunState.READY)
    store.save_run("demo", run)
    return repo, tag, old_head


def test_rollback_button_shown_when_safe(store, client):
    rollback_ready(store)
    response = client.get("/projects/demo/dashboard")
    assert "Roll back here" in response.text


def test_rollback_button_hidden_while_running(store, client):
    rollback_ready(store)
    run = store.load_run("demo")
    run.transition(RunState.RUNNING)
    store.save_run("demo", run)
    response = client.get("/projects/demo/dashboard")
    assert "Roll back here" not in response.text


def test_rollback_post_rewinds_and_refreshes_dashboard(store, client):
    repo, tag, old_head = rollback_ready(store)
    response = client.post("/projects/demo/rollback", data={"tag": tag})
    assert response.status_code == 200
    assert repo.head_of("agent/work") == old_head


def test_rollback_post_while_running_shows_error(store, client):
    repo, tag, old_head = rollback_ready(store)
    run = store.load_run("demo")
    run.transition(RunState.RUNNING)
    store.save_run("demo", run)
    response = client.post("/projects/demo/rollback", data={"tag": tag})
    assert "cannot roll back" in response.text
    assert repo.head_of("agent/work") != old_head  # branch untouched


# --- log viewer (task 5.4) ---


def test_logs_page_renders_with_filters(store, client):
    store.create("demo", "/nowhere/repo.git")
    service.run_log(store, "demo").log("clone", "hello")
    response = client.get("/projects/demo/logs")
    assert response.status_code == 200
    assert 'hx-get="/projects/demo/logs/entries"' in response.text
    assert '<option value="clone">' in response.text  # step dropdown populated
    assert '<option value="error">' in response.text


def test_component_written_entries_appear(store, client):
    store.create("demo", "/nowhere/repo.git")
    log = service.run_log(store, "demo")
    log.log("clone", "cloning repository")
    log.log("deploy", "deploy failed", level="error", exit_code=3)

    response = client.get("/projects/demo/logs/entries")
    assert "cloning repository" in response.text
    assert "deploy failed" in response.text
    assert "lvl-error" in response.text


def test_entries_filtered_by_level(store, client):
    store.create("demo", "/nowhere/repo.git")
    log = service.run_log(store, "demo")
    log.log("a", "fine")
    log.log("b", "broken", level="error")

    response = client.get("/projects/demo/logs/entries?level=error")
    assert "broken" in response.text
    assert "fine" not in response.text


def test_entries_limit_tails(store, client):
    store.create("demo", "/nowhere/repo.git")
    log = service.run_log(store, "demo")
    for i in range(10):
        log.log("s", f"entry-{i}")
    response = client.get("/projects/demo/logs/entries?limit=2")
    assert "entry-9" in response.text
    assert "entry-0" not in response.text


def test_logs_empty_state(store, client):
    store.create("demo", "/nowhere/repo.git")
    response = client.get("/projects/demo/logs/entries")
    assert "No log entries match" in response.text


def test_logs_unknown_project_404(client):
    assert client.get("/projects/nope/logs").status_code == 404
    assert client.get("/projects/nope/logs/entries").status_code == 404
