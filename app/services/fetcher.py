import re
from typing import Any

import httpx
from bs4 import BeautifulSoup

from app.core.config import get_settings


def _clean_text(text: str) -> str:
    text = re.sub(r"\s+", " ", text)
    return text.strip()


def fetch_webpage_text(url: str) -> str:
    settings = get_settings()
    try:
        with httpx.Client(timeout=settings.fetch_timeout_seconds, follow_redirects=True) as client:
            resp = client.get(url)
            resp.raise_for_status()
            html = resp.text
    except Exception:
        return ""

    soup = BeautifulSoup(html, "html.parser")

    for tag in soup(["script", "style", "noscript"]):
        tag.decompose()

    text = _clean_text(soup.get_text(" "))
    if len(text) > 5000:
        text = text[:5000]
    return text


def enrich_search_results(items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    enriched: list[dict[str, Any]] = []
    for item in items:
        url = item.get("url", "")
        page_text = fetch_webpage_text(url) if url else ""
        enriched.append({**item, "page_text": page_text})
    return enriched
