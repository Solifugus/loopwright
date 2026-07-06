"""FastAPI application serving the Loopwright web UI."""

from pathlib import Path
from urllib.parse import quote

from fastapi import FastAPI, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from loopwright import service
from loopwright.core.model import IllegalTransition, ProjectStore
from loopwright.gitctl.repo import GitError
from loopwright.notify.ntfy import NullNotifier

WEB_DIR = Path(__file__).parent


def create_app(store: ProjectStore, notifier=None) -> FastAPI:
    """Build the app around an injected store so tests can use a temp directory."""
    notifier = notifier if notifier is not None else NullNotifier()
    app = FastAPI(title="Loopwright")
    app.state.store = store
    app.state.notifier = notifier
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
            service.create_project(store, name.strip())
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

    @app.get("/projects/{name}/packet", response_class=HTMLResponse)
    def packet_editor(request: Request, name: str, saved: int = 0, error: str = ""):
        project = _load_or_404(name)
        run = store.load_run(name)
        files = service.load_packet(store, name)
        return templates.TemplateResponse(
            request,
            "packet.html",
            {"project": project, "run": run, "files": files, "saved": saved, "error": error},
        )

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
    from loopwright.core.config import load_config
    from loopwright.notify.ntfy import from_config

    config = load_config()
    return create_app(ProjectStore(config.projects_dir), notifier=from_config(config))
