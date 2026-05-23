"""
Sentinel token generator.
"""

import base64
import hashlib
import json
import random
import time
import uuid

from typing import Optional

from .browser_utils import build_playwright_proxy_config


class SentinelTokenGenerator:
    """Pure Python generator for openai-sentinel-token."""

    MAX_ATTEMPTS = 500000
    ERROR_PREFIX = "wQ8Lk5FbGpA2NcR9dShT6gYjU7VxZ4D"

    def __init__(self, device_id: Optional[str] = None, user_agent: Optional[str] = None):
        self.device_id = device_id or str(uuid.uuid4())
        self.user_agent = user_agent or (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/145.0.0.0 Safari/537.36"
        )
        self.requirements_seed = str(random.random())
        self.sid = str(uuid.uuid4())

    @staticmethod
    def _fnv1a_32(text: str) -> str:
        h = 2166136261
        for ch in text:
            h ^= ord(ch)
            h = (h * 16777619) & 0xFFFFFFFF
        h ^= h >> 16
        h = (h * 2246822507) & 0xFFFFFFFF
        h ^= h >> 13
        h = (h * 3266489909) & 0xFFFFFFFF
        h ^= h >> 16
        return format(h & 0xFFFFFFFF, "08x")

    def _get_config(self):
        from datetime import datetime, timezone

        screen_info = "1920x1080"
        now = datetime.now(timezone.utc)
        date_str = now.strftime("%a %b %d %Y %H:%M:%S GMT+0000 (Coordinated Universal Time)")
        js_heap_limit = 4294705152
        nav_random1 = random.random()
        script_src = "https://sentinel.openai.com/sentinel/20260124ceb8/sdk.js"
        language = "en-US"
        languages = "en-US,en"
        nav_random2 = random.random()
        nav_props = [
            "vendorSub", "productSub", "vendor", "maxTouchPoints",
            "scheduling", "userActivation", "doNotTrack", "geolocation",
            "connection", "plugins", "mimeTypes", "pdfViewerEnabled",
            "webkitTemporaryStorage", "webkitPersistentStorage",
            "hardwareConcurrency", "cookieEnabled", "credentials",
            "mediaDevices", "permissions", "locks", "ink",
        ]
        nav_prop = random.choice(nav_props)
        nav_val = f"{nav_prop}−undefined"
        doc_key = random.choice(["location", "implementation", "URL", "documentURI", "compatMode"])
        win_key = random.choice(["Object", "Function", "Array", "Number", "parseFloat", "undefined"])
        perf_now = random.uniform(1000, 50000)
        hardware_concurrency = random.choice([4, 8, 12, 16])
        time_origin = time.time() * 1000 - perf_now
        return [
            screen_info, date_str, js_heap_limit, nav_random1, self.user_agent,
            script_src, None, None, language, languages, nav_random2, nav_val,
            doc_key, win_key, perf_now, self.sid, "", hardware_concurrency, time_origin,
        ]

    @staticmethod
    def _base64_encode(data) -> str:
        json_str = json.dumps(data, separators=(",", ":"), ensure_ascii=False)
        return base64.b64encode(json_str.encode("utf-8")).decode("ascii")

    def _run_check(self, start_time: float, seed: str, difficulty: str, config, nonce: int):
        config[3] = nonce
        config[9] = round((time.time() - start_time) * 1000)
        data = self._base64_encode(config)
        hash_hex = self._fnv1a_32(seed + data)
        diff_len = len(difficulty)
        if hash_hex[:diff_len] <= difficulty:
            return data + "~S"
        return None

    def generate_token(self, seed: Optional[str] = None, difficulty: Optional[str] = None) -> str:
        if seed is None:
            seed = self.requirements_seed
            difficulty = difficulty or "0"

        start_time = time.time()
        config = self._get_config()

        for i in range(self.MAX_ATTEMPTS):
            result = self._run_check(start_time, seed, difficulty or "0", config, i)
            if result:
                return "gAAAAAB" + result

        return "gAAAAAB" + self.ERROR_PREFIX + self._base64_encode(str(None))

    def generate_requirements_token(self) -> str:
        config = self._get_config()
        config[3] = 1
        config[9] = round(random.uniform(5, 50))
        return "gAAAAAC" + self._base64_encode(config)


