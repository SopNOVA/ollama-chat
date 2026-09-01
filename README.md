# Cypher Chat

UI local de chat en streaming para [Ollama](https://ollama.com). Corre en tu máquina y **no envía prompts a una API en la nube**.

Sirve para dos PCs **sin IP pública**: Ollama vive en tu servidor; en la otra PC solo clonas este repo, pegas el link de **ngrok** y chateas.

## Idea

```
PC servidor (la tuya)              Internet               PC cliente (la otra)
Ollama :11434  <── ngrok http 11434 ──  https://xxxx.ngrok-free.app
                                              ↑
                                    Cypher Chat pega ese link
                                    (NO instala Ollama ni Qwen)
```

- En **tu servidor**: Ollama + el modelo (`qwen3:4b` u otro) + ngrok.
- En **la otra PC**: clonar este chat, Python, y el link de ngrok. Nada más.
- Docker no hace falta.

Cualquiera con el link de ngrok puede usar tu GPU. Trátalo como una contraseña y cierra ngrok cuando termines.

## PC servidor (donde está Ollama)

Aquí sí instalas Ollama y descargas el modelo. El chat no hace falta.

```bash
curl -fsSL https://ollama.com/install.sh | sh
export OLLAMA_HOST=0.0.0.0:11434
ollama serve
ollama pull qwen3:4b
```

En otra terminal, túnel (ngrok gratis cambia de URL cada vez):

```bash
ngrok http 11434
```

Copia la URL `https://xxxx.ngrok-free.app` (sin path). Esa es la que pasas a la otra PC.

Si las dos máquinas están en la **misma red**, no hace falta ngrok: usa `http://IP-DEL-SERVIDOR:11434`.

## PC cliente (la otra máquina)

**No** instales Ollama ni Qwen. Solo el chat.

```bash
sudo apt update
sudo apt install -y python3 python3-venv python3-pip git

git clone https://github.com/SopNOVA/ollama-chat.git
cd ollama-chat
python3 -m venv .venv
source .venv/bin/activate
pip install -e .
python -m ollama_chat
```

Abre [http://127.0.0.1:7860](http://127.0.0.1:7860), pega el link de ngrok en **Servidor** y pulsa **Conectar**.

También puedes pasarlo al arrancar:

```bash
python -m ollama_chat --ollama-url https://xxxx.ngrok-free.app
```

o en `.env`:

```bash
OLLAMA_URL=https://xxxx.ngrok-free.app
OLLAMA_MODEL=qwen3:4b
```

## Alternativa: solo pasar un link (sin clonar)

Si no quieres instalar nada en la otra PC, en el **servidor** corre también este chat y expón el puerto 7860:

```bash
python -m ollama_chat
ngrok http 7860
```

En la otra PC solo abres `https://yyyy.ngrok-free.app` en el navegador. El campo Servidor puede quedarse en `http://127.0.0.1:11434` porque Ollama está en la misma máquina que el chat.

## Variables de entorno

| Variable | Default | Significado |
| --- | --- | --- |
| `OLLAMA_URL` | `http://127.0.0.1:11434` | Ollama local, IP de LAN, o `https://….ngrok-free.app` |
| `OLLAMA_MODEL` | `qwen3:4b` | Modelo por defecto (debe existir **en el servidor**) |
| `HOST` | `0.0.0.0` | Dirección de escucha del chat |
| `PORT` | `7860` | Puerto HTTP del chat |
| `RELOAD` | (vacío) | `true` recarga el código al guardar |

## Tests

```bash
source .venv/bin/activate
pip install -e ".[dev]"
pytest
```

## Docker (opcional)

Ollama sigue teniendo que existir **fuera** del contenedor.

```bash
docker compose up --build
```

## Layout

```
src/ollama_chat/          paquete de la aplicación
  app.py                  fábrica FastAPI y lifespan
  config.py               variables de entorno + URL de Ollama
  schemas.py              modelos request/response
  routers/api.py          endpoints HTTP
  services/ollama.py      cliente HTTP de Ollama (incluye ngrok)
  static/                 HTML, CSS, JS
tests/                    suite pytest
```
