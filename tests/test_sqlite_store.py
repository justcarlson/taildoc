from __future__ import annotations

import hashlib
import hmac
import json
import os
import shutil
import socket
import sqlite3
import sys
import tempfile
import threading
import time
import unittest
from contextlib import closing
from http.client import HTTPConnection
from pathlib import Path
from unittest.mock import Mock, patch
from urllib.parse import urlencode

import tailplan_server as server

BASE_URL = "https://tailplan.test"


def write_legacy_metadata(root: Path, data: dict) -> bytes:
    """Make `root` look like a v0.4.0 data directory. Return the file bytes."""
    for path in root.glob("tailplan.db*"):
        path.unlink()
    payload = (json.dumps(data, indent=2) + "\n").encode()
    (root / "metadata.json").write_bytes(payload)
    return payload


class MigrationTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name) / "data"
        self.addCleanup(self.temporary.cleanup)

    def build_legacy_data(self) -> dict:
        store = server.Store(self.root)
        first = store.upsert("<title>One</title><p>a</p>", "one.html", None, BASE_URL, "key-1")
        store.upsert("<title>One v2</title><p>b</p>", "one.html", first["draftId"], BASE_URL, "key-2")
        store.upsert("<title>Two</title>", "two.html", None, BASE_URL, "key-3")
        store.identity_account("guest@example.test", "Guest", "")
        store.create_key("local", "Agent")
        store.set_favorite("local", first["draftId"], True)
        data = store.load_meta()
        data["futureTopLevel"] = {"keep": [1, 2, 3]}
        data["drafts"][first["draftId"]]["futureDraftField"] = {"keep": True}
        return data

    def test_legacy_metadata_migrates_without_loss(self) -> None:
        data = self.build_legacy_data()
        original = write_legacy_metadata(self.root, data)

        store = server.Store(self.root)

        self.assertEqual(data, store.load_meta())
        self.assertEqual(list(data["drafts"]), list(store.load_meta()["drafts"]))
        self.assertEqual(list(data["idempotency"]), list(store.load_meta()["idempotency"]))
        self.assertEqual(original, store.backup.read_bytes())
        self.assertEqual(0o600, store.backup.stat().st_mode & 0o777)
        self.assertEqual(0o600, store.db_path.stat().st_mode & 0o777)
        self.assertFalse(store.meta.exists())
        self.assertEqual([], list(self.root.glob(".*.tmp")))

    def test_backup_restores_the_original_file_for_rollback(self) -> None:
        data = self.build_legacy_data()
        original = write_legacy_metadata(self.root, data)
        store = server.Store(self.root)
        store.upsert("<title>After</title>", "after.html", None, BASE_URL)

        restored = json.loads(store.backup.read_bytes())

        self.assertEqual(data, restored)
        self.assertEqual(original, store.backup.read_bytes())

    def test_draft_files_are_not_touched(self) -> None:
        data = self.build_legacy_data()
        write_legacy_metadata(self.root, data)
        before = {
            path: path.read_bytes() for path in sorted((self.root / "drafts").rglob("*.html"))
        }

        server.Store(self.root)

        after = {
            path: path.read_bytes() for path in sorted((self.root / "drafts").rglob("*.html"))
        }
        self.assertEqual(before, after)

    def test_database_settings(self) -> None:
        store = server.Store(self.root)
        with closing(store._connect(store.db_path)) as connection:
            self.assertEqual("wal", connection.execute("PRAGMA journal_mode").fetchone()[0])
            self.assertEqual(1, connection.execute("PRAGMA foreign_keys").fetchone()[0])
            self.assertEqual(5000, connection.execute("PRAGMA busy_timeout").fetchone()[0])
            self.assertEqual(4, connection.execute("PRAGMA user_version").fetchone()[0])
            tables = {
                row[0]
                for row in connection.execute("SELECT name FROM sqlite_master WHERE type = 'table'")
            }
        self.assertLessEqual(
            {"accounts", "drafts", "draft_versions", "api_keys", "idempotency_keys"}, tables
        )

    def test_rows_land_in_the_indexed_tables(self) -> None:
        data = self.build_legacy_data()
        write_legacy_metadata(self.root, data)
        store = server.Store(self.root)

        with closing(sqlite3.connect(store.db_path)) as connection:
            counts = {
                table: connection.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
                for table in ("accounts", "drafts", "draft_versions", "api_keys", "idempotency_keys")
            }

        self.assertEqual(len(data["accounts"]), counts["accounts"])
        self.assertEqual(len(data["drafts"]), counts["drafts"])
        self.assertEqual(
            sum(len(draft["versions"]) for draft in data["drafts"].values()),
            counts["draft_versions"],
        )
        self.assertEqual(len(data["apiKeys"]), counts["api_keys"])
        self.assertEqual(len(data["idempotency"]), counts["idempotency_keys"])

    def test_deleting_a_draft_row_removes_its_versions(self) -> None:
        store = server.Store(self.root)
        created = store.upsert("<title>One</title>", "one.html", None, BASE_URL)
        store.upsert("<title>Two</title>", "one.html", created["draftId"], BASE_URL)

        with closing(store._connect(store.db_path)) as connection:
            connection.execute("DELETE FROM drafts WHERE draft_id = ?", (created["draftId"],))
            remaining = connection.execute("SELECT COUNT(*) FROM draft_versions").fetchone()[0]

        self.assertEqual(0, remaining)

    def test_existing_database_wins_over_a_stale_metadata_file(self) -> None:
        store = server.Store(self.root)
        created = store.upsert("<title>Live</title>", "live.html", None, BASE_URL)
        stale = b'{"drafts": {}}\n'
        store.meta.write_bytes(stale)

        reopened = server.Store(self.root)

        self.assertIn(created["draftId"], reopened.load_meta()["drafts"])
        self.assertEqual(stale, reopened.meta.read_bytes())
        self.assertFalse(reopened.backup.exists())

    def test_second_start_does_not_migrate_again(self) -> None:
        data = self.build_legacy_data()
        write_legacy_metadata(self.root, data)
        first = server.Store(self.root)
        backup = first.backup.read_bytes()
        first.upsert("<title>New</title>", "new.html", None, BASE_URL)

        second = server.Store(self.root)

        self.assertEqual(len(data["drafts"]) + 1, len(second.load_meta()["drafts"]))
        self.assertEqual(backup, second.backup.read_bytes())

    def test_empty_data_directory_creates_an_empty_database(self) -> None:
        store = server.Store(self.root)

        self.assertTrue(store.db_path.is_file())
        self.assertEqual({}, store.load_meta()["drafts"])
        self.assertFalse(store.meta.exists())
        self.assertFalse(store.backup.exists())

    def test_database_from_a_newer_release_is_refused(self) -> None:
        store = server.Store(self.root)
        with closing(sqlite3.connect(store.db_path)) as connection:
            connection.execute("PRAGMA user_version = 99")

        with self.assertRaises(server.StorageError):
            server.Store(self.root)

    def test_import_that_does_not_match_the_original_is_not_published(self) -> None:
        data = self.build_legacy_data()
        original = write_legacy_metadata(self.root, data)

        with (
            patch.object(
                server.Store,
                "_read_all",
                staticmethod(lambda _connection: {"drafts": {}}),
            ),
            self.assertRaises(server.StorageError),
        ):
            server.Store(self.root)

        self.assertEqual(original, (self.root / "metadata.json").read_bytes())
        self.assertFalse((self.root / "tailplan.db").exists())
        self.assertFalse((self.root / "metadata.json.pre-sqlite.bak").exists())
        self.assertEqual([], list(self.root.glob(".*.tmp")))
        self.assertEqual([], list(self.root.glob(".*migrating*")))

    def test_load_and_save_meta_round_trip_unknown_fields(self) -> None:
        store = server.Store(self.root)
        created = store.upsert("<title>One</title>", "one.html", None, BASE_URL)
        data = store.load_meta()
        data["futureTopLevel"] = {"n": 1}
        data["drafts"][created["draftId"]]["futureDraftField"] = [1, 2]
        data["drafts"][created["draftId"]]["versions"]["1"]["futureVersionField"] = "x"

        store.save_meta(data)

        self.assertEqual(data, store.load_meta())


