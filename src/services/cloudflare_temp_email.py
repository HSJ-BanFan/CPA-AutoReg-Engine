"""
Cloudflare Temp Email 邮箱服务实现
"""

import ipaddress
import logging
import random
import re
import socket
import string
import time
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional
from urllib.parse import quote, urlparse

import requests

from ..config.constants import EmailServiceType, OTP_CODE_PATTERN
from .base import BaseEmailService, EmailServiceError

logger = logging.getLogger(__name__)

MIN_NAME_LENGTH = 8
MAX_NAME_LENGTH = 13
SENSITIVE_CACHE_FIELDS = {"jwt", "password"}
INTERNAL_HOSTS = {"localhost", "localhost.localdomain"}
INTERNAL_HOST_SUFFIXES = (".localhost", ".local", ".internal", ".lan")
DNS_RESOLUTION_TIMEOUT = 3
DNS_FAKE_IP_NETWORKS = (ipaddress.ip_network("198.18.0.0/15"),)
OTP_TIMESTAMP_SKEW_SECONDS = 10
TERMINAL_AUTH_STATUS_MARKERS = (": 401 ", ": 403 ")
CONTEXT_CODE_PATTERNS = (
    r"(?:verification\s+code|security\s+code|one[-\s]?time\s+code|otp|code|验证码|驗證碼)[^\d]{0,40}(\d{6})",
    r"(\d{6})[^\n]{0,40}(?:is\s+your|your\s+code|verification\s+code|验证码|驗證碼)",
)


def _validate_public_address(address_value: str) -> None:
    try:
        address = ipaddress.ip_address(address_value.strip("[]"))
    except ValueError:
        raise ValueError("base_url 不能指向本机或内网地址")
    if not address.is_global:
        raise ValueError("base_url 不能指向本机或内网地址")


def _is_dns_fake_ip(address_value: str) -> bool:
    try:
        address = ipaddress.ip_address(address_value.strip("[]"))
    except ValueError:
        return False
    return any(address in network for network in DNS_FAKE_IP_NETWORKS)


def _resolve_host_addresses(hostname: str) -> set[str]:
    try:
        infos = socket.getaddrinfo(hostname, None, type=socket.SOCK_STREAM)
    except socket.gaierror as e:
        raise ValueError("base_url 主机名无法解析") from e
    return {str(info[4][0]) for info in infos if info[4]}


def normalize_public_base_url(base_url: Any) -> str:
    value = str(base_url).strip().rstrip("/")
    parsed = urlparse(value)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        raise ValueError("base_url 必须是有效的 http(s) URL")
    if parsed.username or parsed.password:
        raise ValueError("base_url 不能包含用户名或密码")

    hostname = (parsed.hostname or "").strip().lower().rstrip(".")
    if hostname in INTERNAL_HOSTS or hostname.endswith(INTERNAL_HOST_SUFFIXES):
        raise ValueError("base_url 不能指向本机或内网地址")
    try:
        _validate_public_address(hostname)
        return value
    except ValueError:
        for resolved_address in _resolve_host_addresses(hostname):
            if _is_dns_fake_ip(resolved_address):
                continue
            _validate_public_address(resolved_address)
    return value


