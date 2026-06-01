"""Tests para _cache.py — cache en memoria con TTL."""
import sys
import time
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "scripts"))
from _cache import get, put, clear, size


class TestCache(unittest.TestCase):

    def setUp(self):
        clear()

    # ── get / put básico ───────────────────────────────────────────────────

    def test_miss_on_empty(self):
        self.assertIsNone(get(3600, "key", 1))

    def test_hit_after_put(self):
        put("valor", "key", 1)
        self.assertEqual(get(3600, "key", 1), "valor")

    def test_miss_different_args(self):
        put("valor", "key", 1)
        self.assertIsNone(get(3600, "key", 2))   # arg distinto
        self.assertIsNone(get(3600, "otra", 1))   # key distinta

    def test_payload_types(self):
        """Cachear dict, lista, bytes, int."""
        put({"a": 1}, "dict_key")
        put([1, 2, 3], "list_key")
        put(b"bytes", "bytes_key")
        put(42, "int_key")
        self.assertEqual(get(3600, "dict_key"), {"a": 1})
        self.assertEqual(get(3600, "list_key"), [1, 2, 3])
        self.assertEqual(get(3600, "bytes_key"), b"bytes")
        self.assertEqual(get(3600, "int_key"), 42)

    def test_overwrite(self):
        put("v1", "k")
        put("v2", "k")
        self.assertEqual(get(3600, "k"), "v2")

    # ── TTL ────────────────────────────────────────────────────────────────

    def test_hit_within_ttl(self):
        put("val", "k")
        self.assertEqual(get(10, "k"), "val")   # TTL 10s — no expiró

    def test_miss_after_ttl(self):
        """TTL de 0 segundos expira inmediatamente."""
        put("val", "k")
        time.sleep(0.01)   # mínima espera real
        self.assertIsNone(get(0, "k"))   # TTL=0 → siempre expirado

    # ── kwargs como parte de la clave ──────────────────────────────────────

    def test_kwargs_in_key(self):
        put("a", "type", zoom=10)
        put("b", "type", zoom=12)
        self.assertEqual(get(3600, "type", zoom=10), "a")
        self.assertEqual(get(3600, "type", zoom=12), "b")

    # ── size / clear ───────────────────────────────────────────────────────

    def test_size(self):
        self.assertEqual(size(), 0)
        put("x", "k1")
        put("y", "k2")
        self.assertEqual(size(), 2)

    def test_clear(self):
        put("x", "k1")
        clear()
        self.assertEqual(size(), 0)
        self.assertIsNone(get(3600, "k1"))


if __name__ == "__main__":
    unittest.main()
