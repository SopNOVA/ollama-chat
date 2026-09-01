"""Fixtures compartidas: una app FastAPI y un TestClient por test.

`TestClient` dispara el `lifespan` (abre el httpx real). Los tests de API
que no deben pegar a Ollama reemplazan `app.state.ollama` después.
"""

from collections.abc import Iterator

import pytest
from fastapi.testclient import TestClient

from ollama_chat.app import create_app


@pytest.fixture
def app():
    """Instancia nueva de FastAPI (no reutiliza el `app` global del módulo)."""
    return create_app()


@pytest.fixture
def client(app) -> Iterator[TestClient]:
    """Cliente HTTP in-process: llama a la app sin abrir un puerto."""
    with TestClient(app) as test_client:
        yield test_client
