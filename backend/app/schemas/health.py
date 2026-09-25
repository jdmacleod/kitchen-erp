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

    # Build identity, populated on the authenticated branch only: naming the exact
    # commit tells an anonymous caller which vulnerabilities apply. Optional so an
    # older API, or the anonymous response, degrades to the shape above rather than
    # sending nulls. `is_dev` is decided here rather than by comparing against the
    # sentinels in the frontend, which would spread one concept across three
    # languages.
    version: str | None = None
    commit: str | None = None
    is_dev: bool | None = None
    # The navigation sections whose migrations are applied (docs/spec/09). Also
    # authenticated-only, like the build identity.
    features: list[str] | None = None
