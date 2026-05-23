import hashlib
import hmac
import secrets
import time
from typing import Any

from fastapi import HTTPException, Request

from ..config.settings import get_settings

WEBUI_AUTH_MAX_AGE_SECONDS = 12 * 60 * 60


def webui_auth_token(password: str, issued_at: int | None = None) -> str:
    timestamp = int(time.time() if issued_at is None else issued_at)
    secret = get_settings().webui_secret_key.get_secret_value().encode("utf-8")
    signature = hmac.new(secret, f"{timestamp}:{password}".encode("utf-8"), hashlib.sha256).hexdigest()
    return f"{timestamp}:{signature}"


def is_webui_authenticated(request: Any) -> bool:
    cookie = getattr(request, "cookies", {}).get("webui_auth")
    if not cookie:
        return False
    try:
        timestamp_text, signature = cookie.split(":", 1)
        timestamp = int(timestamp_text)
    except (TypeError, ValueError):
        return False
    if len(signature) != hashlib.sha256().digest_size * 2 or not all(char in "0123456789abcdef" for char in signature):
        return False
    now = time.time()
    if timestamp - now > 60:
        return False
    if now - timestamp > WEBUI_AUTH_MAX_AGE_SECONDS:
        return False
    expected = webui_auth_token(get_settings().webui_access_password.get_secret_value(), timestamp)
    return secrets.compare_digest(cookie, expected)


def require_webui_auth(request: Request) -> None:
    if not is_webui_authenticated(request):
        raise HTTPException(status_code=401, detail="未登录")
