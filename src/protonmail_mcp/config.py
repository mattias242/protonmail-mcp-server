from __future__ import annotations

from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    imap_host: str = "127.0.0.1"
    imap_port: int = 1143
    smtp_host: str = "127.0.0.1"
    smtp_port: int = 1026
    username: str
    password: str
    verify_ssl: bool = False
    smtp_ca_cert: str | None = None

    model_config = SettingsConfigDict(
        env_prefix="PROTONMAIL_",
        env_file=".env",
    )


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()
