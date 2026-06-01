"""Tests para la lógica de autenticación por API key."""
import hmac
import unittest
from unittest.mock import MagicMock


# ── Lógica extraída de main.py para testear aislada ───────────────────────
# (Evita importar FastAPI y cargar todos los datos del backend)

class AuthError(Exception):
    def __init__(self, status_code: int, detail: str):
        self.status_code = status_code
        self.detail = detail


def require_api_key(request, api_key: str | None) -> None:
    """Réplica exacta de _require_api_key() de main.py."""
    if api_key is None:
        return   # dev mode — sin auth
    key = request.headers.get("x-api-key", "")
    if not key:
        raise AuthError(401, "Autenticación requerida.")
    if not hmac.compare_digest(key, api_key):
        raise AuthError(403, "API key inválida.")


def make_request(headers: dict) -> MagicMock:
    normalized = {k.lower(): v for k, v in headers.items()}
    req = MagicMock()
    req.headers.get = lambda k, default="": normalized.get(k.lower(), default)
    return req


VALID_KEY = "test-secret-key-abc123"


class TestAuthDevMode(unittest.TestCase):
    """Sin REPORT_API_KEY configurada: todo pasa."""

    def test_no_header_allowed_in_dev(self):
        req = make_request({})
        require_api_key(req, api_key=None)   # no debe lanzar

    def test_any_header_allowed_in_dev(self):
        req = make_request({"X-Api-Key": "cualquier-cosa"})
        require_api_key(req, api_key=None)   # no debe lanzar


class TestAuthWithKey(unittest.TestCase):
    """Con REPORT_API_KEY configurada."""

    def test_valid_key_passes(self):
        req = make_request({"X-Api-Key": VALID_KEY})
        require_api_key(req, api_key=VALID_KEY)   # no debe lanzar

    def test_missing_header_returns_401(self):
        req = make_request({})
        with self.assertRaises(AuthError) as ctx:
            require_api_key(req, api_key=VALID_KEY)
        self.assertEqual(ctx.exception.status_code, 401)

    def test_wrong_key_returns_403(self):
        req = make_request({"X-Api-Key": "clave-incorrecta"})
        with self.assertRaises(AuthError) as ctx:
            require_api_key(req, api_key=VALID_KEY)
        self.assertEqual(ctx.exception.status_code, 403)

    def test_empty_key_returns_401(self):
        req = make_request({"X-Api-Key": ""})
        with self.assertRaises(AuthError) as ctx:
            require_api_key(req, api_key=VALID_KEY)
        self.assertEqual(ctx.exception.status_code, 401)

    def test_partial_key_returns_403(self):
        req = make_request({"X-Api-Key": VALID_KEY[:10]})
        with self.assertRaises(AuthError) as ctx:
            require_api_key(req, api_key=VALID_KEY)
        self.assertEqual(ctx.exception.status_code, 403)

    def test_key_with_extra_space_returns_403(self):
        req = make_request({"X-Api-Key": VALID_KEY + " "})
        with self.assertRaises(AuthError) as ctx:
            require_api_key(req, api_key=VALID_KEY)
        self.assertEqual(ctx.exception.status_code, 403)


if __name__ == "__main__":
    unittest.main()
