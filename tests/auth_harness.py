"""Shared setup for the sign-in, role, and sharing tests."""
from __future__ import annotations

import io
import json
import re
import tempfile
import threading
import unittest
from http.client import HTTPConnection
from pathlib import Path
from urllib.parse import parse_qs, urlencode, urlparse

import tailplan_server as server
from fake_idp import CLIENT_ID, CLIENT_SECRET, FakeIdP

TOKEN = "bootstrap-test"
SESSION_SECRET = "dedicated-session-secret-for-tests"
POLICY = {
    "legacy_workspace": "owner",
    "default_workspace": "team",
    "rules": [
        {"match": {"group": "staff"}, "workspaces": {"team": "editor"}, "default_workspace": "team"},
        {"match": {"group": "readers"}, "workspaces": {"team": "viewer"}},
        {"match": {"group": "ops"}, "workspaces": {"ops": "admin"}, "default_workspace": "ops"},
        {"match": {"group": "auditors"}, "instance_role": "auditor"},
        {"match": {"email": "boss@example.test"}, "instance_role": "admin"},
        {"match": {"group": "outsiders"}},
    ],
}


def cookie_pair(header: str) -> str:
    return header.split(";", 1)[0]


class AuthCase(unittest.TestCase):
    """Run Tailplan with a fake OIDC provider. Subclasses can change the settings."""

    viewer_mode = "network"
    max_visibility = "link"
    modes: tuple[str, ...] | None = None
    policy: dict | None = POLICY
    link_emails: frozenset[str] = frozenset()
    use_oidc = True

    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.idp = FakeIdP()
        self.addCleanup(self.idp.close)
        self.store = server.Store(Path(self.temporary.name) / "data")
        self.audit_lines = io.StringIO()
        self.store.audit_stream = self.audit_lines
        self.auth = self.make_auth()
        self.primary, self.proxy = server.create_servers(
            ("127.0.0.1", 0), None, store=self.store, token=TOKEN, base_url="",
            redirect_view_base_url="", session_secret=SESSION_SECRET, auth=self.auth,
        )
        thread = threading.Thread(target=self.primary.serve_forever, daemon=True)
        thread.start()

        def stop() -> None:
            self.primary.shutdown()
            self.primary.server_close()
            thread.join()

        self.addCleanup(stop)
        self.base = self.primary.base_url

    def make_client(self, **options) -> server.OIDCClient:
        return server.OIDCClient(issuer=self.idp.issuer, client_id=CLIENT_ID, client_secret=CLIENT_SECRET,
                                 allow_private=True, **options)

    def make_auth(self) -> server.AuthConfig:
        oidc = self.make_client() if self.use_oidc else None
        modes = self.modes or server.parse_auth_modes(None, oidc=oidc is not None)
        return server.AuthConfig(modes=modes, policy=server.RBACPolicy(self.policy), viewer_mode=self.viewer_mode,
                                 max_visibility=self.max_visibility, oidc=oidc, link_emails=self.link_emails)

    def request(self, path: str, method: str = "GET", payload: object = None, *, token: str | None = None,
                cookies: list[str] | None = None, headers: dict | None = None,
                form: dict | None = None) -> tuple[int, dict, bytes]:
        request_headers = dict(headers or {})
        if token:
            request_headers["Authorization"] = "Bearer " + token
        if cookies:
            request_headers["Cookie"] = "; ".join(cookies)
        body = None
        if form is not None:
            body = urlencode(form).encode()
            request_headers["Content-Type"] = "application/x-www-form-urlencoded"
        elif payload is not None:
            body = json.dumps(payload).encode()
            request_headers["Content-Type"] = "application/json"
        connection = HTTPConnection("127.0.0.1", self.primary.server_port, timeout=10)
        try:
            connection.request(method, path, body=body, headers=request_headers)
            response = connection.getresponse()
            raw = response.read()
            response_headers = {key: value for key, value in response.getheaders() if key != "Set-Cookie"}
            response_headers["Set-Cookie"] = [value for key, value in response.getheaders() if key == "Set-Cookie"]
            return response.status, response_headers, raw
        finally:
            connection.close()

    def api(self, path: str, method: str = "GET", payload: object = None, **options) -> tuple[int, dict]:
        status, _headers, body = self.request(path, method, payload,
                                              headers={"Accept": "application/json"}, **options)
        return status, json.loads(body) if body else {}

    def upload(self, doc: str = "<title>Plan</title><p>Body</p>", *, token: str = TOKEN, **fields) -> dict:
        payload = fields if "files" in fields else {"html": doc, **fields}
        status, result = self.api("/api/uploads", "POST", payload, token=token)
        self.assertIn(status, {200, 201}, result)
        return result

    def oidc_login(self, **claims) -> str:
        """Sign in through the fake provider and return the session cookie."""
        self.idp.claims = {**self.idp.claims, **claims}
        status, headers, _body = self.request("/auth/oidc/start?next=/dashboard")
        self.assertEqual(302, status)
        flow_cookie = cookie_pair(headers["Set-Cookie"][0])
        authorize = urlparse(headers["Location"])
        connection = HTTPConnection(authorize.hostname, authorize.port, timeout=5)
        connection.request("GET", authorize.path + "?" + authorize.query)
        response = connection.getresponse()
        response.read()
        connection.close()
        callback = urlparse(response.getheader("Location"))
        status, headers, body = self.request(callback.path + "?" + callback.query, cookies=[flow_cookie])
        self.assertEqual(303, status, body)
        session = [value for value in headers["Set-Cookie"] if value.startswith("tailplan_session=")]
        return cookie_pair(session[0])

    def oidc_attempt(self, **claims) -> tuple[int, bytes]:
        self.idp.claims = {**self.idp.claims, **claims}
        status, headers, _body = self.request("/auth/oidc/start")
        flow_cookie = cookie_pair(headers["Set-Cookie"][0])
        query = parse_qs(urlparse(headers["Location"]).query)
        code = "code-" + query["state"][0][:8]
        self.idp.codes[code] = {key: values[0] for key, values in query.items()}
        status, _headers, body = self.request(
            "/auth/oidc/callback?" + urlencode({"code": code, "state": query["state"][0]}), cookies=[flow_cookie])
        return status, body

    def csrf(self, cookie: str, path: str = "/dashboard") -> str:
        status, _headers, body = self.request(path, cookies=[cookie])
        self.assertEqual(200, status, body[:300])
        return re.search(rb'name="csrf" value="([^"]+)"', body)[1].decode()

    def bootstrap_session(self) -> str:
        status, headers, body = self.request("/auth/sign-in")
        csrf = re.search(rb'name="csrf" value="([^"]+)"', body)[1].decode()
        login = cookie_pair(headers["Set-Cookie"][0])
        status, headers, _body = self.request("/auth/sign-in", "POST", cookies=[login],
                                              form={"csrf": csrf, "next": "/dashboard", "token": TOKEN})
        self.assertEqual(303, status)
        return cookie_pair(headers["Set-Cookie"][0])

    def events(self, name: str | None = None) -> list[dict]:
        """Return the audit wide events that were printed, or those of one event name such as `auth.login`."""
        lines = [json.loads(line) for line in self.audit_lines.getvalue().splitlines() if line]
        return [line for line in lines if name is None or line["event"] == "tailplan." + name]

    def event_names(self, name: str | None = None) -> list[str]:
        return [event["event"].removeprefix("tailplan.") for event in self.events(name)]

    def create_key(self, cookie: str) -> str:
        """Create an API key on the account of a browser session."""
        csrf = self.csrf(cookie, "/cli/auth")
        status, _headers, body = self.request("/cli/auth/keys", "POST", cookies=[cookie],
                                              form={"csrf": csrf, "name": "test key"})
        self.assertEqual(200, status, body[:300])
        return re.search(rb'id="key" readonly value="([^"]+)"', body)[1].decode()
