"""API 公共依赖：Bearer 解析、当前用户、角色闸门、refresh 白名单存储。

角色边界（API.md §0）：
- **角色级拒绝** → 403/40301（`RoleForbiddenError`）；
- **资源级越权**（S2-3 起）→ 内部 `PermissionDeniedError`，全局处理器转 404，不泄露资源存在性。
两者不能混用，否则"换 id 探测"与"角色不匹配"会得到同样的错误码。
"""
from __future__ import annotations

from functools import lru_cache

from arq.connections import RedisSettings
from fastapi import Depends
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy.orm import Session

from app.core.config import Settings, get_settings
from app.core.database import get_db
from app.core.exceptions import AuthError, NotFoundError, RoleForbiddenError
from app.core.security import TokenType, decode_token
from app.models.enums import UserStatus
from app.models.users import User
from app.services.token_store import RefreshTokenStore, build_token_store
from app.tasks.queue import ArqOcrTaskQueue, OcrTaskQueue

# auto_error=False：缺 Authorization 时由我们统一抛 AuthError（统一壳 + 40101），
# 而不是 FastAPI 默认的 403 "Not authenticated"。
bearer_scheme = HTTPBearer(auto_error=False, description="Bearer JWT（HS256）")


def get_token_store() -> RefreshTokenStore:
    return build_token_store()


@lru_cache(maxsize=1)
def get_task_queue() -> OcrTaskQueue:
    settings = get_settings()
    return ArqOcrTaskQueue(RedisSettings.from_dsn(settings.redis_url))


def get_current_user(
    credentials: HTTPAuthorizationCredentials | None = Depends(bearer_scheme),
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_settings),
) -> User:
    if credentials is None or not credentials.credentials:
        raise AuthError()

    payload = decode_token(
        credentials.credentials, expected_type=TokenType.ACCESS, settings=settings
    )
    user = db.get(User, payload.sub)
    if user is None:
        # token 合法但用户没了（理论上升级/迁移才会出现）：按契约给 404
        raise NotFoundError("用户不存在")
    if user.status != UserStatus.ACTIVE.value:
        # API.md §1：me 对已禁用用户返回 401；登录接口才区分 403
        raise AuthError("账号已停用或锁定")
    return user


def require_roles(*roles: str):
    """生成角色依赖：`Depends(require_roles("reviewer", "admin"))`。"""
    if not roles:
        raise ValueError("require_roles 至少需要一个角色")

    allowed = frozenset(roles)

    def _dependency(user: User = Depends(get_current_user)) -> User:
        # 只信数据库里的角色：token 里的 role 可能过期，甚至是被伪造进日志的
        if user.role not in allowed:
            raise RoleForbiddenError()
        return user

    return _dependency