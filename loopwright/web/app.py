"""FastAPI application serving the Loopwright web UI."""

from pathlib import Path

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from loopwright.core.model import ProjectStore

WEB_DIR = Path(__file__).parent


def create_app(store: ProjectStore) -> FastAPI:
    """Build the app around an injected store so tests can use a temp directory."""
    app = FastAPI(title="Loopwright")
    app.state.store = store
    templates = Jinja2Templates(directory=WEB_DIR / "templates")
    app.mount("/static", StaticFiles(directory=WEB_DIR / "static"), name="static")

    @app.get("/", response_class=HTMLResponse)
    def index(request: Request):
        rows = []
        for name in store.list_projects():
            rows.append({"project": store.load_project(name), "run": store.load_run(name)})
        return templates.TemplateResponse(request, "index.html", {"rows": rows})

    @app.get("/projects/{name}", response_class=HTMLResponse)
    def project_detail(request: Request, name: str):
        try:
            project = store.load_project(name)
        except (FileNotFoundError, ValueError) as exc:
            raise HTTPException(status_code=404, detail=f"no project named {name!r}") from exc
        run = store.load_run(name)
        return templates.TemplateResponse(
            request, "project.html", {"project": project, "run": run}
        )

    return app


def create_app_from_config() -> FastAPI:
    from loopwright.core.config import load_config

    config = load_config()
    return create_app(ProjectStore(config.projects_dir))
