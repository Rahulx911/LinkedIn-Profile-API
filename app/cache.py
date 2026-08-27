import time
from typing import Any


class TTLCache:
    """Minimal in-memory TTL cache. Not shared across processes — fine for a
    single-instance deployment, and its main job here is cutting down on
    repeat live calls to LinkedIn for the same profile (rate-limit / ban risk)."""

    def __init__(self, ttl_seconds: int):
        self._ttl = ttl_seconds
        self._store: dict[str, tuple[float, Any]] = {}

    def get(self, key: str) -> Any | None:
        entry = self._store.get(key)
        if entry is None:
            return None
        expires_at, value = entry
        if time.monotonic() > expires_at:
            del self._store[key]
            return None
        return value

    def set(self, key: str, value: Any) -> None:
        self._store[key] = (time.monotonic() + self._ttl, value)
