import socket

import pytest
from fastapi import HTTPException

from src.config.constants import EmailServiceType
from src.services import CloudflareTempEmailService, EmailServiceFactory
from src.services.base import EmailServiceError
from src.web.routes.email import get_service_types, validate_service_config


@pytest.fixture(autouse=True)
def fake_dns_resolution(monkeypatch) -> None:
    addresses = {
        "mail.example.test": "93.184.216.34",
        "private.example.test": "10.0.0.10",
        "fakeproxy.example.test": "198.18.0.227",
    }

    def getaddrinfo(host: str, port, *args, **kwargs):
        if host not in addresses:
            raise socket.gaierror()
        return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", (addresses[host], 0))]

    monkeypatch.setattr(socket, "getaddrinfo", getaddrinfo)


class FakeResponse:
    def __init__(self, payload: dict, status_code: int = 200, text: str | None = None) -> None:
        self.payload = payload
        self.status_code = status_code
        self.text = text if text is not None else "{}"

    def json(self) -> dict:
        return self.payload


class FakeSession:
    def __init__(self, responses: list[FakeResponse]) -> None:
        self.responses = responses
        self.requests: list[dict] = []
        self.headers: dict = {}

    def request(self, method: str, url: str, **kwargs) -> FakeResponse:
        self.requests.append({"method": method, "url": url, **kwargs})
        if not self.responses:
            raise AssertionError("unexpected HTTP request")
        return self.responses.pop(0)


def make_service(session: FakeSession | None = None) -> CloudflareTempEmailService:
    service = CloudflareTempEmailService(
        {
            "base_url": "https://mail.example.test",
            "domain": "example.test",
            "timeout": 15,
            "poll_interval": 0,
            "mail_limit": 10,
        }
    )
    if session is not None:
        service.session = session
    return service


def test_create_email_posts_new_address_and_keeps_jwt_in_runtime_cache() -> None:
    session = FakeSession(
        [
            FakeResponse(
                {
                    "address": "tmp123@example.test",
                    "jwt": "mailbox.jwt.token",
                    "password": "secret-password",
                    "address_id": 345,
                }
            )
        ]
    )
    service = make_service(session)

    email_info = service.create_email({"name": "tmp123"})

    assert email_info == {
        "email": "tmp123@example.test",
        "service_id": "tmp123@example.test",
        "id": "tmp123@example.test",
        "address_id": 345,
    }
    assert service._created_emails["tmp123@example.test"]["jwt"] == "mailbox.jwt.token"
    assert "jwt" not in service.list_emails()[0]
    assert "password" not in service.list_emails()[0]
    assert session.requests[0]["method"] == "POST"
    assert session.requests[0]["url"] == "https://mail.example.test/api/new_address"
    assert session.requests[0]["json"] == {"name": "tmp123", "domain": "example.test"}
    assert session.requests[0]["allow_redirects"] is False


def test_create_email_requires_address_and_jwt() -> None:
    session = FakeSession([FakeResponse({"address": "tmp123@example.test"})])
    service = make_service(session)

    with pytest.raises(EmailServiceError, match="jwt"):
        service.create_email({"name": "tmp123"})


def test_get_verification_code_reads_code_from_mail_list_with_bearer_jwt() -> None:
    session = FakeSession(
        [
            FakeResponse(
                {
                    "results": [
                        {
                            "id": "msg-1",
                            "subject": "OpenAI verification",
                            "text": "Your code is 123456.",
                            "html": "",
                        }
                    ],
                    "count": 1,
                }
            )
        ]
    )
    service = make_service(session)
    service._created_emails["tmp123@example.test"] = {"jwt": "mailbox.jwt.token"}

    code = service.get_verification_code("tmp123@example.test", timeout=1)

    assert code == "123456"
    assert session.requests[0]["method"] == "GET"
    assert session.requests[0]["url"] == "https://mail.example.test/api/parsed_mails"
    assert session.requests[0]["params"] == {"limit": 10, "offset": 0}
    assert session.requests[0]["headers"] == {"Authorization": "Bearer mailbox.jwt.token"}


