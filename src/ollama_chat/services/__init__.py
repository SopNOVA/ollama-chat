"""Capa de servicios: lógica que no es HTTP de FastAPI.

Hoy solo hay `OllamaClient`. Si más adelante se añade historial en disco
o otro proveedor, iría en este paquete.
"""

from ollama_chat.services.ollama import OllamaClient

__all__ = ["OllamaClient"]
