"""FastAPI application serving the Loopwright web UI."""

from pathlib import Path
from urllib.parse import quote

from fastapi import FastAPI, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from loopwright import service
from loopwright.core.model import IllegalTransition, ProjectStore
from loopwright.core.runlog import LEVELS
from loopwright.gitctl.repo import GitError
from loopwright.notify.ntfy import NullNotifier

WEB_DIR = Path(__file__).parent


def create_app(store: ProjectStore, notifier=None, assistant=None, doctrine_dir=None) -> FastAPI:
    """Build the app around injected collaborators so tests can use fakes."""
    notifier = notifier if notifier is not None else NullNotifier()
    app = FastAPI(title="Loopwright")
    app.state.store = store
    app.state.notifier = notifier
    app.state.assistant = assistant
    templates = Jinja2Templates(directory=WEB_DIR / "templates")
    app.mount("/static", StaticFiles(directory=WEB_DIR / "static"), name="static")

    @app.get("/", response_class=HTMLResponse)
    def index(request: Request):
        rows = []
        for name in store.list_projects():
            rows.append({"project": store.load_project(name), "run": store.load_run(name)})
        return templates.TemplateResponse(request, "index.html", {"rows": rows})

    @app.get("/projects/new", response_class=HTMLResponse)
    def new_project_form(request: Request):
        return templates.TemplateResponse(request, "new_project.html", {"error": None})

    @app.post("/projects", response_class=HTMLResponse)
    def create_project(request: Request, name: str = Form(...)):
        try:
            service.create_project(store, name.strip(), doctrine_dir=doctrine_dir)
        except (ValueError, FileExistsError, GitError) as exc:
            return templates.TemplateResponse(
                request, "new_project.html", {"error": str(exc)}, status_code=400
            )
        return RedirectResponse(f"/projects/{name.strip()}/packet", status_code=303)

    def _load_or_404(name: str):
        try:
            return store.load_project(name)
        except (FileNotFoundError, ValueError) as exc:
            raise HTTPException(status_code=404, detail=f"no project named {name!r}") from exc

    def _dashboard_context(request: Request, name: str, error: str = ""):
        project = _load_or_404(name)
        run = store.load_run(name)
        return {
            "request": request,
            "project": project,
            "run": run,
            "actions": service.available_actions(run),
            "checkpoints": service.list_checkpoints(store, name),
            "can_rollback": run.state in service.ROLLBACK_STATES,
            "release_pending": (
                run.state.value == "REVIEW" and service.release_status(store, name)["pending"]
            ),
            "error": error,
        }

    @app.get("/projects/{name}", response_class=HTMLResponse)
    def project_detail(request: Request, name: str):
        context = _dashboard_context(request, name)
        return templates.TemplateResponse(request, "project.html", context)

    @app.get("/projects/{name}/dashboard", response_class=HTMLResponse)
    def dashboard_partial(request: Request, name: str):
        context = _dashboard_context(request, name)
        return templates.TemplateResponse(request, "_dashboard.html", context)

    @app.post("/projects/{name}/run/{action}", response_class=HTMLResponse)
    def run_control(request: Request, name: str, action: str):
        _load_or_404(name)
        error = ""
        try:
            service.control_run(store, name, action, notifier=notifier)
        except (ValueError, IllegalTransition) as exc:
            error = str(exc)
        context = _dashboard_context(request, name, error=error)
        return templates.TemplateResponse(request, "_dashboard.html", context)

    @app.get("/projects/{name}/report", response_class=HTMLResponse)
    def final_report(request: Request, name: str):
        from loopwright.gitctl.repo import ProjectRepo

        project = _load_or_404(name)
        try:
            report = ProjectRepo(project.repo_path).show("release/candidate", "FINAL_REPORT.md")
        except GitError as exc:
            raise HTTPException(
                status_code=404, detail="no final report yet for this project"
            ) from exc
        return templates.TemplateResponse(
            request, "report.html", {"project": project, "report": report}
        )

    @app.post("/projects/{name}/release/approve", response_class=HTMLResponse)
    def approve_release(request: Request, name: str):
        _load_or_404(name)
        error = ""
        try:
            service.approve_release(store, name)
        except (ValueError, GitError) as exc:
            error = str(exc)
        context = _dashboard_context(request, name, error=error)
        return templates.TemplateResponse(request, "_dashboard.html", context)

    @app.post("/projects/{name}/rollback", response_class=HTMLResponse)
    def rollback(request: Request, name: str, tag: str = Form(...)):
        _load_or_404(name)
        error = ""
        try:
            service.rollback_to_checkpoint(store, name, tag)
        except (ValueError, GitError) as exc:
            error = str(exc)
        context = _dashboard_context(request, name, error=error)
        return templates.TemplateResponse(request, "_dashboard.html", context)

    @app.get("/projects/{name}/logs", response_class=HTMLResponse)
    def logs_page(request: Request, name: str):
        project = _load_or_404(name)
        log = service.run_log(store, name)
        return templates.TemplateResponse(
            request, "logs.html", {"project": project, "steps": log.steps(), "levels": LEVELS}
        )

    @app.get("/projects/{name}/logs/entries", response_class=HTMLResponse)
    def logs_entries(request: Request, name: str, level: str = "", step: str = "", limit: int = 200):
        _load_or_404(name)
        entries = service.run_log(store, name).read(
            level=level or None, step=step or None, limit=limit
        )
        return templates.TemplateResponse(request, "_log_entries.html", {"entries": entries})

    def _packet_context(request: Request, name: str, files: dict, **extra):
        from loopwright.agent import assistant as assistant_mod

        project = _load_or_404(name)
        run = store.load_run(name)
        history = assistant_mod.load_history(service.packet_dir(store, name))
        context = {
            "request": request,
            "project": project,
            "run": run,
            "files": files,
            "chat_history": history,
            "assistant_available": assistant is not None,
            "assistant_error": "",
            "saved": 0,
            "error": "",
        }
        context.update(extra)
        return context

    @app.get("/projects/{name}/packet", response_class=HTMLResponse)
    def packet_editor(request: Request, name: str, saved: int = 0, error: str = ""):
        files = service.load_packet(store, name)
        context = _packet_context(request, name, files, saved=saved, error=error)
        return templates.TemplateResponse(request, "packet.html", context)

    @app.post("/projects/{name}/packet/assistant", response_class=HTMLResponse)
    def packet_assistant(
        request: Request,
        name: str,
        message: str = Form(...),
        design: str = Form(""),
        devplan: str = Form(""),
        testplan: str = Form(""),
    ):
        from loopwright.agent import assistant as assistant_mod
        from loopwright.agent.openai_client import OpenAIError

        _load_or_404(name)
        buffers = {"DESIGN.md": design, "DEVPLAN.md": devplan, "TESTPLAN.md": testplan}
        assistant_error = ""
        if assistant is None:
            assistant_error = (
                "Primary Agent is unavailable: set the OpenAI API key environment "
                "variable and restart the server."
            )
        else:
            packet_directory = service.packet_dir(store, name)
            history = assistant_mod.load_history(packet_directory)
            try:
                reply = assistant.chat(message, buffers, history)
            except OpenAIError as exc:
                assistant_error = str(exc)
            else:
                buffers.update(reply.files)  # editor buffers only — never disk or git
                history.append({"role": "user", "content": message})
                history.append({"role": "assistant", "content": reply.message})
                assistant_mod.save_history(packet_directory, history)
        context = _packet_context(request, name, buffers, assistant_error=assistant_error)
        return templates.TemplateResponse(request, "_packet_editor.html", context)

    @app.post("/projects/{name}/packet/save")
    def packet_save(
        name: str,
        design: str = Form(""),
        devplan: str = Form(""),
        testplan: str = Form(""),
    ):
        _load_or_404(name)
        service.save_packet(
            store, name, {"DESIGN.md": design, "DEVPLAN.md": devplan, "TESTPLAN.md": testplan}
        )
        return RedirectResponse(f"/projects/{name}/packet?saved=1", status_code=303)

    @app.post("/projects/{name}/packet/approve")
    def packet_approve(
        name: str,
        design: str = Form(""),
        devplan: str = Form(""),
        testplan: str = Form(""),
    ):
        _load_or_404(name)
        service.save_packet(
            store, name, {"DESIGN.md": design, "DEVPLAN.md": devplan, "TESTPLAN.md": testplan}
        )
        try:
            service.approve_packet(store, name)
        except (ValueError, GitError) as exc:
            return RedirectResponse(
                f"/projects/{name}/packet?error={quote(str(exc))}", status_code=303
            )
        return RedirectResponse(f"/projects/{name}", status_code=303)

    return app


def create_app_from_config() -> FastAPI:
    from loopwright.agent.assistant import assistant_from_config
    from loopwright.core.config import load_config
    from loopwright.notify.ntfy import from_config

    config = load_config()
    return create_app(
        ProjectStore(config.projects_dir),
        notifier=from_config(config),
        assistant=assistant_from_config(config),
        doctrine_dir=config.doctrine_dir,
    )
