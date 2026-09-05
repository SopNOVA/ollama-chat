"""Búsqueda web para traer enlaces reales al chat.

Ollama no navega Internet: este módulo consulta Google (Custom Search API
si hay claves) y, si no, un metasearch con backend Google. Los hits se
inyectan en el prompt para que el modelo no invente URLs.
"""

from __future__ import annotations

import asyncio
import math
import re
from dataclasses import dataclass
from html import unescape
from urllib.parse import unquote, urlparse

import httpx

from ollama_chat.config import Settings

# Google Custom Search JSON API (100 consultas/día en el cupo gratis).
_GOOGLE_CSE = "https://www.googleapis.com/customsearch/v1"

# Solo estos medios hondureños. Cualquier otro host se descarta.
NEWS_DOMAINS: tuple[str, ...] = (
    "laprensa.hn",
    "elheraldo.hn",
    "latribuna.hn",
    "elpais.hn",
    "hondudiario.com",
    "proceso.hn",
    "ellibertador.hn",
    "hch.tv",
    "canal11.hn",
)
# Y estas redes, si el resultado es de la misma búsqueda.
SOCIAL_DOMAINS: tuple[str, ...] = (
    "facebook.com",
    "fb.com",
    "instagram.com",
    "linkedin.com",
    "lnkd.in",
)
ALLOWED_DOMAINS: tuple[str, ...] = NEWS_DOMAINS + SOCIAL_DOMAINS

_STOPWORDS = {
    "de",
    "del",
    "la",
    "las",
    "el",
    "los",
    "en",
    "y",
    "o",
    "un",
    "una",
    "unos",
    "unas",
    "por",
    "con",
    "para",
    "que",
    "se",
    "su",
    "al",
    "lo",
    "a",
    "es",
}
_UA = {
    "User-Agent": (
        "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
    )
}
_SKIP_PATH = (
    ".avif",
    ".jpg",
    ".jpeg",
    ".png",
    ".gif",
    ".webp",
    ".mp4",
    ".pdf",
    ".css",
    ".js",
)

# Verbos al inicio del mensaje que piden enlaces, no un chat normal.
_LEADING_PHRASE = re.compile(
    r"""^
    [¿¡]*\s*
    (?:por\s+favor,?\s+)?
    (?:(?:puedes?|podr[ií]as?|quiero\s+que(?:\s+me)?)\s+)?
    (?:me\s+)?
    (?:
        /search|
        buscar?|b[uú]sca(?:me)?|
        google(?:ar|a|ame)?|
        search(?:\s+for)?|
        (?:trae(?:r|me)?|dame|muestra(?:me)?)\s+(?:los\s+)?(?:links?|enlaces?|resultados?)
    )
    (?:\s+(?:en\s+google|en\s+internet|en\s+la\s+web))?
    (?:\s+(?:sobre|de|acerca\s+de))?
    [\s:,-]*
    """,
    re.IGNORECASE | re.VERBOSE,
)


class SearchError(Exception):
    """La búsqueda no devolvió resultados ni un proveedor usable."""


@dataclass(frozen=True)
class SearchHit:
    """Un resultado: título, URL http(s) y recorte de texto."""

    title: str
    url: str
    snippet: str = ""

    def as_dict(self) -> dict[str, str]:
        return {"title": self.title, "url": self.url, "snippet": self.snippet}


@dataclass(frozen=True)
class SearchOutcome:
    query: str
    provider: str
    hits: list[SearchHit]


def extract_search_query(text: str, *, forced: bool = False) -> str | None:
    """Devuelve la consulta a buscar, o None si el mensaje es chat normal.

    `forced=True` (toggle Google en la UI): todo el texto es la query,
    aunque no diga "busca".
    """
    raw = (text or "").strip()
    if not raw:
        return None
    lower = raw.lower()
    if lower.startswith("/search"):
        rest = raw[7:].strip(" \t:-")
        return rest or None
    cleaned = _LEADING_PHRASE.sub("", raw, count=1).strip(" \t:-¿?¡!")
    if _LEADING_PHRASE.match(raw):
        return cleaned or None
    if forced:
        return cleaned or raw
    return None


