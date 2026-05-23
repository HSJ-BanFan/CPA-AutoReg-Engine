"""
ChatGPT registration client.
"""

import base64
import json
import random
import time
import uuid
from http.cookiejar import Cookie
from urllib.parse import urlparse
from typing import Any, Dict, Optional

from curl_cffi import requests as curl_requests

from .chatgpt_flow_utils import (
    FlowState,
    build_browser_headers,
    decode_jwt_payload,
    describe_flow_state,
    extract_flow_state,
    generate_datadog_trace,
    normalize_flow_url,
    random_delay,
    seed_oai_device_cookie,
)
from .sentinel_token_v2 import build_browser_sentinel_token, build_sentinel_token


_CLIENT_AUTH_SESSION_COOKIE = "oai-client-auth-session"
_CLIENT_AUTH_SESSION_MAX_BYTES = 4096

_CHROME_PROFILES = [
    {
        "major": 131,
        "impersonate": "chrome131",
        "build": 6778,
        "patch_range": (69, 205),
        "sec_ch_ua": '"Google Chrome";v="131", "Chromium";v="131", "Not_A Brand";v="24"',
    },
    {
        "major": 133,
        "impersonate": "chrome133a",
        "build": 6943,
        "patch_range": (33, 153),
        "sec_ch_ua": '"Not(A:Brand";v="99", "Google Chrome";v="133", "Chromium";v="133"',
    },
    {
        "major": 136,
        "impersonate": "chrome136",
        "build": 7103,
        "patch_range": (48, 175),
        "sec_ch_ua": '"Chromium";v="136", "Google Chrome";v="136", "Not.A/Brand";v="99"',
    },
]


def _random_chrome_version():
    profile = random.choice(_CHROME_PROFILES)
    major = profile["major"]
    build = profile["build"]
    patch = random.randint(*profile["patch_range"])
    full_ver = f"{major}.0.{build}.{patch}"
    ua = f"Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/{full_ver} Safari/537.36"
    return profile["impersonate"], major, full_ver, ua, profile["sec_ch_ua"]


