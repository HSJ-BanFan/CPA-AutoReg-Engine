import time
from types import SimpleNamespace

import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient

from src.config.settings import get_settings
from src.web.app import create_app
from src.web.auth import WEBUI_AUTH_MAX_AGE_SECONDS, is_webui_authenticated, require_webui_auth, webui_auth_token
from src.web.routes import api_router


def test_email_full_config_requires_webui_auth_cookie() -> None:
    request = SimpleNamespace(cookies={})

    with pytest.raises(HTTPException) as exc_info:
        require_webui_auth(request)

    assert exc_info.value.status_code == 401


def test_auth_helper_rejects_object_without_cookies() -> None:
    assert is_webui_authenticated(SimpleNamespace()) is False


def test_email_full_config_accepts_valid_webui_auth_cookie() -> None:
    password = get_settings().webui_access_password.get_secret_value()
    request = SimpleNamespace(cookies={"webui_auth": webui_auth_token(password)})

    assert require_webui_auth(request) is None


def test_expired_webui_auth_cookie_is_rejected() -> None:
    password = get_settings().webui_access_password.get_secret_value()
    issued_at = int(time.time()) - WEBUI_AUTH_MAX_AGE_SECONDS - 1
    request = SimpleNamespace(cookies={"webui_auth": webui_auth_token(password, issued_at)})

    with pytest.raises(HTTPException) as exc_info:
        require_webui_auth(request)

    assert exc_info.value.status_code == 401


def test_future_webui_auth_cookie_is_rejected() -> None:
    password = get_settings().webui_access_password.get_secret_value()
    request = SimpleNamespace(cookies={"webui_auth": webui_auth_token(password, int(time.time()) + 120)})

    with pytest.raises(HTTPException) as exc_info:
        require_webui_auth(request)

    assert exc_info.value.status_code == 401


def test_malformed_webui_auth_cookie_is_rejected() -> None:
    request = SimpleNamespace(cookies={"webui_auth": "1:not-a-signature"})

    with pytest.raises(HTTPException) as exc_info:
        require_webui_auth(request)

    assert exc_info.value.status_code == 401


def test_api_router_requires_webui_auth_for_all_http_endpoints() -> None:
    assert api_router.dependencies
    assert all(dependency.dependency is require_webui_auth for dependency in api_router.dependencies)


def test_payment_page_requires_webui_auth() -> None:
    client = TestClient(create_app())

    response = client.get("/payment", follow_redirects=False)

    assert response.status_code == 302
    assert response.headers["location"] == "/login?next=/payment"
