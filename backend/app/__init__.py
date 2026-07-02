from pathlib import Path

_ROOT_ENV = Path(__file__).resolve().parent.parent.parent / ".env"

try:
    from dotenv import load_dotenv

    if _ROOT_ENV.exists():
        load_dotenv(_ROOT_ENV, override=False)
except ImportError:  # pragma: no cover - python-dotenv is a pydantic-settings dependency
    pass

from app._ssl_patch import apply as _apply_ssl_patch

_apply_ssl_patch()
