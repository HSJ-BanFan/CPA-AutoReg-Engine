import socket
import time
from email.utils import formatdate

import pytest
from fastapi import HTTPException

from src.config.constants import EMAIL_SERVICE_DEFAULTS, EmailServiceType
from src.services import EmailServiceFactory, ImapMailService
from src.services.imap_mail import MAX_MESSAGE_BYTES
from src.web.routes.email import get_service_types, validate_service_config


class FakeIMAP:
    messages: list[bytes] = []
    message_batches: list[list[bytes]] | None = None
    internal_dates: dict[int, float | None] = {}
    instances: list["FakeIMAP"] = []
    fail_first_select = False
    size_response_as_bytes = False

    def __init__(self, host: str, port: int, timeout: int | None = None) -> None:
        self.host = host
        self.port = port
        self.timeout = timeout
        self.logged_in: tuple[str, str] | None = None
        self.selected_folder: str | None = None
        self.search_criteria: tuple[str, ...] | None = None
        self.fetches: list[tuple[bytes, str]] = []
        self.logged_out = False
        self.search_calls = 0
        self.active_messages: list[bytes] = []
        FakeIMAP.instances.append(self)

    def login(self, email: str, password: str) -> tuple[str, list[bytes]]:
        self.logged_in = (email, password)
        return "OK", [b"authenticated"]

    def select(self, folder: str) -> tuple[str, list[bytes]]:
        if FakeIMAP.fail_first_select and len(FakeIMAP.instances) == 1:
            raise OSError("connection dropped")
        self.selected_folder = folder
        messages = self.active_messages or FakeIMAP.messages
        return "OK", [str(len(messages)).encode()]

    def search(self, charset, *criteria: str) -> tuple[str, list[bytes]]:
        self.search_criteria = criteria
        if FakeIMAP.message_batches:
            batch_index = min(self.search_calls, len(FakeIMAP.message_batches) - 1)
            self.active_messages = FakeIMAP.message_batches[batch_index]
        else:
            self.active_messages = FakeIMAP.messages
        self.search_calls += 1
        ids = b" ".join(str(index + 1).encode() for index in range(len(self.active_messages)))
        return "OK", [ids]

    def fetch(self, message_id: bytes, query: str) -> tuple[str, list[tuple[bytes, bytes]]]:
        self.fetches.append((message_id, query))
        index = int(message_id.decode()) - 1
        if "RFC822.SIZE" in query:
            size = len(self.active_messages[index])
            response = f"{message_id.decode()} (RFC822.SIZE {size})".encode()
            if FakeIMAP.size_response_as_bytes:
                return "OK", [response]
            return "OK", [(b"RFC822.SIZE", response)]
        if "INTERNALDATE" in query:
            internal_date = FakeIMAP.internal_dates.get(index)
            if internal_date is None:
                return "NO", []
            timestamp = time.strftime("%d-%b-%Y %H:%M:%S +0000", time.gmtime(internal_date))
            response = f'{message_id.decode()} (INTERNALDATE "{timestamp}")'.encode()
            return "OK", [(b"INTERNALDATE", response)]
        return "OK", [(b"RFC822", self.active_messages[index])]

    def logout(self) -> tuple[str, list[bytes]]:
        self.logged_out = True
        return "BYE", [b"logout"]


@pytest.fixture(autouse=True)
def fake_imap(monkeypatch) -> list[str]:
    FakeIMAP.messages = []
    FakeIMAP.message_batches = None
    FakeIMAP.internal_dates = {}
    FakeIMAP.instances = []
    FakeIMAP.fail_first_select = False
    FakeIMAP.size_response_as_bytes = False
    monkeypatch.setattr("imaplib.IMAP4_SSL", FakeIMAP)

    lookups = []

    def getaddrinfo(host: str, port, *args, **kwargs):
        lookups.append(host)
        if host != "imap.gmail.com":
            raise socket.gaierror()
        return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("142.250.191.109", 0))]

    monkeypatch.setattr(socket, "getaddrinfo", getaddrinfo)
    return lookups


@pytest.fixture
def imap_config() -> dict:
    return {
        "domain": "example.com",
        "host": "imap.gmail.com",
        "port": 993,
        "use_ssl": True,
        "email": "inbox@gmail.com",
        "password": "app-password",
        "folder": "INBOX",
        "timeout": 5,
        "poll_interval": 0,
        "mail_limit": 10,
    }


def make_raw_message(
    *,
    to: str,
    body: str,
    subject: str = "OpenAI verification",
    delivered_to: str | None = None,
    date: float | None = None,
    content_type: str = "text/plain",
) -> bytes:
    headers = [
        "From: noreply@openai.com",
        f"To: {to}",
        f"Subject: {subject}",
        f"Date: {formatdate(date or time.time(), usegmt=True)}",
        f"Content-Type: {content_type}; charset=utf-8",
    ]
    if delivered_to:
        headers.append(f"Delivered-To: {delivered_to}")
    return ("\r\n".join(headers) + "\r\n\r\n" + body).encode()


