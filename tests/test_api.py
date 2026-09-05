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
    assert body["database"] in {"off", "ready"}


def test_index_serves_ui(client):
    """GET / entrega el HTML de la UI y apunta al JS estático."""
    response = client.get("/")
    assert response.status_code == 200
    assert "Cypher Chat" in response.text
    assert "/static/js/chat.js" in response.text
    assert "/static/js/chart.umd.min.js" in response.text


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


def test_update_ollama_url(client):
    """PUT /api/settings acepta un link ngrok y lo deja limpio (sin /api)."""
    response = client.put(
        "/api/settings",
        json={"ollama_url": "https://demo.ngrok-free.app/api"},
    )
    assert response.status_code == 200
    assert response.json()["ollama"] == "https://demo.ngrok-free.app"
    health = client.get("/api/health")
    assert health.json()["ollama"] == "https://demo.ngrok-free.app"


def test_update_ollama_url_rejects_garbage(client):
    response = client.put("/api/settings", json={"ollama_url": "javascript:alert(1)"})
    assert response.status_code == 400


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


def test_index_has_google_toggle(client):
    response = client.get("/")
    assert 'id="websearch"' in response.text
    assert 'id="usedb"' in response.text


class CaptureOllama:
    """Guarda los messages que el router manda a Ollama tras inyectar la búsqueda."""

    def __init__(self) -> None:
        self.seen: list[ChatMessage] | None = None

    async def stream_chat(self, model: str, messages: list[ChatMessage]):
        self.seen = messages
        yield json.dumps({"message": {"role": "assistant", "content": "aquí van"}}) + "\n"
        yield json.dumps({"done": True}) + "\n"


def test_chat_search_injects_links(app, client, monkeypatch):
    """«busca …» emite tarjetas y un system prompt con las URLs reales."""
    from ollama_chat.services.search import SearchHit, SearchOutcome

    async def fake_search(query, settings, http=None):
        assert query == "tutorial fastapi"
        return SearchOutcome(
            query=query,
            provider="google",
            hits=[SearchHit("Congreso", "https://www.laprensa.hn/sucesos/congreso", "nota")],
        )

    monkeypatch.setattr("ollama_chat.routers.api.search_web", fake_search)
    capture = CaptureOllama()
    app.state.ollama = capture
    response = client.post(
        "/api/chat",
        json={
            "model": "qwen3:4b",
            "messages": [{"role": "user", "content": "busca tutorial fastapi"}],
            "stream": True,
        },
    )
    assert response.status_code == 200
    events = [json.loads(line) for line in response.text.splitlines() if line]
    assert events[0] == {"status": "searching", "query": "tutorial fastapi"}
    assert events[1]["search"]["results"][0]["url"] == "https://www.laprensa.hn/sucesos/congreso"
    assert capture.seen is not None
    assert capture.seen[0].role == "system"
    assert "https://www.laprensa.hn/sucesos/congreso" in capture.seen[0].content


def test_chat_web_search_flag_forces_query(app, client, monkeypatch):
    """El toggle Google trata el mensaje entero como consulta."""
    from ollama_chat.services.search import SearchHit, SearchOutcome

    async def fake_search(query, settings, http=None):
        assert query == "qwen3 ollama"
        return SearchOutcome(
            query=query,
            provider="google",
            hits=[SearchHit("Qwen", "https://www.elheraldo.hn/tecnologia/qwen", "")],
        )

    monkeypatch.setattr("ollama_chat.routers.api.search_web", fake_search)
    app.state.ollama = CaptureOllama()
    response = client.post(
        "/api/chat",
        json={
            "model": "qwen3:4b",
            "messages": [{"role": "user", "content": "qwen3 ollama"}],
            "web_search": True,
            "stream": True,
        },
    )
    events = [json.loads(line) for line in response.text.splitlines() if line]
    assert events[0]["status"] == "searching"
    assert events[1]["search"]["query"] == "qwen3 ollama"


def test_chat_db_readonly_table(app, client, monkeypatch):
    """«consulta la base» emite filas SQL y no pasa por UPDATE."""
    from ollama_chat.services.database import QueryResult

    class DbOllama:
        async def chat_complete(self, model, messages):
            return '{"type":"select","sql":"SELECT 1 AS n"}'

        async def stream_chat(self, model, messages):
            yield json.dumps({"message": {"role": "assistant", "content": "hay 1 fila"}}) + "\n"
            yield json.dumps({"done": True}) + "\n"

    monkeypatch.setattr(
        "ollama_chat.routers.api.load_catalog",
        lambda settings: "Tablas: dbo.Personas\nProcedimientos: dbo.sp_Listar",
    )
    monkeypatch.setattr(
        "ollama_chat.routers.api.execute_plan",
        lambda settings, plan: QueryResult(
            sql="SELECT TOP 50 1 AS n", columns=["n"], rows=[[1]]
        ),
    )
    app.state.ollama = DbOllama()
    response = client.post(
        "/api/chat",
        json={
            "model": "qwen3:4b",
            "messages": [{"role": "user", "content": "consulta la base cuántos hay"}],
            "use_db": True,
            "stream": True,
        },
    )
    events = [json.loads(line) for line in response.text.splitlines() if line]
    assert events[0]["status"] == "querying"
    assert events[1]["sql"]["columns"] == ["n"]
    assert events[1]["sql"]["rows"] == [[1]]


def test_chat_search_error(app, client, monkeypatch):
    from ollama_chat.services.search import SearchError

    async def boom(query, settings, http=None):
        raise SearchError("No encontré enlaces")

    monkeypatch.setattr("ollama_chat.routers.api.search_web", boom)
    app.state.ollama = FakeOllama()
    response = client.post(
        "/api/chat",
        json={
            "model": "qwen3:4b",
            "messages": [{"role": "user", "content": "busca algo inventado xyzzy"}],
        },
    )
    events = [json.loads(line) for line in response.text.splitlines() if line]
    assert events[0]["status"] == "searching"
    assert "No encontré enlaces" in events[1]["error"]
