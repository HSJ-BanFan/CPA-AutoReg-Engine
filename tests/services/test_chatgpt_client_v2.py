import base64
import json
from datetime import datetime, timezone

from curl_cffi import requests as curl_requests

from src.core.openai import chatgpt_client_v2
from src.core.openai.chatgpt_client_v2 import ChatGPTClient
from src.core.openai.chatgpt_flow_utils import FlowState
from src.config.constants import EmailServiceType
from src.core.registration_result import RegistrationResult
from src.core.register_v2 import EmailServiceAdapter, RegistrationEngineV2


class DummyResponse:
    status_code = 200
    text = ""
    url = "https://auth.openai.com/about-you"

    def __init__(self, data: dict | None = None) -> None:
        self.data = data or {}

    def json(self) -> dict:
        return self.data


class CapturingCookieJar:
    def __init__(self, cookies: "CapturingCookies") -> None:
        self.cookies = cookies

    def set_cookie(self, cookie) -> None:
        self.cookies.values.append(
            {
                "name": cookie.name,
                "value": cookie.value,
                "domain": cookie.domain,
                "path": cookie.path,
                "secure": cookie.secure,
                "rest": cookie._rest,
            }
        )


class CapturingCookies:
    def __init__(self) -> None:
        self.values: list[dict] = []
        self.jar = CapturingCookieJar(self)

    def set(self, name: str, value: str, domain: str = "", path: str = "", secure: bool = False) -> None:
        self.values.append({"name": name, "value": value, "domain": domain, "path": path, "secure": secure})


class CapturingSession:
    def __init__(self, get_response: DummyResponse | None = None, post_response: DummyResponse | None = None) -> None:
        self.posts: list[dict] = []
        self.gets: list[dict] = []
        self.cookies = CapturingCookies()
        self.get_response = get_response or DummyResponse()
        self.post_response = post_response or DummyResponse()

    def post(self, url: str, **kwargs):
        self.posts.append({"url": url, **kwargs})
        return self.post_response

    def get(self, url: str, **kwargs):
        self.gets.append({"url": url, **kwargs})
        return self.get_response


class DummyEmailService:
    service_type = EmailServiceType.IMAP_MAIL

    def __init__(self, timeout: int | str) -> None:
        self.config = {"timeout": timeout}

    def get_verification_code(self, **kwargs):
        return None


class CapturingEmailAdapter:
    def __init__(self) -> None:
        self.calls: list[dict] = []
        self.verification_timeout = 120

    def wait_for_verification_code(self, email: str, timeout: int, otp_sent_at: float | None = None) -> str:
        self.calls.append({"email": email, "timeout": timeout, "otp_sent_at": otp_sent_at})
        return "123456"


class RotatingEmailService:
    service_type = EmailServiceType.CLOUDFLARE_TEMP_EMAIL

    def __init__(self) -> None:
        self.config = {}
        self.emails = ["new@example.test"]

    def create_email(self) -> dict:
        email = self.emails.pop(0)
        return {"email": email, "service_id": email, "id": email}


def test_verify_email_otp_uses_state_referer_and_device_headers_and_fetches_session_dump(monkeypatch) -> None:
    client = ChatGPTClient.__new__(ChatGPTClient)
    client.AUTH = "https://auth.openai.com"
    client.session = CapturingSession(
        get_response=DummyResponse({"oai-client-auth-session": {"session_id": "session-dump"}}),
        post_response=DummyResponse({}),
    )
    client.device_id = "device-123"
    client.ua = "Mozilla/5.0 Test"
    client.sec_ch_ua = '"Chromium";v="136"'
    client.chrome_full = "136.0.7103.100"
    client.accept_language = "en-US,en;q=0.9"
    client.browser_mode = "protocol"
    client.impersonate = "chrome136"
    client._browser_pause = lambda: None
    logs: list[str] = []
    client._log = logs.append
    client._state_from_payload = lambda data, current_url="": FlowState(page_type="about_you", current_url=current_url)
    monkeypatch.setattr(chatgpt_client_v2, "build_browser_sentinel_token", lambda **kwargs: None)
    monkeypatch.setattr(chatgpt_client_v2, "build_sentinel_token", lambda *args, **kwargs: "sentinel-token")

    state = FlowState(
        page_type="email_otp_verification",
        current_url="https://auth.openai.com/email-verification?state=abc",
        continue_url="https://auth.openai.com/ignored",
    )

    success, next_state = client.verify_email_otp("123456", return_state=True, state=state)

    assert success is True
    assert next_state.page_type == "about_you"
    call = client.session.posts[0]
    assert call["allow_redirects"] is False
    assert call["headers"]["Referer"] == "https://auth.openai.com/email-verification?state=abc"
    assert call["headers"]["oai-device-id"] == "device-123"
    assert call["headers"]["openai-sentinel-token"] == "sentinel-token"
    assert call["json"] == {"code": "123456"}
    assert client.session.gets[0]["url"] == "https://auth.openai.com/api/accounts/client_auth_session_dump"
    assert not any("123456" in message or "sentinel-token" in message for message in logs)


