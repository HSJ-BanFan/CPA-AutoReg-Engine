import imaplib
import ipaddress
import logging
import re
import secrets
import socket
import time
from datetime import datetime, timedelta, timezone
from email import message_from_bytes, policy
from email.message import Message
from email.utils import getaddresses, parsedate_to_datetime
from typing import Any, Optional

from ..config.constants import EMAIL_SERVICE_DEFAULTS, OTP_CODE_PATTERN, EmailServiceType
from .base import BaseEmailService, EmailServiceError

logger = logging.getLogger(__name__)

MIN_LOCAL_PART_LENGTH = 8
MAX_LOCAL_PART_LENGTH = 16
DEFAULT_IMAP_PORT = 993
DEFAULT_IMAP_TIMEOUT = 30
DEFAULT_POLL_INTERVAL = 5
DEFAULT_MAIL_LIMIT = 20
OTP_TIMESTAMP_SKEW_SECONDS = 10
INTERNAL_HOSTS = {"localhost", "localhost.localdomain"}
INTERNAL_HOST_SUFFIXES = (".localhost", ".local", ".internal", ".lan")
DNS_FAKE_IP_NETWORKS = (ipaddress.ip_network("198.18.0.0/15"),)
MAX_MESSAGE_BYTES = 200_000
MAX_PART_BYTES = 100_000
IMAP_MONTHS = {
    "jan": 1,
    "feb": 2,
    "mar": 3,
    "apr": 4,
    "may": 5,
    "jun": 6,
    "jul": 7,
    "aug": 8,
    "sep": 9,
    "oct": 10,
    "nov": 11,
    "dec": 12,
}
CONTEXT_CODE_PATTERNS = (
    r"enter\s+this\s+temporary\s+verification\s+code\s+to\s+continue:?[\s\S]{0,1200}?(\d{6})",
    r"(?:verification\s+code|security\s+code|one[-\s]?time\s+code|otp|code|验证码|驗證碼)[^\d]{0,40}(\d{6})",
    r"(\d{6})[^\n]{0,40}(?:is\s+your|your\s+code|verification\s+code|验证码|驗證碼)",
)
RECIPIENT_HEADERS = ("to", "delivered-to", "x-original-to", "envelope-to")


def validate_public_imap_host(host: Any) -> str:
    value = str(host or "").strip().lower().rstrip(".")
    if not value:
        raise ValueError("host 不能为空")
    if value in INTERNAL_HOSTS or value.endswith(INTERNAL_HOST_SUFFIXES):
        raise ValueError("host 不能指向本机或内网地址")
    try:
        _validate_public_address(value)
        return value
    except ValueError:
        for resolved_address in _resolve_host_addresses(value):
            if _is_dns_fake_ip(resolved_address):
                continue
            _validate_public_address(resolved_address)
        return value


def _is_dns_fake_ip(address_value: str) -> bool:
    try:
        address = ipaddress.ip_address(address_value.strip("[]"))
    except ValueError:
        return False
    return any(address in network for network in DNS_FAKE_IP_NETWORKS)


def _validate_public_address(address_value: str) -> None:
    try:
        address = ipaddress.ip_address(address_value.strip("[]"))
    except ValueError as e:
        raise ValueError("host 不能指向本机或内网地址") from e
    if not address.is_global:
        raise ValueError("host 不能指向本机或内网地址")


def _resolve_host_addresses(hostname: str) -> set[str]:
    try:
        infos = socket.getaddrinfo(hostname, None, type=socket.SOCK_STREAM)
    except socket.gaierror as e:
        raise ValueError("host 主机名无法解析") from e
    return {str(info[4][0]) for info in infos if info[4]}


def normalize_imap_config(config: dict[str, Any]) -> dict[str, Any]:
    return ImapMailService._normalize_config(config)


