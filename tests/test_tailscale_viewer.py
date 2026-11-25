"""Cover the passive Tailscale viewer and the split between logins and emails."""
from __future__ import annotations

import json
import sqlite3
import tempfile
import threading
import unittest
from contextlib import closing
from http.client import HTTPConnection
from pathlib import Path
from unittest.mock import patch

import tailplan_server as server

OWNER = "owner@example.com"
ROSTER = "roster@example.com"
KIN = "kin@example.com"
ZACH = "zachvivier@gmail.com"
FRIEND = "friend@example.com"
ALICE = "alice@github"
BOB = "bob@passkey"
HTML = "<!doctype html><title>{}</title><main><h1>{}</h1></main>"

POLICY = {
    "legacy_workspace": "owner",
    "default_workspace": "collab",
    "rules": [
        {"match": {"email": ROSTER}, "workspaces": {"collab": "editor"}},
        {"match": {"email": KIN}, "workspaces": {"family": "viewer"}},
        {"match": {"tailscale_login": ALICE}, "workspaces": {"collab": "viewer"}},
        # An email rule never matches a dot-free login, even with the same text.
        {"match": {"email": BOB}, "workspaces": {"collab": "viewer"}},
    ],
}


class ViewerCase(unittest.TestCase):
    """Start a primary listener and a trusted proxy listener with the passive viewer on."""

    policy: dict | None = POLICY

    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.store = server.Store(Path(self.temporary.name) / "data")
        auth = server.AuthConfig(
            policy=server.RBACPolicy(self.policy), viewer_mode="session", modes=("api_key", "tailscale"))
        self.primary, self.proxy = server.create_servers(
            ("127.0.0.1", 0), ("127.0.0.1", 0), store=self.store, token="bootstrap-test",
            base_url="", redirect_view_base_url="", auth=auth,
        )
        self.proxy.trust_tailscale_identity = True
        self.proxy.tailscale_viewer = True
        self.proxy.owner_login = OWNER
        self.threads = []
        for listener in (self.primary, self.proxy):
            thread = threading.Thread(target=listener.serve_forever, daemon=True)
            thread.start()
            self.threads.append(thread)

    def tearDown(self) -> None:
        for listener in (self.primary, self.proxy):
            listener.shutdown()
            listener.server_close()
        for thread in self.threads:
            thread.join()
        self.temporary.cleanup()

    def request(self, path, method="GET", *, login=None, payload=None, token=None, proxy=True,
                headers=None):
        request_headers = {"Accept": "text/html", **(headers or {})}
        if login:
            request_headers["Tailscale-User-Login"] = login
            request_headers["Tailscale-User-Name"] = "Viewer"
        if token:
            request_headers["Authorization"] = "Bearer " + token
        body = None
        if payload is not None:
            body = json.dumps(payload).encode()
            request_headers["Content-Type"] = "application/json"
        listener = self.proxy if proxy else self.primary
        connection = HTTPConnection("127.0.0.1", listener.server_port, timeout=5)
        try:
            connection.request(method, path, body=body, headers=request_headers)
            response = connection.getresponse()
            return response.status, response.headers, response.read()
        finally:
            connection.close()

    def draft(self, title, *, workspace="owner", visibility="authenticated", account_id="local") -> str:
        result = self.store.upsert(HTML.format(title, title), f"{title}.html", None, "https://plans.example.test",
                                   account_id=account_id, workspace=workspace, visibility=visibility)
        return result["draftId"]

    def count(self, table: str) -> int:
        with closing(sqlite3.connect(self.store.db_path)) as connection:
            return connection.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]

    def state(self) -> tuple:
        return (len(self.store.load_meta().get("accounts", {})), self.count("principals"),
                self.count("sessions"))

    def events(self, event_type: str) -> list[dict]:
        return self.store.list_audit(limit=500, event=event_type)