def test_create_email_generates_catch_all_address(imap_config: dict) -> None:
    service = ImapMailService(imap_config)

    email_info = service.create_email()

    assert email_info["email"].endswith("@example.com")
    assert email_info["service_id"] == email_info["email"]
    assert email_info["id"] == email_info["email"]
    assert service.list_emails()[0]["email"] == email_info["email"]


def test_default_imap_host_is_empty() -> None:
    assert EMAIL_SERVICE_DEFAULTS[EmailServiceType.IMAP_MAIL.value]["host"] == ""


@pytest.mark.parametrize("missing_key", ["domain", "host", "email", "password"])
def test_config_requires_imap_fields(imap_config: dict, missing_key: str) -> None:
    config = {key: value for key, value in imap_config.items() if key != missing_key}

    with pytest.raises(ValueError, match=missing_key):
        ImapMailService(config)


def test_get_verification_code_reads_forwarded_mail_for_generated_recipient(imap_config: dict, fake_imap: list[str]) -> None:
    service = ImapMailService(imap_config)
    email_info = service.create_email({"name": "tmpabc123"})
    FakeIMAP.messages = [
        make_raw_message(
            to=email_info["email"],
            body="Your verification code is 123456.",
        )
    ]

    code = service.get_verification_code(email_info["email"], timeout=1)

    assert code == "123456"
    assert FakeIMAP.instances[0].host == "imap.gmail.com"
    assert FakeIMAP.instances[0].port == 993
    assert FakeIMAP.instances[0].logged_in == ("inbox@gmail.com", "app-password")
    assert FakeIMAP.instances[0].selected_folder == "INBOX"
    assert FakeIMAP.instances[0].logged_out is True
    assert fake_imap.count("imap.gmail.com") >= 2


def test_get_verification_code_reconnects_after_connection_drop(imap_config: dict) -> None:
    service = ImapMailService(imap_config)
    email_info = service.create_email({"name": "tmpreconnect"})
    FakeIMAP.fail_first_select = True
    FakeIMAP.messages = [
        make_raw_message(
            to=email_info["email"],
            body="Your verification code is 234567.",
        )
    ]

    code = service.get_verification_code(email_info["email"], timeout=1)

    assert code == "234567"
    assert len(FakeIMAP.instances) == 2
    assert FakeIMAP.instances[0].logged_out is True
    assert FakeIMAP.instances[1].logged_out is True


def test_get_verification_code_checks_size_before_fetching_body(imap_config: dict) -> None:
    service = ImapMailService(imap_config)
    email_info = service.create_email({"name": "tmpsizecheck"})
    FakeIMAP.messages = [
        make_raw_message(
            to=email_info["email"],
            body="Your verification code is 345678.",
        ),
        make_raw_message(
            to=email_info["email"],
            body="Your verification code is 999999." + ("x" * MAX_MESSAGE_BYTES),
        ),
    ]

    code = service.get_verification_code(email_info["email"], timeout=1)

    assert code == "345678"
    assert (b"2", "(RFC822.SIZE)") in FakeIMAP.instances[0].fetches
    assert (b"2", "(RFC822)") not in FakeIMAP.instances[0].fetches


def test_get_verification_code_extracts_openai_code_from_html(imap_config: dict) -> None:
    service = ImapMailService(imap_config)
    email_info = service.create_email({"name": "tmphtmlcode"})
    FakeIMAP.messages = [
        make_raw_message(
            to=email_info["email"],
            content_type="text/html",
            body=(
                '<html><style>.color{color:#123456}</style>'
                '<p>Enter this temporary verification code to continue:</p>'
                + ('<span>padding</span>' * 8)
                + '<table><tr><td><strong>567890</strong></td></tr></table>'
                '<p>Footer 987654</p></html>'
            ),
        )
    ]

    assert service.get_verification_code(email_info["email"], timeout=1) == "567890"


def test_get_verification_code_accepts_size_response_as_bytes(imap_config: dict) -> None:
    service = ImapMailService(imap_config)
    email_info = service.create_email({"name": "tmpbytesize"})
    FakeIMAP.size_response_as_bytes = True
    FakeIMAP.messages = [
        make_raw_message(
            to=email_info["email"],
            body="Your verification code is 456789.",
        )
    ]

    assert service.get_verification_code(email_info["email"], timeout=1) == "456789"


def test_get_verification_code_matches_delivered_to_header(imap_config: dict) -> None:
    service = ImapMailService(imap_config)
    email_info = service.create_email({"name": "tmpdelivered"})
    FakeIMAP.messages = [
        make_raw_message(
            to="inbox@gmail.com",
            delivered_to=email_info["email"],
            body="Security code: 654321",
        )
    ]

    assert service.get_verification_code(email_info["email"], timeout=1) == "654321"


