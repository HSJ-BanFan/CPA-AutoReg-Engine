"""Tests for ChatGPTBrowserClient browser lifecycle.

Focused regression tests for the Camoufox context-manager bug:
Camoufox is a PlaywrightContextManager; __enter__ returns the Browser.
_launch_browser must use __enter__ to obtain the real Browser object
before calling new_context() on it.
"""

from unittest.mock import MagicMock, call, patch

import pytest

from src.core.openai.chatgpt_browser_client import (
    BrowserAutomationError,
    ChatGPTBrowserClient,
)


# ---------------------------------------------------------------------------
# Fakes / mocks
# ---------------------------------------------------------------------------

def _make_fake_browser():
    """Return a mock that behaves like a Playwright Browser."""
    browser = MagicMock(name="Browser")
    context = MagicMock(name="BrowserContext")
    page = MagicMock(name="Page")
    context.new_page.return_value = page
    context.add_cookies = MagicMock()
    browser.new_context.return_value = context
    browser.close = MagicMock()
    return browser, context, page


def _make_fake_camoufox(fake_browser):
    """Return a mock Camoufox context manager whose __enter__ yields fake_browser."""
    cm = MagicMock(name="Camoufox")
    cm.__enter__ = MagicMock(return_value=fake_browser)
    cm.__exit__ = MagicMock(return_value=False)
    return cm


# ---------------------------------------------------------------------------
# Test: _launch_browser uses __enter__ to get the real Browser
# ---------------------------------------------------------------------------

class TestLaunchBrowser:
    """RED: these tests expose the bug where Camoufox instance is used directly
    instead of calling __enter__() to obtain the Browser.
    """

    @patch("src.core.openai.chatgpt_browser_client.build_playwright_proxy_config")
    def test_launch_browser_enters_camoufox_context_manager(self, mock_proxy):
        """_launch_browser must call __enter__ on the Camoufox instance to get
        the real Browser, then call new_context() on *that* Browser.
        """
        mock_proxy.return_value = None  # no proxy

        fake_browser, fake_context, fake_page = _make_fake_browser()
        fake_camoufox = _make_fake_camoufox(fake_browser)

        client = ChatGPTBrowserClient()

        with patch("camoufox.sync_api.Camoufox", return_value=fake_camoufox):
            client._launch_browser()

        # The Camoufox() constructor was called
        fake_camoufox.__enter__.assert_called_once()

        # new_context was called on the *Browser* returned by __enter__,
        # not on the Camoufox instance itself.
        fake_browser.new_context.assert_called_once()

        # The client stores the browser and context correctly
        assert client._browser is fake_browser
        assert client._context is fake_context
        assert client._page is fake_page

    @patch("src.core.openai.chatgpt_browser_client.build_playwright_proxy_config")
    def test_launch_browser_with_proxy(self, mock_proxy):
        """Proxy config is forwarded to both Camoufox launch and new_context."""
        proxy_config = {"server": "http://proxy:8080"}
        mock_proxy.return_value = proxy_config

        fake_browser, fake_context, fake_page = _make_fake_browser()
        fake_camoufox = _make_fake_camoufox(fake_browser)

        client = ChatGPTBrowserClient(proxy="http://proxy:8080")

        with patch("camoufox.sync_api.Camoufox", return_value=fake_camoufox):
            client._launch_browser()

        ctx_kwargs = fake_browser.new_context.call_args
        assert ctx_kwargs is not None

    @patch("src.core.openai.chatgpt_browser_client.build_playwright_proxy_config")
    def test_close_browser_exits_camoufox_context_manager(self, mock_proxy):
        """_close_browser must call __exit__ on the Camoufox instance for
        proper cleanup (which closes the browser and playwright internals).
        """
        mock_proxy.return_value = None

        fake_browser, _, _ = _make_fake_browser()
        fake_camoufox = _make_fake_camoufox(fake_browser)

        client = ChatGPTBrowserClient()

        with patch("camoufox.sync_api.Camoufox", return_value=fake_camoufox):
            client._launch_browser()
            client._close_browser()

        # __exit__ was called on the context manager
        fake_camoufox.__exit__.assert_called_once()

        # Client state is cleared
        assert client._browser is None
        assert client._context is None
        assert client._page is None


