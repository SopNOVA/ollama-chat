"""Paquete principal de Cypher Chat.

Este directorio (`src/ollama_chat`) es la aplicación instalable. Al importar
`ollama_chat` solo se expone la versión; el resto se carga bajo demanda:

- `config`     → lee variables de entorno (.env)
- `schemas`    → modelos Pydantic de entrada/salida HTTP
- `services`   → cliente que habla con Ollama
- `routers`    → endpoints REST (`/api/...`)
- `app`        → fábrica FastAPI que une todo y sirve la UI
- `static`     → HTML/CSS/JS del chat en el navegador
"""

__version__ = "1.0.0"
