#!/usr/bin/env python3
"""The literal tier of the PII scanner, in a form that can run in public CI.

The denylist holds the strings that must never appear anywhere in this
repository: loyalty numbers, the household's street addresses, the leading
decimals of its home coordinates. The file holding them in the clear,
`tools/denylist.txt`, is gitignored — it is itself a personal-data file. That
had a consequence worth naming: the strongest rule in the scanner was the only
one a pushed branch never got. CI ran the regex tier alone.

This module closes that gap without publishing anything.

  * `tools/denylist.hashes` is committed. It holds a salted SHA-256 digest of
    each entry and the entry's length. It does not hold the salt.
  * `tools/denylist.salt` is gitignored and holds 32 random bytes.
  * CI receives the same bytes as the `KERP_DENYLIST_SALT` repository secret.

GitHub therefore holds a random string in one place and a list of digests in
another, and neither is worth anything without the other. Publishing the entry
*lengths* is unavoidable — the matcher needs them to know which windows to hash
— and is accepted: "there is a six-character entry" tells an observer nothing.

A pull request from a fork gets no salt, because GitHub does not expose secrets
to forked workflows. Such a run falls back to the regex tier, exactly as every
run did before. That is a deliberate downgrade and not a failure.

## Why a salted hash and not a slow KDF

Every entry is low-entropy: someone who already knows the household's street
address could confirm it against a published digest if they also had the salt.
The salt is what makes that attack impossible, so the hash itself only needs to
be fast — and it needs to be, since matching hashes a great many windows. A
password KDF here would buy nothing and cost seconds per scan.

## Why substring matching, and how it survives hashing

The plaintext tier matched by substring, and has to: a coordinate prefix exists
precisely to match inside a longer coordinate, and an address inside a sentence.
A digest cannot be searched for, so instead every window of the line whose
length equals some entry's length is hashed and looked up. Two cheap filters
keep that affordable: windows are only taken from runs of characters a denylist
entry could be made of, and a window already hashed without a hit is remembered.

Usage:
    python3 -m tools.denylist --write      regenerate the hashes from plaintext
    python3 -m tools.denylist --self-test  prove salt, digests, and matcher agree
    python3 -m tools.denylist --status     report which tier would run, and why
"""

from __future__ import annotations

import argparse
import hashlib
import os
import re
import secrets
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
PLAINTEXT_PATH = REPO_ROOT / "tools" / "denylist.txt"
HASHES_PATH = REPO_ROOT / "tools" / "denylist.hashes"
SALT_PATH = REPO_ROOT / "tools" / "denylist.salt"
SALT_ENV = "KERP_DENYLIST_SALT"

FORMAT_VERSION = 1

# A public string carried in the hashes file so a run with no access to any real
# entry can still prove the whole tier works: that the salt it was handed and the
# digests it was handed agree, and that the matcher fires on a hit. Without it a
# mistyped secret would look exactly like a clean scan.
CANARY = "kerp-denylist-canary-do-not-remove"  # pii-scan: allow public canary

# No entry shorter than this: below it, matches are noise rather than evidence.
MIN_ENTRY_LENGTH = 4

# Characters a denylist entry can be made of — emails, addresses, digit runs,
# coordinate prefixes. A window containing anything else cannot be a hit, so
# windows are only drawn from runs of these. This alphabet is generic and
# describes the *shape* of personal data, not any entry.
ALPHABET_RUN = re.compile(r"[a-z0-9@._+\- ]+")

# Windows hashed without a hit are remembered, which matters because source code
# repeats its tokens. The cap keeps a large history scan from growing unbounded.
MEMO_LIMIT = 200_000


def read_plaintext(path: Path = PLAINTEXT_PATH) -> list[str]:
    """Entries from the cleartext denylist, lowercased. Never leaves this process."""
    if not path.exists():
        return []
    entries = []
    for raw in path.read_text(encoding="utf-8", errors="replace").splitlines():
        line = raw.strip().lower()
        if line and not line.startswith("#") and len(line) >= MIN_ENTRY_LENGTH:
            entries.append(line)
    return sorted(set(entries))


def load_salt(path: Path | None = None) -> bytes | None:
    """The salt, from the environment first so CI need not write it to disk."""
    path = SALT_PATH if path is None else path  # late-bound, so tests can redirect it
    from_env = os.environ.get(SALT_ENV, "").strip()
    if from_env:
        return from_env.encode("utf-8")
    if path.exists():
        text = path.read_text(encoding="utf-8").strip()
        if text:
            return text.encode("utf-8")
    return None


