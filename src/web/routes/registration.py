"""
注册任务 API 路由
"""

import json
import random
import time
import logging
import urllib.parse
import sys
import uuid
import asyncio
import threading
from datetime import datetime
from typing import List, Optional, Dict, Tuple

from fastapi import APIRouter, HTTPException, Query, BackgroundTasks
from pydantic import BaseModel, Field

from ...database import crud
from ...database.session import get_db
from ...database.models import RegistrationTask, Proxy
from ...core.registration_result import RegistrationResult
from ...core.register_v2 import RegistrationEngineV2 as RegistrationEngine
from ...services import EmailServiceFactory, EmailServiceType
from ...config.settings import get_settings
from ..task_manager import task_manager

logger = logging.getLogger(__name__)
router = APIRouter()

# 任务存储（简单的内存存储，生产环境应使用 Redis）
running_tasks: dict = {}
# 批量任务存储
batch_tasks: Dict[str, dict] = {}


# ============== Proxy Helper Functions ==============

def get_proxy_for_registration(db) -> Tuple[Optional[str], Optional[int]]:
    """
    获取用于注册的代理

    策略：
    1. 优先从代理列表中随机选择一个启用的代理
    2. 如果代理列表为空且启用了动态代理，调用动态代理 API 获取
    3. 否则使用系统设置中的静态默认代理

    Returns:
        Tuple[proxy_url, proxy_id]: 代理 URL 和代理 ID（如果来自代理列表）
    """
    # 先尝试从代理列表中获取
    proxy = crud.get_random_proxy(db)
    if proxy:
        return proxy.proxy_url, proxy.id

    # 代理列表为空，尝试动态代理或静态代理
    from ...core.dynamic_proxy import get_proxy_url_for_task
    proxy_url = get_proxy_url_for_task()
    if proxy_url:
        return proxy_url, None

    return None, None


def update_proxy_usage(db, proxy_id: Optional[int]):
    """更新代理的使用时间"""
    if proxy_id:
        crud.update_proxy_last_used(db, proxy_id)


@router.get("/active-tasks")
async def get_active_monitoring_tasks():
    """获取所有正在运行且可监控的任务"""
    from ..task_manager import TaskManager
    return {
        "batches": TaskManager.get_active_batches(),
        "single_task": TaskManager.get_active_single_task()
    }


# ============== Pydantic Models ==============

class RegistrationTaskCreate(BaseModel):
    """创建注册任务请求"""
    email_service_type: str = "tempmail"
    proxy: Optional[str] = None
    email_service_config: Optional[dict] = None
    email_service_id: Optional[int] = None
    auto_upload_cpa: bool = False
    cpa_service_ids: List[int] = []  # 指定 CPA 服务 ID 列表，空则取第一个启用的
    auto_upload_sub2api: bool = False
    sub2api_service_ids: List[int] = []  # 指定 Sub2API 服务 ID 列表
    auto_upload_tm: bool = False
    tm_service_ids: List[int] = []  # 指定 TM 服务 ID 列表


class BatchRegistrationRequest(BaseModel):
    """批量注册请求"""
    count: int = 1
    email_service_type: str = "tempmail"
    proxy: Optional[str] = None
    email_service_config: Optional[dict] = None
    email_service_id: Optional[int] = None
    interval_min: int = 5
    interval_max: int = 30
    concurrency: int = 1
    mode: str = "pipeline"
    auto_upload_cpa: bool = False
    cpa_service_ids: List[int] = []
    auto_upload_sub2api: bool = False
    sub2api_service_ids: List[int] = []
    auto_upload_tm: bool = False
    tm_service_ids: List[int] = []


class RegistrationTaskResponse(BaseModel):
    """注册任务响应"""
    id: int
    task_uuid: str
    status: str
    email_service_id: Optional[int] = None
    proxy: Optional[str] = None
    logs: Optional[str] = None
    result: Optional[dict] = None
    error_message: Optional[str] = None
    created_at: Optional[str] = None
    started_at: Optional[str] = None
    completed_at: Optional[str] = None

    class Config:
        from_attributes = True


class BatchRegistrationResponse(BaseModel):
    """批量注册响应"""
    batch_id: str
    count: int
    tasks: List[RegistrationTaskResponse]


class TaskListResponse(BaseModel):
    """任务列表响应"""
    total: int
    tasks: List[RegistrationTaskResponse]


# ============== Helper Functions ==============

def task_to_response(task: RegistrationTask) -> RegistrationTaskResponse:
    """转换任务模型为响应"""
    return RegistrationTaskResponse(
        id=task.id,
        task_uuid=task.task_uuid,
        status=task.status,
        email_service_id=task.email_service_id,
        proxy=task.proxy,
        logs=task.logs,
        result=task.result,
        error_message=task.error_message,
        created_at=task.created_at.isoformat() if task.created_at else None,
        started_at=task.started_at.isoformat() if task.started_at else None,
        completed_at=task.completed_at.isoformat() if task.completed_at else None,
    )


def _normalize_email_service_config(
    service_type: EmailServiceType,
    config: Optional[dict],
    proxy_url: Optional[str] = None
) -> dict:
    """按服务类型兼容旧字段名，避免不同服务的配置键互相污染。"""
    normalized = config.copy() if config else {}

    if 'api_url' in normalized and 'base_url' not in normalized:
        normalized['base_url'] = normalized.pop('api_url')

    if service_type == EmailServiceType.TEMPMAIL:
        if 'default_domain' in normalized and 'domain' not in normalized:
            normalized['domain'] = normalized.pop('default_domain')
    elif service_type == EmailServiceType.CLOUD_MAIL:
        if 'default_domain' in normalized and 'domain' not in normalized:
            normalized['domain'] = normalized.pop('default_domain')
    elif service_type == EmailServiceType.FREEMAIL:
        if 'adminToken' in normalized and 'admin_token' not in normalized:
            normalized['admin_token'] = normalized.pop('adminToken')

    if proxy_url and 'proxy_url' not in normalized:
        normalized['proxy_url'] = proxy_url

    return normalized


def _get_task_logs_text(task_uuid: str) -> str:
    """把当前内存日志快照为数据库文本，供任务收尾时一次性持久化。"""
    logs = task_manager.get_logs(task_uuid)
    return "\n".join(logs) if logs else ""


