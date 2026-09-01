"""Configuración de la aplicación leída del entorno.

Valores por defecto cubren un Ollama local típico. Se pueden sobreescribir
con variables de entorno o con un archivo `.env` en la raíz del proyecto:

    OLLAMA_URL=http://127.0.0.1:11434
    OLLAMA_MODEL=qwen3:4b
    HOST=0.0.0.0
    PORT=7860
"""

from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


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

    @property
    def ollama_base(self) -> str:
        """URL de Ollama sin barra final, lista para concatenar `/api/...`."""
        return self.ollama_url.rstrip("/")


@lru_cache
def get_settings() -> Settings:
    """Devuelve un único objeto Settings para todo el proceso.

    `lru_cache` evita releer `.env` en cada request. Si en tests se cambian
    variables de entorno, hay que llamar a `get_settings.cache_clear()`.
    """
    return Settings()