def test_get_verification_code_fetches_detail_when_list_item_lacks_body() -> None:
    session = FakeSession(
        [
            FakeResponse({"results": [{"id": "msg-1", "subject": "OpenAI"}], "count": 1}),
            FakeResponse({"id": "msg-1", "html": "<p>Verification code: 654321</p>"}),
        ]
    )
    service = make_service(session)
    service._created_emails["tmp123@example.test"] = {"jwt": "mailbox.jwt.token"}

    code = service.get_verification_code("tmp123@example.test", timeout=1)

    assert code == "654321"
    assert session.requests[1]["method"] == "GET"
    assert session.requests[1]["url"] == "https://mail.example.test/api/parsed_mail/msg-1"


def test_get_verification_code_ignores_non_dict_detail_response() -> None:
    class DefaultEmptySession(FakeSession):
        def request(self, method: str, url: str, **kwargs) -> FakeResponse:
            if self.responses:
                return super().request(method, url, **kwargs)
            self.requests.append({"method": method, "url": url, **kwargs})
            return FakeResponse({"results": [], "count": 0})

    session = DefaultEmptySession(
        [
            FakeResponse({"results": [{"id": "msg-1", "subject": "OpenAI"}], "count": 1}),
            FakeResponse([]),
        ]
    )
    service = make_service(session)
    service._created_emails["tmp123@example.test"] = {"jwt": "mailbox.jwt.token"}

    assert service.get_verification_code("tmp123@example.test", timeout=0.01) is None


def test_get_verification_code_raises_on_terminal_auth_error() -> None:
    session = FakeSession([FakeResponse({}, status_code=401)])
    service = make_service(session)
    service._created_emails["tmp123@example.test"] = {"jwt": "expired.jwt.token"}

    with pytest.raises(EmailServiceError, match="401"):
        service.get_verification_code("tmp123@example.test", timeout=1)


def test_get_verification_code_allows_small_mail_timestamp_skew() -> None:
    session = FakeSession(
        [
            FakeResponse(
                {
                    "results": [
                        {
                            "id": "msg-1",
                            "created_at": "2026-05-18T10:00:05Z",
                            "text": "Your verification code is 222222.",
                        }
                    ],
                    "count": 1,
                }
            )
        ]
    )
    service = make_service(session)
    service._created_emails["tmp123@example.test"] = {"jwt": "mailbox.jwt.token"}

    assert service.get_verification_code(
        "tmp123@example.test",
        timeout=1,
        otp_sent_at=1779098410.0,
    ) == "222222"


def test_get_verification_code_skips_old_detail_messages() -> None:
    class DefaultEmptySession(FakeSession):
        def request(self, method: str, url: str, **kwargs) -> FakeResponse:
            if self.responses:
                return super().request(method, url, **kwargs)
            self.requests.append({"method": method, "url": url, **kwargs})
            return FakeResponse({"results": [], "count": 0})

    session = DefaultEmptySession(
        [
            FakeResponse({"results": [{"id": "msg-1", "subject": "OpenAI"}], "count": 1}),
            FakeResponse(
                {
                    "id": "msg-1",
                    "created_at": "2026-05-18T10:00:00Z",
                    "text": "Your verification code is 111111.",
                }
            ),
        ]
    )
    service = make_service(session)
    service._created_emails["tmp123@example.test"] = {"jwt": "mailbox.jwt.token"}

    assert service.get_verification_code(
        "tmp123@example.test",
        timeout=0.01,
        otp_sent_at=1779098700.0,
    ) is None


def test_extract_code_prefers_verification_context() -> None:
    content = "tracking id 999999\nYour verification code is 123456."

    assert CloudflareTempEmailService._extract_code(content, r"(?<!\d)(\d{6})(?!\d)") == "123456"


def test_extract_code_rejects_ambiguous_unlabeled_numbers() -> None:
    content = "tracking id 999999\nreference 123456"

    assert CloudflareTempEmailService._extract_code(content, r"(?<!\d)(\d{6})(?!\d)") is None


def test_get_verification_code_returns_none_after_timeout() -> None:
    service = make_service(FakeSession([]))
    service._created_emails["tmp123@example.test"] = {"jwt": "mailbox.jwt.token"}

    assert service.get_verification_code("tmp123@example.test", timeout=0) is None


