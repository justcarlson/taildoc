from __future__ import annotations

import contextlib
import fcntl
import hashlib
import http.client
import http.server
import importlib.machinery
import importlib.util
import io
import json
import multiprocessing
import os
import queue
import socket
import stat
import subprocess
import sys
import tempfile
import threading
import time
import unittest
import urllib.error
import urllib.request
from pathlib import Path
from typing import ClassVar
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
SHARE = ROOT / "bin" / "tailplan-share"
loader = importlib.machinery.SourceFileLoader("tailplan_share_reliability", str(SHARE))
spec = importlib.util.spec_from_loader(loader.name, loader)
assert spec and spec.loader
share = importlib.util.module_from_spec(spec)
spec.loader.exec_module(share)


class FakeResponse:
    def __init__(self, body: dict | str, *, status: int = 200, headers: dict[str, str] | None = None):
        self.status = status
        self.headers = headers or {"Content-Type": "application/json"}
        text = json.dumps(body) if isinstance(body, dict) else body
        self._body = text.encode("utf-8")

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, traceback):
        return False

    def read(self) -> bytes:
        return self._body


def http_error(status: int, body: dict) -> urllib.error.HTTPError:
    return urllib.error.HTTPError(
        "https://tailplan.test/api/uploads",
        status,
        "failed",
        {"Content-Type": "application/json"},
        io.BytesIO(json.dumps(body).encode("utf-8")),
    )


def stop_process(process: subprocess.Popen[str]) -> None:
    """Stop a child process and deterministically close its captured pipes."""
    if process.poll() is None:
        process.terminate()
    try:
        process.communicate(timeout=2)
    except subprocess.TimeoutExpired:
        process.kill()
        process.communicate(timeout=2)


