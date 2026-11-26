"""Hashed static assets for the web pages, loaded once at import time.

Each file under ``static/`` is served as ``/assets/<stem>.<sha8><ext>``.
Files in subfolders, such as ``fonts/``, use the same flat URL scheme.
The lookup uses an exact dictionary, so a request path never reaches the filesystem.
"""
from __future__ import annotations

import hashlib
from pathlib import Path

ROOT = Path(__file__).resolve().parent
STATIC_DIR = ROOT / "static"
CONTENT_TYPES = {
    ".css": "text/css; charset=utf-8",
    ".js": "text/javascript; charset=utf-8",
    ".svg": "image/svg+xml",
    ".woff2": "font/woff2",
}
REQUIRED = ("tailplan-mark.svg", "geist-sans.woff2", "geist-mono.woff2", "dashboard.js")


class WebAssetError(RuntimeError):
    """A required static asset is missing, or two assets share one name."""


_SOURCE_TO_HASHED: dict[str, str] = {}
_RELATIVE: dict[str, str] = {}
_HASHED: dict[str, tuple[bytes, str, str]] = {}
_DUPLICATES: list[str] = []


def _load() -> None:
    if not STATIC_DIR.is_dir():
        return
    for path in sorted(STATIC_DIR.rglob("*")):
        content_type = CONTENT_TYPES.get(path.suffix)
        if content_type is None or not path.is_file() or path.is_symlink():
            continue
        if any((STATIC_DIR / part).is_symlink() for part in path.relative_to(STATIC_DIR).parents):
            continue
        name = path.name
        if name in _SOURCE_TO_HASHED:
            _DUPLICATES.append(name)
            continue
        body = path.read_bytes()
        sha8 = hashlib.sha256(body).hexdigest()[:8]
        hashed = f"{path.stem}.{sha8}{path.suffix}"
        _SOURCE_TO_HASHED[name] = hashed
        _RELATIVE[path.relative_to(STATIC_DIR).as_posix()] = name
        _HASHED[hashed] = (body, content_type, f'"{sha8}"')


_load()


def asset_url(base_url: str, name: str) -> str:
    """Return the hashed URL for a static file name or its path, such as ``fonts/geist-sans.woff2``."""
    return f"{base_url.rstrip('/')}/assets/{_SOURCE_TO_HASHED[_RELATIVE.get(name, name)]}"


def lookup_asset(rel_path: str) -> tuple[bytes, str, str] | None:
    """Return ``(body, content_type, etag)`` for a hashed path, or ``None``."""
    return _HASHED.get(rel_path)


def check_ready() -> None:
    """Raise ``WebAssetError`` when a required asset is missing or a file name repeats."""
    missing = [name for name in REQUIRED if name not in _SOURCE_TO_HASHED]
    if missing:
        raise WebAssetError("Missing web assets: " + ", ".join(missing))
    if _DUPLICATES:
        raise WebAssetError("Duplicate web asset names: " + ", ".join(sorted(set(_DUPLICATES))))


def assets_digest() -> str:
    """Return a stable digest of every asset name and byte."""
    digest = hashlib.sha256()
    for name in sorted(_SOURCE_TO_HASHED):
        digest.update(name.encode("utf-8") + b"\0")
        digest.update(_HASHED[_SOURCE_TO_HASHED[name]][0] + b"\0")
    return digest.hexdigest()


def build_id(server_file: str | Path) -> str:
    """Return the 12-character build id for the server file and this package."""
    digest = hashlib.sha256(Path(server_file).read_bytes())
    for source in sorted(ROOT.glob("*.py")):
        digest.update(b"\0" + source.name.encode("utf-8") + b"\0")
        digest.update(source.read_bytes())
    digest.update(b"\0assets\0" + assets_digest().encode("ascii"))
    return digest.hexdigest()[:12]
