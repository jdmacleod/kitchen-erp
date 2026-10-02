"""Opt-in: product photos read by a live model, reporting field accuracy (04, 2L).

Run with ``KERP_LLM_TESTS=1 uv run pytest -m llm -s`` and a reachable
OLLAMA_BASE_URL; set VISION_MODEL to also measure the vision path. The labels
are invented and drawn at test time. Nothing here asserts exact output; the
report is what matters when choosing models, beside the receipt cases.
"""

from __future__ import annotations

import io
import os

import pytest
from PIL import Image, ImageDraw, ImageFont

from app.core.config import get_settings
from app.ingest.llm import LlmClient
from app.services.identify import PRODUCT_SYSTEM_PROMPT, PRODUCT_TASK, ProductReading

pytestmark = [
    pytest.mark.llm,
    pytest.mark.skipif(not os.environ.get("KERP_LLM_TESTS"), reason="set KERP_LLM_TESTS=1"),
]

# Invented products: what is printed, and what a reading should say.
CASES = [
    {
        "lines": ["HOLLOW CREEK", "Cut Green Beans", "NET WT 14.5 OZ (411g)"],
        "want": {"name": "cut green beans", "brand": "hollow creek", "pack_unit": "oz"},
    },
    {
        "lines": ["LARKFIELD MILLS", "Strong White Bread Flour", "1.5 kg"],
        "want": {"name": "strong white bread flour", "brand": "larkfield mills", "pack_unit": "kg"},
    },
    {
        "lines": ["Brightfield", "Oat Drink Unsweetened", "1 L"],
        "want": {"name": "oat drink unsweetened", "brand": "brightfield", "pack_unit": "l"},
    },
]


def label(lines: list[str]) -> bytes:
    image = Image.new("RGB", (900, 600), (245, 240, 228))
    draw = ImageDraw.Draw(image)
    font = ImageFont.load_default(size=56)
    for i, text in enumerate(lines):
        draw.text((60, 80 + i * 140), text, fill=(30, 30, 30), font=font)
    out = io.BytesIO()
    image.save(out, format="JPEG", quality=92)
    return out.getvalue()


def score(reading: ProductReading, want: dict[str, str]) -> tuple[int, int]:
    hits = 0
    for key, expected in want.items():
        got = (getattr(reading, key) or "").strip().lower()
        hits += got == expected or (key == "pack_unit" and got.startswith(expected))
    return hits, len(want)


async def test_product_reading_accuracy_report(capsys):
    text_hits = text_total = vision_hits = vision_total = 0
    vision_model = get_settings().vision_model
    for case in CASES:
        reading, _ = await LlmClient(transport=None).extract(
            ProductReading, PRODUCT_TASK, "\n".join(case["lines"]), system=PRODUCT_SYSTEM_PROMPT
        )
        hits, total = score(reading, case["want"])
        text_hits, text_total = text_hits + hits, text_total + total
        if vision_model:
            reading, _ = await LlmClient(model=vision_model, transport=None).extract(
                ProductReading,
                PRODUCT_TASK,
                "",
                images=[label(case["lines"])],
                think=False,
                system=PRODUCT_SYSTEM_PROMPT,
            )
            hits, total = score(reading, case["want"])
            vision_hits, vision_total = vision_hits + hits, vision_total + total
    with capsys.disabled():
        settings = get_settings()
        print(f"\nproduct text  ({settings.llm_model}): {text_hits}/{text_total} fields")
        if vision_model:
            print(f"product vision ({vision_model}): {vision_hits}/{vision_total} fields")