def test_send_email_otp_uses_resend_endpoint_with_state_referer_and_device_header() -> None:
    client = ChatGPTClient.__new__(ChatGPTClient)
    client.AUTH = "https://auth.openai.com"
    client.session = CapturingSession(post_response=DummyResponse({"oai-client-auth-session": {"session_id": "session-headers"}}))
    client.device_id = "device-send"
    client.ua = "Mozilla/5.0 Test"
    client.sec_ch_ua = '"Chromium";v="136"'
    client.chrome_full = "136.0.7103.100"
    client.accept_language = "en-US,en;q=0.9"
    client.browser_mode = "protocol"
    client._browser_pause = lambda: None
    client._log = lambda message: None
    state = FlowState(current_url="https://auth.openai.com/email-verification?state=send-state")

    assert client.send_email_otp(state=state) is True

    call = client.session.posts[0]
    assert call["url"] == "https://auth.openai.com/api/accounts/email-otp/resend"
    assert call["allow_redirects"] is False
    assert call["headers"]["Referer"] == "https://auth.openai.com/email-verification?state=send-state"
    assert call["headers"]["oai-device-id"] == "device-send"


def test_send_email_otp_persists_returned_client_auth_session() -> None:
    auth_session = {
        "app_name_enum": "chat",
        "email": "user@example.test",
        "session_id": "session-123",
    }
    response = DummyResponse({"oai-client-auth-session": auth_session})
    client = ChatGPTClient.__new__(ChatGPTClient)
    client.AUTH = "https://auth.openai.com"
    client.session = CapturingSession(post_response=response)
    client.device_id = "device-send"
    client.ua = "Mozilla/5.0 Test"
    client.sec_ch_ua = '"Chromium";v="136"'
    client.chrome_full = "136.0.7103.100"
    client.accept_language = "en-US,en;q=0.9"
    client.browser_mode = "protocol"
    client._browser_pause = lambda: None
    client._log = lambda message: None
    state = FlowState(current_url="https://auth.openai.com/email-verification")

    assert client.send_email_otp(state=state) is True

    cookie = client.session.cookies.values[0]
    decoded = base64.urlsafe_b64decode(cookie["value"] + "=" * (-len(cookie["value"]) % 4))
    assert cookie["name"] == "oai-client-auth-session"
    assert cookie["domain"] == "auth.openai.com"
    assert cookie["path"] == "/"
    assert cookie["secure"] is True
    assert cookie["rest"] == {"HttpOnly": True, "SameSite": "Lax"}
    assert json.loads(decoded) == auth_session


def test_persist_client_auth_session_uses_curl_cffi_cookie_jar() -> None:
    auth_session = {
        "app_name_enum": "chat",
        "email": "user@example.test",
        "session_id": "session-curl",
    }
    client = ChatGPTClient.__new__(ChatGPTClient)
    client.session = curl_requests.Session()

    assert client._persist_client_auth_session({"oai-client-auth-session": auth_session}) is True

    cookie = next(cookie for cookie in client.session.cookies.jar if cookie.name == "oai-client-auth-session")
    decoded = base64.urlsafe_b64decode(cookie.value + "=" * (-len(cookie.value) % 4))
    assert cookie.domain == "auth.openai.com"
    assert cookie.secure is True
    assert cookie._rest == {"HttpOnly": True, "SameSite": "Lax"}
    assert json.loads(decoded) == auth_session


def test_persist_client_auth_session_uses_auth_host_for_cookie_domain() -> None:
    client = ChatGPTClient.__new__(ChatGPTClient)
    client.AUTH = "https://auth.example.test"
    client.session = CapturingSession()

    assert client._persist_client_auth_session({"oai-client-auth-session": {"session_id": "session-domain"}}) is True

    assert client.session.cookies.values[0]["domain"] == "auth.example.test"


def test_persist_client_auth_session_rejects_missing_session_id() -> None:
    client = ChatGPTClient.__new__(ChatGPTClient)
    client.session = CapturingSession()

    assert client._persist_client_auth_session({"oai-client-auth-session": {"email": "user@example.test"}}) is False
    assert client.session.cookies.values == []


def test_persist_client_auth_session_rejects_oversized_payload() -> None:
    client = ChatGPTClient.__new__(ChatGPTClient)
    client.session = CapturingSession()

    assert client._persist_client_auth_session({"oai-client-auth-session": {"session_id": "x" * 5000}}) is False
    assert client.session.cookies.values == []


def test_send_email_otp_succeeds_when_auth_session_missing() -> None:
    client = ChatGPTClient.__new__(ChatGPTClient)
    client.AUTH = "https://auth.openai.com"
    client.session = CapturingSession(post_response=DummyResponse({}))
    client.device_id = "device-send"
    client.ua = "Mozilla/5.0 Test"
    client.sec_ch_ua = '"Chromium";v="136"'
    client.chrome_full = "136.0.7103.100"
    client.accept_language = "en-US,en;q=0.9"
    client.browser_mode = "protocol"
    client._browser_pause = lambda: None
    client._log = lambda message: None

    assert client.send_email_otp(state=FlowState(current_url="https://auth.openai.com/email-verification")) is True
    assert client.session.cookies.values == []