def _run_sync_registration_task(task_uuid: str, email_service_type: str, proxy: Optional[str], email_service_config: Optional[dict], email_service_id: Optional[int] = None, log_prefix: str = "", batch_id: str = "", auto_upload_cpa: bool = False, cpa_service_ids: List[int] = None, auto_upload_sub2api: bool = False, sub2api_service_ids: List[int] = None, auto_upload_tm: bool = False, tm_service_ids: List[int] = None):
    """
    在线程池中执行的同步注册任务

    这个函数会被 run_in_executor 调用，运行在独立线程中
    """
    try:
        # 检查是否已取消
        if task_manager.is_cancelled(task_uuid):
            logger.info(f"任务 {task_uuid} 已取消，跳过执行")
            with get_db() as db:
                crud.update_registration_task(
                    db,
                    task_uuid,
                    status="cancelled",
                    completed_at=datetime.utcnow(),
                    error_message="任务已取消",
                    logs=_get_task_logs_text(task_uuid),
                )
            task_manager.update_status(task_uuid, "cancelled", error="任务已取消")
            return

        # 更新任务状态为运行中
        with get_db() as db:
            task = crud.update_registration_task(
                db, task_uuid,
                status="running",
                started_at=datetime.utcnow()
            )

        if not task:
            logger.error(f"任务不存在: {task_uuid}")
            return

        task_manager.update_status(task_uuid, "running")

        actual_proxy_url = proxy
        proxy_id = None
        service_type = EmailServiceType(email_service_type)
        settings = get_settings()

        with get_db() as db:
            if not actual_proxy_url:
                actual_proxy_url, proxy_id = get_proxy_for_registration(db)
                if actual_proxy_url:
                    logger.info(f"任务 {task_uuid} 使用代理: {actual_proxy_url[:50]}...")

            crud.update_registration_task(db, task_uuid, proxy=actual_proxy_url)

            if email_service_id:
                from ...database.models import EmailService as EmailServiceModel

                db_service = db.query(EmailServiceModel).filter(
                    EmailServiceModel.id == email_service_id,
                    EmailServiceModel.enabled == True
                ).first()

                if db_service:
                    service_type = EmailServiceType(db_service.service_type)
                    config = _normalize_email_service_config(service_type, db_service.config, actual_proxy_url)
                    crud.update_registration_task(db, task_uuid, email_service_id=db_service.id)
                    logger.info(f"使用数据库邮箱服务: {db_service.name} (ID: {db_service.id}, 类型: {service_type.value})")
                else:
                    raise ValueError(f"邮箱服务不存在或已禁用: {email_service_id}")
            else:
                if service_type == EmailServiceType.TEMPMAIL:
                    config = {
                        "base_url": settings.tempmail_base_url,
                        "timeout": settings.tempmail_timeout,
                        "max_retries": settings.tempmail_max_retries,
                        "proxy_url": actual_proxy_url,
                    }
                elif service_type == EmailServiceType.CLOUD_MAIL:
                    from ...database.models import EmailService as EmailServiceModel

                    db_service = db.query(EmailServiceModel).filter(
                        EmailServiceModel.service_type == "cloud_mail",
                        EmailServiceModel.enabled == True
                    ).order_by(EmailServiceModel.priority.asc()).first()

                    if db_service and db_service.config:
                        config = _normalize_email_service_config(service_type, db_service.config, actual_proxy_url)
                        crud.update_registration_task(db, task_uuid, email_service_id=db_service.id)
                        logger.info(f"使用数据库 CloudMail 服务: {db_service.name}")
                    else:
                        raise ValueError("没有可用的 CloudMail 邮箱服务，请先在邮箱服务页面添加并启用")
                else:
                    config = email_service_config or {}

        email_service = EmailServiceFactory.create(service_type, config)
        log_callback = task_manager.create_log_callback(task_uuid, prefix=log_prefix, batch_id=batch_id)

        engine = RegistrationEngine(
            email_service=email_service,
            proxy_url=actual_proxy_url,
            browser_mode=settings.registration_browser_mode,
            callback_logger=log_callback,
            task_uuid=task_uuid,
            status_callback=lambda st, **kw: task_manager.update_status(task_uuid, st, **kw),
            check_cancelled=task_manager.create_check_cancelled_callback(task_uuid),
        )

        result = engine.run()

        if task_manager.is_cancelled(task_uuid) or result.error_message == "任务已取消":
            with get_db() as db:
                crud.update_registration_task(
                    db,
                    task_uuid,
                    status="cancelled",
                    completed_at=datetime.utcnow(),
                    error_message="任务已取消",
                    logs=_get_task_logs_text(task_uuid),
                )
            task_manager.update_status(task_uuid, "cancelled", error="任务已取消")
            logger.info(f"注册任务已取消: {task_uuid}")
            return

        if result.success:
            engine.save_to_database(result)
            with get_db() as db:
                update_proxy_usage(db, proxy_id)
                crud.update_registration_task(
                    db, task_uuid,
                    status="completed",
                    completed_at=datetime.utcnow(),
                    result=result.to_dict(),
                    logs=_get_task_logs_text(task_uuid),
                )

            task_manager.update_status(task_uuid, "completed", email=result.email)

            has_post_uploads = auto_upload_cpa or auto_upload_sub2api or auto_upload_tm
            run_uploads_async = bool(batch_id and has_post_uploads)

            if run_uploads_async:
                log_callback("[上传] 注册已成功，后处理上传转入后台执行")
                threading.Thread(
                    target=_run_post_registration_uploads,
                    args=(
                        task_uuid,
                        result.email,
                        log_prefix,
                        batch_id,
                        auto_upload_cpa,
                        cpa_service_ids or [],
                        auto_upload_sub2api,
                        sub2api_service_ids or [],
                        auto_upload_tm,
                        tm_service_ids or [],
                    ),
                    daemon=True,
                ).start()
            elif has_post_uploads:
                _run_post_registration_uploads(
                    task_uuid=task_uuid,
                    email=result.email,
                    log_prefix=log_prefix,
                    batch_id=batch_id,
                    auto_upload_cpa=auto_upload_cpa,
                    cpa_service_ids=cpa_service_ids or [],
                    auto_upload_sub2api=auto_upload_sub2api,
                    sub2api_service_ids=sub2api_service_ids or [],
                    auto_upload_tm=auto_upload_tm,
                    tm_service_ids=tm_service_ids or [],
                )

            logger.info(f"注册任务完成: {task_uuid}, 邮箱: {result.email}")
        else:
            with get_db() as db:
                crud.update_registration_task(
                    db, task_uuid,
                    status="failed",
                    completed_at=datetime.utcnow(),
                    error_message=result.error_message,
                    logs=_get_task_logs_text(task_uuid),
                )

            task_manager.update_status(task_uuid, "failed", error=result.error_message)
            logger.warning(f"注册任务失败: {task_uuid}, 原因: {result.error_message}")

    except Exception as e:
        logger.error(f"注册任务异常: {task_uuid}, 错误: {e}")

        try:
            with get_db() as db:
                if task_manager.is_cancelled(task_uuid):
                    crud.update_registration_task(
                        db, task_uuid,
                        status="cancelled",
                        completed_at=datetime.utcnow(),
                        error_message="任务已取消",
                        logs=_get_task_logs_text(task_uuid),
                    )
                    task_manager.update_status(task_uuid, "cancelled", error="任务已取消")
                    return
                crud.update_registration_task(
                    db, task_uuid,
                    status="failed",
                    completed_at=datetime.utcnow(),
                    error_message=str(e),
                    logs=_get_task_logs_text(task_uuid),
                )

            task_manager.update_status(task_uuid, "failed", error=str(e))
        except Exception:
            pass


