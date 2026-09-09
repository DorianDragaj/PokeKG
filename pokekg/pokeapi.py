"""Cache-backed PokeAPI client.

Responses are cached under data/raw/cache/ keyed by URL path, so a second run
of the extractor makes no network requests. PokeAPI asks consumers to cache
(https://pokeapi.co/docs/v2#fairuse), and the cached JSON also serves as the
provenance record for every triple in the KG.

PokeAPI publishes no rate limit but drops connections under heavy parallel
load, hence the eight workers and the backoff on 429/5xx.
"""
from __future__ import annotations

import json
import random
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any, Callable, Sequence

import requests

BASE_URL = "https://pokeapi.co/api/v2"

MAX_WORKERS = 8
MAX_RETRIES = 5
BACKOFF_BASE = 1.5


class PokeAPIError(RuntimeError):
    pass


class PokeAPIClient:
    def __init__(self, cache_dir: Path, offline: bool = False) -> None:
        self.cache_dir = Path(cache_dir)
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        self.offline = offline
        self.session = requests.Session()
        self.session.headers["User-Agent"] = (
            "pokemon-kg/1.0 (TU Wien Knowledge Graphs coursework)"
        )
        self.hits = 0
        self.misses = 0

    def _cache_path(self, endpoint: str) -> Path:
        return self.cache_dir / (endpoint.strip("/").replace("/", "__") + ".json")

    def get(self, endpoint: str) -> dict[str, Any]:
        """GET BASE_URL/endpoint, from disk if it is already cached."""
        path = self._cache_path(endpoint)
        if path.exists():
            self.hits += 1
            return json.loads(path.read_text(encoding="utf-8"))
        if self.offline:
            raise PokeAPIError(f"offline mode, but {endpoint} is not cached")

        payload = self._get_with_retry(f"{BASE_URL}/{endpoint}")
        path.write_text(json.dumps(payload), encoding="utf-8")
        self.misses += 1
        return payload

    def _get_with_retry(self, url: str) -> dict[str, Any]:
        last_error: Exception | None = None
        for attempt in range(MAX_RETRIES):
            try:
                response = self.session.get(url, timeout=30)
                if response.status_code == 429 or response.status_code >= 500:
                    raise PokeAPIError(f"HTTP {response.status_code}")
                response.raise_for_status()
                return response.json()
            except (requests.RequestException, PokeAPIError, ValueError) as exc:
                last_error = exc
                if attempt == MAX_RETRIES - 1:
                    break
                # Jitter keeps workers throttled together from retrying in lockstep.
                time.sleep(BACKOFF_BASE**attempt + random.uniform(0, 0.4))
        raise PokeAPIError(f"giving up on {url} after {MAX_RETRIES} tries: {last_error}")

    def map(
        self,
        fn: Callable[[Any], Any],
        items: Sequence[Any],
        label: str = "",
    ) -> list[Any]:
        """Run fn over items with bounded concurrency, preserving order."""
        if label:
            print(f"  {label}: {len(items)} request(s)...", end="", flush=True)
        started = time.time()
        with ThreadPoolExecutor(max_workers=MAX_WORKERS) as pool:
            results = list(pool.map(fn, items))
        if label:
            print(f" done in {time.time() - started:.1f}s")
        return results

    def stats(self) -> str:
        total = self.hits + self.misses
        return (
            f"{total} API document(s): {self.hits} from cache, "
            f"{self.misses} downloaded"
        )


def name_from_url(url: str) -> str:
    """'https://pokeapi.co/api/v2/type/4/' -> '4'"""
    return url.rstrip("/").rsplit("/", 1)[-1]
