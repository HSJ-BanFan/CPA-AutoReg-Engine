"""
邮箱服务配置 API 路由
"""

import logging
from typing import List, Optional, Dict, Any
from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel

from ...database import crud
from ...database.session import get_db
from ...database.models import EmailService as EmailServiceModel, RegistrationTask
from ...services import EmailServiceFactory, EmailServiceType
from ...services.cloudflare_temp_email import normalize_public_base_url
from ...services.imap_mail import normalize_imap_config

logger = logging.getLogger(__name__)
router = APIRouter()


# ============== Pydantic Models ==============

class EmailServiceCreate(BaseModel):
    """创建邮箱服务请求"""
    service_type: str
    name: str
    config: Dict[str, Any]
    enabled: bool = True
    priority: int = 0


class EmailServiceUpdate(BaseModel):
    """更新邮箱服务请求"""
    name: Optional[str] = None
    config: Optional[Dict[str, Any]] = None
    enabled: Optional[bool] = None
    priority: Optional[int] = None


class EmailServiceResponse(BaseModel):
    """邮箱服务响应"""
    id: int
    service_type: str
    name: str
    enabled: bool
    priority: int
    config: Optional[Dict[str, Any]] = None  # 过滤敏感信息后的配置
    last_used: Optional[str] = None
    created_at: Optional[str] = None
    updated_at: Optional[str] = None

    class Config:
        from_attributes = True


class EmailServiceListResponse(BaseModel):
    """邮箱服务列表响应"""
    total: int
    services: List[EmailServiceResponse]


class ServiceTestResult(BaseModel):
    """服务测试结果"""
    success: bool
    message: str
    details: Optional[Dict[str, Any]] = None


# ============== Helper Functions ==============

# 敏感字段列表，返回响应时需要过滤
SENSITIVE_FIELDS = {
    'password', 'api_key', 'refresh_token', 'access_token', 'admin_token',
    'admin_password', 'jwt', 'mailbox_jwt', 'token'
}

def filter_sensitive_config(config: Dict[str, Any]) -> Dict[str, Any]:
    """过滤敏感配置信息"""
    if not config:
        return {}

    filtered = {}
    for key, value in config.items():
        if key in SENSITIVE_FIELDS:
            # 敏感字段不返回，但标记是否存在
            filtered[f"has_{key}"] = bool(value)
        else:
            filtered[key] = value

    return filtered


def service_to_response(service: EmailServiceModel) -> EmailServiceResponse:
    """转换服务模型为响应"""
    return EmailServiceResponse(
        id=service.id,
        service_type=service.service_type,
        name=service.name,
        enabled=service.enabled,
        priority=service.priority,
        config=filter_sensitive_config(service.config),
        last_used=service.last_used.isoformat() if service.last_used else None,
        created_at=service.created_at.isoformat() if service.created_at else None,
        updated_at=service.updated_at.isoformat() if service.updated_at else None,
    )


def _delete_service_with_dependencies(db, service: EmailServiceModel) -> None:
    """删除邮箱服务及其关联的注册任务，避免外键约束报错。"""
    db.query(RegistrationTask).filter(
        RegistrationTask.email_service_id == service.id
    ).delete(synchronize_session=False)
    db.delete(service)


def validate_service_config(service_type: EmailServiceType, config: Dict[str, Any]) -> Dict[str, Any]:
    service_config = dict(config or {})
    if service_type == EmailServiceType.CLOUD_MAIL:
        required = ["base_url", "admin_email", "admin_password"]
    elif service_type == EmailServiceType.FREEMAIL:
        required = ["base_url", "admin_token"]
    elif service_type == EmailServiceType.CLOUDFLARE_TEMP_EMAIL:
        required = ["base_url", "domain"]
    elif service_type == EmailServiceType.IMAP_MAIL:
        required = ["domain", "host", "email", "password"]
    else:
        return service_config

    missing = [key for key in required if not service_config.get(key)]
    if missing:
        raise HTTPException(status_code=400, detail=f"缺少必需配置: {', '.join(missing)}")

    try:
        if "base_url" in required:
            service_config["base_url"] = normalize_public_base_url(service_config["base_url"])
        if service_type == EmailServiceType.IMAP_MAIL:
            service_config = normalize_imap_config(service_config)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))

    return service_config


# ============== API Endpoints ==============