async def run_registration_task(task_uuid: str, email_service_type: str, proxy: Optional[str], email_service_config: Optional[dict], email_service_id: Optional[int] = None, log_prefix: str = "", batch_id: str = "", auto_upload_cpa: bool = False, cpa_service_ids: List[int] = None, auto_upload_sub2api: bool = False, sub2api_service_ids: List[int] = None, auto_upload_tm: bool = False, tm_service_ids: List[int] = None):
    """
    异步执行注册任务

    使用 run_in_executor 将同步任务放入线程池执行，避免阻塞主事件循环
    """
    loop = task_manager.get_loop()
    if loop is None:
        loop = asyncio.get_event_loop()
        task_manager.set_loop(loop)

    # 初始化 TaskManager 状态
    task_manager.update_status(task_uuid, "pending", is_subtask=bool(batch_id))
    task_manager.add_log(task_uuid, f"{log_prefix} [系统] 任务 {task_uuid[:8]} 已加入队列" if log_prefix else f"[系统] 任务 {task_uuid[:8]} 已加入队列")

    try:
        # 在线程池中执行同步任务（传入 log_prefix 和 batch_id 供回调使用）
        await loop.run_in_executor(
            task_manager.executor,
            _run_sync_registration_task,
            task_uuid,
            email_service_type,
            proxy,
            email_service_config,
            email_service_id,
            log_prefix,
            batch_id,
            auto_upload_cpa,
            cpa_service_ids or [],
            auto_upload_sub2api,
            sub2api_service_ids or [],
            auto_upload_tm,
            tm_service_ids or [],
        )
    except Exception as e:
        logger.error(f"线程池执行异常: {task_uuid}, 错误: {e}")
        task_manager.add_log(task_uuid, f"[错误] 线程池执行异常: {str(e)}")
        task_manager.update_status(task_uuid, "failed", error=str(e))


def _init_batch_state(batch_id: str, task_uuids: List[str], total: Optional[int] = None):
    """初始化批量任务内存状态"""
    import time
    actual_total = total if total is not None else len(task_uuids)
    task_manager.init_batch(batch_id, actual_total)
    existing = batch_tasks.get(batch_id)
    if existing:
        existing.update({
            "total": actual_total,
            "task_uuids": task_uuids,
            "start_time": existing.get("start_time") or time.time(),
        })
        existing.setdefault("completed", 0)
        existing.setdefault("success", 0)
        existing.setdefault("failed", 0)
        existing.setdefault("cancelled", False)
        existing.setdefault("current_index", 0)
        existing.setdefault("logs", [])
        existing.setdefault("finished", False)
        return

    batch_tasks[batch_id] = {
        "total": actual_total,
        "completed": 0,
        "success": 0,
        "failed": 0,
        "cancelled": False,
        "task_uuids": task_uuids,
        "current_index": 0,
        "logs": [],
        "finished": False,
        "start_time": time.time(),
    }


def _make_batch_helpers(batch_id: str):
    """返回 add_batch_log 和 update_batch_status 辅助函数"""
    def add_batch_log(msg: str):
        batch_tasks[batch_id]["logs"].append(msg)
        task_manager.add_batch_log(batch_id, msg)

    def update_batch_status(**kwargs):
        for key, value in kwargs.items():
            if key in batch_tasks[batch_id]:
                batch_tasks[batch_id][key] = value
        task_manager.update_batch_status(batch_id, **kwargs)

    return add_batch_log, update_batch_status


def _collect_batch_totals_from_db(task_uuids: List[str]) -> Dict[str, int]:
    """从数据库汇总批量任务最新完成情况，避免内存计数遗漏。"""
    completed = 0
    success = 0
    failed = 0

    with get_db() as db:
        for task_uuid in task_uuids:
            task = crud.get_registration_task(db, task_uuid)
            if not task:
                continue
            if task.status in {"completed", "failed", "cancelled"}:
                completed += 1
            if task.status == "completed":
                success += 1
            elif task.status == "failed":
                failed += 1

    return {
        "completed": completed,
        "success": success,
        "failed": failed,
    }


