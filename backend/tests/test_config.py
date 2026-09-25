"""The settings contract: what an operator sets, and what happens to what they set."""

from app.core.config import Settings


def _settings(**overrides: object) -> Settings:
    # `_env_file=None` keeps a developer's own .env out of the assertions.
    return Settings(_env_file=None, database_url="x", migration_database_url="y", **overrides)


def test_the_singular_ocr_adapter_setting_is_gone():
    """`OCR_ADAPTER` was declared, forwarded by compose and documented, and read
    by nothing: setting it changed no behaviour at all. The live knob is the
    ordered list `OCR_ADAPTERS`. Two settings one letter apart, only one of them
    wired up, is the kind of configuration an operator stops trusting.
    """
    assert "ocr_adapter" not in Settings.model_fields
    assert "ocr_adapters" in Settings.model_fields


def test_a_removed_setting_left_in_an_operators_env_does_not_stop_the_app(monkeypatch):
    """Deleting a setting must not turn every existing `.env` into a boot failure."""
    # `_env_file=None` does not stop pydantic-settings reading the ambient
    # environment, and compose forwards OCR_ADAPTERS into the container this
    # runs in. Without this the assertion below fails for any operator who has
    # legitimately set it, which is a test failing because the deployment is
    # configured -- the same trap as the health tests in #32.
    monkeypatch.delenv("OCR_ADAPTERS", raising=False)
    monkeypatch.setenv("OCR_ADAPTER", "tesseract")
    settings = _settings()
    assert settings.ocr_adapters == ["client", "tesseract"]
    assert not hasattr(settings, "ocr_adapter")


def test_the_surviving_ocr_setting_is_read_from_the_environment(monkeypatch):
    """The one that stayed is the one that does something, and an operator sets
    it as the JSON list `.env.example` documents."""
    monkeypatch.setenv("OCR_ADAPTERS", '["client"]')
    assert _settings().ocr_adapters == ["client"]
