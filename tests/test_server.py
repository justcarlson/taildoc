from __future__ import annotations

import shutil
import unittest
from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
from http.client import HTTPConnection
from http.server import BaseHTTPRequestHandler
from pathlib import Path
from socket import create_connection, socket
from tempfile import TemporaryDirectory
from threading import Event, Lock, Thread
from threading import enumerate as active_threads
from unittest.mock import Mock, patch

import tailplan_server as server


class ServerAdmissionTests(unittest.TestCase):
    def test_saturated_server_closes_excess_connection_without_starting_handler(self) -> None:
        entered = Event()
        release = Event()
        calls_lock = Lock()
        calls = 0

        class BlockingHandler(BaseHTTPRequestHandler):
            def handle(self) -> None:
                nonlocal calls
                with calls_lock:
                    calls += 1
                entered.set()
                release.wait(timeout=2)

            def log_message(self, format: str, *args: object) -> None:
                pass

        httpd = server.TailplanHTTPServer(
            ("127.0.0.1", 0), BlockingHandler, max_handlers=1, read_timeout=1
        )
        thread = Thread(target=httpd.serve_forever, daemon=True)
        thread.start()
        first = create_connection(httpd.server_address, timeout=1)
        second = None
        try:
            self.assertTrue(entered.wait(timeout=1), "first handler was not admitted")
            second = create_connection(httpd.server_address, timeout=1)
            second.settimeout(1)
            second.sendall(b"GET /healthz HTTP/1.1\r\nHost: local\r\n\r\n")
            try:
                closed = second.recv(1) == b""
            except ConnectionResetError:
                closed = True
            self.assertTrue(closed, "saturated connection was not closed")
            with calls_lock:
                self.assertEqual(1, calls)
        finally:
            release.set()
            first.close()
            if second is not None:
                second.close()
            httpd.shutdown()
            httpd.server_close()
            thread.join(timeout=2)

    def test_slow_reader_is_closed_after_configured_connection_timeout(self) -> None:
        timed_out = Event()
        observed_timeouts: list[float | None] = []

        class SlowReadHandler(BaseHTTPRequestHandler):
            def handle(self) -> None:
                observed_timeouts.append(self.connection.gettimeout())
                try:
                    self.rfile.readline()
                except TimeoutError:
                    timed_out.set()

            def log_message(self, format: str, *args: object) -> None:
                pass

        httpd = server.TailplanHTTPServer(
            ("127.0.0.1", 0), SlowReadHandler, max_handlers=1, read_timeout=0.1
        )
        thread = Thread(target=httpd.serve_forever, daemon=True)
        thread.start()
        sock = create_connection(httpd.server_address, timeout=1)
        try:
            sock.sendall(b"GET /healthz HTTP/1.1")
            self.assertTrue(timed_out.wait(timeout=1), "slow reader did not time out")
            self.assertEqual([0.1], observed_timeouts)
            sock.settimeout(1)
            self.assertEqual(b"", sock.recv(1))
        finally:
            sock.close()
            httpd.shutdown()
            httpd.server_close()
            thread.join(timeout=2)


class HtmlValidationTests(unittest.TestCase):
    def test_page_shell_has_no_dead_iframe_wrapper_or_styles(self) -> None:
        shell = server.home("https://tailplan.test")

        self.assertNotIn("tailplan-banner", shell)
        self.assertNotIn("draft-frame", shell)
        self.assertFalse(hasattr(server, "draft_wrapper"))

    def test_visible_protocol_like_prose_is_accepted(self) -> None:
        doc = """<!doctype html><html><head><title>Notes</title></head>
        <body><p>profile: default</p><p>file: report.md</p>
        <p>JavaScript: disabled</p></body></html>"""

        ok, errors, _warnings = server.validate_html(doc)

        self.assertTrue(ok, errors)
        self.assertEqual([], errors)

    def test_active_html_and_obfuscated_unsafe_urls_are_rejected(self) -> None:
        cases = {
            "active tag": "<script>alert(1)</script>",
            "event handler": '<p OnClick="alert(1)">x</p>',
            "srcdoc": '<div SRCDOC="<p>x</p>"></div>',
            "meta refresh": '<meta HTTP-EQUIV="Refresh" content="0; URL=https://example.com">',
            "entity scheme": '<a href="java&#x73;cript:alert(1)">x</a>',
            "percent scheme": '<a href="javascript%3Aalert(1)">x</a>',
            "control whitespace": '<a href="java&#x0a;script:alert(1)">x</a>',
            "file source": '<img src="FiLe:/etc/passwd">',
            "style attribute": '<p style="background:url( jAvAsCrIpT:alert(1) )">x</p>',
            "style block": '<style>@import url("file:/secret.css")</style>',
        }

        for label, fragment in cases.items():
            with self.subTest(label=label):
                doc = f"<html><head><title>Unsafe</title></head><body>{fragment}</body></html>"
                ok, errors, _warnings = server.validate_html(doc)
                self.assertFalse(ok, (label, errors))
                self.assertTrue(errors, label)

    def test_anchor_rewrite_preserves_quoted_greater_than_and_existing_attributes(self) -> None:
        doc = (
            '<p><a title="1 > 0" href="https://example.com?q=1>0">Example</a> '
            '<a href="/local" target="named" rel="author">Local</a></p>'
        )

        rewritten = server.make_links_openable(doc)

        self.assertIn('title="1 > 0"', rewritten)
        self.assertIn('href="https://example.com?q=1>0"', rewritten)
        self.assertIn('target="_blank"', rewritten)
        self.assertIn('rel="noopener noreferrer"', rewritten)
        self.assertIn('target="named" rel="noopener noreferrer"', rewritten)
        self.assertEqual(2, rewritten.count("<a "))

    def test_anchor_rewrite_removes_opener_and_duplicate_rel_attributes(self) -> None:
        doc = (
            '<a href="https://example.test" rel="author opener" '
            'REL="external" target="_blank">Example</a>'
        )

        rewritten = server.make_links_openable(doc)

        self.assertEqual(1, rewritten.lower().count(" rel="))
        self.assertIn('rel="noopener noreferrer"', rewritten)
        self.assertNotIn('rel="author opener"', rewritten)
        self.assertNotIn("author", rewritten)
        self.assertNotIn("external", rewritten)

    def test_anchor_rewrite_ignores_close_tokens_inside_quoted_attributes(self) -> None:
        cases = (
            (
                '<a title="both > and /> stay" href="https://example.test/?q=>/>">Double</a>',
                (
                    '<a title="both > and /> stay" href="https://example.test/?q=>/>"'
                    ' target="_blank" rel="noopener noreferrer">Double</a>'
                ),
            ),
            (
                "<a title='both > and /> stay' href='https://example.test/?q=>/>'>Single</a>",
                (
                    "<a title='both > and /> stay' href='https://example.test/?q=>/>'"
                    ' target="_blank" rel="noopener noreferrer">Single</a>'
                ),
            ),
        )

        for original, expected in cases:
            with self.subTest(original=original):
                self.assertEqual(expected, server.make_links_openable(original))

    def test_anchor_rewrite_handles_many_edits_with_exact_linear_growth(self) -> None:
        anchor_count = 10_000
        anchor = '<a href="/target">target</a>'
        addition = ' target="_blank" rel="noopener noreferrer"'
        doc = anchor * anchor_count

        rewritten = server.make_links_openable(doc)

        self.assertEqual(len(doc) + anchor_count * len(addition), len(rewritten))
        self.assertEqual(anchor_count, rewritten.count(addition))
        self.assertEqual(anchor_count, rewritten.count("</a>"))

    def test_anchor_rewrite_applies_many_edits_with_one_pass_of_source_slices(self) -> None:
        class SliceCountingString(str):
            sliced_characters = 0

            def __getitem__(self, key):
                result = super().__getitem__(key)
                if isinstance(key, slice):
                    type(self).sliced_characters += len(result)
                    return type(self)(result)
                return result

            def __add__(self, other):
                return type(self)(super().__add__(other))

            def __radd__(self, other):
                return type(self)(other + str(self))

        anchor_count = 4_000
        start_tag = '<a href="/target">'
        anchor = f"{start_tag}target</a>"
        addition = ' target="_blank" rel="noopener noreferrer"'
        replacement = f'{start_tag[:-1]}{addition}>'
        doc = SliceCountingString(anchor * anchor_count)
        edits = [
            (
                index * len(anchor),
                index * len(anchor) + len(start_tag),
                replacement,
            )
            for index in reversed(range(anchor_count))
        ]
        rewriter = Mock(edits=edits)

        with patch.object(server, "_AnchorRewriter", return_value=rewriter):
            rewritten = server.make_links_openable(doc)

        expected = f"{replacement}target</a>" * anchor_count
        self.assertEqual(expected, rewritten)
        self.assertEqual(anchor_count, rewritten.count(addition))
        self.assertEqual(
            len(doc) - anchor_count * len(start_tag),
            SliceCountingString.sliced_characters,
        )

    def test_anchor_rewrite_uses_lf_boundaries_in_mixed_newline_document(self) -> None:
        doc = (
            '<p>before\r<a href="/one">one</a>\n'
            '<a title="2 > 1"\r\n href="/two">two</a></p>'
        )
        expected = (
            '<p>before\r<a href="/one" target="_blank" rel="noopener noreferrer">one</a>\n'
            '<a title="2 > 1"\r\n href="/two" target="_blank" '
            'rel="noopener noreferrer">two</a></p>'
        )

        self.assertEqual(expected, server.make_links_openable(doc))


