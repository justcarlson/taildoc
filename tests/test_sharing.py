"""Cover draft sharing through the REST API, the dashboard, and the CLI."""
from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

import tailplan_server as server
from auth_harness import TOKEN, AuthCase

ROOT = Path(__file__).resolve().parents[1]
CLI = ROOT / "bin" / "tailplan"


class SharingApiTests(AuthCase):
    def setUp(self) -> None:
        super().setUp()
        self.editor_key = self.create_key(self.oidc_login(sub="editor", email="editor@example.test"))
        self.draft = self.upload(token=self.editor_key, visibility="private")

    def sharing(self, key: str | None = None, method: str = "GET", payload: object = None,
                suffix: str = "") -> tuple[int, dict]:
        return self.api(f"/api/drafts/{self.draft['draftId']}/sharing{suffix}", method, payload,
                        token=key or self.editor_key)

    def test_upload_lands_in_the_default_workspace(self) -> None:
        self.assertEqual(("team", "private"), (self.draft["workspace"], self.draft["visibility"]))
        status, body = self.sharing()
        self.assertEqual(200, status)
        self.assertEqual(["private", "workspace", "authenticated", "link"], body["allowedVisibilities"])
        self.assertEqual([], body["grants"])

    def test_grant_and_revoke_people_and_groups(self) -> None:
        status, body = self.sharing(method="POST", suffix="/grants",
                                    payload={"type": "email", "value": "Friend@Example.test", "role": "editor"})
        self.assertEqual(200, status, body)
        self.assertEqual([("email", "friend@example.test", "editor")],
                         [(grant["type"], grant["value"], grant["role"]) for grant in body["grants"]])
        friend = self.create_key(self.oidc_login(sub="friend", email="friend@example.test", groups=["outsiders"]))
        status, source = self.api(f"/api/drafts/{self.draft['draftId']}/source", token=friend)
        self.assertEqual(200, status)
        status, _body = self.sharing(friend, "POST", {"visibility": "workspace"})
        self.assertEqual(200, status)  # An editor grant may change visibility.
        status, body = self.sharing(method="DELETE", suffix="/grants",
                                    payload={"type": "email", "value": "friend@example.test"})
        self.assertEqual([], body["grants"])
        status, _source = self.api(f"/api/drafts/{self.draft['draftId']}/source", token=friend)
        self.assertEqual(404, status)
        status, _body = self.sharing(method="DELETE", suffix="/grants",
                                     payload={"type": "email", "value": "friend@example.test"})
        self.assertEqual(404, status)
        names = self.event_names()
        self.assertIn("sharing.grant", names)
        self.assertIn("sharing.revoke", names)

    def test_group_grant_admits_members(self) -> None:
        self.sharing(method="POST", suffix="/grants", payload={"type": "group", "value": "Outsiders"})
        outsider = self.create_key(self.oidc_login(sub="outsider", groups=["outsiders"]))
        status, _source = self.api(f"/api/drafts/{self.draft['draftId']}/source", token=outsider)
        self.assertEqual(200, status)
        status, _body = self.sharing(outsider, "POST", {"visibility": "link"})
        self.assertEqual(403, status)  # A viewer grant cannot change sharing.

    def test_bad_grants_are_rejected(self) -> None:
        for payload in ({"type": "email", "value": "not-an-email"}, {"type": "robot", "value": "x"},
                        {"type": "email", "value": "a@b.test", "role": "owner"}, {"type": "group", "value": ""}):
            with self.subTest(payload):
                status, _body = self.sharing(method="POST", suffix="/grants", payload=payload)
                self.assertEqual(400, status)

    def test_visibility_cap_answers_422(self) -> None:
        self.auth.max_visibility = "workspace"
        status, body = self.sharing(method="POST", payload={"visibility": "link"})
        self.assertEqual(422, status)
        self.assertIn("maximum", body["error"])
        status, body = self.sharing(method="POST", payload={"visibility": "workspace"})
        self.assertEqual((200, "workspace"), (status, body["visibility"]))
        status, body = self.sharing(method="POST", payload={"visibility": "secret"})
        self.assertEqual(400, status)

    def test_cap_also_limits_existing_link_drafts(self) -> None:
        link = self.upload(token=self.editor_key, visibility="link")
        self.auth.max_visibility = "authenticated"
        status, body = self.api(f"/api/drafts/{link['draftId']}/sharing", token=self.editor_key)
        self.assertEqual("authenticated", body["visibility"])
        status, _headers, _body = self.request(f"/d/{link['draftId']}", headers={"Accept": "text/html"})
        self.assertEqual(302, status)

    def test_workspace_moves_need_owner_and_membership(self) -> None:
        status, _body = self.sharing(method="POST", payload={"workspace": "ops"})
        self.assertEqual(403, status)  # The editor is not in ops.
        ops_owner = self.create_key(self.oidc_login(sub="both", groups=["staff", "ops"]))
        mine = self.upload(token=ops_owner, workspace="team")
        status, body = self.api(f"/api/drafts/{mine['draftId']}/sharing", "POST", {"workspace": "ops"},
                                token=ops_owner)
        self.assertEqual((200, "ops"), (status, body["workspace"]))
        status, body = self.api(f"/api/drafts/{mine['draftId']}/sharing", "POST", {"workspace": "Bad Name"},
                                token=ops_owner)
        self.assertEqual(400, status)

    def test_strangers_and_viewers_cannot_read_sharing(self) -> None:
        reader = self.create_key(self.oidc_login(sub="reader", groups=["readers"]))
        status, _body = self.sharing(reader)
        self.assertEqual(404, status)  # Private draft: the reader cannot see it.
        self.sharing(method="POST", payload={"visibility": "workspace"})
        status, _body = self.sharing(reader)
        self.assertEqual(403, status)
        status, body = self.sharing(TOKEN)
        self.assertEqual(200, status)  # The bootstrap token is admin.

    def test_upload_sharing_fields_are_checked(self) -> None:
        for fields, expected in (({"visibility": "secret"}, 422), ({"workspace": "Bad Name"}, 422),
                                 ({"workspace": "ops"}, 403)):
            with self.subTest(fields):
                status, _body = self.api("/api/uploads", "POST", {"html": "<title>x</title>", **fields},
                                         token=self.editor_key)
                self.assertEqual(expected, status)
        self.auth.max_visibility = "workspace"
        status, _body = self.api("/api/uploads", "POST", {"html": "<title>x</title>", "visibility": "link"},
                                 token=self.editor_key)
        self.assertEqual(422, status)

    def test_viewer_cannot_upload_into_workspace(self) -> None:
        reader = self.create_key(self.oidc_login(sub="reader", groups=["readers"]))
        status, _body = self.api("/api/uploads", "POST", {"html": "<title>x</title>", "workspace": "team"},
                                 token=reader)
        self.assertEqual(403, status)
        # Omitting workspace must not fall back to defaultWorkspace for a viewer-only account.
        status, _body = self.api("/api/uploads", "POST", {"html": "<title>x</title>"},
                                 token=reader)
        self.assertEqual(403, status)

    def test_upload_commits_visibility_atomically_without_set_sharing(self) -> None:
        from unittest.mock import patch
        with patch.object(self.store, "set_sharing", side_effect=AssertionError("set_sharing should not run")):
            status, body = self.api(
                "/api/uploads", "POST",
                {"html": "<title>Secret</title>", "slug": "atomic-secret", "visibility": "private"},
                token=self.editor_key,
            )
        self.assertEqual(201, status, body)
        self.assertEqual("private", body["visibility"])
        status, _headers, _body = self.request("/d/atomic-secret")
        self.assertEqual(401, status)

    def test_revoking_api_key_invalidates_view_cookie_and_token(self) -> None:
        import re as _re
        key_resp = self.store.create_key("local", "temp-browser-key")
        key_token = key_resp["token"]
        key_id = key_resp["apiKey"]["id"]
        status, headers, login_body = self.request("/auth/sign-in")
        self.assertEqual(200, status)
        login_cookie = [c for c in headers["Set-Cookie"] if c.startswith("tailplan_login=")][0].split(";", 1)[0]
        csrf = _re.search(r'name="csrf" value="([^"]+)"', login_body.decode()).group(1)
        status, headers, _body = self.request(
            "/auth/sign-in", "POST", cookies=[login_cookie],
            form={"csrf": csrf, "token": key_token, "next": "/dashboard"},
        )
        self.assertEqual(303, status)
        sess_cookie = [c for c in headers["Set-Cookie"] if c.startswith("tailplan_session=")][0].split(";", 1)[0]
        # Open a multi-page private draft to mint a tailplan_view cookie and a ?tv= view token.
        multi = self.upload(
            files=[
                {"path": "index.html", "html": '<title>M</title><a href="two.html">Two</a>'},
                {"path": "two.html", "html": "<title>Two</title><p>Two</p>"},
            ],
            token=self.editor_key, visibility="private",
        )
        status, headers, body = self.request(f"/d/{multi['draftId']}/", cookies=[sess_cookie])
        self.assertEqual(200, status)
        view_cookie = [c for c in headers["Set-Cookie"] if c.startswith("tailplan_view=")][0].split(";", 1)[0]
        tv_match = _re.search(r'href="(two\.html\?tv=[^"]+)"', body.decode())
        self.assertIsNotNone(tv_match)
        tv_href = tv_match.group(1)
        # Before revocation, both view cookie and ?tv= work without session cookie.
        self.assertEqual(200, self.request(f"/d/{multi['draftId']}/", cookies=[view_cookie])[0])
        self.assertEqual(200, self.request(f"/d/{multi['draftId']}/{tv_href}")[0])
        # Revoke the originating API key -> both view cookie and ?tv= must fail immediately.
        self.store.revoke_key("local", key_id)
        self.assertEqual(401, self.request(f"/d/{multi['draftId']}/", cookies=[view_cookie])[0])
        self.assertEqual(401, self.request(f"/d/{multi['draftId']}/{tv_href}")[0])


