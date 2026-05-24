import json
import subprocess
from types import SimpleNamespace

from src.config.constants import EmailServiceType
from src.core import register_v2
from src.core.register_v2 import (
    CAMOUFOX_SUBPROCESS_TIMEOUT_BUFFER,
    RegistrationEngineV2,
    _run_camoufox_registration_subprocess,
)


class FakeEmailService:
    service_type = EmailServiceType.IMAP_MAIL
    config = {"timeout": 9}

    def create_email(self):
        return {"email": "user@example.test", "service_id": "email-service-id"}

    def get_verification_code(self, **_kwargs):
        return None


class FakeBrowserClient:
    def __init__(self, *_args, **_kwargs):
        raise AssertionError("ChatGPTBrowserClient should not run in parent process")


class FakeProtocolClient:
    device_id = "protocol-device"

    def __init__(self, *_args, **_kwargs):
        self._log = lambda _message: None

    def register_complete_flow(self, *_args, **_kwargs):
        return True, "ok"

    def reuse_session_and_get_tokens(self):
        return True, {"access_token": "protocol-token", "device_id": self.device_id}


def _settings(max_retries: int = 1) -> SimpleNamespace:
    return SimpleNamespace(
        registration_max_retries=max_retries,
        registration_default_password_length=12,
    )


def test_run_camoufox_subprocess_parses_log_lines_and_result(monkeypatch) -> None:
    client_logs = []
    engine_logs = []
    captured = {}

    def fake_run(command, **kwargs):
        captured["command"] = command
        captured["payload"] = json.loads(kwargs["input"])
        return SimpleNamespace(
            stdout="\n".join(
                [
                    "not-json",
                    json.dumps({"type": "log", "source": "client", "message": "browser ready"}),
                    json.dumps({"type": "log", "source": "engine", "message": "mail ready"}),
                    json.dumps(
                        {
                            "type": "result",
                            "success": True,
                            "message": "done",
                            "session_ok": True,
                            "session_result": {"access_token": "access-token"},
                        }
                    ),
                ]
            )
        )

    monkeypatch.setattr(register_v2.subprocess, "run", fake_run)

    success, message, session_ok, session_result = _run_camoufox_registration_subprocess(
        {"email": "user@example.test"},
        client_logs.append,
        engine_logs.append,
        timeout=30,
    )

    assert captured["command"][-1] == "src.core.camoufox_registration_worker"
    assert captured["payload"] == {"email": "user@example.test"}
    assert client_logs == ["browser ready"]
    assert engine_logs == ["mail ready"]
    assert (success, message, session_ok) == (True, "done", True)
    assert session_result == {"access_token": "access-token"}


def test_run_camoufox_subprocess_handles_timeout(monkeypatch) -> None:
    def fake_run(*_args, **_kwargs):
        raise subprocess.TimeoutExpired(cmd="worker", timeout=30)

    monkeypatch.setattr(register_v2.subprocess, "run", fake_run)

    success, message, session_ok, session_result = _run_camoufox_registration_subprocess(
        {},
        lambda _message: None,
        lambda _message: None,
        timeout=30,
    )

    assert (success, message, session_ok, session_result) == (
        False,
        "Camoufox 子进程超时",
        False,
        {},
    )


def test_run_camoufox_subprocess_handles_missing_result(monkeypatch) -> None:
    monkeypatch.setattr(
        register_v2.subprocess,
        "run",
        lambda *_args, **_kwargs: SimpleNamespace(
            stdout=json.dumps({"type": "log", "source": "engine", "message": "only log"})
        ),
    )

    success, message, session_ok, session_result = _run_camoufox_registration_subprocess(
        {},
        lambda _message: None,
        lambda _message: None,
        timeout=30,
    )

    assert (success, message, session_ok, session_result) == (
        False,
        "Camoufox 子进程未返回结果",
        False,
        {},
    )


