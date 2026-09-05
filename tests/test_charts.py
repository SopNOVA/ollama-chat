"""Specs de gráfico a partir de filas SQL."""

from ollama_chat.services.charts import chart_from_result, wants_chart
from ollama_chat.services.database import QueryResult


def test_wants_chart_spanish():
    assert wants_chart("grafica las ONTs por clasificación")
    assert wants_chart("haz un reporte de clientes")
    assert not wants_chart("hola qué tal")


def test_doughnut_from_counts():
    result = QueryResult(
        sql="SELECT classification, count(*) FROM telemetry_events GROUP BY 1",
        columns=["classification", "count"],
        rows=[["GOOD", 10], ["BAD", 4], ["CRITICAL", 2]],
    )
    chart = chart_from_result(result, question="grafica ONTs por clasificación")
    assert chart is not None
    assert chart["type"] == "doughnut"
    assert chart["labels"] == ["GOOD", "BAD", "CRITICAL"]
    assert chart["datasets"][0]["data"] == [10.0, 4.0, 2.0]


def test_line_from_dates():
    result = QueryResult(
        sql="SELECT day, avg_ping FROM t",
        columns=["day", "avg_ping"],
        rows=[["2026-08-01", 12.2], ["2026-08-02", 18.5], ["2026-08-03", 9.1]],
    )
    chart = chart_from_result(result, question="evolución del ping")
    assert chart is not None
    assert chart["type"] == "line"


def test_no_chart_single_column():
    result = QueryResult(sql="SELECT 1", columns=["n"], rows=[[1]])
    assert chart_from_result(result) is None
