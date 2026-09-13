#!/usr/bin/env python3
"""Tailplan: tailnet-only static HTML draft publisher."""
from __future__ import annotations

import argparse
import base64
import hashlib
import hmac
import html
import ipaddress
import json
import os
import re
import secrets
import stat
import sys
import tempfile
import threading
import time
from collections import OrderedDict
from datetime import UTC, datetime
from html.parser import HTMLParser
from http import HTTPStatus
from http.cookies import CookieError, SimpleCookie
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, quote, unquote, urlparse

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
    r"/d/([a-z0-9]{6,32})(?:/v/([1-9][0-9]{0,8}))?(?:/(?:content|raw))?/?"
)
SAFE_QUERY_RE = re.compile(r"[A-Za-z0-9._~!$&'()*+,;=:@/?%\[\]-]*")
MAX_IDEMPOTENCY_RECEIPTS = 4096

BLOCKED_TAGS = {"applet", "base", "embed", "form", "frame", "iframe", "link", "object"}
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
SECURITY_SENSITIVE_ATTRIBUTES = URL_ATTRIBUTES | {"http-equiv", "srcdoc", "style", "type"}
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
    return bool(re.search(r"expression\s*\(|behavior\s*:", normalized, re.IGNORECASE)) or any(
        _has_unsafe_scheme(url) for url in urls
    )


