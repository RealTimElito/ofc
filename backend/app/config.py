"""OFC application settings — air-gapped by default."""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    ofc_data_dir: Path = Path("./data")
    ofc_secret_key: str = "change-me-to-a-long-random-string"
    ofc_fernet_key: str = ""

    llm_base_url: str = "http://127.0.0.1:11434/v1"
    llm_api_key: str = "ollama"
    llm_model: str = "llama3.1"
    llm_timeout_seconds: int = 300
    llm_max_tokens: int = 4096
    llm_temperature: float = 0.3
    llm_system_prompt: str = (
        "You are a careful technical report writer in an air-gapped environment. "
        "Use only the provided context, examples, and brief. Do not invent facts. "
        "If data is missing, say so explicitly. Match the tone and structure of "
        "example reports when they are supplied."
    )

    ofc_host: str = "0.0.0.0"
    ofc_port: int = 8000
    ofc_cors_origins: str = "http://localhost:5173,http://localhost:8080"

    @property
    def cors_origins(self) -> list[str]:
        return [o.strip() for o in self.ofc_cors_origins.split(",") if o.strip()]

    @property
    def uploads_dir(self) -> Path:
        return self.ofc_data_dir / "uploads"

    @property
    def reports_dir(self) -> Path:
        return self.ofc_data_dir / "reports"

    @property
    def db_path(self) -> Path:
        return self.ofc_data_dir / "db" / "ofc.sqlite3"

    def ensure_dirs(self) -> None:
        for path in (
            self.ofc_data_dir,
            self.uploads_dir,
            self.reports_dir,
            self.db_path.parent,
        ):
            path.mkdir(parents=True, exist_ok=True)


@lru_cache
def get_settings() -> Settings:
    settings = Settings()
    settings.ensure_dirs()
    return settings
