import httpx
import pytest

from app.api import health as health_api
from app.core.config import DEV_VERSION, UNKNOWN_COMMIT, Settings, get_settings, is_dev_build


async def test_health_public_is_status_only(client: httpx.AsyncClient):
    r = await client.get("/api/v1/health")
    assert r.status_code == 200
    body = r.json()
    assert set(body) == {"status"}
    # The model server is unreachable in tests: healthy overall, marked degraded.
    assert body["status"] == "degraded"


async def test_health_details_when_authenticated(admin_client: httpx.AsyncClient):
    r = await admin_client.get("/api/v1/health")
    assert r.status_code == 200
    body = r.json()
    checks = body["checks"]
    assert checks["database"]["status"] == "ok"
    assert checks["migrations"]["status"] == "ok"
    assert checks["migrations"]["detail"]["current"] == checks["migrations"]["detail"]["expected"]
    assert checks["model_server"]["status"] == "degraded"
    assert checks["ingest_queue"]["status"] == "ok"
    assert checks["ingest_queue"]["detail"]["depth"] == 0
    assert body["status"] == "degraded"


async def test_health_public_hides_the_build_identity(client: httpx.AsyncClient):
    """The sha names the exact commit whose vulnerabilities apply.

    `test_health_public_is_status_only` above is the standing guard on the whole
    anonymous shape. This one says why the build fields in particular must not
    appear there, so a later change cannot relax the guard without reading the
    reason.
    """
    body = (await client.get("/api/v1/health")).json()
    assert "version" not in body
    assert "commit" not in body
    assert "is_dev" not in body


async def test_health_reports_the_build_identity_when_authenticated(
    admin_client: httpx.AsyncClient,
):
    body = (await admin_client.get("/api/v1/health")).json()
    assert body["version"] == DEV_VERSION
    assert body["commit"] == UNKNOWN_COMMIT
    # The test image is built without the args, so it honestly does not know.
    assert body["is_dev"] is True


async def test_health_reports_a_release_build_as_not_dev(
    admin_client: httpx.AsyncClient, monkeypatch: pytest.MonkeyPatch
):
    monkeypatch.setattr(
        health_api,
        "get_settings",
        lambda: get_settings().model_copy(
            update={"build_version": "v0.2.0", "build_commit": "a1b2c3d"}
        ),
    )
    body = (await admin_client.get("/api/v1/health")).json()
    assert body["version"] == "v0.2.0"
    assert body["commit"] == "a1b2c3d"
    assert body["is_dev"] is False


async def test_an_untagged_build_is_still_a_dev_cut(
    admin_client: httpx.AsyncClient, monkeypatch: pytest.MonkeyPatch
):
    """The state this repository is actually in: real args, no tag.

    `git describe --tags --always --dirty` falls back to the sha here, so neither
    sentinel survives and a sentinel-only check would have called this a release.
    Caught by looking at the rendered sidebar, which showed no dev badge on a
    dirty working tree.
    """
    monkeypatch.setattr(
        health_api,
        "get_settings",
        lambda: get_settings().model_copy(
            update={"build_version": "4b4d8fc-dirty", "build_commit": "4b4d8fc"}
        ),
    )
    body = (await admin_client.get("/api/v1/health")).json()
    assert body["version"] == "4b4d8fc-dirty"
    assert body["is_dev"] is True


@pytest.mark.parametrize(
    ("version", "commit", "expected"),
    [
        # A clean tag on a clean tree is the only release.
        ("v0.2.0", "a1b2c3d", False),
        ("0.2.0", "a1b2c3d", False),  # tags need not start with v
        # Everything `git describe` says to explain why this is not that tag.
        ("v0.2.0-dirty", "a1b2c3d", True),
        ("v0.2.0-5-gabc1234", "abc1234", True),
        ("v0.2.0-5-gabc1234-dirty", "abc1234", True),
        # `--always` fell back to the sha: the repository has no tags at all.
        ("4b4d8fc", "4b4d8fc", True),
        ("4b4d8fc-dirty", "4b4d8fc", True),
        # Built without the args.
        (DEV_VERSION, UNKNOWN_COMMIT, True),
        (DEV_VERSION, "a1b2c3d", True),
        ("v0.2.0", UNKNOWN_COMMIT, True),
    ],
)
def test_only_a_clean_tag_counts_as_a_release(version: str, commit: str, expected: bool):
    assert is_dev_build(version, commit) is expected


def test_settings_default_to_the_sentinels(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.delenv("BUILD_VERSION", raising=False)
    monkeypatch.delenv("BUILD_COMMIT", raising=False)
    settings = Settings(_env_file=None, database_url="x", migration_database_url="y")
    assert settings.build_version == DEV_VERSION
    assert settings.build_commit == UNKNOWN_COMMIT


def test_settings_take_the_build_args_from_the_environment(monkeypatch: pytest.MonkeyPatch):
    """The Dockerfile promotes both ARGs to ENV; this is the half that reads them."""
    monkeypatch.setenv("BUILD_VERSION", "v0.2.0-5-gabc1234")
    monkeypatch.setenv("BUILD_COMMIT", "abc1234")
    settings = Settings(_env_file=None, database_url="x", migration_database_url="y")
    assert settings.build_version == "v0.2.0-5-gabc1234"
    assert settings.build_commit == "abc1234"