class InvalidJsonResponse(DummyResponse):
    def json(self) -> dict:
        raise ValueError("invalid json")


def test_send_email_otp_returns_false_when_response_json_is_invalid() -> None:
    client = ChatGPTClient.__new__(ChatGPTClient)
    client.AUTH = "https://auth.openai.com"
    client.session = CapturingSession(post_response=InvalidJsonResponse())
    client.device_id = "device-send"
    client.ua = "Mozilla/5.0 Test"
    client.sec_ch_ua = '"Chromium";v="136"'
    client.chrome_full = "136.0.7103.100"
    client.accept_language = "en-US,en;q=0.9"
    client.browser_mode = "protocol"
    client._browser_pause = lambda: None
    client._log = lambda message: None

    assert client.send_email_otp(state=FlowState(current_url="https://auth.openai.com/email-verification")) is False
    assert client.session.cookies.values == []


def test_verify_email_otp_persists_client_auth_session_from_session_dump_and_sanitizes_state(monkeypatch) -> None:
    auth_session = {
        "app_name_enum": "chat",
        "email": "user@example.test",
        "session_id": "session-verify",
    }
    client = ChatGPTClient.__new__(ChatGPTClient)
    client.AUTH = "https://auth.openai.com"
    client.session = CapturingSession(
        get_response=DummyResponse({"oai-client-auth-session": auth_session, "continue_url": "/about-you"}),
        post_response=DummyResponse({"continue_url": "/about-you"}),
    )
    client.device_id = "device-verify"
    client.ua = "Mozilla/5.0 Test"
    client.sec_ch_ua = '"Chromium";v="136"'
    client.chrome_full = "136.0.7103.100"
    client.accept_language = "en-US,en;q=0.9"
    client.browser_mode = "protocol"
    client.impersonate = "chrome136"
    client._browser_pause = lambda: None
    client._log = lambda message: None
    client._state_from_payload = lambda data, current_url="": FlowState(page_type="about_you", current_url=current_url, raw=data)
    monkeypatch.setattr(chatgpt_client_v2, "build_browser_sentinel_token", lambda **kwargs: None)
    monkeypatch.setattr(chatgpt_client_v2, "build_sentinel_token", lambda *args, **kwargs: "verify-sentinel")

    success, next_state = client.verify_email_otp("123456", return_state=True, state=FlowState(current_url="https://auth.openai.com/email-verification"))

    assert success is True
    assert client.session.gets[0]["url"] == "https://auth.openai.com/api/accounts/client_auth_session_dump"
    cookie = client.session.cookies.values[0]
    decoded = base64.urlsafe_b64decode(cookie["value"] + "=" * (-len(cookie["value"]) % 4))
    assert json.loads(decoded) == auth_session
    assert "oai-client-auth-session" not in next_state.raw


def test_verify_email_otp_persists_observed_client_auth_session_dump_shape(monkeypatch) -> None:
    dump = {
        "checksum": "checksum-redacted",
        "client_auth_session": {"email": "user@example.test"},
        "session_id": "session-observed",
    }
    dump_response = DummyResponse(dump)
    dump_response.url = "https://auth.openai.com/api/accounts/client_auth_session_dump"
    client = ChatGPTClient.__new__(ChatGPTClient)
    client.AUTH = "https://auth.openai.com"
    client.session = CapturingSession(get_response=dump_response, post_response=DummyResponse({}))
    client.device_id = "device-verify"
    client.ua = "Mozilla/5.0 Test"
    client.sec_ch_ua = '"Chromium";v="136"'
    client.chrome_full = "136.0.7103.100"
    client.accept_language = "en-US,en;q=0.9"
    client.browser_mode = "protocol"
    client.impersonate = "chrome136"
    client._browser_pause = lambda: None
    client._log = lambda message: None
    client._state_from_payload = lambda data, current_url="": FlowState(page_type="about_you", current_url=current_url, raw=data)
    monkeypatch.setattr(chatgpt_client_v2, "build_browser_sentinel_token", lambda **kwargs: None)
    monkeypatch.setattr(chatgpt_client_v2, "build_sentinel_token", lambda *args, **kwargs: "verify-sentinel")

    success, next_state = client.verify_email_otp("123456", return_state=True, state=FlowState(current_url="https://auth.openai.com/email-verification"))

    assert success is True
    cookie = client.session.cookies.values[0]
    decoded = base64.urlsafe_b64decode(cookie["value"] + "=" * (-len(cookie["value"]) % 4))
    assert json.loads(decoded) == {
        "checksum": "checksum-redacted",
        "email": "user@example.test",
        "session_id": "session-observed",
    }
    assert next_state.current_url == "https://auth.openai.com/about-you"
    assert "client_auth_session" not in next_state.raw
    assert "session_id" not in next_state.raw