def fetch_sentinel_challenge(
    session,
    device_id: str,
    flow: str = "authorize_continue",
    user_agent: Optional[str] = None,
    sec_ch_ua: Optional[str] = None,
    impersonate: Optional[str] = None,
):
    """Fetch sentinel challenge from backend API."""
    generator = SentinelTokenGenerator(device_id=device_id, user_agent=user_agent)
    req_body = {
        "p": generator.generate_requirements_token(),
        "id": device_id,
        "flow": flow,
    }
    headers = {
        "Content-Type": "text/plain;charset=UTF-8",
        "Referer": "https://sentinel.openai.com/backend-api/sentinel/frame.html",
        "Origin": "https://sentinel.openai.com",
        "User-Agent": user_agent or "Mozilla/5.0",
        "sec-ch-ua": sec_ch_ua or '"Not:A-Brand";v="99", "Google Chrome";v="145", "Chromium";v="145"',
        "sec-ch-ua-mobile": "?0",
        "sec-ch-ua-platform": '"Windows"',
    }
    kwargs = {
        "data": json.dumps(req_body),
        "headers": headers,
        "timeout": 20,
    }
    if impersonate:
        kwargs["impersonate"] = impersonate
    try:
        resp = session.post("https://sentinel.openai.com/backend-api/sentinel/req", **kwargs)
        if resp.status_code == 200:
            return resp.json()
    except Exception:
        pass
    return None


def build_browser_sentinel_token(
    *,
    flow: str,
    proxy: Optional[str] = None,
    page_url: Optional[str] = None,
    headless: bool = True,
    device_id: Optional[str] = None,
    user_agent: Optional[str] = None,
    timeout_ms: int = 45000,
) -> Optional[str]:
    try:
        from playwright.sync_api import sync_playwright
    except Exception:
        return None

    target_url = page_url or "https://auth.openai.com/about-you"
    launch_args = {
        "headless": headless,
        "args": [
            "--no-sandbox",
            "--disable-blink-features=AutomationControlled",
        ],
    }
    proxy_config = build_playwright_proxy_config(proxy)
    if proxy_config:
        launch_args["proxy"] = proxy_config

    try:
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch(**launch_args)
            try:
                context = browser.new_context(
                    viewport={"width": 1440, "height": 900},
                    user_agent=user_agent,
                    ignore_https_errors=True,
                )
                if device_id:
                    context.add_cookies([
                        {
                            "name": "oai-did",
                            "value": str(device_id),
                            "domain": domain,
                            "path": "/",
                            "secure": True,
                            "sameSite": "Lax",
                        }
                        for domain in ("auth.openai.com", "chatgpt.com")
                    ])
                page = context.new_page()
                page.goto(target_url, wait_until="domcontentloaded", timeout=timeout_ms)
                page.wait_for_function(
                    "() => typeof window.SentinelSDK !== 'undefined' && typeof window.SentinelSDK.token === 'function'",
                    timeout=min(timeout_ms, 15000),
                )
                result = page.evaluate(
                    """
                    async ({ flow }) => {
                        try {
                            const token = await window.SentinelSDK.token(flow);
                            return { success: true, token };
                        } catch (error) {
                            return { success: false, error: String(error && error.message || error) };
                        }
                    }
                    """,
                    {"flow": flow},
                )
                if isinstance(result, dict) and result.get("success") and result.get("token"):
                    return str(result["token"]).strip() or None
                return None
            finally:
                browser.close()
    except Exception:
        return None


def build_sentinel_token(
    session,
    device_id: str,
    flow: str = "authorize_continue",
    user_agent: Optional[str] = None,
    sec_ch_ua: Optional[str] = None,
    impersonate: Optional[str] = None,
) -> Optional[str]:
    """Build full openai-sentinel-token JSON string."""
    challenge = fetch_sentinel_challenge(
        session,
        device_id,
        flow=flow,
        user_agent=user_agent,
        sec_ch_ua=sec_ch_ua,
        impersonate=impersonate,
    )
    if not challenge:
        return None

    c_value = challenge.get("token", "")
    if not c_value:
        return None

    pow_data = challenge.get("proofofwork") or {}
    generator = SentinelTokenGenerator(device_id=device_id, user_agent=user_agent)
    if pow_data.get("required") and pow_data.get("seed"):
        p_value = generator.generate_token(
            seed=pow_data.get("seed"),
            difficulty=pow_data.get("difficulty", "0"),
        )
    else:
        p_value = generator.generate_requirements_token()

    return json.dumps(
        {
            "p": p_value,
            "t": "",
            "c": c_value,
            "id": device_id,
            "flow": flow,
        },
        separators=(",", ":"),
    )
