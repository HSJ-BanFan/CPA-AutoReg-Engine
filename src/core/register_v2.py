"""
V2 registration engine.
"""

import inspect
import json
import logging
import subprocess
import sys
import time
from datetime import datetime, timezone
from typing import Any, Callable, Dict, List, Optional, Tuple

from ..config.constants import EmailServiceType
from ..config.settings import get_settings
from ..database import crud
from ..database.session import get_db
from .registration_result import RegistrationResult
from .openai.chatgpt_client_v2 import ChatGPTClient
from .openai.chatgpt_flow_utils import (
    generate_random_birthday,
    generate_random_name,
    generate_random_password,
)


logger = logging.getLogger(__name__)

MIN_VERIFICATION_TIMEOUT = 1
MAX_VERIFICATION_TIMEOUT = 180
CAMOUFOX_SUBPROCESS_TIMEOUT_BUFFER = 300


def _parse_verification_timeout(value: Any) -> int:
    try:
        configured_timeout = int(value)
    except (TypeError, ValueError):
        configured_timeout = 30
    return min(MAX_VERIFICATION_TIMEOUT, max(MIN_VERIFICATION_TIMEOUT, configured_timeout))


def _parse_session_expires_at(value: Any) -> Optional[datetime]:
    if not value:
        return None
    try:
        return datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None


def _run_camoufox_registration_subprocess(
    payload: Dict[str, Any],
    log_client_message: Callable[[str], None],
    log_engine_message: Callable[[str], None],
    timeout: int,
) -> Tuple[bool, str, bool, Dict[str, Any]]:
    command = [sys.executable, "-m", "src.core.camoufox_registration_worker"]
    try:
        completed = subprocess.run(
            command,
            input=json.dumps(payload, ensure_ascii=False, default=str),
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=timeout,
        )
    except subprocess.TimeoutExpired:
        return False, "Camoufox 子进程超时", False, {}
    except Exception as exc:
        return False, f"Camoufox 子进程启动失败: {exc.__class__.__name__}", False, {}

    result_payload = None
    non_json_lines: List[str] = []
    for line in (completed.stdout or "").splitlines():
        try:
            event = json.loads(line)
        except json.JSONDecodeError:
            stripped = line.strip()
            if stripped:
                non_json_lines.append(stripped)
            continue
        if event.get("type") == "log":
            message = str(event.get("message") or "")
            if event.get("source") == "client":
                log_client_message(message)
            else:
                log_engine_message(message)
        elif event.get("type") == "result":
            result_payload = event

    if not result_payload:
        diagnostic = (completed.stdout or "").strip()
        if len(diagnostic) > 2000:
            diagnostic = diagnostic[-2000:]
        error_msg = f"Camoufox 子进程未返回结果 (exit={completed.returncode})"
        if non_json_lines:
            error_msg += f"; 原始输出: {diagnostic[:500]}"
        return False, error_msg, False, {}

    session_result = result_payload.get("session_result")
    if not isinstance(session_result, dict):
        session_result = {}
    return (
        bool(result_payload.get("success")),
        str(result_payload.get("message") or ""),
        bool(result_payload.get("session_ok")),
        session_result,
    )


class EmailServiceAdapter:
    """Adapt project email services to the V2 state machine."""

    def __init__(
        self,
        email_service,
        email_info: Optional[Dict[str, Any]],
        log_fn: Callable[[str], None],
        check_cancelled: Optional[Callable[[], bool]] = None,
    ):
        self.email_service = email_service
        self.email_info = email_info or {}
        self.log_fn = log_fn
        self.check_cancelled = check_cancelled or (lambda: False)
        self.verification_timeout = _parse_verification_timeout(
            (getattr(self.email_service, "config", {}) or {}).get("timeout", 30)
        )
        self._used_codes = set()
        self._signature = inspect.signature(self.email_service.get_verification_code)

    def wait_for_verification_code(
        self,
        email: str,
        timeout: int = 60,
        otp_sent_at: Optional[float] = None,
        exclude_codes=None,
    ):
        self.log_fn(f"正在等待邮箱 {email} 的验证码 ({timeout}s)...")
        started = time.time()
        remaining = max(1, int(timeout))

        while remaining > 0:
            if self.check_cancelled():
                self.log_fn("验证码等待已取消")
                return None

            kwargs = {
                "email": email,
                "email_id": self.email_info.get("service_id"),
                "timeout": min(remaining, 8),
                "otp_sent_at": otp_sent_at,
            }
            if "exclude_codes" in self._signature.parameters:
                kwargs["exclude_codes"] = exclude_codes or self._used_codes

            code = self.email_service.get_verification_code(**kwargs)
            if code:
                self._used_codes.add(code)
                self.log_fn("成功获取验证码")
                return code

            elapsed = int(time.time() - started)
            remaining = max(0, int(timeout) - elapsed)

        return None