def test_register_user_fails_when_client_auth_session_cannot_persist(monkeypatch) -> None:
    client = ChatGPTClient.__new__(ChatGPTClient)
    client.AUTH = "https://auth.openai.com"
    client.session = CapturingSession(post_response=DummyResponse({"oai-client-auth-session": {"email": "user@example.test"}}))
    client.device_id = "device-register"
    client.ua = "Mozilla/5.0 Test"
    client.sec_ch_ua = '"Chromium";v="136"'
    client.chrome_full = "136.0.7103.100"
    client.accept_language = "en-US,en;q=0.9"
    client.browser_mode = "protocol"
    client.impersonate = "chrome136"
    client._browser_pause = lambda: None
    client._log = lambda message: None
    monkeypatch.setattr(chatgpt_client_v2, "build_browser_sentinel_token", lambda **kwargs: None)
    monkeypatch.setattr(chatgpt_client_v2, "build_sentinel_token", lambda *args, **kwargs: "register-sentinel")

    assert client.register_user("user@example.test", "password") == (False, "客户端认证会话无效")


def test_verify_email_otp_fails_when_client_auth_session_cannot_persist(monkeypatch) -> None:
    client = ChatGPTClient.__new__(ChatGPTClient)
    client.AUTH = "https://auth.openai.com"
    client.session = CapturingSession(post_response=DummyResponse({"oai-client-auth-session": {"email": "user@example.test"}}))
    client.device_id = "device-verify"
    client.ua = "Mozilla/5.0 Test"
    client.sec_ch_ua = '"Chromium";v="136"'
    client.chrome_full = "136.0.7103.100"
    client.accept_language = "en-US,en;q=0.9"
    client.browser_mode = "protocol"
    client.impersonate = "chrome136"
    client._browser_pause = lambda: None
    client._log = lambda message: None
    monkeypatch.setattr(chatgpt_client_v2, "build_browser_sentinel_token", lambda **kwargs: None)
    monkeypatch.setattr(chatgpt_client_v2, "build_sentinel_token", lambda *args, **kwargs: "verify-sentinel")

    assert client.verify_email_otp("123456", return_state=True, state=FlowState(current_url="https://auth.openai.com/email-verification")) == (False, "客户端认证会话无效")


def test_verify_email_otp_logs_safe_session_dump_diagnostics(monkeypatch) -> None:
    client = ChatGPTClient.__new__(ChatGPTClient)
    client.AUTH = "https://auth.openai.com"
    client.session = CapturingSession(
        get_response=DummyResponse({"continue_url": "/about-you", "oai-client-auth-session": {"email": "user@example.test"}}),
        post_response=DummyResponse({}),
    )
    client.device_id = "device-verify"
    client.ua = "Mozilla/5.0 Test"
    client.sec_ch_ua = '"Chromium";v="136"'
    client.chrome_full = "136.0.7103.100"
    client.accept_language = "en-US,en;q=0.9"
    client.browser_mode = "protocol"
    client.impersonate = "chrome136"
    client._browser_pause = lambda: None
    logs: list[str] = []
    client._log = logs.append
    monkeypatch.setattr(chatgpt_client_v2, "build_browser_sentinel_token", lambda **kwargs: None)
    monkeypatch.setattr(chatgpt_client_v2, "build_sentinel_token", lambda *args, **kwargs: "verify-sentinel")

    assert client.verify_email_otp("123456", return_state=True, state=FlowState(current_url="https://auth.openai.com/email-verification")) == (False, "客户端认证会话无效")

    assert "验证码验证状态: 200" in logs
    assert "客户端认证会话转储状态: 200" in logs
    assert "客户端认证会话转储字段: ['continue_url', 'oai-client-auth-session']" in logs
    assert "客户端认证会话存在: True" in logs
    assert "客户端认证会话包含 session_id: False" in logs
    assert not any("user@example.test" in message or "123456" in message for message in logs)


def test_register_user_persists_returned_client_auth_session(monkeypatch) -> None:
    auth_session = {
        "app_name_enum": "chat",
        "email": "user@example.test",
        "session_id": "session-register",
    }
    client = ChatGPTClient.__new__(ChatGPTClient)
    client.AUTH = "https://auth.openai.com"
    client.session = CapturingSession(post_response=DummyResponse({"oai-client-auth-session": auth_session}))
    client.device_id = "device-register"
    client.ua = "Mozilla/5.0 Test"
    client.sec_ch_ua = '"Chromium";v="136"'
    client.chrome_full = "136.0.7103.100"
    client.accept_language = "en-US,en;q=0.9"
    client.browser_mode = "protocol"
    client.impersonate = "chrome136"
    client._browser_pause = lambda: None
    client._log = lambda message: None
    monkeypatch.setattr(chatgpt_client_v2, "build_browser_sentinel_token", lambda **kwargs: None)
    monkeypatch.setattr(chatgpt_client_v2, "build_sentinel_token", lambda *args, **kwargs: "register-sentinel")

    assert client.register_user("user@example.test", "password") == (True, "注册成功")

    cookie = client.session.cookies.values[0]
    decoded = base64.urlsafe_b64decode(cookie["value"] + "=" * (-len(cookie["value"]) % 4))
    assert cookie["name"] == "oai-client-auth-session"
    assert cookie["domain"] == "auth.openai.com"
    assert cookie["secure"] is True
    assert cookie["rest"] == {"HttpOnly": True, "SameSite": "Lax"}
    assert json.loads(decoded) == auth_session