def _run_post_registration_uploads(
    task_uuid: str,
    email: str,
    log_prefix: str = "",
    batch_id: str = "",
    auto_upload_cpa: bool = False,
    cpa_service_ids: Optional[List[int]] = None,
    auto_upload_sub2api: bool = False,
    sub2api_service_ids: Optional[List[int]] = None,
    auto_upload_tm: bool = False,
    tm_service_ids: Optional[List[int]] = None,
):
    """注册成功后的上传动作，支持后台异步执行，避免拖慢批量收尾。"""
    log_callback = task_manager.create_log_callback(task_uuid, prefix=log_prefix, batch_id=batch_id)

    try:
        from ...database.models import Account as AccountModel

        with get_db() as db:
            saved_account = db.query(AccountModel).filter_by(email=email).first()
            if not saved_account or not saved_account.access_token:
                log_callback("[上传] 未找到可上传的账号访问令牌，已跳过后处理")
                return

            if auto_upload_cpa:
                try:
                    from ...core.upload.cpa_upload import upload_to_cpa, generate_token_json

                    token_data = generate_token_json(saved_account)
                    _cpa_ids = cpa_service_ids or [s.id for s in crud.get_cpa_services(db, enabled=True)]
                    if not _cpa_ids:
                        log_callback("[CPA] 无可用 CPA 服务，跳过上传")
                    for _sid in _cpa_ids:
                        try:
                            _svc = crud.get_cpa_service_by_id(db, _sid)
                            if not _svc:
                                continue
                            log_callback(f"[CPA] 正在把账号打包发往服务站: {_svc.name}")
                            _ok, _msg = upload_to_cpa(token_data, api_url=_svc.api_url, api_token=_svc.api_token)
                            if _ok:
                                saved_account.cpa_uploaded = True
                                saved_account.cpa_uploaded_at = datetime.utcnow()
                                db.commit()
                                log_callback(f"[CPA] 投递成功，服务站已签收: {_svc.name}")
                            else:
                                log_callback(f"[CPA] 上传失败({_svc.name}): {_msg}")
                        except Exception as _e:
                            log_callback(f"[CPA] 异常({_sid}): {_e}")
                except Exception as cpa_err:
                    log_callback(f"[CPA] 上传异常: {cpa_err}")

            if auto_upload_sub2api:
                try:
                    from ...core.upload.sub2api_upload import upload_to_sub2api

                    _s2a_ids = sub2api_service_ids or [s.id for s in crud.get_sub2api_services(db, enabled=True)]
                    if not _s2a_ids:
                        log_callback("[Sub2API] 无可用 Sub2API 服务，跳过上传")
                    for _sid in _s2a_ids:
                        try:
                            _svc = crud.get_sub2api_service_by_id(db, _sid)
                            if not _svc:
                                continue
                            log_callback(f"[Sub2API] 正在把账号发往服务站: {_svc.name}")
                            _ok, _msg = upload_to_sub2api([saved_account], _svc.api_url, _svc.api_key)
                            log_callback(f"[Sub2API] {'成功' if _ok else '失败'}({_svc.name}): {_msg}")
                        except Exception as _e:
                            log_callback(f"[Sub2API] 异常({_sid}): {_e}")
                except Exception as s2a_err:
                    log_callback(f"[Sub2API] 上传异常: {s2a_err}")

            if auto_upload_tm:
                try:
                    from ...core.upload.team_manager_upload import upload_to_team_manager

                    _tm_ids = tm_service_ids or [s.id for s in crud.get_tm_services(db, enabled=True)]
                    if not _tm_ids:
                        log_callback("[TM] 无可用 Team Manager 服务，跳过上传")
                    for _sid in _tm_ids:
                        try:
                            _svc = crud.get_tm_service_by_id(db, _sid)
                            if not _svc:
                                continue
                            log_callback(f"[TM] 正在把账号发往服务站: {_svc.name}")
                            _ok, _msg = upload_to_team_manager(saved_account, _svc.api_url, _svc.api_key)
                            log_callback(f"[TM] {'成功' if _ok else '失败'}({_svc.name}): {_msg}")
                        except Exception as _e:
                            log_callback(f"[TM] 异常({_sid}): {_e}")
                except Exception as tm_err:
                    log_callback(f"[TM] 上传异常: {tm_err}")
    except Exception as e:
        log_callback(f"[上传] 后处理任务异常: {e}")


