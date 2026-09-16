"""Cheap on-disk cache for style notes keyed by example-set fingerprint.

Prefer this over model fine-tuning when the corpus is small (<~1000 reports):
reuse extracted formulation notes for identical example sets instead of
re-running the LLM style-notes stage every time.
"""

from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path
from typing import Optional


_SAFE_KEY = re.compile(r"^[a-f0-9]{16,64}$")


def fingerprint_examples(examples_text: str) -> str:
    """Stable short hash of the examples blob fed to the style-notes prompt."""
    normalized = (examples_text or "").strip().encode("utf-8")
    return hashlib.sha256(normalized).hexdigest()[:32]


def _cache_path(cache_dir: Path, key: str) -> Path:
    if not _SAFE_KEY.match(key):
        raise ValueError("invalid style cache key")
    return cache_dir / f"{key}.json"


def load_style_notes(cache_dir: Path, key: str) -> Optional[str]:
    path = _cache_path(cache_dir, key)
    if not path.is_file():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    notes = data.get("style_notes_md")
    if isinstance(notes, str) and notes.strip():
        return notes
    return None


def save_style_notes(cache_dir: Path, key: str, notes: str) -> None:
    text = (notes or "").strip()
    if not text:
        return
    cache_dir.mkdir(parents=True, exist_ok=True)
    path = _cache_path(cache_dir, key)
    payload = {
        "key": key,
        "style_notes_md": text,
    }
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def delete_style_notes(cache_dir: Path, key: str) -> bool:
    """Remove one cache entry. Returns True if a file was deleted."""
    if not key or not _SAFE_KEY.match(key):
        return False
    path = _cache_path(cache_dir, key)
    if not path.is_file():
        return False
    try:
        path.unlink()
    except OSError:
        return False
    return True


def clear_style_cache(cache_dir: Path) -> int:
    """Delete all on-disk style-note cache files. Returns count removed."""
    if not cache_dir.is_dir():
        return 0
    removed = 0
    for path in cache_dir.glob("*.json"):
        if not _SAFE_KEY.match(path.stem):
            continue
        try:
            path.unlink()
            removed += 1
        except OSError:
            continue
    return removed
