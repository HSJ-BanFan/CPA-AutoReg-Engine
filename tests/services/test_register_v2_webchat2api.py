from datetime import datetime, timezone
from types import SimpleNamespace

from pydantic.types import SecretStr

from src.core.register_v2 import RegistrationEngineV2
from src.core.registration_result import RegistrationResult


class FakeDbContext:
    def __enter__(self):
        return object()

    def __exit__(self, exc_type, exc, tb):
        return False


def _settings(enabled: bool = True) -> SimpleNamespace:
    return SimpleNamespace(
        registration_max_retries=1,
        registration_default_password_length=12,
        openai_client_id="client-id",
        webchat2api_enabled=enabled,
        webchat2api_base_url="http://127.0.0.1:19000",
        webchat2api_api_token=SecretStr("admin"),
    )


def _engine(monkeypatch, settings: SimpleNamespace) -> RegistrationEngineV2:
    monkeypatch.setattr("src.core.register_v2.get_settings", lambda: settings)
    email_service = SimpleNamespace(service_type=SimpleNamespace(value="imap_mail"))
    return RegistrationEngineV2(email_service=email_service, callback_logger=lambda _msg: None)


def _result() -> RegistrationResult:
    return RegistrationResult(
        success=True,
        email="user@example.test",
        password="password-123",
        account_id="account-123",
        access_token="access-token",
        refresh_token="refresh-token",
        id_token="id-token",
        session_token="session-token",
        expires_at=datetime(2026, 5, 26, 10, 0, tzinfo=timezone.utc),
        last_refresh=datetime(2026, 5, 23, 10, 0, tzinfo=timezone.utc),
        metadata={"user_id": "user-123"},
    )


def test_save_to_database_pushes_to_webchat2api_after_db_save(monkeypatch) -> None:
    order = []
    settings = _settings(enabled=True)
    engine = _engine(monkeypatch, settings)

    def fake_create_account(*args, **kwargs):
        order.append("db")
        return SimpleNamespace(id=42)

    def fake_push(result, base_url, api_token):
        order.append("push")
        assert result.email == "user@example.test"
        assert base_url == "http://127.0.0.1:19000"
        assert api_token == "admin"
        return True, "webchat2api 推送成功"

    monkeypatch.setattr("src.core.register_v2.get_db", lambda: FakeDbContext())
    monkeypatch.setattr("src.core.register_v2.crud.create_account", fake_create_account)
    monkeypatch.setattr("src.core.upload.webchat2api_upload.push_registration_to_webchat2api", fake_push)

    assert engine.save_to_database(_result()) is True
    assert order == ["db", "push"]
    assert any("webchat2api 推送成功" in line for line in engine.logs)


def test_save_to_database_skips_webchat2api_when_disabled(monkeypatch) -> None:
    settings = _settings(enabled=False)
    engine = _engine(monkeypatch, settings)

    def fail_push(*args, **kwargs):
        raise AssertionError("push should not be called")

    monkeypatch.setattr("src.core.register_v2.get_db", lambda: FakeDbContext())
    monkeypatch.setattr("src.core.register_v2.crud.create_account", lambda *args, **kwargs: SimpleNamespace(id=42))
    monkeypatch.setattr("src.core.upload.webchat2api_upload.push_registration_to_webchat2api", fail_push)

    assert engine.save_to_database(_result()) is True


def test_save_to_database_remains_successful_when_webchat2api_push_fails(monkeypatch) -> None:
    settings = _settings(enabled=True)
    engine = _engine(monkeypatch, settings)

    monkeypatch.setattr("src.core.register_v2.get_db", lambda: FakeDbContext())
    monkeypatch.setattr("src.core.register_v2.crud.create_account", lambda *args, **kwargs: SimpleNamespace(id=42))
    monkeypatch.setattr(
        "src.core.upload.webchat2api_upload.push_registration_to_webchat2api",
        lambda *args, **kwargs: (False, "webchat2api 推送失败: HTTP 500"),
    )

    assert engine.save_to_database(_result()) is True
    assert any("HTTP 500" in line for line in engine.logs)