class _HtmlValidator(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.errors: list[str] = []
        self._style_depth = 0
        self.has_inline_script = False
        self.external_image_hosts: set[str] = set()

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
        if tag == "script":
            self.has_inline_script = True
            if "src" in attr_values:
                self.errors.append("External script sources are not allowed.")
            if any(value.strip().lower() not in {"", "text/javascript", "application/javascript"}
                   for value in attr_values.get("type", [])):
                self.errors.append("Only inline classic JavaScript is accepted.")
        if tag == "img":
            for value in attr_values.get("src", []):
                try:
                    parsed = urlparse("https:" + value if value.startswith("//") else value)
                    if parsed.scheme in {"http", "https"} and parsed.hostname:
                        self.external_image_hosts.add(parsed.hostname.lower())
                except ValueError:
                    pass
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


def clean_text(value: object, limit: int = 255) -> str | None:
    if not isinstance(value, str):
        return None
    value.encode("utf-8")
    return " ".join(value.split())[:limit] or None


def upload_metadata(value: object) -> dict:
    if not isinstance(value, dict):
        raise TypeError("metadata must be an object.")
    result = {
        field: clean_text(value.get(field), limit)
        for field, limit in {
            "repoOrg": 255, "repoName": 255, "repoHost": 255,
            "gitBranch": 255, "gitCommitSha": 255, "gitCommitSubject": 1000,
            "ciRunUrl": 2048, "ciActor": 255, "ciProvider": 255, "cliVersion": 255,
        }.items()
    }
    result["gitDirty"] = value.get("gitDirty") if type(value.get("gitDirty")) is bool else None
    return result


def _validate_workspace_schema(data: dict) -> None:
    accounts = data.get("accounts", {})
    keys = data.get("apiKeys", {})
    if not isinstance(accounts, dict) or not isinstance(keys, dict):
        raise StorageError("Account storage has an invalid schema.")
    for account_id, account in accounts.items():
        if (not isinstance(account_id, str) or not isinstance(account, dict)
                or not isinstance(account.get("name"), str)):
            raise StorageError("Account storage has an invalid account.")
    for key_id, key in keys.items():
        if (not isinstance(key_id, str) or DRAFT_ID_RE.fullmatch(key_id) is None
                or not isinstance(key, dict)
                or not isinstance(key.get("accountId"), str)
                or key["accountId"] not in {"local", "anonymous", *accounts}
                or not isinstance(key.get("name"), str)
                or not isinstance(key.get("keyHash"), str)
                or SHA256_RE.fullmatch(key["keyHash"]) is None):
            raise StorageError("Account storage has an invalid API key.")
    for draft in data["drafts"].values():
        if not isinstance(draft.get("accountId", "local"), str):
            raise StorageError("Draft account is invalid.")
        versions = draft.get("versions", {})
        if not isinstance(versions, dict):
            raise StorageError("Draft history is invalid.")
        for number, version in versions.items():
            if (not isinstance(number, str) or re.fullmatch(r"[1-9][0-9]{0,8}", number) is None
                    or int(number) > draft["latestVersionNumber"]
                    or not isinstance(version, dict)
                    or version.get("versionNumber") != int(number)
                    or not isinstance(version.get("fileSha256"), str)
                    or SHA256_RE.fullmatch(version["fileSha256"]) is None
                    or type(version.get("fileSize")) is not int
                    or not 0 < version["fileSize"] <= MAX_HTML_BYTES):
                raise StorageError("Draft history has an invalid version.")
        latest = versions.get(str(draft["latestVersionNumber"]))
        if latest and latest["fileSha256"] != draft["fileSha256"]:
            raise StorageError("Current draft and history checksums differ.")


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
    _validate_workspace_schema(data)
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
            doc = resolved.read_bytes().decode("utf-8")
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
        *,
        account_id: str = "local",
        description: str | None = None,
        metadata: dict | None = None,
        audit: dict | None = None,
    ) -> dict:
        with STORE_LOCK:
            return self._upsert_locked(
                html_doc, filename, draft_id, base_url, request_key,
                account_id, description, metadata or {}, audit or {},
            )

    def _upsert_locked(
        self,
        html_doc: str,
        filename: str | None,
        draft_id: str | None,
        base_url: str,
        request_key: str | None,
        account_id: str,
        description: str | None,
        metadata: dict,
        audit: dict,
    ) -> dict:
        data = self.load_meta()
        data.setdefault("drafts", {})
        data.setdefault("idempotency", {})
        if draft_id:
            self._owned_draft(data, draft_id, account_id)
        if request_key and account_id != "local":
            request_key = sha256_text(f"{account_id}:{request_key}")
        fingerprint = sha256_text(
            json.dumps(
                {"html": html_doc, "filename": filename, "draftId": draft_id,
                 "accountId": account_id, "description": description, "metadata": metadata},
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
            )
        )
        if request_key and request_key in data["idempotency"]:
            receipt = data["idempotency"][request_key]
            self._owned_draft(data, receipt["result"]["draftId"], account_id)
            legacy_match = False
            if (account_id == "local" and "versionId" not in receipt["result"]
                    and description is None and all(value is None for value in metadata.values())):
                legacy_match = receipt.get("fingerprint") == sha256_text(json.dumps(
                    {"html": html_doc, "filename": filename, "draftId": draft_id},
                    ensure_ascii=False, sort_keys=True, separators=(",", ":"),
                ))
            if receipt.get("fingerprint") != fingerprint and not legacy_match:
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
        versions = self._version_records(draft) if draft else {}
        stamp = now_iso()
        version_id = new_id()
        signals = _HtmlValidator()
        signals.feed(html_doc)
        signals.close()
        versions[str(version)] = {
            **metadata, **audit,
            "versionId": version_id, "versionNumber": version, "createdAt": stamp,
            "fileSize": len(html_doc.encode("utf-8")), "fileSha256": sha256_text(html_doc),
            "filename": filename, "hasInlineScript": signals.has_inline_script,
            "externalImageHosts": sorted(signals.external_image_hosts),
            "eventType": "draft.created" if creating else "draft.updated",
        }
        object_path = self.drafts / draft_id / f"v{version}.html"
        object_path.parent.mkdir(parents=True, exist_ok=True)
        _atomic_write_text(object_path, html_doc)
        data["drafts"][draft_id] = {
            **draft,
            "draftId": draft_id,
            "title": title,
            "filename": filename,
            "latestVersionNumber": version,
            "currentObject": str(object_path),
            "fileSha256": sha256_text(html_doc),
            "createdAt": draft.get("createdAt") or stamp,
            "updatedAt": stamp,
            "publicUrl": f"{base_url.rstrip('/')}/d/{draft_id}",
            "accountId": account_id,
            "description": clean_text(description, 1000) or draft.get("description"),
            "versions": versions,
            **{field: metadata.get(field) or draft.get(field)
               for field in ("repoHost", "repoOrg", "repoName")},
        }
        result = {
            "draftId": draft_id,
            "title": title,
            "versionNumber": version,
            "publicUrl": data["drafts"][draft_id]["publicUrl"],
            "rawUrl": data["drafts"][draft_id]["publicUrl"] + "/raw",
            "versionId": version_id,
            "fileSha256": sha256_text(html_doc),
            "requestId": audit.get("requestId"),
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
            if not draft or draft.get("deletedAt") or draft.get("disabledAt"):
                return None, None
            if version is None or version == draft["latestVersionNumber"]:
                return draft, self._read_current_object(draft)
            path = Path(draft["currentObject"])
            if version is not None:
                path = self.drafts / draft_id / f"v{version}.html"
            if not path.exists():
                return None, None
            record = draft.get("versions", {}).get(str(version))
            if record:
                return draft, self._read_current_object({
                    "currentObject": str(path), "fileSha256": record["fileSha256"],
                })
            return draft, self._read_legacy_version(path)

    def _read_legacy_version(self, path: Path) -> str:
        try:
            if (path.is_symlink() or not path.resolve(strict=True).is_relative_to(self.drafts)
                    or not path.is_file() or path.stat().st_size > MAX_HTML_BYTES):
                raise StorageError("Historical draft object path is invalid.")
            return path.read_bytes().decode("utf-8")
        except (OSError, UnicodeError, RuntimeError) as exc:
            raise StorageError("Historical draft object is unavailable.") from exc

    def _version_records(self, draft: dict) -> dict:
        records = dict(draft.get("versions", {}))
        for path in (self.drafts / draft["draftId"]).glob("v*.html"):
            match = re.fullmatch(r"v([1-9][0-9]{0,8})\.html", path.name)
            if not match or int(match[1]) > draft["latestVersionNumber"]:
                continue
            number = int(match[1])
            if str(number) in records:
                continue
            doc = self._read_legacy_version(path)
            records[str(number)] = {
                "versionNumber": number, "versionId": f"legacy-{number}",
                "createdAt": datetime.fromtimestamp(path.stat().st_mtime, UTC).isoformat(),
                "fileSize": len(doc.encode("utf-8")), "fileSha256": sha256_text(doc),
                "filename": draft.get("filename"), "legacy": True,
            }
        return records

    @staticmethod
    def _owned_draft(data: dict, draft_id: str, account_id: str) -> dict:
        draft = data["drafts"].get(draft_id)
        if (not draft or draft.get("deletedAt")
                or draft.get("accountId", "local") != account_id):
            raise KeyError("Draft not found.")
        return draft

    def list_drafts(self, account_id: str, base_url: str) -> list[dict]:
        with STORE_LOCK:
            data = self.load_meta()
            drafts = []
            for draft_id, draft in data["drafts"].items():
                if (draft.get("accountId", "local") != account_id or draft.get("deletedAt")):
                    continue
                public_url = f"{base_url.rstrip('/')}/d/{draft_id}"
                drafts.append({
                    **{key: draft.get(key) for key in (
                        "draftId", "title", "description", "repoHost", "repoOrg", "repoName",
                        "latestVersionNumber", "createdAt", "updatedAt", "disabledReason",
                    )},
                    "versionCount": len(self._version_records(draft)),
                    "latestVersionAt": draft["updatedAt"], "disabled": bool(draft.get("disabledAt")),
                    "publicUrl": public_url, "rawUrl": public_url + "/raw",
                })
            return sorted(drafts, key=lambda draft: draft["updatedAt"], reverse=True)

    def draft_detail(self, account_id: str, draft_id: str, base_url: str) -> dict:
        with STORE_LOCK:
            draft = self._owned_draft(self.load_meta(), draft_id, account_id)
            public = next(d for d in self.list_drafts(account_id, base_url) if d["draftId"] == draft_id)
            versions = sorted(self._version_records(draft).values(),
                              key=lambda version: version["versionNumber"], reverse=True)
            return {"ok": True, "draft": public, "versions": versions}

    def change_draft(self, account_id: str, draft_id: str, action: str, reason: str | None) -> None:
        with STORE_LOCK:
            data = self.load_meta()
            draft = self._owned_draft(data, draft_id, account_id)
            stamp = now_iso()
            if action == "delete":
                draft["deletedAt"] = stamp
            elif action == "disable":
                draft.update(disabledAt=stamp, disabledReason=clean_text(reason, 1000) or "Disabled by owner.")
            elif action == "enable":
                draft.update(disabledAt=None, disabledReason=None)
            else:
                raise ValueError("Unknown draft action.")
            draft["updatedAt"] = stamp
            self.save_meta(data)

    def account(self, account_id: str) -> dict | None:
        if account_id == "local":
            return {"accountId": "local", "accountName": "Tailplan local"}
        if account_id == "anonymous":
            return {"accountId": "anonymous", "accountName": "Anonymous"}
        value = self.load_meta().get("accounts", {}).get(account_id)
        return {"accountId": account_id, "accountName": value["name"]} if value else None

    def identity_account(self, login: str, name: str, owner_login: str) -> dict:
        with STORE_LOCK:
            if owner_login and login.casefold() == owner_login.casefold():
                return self.account("local")
            account_id = sha256_text("tailscale:" + login.casefold())[:32]
            data = self.load_meta()
            data.setdefault("accounts", {})[account_id] = {"name": clean_text(name) or login}
            self.save_meta(data)
            return self.account(account_id)

    def authenticate(self, token: str, bootstrap: str) -> dict | None:
        if token and hmac.compare_digest(token.encode(), bootstrap.encode()):
            return {**self.account("local"), "apiKeyId": "bootstrap", "apiKeyName": "Bootstrap"}
        if not token or len(token) > 512:
            return None
        digest = sha256_text(token)
        with STORE_LOCK:
            data = self.load_meta()
            for key_id, key in data.get("apiKeys", {}).items():
                if key.get("revokedAt") or not hmac.compare_digest(key["keyHash"], digest):
                    continue
                last = key.get("lastUsedAt")
                stamp = now_iso()
                if not last or last[:16] != stamp[:16]:
                    key["lastUsedAt"] = stamp
                    self.save_meta(data)
                return {**self.account(key["accountId"]), "apiKeyId": key_id, "apiKeyName": key["name"]}
        return None

    def create_key(self, account_id: str, name: str | None) -> dict:
        with STORE_LOCK:
            data = self.load_meta()
            if self.account(account_id) is None or account_id == "anonymous":
                raise KeyError("Account not found.")
            token = "tp_" + secrets.token_urlsafe(32)
            key_id = new_id()
            key = {"accountId": account_id, "name": clean_text(name) or "CLI API key",
                   "keyHash": sha256_text(token), "createdAt": now_iso(), "lastUsedAt": None}
            data.setdefault("apiKeys", {})[key_id] = key
            self.save_meta(data)
            return {"ok": True, "apiKey": {"id": key_id, "name": key["name"]}, "token": token}

    def list_keys(self, account_id: str) -> list[dict]:
        with STORE_LOCK:
            return sorted([
                {"id": key_id, **{field: key.get(field) for field in ("name", "createdAt", "lastUsedAt")}}
                for key_id, key in self.load_meta().get("apiKeys", {}).items()
                if key["accountId"] == account_id and not key.get("revokedAt")
            ], key=lambda key: key["createdAt"], reverse=True)

    def revoke_key(self, account_id: str, key_id: str) -> None:
        with STORE_LOCK:
            data = self.load_meta()
            key = data.get("apiKeys", {}).get(key_id)
            if not key or key["accountId"] != account_id or key.get("revokedAt"):
                raise KeyError("API key not found.")
            key["revokedAt"] = now_iso()
            self.save_meta(data)

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


class RateLimiter:
    """Keep bounded request counters shared by both listeners."""

    def __init__(self) -> None:
        self.entries: OrderedDict = OrderedDict()
        self.lock = threading.Lock()

    def allow(self, key: str, maximum: int, window: int = 60) -> int:
        with self.lock:
            now = time.monotonic()
            start, count = self.entries.get(key, (now, 0))
            if now - start >= window:
                start, count = now, 0
            self.entries[key] = (start, count + 1)
            self.entries.move_to_end(key)
            while len(self.entries) > 8192:
                self.entries.popitem(last=False)
            return max(1, int(window - (now - start)) + 1) if count >= maximum else 0


def signed_value(payload: dict, secret: str) -> str:
    encoded = base64.urlsafe_b64encode(json.dumps(payload, separators=(",", ":")).encode()).decode().rstrip("=")
    return encoded + "." + hmac.new(secret.encode(), encoded.encode(), hashlib.sha256).hexdigest()


def read_signed(value: str, secret: str) -> dict | None:
    try:
        encoded, signature = value.rsplit(".", 1)
        expected = hmac.new(secret.encode(), encoded.encode(), hashlib.sha256).hexdigest()
        if not hmac.compare_digest(signature, expected):
            return None
        payload = json.loads(base64.urlsafe_b64decode(encoded + "=" * (-len(encoded) % 4)))
        if not isinstance(payload, dict) or type(payload.get("expires")) is not int:
            return None
        return payload if payload["expires"] > time.time() else None
    except (ValueError, UnicodeError, TypeError):
        return None


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
        self.limiter = RateLimiter()
        self.trust_tailscale_identity = False
        self.owner_login = ""
        self.allow_anonymous_uploads = False
        self.upload_ip_limit = 60
        self.upload_key_limit = 30
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

    def send_json(self, status: int, payload: dict, headers: dict | None = None) -> None:
        body = json.dumps(payload).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Content-Length", str(len(body)))
        for name, value in (headers or {}).items():
            self.send_header(name, value)
        self.end_headers()
        if self.command != "HEAD":
            self.wfile.write(body)

    def send_html(self, status: int, body: str, *, web: bool = False, cookie: str | None = None) -> None:
        raw = body.encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        form_policy = "'self'" if web else "'none'"
        self.send_header("Content-Security-Policy", "default-src 'none'; script-src 'none'; "
                         "style-src 'unsafe-inline'; img-src https: data:; base-uri 'none'; "
                         f"frame-ancestors 'none'; form-action {form_policy}")
        self.send_header("Referrer-Policy", "same-origin" if web else "no-referrer")
        self.send_header("X-Frame-Options", "DENY")
        if cookie:
            self.send_header("Set-Cookie", cookie)
        self.send_header("Content-Length", str(len(raw)))
        self.end_headers()
        if self.command != "HEAD":
            self.wfile.write(raw)

    def send_document(self, body: str, *, head_only: bool = False,
                      draft_id: str = "", version: int = 1) -> None:
        raw = body.encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Referrer-Policy", "no-referrer")
        self.send_header("X-Frame-Options", "DENY")
        self.send_header("X-Tailplan-Draft-Id", draft_id)
        self.send_header("X-Tailplan-Draft-Version", str(version))
        self.send_header("X-Postplan-Draft-Id", draft_id)
        self.send_header("X-Postplan-Draft-Version", str(version))
        self.send_header("X-Tailplan-Content-SHA256", sha256_text(body))
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
        return self.api_identity() is not None

    def api_identity(self) -> dict | None:
        header = self.headers.get("Authorization", "")
        match = re.fullmatch(r"Bearer\s+(\S+)", header, re.IGNORECASE)
        return self.store.authenticate(match[1], self.token) if match else None

    def route_path(self) -> str:
        path = urlparse(self.path).path
        prefix = urlparse(self.base_url).path.rstrip("/")
        if prefix and (path == prefix or path.startswith(prefix + "/")):
            path = path[len(prefix):] or "/"
        return path

    def source_ip(self) -> str:
        if self.server.trust_tailscale_identity and self.client_address[0] in {"127.0.0.1", "::1"}:
            value = self.headers.get("X-Forwarded-For", "").split(",")[-1].strip()
            try:
                return str(ipaddress.ip_address(value))
            except ValueError:
                pass
        return self.client_address[0]

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
        self.send_document(doc, head_only=head_only, draft_id=draft["draftId"],
                           version=int(match.group(2)) if match.group(2) else draft["latestVersionNumber"])

    def do_GET(self) -> None:
        parsed_target = urlparse(self.path)
        path = self.route_path()
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
        if path.startswith(("/api/", "/dashboard", "/cli/auth", "/auth/")):
            self.management_request("GET", path)
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
        path = self.route_path()
        match = VIEWER_ROUTE_RE.fullmatch(path)
        if match:
            self.serve_viewer(
                match,
                path=path,
                query=parsed_target.query,
                head_only=True,
            )
            return
        self.do_GET()

    def do_POST(self) -> None:
        path = self.route_path()
        if path != "/api/uploads":
            self.management_request("POST", path)
            return
        try:
            identity = self.api_identity()
        except (StorageError, OSError):
            self.send_json(503, {"ok": False, "error": "Storage unavailable."})
            return
        if identity is None and self.server.allow_anonymous_uploads and not self.headers.get("Authorization"):
            identity = {"accountId": "anonymous", "apiKeyId": "anonymous"}
        if identity is None:
            self.unauthorized()
            return
        if not self.rate_allowed("upload-ip:" + self.source_ip(), self.server.upload_ip_limit):
            return
        if not self.rate_allowed("upload-key:" + identity["apiKeyId"], self.server.upload_key_limit):
            return
        if self.headers.get("Transfer-Encoding"):
            self.send_json(400, {"ok": False, "error": "Transfer-Encoding is not supported."})
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
                html_doc, filename, draft_id, self.base_url, request_key,
                account_id=identity["accountId"],
                description=clean_text(payload.get("description"), 1000),
                metadata=upload_metadata(payload.get("metadata", {})),
                audit={"apiKeyId": identity["apiKeyId"], "sourceIp": self.source_ip(),
                       "userAgent": clean_text(self.headers.get("User-Agent")),
                       "requestId": secrets.token_hex(16)},
            )
            created = result.pop("created")
            status = 201 if created else 200
            self.send_json(status, {"ok": True, **result, "warnings": warnings})
        except (UnicodeDecodeError, json.JSONDecodeError):
            self.send_json(400, {"ok": False, "error": "Request body must be valid UTF-8 JSON."})
        except (ValueError, TypeError, UnicodeEncodeError):
            self.send_json(400, {"ok": False, "error": "Upload metadata is invalid."})
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

    def rate_allowed(self, key: str, maximum: int, window: int = 60) -> bool:
        wait = self.server.limiter.allow(key, maximum, window)
        if wait:
            self.send_json(429, {"ok": False, "error": "Rate limit exceeded."},
                           {"Retry-After": str(wait)})
        return not wait

    def read_control_body(self) -> dict:
        lengths = self.headers.get_all("Content-Length") or []
        if (len(lengths) != 1 or not re.fullmatch(r"[0-9]{1,6}", lengths[0])
                or self.headers.get("Transfer-Encoding")):
            raise ValueError("One valid Content-Length is required.")
        size = int(lengths[0])
        if size > 65536:
            raise ValueError("Request body is too large.")
        body = self.rfile.read(size)
        if len(body) != size:
            raise ValueError("Request body was incomplete.")
        if self.headers.get_content_type() == "application/x-www-form-urlencoded":
            return {key: values[-1] for key, values in parse_qs(body.decode("utf-8"),
                    keep_blank_values=True, max_num_fields=20).items()}
        value = json.loads(body or b"{}")
        if not isinstance(value, dict):
            raise TypeError("Request body must be an object.")
        return value

    def cookie_value(self, name: str) -> str:
        try:
            cookies = SimpleCookie(self.headers.get("Cookie", ""))
            return cookies[name].value if name in cookies else ""
        except CookieError:
            return ""

    def cookie_header(self, name: str, value: str, age: int) -> str:
        cookies = SimpleCookie()
        cookies[name] = value
        cookies[name]["path"] = (urlparse(self.base_url).path.rstrip("/") or "") + "/"
        cookies[name]["httponly"] = True
        cookies[name]["samesite"] = "Lax"
        cookies[name]["max-age"] = age
        if urlparse(self.base_url).scheme == "https":
            cookies[name]["secure"] = True
        return cookies[name].OutputString()

    def session(self) -> dict | None:
        value = read_signed(self.cookie_value("tailplan_session"), self.token)
        if not value or not all(isinstance(value.get(field), str)
                                for field in ("accountId", "apiKeyId", "csrf")):
            return None
        account = self.store.account(value["accountId"])
        if not account:
            return None
        key_id = value["apiKeyId"]
        if key_id == "bootstrap" and value["accountId"] != "local":
            return None
        if key_id == "tailscale":
            if not self.server.trust_tailscale_identity:
                return None
            if value["accountId"] == "local" and value.get("login", "").casefold() != self.server.owner_login.casefold():
                return None
        elif key_id != "bootstrap":
            keys = self.store.list_keys(value["accountId"])
            if not any(key["id"] == key_id for key in keys):
                return None
        return {**value, **account}

    def redirect(self, path: str, cookie: str | None = None) -> None:
        self.send_response(303)
        self.send_header("Location", self.base_url.rstrip("/") + path)
        self.send_header("Cache-Control", "no-store")
        self.send_header("Content-Length", "0")
        if cookie:
            self.send_header("Set-Cookie", cookie)
        self.end_headers()

    def tailscale_login(self) -> tuple[str, str] | None:
        if not self.server.trust_tailscale_identity or self.client_address[0] not in {"127.0.0.1", "::1"}:
            return None
        login = clean_text(self.headers.get("Tailscale-User-Login"))
        if not login:
            return None
        name = clean_text(unquote(self.headers.get("Tailscale-User-Name", ""))) or login
        return login, name

    def sign_in_page(self, next_path: str = "/dashboard") -> None:
        state = {"csrf": secrets.token_hex(24), "expires": int(time.time()) + 600}
        cookie = self.cookie_header("tailplan_login", signed_value(state, self.token), 600)
        body = render_login(self.base_url, state["csrf"], next_path, bool(self.tailscale_login()))
        self.send_html(200, body, web=True, cookie=cookie)

    def valid_form(self, payload: dict, state: dict | None) -> bool:
        origin = self.headers.get("Origin")
        base = urlparse(self.base_url)
        if origin and origin != f"{base.scheme}://{base.netloc}":
            return False
        supplied = payload.get("csrf")
        return bool(state and isinstance(supplied, str) and hmac.compare_digest(
            supplied.encode(), str(state.get("csrf", "")).encode()
        ))

    def do_DELETE(self) -> None:
        self.management_request("DELETE", self.route_path())

    def management_request(self, method: str, path: str) -> None:
        try:
            if path.startswith("/api/"):
                self.management_api(method, path)
            else:
                self.management_web(method, path)
        except (ValueError, TypeError, UnicodeError):
            self.send_json(400, {"ok": False, "error": "Request fields or body are invalid."})
        except KeyError as exc:
            self.send_json(404, {"ok": False, "error": str(exc).strip("'")})
        except (StorageError, OSError) as exc:
            self.log_message("management storage error: %r", exc)
            self.send_json(503, {"ok": False, "error": "Storage unavailable."})

    def management_api(self, method: str, path: str) -> None:
        identity = self.api_identity()
        if identity is None:
            self.unauthorized()
            return
        account_id = identity["accountId"]
        if method == "GET" and path == "/api/me":
            self.send_json(200, {"ok": True, **identity, "scope": "tailnet"})
        elif method == "GET" and path == "/api/drafts":
            self.send_json(200, {"ok": True, "drafts": self.store.list_drafts(account_id, self.base_url)})
        elif method == "GET" and path == "/api/api-keys":
            self.send_json(200, {"ok": True, "apiKeys": self.store.list_keys(account_id)})
        elif method == "POST" and path == "/api/api-keys":
            if self.rate_allowed("keys:" + account_id, 10, 3600):
                payload = self.read_control_body()
                self.send_json(201, self.store.create_key(account_id, payload.get("name")))
        elif (match := re.fullmatch(r"/api/api-keys/([a-z0-9]{6,32})/revoke", path)) and method == "POST":
            self.store.revoke_key(account_id, match[1])
            self.send_json(200, {"ok": True})
        elif match := re.fullmatch(r"/api/drafts/([a-z0-9]{6,32})(?:/(versions|disable|enable))?", path):
            draft_id, action = match.groups()
            if method == "GET" and action in {None, "versions"}:
                self.send_json(200, self.store.draft_detail(account_id, draft_id, self.base_url))
            elif method == "DELETE" and action is None:
                self.store.change_draft(account_id, draft_id, "delete", None)
                self.send_json(200, {"ok": True})
            elif method == "POST" and action in {"disable", "enable"}:
                payload = self.read_control_body()
                self.store.change_draft(account_id, draft_id, action, payload.get("reason"))
                self.send_json(200, {"ok": True})
            else:
                self.send_json(404, {"ok": False, "error": "Not found."})
        else:
            self.send_json(404, {"ok": False, "error": "Not found."})

    def management_web(self, method: str, path: str) -> None:
        known = path in {"/dashboard", "/cli/auth", "/cli/auth/keys", "/auth/sign-in", "/auth/sign-out"}
        draft_match = re.fullmatch(r"/dashboard/drafts/([a-z0-9]{6,32})(?:/(disable|enable|delete))?", path)
        key_match = re.fullmatch(r"/cli/auth/keys/([a-z0-9]{6,32})/revoke", path)
        if not known and not draft_match and not key_match:
            self.send_html(404, not_found())
            return
        if method == "GET" and path == "/auth/sign-in":
            self.sign_in_page()
            return
        if method == "POST" and path == "/auth/sign-in":
            if not self.rate_allowed("login:" + self.source_ip(), 20):
                return
            payload = self.read_control_body()
            state = read_signed(self.cookie_value("tailplan_login"), self.token)
            if not self.valid_form(payload, state):
                self.send_json(403, {"ok": False, "error": "Sign-in expired. Reload and retry."})
                return
            if payload.get("method") == "tailscale" and (login := self.tailscale_login()):
                identity = {**self.store.identity_account(*login, self.server.owner_login),
                            "apiKeyId": "tailscale", "login": login[0]}
            else:
                token = payload.get("token")
                identity = self.store.authenticate(token, self.token) if isinstance(token, str) else None
            if identity is None:
                self.unauthorized()
                return
            session = {**identity, "csrf": secrets.token_hex(24), "expires": int(time.time()) + 30 * 86400}
            next_path = payload.get("next")
            if next_path != "/cli/auth" and not re.fullmatch(r"/dashboard(?:/drafts/[a-z0-9]{6,32})?", str(next_path)):
                next_path = "/dashboard"
            self.redirect(next_path, self.cookie_header("tailplan_session", signed_value(session, self.token), 30 * 86400))
            return
        session = self.session()
        if session is None:
            if method == "GET":
                self.sign_in_page(path)
            else:
                self.unauthorized()
            return
        account_id = session["accountId"]
        if method == "POST":
            payload = self.read_control_body()
            if not self.valid_form(payload, session):
                self.send_json(403, {"ok": False, "error": "Invalid form token. Reload and retry."})
                return
            if path == "/auth/sign-out":
                self.redirect("/", self.cookie_header("tailplan_session", "", 0))
            elif path == "/cli/auth/keys":
                if self.rate_allowed("keys:" + account_id, 10, 3600):
                    result = self.store.create_key(account_id, payload.get("name"))
                    self.send_html(200, render_key(self.base_url, session, result), web=True)
            elif key_match:
                self.store.revoke_key(account_id, key_match[1])
                self.redirect("/cli/auth")
            elif draft_match and draft_match[2]:
                self.store.change_draft(account_id, draft_match[1], draft_match[2], payload.get("reason"))
                target = "/dashboard" if draft_match[2] == "delete" else f"/dashboard/drafts/{draft_match[1]}"
                self.redirect(target)
            else:
                self.send_html(404, not_found())
            return
        if method != "GET":
            self.send_html(404, not_found())
        elif path == "/dashboard":
            self.send_html(200, render_dashboard(self.base_url, session,
                           self.store.list_drafts(account_id, self.base_url)), web=True)
        elif path == "/cli/auth":
            self.send_html(200, render_keys(self.base_url, session,
                           self.store.list_keys(account_id)), web=True)
        elif draft_match and draft_match[2] is None:
            detail = self.store.draft_detail(account_id, draft_match[1], self.base_url)
            self.send_html(200, render_detail(self.base_url, session, detail), web=True)
        else:
            self.send_html(404, not_found())