class SharingDashboardTests(AuthCase):
    def test_sharing_card_forms_and_shared_with_me(self) -> None:
        owner = self.oidc_login(sub="owner", email="owner@example.test")
        draft = self.upload("<title>Roadmap</title>", token=self.create_key(owner), visibility="private")
        detail = f"/dashboard/drafts/{draft['draftId']}"
        status, _headers, body = self.request(detail, cookies=[owner])
        self.assertEqual(200, status)
        self.assertIn(b'id="sharing-title"', body)
        self.assertIn(b'class="badge scope">private', body)
        csrf = self.csrf(owner, detail)
        status, headers, _body = self.request(detail + "/sharing", "POST", cookies=[owner],
                                              form={"csrf": csrf, "visibility": "workspace"})
        self.assertEqual(303, status)
        self.assertTrue(headers["Location"].endswith(detail))
        status, _headers, _body = self.request(detail + "/sharing/grants", "POST", cookies=[owner],
                                               form={"csrf": "wrong", "type": "email", "value": "x@example.test"})
        self.assertEqual(403, status)
        self.request(detail + "/sharing/grants", "POST", cookies=[owner],
                     form={"csrf": csrf, "type": "email", "value": "guest@example.test", "role": "viewer"})
        _status, _headers, body = self.request(detail, cookies=[owner])
        self.assertIn(b"guest@example.test", body)

        guest = self.oidc_login(sub="guest", email="guest@example.test", groups=["outsiders"])
        _status, _headers, body = self.request("/dashboard", cookies=[guest])
        self.assertIn(b"Shared with me", body)
        self.assertIn(b"Roadmap", body)
        reader = self.oidc_login(sub="reader", email="reader@example.test", groups=["readers"])
        _status, _headers, body = self.request("/dashboard", cookies=[reader])
        self.assertIn(b"Roadmap", body)  # Workspace visibility admits team viewers.

        self.request(detail + "/sharing/revoke", "POST", cookies=[owner],
                     form={"csrf": csrf, "type": "email", "value": "guest@example.test"})
        _status, _headers, body = self.request("/dashboard", cookies=[guest])
        self.assertNotIn(b"Roadmap", body)

    def test_sharing_card_offers_tailscale_login_grants(self) -> None:
        owner = self.oidc_login(sub="owner", email="owner@example.test")
        draft = self.upload("<title>Logins</title>", token=self.create_key(owner), visibility="private")
        detail = f"/dashboard/drafts/{draft['draftId']}"
        _status, _headers, body = self.request(detail, cookies=[owner])
        self.assertIn(b'<option value="tailscale_login">Tailscale login</option>', body)
        csrf = self.csrf(owner, detail)
        status, _headers, _body = self.request(
            detail + "/sharing/grants", "POST", cookies=[owner],
            form={"csrf": csrf, "type": "tailscale_login", "value": "alice@github", "role": "viewer"})
        self.assertEqual(303, status)
        _status, _headers, body = self.request(detail, cookies=[owner])
        self.assertIn(b"alice@github", body)

    def test_dashboard_shows_badges(self) -> None:
        owner = self.oidc_login(sub="owner")
        self.upload("<title>Badge</title>", token=self.create_key(owner), visibility="authenticated")
        _status, _headers, body = self.request("/dashboard", cookies=[owner])
        self.assertIn(b'class="badge ws" title="Workspace">team', body)
        self.assertIn(b'class="badge scope" title="Visibility">authenticated', body)

    def test_unverified_email_grant_does_not_match(self) -> None:
        owner = self.oidc_login(sub="owner")
        draft = self.upload("<title>Hidden</title>", token=self.create_key(owner), visibility="private")
        self.api(f"/api/drafts/{draft['draftId']}/sharing/grants", "POST",
                 {"type": "email", "value": "guest@example.test"}, token=TOKEN)
        guest = self.oidc_login(sub="guest", email="guest@example.test", email_verified=False,
                                groups=["outsiders"])
        _status, _headers, body = self.request("/dashboard", cookies=[guest])
        self.assertNotIn(b"Hidden", body)
        status, _headers, _body = self.request(f"/d/{draft['draftId']}", cookies=[guest])
        self.assertEqual(404, status)