class PassiveReadTests(ViewerCase):
    def test_get_and_head_render_draft_pages_with_no_state(self) -> None:
        draft_id = self.draft("Collab", workspace="collab", visibility="workspace")
        before = self.state()
        for method in ("GET", "HEAD"):
            for suffix in ("", "/share", "/preview.png", "/raw", "/content"):
                with self.subTest(method=method, suffix=suffix):
                    status, headers, body = self.request(f"/d/{draft_id}{suffix}", method, login=ROSTER)
                    self.assertEqual(200, status)
                    self.assertIsNone(headers.get("Set-Cookie"))
                    if method == "GET" and suffix in {"", "/raw"}:
                        self.assertIn(b"Collab", body)
        self.assertEqual(before, self.state())

    def test_family_and_roster_users_read_their_workspaces_only(self) -> None:
        collab = self.draft("Collab", workspace="collab", visibility="workspace")
        family = self.draft("Family", workspace="family", visibility="workspace")
        self.assertEqual(200, self.request(f"/d/{collab}", login=ROSTER)[0])
        self.assertEqual(200, self.request(f"/d/{family}", login=KIN)[0])
        self.assertEqual(404, self.request(f"/d/{collab}", login=KIN)[0])
        self.assertEqual(404, self.request(f"/d/{family}", login=ROSTER)[0])

    def test_unlisted_login_reads_only_drafts_granted_to_it(self) -> None:
        granted = self.draft("Granted")
        unshared = self.draft("Unshared")
        self.store.add_grant(granted, "email", ZACH, "viewer", "local")
        self.assertEqual(200, self.request(f"/d/{granted}", login=ZACH)[0])
        self.assertEqual(404, self.request(f"/d/{unshared}", login=ZACH)[0])

    def test_guest_reads_explicit_grants_and_never_authenticated_drafts(self) -> None:
        by_email = self.draft("Email grant")
        by_login = self.draft("Login grant")
        authenticated = self.draft("Everyone signed in")
        self.store.add_grant(by_email, "email", FRIEND, "editor", "local")
        self.store.add_grant(by_login, "tailscale_login", FRIEND, "viewer", "local")
        before = self.state()
        self.assertEqual(200, self.request(f"/d/{by_email}", login=FRIEND)[0])
        self.assertEqual(200, self.request(f"/d/{by_login}", login=FRIEND)[0])
        self.assertEqual(404, self.request(f"/d/{authenticated}", login=FRIEND)[0])
        self.assertEqual(before, self.state())
        self.assertEqual((0, 0, 0), before)

    def test_link_drafts_stay_readable_to_guests(self) -> None:
        link = self.draft("Link", visibility="link")
        self.assertEqual(200, self.request(f"/d/{link}", login=FRIEND)[0])

    def test_dot_free_logins_match_login_grants_and_rules_never_email(self) -> None:
        collab = self.draft("Collab", workspace="collab", visibility="workspace")
        login_grant = self.draft("Login grant")
        email_grant = self.draft("Email grant", visibility="workspace")
        self.store.add_grant(login_grant, "tailscale_login", BOB, "viewer", "local")
        # A raw row: normalize_grant refuses a dot-free email, but older data may hold one.
        self.store.add_grant(email_grant, "email", ALICE, "viewer", "local")
        self.store.add_grant(email_grant, "email", BOB, "viewer", "local")
        self.assertEqual(200, self.request(f"/d/{collab}", login=ALICE)[0])  # tailscale_login rule
        self.assertEqual(404, self.request(f"/d/{collab}", login=BOB)[0])  # email rule only
        self.assertEqual(200, self.request(f"/d/{login_grant}", login=BOB)[0])
        self.assertEqual(404, self.request(f"/d/{email_grant}", login=ALICE)[0])
        self.assertEqual(404, self.request(f"/d/{email_grant}", login=BOB)[0])

    def test_owner_login_reads_owner_drafts_as_a_viewer(self) -> None:
        private = self.draft("Private", visibility="private")
        before = self.state()
        self.assertEqual(200, self.request(f"/d/{private}", login=OWNER)[0])
        self.assertEqual(before, self.state())

    def test_view_is_audited_with_the_header_method_and_no_account(self) -> None:
        draft_id = self.draft("Audited", workspace="collab", visibility="workspace")
        self.request(f"/d/{draft_id}", "HEAD", login=ROSTER)
        self.assertEqual([], self.events("draft.view"))
        status, headers, _ = self.request(f"/d/{draft_id}", login=ROSTER)
        self.assertEqual(200, status)
        self.assertIsNone(headers.get("Set-Cookie"))
        events = self.events("draft.view")
        self.assertEqual(1, len(events))
        self.assertIsNone(events[0]["actorAccountId"])
        self.assertEqual(ROSTER, events[0]["actorEmail"])
        self.assertEqual("tailscale_header", events[0]["detail"]["authMethod"])
        self.assertEqual(draft_id, events[0]["targetId"])