class StoreTests(unittest.TestCase):
    def test_startup_rebases_mixed_legacy_paths_after_data_root_copy(self) -> None:
        with TemporaryDirectory() as td:
            base = Path(td)
            old_root = base / "home" / ".tailplan"
            new_root = base / "var" / "lib" / "tailplan"
            old_store = server.Store(old_root)
            created = [
                old_store.upsert(
                    f"<!doctype html><title>Draft {number}</title><p>{number}</p>",
                    f"draft-{number}.html",
                    None,
                    "https://tailplan.test",
                    f"request-{number}",
                )
                for number in range(41)
            ]
            metadata = old_store.load_meta()
            metadata["futureTopLevel"] = {"preserve": [1, 2, 3]}
            for number, result in enumerate(created):
                metadata["drafts"][result["draftId"]]["futureDraftField"] = {
                    "number": number
                }
            old_store.save_meta(metadata)

            shutil.copytree(old_root, new_root)
            copied = server.json.loads(
                (new_root / "metadata.json").read_text(encoding="utf-8")
            )
            for number, result in enumerate(created):
                if number % 3 == 0:
                    draft = copied["drafts"][result["draftId"]]
                    draft["currentObject"] = str(
                        new_root
                        / "drafts"
                        / result["draftId"]
                        / f"v{draft['latestVersionNumber']}.html"
                    )
            original_text = server.json.dumps(copied, indent=2) + "\n"
            (new_root / "metadata.json").write_text(original_text, encoding="utf-8")
            shutil.rmtree(old_root)

            migrated_store = server.Store(new_root)
            migrated = migrated_store.load_meta()

            expected = deepcopy(copied)
            for draft_id, draft in expected["drafts"].items():
                draft["currentObject"] = str(
                    new_root
                    / "drafts"
                    / draft_id
                    / f"v{draft['latestVersionNumber']}.html"
                )
            self.assertEqual(expected, migrated)
            self.assertEqual(
                original_text,
                migrated_store.backup.read_text(encoding="utf-8"),
            )
            self.assertEqual(41, len(migrated["idempotency"]))
            self.assertEqual([], list(new_root.glob(".*.tmp")))

            metadata_before = migrated_store.meta.read_bytes()
            metadata_inode = migrated_store.meta.stat().st_ino
            backup_before = migrated_store.backup.read_bytes()
            server.Store(new_root)
            self.assertEqual(metadata_before, migrated_store.meta.read_bytes())
            self.assertEqual(metadata_inode, migrated_store.meta.stat().st_ino)
            self.assertEqual(backup_before, migrated_store.backup.read_bytes())

    def test_startup_does_not_rewrite_canonical_metadata(self) -> None:
        with TemporaryDirectory() as td:
            root = Path(td) / "data"
            store = server.Store(root)
            store.upsert(
                "<!doctype html><title>Canonical</title>",
                "canonical.html",
                None,
                "https://tailplan.test",
            )
            before = store.meta.read_bytes()
            inode = store.meta.stat().st_ino

            server.Store(root)

            self.assertEqual(before, store.meta.read_bytes())
            self.assertEqual(inode, store.meta.stat().st_ino)
            self.assertFalse(store.backup.exists())

    def test_startup_rebase_rejects_untrusted_paths_and_invalid_canonical_objects(
        self,
    ) -> None:
        def wrong_id(old_root: Path, _new_root: Path, draft_id: str, draft: dict) -> None:
            draft["currentObject"] = str(
                old_root / "drafts" / "abcdef" / f"v{draft['latestVersionNumber']}.html"
            )

        def wrong_version(
            old_root: Path, _new_root: Path, draft_id: str, draft: dict
        ) -> None:
            draft["currentObject"] = str(old_root / "drafts" / draft_id / "v2.html")

        def traversal(old_root: Path, _new_root: Path, draft_id: str, draft: dict) -> None:
            draft["currentObject"] = (
                f"{old_root}/ignored/../drafts/{draft_id}/"
                f"v{draft['latestVersionNumber']}.html"
            )

        def relative(_old_root: Path, _new_root: Path, draft_id: str, draft: dict) -> None:
            draft["currentObject"] = (
                f"drafts/{draft_id}/v{draft['latestVersionNumber']}.html"
            )

        def symlink(_old_root: Path, new_root: Path, draft_id: str, _draft: dict) -> None:
            canonical = new_root / "drafts" / draft_id / "v1.html"
            outside = new_root / "outside.html"
            outside.write_text(canonical.read_text(encoding="utf-8"), encoding="utf-8")
            canonical.unlink()
            canonical.symlink_to(outside)

        def missing(_old_root: Path, new_root: Path, draft_id: str, _draft: dict) -> None:
            (new_root / "drafts" / draft_id / "v1.html").unlink()

        def wrong_digest(
            _old_root: Path, new_root: Path, draft_id: str, _draft: dict
        ) -> None:
            (new_root / "drafts" / draft_id / "v1.html").write_text(
                "<!doctype html><title>Changed</title>", encoding="utf-8"
            )

        def oversized(_old_root: Path, new_root: Path, draft_id: str, draft: dict) -> None:
            doc = "<!doctype html><title>Large</title>" + (
                "x" * server.MAX_HTML_BYTES
            )
            (new_root / "drafts" / draft_id / "v1.html").write_text(
                doc, encoding="utf-8"
            )
            draft["fileSha256"] = server.sha256_text(doc)

        def invalid_html(
            _old_root: Path, new_root: Path, draft_id: str, draft: dict
        ) -> None:
            doc = "<!doctype html><title>Unsafe</title><script>alert(1)</script>"
            (new_root / "drafts" / draft_id / "v1.html").write_text(
                doc, encoding="utf-8"
            )
            draft["fileSha256"] = server.sha256_text(doc)

        def invalid_schema(
            _old_root: Path, _new_root: Path, _draft_id: str, draft: dict
        ) -> None:
            draft["title"] = ["not", "a", "title"]

        cases = {
            "mismatched draft id": wrong_id,
            "mismatched version": wrong_version,
            "path traversal": traversal,
            "relative path": relative,
            "symlink object": symlink,
            "missing object": missing,
            "digest mismatch": wrong_digest,
            "oversized object": oversized,
            "invalid HTML object": invalid_html,
            "invalid metadata schema": invalid_schema,
        }
        for label, corrupt in cases.items():
            with self.subTest(label=label), TemporaryDirectory() as td:
                base = Path(td)
                old_root = base / "old"
                new_root = base / "new"
                old_store = server.Store(old_root)
                created = old_store.upsert(
                    "<!doctype html><title>Valid</title><p>content</p>",
                    "valid.html",
                    None,
                    "https://tailplan.test",
                )
                draft_id = created["draftId"]
                shutil.copytree(old_root, new_root)
                metadata_path = new_root / "metadata.json"
                metadata = server.json.loads(metadata_path.read_text(encoding="utf-8"))
                draft = metadata["drafts"][draft_id]
                corrupt(old_root, new_root, draft_id, draft)
                original_text = server.json.dumps(metadata, indent=2) + "\n"
                metadata_path.write_text(original_text, encoding="utf-8")
                shutil.rmtree(old_root)

                with self.assertRaises((server.StorageError, OSError, UnicodeError)):
                    server.Store(new_root)

                self.assertEqual(original_text, metadata_path.read_text(encoding="utf-8"))
                self.assertFalse((new_root / "metadata.json.bak").exists())
                self.assertEqual([], list(new_root.glob(".*.tmp")))

    def test_failed_startup_rewrite_keeps_original_metadata_and_backup(self) -> None:
        with TemporaryDirectory() as td:
            base = Path(td)
            old_root = base / "old"
            new_root = base / "new"
            old_store = server.Store(old_root)
            old_store.upsert(
                "<!doctype html><title>Recoverable</title>",
                "recoverable.html",
                None,
                "https://tailplan.test",
            )
            shutil.copytree(old_root, new_root)
            shutil.rmtree(old_root)
            metadata_path = new_root / "metadata.json"
            original = metadata_path.read_text(encoding="utf-8")
            real_atomic_write = server._atomic_write_text

            def fail_primary(path: Path, text: str) -> None:
                if path == metadata_path:
                    raise OSError("injected metadata rewrite failure")
                real_atomic_write(path, text)

            with (
                patch.object(server, "_atomic_write_text", side_effect=fail_primary),
                self.assertRaisesRegex(OSError, "injected metadata rewrite failure"),
            ):
                server.Store(new_root)

            self.assertEqual(original, metadata_path.read_text(encoding="utf-8"))
            self.assertEqual(
                original,
                (new_root / "metadata.json.bak").read_text(encoding="utf-8"),
            )
            self.assertEqual([], list(new_root.glob(".*.tmp")))

    def test_readiness_rejects_deleted_current_object(self) -> None:
        with TemporaryDirectory() as td:
            store = server.Store(Path(td))
            created = store.upsert(
                "<title>Deleted</title>", "deleted.html", None, "https://tailplan.test"
            )
            current = Path(store.load_meta()["drafts"][created["draftId"]]["currentObject"])
            current.unlink()

            with self.assertRaises(server.StorageError):
                store.check_ready()

    def test_malformed_and_wrong_schema_metadata_fail_closed(self) -> None:
        bad_documents = ("{not-json", "[]", '{"drafts": []}')
        for raw in bad_documents:
            with self.subTest(raw=raw), TemporaryDirectory() as td:
                root = Path(td)
                store = server.Store(root)
                store.meta.write_text(raw, encoding="utf-8")

                with self.assertRaises(server.StorageError):
                    store.load_meta()

    def test_malformed_inner_drafts_and_idempotency_receipts_fail_closed(self) -> None:
        with TemporaryDirectory() as td:
            store = server.Store(Path(td))
            created = store.upsert(
                "<title>Valid</title>",
                "valid.html",
                None,
                "https://tailplan.test/base",
                "request-1",
            )
            draft_id = created["draftId"]
            valid = store.load_meta()
            cases: dict[str, dict] = {}

            def candidate() -> dict:
                return deepcopy(valid)

            value = candidate()
            value["drafts"][draft_id] = {}
            cases["empty draft"] = value
            for label, field, replacement in (
                ("wrong title type", "title", 1),
                ("wrong filename type", "filename", []),
                ("boolean version", "latestVersionNumber", True),
                ("zero version", "latestVersionNumber", 0),
                ("out-of-range version", "latestVersionNumber", 1_000_000_000),
                ("bad digest", "fileSha256", "not-a-digest"),
                ("wrong digest type", "fileSha256", 1),
                ("bad public URL", "publicUrl", "file:///tmp/draft"),
                ("wrong timestamp type", "updatedAt", 1),
            ):
                value = candidate()
                value["drafts"][draft_id][field] = replacement
                cases[label] = value
            value = candidate()
            value["drafts"][draft_id]["currentObject"] = str(
                store.root / "outside" / "v1.html"
            )
            cases["object outside drafts"] = value
            value = candidate()
            value["drafts"][draft_id]["currentObject"] = str(
                store.drafts / draft_id / "v2.html"
            )
            cases["object version mismatch"] = value
            value = candidate()
            value["idempotency"]["request-1"] = {}
            cases["empty receipt"] = value
            value = candidate()
            value["idempotency"]["request-1"]["fingerprint"] = "bad"
            cases["bad receipt fingerprint"] = value
            value = candidate()
            value["idempotency"]["request-1"]["result"]["draftId"] = "missing1"
            cases["receipt missing draft"] = value
            value = candidate()
            value["idempotency"]["request-1"]["result"]["versionNumber"] = 0
            cases["receipt zero version"] = value
            value = candidate()
            value["idempotency"]["request-1"]["result"]["versionNumber"] = 2
            cases["receipt future version"] = value
            value = candidate()
            value["idempotency"]["request-1"]["result"]["publicUrl"] = "not a URL"
            cases["receipt bad URL"] = value

            for label, malformed in cases.items():
                with self.subTest(label=label):
                    store.meta.write_text(server.json.dumps(malformed), encoding="utf-8")
                    with self.assertRaises(server.StorageError):
                        store.load_meta()

    def test_existing_metadata_without_idempotency_map_remains_valid(self) -> None:
        with TemporaryDirectory() as td:
            store = server.Store(Path(td))
            created = store.upsert(
                "<title>Existing</title>", "existing.html", None, "https://tailplan.test"
            )
            metadata = store.load_meta()
            metadata.pop("idempotency")
            store.meta.write_text(server.json.dumps(metadata), encoding="utf-8")

            loaded = store.load_meta()

            self.assertEqual(created["draftId"], loaded["drafts"][created["draftId"]]["draftId"])

    def test_update_refuses_to_create_version_beyond_metadata_range(self) -> None:
        with TemporaryDirectory() as td:
            store = server.Store(Path(td))
            created = store.upsert(
                "<title>Existing</title>", "existing.html", None, "https://tailplan.test"
            )
            draft_id = created["draftId"]
            metadata = store.load_meta()
            metadata["drafts"][draft_id]["latestVersionNumber"] = server.MAX_VERSION_NUMBER
            metadata["drafts"][draft_id]["currentObject"] = str(
                store.drafts / draft_id / f"v{server.MAX_VERSION_NUMBER}.html"
            )
            store.meta.write_text(server.json.dumps(metadata), encoding="utf-8")
            overflow_path = store.drafts / draft_id / f"v{server.MAX_VERSION_NUMBER + 1}.html"

            with self.assertRaises(server.StorageError):
                store.upsert(
                    "<title>Overflow</title>",
                    "overflow.html",
                    draft_id,
                    "https://tailplan.test",
                )

            self.assertFalse(overflow_path.exists())

    def test_atomic_writes_retain_a_known_good_metadata_backup(self) -> None:
        with TemporaryDirectory() as td:
            root = Path(td)
            store = server.Store(root)
            first = store.upsert("<title>One</title>", "one.html", None, "https://tailplan.test")
            store.upsert(
                "<title>Two</title>", "one.html", first["draftId"], "https://tailplan.test"
            )

            backup = root / "metadata.json.bak"
            self.assertTrue(backup.is_file())
            backup_data = server.json.loads(backup.read_text(encoding="utf-8"))
            self.assertEqual(
                1, backup_data["drafts"][first["draftId"]]["latestVersionNumber"]
            )
            self.assertEqual(0o600, store.meta.stat().st_mode & 0o777)
            self.assertEqual([], list(root.glob("*.tmp")))
            self.assertEqual([], list(root.glob(".*.tmp")))

    def test_100_parallel_creates_and_updates_have_no_lost_or_duplicate_versions(self) -> None:
        with TemporaryDirectory() as td:
            store = server.Store(Path(td))

            def create(number: int) -> dict:
                return store.upsert(
                    f"<title>Create {number}</title>",
                    f"create-{number}.html",
                    None,
                    "https://tailplan.test",
                )

            with ThreadPoolExecutor(max_workers=20) as executor:
                creates = list(executor.map(create, range(100)))

            created_ids = {result["draftId"] for result in creates}
            self.assertEqual(100, len(created_ids))
            self.assertEqual(100, len(store.load_meta()["drafts"]))

            draft_id = creates[0]["draftId"]

            def update(number: int) -> dict:
                return store.upsert(
                    f"<title>Update {number}</title>",
                    "updated.html",
                    draft_id,
                    "https://tailplan.test",
                )

            with ThreadPoolExecutor(max_workers=20) as executor:
                updates = list(executor.map(update, range(100)))

            self.assertEqual(list(range(2, 102)), sorted(item["versionNumber"] for item in updates))
            metadata = store.load_meta()
            self.assertEqual(101, metadata["drafts"][draft_id]["latestVersionNumber"])
            versions = sorted((store.drafts / draft_id).glob("v*.html"))
            self.assertEqual(101, len(versions))

    def test_request_key_replay_returns_original_and_conflicting_reuse_fails(self) -> None:
        with TemporaryDirectory() as td:
            store = server.Store(Path(td))
            first = store.upsert(
                "<title>Retry</title>",
                "retry.html",
                None,
                "https://tailplan.test",
                "request-123",
            )

            replay = store.upsert(
                "<title>Retry</title>",
                "retry.html",
                None,
                "https://tailplan.test",
                "request-123",
            )

            self.assertEqual(first["draftId"], replay["draftId"])
            self.assertEqual(first["versionNumber"], replay["versionNumber"])
            self.assertEqual(1, len(store.load_meta()["drafts"]))
            self.assertTrue(replay["replayed"])
            with self.assertRaises(server.IdempotencyConflict):
                store.upsert(
                    "<title>Different</title>",
                    "retry.html",
                    None,
                    "https://tailplan.test",
                    "request-123",
                )

    def test_idempotency_receipts_keep_newest_in_insertion_order_at_configured_limit(
        self,
    ) -> None:
        with TemporaryDirectory() as td, patch.object(
            server, "MAX_IDEMPOTENCY_RECEIPTS", 2
        ):
            store = server.Store(Path(td))
            for number in (3, 1, 2):
                store.upsert(
                    f"<title>Request {number}</title>",
                    f"request-{number}.html",
                    None,
                    "https://tailplan.test",
                    f"request-{number}",
                )

            self.assertEqual(
                ["request-1", "request-2"],
                list(store.load_meta()["idempotency"]),
            )
            replay = store.upsert(
                "<title>Request 2</title>",
                "request-2.html",
                None,
                "https://tailplan.test",
                "request-2",
            )
            self.assertTrue(replay["replayed"])
            with self.assertRaises(server.IdempotencyConflict):
                store.upsert(
                    "<title>Conflict</title>",
                    "request-2.html",
                    None,
                    "https://tailplan.test",
                    "request-2",
                )


class ApiTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = TemporaryDirectory()
        self.store = server.Store(Path(self.temp_dir.name))
        self.httpd = server.TailplanHTTPServer(("127.0.0.1", 0), server.Handler)
        self.httpd.store = self.store
        self.httpd.token = "secret-token"
        self.httpd.base_url = "https://configured.example/tailplan"
        self.httpd.redirect_view_base_url = ""
        self.thread = Thread(target=self.httpd.serve_forever, daemon=True)
        self.thread.start()
        self.host, self.port = self.httpd.server_address

    def tearDown(self) -> None:
        self.httpd.shutdown()
        self.httpd.server_close()
        self.thread.join(timeout=2)
        self.temp_dir.cleanup()

    def request(
        self,
        method: str,
        path: str,
        body: bytes | None = None,
        headers: dict[str, str] | None = None,
    ) -> tuple[int, dict[str, str], bytes]:
        connection = HTTPConnection(self.host, self.port, timeout=5)
        connection.request(method, path, body=body, headers=headers or {})
        response = connection.getresponse()
        result = response.status, dict(response.getheaders()), response.read()
        connection.close()
        return result

    def raw_response(self, request: bytes) -> tuple[int, bytes]:
        with create_connection((self.host, self.port), timeout=5) as sock:
            sock.settimeout(5)
            sock.sendall(request)
            sock.shutdown(1)
            response = b""
            while True:
                chunk = sock.recv(4096)
                if not chunk:
                    break
                response += chunk
        headers, body = response.split(b"\r\n\r\n", 1)
        return int(headers.split(b" ", 2)[1]), body

    def raw_status(self, request: bytes) -> int:
        return self.raw_response(request)[0]

    @staticmethod
    def auth_headers(**extra: str) -> dict[str, str]:
        return {"Authorization": "Bearer secret-token", **extra}

    def test_upload_errors_have_precise_status_codes(self) -> None:
        path = "/api/uploads"
        valid = server.json.dumps({"html": "<title>Valid</title>"}).encode()
        self.assertEqual(401, self.request("POST", path, valid)[0])
        self.assertEqual(
            400,
            self.request("POST", path, b"{bad", self.auth_headers())[0],
        )
        self.assertEqual(
            400,
            self.request("POST", path, b"[]", self.auth_headers())[0],
        )
        base_request = (
            b"POST /api/uploads HTTP/1.1\r\nHost: local\r\n"
            b"Authorization: Bearer secret-token\r\nConnection: close\r\n"
        )
        self.assertEqual(411, self.raw_status(base_request + b"\r\n"))
        self.assertEqual(
            400,
            self.raw_status(base_request + b"Content-Length: nope\r\n\r\n"),
        )
        self.assertEqual(
            400,
            self.raw_status(base_request + b"Content-Length: -1\r\n\r\n"),
        )
        oversized = str(server.MAX_REQUEST_BYTES + 1).encode()
        self.assertEqual(
            413,
            self.raw_status(base_request + b"Content-Length: " + oversized + b"\r\n\r\n"),
        )
        unsafe = server.json.dumps({"html": "<script>x</script>"}).encode()
        self.assertEqual(
            422,
            self.request("POST", path, unsafe, self.auth_headers())[0],
        )
        missing = server.json.dumps(
            {"html": "<title>Missing</title>", "draftId": "missing1"}
        ).encode()
        self.assertEqual(
            404,
            self.request("POST", path, missing, self.auth_headers())[0],
        )

    def test_upload_rejects_structural_sanitizer_bypasses(self) -> None:
        unsafe_documents = {
            "duplicate unsafe then safe href": (
                '<title>Unsafe</title><a href="javascript:alert(1)" '
                'href="https://example.test">link</a>'
            ),
            "duplicate refresh then non-refresh http-equiv": (
                '<title>Unsafe</title><meta http-equiv="refresh" '
                'http-equiv="content-type" content="0;url=https://example.test">'
            ),
            "CSS-escaped lowercase scheme": (
                r"<title>Unsafe</title><p style='background:url(javas\63ript:alert(1))'>x</p>"
            ),
            "CSS-escaped mixed-case scheme with whitespace": (
                r"<title>Unsafe</title><style>p { background: URL( JaVaS\63 RiPt : alert(1) ) }</style>"
            ),
        }

        for label, html_doc in unsafe_documents.items():
            with self.subTest(label=label):
                body = server.json.dumps({"html": html_doc}).encode("utf-8")
                status, _headers, response = self.request(
                    "POST", "/api/uploads", body, self.auth_headers()
                )
                self.assertEqual(422, status, response)
                self.assertFalse(server.json.loads(response)["ok"])

        safe = server.json.dumps(
            {
                "html": (
                    "<title>Safe</title><p>javascript: is visible text</p>"
                    "<style>p { background: url('https://example.test/image.png'); "
                    "color: rebeccapurple; }</style>"
                )
            }
        ).encode("utf-8")
        safe_status, _headers, safe_response = self.request(
            "POST", "/api/uploads", safe, self.auth_headers()
        )
        self.assertEqual(201, safe_status, safe_response)

    def test_unexpected_upload_failure_returns_generic_json_500_and_is_logged(self) -> None:
        body = server.json.dumps({"html": "<title>Valid</title>"}).encode("utf-8")
        failure = RuntimeError("private traceback detail")

        with (
            patch.object(self.store, "upsert", side_effect=failure),
            patch.object(server.Handler, "log_message") as log_spy,
        ):
            status, headers, response = self.request(
                "POST", "/api/uploads", body, self.auth_headers()
            )

        self.assertEqual(500, status)
        self.assertEqual("application/json; charset=utf-8", headers["Content-Type"])
        self.assertEqual(
            {"ok": False, "error": "Internal server error."},
            server.json.loads(response),
        )
        self.assertNotIn(b"private traceback detail", response)
        self.assertNotIn(b"RuntimeError", response)
        self.assertTrue(
            any(
                call.args == ("unexpected upload error: %r", failure)
                for call in log_spy.call_args_list
            ),
            log_spy.call_args_list,
        )
        self.assertEqual(200, self.request("GET", "/healthz")[0])

    def test_upload_public_url_uses_configured_base_url(self) -> None:
        body = server.json.dumps({"html": "<title>Configured</title>"}).encode()

        status, _headers, response = self.request(
            "POST", "/api/uploads", body, self.auth_headers()
        )

        result = server.json.loads(response)
        self.assertEqual(201, status)
        self.assertEqual(
            f"https://configured.example/tailplan/d/{result['draftId']}",
            result["publicUrl"],
        )

    def test_authentication_calls_hmac_compare_digest(self) -> None:
        compare_digest = server.hmac.compare_digest
        with patch.object(server.hmac, "compare_digest", wraps=compare_digest) as spy:
            status, _headers, _body = self.request(
                "GET", "/api/me", headers=self.auth_headers()
            )

        self.assertEqual(200, status)
        spy.assert_called_once_with("Bearer secret-token", "Bearer secret-token")

    def test_content_length_accepts_only_positive_bounded_ascii_decimal(self) -> None:
        base_request = (
            b"POST /api/uploads HTTP/1.1\r\nHost: local\r\n"
            + f"Authorization: Bearer {self.httpd.token}\r\n".encode("ascii")
            + b"Connection: close\r\n"
        )
        cases = (
            ("missing", None, 411, "Content-Length is required"),
            ("plus sign", b"+2", 400, "ASCII decimal digits"),
            ("underscore", b"1_0", 400, "ASCII decimal digits"),
            ("negative", b"-1", 400, "ASCII decimal digits"),
            ("zero", b"0", 400, "positive"),
            ("trailing whitespace", b"2 ", 400, "ASCII decimal digits"),
            ("non-ASCII digit", b"\xb2", 400, "ASCII decimal digits"),
            ("over envelope", str(server.MAX_REQUEST_BYTES + 1).encode(), 413, "too large"),
            ("excessive numeral", b"9" * 5000, 413, "too large"),
        )
        for label, value, expected_status, expected_error in cases:
            with self.subTest(label=label):
                length_line = b"" if value is None else b"Content-Length: " + value + b"\r\n"
                status, body = self.raw_response(base_request + length_line + b"\r\n[]")
                self.assertEqual(expected_status, status)
                self.assertIn(expected_error, server.json.loads(body)["error"])

    def test_multiple_content_length_headers_are_rejected_with_stable_json(self) -> None:
        base_request = (
            b"POST /api/uploads HTTP/1.1\r\nHost: local\r\n"
            b"Authorization: Bearer secret-token\r\nConnection: close\r\n"
        )
        expected = {
            "ok": False,
            "error": "Multiple Content-Length headers are not allowed.",
        }
        cases = {
            "identical": b"Content-Length: 2\r\nContent-Length: 2\r\n",
            "conflicting": b"Content-Length: 2\r\nContent-Length: 3\r\n",
        }

        for label, length_headers in cases.items():
            with self.subTest(label=label):
                status, body = self.raw_response(
                    base_request + length_headers + b"\r\n{}"
                )
                self.assertEqual(400, status)
                self.assertEqual(expected, server.json.loads(body))

    def test_near_limit_html_with_default_json_unicode_escaping_is_accepted(self) -> None:
        prefix = "<!doctype html><title>Escaped Unicode</title>"
        emoji_count = (server.MAX_HTML_BYTES - len(prefix.encode("utf-8"))) // 4
        html_doc = prefix + ("🚀" * emoji_count)
        body = server.json.dumps({"html": html_doc}).encode("utf-8")
        self.assertLessEqual(len(html_doc.encode("utf-8")), server.MAX_HTML_BYTES)
        self.assertGreater(len(body), server.MAX_HTML_BYTES + 64 * 1024)

        status, _headers, response = self.request(
            "POST", "/api/uploads", body, self.auth_headers()
        )

        self.assertEqual(201, status, response)

    def test_escaped_lone_surrogate_is_rejected_without_crashing_connection(self) -> None:
        body = b'{"html":"<title>Bad scalar</title>\\ud800"}'

        status, _headers, response = self.request(
            "POST", "/api/uploads", body, self.auth_headers()
        )

        self.assertIn(status, {400, 422}, response)
        self.assertEqual(200, self.request("GET", "/healthz")[0])

    def test_escaped_lone_surrogate_filename_is_rejected_without_crashing(self) -> None:
        body = b'{"html":"<title>Valid</title>","filename":"\\ud800"}'

        status, _headers, response = self.request(
            "POST", "/api/uploads", body, self.auth_headers()
        )

        self.assertEqual(400, status, response)
        self.assertEqual(200, self.request("GET", "/healthz")[0])

    def test_health_has_build_identity_and_readiness_checks_storage(self) -> None:
        health_status, _headers, health_body = self.request("GET", "/healthz")
        health = server.json.loads(health_body)
        self.assertEqual(200, health_status)
        self.assertRegex(health["build"], r"^[a-f0-9]{12}$")

        self.assertEqual(200, self.request("GET", "/readyz")[0])
        self.store.meta.write_text("{corrupt", encoding="utf-8")
        ready_status, _headers, ready_body = self.request("GET", "/readyz")
        self.assertEqual(503, ready_status)
        self.assertFalse(server.json.loads(ready_body)["ok"])

        upload = server.json.dumps({"html": "<title>Unavailable</title>"}).encode()
        self.assertEqual(
            503,
            self.request("POST", "/api/uploads", upload, self.auth_headers())[0],
        )

    def test_readiness_rejects_malformed_inner_metadata_without_rewriting_it(self) -> None:
        created = self.store.upsert(
            "<title>Valid</title>", "valid.html", None, "https://tailplan.test"
        )
        malformed = self.store.load_meta()
        malformed["drafts"][created["draftId"]]["fileSha256"] = "broken"
        raw = server.json.dumps(malformed, sort_keys=True).encode("utf-8")
        self.store.meta.write_bytes(raw)

        status, _headers, body = self.request("GET", "/readyz")

        self.assertEqual(503, status)
        self.assertFalse(server.json.loads(body)["ok"])
        self.assertEqual(raw, self.store.meta.read_bytes())

    def test_deleted_recorded_current_object_returns_generic_json_503(self) -> None:
        created = self.store.upsert(
            "<title>Deleted</title>", "deleted.html", None, "https://tailplan.test"
        )
        current = Path(
            self.store.load_meta()["drafts"][created["draftId"]]["currentObject"]
        )
        current.unlink()

        status, headers, body = self.request("GET", f"/d/{created['draftId']}")

        self.assertEqual(503, status)
        self.assertEqual("application/json; charset=utf-8", headers["Content-Type"])
        self.assertEqual(
            {"ok": False, "error": "Storage unavailable."},
            server.json.loads(body),
        )

    def test_invalid_utf8_recorded_current_object_returns_generic_json_503(self) -> None:
        created = self.store.upsert(
            "<title>Invalid UTF-8</title>",
            "invalid.html",
            None,
            "https://tailplan.test",
        )
        current = Path(
            self.store.load_meta()["drafts"][created["draftId"]]["currentObject"]
        )
        current.write_bytes(b"\xff\xfe")

        ready_status, _headers, _body = self.request("GET", "/readyz")
        status, headers, body = self.request("GET", f"/d/{created['draftId']}")

        self.assertEqual(503, ready_status)
        self.assertEqual(503, status)
        self.assertEqual("application/json; charset=utf-8", headers["Content-Type"])
        self.assertEqual(
            {"ok": False, "error": "Storage unavailable."},
            server.json.loads(body),
        )

    def test_checksum_mismatched_current_object_returns_generic_json_503(self) -> None:
        created = self.store.upsert(
            "<title>Original</title>", "original.html", None, "https://tailplan.test"
        )
        current = Path(
            self.store.load_meta()["drafts"][created["draftId"]]["currentObject"]
        )
        current.write_text("<title>Tampered</title>", encoding="utf-8")

        ready_status, _headers, _body = self.request("GET", "/readyz")
        status, headers, body = self.request("GET", f"/d/{created['draftId']}")

        self.assertEqual(503, ready_status)
        self.assertEqual(503, status)
        self.assertEqual("application/json; charset=utf-8", headers["Content-Type"])
        self.assertEqual(
            {"ok": False, "error": "Storage unavailable."},
            server.json.loads(body),
        )

    def test_symlinked_current_object_returns_generic_json_503(self) -> None:
        created = self.store.upsert(
            "<title>Linked</title>", "linked.html", None, "https://tailplan.test"
        )
        current = Path(
            self.store.load_meta()["drafts"][created["draftId"]]["currentObject"]
        )
        actual = current.with_name("actual.html")
        current.rename(actual)
        current.symlink_to(actual)

        ready_status, _headers, _body = self.request("GET", "/readyz")
        status, headers, body = self.request("GET", f"/d/{created['draftId']}")

        self.assertEqual(503, ready_status)
        self.assertEqual(503, status)
        self.assertEqual("application/json; charset=utf-8", headers["Content-Type"])
        self.assertEqual(
            {"ok": False, "error": "Storage unavailable."},
            server.json.loads(body),
        )

    def test_current_object_path_resolution_failure_returns_generic_json_503(self) -> None:
        created = self.store.upsert(
            "<title>Loop</title>", "loop.html", None, "https://tailplan.test"
        )
        current = Path(
            self.store.load_meta()["drafts"][created["draftId"]]["currentObject"]
        )
        current.unlink()
        current.symlink_to(current)

        ready_status, _headers, _body = self.request("GET", "/readyz")
        status, headers, body = self.request("GET", f"/d/{created['draftId']}")

        self.assertEqual(503, ready_status)
        self.assertEqual(503, status)
        self.assertEqual("application/json; charset=utf-8", headers["Content-Type"])
        self.assertEqual(
            {"ok": False, "error": "Storage unavailable."},
            server.json.loads(body),
        )

    def test_unknown_draft_and_version_remain_404(self) -> None:
        created = self.store.upsert(
            "<title>Known</title>", "known.html", None, "https://tailplan.test"
        )

        self.assertEqual(404, self.request("GET", "/d/missing1")[0])
        self.assertEqual(404, self.request("GET", f"/d/{created['draftId']}/v/2")[0])

    def test_latest_historical_and_content_routes_render_documents_directly(self) -> None:
        first_doc = (
            '<!doctype html><html><head><title>First > Title</title></head><body>'
            '<h1>Version One</h1><a title="1 > 0" href="https://example.com">Link</a>'
            "</body></html>"
        )
        first_payload = server.json.dumps({"html": first_doc}).encode()
        status, _headers, body = self.request(
            "POST", "/api/uploads", first_payload, self.auth_headers()
        )
        self.assertEqual(201, status)
        draft_id = server.json.loads(body)["draftId"]

        second_doc = "<!doctype html><html><head><title>Second</title></head><body>Version Two</body></html>"
        second_payload = server.json.dumps({"html": second_doc, "draftId": draft_id}).encode()
        self.assertEqual(
            200,
            self.request("POST", "/api/uploads", second_payload, self.auth_headers())[0],
        )

        routes = {
            f"/d/{draft_id}": "Version Two",
            f"/d/{draft_id}/content": "Version Two",
            f"/d/{draft_id}/v/1": "Version One",
            f"/d/{draft_id}/v/1/content": "Version One",
        }
        for route, expected in routes.items():
            with self.subTest(route=route):
                view_status, headers, raw = self.request("GET", route)
                rendered = raw.decode("utf-8")
                self.assertEqual(200, view_status)
                self.assertTrue(rendered.startswith("<!doctype html>"))
                self.assertIn(expected, rendered)
                self.assertNotIn("<iframe", rendered.lower())
                self.assertNotIn("srcdoc", rendered.lower())
                self.assertEqual("no-store", headers["Cache-Control"])
                self.assertEqual("nosniff", headers["X-Content-Type-Options"])
                self.assertEqual("no-referrer", headers["Referrer-Policy"])
                self.assertEqual("DENY", headers["X-Frame-Options"])
                csp = headers["Content-Security-Policy"]
                self.assertIn("default-src 'none'", csp)
                self.assertIn("style-src 'unsafe-inline'", csp)
                self.assertIn("img-src https: data:", csp)
                self.assertIn("form-action 'none'", csp)
                self.assertIn("frame-ancestors 'none'", csp)

        _status, _headers, historical_raw = self.request("GET", f"/d/{draft_id}/v/1")
        historical = historical_raw.decode("utf-8")
        self.assertIn('<title>First > Title</title>', historical)
        self.assertIn('title="1 > 0"', historical)
        self.assertIn('target="_blank"', historical)
        self.assertIn('rel="noopener noreferrer"', historical)

    def test_primary_viewer_get_and_head_redirect_preserve_path_and_safe_query(self) -> None:
        self.httpd.redirect_view_base_url = "https://tailplan-https.example/view"
        draft_id = "abcdef12"
        routes = (
            f"/d/{draft_id}",
            f"/d/{draft_id}/",
            f"/d/{draft_id}/content",
            f"/d/{draft_id}/content/",
            f"/d/{draft_id}/v/1",
            f"/d/{draft_id}/v/1/",
            f"/d/{draft_id}/v/1/content",
            f"/d/{draft_id}/v/1/content/?view=full&label=%E2%9C%93",
        )

        for method in ("GET", "HEAD"):
            for route in routes:
                with self.subTest(method=method, route=route):
                    status, headers, body = self.request(method, route)
                    self.assertEqual(308, status)
                    self.assertEqual(
                        f"https://tailplan-https.example/view{route}",
                        headers["Location"],
                    )
                    self.assertEqual("no-store", headers["Cache-Control"])
                    self.assertEqual(b"", body)

    def test_primary_health_ready_and_api_routes_never_redirect(self) -> None:
        self.httpd.redirect_view_base_url = "https://tailplan-https.example"

        for route, headers in (
            ("/healthz", {}),
            ("/readyz", {}),
            ("/api/me", self.auth_headers()),
        ):
            with self.subTest(route=route):
                status, response_headers, _body = self.request(
                    "GET", route, headers=headers
                )
                self.assertEqual(200, status)
                self.assertNotIn("Location", response_headers)

        payload = server.json.dumps({"html": "<title>Direct API</title>"}).encode()
        status, headers, _body = self.request(
            "POST", "/api/uploads", payload, self.auth_headers()
        )
        self.assertEqual(201, status)
        self.assertNotIn("Location", headers)

        status, headers, _body = self.request(
            "POST", "/d/abcdef12", b"credentials", self.auth_headers()
        )
        self.assertEqual(404, status)
        self.assertNotIn("Location", headers)

    def test_malformed_and_out_of_range_version_segments_are_rejected_before_storage(self) -> None:
        invalid_paths = (
            "/d/abcdef/v/0",
            "/d/abcdef/v/00",
            "/d/abcdef/v/01",
            "/d/abcdef/v/+1",
            "/d/abcdef/v/1_0",
            "/d/abcdef/v/1000000000",
        )

        with patch.object(self.store, "get", wraps=self.store.get) as get_spy:
            for path in invalid_paths:
                with self.subTest(path=path):
                    self.assertEqual(404, self.request("GET", path)[0])

        get_spy.assert_not_called()


