"""Tests para la lógica de rate limiting (ventana deslizante por IP)."""
import sys
import time
import collections
import threading
import unittest
from pathlib import Path

# Reimplementamos la misma lógica que main.py para testearla aislada,
# sin tener que importar FastAPI ni cargar todos los datos del backend.
RATE_LIMIT  = 3
RATE_WINDOW = 2   # 2 segundos para que los tests corran rápido

_store: dict = {}
_lock = threading.Lock()


def check(ip: str, limit=RATE_LIMIT, window=RATE_WINDOW):
    now = time.monotonic()
    with _lock:
        ts = _store.setdefault(ip, collections.deque())
        while ts and now - ts[0] > window:
            ts.popleft()
        if len(ts) >= limit:
            retry = int(window - (now - ts[0])) + 1
            return False, retry
        ts.append(now)
        return True, 0


def reset():
    with _lock:
        _store.clear()


class TestRateLimit(unittest.TestCase):

    def setUp(self):
        reset()

    # ── casos normales ─────────────────────────────────────────────────────

    def test_first_request_allowed(self):
        allowed, _ = check("1.2.3.4")
        self.assertTrue(allowed)

    def test_requests_under_limit_all_allowed(self):
        for _ in range(RATE_LIMIT):
            allowed, _ = check("1.2.3.4")
            self.assertTrue(allowed)

    def test_request_at_limit_blocked(self):
        for _ in range(RATE_LIMIT):
            check("1.2.3.4")
        allowed, retry = check("1.2.3.4")
        self.assertFalse(allowed)
        self.assertGreater(retry, 0)

    def test_retry_after_is_positive(self):
        for _ in range(RATE_LIMIT):
            check("1.2.3.4")
        _, retry = check("1.2.3.4")
        self.assertGreaterEqual(retry, 1)

    # ── aislamiento por IP ─────────────────────────────────────────────────

    def test_different_ips_independent(self):
        for _ in range(RATE_LIMIT):
            check("1.1.1.1")
        # IP distinta no afectada
        allowed, _ = check("2.2.2.2")
        self.assertTrue(allowed)

    def test_same_ip_across_calls(self):
        check("5.5.5.5")
        check("5.5.5.5")
        check("5.5.5.5")
        allowed, _ = check("5.5.5.5")
        self.assertFalse(allowed)

    # ── ventana deslizante ─────────────────────────────────────────────────

    def test_resets_after_window(self):
        for _ in range(RATE_LIMIT):
            check("3.3.3.3")
        allowed, _ = check("3.3.3.3")
        self.assertFalse(allowed)

        time.sleep(RATE_WINDOW + 0.1)   # esperar que expire la ventana

        allowed, _ = check("3.3.3.3")
        self.assertTrue(allowed)

    def test_sliding_window_partial_reset(self):
        """Sólo expiran los timestamps fuera de la ventana, no todos."""
        check("4.4.4.4")          # t=0
        time.sleep(RATE_WINDOW * 0.6)
        check("4.4.4.4")          # t=0.6w (aún en ventana)
        check("4.4.4.4")          # t=0.6w
        # A t=0.6w: 3 requests → bloqueado
        allowed, _ = check("4.4.4.4")
        self.assertFalse(allowed)

        time.sleep(RATE_WINDOW * 0.5)
        # A t=1.1w: el primer request (t=0) expiró, quedan 2 → permitido
        allowed, _ = check("4.4.4.4")
        self.assertTrue(allowed)


if __name__ == "__main__":
    unittest.main()
