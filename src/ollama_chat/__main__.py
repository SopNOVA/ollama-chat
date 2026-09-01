"""Punto de entrada de consola.

Permite arrancar el servidor con cualquiera de:

    python -m ollama_chat
    ollama-chat

uvicorn carga `ollama_chat.app:app` (el objeto FastAPI creado al importar).
HOST/PORT/RELOAD se pueden pasar por entorno; si no, se usan Settings.
"""

from __future__ import annotations

import os

import uvicorn

from ollama_chat.config import get_settings


def main() -> None:
    """Arranca uvicorn escuchando en host:port.

    `reload=True` (RELOAD=true) recarga el código al guardar archivos.
    Útil en desarrollo; no usarlo en producción.
    """
    settings = get_settings()
    uvicorn.run(
        "ollama_chat.app:app",
        host=os.environ.get("HOST", settings.host),
        port=int(os.environ.get("PORT", settings.port)),
        reload=os.environ.get("RELOAD", "").lower() in {"1", "true", "yes"},
    )


if __name__ == "__main__":
    main()
