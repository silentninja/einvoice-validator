from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse

from .api.routes.validation import router as validation_router


BASE_DIR = Path(__file__).resolve().parent
TEMPLATE_PATH = BASE_DIR / "templates" / "validation_ui.html"


def create_app() -> FastAPI:
    app = FastAPI(
        title="UAE Peppol PINT-AE Enterprise Diagnostic Engine",
        version="1.1.0",
        description="Monolithic pre-flight validation core running deterministic compliance tracking rules.",
    )
    app.include_router(validation_router)

    @app.get("/", response_class=HTMLResponse)
    async def redirect_to_ui(request: Request) -> HTMLResponse:
        return HTMLResponse(content=TEMPLATE_PATH.read_text(encoding="utf-8"))

    @app.get("/ui", response_class=HTMLResponse)
    async def render_validation_ui(request: Request) -> HTMLResponse:
        return HTMLResponse(content=TEMPLATE_PATH.read_text(encoding="utf-8"))

    return app


app = create_app()
