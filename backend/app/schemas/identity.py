from __future__ import annotations

import uuid
from datetime import datetime
from typing import Annotated, Literal

from pydantic import EmailStr, Field, StringConstraints, model_validator

from app.schemas.base import ApiModel

Role = Literal["admin", "member"]

# Trimmed before the length check, so "   " is rejected rather than stored empty.
DisplayName = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=200)]


class UserOut(ApiModel):
    id: uuid.UUID
    email: str
    display_name: str
    role: Role
    active: bool
    created_at: datetime


class UserCreate(ApiModel):
    email: EmailStr
    display_name: DisplayName
    password: str = Field(min_length=8, max_length=1024)
    role: Role = "member"


class UserList(ApiModel):
    items: list[UserOut]


class UserEdit(ApiModel):
    """An admin's change to a member. Every field is optional; only those sent change."""

    email: EmailStr | None = None
    display_name: DisplayName | None = None
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


TokenScope = Literal["*", "vendors:read", "vendors:suggest"]


class ApiTokenOut(ApiModel):
    id: uuid.UUID
    name: str
    # "*" is full access; see app.api.deps (1F).
    scopes: list[str]
    created_at: datetime
    last_used_at: datetime | None
    revoked_at: datetime | None


class ApiTokenCreate(ApiModel):
    name: str = Field(min_length=1, max_length=200)
    # Full access by default, as every token had before scopes existed.
    scopes: list[TokenScope] = Field(default_factory=lambda: ["*"], min_length=1, max_length=3)

    @model_validator(mode="after")
    def _full_alone(self) -> ApiTokenCreate:
        if "*" in self.scopes and len(self.scopes) > 1:
            raise ValueError("Full access (*) already includes every other scope.")
        self.scopes = sorted(set(self.scopes))
        return self


class ApiTokenCreated(ApiModel):
    token: ApiTokenOut
    plaintext: str


class ApiTokenList(ApiModel):
    items: list[ApiTokenOut]
