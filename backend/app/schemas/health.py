from __future__ import annotations

from typing import Any, Literal

from app.schemas.base import ApiModel

OverallStatus = Literal["ok", "degraded", "failed"]
CheckStatus = Literal["ok", "degraded", "failed"]


class Check(ApiModel):
    status: CheckStatus
    detail: dict[str, Any] | None = None


class HealthOut(ApiModel):
    status: OverallStatus
    checks: dict[str, Check] | None = None
