from datetime import datetime, timezone
from types import SimpleNamespace

from src.core.registration_result import RegistrationResult
from src.core.upload.webchat2api_upload import push_registration_to_webchat2api


class FakeResponse:
    def __init__(self, status_code: int, payload: dict | None = None, text: str = "") -> None:
        self.status_code = status_code
        self._payload = payload or {}
        self.text = text

    def json(self) -> dict:
        return self._payload


def _result() -> RegistrationResult:
    return RegistrationResult(
        success=True,
        email="user@example.test",
        account_id="account-123",
        access_token="access-token",
        refresh_token="refresh-token",
        id_token="id-token",
        expires_at=datetime(2026, 5, 26, 10, 0, tzinfo=timezone.utc),
        last_refresh=datetime(2026, 5, 23, 10, 0, tzinfo=timezone.utc),
        metadata={"user_id": "user-123"},
    )


def test_push_registration_to_webchat2api_posts_expected_payload(monkeypatch) -> None:
    calls = []

    def fake_post(url, **kwargs):
        calls.append({"url": url, **kwargs})
        return FakeResponse(200, {"added": 1})

    monkeypatch.setattr("src.core.upload.webchat2api_upload.cffi_requests.post", fake_post)

    success, message = push_registration_to_webchat2api(
        _result(),
        base_url="http://127.0.0.1:19000",
        api_token="admin",
    )

    assert success is True
    assert "推送成功" in message
    assert calls[0]["url"] == "http://127.0.0.1:19000/api/accounts"
    assert calls[0]["headers"] == {
        "Content-Type": "application/json",
        "Authorization": "Bearer admin",
    }
    assert calls[0]["json"] == {
        "accounts": [
            {
                "access_token": "access-token",
                "provider": "gpt",
                "type": "free",
                "email": "user@example.test",
                "account_id": "account-123",
                "refresh_token": "refresh-token",
                "id_token": "id-token",
                "expired": "2026-05-26T10:00:00+00:00",
                "last_refresh": "2026-05-23T10:00:00+00:00",
                "user_id": "user-123",
            }
        ]
    }
    assert calls[0]["proxies"] is None


def test_push_registration_to_webchat2api_rejects_missing_access_token() -> None:
    result = _result()
    result.access_token = ""

    success, message = push_registration_to_webchat2api(
        result,
        base_url="http://127.0.0.1:19000",
        api_token="admin",
    )

    assert success is False
    assert "access_token" in message


def test_push_registration_to_webchat2api_rejects_external_url() -> None:
    success, message = push_registration_to_webchat2api(
        _result(),
        base_url="https://example.com",
        api_token="admin",
    )

    assert success is False
    assert "本机或私有网络" in message


def test_push_registration_to_webchat2api_handles_http_error(monkeypatch) -> None:
    def fake_post(url, **kwargs):
        return FakeResponse(500, text="access-token refresh-token")

    monkeypatch.setattr("src.core.upload.webchat2api_upload.cffi_requests.post", fake_post)

    success, message = push_registration_to_webchat2api(
        _result(),
        base_url="http://127.0.0.1:19000",
        api_token="admin",
    )

    assert success is False
    assert "HTTP 500" in message
    assert "access-token" not in message
    assert "refresh-token" not in message


def test_push_registration_to_webchat2api_handles_network_error(monkeypatch) -> None:
    def fake_post(url, **kwargs):
        raise RuntimeError("connection failed with access-token")

    monkeypatch.setattr("src.core.upload.webchat2api_upload.cffi_requests.post", fake_post)

    success, message = push_registration_to_webchat2api(
        _result(),
        base_url="http://127.0.0.1:19000",
        api_token="admin",
    )

    assert success is False
    assert "推送异常" in message
    assert "access-token" not in message


def test_push_registration_to_webchat2api_handles_missing_metadata(monkeypatch) -> None:
    calls = []

    def fake_post(url, **kwargs):
        calls.append(kwargs)
        return FakeResponse(200)

    result = _result()
    result.metadata = None
    result.last_refresh = None
    monkeypatch.setattr("src.core.upload.webchat2api_upload.cffi_requests.post", fake_post)

    success, _ = push_registration_to_webchat2api(
        result,
        base_url="http://127.0.0.1:19000",
        api_token="admin",
    )

    assert success is True
    account = calls[0]["json"]["accounts"][0]
    assert account["user_id"] == ""
    assert account["last_refresh"] == ""
