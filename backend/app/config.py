"""Settings read from environment variables (a .env file is supported)."""
from __future__ import annotations

from functools import lru_cache
from pathlib import Path

from pydantic import AliasChoices, Field
from pydantic_settings import BaseSettings, SettingsConfigDict

BACKEND_DIR = Path(__file__).resolve().parent.parent


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=(BACKEND_DIR / ".env", BACKEND_DIR.parent / ".env"),
        env_file_encoding="utf-8",
        extra="ignore",
        populate_by_name=True,
    )

    # GitHub
    github_token: str = ""
    github_api_url: str = "https://api.github.com"

    # LLM
    # "auto": use Claude if ANTHROPIC_API_KEY is set, otherwise Gemini if GEMINI_API_KEY is set
    llm_provider: str = "auto"  # auto | anthropic | gemini
    anthropic_api_key: str = ""
    gemini_api_key: str = Field("", validation_alias=AliasChoices("GEMINI_API_KEY", "GOOGLE_API_KEY"))
    gemini_model: str = "gemini-2.5-flash"
    gemini_api_url: str = "https://generativelanguage.googleapis.com/v1beta"
    errlens_model: str = "claude-sonnet-5"
    errlens_max_tokens: int = 4096
    # Language the model writes its explanations in
    errlens_output_language: str = "English"
    # When true, /analyze and /analyze/stream return the fixture result instead of a real analysis (UI work)
    errlens_fixture_mode: bool = False

    # Application
    errlens_db_path: str = str(BACKEND_DIR / "errlens.db")
    cors_origins: str = "http://localhost:3000"
    # Accept a local git repository path as repo_url (offline rehearsals and tests)
    errlens_allow_local_repos: bool = False

    # Timeouts (seconds). The pipeline budget covers one LLM call plus one retry.
    github_timeout: float = 15.0
    llm_timeout: float = 90.0
    pipeline_timeout: float = 240.0

    # Context settings
    snippet_radius: int = 20
    max_context_frames: int = 3
    blame_radius: int = 3
    recent_days: int = 14

    @property
    def resolved_llm_provider(self) -> str | None:
        p = self.llm_provider.lower().strip()
        if p == "anthropic":
            return "anthropic" if self.anthropic_api_key else None
        if p == "gemini":
            return "gemini" if self.gemini_api_key else None
        if self.anthropic_api_key:
            return "anthropic"
        if self.gemini_api_key:
            return "gemini"
        return None

    @property
    def cors_origin_list(self) -> list[str]:
        return [o.strip() for o in self.cors_origins.split(",") if o.strip()]


@lru_cache
def get_settings() -> Settings:
    return Settings()