def test_get_verification_code_ignores_other_catch_all_recipients(imap_config: dict) -> None:
    service = ImapMailService(imap_config)
    email_info = service.create_email({"name": "tmpwanted"})
    FakeIMAP.messages = [
        make_raw_message(
            to="other@example.com",
            body="Your verification code is 111111.",
        ),
        make_raw_message(
            to=email_info["email"],
            body="Your verification code is 222222.",
        ),
    ]

    assert service.get_verification_code(email_info["email"], timeout=1) == "222222"


def test_get_verification_code_skips_old_messages(imap_config: dict) -> None:
    service = ImapMailService(imap_config)
    email_info = service.create_email({"name": "tmpnewonly"})
    FakeIMAP.messages = [
        make_raw_message(
            to=email_info["email"],
            body="Your verification code is 111111.",
            date=time.time() - 300,
        )
    ]

    assert service.get_verification_code(email_info["email"], timeout=0.01, otp_sent_at=time.time()) is None


def test_get_verification_code_accepts_recent_internaldate_with_old_header_date(imap_config: dict) -> None:
    service = ImapMailService(imap_config)
    email_info = service.create_email({"name": "tmpinternaldate"})
    otp_sent_at = time.time()
    FakeIMAP.messages = [
        make_raw_message(
            to=email_info["email"],
            body="Your verification code is 112233.",
            date=otp_sent_at - 300,
        )
    ]
    FakeIMAP.internal_dates = {0: otp_sent_at + 1}

    assert service.get_verification_code(email_info["email"], timeout=1, otp_sent_at=otp_sent_at) == "112233"


def test_get_verification_code_polls_until_message_arrives(imap_config: dict) -> None:
    service = ImapMailService(imap_config)
    email_info = service.create_email({"name": "tmppolling"})
    FakeIMAP.message_batches = [
        [],
        [
            make_raw_message(
                to=email_info["email"],
                body="Your verification code is 223344.",
            )
        ],
    ]

    assert service.get_verification_code(email_info["email"], timeout=1) == "223344"


def test_fetch_internal_timestamp_does_not_depend_on_strptime_locale(monkeypatch) -> None:
    real_datetime = __import__("datetime").datetime

    class StrictDateTime:
        def __new__(cls, *args, **kwargs):
            return real_datetime(*args, **kwargs)

        @staticmethod
        def strptime(*args, **kwargs):
            raise AssertionError("strptime should not be used")

    monkeypatch.setattr("src.services.imap_mail.datetime", StrictDateTime)
    FakeIMAP.internal_dates = {0: 1779529645.0}
    client = FakeIMAP("imap.gmail.com", 993)

    assert ImapMailService._fetch_internal_timestamp(client, b"1") == pytest.approx(1779529645.0)


def test_get_verification_code_returns_none_after_timeout(imap_config: dict) -> None:
    service = ImapMailService(imap_config)
    email_info = service.create_email({"name": "tmpempty"})

    assert service.get_verification_code(email_info["email"], timeout=0) is None


def test_config_allows_dns_fake_ip_for_proxy_mode(imap_config: dict, monkeypatch) -> None:
    def getaddrinfo(host: str, port, *args, **kwargs):
        assert host == "imap.gmail.com"
        return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("198.18.0.254", 0))]

    monkeypatch.setattr(socket, "getaddrinfo", getaddrinfo)

    service = ImapMailService(imap_config)

    assert service.config["host"] == "imap.gmail.com"


def test_config_requires_ssl(imap_config: dict) -> None:
    config = {**imap_config, "use_ssl": False}

    with pytest.raises(ValueError, match="SSL"):
        ImapMailService(config)


def test_route_config_validation_requires_ssl(imap_config: dict) -> None:
    config = {**imap_config, "use_ssl": False}

    with pytest.raises(HTTPException) as exc_info:
        validate_service_config(EmailServiceType.IMAP_MAIL, config)

    assert exc_info.value.status_code == 400


@pytest.mark.parametrize(
    "config",
    [
        {"host": "imap.gmail.com", "email": "inbox@gmail.com", "password": "app-password"},
        {"domain": "example.com", "email": "inbox@gmail.com", "password": "app-password"},
        {"domain": "example.com", "host": "imap.gmail.com", "password": "app-password"},
        {"domain": "example.com", "host": "imap.gmail.com", "email": "inbox@gmail.com"},
    ],
)
def test_route_config_validation_requires_imap_fields(config: dict) -> None:
    with pytest.raises(HTTPException) as exc_info:
        validate_service_config(EmailServiceType.IMAP_MAIL, config)

    assert exc_info.value.status_code == 400


def test_imap_mail_service_is_registered_in_factory(imap_config: dict) -> None:
    assert EmailServiceType.IMAP_MAIL in EmailServiceFactory.get_available_services()

    service = EmailServiceFactory.create(EmailServiceType.IMAP_MAIL, imap_config)

    assert isinstance(service, ImapMailService)


@pytest.mark.anyio
async def test_service_types_api_includes_imap_mail() -> None:
    service_types = await get_service_types()

    assert any(service_type["value"] == EmailServiceType.IMAP_MAIL.value for service_type in service_types["types"])
