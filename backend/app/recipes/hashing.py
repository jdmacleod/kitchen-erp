"""Git blob hashes in pure Python (07, "Git access").

A file's identity in the index is the SHA-1 git would give its blob: the header
``blob <size>\\0`` followed by the bytes. Computing it here, rather than asking
git, means a file's hash can be compared with the blob at the same path in the
``HEAD`` tree without touching the repository's index or taking any lock.
"""

from __future__ import annotations

import hashlib


def blob_sha1(content: bytes) -> str:
    """The hex SHA-1 of ``content`` as a git blob object."""
    digest = hashlib.sha1(b"blob %d\0" % len(content))  # noqa: S324  git's own object id
    digest.update(content)
    return digest.hexdigest()
