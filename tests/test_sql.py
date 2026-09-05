"""Solo lectura: el guard rechaza UPDATE/DELETE y admite SELECT/EXEC."""

from __future__ import annotations

import pytest

from ollama_chat.config import Settings
from ollama_chat.services.database import (
    QueryResult,
    SqlError,
    assert_readonly,
    ensure_limit,
    ensure_top,
    execute_plan,
    extract_db_question,
    parse_plan,
    procedure_sql,
)


@pytest.mark.parametrize(
    ("text", "forced", "expected"),
    [
        ("/sql SELECT TOP 1 * FROM Personas", False, "SELECT TOP 1 * FROM Personas"),
        ("consulta la base cuántos clientes hay", False, "cuántos clientes hay"),
        ("hola qué tal", False, None),
        ("listar personas", True, "listar personas"),
        ("ejecuta el sp_BuscarPersona Juan", False, "BuscarPersona Juan"),
        ("grafica las onts por clasificación", False, "las onts por clasificación"),
    ],
)
def test_extract_db_question(text, forced, expected):
    assert extract_db_question(text, forced=forced) == expected


@pytest.mark.parametrize(
    "sql",
    [
        "SELECT * FROM Personas",
        "SELECT TOP 10 id FROM dbo.Foo WHERE nombre = 'x'",
        "WITH x AS (SELECT 1 AS a) SELECT * FROM x",
        "EXEC dbo.sp_BuscarPersona @nombre='Ana'",
        "EXECUTE dbo.sp_Listar",
        "CALL public.sp_listar()",
        "SELECT * FROM t WHERE nota = 'hay que UPDATE mañana'",
    ],
)
def test_assert_readonly_allows_select_and_exec(sql):
    assert assert_readonly(sql) == sql


@pytest.mark.parametrize(
    "sql",
    [
        "UPDATE t SET x=1",
        "DELETE FROM t",
        "INSERT INTO t VALUES (1)",
        "DROP TABLE t",
        "SELECT * INTO t2 FROM t",
        "EXEC('UPDATE t SET x=1')",
        "EXECUTE AS USER = 'sa'",
        "TRUNCATE TABLE t",
        "ALTER TABLE t ADD x int",
        "MERGE t USING s ON 1=1 WHEN MATCHED THEN UPDATE SET x=1;",
        "GRANT SELECT ON t TO u",
    ],
)
def test_assert_readonly_blocks_writes(sql):
    with pytest.raises(SqlError):
        assert_readonly(sql)


def test_ensure_top_inserts_limit():
    out = ensure_top("SELECT * FROM Personas", 50)
    assert "TOP 50" in out.upper()
    assert ensure_top("SELECT TOP 3 * FROM Personas", 50) == "SELECT TOP 3 * FROM Personas"


def test_ensure_limit_postgres():
    out = ensure_limit("SELECT * FROM customers", 50, "postgres")
    assert out.endswith("LIMIT 50")
    already = "SELECT * FROM customers LIMIT 10"
    assert ensure_limit(already, 50, "postgres") == already


def test_ensure_limit_sqlite():
    out = ensure_limit("SELECT * FROM onts", 50, "sqlite")
    assert out.endswith("LIMIT 50")


def test_procedure_sql_validates_name():
    sql = procedure_sql("dbo.sp_Buscar", {"@nombre": "Ana"})
    assert sql == "EXEC dbo.sp_Buscar @nombre=%s"
    with pytest.raises(SqlError):
        procedure_sql("dbo.sp; DROP TABLE t", {})


def test_parse_plan_strips_think_and_json():
    raw = '<think>mmm</think>{"type":"select","sql":"SELECT 1"}'
    assert parse_plan(raw)["sql"] == "SELECT 1"


def test_execute_plan_select_is_readonly(monkeypatch):
    captured = {}

    def fake_run(settings, sql, params=None):
        captured["sql"] = sql
        return QueryResult(sql=sql, columns=["n"], rows=[[1]])

    monkeypatch.setattr("ollama_chat.services.database._run", fake_run)
    out = execute_plan(
        Settings(db_engine="postgres"), {"type": "select", "sql": "SELECT 1 AS n"}
    )
    assert "LIMIT" in captured["sql"].upper()
    assert out.rows == [[1]]


def test_execute_plan_rejects_update():
    with pytest.raises(SqlError):
        execute_plan(Settings(), {"type": "select", "sql": "UPDATE t SET x=1"})
