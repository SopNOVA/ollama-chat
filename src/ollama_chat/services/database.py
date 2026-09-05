"""Consulta de solo lectura a SQLite (Hyperion ONMS), PostgreSQL o SQL Server.

Nunca se envían INSERT/UPDATE/DELETE/DROP. SQLite se abre en modo ro;
Postgres usa transacción READ ONLY. Ruta/credenciales en `.env`.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from pathlib import Path
from datetime import date, datetime, time
from decimal import Decimal

from ollama_chat.config import Settings

# Palabras que cambian datos o el servidor. Se buscan fuera de literales.
_FORBIDDEN = re.compile(
    r"\b("
    r"INSERT|UPDATE|DELETE|MERGE|DROP|ALTER|TRUNCATE|CREATE|GRANT|REVOKE|"
    r"BACKUP|RESTORE|SHUTDOWN|RECONFIGURE|OPENROWSET|OPENDATASOURCE|OPENQUERY|"
    r"WRITETEXT|UPDATETEXT|BULK|DUMP|"
    r"xp_|sp_configure|sp_password|sp_executesql|KILL|DBCC|"
    r"EXECUTE\s+AS"
    r")\b",
    re.IGNORECASE,
)
_DYNAMIC_SQL = re.compile(r"\bEXEC(?:UTE)?\s*\(", re.IGNORECASE)
_SELECT_INTO = re.compile(r"\bSELECT\b[\s\S]*?\bINTO\b", re.IGNORECASE)
_ALLOWED_START = re.compile(r"^\s*(SELECT|WITH|EXEC|EXECUTE|CALL)\b", re.IGNORECASE)
_PROC_NAME = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*(\.[A-Za-z_][A-Za-z0-9_]*)?$")

_DB_LEADING = re.compile(
    r"""^
    [¿¡]*\s*
    (?:por\s+favor,?\s+)?
    (?:
        /sql\b|
        (?:gr[aá]fica(?:r)?|reporte|dashboard)\s+
        (?:de\s+|del\s+|de\s+la\s+)?|
        (?:consulta(?:r)?|listar|mostrar|trae(?:r|me)?|dame)\s+
        (?:en\s+)?(?:la\s+)?(?:base(?:\s+de\s+datos)?|sql|tabla|sp\b|procedimiento)|
        (?:ejecuta(?:r)?|corre)\s+(?:el\s+)?(?:sp_|stored\s*procedure|procedimiento)|
        stored\s*procedure|store\s*procedure
    )
    [\s:,-]*
    """,
    re.IGNORECASE | re.VERBOSE,
)
_DB_HINT = re.compile(
    r"\b(/sql|base de datos|stored\s*procedure|store\s*procedure|"
    r"procedimiento almacenado|ejecuta(?:r)?\s+(?:el\s+)?sp_|"
    r"consulta(?:r)?\s+la\s+base|"
    r"gr[aá]fic[oa]s?|graficar|reporte|dashboard)\b",
    re.IGNORECASE,
)


class SqlError(Exception):
    """SQL rechazado (escritura) o error al hablar con SQL Server."""


@dataclass
class QueryResult:
    sql: str
    columns: list[str] = field(default_factory=list)
    rows: list[list[object]] = field(default_factory=list)
    truncated: bool = False
    chart: dict | None = None

    def as_dict(self) -> dict:
        payload = {
            "sql": self.sql,
            "columns": self.columns,
            "rows": self.rows,
            "truncated": self.truncated,
        }
        if self.chart:
            payload["chart"] = self.chart
        return payload


def extract_db_question(text: str, *, forced: bool = False) -> str | None:
    """Devuelve la pregunta para la base, o None si el mensaje es chat/búsqueda."""
    raw = (text or "").strip()
    if not raw:
        return None
    lower = raw.lower()
    if lower.startswith("/sql"):
        rest = raw[4:].strip(" \t:-")
        return rest or None
    cleaned = _DB_LEADING.sub("", raw, count=1).strip(" \t:-¿?¡!")
    if _DB_LEADING.match(raw):
        return cleaned or raw
    if forced or _DB_HINT.search(raw):
        return cleaned or raw
    return None


def assert_readonly(sql: str) -> str:
    """Admite solo SELECT/WITH/EXEC. Rechaza UPDATE, DELETE, INSERT, DROP, etc."""
    raw = (sql or "").strip()
    if not raw:
        raise SqlError("La consulta está vacía.")
    stripped = _strip_comments(_strip_literals(raw))
    if _FORBIDDEN.search(stripped):
        raise SqlError(
            "Solo lectura: no se permite INSERT, UPDATE, DELETE ni cambiar la base."
        )
    if _DYNAMIC_SQL.search(stripped):
        raise SqlError("No se permite SQL dinámico (EXEC (...)).")
    if _SELECT_INTO.search(stripped):
        raise SqlError("No se permite SELECT INTO (escribe una tabla).")
    for chunk in _split_batches(stripped):
        if not chunk.strip():
            continue
        if not _ALLOWED_START.match(chunk):
            raise SqlError(
                "Solo se aceptan SELECT, WITH, CALL o EXEC (solo lectura)."
            )
    return raw


def ensure_top(sql: str, limit: int = 50) -> str:
    """Añade TOP n (SQL Server). Preferir ensure_limit con el motor real."""
    return ensure_limit(sql, limit, "mssql")


def ensure_limit(sql: str, limit: int, engine: str) -> str:
    """Tope de filas: LIMIT en Postgres, TOP en SQL Server."""
    engine = (engine or "sqlite").lower()
    if re.match(r"^\s*(EXEC(?:UTE)?|CALL)\b", sql, re.IGNORECASE):
        return sql
    if engine in {"postgres", "sqlite"}:
        if re.search(r"\bLIMIT\s+\d+", sql, re.IGNORECASE):
            return sql
        return sql.rstrip().rstrip(";") + f" LIMIT {int(limit)}"
    if re.search(r"\bTOP\s+\d+", sql, re.IGNORECASE):
        return sql
    if re.search(r"\bFETCH\s+NEXT\b", sql, re.IGNORECASE):
        return sql
    return re.sub(
        r"(?i)^(\s*SELECT(?:\s+DISTINCT)?)",
        rf"\1 TOP {int(limit)}",
        sql,
        count=1,
    )


def parse_plan(text: str) -> dict:
    """Saca el JSON {type, sql|name, params} de la respuesta del modelo."""
    cleaned = re.sub(r"<think>.*?</think>", "", text or "", flags=re.DOTALL)
    match = re.search(r"\{[\s\S]*\}", cleaned)
    if not match:
        raise SqlError("El modelo no devolvió un plan SQL JSON.")
    try:
        plan = json.loads(match.group(0))
    except json.JSONDecodeError as exc:
        raise SqlError(f"Plan SQL inválido: {exc}") from exc
    if not isinstance(plan, dict):
        raise SqlError("El plan SQL no es un objeto JSON.")
    kind = str(plan.get("type") or "").lower().strip()
    if kind in {"none", "chat"}:
        raise SqlError(plan.get("reason") or "No hay consulta de solo lectura para eso.")
    return plan


def procedure_sql(name: str, params: dict | None, engine: str = "mssql") -> str:
    """Arma CALL/EXEC con nombre validado (sin concatenar valores)."""
    proc = (name or "").strip().lstrip("[").rstrip("]")
    proc = proc.replace("].[", ".")
    if not _PROC_NAME.match(proc):
        raise SqlError(f"Nombre de procedimiento inválido: {name!r}")
    params = params or {}
    if (engine or "").lower() == "postgres":
        if not params:
            return f"CALL {proc}()"
        return f"CALL {proc}(" + ", ".join(["%s"] * len(params)) + ")"
    if not params:
        return f"EXEC {proc}"
    placeholders = []
    for key in params:
        flag = str(key).strip()
        if not re.match(r"^@?[A-Za-z_][A-Za-z0-9_]*$", flag):
            raise SqlError(f"Parámetro inválido: {key!r}")
        if not flag.startswith("@"):
            flag = "@" + flag
        placeholders.append(f"{flag}=%s")
    return f"EXEC {proc} " + ", ".join(placeholders)


def format_catalog(tables: list[str], procedures: list[str]) -> str:
    t = ", ".join(tables[:40]) or "(sin tablas)"
    p = ", ".join(procedures[:40]) or "(sin procedimientos)"
    return f"Tablas: {t}\nProcedimientos: {p}"


def plan_system_prompt(catalog: str, engine: str) -> str:
    """Instrucciones JSON para el modelo según el motor."""
    engine = (engine or "sqlite").lower()
    if engine == "mssql":
        return (
            "Eres un asistente SQL Server de SOLO LECTURA.\n"
            "Nunca generes INSERT, UPDATE, DELETE, MERGE, DROP, ALTER, TRUNCATE ni SELECT INTO.\n"
            "Solo SELECT (con TOP) o EXEC de procedimientos de consulta.\n\n"
            f"Catálogo:\n{catalog}\n\n"
            "Responde ÚNICAMENTE un JSON, sin markdown:\n"
            '{"type":"select","sql":"SELECT TOP 50 ..."}\n'
            'o {"type":"procedure","name":"dbo.NombreSP","params":{"@Param":"valor"}}\n'
            'o {"type":"none","reason":"no aplica a la base"}\n'
            "Usa solo tablas y procedimientos del catálogo. SQL Server, no MySQL.\n"
        )
    dialect = "SQLite" if engine == "sqlite" else "PostgreSQL"
    return (
        f"Eres un asistente {dialect} de SOLO LECTURA para Hyperion ONMS.\n"
        "Interpreta el pedido en español; NO inventes tablas ni columnas.\n"
        "Diccionario: ONU/ONT→onts; OLT→olts; cliente→customers; "
        "clasificación/calidad→telemetry_events.classification; "
        "ping/latencia→ping_logs.ping_ms; señal/rx→telemetry_events.rx_power_dbm; "
        "plan→customers.plan; serial/GPON→gpon_sn.\n"
        "Nunca INSERT/UPDATE/DELETE/DROP. Solo SELECT ... LIMIT n. Sin TOP. "
        "Nunca hashed_password.\n\n"
        f"Catálogo real:\n{catalog}\n\n"
        "Responde ÚNICAMENTE un JSON, sin markdown:\n"
        '{"type":"select","sql":"SELECT ... LIMIT 50","chart":"bar"}\n'
        "chart: bar, line, doughnut o none. Si piden gráfico, GROUP BY.\n"
        f"Usa SOLO tablas y columnas del catálogo. {dialect}.\n"
    )


def format_result_for_model(result: QueryResult) -> str:
    """Texto compacto para el system prompt, sin inventar filas."""
    lines = [f"SQL ejecutado (solo lectura):\n{result.sql}", ""]
    if not result.columns:
        lines.append("(sin filas)")
        return "\n".join(lines)
    lines.append(" | ".join(result.columns))
    for row in result.rows[:30]:
        lines.append(" | ".join("" if v is None else str(v) for v in row))
    if result.truncated:
        lines.append("… (hay más filas; se recortó el resultado)")
    return "\n".join(lines)


def load_catalog(settings: Settings) -> str:
    """Lista tablas/columnas (y procedimientos) para que el modelo no invente nombres."""
    engine = settings.db_engine.lower()
    if engine == "sqlite":
        cols = _run(
            settings,
            "SELECT m.name AS table_name, p.name AS column_name "
            "FROM sqlite_master AS m JOIN pragma_table_info(m.name) AS p "
            "WHERE m.type = 'table' "
            "AND m.name NOT LIKE 'sqlite_%' "
            "AND m.name <> 'alembic_version' "
            "AND p.name <> 'hashed_password' "
            "ORDER BY m.name, p.cid",
            row_limit=500,
        )
        return _format_column_catalog(cols.rows)
    if engine == "postgres":
        cols = _run(
            settings,
            "SELECT table_name, column_name FROM information_schema.columns "
            "WHERE table_schema = 'public' "
            "AND table_name <> 'alembic_version' "
            "AND column_name <> 'hashed_password' "
            "ORDER BY table_name, ordinal_position",
            row_limit=500,
        )
        return _format_column_catalog(cols.rows)

    tables_sql = (
        "SELECT TOP 40 TABLE_SCHEMA + '.' + TABLE_NAME "
        "FROM INFORMATION_SCHEMA.TABLES "
        "WHERE TABLE_TYPE='BASE TABLE' "
        "ORDER BY TABLE_SCHEMA, TABLE_NAME"
    )
    procs_sql = (
        "SELECT TOP 40 ROUTINE_SCHEMA + '.' + ROUTINE_NAME "
        "FROM INFORMATION_SCHEMA.ROUTINES "
        "WHERE ROUTINE_TYPE='PROCEDURE' "
        "ORDER BY ROUTINE_SCHEMA, ROUTINE_NAME"
    )
    tables = [str(row[0]) for row in _run(settings, tables_sql, row_limit=200).rows]
    procs = [str(row[0]) for row in _run(settings, procs_sql, row_limit=200).rows]
    return format_catalog(tables, procs)


def _format_column_catalog(pairs: list[list[object]]) -> str:
    grouped: dict[str, list[str]] = {}
    for table, column in pairs:
        grouped.setdefault(str(table), []).append(str(column))
    if not grouped:
        return "Tablas: (sin tablas)"
    lines = ["Tablas:"]
    for table, columns in grouped.items():
        lines.append(f"- {table}({', '.join(columns)})")
    return "\n".join(lines)


def execute_plan(settings: Settings, plan: dict) -> QueryResult:
    """Ejecuta el JSON del modelo si es SELECT o EXEC de solo lectura."""
    kind = str(plan.get("type") or "").lower().strip()
    if kind in {"select", "sql", "query"}:
        return run_select(settings, str(plan.get("sql") or plan.get("query") or ""))
    if kind in {"procedure", "exec", "sp"}:
        params = plan.get("params") if isinstance(plan.get("params"), dict) else {}
        return run_procedure(settings, str(plan.get("name") or ""), params)
    raise SqlError(f"Tipo de plan no soportado: {kind!r}. Usa select o procedure.")


def run_select(settings: Settings, sql: str) -> QueryResult:
    sql = ensure_limit(assert_readonly(sql), settings.db_max_rows, settings.db_engine)
    return _run(settings, sql)


def run_procedure(settings: Settings, name: str, params: dict | None) -> QueryResult:
    if settings.db_engine.lower() == "sqlite":
        raise SqlError("SQLite no tiene stored procedures; usa un SELECT.")
    sql = procedure_sql(name, params, settings.db_engine)
    assert_readonly(sql)
    values = list((params or {}).values())
    return _run(settings, sql, values)


def _run(
    settings: Settings,
    sql: str,
    params: list | None = None,
    row_limit: int | None = None,
) -> QueryResult:
    if not settings.db_configured:
        raise SqlError(
            "Falta configurar la base: DB_PATH (sqlite) o DB_HOST/DB_NAME/DB_USER/.env."
        )
    limit = settings.db_max_rows if row_limit is None else row_limit
    engine = settings.db_engine.lower()
    if engine == "sqlite":
        return _run_sqlite(settings, sql, params, limit)
    if engine == "postgres":
        return _run_postgres(settings, sql, params, limit)
    return _run_mssql(settings, sql, params, limit)


def _run_sqlite(
    settings: Settings,
    sql: str,
    params: list | None,
    limit: int,
) -> QueryResult:
    import sqlite3

    path = Path(settings.db_path).expanduser().resolve()
    if not path.is_file():
        raise SqlError(f"No encuentro el archivo SQLite: {path}")
    uri = path.as_uri() + "?mode=ro"
    try:
        conn = sqlite3.connect(uri, uri=True, timeout=float(settings.db_timeout))
        try:
            conn.execute("PRAGMA query_only = ON")
            cursor = conn.cursor()
            if params:
                cursor.execute(sql, tuple(params))
            else:
                cursor.execute(sql)
            rows, columns, truncated = _fetch(cursor, limit)
            return QueryResult(sql=sql, columns=columns, rows=rows, truncated=truncated)
        finally:
            conn.close()
    except SqlError:
        raise
    except Exception as exc:
        raise SqlError(f"SQLite: {exc}") from exc


def _run_postgres(
    settings: Settings,
    sql: str,
    params: list | None,
    limit: int,
) -> QueryResult:
    import psycopg

    try:
        with psycopg.connect(
            host=settings.db_host,
            port=settings.db_port,
            dbname=settings.db_name,
            user=settings.db_user,
            password=settings.db_password,
            connect_timeout=8,
            options=f"-c statement_timeout={int(settings.db_timeout) * 1000}",
        ) as conn:
            conn.execute("BEGIN READ ONLY")
            try:
                with conn.cursor() as cursor:
                    if params:
                        cursor.execute(sql, tuple(params))
                    else:
                        cursor.execute(sql)
                    rows, columns, truncated = _fetch(cursor, limit)
            finally:
                conn.rollback()
        return QueryResult(sql=sql, columns=columns, rows=rows, truncated=truncated)
    except SqlError:
        raise
    except Exception as exc:
        raise SqlError(f"PostgreSQL: {exc}") from exc


def _run_mssql(
    settings: Settings,
    sql: str,
    params: list | None,
    limit: int,
) -> QueryResult:
    import pymssql

    server, port = _server_and_port(settings)
    conn = pymssql.connect(
        server=server,
        user=settings.db_user,
        password=settings.db_password,
        database=settings.db_name,
        port=port,
        login_timeout=8,
        timeout=settings.db_timeout,
        charset="utf8",
        tds_version="7.4",
    )
    try:
        cursor = conn.cursor()
        cursor.execute("SET TRANSACTION ISOLATION LEVEL READ UNCOMMITTED")
        cursor.execute("BEGIN TRANSACTION")
        try:
            if params:
                cursor.execute(sql, tuple(params))
            else:
                cursor.execute(sql)
            rows, columns, truncated = _fetch(cursor, limit)
        finally:
            try:
                cursor.execute("ROLLBACK TRANSACTION")
            except Exception:  # noqa: BLE001 — el SELECT puro a veces no abre tran
                pass
        return QueryResult(sql=sql, columns=columns, rows=rows, truncated=truncated)
    except Exception as exc:
        raise SqlError(f"SQL Server: {exc}") from exc
    finally:
        conn.close()


def _fetch(cursor, limit: int) -> tuple[list[list[object]], list[str], bool]:
    desc = cursor.description or []
    columns = [str(col[0]) for col in desc]
    if not columns:
        return [], [], False
    raw = cursor.fetchmany(limit + 1)
    truncated = len(raw) > limit
    rows = [_serialize_row(row) for row in raw[:limit]]
    return rows, columns, truncated


def _serialize_row(row: tuple) -> list[object]:
    out: list[object] = []
    for value in row:
        if value is None or isinstance(value, (str, int, float, bool)):
            out.append(value)
        elif isinstance(value, Decimal):
            out.append(float(value))
        elif isinstance(value, (datetime, date, time)):
            out.append(value.isoformat())
        elif isinstance(value, bytes):
            out.append(value.hex())
        else:
            out.append(str(value))
    return out


def _server_and_port(settings: Settings) -> tuple[str, int]:
    host = settings.db_host.strip()
    if "\\" in host:
        return host, settings.db_port
    return host, settings.db_port


def _strip_literals(sql: str) -> str:
    sql = re.sub(r"N?'(?:''|[^'])*'", " '' ", sql)
    sql = re.sub(r'"(?:""|[^"])*"', " \"\" ", sql)
    sql = re.sub(r"\[[^\]]*\]", " ident ", sql)
    return sql


def _strip_comments(sql: str) -> str:
    sql = re.sub(r"/\*.*?\*/", " ", sql, flags=re.DOTALL)
    sql = re.sub(r"--[^\n]*", " ", sql)
    return sql


def _split_batches(sql: str) -> list[str]:
    return [part.strip() for part in re.split(r";\s*", sql) if part.strip()]