def test_register_user_return_state_sanitizes_client_auth_session(monkeypatch) -> None:
    auth_session = {
        "app_name_enum": "chat",
        "email": "user@example.test",
        "session_id": "session-register-state",
    }
    client = ChatGPTClient.__new__(ChatGPTClient)
    client.AUTH = "https://auth.openai.com"
    client.session = CapturingSession(post_response=DummyResponse({"oai-client-auth-session": auth_session, "continue_url": "/email-verification"}))
    client.device_id = "device-register"
    client.ua = "Mozilla/5.0 Test"
    client.sec_ch_ua = '"Chromium";v="136"'
    client.chrome_full = "136.0.7103.100"
    client.accept_language = "en-US,en;q=0.9"
    client.browser_mode = "protocol"
    client.impersonate = "chrome136"
    client._browser_pause = lambda: None
    client._log = lambda message: None
    monkeypatch.setattr(chatgpt_client_v2, "build_browser_sentinel_token", lambda **kwargs: None)
    monkeypatch.setattr(chatgpt_client_v2, "build_sentinel_token", lambda *args, **kwargs: "register-sentinel")

    success, next_state = client.register_user("user@example.test", "password", return_state=True)

    assert success is True
    assert "oai-client-auth-session" not in next_state.raw


def test_create_account_uses_browser_sentinel_and_sanitizes_client_auth_session(monkeypatch) -> None:
    auth_session = {
        "app_name_enum": "chat",
        "email": "user@example.test",
        "session_id": "session-create",
    }
    client = ChatGPTClient.__new__(ChatGPTClient)
    client.AUTH = "https://auth.openai.com"
    client.BASE = "https://chatgpt.com"
    client.session = CapturingSession(post_response=DummyResponse({"oai-client-auth-session": auth_session, "continue_url": "/"}))
    client.device_id = "device-create"
    client.ua = "Mozilla/5.0 Test"
    client.sec_ch_ua = '"Chromium";v="136"'
    client.chrome_full = "136.0.7103.100"
    client.accept_language = "en-US,en;q=0.9"
    client.browser_mode = "protocol"
    client.impersonate = "chrome136"
    client.proxy = "http://127.0.0.1:7897"
    client._browser_pause = lambda: None
    client._log = lambda message: None
    monkeypatch.setattr(chatgpt_client_v2, "build_browser_sentinel_token", lambda **kwargs: "browser-create-sentinel")
    monkeypatch.setattr(
        chatgpt_client_v2,
        "build_sentinel_token",
        lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("HTTP fallback should not run")),
    )

    success, next_state = client.create_account("Jane", "Doe", "2000-01-01", return_state=True)

    assert success is True
    assert client.session.posts[0]["headers"]["openai-sentinel-token"] == "browser-create-sentinel"
    assert "oai-client-auth-session" not in next_state.raw


def test_create_account_succeeds_when_response_has_no_client_auth_session(monkeypatch) -> None:
    client = ChatGPTClient.__new__(ChatGPTClient)
    client.AUTH = "https://auth.openai.com"
    client.BASE = "https://chatgpt.com"
    response = DummyResponse({"continue_url": "/"})
    response.url = "https://auth.openai.com/api/accounts/create_account"
    client.session = CapturingSession(post_response=response)
    client.device_id = "device-create"
    client.ua = "Mozilla/5.0 Test"
    client.sec_ch_ua = '"Chromium";v="136"'
    client.chrome_full = "136.0.7103.100"
    client.accept_language = "en-US,en;q=0.9"
    client.browser_mode = "protocol"
    client.impersonate = "chrome136"
    client.proxy = "http://127.0.0.1:7897"
    client._browser_pause = lambda: None
    client._log = lambda message: None
    client._state_from_payload = lambda data, current_url="": FlowState(page_type="registration_complete", current_url=current_url, raw=data)
    monkeypatch.setattr(chatgpt_client_v2, "build_browser_sentinel_token", lambda **kwargs: "browser-create-sentinel")
    monkeypatch.setattr(chatgpt_client_v2, "build_sentinel_token", lambda *args, **kwargs: "fallback-create-sentinel")

    success, next_state = client.create_account("Jane", "Doe", "2000-01-01", return_state=True)

    assert success is True
    assert next_state.page_type == "registration_complete"
    assert client.session.cookies.values == []


def test_create_account_reports_registration_disallowed_code(monkeypatch) -> None:
    client = ChatGPTClient.__new__(ChatGPTClient)
    client.AUTH = "https://auth.openai.com"
    client.BASE = "https://chatgpt.com"
    response = DummyResponse()
    response.status_code = 400
    response.text = json.dumps(
        {
            "error": {
                "message": "Sorry, we cannot create your account with the given information.",
                "type": "invalid_request_error",
                "code": "registration_disallowed",
            }
        }
    )
    client.session = CapturingSession(post_response=response)
    client.device_id = "device-create"
    client.ua = "Mozilla/5.0 Test"
    client.sec_ch_ua = '"Chromium";v="136"'
    client.chrome_full = "136.0.7103.100"
    client.accept_language = "en-US,en;q=0.9"
    client.browser_mode = "protocol"
    client.impersonate = "chrome136"
    client.proxy = "http://127.0.0.1:7897"
    client._browser_pause = lambda: None
    client._log = lambda message: None
    monkeypatch.setattr(chatgpt_client_v2, "build_browser_sentinel_token", lambda **kwargs: "browser-create-sentinel")
    monkeypatch.setattr(chatgpt_client_v2, "build_sentinel_token", lambda *args, **kwargs: "fallback-create-sentinel")

    assert client.create_account("Jane", "Doe", "2000-01-01", return_state=True) == (
        False,
        "HTTP 400: registration_disallowed",
    )