class ImapMailService(BaseEmailService):
    def __init__(self, config: dict[str, Any], name: str = None) -> None:
        super().__init__(EmailServiceType.IMAP_MAIL, name)
        self.config = self._normalize_config(config)
        self._created_emails: dict[str, dict[str, Any]] = {}

    def create_email(self, config: dict[str, Any] = None) -> dict[str, Any]:
        local_part = self._generate_local_part((config or {}).get("name"))
        email_address = f"{local_part}@{self.config['domain']}"
        email_info = {
            "email": email_address,
            "service_id": email_address,
            "id": email_address,
        }
        self._created_emails[email_address] = dict(email_info)
        self.update_status(True)
        return email_info

    def get_verification_code(
        self,
        email: str,
        email_id: str = None,
        timeout: int = 120,
        pattern: str = OTP_CODE_PATTERN,
        otp_sent_at: Optional[float] = None,
        exclude_codes: Optional[set[str]] = None,
    ) -> Optional[str]:
        start_time = time.time()
        seen_message_ids: set[str] = set()
        excluded_codes = exclude_codes or set()
        client = None

        try:
            client = self._connect_logged_in()
            while time.time() - start_time < timeout:
                try:
                    for message in self._fetch_recent_messages(client):
                        message_id = message.get("message_id") or message.get("imap_id")
                        if message_id in seen_message_ids:
                            continue
                        if message_id:
                            seen_message_ids.add(message_id)
                        if not self._is_for_recipient(message["message"], email):
                            continue
                        if self._is_before_otp(message.get("received_ts"), otp_sent_at):
                            continue
                        code = self._extract_code(message.get("content", ""), pattern)
                        if code and code not in excluded_codes:
                            self.update_status(True)
                            return code
                except EmailServiceError:
                    logger.warning("IMAP 邮箱拉取验证码失败")
                    self.update_status(False, EmailServiceError("IMAP 邮箱拉取验证码失败"))
                    raise
                except Exception:
                    logger.warning("IMAP 邮箱拉取验证码失败")
                    self.update_status(False, EmailServiceError("IMAP 邮箱拉取验证码失败"))
                    if client is not None:
                        self._logout(client)
                    client = self._connect_logged_in()

                time.sleep(self.config["poll_interval"])
        finally:
            if client is not None:
                self._logout(client)

        return None

    def list_emails(self, **kwargs) -> list[dict[str, Any]]:
        return list(self._created_emails.values())

    def delete_email(self, email_id: str) -> bool:
        self._created_emails.pop(email_id, None)
        return True

    def check_health(self) -> bool:
        client = None
        try:
            client = self._connect_logged_in()
            self.update_status(True)
            return True
        except Exception:
            logger.warning("IMAP 邮箱健康检查失败")
            self.update_status(False, EmailServiceError("IMAP 邮箱健康检查失败"))
            return False
        finally:
            if client is not None:
                self._logout(client)

    @staticmethod
    def _extract_code(content: str, pattern: str = OTP_CODE_PATTERN) -> Optional[str]:
        for context_pattern in CONTEXT_CODE_PATTERNS:
            match = re.search(context_pattern, content, re.IGNORECASE | re.DOTALL)
            if match:
                return match.group(1)
        matches = re.findall(pattern, content)
        if len(set(matches)) == 1:
            return matches[0]
        return None

    @classmethod
    def _normalize_config(cls, config: dict[str, Any]) -> dict[str, Any]:
        defaults = EMAIL_SERVICE_DEFAULTS[EmailServiceType.IMAP_MAIL.value]
        provided_config = config or {}
        required = ("domain", "host", "email", "password")
        missing = [key for key in required if not provided_config.get(key)]
        if missing:
            raise ValueError(f"缺少必需配置: {', '.join(missing)}")
        normalized = {**defaults, **provided_config}
        normalized["domain"] = str(normalized["domain"]).strip().lower().lstrip("@")
        normalized["host"] = validate_public_imap_host(normalized["host"])
        normalized["port"] = int(normalized.get("port") or DEFAULT_IMAP_PORT)
        normalized["use_ssl"] = cls._parse_bool(normalized.get("use_ssl", True))
        if not normalized["use_ssl"]:
            raise ValueError("IMAP 必须启用 SSL")
        normalized["timeout"] = int(normalized.get("timeout") or DEFAULT_IMAP_TIMEOUT)
        poll_interval = normalized.get("poll_interval")
        normalized["poll_interval"] = float(DEFAULT_POLL_INTERVAL if poll_interval is None else poll_interval)
        normalized["mail_limit"] = int(normalized.get("mail_limit") or DEFAULT_MAIL_LIMIT)
        normalized["folder"] = str(normalized.get("folder") or "INBOX")
        return normalized

    @staticmethod
    def _parse_bool(value: Any) -> bool:
        if isinstance(value, bool):
            return value
        return str(value).strip().lower() not in {"0", "false", "no", "off"}

    @staticmethod
    def _generate_local_part(name: Any = None) -> str:
        if name:
            value = re.sub(r"[^a-zA-Z0-9._-]", "", str(name).strip().lower())
            if MIN_LOCAL_PART_LENGTH <= len(value) <= 64:
                return value
        token = secrets.token_hex(6)
        return f"tmp{token}"[:MAX_LOCAL_PART_LENGTH]

    def _connect_logged_in(self):
        client = self._connect()
        self._login(client)
        return client

    def _connect(self):
        host = validate_public_imap_host(self.config["host"])
        if self.config["use_ssl"]:
            return imaplib.IMAP4_SSL(
                host,
                self.config["port"],
                timeout=self.config["timeout"],
            )
        return imaplib.IMAP4(
            host,
            self.config["port"],
            timeout=self.config["timeout"],
        )

    def _login(self, client) -> None:
        status, _ = client.login(self.config["email"], self.config["password"])
        if status != "OK":
            raise EmailServiceError("IMAP 登录失败")

    def _fetch_recent_messages(self, client) -> list[dict[str, Any]]:
        status, _ = client.select(self.config["folder"])
        if status != "OK":
            raise EmailServiceError("IMAP 选择文件夹失败")
        status, data = client.search(None, "ALL")
        if status != "OK":
            raise EmailServiceError("IMAP 搜索邮件失败")
        message_ids = (data[0] or b"").split()[-self.config["mail_limit"] :]
        messages = []
        for message_id in reversed(message_ids):
            message_size = self._fetch_message_size(client, message_id)
            if message_size is None or message_size > MAX_MESSAGE_BYTES:
                continue
            status, payload = client.fetch(message_id, "(RFC822)")
            if status != "OK" or not payload:
                continue
            raw_message = self._extract_raw_message(payload)
            if not raw_message:
                continue
            parsed_message = message_from_bytes(raw_message, policy=policy.default)
            received_ts = self._fetch_internal_timestamp(client, message_id)
            messages.append(
                {
                    "imap_id": message_id.decode(errors="ignore"),
                    "message_id": parsed_message.get("Message-ID", ""),
                    "message": parsed_message,
                    "received_ts": received_ts if received_ts is not None else self._message_timestamp(parsed_message),
                    "content": self._message_content(parsed_message),
                }
            )
        return messages

    @staticmethod
    def _fetch_message_size(client, message_id: bytes) -> Optional[int]:
        status, payload = client.fetch(message_id, "(RFC822.SIZE)")
        if status != "OK" or not payload:
            return None
        for item in payload:
            value = ""
            if isinstance(item, tuple) and len(item) >= 2:
                value = item[1].decode(errors="ignore") if isinstance(item[1], bytes) else str(item[1])
            elif isinstance(item, bytes):
                value = item.decode(errors="ignore")
            if value:
                match = re.search(r"RFC822\.SIZE\s+(\d+)", value)
                if match:
                    return int(match.group(1))
        return None

    @staticmethod
    def _extract_raw_message(payload: list[Any]) -> bytes | None:
        for item in payload:
            if isinstance(item, tuple) and len(item) >= 2 and isinstance(item[1], bytes):
                return item[1]
        return None

    @staticmethod
    def _fetch_internal_timestamp(client, message_id: bytes) -> Optional[float]:
        status, payload = client.fetch(message_id, "(INTERNALDATE)")
        if status != "OK" or not payload:
            return None
        for item in payload:
            value = ""
            if isinstance(item, tuple) and len(item) >= 2:
                value = item[1].decode(errors="ignore") if isinstance(item[1], bytes) else str(item[1])
            elif isinstance(item, bytes):
                value = item.decode(errors="ignore")
            if value:
                return ImapMailService._parse_internal_timestamp_value(value)
        return None

    @staticmethod
    def _parse_internal_timestamp_value(value: str) -> Optional[float]:
        match = re.search(r'INTERNALDATE\s+"([^"]+)"', value)
        if not match:
            return None
        date_value = match.group(1).strip()
        parsed = re.fullmatch(
            r"(\d{1,2})-([A-Za-z]{3})-(\d{4}) (\d{2}):(\d{2}):(\d{2}) ([+-])(\d{2})(\d{2})",
            date_value,
        )
        if not parsed:
            return None
        day, month_name, year, hour, minute, second, sign, tz_hour, tz_minute = parsed.groups()
        month = IMAP_MONTHS.get(month_name.lower())
        if month is None:
            return None
        offset_minutes = (int(tz_hour) * 60) + int(tz_minute)
        if sign == "-":
            offset_minutes = -offset_minutes
        try:
            tz_info = timezone(timedelta(minutes=offset_minutes))
            return datetime(
                int(year),
                month,
                int(day),
                int(hour),
                int(minute),
                int(second),
                tzinfo=tz_info,
            ).timestamp()
        except ValueError:
            return None

    @staticmethod
    def _message_timestamp(message: Message) -> Optional[float]:
        date_value = message.get("Date")
        if not date_value:
            return None
        try:
            return parsedate_to_datetime(date_value).timestamp()
        except (TypeError, ValueError, OverflowError):
            return None

    @staticmethod
    def _message_content(message: Message) -> str:
        parts = [str(message.get("Subject", ""))]
        if message.is_multipart():
            for part in message.walk():
                content_type = part.get_content_type()
                disposition = str(part.get("Content-Disposition", "")).lower()
                if content_type in {"text/plain", "text/html"} and "attachment" not in disposition:
                    parts.append(ImapMailService._part_content(part))
        else:
            parts.append(ImapMailService._part_content(message))
        return "\n".join(part for part in parts if part)

    @staticmethod
    def _part_content(part: Message) -> str:
        try:
            payload = part.get_payload(decode=True)
            if isinstance(payload, bytes):
                return payload[:MAX_PART_BYTES].decode(part.get_content_charset() or "utf-8", errors="replace")
            content = part.get_payload() or ""
            return str(content)[:MAX_PART_BYTES]
        except Exception:
            return ""

    @staticmethod
    def _is_for_recipient(message: Message, email_address: str) -> bool:
        target = email_address.lower()
        values: list[str] = []
        for header in RECIPIENT_HEADERS:
            values.extend(message.get_all(header, []))
        addresses = [address.lower() for _, address in getaddresses(values)]
        return target in addresses or any(target in value.lower() for value in values)

    @staticmethod
    def _is_before_otp(received_ts: Optional[float], otp_sent_at: Optional[float]) -> bool:
        return bool(received_ts and otp_sent_at and received_ts < (otp_sent_at - OTP_TIMESTAMP_SKEW_SECONDS))

    @staticmethod
    def _logout(client) -> None:
        try:
            client.logout()
        except Exception:
            pass
