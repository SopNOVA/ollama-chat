"""Capa de servicios: lógica que no es HTTP de FastAPI.

Hoy: `OllamaClient`, `search` (prensa HN) y `database` (SQL Server solo lectura).
"""

from ollama_chat.services.ollama import OllamaClient

__all__ = ["OllamaClient"]