class SessionSecretTests(unittest.TestCase):
    def test_derived_secret_is_domain_separated_hmac_of_the_token(self) -> None:
        expected = hmac.new(b"upload-token", b"tailplan-session-v1", hashlib.sha256).hexdigest()

        derived = server.derive_session_secret("upload-token")

        self.assertEqual(expected, derived)
        self.assertNotEqual("upload-token", derived)
        self.assertNotEqual(derived, server.derive_session_secret("other-token"))

    def test_unset_secret_means_derive_from_the_token(self) -> None:
        self.assertIsNone(server.load_session_secret(None, None, "token"))
        self.assertIsNone(server.load_session_secret(None, "   ", "token"))
        self.assertIsNone(server.load_session_secret("", "", "token"))

    def test_secret_file_value_and_precedence(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            path = Path(td) / "secret"
            path.write_text("  file-secret\n", encoding="utf-8")

            self.assertEqual("file-secret", server.load_session_secret(str(path), "env", "token"))
            self.assertEqual("env-secret", server.load_session_secret(None, " env-secret ", "token"))

    def test_bad_secret_files_stop_startup(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            empty = Path(td) / "empty"
            empty.write_text("\n", encoding="utf-8")
            same = Path(td) / "same"
            same.write_text("token\n", encoding="utf-8")
            for label, path, value in (
                ("missing file", str(Path(td) / "missing"), None),
                ("empty file", str(empty), None),
                ("same as token", str(same), None),
                ("same as token in env", None, "token"),
            ):
                with self.subTest(label=label), self.assertRaises(SystemExit):
                    server.load_session_secret(path, value, "token")

    def test_option_and_environment_set_the_secret_file(self) -> None:
        with patch.dict(os.environ, {}, clear=False):
            os.environ.pop("TAILPLAN_SESSION_SECRET_FILE", None)
            self.assertIsNone(server.parse_args([]).session_secret_file)
            self.assertEqual(
                "/run/flag",
                server.parse_args(["--session-secret-file", "/run/flag"]).session_secret_file,
            )
        with patch.dict(os.environ, {"TAILPLAN_SESSION_SECRET_FILE": "/run/env"}):
            self.assertEqual("/run/env", server.parse_args([]).session_secret_file)
            self.assertEqual(
                "/run/flag",
                server.parse_args(["--session-secret-file", "/run/flag"]).session_secret_file,
            )

    def test_main_passes_the_secret_to_both_listeners(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            (root / "token").write_text("upload-token\n", encoding="utf-8")
            (root / "secret").write_text("dedicated-secret\n", encoding="utf-8")
            argv = [
                "tailplan-server",
                "--data-dir", str(root / "data"),
                "--token-file", str(root / "token"),
                "--session-secret-file", str(root / "secret"),
            ]
            primary = Mock(server_address=("127.0.0.1", 9127))
            with (
                patch.object(sys, "argv", argv),
                patch.object(server, "create_servers", return_value=(primary, None)) as create,
                patch.object(server, "run_servers"),
            ):
                result = server.main()

        self.assertEqual(0, result)
        self.assertEqual("dedicated-secret", create.call_args.kwargs["session_secret"])
        self.assertEqual("upload-token", create.call_args.kwargs["token"])

    def test_main_rejects_a_secret_equal_to_the_upload_token(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            (root / "token").write_text("upload-token\n", encoding="utf-8")
            (root / "secret").write_text("upload-token\n", encoding="utf-8")
            argv = [
                "tailplan-server",
                "--data-dir", str(root / "data"),
                "--token-file", str(root / "token"),
                "--session-secret-file", str(root / "secret"),
            ]
            with patch.object(sys, "argv", argv), self.assertRaises(SystemExit):
                server.main()


class SessionCookieTests(unittest.TestCase):
    token = "bootstrap-test"

    def start(self, session_secret: str | None) -> None:
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        store = server.Store(Path(temporary.name) / "data")
        httpd, _proxy = server.create_servers(
            ("127.0.0.1", 0), None, store=store, token=self.token,
            base_url="", redirect_view_base_url="", session_secret=session_secret,
        )
        self.httpd = httpd
        thread = threading.Thread(target=httpd.serve_forever, daemon=True)
        thread.start()

        def stop() -> None:
            httpd.shutdown()
            httpd.server_close()
            thread.join()

        self.addCleanup(stop)

    def request(self, path: str, method: str = "GET", body: bytes | None = None,
                headers: dict | None = None) -> tuple[int, dict, bytes]:
        connection = HTTPConnection("127.0.0.1", self.httpd.server_port, timeout=5)
        try:
            connection.request(method, path, body=body, headers=headers or {})
            response = connection.getresponse()
            return response.status, dict(response.headers), response.read()
        finally:
            connection.close()

    def sign_in(self) -> tuple[str, str]:
        """Return the login cookie and the session cookie from a real sign-in."""
        status, headers, body = self.request("/dashboard")
        self.assertEqual(200, status)
        login_cookie = headers["Set-Cookie"].split(";", 1)[0]
        csrf = body.split(b'name="csrf" value="', 1)[1].split(b'"', 1)[0].decode()
        form = urlencode({"csrf": csrf, "next": "/dashboard", "token": self.token}).encode()
        status, headers, body = self.request(
            "/auth/sign-in", "POST", form,
            {"Cookie": login_cookie, "Content-Type": "application/x-www-form-urlencoded"},
        )
        self.assertEqual(303, status, body)
        return login_cookie, headers["Set-Cookie"].split(";", 1)[0]

    def forged_session(self, secret: str) -> str:
        payload = {
            "accountId": "local", "apiKeyId": "bootstrap", "csrf": "csrf",
            "expires": int(time.time()) + 600,
        }
        return "tailplan_session=" + server.signed_value(payload, secret)

    def dashboard_requires_sign_in(self, cookie: str) -> bool:
        _status, _headers, body = self.request("/dashboard", headers={"Cookie": cookie})
        return b"Sign in" in body

    def test_derived_key_signs_cookies_and_the_raw_token_does_not(self) -> None:
        self.start(None)
        derived = server.derive_session_secret(self.token)

        login_cookie, session_cookie = self.sign_in()

        for cookie in (login_cookie, session_cookie):
            value = cookie.split("=", 1)[1]
            self.assertIsNone(server.read_signed(value, self.token))
        self.assertIsNotNone(server.read_signed(session_cookie.split("=", 1)[1], derived))
        self.assertIsNotNone(server.read_signed(login_cookie.split("=", 1)[1], derived))
        self.assertFalse(self.dashboard_requires_sign_in(session_cookie))
        self.assertTrue(self.dashboard_requires_sign_in(self.forged_session(self.token)))
        self.assertFalse(self.dashboard_requires_sign_in(self.forged_session(derived)))

    def test_dedicated_secret_signs_cookies_and_the_token_derivation_does_not(self) -> None:
        self.start("dedicated-secret")

        login_cookie, session_cookie = self.sign_in()

        for cookie in (login_cookie, session_cookie):
            value = cookie.split("=", 1)[1]
            self.assertIsNotNone(server.read_signed(value, "dedicated-secret"))
            self.assertIsNone(server.read_signed(value, server.derive_session_secret(self.token)))
            self.assertIsNone(server.read_signed(value, self.token))
        self.assertFalse(self.dashboard_requires_sign_in(session_cookie))
        self.assertTrue(
            self.dashboard_requires_sign_in(
                self.forged_session(server.derive_session_secret(self.token))
            )
        )

    def test_changing_the_upload_token_keeps_sessions_only_with_a_dedicated_secret(self) -> None:
        self.start("dedicated-secret")
        _login, session_cookie = self.sign_in()
        self.httpd.token = "rotated-token"
        self.assertFalse(self.dashboard_requires_sign_in(session_cookie))

        self.start(None)
        _login, derived_cookie = self.sign_in()
        self.httpd.token = "rotated-token"
        self.assertTrue(self.dashboard_requires_sign_in(derived_cookie))


class ReadinessTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.store = server.Store(Path(self.temporary.name) / "data")

    def make_drafts(self, count: int) -> list[dict]:
        return [
            self.store.upsert(f"<title>Draft {number}</title>", f"d{number}.html", None, BASE_URL)
            for number in range(count)
        ]

    def test_healthy_store_is_ready_and_leaves_no_probe_files(self) -> None:
        self.make_drafts(3)

        self.store.check_ready()

        self.assertEqual([], list(self.store.root.glob(".ready-*")))

    def test_ready_check_uses_quick_check_and_a_write_probe(self) -> None:
        statements: list[str] = []
        real_connect = server.Store._connect

        def traced(path: Path) -> sqlite3.Connection:
            connection = real_connect(path)
            connection.set_trace_callback(statements.append)
            return connection

        with patch.object(server.Store, "_connect", staticmethod(traced)):
            self.store.check_ready()

        text = "\n".join(statements).lower()
        self.assertIn("quick_check", text)
        self.assertNotIn("integrity_check", text)
        self.assertIn("begin immediate", text)

    def test_ready_check_does_not_read_or_hash_draft_files(self) -> None:
        self.make_drafts(40)
        for page in (self.store.drafts).rglob("v1.html"):
            page.write_text("<title>Tampered</title>", encoding="utf-8")

        with (
            patch.object(server.Store, "_read_page_object", side_effect=AssertionError("read")),
            patch.object(server.hashlib, "sha256", side_effect=AssertionError("hash")),
        ):
            self.store.check_ready()

    def test_ready_check_works_without_network_access(self) -> None:
        self.make_drafts(2)
        with (
            patch.object(socket.socket, "connect", side_effect=AssertionError("network")),
            patch.object(socket, "getaddrinfo", side_effect=AssertionError("dns")),
        ):
            self.store.check_ready()

    def test_ready_check_rejects_a_missing_draft_file(self) -> None:
        created = self.make_drafts(1)[0]
        (self.store.drafts / created["draftId"] / "v1.html").unlink()

        with self.assertRaises(server.StorageError):
            self.store.check_ready()

    def test_ready_check_rejects_a_corrupt_database(self) -> None:
        self.make_drafts(1)
        self.store.db_path.write_bytes(b"not a database" * 100)

        with self.assertRaises(server.StorageError):
            self.store.check_ready()

    def test_ready_check_rejects_damaged_database_pages(self) -> None:
        self.make_drafts(5)
        with closing(sqlite3.connect(self.store.db_path)) as connection:
            connection.execute("PRAGMA wal_checkpoint(TRUNCATE)")
        size = self.store.db_path.stat().st_size
        with open(self.store.db_path, "r+b") as stream:
            stream.seek(size // 2)
            stream.write(os.urandom(size // 4))

        with self.assertRaises(server.StorageError):
            self.store.check_ready()

    @unittest.skipIf(os.geteuid() == 0, "root can write to a read-only database")
    def test_ready_check_rejects_a_database_that_cannot_be_written(self) -> None:
        self.make_drafts(1)
        os.chmod(self.store.db_path, 0o400)
        self.addCleanup(os.chmod, self.store.db_path, 0o600)

        with self.assertRaises(server.StorageError):
            self.store.check_ready()

    def test_ready_check_rejects_an_unwritable_data_directory(self) -> None:
        self.make_drafts(1)
        with patch.object(server, "_atomic_write_text", side_effect=OSError("disk full")):
            with self.assertRaises(OSError):
                self.store.check_ready()

    def test_ready_check_stays_fast_with_many_drafts(self) -> None:
        self.make_drafts(150)

        started = time.perf_counter()
        for _ in range(5):
            self.store.check_ready()
        elapsed = (time.perf_counter() - started) / 5

        self.assertLess(elapsed, 1.0)

    def test_reopening_copied_sqlite_data_directory_rebases_object_paths(self) -> None:
        created = self.make_drafts(1)[0]
        copied = Path(self.temporary.name) / "copied-data"
        shutil.copytree(self.store.root, copied)

        restored = server.Store(copied)
        restored.check_ready()
        draft, body = restored.get(created["draftId"])
        self.assertIsNotNone(draft)
        self.assertIn("Draft 0", body)
        self.assertEqual(
            draft["currentObject"],
            str(copied / "drafts" / created["draftId"] / "v1.html"),
        )

    def test_tailplan_share_public_restores_versions_from_sqlite(self) -> None:
        import importlib.machinery
        import importlib.util

        created = self.store.upsert(
            None,
            None,
            None,
            BASE_URL,
            description="Public mirror check",
            slug="multi-public",
            files=[
                {"path": "index.html", "html": "<!doctype html><meta name=viewport content='width=device-width'><title>Multi</title><a href='page2.html'>p2</a>"},
                {"path": "page2.html", "html": "<!doctype html><meta name=viewport content='width=device-width'><title>P2</title><p>two</p>"},
            ],
        )
        script = Path(__file__).resolve().parents[1] / "bin" / "tailplan-share-public"
        loader = importlib.machinery.SourceFileLoader("tailplan_share_public_mod", str(script))
        spec = importlib.util.spec_from_loader(loader.name, loader)
        module = importlib.util.module_from_spec(spec)
        loader.exec_module(module)
        with patch.object(module, "TAILPLAN_META", self.store.root / "metadata.json"):
            draft = module.load_draft(created["draftId"])
        versions = draft.get("versions", {})
        self.assertIn("1", versions)
        self.assertEqual(versions["1"].get("pageCount"), 2)
        self.assertIn("page2.html", [p["path"] for p in versions["1"].get("pages", [])])


if __name__ == "__main__":
    unittest.main()
