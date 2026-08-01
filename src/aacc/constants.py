import os
import sys
from pathlib import Path

APP_NAME = "AACC"


def default_app_support_dir(platform_name: str, appdata: str | None) -> Path:
    """Per-platform application support directory (pure, testable)."""
    if platform_name == "win32":
        if appdata:
            return Path(appdata) / APP_NAME
        return Path.home() / "AppData" / "Roaming" / APP_NAME
    return Path.home() / "Library" / "Application Support" / APP_NAME


APP_SUPPORT_DIR = default_app_support_dir(sys.platform, os.environ.get("APPDATA"))
DEFAULT_CONFIG_PATH = APP_SUPPORT_DIR / "config.yaml"
DEFAULT_DATABASE_PATH = APP_SUPPORT_DIR / "aacc.db"
DEFAULT_PORT = 17650


def local_api_url(host: str, port: int, path: str) -> str:
    """Format a loopback API URL, including the brackets required for IPv6."""
    normalized_host = host.strip("[]") if ":" in host else host
    display_host = f"[{normalized_host}]" if ":" in normalized_host else normalized_host
    return f"http://{display_host}:{port}{path}"


def resolve_database_path() -> Path:
    """Single source for the runtime database path (app, CLI, doctor)."""
    return Path(os.environ.get("AACC_DATABASE_PATH", DEFAULT_DATABASE_PATH))
