"""Cover the passive Tailscale viewer and the split between logins and emails."""
from __future__ import annotations

import json
import re
import sqlite3
import tempfile
import threading
import unittest
from contextlib import closing
from http.client import HTTPConnection
from pathlib import Path
from unittest.mock import Mock, patch
from urllib.parse import urlencode

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
    ],
}


class ViewerCase(unittest.TestCase):
    """Start a primary listener and a trusted proxy listener with the passive viewer on."""

    policy: dict | None = POLICY
    modes: tuple[str, ...] = ("api_key", "tailscale")
    oidc_client: object = None

    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.store = server.Store(Path(self.temporary.name) / "data")
        auth = server.AuthConfig(
            policy=server.RBACPolicy(self.policy), viewer_mode="session", modes=self.modes,
            oidc=self.oidc_client)
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
                headers=None, form=None, cookies=None):
        request_headers = {"Accept": "text/html", **(headers or {})}
        if login:
            request_headers["Tailscale-User-Login"] = login
            request_headers["Tailscale-User-Name"] = "Viewer"
        if token:
            request_headers["Authorization"] = "Bearer " + token
        if cookies:
            request_headers["Cookie"] = "; ".join(cookies)
        body = None
        if payload is not None:
            body = json.dumps(payload).encode()
            request_headers["Content-Type"] = "application/json"
        if form is not None:
            body = urlencode(form).encode()
            request_headers["Content-Type"] = "application/x-www-form-urlencoded"
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

    def tailscale_sign_in(self, login: str):
        """Sign in with method=tailscale on the proxy. Return (status, session cookie or None)."""
        status, headers, body = self.request("/auth/sign-in", login=login)
        self.assertEqual(200, status)
        login_cookie = next(value.split(";", 1)[0] for value in headers.get_all("Set-Cookie")
                            if value.startswith("tailplan_login="))
        csrf = re.search(r'name="csrf" value="([^"]+)"', body.decode())[1]
        status, headers, _ = self.request(
            "/auth/sign-in", "POST", login=login, cookies=[login_cookie],
            form={"csrf": csrf, "method": "tailscale", "next": "/dashboard"})
        session = next((value.split(";", 1)[0] for value in headers.get_all("Set-Cookie") or []
                        if value.startswith("tailplan_session=")), None)
        return status, session


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
        self.assertEqual(404, self.request(f"/d/{collab}", login=BOB)[0])  # no rule names bob
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
        self.assertEqual(ROSTER, events[0]["detail"]["actorLogin"])
        self.assertEqual(draft_id, events[0]["targetId"])

    def test_share_and_preview_reads_are_audited(self) -> None:
        draft_id = self.draft("Previewed", workspace="collab", visibility="workspace")
        self.request(f"/d/{draft_id}/share", "HEAD", login=ROSTER)
        self.request(f"/d/{draft_id}/v/1/preview.png", "HEAD", login=ROSTER)
        self.assertEqual([], self.events("draft.view"))
        self.assertEqual(200, self.request(f"/d/{draft_id}/share", login=ROSTER)[0])
        self.assertEqual(200, self.request(f"/d/{draft_id}/v/1/preview.png", login=ROSTER)[0])
        events = self.events("draft.view")
        self.assertEqual({"share", "preview.png"}, {event["detail"]["path"] for event in events})
        for event in events:
            self.assertIsNone(event["actorAccountId"])
            self.assertEqual(ROSTER, event["actorEmail"])
            self.assertEqual("tailscale_header", event["detail"]["authMethod"])
            self.assertEqual(ROSTER, event["detail"]["actorLogin"])
            self.assertEqual(draft_id, event["targetId"])


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

    def test_bearer_key_wins_over_an_unmatched_header_on_pages(self) -> None:
        draft_id = self.draft("Owned", visibility="private")
        self.assertEqual(404, self.request(f"/d/{draft_id}/", login=FRIEND)[0])
        status, _, body = self.request(f"/d/{draft_id}/", login=FRIEND, token="bootstrap-test")
        self.assertEqual(200, status)
        self.assertIn(b"Owned", body)

    def test_denied_reader_gets_the_same_404_with_a_sign_in_link(self) -> None:
        draft_id = self.draft("Hidden", visibility="private")
        denied = self.request(f"/d/{draft_id}/", login=FRIEND)
        missing = self.request("/d/nosuchdraft/", login=FRIEND)
        self.assertEqual((404, 404), (denied[0], missing[0]))
        self.assertEqual(denied[2], missing[2])
        self.assertRegex(denied[2].decode(), r'href="[^"]*/auth/sign-in">Sign in</a>')
        self.assertIsNone(denied[1].get("Location"))

    def test_session_and_bearer_keep_precedence_and_full_roles(self) -> None:
        draft_id = self.draft("Collab", workspace="collab", visibility="workspace")
        status, _, body = self.request(f"/api/drafts/{draft_id}/source", token="bootstrap-test",
                                       login=FRIEND, headers={"Accept": "application/json"})
        self.assertEqual(200, status)
        self.assertEqual(draft_id, json.loads(body)["draftId"])


