"""Endpoints HTTP de Cypher Chat (prefijo `/api`).

El navegador no habla con Ollama directo: pasa por estos handlers, que
delegan en `OllamaClient` guardado en `app.state` durante el lifespan.
"""

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import StreamingResponse

from ollama_chat.config import get_settings, normalize_ollama_url
from ollama_chat.schemas import (
    ChatRequest,
    HealthResponse,
    ModelsResponse,
    SettingsUpdate,
)
from ollama_chat.services.ollama import OllamaClient

router = APIRouter(prefix="/api")


def _client(request: Request) -> OllamaClient:
    """Recupera el cliente Ollama compartido (un HTTP pool para todo el proceso)."""
    return request.app.state.ollama


@router.get("/health", response_model=HealthResponse)
async def health() -> HealthResponse:
    """Comprueba que el servidor web está arriba. No pega a Ollama.

    Útil para Docker/monitoreo: si esto falla, el proceso de Cypher Chat
    está caído; si `/models` falla y esto no, el problema es Ollama.
    """
    settings = get_settings()
    return HealthResponse(
        status="ok",
        ollama=settings.ollama_base,
        default_model=settings.ollama_model,
    )


@router.put("/settings", response_model=HealthResponse)
async def update_settings(body: SettingsUpdate) -> HealthResponse:
    """Cambia la URL de Ollama en caliente (localhost, LAN o ngrok).

    La otra PC pega el link y no hace falta reiniciar ni editar `.env`.
    No instala modelos: solo apunta a *tu* servidor donde ya corre Ollama.
    """
    settings = get_settings()
    try:
        settings.ollama_url = normalize_ollama_url(body.ollama_url)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return HealthResponse(
        status="ok",
        ollama=settings.ollama_base,
        default_model=settings.ollama_model,
    )


@router.get("/models", response_model=ModelsResponse)
async def models(request: Request) -> ModelsResponse:
    """Lista los modelos instalados (`ollama list` vía HTTP /api/tags).

    La UI llama esto al cargar la página para rellenar el `<select>`.
    Si Ollama no responde se propaga un 502.
    """
    settings = get_settings()
    names = await _client(request).list_models()
    return ModelsResponse(
        ollama=settings.ollama_base,
        models=names,
        default=settings.ollama_model,
    )


@router.post("/chat")
async def chat(body: ChatRequest, request: Request) -> StreamingResponse:
    """Reenvía el historial a Ollama y devuelve tokens en NDJSON.

    Cada línea del stream es un JSON de Ollama, p.ej.:
        {"message": {"role": "assistant", "content": "Ho"}, "done": false}

    El JS del frontend lee esas líneas y va pintando el texto.
    """
    settings = get_settings()
    model = body.model or settings.ollama_model
    return StreamingResponse(
        _client(request).stream_chat(model, body.messages),
        media_type="application/x-ndjson",
    )
