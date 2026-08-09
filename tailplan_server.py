#!/usr/bin/env python3
"""Tailplan: tailnet-only static HTML draft publisher."""
from __future__ import annotations

import argparse
import hashlib
import hmac
import html
import json
import os
import re
import secrets
import stat
import sys
import tempfile
import threading
from datetime import UTC, datetime
from html.parser import HTMLParser
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import unquote, urlparse

TITLE_RE = re.compile(
    r"<\s*title\b[^>]*>(.*?)<\s*/\s*title\s*>", re.IGNORECASE | re.DOTALL
)
MAX_HTML_BYTES = 512 * 1024
MAX_REQUEST_BYTES = MAX_HTML_BYTES * 6 + 64 * 1024
MAX_VERSION_NUMBER = 999_999_999
DEFAULT_MAX_HANDLERS = 32
DEFAULT_READ_TIMEOUT = 10.0
DRAFT_ID_RE = re.compile(r"[a-z0-9]{6,32}")
SHA256_RE = re.compile(r"[a-f0-9]{64}")
IDEMPOTENCY_KEY_RE = re.compile(r"[A-Za-z0-9._~-]{1,128}")
VIEWER_ROUTE_RE = re.compile(
    r"/d/([a-z0-9]{6,32})(?:/v/([1-9][0-9]{0,8}))?(?:/content)?/?"
)
SAFE_QUERY_RE = re.compile(r"[A-Za-z0-9._~!$&'()*+,;=:@/?%\[\]-]*")
MAX_IDEMPOTENCY_RECEIPTS = 4096

BLOCKED_TAGS = {"applet", "base", "embed", "form", "frame", "iframe", "link", "object", "script"}
URL_ATTRIBUTES = {
    "action",
    "background",
    "cite",
    "data",
    "formaction",
    "href",
    "poster",
    "src",
    "xlink:href",
}
SECURITY_SENSITIVE_ATTRIBUTES = URL_ATTRIBUTES | {"http-equiv", "srcdoc", "style"}
BLOCKED_URL_SCHEMES = {"file", "javascript", "vbscript"}
CSS_URL_RE = re.compile(
    r"url\s*\(\s*(['\"]?)(.*?)\1\s*\)", re.IGNORECASE | re.DOTALL
)
CSS_IMPORT_RE = re.compile(
    r"@import\s+(['\"])(.*?)\1", re.IGNORECASE | re.DOTALL
)
STORE_LOCK = threading.RLock()
BUILD_ID = hashlib.sha256(Path(__file__).read_bytes()).hexdigest()[:12]

CSS = """
*{box-sizing:border-box}html,body{width:100%;max-width:100%;margin:0;background:#fff;color:#111827;font-family:ui-sans-serif,system-ui,-apple-system,BlinkMacSystemFont,"Segoe UI",sans-serif}.home{width:min(100% - 32px,760px);margin:48px auto;padding:0;line-height:1.55}.home h1{margin:0 0 12px;font-size:clamp(2rem,8vw,40px);line-height:1.1}.home p{color:#374151;font-size:17px;overflow-wrap:anywhere}.home pre{max-width:100%;overflow-x:auto;padding:14px;border:1px solid #d1d5db;background:#fff;border-radius:6px}@media(max-width:640px){.home{width:min(100% - 24px,760px);margin:32px auto}}
""".strip()


def now_iso() -> str:
    return datetime.now(UTC).isoformat().replace("+00:00", "Z")


