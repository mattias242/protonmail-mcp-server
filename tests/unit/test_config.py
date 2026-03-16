"""Tester for config.py — Settings och get_settings()."""
import os

import pytest
from pydantic import ValidationError

from protonmail_mcp.config import Settings, get_settings


class TestSettings:
    """Testar Settings-klassen."""

    def test_loads_from_env_vars(self, monkeypatch):
        """Settings ska ladda varden fran PROTONMAIL_-prefix miljovariabler."""
        monkeypatch.setenv("PROTONMAIL_USERNAME", "testuser")
        monkeypatch.setenv("PROTONMAIL_PASSWORD", "testpass")
        monkeypatch.setenv("PROTONMAIL_IMAP_HOST", "10.0.0.1")
        monkeypatch.setenv("PROTONMAIL_IMAP_PORT", "993")
        monkeypatch.setenv("PROTONMAIL_SMTP_HOST", "10.0.0.2")
        monkeypatch.setenv("PROTONMAIL_SMTP_PORT", "587")

        settings = Settings()

        assert settings.username == "testuser"
        assert settings.password == "testpass"
        assert settings.imap_host == "10.0.0.1"
        assert settings.imap_port == 993
        assert settings.smtp_host == "10.0.0.2"
        assert settings.smtp_port == 587

    def test_default_values(self, monkeypatch):
        """Standardvarden ska vara korrekta."""
        monkeypatch.setenv("PROTONMAIL_USERNAME", "user")
        monkeypatch.setenv("PROTONMAIL_PASSWORD", "pass")

        settings = Settings()

        assert settings.imap_host == "127.0.0.1"
        assert settings.imap_port == 1143
        assert settings.smtp_host == "127.0.0.1"
        assert settings.smtp_port == 1026

    def test_validation_error_missing_username(self, monkeypatch):
        """ValidationError ska kastas om username saknas."""
        monkeypatch.delenv("PROTONMAIL_USERNAME", raising=False)
        monkeypatch.setenv("PROTONMAIL_PASSWORD", "pass")

        with pytest.raises(ValidationError):
            Settings(_env_file=None)

    def test_validation_error_missing_password(self, monkeypatch):
        """ValidationError ska kastas om password saknas."""
        monkeypatch.setenv("PROTONMAIL_USERNAME", "user")
        monkeypatch.delenv("PROTONMAIL_PASSWORD", raising=False)

        with pytest.raises(ValidationError):
            Settings(_env_file=None)


class TestGetSettings:
    """Testar get_settings() med lru_cache."""

    def test_returns_settings_instance(self, monkeypatch):
        """get_settings() ska returnera en Settings-instans."""
        get_settings.cache_clear()
        monkeypatch.setenv("PROTONMAIL_USERNAME", "user")
        monkeypatch.setenv("PROTONMAIL_PASSWORD", "pass")

        result = get_settings()

        assert isinstance(result, Settings)

    def test_returns_cached_instance(self, monkeypatch):
        """get_settings() ska returnera samma cachade instans vid upprepade anrop."""
        get_settings.cache_clear()
        monkeypatch.setenv("PROTONMAIL_USERNAME", "user")
        monkeypatch.setenv("PROTONMAIL_PASSWORD", "pass")

        first = get_settings()
        second = get_settings()

        assert first is second
