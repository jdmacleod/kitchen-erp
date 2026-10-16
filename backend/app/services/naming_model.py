"""The local model on the naming pass: asked without a button, with a shortlist (#387).

With ``NAMING_AUTO_SUGGEST`` on, opening the naming pass queues the rows the
wording couldn't name that were never asked; a failed answer waits for the
button. The model is local (non-negotiable 9).

Each question carries a shortlist: product names the catalog already has near
the line's suggested name, and what this vendor's printed words were learned to
mean. The shortlist is catalog text, which can come from receipts and pages, so
it goes in the data the model reads between the delimiters, never in the task.
The answer still only fills fields, after validating (non-negotiable 7).
"""

from __future__ import annotations

import uuid

from sqlalchemy import func, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.catalog import sameness
from app.ingest.llm import NAMING_TASK, LlmClient
from app.ingest.schemas import LineNaming
from app.models import NamingSuggestion, Product
from app.services import line_words, naming_words
from app.services.naming import read_pack

# The task is fixed text. The shortlist, when there is one, follows the line in the data.
TASK_WITH_SHORTLIST = (
    NAMING_TASK + " After the line, the data may list product names this household already uses "
    "and what this store's abbreviations stand for. Prefer those words and spellings "
    "when they fit the line; they are hints, not answers."
)
NEARBY = 10


async def queue_unasked(db: AsyncSession, rows: list[dict]) -> int:
    """Queue every row the wording couldn't name that was never asked, and mark it
    asking in ``rows``. A failed one is left for the button. Returns how many."""
    queued = 0
    for row in rows:
        if row["ingredient"] is not None or not row["raw_text_norm"] or row["model"] is not None:
            continue
        await db.execute(
            insert(NamingSuggestion)
            .values(
                id=uuid.uuid4(),
                vendor_id=row["vendor"]["id"],
                raw_text_norm=row["raw_text_norm"],
                status="pending",
            )
            .on_conflict_do_nothing(constraint="uq_naming_suggestion_vendor_text")
        )
        row["model"] = {"status": "asking", "name": None, "ingredient": None}
        queued += 1
    if queued:
        await db.commit()
    return queued


async def shortlist(db: AsyncSession, vendor_id: uuid.UUID, raw_text_norm: str) -> str:
    """The hints for one line, as data lines; empty when there are none."""
    known = await naming_words.load(db)
    _, words = read_pack(raw_text_norm.split())
    named = await naming_words.suggest(db, known, vendor_id, words)
    key = sameness.key_text(named.name) or named.name.lower()
    names = (
        await db.scalars(
            select(Product.name)
            .where(Product.active, func.lower(Product.name).op("%")(key))
            .order_by(func.similarity(func.lower(Product.name), key).desc())
            .limit(NEARBY)
        )
    ).all()
    learned = await line_words.expansions(db, vendor_id)
    read = {
        w.casefold(): learned[w.casefold()][0].expansion for w in words if w.casefold() in learned
    }
    out: list[str] = []
    if names:
        out.append("Product names already in this kitchen: " + "; ".join(dict.fromkeys(names)))
    if read:
        out.append(
            "This store's abbreviations: " + "; ".join(f"{w} = {e}" for w, e in read.items())
        )
    return "\n".join(out)


async def ask(
    db: AsyncSession, suggestion: NamingSuggestion, *, client: LlmClient | None = None
) -> LineNaming:
    """Ask the model about one line, with its shortlist when there is one."""
    hints = await shortlist(db, suggestion.vendor_id, suggestion.raw_text_norm)
    task = TASK_WITH_SHORTLIST if hints else NAMING_TASK
    text = f"{suggestion.raw_text_norm}\n\n{hints}" if hints else suggestion.raw_text_norm
    answer, _attempts = await (client or LlmClient()).extract(LineNaming, task, text)
    return answer
