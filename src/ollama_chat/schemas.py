"""Contratos JSON de la API.

FastAPI usa estos modelos para:
- validar el body que manda el navegador
- documentar `/docs` (OpenAPI)
- serializar las respuestas
"""

from pydantic import BaseModel, Field

from ollama_chat.config import get_settings


class ChatMessage(BaseModel):
    """Un turno del historial, compatible con la API de Ollama.

    `role` suele ser `user` o `assistant`. `content` es el texto del mensaje.
    """

    role: str
    content: str


class ChatRequest(BaseModel):
    """Body de POST /api/chat.

    El frontend envía el modelo elegido, todo el historial y `stream=true`.
    Aunque `stream` exista por compatibilidad, el servidor siempre reenvía
    el flujo NDJSON de Ollama (token a token).
    """

    model: str = Field(default_factory=lambda: get_settings().ollama_model)
    messages: list[ChatMessage] = Field(default_factory=list)
    stream: bool = True


class ModelsResponse(BaseModel):
    """Respuesta de GET /api/models: lista de tags que Ollama tiene descargados."""

    ollama: str
    models: list[str]
    default: str


class HealthResponse(BaseModel):
    """Respuesta de GET /api/health. No consulta a Ollama; solo confirma
    que este proceso web está vivo y con qué URL/modelo está configurado.
    """

    status: str
    ollama: str
    default_model: str


class SettingsUpdate(BaseModel):
    """Body de PUT /api/settings: pegar aquí el link local o de ngrok."""

    ollama_url: str