def test_registration_engine_does_not_retry_registration_disallowed() -> None:
    engine = RegistrationEngineV2(DummyEmailService(30), callback_logger=lambda message: None)

    assert engine._should_retry("创建账号失败: HTTP 400: registration_disallowed") is False


def test_register_complete_flow_stops_when_send_email_otp_fails() -> None:
    client = ChatGPTClient.__new__(ChatGPTClient)
    client.AUTH = "https://auth.openai.com"
    client.last_registration_state = None
    client._log = lambda message: None
    client._reset_session = lambda: None
    client.visit_homepage = lambda: True
    client.get_csrf_token = lambda: "csrf"
    client.signin = lambda email, csrf_token: "auth-url"
    client.authorize = lambda auth_url: "https://auth.openai.com/email-verification"
    client._state_from_url = lambda url: FlowState(page_type="email_otp_verification")
    client._state_signature = lambda state: state.page_type
    client._is_registration_complete_state = lambda state: False
    client._state_is_password_registration = lambda state: False
    client._state_is_email_otp = lambda state: state.page_type == "email_otp_verification"
    client._state_is_about_you = lambda state: False
    client.send_email_otp = lambda state=None: False

    assert client.register_complete_flow(
        "user@example.test",
        "password",
        "Jane",
        "Doe",
        "2000-01-01",
        CapturingEmailAdapter(),
    ) == (False, "发送验证码失败")


def test_build_sentinel_token_prefers_browser_token(monkeypatch) -> None:
    client = ChatGPTClient.__new__(ChatGPTClient)
    client.proxy = "http://127.0.0.1:7897"
    client.browser_mode = "protocol"
    client.device_id = "device-browser"
    client.ua = "Mozilla/5.0 Test"
    client.sec_ch_ua = '"Chromium";v="136"'
    client.impersonate = "chrome136"
    client.session = object()

    captured = {}

    def browser_token(**kwargs):
        captured.update(kwargs)
        return "browser-sentinel"

    monkeypatch.setattr(chatgpt_client_v2, "build_browser_sentinel_token", browser_token)
    monkeypatch.setattr(
        chatgpt_client_v2,
        "build_sentinel_token",
        lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("HTTP fallback should not run")),
    )

    token = client._build_sentinel_token("email_otp_validate", "https://auth.openai.com/email-verification")

    assert token == "browser-sentinel"
    assert captured["flow"] == "email_otp_validate"
    assert captured["proxy"] == "http://127.0.0.1:7897"
    assert captured["device_id"] == "device-browser"


def test_register_user_uses_device_and_sentinel_headers(monkeypatch) -> None:
    client = ChatGPTClient.__new__(ChatGPTClient)
    client.AUTH = "https://auth.openai.com"
    client.session = CapturingSession(post_response=DummyResponse({"oai-client-auth-session": {"session_id": "session-register"}}))
    client.device_id = "device-456"
    client.ua = "Mozilla/5.0 Test"
    client.sec_ch_ua = '"Chromium";v="136"'
    client.chrome_full = "136.0.7103.100"
    client.accept_language = "en-US,en;q=0.9"
    client.browser_mode = "protocol"
    client.impersonate = "chrome136"
    client._browser_pause = lambda: None
    logs: list[str] = []
    client._log = logs.append

    def build_token(*args, **kwargs):
        assert kwargs["flow"] == "username_password_create"
        return "register-sentinel"

    monkeypatch.setattr(chatgpt_client_v2, "build_browser_sentinel_token", lambda **kwargs: None)
    monkeypatch.setattr(chatgpt_client_v2, "build_sentinel_token", build_token)

    success, message = client.register_user("user@example.test", "password")

    assert success is True
    assert message == "注册成功"
    call = client.session.posts[0]
    assert call["allow_redirects"] is False
    assert call["headers"]["oai-device-id"] == "device-456"
    assert call["headers"]["openai-sentinel-token"] == "register-sentinel"
    assert not any("password" in message or "register-sentinel" in message for message in logs)


