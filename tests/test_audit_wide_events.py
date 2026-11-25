"""Cover the audit wide events: the `logs` standard, sections 2 and 3, and the no-PII rule."""
from __future__ import annotations

import contextlib
import io
import json
import os
import re
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import tailplan_server as server
from auth_harness import TOKEN, AuthCase

EMAIL = "Wide.Event@Example.Test"


class BuilderTests(unittest.TestCase):
    def build(self, *args, **kwargs) -> dict:
        return server.audit_wide_event(*args, **kwargs)

    def test_row_has_the_required_keys_and_types(self) -> None:
        row = self.build("draft.change", {"accountId": "acct-1"}, "draft", "abc-123", "success", {"action": "delete"})
        self.assertEqual(server.AUDIT_BASE_KEYS, set(row) - {"action", "client.address"})
        self.assertEqual("tailplan.draft.change", row["event"])
        self.assertEqual(1, row["event_version"])
        self.assertRegex(row["run_id"], r"^[0-9a-f]{32}$")
        self.assertRegex(row["started_at"], r"^\d{4}-\d\d-\d\dT\d\d:\d\d:\d\d\.\d{3}Z$")
        self.assertEqual((0, "success", "", server.TAILPLAN_VERSION),
                         (row["duration_ms"], row["outcome"], row["error_type"], row["release"]))
        self.assertEqual("delete", row["action"])
        self.assertNotIn("ts", row)
        self.assertNotIn("service", row)

    def test_run_ids_differ(self) -> None:
        self.assertNotEqual(self.build("auth.login", None, None, None, "success", None)["run_id"],
                            self.build("auth.login", None, None, None, "success", None)["run_id"])

    def test_denied_and_failed_map_to_error(self) -> None:
        denied = self.build("auth.denied", {"email": EMAIL}, "principal", EMAIL, "denied", {"reason": "state"})
        self.assertEqual(("error", "denied", "anonymous"),
                         (denied["outcome"], denied["error_type"], denied["actor_kind"]))
        failed = self.build("draft.upload", None, "draft", "x", "failure", None)
        self.assertEqual(("error", "failure"), (failed["outcome"], failed["error_type"]))

    def test_email_never_leaves_the_host(self) -> None:
        row = self.build("auth.denied", {"email": EMAIL, "accountId": None}, "principal", EMAIL, "denied",
                         {"method": "oidc", "actorLogin": EMAIL, "value": EMAIL, "type": "email",
                          "reason": EMAIL, "path": "/home/someone/x.html", "note": "token=abc"})
        text = json.dumps(row)
        self.assertNotIn("@", text)
        self.assertNotIn("example", text.lower())
        self.assertNotIn("/home", text)
        self.assertNotIn("token", text)
        self.assertRegex(row["target_id"], r"^[0-9a-f]{16}$")
        self.assertRegex(row["grant_hash"], r"^[0-9a-f]{16}$")
        self.assertNotIn("reason", row)

    def test_actor_hash_is_stable_keyed_and_case_blind(self) -> None:
        with patch.dict(os.environ, {"TAILPLAN_AUDIT_HMAC_KEY": "one"}):
            first = self.build("draft.view", {"email": EMAIL}, "draft", "d", "success", None)["actor_hash"]
            again = self.build("draft.view", {"email": EMAIL.lower()}, "draft", "d", "success", None)["actor_hash"]
        with patch.dict(os.environ, {"TAILPLAN_AUDIT_HMAC_KEY": "two"}):
            other = self.build("draft.view", {"email": EMAIL}, "draft", "d", "success", None)["actor_hash"]
        self.assertEqual(first, again)
        self.assertNotEqual(first, other)
        self.assertRegex(first, r"^[0-9a-f]{16}$")

    def test_hash_works_without_a_configured_key(self) -> None:
        with patch.dict(os.environ):
            os.environ.pop("TAILPLAN_AUDIT_HMAC_KEY", None)
            self.assertEqual(server.audit_hash("x"), server.audit_hash("x"))

    def test_actor_kinds(self) -> None:
        cases = [
            (None, "anonymous"),
            ({"accountId": "local", "apiKeyId": "bootstrap"}, "local"),
            ({"accountId": "anonymous", "apiKeyId": "anonymous"}, "anonymous"),
            ({"accountId": "a1", "apiKeyId": "key-1", "instanceRole": "admin"}, "api_key"),
            ({"accountId": "a1", "apiKeyId": "oidc", "instanceRole": "admin"}, "owner"),
            ({"accountId": "a1", "apiKeyId": "oidc", "instanceRole": None}, "member"),
            ({"accountId": None, "email": EMAIL}, "member"),
        ]
        for actor, kind in cases:
            with self.subTest(actor=actor):
                self.assertEqual(kind, self.build("auth.login", actor, None, None, "success", None)["actor_kind"])

    def test_target_id_pattern(self) -> None:
        self.assertEqual("ok_1.2:3-4", self.build("e.x", None, "draft", "ok_1.2:3-4", "success", None)["target_id"])
        for bad in ("a b", "a@b.c", "x" * 81, "../etc"):
            with self.subTest(bad=bad):
                self.assertEqual("other", self.build("e.x", None, "draft", bad, "success", None)["target_id"])
        self.assertEqual("other", self.build("e.x", None, "nonsense", None, "success", None)["target_type"])

    def test_detail_is_flat_and_scalar(self) -> None:
        row = self.build("draft.upload", {"accountId": "local"}, "draft", "d", "success", {
            "created": True, "version": 3, "visibility": "link", "workspace": "team",
            "grants": [{"type": "email", "value": EMAIL, "role": "viewer"}], "sourceIp": "100.64.0.9",
            "nested": {"a": 1}, "bypass": "x"})
        for key, value in row.items():
            self.assertNotIsInstance(value, (dict, list), key)
        self.assertEqual((True, 3, "link", "team", 1, "100.64.0.9"),
                         tuple(row[k] for k in ("created", "draft_version", "visibility", "workspace",
                                                "grant_count", "client.address")))
        self.assertNotIn("nested", row)

    def test_a_bad_value_is_mapped_or_dropped(self) -> None:
        row = self.build("draft.upload", None, "draft", "d", "success",
                         {"workspace": "has space", "visibility": "everyone", "sourceIp": "not-an-ip",
                          "version": "3", "action": "rm -rf"})
        for key in ("workspace", "visibility", "client.address", "draft_version", "action"):
            self.assertNotIn(key, row)

    def test_unknown_event_name_maps_to_other(self) -> None:
        self.assertEqual("tailplan.other", self.build("Bad Name=1", None, None, None, "success", None)["event"])


