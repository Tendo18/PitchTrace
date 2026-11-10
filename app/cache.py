# A tiny file cache for AI results. Each result is one JSON file named by a
# hash of its inputs, so the same inputs always find the same file.

import hashlib
from pathlib import Path

CACHE_DIR = Path("cache")   # relative to where you start the server


def make_key(*parts: str) -> str:
    return hashlib.sha256("\n".join(parts).encode("utf-8")).hexdigest()[:32]


def get(key: str) -> str | None:
    path = CACHE_DIR / f"{key}.json"
    return path.read_text(encoding="utf-8") if path.exists() else None


def put(key: str, text: str) -> None:
    CACHE_DIR.mkdir(exist_ok=True)
    (CACHE_DIR / f"{key}.json").write_text(text, encoding="utf-8")