class CloudflareTempEmailService(BaseEmailService):
    def __init__(self, config: Optional[Dict[str, Any]] = None, name: Optional[str] = None) -> None:
        super().__init__(EmailServiceType.CLOUDFLARE_TEMP_EMAIL, name)
        required_keys = ["base_url", "domain"]
        missing_keys = [key for key in required_keys if not (config or {}).get(key)]
        if missing_keys:
            raise ValueError(f"缺少必需配置: {missing_keys}")

        default_config = {
            "timeout": 30,
            "poll_interval": 3,
            "mail_limit": 10,
            "max_retries": 3,
            "retry_delay": 1,
        }
        self.config = {**default_config, **(config or {})}
        self.config["base_url"] = self._normalize_base_url(self.config["base_url"])
        self.config["domain"] = str(self.config["domain"]).strip()

        self.session = requests.Session()
        self.session.headers.update(
            {
                "Accept": "application/json",
                "User-Agent": "CPA-Codex-Manager/1.0",
                "Connection": "close",
            }
        )
        self._created_emails: Dict[str, Dict[str, Any]] = {}
        self._seen_email_ids: Dict[str, set[str]] = {}

    @staticmethod
    def _normalize_base_url(base_url: Any) -> str:
        return normalize_public_base_url(base_url)

    @staticmethod
    def _generate_name() -> str:
        alphabet = string.ascii_lowercase + string.digits
        length = random.randint(MIN_NAME_LENGTH, MAX_NAME_LENGTH)
        return "".join(random.choice(alphabet) for _ in range(length))

    @staticmethod
    def _parse_response_json(response: Any, method: str, path: str) -> Any:
        if not getattr(response, "text", ""):
            return {}
        try:
            return response.json()
        except ValueError as e:
            raise EmailServiceError(f"响应解析失败: {method} {path} - {e}")

    @staticmethod
    def _raise_for_status(response: Any, method: str, path: str) -> None:
        status_code = int(getattr(response, "status_code", 0))
        if status_code >= 400:
            raise EmailServiceError(f"请求失败: {status_code} {method} {path}")

    def _make_request(self, method: str, path: str, token: Optional[str] = None, **kwargs) -> Any:
        url = f"{self.config['base_url']}{path}"
        request_kwargs = dict(kwargs)
        request_kwargs.setdefault("timeout", self.config["timeout"])
        request_kwargs.setdefault("verify", True)
        request_kwargs.setdefault("allow_redirects", False)
        if token:
            headers = dict(request_kwargs.get("headers") or {})
            headers["Authorization"] = f"Bearer {token}"
            request_kwargs["headers"] = headers

        retries = max(0, int(self.config.get("max_retries", 3)))
        retry_delay = max(0, float(self.config.get("retry_delay", 1)))
        last_error: Optional[Exception] = None

        for attempt in range(retries + 1):
            try:
                response = self.session.request(method, url, **request_kwargs)
                status_code = int(getattr(response, "status_code", 0))
                if status_code >= 500 or status_code == 429:
                    last_error = EmailServiceError(f"请求失败: {status_code} {method} {path}")
                    if attempt < retries:
                        time.sleep(retry_delay * (attempt + 1))
                        continue
                    raise last_error
                self._raise_for_status(response, method, path)
                return self._parse_response_json(response, method, path)
            except requests.RequestException as e:
                last_error = e
                if attempt < retries:
                    time.sleep(retry_delay * (attempt + 1))
                    continue
                raise EmailServiceError(f"请求失败: {method} {path}") from e

        raise EmailServiceError(f"请求失败: {method} {path}") from last_error

    @staticmethod
    def _extract_code(content: str, pattern: str) -> Optional[str]:
        if not content:
            return None
        for context_pattern in CONTEXT_CODE_PATTERNS:
            match = re.search(context_pattern, content, flags=re.IGNORECASE)
            if match:
                return match.group(1)
        matches = re.findall(pattern, content)
        if len(matches) == 1:
            return matches[0] if isinstance(matches[0], str) else matches[0][0]
        return None

    @staticmethod
    def _message_content(message: Dict[str, Any]) -> str:
        if not isinstance(message, dict):
            return ""
        fields = ["subject", "text", "html", "body", "content", "from", "sender"]
        return "\n".join(str(message.get(field) or "") for field in fields)

    @staticmethod
    def _parse_received_ts(received_at: Any) -> Optional[float]:
        if not received_at:
            return None
        if isinstance(received_at, (int, float)):
            return float(received_at)
        value = str(received_at).strip()
        formats = ["%Y-%m-%d %H:%M:%S", "%Y-%m-%dT%H:%M:%S.%fZ", "%Y-%m-%dT%H:%M:%SZ"]
        for date_format in formats:
            try:
                parsed = datetime.strptime(value, date_format)
                return parsed.replace(tzinfo=timezone.utc).timestamp()
            except ValueError:
                continue
        try:
            return datetime.fromisoformat(value.replace("Z", "+00:00")).timestamp()
        except ValueError:
            return None

    @staticmethod
    def _normalize_messages(data: Any) -> List[Dict[str, Any]]:
        if isinstance(data, dict):
            messages = data.get("results") or data.get("mails") or data.get("emails") or []
        elif isinstance(data, list):
            messages = data
        else:
            return []
        return [message for message in messages if isinstance(message, dict)]

    @classmethod
    def _is_before_otp(cls, message: Dict[str, Any], otp_sent_at: Optional[float]) -> bool:
        if not otp_sent_at:
            return False
        received_ts = cls._parse_received_ts(
            message.get("received_at") or message.get("created_at") or message.get("date")
        )
        return bool(received_ts and received_ts < (otp_sent_at - OTP_TIMESTAMP_SKEW_SECONDS))

    def _mailbox_token(self, email: str, email_id: str = None) -> str:
        target_email = email_id or email
        email_info = self._created_emails.get(target_email) or self._created_emails.get(email)
        token = str((email_info or {}).get("jwt") or "").strip()
        if not token:
            raise EmailServiceError("缺少邮箱访问 JWT，请先通过当前服务实例创建邮箱")
        return token

    def create_email(self, config: Dict[str, Any] = None) -> Dict[str, Any]:
        req_config = config or {}
        name = str(req_config.get("name") or req_config.get("local") or self._generate_name()).strip()
        domain = str(req_config.get("domain") or self.config["domain"]).strip()
        if not name or not domain:
            raise EmailServiceError("缺少邮箱名称或域名")

        data = self._make_request("POST", "/api/new_address", json={"name": name, "domain": domain})
        address = str(data.get("address") or "").strip()
        jwt = str(data.get("jwt") or "").strip()
        if not address:
            self.update_status(False, EmailServiceError("Cloudflare Temp Email 返回数据中缺少 address"))
            raise EmailServiceError("Cloudflare Temp Email 返回数据中缺少 address")
        if not jwt:
            self.update_status(False, EmailServiceError("Cloudflare Temp Email 返回数据中缺少 jwt"))
            raise EmailServiceError("Cloudflare Temp Email 返回数据中缺少 jwt")

        email_info = {
            "email": address,
            "service_id": address,
            "id": address,
        }
        if data.get("address_id") is not None:
            email_info["address_id"] = data.get("address_id")

        cached_info = {
            **email_info,
            "jwt": jwt,
            "password": data.get("password"),
            "created_at": time.time(),
        }
        self._created_emails[address] = cached_info
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
        target_email = email_id or email
        token = self._mailbox_token(email, email_id)
        excluded_codes = set(exclude_codes or [])
        start_time = time.time()
        if target_email not in self._seen_email_ids:
            self._seen_email_ids[target_email] = set()
        seen_ids = self._seen_email_ids[target_email]

        while time.time() - start_time < timeout:
            try:
                data = self._make_request(
                    "GET",
                    "/api/parsed_mails",
                    token=token,
                    params={"limit": self.config.get("mail_limit", 10), "offset": 0},
                )
                for message in self._normalize_messages(data):
                    message_id = str(message.get("id") or message.get("mail_id") or "").strip()
                    if message_id and message_id in seen_ids:
                        continue
                    if self._is_before_otp(message, otp_sent_at):
                        if message_id:
                            seen_ids.add(message_id)
                        continue

                    code = self._extract_code(self._message_content(message), pattern)
                    if code and code not in excluded_codes:
                        self.update_status(True)
                        return code

                    if message_id:
                        detail = self._make_request(
                            "GET",
                            f"/api/parsed_mail/{quote(message_id, safe='')}",
                            token=token,
                        )
                        if not isinstance(detail, dict):
                            seen_ids.add(message_id)
                            continue
                        if self._is_before_otp(detail, otp_sent_at):
                            seen_ids.add(message_id)
                            continue
                        code = self._extract_code(self._message_content(detail), pattern)
                        if code and code not in excluded_codes:
                            self.update_status(True)
                            return code
                        seen_ids.add(message_id)
            except EmailServiceError as e:
                logger.warning("Cloudflare Temp Email 拉取验证码失败")
                self.update_status(False, EmailServiceError("Cloudflare Temp Email 拉取验证码失败"))
                if any(marker in str(e) for marker in TERMINAL_AUTH_STATUS_MARKERS):
                    raise

            time.sleep(float(self.config.get("poll_interval", 3)))

        return None

    def list_emails(self, **kwargs) -> List[Dict[str, Any]]:
        return [
            {key: value for key, value in email_info.items() if key not in SENSITIVE_CACHE_FIELDS}
            for email_info in self._created_emails.values()
        ]

    def delete_email(self, email_id: str) -> bool:
        self._created_emails.pop(email_id, None)
        self._seen_email_ids.pop(email_id, None)
        return True

    def check_health(self) -> bool:
        return bool(self.config.get("base_url") and self.config.get("domain"))

    def get_email_messages(self, email_id: str, **kwargs) -> List[Dict[str, Any]]:
        token = self._mailbox_token(email_id, email_id)
        data = self._make_request(
            "GET",
            "/api/parsed_mails",
            token=token,
            params={
                "limit": kwargs.get("limit", self.config.get("mail_limit", 10)),
                "offset": kwargs.get("offset", 0),
            },
        )
        return self._normalize_messages(data)

    def get_message_content(self, email_id: str, message_id: str) -> Optional[Dict[str, Any]]:
        token = self._mailbox_token(email_id, email_id)
        data = self._make_request(
            "GET",
            f"/api/parsed_mail/{quote(str(message_id), safe='')}",
            token=token,
        )
        return data if isinstance(data, dict) else None