def test_register_complete_flow_passes_otp_sent_at_to_email_adapter(monkeypatch) -> None:
    client = ChatGPTClient.__new__(ChatGPTClient)
    client.AUTH = "https://auth.openai.com"
    client.last_registration_state = None
    client._log = lambda message: None
    client._reset_session = lambda: None
    client.visit_homepage = lambda: True
    client.get_csrf_token = lambda: "csrf"
    client.signin = lambda email, csrf_token: "auth-url"
    client.authorize = lambda auth_url: "https://auth.openai.com/create-account/password"
    register_state = FlowState(
        page_type="email_otp_verification",
        current_url="https://auth.openai.com/email-verification?state=from-register",
    )
    client._state_from_url = lambda url: FlowState(page_type="create_account_password")
    client._state_signature = lambda state: state.page_type
    client._is_registration_complete_state = lambda state: state.page_type == "registration_complete"
    client._state_is_password_registration = lambda state: state.page_type == "create_account_password"
    client._state_is_email_otp = lambda state: state.page_type == "email_otp_verification"
    client._state_is_about_you = lambda state: False
    client.register_user = lambda email, password, return_state=False: (True, register_state if return_state else "ok")
    sent = {"count": 0}

    def send_email_otp(state=None) -> bool:
        sent["count"] += 1
        return True

    client.send_email_otp = send_email_otp
    captured_verify = {}

    def verify_email_otp(otp_code, return_state=False, state=None):
        captured_verify["state"] = state
        return True, FlowState(page_type="registration_complete")

    client.verify_email_otp = verify_email_otp
    monkeypatch.setattr(chatgpt_client_v2.time, "time", lambda: 1234.5)
    adapter = CapturingEmailAdapter()

    success, message = client.register_complete_flow(
        "user@example.test",
        "password",
        "Jane",
        "Doe",
        "2000-01-01",
        adapter,
    )

    assert success is True
    assert message == "注册成功"
    assert sent["count"] == 0
    assert adapter.calls == [
        {"email": "user@example.test", "timeout": 120, "otp_sent_at": 1234.5}
    ]
    assert captured_verify["state"].page_type == "email_otp_verification"
    assert captured_verify["state"].current_url == "https://auth.openai.com/email-verification?state=from-register"


def test_register_complete_flow_sends_otp_when_authorize_starts_on_email_otp(monkeypatch) -> None:
    client = ChatGPTClient.__new__(ChatGPTClient)
    client.AUTH = "https://auth.openai.com"
    client.last_registration_state = None
    client._log = lambda message: None
    client._reset_session = lambda: None
    client.visit_homepage = lambda: True
    client.get_csrf_token = lambda: "csrf"
    client.signin = lambda email, csrf_token: "auth-url"
    client.authorize = lambda auth_url: "https://auth.openai.com/email-verification"
    client._state_from_url = lambda url: FlowState(page_type="email_otp_verification")
    client._state_signature = lambda state: state.page_type
    client._is_registration_complete_state = lambda state: state.page_type == "registration_complete"
    client._state_is_password_registration = lambda state: False
    client._state_is_email_otp = lambda state: state.page_type == "email_otp_verification"
    client._state_is_about_you = lambda state: False
    sent = {"count": 0, "state": None}

    def send_email_otp(state=None) -> bool:
        sent["count"] += 1
        sent["state"] = state
        return True

    client.send_email_otp = send_email_otp
    client.verify_email_otp = lambda otp_code, return_state=False, state=None: (
        True,
        FlowState(page_type="registration_complete"),
    )
    monkeypatch.setattr(chatgpt_client_v2.time, "time", lambda: 2345.5)
    adapter = CapturingEmailAdapter()

    success, message = client.register_complete_flow(
        "user@example.test",
        "password",
        "Jane",
        "Doe",
        "2000-01-01",
        adapter,
    )

    assert success is True
    assert message == "注册成功"
    assert sent["count"] == 1
    assert sent["state"].page_type == "email_otp_verification"
    assert adapter.calls == [
        {"email": "user@example.test", "timeout": 120, "otp_sent_at": 2345.5}
    ]


def test_reuse_session_and_get_tokens_returns_all_session_token_fields() -> None:
    session_data = {
        "accessToken": "access-token",
        "refreshToken": "refresh-token",
        "idToken": "id-token",
        "sessionToken": "session-token",
        "expires": "2026-05-26T10:00:00.000Z",
        "user": {"id": "user-123"},
        "account": {"id": "account-123"},
    }
    client = ChatGPTClient.__new__(ChatGPTClient)
    client.BASE = "https://chatgpt.com"
    client.last_registration_state = FlowState(page_type="registration_complete")
    client.session = CapturingSession(get_response=DummyResponse(session_data))
    client.browser_mode = "headless"
    client.ua = "Mozilla/5.0 Test"
    client.sec_ch_ua = '"Chromium";v="136"'
    client.chrome_full = "136.0.0.0"
    client.accept_language = "en-US,en;q=0.9"
    client._log = lambda message: None
    client._state_requires_navigation = lambda state: False
    client.get_next_auth_session_token = lambda: "cookie-session-token"

    ok, result = client.reuse_session_and_get_tokens()

    assert ok is True
    assert result["access_token"] == "access-token"
    assert result["refresh_token"] == "refresh-token"
    assert result["id_token"] == "id-token"
    assert result["session_token"] == "session-token"
    assert result["expires"] == "2026-05-26T10:00:00.000Z"


