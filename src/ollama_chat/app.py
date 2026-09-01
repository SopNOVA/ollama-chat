"""Fábrica de la aplicación FastAPI.

Flujo al arrancar:

1. `create_app()` construye FastAPI y registra rutas + estáticos.
2. Al aceptar la primera conexión, `lifespan` abre un cliente HTTP
   compartido hacia Ollama y lo guarda en `app.state`.
3. GET `/` sirve `static/index.html`; CSS/JS salen por `/static/...`.
4. Las llamadas `/api/*` las resuelve el router.

Se usa una fábrica (`create_app`) además del objeto global `app` para que
los tests puedan crear instancias limpias sin reutilizar estado.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path

import httpx
from fastapi import FastAPI
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from ollama_chat import __version__
from ollama_chat.config import get_settings
from ollama_chat.routers import router
from ollama_chat.services.ollama import OllamaClient

# Carpeta empaquetada junto al código: HTML, CSS y JS del chat.
STATIC_DIR = Path(__file__).resolve().parent / "static"


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    """Ciclo de vida del proceso: abre recursos al start y los cierra al stop.

    El timeout de lectura es `None` porque un chat puede tardar minutos
    en generar tokens. La conexión inicial a Ollama sí tiene límite (10s).
    """
    settings = get_settings()
    timeout = httpx.Timeout(connect=10.0, read=None, write=30.0, pool=10.0)
    async with httpx.AsyncClient(timeout=timeout) as http:
        app.state.http = http
        app.state.ollama = OllamaClient(http, settings)
        yield  # aquí la app ya está sirviendo requests


def create_app() -> FastAPI:
    """Construye la app: API, archivos estáticos y la página principal."""
    app = FastAPI(
        title="Cypher Chat",
        version=__version__,
        lifespan=lifespan,
    )
    # Endpoints JSON: /api/health, /api/models, /api/chat
    app.include_router(router)
    # CSS y JS que referencia index.html (href="/static/css/...", src="/static/js/...")
    app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")

    @app.get("/", include_in_schema=False)
    async def index() -> FileResponse:
        """Entrega la UI. `include_in_schema=False` la oculta de /docs."""
        return FileResponse(STATIC_DIR / "index.html")

    return app


# Instancia que uvicorn importa con `ollama_chat.app:app`.
app = create_app()