class DualListenerTests(unittest.TestCase):
    @staticmethod
    def request(
        address: tuple[str, int],
        method: str,
        path: str,
        body: bytes | None = None,
        headers: dict[str, str] | None = None,
    ) -> tuple[int, dict[str, str], bytes]:
        connection = HTTPConnection(*address, timeout=5)
        connection.request(method, path, body=body, headers=headers or {})
        response = connection.getresponse()
        result = response.status, dict(response.getheaders()), response.read()
        connection.close()
        return result

    def test_listeners_share_store_token_and_base_url_while_proxy_serves_content(self) -> None:
        with TemporaryDirectory() as td:
            store = server.Store(Path(td))
            primary, proxy = server.create_servers(
                ("127.0.0.1", 0),
                ("127.0.0.1", 0),
                store=store,
                token="shared-token",
                base_url="https://tailplan.example.test",
                redirect_view_base_url="https://tailplan-https.example.test",
            )
            self.assertIsNotNone(proxy)
            assert proxy is not None
            threads = [
                Thread(target=httpd.serve_forever, daemon=True)
                for httpd in (primary, proxy)
            ]
            for thread in threads:
                thread.start()
            try:
                self.assertIs(primary.store, proxy.store)
                self.assertEqual("shared-token", primary.token)
                self.assertEqual(primary.token, proxy.token)
                self.assertEqual(primary.base_url, proxy.base_url)
                self.assertEqual("https://tailplan.example.test", proxy.base_url)
                self.assertEqual(
                    "https://tailplan-https.example.test",
                    primary.redirect_view_base_url,
                )
                self.assertEqual("", proxy.redirect_view_base_url)

                auth = {"Authorization": "Bearer shared-token"}
                first = server.json.dumps(
                    {"html": "<title>Shared</title><p>Version One</p>"}
                ).encode()
                status, _headers, body = self.request(
                    primary.server_address, "POST", "/api/uploads", first, auth
                )
                self.assertEqual(201, status, body)
                draft_id = server.json.loads(body)["draftId"]

                second = server.json.dumps(
                    {
                        "html": "<title>Shared</title><p>Version Two</p>",
                        "draftId": draft_id,
                    }
                ).encode()
                status, _headers, body = self.request(
                    primary.server_address, "POST", "/api/uploads", second, auth
                )
                self.assertEqual(200, status, body)

                for route, expected in (
                    (f"/d/{draft_id}", b"Version Two"),
                    (f"/d/{draft_id}/content", b"Version Two"),
                    (f"/d/{draft_id}/v/1", b"Version One"),
                    (f"/d/{draft_id}/v/1/content", b"Version One"),
                ):
                    with self.subTest(route=route):
                        status, headers, content = self.request(
                            proxy.server_address, "GET", route
                        )
                        self.assertEqual(200, status)
                        self.assertIn(expected, content)
                        self.assertEqual("nosniff", headers["X-Content-Type-Options"])
                        self.assertEqual("no-referrer", headers["Referrer-Policy"])
                        self.assertEqual("DENY", headers["X-Frame-Options"])
                        self.assertIn(
                            "frame-ancestors 'none'",
                            headers["Content-Security-Policy"],
                        )

                status, headers, content = self.request(
                    proxy.server_address, "HEAD", f"/d/{draft_id}"
                )
                self.assertEqual(200, status)
                self.assertEqual(b"", content)
                self.assertEqual("DENY", headers["X-Frame-Options"])
            finally:
                for httpd in (primary, proxy):
                    httpd.shutdown()
                    httpd.server_close()
                for thread in threads:
                    thread.join(timeout=2)

    def test_proxy_bind_conflict_fails_clearly_before_primary_is_created(self) -> None:
        blocker = socket()
        blocker.bind(("127.0.0.1", 0))
        blocker.listen()
        try:
            with TemporaryDirectory() as td, self.assertRaisesRegex(
                server.ListenerStartupError, "proxy listener"
            ):
                server.create_servers(
                    ("127.0.0.1", 0),
                    blocker.getsockname(),
                    store=server.Store(Path(td)),
                    token="token",
                    base_url="https://tailplan.example.test",
                    redirect_view_base_url="",
                )
        finally:
            blocker.close()

    def test_primary_bind_failure_closes_already_bound_proxy_socket(self) -> None:
        primary_blocker = socket()
        primary_blocker.bind(("127.0.0.1", 0))
        primary_blocker.listen()
        proxy_probe = socket()
        proxy_probe.bind(("127.0.0.1", 0))
        proxy_address = proxy_probe.getsockname()
        proxy_probe.close()
        try:
            with TemporaryDirectory() as td, self.assertRaisesRegex(
                server.ListenerStartupError, "primary listener"
            ):
                server.create_servers(
                    primary_blocker.getsockname(),
                    proxy_address,
                    store=server.Store(Path(td)),
                    token="token",
                    base_url="https://tailplan.example.test",
                    redirect_view_base_url="",
                )

            rebound = socket()
            try:
                rebound.bind(proxy_address)
            finally:
                rebound.close()
        finally:
            primary_blocker.close()

    def test_runner_closes_both_sockets_and_joins_proxy_after_primary_failure(self) -> None:
        with TemporaryDirectory() as td:
            primary, proxy = server.create_servers(
                ("127.0.0.1", 0),
                ("127.0.0.1", 0),
                store=server.Store(Path(td)),
                token="token",
                base_url="https://tailplan.example.test",
                redirect_view_base_url="",
            )
            assert proxy is not None
            primary_address = primary.server_address
            proxy_address = proxy.server_address

            with (
                patch.object(
                    primary,
                    "serve_forever",
                    side_effect=RuntimeError("primary startup failed"),
                ),
                self.assertRaisesRegex(RuntimeError, "primary startup failed"),
            ):
                server.run_servers(primary, proxy)

            self.assertFalse(
                any(thread.name == "tailplan-proxy" for thread in active_threads())
            )
            for address in (primary_address, proxy_address):
                rebound = socket()
                try:
                    rebound.bind(address)
                finally:
                    rebound.close()

    def test_runner_closes_sockets_without_joining_when_proxy_thread_cannot_start(self) -> None:
        with TemporaryDirectory() as td:
            primary, proxy = server.create_servers(
                ("127.0.0.1", 0),
                ("127.0.0.1", 0),
                store=server.Store(Path(td)),
                token="token",
                base_url="https://tailplan.example.test",
                redirect_view_base_url="",
            )
            assert proxy is not None
            primary_address = primary.server_address
            proxy_address = proxy.server_address
            failed_thread = Mock()
            failed_thread.start.side_effect = RuntimeError("thread start failed")
            failed_thread.is_alive.return_value = False

            with (
                patch.object(server.threading, "Thread", return_value=failed_thread),
                self.assertRaisesRegex(RuntimeError, "thread start failed"),
            ):
                server.run_servers(primary, proxy)

            failed_thread.join.assert_not_called()
            for address in (primary_address, proxy_address):
                rebound = socket()
                try:
                    rebound.bind(address)
                finally:
                    rebound.close()

    def test_main_builds_and_runs_configured_listener_pair(self) -> None:
        with TemporaryDirectory() as td:
            root = Path(td)
            token_file = root / "token"
            token_file.write_text("shared-token\n", encoding="utf-8")
            args = server.argparse.Namespace(
                host="100.64.0.1",
                port=9127,
                proxy_host="127.0.0.1",
                proxy_port=9128,
                data_dir=str(root / "data"),
                token_file=str(token_file),
                base_url="https://tailplan.example.test/",
                redirect_view_base_url="https://tailplan-https.example.test",
            )
            primary = Mock(server_address=("100.64.0.1", 9127))
            proxy = Mock(server_address=("127.0.0.1", 9128))

            with (
                patch.object(server, "parse_args", return_value=args),
                patch.object(
                    server, "create_servers", return_value=(primary, proxy)
                ) as create_spy,
                patch.object(server, "run_servers") as run_spy,
                patch.object(server, "TailplanHTTPServer", return_value=Mock()),
            ):
                result = server.main()

            self.assertEqual(0, result)
            create_spy.assert_called_once()
            call = create_spy.call_args
            self.assertEqual(("100.64.0.1", 9127), call.args[0])
            self.assertEqual(("127.0.0.1", 9128), call.args[1])
            self.assertEqual("shared-token", call.kwargs["token"])
            self.assertEqual(
                "https://tailplan-https.example.test",
                call.kwargs["redirect_view_base_url"],
            )
            run_spy.assert_called_once_with(primary, proxy)