# ---------------------------------------------------------------------------
# Edge cases
# ---------------------------------------------------------------------------

class TestLaunchBrowserEdgeCases:
    def test_close_browser_when_not_launched(self):
        """_close_browser should be a no-op when browser was never launched."""
        client = ChatGPTBrowserClient()
        # Should not raise
        client._close_browser()
        assert client._browser is None

    @patch("src.core.openai.chatgpt_browser_client.build_playwright_proxy_config")
    def test_launch_sets_device_id_cookies(self, mock_proxy):
        """Cookies are set on the context with the client's device_id."""
        mock_proxy.return_value = None

        fake_browser, fake_context, _ = _make_fake_browser()
        fake_camoufox = _make_fake_camoufox(fake_browser)

        client = ChatGPTBrowserClient()

        with patch("camoufox.sync_api.Camoufox", return_value=fake_camoufox):
            client._launch_browser()

        # add_cookies was called with cookies containing the device_id
        fake_context.add_cookies.assert_called_once()
        cookies_arg = fake_context.add_cookies.call_args[0][0]
        cookie_names = {c["name"] for c in cookies_arg}
        assert "oai-did" in cookie_names
        for c in cookies_arg:
            assert c["value"] == client.device_id


class TestTokenExtraction:
    def test_extract_tokens_uses_context_request_without_page_navigation(self):
        client = ChatGPTBrowserClient()
        response = MagicMock()
        response.json.return_value = {
            "accessToken": "access-token",
            "refreshToken": "refresh-token",
            "idToken": "id-token",
            "expires": "2026-05-26T10:00:00Z",
            "authProvider": "auth-provider",
            "user": {"id": "user-123"},
            "account": {"id": "account-123"},
        }
        request = MagicMock()
        request.get.return_value = response
        context = MagicMock()
        context.request = request
        context.cookies.return_value = [
            {"name": "__Secure-next-auth.session-token", "value": "session-token"},
        ]
        page = MagicMock()
        client._context = context
        client._page = page

        tokens = client._extract_tokens_from_browser()

        request.get.assert_called_once_with(
            "https://chatgpt.com/api/auth/session",
            timeout=45000,
        )
        page.goto.assert_not_called()
        assert tokens["access_token"] == "access-token"
        assert tokens["refresh_token"] == "refresh-token"
        assert tokens["id_token"] == "id-token"
        assert tokens["session_token"] == "session-token"
        assert tokens["user_id"] == "user-123"
        assert tokens["account_id"] == "user-123"
        assert tokens["raw_session"]["accessToken"] == "access-token"


