"""
ChatGPT browser-based registration client using Camoufox.

Drives a real Firefox browser (via Camoufox/Playwright) through the ChatGPT
web UI registration flow — form filling, button clicks, OTP entry — instead
of raw HTTP API calls.
"""

import time
import uuid
from datetime import date
from typing import Any, Dict, Optional, Tuple

from .browser_utils import build_playwright_proxy_config

OAUTH_CLIENT_ID = "app_EMoamEEZ73f0CkXaXp7hrann"
AUTH_BASE = "https://auth.openai.com"
CHATGPT_BASE = "https://chatgpt.com"

SIGNUP_URL = (
    f"{AUTH_BASE}/authorize"
    f"?client_id={OAUTH_CLIENT_ID}"
    f"&prompt=login"
    f"&screen_hint=signup"
)

DEFAULT_TIMEOUT = 15000
PAGE_LOAD_TIMEOUT = 45000


class BrowserAutomationError(Exception):
    """Raised when a browser automation step fails."""


class ChatGPTBrowserClient:
    """Registration client using Camoufox browser to navigate ChatGPT web UI."""

    def __init__(
        self,
        proxy: Optional[str] = None,
        headless: bool = True,
        timeout_ms: int = 60000,
    ):
        self.proxy = proxy
        self.headless = headless
        self.timeout_ms = timeout_ms
        self.device_id = str(uuid.uuid4())
        self._log_callback = None
        self._camoufox = None
        self._browser = None
        self._context = None
        self._page = None
        self._cached_tokens: Optional[dict] = None

    @property
    def _log(self):
        if self._log_callback:

            def log(msg: str):
                self._log_callback(msg)

            return log
        return lambda msg: None

    @_log.setter
    def _log(self, fn):
        self._log_callback = fn

    @staticmethod
    def _mask_email(email: str) -> str:
        if "@" not in email:
            return "***"
        local, domain = email.split("@", 1)
        return f"{local[:2]}***@{domain}"

    # ------------------------------------------------------------------
    # Browser lifecycle
    # ------------------------------------------------------------------

    def _launch_browser(self) -> None:
        from camoufox.addons import DefaultAddons
        from camoufox.sync_api import Camoufox

        self._log("正在启动 Camoufox 浏览器...")
        launch_kwargs: Dict[str, Any] = {
            "headless": self.headless,
            "os": ["windows"],
            "humanize": True,
            "block_webrtc": True,
            "exclude_addons": [DefaultAddons.UBO],
        }
        proxy_config = build_playwright_proxy_config(self.proxy)
        if proxy_config:
            launch_kwargs["proxy"] = proxy_config

        self._camoufox = Camoufox(**launch_kwargs)
        self._browser = self._camoufox.__enter__()
        context_options: Dict[str, Any] = {
            "viewport": {"width": 1440, "height": 900},
            "locale": "en-US",
            "timezone_id": "America/New_York",
            "ignore_https_errors": True,
        }
        if proxy_config:
            context_options["proxy"] = proxy_config

        self._context = self._browser.new_context(**context_options)
        self._context.add_cookies(
            [
                {
                    "name": "oai-did",
                    "value": self.device_id,
                    "domain": domain,
                    "path": "/",
                    "secure": True,
                    "sameSite": "Lax",
                }
                for domain in (".auth.openai.com", ".chatgpt.com")
            ]
        )
        self._page = self._context.new_page()
        self._log("Camoufox 浏览器已启动")

    def _close_browser(self) -> None:
        if self._camoufox is not None:
            try:
                self._camoufox.__exit__(None, None, None)
            except Exception as exc:
                self._log(f"关闭浏览器失败: {exc}")
            self._camoufox = None
            self._browser = None
            self._context = None
            self._page = None

    # ------------------------------------------------------------------
    # Element interaction helpers
    # ------------------------------------------------------------------

    def _wait_and_fill(self, selector, value, timeout=DEFAULT_TIMEOUT) -> None:
        el = self._page.wait_for_selector(selector, state="visible", timeout=timeout)
        el.click()
        el.fill("")
        el.fill(value)
        time.sleep(0.1)

    def _wait_and_click(self, selector, timeout=DEFAULT_TIMEOUT) -> None:
        el = self._page.wait_for_selector(selector, state="visible", timeout=timeout)
        el.click()

    def _click_first(self, selectors, timeout=DEFAULT_TIMEOUT) -> bool:
        for sel in selectors:
            try:
                self._wait_and_click(sel, timeout=timeout)
                return True
            except Exception:
                continue
        return False

    def _fill_first(self, selectors, value, timeout=DEFAULT_TIMEOUT) -> bool:
        for sel in selectors:
            try:
                self._wait_and_fill(sel, value, timeout=timeout)
                return True
            except Exception:
                continue
        return False

    # ------------------------------------------------------------------
    # Page interaction steps
    # ------------------------------------------------------------------

    def _has_visible_selector(self, selectors, timeout=1500) -> bool:
        for sel in selectors:
            try:
                self._page.wait_for_selector(sel, state="visible", timeout=timeout)
                return True
            except Exception:
                continue
        return False

    def _dismiss_cookie_banner(self) -> None:
        cookie_selectors = [
            'button:has-text("Reject non-essential")',
            'button:has-text("Accept all")',
            'button[aria-label="Close"]',
        ]
        self._click_first(cookie_selectors, timeout=2000)

    def _open_signup_entry(self) -> None:
        email_selectors = [
            'input[type="email"]',
            'input[name="email"]',
            'input[id="email"]',
            'input[autocomplete="email"]',
        ]
        if self._has_visible_selector(email_selectors):
            return

        signup_selectors = [
            'button:has-text("Sign up for free")',
            'button:has-text("Sign up")',
            'text="Sign up for free"',
        ]
        if not self._click_first(signup_selectors, timeout=5000):
            raise BrowserAutomationError("未打开注册入口")
        if not self._has_visible_selector(email_selectors, timeout=5000):
            raise BrowserAutomationError("未找到邮箱输入框")

    def _enter_email(self, email: str) -> None:
        self._log(f"提交邮箱: {self._mask_email(email)}")
        email_selectors = [
            'input[type="email"]',
            'input[name="email"]',
            'input[id="email"]',
            'input[autocomplete="email"]',
        ]
        if not self._fill_first(email_selectors, email):
            raise BrowserAutomationError("未找到邮箱输入框")

        continue_selectors = [
            'button[type="submit"]',
            'button:has-text("Continue")',
            'button:has-text("Next")',
            'input[type="submit"]',
        ]
        if not self._click_first(continue_selectors):
            raise BrowserAutomationError("未找到继续按钮")

    def _handle_email_verification_page(self) -> None:
        current_url = (self._page.url or "").lower()
        if "email-verification" not in current_url:
            return

        continue_with_password_selectors = [
            'a:has-text("Continue with password")',
            'button:has-text("Continue with password")',
            'text="Continue with password"',
        ]
        if not self._click_first(continue_with_password_selectors, timeout=5000):
            raise BrowserAutomationError('未找到 "Continue with password" 按钮')

        try:
            self._page.wait_for_url(
                lambda url: "password" in url.lower(),
                timeout=PAGE_LOAD_TIMEOUT,
            )
        except Exception as exc:
            raise BrowserAutomationError("切换到密码页面失败") from exc

    def _enter_password(self, password: str) -> None:
        self._log("设置账号密码...")
        try:
            self._page.wait_for_url(
                lambda url: any(
                    marker in url.lower()
                    for marker in ("email-verification", "password", "register")
                ),
                timeout=PAGE_LOAD_TIMEOUT,
            )
        except Exception as exc:
            current_url = (self._page.url or "").lower()
            if not any(marker in current_url for marker in ("email-verification", "password", "register")):
                raise BrowserAutomationError("未进入密码页面") from exc

        self._handle_email_verification_page()
        try:
            self._page.wait_for_url(
                lambda url: "password" in url.lower() or "register" in url.lower(),
                timeout=PAGE_LOAD_TIMEOUT,
            )
        except Exception as exc:
            current_url = (self._page.url or "").lower()
            if "password" not in current_url and "register" not in current_url:
                raise BrowserAutomationError("未进入密码页面") from exc

        password_selectors = [
            'input[type="password"]',
            'input[name="password"]',
            'input[id="password"]',
        ]
        if not self._fill_first(password_selectors, password):
            raise BrowserAutomationError("未找到密码输入框")

        continue_selectors = [
            'button[type="submit"]',
            'button:has-text("Continue")',
            'button:has-text("Next")',
        ]
        if not self._click_first(continue_selectors):
            raise BrowserAutomationError("未找到继续按钮")

    def _trigger_otp_resend_if_needed(self) -> None:
        resend_selectors = [
            'button:has-text("Resend")',
            'button:has-text("Send code")',
            'a:has-text("Resend")',
            'text="Resend"',
        ]
        for sel in resend_selectors:
            try:
                el = self._page.wait_for_selector(sel, state="visible", timeout=3000)
                el.click()
                self._log("触发发送验证码...")
                return
            except Exception:
                continue

    def _wait_for_enabled_locator(self, selector: str, timeout: int = DEFAULT_TIMEOUT):
        locator = self._page.locator(selector).first
        locator.wait_for(state="visible", timeout=timeout)

        deadline = time.time() + (timeout / 1000)
        while time.time() < deadline:
            if locator.is_enabled():
                return locator
            time.sleep(0.1)
        raise BrowserAutomationError(f"未找到可用元素: {selector}")

    @staticmethod
    def _read_input_value(element) -> str:
        try:
            return element.input_value()
        except Exception:
            return ""

    def _enter_single_otp_input(self, selector: str, code: str, continue_selectors, timeout: int = 3000) -> bool:
        try:
            otp_input = self._wait_for_enabled_locator(selector, timeout=timeout)
            otp_input.fill(code)
            time.sleep(0.2)
            if self._read_input_value(otp_input) != code:
                otp_input.press("Control+A")
                otp_input.type(code)
                time.sleep(0.2)
            if self._read_input_value(otp_input) != code:
                raise BrowserAutomationError("OTP 输入框未写入验证码")
            if not self._click_first(continue_selectors, timeout=3000):
                raise BrowserAutomationError("未找到继续按钮")
            return True
        except Exception as exc:
            self._log(f"OTP 输入选择器失败 {selector}: {exc}")
            return False

    def _wait_for_otp_accepted(self, timeout: int = DEFAULT_TIMEOUT) -> bool:
        deadline = time.time() + (timeout / 1000)
        error_selectors = [
            'text="Incorrect code"',
            'text="Invalid code"',
            'text="Expired code"',
        ]
        while time.time() < deadline:
            current_url = (self._page.url or "").lower()
            if "email-verification" not in current_url and "otp" not in current_url:
                return True
            if self._has_visible_selector(error_selectors, timeout=500):
                return False
            time.sleep(0.2)
        return False

    def _enter_otp(self, code: str) -> bool:
        self._log("验证 OTP 码: ******")
        try:
            self._page.wait_for_url(
                lambda url: "email-verification" in url.lower() or "otp" in url.lower(),
                timeout=PAGE_LOAD_TIMEOUT,
            )
        except Exception:
            pass

        single_selectors = [
            'input[aria-label*="code" i]',
            'input[aria-label*="verification" i]',
            'input[id$="-code"]',
            'input[placeholder="Code"]',
            'input[data-testid="otp-input"]',
            'input[maxlength="6"]',
            'input[inputmode="numeric"][maxlength="6"]',
            'input[inputmode="numeric"][name="code"]',
        ]
        continue_selectors = [
            'button[type="submit"]',
            'button:has-text("Continue")',
            'button:has-text("Next")',
            'input[type="submit"]',
        ]
        if self._enter_single_otp_input('input[name="code"]', code, continue_selectors, timeout=DEFAULT_TIMEOUT):
            return self._wait_for_otp_accepted()
        for sel in single_selectors:
            if self._enter_single_otp_input(sel, code, continue_selectors, timeout=DEFAULT_TIMEOUT):
                return self._wait_for_otp_accepted()

        digit_selectors = [
            'input[aria-label*="digit"]',
            'input[data-index]',
            'input[inputmode="numeric"][maxlength="1"]',
        ]
        for digit_sel in digit_selectors:
            try:
                inputs = self._page.query_selector_all(digit_sel)
                if len(inputs) >= 6:
                    for i, inp in enumerate(inputs[:6]):
                        inp.click()
                        inp.fill(code[i])
                    time.sleep(0.3)
                    if not self._click_first(continue_selectors, timeout=3000):
                        raise BrowserAutomationError("未找到继续按钮")
                    return self._wait_for_otp_accepted()
            except Exception:
                continue

        raise BrowserAutomationError("未找到 OTP 输入框")

    @staticmethod
    def _age_from_birthdate(birthdate: str) -> str:
        born = date.fromisoformat(birthdate)
        today = date.today()
        age = today.year - born.year - ((today.month, today.day) < (born.month, born.day))
        return str(max(age, 18))

    def _fill_about_you(self, first_name: str, last_name: str, birthdate: str) -> None:
        name = f"{first_name} {last_name}"
        self._log("完成账号创建信息...")

        try:
            self._page.wait_for_url(
                lambda url: "about-you" in url.lower(),
                timeout=PAGE_LOAD_TIMEOUT,
            )
        except Exception:
            pass

        try:
            full_name_input = self._wait_for_enabled_locator('input[name="name"]', timeout=5000)
        except Exception:
            full_name_input = None
        if full_name_input is not None:
            full_name_input.fill(name)
            age_input = self._wait_for_enabled_locator('input[name="age"]', timeout=5000)
            age_input.fill(self._age_from_birthdate(birthdate))
            for selector in (
                'input[name="allCheckboxes"]',
                'input[name="personalInfoConsent"]',
                'input[name="thirdPartyConsent"]',
                'input[name="overseasTransferConsent"]',
            ):
                try:
                    checkbox = self._page.locator(selector).first
                    if checkbox.count() > 0 and not checkbox.is_checked():
                        checkbox.check(force=True, timeout=3000)
                except Exception:
                    continue
            try:
                submit_button = self._page.locator('button:has-text("Finish creating account")').first
                submit_button.wait_for(state="visible", timeout=DEFAULT_TIMEOUT)
                submit_button.click(timeout=DEFAULT_TIMEOUT)
            except Exception as exc:
                raise BrowserAutomationError("未找到提交按钮") from exc
            return

        first_selectors = [
            'input[name="given_name"]',
            'input[id="first_name"]',
            'input[autocomplete="given-name"]',
        ]
        last_selectors = [
            'input[name="family_name"]',
            'input[id="last_name"]',
            'input[autocomplete="family-name"]',
        ]
        birth_selectors = [
            'input[type="date"]',
            'input[name="birthdate"]',
            'input[id="birthdate"]',
        ]

        if not self._fill_first(first_selectors, first_name):
            raise BrowserAutomationError("未找到名字输入框")
        if not self._fill_first(last_selectors, last_name):
            raise BrowserAutomationError("未找到姓氏输入框")
        if not self._fill_first(birth_selectors, birthdate):
            raise BrowserAutomationError("未找到生日输入框")

        continue_selectors = [
            'button[type="submit"]',
            'button:has-text("Continue")',
            'button:has-text("Next")',
            'button:has-text("Submit")',
        ]
        if not self._click_first(continue_selectors):
            raise BrowserAutomationError("未找到提交按钮")

    def _skip_post_registration_prompts(self) -> None:
        self._log("等待注册完成...")
        skip_selectors = [
            'button:has-text("Skip")',
            'button:has-text("Maybe later")',
            'button:has-text("Not now")',
            'a:has-text("Skip")',
        ]

        for _ in range(5):
            try:
                self._page.wait_for_load_state("networkidle", timeout=10000)
            except Exception:
                pass

            clicked = False
            for sel in skip_selectors:
                try:
                    el = self._page.wait_for_selector(sel, state="visible", timeout=2000)
                    el.click()
                    clicked = True
                    time.sleep(0.5)
                    break
                except Exception:
                    continue

            if not clicked:
                current_url = self._page.url
                if "chatgpt.com" in current_url and "auth" not in current_url:
                    self._log("注册流程完成")
                    return

    # ------------------------------------------------------------------
    # Token extraction
    # ------------------------------------------------------------------

    def _extract_tokens_from_browser(self) -> dict:
        response = self._context.request.get(
            f"{CHATGPT_BASE}/api/auth/session",
            timeout=PAGE_LOAD_TIMEOUT,
        )
        session_data = response.json()
        cookies = self._context.cookies()
        cookie_map = {c["name"]: c["value"] for c in cookies}
        session_token = cookie_map.get("__Secure-next-auth.session-token", "")

        return {
            "access_token": session_data.get("accessToken", ""),
            "refresh_token": session_data.get("refreshToken", ""),
            "id_token": session_data.get("idToken", ""),
            "session_token": session_token,
            "expires": session_data.get("expires", ""),
            "user_id": session_data.get("user", {}).get("id", ""),
            "account_id": (
                (session_data.get("user") or {}).get("id", "")
                or session_data.get("account_id", "")
            ),
            "workspace_id": "",
            "auth_provider": session_data.get("authProvider", ""),
            "user": session_data.get("user") or {},
            "account": session_data.get("account") or {},
            "raw_session": session_data,
        }

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def register_complete_flow(
        self,
        email: str,
        password: str,
        first_name: str,
        last_name: str,
        birthdate: str,
        email_adapter,
    ) -> Tuple[bool, str]:
        try:
            self._launch_browser()
            self._page.goto(
                CHATGPT_BASE,
                wait_until="domcontentloaded",
                timeout=PAGE_LOAD_TIMEOUT,
            )
            self._log("进入注册页面...")
            self._dismiss_cookie_banner()
            self._open_signup_entry()

            self._enter_email(email)
            self._enter_password(password)
            time.sleep(1.5)

            self._trigger_otp_resend_if_needed()

            self._log("等待邮箱验证码...")
            otp_timeout = int(getattr(email_adapter, "verification_timeout", 30))
            rejected_codes: set[str] = set()
            otp_sent_at = time.time()
            for attempt in range(2):
                otp_code = email_adapter.wait_for_verification_code(
                    email,
                    timeout=otp_timeout,
                    otp_sent_at=otp_sent_at,
                    exclude_codes=rejected_codes,
                )
                if not otp_code:
                    return False, "未收到验证码"
                if self._enter_otp(otp_code):
                    break
                rejected_codes.add(otp_code)
                if attempt == 1:
                    return False, "验证码被拒绝"
                self._log("验证码被拒绝，重新发送验证码...")
                self._trigger_otp_resend_if_needed()
                otp_sent_at = time.time()

            time.sleep(1)

            self._fill_about_you(first_name, last_name, birthdate)
            time.sleep(1.5)

            self._skip_post_registration_prompts()

            self._cached_tokens = self._extract_tokens_from_browser()

            return True, "注册成功"
        except BrowserAutomationError as e:
            self._log(f"浏览器自动化失败: {e}")
            return False, str(e)
        except Exception as e:
            self._log(f"注册流程异常: {e}")
            return False, str(e)
        finally:
            self._close_browser()

    def reuse_session_and_get_tokens(self) -> Tuple[bool, dict]:
        if self._cached_tokens:
            return True, self._cached_tokens
        return False, {"error": "浏览器已关闭，无可用会话"}
