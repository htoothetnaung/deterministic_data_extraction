"""Application configuration.

Centralised settings for the FastAPI service. Values can be overridden via
environment variables when running in production.
"""
from __future__ import annotations

from pathlib import Path
from pydantic_settings import BaseSettings, SettingsConfigDict


BASE_DIR = Path(__file__).resolve().parent.parent.parent  # backend/
UPLOAD_DIR = BASE_DIR / "uploads"
UPLOAD_DIR.mkdir(parents=True, exist_ok=True)


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="EXTRACT_", env_file=".env", extra="ignore")

    # The FastAPI service runs on its own port (8000). The Next.js gateway
    # forwards requests here using the ``XTransformPort`` query parameter.
    app_name: str = "ExtractIQ — Deterministic Document Extraction API"
    app_version: str = "0.1.0"
    debug: bool = True

    host: str = "0.0.0.0"
    port: int = 8000

    upload_dir: str = str(UPLOAD_DIR)

    # CORS — allow the Next.js dev server (port 3000) and the gateway origin.
    cors_origins: list[str] = [
        "http://localhost:3000",
        "http://127.0.0.1:3000",
    ]


settings = Settings()