def format_search_context(query: str, hits: list[SearchHit]) -> str:
    """System prompt con los hits reales. El modelo solo puede citar estos URLs."""
    lines = [
        f'El usuario pidió enlaces de esta búsqueda: "{query}"',
        "",
        "Resultados reales (no inventes URLs ni cambies los links):",
        "",
    ]
    for i, hit in enumerate(hits, start=1):
        lines.append(f"{i}. {hit.title}")
        lines.append(f"   URL: {hit.url}")
        if hit.snippet:
            lines.append(f"   Resumen: {hit.snippet}")
        lines.append("")
    lines.extend(
        [
            "Instrucciones:",
            "- Responde en español.",
            "- Lista todos los enlaces con título y URL exactamente como aparecen arriba.",
            "- El resumen es la descripción de la nota (el nombre puede no estar en el titular).",
            "- Una frase de por qué cada uno es relevante.",
            "- Al final, sugiere 2 o 3 búsquedas relacionadas (solo texto, no URLs nuevas).",
            "- Prohibido inventar enlaces o citar sitios que no estén en la lista.",
            "- Fuentes permitidas: La Prensa, El Heraldo, La Tribuna, El País HN, "
            "Hondudiario, Proceso Digital, El Libertador, HCH, Canal 11, "
            "Facebook, Instagram y LinkedIn.",
        ]
    )
    return "\n".join(lines)


async def search_web(
    query: str,
    settings: Settings,
    http: httpx.AsyncClient | None = None,
) -> SearchOutcome:
    """Busca en prensa hondureña + redes. El nombre puede estar en el titular o en la bajada."""
    q = (query or "").strip()
    if not q:
        raise SearchError("La búsqueda está vacía.")
    max_results = max(1, min(settings.search_max_results, 10))
    errors: list[str] = []
    news_hits: list[SearchHit] = []
    social_hits: list[SearchHit] = []
    provider = "web"

    if settings.google_api_key and settings.google_cse_id:
        try:
            news_hits, social_hits = await asyncio.gather(
                _google_cse(_site_query(q, NEWS_DOMAINS), settings, http, max_results),
                _google_cse(_site_query(q, SOCIAL_DOMAINS), settings, http, max_results),
            )
            provider = "google"
        except Exception as exc:  # noqa: BLE001 — caer al fallback
            errors.append(str(exc))
            news_hits, social_hits = [], []

    if not news_hits and not social_hits:
        raw, provider = await asyncio.to_thread(
            _ddgs_restricted, q, max_results, settings.search_region
        )
        news_hits, social_hits = _partition(raw)

    hits = await _finalize(q, news_hits, social_hits, max_results, http)
    if hits:
        return SearchOutcome(query=q, provider=provider, hits=hits)
    detail = "; ".join(errors) if errors else "el nombre no apareció en titular ni en la descripción"
    raise SearchError(
        f"No encontré enlaces en La Prensa, El Heraldo, La Tribuna, El País HN, "
        f"Hondudiario, Proceso, El Libertador, HCH, Canal 11, Facebook, Instagram "
        f"ni LinkedIn para «{q}»: {detail}"
    )


async def _google_cse(
    query: str,
    settings: Settings,
    http: httpx.AsyncClient | None,
    max_results: int,
) -> list[SearchHit]:
    """GET customsearch.googleapis.com. `num` admite como máximo 10."""
    params = {
        "key": settings.google_api_key,
        "cx": settings.google_cse_id,
        "q": query,
        "num": max_results,
        "hl": "es",
    }
    own_client = http is None
    client = http or httpx.AsyncClient(timeout=15.0)
    try:
        response = await client.get(_GOOGLE_CSE, params=params)
        if response.status_code >= 400:
            raise SearchError(
                f"Google CSE {response.status_code}: {response.text[:300]}"
            )
        payload = response.json()
    finally:
        if own_client:
            await client.aclose()

    items = payload.get("items") or []
    hits: list[SearchHit] = []
    for item in items:
        hit = _hit(item.get("title") or "", item.get("link") or "", item.get("snippet") or "")
        if hit:
            hits.append(hit)
    return hits


