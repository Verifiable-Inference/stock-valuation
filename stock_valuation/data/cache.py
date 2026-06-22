"""Simple on-disk JSON cache for API responses."""

from __future__ import annotations

import hashlib
import json
import os
import time
from typing import Any, Callable, Optional

_DEFAULT_CACHE_DIR = os.path.join(
    os.path.expanduser("~"), ".cache", "stock_valuation"
)


class JsonCache:
    """File-backed cache keyed by an arbitrary string.

    Entries older than ``ttl_seconds`` are treated as stale and refetched.
    """

    def __init__(
        self,
        cache_dir: Optional[str] = None,
        ttl_seconds: int = 24 * 3600,
        enabled: bool = True,
    ) -> None:
        self.cache_dir = cache_dir or _DEFAULT_CACHE_DIR
        self.ttl_seconds = ttl_seconds
        self.enabled = enabled
        if self.enabled:
            os.makedirs(self.cache_dir, exist_ok=True)

    def _path(self, key: str) -> str:
        digest = hashlib.sha256(key.encode("utf-8")).hexdigest()[:32]
        return os.path.join(self.cache_dir, f"{digest}.json")

    def get(self, key: str) -> Optional[Any]:
        if not self.enabled:
            return None
        path = self._path(key)
        if not os.path.exists(path):
            return None
        if (time.time() - os.path.getmtime(path)) > self.ttl_seconds:
            return None
        try:
            with open(path, "r") as fh:
                return json.load(fh)
        except (json.JSONDecodeError, OSError):
            return None

    def set(self, key: str, value: Any) -> None:
        if not self.enabled:
            return
        try:
            with open(self._path(key), "w") as fh:
                json.dump(value, fh)
        except (OSError, TypeError):
            pass

    def get_or_fetch(self, key: str, fetch: Callable[[], Any]) -> Any:
        cached = self.get(key)
        if cached is not None:
            return cached
        value = fetch()
        self.set(key, value)
        return value
