"""Push registered accounts to a webchat2api account pool."""

import ipaddress
import logging
from typing import Any, Tuple
from urllib.parse import urlparse

from curl_cffi import requests as cffi_requests

from ..registration_result import RegistrationResult

logger = logging.getLogger(__name__)


def _isoformat(value: Any) -> str:
    return value.isoformat() if value else ""


def _is_allowed_local_target(base_url: str) -> bool:
    parsed = urlparse(base_url)
    if parsed.scheme not in {"http", "https"}:
        return False
    host = parsed.hostname
    if not host:
        return False
    if host.lower() == "localhost":
        return True
    try:
        ip = ipaddress.ip_address(host)
    except ValueError:
        return False
    return ip.is_loopback or ip.is_private


def _build_account_payload(result: RegistrationResult) -> dict:
    metadata = result.metadata if isinstance(result.metadata, dict) else {}
    return {
        "access_token": result.access_token,
        "provider": "gpt",
        "type": "free",
        "email": result.email,
        "account_id": result.account_id,
        "refresh_token": result.refresh_token,
        "id_token": result.id_token,
        "expired": _isoformat(result.expires_at),
        "last_refresh": _isoformat(result.last_refresh),
        "user_id": metadata.get("user_id", ""),
    }


def push_registration_to_webchat2api(
    result: RegistrationResult,
    base_url: str,
    api_token: str,
    timeout: int = 15,
) -> Tuple[bool, str]:
    if not base_url:
        return False, "webchat2api URL 未配置"
    if not api_token:
        return False, "webchat2api API Token 未配置"
    if not _is_allowed_local_target(base_url):
        return False, "webchat2api URL 必须是本机或私有网络地址"
    if not result.access_token:
        return False, "缺少 access_token，无法推送到 webchat2api"

    url = base_url.rstrip("/") + "/api/accounts"
    headers = {
        "Content-Type": "application/json",
        "Authorization": f"Bearer {api_token}",
    }
    payload = {"accounts": [_build_account_payload(result)]}

    try:
        response = cffi_requests.post(
            url,
            json=payload,
            headers=headers,
            proxies=None,
            timeout=timeout,
            impersonate="chrome110",
        )
        if response.status_code in (200, 201):
            return True, "webchat2api 推送成功"

        return False, f"webchat2api 推送失败: HTTP {response.status_code}"
    except Exception as exc:
        logger.error("webchat2api 推送异常: %s", exc.__class__.__name__)
        return False, f"webchat2api 推送异常: {exc.__class__.__name__}"