class ModesDecoupledTests(ViewerCase):
    """The passive viewer works when TAILPLAN_AUTH_MODES leaves out tailscale."""

    modes = ("oidc", "api_key")
    oidc_client = Mock()  # Never called: no test here signs in with OIDC.

    def test_passive_viewer_works_while_tailscale_sign_in_stays_off(self) -> None:
        draft_id = self.draft("Collab", workspace="collab", visibility="workspace")
        before = self.state()
        self.assertEqual(200, self.request(f"/d/{draft_id}", login=ROSTER)[0])
        status, session = self.tailscale_sign_in(ROSTER)
        self.assertEqual(403, status)
        self.assertIsNone(session)
        self.assertEqual(before, self.state())


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

    def test_policy_refuses_dot_free_email_rules_and_odd_logins(self) -> None:
        with self.assertRaisesRegex(ValueError, "tailscale_login"):
            server.RBACPolicy({"rules": [{"match": {"email": BOB}}]})
        for login in ("a\u200b@github", "a@git\u202ehub", "<a>@github", "a@github;id", "a b@github"):
            with self.subTest(login=login), self.assertRaises(ValueError):
                server.RBACPolicy({"rules": [{"match": {"tailscale_login": login}}]})
            self.assertIsNone(server.TAILSCALE_LOGIN_RE.fullmatch(login))

    def test_revoke_accepts_legacy_values_that_grants_refuse(self) -> None:
        with self.assertRaises(ValueError):
            server.normalize_grant("email", "Alice@GitHub")
        self.assertEqual("alice@github", server.revoke_value("email", " Alice@GitHub "))
        self.assertEqual("family", server.revoke_value("group", "/family"))
        for principal_type, value in (("user", "x"), ("email", ""), ("email", "a\x00b"), ("email", 1)):
            with self.subTest(value=value), self.assertRaises(ValueError):
                server.revoke_value(principal_type, value)

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