def page(title: str, body: str) -> str:
    return f"<!doctype html><html lang=\"en\"><head><meta charset=\"utf-8\"><meta name=\"viewport\" content=\"width=device-width, initial-scale=1\"><title>{html.escape(title)}</title><style>{CSS}</style></head><body>{body}</body></html>"


def home(base_url: str) -> str:
    base = html.escape(base_url.rstrip("/"))
    return page("Tailplan", f'<main class="home"><h1>Tailplan</h1>'
                '<p>Publish a draft. Keep its URL. Track every version.</p>'
                '<pre>tailplan upload ./plan.md</pre>'
                f'<p><a href="{base}/dashboard">My drafts</a> · '
                f'<a href="{base}/cli/auth">CLI setup</a></p>'
                f'<p>Health: <a href="{base}/healthz">/healthz</a></p></main>')


def not_found() -> str:
    return page("Draft not found", "<main class=\"home\"><h1>Draft not found</h1><p>The requested Tailplan draft is unavailable.</p></main>")


WEB_CSS = """
:root{color-scheme:light}*{box-sizing:border-box}body{margin:0;background:#f8fafc;color:#172033;font:16px/1.6 system-ui,sans-serif}
header,main{width:min(100% - 32px,1050px);margin:auto}header{display:flex;justify-content:space-between;gap:16px;flex-wrap:wrap;padding:20px 0;border-bottom:1px solid #dce3ec}
nav{display:flex;gap:20px;align-items:center}main{margin:36px auto 80px}h1{font-size:clamp(26px,5vw,36px);line-height:1.2;margin:0 0 20px}h2{font-size:18px;margin:30px 0 10px}
a{color:#245bc0;text-underline-offset:3px;overflow-wrap:anywhere}p{overflow-wrap:anywhere}.muted,small{color:#59687d}.row{padding:18px 20px;background:white;border:1px solid #dce3ec;border-radius:10px;margin:10px 0}
.row h3{margin:0;font-size:18px}.row p{margin:5px 0}.meta{font-size:14px;display:flex;gap:12px;flex-wrap:wrap}.badge{font-size:12px;background:#fef3c7;border-radius:4px;padding:2px 6px}
button,.button{font:inherit;background:#172033;color:white;border:0;border-radius:7px;padding:9px 14px;cursor:pointer;text-decoration:none}button.secondary{background:#e5ebf4;color:#172033}
input{font:inherit;padding:10px;border:1px solid #b6c3d5;border-radius:6px;width:100%;max-width:600px}label{display:block;margin:12px 0 4px}form{margin:12px 0}header form{margin:0}header button{padding:4px 10px}
.actions{display:flex;gap:12px;flex-wrap:wrap}.actions form{margin:0}code{background:#eaf0f8;padding:2px 5px;border-radius:4px;overflow-wrap:anywhere}pre{white-space:pre-wrap;overflow-wrap:anywhere;background:white;padding:16px;border:1px solid #dce3ec;border-radius:8px}
.table-wrap{width:100%;overflow-x:auto}table{width:100%;border-collapse:collapse;background:white}th,td{text-align:left;padding:12px;border-bottom:1px solid #dce3ec;vertical-align:top}th{font-size:13px;color:#59687d}td{min-width:110px;max-width:430px;overflow-wrap:anywhere}
.narrow{max-width:620px}details{margin:10px 0}summary{cursor:pointer}dl{display:grid;grid-template-columns:minmax(90px,1fr) 3fr;gap:6px}dt{color:#59687d}dd{margin:0;overflow-wrap:anywhere}
@media(max-width:600px){header,main{width:calc(100% - 24px)}header{padding:12px 0}nav{gap:12px}.row{padding:14px}main{margin-top:24px}table{min-width:650px}}
""".strip()