class SourceIdentityTests(unittest.TestCase):
    def test_same_basename_sources_have_distinct_generated_paths_and_mapping_keys(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            home = root / "home"
            first = root / "one" / "report.md"
            second = root / "two" / "report.md"
            first.parent.mkdir()
            second.parent.mkdir()
            first.write_text("# First\nmarker-one", encoding="utf-8")
            second.write_text("# Second\nmarker-two", encoding="utf-8")

            first_output = share.build_html(first, home=home)
            second_output = share.build_html(second, home=home)

            self.assertNotEqual(first_output, second_output)
            self.assertEqual(home / ".tailplan" / "generated", first_output.parent)
            self.assertIn(hashlib.sha256(str(first.resolve()).encode()).hexdigest()[:12], first_output.name)
            self.assertIn(hashlib.sha256(str(second.resolve()).encode()).hexdigest()[:12], second_output.name)
            mappings = {"files": {str(first.resolve()): {"draftId": "first12"}}}
            self.assertEqual("first12", share.mapped_draft_id(mappings, first))
            self.assertIsNone(share.mapped_draft_id(mappings, second))

    def test_markdown_legacy_generated_mapping_requires_explicit_migration(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            source = root / "notes.md"
            source.write_text("# Notes", encoding="utf-8")
            mapping_path = root / ".tailplan" / "drafts.json"
            mapping_path.parent.mkdir(parents=True)
            legacy_generated = root / ".tailplan" / "generated" / "notes.html"
            mapping_path.write_text(
                json.dumps({"files": {str(legacy_generated): {"draftId": "legacy"}}}),
                encoding="utf-8",
            )
            uploads = []
            stderr = io.StringIO()

            exit_code = share.publish(
                source,
                "https://tailplan.test",
                home=root,
                uploader=lambda *args: uploads.append(args),
                verifier=lambda *args: self.fail("must not verify"),
                stdout=io.StringIO(),
                stderr=stderr,
            )

            self.assertEqual(1, exit_code)
            self.assertEqual([], uploads)
            self.assertIn("legacy", stderr.getvalue())
            self.assertEqual(1, stderr.getvalue().count("--draft legacy"))

    def test_explicit_legacy_migration_writes_source_mapping_and_preserves_lineage(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            source = root / "notes.md"
            source.write_text("# Notes", encoding="utf-8")
            mapping_path = root / ".tailplan" / "drafts.json"
            mapping_path.parent.mkdir(parents=True)
            legacy_generated = root / ".tailplan" / "generated" / "notes.html"
            mapping_path.write_text(
                json.dumps({"files": {str(legacy_generated): {"draftId": "legacy"}}}),
                encoding="utf-8",
            )
            requested_drafts = []

            def uploader(html_path, base_url, draft_id):
                requested_drafts.append(draft_id)
                return {
                    "ok": True,
                    "draftId": "legacy",
                    "publicUrl": "https://tailplan.test/d/legacy",
                    "versionNumber": len(requested_drafts) + 1,
                }

            for explicit_draft_id in ("legacy", None):
                exit_code = share.publish(
                    source,
                    "https://tailplan.test",
                    home=root,
                    explicit_draft_id=explicit_draft_id,
                    uploader=uploader,
                    verifier=lambda *args: None,
                    stdout=io.StringIO(),
                    stderr=io.StringIO(),
                )
                self.assertEqual(0, exit_code)

            self.assertEqual(["legacy", "legacy"], requested_drafts)
            mappings = share.load_mappings(mapping_path)
            self.assertEqual("legacy", share.mapped_draft_id(mappings, source))
            self.assertEqual("legacy", share.mapped_draft_id(mappings, legacy_generated))
            self.assertEqual(3, mappings["files"][str(source.resolve())]["versionNumber"])

    def test_direct_html_mapping_remains_compatible(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            source = Path(td) / "page.html"
            source.write_text("<title>Page</title>", encoding="utf-8")
            mappings = {"files": {str(source.resolve()): {"draftId": "existing"}}}

            self.assertEqual("existing", share.mapped_draft_id(mappings, source))


class MappingTests(unittest.TestCase):
    def test_new_explicit_and_default_select_expected_draft(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            source = Path(td) / "page.html"
            source.write_text("<title>Page</title>", encoding="utf-8")
            mappings = {"files": {str(source.resolve()): {"draftId": "mapped"}}}

            self.assertEqual("mapped", share.choose_draft_id(mappings, source))
            self.assertIsNone(share.choose_draft_id(mappings, source, force_new=True))
            self.assertEqual(
                "explicit",
                share.choose_draft_id(mappings, source, explicit_draft_id="explicit"),
            )

    def test_atomic_mapping_update_preserves_other_readable_entries(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            mapping_path = root / "state" / "drafts.json"
            source = root / "source" / "report.md"
            source.parent.mkdir()
            source.write_text("# Report", encoding="utf-8")
            mapping_path.parent.mkdir()
            original = {
                "files": {
                    "/legacy/generated/report.html": {"draftId": "legacy"},
                    "/other/source.html": {"draftId": "other"},
                },
                "futureField": {"preserve": True},
            }
            mapping_path.write_text(json.dumps(original), encoding="utf-8")
            real_replace = os.replace
            real_fsync = os.fsync

            with (
                patch.object(share.os, "replace", wraps=real_replace) as replace_spy,
                patch.object(share.os, "fsync", wraps=real_fsync) as fsync_spy,
            ):
                share.update_mapping(
                    mapping_path,
                    source,
                    {"draftId": "new-id", "publicUrl": "https://example.test/d/new-id", "versionNumber": 1},
                )

            saved = json.loads(mapping_path.read_text(encoding="utf-8"))
            self.assertEqual(original["futureField"], saved["futureField"])
            self.assertEqual("legacy", saved["files"]["/legacy/generated/report.html"]["draftId"])
            self.assertEqual("other", saved["files"]["/other/source.html"]["draftId"])
            self.assertEqual("new-id", saved["files"][str(source.resolve())]["draftId"])
            self.assertEqual(0o600, stat.S_IMODE(mapping_path.stat().st_mode))
            self.assertGreaterEqual(fsync_spy.call_count, 1)
            replace_spy.assert_called_once()
            temporary, destination = map(Path, replace_spy.call_args.args)
            self.assertEqual(mapping_path.parent, temporary.parent)
            self.assertNotEqual(mapping_path, temporary)
            self.assertEqual(mapping_path, destination)
            self.assertEqual([], list(mapping_path.parent.glob("*.tmp")))
            self.assertEqual([], list(mapping_path.parent.glob(".*.tmp")))

    def test_mapping_update_failure_releases_secure_lock_and_cleans_temporary_file(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            mapping_path = root / "state" / "drafts.json"
            source = root / "source.html"
            source.write_text("source", encoding="utf-8")

            with (
                patch.object(share.os, "replace", side_effect=OSError("replace failed")),
                self.assertRaisesRegex(OSError, "replace failed"),
            ):
                share.update_mapping(
                    mapping_path,
                    source,
                    {
                        "draftId": "draft123",
                        "publicUrl": "https://tailplan.test/d/draft123",
                        "versionNumber": 1,
                    },
                )

            lock_path = mapping_path.with_name(f".{mapping_path.name}.lock")
            self.assertEqual(0o600, stat.S_IMODE(lock_path.stat().st_mode))
            lock_fd = os.open(lock_path, os.O_RDWR)
            try:
                fcntl.flock(lock_fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
                fcntl.flock(lock_fd, fcntl.LOCK_UN)
            finally:
                os.close(lock_fd)
            self.assertFalse(mapping_path.exists())
            self.assertEqual([], list(mapping_path.parent.glob(".*.tmp")))

    def test_mapping_update_changes_only_selected_source(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            mapping_path = root / "drafts.json"
            first = root / "one.html"
            second = root / "two.html"
            first.write_text("one", encoding="utf-8")
            second.write_text("two", encoding="utf-8")
            mapping_path.write_text(
                json.dumps(
                    {
                        "schemaVersion": 7,
                        "files": {
                            str(first.resolve()): {
                                "draftId": "firstold",
                                "publicUrl": "https://old.test/d/firstold",
                                "versionNumber": 1,
                                "futureEntryField": {"keep": True},
                            },
                            str(second.resolve()): {"draftId": "secondold"},
                        },
                    }
                ),
                encoding="utf-8",
            )

            share.update_mapping(
                mapping_path,
                first,
                {"draftId": "firstnew", "publicUrl": "https://example.test/d/firstnew", "versionNumber": 2},
            )

            saved = share.load_mappings(mapping_path)
            self.assertEqual("firstnew", share.mapped_draft_id(saved, first))
            self.assertEqual("secondold", share.mapped_draft_id(saved, second))
            self.assertEqual(7, saved["schemaVersion"])
            self.assertEqual({"keep": True}, saved["files"][str(first.resolve())]["futureEntryField"])
            self.assertEqual(2, saved["files"][str(first.resolve())]["versionNumber"])

    def test_two_process_mapping_updates_preserve_both_entries_and_future_fields(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            mapping_path = root / "state" / "drafts.json"
            mapping_path.parent.mkdir()
            first = root / "first.html"
            second = root / "second.html"
            first.write_text("first", encoding="utf-8")
            second.write_text("second", encoding="utf-8")
            mapping_path.write_text(
                json.dumps(
                    {
                        "files": {
                            str(first.resolve()): {
                                "draftId": "firstold",
                                "future": {"owner": "first"},
                            },
                            str(second.resolve()): {
                                "draftId": "secondold",
                                "future": {"owner": "second"},
                            },
                        },
                        "futureTopLevel": {"keep": True},
                    }
                ),
                encoding="utf-8",
            )
            context = multiprocessing.get_context("fork")
            ready = context.Queue()
            release = context.Event()

            def update_in_child(source: Path, result: dict) -> None:
                real_load = share.load_mappings

                def synchronized_load(path: Path) -> dict:
                    mappings = real_load(path)
                    ready.put(str(source))
                    if not release.wait(timeout=5):
                        raise RuntimeError("mapping update test synchronization timed out")
                    return mappings

                share.__dict__["load_mappings"] = synchronized_load
                share.update_mapping(mapping_path, source, result)

            processes = (
                context.Process(
                    target=update_in_child,
                    args=(
                        first,
                        {
                            "draftId": "firstnew",
                            "publicUrl": "https://tailplan.test/d/firstnew",
                            "versionNumber": 2,
                        },
                    ),
                ),
                context.Process(
                    target=update_in_child,
                    args=(
                        second,
                        {
                            "draftId": "secondnew",
                            "publicUrl": "https://tailplan.test/d/secondnew",
                            "versionNumber": 2,
                        },
                    ),
                ),
            )
            for process in processes:
                process.start()
            try:
                ready.get(timeout=5)
                try:
                    ready.get(timeout=0.5)
                except queue.Empty:
                    pass
            finally:
                release.set()
            for process in processes:
                process.join(timeout=5)
                if process.is_alive():
                    process.terminate()
                    process.join(timeout=2)
                self.assertEqual(0, process.exitcode)

            saved = share.load_mappings(mapping_path)
            self.assertEqual("firstnew", saved["files"][str(first.resolve())]["draftId"])
            self.assertEqual("secondnew", saved["files"][str(second.resolve())]["draftId"])
            self.assertEqual({"owner": "first"}, saved["files"][str(first.resolve())]["future"])
            self.assertEqual({"owner": "second"}, saved["files"][str(second.resolve())]["future"])
            self.assertEqual({"keep": True}, saved["futureTopLevel"])

    def test_two_process_same_source_first_create_has_one_lineage_and_serial_versions(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            home = root / "home"
            source = root / "report.md"
            source.write_text("# Concurrent create", encoding="utf-8")
            context = multiprocessing.get_context("fork")
            entered = context.Queue()
            release = context.Event()
            versions = context.Value("i", 0)
            creates = context.Value("i", 0)
            state_lock = context.Lock()

            def publish_in_child() -> None:
                def uploader(html_path, base_url, draft_id):
                    entered.put(draft_id)
                    if not release.wait(timeout=5):
                        raise RuntimeError("same-source create synchronization timed out")
                    with state_lock:
                        versions.value += 1
                        version = versions.value
                        if draft_id is None:
                            creates.value += 1
                            draft = f"lineage{creates.value}"
                        else:
                            draft = draft_id
                    return {
                        "ok": True,
                        "draftId": draft,
                        "publicUrl": f"https://tailplan.test/d/{draft}",
                        "versionNumber": version,
                    }

                exit_code = share.publish(
                    source,
                    "https://tailplan.test",
                    home=home,
                    uploader=uploader,
                    verifier=lambda *args: None,
                    stdout=io.StringIO(),
                    stderr=io.StringIO(),
                )
                if exit_code != 0:
                    raise RuntimeError(f"publish failed with {exit_code}")

            first = context.Process(target=publish_in_child)
            second = context.Process(target=publish_in_child)
            first.start()
            self.assertIsNone(entered.get(timeout=5))
            second.start()
            second_before_release = False
            second_draft = None
            try:
                second_draft = entered.get(timeout=0.4)
                second_before_release = True
            except queue.Empty:
                pass
            finally:
                release.set()
            if not second_before_release:
                second_draft = entered.get(timeout=5)
            for process in (first, second):
                process.join(timeout=5)
                if process.is_alive():
                    process.terminate()
                    process.join(timeout=2)
                self.assertEqual(0, process.exitcode)

            self.assertFalse(
                second_before_release, "second publish reached upload before first released lock"
            )
            self.assertEqual("lineage1", second_draft)
            self.assertEqual(1, creates.value)
            self.assertEqual(2, versions.value)
            mapping_path = home / ".tailplan" / "drafts.json"
            saved = share.load_mappings(mapping_path)
            self.assertEqual("lineage1", share.mapped_draft_id(saved, source))
            self.assertEqual(2, saved["files"][str(source.resolve())]["versionNumber"])
            lock_path = share.source_publish_lock_path(mapping_path, source)
            self.assertEqual(mapping_path.parent, lock_path.parent)
            self.assertEqual(0o600, stat.S_IMODE(lock_path.stat().st_mode))
            lock_fd = os.open(lock_path, os.O_RDWR)
            try:
                fcntl.flock(lock_fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
                fcntl.flock(lock_fd, fcntl.LOCK_UN)
            finally:
                os.close(lock_fd)

    def test_two_process_same_source_existing_draft_updates_one_lineage_serially(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            home = root / "home"
            source = root / "report.md"
            source.write_text("# Concurrent update", encoding="utf-8")
            mapping_path = home / ".tailplan" / "drafts.json"
            mapping_path.parent.mkdir(parents=True)
            mapping_path.write_text(
                json.dumps(
                    {
                        "files": {
                            str(source.resolve()): {
                                "draftId": "existing123",
                                "publicUrl": "https://tailplan.test/d/existing123",
                                "versionNumber": 7,
                            }
                        }
                    }
                ),
                encoding="utf-8",
            )
            context = multiprocessing.get_context("fork")
            transactions = context.Queue()
            uploads = context.Queue()
            verifications = context.Queue()
            release_first_verification = context.Event()
            versions = context.Value("i", 7)
            creates = context.Value("i", 0)
            state_lock = context.Lock()

            def publish_in_child(label: str) -> None:
                real_source_publish_lock = share.source_publish_lock

                @contextlib.contextmanager
                def observed_source_publish_lock(selected_mapping_path, selected_source):
                    transactions.put(("attempted", label))
                    with real_source_publish_lock(selected_mapping_path, selected_source):
                        transactions.put(("acquired", label))
                        yield

                share.__dict__["source_publish_lock"] = observed_source_publish_lock
                published_version = None

                def uploader(html_path, base_url, draft_id):
                    nonlocal published_version
                    with state_lock:
                        if draft_id is None:
                            creates.value += 1
                        versions.value += 1
                        published_version = versions.value
                    uploads.put((label, draft_id, published_version))
                    return {
                        "ok": True,
                        "draftId": draft_id or f"unexpected{creates.value}",
                        "publicUrl": f"https://tailplan.test/d/{draft_id}",
                        "versionNumber": published_version,
                    }

                def verifier(public_url, expected_html):
                    verifications.put((label, public_url, published_version))
                    if published_version == 8 and not release_first_verification.wait(timeout=5):
                        raise RuntimeError("same-source update synchronization timed out")

                exit_code = share.publish(
                    source,
                    "https://tailplan.test",
                    home=home,
                    uploader=uploader,
                    verifier=verifier,
                    stdout=io.StringIO(),
                    stderr=io.StringIO(),
                )
                if exit_code != 0:
                    raise RuntimeError(f"publish failed with {exit_code}")

            first = context.Process(target=publish_in_child, args=("first",))
            second = context.Process(target=publish_in_child, args=("second",))
            first.start()
            self.assertEqual(("attempted", "first"), transactions.get(timeout=5))
            self.assertEqual(("acquired", "first"), transactions.get(timeout=5))
            self.assertEqual(("first", "existing123", 8), uploads.get(timeout=5))
            self.assertEqual(
                ("first", "https://tailplan.test/d/existing123", 8),
                verifications.get(timeout=5),
            )

            second.start()
            self.assertEqual(("attempted", "second"), transactions.get(timeout=5))
            lock_path = share.source_publish_lock_path(mapping_path, source)
            lock_exists_during_verification = lock_path.exists()
            lock_held_during_verification = False
            if lock_exists_during_verification:
                lock_fd = os.open(lock_path, os.O_RDWR)
                try:
                    try:
                        fcntl.flock(lock_fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
                    except BlockingIOError:
                        lock_held_during_verification = True
                    else:
                        fcntl.flock(lock_fd, fcntl.LOCK_UN)
                finally:
                    os.close(lock_fd)

            release_first_verification.set()
            self.assertEqual(("acquired", "second"), transactions.get(timeout=5))
            self.assertEqual(("second", "existing123", 9), uploads.get(timeout=5))
            self.assertEqual(
                ("second", "https://tailplan.test/d/existing123", 9),
                verifications.get(timeout=5),
            )
            for process in (first, second):
                process.join(timeout=5)
                if process.is_alive():
                    process.terminate()
                    process.join(timeout=2)
                self.assertEqual(0, process.exitcode)

            self.assertTrue(lock_exists_during_verification)
            self.assertTrue(
                lock_held_during_verification,
                "per-source lock was not held across first publish verification",
            )
            self.assertEqual(0, creates.value)
            self.assertEqual(9, versions.value)
            saved = share.load_mappings(mapping_path)
            self.assertEqual({str(source.resolve())}, set(saved["files"]))
            self.assertEqual("existing123", share.mapped_draft_id(saved, source))
            self.assertEqual(9, saved["files"][str(source.resolve())]["versionNumber"])

    def test_mapping_load_fails_closed_for_corrupt_or_wrong_schema(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            mapping_path = Path(td) / "drafts.json"
            cases = ("{broken", "[]", '{"files": []}')
            for content in cases:
                with self.subTest(content=content):
                    mapping_path.write_text(content, encoding="utf-8")
                    with self.assertRaises(share.MappingFailure):
                        share.load_mappings(mapping_path)
                    self.assertEqual(content, mapping_path.read_text(encoding="utf-8"))

    def test_mapping_load_treats_only_missing_file_as_empty(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            mapping_path = Path(td) / "missing.json"
            self.assertEqual({"files": {}}, share.load_mappings(mapping_path))
            with (
                patch.object(Path, "read_text", side_effect=PermissionError("denied")),
                self.assertRaisesRegex(share.MappingFailure, "read"),
            ):
                share.load_mappings(mapping_path)

    def test_invalid_selected_mapping_entry_blocks_upload(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            source = root / "page.html"
            source.write_text("<title>Page</title>", encoding="utf-8")
            mapping_path = root / "home" / ".tailplan" / "drafts.json"
            mapping_path.parent.mkdir(parents=True)
            invalid_entries = (
                "not-an-object",
                {},
                {"draftId": 123},
                {"draftId": ""},
                {"draftId": "short"},
                {"draftId": "BAD123"},
                {"draftId": "../../draft123"},
                {"draftId": "a" * 33},
                {"draftId": "valid123", "publicUrl": 123},
                {"draftId": "valid123", "versionNumber": "1"},
                {"draftId": "valid123", "versionNumber": True},
            )
            for entry in invalid_entries:
                with self.subTest(entry=entry):
                    mapping_path.write_text(
                        json.dumps({"files": {str(source.resolve()): entry}}), encoding="utf-8"
                    )
                    uploads = []
                    stderr = io.StringIO()
                    exit_code = share.publish(
                        source,
                        "https://tailplan.test",
                        home=root / "home",
                        uploader=lambda *args, uploads=uploads: uploads.append(args),
                        verifier=lambda *args: self.fail("must not verify"),
                        stdout=io.StringIO(),
                        stderr=stderr,
                    )
                    self.assertEqual(1, exit_code)
                    self.assertEqual([], uploads)
                    self.assertIn("mapping", stderr.getvalue().lower())

    def test_update_mapping_does_not_overwrite_malformed_state(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            mapping_path = root / "drafts.json"
            source = root / "page.html"
            source.write_text("page", encoding="utf-8")
            mapping_path.write_text("{malformed", encoding="utf-8")

            with self.assertRaises(share.MappingFailure):
                share.update_mapping(
                    mapping_path,
                    source,
                    {
                        "draftId": "new123",
                        "publicUrl": "https://tailplan.test/d/new123",
                        "versionNumber": 1,
                    },
                )

            self.assertEqual("{malformed", mapping_path.read_text(encoding="utf-8"))

    def test_publish_new_explicit_and_default_mapping_semantics(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            home = root / "home"
            source = root / "report.md"
            source.write_text("# Report\nunique publish marker", encoding="utf-8")
            requested_drafts = []
            returned_ids = iter(("mapped1", "mapped1", "fresh2", "chosen3"))

            def uploader(html_path, base_url, draft_id):
                requested_drafts.append(draft_id)
                returned = next(returned_ids)
                return {
                    "ok": True,
                    "draftId": returned,
                    "publicUrl": f"https://tailplan.test/d/{returned}",
                    "versionNumber": 1 if draft_id is None else 2,
                }

            options = ({}, {}, {"force_new": True}, {"explicit_draft_id": "chosen3"})
            for option in options:
                exit_code = share.publish(
                    source,
                    "https://tailplan.test",
                    home=home,
                    uploader=uploader,
                    verifier=lambda public_url, expected_html: None,
                    stdout=io.StringIO(),
                    stderr=io.StringIO(),
                    **option,
                )
                self.assertEqual(0, exit_code)

            self.assertEqual([None, "mapped1", None, "chosen3"], requested_drafts)
            mappings = share.load_mappings(home / ".tailplan" / "drafts.json")
            self.assertEqual("chosen3", share.mapped_draft_id(mappings, source))

    def test_stale_implicit_mapping_falls_back_once_to_create_and_replaces_mapping(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            home = root / "home"
            source = root / "report.md"
            source.write_text("# Report\nstale mapping recovery", encoding="utf-8")
            mapping_path = home / ".tailplan" / "drafts.json"
            mapping_path.parent.mkdir(parents=True)
            mapping_path.write_text(
                json.dumps({"files": {str(source.resolve()): {"draftId": "gone123"}}}),
                encoding="utf-8",
            )
            requested_drafts = []
            verified = []

            def uploader(html_path, base_url, draft_id):
                requested_drafts.append(draft_id)
                if draft_id == "gone123":
                    raise share.UploadFailure("missing draft", "Draft not found.", status=404)
                return {
                    "ok": True,
                    "draftId": "fresh123",
                    "publicUrl": "https://tailplan.test/d/fresh123",
                    "versionNumber": 1,
                }

            exit_code = share.publish(
                source,
                "https://tailplan.test",
                home=home,
                uploader=uploader,
                verifier=lambda public_url, expected_html: verified.append(public_url),
                stdout=io.StringIO(),
                stderr=io.StringIO(),
            )

            self.assertEqual(0, exit_code)
            self.assertEqual(["gone123", None], requested_drafts)
            self.assertEqual(["https://tailplan.test/d/fresh123"], verified)
            self.assertEqual("fresh123", share.mapped_draft_id(share.load_mappings(mapping_path), source))

    def test_explicit_missing_draft_fails_closed_without_fallback(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            source = Path(td) / "page.html"
            source.write_text("<title>Page</title>", encoding="utf-8")
            requested_drafts = []
            stderr = io.StringIO()

            def uploader(html_path, base_url, draft_id):
                requested_drafts.append(draft_id)
                raise share.UploadFailure("missing draft", "Draft not found.", status=404)

            exit_code = share.publish(
                source,
                "https://tailplan.test",
                explicit_draft_id="explicit123",
                home=Path(td) / "home",
                uploader=uploader,
                verifier=lambda public_url, expected_html: self.fail("must not verify"),
                stdout=io.StringIO(),
                stderr=stderr,
            )

            self.assertEqual(1, exit_code)
            self.assertEqual(["explicit123"], requested_drafts)
            self.assertIn("missing draft", stderr.getvalue())

    def test_stale_mapping_fallback_uses_a_new_logical_idempotency_key(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            home = root / "home"
            source = root / "page.html"
            source.write_text("<title>Page</title>", encoding="utf-8")
            mapping_path = home / ".tailplan" / "drafts.json"
            mapping_path.parent.mkdir(parents=True)
            mapping_path.write_text(
                json.dumps({"files": {str(source.resolve()): {"draftId": "stale123"}}}),
                encoding="utf-8",
            )
            token_path = root / "token"
            token_path.write_text("safe-token", encoding="utf-8")
            requests = []
            real_upload = share.upload

            def opener(request, timeout):
                requests.append(request)
                payload = json.loads(request.data.decode("utf-8"))
                if payload["draftId"] == "stale123":
                    raise http_error(404, {"ok": False, "error": "Draft not found."})
                return FakeResponse(
                    {
                        "ok": True,
                        "draftId": "fresh123",
                        "publicUrl": "https://tailplan.test/d/fresh123",
                        "versionNumber": 1,
                    }
                )

            def routed_upload(
                html_path,
                base_url,
                draft_id,
                *,
                html_doc,
                allow_insecure_http,
            ):
                return real_upload(
                    html_path,
                    base_url,
                    draft_id,
                    html_doc=html_doc,
                    token_path=token_path,
                    opener=opener,
                    sleeper=lambda _delay: None,
                    allow_insecure_http=allow_insecure_http,
                )

            with (
                patch.object(share, "upload", side_effect=routed_upload),
                patch.object(share.secrets, "token_urlsafe", side_effect=("update-key", "create-key")),
            ):
                exit_code = share.publish(
                    source,
                    "https://tailplan.test",
                    home=home,
                    verifier=lambda *args: None,
                    stdout=io.StringIO(),
                    stderr=io.StringIO(),
                )

            self.assertEqual(0, exit_code)
            self.assertEqual(2, len(requests))
            self.assertEqual(
                ["update-key", "create-key"],
                [request.get_header("Idempotency-key") for request in requests],
            )


class UploadRetryTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.html_path = Path(self.temp_dir.name) / "unicode.html"
        self.html_path.write_text("<!doctype html><title>Café 🚀</title><p>naïve</p>", encoding="utf-8")
        self.token_path = Path(self.temp_dir.name) / "token"
        self.token_path.write_text("secret-token\n", encoding="utf-8")

    def tearDown(self) -> None:
        self.temp_dir.cleanup()

    @staticmethod
    def result() -> dict:
        return {
            "ok": True,
            "draftId": "draft123",
            "publicUrl": "https://tailplan.test/d/draft123",
            "versionNumber": 1,
        }

    def test_upload_uses_utf8_unescaped_json(self) -> None:
        requests = []

        def opener(request, timeout):
            requests.append(request)
            return FakeResponse(self.result(), status=201)

        share.upload(
            self.html_path,
            "https://tailplan.test",
            None,
            token_path=self.token_path,
            opener=opener,
            sleeper=lambda _delay: None,
            idempotency_key="request-key",
        )

        body = requests[0].data
        self.assertIn("Café 🚀".encode(), body)
        self.assertNotIn(b"\\u", body)
        self.assertEqual("request-key", requests[0].get_header("Idempotency-key"))

    def test_main_real_truncated_503_is_bounded_and_safe_in_human_and_json_modes(self) -> None:
        idempotency_keys = []
        complete_body = b'{"ok":false,"error":"partial-server-body-marker must never be echoed"}'
        partial_body = b'{"ok":false,"error":"partial-server-body-marker'

        class TruncatedErrorHandler(http.server.BaseHTTPRequestHandler):
            def do_POST(self) -> None:
                content_length = int(self.headers.get("Content-Length", "0"))
                self.rfile.read(content_length)
                idempotency_keys.append(self.headers.get("Idempotency-Key"))
                self.send_response(503)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(complete_body)))
                self.end_headers()
                self.wfile.write(partial_body)
                self.wfile.flush()
                self.close_connection = True

            def log_message(self, format, *args) -> None:
                pass

        server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), TruncatedErrorHandler)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            with tempfile.TemporaryDirectory() as td:
                root = Path(td)
                home = root / "home"
                token_path = home / ".tailplan" / "token"
                token_path.parent.mkdir(parents=True)
                token_path.write_text("truncated-error-token", encoding="utf-8")
                source = root / "page.html"
                source.write_text("<title>Truncated error</title>", encoding="utf-8")
                base_command = [
                    sys.executable,
                    str(SHARE),
                    str(source),
                    "--base-url",
                    f"http://127.0.0.1:{server.server_port}",
                ]
                environment = {**os.environ, "HOME": str(home)}

                for json_output in (False, True):
                    with self.subTest(json_output=json_output):
                        attempt_start = len(idempotency_keys)
                        result = subprocess.run(
                            base_command + (["--json"] if json_output else []),
                            capture_output=True,
                            text=True,
                            timeout=10,
                            env=environment,
                            check=False,
                        )
                        attempt_keys = idempotency_keys[attempt_start:]

                        self.assertEqual(1, result.returncode)
                        self.assertEqual(3, len(attempt_keys))
                        self.assertEqual(1, len(set(attempt_keys)))
                        self.assertTrue(attempt_keys[0])
                        self.assertNotIn("Traceback", result.stdout + result.stderr)
                        self.assertNotIn("partial-server-body-marker", result.stdout + result.stderr)
                        if json_output:
                            self.assertEqual("", result.stderr)
                            self.assertEqual(1, len(result.stdout.splitlines()))
                            self.assertEqual(
                                {
                                    "ok": False,
                                    "category": "server unavailable",
                                    "error": "Upload failed (server unavailable): HTTP 503",
                                },
                                json.loads(result.stdout),
                            )
                        else:
                            self.assertEqual("", result.stdout)
                            self.assertEqual(
                                "Upload failed (server unavailable): HTTP 503\n",
                                result.stderr,
                            )
        finally:
            server.shutdown()
            server.server_close()
            thread.join(timeout=2)

    def test_main_json_upload_redirect_does_not_reach_cross_origin_destination(self) -> None:
        destination_requests = []

        class DestinationHandler(http.server.BaseHTTPRequestHandler):
            def do_GET(self) -> None:
                destination_requests.append(dict(self.headers))
                self.send_response(200)
                self.end_headers()

            def do_POST(self) -> None:
                destination_requests.append(dict(self.headers))
                self.send_response(200)
                self.end_headers()

            def log_message(self, format, *args) -> None:
                pass

        destination = http.server.ThreadingHTTPServer(("127.0.0.1", 0), DestinationHandler)
        destination_url = f"http://127.0.0.1:{destination.server_port}/stolen"

        class RedirectHandler(http.server.BaseHTTPRequestHandler):
            def do_POST(self) -> None:
                self.send_response(302)
                self.send_header("Location", destination_url)
                self.end_headers()

            def log_message(self, format, *args) -> None:
                pass

        redirector = http.server.ThreadingHTTPServer(("127.0.0.1", 0), RedirectHandler)
        threads = [
            threading.Thread(target=server.serve_forever, daemon=True)
            for server in (destination, redirector)
        ]
        for thread in threads:
            thread.start()
        try:
            with tempfile.TemporaryDirectory() as td:
                root = Path(td)
                home = root / "home"
                token_path = home / ".tailplan" / "token"
                token_path.parent.mkdir(parents=True)
                token_path.write_text("redirect-secret", encoding="utf-8")
                source = root / "page.html"
                source.write_text("<title>Redirect</title>", encoding="utf-8")
                stdout = io.StringIO()
                stderr = io.StringIO()
                with (
                    patch.object(share.Path, "home", return_value=home),
                    patch.object(share.sys, "stdout", stdout),
                    patch.object(share.sys, "stderr", stderr),
                ):
                    exit_code = share.main(
                        [
                            str(source),
                            "--base-url",
                            f"http://127.0.0.1:{redirector.server_port}",
                            "--json",
                        ]
                    )

                self.assertEqual(1, exit_code)
                self.assertEqual("", stderr.getvalue())
                self.assertEqual("protocol", json.loads(stdout.getvalue())["category"])
                self.assertEqual(1, len(stdout.getvalue().splitlines()))
                self.assertNotIn("Traceback", stdout.getvalue())
                self.assertEqual([], destination_requests)
        finally:
            for server in (redirector, destination):
                server.shutdown()
                server.server_close()
            for thread in threads:
                thread.join(timeout=2)

    def test_committed_then_disconnected_retry_reuses_idempotency_key(self) -> None:
        requests = []
        outcomes = [urllib.error.URLError("connection closed after commit"), FakeResponse(self.result())]

        def opener(request, timeout):
            requests.append(request)
            outcome = outcomes.pop(0)
            if isinstance(outcome, Exception):
                raise outcome
            return outcome

        result = share.upload(
            self.html_path,
            "https://tailplan.test",
            None,
            token_path=self.token_path,
            opener=opener,
            sleeper=lambda _delay: None,
            idempotency_key="single-strong-key",
        )

        self.assertEqual("draft123", result["draftId"])
        self.assertEqual(2, len(requests))
        self.assertEqual(
            ["single-strong-key", "single-strong-key"],
            [request.get_header("Idempotency-key") for request in requests],
        )

    def test_generated_idempotency_key_is_cryptographically_created_once(self) -> None:
        requests = []
        outcomes = [urllib.error.URLError("disconnected"), FakeResponse(self.result())]

        def opener(request, timeout):
            requests.append(request)
            outcome = outcomes.pop(0)
            if isinstance(outcome, Exception):
                raise outcome
            return outcome

        with patch.object(
            share.secrets, "token_urlsafe", return_value="generated-strong-request-key"
        ) as key_spy:
            share.upload(
                self.html_path,
                "https://tailplan.test",
                None,
                token_path=self.token_path,
                opener=opener,
                sleeper=lambda _delay: None,
            )

        key_spy.assert_called_once_with(32)
        self.assertEqual(
            ["generated-strong-request-key"] * 2,
            [request.get_header("Idempotency-key") for request in requests],
        )

    def test_429_retries_exact_attempts_with_one_idempotency_key_and_retry_after(self) -> None:
        requests = []
        delays = []
        first = http_error(429, {"ok": False, "error": "Slow down."})
        first.headers["Retry-After"] = "2"
        second = http_error(429, {"ok": False, "error": "Still slow."})
        second.headers["Retry-After"] = "61"
        third = http_error(429, {"ok": False, "error": "Still throttled."})
        third.headers["Retry-After"] = "not-a-clean-integer"
        outcomes = [first, second, third, FakeResponse(self.result())]

        def opener(request, timeout):
            requests.append(request)
            outcome = outcomes.pop(0)
            if isinstance(outcome, Exception):
                raise outcome
            return outcome

        result = share.upload(
            self.html_path,
            "https://tailplan.test",
            None,
            token_path=self.token_path,
            opener=opener,
            sleeper=delays.append,
            idempotency_key="one-key-for-all-429-attempts",
            max_attempts=4,
        )

        self.assertEqual("draft123", result["draftId"])
        self.assertEqual(4, len(requests))
        self.assertEqual(
            ["one-key-for-all-429-attempts"] * 4,
            [request.get_header("Idempotency-key") for request in requests],
        )
        self.assertEqual([2, 0.5, 1.0], delays)

    def test_does_not_retry_4xx(self) -> None:
        attempts = 0

        def opener(request, timeout):
            nonlocal attempts
            attempts += 1
            raise http_error(422, {"ok": False, "error": "Blocked active content"})

        with self.assertRaises(share.UploadFailure) as raised:
            share.upload(
                self.html_path,
                "https://tailplan.test",
                None,
                token_path=self.token_path,
                opener=opener,
                sleeper=lambda _delay: None,
            )

        self.assertEqual(1, attempts)
        self.assertEqual("validation", raised.exception.category)
        self.assertNotIn("secret-token", str(raised.exception))
        self.assertNotIn("naïve", str(raised.exception))

    def test_5xx_retries_are_bounded(self) -> None:
        attempts = 0

        def opener(request, timeout):
            nonlocal attempts
            attempts += 1
            raise http_error(503, {"ok": False, "error": "Storage unavailable."})

        with self.assertRaises(share.UploadFailure) as raised:
            share.upload(
                self.html_path,
                "https://tailplan.test",
                None,
                token_path=self.token_path,
                opener=opener,
                sleeper=lambda _delay: None,
                max_attempts=3,
            )

        self.assertEqual(3, attempts)
        self.assertEqual("server unavailable", raised.exception.category)

    def test_transport_retries_are_bounded(self) -> None:
        attempts = 0

        def opener(request, timeout):
            nonlocal attempts
            attempts += 1
            raise urllib.error.URLError("network down")

        with self.assertRaises(share.UploadFailure) as raised:
            share.upload(
                self.html_path,
                "https://tailplan.test",
                None,
                token_path=self.token_path,
                opener=opener,
                sleeper=lambda _delay: None,
                max_attempts=3,
            )

        self.assertEqual(3, attempts)
        self.assertEqual("server unavailable", raised.exception.category)

    def test_truncated_upload_retries_with_same_key_and_respects_attempt_bound(self) -> None:
        requests = []
        outcomes = [
            http.client.IncompleteRead(b'{"ok":', 20),
            FakeResponse(self.result()),
        ]

        def opener(request, timeout):
            requests.append(request)
            outcome = outcomes.pop(0)
            if isinstance(outcome, Exception):
                raise outcome
            return outcome

        result = share.upload(
            self.html_path,
            "https://tailplan.test",
            None,
            token_path=self.token_path,
            opener=opener,
            sleeper=lambda _delay: None,
            idempotency_key="truncated-response-key",
            max_attempts=2,
        )

        self.assertEqual("draft123", result["draftId"])
        self.assertEqual(2, len(requests))
        self.assertEqual(
            ["truncated-response-key", "truncated-response-key"],
            [request.get_header("Idempotency-key") for request in requests],
        )

    def test_http_statuses_have_actionable_error_categories(self) -> None:
        cases = {
            401: "authentication",
            400: "validation",
            413: "size",
            404: "missing draft",
            409: "conflict",
            503: "server unavailable",
        }
        for status, category in cases.items():
            with self.subTest(status=status):

                def opener(request, timeout, status=status):
                    raise http_error(status, {"ok": False, "error": "safe server detail"})

                with self.assertRaises(share.UploadFailure) as raised:
                    share.upload(
                        self.html_path,
                        "https://tailplan.test",
                        "draft123",
                        token_path=self.token_path,
                        opener=opener,
                        sleeper=lambda _delay: None,
                        max_attempts=1,
                    )
                self.assertEqual(category, raised.exception.category)
                self.assertIn("safe server detail", str(raised.exception))

    def test_validation_errors_list_is_bounded_and_redacts_token_or_html(self) -> None:
        def opener(request, timeout):
            raise http_error(
                422,
                {
                    "ok": False,
                    "errors": [
                        "secret-token",
                        "<script>naïve request html</script>",
                        "x" * 500,
                        "must-not-include-fourth",
                    ],
                },
            )

        with self.assertRaises(share.UploadFailure) as raised:
            share.upload(
                self.html_path,
                "https://tailplan.test",
                None,
                token_path=self.token_path,
                opener=opener,
                max_attempts=1,
            )

        self.assertEqual("validation", raised.exception.category)
        detail = raised.exception.detail
        self.assertNotIn("secret-token", detail)
        self.assertNotIn("naïve request html", detail)
        self.assertNotIn("must-not-include-fourth", detail)
        self.assertIn("[redacted]", detail)
        self.assertIn("xxx", detail)
        self.assertLessEqual(len(detail), 500)

    def test_real_isolated_server_422_errors_shape_is_reported(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            token_path = root / "token"
            token_path.write_text("isolated-token\n", encoding="utf-8")
            data_dir = root / "data"
            data_dir.mkdir()
            html_path = root / "blocked.html"
            html_path.write_text("<title>Blocked</title><script>alert(1)</script>", encoding="utf-8")
            with socket.socket() as sock:
                sock.bind(("127.0.0.1", 0))
                port = sock.getsockname()[1]
            process = subprocess.Popen(
                [
                    sys.executable,
                    str(ROOT / "tailplan_server.py"),
                    "--host",
                    "127.0.0.1",
                    "--port",
                    str(port),
                    "--data-dir",
                    str(data_dir),
                    "--token-file",
                    str(token_path),
                    "--base-url",
                    f"http://127.0.0.1:{port}",
                ],
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
            )
            try:
                base_url = f"http://127.0.0.1:{port}"
                for _ in range(50):
                    try:
                        with urllib.request.urlopen(base_url + "/healthz", timeout=1):
                            break
                    except urllib.error.URLError:
                        if process.poll() is not None:
                            self.fail("isolated Tailplan server exited during startup")
                        time.sleep(0.05)
                else:
                    self.fail("isolated Tailplan server did not become healthy")

                with self.assertRaises(share.UploadFailure) as raised:
                    share.upload(
                        html_path,
                        base_url,
                        None,
                        token_path=token_path,
                        max_attempts=1,
                    )

                self.assertEqual("validation", raised.exception.category)
                self.assertIn("Blocked active/embedding tag found.", raised.exception.detail)
                self.assertNotIn("<script>", raised.exception.detail)
                self.assertNotIn("isolated-token", raised.exception.detail)
            finally:
                stop_process(process)


class UrlValidationTests(unittest.TestCase):
    def test_base_url_normalizes_paths_and_rejects_malformed_or_ambiguous_values(self) -> None:
        valid = {
            "https://tailplan.test/": "https://tailplan.test",
            "https://tailplan.test/team/tailplan///": "https://tailplan.test/team/tailplan",
            "http://[::1]:9127/tailplan/": "http://[::1]:9127/tailplan",
        }
        for value, expected in valid.items():
            with self.subTest(valid=value):
                self.assertEqual(expected, share.validate_base_url(value))

        invalid = (
            "http://[::1",
            "https://tailplan.test:not-a-port",
            "https://tailplan.test:70000",
            "https://user:secret@tailplan.test",
            "https://tailplan.test/base?query=1",
            "https://tailplan.test/base#fragment",
            "https://tailplan.test/white space",
            "https://tailplan.test/control\x1f",
            "https:////tailplan.test",
            "https://tailplan.test\\@evil.test",
        )
        for value in invalid:
            with self.subTest(invalid=value), self.assertRaises(share.ConfigurationFailure):
                share.validate_base_url(value)

    def test_base_url_requires_https_outside_safe_private_ranges(self) -> None:
        safe_http_urls = (
            "http://localhost:9127",
            "http://127.0.0.1:9127",
            "http://[::1]:9127",
            "http://100.64.0.1:9127",
            "http://[fd7a:115c:a1e0::1]:9127",
        )
        for value in safe_http_urls:
            with self.subTest(safe=value):
                self.assertEqual(value, share.validate_base_url(value))

        with self.assertRaisesRegex(
            share.ConfigurationFailure,
            "must use HTTPS",
        ):
            share.validate_base_url("http://tailplan.internal.example")

        self.assertEqual(
            "http://tailplan.internal.example",
            share.validate_base_url(
                "http://tailplan.internal.example",
                allow_insecure_http=True,
            ),
        )

    def test_cli_requires_the_explicit_insecure_http_override(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            source = Path(td) / "draft.md"
            source.write_text("# Draft\n", encoding="utf-8")
            stderr = io.StringIO()
            with (
                patch.object(share.sys, "stderr", stderr),
                patch.object(share, "publish", return_value=0) as publish,
            ):
                self.assertEqual(
                    2,
                    share.main(
                        [
                            str(source),
                            "--base-url",
                            "http://tailplan.internal.example",
                        ]
                    ),
                )
                publish.assert_not_called()
                self.assertIn("must use HTTPS", stderr.getvalue())

                self.assertEqual(
                    0,
                    share.main(
                        [
                            str(source),
                            "--base-url",
                            "http://tailplan.internal.example",
                            "--allow-insecure-http",
                        ]
                    ),
                )
                self.assertTrue(publish.call_args.kwargs["allow_insecure_http"])

    def test_public_url_accepts_only_equivalent_loopback_hosts(self) -> None:
        result = {
            "ok": True,
            "draftId": "draft123",
            "publicUrl": "http://localhost:9127/team/tailplan/d/draft123",
            "versionNumber": 1,
        }

        self.assertIs(
            result,
            share.validate_upload_result(
                result,
                "http://127.25.50.75:9127/team/tailplan",
            ),
        )
        with self.assertRaises(share.ProtocolFailure):
            share.validate_upload_result(
                {
                    **result,
                    "publicUrl": "http://192.0.2.10:9127/team/tailplan/d/draft123",
                },
                "http://127.0.0.1:9127/team/tailplan",
                allow_insecure_http=True,
            )


    def test_public_url_must_be_the_exact_draft_endpoint_under_the_base_path(self) -> None:
        base_url = "https://tailplan.test/team/tailplan/"
        valid = {
            "ok": True,
            "draftId": "draft123",
            "publicUrl": "https://tailplan.test/team/tailplan/d/draft123",
            "versionNumber": 1,
        }
        self.assertIs(valid, share.validate_upload_result(valid, base_url))

        invalid_urls = (
            "https://other.test/team/tailplan/d/draft123",
            "https://tailplan.test/other/team/tailplan/d/draft123",
            "https://tailplan.test/team/tailplan/d/draft123/",
            "https://user@tailplan.test/team/tailplan/d/draft123",
            "https://tailplan.test:bad/team/tailplan/d/draft123",
            "https://[::1/team/tailplan/d/draft123",
            "https://tailplan.test/team/tail plan/d/draft123",
            "https://tailplan.test/team/tailplan/d/draft123?query=1",
            "https://tailplan.test/team/tailplan/d/draft123#fragment",
        )
        for public_url in invalid_urls:
            with self.subTest(public_url=public_url), self.assertRaises(share.ProtocolFailure):
                share.validate_upload_result({**valid, "publicUrl": public_url}, base_url)


class ResponseSchemaTests(unittest.TestCase):
    def test_publish_rejects_malformed_success_response_before_mapping_or_verification(self) -> None:
        valid = {
            "ok": True,
            "draftId": "draft123",
            "publicUrl": "https://tailplan.test/d/draft123",
            "versionNumber": 1,
        }
        invalid_results = (
            None,
            [],
            {**valid, "ok": 1},
            {**valid, "draftId": ""},
            {**valid, "draftId": "BAD/id"},
            {**valid, "publicUrl": "file:///d/draft123"},
            {**valid, "publicUrl": "https://tailplan.test/d/other123"},
            {**valid, "publicUrl": "https://tailplan.test/d/draft123?leak=1"},
            {**valid, "versionNumber": True},
            {**valid, "versionNumber": 0},
            {**valid, "warnings": "warning"},
            {**valid, "warnings": ["fine", 3]},
        )
        for result in invalid_results:
            with self.subTest(result=result), tempfile.TemporaryDirectory() as td:
                root = Path(td)
                source = root / "page.html"
                source.write_text("<title>Page</title>", encoding="utf-8")
                stdout = io.StringIO()
                stderr = io.StringIO()
                verifications = []

                exit_code = share.publish(
                    source,
                    "https://tailplan.test",
                    home=root / "home",
                    uploader=lambda *args, result=result: result,
                    verifier=lambda *args, verifications=verifications: verifications.append(args),
                    stdout=stdout,
                    stderr=stderr,
                )

                self.assertEqual(1, exit_code)
                self.assertEqual([], verifications)
                self.assertFalse((root / "home" / ".tailplan" / "drafts.json").exists())
                self.assertNotIn("https://", stdout.getvalue())
                self.assertIn("protocol", stderr.getvalue().lower())

    def test_publish_accepts_absent_or_string_list_warnings(self) -> None:
        for warnings in (None, [], ["first", "second"]):
            with self.subTest(warnings=warnings), tempfile.TemporaryDirectory() as td:
                root = Path(td)
                source = root / "page.html"
                source.write_text("<title>Page</title>", encoding="utf-8")
                result = {
                    "ok": True,
                    "draftId": "draft123",
                    "publicUrl": "https://tailplan.test/d/draft123",
                    "versionNumber": 1,
                }
                if warnings is not None:
                    result["warnings"] = warnings

                exit_code = share.publish(
                    source,
                    "https://tailplan.test",
                    home=root / "home",
                    uploader=lambda *args, result=result: result,
                    verifier=lambda *args: None,
                    stdout=io.StringIO(),
                    stderr=io.StringIO(),
                )

                self.assertEqual(0, exit_code)


class VerificationTests(unittest.TestCase):
    HTML = (
        '<!doctype html><html><head><title>Verified Café</title></head><body>'
        '<p>unique-marker-8f30</p><a href="https://example.test" '
        'target="_blank" rel="noopener noreferrer">example</a></body></html>'
    )
    HEADERS: ClassVar[dict[str, str]] = {
        "Content-Type": "text/html; charset=utf-8",
        "Cache-Control": "no-store",
        "X-Content-Type-Options": "nosniff",
        "Referrer-Policy": "no-referrer",
        "X-Frame-Options": "DENY",
        "Content-Security-Policy": (
            "default-src 'none'; script-src 'none'; style-src 'unsafe-inline'; "
            "form-action 'none'; frame-ancestors 'none'"
        ),
    }

    def test_readback_accepts_exact_utf8_document_and_security_headers(self) -> None:
        requests = []

        def opener(request, timeout):
            requests.append(request)
            return FakeResponse(self.HTML, status=200, headers=self.HEADERS)

        public_url = "https://tailplan.test/d/draft123"
        share.verify_readback(public_url, self.HTML, opener=opener)

        self.assertEqual(public_url, "https://tailplan.test/d/draft123")
        self.assertRegex(requests[0].full_url, r"^https://tailplan\.test/d/draft123\?verify=")

    def test_readback_requires_status_and_html_media_type(self) -> None:
        cases = (
            (204, self.HEADERS, "expected 200"),
            (200, {**self.HEADERS, "Content-Type": "application/json"}, "Content-Type"),
        )
        for status, headers, message in cases:
            with (
                self.subTest(status=status, content_type=headers["Content-Type"]),
                self.assertRaisesRegex(share.VerificationFailure, message),
            ):
                share.verify_readback(
                    "https://tailplan.test/d/draft123",
                    self.HTML,
                    opener=lambda request, timeout, status=status, headers=headers: FakeResponse(
                        self.HTML, status=status, headers=headers
                    ),
                )

    def test_readback_requires_each_security_header_with_exact_value(self) -> None:
        required = {
            "Cache-Control": "no-store",
            "X-Content-Type-Options": "nosniff",
            "Referrer-Policy": "no-referrer",
            "X-Frame-Options": "DENY",
        }
        for name, expected in required.items():
            for headers in (
                {key: value for key, value in self.HEADERS.items() if key != name},
                {**self.HEADERS, name: expected + "-wrong"},
            ):
                with (
                    self.subTest(name=name, present=name in headers),
                    self.assertRaisesRegex(share.VerificationFailure, name),
                ):
                    share.verify_readback(
                        "https://tailplan.test/d/draft123",
                        self.HTML,
                        opener=lambda request, timeout, headers=headers: FakeResponse(
                            self.HTML, status=200, headers=headers
                        ),
                    )

    def test_readback_requires_csp_and_exact_required_directive_values(self) -> None:
        cases = (
            None,
            "default-src 'none'; script-src 'self'; form-action 'none'; frame-ancestors 'none'",
            "default-src 'none'; script-src 'none'; form-action 'none'",
            "default-src 'none' https:; script-src 'none'; form-action 'none'; frame-ancestors 'none'",
        )
        for csp in cases:
            headers = dict(self.HEADERS)
            if csp is None:
                del headers["Content-Security-Policy"]
            else:
                headers["Content-Security-Policy"] = csp
            with (
                self.subTest(csp=csp),
                self.assertRaisesRegex(share.VerificationFailure, "Content-Security-Policy"),
            ):
                share.verify_readback(
                    "https://tailplan.test/d/draft123",
                    self.HTML,
                    opener=lambda request, timeout, headers=headers: FakeResponse(
                        self.HTML, status=200, headers=headers
                    ),
                )

    def test_readback_rejects_content_mismatch(self) -> None:
        changed = self.HTML.replace("unique-marker-8f30", "wrong-marker")

        with self.assertRaisesRegex(share.VerificationFailure, "content digest mismatch"):
            share.verify_readback(
                "https://tailplan.test/d/draft123",
                self.HTML,
                opener=lambda request, timeout: FakeResponse(
                    changed, status=200, headers=self.HEADERS
                ),
            )

    def test_readback_rejects_unavailable_url(self) -> None:
        def opener(request, timeout):
            raise urllib.error.URLError("unreachable private address")

        with self.assertRaisesRegex(share.VerificationFailure, "unavailable"):
            share.verify_readback(
                "https://tailplan.test/d/draft123",
                self.HTML,
                opener=opener,
            )

    def test_readback_converts_truncated_http_to_verification_failure(self) -> None:
        def opener(request, timeout):
            raise http.client.IncompleteRead(b"partial", 20)

        with self.assertRaisesRegex(share.VerificationFailure, "unavailable"):
            share.verify_readback(
                "https://tailplan.test/d/draft123",
                self.HTML,
                opener=opener,
            )

    def test_client_link_rewrite_is_idempotent_for_exact_readback(self) -> None:
        source = (
            '<title>Links</title><a href="https://example.test" '
            'target="_blank" rel="opener author">example</a>'
        )
        rewritten = share.make_links_openable(source)

        self.assertIn('target="_blank" rel="noopener noreferrer"', rewritten)
        self.assertNotIn('rel="opener author"', rewritten)
        self.assertEqual(rewritten, share.make_links_openable(rewritten))

    def test_failed_verification_persists_mapping_without_printing_success_url(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            home = Path(td) / "home"
            source = Path(td) / "page.html"
            source.write_text(self.HTML, encoding="utf-8")
            stdout = io.StringIO()
            stderr = io.StringIO()

            def uploader(html_path, base_url, draft_id):
                return {
                    "ok": True,
                    "draftId": "saved123",
                    "publicUrl": "https://tailplan.test/d/saved123",
                    "versionNumber": 1,
                }

            def verifier(public_url, expected_html):
                raise share.VerificationFailure("content digest mismatch; publish again")

            exit_code = share.publish(
                source,
                "https://tailplan.test",
                home=home,
                uploader=uploader,
                verifier=verifier,
                stdout=stdout,
                stderr=stderr,
            )

            self.assertEqual(1, exit_code)
            self.assertNotIn("https://tailplan.test/d/saved123", stdout.getvalue())
            self.assertNotIn("URL:", stdout.getvalue())
            self.assertIn("verification failed", stderr.getvalue().lower())
            mappings = share.load_mappings(home / ".tailplan" / "drafts.json")
            self.assertEqual("saved123", share.mapped_draft_id(mappings, source))

    def test_verification_uses_the_uploaded_document_snapshot(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            home = Path(td) / "home"
            source = Path(td) / "page.html"
            original = "<!doctype html><title>Original</title><p>snapshot-marker</p>"
            source.write_text(original, encoding="utf-8")
            uploaded = []

            def uploader(html_path, base_url, draft_id):
                uploaded.append(share.final_html(html_path))
                source.write_text("<!doctype html><title>Changed</title>", encoding="utf-8")
                return {
                    "ok": True,
                    "draftId": "snapshot1",
                    "publicUrl": "https://tailplan.test/d/snapshot1",
                    "versionNumber": 1,
                }

            def verifier(public_url, expected_html):
                self.assertEqual(uploaded[0], expected_html)

            exit_code = share.publish(
                source,
                "https://tailplan.test",
                home=home,
                uploader=uploader,
                verifier=verifier,
                stdout=io.StringIO(),
                stderr=io.StringIO(),
            )

            self.assertEqual(0, exit_code)

    def test_mapping_failure_is_actionable_and_does_not_claim_success(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            source = Path(td) / "page.html"
            source.write_text(self.HTML, encoding="utf-8")
            stdout = io.StringIO()
            stderr = io.StringIO()

            def uploader(html_path, base_url, draft_id):
                return {
                    "ok": True,
                    "draftId": "recover123",
                    "publicUrl": "https://tailplan.test/d/recover123",
                    "versionNumber": 1,
                }

            with patch.object(share, "update_mapping", side_effect=OSError("disk full")):
                exit_code = share.publish(
                    source,
                    "https://tailplan.test",
                    home=Path(td) / "home",
                    uploader=uploader,
                    verifier=lambda public_url, expected_html: None,
                    stdout=stdout,
                    stderr=stderr,
                )

            self.assertEqual(1, exit_code)
            self.assertEqual("", stdout.getvalue())
            self.assertIn("local draft mapping", stderr.getvalue())
            self.assertIn("--draft recover123", stderr.getvalue())
            self.assertNotIn("secret", stderr.getvalue())


class CliContractTests(unittest.TestCase):
    def test_main_rejects_unsupported_suffixes_and_accepts_supported_case_insensitively(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            for suffix in (".pdf", ".md.bak", ""):
                source = root / f"bad{suffix}"
                source.write_text("content", encoding="utf-8")
                stdout = io.StringIO()
                stderr = io.StringIO()
                with (
                    patch.object(share.sys, "stdout", stdout),
                    patch.object(share.sys, "stderr", stderr),
                    patch.object(share, "publish") as publish_spy,
                ):
                    exit_code = share.main([str(source), "--base-url", "https://tailplan.test"])
                self.assertEqual(2, exit_code)
                publish_spy.assert_not_called()
                self.assertIn(".md, .txt, .html, or .htm", stderr.getvalue())

            for suffix in (".MD", ".Txt", ".HTML", ".HtM"):
                source = root / f"good{suffix}"
                source.write_text("content", encoding="utf-8")
                with patch.object(share, "publish", return_value=0) as publish_spy:
                    exit_code = share.main([str(source), "--base-url", "https://tailplan.test"])
                self.assertEqual(0, exit_code)
                publish_spy.assert_called_once()

    def test_main_json_invalid_base_url_is_one_stable_object_and_does_not_publish(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            source = Path(td) / "page.html"
            source.write_text("<title>Page</title>", encoding="utf-8")
            stdout = io.StringIO()
            stderr = io.StringIO()
            with (
                patch.object(share.sys, "stdout", stdout),
                patch.object(share.sys, "stderr", stderr),
                patch.object(share, "publish") as publish_spy,
            ):
                exit_code = share.main(
                    [str(source), "--base-url", "ftp://tailplan.test", "--json"]
                )

            self.assertEqual(2, exit_code)
            publish_spy.assert_not_called()
            self.assertEqual("", stderr.getvalue())
            self.assertEqual(
                {"ok": False, "category": "configuration", "error": "Base URL must be an http(s) URL."},
                json.loads(stdout.getvalue()),
            )
            self.assertEqual(1, len(stdout.getvalue().splitlines()))

    def test_publish_json_success_is_one_stable_object_with_no_other_prose(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            source = root / "page.html"
            source.write_text("<title>Page</title>", encoding="utf-8")
            stdout = io.StringIO()
            stderr = io.StringIO()
            result = {
                "ok": True,
                "draftId": "draft123",
                "publicUrl": "https://tailplan.test/d/draft123",
                "versionNumber": 3,
                "warnings": ["safe warning"],
            }

            exit_code = share.publish(
                source,
                "https://tailplan.test",
                home=root / "home",
                uploader=lambda *args: result,
                verifier=lambda *args: None,
                stdout=stdout,
                stderr=stderr,
                json_output=True,
            )

            self.assertEqual(0, exit_code)
            self.assertEqual("", stderr.getvalue())
            self.assertEqual(
                {
                    "ok": True,
                    "draftId": "draft123",
                    "publicUrl": "https://tailplan.test/d/draft123",
                    "versionNumber": 3,
                    "warnings": ["safe warning"],
                },
                json.loads(stdout.getvalue()),
            )
            self.assertEqual(1, len(stdout.getvalue().splitlines()))

    def test_publish_json_expected_errors_are_one_object_without_traceback_or_secrets(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            source = root / "page.html"
            source.write_bytes(b"<title>\xff</title>")
            stdout = io.StringIO()
            stderr = io.StringIO()
            uploads = []

            exit_code = share.publish(
                source,
                "https://tailplan.test",
                home=root / "home",
                uploader=lambda *args: uploads.append(args),
                verifier=lambda *args: None,
                stdout=stdout,
                stderr=stderr,
                json_output=True,
            )

            self.assertEqual(1, exit_code)
            self.assertEqual([], uploads)
            self.assertEqual("", stderr.getvalue())
            error = json.loads(stdout.getvalue())
            self.assertEqual(False, error["ok"])
            self.assertEqual("file", error["category"])
            self.assertNotIn("Traceback", stdout.getvalue())
            self.assertNotIn("\ufffd", stdout.getvalue())

    def test_missing_or_invalid_utf8_token_is_safe_configuration_error(self) -> None:
        for token_bytes in (None, b"\xffsecret"):
            with self.subTest(token_bytes=token_bytes), tempfile.TemporaryDirectory() as td:
                root = Path(td)
                home = root / "home"
                source = root / "page.html"
                source.write_text("<title>Page</title>", encoding="utf-8")
                token_path = home / ".tailplan" / "token"
                if token_bytes is not None:
                    token_path.parent.mkdir(parents=True)
                    token_path.write_bytes(token_bytes)
                stdout = io.StringIO()
                stderr = io.StringIO()

                with patch.object(share.Path, "home", return_value=home):
                    exit_code = share.publish(
                        source,
                        "https://tailplan.test",
                        home=home,
                        stdout=stdout,
                        stderr=stderr,
                        json_output=True,
                    )

                self.assertEqual(1, exit_code)
                self.assertEqual("", stderr.getvalue())
                error = json.loads(stdout.getvalue())
                self.assertEqual("configuration", error["category"])
                self.assertNotIn("secret", stdout.getvalue().lower())
                self.assertNotIn("Traceback", stdout.getvalue())

    def test_main_json_truncated_upload_is_one_stable_error_without_traceback(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            home = root / "home"
            token_path = home / ".tailplan" / "token"
            token_path.parent.mkdir(parents=True)
            token_path.write_text("truncation-secret", encoding="utf-8")
            source = root / "page.html"
            source.write_text("<title>Page</title>", encoding="utf-8")
            stdout = io.StringIO()
            stderr = io.StringIO()
            truncated = http.client.IncompleteRead(b'{"ok":', 40)

            with (
                patch.object(share.Path, "home", return_value=home),
                patch.object(share.sys, "stdout", stdout),
                patch.object(share.sys, "stderr", stderr),
                patch.object(share, "_open_without_redirects", side_effect=truncated) as open_spy,
            ):
                exit_code = share.main(
                    [str(source), "--base-url", "https://tailplan.test", "--json"]
                )

            self.assertEqual(1, exit_code)
            self.assertEqual(3, open_spy.call_count)
            self.assertEqual("", stderr.getvalue())
            error = json.loads(stdout.getvalue())
            self.assertEqual("server unavailable", error["category"])
            self.assertEqual(1, len(stdout.getvalue().splitlines()))
            self.assertNotIn("Traceback", stdout.getvalue())
            self.assertNotIn("truncation-secret", stdout.getvalue())

    def test_json_usage_and_mapping_errors_have_no_extra_prose_or_upload(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            source = root / "page.html"
            source.write_text("<title>Page</title>", encoding="utf-8")
            stdout = io.StringIO()
            stderr = io.StringIO()
            with (
                patch.object(share.sys, "stdout", stdout),
                patch.object(share.sys, "stderr", stderr),
                patch.object(share, "publish") as publish_spy,
            ):
                exit_code = share.main(
                    [
                        str(source),
                        "--base-url",
                        "https://tailplan.test",
                        "--draft",
                        "draft123",
                        "--new",
                        "--json",
                    ]
                )
            self.assertEqual(2, exit_code)
            publish_spy.assert_not_called()
            self.assertEqual("", stderr.getvalue())
            self.assertEqual("usage", json.loads(stdout.getvalue())["category"])
            self.assertEqual(1, len(stdout.getvalue().splitlines()))

            mapping_path = root / "home" / ".tailplan" / "drafts.json"
            mapping_path.parent.mkdir(parents=True)
            mapping_path.write_text("{broken", encoding="utf-8")
            stdout = io.StringIO()
            stderr = io.StringIO()
            uploads = []
            exit_code = share.publish(
                source,
                "https://tailplan.test",
                home=root / "home",
                uploader=lambda *args: uploads.append(args),
                verifier=lambda *args: None,
                stdout=stdout,
                stderr=stderr,
                json_output=True,
            )
            self.assertEqual(1, exit_code)
            self.assertEqual([], uploads)
            self.assertEqual("", stderr.getvalue())
            self.assertEqual("mapping", json.loads(stdout.getvalue())["category"])
            self.assertEqual("{broken", mapping_path.read_text(encoding="utf-8"))

    def test_main_json_real_server_publish_and_readback(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            home = root / "home"
            token_path = home / ".tailplan" / "token"
            token_path.parent.mkdir(parents=True)
            token_path.write_text("cli-token\n", encoding="utf-8")
            data_dir = root / "data"
            data_dir.mkdir()
            source = root / "report.md"
            source.write_text("# CLI Integration\n\nreal-main-marker", encoding="utf-8")
            with socket.socket() as sock:
                sock.bind(("127.0.0.1", 0))
                port = sock.getsockname()[1]
            process = subprocess.Popen(
                [
                    sys.executable,
                    str(ROOT / "tailplan_server.py"),
                    "--host",
                    "127.0.0.1",
                    "--port",
                    str(port),
                    "--data-dir",
                    str(data_dir),
                    "--token-file",
                    str(token_path),
                    "--base-url",
                    f"http://127.0.0.1:{port}",
                ],
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
            )
            try:
                base_url = f"http://127.0.0.1:{port}"
                for _ in range(50):
                    try:
                        with urllib.request.urlopen(base_url + "/healthz", timeout=1):
                            break
                    except urllib.error.URLError:
                        if process.poll() is not None:
                            self.fail("isolated Tailplan server exited during startup")
                        time.sleep(0.05)
                else:
                    self.fail("isolated Tailplan server did not become healthy")

                stdout = io.StringIO()
                stderr = io.StringIO()
                with (
                    patch.object(share.Path, "home", return_value=home),
                    patch.object(share.sys, "stdout", stdout),
                    patch.object(share.sys, "stderr", stderr),
                ):
                    exit_code = share.main(
                        [str(source), "--base-url", base_url, "--json"]
                    )

                self.assertEqual(0, exit_code)
                self.assertEqual("", stderr.getvalue())
                result = json.loads(stdout.getvalue())
                self.assertIs(result["ok"], True)
                self.assertEqual(1, result["versionNumber"])
                self.assertEqual([], result["warnings"])
                self.assertEqual(1, len(stdout.getvalue().splitlines()))
                mapping = share.load_mappings(home / ".tailplan" / "drafts.json")
                self.assertEqual(result["draftId"], share.mapped_draft_id(mapping, source))
            finally:
                stop_process(process)


if __name__ == "__main__":
    unittest.main()