def create_salt(path: Path = SALT_PATH) -> bytes:
    """Make a salt if there is none. Never overwrites: that would orphan the hashes."""
    if path.exists() and path.read_text(encoding="utf-8").strip():
        return path.read_text(encoding="utf-8").strip().encode("utf-8")
    salt = secrets.token_hex(32)
    path.write_text(salt + "\n", encoding="utf-8")
    path.chmod(0o600)
    return salt.encode("utf-8")


def digest(salt: bytes, entry: str) -> str:
    return hashlib.sha256(salt + entry.encode("utf-8")).hexdigest()


def write_hashes(
    entries: list[str], salt: bytes, path: Path = HASHES_PATH
) -> tuple[int, int]:
    """Write the committed digest file. Returns (entries, distinct lengths)."""
    payload = sorted(set(entries) | {CANARY})
    rows = sorted((len(e), digest(salt, e)) for e in payload)
    lines = [
        "# Salted SHA-256 of every literal denylist entry. Committed on purpose.",
        "#",
        "# The salt is NOT in this file. It lives in tools/denylist.salt, which is",
        "# gitignored, and in the KERP_DENYLIST_SALT repository secret. Without it",
        "# these digests are inert. See tools/denylist.py and SECURITY.md.",
        "#",
        "# Regenerate with: python3 -m tools.denylist --write",
        "# Columns: <entry length> <digest>. Sorted, so the order says nothing.",
        f"version {FORMAT_VERSION}",
        "# pii-scan: allow public canary, named below so CI can prove the tier runs",
        f"canary {CANARY}",
    ]
    lines += [f"{length} {d}" for length, d in rows]
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return len(payload), len({length for length, _ in rows})


def read_hashes(path: Path = HASHES_PATH) -> tuple[dict[int, set[str]], str]:
    """Return ({length: digests}, canary) from the committed digest file."""
    if not path.exists():
        return {}, ""
    buckets: dict[int, set[str]] = {}
    canary = ""
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        head, _, rest = line.partition(" ")
        if head == "version":
            if rest.strip() != str(FORMAT_VERSION):
                raise ValueError(
                    f"{path.name}: format version {rest.strip()!r}, expected "
                    f"{FORMAT_VERSION}. Regenerate with `python3 -m tools.denylist --write`."
                )
            continue
        if head == "canary":
            canary = rest.strip()
            continue
        if not head.isdigit() or not rest.strip():
            raise ValueError(f"{path.name}: malformed line {line!r}")
        buckets.setdefault(int(head), set()).add(rest.strip())
    return buckets, canary


class Denylist:
    """Literal-string matching, over cleartext entries or over salted digests.

    Both modes return the matched text taken from the scanned line, never an
    entry, so the caller's masking behaves identically either way.
    """

    def __init__(
        self,
        *,
        source: str,
        plain: tuple[str, ...] = (),
        buckets: dict[int, set[str]] | None = None,
        salt: bytes | None = None,
    ) -> None:
        self.source = source
        self.plain = plain
        self.buckets = buckets or {}
        self.salt = salt
        self.lengths = sorted(self.buckets)
        self._misses: set[str] = set()

    def __bool__(self) -> bool:
        return bool(self.plain or self.buckets)

    @property
    def size(self) -> int:
        return len(self.plain) or sum(len(v) for v in self.buckets.values())

    def find(self, lowered: str) -> list[str]:
        """Every denylisted string occurring in `lowered`, as it appears there."""
        if self.plain:
            return [n for n in self.plain if n in lowered]
        if not self.buckets or self.salt is None:
            return []
        return self._find_hashed(lowered)

    def _find_hashed(self, lowered: str) -> list[str]:
        hits: list[str] = []
        salt = self.salt
        assert salt is not None
        misses = self._misses
        memo_open = len(misses) < MEMO_LIMIT
        for run in ALPHABET_RUN.finditer(lowered):
            text = run.group(0)
            span = len(text)
            for length in self.lengths:
                if length > span:
                    break
                bucket = self.buckets[length]
                for start in range(span - length + 1):
                    window = text[start : start + length]
                    if window in misses:
                        continue
                    if hashlib.sha256(salt + window.encode("utf-8")).hexdigest() in (
                        bucket
                    ):
                        hits.append(window)
                    elif memo_open:
                        misses.add(window)
                        memo_open = len(misses) < MEMO_LIMIT
        return hits