def esc(value: object) -> str:
    return html.escape(str(value) if value is not None else "", quote=True)


def csrf_field(value: str) -> str:
    return f'<input type="hidden" name="csrf" value="{esc(value)}">'


def web_page(title: str, body: str, base_url: str, session: dict | None = None) -> str:
    base = esc(base_url.rstrip("/"))
    header = f'<header><nav><strong>Tailplan</strong><a href="{base}/dashboard">My drafts</a><a href="{base}/cli/auth">CLI setup</a></nav>'
    if session:
        header += f'<form method="post" action="{base}/auth/sign-out">{csrf_field(session["csrf"])}'
        header += f'<small>{esc(session["accountName"])}</small> <button class="secondary">Sign out</button></form>'
    header += '</header>'
    return ('<!doctype html><html lang="en"><head><meta charset="utf-8">'
            '<meta name="viewport" content="width=device-width,initial-scale=1">'
            f'<title>{esc(title)} · Tailplan</title><style>{WEB_CSS}</style></head>'
            f'<body>{header}<main>{body}</main></body></html>')


def render_login(base_url: str, csrf: str, next_path: str, tailscale: bool) -> str:
    fields = csrf_field(csrf) + f'<input type="hidden" name="next" value="{esc(next_path)}">'
    action = esc(base_url.rstrip("/")) + "/auth/sign-in"
    body = '<div class="narrow"><h1>Your drafts, in one place</h1><p>Sign in to manage drafts and connect your agents.</p>'
    if tailscale:
        body += f'<form method="post" action="{action}">{fields}<input type="hidden" name="method" value="tailscale"><button>Continue with Tailscale</button></form>'
    body += f'<form method="post" action="{action}">{fields}<label for="token">API key</label>'
    body += '<input id="token" name="token" type="password" autocomplete="current-password" required maxlength="512"><p><button>Sign in with API key</button></p></form>'
    body += '<p class="muted">Use your installed Tailplan token or a named API key.</p></div>'
    return web_page("Sign in", body, base_url)