@router.get("/stats")
async def get_email_services_stats():
    """获取邮箱服务统计信息"""
    with get_db() as db:
        from sqlalchemy import func

        # 按类型统计
        type_stats = db.query(
            EmailServiceModel.service_type,
            func.count(EmailServiceModel.id)
        ).group_by(EmailServiceModel.service_type).all()

        # 启用数量
        enabled_count = db.query(func.count(EmailServiceModel.id)).filter(
            EmailServiceModel.enabled == True
        ).scalar()

        stats = {
            'custom_count': 0,
            'temp_mail_count': 0,
            'duck_mail_count': 0,
            'freemail_count': 0,
            'cloudflare_temp_email_count': 0,
            'imap_mail_count': 0,
            'tempmail_available': True,  # 临时邮箱始终可用
            'enabled_count': enabled_count
        }

        for service_type, count in type_stats:
            if service_type == 'moe_mail':
                stats['custom_count'] = count
            elif service_type == 'temp_mail':
                stats['temp_mail_count'] = count
            elif service_type == 'duck_mail':
                stats['duck_mail_count'] = count
            elif service_type == 'freemail':
                stats['freemail_count'] = count
            elif service_type == 'cloudflare_temp_email':
                stats['cloudflare_temp_email_count'] = count
            elif service_type == 'imap_mail':
                stats['imap_mail_count'] = count

        return stats


@router.get("/types")
async def get_service_types():
    """获取支持的邮箱服务类型"""
    return {
        "types": [
            {
                "value": "tempmail",
                "label": "Tempmail.lol",
                "description": "临时邮箱服务，无需配置",
                "config_fields": [
                    {"name": "base_url", "label": "API 地址", "default": "https://api.tempmail.lol/v2", "required": False},
                    {"name": "timeout", "label": "超时时间", "default": 30, "required": False},
                ]
            },
            {
                "value": "moe_mail",
                "label": "MoeMail",
                "description": "自定义域名邮箱服务",
                "config_fields": [
                    {"name": "base_url", "label": "API 地址", "required": True},
                    {"name": "api_key", "label": "API Key", "required": True},
                    {"name": "default_domain", "label": "默认域名", "required": False},
                ]
            },
            {
                "value": "temp_mail",
                "label": "Temp-Mail（自部署）",
                "description": "自部署 Cloudflare Worker 临时邮箱，admin 模式管理",
                "config_fields": [
                    {"name": "base_url", "label": "Worker 地址", "required": True, "placeholder": "https://mail.example.com"},
                    {"name": "admin_password", "label": "Admin 密码", "required": True, "secret": True},
                    {"name": "domain", "label": "邮箱域名", "required": True, "placeholder": "example.com"},
                    {"name": "enable_prefix", "label": "启用前缀", "required": False, "default": True},
                ]
            },
            {
                "value": "duck_mail",
                "label": "DuckMail",
                "description": "DuckMail 接口邮箱服务，支持 API Key 私有域名访问",
                "config_fields": [
                    {"name": "base_url", "label": "API 地址", "required": True, "placeholder": "https://api.duckmail.sbs"},
                    {"name": "default_domain", "label": "默认域名", "required": True, "placeholder": "duckmail.sbs"},
                    {"name": "api_key", "label": "API Key", "required": False, "secret": True},
                    {"name": "password_length", "label": "随机密码长度", "required": False, "default": 12},
                ]
            },
            {
                "value": "freemail",
                "label": "Freemail",
                "description": "Freemail 自部署 Cloudflare Worker 临时邮箱服务",
                "config_fields": [
                    {"name": "base_url", "label": "API 地址", "required": True, "placeholder": "https://freemail.example.com"},
                    {"name": "admin_token", "label": "Admin Token", "required": True, "secret": True},
                    {"name": "domain", "label": "邮箱域名", "required": False, "placeholder": "example.com"},
                ]
            },
            {
                "value": "cloudflare_temp_email",
                "label": "Cloudflare Temp Email",
                "description": "Cloudflare Worker 临时邮箱服务，使用邮箱 JWT 轮询收件箱",
                "config_fields": [
                    {"name": "base_url", "label": "API 地址", "required": True, "placeholder": "https://mail.example.com"},
                    {"name": "domain", "label": "邮箱域名", "required": True, "placeholder": "example.com"},
                    {"name": "timeout", "label": "超时时间", "required": False, "default": 120},
                    {"name": "poll_interval", "label": "轮询间隔", "required": False, "default": 3},
                    {"name": "mail_limit", "label": "邮件数量", "required": False, "default": 10},
                ]
            },
            {
                "value": "imap_mail",
                "label": "IMAP 邮箱",
                "description": "标准 IMAP 协议邮箱（Gmail/QQ/163等），仅用于接收验证码，强制直连",
                "config_fields": [
                    {"name": "domain", "label": "收信域名", "required": True, "placeholder": "example.com"},
                    {"name": "host", "label": "IMAP 服务器", "required": True, "placeholder": "imap.gmail.com"},
                    {"name": "port", "label": "端口", "required": False, "default": 993},
                    {"name": "use_ssl", "label": "使用 SSL", "required": False, "default": True},
                    {"name": "email", "label": "邮箱地址", "required": True},
                    {"name": "password", "label": "密码/授权码", "required": True, "secret": True},
                    {"name": "timeout", "label": "超时时间", "required": False, "default": 120},
                    {"name": "poll_interval", "label": "轮询间隔", "required": False, "default": 5},
                ]
            }
        ]
    }


