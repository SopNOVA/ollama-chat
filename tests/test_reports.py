"""El pedido en español se mapea a tablas reales de Hyperion."""

from ollama_chat.services.reports import (
    default_report,
    interpret_report,
    is_schema_error,
    rewrite_sql,
)


def test_interpret_classification_chart():
    plan = interpret_report("grafica las ONTs por clasificación")
    assert plan is not None
    assert "telemetry_events" in plan["sql"]
    assert "classification" in plan["sql"]
    assert "JOIN onts" not in plan["sql"]
    assert plan["chart"] in {"doughnut", "auto", "bar"}


def test_interpret_bad_onts_with_customer_ip_mac():
    plan = interpret_report(
        "grafica las ONTs que tengan clasificacion bad, con la IP, MAC y nombre del cliente"
    )
    assert plan is not None
    sql = plan["sql"]
    assert "JOIN onts" in sql
    assert "customers" in sql
    assert "ip_address" in sql
    assert "mac_address" in sql
    assert "BAD" in sql
    assert "COUNT(*)" not in sql or "GROUP BY classification" not in sql


def test_interpret_customers_plan():
    plan = interpret_report("reporte de clientes por plan")
    assert plan is not None
    assert "customers" in plan["sql"]
    assert "plan" in plan["sql"]


def test_interpret_ping_trend():
    plan = interpret_report("evolución del ping")
    assert plan is not None
    assert "ping_logs" in plan["sql"]
    assert plan["chart"] == "line"


def test_rewrite_invented_names():
    sql = rewrite_sql("SELECT clasificacion, count(*) FROM ont GROUP BY clasificacion")
    assert "classification" in sql
    assert "FROM onts" in sql
    assert "FROM ont " not in sql + " "


def test_schema_error_detection():
    assert is_schema_error(Exception("SQLite: no such column: estado"))
    assert is_schema_error(Exception("no such table: onu"))
    assert not is_schema_error(Exception("Solo lectura: no se permite UPDATE"))


def test_default_report_for_vague_chart():
    plan = default_report("hazme un grafico")
    assert plan["type"] == "select"
    assert "FROM" in plan["sql"].upper()
