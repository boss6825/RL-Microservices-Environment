from __future__ import annotations

import os
from pathlib import Path

from dotenv import load_dotenv


def load_local_env() -> None:
    """Load the first .env file found in common local project locations."""
    package_root = Path(__file__).resolve().parent
    candidates = (
        Path.cwd() / ".env",
        package_root / ".env",
        package_root.parent / ".env",
    )

    seen: set[Path] = set()
    for candidate in candidates:
        resolved = candidate.resolve()
        if resolved in seen:
            continue
        seen.add(resolved)
        if resolved.is_file():
            load_dotenv(resolved, override=False)
            break


def get_env(name: str, *aliases: str) -> str | None:
    for key in (name, *aliases):
        value = os.getenv(key)
        if value:
            return value
    return None
