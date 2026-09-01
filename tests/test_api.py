"""Tests de los endpoints FastAPI (capa HTTP, sin Ollama real).

Sustituyen `app.state.ollama` por fakes que imitan list_models/stream_chat.
Así se verifica el contrato JSON/NDJSON que consume el frontend.
"""

from __future__ import annotations

import json
from collections.abc import AsyncIterator

from fastapi import HTTPException

from ollama_chat.schemas import ChatMessage


class FakeOllama:
    """Ollama de mentira: siempre hay 2 modelos y el chat responde "hola"."""

    async def list_models(self) -> list[str]:
        return ["qwen3:4b", "llama3.2"]

    async def stream_chat(
        self,
        model: str,
        messages: list[ChatMessage],
    ) -> AsyncIterator[str]:
        assert model
        assert messages
        yield json.dumps({"message": {"role": "assistant", "content": "hola"}}) + "\n"
        yield json.dumps({"done": True}) + "\n"


class DownOllama:
    """Simula que el daemon Ollama no está escuchando."""

    async def list_models(self) -> list[str]:
        raise HTTPException(status_code=502, detail="Ollama no responde")


def test_health(client):
    """El healthcheck no depende de Ollama y debe devolver 200 + status ok."""
    response = client.get("/api/health")
    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "ok"
    assert "ollama" in body
    assert "default_model" in body


def test_index_serves_ui(client):
    """GET / entrega el HTML de la UI y apunta al JS estático."""
    response = client.get("/")
    assert response.status_code == 200
    assert "Cypher Chat" in response.text
    assert "/static/js/chat.js" in response.text


def test_static_assets(client):
    """CSS y JS se sirven como archivos independientes (ya no van embebidos)."""
    css = client.get("/static/css/styles.css")
    js = client.get("/static/js/chat.js")
    assert css.status_code == 200
    assert "Cypher Chat" not in css.text
    assert "--accent" in css.text
    assert js.status_code == 200
    assert "api/chat" in js.text


def test_models_ok(app, client):
    """GET /api/models serializa la lista que devuelve el cliente Ollama."""
    app.state.ollama = FakeOllama()
    response = client.get("/api/models")
    assert response.status_code == 200
    body = response.json()
    assert body["models"] == ["qwen3:4b", "llama3.2"]
    assert body["default"] == "qwen3:4b"


def test_models_ollama_down(app, client):
    """Si Ollama falla, el endpoint responde 502 (bad gateway)."""
    app.state.ollama = DownOllama()
    response = client.get("/api/models")
    assert response.status_code == 502


def test_chat_streams_ndjson(app, client):
    """POST /api/chat reenvía líneas NDJSON y el content-type es ndjson."""
    app.state.ollama = FakeOllama()
    response = client.post(
        "/api/chat",
        json={
            "model": "qwen3:4b",
            "messages": [{"role": "user", "content": "hola"}],
            "stream": True,
        },
    )
    assert response.status_code == 200
    assert "ndjson" in response.headers["content-type"]
    lines = [json.loads(line) for line in response.text.splitlines() if line]
    assert lines[0]["message"]["content"] == "hola"
    assert lines[-1]["done"] is True