async def run_batch_parallel(
    batch_id: str,
    task_uuids: List[str],
    email_service_type: str,
    proxy: Optional[str],
    email_service_config: Optional[dict],
    email_service_id: Optional[int],
    concurrency: int,
    auto_upload_cpa: bool = False,
    cpa_service_ids: List[int] = None,
    auto_upload_sub2api: bool = False,
    sub2api_service_ids: List[int] = None,
    auto_upload_tm: bool = False,
    tm_service_ids: List[int] = None,
):
    """
    并行模式：所有任务同时提交，Semaphore 控制最大并发数
    """
    _init_batch_state(batch_id, task_uuids)
    add_batch_log, update_batch_status = _make_batch_helpers(batch_id)
    semaphore = asyncio.Semaphore(concurrency)
    counter_lock = asyncio.Lock()
    add_batch_log(f"[系统] 并行模式启动 (并发: {concurrency}, 总任务: {len(task_uuids)})")

    async def _worker():
        while task_uuids:
            try:
                # 平滑启动抖动：避免瞬间多线程爆发拉满 CPU
                await asyncio.sleep(random.uniform(0.015, 0.08))
                uuid = task_uuids.pop(0)
                idx = total_orig - len(task_uuids) - 1
                prefix = f"任务 {idx + 1}:"
                
                # 执行任务
                await run_registration_task(
                    uuid, email_service_type, proxy, email_service_config, email_service_id,
                    log_prefix=prefix, batch_id=batch_id,
                    auto_upload_cpa=auto_upload_cpa, cpa_service_ids=cpa_service_ids or [],
                    auto_upload_sub2api=auto_upload_sub2api, sub2api_service_ids=sub2api_service_ids or [],
                    auto_upload_tm=auto_upload_tm, tm_service_ids=tm_service_ids or [],
                )
                
                # 核心修复：执行完后查询数据库并显式更新批次统计状态
                with get_db() as db:
                    t = crud.get_registration_task(db, uuid)
                    if t:
                        async with counter_lock:
                            new_completed = batch_tasks[batch_id]["completed"] + 1
                            new_success = batch_tasks[batch_id]["success"]
                            new_failed = batch_tasks[batch_id]["failed"]

                            if t.status == "completed":
                                new_success += 1
                                add_batch_log(f"{prefix} [成功] 注册已完成")
                            elif t.status == "failed":
                                new_failed += 1
                                add_batch_log(f"{prefix} [失败] 注册异常: {t.error_message}")

                            update_batch_status(
                                completed=new_completed,
                                success=new_success,
                                failed=new_failed
                            )
            except Exception as e:
                logger.error(f"Worker 执行任务异常: {e}")

    try:
        import time
        start_time = time.time()
        total_orig = len(task_uuids)
        original_task_uuids = task_uuids.copy()
        
        # 复制一份 UUID 列表以免修改原始数据
        task_uuids = task_uuids.copy()
        
        # 创建工作协程组
        worker_count = min(concurrency, total_orig)
        workers = [asyncio.create_task(_worker()) for _ in range(worker_count)]
        
        # 等待所有工作完成
        await asyncio.gather(*workers, return_exceptions=True)
        final_totals = _collect_batch_totals_from_db(original_task_uuids)
        update_batch_status(**final_totals)
        
        # 计算总耗时
        end_time = time.time()
        total_seconds = end_time - start_time
        
        if not task_manager.is_batch_cancelled(batch_id):
            success_count = batch_tasks[batch_id]['success']
            failed_count = batch_tasks[batch_id]['failed']
            
            # 计算平均每个账号的时间
            total_accounts = success_count + failed_count
            avg_time = total_seconds / total_accounts if total_accounts > 0 else 0
            
            minutes = int(total_seconds // 60)
            seconds = int(total_seconds % 60)
            time_str = f"{minutes}m {seconds}s" if minutes > 0 else f"{seconds}s"

            add_batch_log(f"[完成] 批量任务执行结束. 成功: {success_count}, 失败: {failed_count}")
            add_batch_log(f"[统计] 总耗时: {time_str}, 平均速率: {avg_time:.1f}s/账户")
            update_batch_status(
                completed=final_totals["completed"],
                success=final_totals["success"],
                failed=final_totals["failed"],
                finished=True,
                status="completed",
            )
        else:
            update_batch_status(finished=True, status="cancelled")
    except Exception as e:
        logger.error(f"批量任务 {batch_id} 异常: {e}")
        add_batch_log(f"[错误] 批量任务异常: {str(e)}")
        update_batch_status(finished=True, status="failed")
    finally:
        batch_tasks[batch_id]["finished"] = True


async def run_batch_pipeline(
    batch_id: str,
    task_uuids: List[str],
    email_service_type: str,
    proxy: Optional[str],
    email_service_config: Optional[dict],
    email_service_id: Optional[int],
    interval_min: int,
    interval_max: int,
    concurrency: int,
    auto_upload_cpa: bool = False,
    cpa_service_ids: List[int] = None,
    auto_upload_sub2api: bool = False,
    sub2api_service_ids: List[int] = None,
    auto_upload_tm: bool = False,
    tm_service_ids: List[int] = None,
):
    """
    流水线模式：每隔 interval 秒启动一个新任务，Semaphore 限制最大并发数
    """
    _init_batch_state(batch_id, task_uuids)
    add_batch_log, update_batch_status = _make_batch_helpers(batch_id)
    semaphore = asyncio.Semaphore(concurrency)
    counter_lock = asyncio.Lock()
    running_tasks_list = []
    add_batch_log(f"[系统] 流水线模式启动 (间歇: {interval_min}-{interval_max}s, 并发: {concurrency}, 总数: {len(task_uuids)})")

    async def _run_and_release(idx: int, uuid: str, pfx: str):
        try:
            await run_registration_task(
                uuid, email_service_type, proxy, email_service_config, email_service_id,
                log_prefix=pfx, batch_id=batch_id,
                auto_upload_cpa=auto_upload_cpa, cpa_service_ids=cpa_service_ids or [],
                auto_upload_sub2api=auto_upload_sub2api, sub2api_service_ids=sub2api_service_ids or [],
                auto_upload_tm=auto_upload_tm, tm_service_ids=tm_service_ids or [],
            )
            with get_db() as db:
                t = crud.get_registration_task(db, uuid)
                if t:
                    async with counter_lock:
                        new_completed = batch_tasks[batch_id]["completed"] + 1
                        new_success = batch_tasks[batch_id]["success"]
                        new_failed = batch_tasks[batch_id]["failed"]
                        if t.status == "completed":
                            new_success += 1
                            add_batch_log(f"{pfx} [成功] 注册成功")
                        elif t.status == "failed":
                            new_failed += 1
                            add_batch_log(f"{pfx} [失败] 注册失败: {t.error_message}")
                        update_batch_status(completed=new_completed, success=new_success, failed=new_failed)
        finally:
            semaphore.release()

    try:
        import time
        start_time = time.time()  # 记录开始时间
        
        for i, task_uuid in enumerate(task_uuids):
            if task_manager.is_batch_cancelled(batch_id) or batch_tasks[batch_id]["cancelled"]:
                with get_db() as db:
                    for remaining_uuid in task_uuids[i:]:
                        crud.update_registration_task(db, remaining_uuid, status="cancelled")
                add_batch_log("[取消] 批量任务已取消")
                update_batch_status(finished=True, status="cancelled")
                break

            update_batch_status(current_index=i)
            await semaphore.acquire()
            prefix = f"[任务{i + 1}]"
            add_batch_log(f"{prefix} 开始注册...")
            t = asyncio.create_task(_run_and_release(i, task_uuid, prefix))
            running_tasks_list.append(t)

            if i < len(task_uuids) - 1 and not task_manager.is_batch_cancelled(batch_id):
                wait_time = random.randint(interval_min, interval_max)
                logger.info(f"批量任务 {batch_id}: 等待 {wait_time} 秒后启动下一个任务")
                await asyncio.sleep(wait_time)

        if running_tasks_list:
            await asyncio.gather(*running_tasks_list, return_exceptions=True)
        final_totals = _collect_batch_totals_from_db(task_uuids)
        update_batch_status(**final_totals)

        # 计算总耗时
        end_time = time.time()
        total_seconds = end_time - start_time

        if not task_manager.is_batch_cancelled(batch_id):
            success_count = batch_tasks[batch_id]['success']
            failed_count = batch_tasks[batch_id]['failed']
            
            # 计算平均每个账号的时间
            total_accounts = success_count + failed_count
            avg_time = total_seconds / total_accounts if total_accounts > 0 else 0
            
            # 格式化时间显示
            minutes = int(total_seconds // 60)
            seconds = int(total_seconds % 60)
            time_str = f"{minutes}分{seconds}秒" if minutes > 0 else f"{seconds}秒"
            
            service_name = email_service_type.capitalize()
            if failed_count > 0:
                add_batch_log(f"[完成] {service_name} 批量任务完成！成功: {success_count}, 未成功: {failed_count}")
            else:
                add_batch_log(f"[完成] {service_name} 批量任务完成！全部成功: {success_count} 个")
            
            add_batch_log(f"[统计] 总耗时: {time_str}, 平均速率: {avg_time:.1f}s/账号")
            update_batch_status(
                completed=final_totals["completed"],
                success=final_totals["success"],
                failed=final_totals["failed"],
                finished=True,
                status="completed",
            )
    except Exception as e:
        logger.error(f"批量任务 {batch_id} 异常: {e}")
        add_batch_log(f"[错误] 批量任务异常: {str(e)}")
        update_batch_status(finished=True, status="failed")
    finally:
        batch_tasks[batch_id]["finished"] = True


async def run_batch_registration(
    batch_id: str,
    task_uuids: List[str],
    email_service_type: str,
    proxy: Optional[str],
    email_service_config: Optional[dict],
    email_service_id: Optional[int],
    interval_min: int,
    interval_max: int,
    concurrency: int = 1,
    mode: str = "pipeline",
    auto_upload_cpa: bool = False,
    cpa_service_ids: List[int] = None,
    auto_upload_sub2api: bool = False,
    sub2api_service_ids: List[int] = None,
    auto_upload_tm: bool = False,
    tm_service_ids: List[int] = None,
):
    """根据 mode 分发到并行或流水线执行"""
    if mode == "parallel":
        await run_batch_parallel(
            batch_id, task_uuids, email_service_type, proxy,
            email_service_config, email_service_id, concurrency,
            auto_upload_cpa=auto_upload_cpa, cpa_service_ids=cpa_service_ids,
            auto_upload_sub2api=auto_upload_sub2api, sub2api_service_ids=sub2api_service_ids,
            auto_upload_tm=auto_upload_tm, tm_service_ids=tm_service_ids,
        )
    else:
        await run_batch_pipeline(
            batch_id, task_uuids, email_service_type, proxy,
            email_service_config, email_service_id,
            interval_min, interval_max, concurrency,
            auto_upload_cpa=auto_upload_cpa, cpa_service_ids=cpa_service_ids,
            auto_upload_sub2api=auto_upload_sub2api, sub2api_service_ids=sub2api_service_ids,
            auto_upload_tm=auto_upload_tm, tm_service_ids=tm_service_ids,
        )


async def prepare_and_run_batch_registration(
    batch_id: str,
    count: int,
    email_service_type: str,
    proxy: Optional[str],
    email_service_config: Optional[dict],
    email_service_id: Optional[int],
    interval_min: int,
    interval_max: int,
    concurrency: int = 1,
    mode: str = "pipeline",
    auto_upload_cpa: bool = False,
    cpa_service_ids: List[int] = None,
    auto_upload_sub2api: bool = False,
    sub2api_service_ids: List[int] = None,
    auto_upload_tm: bool = False,
    tm_service_ids: List[int] = None,
):
    """后台初始化批量子任务后再启动执行，避免阻塞启动响应。"""
    task_manager.init_batch(batch_id, count)
    batch_tasks[batch_id] = {
        "total": count,
        "completed": 0,
        "success": 0,
        "failed": 0,
        "cancelled": False,
        "task_uuids": [],
        "current_index": 0,
        "logs": [],
        "finished": False,
        "start_time": time.time(),
    }
    task_manager.add_batch_log(batch_id, f"[系统] 批量任务已创建，正在初始化 {count} 个子任务...")

    task_uuids: List[str] = []
    try:
        with get_db() as db:
            for index in range(count):
                if task_manager.is_batch_cancelled(batch_id) or batch_tasks[batch_id]["cancelled"]:
                    task_manager.add_batch_log(batch_id, "[取消] 初始化阶段收到取消请求，已停止创建后续任务")
                    break
                task_uuid = str(uuid.uuid4())
                crud.create_registration_task(
                    db,
                    task_uuid=task_uuid,
                    proxy=proxy
                )
                task_uuids.append(task_uuid)
                if index == 0 or (index + 1) % 20 == 0 or index + 1 == count:
                    task_manager.add_batch_log(batch_id, f"[系统] 子任务初始化进度: {index + 1}/{count}")

        _init_batch_state(batch_id, task_uuids, total=count)

        if not task_uuids:
            batch_tasks[batch_id]["finished"] = True
            task_manager.update_batch_status(batch_id, finished=True, status="cancelled")
            return

        task_manager.add_batch_log(batch_id, f"[系统] 子任务初始化完成，开始执行 (模式: {mode}, 并发: {concurrency})")
        await run_batch_registration(
            batch_id,
            task_uuids,
            email_service_type,
            proxy,
            email_service_config,
            email_service_id,
            interval_min,
            interval_max,
            concurrency,
            mode,
            auto_upload_cpa,
            cpa_service_ids,
            auto_upload_sub2api,
            sub2api_service_ids,
            auto_upload_tm,
            tm_service_ids,
        )
    except Exception as e:
        logger.error(f"批量任务 {batch_id} 初始化异常: {e}")
        task_manager.add_batch_log(batch_id, f"[错误] 批量任务初始化异常: {str(e)}")
        if batch_id in batch_tasks:
            batch_tasks[batch_id]["finished"] = True
        task_manager.update_batch_status(batch_id, finished=True, status="failed")


# ============== API Endpoints ==============

@router.post("/start", response_model=RegistrationTaskResponse)
async def start_registration(
    request: RegistrationTaskCreate,
    background_tasks: BackgroundTasks
):
    """
    启动注册任务

    - email_service_type: 邮箱服务类型 (tempmail, cloud_mail)
    - proxy: 代理地址
    - email_service_config: 邮箱服务配置
    """
    # 验证邮箱服务类型
    try:
        EmailServiceType(request.email_service_type)
    except ValueError:
        raise HTTPException(
            status_code=400,
            detail=f"无效的邮箱服务类型: {request.email_service_type}"
        )

    # 创建任务
    task_uuid = str(uuid.uuid4())

    with get_db() as db:
        task = crud.create_registration_task(
            db,
            task_uuid=task_uuid,
            proxy=request.proxy
        )

    # 在后台运行注册任务
    background_tasks.add_task(
        run_registration_task,
        task_uuid,
        request.email_service_type,
        request.proxy,
        request.email_service_config,
        request.email_service_id,
        "",
        "",
        request.auto_upload_cpa,
        request.cpa_service_ids,
        request.auto_upload_sub2api,
        request.sub2api_service_ids,
        request.auto_upload_tm,
        request.tm_service_ids,
    )

    return task_to_response(task)


@router.post("/batch", response_model=BatchRegistrationResponse)
async def start_batch_registration(
    request: BatchRegistrationRequest,
    background_tasks: BackgroundTasks
):
    """
    启动批量注册任务

    - count: 注册数量 (≥1)
    - email_service_type: 邮箱服务类型
    - proxy: 代理地址
    - interval_min: 最小间隔秒数
    - interval_max: 最大间隔秒数
    """
    # 验证参数
    if request.count < 1:
        raise HTTPException(status_code=400, detail="注册数量必须大于 0")

    try:
        EmailServiceType(request.email_service_type)
    except ValueError:
        raise HTTPException(
            status_code=400,
            detail=f"无效的邮箱服务类型: {request.email_service_type}"
        )

    if request.interval_min < 0 or request.interval_max < request.interval_min:
        raise HTTPException(status_code=400, detail="间隔时间参数无效")

    if not 1 <= request.concurrency <= 50:
        raise HTTPException(status_code=400, detail="并发数必须在 1-50 之间")

    if request.mode not in ("parallel", "pipeline"):
        raise HTTPException(status_code=400, detail="模式必须为 parallel 或 pipeline")

    batch_id = str(uuid.uuid4())
    # 预先初始化批量监控状态，确保前端可立即连上监控
    task_manager.init_batch(batch_id, request.count)
    batch_tasks[batch_id] = {
        "total": request.count,
        "completed": 0,
        "success": 0,
        "failed": 0,
        "cancelled": False,
        "task_uuids": [],
        "current_index": 0,
        "logs": [],
        "finished": False,
        "start_time": time.time(),
    }

    background_tasks.add_task(
        prepare_and_run_batch_registration,
        batch_id,
        request.count,
        request.email_service_type,
        request.proxy,
        request.email_service_config,
        request.email_service_id,
        request.interval_min,
        request.interval_max,
        request.concurrency,
        request.mode,
        request.auto_upload_cpa,
        request.cpa_service_ids,
        request.auto_upload_sub2api,
        request.sub2api_service_ids,
        request.auto_upload_tm,
        request.tm_service_ids,
    )

    return BatchRegistrationResponse(
        batch_id=batch_id,
        count=request.count,
        tasks=[]
    )


@router.get("/batch/{batch_id}")
async def get_batch_status(batch_id: str):
    """获取批量任务状态"""
    if batch_id not in batch_tasks:
        raise HTTPException(status_code=404, detail="批量任务不存在")

    batch = batch_tasks[batch_id]
    return {
        "batch_id": batch_id,
        "total": batch["total"],
        "completed": batch["completed"],
        "success": batch["success"],
        "failed": batch["failed"],
        "current_index": batch["current_index"],
        "cancelled": batch["cancelled"],
        "finished": batch.get("finished", False),
        "progress": f"{batch['completed']}/{batch['total']}",
        "logs": task_manager.get_batch_logs(batch_id),
    }


@router.get("/batch/{batch_id}/logs")
async def get_batch_logs(batch_id: str, offset: int = Query(0, ge=0)):
    """增量拉取批量任务日志（WebSocket 断开后的降级轮询接口）

    - offset: 已收到的日志条数，只返回 offset 之后的新日志
    """
    if batch_id not in batch_tasks:
        raise HTTPException(status_code=404, detail="批量任务不存在")

    batch = batch_tasks[batch_id]
    all_logs = task_manager.get_batch_logs(batch_id)
    new_logs = all_logs[offset:]

    return {
        "batch_id": batch_id,
        "total": batch["total"],
        "completed": batch["completed"],
        "success": batch["success"],
        "failed": batch["failed"],
        "finished": batch.get("finished", False),
        "cancelled": batch["cancelled"],
        "logs": new_logs,
        "log_offset": len(all_logs),  # 下次请求时使用此 offset
    }


@router.post("/batch/{batch_id}/cancel")
async def cancel_batch(batch_id: str):
    """取消批量任务"""
    if batch_id not in batch_tasks:
        raise HTTPException(status_code=404, detail="批量任务不存在")

    batch = batch_tasks[batch_id]
    if batch.get("finished"):
        raise HTTPException(status_code=400, detail="批量任务已完成")

    batch["cancelled"] = True
    task_manager.cancel_batch(batch_id)
    for task_uuid in batch.get("task_uuids", []):
        task_manager.cancel_task(task_uuid)
    return {"success": True, "message": "批量任务取消请求已提交，正在让它们有序收工"}


@router.get("/tasks", response_model=TaskListResponse)
async def list_tasks(
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    status: Optional[str] = Query(None),
):
    """获取任务列表"""
    with get_db() as db:
        query = db.query(RegistrationTask)

        if status:
            query = query.filter(RegistrationTask.status == status)

        total = query.count()
        offset = (page - 1) * page_size
        tasks = query.order_by(RegistrationTask.created_at.desc()).offset(offset).limit(page_size).all()

        return TaskListResponse(
            total=total,
            tasks=[task_to_response(t) for t in tasks]
        )


@router.get("/tasks/{task_uuid}", response_model=RegistrationTaskResponse)
async def get_task(task_uuid: str):
    """获取任务详情"""
    with get_db() as db:
        task = crud.get_registration_task(db, task_uuid)
        if not task:
            raise HTTPException(status_code=404, detail="任务不存在")
        return task_to_response(task)


@router.get("/tasks/{task_uuid}/logs")
async def get_task_logs(task_uuid: str):
    """获取任务日志"""
    with get_db() as db:
        task = crud.get_registration_task(db, task_uuid)
        if not task:
            raise HTTPException(status_code=404, detail="任务不存在")

        live_logs = task_manager.get_logs(task_uuid)
        db_logs = (task.logs or "").split("\n") if task.logs else []
        status_meta = task_manager.get_status(task_uuid) or {}
        result = task.result or {}
        email_service = None
        if task.email_service:
            email_service = task.email_service.service_type
        elif isinstance(result.get("metadata"), dict):
            email_service = result["metadata"].get("email_service")

        return {
            "task_uuid": task_uuid,
            "status": status_meta.get("status") or task.status,
            "email": status_meta.get("email") or result.get("email"),
            "email_service": email_service,
            "logs": live_logs or db_logs,
        }


@router.post("/tasks/{task_uuid}/cancel")
async def cancel_task(task_uuid: str):
    """取消任务"""
    with get_db() as db:
        task = crud.get_registration_task(db, task_uuid)
        if not task:
            raise HTTPException(status_code=404, detail="任务不存在")

        if task.status not in ["pending", "running"]:
            raise HTTPException(status_code=400, detail="任务已完成或已取消")

        task_manager.cancel_task(task_uuid)
        task_manager.update_status(task_uuid, "cancelling")
        crud.update_registration_task(db, task_uuid, status="cancelled", error_message="任务已取消")

        return {"success": True, "message": "任务已取消"}


@router.delete("/tasks/{task_uuid}")
async def delete_task(task_uuid: str):
    """删除任务"""
    with get_db() as db:
        task = crud.get_registration_task(db, task_uuid)
        if not task:
            raise HTTPException(status_code=404, detail="任务不存在")

        if task.status == "running":
            raise HTTPException(status_code=400, detail="无法删除运行中的任务")

        crud.delete_registration_task(db, task_uuid)

        return {"success": True, "message": "任务已删除"}


@router.get("/stats")
async def get_registration_stats():
    """获取注册统计信息"""
    with get_db() as db:
        from sqlalchemy import func

        # 按状态统计
        status_stats = db.query(
            RegistrationTask.status,
            func.count(RegistrationTask.id)
        ).group_by(RegistrationTask.status).all()

        # 今日注册数
        today = datetime.utcnow().date()
        today_count = db.query(func.count(RegistrationTask.id)).filter(
            func.date(RegistrationTask.created_at) == today
        ).scalar()

        return {
            "by_status": {status: count for status, count in status_stats},
            "today_count": today_count
        }


@router.get("/available-services")
async def get_available_email_services():
    """
    获取可用于注册的邮箱服务列表

    返回所有已启用的邮箱服务，包括：
    - tempmail: 临时邮箱（无需配置）
    - cloud_mail: 已配置的 CloudMail 服务
    """
    from ...database.models import EmailService as EmailServiceModel
    from ...config.settings import get_settings

    settings = get_settings()
    result = {
        "tempmail": {
            "available": True,
            "count": 1,
            "services": [{
                "id": None,
                "name": "Tempmail.lol",
                "type": "tempmail",
                "description": "临时邮箱，自动创建"
            }]
        },
        "cloud_mail": {
            "available": False,
            "count": 0,
            "services": []
        },
        "freemail": {
            "available": False,
            "count": 0,
            "services": []
        },
        "cloudflare_temp_email": {
            "available": False,
            "count": 0,
            "services": []
        },
        "imap_mail": {
            "available": False,
            "count": 0,
            "services": []
        }
    }

    with get_db() as db:
        # 获取 Cloud Mail 服务
        cloud_mail_services = db.query(EmailServiceModel).filter(
            EmailServiceModel.service_type == "cloud_mail",
            EmailServiceModel.enabled == True
        ).order_by(EmailServiceModel.priority.asc()).all()

        for service in cloud_mail_services:
            config = service.config or {}
            domain = config.get("domain")
            # 如果是列表，显示第一个域名
            if isinstance(domain, list) and domain:
                domain_display = domain[0]
            else:
                domain_display = domain
            
            result["cloud_mail"]["services"].append({
                "id": service.id,
                "name": service.name,
                "type": "cloud_mail",
                "domain": domain_display,
                "priority": service.priority
            })

        result["cloud_mail"]["count"] = len(cloud_mail_services)
        result["cloud_mail"]["available"] = len(cloud_mail_services) > 0

        freemail_services = db.query(EmailServiceModel).filter(
            EmailServiceModel.service_type == "freemail",
            EmailServiceModel.enabled == True
        ).order_by(EmailServiceModel.priority.asc()).all()

        for service in freemail_services:
            config = service.config or {}
            result["freemail"]["services"].append({
                "id": service.id,
                "name": service.name,
                "type": "freemail",
                "domain": config.get("domain"),
                "priority": service.priority
            })

        result["freemail"]["count"] = len(freemail_services)
        result["freemail"]["available"] = len(freemail_services) > 0

        cloudflare_temp_email_services = db.query(EmailServiceModel).filter(
            EmailServiceModel.service_type == "cloudflare_temp_email",
            EmailServiceModel.enabled == True
        ).order_by(EmailServiceModel.priority.asc()).all()

        for service in cloudflare_temp_email_services:
            config = service.config or {}
            result["cloudflare_temp_email"]["services"].append({
                "id": service.id,
                "name": service.name,
                "type": "cloudflare_temp_email",
                "domain": config.get("domain"),
                "priority": service.priority
            })

        result["cloudflare_temp_email"]["count"] = len(cloudflare_temp_email_services)
        result["cloudflare_temp_email"]["available"] = len(cloudflare_temp_email_services) > 0

        imap_mail_services = db.query(EmailServiceModel).filter(
            EmailServiceModel.service_type == "imap_mail",
            EmailServiceModel.enabled == True
        ).order_by(EmailServiceModel.priority.asc()).all()

        for service in imap_mail_services:
            config = service.config or {}
            result["imap_mail"]["services"].append({
                "id": service.id,
                "name": service.name,
                "type": "imap_mail",
                "domain": config.get("domain"),
                "host": config.get("host"),
                "priority": service.priority
            })

        result["imap_mail"]["count"] = len(imap_mail_services)
        result["imap_mail"]["available"] = len(imap_mail_services) > 0

    return result

    return {"success": True, "message": "批量任务取消请求已提交，正在让它们有序收工"}
