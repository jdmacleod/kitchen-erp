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
