"""Punto de entrada de consola.

    python -m ollama_chat
    python -m ollama_chat --ollama-url https://xxxx.ngrok-free.app
    ollama-chat

`--ollama-url` es el link de tu servidor (local, LAN o ngrok). La otra PC
no corre Ollama: solo este proceso web y el navegador.
"""

from __future__ import annotations

import argparse
import os

import uvicorn

from ollama_chat.config import get_settings


def main() -> None:
    """Arranca uvicorn. HOST/PORT/OLLAMA_URL: flags, env o Settings."""
    parser = argparse.ArgumentParser(
        description="Cypher Chat — UI local para un Ollama local o remoto (ngrok).",
    )
    parser.add_argument(
        "--ollama-url",
        help="URL de Ollama: http://127.0.0.1:11434 o el https de ngrok",
    )
    parser.add_argument("--host", help="Dirección de escucha (default 0.0.0.0)")
    parser.add_argument("--port", type=int, help="Puerto HTTP del chat (default 7860)")
    args = parser.parse_args()

    if args.ollama_url:
        os.environ["OLLAMA_URL"] = args.ollama_url
    if args.host:
        os.environ["HOST"] = args.host
    if args.port is not None:
        os.environ["PORT"] = str(args.port)

    get_settings.cache_clear()
    settings = get_settings()
    uvicorn.run(
        "ollama_chat.app:app",
        host=os.environ.get("HOST", settings.host),
        port=int(os.environ.get("PORT", settings.port)),
        reload=os.environ.get("RELOAD", "").lower() in {"1", "true", "yes"},
    )


if __name__ == "__main__":
    main()
