"""Configuración de la aplicación leída del entorno.

`OLLAMA_URL` puede ser localhost, una IP de LAN, o un túnel ngrok
cuando el chat corre en otra PC y Ollama está en tu servidor:

    OLLAMA_URL=http://127.0.0.1:11434
    OLLAMA_URL=https://xxxx.ngrok-free.app
    OLLAMA_MODEL=qwen3:4b
    HOST=0.0.0.0
    PORT=7860
    GOOGLE_API_KEY=   # opcional, Custom Search JSON API
    GOOGLE_CSE_ID=    # id del buscador programable (toda la web)
    DB_ENGINE=sqlite  # sqlite | postgres | mssql
    DB_PATH=/home/cypherhn/ontmonitor/hyperion_onms.db
"""

from functools import lru_cache
from urllib.parse import urlparse

from pydantic_settings import BaseSettings, SettingsConfigDict

# Sufijos que la gente pega al copiar el link (ngrok / docs de Ollama).
_STRIP_SUFFIXES = ("/api/tags", "/api/chat", "/api")


class Settings(BaseSettings):
    """Ajustes de arranque. Los nombres de campo coinciden con las env vars.

    Ejemplo: `ollama_url` se llena desde `OLLAMA_URL`.
    """

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",  # no fallar si hay variables extra en el entorno
    )

    # URL base del daemon Ollama (puerto 11434 por defecto).
    ollama_url: str = "http://127.0.0.1:11434"
    # Modelo que aparece seleccionado en la UI si existe en Ollama.
    ollama_model: str = "qwen3:4b"
    # Dirección de escucha del servidor web. 0.0.0.0 = todas las interfaces.
    host: str = "0.0.0.0"
    # Puerto HTTP de Cypher Chat (no confundir con el 11434 de Ollama).
    port: int = 7860
    # Custom Search JSON API (opcional). Sin esto se usa metasearch con Google.
    google_api_key: str = ""
    google_cse_id: str = ""
    # Cuántos enlaces pedir (Google CSE admite como máximo 10).
    search_max_results: int = 8
    # Región ddgs, p.ej. es-es o us-en.
    search_region: str = "es-es"
    # sqlite (Hyperion local), postgres o mssql.
    db_engine: str = "sqlite"
    db_path: str = ""
    db_host: str = ""
    db_port: int = 5432
    db_name: str = ""
    db_user: str = ""
    db_password: str = ""
    db_max_rows: int = 50
    db_timeout: int = 30

    @property
    def db_configured(self) -> bool:
        if self.db_engine.lower() == "sqlite":
            return bool(self.db_path.strip())
        return bool(self.db_host and self.db_name and self.db_user and self.db_password)

    @property
    def ollama_base(self) -> str:
        """URL de Ollama sin barra final, lista para concatenar `/api/...`."""
        return self.ollama_url.rstrip("/")


def normalize_ollama_url(url: str) -> str:
    """Limpia un link pegado (local o ngrok) y valida que sea http(s).

    Acepta `https://abc.ngrok-free.app` o con `/api` al final.
    Rechaza esquemas raros (`file:`, `javascript:`) para no abrir SSRF obvio.
    """
    cleaned = (url or "").strip().rstrip("/")
    for suffix in _STRIP_SUFFIXES:
        if cleaned.endswith(suffix):
            cleaned = cleaned[: -len(suffix)].rstrip("/")
            break
    parsed = urlparse(cleaned)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        raise ValueError(
            "URL inválida. Usa http://127.0.0.1:11434 o el link https de ngrok."
        )
    return cleaned


@lru_cache
def get_settings() -> Settings:
    """Devuelve un único objeto Settings para todo el proceso.

    `lru_cache` evita releer `.env` en cada request. Si en tests se cambian
    variables de entorno, hay que llamar a `get_settings.cache_clear()`.
    """
    return Settings()