class RegistrationEngineV2:
    """Registration engine using the V2 ChatGPT state machine."""

    def __init__(
        self,
        email_service,
        proxy_url: Optional[str] = None,
        browser_mode: str = "protocol",
        callback_logger: Optional[Callable[[str], None]] = None,
        task_uuid: Optional[str] = None,
        status_callback: Optional[Callable[[str, Any], None]] = None,
        check_cancelled: Optional[Callable[[], bool]] = None,
        max_retries: Optional[int] = None,
    ):
        self.email_service = email_service
        self.proxy_url = proxy_url
        self.browser_mode = browser_mode or "protocol"
        self.callback_logger = callback_logger or (lambda msg: logger.info(msg))
        self.task_uuid = task_uuid
        self.status_callback = status_callback
        self.check_cancelled = check_cancelled or (lambda: False)

        settings = get_settings()
        self.max_retries = max(1, int(max_retries or settings.registration_max_retries or 3))
        self.default_password_length = max(12, int(getattr(settings, "registration_default_password_length", 12) or 12))

        self.email: Optional[str] = None
        self.password: Optional[str] = None
        self.email_info: Optional[Dict[str, Any]] = None
        self.logs: List[str] = []

    def _is_cancelled(self) -> bool:
        return bool(self.check_cancelled and self.check_cancelled())

    def _raise_if_cancelled(self):
        if self._is_cancelled():
            raise RuntimeError("任务已取消")

    def _log(self, message: str, level: str = "info"):
        tags = {
            "info": "信息",
            "success": "成功",
            "warning": "警告",
            "error": "错误",
            "system": "系统",
        }
        log_message = f"[{tags.get(level.lower(), level.upper())}] {message}"
        self.logs.append(log_message)
        self.callback_logger(log_message)
        if level == "error":
            logger.error(message)
        elif level == "warning":
            logger.warning(message)
        else:
            logger.info(message)

    def _should_retry(self, message: str) -> bool:
        text = str(message or "").lower()
        if "registration_disallowed" in text:
            return False

        retriable_markers = [
            "tls",
            "ssl",
            "curl: (35)",
            "预授权被拦截",
            "authorize",
            "http 400",
            "创建账号失败",
            "未获取到 authorization code",
            "consent",
            "workspace",
            "organization",
            "otp",
            "验证码",
            "session",
            "accesstoken",
            "next-auth",
        ]
        if self.browser_mode == "camoufox":
            retriable_markers.extend([
                "timeout",
                "navigation",
                "selector",
                "element not found",
                "cloudflare",
                "challenge",
            ])
        return any(marker in text for marker in retriable_markers)

    def _log_client_message(self, message: str):
        """Translate V2 low-level client logs into the old stage-based style."""
        text = str(message or "").strip()
        if not text:
            return

        mapped = None
        level = "info"

        if text.startswith("正在启动 Camoufox"):
            mapped = "[系统] 正在启动浏览器引擎..."
        elif text == "Camoufox 浏览器已启动":
            mapped = "[系统] 浏览器引擎就绪"
        elif text == "进入注册页面...":
            mapped = "[阶段 2] 正在初始化浏览器授权会话..."
        elif text.startswith("设置账号密码"):
            mapped = "[阶段 3] 正在配置账号凭据..."
        elif text == "触发发送验证码...":
            mapped = "[阶段 4] 正在分发验证码..."
        elif text == "等待邮箱验证码...":
            mapped = "[阶段 5] 正在同步邮箱数据..."
        elif text.startswith("验证 OTP 码:"):
            mapped = "[阶段 6] 正在核验身份信息..."
        elif text == "等待注册完成...":
            mapped = "[阶段 7] 正在完成账户配置..."
        elif text == "注册流程完成":
            mapped = "注册主流程已完成"
        elif text.startswith("浏览器自动化失败"):
            mapped = text
            level = "error"
        elif text == "访问 ChatGPT 首页...":
            mapped = "[阶段 2] 正在初始化授权会话..."
        elif text == "获取 CSRF token...":
            mapped = "[阶段 2] 正在获取授权上下文..."
        elif text.startswith("CSRF token:"):
            mapped = "[阶段 2] 授权上下文已就绪"
        elif text.startswith("提交邮箱:"):
            mapped = "[阶段 2] 正在提交身份核验..."
        elif text.startswith("访问 authorize URL..."):
            mapped = "[阶段 2] 正在建立授权链路..."
        elif text.startswith("重定向到:"):
            mapped = "[阶段 2] 授权入口已响应"
        elif text.startswith("Authorize →"):
            mapped = "[阶段 2] 身份核验已通过"
        elif text.startswith("注册状态起点:"):
            mapped = "[阶段 2] 已进入账号创建流程"
        elif text == "全新注册流程":
            mapped = "[阶段 3] 正在配置账号凭据..."
        elif text.startswith("注册用户:"):
            mapped = "正在提交账号凭据..."
        elif text == "注册成功":
            mapped = "账号凭据配置完成"
        elif text == "触发发送验证码...":
            mapped = "[阶段 4] 正在分发验证码..."
        elif text == "等待邮箱验证码...":
            mapped = "[阶段 5] 正在同步邮箱数据..."
        elif text.startswith("验证 OTP 码"):
            mapped = "[阶段 6] 正在核验身份信息..."
        elif text.startswith("验证成功"):
            mapped = "身份核验完成"
        elif text.startswith("完成账号创建:"):
            mapped = "[阶段 7] 正在完成账户配置..."
        elif text.startswith("create_account: 已生成 sentinel token"):
            return
        elif text.startswith("create_account: 未生成 sentinel token"):
            mapped = "Sentinel 降级继续执行账户配置"
            level = "warning"
        elif text.startswith("账号创建成功"):
            mapped = "账户配置完成"
        elif text.startswith("follow ->"):
            return
        elif text.startswith("follow state ->"):
            return
        elif text.startswith("步骤 1/4:"):
            mapped = "[阶段 8] 正在同步注册回调状态..."
        elif text.startswith("步骤 2/4:"):
            mapped = "[阶段 9] 正在同步会话令牌..."
        elif text.startswith("步骤 3/4:"):
            mapped = "[阶段 10] 正在获取账户访问令牌..."
        elif text.startswith("步骤 4/4:"):
            mapped = "[阶段 10] 访问令牌同步完成"
        elif text == "注册回调已落地，跳过额外跟随":
            return
        elif text.startswith("Session Account ID:"):
            return
        elif text.startswith("Session User ID:"):
            return
        elif text.startswith("Session Workspace ID:"):
            mapped = f"组织 ID: {text.split(':', 1)[1].strip()}"
        elif text.startswith("预授权阶段重试"):
            mapped = text
            level = "warning"
        elif "Cloudflare/SPA 中间页" in text:
            mapped = "授权链路被风控中间页拦截，准备重试"
            level = "warning"
        elif text.startswith("发送验证码接口返回失败"):
            mapped = "验证码发送接口返回异常，继续等待邮件验证码"
            level = "warning"
        elif text.startswith("未知起始状态"):
            mapped = "授权状态未稳定，正在回退到注册入口继续"
            level = "warning"
        elif text == "注册流程完成":
            mapped = "注册主流程已完成"
        elif text.startswith("获取到 authorize URL"):
            return

        if mapped is None:
            mapped = text

        self._log(mapped, level)

    def _prepare_email(self) -> bool:
        try:
            self._raise_if_cancelled()
            self._log(f"正在准备 {self.email_service.service_type.value} 邮箱账户...")
            self.email_info = self.email_service.create_email()
            resolved_email = (self.email_info or {}).get("email") or self.email
            if not resolved_email:
                self._log("邮箱创建失败: 返回信息不完整", "error")
                return False
            self.email = resolved_email
            self._log(f"成功创建邮箱: {self.email}")
            if self.status_callback:
                self.status_callback("running", email=self.email)
            return True
        except Exception as e:
            self._log(f"创建邮箱失败: {e}", "error")
            return False

    def run(self) -> RegistrationResult:
        result = RegistrationResult(success=False, logs=self.logs)
        try:
            last_error = ""
            for attempt in range(self.max_retries):
                try:
                    self._raise_if_cancelled()
                    if attempt == 0:
                        self._log("-" * 40)
                        self._log("注册引擎: 流程启动")
                        self._log("-" * 40)
                    else:
                        self._log(f"整流程重试 {attempt + 1}/{self.max_retries} ...")
                        time.sleep(1)

                    self.email_info = None
                    self._log("[阶段 1] 正在开通邮箱账户...")
                    if not self._prepare_email():
                        result.error_message = "邮箱账户开通失败"
                        return result

                    result.email = self.email or ""
                    pwd = self.password or generate_random_password(self.default_password_length)
                    self.password = pwd
                    result.password = pwd

                    first_name, last_name = generate_random_name()
                    birthdate = generate_random_birthday()
                    self._log(f"邮箱账户: {result.email}")

                    self._raise_if_cancelled()
                    email_adapter = EmailServiceAdapter(
                        self.email_service,
                        self.email_info,
                        self._log,
                        check_cancelled=self.check_cancelled,
                    )
                    if self.browser_mode == "camoufox":
                        email_config = getattr(self.email_service, "config", {}) or {}

                        # Pack email service internal state for the isolated worker
                        internal_state = {}
                        if self.email_service.service_type == EmailServiceType.CLOUDFLARE_TEMP_EMAIL:
                            # Safely extract internal state if it exists
                            created_emails = getattr(self.email_service, "_created_emails", {})
                            if self.email in created_emails:
                                internal_state["cf_email_state"] = created_emails[self.email]

                        payload = {
                            "email": result.email,
                            "password": pwd,
                            "first_name": first_name,
                            "last_name": last_name,
                            "birthdate": birthdate,
                            "proxy_url": self.proxy_url,
                            "email_service_type": self.email_service.service_type.value,
                            "email_service_config": email_config,
                            "email_info": self.email_info or {},
                            "email_internal_state": internal_state,
                        }
                        success, msg, session_ok, session_result = _run_camoufox_registration_subprocess(
                            payload,
                            self._log_client_message,
                            self._log,
                            email_adapter.verification_timeout + CAMOUFOX_SUBPROCESS_TIMEOUT_BUFFER,
                        )
                    else:
                        client = ChatGPTClient(
                            proxy=self.proxy_url,
                            verbose=False,
                            browser_mode=self.browser_mode,
                        )
                        client._log = self._log_client_message
                        success, msg = client.register_complete_flow(
                            result.email,
                            pwd,
                            first_name,
                            last_name,
                            birthdate,
                            email_adapter,
                        )
                        self._raise_if_cancelled()
                        session_ok = False
                        session_result = {}
                        if success:
                            self._log("[阶段 8] 正在同步账户访问令牌...")
                            self._raise_if_cancelled()
                            session_ok, session_result = client.reuse_session_and_get_tokens()

                    self._raise_if_cancelled()
                    if not success:
                        last_error = f"注册流失败: {msg}"
                        if attempt < self.max_retries - 1 and self._should_retry(msg):
                            self._log(f"注册流失败，准备整流程重试: {msg}", "warning")
                            continue
                        result.error_message = last_error
                        return result
                    if session_ok:
                        self._raise_if_cancelled()
                        result.success = True
                        result.access_token = session_result.get("access_token", "")
                        result.refresh_token = session_result.get("refresh_token", "")
                        result.id_token = session_result.get("id_token", "")
                        result.session_token = session_result.get("session_token", "")
                        result.expires_at = _parse_session_expires_at(session_result.get("expires"))
                        refresh_tz = result.expires_at.tzinfo if result.expires_at else timezone.utc
                        result.last_refresh = datetime.now(refresh_tz or timezone.utc)
                        device_id = str(session_result.get("device_id", ""))
                        result.account_id = (
                            session_result.get("account_id")
                            or session_result.get("user_id")
                            or ("v2_acct_" + device_id[:8])
                        )
                        result.workspace_id = session_result.get("workspace_id", "")
                        result.source = "register"
                        result.metadata = {
                            "email_service": self.email_service.service_type.value,
                            "proxy_used": self.proxy_url,
                            "registered_at": datetime.now().isoformat(),
                            "auth_provider": session_result.get("auth_provider", ""),
                            "expires": session_result.get("expires", ""),
                            "user_id": session_result.get("user_id", ""),
                            "user": session_result.get("user") or {},
                            "account": session_result.get("account") or {},
                            "raw_session": session_result.get("raw_session") or {},
                            "registration_engine": "v2",
                            "browser_mode": self.browser_mode,
                        }
                        self._log("-" * 40)
                        self._log("注册: 流程执行成功", "success")
                        self._log(f"邮箱账户: {result.email}")
                        self._log(f"账号 ID: {result.account_id}")
                        if result.workspace_id:
                            self._log(f"组织 ID: {result.workspace_id}")
                        self._log("-" * 40)
                        return result

                    last_error = f"注册成功，但复用会话获取 AccessToken 失败: {session_result}"
                    if attempt < self.max_retries - 1:
                        self._log(f"{last_error}，准备整流程重试", "warning")
                        continue
                    result.error_message = last_error
                    return result
                except Exception as attempt_error:
                    last_error = str(attempt_error)
                    if attempt < self.max_retries - 1 and self._should_retry(last_error):
                        self._log(f"本轮出现异常，准备整流程重试: {last_error}", "warning")
                        continue
                    raise

            result.error_message = last_error or "注册失败"
            return result
        except Exception as e:
            if str(e) == "任务已取消":
                self._log("注册流程已收到取消信号", "warning")
                result.error_message = "任务已取消"
                return result
            self._log(f"V2 注册全流程执行异常: {e}", "error")
            result.error_message = str(e)
            return result

    def _push_to_webchat2api(self, result: RegistrationResult, settings) -> None:
        if not getattr(settings, "webchat2api_enabled", False):
            return
        try:
            from .upload.webchat2api_upload import push_registration_to_webchat2api

            api_token = settings.webchat2api_api_token.get_secret_value()
            success, message = push_registration_to_webchat2api(
                result,
                base_url=settings.webchat2api_base_url,
                api_token=api_token,
            )
            self._log(message, "success" if success else "warning")
        except Exception as e:
            self._log(f"webchat2api 推送失败: {e}", "warning")

    def save_to_database(self, result: RegistrationResult) -> bool:
        """Persist registration result in the current project's schema."""
        if not result.success:
            return False
        try:
            settings = get_settings()
            with get_db() as db:
                account = crud.create_account(
                    db,
                    email=result.email,
                    password=result.password,
                    client_id=settings.openai_client_id,
                    session_token=result.session_token,
                    email_service=self.email_service.service_type.value,
                    email_service_id=self.email_info.get("service_id") if self.email_info else None,
                    account_id=result.account_id,
                    workspace_id=result.workspace_id,
                    access_token=result.access_token,
                    refresh_token=result.refresh_token,
                    id_token=result.id_token,
                    proxy_used=self.proxy_url,
                    expires_at=result.expires_at,
                    last_refresh=result.last_refresh,
                    extra_data=result.metadata,
                    source=result.source,
                )
                database_id = account.id
            self._log(f"数据持久化操作完成. 数据库 ID: {database_id}")
            self._push_to_webchat2api(result, settings)
            return True
        except Exception as e:
            self._log(f"保存到数据库失败: {e}", "error")
            return False
