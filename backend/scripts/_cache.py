"""
_cache.py
Cache en memoria con TTL para respuestas de APIs externas.

Diseño deliberadamente simple para el MVP en Railway (un solo proceso):
- dict module-level protegido con threading.Lock
- Clave = hash MD5 de los argumentos de la query
- TTL configurable por llamada
- Sin límite de tamaño (los payloads son chicos: JSON de elevación,
  bytes de NDVI, bytes de PNG del mapa)

Si Railway escala a múltiples workers, migrar a Redis.
"""

import hashlib
import json
import threading
import time
from typing import Any

_store: dict[str, tuple[float, Any]] = {}   # key → (timestamp, payload)
_lock  = threading.Lock()


def _key(*args, **kwargs) -> str:
    """Genera clave determinista a partir de cualquier combinación de args."""
    raw = json.dumps({"a": args, "k": kwargs}, sort_keys=True, default=str)
    return hashlib.md5(raw.encode()).hexdigest()


def get(ttl: int, *args, **kwargs) -> Any | None:
    """
    Busca en cache. Retorna el payload si existe y no expiró, None si no.

    ttl: segundos de vida del entry.
    args/kwargs: identifican la query (coordenadas, parámetros, etc.)
    """
    k = _key(*args, **kwargs)
    with _lock:
        entry = _store.get(k)
        if entry is None:
            return None
        ts, payload = entry
        if time.monotonic() - ts > ttl:
            del _store[k]
            return None
        return payload


def put(payload: Any, *args, **kwargs) -> None:
    """Guarda payload en cache. Mismos args/kwargs que get()."""
    k = _key(*args, **kwargs)
    with _lock:
        _store[k] = (time.monotonic(), payload)


def size() -> int:
    """Cantidad de entries vivos (incluye algunos expirados no limpiados aún)."""
    with _lock:
        return len(_store)


def clear() -> None:
    """Vacía el cache — útil para tests."""
    with _lock:
        _store.clear()


# TTLs recomendados (en segundos)
TTL_ELEVATION  = 30 * 24 * 3600   # 30 días — terreno no cambia
TTL_NDVI       =  7 * 24 * 3600   # 7 días  — Sentinel-2 revisit ~5 días
TTL_MAP_RENDER =  7 * 24 * 3600   # 7 días  — tiles CARTO casi estáticos
