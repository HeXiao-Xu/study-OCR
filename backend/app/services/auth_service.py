"""认证业务逻辑：注册、认证、签发、轮换、吊销。

事务边界（AGENTS §10.2）：一个用例一个事务。`create_user` 自己提交/回滚；
登录、刷新、登出不写业务表（只写 Redis 白名单），因此不需要事务。
"""
from __future__ import annotations

import hashlib
import logging

from redis.exceptions import RedisError
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.core.config import Settings, get_settings
from app.core.exceptions import (
    AccountDisabledError,
    AuthError,
    ConflictError,
    ServiceUnavailableError,
)
from app.core.security import (
    TokenType,
    decode_token,
    dummy_verify,
    encode_token,
    hash_password,
    verify_password,
)
from app.models.enums import UserRole, UserStatus
from app.models.users import User
from app.schemas.auth import AuthTokens
from app.services.token_store import RefreshTokenStore
from app.utils.text import normalize_username

logger = logging.getLogger("app.auth")


def _username_digest(username: str) -> str:
    """审计/日志里只留用户名哈希：可关联、不可反查（API.md §1 备注）。"""
    return hashlib.sha256(username.encode("utf-8")).hexdigest()


def _log_failure(event: str, username: str, **extra: object) -> None:
    logger.warning(
        "认证失败",
        extra={
            "event": event,
            "username_sha256": _username_digest(username),
            **extra,
        },
    )


def create_user(
    db: Session,
    *,
    username: str,
    password: str,
    real_name: str,
    role: str = UserRole.SUBMITTER.value,
    dept: str | None = None,
) -> User:
    """创建用户（注册接口与初始化脚本共用）。

    角色只允许三角色；注册接口固定传 submitter，reviewer/admin 只能由脚本创建。
    """
    if role not in {member.value for member in UserRole}:
        raise ValueError(f"非法角色：{role}")

    user = User(
        username=normalize_username(username),
        password_hash=hash_password(password),
        real_name=real_name.strip(),
        dept=dept,
        role=role,
        status=UserStatus.ACTIVE.value,
    )
    db.add(user)
    try:
        db.commit()
    except IntegrityError as exc:
        db.rollback()
        # 唯一索引 uq_users_username 兜底：并发注册也只可能成功一个
        raise ConflictError("用户名已存在") from exc
    db.refresh(user)
    return user


def authenticate(
    db: Session, username: str, password: str, settings: Settings | None = None
) -> User:
    """校验凭据。失败语义（API.md §1）：
    - 账号不存在 / 口令错误 / 无口令哈希 → 401 + 同一文案（不泄露账号是否存在）；
    - 账号 disabled / locked → 403（管理员需要知道账号被停用）。
    """
    user = db.scalar(select(User).where(User.username == normalize_username(username)))

    if user is None or not user.password_hash:
        # 也跑一次 Argon2，抹平"账号是否存在"的响应时间差
        dummy_verify(password, settings=settings)
        _log_failure("login_failed", username, reason="unknown_or_no_password")
        raise AuthError("用户名或密码错误")

    if not verify_password(password, user.password_hash, settings=settings):
        _log_failure("login_failed", username, reason="bad_password")
        raise AuthError("用户名或密码错误")

    if user.status != UserStatus.ACTIVE.value:
        logger.warning(
            "账号不可用",
            extra={
                "event": "login_rejected_disabled",
                "username_sha256": _username_digest(username),
                "user_id": user.id,
                "status": user.status,
            },
        )
        raise AccountDisabledError()

    return user


def issue_session(
    store: RefreshTokenStore, user: User, settings: Settings | None = None
) -> AuthTokens:
    """签发 access + refresh，并把 refresh 的 jti 写入白名单。"""
    s = settings or get_settings()
    access_token, _ = encode_token(
        user_id=user.id,
        role=user.role,
        token_type=TokenType.ACCESS,
        ttl_seconds=s.access_token_ttl_seconds,
        settings=s,
    )
    refresh_token, jti = encode_token(
        user_id=user.id,
        role=user.role,
        token_type=TokenType.REFRESH,
        ttl_seconds=s.refresh_token_ttl_seconds,
        settings=s,
    )
    try:
        store.save(jti, str(user.id), s.refresh_token_ttl_seconds)
    except RedisError as exc:
        # 白名单写不进就不能返回令牌：否则拿到的是"无法登出"的会话
        logger.error("refresh 白名单写入失败", extra={"event": "token_store_unavailable"})
        raise ServiceUnavailableError("会话存储不可用") from exc

    return AuthTokens(
        access_token=access_token,
        expires_in=s.access_token_ttl_seconds,
        refresh_token=refresh_token,
        refresh_expires_in=s.refresh_token_ttl_seconds,
    )


def rotate_refresh(
    db: Session,
    store: RefreshTokenStore,
    refresh_token: str,
    settings: Settings | None = None,
) -> AuthTokens:
    """轮换式刷新：校验白名单 → 删旧 jti → 校验用户 → 签新对。

    旧令牌重放时白名单已无该 jti，直接 401。
    """
    s = settings or get_settings()
    payload = decode_token(refresh_token, expected_type=TokenType.REFRESH, settings=s)

    try:
        stored_user_id = store.get(payload.jti)
    except RedisError as exc:
        logger.error("refresh 白名单读取失败", extra={"event": "token_store_unavailable"})
        raise ServiceUnavailableError("会话存储不可用") from exc

    if stored_user_id is None or stored_user_id != str(payload.sub):
        raise AuthError("令牌已失效")

    try:
        store.delete(payload.jti)
    except RedisError as exc:
        logger.error("refresh 白名单删除失败", extra={"event": "token_store_unavailable"})
        raise ServiceUnavailableError("会话存储不可用") from exc

    user = db.get(User, payload.sub)
    if user is None or user.status != UserStatus.ACTIVE.value:
        raise AuthError("令牌已失效")

    return issue_session(store, user, settings=s)


def revoke_refresh(
    store: RefreshTokenStore,
    refresh_token: str | None,
    current_user: User,
    settings: Settings | None = None,
) -> None:
    """登出：仅当 refresh 令牌有效且属于当前用户时删白名单；其余情况静默（幂等）。

    只吊销 refresh：access token 在剩余 30 分钟内仍可用，这是无状态令牌的固有代价（D-039）。
    """
    if not refresh_token:
        return

    try:
        payload = decode_token(
            refresh_token, expected_type=TokenType.REFRESH, settings=settings
        )
    except AuthError:
        # 伪造/过期令牌的登出仍然返回 200：登出必须是幂等的
        return

    if payload.sub != current_user.id:
        return

    try:
        store.delete(payload.jti)
    except RedisError as exc:
        logger.error("refresh 白名单删除失败", extra={"event": "token_store_unavailable"})
        raise ServiceUnavailableError("会话存储不可用") from exc