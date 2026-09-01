"""Tests de limpieza/validación de la URL de Ollama (local o ngrok)."""

import pytest

from ollama_chat.config import normalize_ollama_url


def test_normalize_strips_slash_and_api_path():
    assert normalize_ollama_url("https://abc.ngrok-free.app/") == "https://abc.ngrok-free.app"
    assert (
        normalize_ollama_url("https://abc.ngrok-free.app/api/tags")
        == "https://abc.ngrok-free.app"
    )


def test_normalize_keeps_local():
    assert normalize_ollama_url(" http://127.0.0.1:11434 ") == "http://127.0.0.1:11434"


def test_normalize_rejects_bad_scheme():
    with pytest.raises(ValueError):
        normalize_ollama_url("not-a-url")
