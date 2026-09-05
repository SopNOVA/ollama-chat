"""Interpreta pedidos en español y los traduce al esquema real de Hyperion.

El modelo 4B se equivoca de tabla/columna. Aquí hay plantillas con los
nombres que sí existen, más un reintento si el SQL igual falla.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from ollama_chat.services.charts import preferred_chart_type, wants_chart

# Palabras del usuario → tablas/columnas reales (sqlite hyperion_onms.db).
_TABLE_ALIASES = {
    "ont": "onts",
    "onts": "onts",
    "onu": "onts",
    "onus": "onts",
    "onu's": "onts",
    "olt": "olts",
    "olts": "olts",
    "cliente": "customers",
    "clientes": "customers",
    "customer": "customers",
    "customers": "customers",
    "usuario": "users",
    "usuarios": "users",
    "users": "users",
    "ping": "ping_logs",
    "pings": "ping_logs",
    "latencia": "ping_logs",
    "telemetria": "telemetry_events",
    "telemetría": "telemetry_events",
    "eventos": "telemetry_events",
    "señal": "telemetry_events",
    "optica": "telemetry_events",
    "óptica": "telemetry_events",
}

_COLUMN_ALIASES = {
    "clasificacion": "classification",
    "clasificación": "classification",
    "calidad": "classification",
    "serial": "gpon_sn",
    "serie": "gpon_sn",
    "sn": "gpon_sn",
    "gpon": "gpon_sn",
    "mac": "mac_address",
    "ip": "ip_address",
    "nombre": "name",
    "plan": "plan",
    "cedula": "document",
    "cédula": "document",
    "documento": "document",
    "telefono": "phone",
    "teléfono": "phone",
    "activo": "is_active",
    "estado": "status",
    "potencia": "rx_power_dbm",
    "rx": "rx_power_dbm",
    "tx": "tx_power_dbm",
    "señal": "rx_power_dbm",
    "ping": "ping_ms",
    "latencia": "ping_ms",
}


@dataclass(frozen=True)
class ReportTemplate:
    key: str
    keywords: tuple[str, ...]
    sql: str
    chart: str
    title: str
    weight: int = 1
    kind: str = "agg"  # agg = conteo; detail = filas con IP/MAC/cliente


_CLASS_FILTERS: tuple[tuple[str, str], ...] = (
    (r"\b(critical|critic[oa]s?)\b", "CRITICAL"),
    (r"\b(bad|mal[ao]s?)\b", "BAD"),
    (r"\b(good|buen[ao]s?)\b", "GOOD"),
    (r"\b(regular|media?s?)\b", "REGULAR"),
)

_IDENTITY_RE = re.compile(
    r"\b(ip|mac|gpon|serial|detalle|detalles)\b|"
    r"nombre del cliente|nombre de cliente|con la ip|con mac|que tengan",
    re.IGNORECASE,
)

# Última telemetría por ONT + datos de inventario. {cls} es BAD/GOOD/...
_ONT_DETAIL_SQL = (
    "SELECT COALESCE(c.name, 'sin cliente') AS cliente, "
    "n.ip_address AS ip, n.mac_address AS mac, n.gpon_sn AS gpon, "
    "t.classification AS clasificacion, t.rx_power_dbm AS rx_dbm "
    "FROM ("
    " SELECT ont_id, classification, rx_power_dbm, "
    " ROW_NUMBER() OVER (PARTITION BY ont_id ORDER BY received_at DESC) AS rn "
    " FROM telemetry_events"
    ") t "
    "JOIN onts n ON n.id = t.ont_id "
    "LEFT JOIN customers c ON c.id = n.customer_id "
    "WHERE t.rn = 1 AND UPPER(COALESCE(t.classification, '')) = '{cls}' "
    "ORDER BY cliente"
)

_TEMPLATES: tuple[ReportTemplate, ...] = (
    ReportTemplate(
        key="onts_class_detail",
        keywords=(
            "ip",
            "mac",
            "nombre del cliente",
            "nombre de cliente",
            "con la ip",
            "que tengan",
            "detalle",
        ),
        sql=_ONT_DETAIL_SQL,
        chart="bar",
        title="ONTs {cls} (cliente, IP, MAC)",
        weight=6,
        kind="detail",
    ),
    ReportTemplate(
        key="ont_classification",
        keywords=(
            "clasific",
            "calidad",
            "critical",
            "telemetr",
            "good",
            "bad",
            "estado de la red",
            "salud",
        ),
        sql=(
            "SELECT COALESCE(classification, 'SIN DATO') AS clasificacion, "
            "COUNT(*) AS cantidad FROM telemetry_events "
            "GROUP BY classification ORDER BY cantidad DESC"
        ),
        chart="doughnut",
        title="Eventos de telemetría por clasificación",
        weight=3,
    ),
    ReportTemplate(
        key="ont_latest_class",
        keywords=("ont por clasific", "onu por clasific", "onts por calidad"),
        sql=(
            "SELECT COALESCE(classification, 'SIN DATO') AS clasificacion, "
            "COUNT(*) AS onts FROM ("
            " SELECT classification, ROW_NUMBER() OVER "
            "(PARTITION BY ont_id ORDER BY received_at DESC) AS rn "
            " FROM telemetry_events"
            ") t WHERE rn = 1 "
            "GROUP BY classification ORDER BY onts DESC"
        ),
        chart="doughnut",
        title="Última clasificación por ONT",
        weight=4,
    ),
    ReportTemplate(
        key="optical",
        keywords=("optic", "óptic", "rx", "tx", "potencia", "señal", "laser"),
        sql=(
            "SELECT COALESCE(optical_status, 'UNKNOWN') AS estado_optico, "
            "COUNT(*) AS cantidad FROM telemetry_events "
            "WHERE optical_status IS NOT NULL "
            "GROUP BY optical_status ORDER BY cantidad DESC"
        ),
        chart="doughnut",
        title="Estado óptico GPON",
        weight=3,
    ),
    ReportTemplate(
        key="ping_trend",
        keywords=("ping", "latencia", "evoluci", "tendencia", "tiempo"),
        sql=(
            "SELECT date(ts) AS dia, ROUND(AVG(ping_ms), 1) AS ping_ms "
            "FROM ping_logs WHERE ping_ms IS NOT NULL "
            "GROUP BY date(ts) ORDER BY dia"
        ),
        chart="line",
        title="Ping promedio por día",
        weight=3,
    ),
    ReportTemplate(
        key="customers_plan",
        keywords=("plan", "planes", "paquete", "clientes por"),
        sql=(
            "SELECT COALESCE(plan, 'sin plan') AS plan, COUNT(*) AS clientes "
            "FROM customers GROUP BY plan ORDER BY clientes DESC"
        ),
        chart="bar",
        title="Clientes por plan",
        weight=3,
    ),
    ReportTemplate(
        key="onts_by_olt",
        keywords=("por olt", "onts por olt", "onu por olt", "en cada olt"),
        sql=(
            "SELECT COALESCE(o.name, 'sin OLT') AS olt, COUNT(*) AS onts "
            "FROM onts n LEFT JOIN olts o ON o.id = n.olt_id "
            "GROUP BY o.name ORDER BY onts DESC"
        ),
        chart="bar",
        title="ONTs por OLT",
        weight=3,
    ),
    ReportTemplate(
        key="onts_active",
        keywords=("activ", "inactiv", "encendid", "apagad"),
        sql=(
            "SELECT CASE WHEN is_active = 1 THEN 'activas' ELSE 'inactivas' END "
            "AS estado, COUNT(*) AS onts FROM onts GROUP BY is_active"
        ),
        chart="doughnut",
        title="ONTs activas vs inactivas",
        weight=2,
    ),
    ReportTemplate(
        key="overview",
        keywords=("resumen", "overview", "totales", "cuántos hay", "cuantos hay", "inventario"),
        sql=(
            "SELECT 'clientes' AS tipo, COUNT(*) AS cantidad FROM customers "
            "UNION ALL SELECT 'olts', COUNT(*) FROM olts "
            "UNION ALL SELECT 'onts', COUNT(*) FROM onts "
            "UNION ALL SELECT 'eventos', COUNT(*) FROM telemetry_events "
            "UNION ALL SELECT 'pings', COUNT(*) FROM ping_logs"
        ),
        chart="bar",
        title="Inventario Hyperion",
        weight=1,
    ),
    ReportTemplate(
        key="customers_list",
        keywords=("lista de clientes", "listar clientes", "quiénes son los clientes"),
        sql=(
            "SELECT id, name, document, plan, phone FROM customers "
            "ORDER BY name"
        ),
        chart="none",
        title="Clientes",
        weight=2,
        kind="detail",
    ),
    ReportTemplate(
        key="onts_list",
        keywords=("lista de ont", "listar ont", "listar onu"),
        sql=(
            "SELECT id, gpon_sn, mac_address, ip_address, model, is_active "
            "FROM onts ORDER BY id"
        ),
        chart="none",
        title="ONTs",
        weight=2,
        kind="detail",
    ),
    ReportTemplate(
        key="olts_list",
        keywords=("lista de olt", "listar olt", "estado de las olt"),
        sql=(
            "SELECT id, name, ip_address, status, is_active, last_ping_ms "
            "FROM olts ORDER BY name"
        ),
        chart="none",
        title="OLTs",
        weight=2,
        kind="detail",
    ),
)


def interpret_report(question: str) -> dict | None:
    """Si el pedido encaja en Hyperion, devuelve un plan SQL ya validado."""
    q = _norm(question)
    if not q:
        return None
    wants_detail = bool(_IDENTITY_RE.search(q))
    cls = _classification_from_question(q)
    best: ReportTemplate | None = None
    best_score = 0
    for tpl in _TEMPLATES:
        score = 0
        for kw in tpl.keywords:
            if _has_kw(q, kw):
                score += tpl.weight
        if wants_detail:
            if tpl.kind == "detail":
                score += 10
            else:
                score -= 4
        if cls and tpl.key == "onts_class_detail":
            score += 8
        if score > best_score:
            best_score = score
            best = tpl
    if best is None or best_score <= 0:
        if wants_detail and cls:
            best = next(t for t in _TEMPLATES if t.key == "onts_class_detail")
        else:
            return None
    chart = preferred_chart_type(question, best.chart) or best.chart
    if chart == "auto":
        chart = best.chart
    sql = best.sql
    title = best.title
    if "{cls}" in sql:
        sql = sql.format(cls=cls or "BAD")
        title = title.format(cls=cls or "BAD")
    return {
        "type": "select",
        "sql": sql,
        "chart": chart,
        "title": title,
    }


def default_report(question: str) -> dict:
    """Cuando piden gráfico/reporte sin detalle, un inventario o el detalle filtrado."""
    nq = _norm(question)
    cls = _classification_from_question(nq)
    if _IDENTITY_RE.search(nq) or cls:
        tpl = next(t for t in _TEMPLATES if t.key == "onts_class_detail")
        sql = tpl.sql.format(cls=cls or "BAD")
        return {
            "type": "select",
            "sql": sql,
            "chart": tpl.chart,
            "title": tpl.title.format(cls=cls or "BAD"),
        }
    if wants_chart(question) or "reporte" in nq:
        if any(_has_kw(nq, w) or w in nq for w in ("ont", "onu", "calidad", "clasific", "red")):
            tpl = next(t for t in _TEMPLATES if t.key == "ont_classification")
        else:
            tpl = next(t for t in _TEMPLATES if t.key == "overview")
        return {
            "type": "select",
            "sql": tpl.sql,
            "chart": tpl.chart,
            "title": tpl.title,
        }
    tpl = next(t for t in _TEMPLATES if t.key == "overview")
    return {"type": "select", "sql": tpl.sql, "chart": tpl.chart, "title": tpl.title}


def _classification_from_question(question: str) -> str | None:
    q = _norm(question)
    for pattern, value in _CLASS_FILTERS:
        if re.search(pattern, q, flags=re.IGNORECASE):
            return value
    return None


def rewrite_sql(sql: str) -> str:
    """Corrige ont→onts, cliente→customers, clasificación→classification, etc."""
    out = sql
    for alias, real in sorted(_TABLE_ALIASES.items(), key=lambda kv: -len(kv[0])):
        out = re.sub(
            rf"\b{re.escape(alias)}\b",
            real,
            out,
            flags=re.IGNORECASE,
        )
    for alias, real in sorted(_COLUMN_ALIASES.items(), key=lambda kv: -len(kv[0])):
        out = re.sub(
            rf"\b{re.escape(alias)}\b",
            real,
            out,
            flags=re.IGNORECASE,
        )
    return out


def is_schema_error(exc: BaseException) -> bool:
    msg = str(exc).lower()
    return any(
        token in msg
        for token in (
            "no such table",
            "no such column",
            "unknown column",
            "does not exist",
            "invalid column",
            "no existe",
        )
    )


def _norm(text: str) -> str:
    text = (text or "").lower()
    repl = str.maketrans("áéíóúü", "aeiouu")
    return text.translate(repl)


def _has_kw(haystack: str, keyword: str) -> bool:
    key = _norm(keyword)
    if " " in key or len(key) >= 5:
        return key in haystack
    return bool(re.search(rf"\b{re.escape(key)}\b", haystack))
