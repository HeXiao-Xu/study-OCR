"""认证路由（API.md §1）。

路由只做"参数校验 → service → 统一壳"，业务与事务都在 `services/auth_service.py`。
响应体一律不含 `password_hash`；`ok()` 负责把 trace_id 填进壳里，与响应头一致。
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, status
from sqlalchemy.orm import Session

from app.api.deps import get_current_user, get_token_store
from app.core.config import Settings, get_settings
from app.core.database import get_db
from app.core.exceptions import ok
from app.models.enums import UserRole
from app.models.users import User
from app.schemas.auth import (
    AuthTokens,
    LoginData,
    LoginRequest,
    LogoutData,
    LogoutRequest,
    RefreshRequest,
    RegisterRequest,
    UserOut,
)
from app.schemas.common import Envelope
from app.services import auth_service
from app.services.token_store import RefreshTokenStore

router = APIRouter(prefix="/auth", tags=["auth"])


@router.post(
    "/register",
    status_code=status.HTTP_201_CREATED,
    response_model=Envelope[UserOut],
    summary="注册（固定 submitter，reviewer/admin 只能由初始化脚本创建）",
)
def register(
    payload: RegisterRequest,
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_settings),
) -> dict:
    user = auth_service.create_user(
        db,
        username=payload.username,
        password=payload.password,
        real_name=payload.real_name,
        dept=payload.dept,
        role=UserRole.SUBMITTER.value,
    )
    return ok(UserOut.model_validate(user))


@router.post("/login", response_model=Envelope[LoginData], summary="登录（access 30min + refresh 7d）")
def login(
    payload: LoginRequest,
    db: Session = Depends(get_db),
    store: RefreshTokenStore = Depends(get_token_store),
    settings: Settings = Depends(get_settings),
) -> dict:
    user = auth_service.authenticate(db, payload.username, payload.password, settings)
    tokens = auth_service.issue_session(store, user, settings)
    return ok(LoginData(**tokens.model_dump(), user=UserOut.model_validate(user)))


@router.get("/me", response_model=Envelope[UserOut], summary="当前用户（角色以数据库为准）")
def me(user: User = Depends(get_current_user)) -> dict:
    return ok(UserOut.model_validate(user))


@router.post("/refresh", response_model=Envelope[AuthTokens], summary="轮换式刷新（旧 jti 立即作废）")
def refresh(
    payload: RefreshRequest,
    db: Session = Depends(get_db),
    store: RefreshTokenStore = Depends(get_token_store),
    settings: Settings = Depends(get_settings),
) -> dict:
    tokens = auth_service.rotate_refresh(db, store, payload.refresh_token, settings)
    return ok(tokens)


@router.post("/logout", response_model=Envelope[LogoutData], summary="登出（删除 refresh 白名单，幂等）")
def logout(
    payload: LogoutRequest,
    user: User = Depends(get_current_user),
    store: RefreshTokenStore = Depends(get_token_store),
    settings: Settings = Depends(get_settings),
) -> dict:
    auth_service.revoke_refresh(store, payload.refresh_token, user, settings)
    return ok(LogoutData(logged_out=True))