def test_registration_engine_camoufox_uses_subprocess(monkeypatch) -> None:
    monkeypatch.setattr(register_v2, "get_settings", lambda: _settings())
    monkeypatch.setattr(register_v2, "generate_random_password", lambda _length: "password-123")
    monkeypatch.setattr(register_v2, "generate_random_name", lambda: ("Ada", "Lovelace"))
    monkeypatch.setattr(register_v2, "generate_random_birthday", lambda: "2000-01-01")
    monkeypatch.setattr("src.core.openai.chatgpt_browser_client.ChatGPTBrowserClient", FakeBrowserClient)

    calls = []

    def fake_subprocess(payload, _log_client_message, _log_engine_message, timeout):
        calls.append({"payload": payload, "timeout": timeout})
        return True, "ok", True, {
            "access_token": "access-token",
            "refresh_token": "refresh-token",
            "id_token": "id-token",
            "session_token": "session-token",
            "expires": "2026-05-26T10:00:00+00:00",
            "account_id": "account-123",
            "workspace_id": "workspace-123",
            "auth_provider": "auth-provider",
            "user_id": "user-123",
            "user": {"id": "user-123"},
            "account": {"id": "account-123"},
            "raw_session": {"accessToken": "access-token"},
            "device_id": "device-123456",
        }

    monkeypatch.setattr(register_v2, "_run_camoufox_registration_subprocess", fake_subprocess)

    engine = RegistrationEngineV2(
        email_service=FakeEmailService(),
        browser_mode="camoufox",
        callback_logger=lambda _message: None,
        max_retries=1,
    )

    result = engine.run()

    assert result.success is True
    assert result.email == "user@example.test"
    assert result.password == "password-123"
    assert result.access_token == "access-token"
    assert result.refresh_token == "refresh-token"
    assert result.id_token == "id-token"
    assert result.session_token == "session-token"
    assert result.account_id == "account-123"
    assert result.workspace_id == "workspace-123"
    assert result.metadata["browser_mode"] == "camoufox"
    assert result.metadata["user_id"] == "user-123"
    assert len(calls) == 1
    assert calls[0]["payload"]["email"] == "user@example.test"
    assert calls[0]["payload"]["password"] == "password-123"
    assert calls[0]["payload"]["email_service_type"] == "imap_mail"
    assert calls[0]["payload"]["email_service_config"] == {"timeout": 9}
    assert calls[0]["timeout"] == 9 + CAMOUFOX_SUBPROCESS_TIMEOUT_BUFFER


def test_registration_engine_camoufox_retries_retriable_subprocess_failure(monkeypatch) -> None:
    monkeypatch.setattr(register_v2, "get_settings", lambda: _settings(max_retries=2))
    monkeypatch.setattr(register_v2.time, "sleep", lambda _seconds: None)

    calls = []

    def fake_subprocess(*_args, **_kwargs):
        calls.append(1)
        if len(calls) == 1:
            return False, "cloudflare challenge", False, {}
        return True, "ok", True, {"access_token": "access-token", "device_id": "device-123456"}

    monkeypatch.setattr(register_v2, "_run_camoufox_registration_subprocess", fake_subprocess)

    engine = RegistrationEngineV2(
        email_service=FakeEmailService(),
        browser_mode="camoufox",
        callback_logger=lambda _message: None,
        max_retries=2,
    )

    result = engine.run()

    assert result.success is True
    assert len(calls) == 2


def test_registration_engine_protocol_does_not_use_camoufox_subprocess(monkeypatch) -> None:
    monkeypatch.setattr(register_v2, "get_settings", lambda: _settings())
    monkeypatch.setattr(register_v2, "ChatGPTClient", FakeProtocolClient)

    def fail_subprocess(*_args, **_kwargs):
        raise AssertionError("camoufox subprocess should not run in protocol mode")

    monkeypatch.setattr(register_v2, "_run_camoufox_registration_subprocess", fail_subprocess)

    engine = RegistrationEngineV2(
        email_service=FakeEmailService(),
        browser_mode="protocol",
        callback_logger=lambda _message: None,
        max_retries=1,
    )

    result = engine.run()

    assert result.success is True
    assert result.access_token == "protocol-token"
