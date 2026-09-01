"""Tests unitarios de OllamaClient (sin FastAPI ni Ollama real).

httpx.MockTransport intercepta las peticiones y devuelve respuestas
fabricadas, para cubrir éxito, conexión rechazada y HTTP 500 en el stream.
"""

from __future__ import annotations

import json

import httpx
import pytest
from fastapi import HTTPException

from ollama_chat.config import Settings
from ollama_chat.schemas import ChatMessage
from ollama_chat.services.ollama import OllamaClient


def _settings() -> Settings:
    """Settings fijos para no depender de .env ni del Ollama de la máquina."""
    return Settings(ollama_url="http://ollama.test", ollama_model="qwen3:4b")


@pytest.mark.asyncio
async def test_list_models():
    """Parsea /api/tags y descarta entradas sin nombre."""

    def handler(request: httpx.Request) -> httpx.Response:
        assert str(request.url) == "http://ollama.test/api/tags"
        return httpx.Response(200, json={"models": [{"name": "qwen3:4b"}, {"name": ""}]})

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
        client = OllamaClient(http, _settings())
        assert await client.list_models() == ["qwen3:4b"]


@pytest.mark.asyncio
async def test_list_models_down():
    """Un ConnectError se convierte en HTTP 502 para el router."""

    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("refused", request=request)

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
        client = OllamaClient(http, _settings())
        with pytest.raises(HTTPException) as exc:
            await client.list_models()
        assert exc.value.status_code == 502


@pytest.mark.asyncio
async def test_stream_chat_chunks():
    """Reenvía cada línea del stream de Ollama tal cual (NDJSON)."""
    chunks = [
        json.dumps({"message": {"content": "ho"}}),
        json.dumps({"message": {"content": "la"}, "done": True}),
    ]

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/api/chat"
        body = json.loads(request.content)
        assert body["model"] == "qwen3:4b"
        assert body["stream"] is True
        return httpx.Response(200, text="\n".join(chunks) + "\n")

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
        client = OllamaClient(http, _settings())
        lines = [
            line
            async for line in client.stream_chat(
                "qwen3:4b",
                [ChatMessage(role="user", content="hola")],
            )
        ]
    assert len(lines) == 2
    assert json.loads(lines[0])["message"]["content"] == "ho"


@pytest.mark.asyncio
async def test_stream_chat_http_error():
    """Un 500 de Ollama no rompe el generador: emite {"error": "..."}."""

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(500, text="boom")

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
        client = OllamaClient(http, _settings())
        lines = [line async for line in client.stream_chat("qwen3:4b", [])]
    assert json.loads(lines[0])["error"] == "boom"