def test_registration_engine_maps_full_session_result_to_registration_result(monkeypatch) -> None:
    engine = RegistrationEngineV2(DummyEmailService(30), callback_logger=lambda message: None)
    engine.email = "user@example.test"
    engine.email_info = {"email": "user@example.test", "service_id": "user@example.test"}
    monkeypatch.setattr("src.core.register_v2.generate_random_password", lambda length: "Password123")
    monkeypatch.setattr("src.core.register_v2.generate_random_name", lambda: ("Jane", "Doe"))
    monkeypatch.setattr("src.core.register_v2.generate_random_birthday", lambda: "2000-01-01")
    monkeypatch.setattr(RegistrationEngineV2, "_prepare_email", lambda self: True)

    class FakeClient:
        device_id = "device-12345678"

        def __init__(self, **kwargs) -> None:
            self._log = lambda message: None

        def register_complete_flow(self, *args, **kwargs):
            return True, "注册成功"

        def reuse_session_and_get_tokens(self):
            return True, {
                "access_token": "access-token",
                "refresh_token": "refresh-token",
                "id_token": "id-token",
                "session_token": "session-token",
                "account_id": "account-123",
                "workspace_id": "workspace-123",
                "expires": "2026-05-26T10:00:00.000Z",
            }

    monkeypatch.setattr("src.core.register_v2.ChatGPTClient", FakeClient)

    result = engine.run()

    assert result.success is True
    assert result.access_token == "access-token"
    assert result.refresh_token == "refresh-token"
    assert result.id_token == "id-token"
    assert result.session_token == "session-token"
    assert result.expires_at == datetime.fromisoformat("2026-05-26T10:00:00+00:00")
    assert result.last_refresh is not None
    assert result.metadata["expires"] == "2026-05-26T10:00:00.000Z"


def test_registration_engine_sets_timezone_aware_last_refresh_without_session_expires(monkeypatch) -> None:
    engine = RegistrationEngineV2(DummyEmailService(30), callback_logger=lambda message: None)
    engine.email = "user@example.test"
    engine.email_info = {"email": "user@example.test", "service_id": "user@example.test"}
    monkeypatch.setattr("src.core.register_v2.generate_random_password", lambda length: "Password123")
    monkeypatch.setattr("src.core.register_v2.generate_random_name", lambda: ("Jane", "Doe"))
    monkeypatch.setattr("src.core.register_v2.generate_random_birthday", lambda: "2000-01-01")
    monkeypatch.setattr(RegistrationEngineV2, "_prepare_email", lambda self: True)

    class FakeClient:
        device_id = "device-12345678"

        def __init__(self, **kwargs) -> None:
            self._log = lambda message: None

        def register_complete_flow(self, *args, **kwargs):
            return True, "注册成功"

        def reuse_session_and_get_tokens(self):
            return True, {
                "access_token": "access-token",
                "session_token": "session-token",
                "account_id": "account-123",
            }

    monkeypatch.setattr("src.core.register_v2.ChatGPTClient", FakeClient)

    result = engine.run()

    assert result.success is True
    assert result.expires_at is None
    assert result.last_refresh is not None
    assert result.last_refresh.tzinfo is timezone.utc


def test_registration_engine_save_to_database_persists_expiry_fields(monkeypatch) -> None:
    engine = RegistrationEngineV2(DummyEmailService(30), callback_logger=lambda message: None)
    engine.email_info = {"service_id": "user@example.test"}
    result = RegistrationResult(
        success=True,
        email="user@example.test",
        password="Password123",
        account_id="account-123",
        workspace_id="workspace-123",
        access_token="access-token",
        refresh_token="refresh-token",
        id_token="id-token",
        session_token="session-token",
        expires_at=datetime.fromisoformat("2026-05-26T10:00:00+00:00"),
        last_refresh=datetime.fromisoformat("2026-05-19T10:00:00+00:00"),
        metadata={"expires": "2026-05-26T10:00:00.000Z"},
    )
    captured = {}

    class DummySettings:
        openai_client_id = "client-123"

    class DummyDbContext:
        def __enter__(self):
            return object()

        def __exit__(self, exc_type, exc, traceback):
            return False

    class DummyAccount:
        id = 123

    def create_account(db, **kwargs):
        captured.update(kwargs)
        return DummyAccount()

    monkeypatch.setattr("src.core.register_v2.get_settings", lambda: DummySettings())
    monkeypatch.setattr("src.core.register_v2.get_db", lambda: DummyDbContext())
    monkeypatch.setattr("src.core.register_v2.crud.create_account", create_account)

    assert engine.save_to_database(result) is True
    assert captured["expires_at"] == result.expires_at
    assert captured["last_refresh"] == result.last_refresh


def test_registration_engine_prepare_email_replaces_previous_retry_email() -> None:
    engine = RegistrationEngineV2(RotatingEmailService(), callback_logger=lambda message: None)
    engine.email = "old@example.test"

    assert engine._prepare_email() is True
    assert engine.email == "new@example.test"
    assert engine.email_info == {
        "email": "new@example.test",
        "service_id": "new@example.test",
        "id": "new@example.test",
    }


def test_email_service_adapter_caps_configured_timeout() -> None:
    adapter = EmailServiceAdapter(DummyEmailService(999), {}, lambda message: None)

    assert adapter.verification_timeout == 180


def test_email_service_adapter_uses_default_for_invalid_timeout() -> None:
    adapter = EmailServiceAdapter(DummyEmailService("invalid"), {}, lambda message: None)

    assert adapter.verification_timeout == 30
