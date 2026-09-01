"""Cliente HTTP hacia el daemon Ollama.

Ollama expone una API local (por defecto :11434). Este módulo traduce
nuestras llamadas a esa API y no sabe nada de HTML:

- GET  {OLLAMA_URL}/api/tags   → modelos descargados
- POST {OLLAMA_URL}/api/chat   → chat con stream NDJSON
"""

from __future__ import annotations

import json
from collections.abc import AsyncIterator

import httpx
from fastapi import HTTPException

from ollama_chat.config import Settings
from ollama_chat.schemas import ChatMessage


class OllamaClient:
    """Wrapper fino sobre httpx.AsyncClient + Settings.

    Recibe el cliente HTTP ya creado en `lifespan` para reutilizar
    conexiones TCP en lugar de abrir una por cada request.
    """

    def __init__(self, http: httpx.AsyncClient, settings: Settings) -> None:
        self._http = http
        self._settings = settings

    @property
    def base_url(self) -> str:
        """Base sin `/` final, p.ej. `http://127.0.0.1:11434`."""
        return self._settings.ollama_base

    async def list_models(self) -> list[str]:
        """Devuelve los nombres (`llama3.2:latest`, etc.) o lanza 502.

        El 502 (Bad Gateway) indica: Cypher Chat está bien, el backend
        Ollama no contestó o contestó mal.
        """
        try:
            response = await self._http.get(f"{self.base_url}/api/tags", timeout=10.0)
            response.raise_for_status()
        except httpx.HTTPError as exc:
            raise HTTPException(
                status_code=502,
                detail=f"Ollama no responde en {self.base_url}: {exc}",
            ) from exc

        payload = response.json()
        return [m["name"] for m in payload.get("models", []) if m.get("name")]

    async def stream_chat(
        self,
        model: str,
        messages: list[ChatMessage],
    ) -> AsyncIterator[str]:
        """Generador asíncrono: cada `yield` es una línea NDJSON.

        FastAPI lo envuelve en `StreamingResponse`, así el navegador
        recibe tokens en cuanto Ollama los produce, sin esperar el
        mensaje completo.

        Si Ollama devuelve 4xx/5xx o hay error de red, en lugar de
        romper el stream se emite `{"error": "..."}` para que el JS
        lo muestre en la burbuja de error.
        """
        payload = {
            "model": model or self._settings.ollama_model,
            "messages": [m.model_dump() for m in messages],
            "stream": True,
        }
        try:
            async with self._http.stream(
                "POST",
                f"{self.base_url}/api/chat",
                json=payload,
                timeout=None,  # la generación puede durar mucho
            ) as response:
                if response.status_code >= 400:
                    err = await response.aread()
                    yield _ndjson_error(err.decode(errors="replace"))
                    return
                async for line in response.aiter_lines():
                    if line:
                        yield line + "\n"
        except httpx.HTTPError as exc:
            yield _ndjson_error(str(exc))


def _ndjson_error(message: str) -> str:
    """Una línea JSON con el campo `error`, mismo formato que espera el frontend."""
    return json.dumps({"error": message}) + "\n"
