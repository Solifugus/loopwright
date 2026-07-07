import pytest
from fastapi.testclient import TestClient

from loopwright import service
from loopwright.core.model import ProjectStore, RunState
from loopwright.gitctl.repo import ProjectRepo
from loopwright.orchestrator.decision import DEPLOY_STEP, DEV_STEP
from loopwright.orchestrator.loop import FINISHED, run_loop
from loopwright.orchestrator.report import generate_report
from loopwright.web.app import create_app


@pytest.fixture
def store(tmp_path):
    return ProjectStore(tmp_path / "projects")


def finished_run(store, name="demo"):
    """A project that just finished its last cycle and sits in REVIEW."""
    project = service.create_project(store, name)
    repo = ProjectRepo(project.repo_path)
    # workers "completed" the plan: tick the template's tasks on agent/work
    devplan = repo.show("agent/work", "DEVPLAN.md").replace("- [ ]", "- [x]")
    repo.commit_files({"DEVPLAN.md": devplan}, branch="agent/work", message="Task 1: done")
    repo.tag_checkpoint("scaffold")

    run = store.load_run(name)
    run.transition(RunState.READY)
    run.transition(RunState.RUNNING)
    run.record_step(DEV_STEP, "ok", "t", {"tasks_remaining": False})
    run.record_step(DEPLOY_STEP, "ok", "t", {})
    run.transition(RunState.REVIEW)
    store.save_run(name, run)

    log = service.run_log(store, name)
    log.log("deploy-test", "scripts/acceptance.sh passed")
    log.log("review", "decision: finish — all tasks complete and deployment passed")
    return repo


def test_generate_report_covers_all_sections(store):
    finished_run(store)
    report = generate_report(store, "demo")
    assert "# Final Report — demo" in report
    assert "Run state: **REVIEW** after **1** cycle(s)" in report
    assert "`checkpoint/0001-scaffold`" in report
    assert "scripts/acceptance.sh passed" in report
    assert "decision: finish" in report
    assert "None — every DEVPLAN task is checked off." in report
    assert "READY → RUNNING" in report


def test_report_lists_open_tasks_as_deviations(store):
    repo = finished_run(store)
    devplan = repo.show("agent/work", "DEVPLAN.md") + "- [ ] 9. never got to this\n"
    repo.commit_files({"DEVPLAN.md": devplan}, branch="agent/work", message="add task")
    report = generate_report(store, "demo")
    assert "were **not** completed" in report
    assert "never got to this" in report


def test_promote_candidate_commits_report(store):
    repo = finished_run(store)
    report = service.promote_candidate(store, "demo")
    assert "# Final Report — demo" in report
    assert repo.show("release/candidate", "FINAL_REPORT.md") == report
    # candidate now carries the work AND the report; main still behind
    assert service.release_status(store, "demo")["pending"] is True


def test_promote_requires_review_state(store):
    service.create_project(store, "demo")
    with pytest.raises(ValueError, match="cannot promote"):
        service.promote_candidate(store, "demo")


def test_approve_release_fast_forwards_main_and_finishes(store):
    repo = finished_run(store)
    service.promote_candidate(store, "demo")
    head = service.approve_release(store, "demo")

    assert repo.head_of("main") == head == repo.head_of("release/candidate")
    run = store.load_run("demo")
    assert run.state is RunState.DONE
    assert service.release_status(store, "demo")["pending"] is False


def test_approve_release_requires_pending_candidate(store):
    service.create_project(store, "demo")
    run = store.load_run("demo")
    run.transition(RunState.READY)
    run.transition(RunState.RUNNING)
    run.transition(RunState.REVIEW)
    store.save_run("demo", run)
    with pytest.raises(ValueError, match="no release candidate"):
        service.approve_release(store, "demo")


def test_approve_release_requires_review_state(store):
    finished_run(store)
    service.promote_candidate(store, "demo")
    service.approve_release(store, "demo")  # run is now DONE
    with pytest.raises(ValueError, match="cannot approve"):
        service.approve_release(store, "demo")


def test_loop_finish_promotes_candidate(store):
    from loopwright.orchestrator.engine import Step

    service.create_project(store, "demo")
    repo = ProjectRepo(store.project_dir("demo") / "repo.git")
    run = store.load_run("demo")
    run.transition(RunState.READY)
    store.save_run("demo", run)

    steps = [
        Step(DEV_STEP, lambda ctx: {"tasks_remaining": False}),
        Step(DEPLOY_STEP, lambda ctx: {}),
    ]
    assert run_loop(store, "demo", steps) == FINISHED
    assert "# Final Report — demo" in repo.show("release/candidate", "FINAL_REPORT.md")
    assert service.release_status(store, "demo")["pending"] is True


# --- the approval flow in the UI ---


def make_client(store):
    return TestClient(create_app(store))


def test_dashboard_offers_approval_when_pending(store):
    finished_run(store)
    service.promote_candidate(store, "demo")
    client = make_client(store)
    page = client.get("/projects/demo/dashboard")
    assert "Release candidate ready" in page.text
    assert "Approve release" in page.text
    assert "/projects/demo/report" in page.text


def test_dashboard_hides_approval_without_candidate(store):
    service.create_project(store, "demo")
    client = make_client(store)
    assert "Approve release" not in client.get("/projects/demo/dashboard").text


def test_report_page_renders(store):
    finished_run(store)
    service.promote_candidate(store, "demo")
    client = make_client(store)
    page = client.get("/projects/demo/report")
    assert page.status_code == 200
    assert "Final Report — demo" in page.text


def test_report_page_404_before_promotion(store):
    service.create_project(store, "demo")
    assert make_client(store).get("/projects/demo/report").status_code == 404


def test_approve_from_ui_completes_run(store):
    repo = finished_run(store)
    service.promote_candidate(store, "demo")
    client = make_client(store)

    response = client.post("/projects/demo/release/approve")
    assert response.status_code == 200
    assert "state-DONE" in response.text
    assert store.load_run("demo").state is RunState.DONE
    assert repo.head_of("main") == repo.head_of("release/candidate")


def test_approve_from_ui_error_when_not_pending(store):
    service.create_project(store, "demo")
    run = store.load_run("demo")
    run.transition(RunState.READY)
    run.transition(RunState.RUNNING)
    run.transition(RunState.REVIEW)
    store.save_run("demo", run)

    response = make_client(store).post("/projects/demo/release/approve")
    assert "no release candidate" in response.text
    assert store.load_run("demo").state is RunState.REVIEW