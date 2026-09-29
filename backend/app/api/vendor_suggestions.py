"""Vendor suggestions: intake from an outside tool, and review by a person (spec 03 §1F)."""

from __future__ import annotations

import json
import uuid
from typing import Annotated, Any

from fastapi import APIRouter, Depends, Request
from pydantic import ValidationError

from app.api.deps import CurrentUser, DbSession, scoped_user, token_id
from app.core.errors import ApiError
from app.models import AppUser
from app.schemas.vendor_suggestions import (
    MAX_BODY_BYTES,
    AcceptAllOut,
    SuggestionBatchIn,
    SuggestionBatchOut,
    SuggestionDecisionIn,
    SuggestionDecisionOut,
    SuggestionIn,
    SuggestionList,
    SuggestionSummary,
)
from app.services import vendor_suggestions

router = APIRouter(prefix="/vendor-suggestions", tags=["vendor-suggestions"])


def _batch_schema() -> dict[str, Any]:
    """The batch's JSON schema, inline: the route reads its body itself (no floats)."""
    schema = SuggestionBatchIn.model_json_schema()
    schema.pop("$defs", None)
    schema["properties"]["items"]["items"] = SuggestionIn.model_json_schema()
    return schema


def _refuse_float(text: str) -> Any:
    raise ValueError(f"{text} is a floating-point number; send numbers as strings")


@router.post(
    "",
    response_model=SuggestionBatchOut,
    status_code=201,
    openapi_extra={
        "requestBody": {
            "required": True,
            "content": {"application/json": {"schema": _batch_schema()}},
        }
    },
)
async def post_suggestions(
    request: Request,
    _: Annotated[AppUser, Depends(scoped_user("vendors:suggest"))],
    db: DbSession,
) -> SuggestionBatchOut:
    """A batch of proposed facts from a tool with a ``vendors:suggest`` token.

    At most 200 items and 1 MB; nothing is stored if any item is invalid, and
    posting never changes a vendor or location.
    """
    raw = bytearray()
    async for chunk in request.stream():
        raw.extend(chunk)
        if len(raw) > MAX_BODY_BYTES:
            raise ApiError(413, "body_too_large", "At most 1 MB per request.")
    try:
        batch = SuggestionBatchIn.model_validate(
            json.loads(bytes(raw), parse_float=_refuse_float, parse_constant=_refuse_float)
        )
    except (ValueError, ValidationError) as exc:
        errors = (
            [
                {"at": ".".join(str(p) for p in e["loc"]), "problem": e["msg"]}
                for e in exc.errors()[:50]
            ]
            if isinstance(exc, ValidationError)
            else [{"at": "body", "problem": str(exc)}]
        )
        raise ApiError(
            422,
            "invalid_suggestions",
            "The batch is not valid; none was stored.",
            {"errors": errors},
        ) from None
    stored = await vendor_suggestions.intake(db, batch, token_id=token_id(request))
    return SuggestionBatchOut(
        batch_id=stored.batch_id, stored=stored.stored, collapsed=stored.collapsed
    )


@router.get("", response_model=SuggestionList)
async def list_suggestions(
    _: CurrentUser, db: DbSession, vendor_id: uuid.UUID | None = None
) -> SuggestionList:
    """Suggestions waiting for a person, grouped by vendor then location."""
    return SuggestionList(items=await vendor_suggestions.awaiting(db, vendor_id=vendor_id))


@router.get("/summary", response_model=SuggestionSummary)
async def suggestion_summary(_: CurrentUser, db: DbSession) -> SuggestionSummary:
    return await vendor_suggestions.summary(db)


@router.post("/{suggestion_id}/accept", response_model=SuggestionDecisionOut)
async def accept_suggestion(
    suggestion_id: uuid.UUID,
    user: CurrentUser,
    db: DbSession,
    payload: SuggestionDecisionIn | None = None,
) -> SuggestionDecisionOut:
    """Apply a suggestion, or mark it stale when its field changed since it was proposed."""
    override = payload.override if payload is not None else False
    outcome, view = await vendor_suggestions.accept(db, user, suggestion_id, override=override)
    return SuggestionDecisionOut(outcome=outcome, suggestion=view)  # type: ignore[arg-type]


@router.post("/{suggestion_id}/reject", response_model=SuggestionDecisionOut)
async def reject_suggestion(
    suggestion_id: uuid.UUID, user: CurrentUser, db: DbSession
) -> SuggestionDecisionOut:
    view = await vendor_suggestions.reject(db, user, suggestion_id)
    return SuggestionDecisionOut(outcome="rejected", suggestion=view)


@router.post("/vendors/{vendor_id}/accept-all", response_model=AcceptAllOut)
async def accept_all_for_vendor(
    vendor_id: uuid.UUID, user: CurrentUser, db: DbSession
) -> AcceptAllOut:
    """Accept a vendor's suggestions whose fields are still as expected; skip stale ones."""
    accepted, skipped = await vendor_suggestions.accept_all(db, user, vendor_id)
    return AcceptAllOut(accepted=accepted, skipped_stale=skipped)