class ArgumentParserTests(unittest.TestCase):
    def parse(self, *arguments: str):
        with patch.dict(server.os.environ, {}, clear=True):
            return server.parse_args(list(arguments))

    def test_proxy_listener_arguments_must_be_supplied_as_a_pair(self) -> None:
        for arguments in (
            ("--proxy-host", "127.0.0.1"),
            ("--proxy-port", "9128"),
        ):
            with self.subTest(arguments=arguments), self.assertRaises(SystemExit):
                self.parse(*arguments)

    def test_listener_ports_must_be_in_the_tcp_port_range(self) -> None:
        for option in ("--port", "--proxy-port"):
            for value in ("0", "65536", "not-a-port"):
                arguments = [option, value]
                if option == "--proxy-port":
                    arguments = ["--proxy-host", "127.0.0.1", *arguments]
                with (
                    self.subTest(option=option, value=value),
                    self.assertRaises(SystemExit),
                ):
                    self.parse(*arguments)

    def test_proxy_listener_must_differ_from_primary_listener(self) -> None:
        with self.assertRaises(SystemExit):
            self.parse(
                "--host",
                "127.0.0.1",
                "--port",
                "9127",
                "--proxy-host",
                "127.0.0.1",
                "--proxy-port",
                "9127",
            )

    def test_redirect_view_base_url_requires_clean_absolute_https_url(self) -> None:
        parsed = self.parse(
            "--redirect-view-base-url", "https://tailplan.example.test/view/"
        )
        self.assertEqual(
            "https://tailplan.example.test/view", parsed.redirect_view_base_url
        )

        invalid = (
            "tailplan.example.test",
            "http://tailplan.example.test",
            "https://user:secret@tailplan.example.test",
            "https://tailplan.example.test/view?token=secret",
            "https://tailplan.example.test/view#fragment",
        )
        for value in invalid:
            with self.subTest(value=value), self.assertRaises(SystemExit):
                self.parse("--redirect-view-base-url", value)

    def test_options_absent_keep_single_listener_defaults(self) -> None:
        parsed = self.parse()

        self.assertEqual("127.0.0.1", parsed.host)
        self.assertEqual(9127, parsed.port)
        self.assertIsNone(parsed.proxy_host)
        self.assertIsNone(parsed.proxy_port)
        self.assertEqual("", parsed.redirect_view_base_url)


if __name__ == "__main__":
    unittest.main()
