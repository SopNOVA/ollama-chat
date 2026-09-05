"""Arma un spec de gráfico a partir de filas SQL (solo lectura).

El navegador lo pinta con Chart.js. No genera imágenes en el servidor.
"""

from __future__ import annotations

import re
from datetime import date, datetime
from typing import Any

from ollama_chat.services.database import QueryResult

_CHART_TYPES = {"bar", "line", "doughnut", "pie"}
_TIME_RE = re.compile(
    r"^\d{4}-\d{2}-\d{2}([ tT]\d{2}:\d{2})?",
)
_CHART_HINT = re.compile(
    r"\b(gr[aá]fic[oa]s?|graficar|chart|reporte|dashboard|dona|pastel|barras?|l[ií]neas?)\b",
    re.IGNORECASE,
)


def wants_chart(text: str) -> bool:
    return bool(_CHART_HINT.search(text or ""))


def preferred_chart_type(text: str, plan_chart: object = None) -> str | None:
    raw = str(plan_chart or "").lower().strip()
    if raw in {"none", "no", "table"}:
        return "none"
    if raw in _CHART_TYPES:
        return raw
    q = (text or "").lower()
    if re.search(r"\b(l[ií]nea|evoluci[oó]n|tendencia|serie|tiempo)\b", q):
        return "line"
    if re.search(r"\b(dona|pastel|pie|porcentaje)\b", q):
        return "doughnut"
    if re.search(r"\b(barra|histograma)\b", q):
        return "bar"
    if wants_chart(text):
        return "auto"
    return None


def chart_from_result(
    result: QueryResult,
    *,
    question: str = "",
    plan_chart: object = None,
) -> dict[str, Any] | None:
    """Devuelve {type, title, labels, datasets} o None si no se puede graficar."""
    if not result.columns or not result.rows:
        return None
    preferred = preferred_chart_type(question, plan_chart)
    if preferred == "none":
        return None
    labels_idx, numeric_idxs = _split_columns(result)
    if labels_idx is None or not numeric_idxs:
        return None
    labels = [_label(row[labels_idx]) for row in result.rows]
    if len(labels) > 60:
        labels = labels[-60:]
        rows = result.rows[-60:]
    else:
        rows = result.rows
        labels = [_label(row[labels_idx]) for row in rows]

    datasets = []
    for idx in numeric_idxs[:4]:
        data = [_number(row[idx]) if idx < len(row) else None for row in rows]
        if all(v is None for v in data):
            continue
        datasets.append(
            {
                "label": str(result.columns[idx]),
                "data": data,
            }
        )
    if not datasets:
        return None

    chart_type = _pick_type(preferred, labels, datasets)
    title = _title(question, result.columns[labels_idx], datasets)
    return {
        "type": chart_type,
        "title": title,
        "labels": labels,
        "datasets": datasets,
    }


def _split_columns(result: QueryResult) -> tuple[int | None, list[int]]:
    numeric: list[int] = []
    label: int | None = None
    sample = result.rows[:12]
    for i, _name in enumerate(result.columns):
        values = [row[i] if i < len(row) else None for row in sample]
        if _mostly_numeric(values):
            numeric.append(i)
        elif label is None:
            label = i
    if label is None:
        if len(numeric) >= 2:
            return numeric[0], numeric[1:]
        return None, []
    numeric = [i for i in numeric if i != label]
    return label, numeric


def _mostly_numeric(values: list[object]) -> bool:
    seen = 0
    ok = 0
    for value in values:
        if value is None or value == "":
            continue
        seen += 1
        if _number(value) is not None:
            ok += 1
    return seen > 0 and ok >= max(1, int(seen * 0.7))


def _number(value: object) -> float | None:
    if isinstance(value, bool):
        return float(int(value))
    if isinstance(value, (int, float)):
        return float(value)
    if isinstance(value, str):
        text = value.strip().replace(",", "")
        try:
            return float(text)
        except ValueError:
            return None
    return None


def _label(value: object) -> str:
    if value is None:
        return ""
    if isinstance(value, datetime):
        return value.strftime("%Y-%m-%d %H:%M")
    if isinstance(value, date):
        return value.isoformat()
    text = str(value)
    if len(text) > 42:
        return text[:40] + "…"
    return text


def _pick_type(preferred: str | None, labels: list[str], datasets: list[dict]) -> str:
    if preferred in _CHART_TYPES:
        return preferred
    timed = sum(1 for lab in labels if _TIME_RE.match(lab))
    if timed >= max(2, len(labels) // 2):
        return "line"
    if len(datasets) == 1 and 1 <= len(labels) <= 8:
        return "doughnut"
    return "bar"


def _title(question: str, label_col: str, datasets: list[dict]) -> str:
    q = re.sub(
        r"^(consulta(?:r)?(?:\s+la\s+base)?|/sql|gr[aá]fica(?:r)?|reporte)\s+",
        "",
        (question or "").strip(),
        flags=re.IGNORECASE,
    )
    q = q.strip(" .:¿?")
    if 3 <= len(q) <= 80:
        return q[0].upper() + q[1:]
    series = datasets[0]["label"] if datasets else "serie"
    return f"{series} por {label_col}"
