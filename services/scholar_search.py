from __future__ import annotations

import json
import os
import re
import urllib.error
import urllib.parse
import urllib.request
from typing import Any

from core.version import APP_VERSION


CROSSREF_WORKS_URL = "https://api.crossref.org/works"


class ScholarSearchError(RuntimeError):
    """Raised when the public scholarly metadata service cannot be reached."""


def _first_text(value: Any) -> str:
    if isinstance(value, list):
        for item in value:
            text = str(item or "").strip()
            if text:
                return text
        return ""
    return str(value or "").strip()


def _year(item: dict[str, Any]) -> int | str:
    for key in ("published-print", "published-online", "published", "issued", "created"):
        value = item.get(key)
        if not isinstance(value, dict):
            continue
        parts = value.get("date-parts")
        if (
            isinstance(parts, list)
            and parts
            and isinstance(parts[0], list)
            and parts[0]
        ):
            try:
                return int(parts[0][0])
            except (TypeError, ValueError):
                continue
    return ""


def _authors(item: dict[str, Any], limit: int = 5) -> str:
    names: list[str] = []
    for author in item.get("author", []) if isinstance(item.get("author"), list) else []:
        if not isinstance(author, dict):
            continue
        family = str(author.get("family") or "").strip()
        given = str(author.get("given") or "").strip()
        name = " ".join(part for part in (given, family) if part)
        if name:
            names.append(name)
        if len(names) >= limit:
            break
    return ", ".join(names)


def _nonnegative_int(value: Any) -> int:
    try:
        return max(0, int(value or 0))
    except (TypeError, ValueError):
        return 0


def _safe_http_url(value: Any) -> str:
    url = str(value or "").strip()
    parsed = urllib.parse.urlparse(url)
    return url if parsed.scheme in {"http", "https"} and parsed.netloc else ""


def _normalize_item(item: dict[str, Any]) -> dict[str, Any] | None:
    title = _first_text(item.get("title"))
    if not title:
        return None
    doi = str(item.get("DOI") or "").strip()
    url = (
        f"https://doi.org/{urllib.parse.quote(doi, safe='/().:;-_')}"
        if doi
        else _safe_http_url(item.get("URL"))
    )
    return {
        "provider": "Crossref",
        "external_id": doi or url or f"{title}|{_year(item)}",
        "title": title,
        "year": _year(item),
        "authors": _authors(item),
        "source": _first_text(item.get("container-title")),
        "doi": doi,
        "cited_by": _nonnegative_int(item.get("is-referenced-by-count")),
        "url": url,
        "work_type": str(item.get("type") or ""),
        "open_access": bool(item.get("license")),
    }


def parse_crossref_payload(payload: dict[str, Any], limit: int = 10) -> list[dict[str, Any]]:
    message = payload.get("message")
    items = message.get("items", []) if isinstance(message, dict) else []
    results: list[dict[str, Any]] = []
    seen: set[str] = set()
    for raw in items if isinstance(items, list) else []:
        if not isinstance(raw, dict):
            continue
        item = _normalize_item(raw)
        if not item:
            continue
        identity = str(item["external_id"]).casefold()
        if identity in seen:
            continue
        seen.add(identity)
        results.append(item)
        if len(results) >= limit:
            break
    return results


def search_works(query: str, *, limit: int = 10, timeout: float = 10.0) -> list[dict[str, Any]]:
    """Search scholarly metadata without requiring a user API key.

    Crossref is intentionally used as the default because its public REST API
    requires no account. The app sends a fixed endpoint request only; user
    input is encoded as a query parameter and can never select an arbitrary
    host.
    """

    search = re.sub(r"\s+", " ", str(query or "")).strip()
    if len(search) < 2:
        raise ValueError("检索词至少需要 2 个字符。")
    rows = max(1, min(int(limit or 10), 20))
    params: dict[str, str | int] = {
        "query.bibliographic": search[:500],
        "rows": rows,
        "sort": "relevance",
        "order": "desc",
    }
    contact = os.environ.get("RESEARCH_OS_CONTACT_EMAIL", "").strip()
    if contact and re.fullmatch(r"[^@\s]+@[^@\s]+\.[^@\s]+", contact):
        params["mailto"] = contact[:200]
    request = urllib.request.Request(
        f"{CROSSREF_WORKS_URL}?{urllib.parse.urlencode(params)}",
        headers={
            "Accept": "application/json",
            "User-Agent": (
                f"ResearchCultivationOS/{APP_VERSION} "
                f"(local research tool{f'; mailto:{contact}' if contact else ''})"
            ),
        },
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            payload = json.load(response)
    except urllib.error.HTTPError as exc:
        raise ScholarSearchError(f"论文索引返回 HTTP {exc.code}") from exc
    except (urllib.error.URLError, TimeoutError, json.JSONDecodeError) as exc:
        raise ScholarSearchError("无法连接论文索引，请检查网络后重试") from exc
    if not isinstance(payload, dict):
        raise ScholarSearchError("论文索引返回了无法识别的数据。")
    return parse_crossref_payload(payload, limit=rows)