def external_link(url: object, label: str) -> str:
    try:
        parsed = urlparse(str(url))
        if parsed.scheme not in {"https", "http"} or not parsed.hostname or parsed.username:
            return esc(label)
    except ValueError:
        return esc(label)
    return f'<a href="{esc(url)}" target="_blank" rel="noopener noreferrer">{esc(label)}</a>'


def render_dashboard(base_url: str, session: dict, drafts: list[dict]) -> str:
    groups: dict[tuple, list] = {}
    for draft in drafts:
        key = tuple(draft.get(field) or "" for field in ("repoHost", "repoOrg", "repoName"))
        groups.setdefault(key, []).append(draft)
    body = f'<h1>My drafts <small>{len(drafts)}</small></h1>'
    if not drafts:
        body += '<p>No drafts yet. Publish one with <code>tailplan upload plan.html</code>.</p>'
    for key, members in sorted(groups.items(), key=lambda entry: not bool(entry[0][2])):
        host, org, name = key
        label = f"{org}/{name}" if org and name else "No repository"
        if host and re.fullmatch(r"[A-Za-z0-9.-]+", host) and org and name:
            label = external_link(f"https://{host}/{quote(org, safe='')}/{quote(name, safe='')}", label)
        else:
            label = esc(label)
        body += f'<section><h2>{label}</h2>'
        for draft in members:
            badge = ' <span class="badge">disabled</span>' if draft["disabled"] else ""
            body += f'<article class="row"><h3>{external_link(draft["publicUrl"], draft["title"])}{badge}</h3>'
            if draft["description"]:
                body += f'<p>{esc(draft["description"])}</p>'
            details = esc(base_url.rstrip("/")) + "/dashboard/drafts/" + draft["draftId"]
            body += f'<div class="meta"><a href="{details}">Details and history</a><span>v{draft["latestVersionNumber"]}</span>'
            body += f'<span>{draft["versionCount"]} versions</span><time>{esc(draft["updatedAt"][:16].replace("T", " "))} UTC</time></div></article>'
        body += '</section>'
    return web_page("My drafts", body, base_url, session)


