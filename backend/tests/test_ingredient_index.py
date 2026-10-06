"""Reading receipt lines against one load of the ingredient names (#183).

``ingredients_in_text`` used to run a search per phrase of a line; the naming
list reads a hundred lines at once. It now looks phrases up in an index, and
must answer exactly as the per-phrase search did.
"""

from __future__ import annotations

import uuid

from sqlalchemy import event, update

from app.catalog import standard
from app.models.catalog import Ingredient
from app.schemas.catalog import IngredientMatch
from app.services import catalog, naming, resolution
from app.services.catalog import _LINE_PHRASE_WORDS, search_ingredients
from app.services.normalize import normalize_receipt_text
from app.services.spellings import add_spelling
from tests.catalog_helpers import make_ingredient, seed_units_via_service
from tests.pricebook_helpers import make_location
from tests.resolution_helpers import make_receipt_purchase


async def _reference_in_text(db, receipt_text: str, *, limit: int = 3) -> list[IngredientMatch]:
    """The per-phrase implementation this replaced, kept as the oracle."""
    words = [w for w in normalize_receipt_text(receipt_text).casefold().split() if w.isalpha()]
    phrases: list[str] = []
    for n in range(min(_LINE_PHRASE_WORDS, len(words)), 0, -1):
        for i in range(len(words) - n + 1):
            phrase = " ".join(words[i : i + n])
            if phrase not in phrases:
                phrases.append(phrase)
            if n == 2 and (swapped := f"{words[i + 1]} {words[i]}") not in phrases:
                phrases.append(swapped)
    found: list[tuple[int, int, int, IngredientMatch]] = []
    seen: set[str] = set()
    for order, phrase in enumerate(phrases):
        for match in await search_ingredients(db, phrase, include_standard=True, limit=5):
            ident = f"i:{match.id}" if match.kind == "ingredient" else f"s:{match.key}"
            if not match.exact or ident in seen:
                continue
            seen.add(ident)
            kind = 0 if match.kind == "ingredient" else 1
            found.append((-len(phrase.split()), kind, order, match))
    found.sort(key=lambda t: t[:3])
    return [m for *_, m in found][:limit]


async def _invented_catalog(client, db) -> None:
    await seed_units_via_service(db)
    await make_ingredient(client, "broccoli")  # takes its standard twin
    await make_ingredient(client, "Smoked trout")
    await make_ingredient(client, "Crème fraîche")
    await make_ingredient(client, "scallion", standard_key="scallion")
    oats = await make_ingredient(client, "Larkfield oats")
    await add_spelling(db, uuid.UUID(oats["id"]), "lark oats")
    await db.commit()
    gone = await make_ingredient(client, "garlic")
    # An inactive ingredient is never offered, but still holds its standard name.
    await db.execute(
        update(Ingredient).where(Ingredient.id == uuid.UUID(gone["id"])).values(active=False)
    )
    await db.commit()
    kale = await make_ingredient(client, "kale")
    r = await client.patch(f"/api/v1/ingredients/{kale['id']}", json={"name": "Lacinato kale"})
    assert r.status_code == 200, r.text


_LINES = [
    "WT BROCCOLI CROWNS 4.12 F",
    "SMOKED TROUT 7.25",
    "SMOKED TROUTS",
    "CREME FRAICHE 8OZ",
    "GREEN ONION BNCH",
    "ONIONS GREEN",
    "LARK OATS 1KG",
    "LARKFIELD OATS ROLLED",
    "GARLIC POWDER JAR",
    "GARLIC",
    "KALE LACINATO",
    "KALE",
    "SQUASH BUTTERNUT EA",
    "PARSNIPS LOOSE",
    "CHERRIES RED",
    "BNLS CHKN BRST 4011",
    "BAY LEAF",
    "PEPPERS RED",
    "",
    "12.99",
]


async def test_the_index_answers_as_the_per_phrase_search_did(admin_client, db_session):
    await _invented_catalog(admin_client, db_session)
    lines = list(_LINES)
    # Every fifth standard name, printed as a till prints it, plural and noun first.
    for e in standard.standard_list().ingredients[::5]:
        words = e.name.upper().split()
        lines.append(f"WT {' '.join(words)} 2.99")
        lines.append(f"{' '.join(words)}S")
        if len(words) == 2:
            lines.append(f"{words[1]} {words[0]} EA")
    index = await catalog.ingredient_index(db_session)
    for line in lines:
        expected = [m.model_dump() for m in await _reference_in_text(db_session, line, limit=10)]
        got = await catalog.ingredients_in_text(db_session, line, limit=10, index=index)
        assert [m.model_dump() for m in got] == expected, line


async def test_the_naming_list_costs_the_same_queries_for_ten_groups_or_a_hundred(
    admin_client, admin, db_session
):
    await _invented_catalog(admin_client, db_session)
    loc = await make_location(admin_client, "Quayside Grocer", "Quayside Grocer North")
    names = [e.name.upper() for e in standard.standard_list().ingredients[:100]]

    async def committed(texts: list[str]) -> None:
        lines = [{"raw_text": f"{t} {i}.25", "line_total": f"{i}.25"} for i, t in enumerate(texts)]
        pid = await make_receipt_purchase(admin.id, loc["id"], lines)
        await resolution.resolve_purchase(db_session, uuid.UUID(pid))
        r = await admin_client.post(f"/api/v1/purchases/{pid}/commit")
        assert r.status_code == 200, r.text

    async def queries() -> tuple[int, int]:
        count = 0

        def tick(*_):
            nonlocal count
            count += 1

        engine = db_session.bind.sync_engine
        event.listen(engine, "before_cursor_execute", tick)
        try:
            rows = await naming.naming_rows(db_session)
        finally:
            event.remove(engine, "before_cursor_execute", tick)
        return len(rows), count

    await committed(names[:10])
    ten = await queries()
    await committed(names[10:])
    hundred = await queries()
    assert (ten[0], hundred[0]) == (10, 100)
    assert hundred[1] == ten[1]
