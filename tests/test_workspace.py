from __future__ import annotations

import hashlib
import json
import os
import re
import subprocess
import sys
import tempfile
import threading
import unittest
from http.client import HTTPConnection
from pathlib import Path
from urllib.parse import urlencode

import tailplan_server as server

ROOT = Path(__file__).resolve().parents[1]


class WorkspaceTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        self.store = server.Store(self.root / "data")
        self.httpd, self.proxy = server.create_servers(
            ("127.0.0.1", 0), ("127.0.0.1", 0), store=self.store, token="bootstrap-test",
            base_url="", redirect_view_base_url="",
        )
        self.base = f"http://127.0.0.1:{self.httpd.server_port}/tailplan"
        self.httpd.base_url = self.proxy.base_url = self.base
        self.threads = []
        for listener in (self.httpd, self.proxy):
            thread = threading.Thread(target=listener.serve_forever, daemon=True)
            thread.start()
            self.threads.append(thread)

    def tearDown(self):
        for listener in (self.httpd, self.proxy):
            listener.shutdown()
            listener.server_close()
        for thread in self.threads:
            thread.join()
        self.temporary.cleanup()

    def request(self, path, method="GET", payload=None, token="bootstrap-test", headers=None, proxy=False):
        request_headers = dict(headers or {})
        if token:
            request_headers["Authorization"] = "Bearer " + token
        body = None
        if payload is not None:
            body = payload if isinstance(payload, bytes) else json.dumps(payload).encode()
            request_headers.setdefault("Content-Type", "application/json")
        listener = self.proxy if proxy else self.httpd
        conn = HTTPConnection("127.0.0.1", listener.server_port, timeout=5)
        try:
            conn.request(method, path, body=body, headers=request_headers)
            response = conn.getresponse()
            raw = response.read()
            return response.status, dict(response.headers), raw
        finally:
            conn.close()

    def api(self, path, method="GET", payload=None, **kwargs):
        status, headers, body = self.request(path, method, payload, **kwargs)
        return status, headers, json.loads(body)

    def upload(self, doc="<title>Draft</title><p>content</p>", **fields):
        status, _, result = self.api("/api/uploads", "POST", {"html": doc, **fields})
        self.assertIn(status, {200, 201}, result)
        return result

    def login(self, token="bootstrap-test", *, proxy=False, headers=None):
        status, response_headers, body = self.request("/tailplan/dashboard", token=None,
                                                     proxy=proxy, headers=headers)
        self.assertEqual(200, status)
        csrf = re.search(rb'name="csrf" value="([^"]+)"', body)[1].decode()
        login_cookie = response_headers["Set-Cookie"].split(";", 1)[0]
        payload = {"csrf": csrf, "next": "/dashboard", "token": token}
        if token is None:
            payload["method"] = "tailscale"
        status, response_headers, body = self.request(
            "/tailplan/auth/sign-in", "POST", urlencode(payload).encode(), token=None,
            proxy=proxy, headers={**(headers or {}), "Cookie": login_cookie,
                                  "Content-Type": "application/x-www-form-urlencoded"},
        )
        self.assertEqual(303, status, body)
        return response_headers["Set-Cookie"].split(";", 1)[0]

    def test_raw_and_version_contract_preserves_utf8_and_crlf(self):
        doc = '<!doctype html>\r\n<title>Résumé</title>\r\n<script>window.didRun=true</script><a href="https://example.test">Link</a>'
        first = self.upload(doc)
        second = self.upload("<title>Second</title>", draftId=first["draftId"])
        self.assertEqual(first["publicUrl"], second["publicUrl"])
        self.assertEqual(first["publicUrl"] + "/raw", first["rawUrl"])
        for suffix in ("", "/raw", "/content"):
            for agent in ("Mozilla/5.0", "curl/8.0", "agent-fetch/1"):
                status, headers, body = self.request(f"/tailplan/d/{first['draftId']}/v/1{suffix}",
                                                    token=None, headers={"User-Agent": agent})
                self.assertEqual(200, status)
                self.assertEqual(doc.encode(), body)
                self.assertEqual("1", headers["X-Postplan-Draft-Version"])
                self.assertEqual(first["draftId"], headers["X-Tailplan-Draft-Id"])
                self.assertEqual(hashlib.sha256(body).hexdigest(), headers["X-Tailplan-Content-SHA256"])
                self.assertIn("script-src 'none'", headers["Content-Security-Policy"])
        status, headers, body = self.request(f"/d/{first['draftId']}/raw", "HEAD", token=None)
        self.assertEqual(200, status)
        self.assertEqual(b"", body)
        self.assertEqual("2", headers["X-Tailplan-Draft-Version"])

    def test_descriptions_provenance_history_and_replay(self):
        metadata = {"repoHost": "git.example.test", "repoOrg": "team", "repoName": "repo",
                    "gitBranch": "feature", "gitCommitSha": "abc123", "gitCommitSubject": "Change", "gitDirty": True,
                    "ciRunUrl": "https://ci.example.test/runs/1", "ciActor": "test-agent", "cliVersion": "0.0.4"}
        payload = {"html": '<title>First</title><img src="https://images.example.test/a.png"><script>x=1</script>',
                   "description": "Stable label", "metadata": metadata}
        status, _, first = self.api("/api/uploads", "POST", payload, headers={"Idempotency-Key": "same"})
        self.assertEqual(201, status)
        status, _, replay = self.api("/api/uploads", "POST", payload, headers={"Idempotency-Key": "same"})
        self.assertEqual(200, status)
        self.assertEqual(first["versionId"], replay["versionId"])
        self.assertEqual(409, self.api("/api/uploads", "POST", {**payload, "description": "Other"},
                                     headers={"Idempotency-Key": "same"})[0])
        self.upload("<title>Second</title>", draftId=first["draftId"])
        drafts = self.api("/tailplan/api/drafts")[2]["drafts"]
        self.assertEqual(1, len(drafts))
        self.assertEqual("Stable label", drafts[0]["description"])
        self.assertEqual("repo", drafts[0]["repoName"])
        self.assertEqual(2, drafts[0]["versionCount"])
        history = self.api(f"/api/drafts/{first['draftId']}/versions")[2]["versions"]
        self.assertEqual([2, 1], [v["versionNumber"] for v in history])
        for key, value in metadata.items():
            self.assertEqual(value, history[1][key])
        self.assertEqual(["images.example.test"], history[1]["externalImageHosts"])
        self.assertTrue(history[1]["hasInlineScript"])
        self.assertEqual("bootstrap", history[1]["apiKeyId"])
        self.assertEqual("127.0.0.1", history[1]["sourceIp"])
        self.assertTrue(history[1]["requestId"])
        reopened = server.Store(self.store.root)
        self.assertEqual(history, reopened.draft_detail("local", first["draftId"], self.base)["versions"])

    def test_account_keys_enforce_ownership_and_revocation(self):
        self.store.identity_account("second@example.test", "Second", "")
        other_id = server.sha256_text("tailscale:second@example.test")[:32]
        other_key = self.store.create_key(other_id, "Other")["token"]
        _, _, minted = self.api("/api/api-keys", "POST", {"name": "Agent"})
        token, key_id = minted["token"], minted["apiKey"]["id"]
        identity = self.api("/api/me", token=token)[2]
        self.assertEqual("local", identity["accountId"])
        self.assertEqual("Agent", identity["apiKeyName"])
        self.assertNotIn(token, self.store.meta.read_text())
        first = self.upload()
        for path, method, payload in (
            (f"/api/drafts/{first['draftId']}", "GET", None),
            (f"/api/drafts/{first['draftId']}", "DELETE", None),
            (f"/api/drafts/{first['draftId']}/disable", "POST", {}),
            ("/api/uploads", "POST", {"html": "<title>Other</title>", "draftId": first["draftId"]}),
            (f"/api/api-keys/{key_id}/revoke", "POST", {}),
        ):
            self.assertEqual(404, self.api(path, method, payload, token=other_key)[0])
        self.assertEqual([], self.api("/api/drafts", token=other_key)[2]["drafts"])
        self.assertEqual(200, self.api(f"/api/api-keys/{key_id}/revoke", "POST", {})[0])
        self.assertEqual(401, self.api("/api/me", token=token)[0])
        self.assertEqual(401, self.api("/api/me", token="invalid")[0])

    def test_lifecycle_hides_every_version_and_retains_files(self):
        first = self.upload()
        draft_id = first["draftId"]
        self.upload("<title>Second</title>", draftId=draft_id)
        for action in ("disable", "enable", "disable"):
            self.assertEqual(200, self.api(f"/api/drafts/{draft_id}/{action}", "POST", {"reason": "Paused"})[0])
            for route in (f"/d/{draft_id}", f"/d/{draft_id}/v/1/raw"):
                self.assertEqual(200 if action == "enable" else 404, self.request(route, token=None)[0])
        self.assertEqual(200, self.api(f"/api/drafts/{draft_id}", "DELETE")[0])
        self.assertEqual([], self.api("/api/drafts")[2]["drafts"])
        self.assertEqual(404, self.api(f"/api/drafts/{draft_id}/enable", "POST", {})[0])
        self.assertTrue((self.store.drafts / draft_id / "v1.html").is_file())

    def test_sessions_dashboard_key_forms_and_csrf(self):
        draft = self.upload('<title>&lt;script&gt;Title&lt;/script&gt;</title>', description="<img src=x>",
                            metadata={"repoHost": "example.test", "repoOrg": "team", "repoName": "repo"})
        cookie = self.login()
        headers = {"Cookie": cookie}
        status, response_headers, body = self.request("/tailplan/dashboard", token=None, headers=headers)
        self.assertEqual(200, status)
        self.assertIn(b"team/repo", body)
        self.assertIn(b"&lt;img src=x&gt;", body)
        self.assertNotIn(b"<script>Title", body)
        self.assertIn(b"/tailplan/dashboard/drafts/", body)
        self.assertIn("form-action 'self'", response_headers["Content-Security-Policy"])
        self.assertEqual("same-origin", response_headers["Referrer-Policy"])
        csrf = re.search(rb'name="csrf" value="([^"]+)"', body)[1].decode()
        form_headers = {**headers, "Content-Type": "application/x-www-form-urlencoded"}
        self.assertEqual(403, self.api("/cli/auth/keys", "POST", b"name=Bad", token=None, headers=form_headers)[0])
        payload = urlencode({"csrf": csrf, "name": "Browser key"}).encode()
        self.assertEqual(403, self.request("/cli/auth/keys", "POST", payload, token=None,
                                          headers={**form_headers, "Origin": "https://other.test"})[0])
        status, _, body = self.request("/cli/auth/keys", "POST", payload, token=None, headers=form_headers)
        self.assertEqual(200, status)
        key = re.search(rb'id="key" readonly value="([^"]+)"', body)[1].decode()
        self.assertEqual(200, self.api("/api/me", token=key)[0])
        status, _, body = self.request("/cli/auth", token=None, headers=headers)
        self.assertNotIn(key.encode(), body)
        status, _, body = self.request(f"/dashboard/drafts/{draft['draftId']}", token=None, headers=headers)
        self.assertEqual(200, status)
        self.assertIn(b"Version history", body)
        self.assertIn(b"Audit", body)
        status, response_headers, _ = self.request("/auth/sign-out", "POST", urlencode({"csrf": csrf}).encode(),
                                                 token=None, headers=form_headers)
        self.assertEqual(303, status)
        self.assertIn("Max-Age=0", response_headers["Set-Cookie"])

    def test_revoked_key_invalidates_its_browser_session(self):
        key = self.store.create_key("local", "Session")
        cookie = self.login(key["token"])
        self.store.revoke_key("local", key["apiKey"]["id"])
        _, _, body = self.request("/dashboard", token=None, headers={"Cookie": cookie})
        self.assertIn(b"Sign in", body)
        self.assertIsNone(server.read_signed(server.signed_value({"expires": 1}, "secret"), "secret"))

    def test_tailscale_identity_is_trusted_only_on_configured_proxy(self):
        headers = {"Tailscale-User-Login": "owner@example.test", "Tailscale-User-Name": "Owner"}
        _, _, body = self.request("/dashboard", token=None, headers=headers)
        self.assertNotIn(b"Continue with Tailscale", body)
        self.proxy.trust_tailscale_identity = True
        self.proxy.owner_login = "owner@example.test"
        cookie = self.login(None, proxy=True, headers=headers)
        session = server.read_signed(cookie.split("=", 1)[1], "bootstrap-test")
        self.assertEqual("local", session["accountId"])
        other_headers = {"Tailscale-User-Login": "other@example.test"}
        other = self.login(None, proxy=True, headers=other_headers)
        other_session = server.read_signed(other.split("=", 1)[1], "bootstrap-test")
        self.assertNotEqual("local", other_session["accountId"])

    def test_legacy_data_and_historical_checksums(self):
        draft = self.upload()
        self.upload("<title>Two</title>", draftId=draft["draftId"])
        data = self.store.load_meta()
        for field in ("versions", "accountId", "description"):
            data["drafts"][draft["draftId"]].pop(field, None)
        self.store.save_meta(data)
        history = self.api(f"/api/drafts/{draft['draftId']}")[2]
        self.assertEqual(2, len(history["versions"]))
        self.assertTrue(all(version["legacy"] for version in history["versions"]))
        self.upload("<title>Three</title>", draftId=draft["draftId"])
        path = self.store.drafts / draft["draftId"] / "v1.html"
        path.write_text("<title>Changed</title>")
        self.assertEqual(503, self.request(f"/d/{draft['draftId']}/v/1", token=None)[0])

    def test_upload_limits_and_anonymous_opt_in(self):
        self.assertEqual(401, self.api("/api/uploads", "POST", {"html": "<title>Anonymous</title>"}, token=None)[0])
        self.httpd.allow_anonymous_uploads = True
        self.assertEqual(201, self.api("/api/uploads", "POST", {"html": "<title>Anonymous</title>"}, token=None)[0])
        self.assertEqual([], self.api("/api/drafts")[2]["drafts"])
        self.httpd.upload_key_limit = 1
        self.upload()
        status, headers, _ = self.api("/api/uploads", "POST", {"html": "<title>Too many</title>"})
        self.assertEqual(429, status)
        self.assertGreater(int(headers["Retry-After"]), 0)

    def test_cli_auth_upload_list_history_and_keys(self):
        home = self.root / "home"
        home.mkdir()
        source = self.root / "draft.html"
        source.write_bytes(b'<title>CLI</title>\r\n<a href="https://example.test">Exact</a>')
        env = {**os.environ, "HOME": str(home), "TAILPLAN_BASE_URL": self.base}
        env.pop("TAILPLAN_API_KEY", None)
        env.pop("TAILPLAN_TOKEN_FILE", None)

        def cli(*args, stdin=None):
            run = subprocess.run([sys.executable, str(ROOT / "bin/tailplan"), *args],
                                 env=env, input=stdin, text=True, capture_output=True, timeout=10, check=False)
            self.assertEqual(0, run.returncode, run.stderr + run.stdout)
            return json.loads(run.stdout)

        self.assertEqual("local", cli("auth", "set", "--json", stdin="bootstrap-test\n")["accountId"])
        self.assertEqual(0o600, (home / ".tailplan/token").stat().st_mode & 0o777)
        first = cli("upload", str(source), "--description", "CLI draft", "--json")
        self.assertTrue(first["rawUrl"].endswith("/raw"))
        _, _, content = self.request(first["rawUrl"].removeprefix(self.base), token=None)
        self.assertEqual(source.read_bytes(), content)
        second = cli("upload", str(source), "--json")
        self.assertEqual(first["draftId"], second["draftId"])
        self.assertEqual(2, second["versionNumber"])
        drafts = cli("list", "--json")
        self.assertEqual("CLI draft", drafts[0]["description"])
        self.assertEqual(2, len(cli("history", first["draftId"], "--json")["versions"]))
        key = cli("keys", "create", "CLI key", "--json")
        cli("keys", "revoke", key["apiKey"]["id"], "--json")
        self.assertEqual(401, self.api("/api/me", token=key["token"])[0])
        cli("disable", first["draftId"], "--json")
        cli("enable", first["draftId"], "--json")
        cli("delete", first["draftId"], "--json")
        self.assertEqual([], cli("list", "--json"))


if __name__ == "__main__":
    unittest.main()