EMPTY = Denylist(source="none")


def load_denylist() -> Denylist:
    """The strongest tier available here.

    Cleartext when it is present, which is the owner's own machine and is both
    faster and exact. Otherwise the digests, if a salt came with them. Otherwise
    nothing, and the caller says so.
    """
    plain = read_plaintext()
    if plain:
        # The canary rides along so that the cleartext and hashed tiers match
        # exactly the same set. A scan that is clean here is clean in CI.
        return Denylist(source="tools/denylist.txt", plain=(*plain, CANARY))
    buckets, _ = read_hashes()
    if buckets:
        salt = load_salt()
        if salt is None:
            return Denylist(source="hashes present, no salt")
        return Denylist(source="tools/denylist.hashes", buckets=buckets, salt=salt)
    return EMPTY


load = load_denylist


def _self_test() -> int:
    """Prove the tier works end to end, using only the public canary."""
    buckets, canary = read_hashes()
    if not buckets:
        print("denylist: no tools/denylist.hashes to test", file=sys.stderr)
        return 1
    if canary != CANARY:
        print(
            f"denylist: canary in the file is {canary!r}, expected {CANARY!r}",
            file=sys.stderr,
        )
        return 1
    salt = load_salt()
    if salt is None:
        # Not a failure. A contributor's clone and a fork's pull request both
        # legitimately reach here, and both run the regex tier. Only a salt that
        # is present and *wrong* means something is broken.
        print(
            f"denylist: self-test skipped — no salt here (set {SALT_ENV} or "
            "create tools/denylist.salt). The literal tier will not run."
        )
        return 0
    dl = Denylist(source="self-test", buckets=buckets, salt=salt)
    line = f"a line that happens to contain {CANARY} in the middle of it"
    hits = dl.find(line)
    if CANARY not in hits:
        print(
            "denylist: the canary did not match. The salt and the digests in "
            "tools/denylist.hashes do not agree, so the literal tier is checking "
            "nothing. Fix the KERP_DENYLIST_SALT secret, or regenerate the file.",
            file=sys.stderr,
        )
        return 1
    if dl.find("a line with nothing of interest in it"):
        print("denylist: matched a line that holds no entry", file=sys.stderr)
        return 1
    print(
        f"denylist: self-test passed — {sum(len(v) for v in buckets.values())} "
        f"digest(s) over {len(buckets)} length(s), canary matched"
    )
    return 0


def _status() -> int:
    dl = load_denylist()
    if dl.plain:
        print(f"denylist: cleartext tier — {dl.size} entries from {dl.source}")
    elif dl.buckets:
        print(f"denylist: hashed tier — {dl.size} digests from {dl.source}")
    else:
        buckets, _ = read_hashes()
        if buckets and load_salt() is None:
            print(
                f"denylist: DISABLED — {HASHES_PATH.name} is present but no salt is "
                f"available. Set {SALT_ENV}, or create tools/denylist.salt. A fork's "
                "pull request reaches this state by design."
            )
        else:
            print("denylist: DISABLED — no denylist at all. See SECURITY.md.")
    return 0


def _write() -> int:
    entries = read_plaintext()
    if not entries:
        print(
            f"denylist: {PLAINTEXT_PATH} is empty or missing; nothing to hash. "
            "Build it first with tools/build_denylist.py.",
            file=sys.stderr,
        )
        return 2
    salt = create_salt()
    count, lengths = write_hashes(entries, salt)
    print(
        f"denylist: wrote {HASHES_PATH.name} — {count} digest(s) "
        f"(including the canary) over {lengths} length(s)"
    )
    print(f"denylist: salt is in {SALT_PATH.name} (gitignored). Copy it into the")
    print(f"          {SALT_ENV} repository secret so CI runs this tier too.")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument(
        "--write", action="store_true", help="regenerate the digests from cleartext"
    )
    group.add_argument(
        "--self-test", action="store_true", help="prove salt and digests agree"
    )
    group.add_argument(
        "--status", action="store_true", help="report which tier would run"
    )
    args = parser.parse_args(argv)
    if args.write:
        return _write()
    if args.self_test:
        return _self_test()
    return _status()


if __name__ == "__main__":
    raise SystemExit(main())