class ShareCliTests(AuthCase):
    def run_cli(self, *arguments: str, key: str = TOKEN) -> subprocess.CompletedProcess:
        with tempfile.TemporaryDirectory() as home:
            env = {**os.environ, "HOME": home, "TAILPLAN_API_KEY": key, "TAILPLAN_BASE_URL": self.base}
            return subprocess.run([sys.executable, str(CLI), *arguments], capture_output=True, text=True,
                                  env=env, timeout=30, check=False)

    def test_share_command_changes_visibility_and_grants(self) -> None:
        draft = self.upload(visibility="private")
        result = self.run_cli("share", draft["draftId"], "--visibility", "authenticated", "--grant-email",
                              "a@example.test", "--grant-group", "ops", "--role", "editor", "--json")
        self.assertEqual(0, result.returncode, result.stderr)
        body = json.loads(result.stdout)
        self.assertEqual("authenticated", body["visibility"])
        self.assertEqual({("email", "a@example.test", "editor"), ("group", "ops", "editor")},
                         {(grant["type"], grant["value"], grant["role"]) for grant in body["grants"]})
        result = self.run_cli("share", draft["draftId"], "--revoke-email", "a@example.test")
        self.assertEqual(0, result.returncode, result.stderr)
        self.assertIn("authenticated in workspace", result.stdout)
        self.assertIn("group ops", result.stdout)
        self.assertNotIn("a@example.test", result.stdout)

    def test_share_command_reports_the_cap(self) -> None:
        draft = self.upload(visibility="private")
        self.auth.max_visibility = "workspace"
        result = self.run_cli("share", draft["draftId"], "--visibility", "link", "--json")
        self.assertNotEqual(0, result.returncode)
        self.assertIn("maximum", result.stdout)

    def test_upload_sends_workspace_and_visibility(self) -> None:
        with tempfile.TemporaryDirectory() as folder:
            page = Path(folder) / "page.html"
            page.write_text("<title>CLI page</title><p>Body</p>", encoding="utf-8")
            result = self.run_cli("upload", str(page), "--workspace", "ops", "--visibility", "workspace",
                                  "--json", "--new")
        self.assertEqual(0, result.returncode, result.stderr + result.stdout)
        draft_id = json.loads(result.stdout)["draftId"]
        stored = self.store.resolve_draft(draft_id)
        self.assertEqual(("ops", "workspace"), (stored["workspace"], stored["visibility"]))
        result = self.run_cli("upload", "--visibility", "everyone", str(page))
        self.assertEqual(2, result.returncode)

    def test_upload_link_visibility_readback_succeeds_in_session_mode(self) -> None:
        self.auth.viewer_mode = "session"
        with tempfile.TemporaryDirectory() as folder:
            page = Path(folder) / "page.html"
            page.write_text("<title>Session Link</title><p>Body</p>", encoding="utf-8")
            result = self.run_cli("upload", str(page), "--visibility", "link", "--json", "--new")
        self.assertEqual(0, result.returncode, result.stderr + result.stdout)
        draft_id = json.loads(result.stdout)["draftId"]
        self.assertEqual("link", self.store.resolve_draft(draft_id)["visibility"])

    def test_share_command_grants_and_revokes_tailscale_logins(self) -> None:
        draft = self.upload(visibility="private")
        result = self.run_cli("share", draft["draftId"], "--grant-tailscale", "alice@github", "--json")
        self.assertEqual(0, result.returncode, result.stderr)
        self.assertIn(("tailscale_login", "alice@github", "viewer"),
                      {(grant["type"], grant["value"], grant["role"])
                       for grant in json.loads(result.stdout)["grants"]})
        result = self.run_cli("share", draft["draftId"], "--grant-email", "alice@github")
        self.assertNotEqual(0, result.returncode)
        result = self.run_cli("share", draft["draftId"], "--revoke-tailscale", "alice@github", "--json")
        self.assertEqual(0, result.returncode, result.stderr)
        self.assertEqual([], json.loads(result.stdout)["grants"])

    def test_upload_shares_with_people_in_the_same_request(self) -> None:
        with tempfile.TemporaryDirectory() as folder:
            page = Path(folder) / "page.html"
            page.write_text("<title>Shared page</title><p>Body</p>", encoding="utf-8")
            result = self.run_cli("upload", str(page), "--visibility", "private", "--grant-email",
                                  "Zed@Example.test", "--grant-tailscale", "alice@github",
                                  "--grant-group", "ops", "--grant-role", "editor", "--json", "--new")
            self.assertEqual(0, result.returncode, result.stderr + result.stdout)
            draft_id = json.loads(result.stdout)["draftId"]
            self.assertEqual({("email", "zed@example.test", "editor"),
                              ("tailscale_login", "alice@github", "editor"), ("group", "ops", "editor")},
                             {(grant["type"], grant["value"], grant["role"])
                              for grant in self.store.list_grants(draft_id)})
            drafts = len(self.store.load_meta()["drafts"])
            result = self.run_cli("upload", str(page), "--grant-email", "alice@github", "--new")
            self.assertNotEqual(0, result.returncode)
            self.assertEqual(drafts, len(self.store.load_meta()["drafts"]))

    def test_version_is_current(self) -> None:
        result = self.run_cli("--version")
        self.assertEqual(f"tailplan {server.TAILPLAN_VERSION}", result.stdout.strip())


