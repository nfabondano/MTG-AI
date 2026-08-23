"""Shared HTTP client for every upstream source.

Three of the four APIs we use (Archidekt, EDHREC, Commander Spellbook) are
unofficial and unsupported. This module is the politeness contract with all of
them: a descriptive User-Agent, per-host throttling, bounded retries, and an
on-disk cache so repeated analysis costs no network at all.
"""

from __future__ import annotations

import hashlib
import json
import time
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

import httpx

from .config import (
    CACHE_TTL,
    DEFAULT_CACHE_TTL,
    DEFAULT_RATE_LIMIT,
    RATE_LIMITS,
    USER_AGENT,
    http_cache_dir,
)


class SourceError(RuntimeError):
    """An upstream source failed in a way the caller should handle."""

    def __init__(self, message: str, *, status: int | None = None, url: str = ""):
        super().__init__(message)
        self.status = status
        self.url = url


class NotFound(SourceError):
    """404 — the resource genuinely does not exist."""


class Forbidden(SourceError):
    """403 — the endpoint is gated. Retrying will not help.

    EDHREC gates several of its JSON paths this way; callers degrade rather
    than fail.
    """


_last_request_at: dict[str, float] = {}


def _throttle(host: str) -> None:
    delay = RATE_LIMITS.get(host, DEFAULT_RATE_LIMIT)
    if delay <= 0:
        return
    last = _last_request_at.get(host)
    if last is not None:
        elapsed = time.monotonic() - last
        if elapsed < delay:
            time.sleep(delay - elapsed)
    _last_request_at[host] = time.monotonic()


def _cache_path(method: str, url: str, body: str | None) -> Path:
    key = hashlib.sha256(f"{method} {url} {body or ''}".encode()).hexdigest()
    return http_cache_dir() / f"{key}.json"


def _read_cache(path: Path, ttl: float) -> Any | None:
    if not path.exists():
        return None
    if ttl >= 0 and time.time() - path.stat().st_mtime > ttl:
        return None
    try:
        return json.loads(path.read_text())
    except (json.JSONDecodeError, OSError):
        return None


def _write_cache(path: Path, payload: Any) -> None:
    try:
        # Write-then-rename so a crash mid-write cannot leave a corrupt entry.
        tmp = path.with_suffix(".tmp")
        tmp.write_text(json.dumps(payload))
        tmp.replace(path)
    except OSError:
        pass  # a cache write failure must never break a request


def get_json(
    url: str,
    *,
    params: dict[str, Any] | None = None,
    use_cache: bool = True,
    ttl: float | None = None,
    max_retries: int = 3,
    timeout: float = 30.0,
) -> Any:
    """GET a JSON document, with caching, throttling and bounded retries."""
    return _request_json(
        "GET",
        url,
        params=params,
        use_cache=use_cache,
        ttl=ttl,
        max_retries=max_retries,
        timeout=timeout,
    )


def post_json(
    url: str,
    payload: dict[str, Any],
    *,
    use_cache: bool = True,
    ttl: float | None = None,
    max_retries: int = 3,
    timeout: float = 30.0,
) -> Any:
    """POST a JSON body and decode the JSON response.

    Used for Scryfall's /cards/collection batch endpoint, which is a POST but
    is semantically a read, so it is safe to cache.
    """
    return _request_json(
        "POST",
        url,
        json_body=payload,
        use_cache=use_cache,
        ttl=ttl,
        max_retries=max_retries,
        timeout=timeout,
    )


def _request_json(
    method: str,
    url: str,
    *,
    params: dict[str, Any] | None = None,
    json_body: dict[str, Any] | None = None,
    use_cache: bool = True,
    ttl: float | None = None,
    max_retries: int = 3,
    timeout: float = 30.0,
) -> Any:
    host = urlparse(url).netloc
    effective_ttl = ttl if ttl is not None else CACHE_TTL.get(host, DEFAULT_CACHE_TTL)

    full_url = str(httpx.URL(url, params=params)) if params else url
    body_key = json.dumps(json_body, sort_keys=True) if json_body else None
    cache_file = _cache_path(method, full_url, body_key)

    if use_cache:
        cached = _read_cache(cache_file, effective_ttl)
        if cached is not None:
            return cached

    headers = {"User-Agent": USER_AGENT, "Accept": "application/json"}
    last_error: Exception | None = None

    for attempt in range(max_retries):
        _throttle(host)
        try:
            with httpx.Client(timeout=timeout, follow_redirects=True) as client:
                response = client.request(
                    method, url, params=params, json=json_body, headers=headers
                )
        except httpx.HTTPError as exc:
            last_error = exc
            if attempt < max_retries - 1:
                time.sleep(2**attempt)
                continue
            raise SourceError(f"network error for {url}: {exc}", url=url) from exc

        # 403/404 are terminal: the endpoint is gated or the resource is gone.
        # Retrying wastes time and is rude to an unofficial API.
        if response.status_code == 404:
            raise NotFound(f"not found: {url}", status=404, url=url)
        if response.status_code == 403:
            raise Forbidden(f"forbidden (gated endpoint): {url}", status=403, url=url)

        if response.status_code == 429 or response.status_code >= 500:
            last_error = SourceError(
                f"HTTP {response.status_code} from {url}",
                status=response.status_code,
                url=url,
            )
            if attempt < max_retries - 1:
                retry_after = response.headers.get("Retry-After")
                wait = float(retry_after) if retry_after and retry_after.isdigit() else 2**attempt
                time.sleep(wait)
                continue
            raise last_error

        if response.status_code >= 400:
            raise SourceError(
                f"HTTP {response.status_code} from {url}: {response.text[:200]}",
                status=response.status_code,
                url=url,
            )

        try:
            payload = response.json()
        except json.JSONDecodeError as exc:
            raise SourceError(f"non-JSON response from {url}", url=url) from exc

        if use_cache:
            _write_cache(cache_file, payload)
        return payload

    raise SourceError(f"exhausted retries for {url}: {last_error}", url=url)


def download(url: str, dest: Path, *, timeout: float = 300.0) -> Path:
    """Stream a large file to disk (the Scryfall bulk download)."""
    host = urlparse(url).netloc
    _throttle(host)
    dest.parent.mkdir(parents=True, exist_ok=True)
    tmp = dest.with_suffix(dest.suffix + ".part")
    headers = {"User-Agent": USER_AGENT}
    with httpx.Client(timeout=timeout, follow_redirects=True) as client:
        with client.stream("GET", url, headers=headers) as response:
            if response.status_code >= 400:
                raise SourceError(
                    f"HTTP {response.status_code} downloading {url}",
                    status=response.status_code,
                    url=url,
                )
            with tmp.open("wb") as fh:
                for chunk in response.iter_bytes(chunk_size=1 << 16):
                    fh.write(chunk)
    tmp.replace(dest)
    return dest