def test_missing_required_service_config_raises_value_error() -> None:
    with pytest.raises(ValueError, match="domain"):
        CloudflareTempEmailService({"base_url": "https://mail.example.test"})


def test_invalid_base_url_raises_value_error() -> None:
    with pytest.raises(ValueError, match="base_url"):
        CloudflareTempEmailService({"base_url": "file:///tmp/mail", "domain": "example.test"})


def test_private_base_url_raises_value_error() -> None:
    with pytest.raises(ValueError, match="base_url"):
        CloudflareTempEmailService({"base_url": "http://127.0.0.1:8787", "domain": "example.test"})


def test_base_url_rejects_hostname_resolving_to_private_address() -> None:
    with pytest.raises(ValueError, match="base_url"):
        CloudflareTempEmailService({"base_url": "https://private.example.test", "domain": "example.test"})


def test_base_url_allows_hostname_resolving_to_proxy_fake_ip() -> None:
    service = CloudflareTempEmailService({"base_url": "https://fakeproxy.example.test", "domain": "example.test"})

    assert service.config["base_url"] == "https://fakeproxy.example.test"


@pytest.mark.parametrize(
    "config",
    [
        {"base_url": "https://mail.example.test"},
        {"domain": "example.test"},
    ],
)
def test_route_config_validation_requires_base_url_and_domain(config: dict) -> None:
    with pytest.raises(HTTPException) as exc_info:
        validate_service_config(EmailServiceType.CLOUDFLARE_TEMP_EMAIL, config)

    assert exc_info.value.status_code == 400


def test_route_config_validation_rejects_invalid_base_url() -> None:
    with pytest.raises(HTTPException) as exc_info:
        validate_service_config(
            EmailServiceType.CLOUDFLARE_TEMP_EMAIL,
            {"base_url": "https://user:pass@mail.example.test", "domain": "example.test"},
        )

    assert exc_info.value.status_code == 400


def test_route_config_validation_rejects_private_base_url() -> None:
    with pytest.raises(HTTPException) as exc_info:
        validate_service_config(
            EmailServiceType.CLOUDFLARE_TEMP_EMAIL,
            {"base_url": "http://localhost:8787", "domain": "example.test"},
        )

    assert exc_info.value.status_code == 400


def test_route_config_validation_rejects_private_dns_resolution() -> None:
    with pytest.raises(HTTPException) as exc_info:
        validate_service_config(
            EmailServiceType.CLOUDFLARE_TEMP_EMAIL,
            {"base_url": "https://private.example.test", "domain": "example.test"},
        )

    assert exc_info.value.status_code == 400


@pytest.mark.parametrize(
    "service_type,config",
    [
        (EmailServiceType.FREEMAIL, {"base_url": "http://127.0.0.1:8787", "admin_token": "token"}),
        (
            EmailServiceType.CLOUD_MAIL,
            {
                "base_url": "http://127.0.0.1:8787",
                "admin_email": "admin@example.test",
                "admin_password": "password",
            },
        ),
    ],
)
def test_route_config_validation_rejects_private_base_url_for_custom_services(
    service_type: EmailServiceType,
    config: dict,
) -> None:
    with pytest.raises(HTTPException) as exc_info:
        validate_service_config(service_type, config)

    assert exc_info.value.status_code == 400


def test_cloudflare_temp_email_service_is_registered_in_factory() -> None:
    assert EmailServiceType.CLOUDFLARE_TEMP_EMAIL in EmailServiceFactory.get_available_services()

    service = EmailServiceFactory.create(
        EmailServiceType.CLOUDFLARE_TEMP_EMAIL,
        {"base_url": "https://mail.example.test", "domain": "example.test"},
    )

    assert isinstance(service, CloudflareTempEmailService)


@pytest.mark.anyio
async def test_service_types_api_includes_cloudflare_temp_email() -> None:
    service_types = await get_service_types()

    assert any(
        service_type["value"] == EmailServiceType.CLOUDFLARE_TEMP_EMAIL.value
        for service_type in service_types["types"]
    )
