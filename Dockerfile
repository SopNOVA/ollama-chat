# Imagen de producción: instala el paquete y arranca uvicorn vía python -m.
# Ollama NO va dentro de este contenedor; se apunta con OLLAMA_URL.
FROM python:3.12-slim

WORKDIR /app

COPY pyproject.toml README.md ./
COPY src ./src

RUN pip install --no-cache-dir .

EXPOSE 7860

CMD ["python", "-m", "ollama_chat"]
