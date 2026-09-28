from __future__ import annotations

import uuid
from datetime import datetime
from typing import Literal

from pydantic import EmailStr, Field

from app.schemas.base import ApiModel

Role = Literal["admin", "member"]


class UserOut(ApiModel):
    id: uuid.UUID
    email: str
    display_name: str
    role: Role
    active: bool
    created_at: datetime


class UserCreate(ApiModel):
    email: EmailStr
    display_name: str = Field(min_length=1, max_length=200)
    password: str = Field(min_length=8, max_length=1024)
    role: Role = "member"


class UserList(ApiModel):
    items: list[UserOut]


class UserEdit(ApiModel):
    """An admin's change to a member. Every field is optional; only those sent change."""

    email: EmailStr | None = None
    display_name: str | None = Field(default=None, min_length=1, max_length=200)
    role: Role | None = None
    active: bool | None = None


class PasswordSet(ApiModel):
    """An admin setting a new password for another member."""

    password: str = Field(min_length=8, max_length=1024)


class PasswordChange(ApiModel):
    """Changing your own password: the current one proves it is you."""

    current_password: str = Field(min_length=1, max_length=1024)
    new_password: str = Field(min_length=8, max_length=1024)


class LoginIn(ApiModel):
    email: str
    password: str


class LoginOut(ApiModel):
    user: UserOut


class ApiTokenOut(ApiModel):
    id: uuid.UUID
    name: str
    created_at: datetime
    last_used_at: datetime | None
    revoked_at: datetime | None


class ApiTokenCreate(ApiModel):
    name: str = Field(min_length=1, max_length=200)


class ApiTokenCreated(ApiModel):
    token: ApiTokenOut
    plaintext: str


class ApiTokenList(ApiModel):
    items: list[ApiTokenOut]