def is_allowed_url(url: str) -> bool:
    """True solo si el host es un medio hondureño de la lista o FB/IG/LinkedIn."""
    host = urlparse((url or "").strip()).netloc.lower()
    if host.startswith("www."):
        host = host[4:]
    if not host:
        return False
    return any(host == domain or host.endswith("." + domain) for domain in ALLOWED_DOMAINS)


def _site_query(query: str, domains: tuple[str, ...]) -> str:
    """Query con operadores site: para no traer el resto de Internet."""
    or_sites = " OR ".join(f"site:{domain}" for domain in domains)
    return f"({query}) ({or_sites})"


def _mix(news: list[SearchHit], social: list[SearchHit], max_results: int) -> list[SearchHit]:
    """Prioriza prensa hondureña y deja un par de cupos para redes."""
    seen: set[str] = set()
    out: list[SearchHit] = []

    def take(hit: SearchHit) -> None:
        if len(out) >= max_results:
            return
        key = hit.url.split("#", 1)[0].rstrip("/").lower()
        if key in seen:
            return
        seen.add(key)
        out.append(hit)

    social_slots = min(2, len(social), max(0, max_results // 3)) if social else 0
    news_budget = max_results - social_slots
    for hit in news:
        if len(out) >= news_budget:
            break
        take(hit)
    for hit in social:
        take(hit)
    for hit in news:
        take(hit)
    return out


def query_tokens(query: str) -> list[str]:
    """Palabras de la búsqueda, sin stopwords cortas (de, la, en…)."""
    words = re.findall(r"[0-9a-záéíóúüñ]+", (query or "").lower())
    return [w for w in words if len(w) > 2 and w not in _STOPWORDS]


def _token_hits(query: str, *parts: str) -> int:
    tokens = query_tokens(query)
    haystack = " ".join(p or "" for p in parts).lower()
    return sum(1 for token in tokens if token in haystack)


def query_matches(query: str, *parts: str) -> bool:
    """True si basta un campo (titular, bajada, URL o cuerpo) con la búsqueda."""
    tokens = query_tokens(query)
    if not tokens:
        return True
    haystack = " ".join(p or "" for p in parts).lower()
    if not haystack.strip():
        return False
    found = _token_hits(query, *parts)
    need = len(tokens) if len(tokens) <= 2 else max(2, math.ceil(len(tokens) * 0.7))
    return found >= need


def _url_haystack(url: str) -> str:
    """Palabras del slug: /sucesos/cae-joven-estafas → 'sucesos cae joven estafas'."""
    path = unquote(urlparse(url or "").path or "")
    return re.sub(r"[-_/]+", " ", path).strip()


def hit_matches(
    query: str,
    title: str = "",
    snippet: str = "",
    url: str = "",
    body: str = "",
) -> bool:
    """El link sale si la búsqueda está en el título, en la URL o en la descripción.

    No hace falta que esté en los dos. Título sí y bajada no → aparece.
    Título no y bajada sí → aparece. URL sí y bajada no → aparece.
    """
    return (
        query_matches(query, title)
        or query_matches(query, snippet)
        or query_matches(query, body)
        or query_matches(query, _url_haystack(url))
    )


def excerpt_around_query(query: str, *parts: str, width: int = 220) -> str:
    """Recorte estilo Google, centrado en el nombre (suele estar en la bajada)."""
    text = unescape(re.sub(r"\s+", " ", " ".join(p for p in parts if p)).strip())
    if not text:
        return ""
    lower = text.lower()
    pos = -1
    for token in sorted(query_tokens(query), key=len, reverse=True):
        pos = lower.find(token)
        if pos >= 0:
            break
    if pos < 0:
        return text[:width].strip()
    start = max(0, pos - 70)
    end = min(len(text), start + width)
    start = max(0, end - width)
    chunk = text[start:end].strip()
    if start:
        chunk = "…" + chunk
    if end < len(text):
        chunk += "…"
    return chunk


def _partition(hits: list[SearchHit]) -> tuple[list[SearchHit], list[SearchHit]]:
    news: list[SearchHit] = []
    social: list[SearchHit] = []
    for hit in hits:
        if _is_news_url(hit.url):
            news.append(hit)
        else:
            social.append(hit)
    return news, social


def _is_news_url(url: str) -> bool:
    host = urlparse(url).netloc.lower()
    if host.startswith("www."):
        host = host[4:]
    return any(host == d or host.endswith("." + d) for d in NEWS_DOMAINS)


async def _finalize(
    query: str,
    news: list[SearchHit],
    social: list[SearchHit],
    max_results: int,
    http: httpx.AsyncClient | None,
) -> list[SearchHit]:
    """Enriquece notas (bajada real) y se queda con las que mencionan la búsqueda."""
    news = await _enrich_news(query, news, http)
    social = [
        SearchHit(
            hit.title,
            hit.url,
            excerpt_around_query(query, hit.snippet) or hit.snippet,
        )
        for hit in social
        if hit_matches(query, hit.title, hit.snippet, hit.url)
    ]
    news.sort(key=lambda hit: 0 if query_matches(query, hit.title) else 1)
    return _mix(news, social, max_results)


async def _enrich_news(
    query: str,
    hits: list[SearchHit],
    http: httpx.AsyncClient | None,
    fetch_limit: int = 12,
) -> list[SearchHit]:
    """Conserva la nota si el texto está en el título, la URL o la bajada (no en ambos)."""
    seen: set[str] = set()
    unique: list[SearchHit] = []
    for hit in hits:
        key = hit.url.split("#", 1)[0].rstrip("/").lower()
        if key in seen:
            continue
        seen.add(key)
        unique.append(hit)

    ready: list[SearchHit] = []
    need_fetch: list[SearchHit] = []
    for hit in unique:
        if hit_matches(query, hit.title, hit.snippet, hit.url):
            snippet = excerpt_around_query(query, hit.snippet, hit.title) or hit.snippet
            ready.append(SearchHit(hit.title, hit.url, snippet))
        else:
            need_fetch.append(hit)

    async def fetch_one(hit: SearchHit) -> SearchHit | None:
        page = await _fetch_article(hit.url, http)
        if not page:
            if hit_matches(query, hit.title, hit.snippet, hit.url):
                return SearchHit(hit.title, hit.url, hit.snippet)
            return None
        title, desc, body = page
        title = title or hit.title
        if not hit_matches(query, title, desc or hit.snippet, hit.url, body):
            return None
        snippet = excerpt_around_query(query, desc, body, hit.snippet) or desc or hit.snippet
        return SearchHit(title, hit.url, snippet)

    fetched = await asyncio.gather(*[fetch_one(hit) for hit in need_fetch[:fetch_limit]])
    ready.extend(hit for hit in fetched if hit)
    return ready


async def _fetch_article(
    url: str,
    http: httpx.AsyncClient | None,
) -> tuple[str, str, str] | None:
    """Baja HTML de un medio permitido y saca titular, meta description y párrafos."""
    if not _is_news_url(url) or not is_allowed_url(url):
        return None
    own = http is None
    client = http or httpx.AsyncClient(
        timeout=8.0, follow_redirects=True, headers=_UA
    )
    try:
        response = await client.get(url, headers=_UA)
        final = str(response.url)
        if response.status_code >= 400 or not is_allowed_url(final) or not _is_news_url(final):
            return None
        ctype = response.headers.get("content-type", "text/html")
        if "html" not in ctype.lower():
            return None
        return _parse_article(response.text[:200_000])
    except httpx.HTTPError:
        return None
    finally:
        if own:
            await client.aclose()


def _parse_article(html: str) -> tuple[str, str, str]:
    """Titular, descripción (og/meta) y texto de los primeros <p>."""
    title = _meta_content(html, "og:title") or _tag_inner(html, "title")
    desc = _meta_content(html, "og:description") or _meta_content(html, "description")
    paragraphs = re.findall(r"<p\b[^>]*>(.*?)</p>", html, flags=re.IGNORECASE | re.DOTALL)
    body = " ".join(_strip_tags(p) for p in paragraphs[:15])
    return unescape(title).strip(), unescape(desc).strip(), unescape(body).strip()


def _meta_content(html: str, key: str) -> str:
    key_re = re.escape(key)
    patterns = (
        rf'<meta\b[^>]*(?:name|property)=["\']{key_re}["\'][^>]*content=["\']([^"\']+)',
        rf'<meta\b[^>]*content=["\']([^"\']+)["\'][^>]*(?:name|property)=["\']{key_re}["\']',
    )
    for pattern in patterns:
        match = re.search(pattern, html, flags=re.IGNORECASE)
        if match:
            return _strip_tags(match.group(1))
    return ""


def _tag_inner(html: str, tag: str) -> str:
    match = re.search(rf"<{tag}\b[^>]*>(.*?)</{tag}>", html, flags=re.IGNORECASE | re.DOTALL)
    return _strip_tags(match.group(1)) if match else ""


def _strip_tags(blob: str) -> str:
    text = re.sub(r"<[^>]+>", " ", blob or "")
    return re.sub(r"\s+", " ", unescape(text)).strip()


def _ddgs_restricted(
    query: str,
    max_results: int,
    region: str,
) -> tuple[list[SearchHit], str]:
    """Una búsqueda por medio, en serie: el paralelo satura y se pierde la bajada."""
    news: list[SearchHit] = []
    good = 0
    for domain in NEWS_DOMAINS:
        hits, _prov = _ddgs_query(f"{query} site:{domain}", 8, region, query)
        news.extend(hits)
        good += sum(1 for hit in hits if hit_matches(query, hit.title, hit.snippet, hit.url))
        if good >= max_results:
            break
    social, _prov = _ddgs_query(
        _site_query(query, SOCIAL_DOMAINS), max_results, region, query
    )
    return news + social, "web"


def _ddgs_query(
    query: str,
    max_results: int,
    region: str,
    match: str = "",
) -> tuple[list[SearchHit], str]:
    """Una consulta ddgs (auto). Incluye notas cuyo nombre está en la descripción."""
    from ddgs import DDGS

    try:
        rows = DDGS(timeout=15).text(
            query,
            max_results=max_results,
            region=region,
            backend="auto",
        )
    except Exception:  # noqa: BLE001 — el caller prueba otro medio
        return [], "web"
    hits: list[SearchHit] = []
    for row in rows or []:
        hit = _hit(
            row.get("title") or "",
            row.get("href") or row.get("url") or "",
            row.get("body") or row.get("snippet") or "",
        )
        if not hit:
            continue
        if not match:
            hits.append(hit)
        elif hit_matches(match, hit.title, hit.snippet, hit.url):
            hits.append(hit)
        elif _token_hits(match, hit.title, hit.snippet, hit.url) >= 2:
            # Candidato a abrir la nota: la bajada real puede tener el nombre.
            hits.append(hit)
    return hits, "web"


def _hit(title: str, url: str, snippet: str) -> SearchHit | None:
    """Descarta URLs que no sean http(s) o que no estén en la lista de medios."""
    cleaned = (url or "").strip()
    parsed = urlparse(cleaned)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        return None
    if not is_allowed_url(cleaned):
        return None
    path = parsed.path.lower()
    if path.endswith(_SKIP_PATH):
        return None
    host = parsed.netloc.lower()
    if host.startswith("cdn.") or host.startswith("www.cdn."):
        return None
    return SearchHit(title=title.strip() or cleaned, url=cleaned, snippet=snippet.strip())
