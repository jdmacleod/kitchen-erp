"""Where a stored receipt image lives on disk.

This is its own module for a structural reason rather than a tidiness one.
`app/services/ingest.py` imports the stage machine from `app/ingest/stages.py`,
and the stages need to resolve a document to a path. Putting that function in
`services/ingest.py` made the two modules mutually dependent, which the stages
worked around by importing inside the function body. A deferred import hides a
cycle rather than removing one; the path helper simply belongs below both.
"""

from __future__ import annotations

from pathlib import Path

from app.core.config import get_settings
from app.models.purchases import ReceiptDocument


def document_path(document: ReceiptDocument) -> Path:
    """Absolute path of the stored image. Built from the digest, not the stored string."""
    return Path(get_settings().receipts_path) / document.image_path