@router.get("", response_model=EmailServiceListResponse)
async def list_email_services(
    service_type: Optional[str] = Query(None, description="服务类型筛选"),
    enabled_only: bool = Query(False, description="只显示启用的服务"),
):
    """获取邮箱服务列表"""
    with get_db() as db:
        query = db.query(EmailServiceModel)

        if service_type:
            query = query.filter(EmailServiceModel.service_type == service_type)

        if enabled_only:
            query = query.filter(EmailServiceModel.enabled == True)

        services = query.order_by(EmailServiceModel.priority.asc(), EmailServiceModel.id.asc()).all()

        return EmailServiceListResponse(
            total=len(services),
            services=[service_to_response(s) for s in services]
        )


@router.get("/{service_id}", response_model=EmailServiceResponse)
async def get_email_service(service_id: int):
    """获取单个邮箱服务详情"""
    with get_db() as db:
        service = db.query(EmailServiceModel).filter(EmailServiceModel.id == service_id).first()
        if not service:
            raise HTTPException(status_code=404, detail="服务不存在")
        return service_to_response(service)


@router.get("/{service_id}/full")
async def get_email_service_full(service_id: int):
    """获取单个邮箱服务完整详情（包含敏感字段，用于编辑）"""
    with get_db() as db:
        service = db.query(EmailServiceModel).filter(EmailServiceModel.id == service_id).first()
        if not service:
            raise HTTPException(status_code=404, detail="服务不存在")

        return {
            "id": service.id,
            "service_type": service.service_type,
            "name": service.name,
            "enabled": service.enabled,
            "priority": service.priority,
            "config": service.config or {},  # 返回完整配置
            "last_used": service.last_used.isoformat() if service.last_used else None,
            "created_at": service.created_at.isoformat() if service.created_at else None,
            "updated_at": service.updated_at.isoformat() if service.updated_at else None,
        }


@router.post("", response_model=EmailServiceResponse)
async def create_email_service(request: EmailServiceCreate):
    """创建邮箱服务配置"""
    # 验证服务类型
    try:
        service_type = EmailServiceType(request.service_type)
    except ValueError:
        raise HTTPException(status_code=400, detail=f"无效的服务类型: {request.service_type}")

    validated_config = validate_service_config(service_type, request.config)

    with get_db() as db:
        # 检查名称是否重复
        existing = db.query(EmailServiceModel).filter(EmailServiceModel.name == request.name).first()
        if existing:
            raise HTTPException(status_code=400, detail="服务名称已存在")

        service = EmailServiceModel(
            service_type=request.service_type,
            name=request.name,
            config=validated_config,
            enabled=request.enabled,
            priority=request.priority
        )
        db.add(service)
        db.commit()
        db.refresh(service)

        return service_to_response(service)


@router.patch("/{service_id}", response_model=EmailServiceResponse)
async def update_email_service(service_id: int, request: EmailServiceUpdate):
    """更新邮箱服务配置"""
    with get_db() as db:
        service = db.query(EmailServiceModel).filter(EmailServiceModel.id == service_id).first()
        if not service:
            raise HTTPException(status_code=404, detail="服务不存在")

        update_data = {}
        if request.name is not None:
            update_data["name"] = request.name
        if request.config is not None:
            # 合并配置而不是替换
            current_config = service.config or {}
            merged_config = {**current_config, **request.config}
            # 移除空值
            merged_config = {k: v for k, v in merged_config.items() if v}
            service_type = EmailServiceType(service.service_type)
            merged_config = validate_service_config(service_type, merged_config)
            update_data["config"] = merged_config
        if request.enabled is not None:
            update_data["enabled"] = request.enabled
        if request.priority is not None:
            update_data["priority"] = request.priority

        for key, value in update_data.items():
            setattr(service, key, value)

        db.commit()
        db.refresh(service)

        return service_to_response(service)


