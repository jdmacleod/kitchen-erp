"""Test harness: a throwaway database per run on the Compose `db` service.

MIGRATION_DATABASE_URL (owner) and DATABASE_URL (app role) must point at the
service; both are rewritten to a fresh database name before the app is imported.
"""

from __future__ import annotations

import os
import secrets
import subprocess
import sys
from collections.abc import AsyncIterator
from pathlib import Path
from urllib.parse import urlsplit, urlunsplit

import pytest

BACKEND_ROOT = Path(__file__).resolve().parent.parent
TEST_DB = f"kerp_test_{secrets.token_hex(4)}"


def _with_db(url: str, name: str) -> str:
    parts = urlsplit(url)
    return urlunsplit(parts._replace(path=f"/{name}"))


def _dsn(url: str) -> str:
    """SQLAlchemy URL -> plain asyncpg DSN."""
    return url.replace("postgresql+asyncpg://", "postgresql://", 1)


OWNER_URL_ADMIN = os.environ["MIGRATION_DATABASE_URL"]
os.environ["MIGRATION_DATABASE_URL"] = _with_db(OWNER_URL_ADMIN, TEST_DB)
os.environ["DATABASE_URL"] = _with_db(os.environ["DATABASE_URL"], TEST_DB)
os.environ.setdefault(
    "OLLAMA_BASE_URL", "http://127.0.0.1:9"
)  # nothing listens: model server unreachable
os.environ.setdefault("MODEL_SERVER_TIMEOUT_SECONDS", "0.5")

import asyncpg  # noqa: E402
import httpx  # noqa: E402
from sqlalchemy import text  # noqa: E402

from app.core.db import dispose_engine, get_sessionmaker  # noqa: E402
from app.main import app  # noqa: E402
from app.services import identity  # noqa: E402


def run_alembic(*args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, "-m", "alembic", *args],
        cwd=BACKEND_ROOT,
        env=os.environ.copy(),
        capture_output=True,
        text=True,
        check=True,
    )


@pytest.fixture(scope="session", autouse=True)
async def database() -> AsyncIterator[None]:
    admin = await asyncpg.connect(_dsn(OWNER_URL_ADMIN))
    try:
        await admin.execute(f'CREATE DATABASE "{TEST_DB}"')
    finally:
        await admin.close()
    try:
        run_alembic("upgrade", "head")
        # Reference units survive per-test truncation; seed them once.
        from app.services.units import seed_units

        async with get_sessionmaker()() as db:
            await seed_units(db)
        yield
    finally:
        await dispose_engine()
        admin = await asyncpg.connect(_dsn(OWNER_URL_ADMIN))
        try:
            await admin.execute(f'DROP DATABASE "{TEST_DB}" WITH (FORCE)')
        finally:
            await admin.close()


KEEP_TABLES = {"alembic_version", "unit", "spatial_ref_sys"}


@pytest.fixture(autouse=True)
async def clean_tables() -> AsyncIterator[None]:
    """Empty every application table after each test; reference data stays."""
    yield
    owner = await asyncpg.connect(_dsn(os.environ["MIGRATION_DATABASE_URL"]))
    try:
        names = [
            r["tablename"]
            for r in await owner.fetch(
                "SELECT tablename FROM pg_tables WHERE schemaname = 'public'"
            )
            if r["tablename"] not in KEEP_TABLES
        ]
        if names:
            quoted = ", ".join(f'"{n}"' for n in names)
            await owner.execute(f"TRUNCATE {quoted} CASCADE")
    finally:
        await owner.close()


@pytest.fixture
async def client() -> AsyncIterator[httpx.AsyncClient]:
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as c:
        yield c


ADMIN = {"email": "admin@example.com", "display_name": "Admin", "password": "correct-horse-battery"}
MEMBER = {
    "email": "member@example.com",
    "display_name": "Member",
    "password": "another-good-secret",
}


async def make_user(role: str = "admin", **overrides):
    data = {**(ADMIN if role == "admin" else MEMBER), **overrides}
    async with get_sessionmaker()() as db:
        return await identity.create_user(db, role=role, **data)


@pytest.fixture
async def admin():
    return await make_user("admin")


@pytest.fixture
async def admin_client(client: httpx.AsyncClient, admin) -> httpx.AsyncClient:
    r = await client.post(
        "/api/v1/auth/login", json={"email": ADMIN["email"], "password": ADMIN["password"]}
    )
    assert r.status_code == 200, r.text
    return client


@pytest.fixture
async def owner_conn() -> AsyncIterator[asyncpg.Connection]:
    conn = await asyncpg.connect(_dsn(os.environ["MIGRATION_DATABASE_URL"]))
    try:
        yield conn
    finally:
        await conn.close()


@pytest.fixture
async def app_conn() -> AsyncIterator[asyncpg.Connection]:
    conn = await asyncpg.connect(_dsn(os.environ["DATABASE_URL"]))
    try:
        yield conn
    finally:
        await conn.close()


@pytest.fixture
async def db_session():
    async with get_sessionmaker()() as db:
        yield db
        await db.execute(text("SELECT 1"))
