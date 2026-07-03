"""
TTL Disk Cache
--------------
A tiny, dependency-free JSON cache with per-key time-to-live.

Why this exists (council Phase 3 — durability):
  * ScoutAI cached nothing between runs except an in-process EDGAR ticker list,
    so re-analysing the same company re-hit every rate-limited endpoint from
    scratch, burning the shared HuggingFace-IP rate budget.
  * HuggingFace Spaces free-tier disk is EPHEMERAL — it is wiped on every rebuild.
    So this cache is a best-effort *latency + rate-limit* optimisation, NOT a
    source of truth. Persistence-across-rebuilds belongs to the nightly HF-Dataset
    precompute job, not here. Never rely on a cache hit existing.

Design:
  * One JSON file per namespace under CACHE_DIR (default: system temp).
  * Each entry stores {"ts": <epoch>, "value": <json>}. Reads past TTL miss.
  * Fails open: any I/O or JSON error behaves as a cache miss and never raises.
"""

import json
import logging
import os
import tempfile
import threading
import time
from typing import Any, Callable, Optional

logger = logging.getLogger(__name__)

# HF Spaces disk is ephemeral; a temp dir is the right home for a throwaway cache.
CACHE_DIR = os.environ.get("SCOUTAI_CACHE_DIR", os.path.join(tempfile.gettempdir(), "scoutai_cache"))

# Serialise writes within a process so two threads don't clobber the same file.
_LOCK = threading.Lock()


def _path(namespace: str) -> str:
    safe = "".join(c if c.isalnum() or c in "-_" else "_" for c in namespace)
    return os.path.join(CACHE_DIR, f"{safe}.json")


def _load(namespace: str) -> dict:
    try:
        with open(_path(namespace), "r", encoding="utf-8") as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError, OSError):
        return {}


def _store(namespace: str, data: dict) -> None:
    try:
        os.makedirs(CACHE_DIR, exist_ok=True)
        tmp = _path(namespace) + f".tmp.{os.getpid()}.{threading.get_ident()}"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(data, f)
        os.replace(tmp, _path(namespace))   # atomic on POSIX
    except OSError as e:
        logger.debug(f"Cache write failed for {namespace}: {e}")


def get(namespace: str, key: str, ttl_seconds: float) -> Optional[Any]:
    """Return the cached value for key if present and younger than ttl_seconds, else None."""
    entry = _load(namespace).get(key)
    if not entry:
        return None
    if time.time() - entry.get("ts", 0) > ttl_seconds:
        return None
    return entry.get("value")


def set(namespace: str, key: str, value: Any) -> None:
    """Store value under key. JSON-serialisable values only; failures are swallowed."""
    with _LOCK:
        data = _load(namespace)
        data[key] = {"ts": time.time(), "value": value}
        _store(namespace, data)


def cached(namespace: str, key: str, ttl_seconds: float, producer: Callable[[], Any]) -> Any:
    """
    Return the cached value, or call producer(), cache its result, and return it.
    A producer that returns None or raises is NOT cached (so transient failures
    don't poison the cache with an empty result until the TTL expires).
    """
    hit = get(namespace, key, ttl_seconds)
    if hit is not None:
        logger.debug(f"cache hit {namespace}/{key}")
        return hit
    try:
        value = producer()
    except Exception as e:
        logger.debug(f"cache producer raised for {namespace}/{key}: {e}")
        return None
    if value is not None:
        set(namespace, key, value)
    return value
