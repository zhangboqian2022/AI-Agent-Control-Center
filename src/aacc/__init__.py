"""AI Agent Control Center."""

import re as _re

__version__ = "1.3.0rc6"


def public_version() -> str:
    """Return the user-facing version (e.g. ``1.3.0-rc.5``) used in DMG names."""
    match = _re.fullmatch(r"(\d+\.\d+\.\d+)rc(\d+)", __version__)
    return f"{match.group(1)}-rc.{match.group(2)}" if match else __version__