class TailscaleSignInIsolationTests(ViewerCase):
    policy = None

    def test_sign_in_never_joins_an_oidc_admin_with_the_same_email(self) -> None:
        self.store.ensure_account("oidc-account", "Linked")
        self.store.upsert_principal(
            issuer="https://idp.example.com", subject="sub-1", account_id="oidc-account",
            email="linked@example.com", email_verified=True, display_name="Linked", groups=["admins"],
            instance_role="admin", workspaces={"collab": "admin"}, default_workspace="collab",
        )
        admin_draft = self.draft("Admin only", visibility="private", account_id="oidc-account")
        status, headers, body = self.request("/api/uploads", "POST", token="bootstrap-test",
                                             headers={"Accept": "application/json"}, payload={
            "files": [{"path": "index.html", "html": '<title>Pair</title><a href="two.html">Two</a>'},
                      {"path": "two.html", "html": "<title>Two</title><p>Second page</p>"}],
            "visibility": "private",
            "grants": [{"type": "tailscale_login", "value": "linked@example.com"}],
        })
        self.assertEqual(201, status, body)
        granted = json.loads(body)["draftId"]

        status, session = self.tailscale_sign_in("Linked@Example.com")
        self.assertEqual(303, status)
        self.assertIsNotNone(session)
        principal = self.store.principal("tailscale", "linked@example.com")
        self.assertIsNotNone(principal)
        self.assertNotEqual("oidc-account", principal["accountId"])
        self.assertIsNone(principal["instanceRole"])
        self.assertEqual("https://idp.example.com",
                         self.store.account_principal("oidc-account")["issuer"])
        # No admin role: the OIDC account's private draft stays hidden.
        self.assertEqual(404, self.request(f"/d/{admin_draft}/", cookies=[session])[0])

        status, _, body = self.request(f"/d/{granted}/", cookies=[session])
        self.assertEqual(200, status)
        href = re.search(r'href="(two\.html\?tv=[^"]+)"', body.decode())
        self.assertIsNotNone(href)
        # The view token keeps the Tailscale login, so the tailscale_login grant still matches.
        status, _, body = self.request(f"/d/{granted}/{href[1]}")
        self.assertEqual(200, status)
        self.assertIn(b"Second page", body)

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
            legacy = ("draft3", "email", "alice@github", "viewer", "local", "2026-01-04T00:00:00Z")
            with closing(sqlite3.connect(db_path)) as connection:
                connection.execute("PRAGMA journal_mode=WAL")
                for version in (1, 2):
                    for statement in server._SCHEMA_STATEMENTS[version]:
                        connection.execute(statement)
                connection.executemany("INSERT INTO draft_grants VALUES (?, ?, ?, ?, ?, ?)", [*rows, legacy])
                principal = ("INSERT INTO principals (issuer, subject, account_id, email, email_verified,"
                             " created_at, updated_at) VALUES (?, ?, ?, ?, 1, 'now', 'now')")
                connection.execute(principal, ("tailscale", "bob@passkey", "acct-bob", "bob@passkey"))
                connection.execute(principal, ("tailscale", "c@example.com", "acct-c", "c@example.com"))
                connection.execute(principal, ("https://idp.example.com", "s1", "acct-d", "d@corp"))
                connection.execute("PRAGMA user_version = 2")
                connection.commit()
                with self.assertRaises(sqlite3.IntegrityError):
                    connection.execute("INSERT INTO draft_grants VALUES "
                                       "('d', 'tailscale_login', 'x@y', 'viewer', NULL, 'now')")

            store = server.Store(root)

            with closing(sqlite3.connect(store.db_path)) as connection:
                self.assertEqual(4, connection.execute("PRAGMA user_version").fetchone()[0])
                kept = connection.execute(
                    "SELECT draft_id, principal_type, principal_value, role, granted_by, created_at "
                    "FROM draft_grants ORDER BY draft_id, principal_type").fetchall()
                indexes = {row[0] for row in connection.execute(
                    "SELECT name FROM sqlite_master WHERE type = 'index' AND tbl_name = 'draft_grants'")}
                tables = {row[0] for row in connection.execute(
                    "SELECT name FROM sqlite_master WHERE type = 'table'")}
            self.assertEqual(sorted([*rows, ("draft3", "tailscale_login", *legacy[2:])]), sorted(kept))
            self.assertIsNone(store.principal("tailscale", "bob@passkey")["email"])
            self.assertFalse(store.principal("tailscale", "bob@passkey")["emailVerified"])
            self.assertEqual("c@example.com", store.principal("tailscale", "c@example.com")["email"])
            # Only Tailscale principals change; an IdP's claim stays as it was.
            self.assertEqual("d@corp", store.principal("https://idp.example.com", "s1")["email"])
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

    def test_upload_grants_are_audited_once(self) -> None:
        payload = {"visibility": "private", "grants": [
            {"type": "email", "value": "zed@example.com"}, {"type": "tailscale_login", "value": ALICE}]}
        headers = {"Accept": "application/json", "Idempotency-Key": "grant-audit-1"}
        for expected in (201, 200):
            status, _, body = self.request("/api/uploads", "POST", token="bootstrap-test", headers=headers,
                                           payload={"html": HTML.format("Up", "Up"), **payload})
            self.assertEqual(expected, status, body)
        events = self.events("sharing.grant")
        self.assertEqual({("email", "zed@example.com"), ("tailscale_login", ALICE)},
                         {(event["detail"]["type"], event["detail"]["value"]) for event in events})
        self.assertEqual(2, len(events))

    def test_reused_key_with_other_sharing_is_a_conflict(self) -> None:
        headers = {"Accept": "application/json", "Idempotency-Key": "grant-conflict-1"}
        base = {"html": HTML.format("Up", "Up")}
        status, _, _ = self.request("/api/uploads", "POST", token="bootstrap-test", headers=headers,
                                    payload=base)
        self.assertEqual(201, status)
        for extra in ({"grants": [{"type": "email", "value": "zed@example.com"}]},
                      {"workspace": "owner"}, {"visibility": "private"}):
            with self.subTest(extra=extra):
                status, _, _ = self.request("/api/uploads", "POST", token="bootstrap-test",
                                            headers=headers, payload={**base, **extra})
                self.assertEqual(409, status)

    def test_slug_upload_retry_with_same_key_replays(self) -> None:
        headers = {"Accept": "application/json", "Idempotency-Key": "slug-retry-1"}
        payload = {"html": HTML.format("Slug", "Slug"), "slug": "idempotent-slug-plan"}
        status1, _, raw1 = self.request("/api/uploads", "POST", token="bootstrap-test", headers=headers,
                                        payload=payload)
        self.assertEqual(201, status1)
        first = json.loads(raw1)
        status2, _, raw2 = self.request("/api/uploads", "POST", token="bootstrap-test", headers=headers,
                                        payload=payload)
        self.assertEqual(200, status2)
        second = json.loads(raw2)
        self.assertTrue(second.get("replayed"))
        self.assertEqual(first["draftId"], second["draftId"])

    def test_interactive_tailscale_login_rejects_non_ascii_and_matches_lowercase(self) -> None:
        status, _, body = self.request("/auth/sign-in", "GET", proxy=True,
                                       headers={"Tailscale-User-Login": "boß@example.com"})
        self.assertEqual(200, status)
        self.assertNotIn(b"Continue with Tailscale", body)
        self.assertNotEqual(
            self.store.identity_account("boss@example.com", "Boss", "boss@example.com")["accountId"],
            self.store.identity_account("boß@example.com", "Fake", "boss@example.com")["accountId"],
        )

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
    PROXY = ["--trust-tailscale-identity", "--proxy-host", "127.0.0.1", "--proxy-port", "9128"]

    def test_viewer_flag_reads_the_environment(self) -> None:
        with patch.dict("os.environ", {"TAILPLAN_TAILSCALE_VIEWER": "1"}):
            self.assertTrue(server.parse_args(self.PROXY).tailscale_viewer)
        with patch.dict("os.environ", {"TAILPLAN_TAILSCALE_VIEWER": "0"}):
            self.assertFalse(server.parse_args([]).tailscale_viewer)
            self.assertTrue(server.parse_args(["--tailscale-viewer", *self.PROXY]).tailscale_viewer)

    def test_viewer_flag_fails_fast_without_trust_and_proxy(self) -> None:
        with patch.dict("os.environ", {"TAILPLAN_TAILSCALE_VIEWER": "0"}):
            for argv in (["--tailscale-viewer"],
                         ["--tailscale-viewer", "--proxy-host", "127.0.0.1", "--proxy-port", "9128"]):
                with self.subTest(argv=argv), patch("sys.stderr"), self.assertRaises(SystemExit):
                    server.parse_args(argv)

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