@router.delete("/{service_id}")
async def delete_email_service(service_id: int):
    """删除邮箱服务配置"""
    with get_db() as db:
        service = db.query(EmailServiceModel).filter(EmailServiceModel.id == service_id).first()
        if not service:
            raise HTTPException(status_code=404, detail="服务不存在")
        service_name = service.name
        try:
            _delete_service_with_dependencies(db, service)
            db.commit()
            return {"success": True, "message": f"服务 {service_name} 已删除"}
        except Exception as e:
            db.rollback()
            logger.error(f"删除邮箱服务失败: {service_id}, 错误: {e}")
            raise HTTPException(status_code=500, detail=f"删除服务失败: {str(e)}")


@router.post("/{service_id}/test", response_model=ServiceTestResult)
async def test_email_service(service_id: int):
    """测试邮箱服务是否可用"""
    with get_db() as db:
        service = db.query(EmailServiceModel).filter(EmailServiceModel.id == service_id).first()
        if not service:
            raise HTTPException(status_code=404, detail="服务不存在")

        try:
            service_type = EmailServiceType(service.service_type)
            email_service = EmailServiceFactory.create(service_type, service.config, name=service.name)

            health = email_service.check_health()

            if health:
                return ServiceTestResult(
                    success=True,
                    message="服务连接正常",
                    details=email_service.get_service_info() if hasattr(email_service, 'get_service_info') else None
                )
            else:
                return ServiceTestResult(
                    success=False,
                    message="服务连接失败"
                )

        except Exception as e:
            logger.error(f"测试邮箱服务失败: {e}")
            return ServiceTestResult(
                success=False,
                message=f"测试失败: {str(e)}"
            )


@router.post("/{service_id}/enable")
async def enable_email_service(service_id: int):
    """启用邮箱服务"""
    with get_db() as db:
        service = db.query(EmailServiceModel).filter(EmailServiceModel.id == service_id).first()
        if not service:
            raise HTTPException(status_code=404, detail="服务不存在")

        service.enabled = True
        db.commit()

        return {"success": True, "message": f"服务 {service.name} 已启用"}


@router.post("/{service_id}/disable")
async def disable_email_service(service_id: int):
    """禁用邮箱服务"""
    with get_db() as db:
        service = db.query(EmailServiceModel).filter(EmailServiceModel.id == service_id).first()
        if not service:
            raise HTTPException(status_code=404, detail="服务不存在")

        service.enabled = False
        db.commit()

        return {"success": True, "message": f"服务 {service.name} 已禁用"}


@router.post("/reorder")
async def reorder_services(service_ids: List[int]):
    """重新排序邮箱服务优先级"""
    with get_db() as db:
        for index, service_id in enumerate(service_ids):
            service = db.query(EmailServiceModel).filter(EmailServiceModel.id == service_id).first()
            if service:
                service.priority = index

        db.commit()

        return {"success": True, "message": "优先级已更新"}


# ============== 临时邮箱测试 ==============

class TempmailTestRequest(BaseModel):
    """临时邮箱测试请求"""
    api_url: Optional[str] = None


@router.post("/test-tempmail")
async def test_tempmail_service(request: TempmailTestRequest):
    """测试临时邮箱服务是否可用"""
    try:
        from ...services import EmailServiceFactory, EmailServiceType
        from ...config.settings import get_settings

        settings = get_settings()
        base_url = request.api_url or settings.tempmail_base_url

        config = {"base_url": base_url}
        tempmail = EmailServiceFactory.create(EmailServiceType.TEMPMAIL, config)

        # 检查服务健康状态
        health = tempmail.check_health()

        if health:
            return {"success": True, "message": "临时邮箱连接正常"}
        else:
            return {"success": False, "message": "临时邮箱连接失败"}

    except Exception as e:
        logger.error(f"测试临时邮箱失败: {e}")
        return {"success": False, "message": f"测试失败: {str(e)}"}
