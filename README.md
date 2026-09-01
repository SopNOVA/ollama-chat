# Cypher Chat

UI local de chat en streaming para [Ollama](https://ollama.com). Corre en tu máquina, habla con modelos en la red privada y **no envía prompts a una API en la nube**.

Clonar el repo **no alcanza**. Docker **no hace falta**. Hay que instalar Python, las dependencias del chat, Ollama y al menos un modelo.

## Qué hay que instalar

| Cosa | ¿Hace falta? |
| --- | --- |
| Clonar el repo | Sí |
| Python 3.11+ y `pip install` | Sí |
| [Ollama](https://ollama.com) | Sí (el chat es solo la UI; el modelo lo ejecuta Ollama) |
| Un modelo (`qwen3:4b` u otro) | Sí, al menos uno |
| Docker | No |

`qwen3:4b` es el modelo por defecto de la UI. Puedes usar cualquier tag que tengas (`llama3.2`, `mistral`, etc.).

## WSL / Linux (recomendado)

Todo en la misma distro WSL: Python, el chat y Ollama.

### 1. Python

```bash
sudo apt update
sudo apt install -y python3 python3-venv python3-pip git
```

### 2. Ollama y un modelo

```bash
curl -fsSL https://ollama.com/install.sh | sh
ollama serve          # si no quedó corriendo como servicio
ollama pull qwen3:4b  # u otro modelo
```

Comprueba que responde:

```bash
curl http://127.0.0.1:11434/api/tags
```

### 3. Clonar e instalar el chat

```bash
git clone https://github.com/SopNOVA/ollama-chat.git
cd ollama-chat
python3 -m venv .venv
source .venv/bin/activate
pip install -e .
cp .env.example .env   # opcional; los valores por defecto ya apuntan a Ollama local
```

### 4. Arrancar

```bash
python -m ollama_chat
```

O:

```bash
ollama-chat
```

Abre [http://127.0.0.1:7860](http://127.0.0.1:7860).

## Variables de entorno

| Variable | Default | Significado |
| --- | --- | --- |
| `OLLAMA_URL` | `http://127.0.0.1:11434` | URL de la API de Ollama |
| `OLLAMA_MODEL` | `qwen3:4b` | Modelo seleccionado por defecto en la UI |
| `HOST` | `0.0.0.0` | Dirección de escucha |
| `PORT` | `7860` | Puerto HTTP del chat |
| `RELOAD` | (vacío) | `true` recarga el código al guardar (desarrollo) |

Ejemplo si Ollama está en **Windows** y el chat corre en **WSL**:

```bash
export OLLAMA_URL=http://$(hostname).local:11434
# o la IP de Windows, p.ej. http://172.x.x.x:11434
python -m ollama_chat
```

Si Ollama y el chat están **los dos en WSL**, deja el default `http://127.0.0.1:11434`.

## Tests

```bash
source .venv/bin/activate
pip install -e ".[dev]"
pytest
```

## Docker (opcional)

Ollama sigue teniendo que existir **fuera** del contenedor (en el host).

```bash
docker compose up --build
```

Compose apunta a Ollama en `host.docker.internal:11434`.

## Layout

```
src/ollama_chat/          paquete de la aplicación
  app.py                  fábrica FastAPI y lifespan
  config.py               variables de entorno
  schemas.py              modelos request/response
  routers/api.py          endpoints HTTP
  services/ollama.py      cliente HTTP de Ollama
  static/                 HTML, CSS, JS
tests/                    suite pytest
```