def render_detail(base_url: str, session: dict, detail: dict) -> str:
    draft = detail["draft"]
    body = f'<h1>{esc(draft["title"])}</h1><p>{esc(draft["description"])}</p>'
    body += f'<p>{external_link(draft["publicUrl"], "Open latest draft")} · {external_link(draft["rawUrl"], "Raw HTML")}</p>'
    if draft["disabled"]:
        body += f'<p class="badge">Disabled: {esc(draft["disabledReason"])}</p>'
    action_base = esc(base_url.rstrip("/")) + "/dashboard/drafts/" + draft["draftId"]
    body += '<div class="actions">'
    for action, label in (("enable", "Enable draft") if draft["disabled"] else ("disable", "Disable draft"),):
        body += f'<form method="post" action="{action_base}/{action}">{csrf_field(session["csrf"])}<button class="secondary">{label}</button></form>'
    body += '</div><h2>Version history</h2><div class="table-wrap"><table><thead><tr><th>Version</th><th>Commit</th><th>Ref</th><th>Published</th><th>Source</th></tr></thead><tbody>'
    for version in detail["versions"]:
        number = version["versionNumber"]
        url = draft["publicUrl"] + f"/v/{number}"
        dirty = ' <span class="badge">dirty</span>' if version.get("gitDirty") else ""
        body += f'<tr><td>{external_link(url, "v" + str(number))}<br>{external_link(url + "/raw", "Raw HTML")}</td>'
        body += f'<td>{esc(version.get("gitCommitSubject"))}{dirty}</td><td>{esc(version.get("gitBranch"))}<br><code>{esc((version.get("gitCommitSha") or "")[:12])}</code></td>'
        body += f'<td>{esc(version["createdAt"][:16].replace("T", " "))} UTC</td><td>{version["fileSize"]} bytes'
        if version.get("ciRunUrl"):
            body += "<br>" + external_link(version["ciRunUrl"], "CI run")
        body += '<details><summary>Audit</summary><dl>'
        for field in ("fileSha256", "requestId", "apiKeyId", "sourceIp", "cliVersion", "ciActor", "hasInlineScript", "externalImageHosts"):
            if version.get(field) is not None:
                body += f'<dt>{esc(field)}</dt><dd>{esc(version[field])}</dd>'
        body += '</dl></details></td></tr>'
    body += '</tbody></table></div><details><summary>Delete this draft</summary><p>Deletion removes this draft from the list and disables every version URL.</p>'
    body += f'<form method="post" action="{action_base}/delete">{csrf_field(session["csrf"])}<button>Delete draft</button></form></details>'
    return web_page(draft["title"], body, base_url, session)


