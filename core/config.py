"""
core/config.py
──────────────
Single source of truth for all environment configuration.
Every module in NoPlanB imports Settings from here.
No scattered os.getenv() calls anywhere else.
"""

from pathlib import Path
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """
    Loads values from .env (if present) and environment variables.
    Fields are typed and validated by Pydantic.
    """

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # ── Ollama / Local Gemma ──────────────────────────────────
    ollama_base_url: str = "http://localhost:11434/v1"
    gemma_model: str = "gemma3:4b"

    # ── Temporal ─────────────────────────────────────────────
    temporal_host: str = "localhost:7233"
    temporal_namespace: str = "default"
    temporal_task_queue: str = "noplanb-queue"

    # ── Sentry ───────────────────────────────────────────────
    sentry_dsn: str = ""
    # 0 = keep journal content off Sentry cloud (privacy-first default)
    sentry_send_pii: int = 0

    # ── Vault ─────────────────────────────────────────────────
    vault_root: Path = Path("./vault")

    # ── API Server ────────────────────────────────────────────
    api_host: str = "127.0.0.1"
    api_port: int = 8000

    # ── Notifications ─────────────────────────────────────────
    telegram_bot_token: str = ""
    telegram_chat_id: str = ""

    # ── Derived Paths (computed from vault_root) ───────────────
    @property
    def horizons_dir(self) -> Path:
        return self.vault_root / "horizons"

    @property
    def crucibles_dir(self) -> Path:
        return self.vault_root / "crucibles"

    @property
    def daily_dir(self) -> Path:
        return self.vault_root / "daily"

    @property
    def embeddings_dir(self) -> Path:
        return self.vault_root / "embeddings"

    def ensure_vault_dirs(self) -> None:
        """Create vault subdirectories if they don't exist."""
        for d in [
            self.horizons_dir,
            self.crucibles_dir,
            self.daily_dir,
            self.embeddings_dir,
        ]:
            d.mkdir(parents=True, exist_ok=True)


# ── Module-level singleton ─────────────────────────────────────
# Import this everywhere: `from core.config import settings`
settings = Settings()
