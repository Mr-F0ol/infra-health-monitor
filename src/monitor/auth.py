"""Optional API authentication — API key header and/or HTTP Basic Auth.

Auth is *opt-in*: with no credentials configured the dependency is a no-op, so
the quick-start stays zero-friction. When ``api_key`` and/or ``basic_auth_*``
are set, a protected request must satisfy at least one of the configured
schemes. Comparisons use ``secrets.compare_digest`` to avoid timing leaks.
"""

from __future__ import annotations

import base64
import binascii
import secrets

from fastapi import HTTPException, Request, status

from .config import settings


def _auth_enabled() -> bool:
    return bool(settings.api_key or (settings.basic_auth_user and settings.basic_auth_password))


def _api_key_ok(request: Request) -> bool:
    if not settings.api_key:
        return False
    provided = request.headers.get("x-api-key", "")
    return bool(provided) and secrets.compare_digest(provided, settings.api_key)


def _basic_ok(request: Request) -> bool:
    if not (settings.basic_auth_user and settings.basic_auth_password):
        return False
    header = request.headers.get("authorization", "")
    scheme, _, encoded = header.partition(" ")
    if scheme.lower() != "basic" or not encoded:
        return False
    try:
        user, _, password = base64.b64decode(encoded).decode("utf-8").partition(":")
    except (binascii.Error, UnicodeDecodeError):
        return False
    user_ok = secrets.compare_digest(user, settings.basic_auth_user)
    password_ok = secrets.compare_digest(password, settings.basic_auth_password)
    return user_ok and password_ok


async def require_auth(request: Request) -> None:
    """FastAPI dependency enforcing auth when any credential is configured."""
    if not _auth_enabled():
        return
    if _api_key_ok(request) or _basic_ok(request):
        return
    # WWW-Authenticate lets browsers show the native Basic Auth prompt.
    raise HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="authentication required",
        headers={"WWW-Authenticate": 'Basic realm="infra-health-monitor"'},
    )
