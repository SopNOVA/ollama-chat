# Cypher Chat

Local streaming chat UI for [Ollama](https://ollama.com). Runs on your machine, talks to models on the private network, and does not send prompts to a cloud API.

## Requirements

- Python 3.11+
- A running Ollama instance (`ollama serve`)
- At least one pulled model, e.g. `ollama pull qwen3:4b`

## Setup

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -e ".[dev]"
cp .env.example .env
```

## Run

```bash
python -m ollama_chat
```

Or:

```bash
ollama-chat
```

Open [http://127.0.0.1:7860](http://127.0.0.1:7860).

| Variable | Default | Meaning |
| --- | --- | --- |
| `OLLAMA_URL` | `http://127.0.0.1:11434` | Ollama API base URL |
| `OLLAMA_MODEL` | `qwen3:4b` | Default model in the UI |
| `HOST` | `0.0.0.0` | Bind address |
| `PORT` | `7860` | HTTP port |
| `RELOAD` | unset | Set to `true` for auto-reload while developing |

## Tests

```bash
pytest
```

## Docker

```bash
docker compose up --build
```

The compose file reaches Ollama on the host at `host.docker.internal:11434`.

## Layout

```
src/ollama_chat/          application package
  app.py                  FastAPI factory and lifespan
  config.py               environment settings
  schemas.py              request/response models
  routers/api.py          HTTP endpoints
  services/ollama.py      Ollama HTTP client
  static/                 HTML, CSS, JS
tests/                    pytest suite
```
