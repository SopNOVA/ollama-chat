"""Endpoints HTTP de Cypher Chat (prefijo `/api`).

El navegador no habla con Ollama directo: pasa por estos handlers, que
delegan en `OllamaClient` guardado en `app.state` durante el lifespan.
"""

from __future__ import annotations

import json
from collections.abc import AsyncIterator

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import StreamingResponse

from ollama_chat.config import get_settings, normalize_ollama_url
from ollama_chat.schemas import (
    ChatMessage,
    ChatRequest,
    HealthResponse,
    ModelsResponse,
    SettingsUpdate,
)
from ollama_chat.services.ollama import OllamaClient
from ollama_chat.services.charts import chart_from_result
from ollama_chat.services.database import (
    QueryResult,
    SqlError,
    execute_plan,
    extract_db_question,
    format_result_for_model,
    load_catalog,
    parse_plan,
    plan_system_prompt,
)
from ollama_chat.services.reports import (
    default_report,
    interpret_report,
    is_schema_error,
    rewrite_sql,
)
from ollama_chat.services.search import (
    SearchError,
    extract_search_query,
    format_search_context,
    search_web,
)

router = APIRouter(prefix="/api")


def _client(request: Request) -> OllamaClient:
    """Recupera el cliente Ollama compartido (un HTTP pool para todo el proceso)."""
    return request.app.state.ollama


@router.get("/health", response_model=HealthResponse)
async def health() -> HealthResponse:
    """Comprueba que el servidor web está arriba. No pega a Ollama.

    Útil para Docker/monitoreo: si esto falla, el proceso de Cypher Chat
    está caído; si `/models` falla y esto no, el problema es Ollama.
    """
    settings = get_settings()
    return HealthResponse(
        status="ok",
        ollama=settings.ollama_base,
        default_model=settings.ollama_model,
        database="ready" if settings.db_configured else "off",
    )


@router.put("/settings", response_model=HealthResponse)
async def update_settings(body: SettingsUpdate) -> HealthResponse:
    """Cambia la URL de Ollama en caliente (localhost, LAN o ngrok).

    La otra PC pega el link y no hace falta reiniciar ni editar `.env`.
    No instala modelos: solo apunta a *tu* servidor donde ya corre Ollama.
    """
    settings = get_settings()
    try:
        settings.ollama_url = normalize_ollama_url(body.ollama_url)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return HealthResponse(
        status="ok",
        ollama=settings.ollama_base,
        default_model=settings.ollama_model,
        database="ready" if settings.db_configured else "off",
    )


@router.get("/models", response_model=ModelsResponse)
async def models(request: Request) -> ModelsResponse:
    """Lista los modelos instalados (`ollama list` vía HTTP /api/tags).

    La UI llama esto al cargar la página para rellenar el `<select>`.
    Si Ollama no responde se propaga un 502.
    """
    settings = get_settings()
    names = await _client(request).list_models()
    return ModelsResponse(
        ollama=settings.ollama_base,
        models=names,
        default=settings.ollama_model,
    )


@router.post("/chat")
async def chat(body: ChatRequest, request: Request) -> StreamingResponse:
    """Reenvía el historial a Ollama y devuelve tokens en NDJSON.

    Si el usuario pidió buscar (toggle Google, "/search …" o "busca …"),
    primero se consultan enlaces reales y se emiten eventos:
        {"status": "searching", "query": "..."}
        {"search": {"query": "...", "provider": "google", "results": [...]}}
    Luego el stream de Ollama, p.ej.:
        {"message": {"role": "assistant", "content": "Ho"}, "done": false}

    El JS del frontend lee esas líneas, pinta las tarjetas y el texto.
    """
    settings = get_settings()
    model = body.model or settings.ollama_model
    return StreamingResponse(
        _stream_chat(
            request, model, list(body.messages), body.web_search, body.use_db
        ),
        media_type="application/x-ndjson",
    )


async def _stream_chat(
    request: Request,
    model: str,
    messages: list[ChatMessage],
    web_search: bool,
    use_db: bool,
) -> AsyncIterator[str]:
    """Busca enlaces o consulta la base (solo lectura) y reenvía Ollama."""
    settings = get_settings()
    last = messages[-1].content if messages and messages[-1].role == "user" else ""
    query = extract_search_query(last, forced=web_search) if last else None
    db_question = None if query else extract_db_question(last, forced=use_db)

    if query:
        yield json.dumps({"status": "searching", "query": query}, ensure_ascii=False) + "\n"
        try:
            outcome = await search_web(query, settings, http=request.app.state.http)
        except SearchError as exc:
            yield json.dumps({"error": str(exc)}, ensure_ascii=False) + "\n"
            return
        yield json.dumps(
            {
                "search": {
                    "query": outcome.query,
                    "provider": outcome.provider,
                    "results": [hit.as_dict() for hit in outcome.hits],
                }
            },
            ensure_ascii=False,
        ) + "\n"
        messages = [
            ChatMessage(role="system", content=format_search_context(outcome.query, outcome.hits)),
            *messages,
        ]

    if db_question:
        yield json.dumps({"status": "querying", "query": db_question}, ensure_ascii=False) + "\n"
        try:
            result = await _run_db_question(_client(request), model, db_question, settings)
        except SqlError as exc:
            yield json.dumps({"error": str(exc)}, ensure_ascii=False) + "\n"
            return
        yield json.dumps({"sql": result.as_dict()}, ensure_ascii=False) + "\n"
        messages = [
            ChatMessage(
                role="system",
                content=(
                    "Resultados reales de la base (solo lectura). "
                    "Resume en español. Si hay gráfico, coméntalo. "
                    "No inventes filas ni columnas.\n\n"
                    + format_result_for_model(result)
                ),
            ),
            *messages,
        ]

    async for line in _client(request).stream_chat(model, messages):
        yield line


async def _run_db_question(
    client: OllamaClient, model: str, question: str, settings
) -> QueryResult:
    """Interpreta el pedido, ejecuta SQL real y reintenta si falla el esquema."""
    import asyncio

    async def _exec(plan: dict) -> QueryResult:
        if isinstance(plan.get("sql"), str):
            plan = {**plan, "sql": rewrite_sql(plan["sql"])}
        result = await asyncio.to_thread(execute_plan, settings, plan)
        result.chart = chart_from_result(
            result, question=question, plan_chart=plan.get("chart")
        )
        return result

    interpreted = interpret_report(question)
    if interpreted:
        try:
            return await _exec(interpreted)
        except SqlError:
            pass

    catalog = await asyncio.to_thread(load_catalog, settings)
    system = plan_system_prompt(catalog, settings.db_engine)
    last_error = ""
    for attempt in range(3):
        if attempt == 0:
            user = (
                "Interpreta este pedido en lenguaje natural y arma SQL con el "
                f"catálogo real.\nPedido: {question}"
            )
        else:
            user = (
                f"Ese SQL falló ({last_error}). Corrige usando SOLO tablas y "
                f"columnas del catálogo. Pedido original: {question}"
            )
        raw = await client.chat_complete(
            model,
            [
                ChatMessage(role="system", content=system),
                ChatMessage(role="user", content=user),
            ],
        )
        try:
            plan = parse_plan(raw)
            return await _exec(plan)
        except SqlError as exc:
            last_error = str(exc)
            if attempt < 2 and is_schema_error(exc):
                continue
            break

    return await _exec(default_report(question))