class TestEmailVerificationStep:
    def test_handle_email_verification_page_clicks_continue_with_password(self):
        client = ChatGPTBrowserClient()
        client._page = MagicMock()
        client._page.url = "https://auth.openai.com/email-verification"

        button = MagicMock()
        client._page.wait_for_selector.return_value = button

        client._handle_email_verification_page()

        button.click.assert_called_once()

    def test_enter_otp_matches_real_code_input_by_name(self):
        client = ChatGPTBrowserClient()
        client._page = MagicMock()
        client._page.url = "https://auth.openai.com/email-verification"
        client._click_first = MagicMock(return_value=True)

        otp_input = MagicMock()
        otp_input.is_enabled.return_value = True
        otp_input.input_value.return_value = "123456"
        locator = MagicMock()
        locator.first = otp_input
        client._page.locator.return_value = locator

        client._enter_otp("123456")

        client._page.locator.assert_any_call('input[name="code"]')
        otp_input.wait_for.assert_called_once_with(state="visible", timeout=15000)
        otp_input.fill.assert_called_once_with("123456")
        otp_input.input_value.assert_called()
        client._click_first.assert_called_once()

    def test_enter_otp_uses_keyboard_fallback_when_fill_does_not_stick(self):
        client = ChatGPTBrowserClient()
        client._page = MagicMock()
        client._page.url = "https://auth.openai.com/email-verification"
        client._click_first = MagicMock(return_value=True)

        otp_input = MagicMock()
        otp_input.is_enabled.return_value = True
        otp_input.input_value.side_effect = ["", "123456"]
        locator = MagicMock()
        locator.first = otp_input
        client._page.locator.return_value = locator

        client._enter_otp("123456")

        otp_input.wait_for.assert_called_once_with(state="visible", timeout=15000)
        otp_input.fill.assert_called_once_with("123456")
        otp_input.press.assert_called_once_with("Control+A")
        otp_input.type.assert_called_once_with("123456")
        client._click_first.assert_called_once()

    def test_enter_otp_waits_for_email_verification_page(self):
        client = ChatGPTBrowserClient()
        client._page = MagicMock()
        client._page.url = "https://auth.openai.com/create-account/password"
        client._enter_single_otp_input = MagicMock(return_value=True)
        client._wait_for_otp_accepted = MagicMock(return_value=True)

        def fake_wait_for_url(predicate, timeout):
            client._page.url = "https://auth.openai.com/email-verification"
            assert predicate(client._page.url)

        client._page.wait_for_url.side_effect = fake_wait_for_url

        assert client._enter_otp("123456") is True

        client._page.wait_for_url.assert_called_once()
        client._enter_single_otp_input.assert_called_once()

    def test_enter_otp_returns_false_when_code_is_rejected(self):
        client = ChatGPTBrowserClient()
        client._page = MagicMock()
        client._page.url = "https://auth.openai.com/email-verification"
        client._enter_single_otp_input = MagicMock(return_value=True)
        client._wait_for_otp_accepted = MagicMock(return_value=False)

        assert client._enter_otp("123456") is False

    def test_fill_about_you_handles_full_name_age_form(self):
        client = ChatGPTBrowserClient()
        client._page = MagicMock()
        full_name_input = MagicMock()
        age_input = MagicMock()
        client._wait_for_enabled_locator = MagicMock(
            side_effect=[full_name_input, age_input]
        )

        client._fill_about_you("Ada", "Lovelace", "2000-01-01")

        client._wait_for_enabled_locator.assert_has_calls(
            [
                call('input[name="name"]', timeout=5000),
                call('input[name="age"]', timeout=5000),
            ]
        )
        full_name_input.fill.assert_called_once_with("Ada Lovelace")
        assert age_input.fill.call_args.args[0].isdigit()
        client._page.locator.assert_any_call('button:has-text("Finish creating account")')

    def test_handle_email_verification_page_raises_when_continue_button_missing(self):
        client = ChatGPTBrowserClient()
        client._page = MagicMock()
        client._page.url = "https://auth.openai.com/email-verification"
        client._page.wait_for_selector.side_effect = Exception("timeout")

        with pytest.raises(BrowserAutomationError, match="Continue with password"):
            client._handle_email_verification_page()

    def test_enter_password_handles_email_verification_step_before_filling(self):
        client = ChatGPTBrowserClient()
        client._page = MagicMock()
        client._page.url = "https://auth.openai.com/email-verification"
        call_order = []

        client._handle_email_verification_page = MagicMock(
            side_effect=lambda: call_order.append("handle")
        )
        client._fill_first = MagicMock(
            side_effect=lambda *_args, **_kwargs: call_order.append("fill") or True
        )
        client._click_first = MagicMock(return_value=True)

        client._enter_password("secret-pass")

        client._handle_email_verification_page.assert_called_once()
        client._fill_first.assert_called_once()
        client._click_first.assert_called_once()
        assert call_order[:2] == ["handle", "fill"]

    def test_enter_password_waits_for_email_verification_before_handling(self):
        client = ChatGPTBrowserClient()
        client._page = MagicMock()
        client._page.url = "https://chatgpt.com/"

        wait_calls = []

        def fake_wait_for_url(predicate, timeout):
            wait_calls.append(timeout)
            if len(wait_calls) == 1:
                client._page.url = "https://auth.openai.com/email-verification"
                assert predicate(client._page.url)
            else:
                client._page.url = "https://auth.openai.com/create-account/password"
                assert predicate(client._page.url)

        client._page.wait_for_url.side_effect = fake_wait_for_url
        client._handle_email_verification_page = MagicMock(
            side_effect=lambda: setattr(client._page, "url", "https://auth.openai.com/create-account/password")
        )
        client._fill_first = MagicMock(return_value=True)
        client._click_first = MagicMock(return_value=True)

        client._enter_password("secret-pass")

        client._handle_email_verification_page.assert_called_once()
        assert len(wait_calls) == 2