class PassiveBoundaryTests(ViewerCase):
    def test_no_header_sends_the_browser_to_sign_in(self) -> None:
        draft_id = self.draft("Closed")
        status, headers, _ = self.request(f"/d/{draft_id}")
        self.assertEqual(302, status)
        self.assertIn("/auth/sign-in", headers["Location"])

    def test_direct_listener_ignores_a_forged_header(self) -> None:
        draft_id = self.draft("Collab", workspace="collab", visibility="workspace")
        status, _, _ = self.request(f"/d/{draft_id}", login=ROSTER, proxy=False)
        self.assertIn(status, {302, 401})
        status, _, _ = self.request(f"/d/{draft_id}", login=ROSTER, proxy=False,
                                    headers={"Accept": "application/json"})
        self.assertIn(status, {302, 401})

    def test_header_needs_the_viewer_flag(self) -> None:
        draft_id = self.draft("Collab", workspace="collab", visibility="workspace")
        self.proxy.tailscale_viewer = False
        self.assertEqual(302, self.request(f"/d/{draft_id}", login=ROSTER)[0])
        self.proxy.tailscale_viewer = True
        self.proxy.trust_tailscale_identity = False
        self.assertEqual(302, self.request(f"/d/{draft_id}", login=ROSTER)[0])

    def test_header_respects_the_login_allowlist(self) -> None:
        draft_id = self.draft("Collab", workspace="collab", visibility="workspace")
        self.proxy.allowed_logins = server.parse_allowed_logins(OWNER)
        self.assertEqual(302, self.request(f"/d/{draft_id}", login=ROSTER)[0])

    def test_header_alone_never_authorizes_writes_api_or_dashboard(self) -> None:
        draft_id = self.draft("Collab", workspace="collab", visibility="workspace")
        before = self.state()
        json_accept = {"Accept": "application/json"}
        checks = [
            ("GET", "/api/drafts", None, {401}),
            ("GET", f"/api/drafts/{draft_id}", None, {401}),
            ("GET", f"/api/drafts/{draft_id}/source", None, {401}),
            ("GET", f"/api/drafts/{draft_id}/sharing", None, {401}),
            ("POST", "/api/uploads", {"html": HTML.format("X", "X")}, {401}),
            ("POST", f"/api/drafts/{draft_id}/disable", {}, {401, 403}),
            ("DELETE", f"/api/drafts/{draft_id}", None, {401, 403}),
            ("POST", f"/d/{draft_id}", {}, {401, 403, 404, 405}),
        ]
        for method, path, payload, allowed in checks:
            with self.subTest(method=method, path=path):
                status, headers, _ = self.request(path, method, login=ROSTER, payload=payload,
                                                  headers=json_accept)
                self.assertIn(status, allowed)
                self.assertIsNone(headers.get("Set-Cookie"))
        status, _, body = self.request("/dashboard", login=ROSTER)
        self.assertNotIn(b"Sign out", body)
        self.assertNotIn(b"Collab", body)
        self.assertEqual(before, self.state())
        draft = self.store.resolve_draft(draft_id)
        self.assertFalse(draft.get("disabledAt"))
        self.assertEqual([], self.events("draft.source"))

    def test_session_and_bearer_keep_precedence_and_full_roles(self) -> None:
        draft_id = self.draft("Collab", workspace="collab", visibility="workspace")
        status, _, body = self.request(f"/api/drafts/{draft_id}/source", token="bootstrap-test",
                                       login=FRIEND, headers={"Accept": "application/json"})
        self.assertEqual(200, status)
        self.assertEqual(draft_id, json.loads(body)["draftId"])


class OpenPolicyTests(ViewerCase):
    policy = None

    def test_without_a_policy_any_login_reads_authenticated_drafts(self) -> None:
        draft_id = self.draft("Open")
        private = self.draft("Private", visibility="private")
        self.assertEqual(200, self.request(f"/d/{draft_id}", login=FRIEND)[0])
        self.assertEqual(404, self.request(f"/d/{private}", login=FRIEND)[0])


