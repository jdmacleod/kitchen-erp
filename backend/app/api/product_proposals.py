"""Product proposals (04, 2L): read, edit, accept, reject."""

from __future__ import annotations

import uuid

from fastapi import APIRouter, Query
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import CurrentUser, DbSession
from app.catalog import proposals as merging
from app.models import ProductCapture, ProductProposal
from app.schemas.catalog import HouseBrandOut
from app.schemas.product_photos import ProductPhotoOut
from app.schemas.proposals import (
    AcceptIn,
    CaptureOut,
    ProductJobOut,
    ProposalEditIn,
    ProposalList,
    ProposalOut,
    ProposalSummary,
)
from app.services import brands, lookups, proposals, resolution

router = APIRouter(tags=["product proposals"])


async def proposal_out(db: AsyncSession, proposal: ProductProposal) -> ProposalOut:
    capture = await db.get(ProductCapture, proposal.capture_id) if proposal.capture_id else None
    return ProposalOut(
        id=proposal.id,
        kind=proposal.kind,  # type: ignore[arg-type]
        status=proposal.status,  # type: ignore[arg-type]
        product_id=proposal.product_id,
        capture=CaptureOut.model_validate(capture) if capture else None,
        fields=proposal.fields,  # type: ignore[arg-type]
        match=proposal.match,
        candidates=await proposals.candidates_of(db, proposal),  # type: ignore[arg-type]
        look_alikes=[
            {"id": other.id, "title": merging.value(other.fields, "title")}
            for other in (await proposals.look_alikes(db)).get(proposal.id, [])
        ]
        if proposal.status == "pending"
        else [],  # type: ignore[arg-type]
        listing=proposal.listing,
        vendor=await proposals.vendor_context(db, proposal),  # type: ignore[arg-type]
        price=proposal.price,
        photos=[
            ProductPhotoOut.model_validate(p) for p in await proposals.photos_of(db, proposal.id)
        ],
        jobs=[ProductJobOut.model_validate(j) for j in await proposals.jobs_of(db, proposal)],
        reading=await proposals.reading_of(db, proposal),  # type: ignore[arg-type]
        lookup=await lookups.request_for_proposal(db, proposal.id),  # type: ignore[arg-type]
        house_brand=await _house_brand(db, proposal),
        decided_at=proposal.decided_at,
        result=proposal.result,
        created_at=proposal.created_at,
    )


async def _house_brand(db: AsyncSession, proposal: ProductProposal) -> HouseBrandOut | None:
    brand = merging.value(proposal.fields, "brand")
    found = await brands.find_brand(db, str(brand)) if brand else None
    return HouseBrandOut.model_validate(found) if found else None


@router.get("/product-proposals", response_model=ProposalList)
async def list_proposals(
    _: CurrentUser, db: DbSession, limit: int = Query(50, ge=1, le=200)
) -> ProposalList:
    """Pending proposals, oldest first, with the counts the inbox shows."""
    items = []
    alike = await proposals.look_alikes(db)
    for p in await proposals.list_pending(db, limit):
        capture = await db.get(ProductCapture, p.capture_id) if p.capture_id else None
        items.append(
            ProposalSummary(
                id=p.id,
                kind=p.kind,  # type: ignore[arg-type]
                status=p.status,  # type: ignore[arg-type]
                title=merging.value(p.fields, "title"),
                brand=merging.value(p.fields, "brand"),
                channel=capture.channel if capture else None,  # type: ignore[arg-type]
                has_conflict=bool(merging.has_conflict(p.fields)),
                look_alikes=[other.id for other in alike.get(p.id, [])],
                created_at=p.created_at,
            )
        )
    return ProposalList(items=items, counts=await proposals.pending_counts(db))


@router.get("/product-proposals/{proposal_id}", response_model=ProposalOut)
async def get_proposal(proposal_id: uuid.UUID, _: CurrentUser, db: DbSession) -> ProposalOut:
    """A proposal with its fields, sources, alternatives, matches, photos and jobs."""
    return await proposal_out(db, await proposals.get_proposal(db, proposal_id))


@router.patch("/product-proposals/{proposal_id}", response_model=ProposalOut)
async def edit_proposal(
    proposal_id: uuid.UUID, payload: ProposalEditIn, _: CurrentUser, db: DbSession
) -> ProposalOut:
    """Set fields as a person; a new GTIN may supersede another pending proposal."""
    return await proposal_out(db, await proposals.edit(db, proposal_id, payload.edits))


@router.post("/product-proposals/{proposal_id}/accept", response_model=ProposalOut)
async def accept_proposal(
    proposal_id: uuid.UUID, payload: AcceptIn, user: CurrentUser, db: DbSession
) -> ProposalOut:
    """Create or update the product, its codes, listing, photos and posted price, together."""
    data = proposals.AcceptInput(**payload.model_dump())
    accepted = await proposals.accept(db, user, proposal_id, data)
    if accepted.product_id:
        # Proposals clipped before this product existed now see it (2P).
        await proposals.rematch_pending(db, accepted.product_id)
        if (accepted.result or {}).get("identifiers"):
            # Receipt lines that print the new code and wait in the queue resolve now.
            await resolution.match_waiting(db, user, accepted.product_id)
        accepted = await proposals.get_proposal(db, proposal_id)
    return await proposal_out(db, accepted)


@router.post("/product-proposals/{proposal_id}/reject", response_model=ProposalOut)
async def reject_proposal(proposal_id: uuid.UUID, user: CurrentUser, db: DbSession) -> ProposalOut:
    return await proposal_out(db, await proposals.reject(db, user, proposal_id))
