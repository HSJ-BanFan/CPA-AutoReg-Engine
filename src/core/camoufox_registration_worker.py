"""Isolated Camoufox registration worker process."""

import json
import sys
import traceback
from typing import Any

from .openai.chatgpt_browser_client import ChatGPTBrowserClient
from .register_v2 import EmailServiceAdapter
from ..services import EmailServiceFactory, EmailServiceType


def _emit(payload: dict) -> None:
    print(json.dumps(payload, ensure_ascii=False), flush=True)


def _log(source: str, message: str) -> None:
    _emit({"type": "log", "source": source, "message": message})


def _jsonable(value: Any) -> Any:
    try:
        json.dumps(value)
        return value
    except TypeError:
        if isinstance(value, dict):
            return {str(k): _jsonable(v) for k, v in value.items()}
        if isinstance(value, (list, tuple)):
            return [_jsonable(item) for item in value]
        return str(value)


def run(payload: dict) -> dict:
    email_service = EmailServiceFactory.create(
        EmailServiceType(payload["email_service_type"]),
        payload.get("email_service_config") or {},
    )

    # Restore internal state for stateful email services
    internal_state = payload.get("email_internal_state") or {}
    if email_service.service_type == EmailServiceType.CLOUDFLARE_TEMP_EMAIL and "cf_email_state" in internal_state:
        email = payload["email"]
        email_service._created_emails[email] = internal_state["cf_email_state"]

    email_adapter = EmailServiceAdapter(
        email_service,
        payload.get("email_info") or {},
        lambda msg: _log("engine", msg),
    )
    client = ChatGPTBrowserClient(
        proxy=payload.get("proxy_url") or None,
        headless=True,
    )
    client._log = lambda msg: _log("client", msg)

    success, message = client.register_complete_flow(
        payload["email"],
        payload["password"],
        payload["first_name"],
        payload["last_name"],
        payload["birthdate"],
        email_adapter,
    )
    session_ok = False
    session_result = {}
    if success:
        session_ok, session_result = client.reuse_session_and_get_tokens()
        if isinstance(session_result, dict):
            session_result = {**session_result, "device_id": client.device_id}
    return {
        "type": "result",
        "success": success,
        "message": message,
        "session_ok": session_ok,
        "session_result": _jsonable(session_result),
    }


def main() -> int:
    try:
        payload = json.loads(sys.stdin.read() or "{}")
        _emit(run(payload))
        return 0
    except Exception as exc:
        _emit(
            {
                "type": "result",
                "success": False,
                "message": str(exc),
                "session_ok": False,
                "session_result": {},
                "traceback": traceback.format_exc(limit=8),
            }
        )
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
