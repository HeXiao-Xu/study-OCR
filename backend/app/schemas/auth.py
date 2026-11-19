"""认证接口的出入参模型（字段与约束见 API.md §1）。

口令只出现在请求体里：任何出参、日志、异常结构都不得包含 `password` 或 `password_hash`。
"""
from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field, field_validator

from app.utils.text import normalize_username, username_error


class RegisterRequest(BaseModel):
    username: str = Field(min_length=1, max_length=64, description="登录名，服务端 trim + 转小写")
    password: str = Field(min_length=8, max_length=128, description="8–128 字符，只存 Argon2id 哈希")
    real_name: str = Field(min_length=1, max_length=50)
    dept: str | None = Field(default=None, max_length=50)

    @field_validator("username")
    @classmethod
    def _check_username(cls, value: str) -> str:
        error = username_error(value)
        if error:
            raise ValueError(error)
        return normalize_username(value)

    @field_validator("real_name")
    @classmethod
    def _check_real_name(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("真实姓名不能为空")
        return value

    @field_validator("dept")
    @classmethod
    def _check_dept(cls, value: str | None) -> str | None:
        if value is None:
            return None
        value = value.strip()
        return value or None


class LoginRequest(BaseModel):
    username: str = Field(min_length=1, max_length=64)
    password: str = Field(min_length=8, max_length=128)

    @field_validator("username")
    @classmethod
    def _check_username(cls, value: str) -> str:
        error = username_error(value)
        if error:
            raise ValueError(error)
        return normalize_username(value)


class RefreshRequest(BaseModel):
    refresh_token: str = Field(min_length=1)


class LogoutRequest(BaseModel):
    # 可选：前端只带 access token 也能登出，但那样不会吊销 refresh（见 auth_service.revoke_refresh）
    refresh_token: str | None = None


class UserOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    username: str
    real_name: str | None
    dept: str | None
    role: str
    status: str
    created_at: datetime


class AuthTokens(BaseModel):
    access_token: str
    token_type: str = "bearer"
    expires_in: int
    refresh_token: str
    refresh_expires_in: int


class LoginData(AuthTokens):
    user: UserOut


class LogoutData(BaseModel):
    logged_out: bool