class RoleUnitTests(unittest.TestCase):
    def setUp(self) -> None:
        self.auth = server.AuthConfig(policy=server.RBACPolicy(POLICY), viewer_mode="session")
        self.draft = {"draftId": "abc123", "accountId": "local", "workspace": "collab",
                      "visibility": "authenticated"}

    def test_email_pattern_needs_a_dotted_domain(self) -> None:
        for value in ("alice@example.com", "a.b+c@mail.example.co.uk"):
            self.assertIsNotNone(server.EMAIL_RE.fullmatch(value), value)
        for value in ("alice@github", "bob@passkey", ".a@example.com", "a.@example.com",
                      "a..b@example.com", "a@.example.com", "a@example.com.", "a@example..com",
                      "a@b@example.com", "a b@example.com"):
            self.assertIsNone(server.EMAIL_RE.fullmatch(value), value)

    def test_grants_keep_logins_and_emails_apart(self) -> None:
        self.assertEqual(("email", "group", "workspace", "tailscale_login"), server.GRANT_TYPES)
        self.assertEqual("alice@github", server.normalize_grant("tailscale_login", " Alice@GitHub "))
        with self.assertRaises(ValueError):
            server.normalize_grant("email", "alice@github")
        with self.assertRaises(ValueError):
            server.normalize_grant("tailscale_login", "no-at-sign")

    def test_policy_matches_tailscale_login_rules(self) -> None:
        policy = server.RBACPolicy(POLICY)
        self.assertEqual({"collab": "viewer"},
                         policy.resolve(email=None, email_verified=False, groups=[],
                                        tailscale_login="ALICE@github")["workspaces"])
        self.assertIsNone(policy.resolve(email=None, email_verified=False, groups=[], tailscale_login=BOB))
        self.assertIsNone(policy.resolve(email=ALICE, email_verified=True, groups=[]))
        with self.assertRaises(ValueError):
            server.RBACPolicy({"rules": [{"match": {"login": "x@y"}}]})

    def test_unmatched_identity_loses_authenticated_access(self) -> None:
        identity = {"accountId": "acct", "email": FRIEND, "emailVerified": True, "workspaces": {},
                    "policyMatched": False}
        self.assertIsNone(server.draft_role(identity, self.draft, [], self.auth))
        self.assertEqual("viewer", server.draft_role(identity, {**self.draft, "visibility": "link"}, [], self.auth))
        grant = [{"type": "email", "value": FRIEND, "role": "editor"}]
        self.assertEqual("editor", server.draft_role(identity, self.draft, grant, self.auth))
        identity.pop("policyMatched")
        self.assertEqual("viewer", server.draft_role(identity, self.draft, [], self.auth))

    def test_passive_role_is_capped_at_viewer(self) -> None:
        identity = {"accountId": None, "passive": True, "tailscaleLogin": ROSTER, "email": ROSTER,
                    "emailVerified": True, "workspaces": {"collab": "admin"}, "policyMatched": True}
        grant = [{"type": "tailscale_login", "value": ROSTER, "role": "editor"}]
        self.assertEqual("viewer", server.draft_role(identity, self.draft, grant, self.auth))
        self.assertEqual("viewer", server.draft_role(identity, self.draft, [], self.auth))
        owned = {**self.draft, "accountId": "someone"}
        self.assertNotEqual("owner", server.draft_role({**identity, "accountId": None}, owned, [], self.auth))


class TailscaleSignInLinkTests(ViewerCase):
    policy = None

    def test_verified_email_login_joins_the_existing_oidc_account(self) -> None:
        self.store.ensure_account("oidc-account", "Linked")
        self.store.upsert_principal(
            issuer="https://idp.example.com", subject="sub-1", account_id="oidc-account",
            email="linked@example.com", email_verified=True, display_name="Linked", groups=[],
            instance_role=None, workspaces={"collab": "editor"}, default_workspace="collab",
        )
        handler = server.Handler.__new__(server.Handler)
        handler.server = self.proxy
        handler.client_address = ("127.0.0.1", 0)
        handler.headers = {}
        account = handler.tailscale_identity("Linked@Example.com", "Linked")
        self.assertEqual("oidc-account", account["accountId"])
        self.assertEqual({"oidc-account"}, set(self.store.load_meta().get("accounts", {})))
        self.assertEqual("https://idp.example.com", self.store.account_principal("oidc-account")["issuer"])

    def test_dot_free_login_gets_its_own_account(self) -> None:
        handler = server.Handler.__new__(server.Handler)
        handler.server = self.proxy
        handler.client_address = ("127.0.0.1", 0)
        account = handler.tailscale_identity(ALICE, "Alice")
        principal = self.store.account_principal(account["accountId"])
        self.assertIsNone(principal["email"])
        self.assertFalse(principal["emailVerified"])


