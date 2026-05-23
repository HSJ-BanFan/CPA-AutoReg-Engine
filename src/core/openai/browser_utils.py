"""
Shared browser automation utilities.
"""

from typing import Optional
from urllib.parse import urlparse


def build_playwright_proxy_config(proxy: Optional[str]) -> Optional[dict]:
    """Parse a proxy URL string into a Playwright-compatible proxy dict.

    Returns None if proxy is empty/None, otherwise a dict with server,
    optional username/password keys.
    """
    if not proxy:
        return None
    if "://" not in proxy:
        proxy = f"http://{proxy}"
    parsed = urlparse(proxy)
    if not parsed.scheme or not parsed.hostname:
        return {"server": proxy}
    server = f"{parsed.scheme}://{parsed.hostname}"
    if parsed.port:
        server = f"{server}:{parsed.port}"
    config: dict = {"server": server}
    if parsed.username:
        config["username"] = parsed.username
    if parsed.password:
        config["password"] = parsed.password
    return config
