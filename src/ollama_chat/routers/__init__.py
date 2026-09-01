"""Paquete de rutas HTTP.

Reexporta el `router` de `api.py` para que `app.py` pueda hacer:

    from ollama_chat.routers import router
"""

from ollama_chat.routers.api import router

__all__ = ["router"]