class MigrationTests(unittest.TestCase):
    def test_schema_two_grants_survive_the_move_to_three(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary) / "data"
            root.mkdir()
            db_path = root / server.DB_FILENAME
            rows = [
                ("draft1", "email", "a@example.com", "viewer", "local", "2026-01-01T00:00:00Z"),
                ("draft1", "group", "staff", "editor", None, "2026-01-02T00:00:00Z"),
                ("draft2", "workspace", "collab", "viewer", "acct", "2026-01-03T00:00:00Z"),
            ]
            with closing(sqlite3.connect(db_path)) as connection:
                connection.execute("PRAGMA journal_mode=WAL")
                for version in (1, 2):
                    for statement in server._SCHEMA_STATEMENTS[version]:
                        connection.execute(statement)
                connection.executemany("INSERT INTO draft_grants VALUES (?, ?, ?, ?, ?, ?)", rows)
                connection.execute("PRAGMA user_version = 2")
                connection.commit()
                with self.assertRaises(sqlite3.IntegrityError):
                    connection.execute("INSERT INTO draft_grants VALUES "
                                       "('d', 'tailscale_login', 'x@y', 'viewer', NULL, 'now')")

            store = server.Store(root)

            with closing(sqlite3.connect(store.db_path)) as connection:
                self.assertEqual(3, connection.execute("PRAGMA user_version").fetchone()[0])
                kept = connection.execute(
                    "SELECT draft_id, principal_type, principal_value, role, granted_by, created_at "
                    "FROM draft_grants ORDER BY draft_id, principal_type").fetchall()
                indexes = {row[0] for row in connection.execute(
                    "SELECT name FROM sqlite_master WHERE type = 'index' AND tbl_name = 'draft_grants'")}
                tables = {row[0] for row in connection.execute(
                    "SELECT name FROM sqlite_master WHERE type = 'table'")}
            self.assertEqual(sorted(rows), sorted(kept))
            self.assertIn("draft_grants_principal", indexes)
            self.assertNotIn("draft_grants_v3", tables)
            store.add_grant("draft1", "tailscale_login", "alice@github", "viewer", "local")
            self.assertIn({"type": "tailscale_login", "value": "alice@github", "role": "viewer"},
                          [{key: grant[key] for key in ("type", "value", "role")}
                           for grant in store.list_grants("draft1")])


class UploadGrantTests(ViewerCase):
    def upload(self, payload):
        status, _, body = self.request("/api/uploads", "POST", token="bootstrap-test",
                                       payload={"html": HTML.format("Up", "Up"), **payload},
                                       headers={"Accept": "application/json"})
        return status, json.loads(body)

    def test_upload_applies_grants_with_the_draft(self) -> None:
        with patch.object(self.store, "set_sharing", side_effect=AssertionError("not atomic")), \
                patch.object(self.store, "add_grant", side_effect=AssertionError("not atomic")):
            status, body = self.upload({"visibility": "private", "grants": [
                {"type": "email", "value": "Zed@Example.com"},
                {"type": "tailscale_login", "value": ALICE, "role": "editor"},
                {"type": "group", "value": "/family"},
            ]})
        self.assertEqual(201, status, body)
        grants = {(grant["type"], grant["value"], grant["role"])
                  for grant in self.store.list_grants(body["draftId"])}
        self.assertEqual({("email", "zed@example.com", "viewer"), ("tailscale_login", ALICE, "editor"),
                          ("group", "family", "viewer")}, grants)
        self.assertEqual(200, self.request(f"/d/{body['draftId']}", login=ALICE)[0])

    def test_invalid_grants_reject_the_whole_upload(self) -> None:
        for grants in ([{"type": "email", "value": ALICE}], [{"type": "user", "value": "x"}],
                       [{"type": "email", "value": FRIEND, "role": "owner"}], "email:x@y.com",
                       [{"type": "email", "value": FRIEND, "extra": 1}]):
            with self.subTest(grants=grants):
                status, _ = self.upload({"grants": grants})
                self.assertEqual(422, status)
        self.assertEqual({}, self.store.load_meta()["drafts"])

    def test_a_failed_grant_write_rolls_back_the_draft(self) -> None:
        with self.assertRaises((sqlite3.IntegrityError, server.StorageError)):
            self.store.upsert(HTML.format("X", "X"), "x.html", None, "https://plans.example.test",
                              grants=[{"type": "bogus", "value": "x", "role": "viewer"}])
        self.assertEqual({}, self.store.load_meta()["drafts"])


class ArgumentTests(unittest.TestCase):
    def test_viewer_flag_reads_the_environment(self) -> None:
        with patch.dict("os.environ", {"TAILPLAN_TAILSCALE_VIEWER": "1"}):
            self.assertTrue(server.parse_args([]).tailscale_viewer)
        with patch.dict("os.environ", {"TAILPLAN_TAILSCALE_VIEWER": "0"}):
            self.assertFalse(server.parse_args([]).tailscale_viewer)
            self.assertTrue(server.parse_args(["--tailscale-viewer"]).tailscale_viewer)

    def test_primary_listener_never_turns_on_the_viewer(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            store = server.Store(Path(temporary) / "data")
            primary, proxy = server.create_servers(
                ("127.0.0.1", 0), ("127.0.0.1", 0), store=store, token="t", base_url="",
                redirect_view_base_url="")
            try:
                self.assertFalse(primary.tailscale_viewer)
                self.assertFalse(proxy.tailscale_viewer)
            finally:
                primary.server_close()
                proxy.server_close()


if __name__ == "__main__":
    unittest.main()