class ChatGPTClient:
    """State-machine registration client."""

    BASE = "https://chatgpt.com"
    AUTH = "https://auth.openai.com"

    def __init__(self, proxy: Optional[str] = None, verbose: bool = True, browser_mode: str = "protocol"):
        self.proxy = proxy
        self.verbose = verbose
        self.browser_mode = browser_mode or "protocol"
        self.device_id = str(uuid.uuid4())
        self.accept_language = random.choice([
            "en-US,en;q=0.9",
            "en-US,en;q=0.9,zh-CN;q=0.8",
            "en,en-US;q=0.9",
            "en-US,en;q=0.8",
        ])
        self.impersonate, self.chrome_major, self.chrome_full, self.ua, self.sec_ch_ua = _random_chrome_version()
        self.session = curl_requests.Session(impersonate=self.impersonate)
        if self.proxy:
            self.session.proxies = {"http": self.proxy, "https": self.proxy}
        self.session.headers.update(
            {
                "User-Agent": self.ua,
                "Accept-Language": self.accept_language,
                "sec-ch-ua": self.sec_ch_ua,
                "sec-ch-ua-mobile": "?0",
                "sec-ch-ua-platform": '"Windows"',
                "sec-ch-ua-arch": '"x86"',
                "sec-ch-ua-bitness": '"64"',
                "sec-ch-ua-full-version": f'"{self.chrome_full}"',
                "sec-ch-ua-platform-version": f'"{random.randint(10, 15)}.0.0"',
            }
        )
        seed_oai_device_cookie(self.session, self.device_id)
        self.last_registration_state = FlowState()

    def _log(self, msg: str):
        if self.verbose:
            print(f"  {msg}")

    def _browser_pause(self, low: float = 0.15, high: float = 0.45):
        if self.browser_mode == "headed":
            random_delay(low, high)

    def _headers(
        self,
        url: str,
        *,
        accept: str,
        referer: Optional[str] = None,
        origin: Optional[str] = None,
        content_type: Optional[str] = None,
        navigation: bool = False,
        fetch_mode: Optional[str] = None,
        fetch_dest: Optional[str] = None,
        fetch_site: Optional[str] = None,
        extra_headers: Optional[Dict[str, str]] = None,
    ):
        return build_browser_headers(
            url=url,
            user_agent=self.ua,
            sec_ch_ua=self.sec_ch_ua,
            chrome_full_version=self.chrome_full,
            accept=accept,
            accept_language=self.accept_language,
            referer=referer,
            origin=origin,
            content_type=content_type,
            navigation=navigation,
            fetch_mode=fetch_mode,
            fetch_dest=fetch_dest,
            fetch_site=fetch_site,
            headed=self.browser_mode == "headed",
            extra_headers=extra_headers,
        )

    def _reset_session(self):
        self.device_id = str(uuid.uuid4())
        self.impersonate, self.chrome_major, self.chrome_full, self.ua, self.sec_ch_ua = _random_chrome_version()
        self.accept_language = random.choice([
            "en-US,en;q=0.9",
            "en-US,en;q=0.9,zh-CN;q=0.8",
            "en,en-US;q=0.9",
            "en-US,en;q=0.8",
        ])
        self.session = curl_requests.Session(impersonate=self.impersonate)
        if self.proxy:
            self.session.proxies = {"http": self.proxy, "https": self.proxy}
        self.session.headers.update(
            {
                "User-Agent": self.ua,
                "Accept-Language": self.accept_language,
                "sec-ch-ua": self.sec_ch_ua,
                "sec-ch-ua-mobile": "?0",
                "sec-ch-ua-platform": '"Windows"',
                "sec-ch-ua-arch": '"x86"',
                "sec-ch-ua-bitness": '"64"',
                "sec-ch-ua-full-version": f'"{self.chrome_full}"',
                "sec-ch-ua-platform-version": f'"{random.randint(10, 15)}.0.0"',
            }
        )
        seed_oai_device_cookie(self.session, self.device_id)

    def _state_from_url(self, url: str, method: str = "GET") -> FlowState:
        state = extract_flow_state(
            current_url=normalize_flow_url(url, auth_base=self.AUTH),
            auth_base=self.AUTH,
            default_method=method,
        )
        state.method = str(method).upper()
        return state

    def _state_from_payload(self, data, current_url: str = "") -> FlowState:
        return extract_flow_state(data=data, current_url=current_url, auth_base=self.AUTH)

    def _state_signature(self, state: FlowState):
        return (
            state.page_type or "",
            state.method or "",
            state.continue_url or "",
            state.current_url or "",
        )

    def _is_registration_complete_state(self, state: FlowState) -> bool:
        current_url = (state.current_url or "").lower()
        continue_url = (state.continue_url or "").lower()
        page_type = state.page_type or ""
        return (
            page_type in {"callback", "chatgpt_home", "oauth_callback"}
            or ("chatgpt.com" in current_url and "redirect_uri" not in current_url)
            or ("chatgpt.com" in continue_url and "redirect_uri" not in continue_url and page_type != "external_url")
        )

    def _state_is_password_registration(self, state: FlowState) -> bool:
        return state.page_type in {"create_account_password", "password"}

    def _state_is_email_otp(self, state: FlowState) -> bool:
        target = f"{state.continue_url} {state.current_url}".lower()
        return state.page_type == "email_otp_verification" or "email-verification" in target or "email-otp" in target

    def _state_is_about_you(self, state: FlowState) -> bool:
        target = f"{state.continue_url} {state.current_url}".lower()
        return state.page_type == "about_you" or "about-you" in target

    def _state_requires_navigation(self, state: FlowState) -> bool:
        if (state.method or "GET").upper() != "GET":
            return False
        if state.page_type == "external_url" and state.continue_url:
            return True
        if state.continue_url and state.continue_url != state.current_url:
            return True
        return False

    def _follow_flow_state(self, state: FlowState, referer: Optional[str] = None):
        target_url = state.continue_url or state.current_url
        if not target_url:
            return False, "缺少可跟随的 continue_url"
        try:
            self._browser_pause()
            r = self.session.get(
                target_url,
                headers=self._headers(
                    target_url,
                    accept="text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
                    referer=referer,
                    navigation=True,
                ),
                allow_redirects=True,
                timeout=30,
            )
            final_url = str(r.url)
            self._log(f"follow -> {r.status_code} {final_url}")
            content_type = (r.headers.get("content-type", "") or "").lower()
            if "application/json" in content_type:
                try:
                    next_state = self._state_from_payload(r.json(), current_url=final_url)
                except Exception:
                    next_state = self._state_from_url(final_url)
            else:
                next_state = self._state_from_url(final_url)
            self._log(f"follow state -> {describe_flow_state(next_state)}")
            return True, next_state
        except Exception as e:
            self._log(f"跟随 continue_url 失败: {e}")
            return False, str(e)

    def _get_cookie_value(self, name: str, domain_hint: Optional[str] = None) -> str:
        for cookie in self.session.cookies.jar:
            if cookie.name != name:
                continue
            if domain_hint and domain_hint not in (cookie.domain or ""):
                continue
            return cookie.value
        return ""

    def _build_sentinel_token(self, flow: str, page_url: str) -> Optional[str]:
        token = build_browser_sentinel_token(
            flow=flow,
            proxy=getattr(self, "proxy", None),
            page_url=page_url,
            headless=self.browser_mode != "headed",
            device_id=self.device_id,
            user_agent=self.ua,
        )
        if token:
            return token
        return build_sentinel_token(
            self.session,
            self.device_id,
            flow=flow,
            user_agent=self.ua,
            sec_ch_ua=self.sec_ch_ua,
            impersonate=self.impersonate,
        )

    def get_next_auth_session_token(self) -> str:
        return self._get_cookie_value("__Secure-next-auth.session-token", "chatgpt.com")

    def fetch_chatgpt_session(self):
        url = f"{self.BASE}/api/auth/session"
        self._browser_pause()
        response = self.session.get(
            url,
            headers=self._headers(
                url,
                accept="application/json",
                referer=f"{self.BASE}/",
                fetch_site="same-origin",
            ),
            timeout=30,
        )
        if response.status_code != 200:
            return False, f"/api/auth/session -> HTTP {response.status_code}"
        try:
            data = response.json()
        except Exception as exc:
            return False, f"/api/auth/session 返回非 JSON: {exc}"
        access_token = str(data.get("accessToken") or "").strip()
        if not access_token:
            return False, "/api/auth/session 未返回 accessToken"
        return True, data

    def reuse_session_and_get_tokens(self):
        state = self.last_registration_state or FlowState()
        self._log("步骤 1/4: 跟随注册回调 external_url ...")
        if state.page_type == "external_url" or self._state_requires_navigation(state):
            ok, followed = self._follow_flow_state(
                state,
                referer=state.current_url or f"{self.AUTH}/about-you",
            )
            if not ok:
                return False, f"注册回调落地失败: {followed}"
            self.last_registration_state = followed
        else:
            self._log("注册回调已落地，跳过额外跟随")

        self._log("步骤 2/4: 检查 __Secure-next-auth.session-token ...")
        session_cookie = self.get_next_auth_session_token()
        if not session_cookie:
            return False, "缺少 __Secure-next-auth.session-token，注册回调可能未落地"

        self._log("步骤 3/4: 请求 ChatGPT /api/auth/session ...")
        ok, session_or_error = self.fetch_chatgpt_session()
        if not ok:
            return False, session_or_error

        session_data = session_or_error
        access_token = str(session_data.get("accessToken") or "").strip()
        session_token = str(session_data.get("sessionToken") or session_cookie or "").strip()
        user = session_data.get("user") or {}
        account = session_data.get("account") or {}
        jwt_payload = decode_jwt_payload(access_token)
        auth_payload = jwt_payload.get("https://api.openai.com/auth") or {}
        account_id = str(account.get("id") or "").strip() or str(auth_payload.get("chatgpt_account_id") or "").strip()
        user_id = (
            str(user.get("id") or "").strip()
            or str(auth_payload.get("chatgpt_user_id") or "").strip()
            or str(auth_payload.get("user_id") or "").strip()
        )
        normalized = {
            "access_token": access_token,
            "refresh_token": str(session_data.get("refreshToken") or "").strip(),
            "id_token": str(session_data.get("idToken") or "").strip(),
            "session_token": session_token,
            "account_id": account_id,
            "user_id": user_id,
            "workspace_id": account_id,
            "expires": session_data.get("expires"),
            "user": user,
            "account": account,
            "auth_provider": session_data.get("authProvider"),
            "raw_session": session_data,
        }
        self._log("步骤 4/4: 已从复用会话中提取 accessToken")
        if account_id:
            self._log(f"Session Account ID: {account_id}")
        if user_id:
            self._log(f"Session User ID: {user_id}")
        return True, normalized

    def visit_homepage(self) -> bool:
        self._log("访问 ChatGPT 首页...")
        url = f"{self.BASE}/"
        try:
            self._browser_pause()
            r = self.session.get(
                url,
                headers=self._headers(
                    url,
                    accept="text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8",
                    navigation=True,
                ),
                allow_redirects=True,
                timeout=30,
            )
            return r.status_code == 200
        except Exception as e:
            self._log(f"访问首页失败: {e}")
            return False

    def get_csrf_token(self) -> Optional[str]:
        self._log("获取 CSRF token...")
        url = f"{self.BASE}/api/auth/csrf"
        try:
            r = self.session.get(
                url,
                headers=self._headers(
                    url,
                    accept="application/json",
                    referer=f"{self.BASE}/",
                    fetch_site="same-origin",
                ),
                timeout=30,
            )
            if r.status_code == 200:
                token = r.json().get("csrfToken", "")
                if token:
                    self._log(f"CSRF token: {token[:20]}...")
                    return token
        except Exception as e:
            self._log(f"获取 CSRF token 失败: {e}")
        return None

    def signin(self, email: str, csrf_token: str) -> Optional[str]:
        self._log(f"提交邮箱: {email}")
        url = f"{self.BASE}/api/auth/signin/openai"
        params = {
            "prompt": "login",
            "ext-oai-did": self.device_id,
            "auth_session_logging_id": str(uuid.uuid4()),
            "screen_hint": "login_or_signup",
            "login_hint": email,
        }
        form_data = {
            "callbackUrl": f"{self.BASE}/",
            "csrfToken": csrf_token,
            "json": "true",
        }
        try:
            self._browser_pause()
            r = self.session.post(
                url,
                params=params,
                data=form_data,
                headers=self._headers(
                    url,
                    accept="application/json",
                    referer=f"{self.BASE}/",
                    origin=self.BASE,
                    content_type="application/x-www-form-urlencoded",
                    fetch_site="same-origin",
                ),
                timeout=30,
            )
            if r.status_code == 200:
                authorize_url = r.json().get("url", "")
                if authorize_url:
                    self._log("获取到 authorize URL")
                    return authorize_url
        except Exception as e:
            self._log(f"提交邮箱失败: {e}")
        return None

    def authorize(self, url: str, max_retries: int = 3) -> str:
        for attempt in range(max_retries):
            try:
                if attempt > 0:
                    self._log(f"访问 authorize URL... (尝试 {attempt + 1}/{max_retries})")
                    time.sleep(1)
                else:
                    self._log("访问 authorize URL...")
                self._browser_pause()
                r = self.session.get(
                    url,
                    headers=self._headers(
                        url,
                        accept="text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
                        referer=f"{self.BASE}/",
                        navigation=True,
                    ),
                    allow_redirects=True,
                    timeout=30,
                )
                final_url = str(r.url)
                self._log(f"重定向到: {final_url}")
                return final_url
            except Exception as e:
                error_msg = str(e)
                is_tls_error = "TLS" in error_msg or "SSL" in error_msg or "curl: (35)" in error_msg
                if is_tls_error and attempt < max_retries - 1:
                    self._log(f"Authorize TLS 错误 (尝试 {attempt + 1}/{max_retries}): {error_msg[:100]}")
                    continue
                self._log(f"Authorize 失败: {e}")
                return ""
        return ""

    def register_user(self, email: str, password: str, return_state: bool = False):
        self._log(f"注册用户: {email}")
        url = f"{self.AUTH}/api/accounts/user/register"
        sentinel_token = self._build_sentinel_token(
            flow="username_password_create",
            page_url=f"{self.AUTH}/create-account/password",
        )
        extra_headers = {"oai-device-id": self.device_id}
        if sentinel_token:
            extra_headers["openai-sentinel-token"] = sentinel_token
        headers = self._headers(
            url,
            accept="application/json",
            referer=f"{self.AUTH}/create-account/password",
            origin=self.AUTH,
            content_type="application/json",
            fetch_site="same-origin",
            extra_headers=extra_headers,
        )
        headers.update(generate_datadog_trace())
        payload = {"username": email, "password": password}
        try:
            self._browser_pause()
            r = self.session.post(url, json=payload, headers=headers, timeout=30, allow_redirects=False)
            if r.status_code == 200:
                try:
                    data = r.json()
                except Exception:
                    data = {}
                if not self._persist_client_auth_session(data):
                    self._log("客户端认证会话无效")
                    return False, "客户端认证会话无效"
                sanitized_data = self._sanitize_client_auth_session(data)
                self._log("注册成功")
                if return_state:
                    response_url = getattr(r, "url", "") or f"{self.AUTH}/email-verification"
                    return True, self._state_from_payload(sanitized_data, current_url=str(response_url))
                return True, "注册成功"
            try:
                error_msg = (r.json().get("error") or {}).get("message", r.text[:200])
            except Exception:
                error_msg = r.text[:200]
            self._log(f"注册失败: {r.status_code} - {error_msg}")
            return False, f"HTTP {r.status_code}: {error_msg}"
        except Exception as e:
            self._log(f"注册异常: {e}")
            return False, str(e)

    @staticmethod
    def _sanitize_client_auth_session(data: Dict[str, Any]) -> Dict[str, Any]:
        return {
            key: value
            for key, value in data.items()
            if key not in {_CLIENT_AUTH_SESSION_COOKIE, "client_auth_session", "session_id"}
        }

    @staticmethod
    def _extract_client_auth_session(data: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        auth_session = data.get(_CLIENT_AUTH_SESSION_COOKIE)
        if isinstance(auth_session, dict):
            return auth_session
        auth_session = data.get("client_auth_session")
        if not isinstance(auth_session, dict):
            return None
        session_id = data.get("session_id")
        if not isinstance(session_id, str) or not session_id:
            return None
        normalized = {**auth_session, "session_id": session_id}
        checksum = data.get("checksum")
        if isinstance(checksum, str) and checksum:
            normalized["checksum"] = checksum
        return normalized

    def _client_auth_session_domain(self) -> str:
        return urlparse(self.AUTH).hostname or "auth.openai.com"

    @staticmethod
    def _encode_client_auth_session(auth_session: Dict[str, Any]) -> Optional[str]:
        if not isinstance(auth_session.get("session_id"), str) or not auth_session["session_id"]:
            return None
        encoded = base64.urlsafe_b64encode(
            json.dumps(auth_session, separators=(",", ":"), sort_keys=True).encode("utf-8")
        ).rstrip(b"=").decode("ascii")
        if len(encoded) > _CLIENT_AUTH_SESSION_MAX_BYTES:
            return None
        return encoded

    def _persist_client_auth_session(self, data: Dict[str, Any]) -> bool:
        auth_session = self._extract_client_auth_session(data)
        if auth_session is None:
            return False
        encoded = self._encode_client_auth_session(auth_session)
        if encoded is None:
            return False
        cookie = Cookie(
            version=0,
            name=_CLIENT_AUTH_SESSION_COOKIE,
            value=encoded,
            port=None,
            port_specified=False,
            domain=self._client_auth_session_domain(),
            domain_specified=True,
            domain_initial_dot=False,
            path="/",
            path_specified=True,
            secure=True,
            expires=None,
            discard=True,
            comment=None,
            comment_url=None,
            rest={"HttpOnly": True, "SameSite": "Lax"},
            rfc2109=False,
        )
        if hasattr(self.session.cookies, "set_cookie"):
            self.session.cookies.set_cookie(cookie)
        elif hasattr(self.session.cookies, "jar") and hasattr(self.session.cookies.jar, "set_cookie"):
            self.session.cookies.jar.set_cookie(cookie)
        else:
            return False
        return True

    def send_email_otp(self, state: Optional[FlowState] = None) -> bool:
        self._log("触发发送验证码...")
        url = f"{self.AUTH}/api/accounts/email-otp/resend"
        referer = (state.current_url or state.continue_url) if state else ""
        referer = referer or f"{self.AUTH}/create-account/password"
        try:
            self._browser_pause()
            r = self.session.post(
                url,
                json={},
                headers=self._headers(
                    url,
                    accept="application/json",
                    referer=referer,
                    origin=self.AUTH,
                    content_type="application/json",
                    fetch_site="same-origin",
                    extra_headers={"oai-device-id": self.device_id},
                ),
                allow_redirects=False,
                timeout=30,
            )
            if r.status_code != 200:
                return False
            try:
                data = r.json()
            except Exception:
                self._log("发送验证码响应不是有效 JSON")
                return False
            if _CLIENT_AUTH_SESSION_COOKIE in data and not self._persist_client_auth_session(data):
                self._log("发送验证码响应包含无效客户端认证会话")
                return False
            return True
        except Exception as e:
            self._log(f"发送验证码失败: {e}")
            return False

    def _fetch_client_auth_session_dump(self, referer: str) -> tuple[Optional[dict], str]:
        url = f"{self.AUTH}/api/accounts/client_auth_session_dump"
        headers = self._headers(
            url,
            accept="application/json",
            referer=referer,
            fetch_site="same-origin",
            extra_headers={"oai-device-id": self.device_id},
        )
        headers.update(generate_datadog_trace())
        try:
            self._browser_pause()
            r = self.session.get(url, headers=headers, timeout=30)
            self._log(f"客户端认证会话转储状态: {r.status_code}")
            if r.status_code != 200:
                self._log(f"获取客户端认证会话失败: {r.status_code}")
                return None, ""
            try:
                data = r.json()
            except Exception:
                self._log("客户端认证会话响应不是有效 JSON")
                return None, ""
            field_names = sorted(data.keys()) if isinstance(data, dict) else []
            auth_session = self._extract_client_auth_session(data) if isinstance(data, dict) else None
            self._log(f"客户端认证会话转储字段: {field_names}")
            self._log(f"客户端认证会话存在: {isinstance(auth_session, dict)}")
            self._log(f"客户端认证会话包含 session_id: {isinstance(auth_session, dict) and bool(auth_session.get('session_id'))}")
            return data, f"{self.AUTH}/about-you"
        except Exception as e:
            self._log(f"获取客户端认证会话异常: {e}")
            return None, ""

    def verify_email_otp(self, otp_code: str, return_state: bool = False, state: Optional[FlowState] = None):
        self._log("验证 OTP 码")
        url = f"{self.AUTH}/api/accounts/email-otp/validate"
        referer = (state.current_url or state.continue_url) if state else ""
        referer = referer or f"{self.AUTH}/email-verification"
        sentinel_token = self._build_sentinel_token(
            flow="authorize_continue",
            page_url=referer,
        )
        extra_headers = {"oai-device-id": self.device_id}
        if sentinel_token:
            extra_headers["openai-sentinel-token"] = sentinel_token
        headers = self._headers(
            url,
            accept="application/json",
            referer=referer,
            origin=self.AUTH,
            content_type="application/json",
            fetch_site="same-origin",
            extra_headers=extra_headers,
        )
        headers.update(generate_datadog_trace())
        payload = {"code": otp_code}
        try:
            self._browser_pause()
            r = self.session.post(url, json=payload, headers=headers, timeout=30, allow_redirects=False)
            self._log(f"验证码验证状态: {r.status_code}")
            if r.status_code == 200:
                data, response_url = self._fetch_client_auth_session_dump(referer)
                if data is None or not self._persist_client_auth_session(data):
                    self._log("客户端认证会话无效")
                    return False, "客户端认证会话无效"
                sanitized_data = self._sanitize_client_auth_session(data)
                next_state = self._state_from_payload(sanitized_data, current_url=response_url)
                self._log(f"验证成功 {describe_flow_state(next_state)}")
                return (True, next_state) if return_state else (True, "验证成功")
            error_msg = r.text[:200]
            self._log(f"验证失败: {r.status_code} - {error_msg}")
            return False, f"HTTP {r.status_code}"
        except Exception as e:
            self._log(f"验证异常: {e}")
            return False, str(e)

    def create_account(self, first_name: str, last_name: str, birthdate: str, return_state: bool = False):
        name = f"{first_name} {last_name}"
        self._log(f"完成账号创建: {name}")
        url = f"{self.AUTH}/api/accounts/create_account"
        sentinel_token = self._build_sentinel_token(
            flow="authorize_continue",
            page_url=f"{self.AUTH}/about-you",
        )
        if sentinel_token:
            self._log("create_account: 已生成 sentinel token")
        else:
            self._log("create_account: 未生成 sentinel token，降级继续请求")
        headers = self._headers(
            url,
            accept="application/json",
            referer=f"{self.AUTH}/about-you",
            origin=self.AUTH,
            content_type="application/json",
            fetch_site="same-origin",
            extra_headers={"oai-device-id": self.device_id},
        )
        if sentinel_token:
            headers["openai-sentinel-token"] = sentinel_token
        headers.update(generate_datadog_trace())
        payload = {"name": name, "birthdate": birthdate}
        try:
            self._browser_pause()
            r = self.session.post(url, json=payload, headers=headers, timeout=30)
            if r.status_code == 200:
                try:
                    data = r.json()
                except Exception:
                    data = {}
                if self._extract_client_auth_session(data) is not None and not self._persist_client_auth_session(data):
                    self._log("客户端认证会话无效")
                    return False, "客户端认证会话无效"
                sanitized_data = self._sanitize_client_auth_session(data)
                next_state = self._state_from_payload(sanitized_data, current_url=str(r.url) or self.BASE)
                self._log(f"账号创建成功 {describe_flow_state(next_state)}")
                return (True, next_state) if return_state else (True, "账号创建成功")
            error_msg = r.text[:200]
            self._log(f"创建失败: {r.status_code} - {error_msg}")
            error_code = ""
            try:
                data = r.json()
            except Exception:
                data = {}
            if not (data.get("error") or {}).get("code"):
                try:
                    data = json.loads(r.text)
                except Exception:
                    pass
            error_code = str((data.get("error") or {}).get("code") or "")
            suffix = f": {error_code}" if error_code else ""
            return False, f"HTTP {r.status_code}{suffix}"
        except Exception as e:
            self._log(f"创建异常: {e}")
            return False, str(e)

    def register_complete_flow(self, email: str, password: str, first_name: str, last_name: str, birthdate: str, email_adapter):
        max_auth_attempts = 3
        final_url = ""

        for auth_attempt in range(max_auth_attempts):
            if auth_attempt > 0:
                self._log(f"预授权阶段重试 {auth_attempt + 1}/{max_auth_attempts}...")
                self._reset_session()

            if not self.visit_homepage():
                if auth_attempt < max_auth_attempts - 1:
                    continue
                return False, "访问首页失败"

            csrf_token = self.get_csrf_token()
            if not csrf_token:
                if auth_attempt < max_auth_attempts - 1:
                    continue
                return False, "获取 CSRF token 失败"

            auth_url = self.signin(email, csrf_token)
            if not auth_url:
                if auth_attempt < max_auth_attempts - 1:
                    continue
                return False, "提交邮箱失败"

            final_url = self.authorize(auth_url)
            if not final_url:
                if auth_attempt < max_auth_attempts - 1:
                    continue
                return False, "Authorize 失败"

            final_path = urlparse(final_url).path
            self._log(f"Authorize → {final_path}")
            if "api/accounts/authorize" in final_path or final_path == "/error":
                self._log(f"检测到 Cloudflare/SPA 中间页，准备重试预授权: {final_url[:160]}...")
                if auth_attempt < max_auth_attempts - 1:
                    continue
                return False, f"预授权被拦截: {final_path}"
            break

        state = self._state_from_url(final_url)
        self._log(f"注册状态起点: {describe_flow_state(state)}")
        register_submitted = False
        otp_verified = False
        account_created = False
        otp_sent_at = None
        seen_states = {}

        for _ in range(12):
            signature = self._state_signature(state)
            seen_states[signature] = seen_states.get(signature, 0) + 1
            if seen_states[signature] > 2:
                return False, f"注册状态卡住: {describe_flow_state(state)}"

            if self._is_registration_complete_state(state):
                self.last_registration_state = state
                self._log("注册流程完成")
                return True, "注册成功"

            if self._state_is_password_registration(state):
                self._log("全新注册流程")
                if register_submitted:
                    return False, "注册密码阶段重复进入"
                otp_sent_at = time.time()
                success, next_state = self.register_user(email, password, return_state=True)
                if not success:
                    return False, f"注册失败: {next_state}"
                register_submitted = True
                if isinstance(next_state, FlowState) and self._state_is_email_otp(next_state):
                    state = next_state
                else:
                    state = self._state_from_url(f"{self.AUTH}/email-verification")
                continue

            if self._state_is_email_otp(state):
                self._log("等待邮箱验证码...")
                if otp_sent_at is None:
                    otp_sent_at = time.time()
                    if not register_submitted and not self.send_email_otp(state=state):
                        self._log("发送验证码接口返回失败")
                        return False, "发送验证码失败"
                otp_timeout = int(getattr(email_adapter, "verification_timeout", 30))
                otp_code = email_adapter.wait_for_verification_code(email, timeout=otp_timeout, otp_sent_at=otp_sent_at)
                if not otp_code:
                    return False, "未收到验证码"
                success, next_state = self.verify_email_otp(otp_code, return_state=True, state=state)
                if not success:
                    return False, f"验证码失败: {next_state}"
                otp_verified = True
                state = next_state
                self.last_registration_state = state
                continue

            if self._state_is_about_you(state):
                if account_created:
                    return False, "填写信息阶段重复进入"
                success, next_state = self.create_account(first_name, last_name, birthdate, return_state=True)
                if not success:
                    return False, f"创建账号失败: {next_state}"
                account_created = True
                state = next_state
                self.last_registration_state = state
                continue

            if self._state_requires_navigation(state):
                success, next_state = self._follow_flow_state(
                    state,
                    referer=state.current_url or f"{self.AUTH}/about-you",
                )
                if not success:
                    return False, f"跳转失败: {next_state}"
                state = next_state
                self.last_registration_state = state
                continue

            if (not register_submitted) and (not otp_verified) and (not account_created):
                self._log(f"未知起始状态，回退为全新注册流程: {describe_flow_state(state)}")
                state = self._state_from_url(f"{self.AUTH}/create-account/password")
                continue

            return False, f"未支持的注册状态: {describe_flow_state(state)}"

        return False, "注册状态机超出最大步数"
