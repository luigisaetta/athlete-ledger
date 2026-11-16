"""
Author: L. Saetta
Date Last Modified: 2026-11-16
License: MIT
Description: Test API-key settings using synthetic configuration only.
"""  # noqa: D205, D212

from pathlib import Path

from athlete_ledger.settings import Settings


def test_standard_key_environment_alias(monkeypatch) -> None:
    """Verify the unprefixed SDK variable maps to a redacted secret field."""
    monkeypatch.setenv("GARMIN_PERSONAL_API_KEY", "SYNTHETIC-KEY-ONLY")
    settings = Settings(_env_file=None)
    assert settings.personal_api_key.get_secret_value() == "SYNTHETIC-KEY-ONLY"
    assert "SYNTHETIC-KEY-ONLY" not in repr(settings)


def test_synthetic_settings_file_does_not_export_key(tmp_path: Path, monkeypatch) -> None:
    """Verify a synthetic settings file supplies the key without modifying the process."""
    import os

    monkeypatch.delenv("GARMIN_PERSONAL_API_KEY", raising=False)
    path = tmp_path / "synthetic-settings.env"
    path.write_text("GARMIN_PERSONAL_API_KEY=SYNTHETIC-FILE-KEY\n")
    settings = Settings(_env_file=path)
    assert settings.personal_api_key.get_secret_value() == "SYNTHETIC-FILE-KEY"
    assert "GARMIN_PERSONAL_API_KEY" not in os.environ
    assert "SYNTHETIC-FILE-KEY" not in repr(settings)