class DeviceCliTests(AuthCase):
    def test_auth_login_device_saves_the_approved_key(self) -> None:
        import importlib.machinery
        import importlib.util

        loader = importlib.machinery.SourceFileLoader("tailplan_share_cli", str(ROOT / "bin" / "tailplan-share"))
        spec = importlib.util.spec_from_loader(loader.name, loader)
        cli = importlib.util.module_from_spec(spec)
        loader.exec_module(cli)
        cookie = self.oidc_login(sub="laptop-owner")
        messages: list[str] = []

        class Output:
            def write(self, text: str) -> None:
                messages.append(text)

            def flush(self) -> None:
                pass

        def request(path: str, method: str = "GET", payload: object = None, token: object = None) -> dict:
            status, body = self.api(path, method, payload)
            if status >= 400:
                raise cli.ConfigurationFailure(f"HTTP {status}: {body.get('error')}")
            return body

        def approve(_seconds: float) -> None:
            code = [line for line in "".join(messages).splitlines() if "code " in line][0].split("code ")[1][:9]
            csrf = self.csrf(cookie, "/cli/activate")
            self.request("/cli/activate", "POST", cookies=[cookie],
                         form={"csrf": csrf, "code": code, "decision": "approve"})

        key = cli.device_login(request, self.base, client_name="test", stderr=Output(), sleeper=approve)
        status, me = self.api("/api/me", token=key)
        self.assertEqual(server.oidc_account_id(self.idp.issuer, "laptop-owner"), me["accountId"])
        self.assertIn("/cli/activate?code=", "".join(messages))


if __name__ == "__main__":
    unittest.main()