def render_keys(base_url: str, session: dict, keys: list[dict]) -> str:
    base = esc(base_url.rstrip("/")) + "/cli/auth/keys"
    body = '<h1>Connect your CLI</h1><p>Run <code>tailplan auth login</code>, then paste a new key into the terminal.</p>'
    body += f'<form method="post" action="{base}">{csrf_field(session["csrf"])}<label for="name">Key name</label>'
    body += '<input id="name" name="name" maxlength="255" placeholder="Laptop agent" required><p><button>Generate API key</button></p></form>'
    body += '<h2>Active keys</h2><div class="table-wrap"><table><thead><tr><th>Name</th><th>Created</th><th>Last used</th><th>Action</th></tr></thead><tbody>'
    for key in keys:
        body += f'<tr><td>{esc(key["name"])}</td><td>{esc(key["createdAt"][:16])}</td><td>{esc((key["lastUsedAt"] or "Never")[:16])}</td>'
        body += f'<td><form method="post" action="{base}/{key["id"]}/revoke">{csrf_field(session["csrf"])}<button class="secondary">Revoke</button></form></td></tr>'
    body += '</tbody></table></div>'
    return web_page("CLI setup", body, base_url, session)


def render_key(base_url: str, session: dict, result: dict) -> str:
    body = '<h1>Your new API key</h1><p>Copy this key now. Tailplan shows each key once.</p>'
    body += f'<label for="key">{esc(result["apiKey"]["name"])}</label><input id="key" readonly value="{esc(result["token"])}">'
    body += '<p>Paste the key into <code>tailplan auth login</code>.</p>'
    body += f'<p><a href="{esc(base_url.rstrip("/"))}/cli/auth">Back to CLI setup</a></p>'
    return web_page("New API key", body, base_url, session)


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
        proxy.limiter = primary.limiter
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
    ap.add_argument("--trust-tailscale-identity", action="store_true",
                    default=os.getenv("TAILPLAN_TRUST_TAILSCALE_IDENTITY") == "1")
    ap.add_argument("--owner-login", default=os.getenv("TAILPLAN_OWNER_LOGIN", ""))
    ap.add_argument("--allow-anonymous-uploads", action="store_true",
                    default=os.getenv("TAILPLAN_ALLOW_ANONYMOUS_UPLOADS") == "1")
    ap.add_argument("--upload-ip-limit", type=int, default=os.getenv("TAILPLAN_UPLOAD_IP_LIMIT", "60"))
    ap.add_argument("--upload-key-limit", type=int, default=os.getenv("TAILPLAN_UPLOAD_KEY_LIMIT", "30"))
    ap.add_argument(
        "--redirect-view-base-url",
        type=_redirect_base_url,
        default=os.getenv("TAILPLAN_REDIRECT_VIEW_BASE_URL") or None,
    )
    args = ap.parse_args(argv)
    args.redirect_view_base_url = args.redirect_view_base_url or ""
    if args.upload_ip_limit < 1 or args.upload_key_limit < 1:
        ap.error("upload limits must be positive")
    if args.trust_tailscale_identity and args.proxy_host not in {"127.0.0.1", "::1"}:
        ap.error("Tailscale identity requires a loopback proxy listener")
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
    for listener in (primary, proxy):
        if listener is not None:
            listener.allow_anonymous_uploads = args.allow_anonymous_uploads
            listener.upload_ip_limit = args.upload_ip_limit
            listener.upload_key_limit = args.upload_key_limit
    if proxy is not None:
        proxy.trust_tailscale_identity = args.trust_tailscale_identity
        proxy.owner_login = args.owner_login
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
