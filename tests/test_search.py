"""Tests de intención de búsqueda y del cliente Google CSE (sin red real)."""

from __future__ import annotations

import httpx
import pytest

from ollama_chat.config import Settings
from ollama_chat.services.search import (
    SearchError,
    SearchHit,
    _enrich_news,
    _hit,
    _mix,
    _parse_article,
    excerpt_around_query,
    extract_search_query,
    format_search_context,
    hit_matches,
    is_allowed_url,
    query_matches,
    search_web,
)


@pytest.mark.parametrize(
    ("text", "forced", "expected"),
    [
        ("busca tutorial fastapi", False, "tutorial fastapi"),
        ("Búscame en google ollama", False, "ollama"),
        ("/search python asyncio", False, "python asyncio"),
        ("google python", False, "python"),
        ("dame los enlaces de django rest", False, "django rest"),
        ("¿puedes buscar documentación de ollama?", False, "documentación de ollama"),
        ("hola qué tal", False, None),
        ("hola qué tal", True, "hola qué tal"),
        ("busca", False, None),
        ("", False, None),
        ("", True, None),
    ],
)
def test_extract_search_query(text, forced, expected):
    assert extract_search_query(text, forced=forced) == expected


@pytest.mark.parametrize(
    ("url", "ok"),
    [
        ("https://www.laprensa.hn/sucesos/nota", True),
        ("https://elheraldo.hn/elheraldoplus/x", True),
        ("https://www.latribuna.hn/2024/01/01/x", True),
        ("https://www.elpais.hn/nacionales/", True),
        ("https://hondudiario.com/politica/x", True),
        ("https://proceso.hn/nacionales/x", True),
        ("https://ellibertador.hn/x", True),
        ("https://hch.tv/nacionales/x", True),
        ("https://canal11.hn/x", True),
        ("https://m.facebook.com/story.php?story_fbid=1", True),
        ("https://www.instagram.com/p/abc/", True),
        ("https://www.linkedin.com/posts/x", True),
        ("https://www.elpais.com/internacional/", False),
        ("https://fastapi.tiangolo.com/", False),
        ("https://cnn.com/world", False),
        ("javascript:alert(1)", False),
    ],
)
def test_is_allowed_url(url, ok):
    assert is_allowed_url(url) is ok


_HERALDO_TITLE = "Cae joven que tenía unas 50 denuncias por estafas en la capital"
_HERALDO_DESC = (
    "El imputado es Jimy Cristopher Mairena Banegas, de 29 años de edad, "
    "originario de Tegucigalpa, con domicilio en la residencial Santa Clara. "
    "Él tenía una orden de captura."
)
_HERALDO_NAME = "Jimy Cristopher Mairena Banegas"


def test_hit_skips_images_and_keeps_articles():
    assert _hit("x", "https://cdn.latribuna.hn/foto.avif", "texto") is None
    hit = _hit(
        _HERALDO_TITLE,
        "https://www.elheraldo.hn/sucesos/cae-joven-que-tenia",
        _HERALDO_DESC,
    )
    assert hit is not None
    assert hit.url.startswith("https://www.elheraldo.hn/")


def test_query_matches_name_in_description_not_title():
    """El nombre está en la bajada, no en el titular — igual debe contar."""
    assert not query_matches(_HERALDO_NAME, _HERALDO_TITLE)
    assert query_matches(_HERALDO_NAME, _HERALDO_DESC)
    excerpt = excerpt_around_query(_HERALDO_NAME, _HERALDO_DESC)
    assert "Jimy Cristopher Mairena Banegas" in excerpt


def test_hit_matches_title_or_description_or_url_not_both():
    """Basta un campo: no se exige título Y descripción a la vez."""
    url = "https://www.elheraldo.hn/sucesos/cae-joven-que-tenia"
    url_with_name = (
        "https://www.elheraldo.hn/sucesos/jimy-cristopher-mairena-banegas-estafas"
    )
    # Solo título (bajada vacía) → aparece
    assert hit_matches(
        "estafas denuncias capital",
        title=_HERALDO_TITLE,
        snippet="",
        url=url,
    )
    # Solo descripción (título no trae el nombre) → aparece
    assert hit_matches(_HERALDO_NAME, title=_HERALDO_TITLE, snippet=_HERALDO_DESC, url=url)
    # Solo el slug del link, sin titular ni bajada → aparece
    assert hit_matches(_HERALDO_NAME, title="Sucesos", snippet="", url=url_with_name)
    # En ningún campo → no aparece
    assert not hit_matches(
        _HERALDO_NAME, title=_HERALDO_TITLE, snippet="Hechos en Tegucigalpa", url=url
    )


def test_parse_article_reads_meta_description():
    html = f"""
    <html><head>
      <title>{_HERALDO_TITLE}</title>
      <meta name="description" content="{_HERALDO_DESC}" />
    </head>
    <body><p>{_HERALDO_DESC}</p></body></html>
    """
    title, desc, body = _parse_article(html)
    assert "Cae joven" in title
    assert "Jimy Cristopher Mairena Banegas" in desc
    assert "Jimy" in body