class RowCheckTests(unittest.TestCase):
    def good(self) -> dict:
        return server.audit_wide_event("auth.login", {"accountId": "a"}, "account", "a", "success", {"method": "oidc"})

    def test_good_row_passes(self) -> None:
        row = self.good()
        self.assertIs(row, server.check_audit_row(row))

    def test_rejections(self) -> None:
        mutations = {
            "missing key": lambda r: r.pop("release"),
            "extra key": lambda r: r.update(actor_email="a"),
            "ts": lambda r: r.update(ts="x"),
            "email value": lambda r: r.update(target_id="a@b.c"),
            "nested": lambda r: r.update(action={"a": 1}),
            "float": lambda r: r.update(duration_ms=1.5),
            "bad outcome": lambda r: r.update(outcome="denied"),
            "error without type": lambda r: r.update(outcome="error"),
            "bad kind": lambda r: r.update(actor_kind="root"),
            "bad started_at": lambda r: r.update(started_at="2026-01-01 00:00:00"),
            "bad event": lambda r: r.update(event="auth.login"),
            "secret": lambda r: r.update(reason="ghp_" + "a" * 24),
            "jwt": lambda r: r.update(reason="eyJhbGciOiJI.eyJzdWIiOiIx.abcdefghij"),
            "colon rule": lambda r: r.update(reason="Password: x"),
            "equals rule": lambda r: r.update(reason="a token =x"),
            "command": lambda r: r.update(reason="COMMAND=ls"),
            "bearer": lambda r: r.update(reason="Bearer abcdefghijk"),
            "secret key name": lambda r: r.update(api_key="x"),
        }
        for name, mutate in mutations.items():
            with self.subTest(name):
                row = self.good()
                mutate(row)
                with self.assertRaises(ValueError):
                    server.check_audit_row(row)

    def test_key_names_follow_the_secret_name_rule(self) -> None:
        for key in server.AUDIT_BASE_KEYS | server.AUDIT_DETAIL_KEYS:
            self.assertIsNone(server.AUDIT_KEY_NAME_RE.search(key), key)
        for key in ("api_key", "client_secret", "password", "auth_token", "session_cookie", "authorization",
                    "secret.key", "pass_key", "cache_bypass"):
            self.assertIsNotNone(server.AUDIT_KEY_NAME_RE.search(key), key)

    def test_a_row_that_fails_its_check_is_replaced(self) -> None:
        with patch.object(server, "_audit_detail", return_value={"nope": 1}):
            row = server.audit_wide_event("auth.login", {"email": EMAIL}, "account", "a", "success", None)
        self.assertEqual(("tailplan.audit.invalid", "error", "failure"),
                         (row["event"], row["outcome"], row["error_type"]))
        self.assertEqual(server.AUDIT_BASE_KEYS, set(row))


class StreamTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.store = server.Store(Path(self.temporary.name) / "data")

    def emit(self, **kwargs) -> tuple[str, str]:
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            self.store.audit("auth.login", actor={"accountId": "a", "email": EMAIL}, **kwargs)
        return out.getvalue(), err.getvalue()

    def test_success_prints_compact_sorted_json_to_stdout(self) -> None:
        out, err = self.emit(target_type="account", target_id="a", detail={"method": "oidc"})
        self.assertEqual("", err)
        self.assertEqual(1, len(out.splitlines()))
        row = json.loads(out)
        self.assertEqual(out.strip(), json.dumps(row, separators=(",", ":"), sort_keys=True))
        self.assertEqual("success", row["outcome"])

    def test_failure_prints_to_stderr_with_no_prefix(self) -> None:
        out, err = self.emit(outcome="denied", detail={"reason": "state"})
        self.assertEqual("", out)
        self.assertTrue(err.startswith("{"), err)
        self.assertEqual(("error", "denied"), (json.loads(err)["outcome"], json.loads(err)["error_type"]))

    def test_the_database_keeps_the_full_record(self) -> None:
        out, _err = self.emit(target_type="account", target_id="a", detail={"method": "oidc", "note": "kept"})
        self.assertNotIn("@", out)
        stored = self.store.list_audit(limit=1)[0]
        self.assertEqual((EMAIL, "a"), (stored["actorEmail"], stored["actorAccountId"]))
        self.assertEqual("kept", stored["detail"]["note"])
        self.assertIn("ts", stored)


class EndToEndTests(AuthCase):
    def test_a_full_session_prints_no_email_and_no_secret(self) -> None:
        cookie = self.oidc_login(groups=["staff"])
        key = self.create_key(cookie)
        draft = self.upload()
        self.api(f"/api/drafts/{draft['draftId']}/sharing/grants", "POST",
                 {"type": "email", "value": "friend@example.test", "role": "viewer"}, token=TOKEN)
        self.api("/api/me", token="not-a-key")
        text = self.audit_lines.getvalue()
        self.assertTrue(text)
        self.assertNotRegex(text, r"@")
        for secret in (key, TOKEN):
            self.assertNotIn(secret, text)
        for line in text.splitlines():
            row = json.loads(line)
            server.check_audit_row(row)
            self.assertRegex(line, r"^\{")
            self.assertEqual(sorted(row), list(row))
        denied = self.events("auth.denied")
        self.assertTrue(all(event["outcome"] == "error" for event in denied))
        self.assertTrue(re.fullmatch(r"[0-9a-f]{16}", self.events("auth.login")[0]["actor_hash"]))


if __name__ == "__main__":
    unittest.main()
