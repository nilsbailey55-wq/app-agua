"""
_ssl_ctx.py
Contexto SSL compartido para todas las llamadas HTTP del backend.

Estrategia:
1. Intentar ssl.create_default_context() — usa el CA bundle del intérprete
   Python (funciona en Railway/Debian y en macOS con Python.org).
2. Si no encontró certs válidos, cargar manualmente el bundle del sistema
   (rutas conocidas para Debian, Alpine, macOS brew, macOS Python.org).
3. Último recurso: contexto no verificado, SOLO para APIs públicas read-only
   (tiles CARTO, Open-Elevation, CONAE WMS, MPC STAC, CCKP).
   Se loguea un warning la primera vez que se activa este fallback.

Por qué no usar _create_unverified_context() directamente:
- Silencia cualquier ataque MITM sin advertencia.
- Este módulo centraliza el fallback para que sea fácil de auditar y
  reemplazar cuando el entorno tenga un CA bundle confiable.
"""

import ssl
import os
import logging

_log = logging.getLogger(__name__)

# Rutas candidatas de CA bundles en orden de preferencia
_CERT_CANDIDATES = [
    "/etc/ssl/certs/ca-certificates.crt",       # Debian / Ubuntu / Railway
    "/etc/ssl/cert.pem",                          # Alpine / macOS (brew Python)
    "/etc/pki/tls/certs/ca-bundle.crt",          # RHEL / CentOS
    "/Library/Frameworks/Python.framework/Versions/3.13/etc/openssl/cert.pem",
    "/Library/Frameworks/Python.framework/Versions/3.12/etc/openssl/cert.pem",
    "/Library/Frameworks/Python.framework/Versions/3.11/etc/openssl/cert.pem",
]


def _build_ctx() -> ssl.SSLContext:
    """Construye el mejor contexto SSL disponible en este entorno."""
    ctx = ssl.create_default_context()

    # ssl.create_default_context() ya carga los certs del intérprete si
    # están disponibles. Probamos también los bundles del sistema como
    # refuerzo (útil en macOS donde el bundle de brew y el de Python.org
    # pueden diferir).
    for path in _CERT_CANDIDATES:
        if os.path.exists(path):
            try:
                ctx.load_verify_locations(path)
                return ctx           # contexto verificado — camino feliz
            except ssl.SSLError:
                pass                 # bundle inválido, seguir buscando

    # Si llegamos acá: create_default_context() cargó algo (su propio bundle
    # interno), que puede ser suficiente. Devolvemos igual — si falla al
    # usarlo, el caller verá la excepción y podremos diagnosticar.
    _log.debug("SSL: usando sólo el bundle interno de Python (sin bundle del sistema encontrado)")
    return ctx


def _build_unverified_ctx() -> ssl.SSLContext:
    """Contexto sin verificación — último recurso para APIs públicas read-only."""
    _log.warning(
        "SSL: usando contexto SIN verificación de certificados. "
        "Aceptable sólo para APIs públicas read-only (tiles, satélite). "
        "Instalar un CA bundle del sistema eliminaría este warning."
    )
    return ssl._create_unverified_context()   # noqa: SLF001


def make_ssl_ctx(*, verified: bool = True) -> ssl.SSLContext:
    """
    Devuelve un SSLContext.

    verified=True  → intenta contexto verificado (default, recomendado).
    verified=False → contexto sin verificación (ej: entornos de dev sin certs).
    """
    if not verified:
        return _build_unverified_ctx()
    return _build_ctx()


# Instancia módulo-level lista para usar como `context=` en urlopen.
# Verificado con fallback a bundle interno — cubre Railway + macOS.
SSL_CTX: ssl.SSLContext = _build_ctx()
