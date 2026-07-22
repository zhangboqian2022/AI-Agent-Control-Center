import os
from pathlib import Path

APP_NAME = "AACC"
APP_SUPPORT_DIR = Path.home() / "Library" / "Application Support" / APP_NAME
DEFAULT_CONFIG_PATH = APP_SUPPORT_DIR / "config.yaml"
DEFAULT_DATABASE_PATH = APP_SUPPORT_DIR / "aacc.db"
DEFAULT_PORT = 17650


def resolve_database_path() -> Path:
    """Single source for the runtime database path (app, CLI, doctor)."""
    return Path(os.environ.get("AACC_DATABASE_PATH", DEFAULT_DATABASE_PATH))