def sha256_text(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def new_id() -> str:
    return secrets.token_urlsafe(9).replace("-", "").replace("_", "")[:12].lower()


def title_from_html(doc: str, filename: str | None = None) -> str:
    m = TITLE_RE.search(doc)
    if m:
        text = re.sub(r"\s+", " ", html.unescape(re.sub(r"<[^>]*>", "", m.group(1)))).strip()
        if text:
            return text[:140]
    return (filename or "Tailplan Draft")[:140]


def make_links_openable(doc: str) -> str:
    """Add safe external-link attributes without reparsing or corrupting markup."""
    rewriter = _AnchorRewriter(doc)
    rewriter.feed(doc)
    rewriter.close()
    pieces: list[str] = []
    previous_end = 0
    for start, end, replacement in sorted(rewriter.edits):
        pieces.extend((doc[previous_end:start], replacement))
        previous_end = end
    pieces.append(doc[previous_end:])
    return "".join(pieces)


def _start_tag_attributes(raw: str) -> tuple[list[tuple[str, int, int]], int]:
    spans: list[tuple[str, int, int]] = []
    index = 1
    while index < len(raw) and not raw[index].isspace() and raw[index] not in "/>":
        index += 1
    close_at = len(raw)
    while index < len(raw):
        while index < len(raw) and raw[index].isspace():
            index += 1
        if index >= len(raw):
            break
        if raw[index] == ">":
            close_at = index
            break
        if raw[index] == "/" and index + 1 < len(raw) and raw[index + 1] == ">":
            close_at = index
            break
        start = index
        while (
            index < len(raw)
            and not raw[index].isspace()
            and raw[index] not in "/=>"
        ):
            index += 1
        if index == start:
            index += 1
            continue
        name = raw[start:index].lower()
        while index < len(raw) and raw[index].isspace():
            index += 1
        if index < len(raw) and raw[index] == "=":
            index += 1
            while index < len(raw) and raw[index].isspace():
                index += 1
            if index < len(raw) and raw[index] in {'"', "'"}:
                quote = raw[index]
                index += 1
                while index < len(raw) and raw[index] != quote:
                    index += 1
                if index < len(raw):
                    index += 1
            else:
                while (
                    index < len(raw)
                    and not raw[index].isspace()
                    and raw[index] != ">"
                ):
                    index += 1
        spans.append((name, start, index))
    return spans, close_at


class _AnchorRewriter(HTMLParser):
    def __init__(self, doc: str) -> None:
        super().__init__(convert_charrefs=True)
        self.edits: list[tuple[int, int, str]] = []
        self._line_offsets = [0]
        self._line_offsets.extend(
            index + 1 for index, character in enumerate(doc) if character == "\n"
        )

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag.lower() != "a":
            return
        names = {name.lower() for name, _value in attrs}
        if "href" not in names:
            return
        raw = self.get_starttag_text()
        if raw is None:
            return
        spans, close_at = _start_tag_attributes(raw)
        rel_spans = [(start, end) for name, start, end in spans if name == "rel"]
        changes: list[tuple[int, int, str]] = []
        additions = ""
        if rel_spans:
            changes.append((*rel_spans[0], 'rel="noopener noreferrer"'))
            changes.extend((start, end, "") for start, end in rel_spans[1:])
        else:
            additions += ' rel="noopener noreferrer"'
        if "target" not in names:
            additions = ' target="_blank"' + additions
        if additions:
            changes.append((close_at, close_at, additions))
        parts: list[str] = []
        previous_end = 0
        for start, end, replacement in sorted(changes):
            parts.extend((raw[previous_end:start], replacement))
            previous_end = end
        parts.append(raw[previous_end:])
        replacement = "".join(parts)
        line, column = self.getpos()
        start = self._line_offsets[line - 1] + column
        self.edits.append((start, start + len(raw), replacement))


def _has_unsafe_scheme(value: str) -> bool:
    decoded = html.unescape(value)
    for _ in range(3):
        expanded = unquote(decoded)
        if expanded == decoded:
            break
        decoded = expanded
    compact = re.sub(r"[\x00-\x20\x7f]+", "", decoded)
    match = re.match(r"^([a-z][a-z0-9+.-]*):", compact, re.IGNORECASE)
    return bool(match and match.group(1).lower() in BLOCKED_URL_SCHEMES)


def _decode_css_escapes(value: str) -> str:
    """Decode CSS escapes before applying URL allow/block rules."""
    decoded: list[str] = []
    index = 0
    while index < len(value):
        if value[index] != "\\":
            decoded.append(value[index])
            index += 1
            continue
        index += 1
        if index == len(value):
            break
        if value[index] in "\n\f":
            index += 1
            continue
        if value[index] == "\r":
            index += 1
            if index < len(value) and value[index] == "\n":
                index += 1
            continue
        escape_start = index
        while index < len(value) and index - escape_start < 6 and value[index] in "0123456789abcdefABCDEF":
            index += 1
        if index > escape_start:
            codepoint = int(value[escape_start:index], 16)
            if index < len(value) and value[index] in " \t\n\r\f":
                index += 1
            if codepoint == 0 or codepoint > 0x10FFFF or 0xD800 <= codepoint <= 0xDFFF:
                decoded.append("\N{REPLACEMENT CHARACTER}")
            else:
                decoded.append(chr(codepoint))
            continue
        decoded.append(value[index])
        index += 1
    return "".join(decoded)


def _css_has_unsafe_url(value: str) -> bool:
    normalized = _decode_css_escapes(value)
    urls = [match.group(2) for match in CSS_URL_RE.finditer(normalized)]
    urls.extend(match.group(2) for match in CSS_IMPORT_RE.finditer(normalized))
    return any(_has_unsafe_scheme(url) for url in urls)


class _HtmlValidator(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.errors: list[str] = []
        self._style_depth = 0

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        self._inspect_tag(tag, attrs)

    def handle_startendtag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        self._inspect_tag(tag, attrs)

    def handle_endtag(self, tag: str) -> None:
        if tag.lower() == "style" and self._style_depth:
            self._style_depth -= 1

    def handle_data(self, data: str) -> None:
        if self._style_depth and _css_has_unsafe_url(data):
            self.errors.append("Blocked unsafe CSS URL found.")

    def _inspect_tag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        tag = tag.lower()
        attr_values: dict[str, list[str]] = {}
        for name, value in attrs:
            attr_values.setdefault(name.lower(), []).append(value or "")
        if tag == "style":
            self._style_depth += 1
        if tag in BLOCKED_TAGS:
            self.errors.append("Blocked active/embedding tag found.")
        if any(name.startswith("on") or name == "srcdoc" for name in attr_values):
            self.errors.append("Blocked inline event handler or srcdoc attribute found.")
        if any(
            len(values) > 1 and name in SECURITY_SENSITIVE_ATTRIBUTES
            for name, values in attr_values.items()
        ):
            self.errors.append("Blocked duplicate security-sensitive attribute found.")
        if tag == "meta" and any(
            value.strip().lower() == "refresh"
            for value in attr_values.get("http-equiv", [])
        ):
            self.errors.append("Blocked meta refresh tag found.")
        if any(_css_has_unsafe_url(value) for value in attr_values.get("style", [])):
            self.errors.append("Blocked unsafe CSS URL found.")
        for name, values in attr_values.items():
            if name not in URL_ATTRIBUTES:
                continue
            if any(_has_unsafe_scheme(value) for value in values):
                self.errors.append("Blocked unsafe URL protocol found.")


def validate_html(doc: object) -> tuple[bool, list[str], list[str]]:
    errors: list[str] = []
    warnings: list[str] = []
    if not isinstance(doc, str) or not doc.strip():
        return False, ["HTML document is empty."], []
    try:
        encoded = doc.encode("utf-8")
    except UnicodeEncodeError:
        return False, ["HTML document contains invalid Unicode scalar values."], []
    size = len(encoded)
    if size > MAX_HTML_BYTES:
        errors.append(f"HTML document is {size} bytes; maximum is {MAX_HTML_BYTES} bytes.")
    validator = _HtmlValidator()
    validator.feed(doc)
    validator.close()
    errors.extend(validator.errors)
    if not TITLE_RE.search(doc):
        warnings.append("No <title> found; Tailplan will use a generic title.")
    return not errors, sorted(set(errors)), sorted(set(warnings))


class StorageError(RuntimeError):
    """Metadata or draft storage is unavailable or corrupt."""


class IdempotencyConflict(RuntimeError):
    """A request key was reused with a different upload payload."""


class ListenerStartupError(RuntimeError):
    """A configured HTTP listener could not be created safely."""


def _valid_public_url(value: object, draft_id: str) -> bool:
    if not isinstance(value, str):
        return False
    parsed = urlparse(value)
    return (
        parsed.scheme in {"http", "https"}
        and bool(parsed.netloc)
        and parsed.path.endswith(f"/d/{draft_id}")
        and not parsed.params
        and not parsed.query
        and not parsed.fragment
    )


def _validate_metadata_schema(
    data: object,
    root: Path,
    *,
    allow_legacy_current_objects: bool = False,
) -> dict:
    if not isinstance(data, dict) or not isinstance(data.get("drafts"), dict):
        raise StorageError("Metadata has an invalid schema.")
    required_draft_keys = {
        "draftId",
        "title",
        "filename",
        "latestVersionNumber",
        "currentObject",
        "fileSha256",
        "createdAt",
        "updatedAt",
        "publicUrl",
    }
    for draft_id, draft in data["drafts"].items():
        if (
            not isinstance(draft_id, str)
            or DRAFT_ID_RE.fullmatch(draft_id) is None
            or not isinstance(draft, dict)
            or not required_draft_keys.issubset(draft)
            or draft["draftId"] != draft_id
            or not isinstance(draft["title"], str)
            or not 1 <= len(draft["title"]) <= 140
            or (
                draft["filename"] is not None
                and not isinstance(draft["filename"], str)
            )
            or type(draft["latestVersionNumber"]) is not int
            or not 1 <= draft["latestVersionNumber"] <= MAX_VERSION_NUMBER
            or not isinstance(draft["currentObject"], str)
            or not isinstance(draft["fileSha256"], str)
            or SHA256_RE.fullmatch(draft["fileSha256"]) is None
            or not isinstance(draft["createdAt"], str)
            or not draft["createdAt"]
            or not isinstance(draft["updatedAt"], str)
            or not draft["updatedAt"]
            or not _valid_public_url(draft["publicUrl"], draft_id)
        ):
            raise StorageError("Metadata has an invalid draft schema.")
        expected_object = root / "drafts" / draft_id / f"v{draft['latestVersionNumber']}.html"
        current_object_text = draft["currentObject"]
        if current_object_text != str(expected_object):
            current_object = Path(current_object_text)
            expected_tail = (
                "drafts",
                draft_id,
                f"v{draft['latestVersionNumber']}.html",
            )
            if (
                not allow_legacy_current_objects
                or not current_object.is_absolute()
                or any(part in {".", ".."} for part in current_object_text.split(os.sep))
                or current_object.parts[-3:] != expected_tail
            ):
                raise StorageError("Metadata draft object path is invalid.")
    idempotency = data.get("idempotency", {})
    if not isinstance(idempotency, dict):
        raise StorageError("Metadata has an invalid idempotency schema.")
    required_result_keys = {"draftId", "title", "versionNumber", "publicUrl"}
    for request_key, receipt in idempotency.items():
        if (
            not isinstance(request_key, str)
            or IDEMPOTENCY_KEY_RE.fullmatch(request_key) is None
            or not isinstance(receipt, dict)
            or not {"fingerprint", "result"}.issubset(receipt)
            or not isinstance(receipt["fingerprint"], str)
            or SHA256_RE.fullmatch(receipt["fingerprint"]) is None
            or not isinstance(receipt["result"], dict)
            or not required_result_keys.issubset(receipt["result"])
        ):
            raise StorageError("Metadata has an invalid idempotency schema.")
        result = receipt["result"]
        result_draft_id = result["draftId"]
        if (
            not isinstance(result_draft_id, str)
            or result_draft_id not in data["drafts"]
            or not isinstance(result["title"], str)
            or not 1 <= len(result["title"]) <= 140
            or type(result["versionNumber"]) is not int
            or not 1
            <= result["versionNumber"]
            <= data["drafts"][result_draft_id]["latestVersionNumber"]
            or not _valid_public_url(result["publicUrl"], result_draft_id)
        ):
            raise StorageError("Metadata has an invalid idempotency result schema.")
    return data


def _atomic_write_text(path: Path, text: str) -> None:
    fd, tmp_name = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp", dir=path.parent)
    tmp_path = Path(tmp_name)
    try:
        os.fchmod(fd, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8") as stream:
            stream.write(text)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(tmp_path, path)
        directory_fd = os.open(path.parent, os.O_RDONLY)
        try:
            os.fsync(directory_fd)
        finally:
            os.close(directory_fd)
    except Exception:
        try:
            os.close(fd)
        except OSError:
            pass
        tmp_path.unlink(missing_ok=True)
        raise


class Store:
    def __init__(self, root: Path):
        self.root = Path(os.path.abspath(root))
        self.drafts = self.root / "drafts"
        self.meta = self.root / "metadata.json"
        self.backup = self.root / "metadata.json.bak"
        self.drafts.mkdir(parents=True, exist_ok=True)
        self.root.mkdir(parents=True, exist_ok=True)
        os.chmod(self.root, 0o700)
        os.chmod(self.drafts, 0o700)
        with STORE_LOCK:
            self._rebase_metadata_paths_locked()

    def _rebase_metadata_paths_locked(self) -> None:
        if not self.meta.exists():
            return
        try:
            original_text = self.meta.read_text(encoding="utf-8")
            data = json.loads(original_text)
        except (OSError, UnicodeError, json.JSONDecodeError) as exc:
            raise StorageError("Metadata is unreadable or malformed.") from exc
        _validate_metadata_schema(
            data,
            self.root,
            allow_legacy_current_objects=True,
        )
        changed: list[tuple[dict, Path]] = []
        for draft_id, draft in data["drafts"].items():
            canonical = (
                self.drafts
                / draft_id
                / f"v{draft['latestVersionNumber']}.html"
            )
            self._verify_rebase_object(canonical, draft)
            if draft["currentObject"] != str(canonical):
                changed.append((draft, canonical))
        if not changed:
            return
        for draft, canonical in changed:
            draft["currentObject"] = str(canonical)
        _validate_metadata_schema(data, self.root)
        replacement = json.dumps(data, indent=2) + "\n"
        _atomic_write_text(self.backup, original_text)
        _atomic_write_text(self.meta, replacement)

    def _verify_rebase_object(self, path: Path, draft: dict) -> None:
        fd = None
        try:
            if path.is_symlink():
                raise StorageError("Canonical draft object path is invalid.")
            resolved = path.resolve(strict=True)
            if not resolved.is_relative_to(self.drafts.resolve(strict=True)):
                raise StorageError("Canonical draft object path is invalid.")
            flags = os.O_RDONLY | getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NOFOLLOW", 0)
            fd = os.open(path, flags)
            object_stat = os.fstat(fd)
            if not stat.S_ISREG(object_stat.st_mode):
                raise StorageError("Canonical draft object path is invalid.")
            if object_stat.st_size > MAX_HTML_BYTES:
                raise StorageError("Canonical draft object size is invalid.")
            with os.fdopen(fd, "rb") as stream:
                fd = None
                payload = stream.read(MAX_HTML_BYTES + 1)
            if len(payload) != object_stat.st_size:
                raise StorageError("Canonical draft object size changed while reading.")
            doc = payload.decode("utf-8")
        except StorageError:
            raise
        except (OSError, UnicodeError, RuntimeError) as exc:
            raise StorageError(
                "Canonical draft object is unavailable or unreadable."
            ) from exc
        finally:
            if fd is not None:
                os.close(fd)
        if hashlib.sha256(payload).hexdigest() != draft["fileSha256"]:
            raise StorageError("Canonical draft object checksum does not match metadata.")
        valid, _errors, _warnings = validate_html(doc)
        if not valid:
            raise StorageError("Canonical draft object has an invalid HTML schema.")

    def load_meta(self) -> dict:
        with STORE_LOCK:
            if not self.meta.exists():
                return {"drafts": {}}
            try:
                data = json.loads(self.meta.read_text(encoding="utf-8"))
            except (OSError, UnicodeError, json.JSONDecodeError) as exc:
                raise StorageError("Metadata is unreadable or malformed.") from exc
            return _validate_metadata_schema(data, self.root)

    def save_meta(self, data: dict) -> None:
        with STORE_LOCK:
            _validate_metadata_schema(data, self.root)
            if self.meta.exists():
                previous = self.load_meta()
                _atomic_write_text(
                    self.backup, json.dumps(previous, indent=2) + "\n"
                )
            _atomic_write_text(self.meta, json.dumps(data, indent=2) + "\n")

    def _read_current_object(self, draft: dict) -> str:
        path = Path(draft["currentObject"])
        try:
            if path.is_symlink():
                raise StorageError("Recorded draft object path is invalid.")
            resolved = path.resolve(strict=True)
            if not resolved.is_relative_to(self.drafts.resolve(strict=True)) or not resolved.is_file():
                raise StorageError("Recorded draft object path is invalid.")
            doc = resolved.read_text(encoding="utf-8")
        except (OSError, UnicodeError, RuntimeError) as exc:
            raise StorageError("Recorded draft object is unavailable or unreadable.") from exc
        if sha256_text(doc) != draft["fileSha256"]:
            raise StorageError("Recorded draft object checksum does not match metadata.")
        return doc

    def upsert(
        self,
        html_doc: str,
        filename: str | None,
        draft_id: str | None,
        base_url: str,
        request_key: str | None = None,
    ) -> dict:
        with STORE_LOCK:
            return self._upsert_locked(html_doc, filename, draft_id, base_url, request_key)

    def _upsert_locked(
        self,
        html_doc: str,
        filename: str | None,
        draft_id: str | None,
        base_url: str,
        request_key: str | None,
    ) -> dict:
        data = self.load_meta()
        data.setdefault("drafts", {})
        data.setdefault("idempotency", {})
        fingerprint = sha256_text(
            json.dumps(
                {"html": html_doc, "filename": filename, "draftId": draft_id},
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
            )
        )
        if request_key and request_key in data["idempotency"]:
            receipt = data["idempotency"][request_key]
            if receipt.get("fingerprint") != fingerprint:
                raise IdempotencyConflict("Request key was reused with a different payload.")
            return {**receipt["result"], "created": False, "replayed": True}
        creating = not draft_id
        if draft_id and draft_id not in data["drafts"]:
            raise KeyError("Draft not found.")
        if not draft_id:
            draft_id = new_id()
            while draft_id in data["drafts"]:
                draft_id = new_id()
        draft = data["drafts"].get(draft_id, {})
        latest_version = int(draft.get("latestVersionNumber") or 0)
        if latest_version >= MAX_VERSION_NUMBER:
            raise StorageError("Draft has reached the maximum version number.")
        version = latest_version + 1
        title = title_from_html(html_doc, filename)
        object_path = self.drafts / draft_id / f"v{version}.html"
        object_path.parent.mkdir(parents=True, exist_ok=True)
        _atomic_write_text(object_path, html_doc)
        data["drafts"][draft_id] = {
            "draftId": draft_id,
            "title": title,
            "filename": filename,
            "latestVersionNumber": version,
            "currentObject": str(object_path),
            "fileSha256": sha256_text(html_doc),
            "createdAt": draft.get("createdAt") or now_iso(),
            "updatedAt": now_iso(),
            "publicUrl": f"{base_url.rstrip('/')}/d/{draft_id}",
        }
        result = {
            "draftId": draft_id,
            "title": title,
            "versionNumber": version,
            "publicUrl": data["drafts"][draft_id]["publicUrl"],
        }
        if request_key:
            data["idempotency"][request_key] = {"fingerprint": fingerprint, "result": result}
            while len(data["idempotency"]) > MAX_IDEMPOTENCY_RECEIPTS:
                del data["idempotency"][next(iter(data["idempotency"]))]
        self.save_meta(data)
        return {**result, "created": creating, "replayed": False}

    def get(self, draft_id: str, version: int | None = None) -> tuple[dict | None, str | None]:
        with STORE_LOCK:
            data = self.load_meta()
            draft = data.get("drafts", {}).get(draft_id)
            if not draft:
                return None, None
            if version is None or version == draft["latestVersionNumber"]:
                return draft, self._read_current_object(draft)
            path = Path(draft["currentObject"])
            if version is not None:
                path = self.drafts / draft_id / f"v{version}.html"
            if not path.exists():
                return None, None
            return draft, path.read_text(encoding="utf-8")

    def check_ready(self) -> None:
        with STORE_LOCK:
            data = self.load_meta()
            for draft in data.get("drafts", {}).values():
                self._read_current_object(draft)
            probe = self.root / f".ready-{secrets.token_hex(8)}"
            try:
                _atomic_write_text(probe, "ready\n")
            finally:
                probe.unlink(missing_ok=True)


class TailplanHTTPServer(ThreadingHTTPServer):
    """Threaded HTTP server with bounded admission and finite socket reads."""

    store: Store
    token: str
    base_url: str
    redirect_view_base_url: str

    def __init__(
        self,
        server_address: tuple[str, int],
        request_handler_class: type[BaseHTTPRequestHandler],
        *,
        max_handlers: int = DEFAULT_MAX_HANDLERS,
        read_timeout: float = DEFAULT_READ_TIMEOUT,
    ) -> None:
        if max_handlers <= 0:
            raise ValueError("max_handlers must be positive")
        if read_timeout <= 0:
            raise ValueError("read_timeout must be positive")
        self.read_timeout = read_timeout
        self._handler_slots = threading.BoundedSemaphore(max_handlers)
        super().__init__(server_address, request_handler_class)

    def get_request(self):
        request, client_address = super().get_request()
        request.settimeout(self.read_timeout)
        return request, client_address

    def process_request(self, request, client_address) -> None:
        if not self._handler_slots.acquire(blocking=False):
            self.shutdown_request(request)
            return
        try:
            super().process_request(request, client_address)
        except BaseException:
            self._handler_slots.release()
            raise

    def process_request_thread(self, request, client_address) -> None:
        try:
            super().process_request_thread(request, client_address)
        finally:
            self._handler_slots.release()


class Handler(BaseHTTPRequestHandler):
    server_version = "Tailplan/1.0"

    @property
    def store(self) -> Store:
        return self.server.store  # type: ignore[attr-defined]

    @property
    def token(self) -> str:
        return self.server.token  # type: ignore[attr-defined]

    @property
    def base_url(self) -> str:
        configured = self.server.base_url  # type: ignore[attr-defined]
        if configured:
            return configured
        host, port = self.server.server_address[:2]
        return f"http://{host}:{port}"

    def log_message(self, fmt: str, *args: object) -> None:
        sys.stderr.write(f"{self.log_date_time_string()} {fmt % args}\n")

    def send_json(self, status: int, payload: dict) -> None:
        body = json.dumps(payload).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def send_html(self, status: int, body: str) -> None:
        raw = body.encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Content-Security-Policy", "default-src 'none'; style-src 'unsafe-inline'; img-src https: data:; frame-src 'self' about:; base-uri 'none'; form-action 'none'")
        self.send_header("Content-Length", str(len(raw)))
        self.end_headers()
        self.wfile.write(raw)

    def send_document(self, body: str, *, head_only: bool = False) -> None:
        raw = body.encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Referrer-Policy", "no-referrer")
        self.send_header("X-Frame-Options", "DENY")
        self.send_header(
            "Content-Security-Policy",
            "default-src 'none'; script-src 'none'; style-src 'unsafe-inline'; "
            "img-src https: data:; font-src https: data:; connect-src 'none'; "
            "object-src 'none'; frame-src 'none'; frame-ancestors 'none'; "
            "base-uri 'none'; form-action 'none'",
        )
        self.send_header("Content-Length", str(len(raw)))
        self.end_headers()
        if not head_only:
            self.wfile.write(raw)

    def send_view_redirect(self, path: str, query: str) -> None:
        location = f"{self.server.redirect_view_base_url}{path}"  # type: ignore[attr-defined]
        if query and SAFE_QUERY_RE.fullmatch(query):
            location = f"{location}?{query}"
        self.send_response(HTTPStatus.PERMANENT_REDIRECT)
        self.send_header("Location", location)
        self.send_header("Cache-Control", "no-store")
        self.send_header("Content-Length", "0")
        self.end_headers()

    def unauthorized(self) -> None:
        self.send_json(HTTPStatus.UNAUTHORIZED, {"ok": False, "error": "Missing or invalid API token."})

    def is_authorized(self) -> bool:
        hdr = self.headers.get("Authorization", "")
        return hmac.compare_digest(hdr, f"Bearer {self.token}")

    def serve_viewer(
        self,
        match: re.Match[str],
        *,
        path: str,
        query: str,
        head_only: bool = False,
    ) -> None:
        redirect_base = self.server.redirect_view_base_url  # type: ignore[attr-defined]
        if redirect_base:
            self.send_view_redirect(path, query)
            return
        try:
            draft, doc = self.store.get(
                match.group(1), int(match.group(2)) if match.group(2) else None
            )
        except (StorageError, OSError) as exc:
            self.log_message("storage error: %r", exc)
            self.send_json(503, {"ok": False, "error": "Storage unavailable."})
            return
        if not draft or doc is None:
            self.send_html(404, not_found())
            return
        self.send_document(make_links_openable(doc), head_only=head_only)

    def do_GET(self) -> None:
        parsed_target = urlparse(self.path)
        path = parsed_target.path
        if path == "/healthz":
            self.send_json(
                200,
                {"ok": True, "service": "tailplan", "build": BUILD_ID, "time": now_iso()},
            )
            return
        if path == "/readyz":
            try:
                self.store.check_ready()
            except (StorageError, OSError) as exc:
                self.log_message("readiness storage error: %r", exc)
                self.send_json(
                    503,
                    {"ok": False, "service": "tailplan", "build": BUILD_ID},
                )
                return
            self.send_json(
                200,
                {"ok": True, "service": "tailplan", "build": BUILD_ID},
            )
            return
        if path == "/api/me":
            if not self.is_authorized():
                self.unauthorized()
                return
            self.send_json(200, {"ok": True, "accountName": "Tailplan local", "scope": "tailnet"})
            return
        m = VIEWER_ROUTE_RE.fullmatch(path)
        if m:
            self.serve_viewer(m, path=path, query=parsed_target.query)
            return
        if path == "/" or path == "":
            self.send_html(200, home(self.base_url))
            return
        self.send_html(404, not_found())

    def do_HEAD(self) -> None:
        parsed_target = urlparse(self.path)
        path = parsed_target.path
        match = VIEWER_ROUTE_RE.fullmatch(path)
        if match:
            self.serve_viewer(
                match,
                path=path,
                query=parsed_target.query,
                head_only=True,
            )
            return
        self.send_error(HTTPStatus.NOT_IMPLEMENTED, "Unsupported method ('HEAD')")

    def do_POST(self) -> None:
        path = urlparse(self.path).path
        if path != "/api/uploads":
            self.send_json(404, {"ok": False, "error": "Not found."})
            return
        if not self.is_authorized():
            self.unauthorized()
            return
        length_headers = self.headers.get_all("Content-Length") or []
        if len(length_headers) > 1:
            self.send_json(
                400,
                {
                    "ok": False,
                    "error": "Multiple Content-Length headers are not allowed.",
                },
            )
            return
        length_header = length_headers[0] if length_headers else None
        if length_header is None:
            self.send_json(411, {"ok": False, "error": "Content-Length is required."})
            return
        if re.fullmatch(r"[0-9]+", length_header) is None:
            self.send_json(
                400,
                {
                    "ok": False,
                    "error": "Content-Length must contain only ASCII decimal digits.",
                },
            )
            return
        if len(length_header) > len(str(MAX_REQUEST_BYTES)):
            self.send_json(413, {"ok": False, "error": "Upload body too large."})
            return
        length = int(length_header)
        if length == 0:
            self.send_json(400, {"ok": False, "error": "Content-Length must be positive."})
            return
        if length > MAX_REQUEST_BYTES:
            self.send_json(413, {"ok": False, "error": "Upload body too large."})
            return
        try:
            body = self.rfile.read(length)
            if len(body) != length:
                self.send_json(400, {"ok": False, "error": "Upload body was incomplete."})
                return
            payload = json.loads(body.decode("utf-8"))
            if not isinstance(payload, dict):
                self.send_json(400, {"ok": False, "error": "JSON payload must be an object."})
                return
            html_doc = payload.get("html")
            ok, errors, warnings = validate_html(html_doc)
            if not ok:
                self.send_json(422, {"ok": False, "errors": errors, "warnings": warnings})
                return
            filename = payload.get("filename")
            draft_id = payload.get("draftId")
            if filename is not None and not isinstance(filename, str):
                self.send_json(400, {"ok": False, "error": "filename must be a string."})
                return
            if isinstance(filename, str):
                try:
                    filename.encode("utf-8")
                except UnicodeEncodeError:
                    self.send_json(
                        400,
                        {"ok": False, "error": "filename contains invalid Unicode."},
                    )
                    return
            if draft_id is not None and (
                not isinstance(draft_id, str) or DRAFT_ID_RE.fullmatch(draft_id) is None
            ):
                self.send_json(400, {"ok": False, "error": "draftId is invalid."})
                return
            request_key = self.headers.get("Idempotency-Key")
            if request_key is not None and (
                IDEMPOTENCY_KEY_RE.fullmatch(request_key) is None
            ):
                self.send_json(400, {"ok": False, "error": "Idempotency-Key is invalid."})
                return
            result = self.store.upsert(
                html_doc, filename, draft_id, self.base_url, request_key
            )
            created = result.pop("created")
            status = 201 if created else 200
            self.send_json(status, {"ok": True, **result, "warnings": warnings})
        except (UnicodeDecodeError, json.JSONDecodeError):
            self.send_json(400, {"ok": False, "error": "Request body must be valid UTF-8 JSON."})
        except IdempotencyConflict as exc:
            self.send_json(409, {"ok": False, "error": str(exc)})
        except KeyError as e:
            self.send_json(404, {"ok": False, "error": str(e).strip("'")})
        except (StorageError, OSError) as e:
            self.send_json(503, {"ok": False, "error": "Storage unavailable."})
            self.log_message("storage error: %r", e)
        except Exception as e:  # noqa: BLE001 - The request handler returns JSON for unexpected upload errors.
            self.log_message("unexpected upload error: %r", e)
            self.send_json(500, {"ok": False, "error": "Internal server error."})



def page(title: str, body: str) -> str:
    return f"<!doctype html><html lang=\"en\"><head><meta charset=\"utf-8\"><meta name=\"viewport\" content=\"width=device-width, initial-scale=1\"><title>{html.escape(title)}</title><style>{CSS}</style></head><body>{body}</body></html>"


def home(base_url: str) -> str:
    return page("Tailplan", f"<main class=\"home\"><h1>Tailplan</h1><p>Tailplan publishes static HTML drafts inside a tailnet.</p><pre>tailplan-share ./plan.md</pre><p>Base URL: <code>{html.escape(base_url)}</code></p><p>Health: <a href=\"/healthz\">/healthz</a></p></main>")


def not_found() -> str:
    return page("Draft not found", "<main class=\"home\"><h1>Draft not found</h1><p>The requested Tailplan draft is unavailable.</p></main>")


def create_servers(
    primary_address: tuple[str, int],
    proxy_address: tuple[str, int] | None,
    *,
    store: Store,
    token: str,
    base_url: str,
    redirect_view_base_url: str,
) -> tuple[TailplanHTTPServer, TailplanHTTPServer | None]:
    proxy = None
    if proxy_address is not None:
        try:
            proxy = TailplanHTTPServer(proxy_address, Handler)
        except OSError as exc:
            raise ListenerStartupError(
                f"failed to bind proxy listener at {proxy_address[0]}:{proxy_address[1]}: {exc}"
            ) from exc
    try:
        primary = TailplanHTTPServer(primary_address, Handler)
    except BaseException as exc:
        if proxy is not None:
            proxy.server_close()
        if isinstance(exc, OSError):
            raise ListenerStartupError(
                f"failed to bind primary listener at {primary_address[0]}:{primary_address[1]}: {exc}"
            ) from exc
        raise
    configured_base_url = base_url.rstrip("/")
    if not configured_base_url:
        host, port = primary.server_address[:2]
        configured_base_url = f"http://{host}:{port}"
    primary.store = store
    primary.token = token
    primary.base_url = configured_base_url
    primary.redirect_view_base_url = redirect_view_base_url
    if proxy is not None:
        proxy.store = store
        proxy.token = token
        proxy.base_url = configured_base_url
        proxy.redirect_view_base_url = ""
    return primary, proxy


def run_servers(
    primary: TailplanHTTPServer,
    proxy: TailplanHTTPServer | None = None,
) -> None:
    proxy_thread = None
    proxy_thread_started = False
    try:
        if proxy is not None:
            proxy_thread = threading.Thread(
                target=proxy.serve_forever,
                name="tailplan-proxy",
            )
            proxy_thread.start()
            proxy_thread_started = True
        primary.serve_forever()
    finally:
        if proxy is not None:
            if (
                proxy_thread_started
                and proxy_thread is not None
                and proxy_thread.is_alive()
            ):
                proxy.shutdown()
            if proxy_thread_started and proxy_thread is not None:
                proxy_thread.join()
            proxy.server_close()
        primary.server_close()


def _tcp_port(value: str) -> int:
    try:
        port = int(value)
    except ValueError as exc:
        raise argparse.ArgumentTypeError("must be an integer TCP port") from exc
    if not 1 <= port <= 65535:
        raise argparse.ArgumentTypeError("must be between 1 and 65535")
    return port


def _redirect_base_url(value: str) -> str:
    if any(character.isspace() or ord(character) < 32 for character in value):
        raise argparse.ArgumentTypeError("must not contain whitespace or control characters")
    parsed = urlparse(value)
    try:
        _ = parsed.port
    except ValueError as exc:
        raise argparse.ArgumentTypeError("contains an invalid port") from exc
    if (
        parsed.scheme != "https"
        or not parsed.hostname
        or parsed.username is not None
        or parsed.password is not None
        or parsed.params
        or parsed.query
        or parsed.fragment
    ):
        raise argparse.ArgumentTypeError(
            "must be an absolute HTTPS URL without credentials, query, or fragment"
        )
    return value.rstrip("/")


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    ap = argparse.ArgumentParser()
    ap.add_argument("--host", default=os.getenv("TAILPLAN_HOST", "127.0.0.1"))
    ap.add_argument("--port", type=_tcp_port, default=os.getenv("TAILPLAN_PORT", "9127"))
    ap.add_argument("--proxy-host", default=os.getenv("TAILPLAN_PROXY_HOST"))
    ap.add_argument("--proxy-port", type=_tcp_port, default=os.getenv("TAILPLAN_PROXY_PORT"))
    ap.add_argument("--data-dir", default=os.getenv("TAILPLAN_DATA_DIR", str(Path.home() / ".tailplan")))
    ap.add_argument("--token-file", default=os.getenv("TAILPLAN_TOKEN_FILE", str(Path.home() / ".tailplan" / "token")))
    ap.add_argument("--base-url", default=os.getenv("TAILPLAN_BASE_URL", ""))
    ap.add_argument(
        "--redirect-view-base-url",
        type=_redirect_base_url,
        default=os.getenv("TAILPLAN_REDIRECT_VIEW_BASE_URL") or None,
    )
    args = ap.parse_args(argv)
    args.redirect_view_base_url = args.redirect_view_base_url or ""
    if (args.proxy_host is None) != (args.proxy_port is None):
        ap.error("--proxy-host and --proxy-port must be supplied together")
    if args.proxy_host == "":
        ap.error("--proxy-host must not be empty")
    if args.proxy_host is not None and (args.proxy_host, args.proxy_port) == (
        args.host,
        args.port,
    ):
        ap.error("proxy listener must differ from primary listener")
    return args


def main() -> int:
    args = parse_args()
    token_path = Path(args.token_file).expanduser()
    token = token_path.read_text().strip()
    if not token:
        raise SystemExit("empty token file")
    store = Store(Path(args.data_dir).expanduser())
    proxy_address = (
        (args.proxy_host, args.proxy_port) if args.proxy_host is not None else None
    )
    try:
        primary, proxy = create_servers(
            (args.host, args.port),
            proxy_address,
            store=store,
            token=token,
            base_url=args.base_url,
            redirect_view_base_url=args.redirect_view_base_url,
        )
    except ListenerStartupError as exc:
        raise SystemExit(str(exc)) from exc
    primary_host, primary_port = primary.server_address[:2]
    print(f"Tailplan listening on http://{primary_host}:{primary_port}", flush=True)
    if proxy is not None:
        proxy_host, proxy_port = proxy.server_address[:2]
        print(
            f"Tailplan proxy backend listening on http://{proxy_host}:{proxy_port}",
            flush=True,
        )
    try:
        run_servers(primary, proxy)
    except KeyboardInterrupt:
        pass
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