@pytest.mark.asyncio
async def test_enrich_keeps_title_match_without_description():
    """Si el nombre va en el título y la bajada viene vacía, el link igual sale."""
    hits = [
        SearchHit(
            "Capturan a Jimy Cristopher Mairena Banegas en la capital",
            "https://www.elheraldo.hn/sucesos/capturan-jimy",
            "",
        )
    ]
    out = await _enrich_news(_HERALDO_NAME, hits, None)
    assert len(out) == 1
    assert out[0].url.endswith("capturan-jimy")


@pytest.mark.asyncio
async def test_enrich_uses_description_when_title_lacks_name(monkeypatch):
    html = f"""
    <html><head>
      <title>{_HERALDO_TITLE}</title>
      <meta property="og:description" content="{_HERALDO_DESC}" />
    </head></html>
    """

    async def fake_fetch(url, http):
        assert "elheraldo.hn" in url
        return _parse_article(html)

    monkeypatch.setattr("ollama_chat.services.search._fetch_article", fake_fetch)
    hits = [
        SearchHit(
            _HERALDO_TITLE,
            "https://www.elheraldo.hn/sucesos/cae-joven-que-tenia",
            "",
        )
    ]
    out = await _enrich_news(_HERALDO_NAME, hits, None)
    assert len(out) == 1
    assert "Jimy Cristopher Mairena Banegas" in out[0].snippet


@pytest.mark.asyncio
async def test_enrich_drops_notes_that_never_mention_the_name(monkeypatch):
    async def fake_fetch(url, http):
        return ("Otra nota", "Hechos distintos en San Pedro Sula", "Sin relación")

    monkeypatch.setattr("ollama_chat.services.search._fetch_article", fake_fetch)
    hits = [SearchHit("Otra nota", "https://www.elheraldo.hn/sucesos/otra", "")]
    out = await _enrich_news(_HERALDO_NAME, hits, None)
    assert out == []


def test_mix_prefers_news_over_social():
    news = [SearchHit(f"n{i}", f"https://www.laprensa.hn/{i}") for i in range(8)]
    social = [SearchHit(f"s{i}", f"https://www.facebook.com/{i}") for i in range(8)]
    mixed = _mix(news, social, 8)
    news_n = sum(1 for hit in mixed if "laprensa.hn" in hit.url)
    social_n = sum(1 for hit in mixed if "facebook.com" in hit.url)
    assert len(mixed) == 8
    assert news_n >= 6
    assert social_n >= 1


def test_format_search_context_lists_urls():
    hits = [
        SearchHit("FastAPI", "https://fastapi.tiangolo.com/", "framework"),
    ]
    text = format_search_context("fastapi", hits)
    assert "https://fastapi.tiangolo.com/" in text
    assert "no inventes" in text.lower()


@pytest.mark.asyncio
async def test_google_cse_search():
    """Con API key + cx usa Custom Search y no inventa el link."""
    settings = Settings(
        google_api_key="k",
        google_cse_id="cx",
        search_max_results=3,
    )

    def handler(request: httpx.Request) -> httpx.Response:
        assert "googleapis.com/customsearch/v1" in str(request.url)
        q = request.url.params["q"]
        assert "congreso" in q
        assert "site:" in q
        assert request.url.params["cx"] == "cx"
        if "facebook.com" in q:
            return httpx.Response(
                200,
                json={
                    "items": [
                        {
                            "title": "La Prensa HN",
                            "link": "https://www.facebook.com/laprensahn",
                            "snippet": "Publicación sobre el congreso nacional",
                        }
                    ]
                },
            )
        return httpx.Response(
            200,
            json={
                "items": [
                    {
                        "title": "Congreso",
                        "link": "https://www.laprensa.hn/sucesos/congreso",
                        "snippet": "nota",
                    },
                    {
                        "title": "CNN",
                        "link": "https://edition.cnn.com/honduras",
                        "snippet": "fuera",
                    },
                ]
            },
        )

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
        outcome = await search_web("congreso", settings, http=http)
    assert outcome.provider == "google"
    urls = [h.url for h in outcome.hits]
    assert "https://www.laprensa.hn/sucesos/congreso" in urls
    assert "https://www.facebook.com/laprensahn" in urls
    assert all("cnn.com" not in u for u in urls)


@pytest.mark.asyncio
async def test_google_cse_error_falls_back_to_ddgs(monkeypatch):
    settings = Settings(google_api_key="k", google_cse_id="cx")
    hit = SearchHit("Heraldo ollama", "https://www.elheraldo.hn/nota", "artículo sobre ollama")
    monkeypatch.setattr(
        "ollama_chat.services.search._ddgs_restricted",
        lambda *args, **kwargs: ([hit], "google"),
    )

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(403, text="quota")

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
        outcome = await search_web("ollama", settings, http=http)
    assert outcome.hits[0].url == "https://www.elheraldo.hn/nota"


@pytest.mark.asyncio
async def test_search_empty_query_raises():
    with pytest.raises(SearchError):
        await search_web("  ", Settings())
