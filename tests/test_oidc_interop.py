"""Opt-in checks against real OpenID Connect providers.

Two gates control these tests:

- TAILPLAN_INTEROP_ISSUER names a live issuer, for example
  https://auth.carlsonhouse.com/application/o/tailplan/ for Authentik.
  TAILPLAN_INTEROP_CLIENT_ID and TAILPLAN_INTEROP_CLIENT_SECRET_FILE add the
  client checks. The secret stays in that file.
- TAILPLAN_INTEROP_DOCKER=1 starts a disposable Keycloak 26 in Docker. The test
  signs in through its login form and reads the account from Tailplan.

The unit lane ignores this file. The interop CI job runs the Keycloak tests.
"""
from __future__ import annotations

import base64
import html
import json
import os
import re
import secrets
import shutil
import subprocess
import tempfile
import time
import unittest
from http.cookiejar import CookieJar
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.parse import parse_qs, quote, urlencode, urlparse
from urllib.request import HTTPCookieProcessor, HTTPRedirectHandler, Request, build_opener, urlopen

import tailplan_server as server
from auth_harness import AuthCase, cookie_pair

ISSUER = os.getenv("TAILPLAN_INTEROP_ISSUER", "")
CLIENT_ID = os.getenv("TAILPLAN_INTEROP_CLIENT_ID", "")
SECRET_FILE = os.getenv("TAILPLAN_INTEROP_CLIENT_SECRET_FILE", "")
DOCKER = os.getenv("TAILPLAN_INTEROP_DOCKER") == "1"
KEYCLOAK_IMAGE = os.getenv("TAILPLAN_INTEROP_KEYCLOAK_IMAGE", "quay.io/keycloak/keycloak:26.0")
ASYMMETRIC = {"RS256", "RS384", "RS512", "PS256", "PS384", "PS512", "ES256", "ES384", "ES512", "EdDSA"}


def basic(client_id: str, secret: str) -> str:
    pair = f"{quote(client_id, safe='')}:{quote(secret, safe='')}"
    return "Basic " + base64.b64encode(pair.encode()).decode()


@unittest.skipUnless(ISSUER, "Set TAILPLAN_INTEROP_ISSUER to check a live identity provider")
class LiveIssuerTests(unittest.TestCase):
    """Check discovery, keys, and client authentication on a live issuer."""

    def client(self) -> server.OIDCClient:
        return server.OIDCClient(issuer=ISSUER, client_id=CLIENT_ID or "interop-probe",
                                 client_secret=self.secret() or "interop-probe")

    def secret(self) -> str:
        return Path(SECRET_FILE).read_text().strip() if SECRET_FILE else ""

    def test_discovery_passes_the_origin_and_issuer_checks(self):
        document = self.client().metadata()
        self.assertEqual(ISSUER, document["issuer"])
        self.assertIn("S256", document.get("code_challenge_methods_supported", ["S256"]))
        self.assertIn("client_secret_basic",
                      document.get("token_endpoint_auth_methods_supported", ["client_secret_basic"]))
        algorithms = set(document.get("id_token_signing_alg_values_supported", ["RS256"]))
        self.assertTrue(algorithms & ASYMMETRIC, algorithms)

    def test_key_set_has_asymmetric_signing_keys_only_after_filtering(self):
        keys = self.client()._load_jwks()
        self.assertTrue(keys)
        self.assertTrue(all(key["kty"] in {"RSA", "EC", "OKP"} for key in keys))

    def test_authorization_url_uses_pkce_s256(self):
        url = self.client().authorization_url(state="s", nonce="n", challenge="c" * 43,
                                              redirect_uri="https://tailplan.example/auth/oidc/callback")
        query = parse_qs(urlparse(url).query)
        self.assertEqual(["S256"], query["code_challenge_method"])
        self.assertEqual(server.url_origin(ISSUER), server.url_origin(url))

    @unittest.skipUnless(CLIENT_ID and SECRET_FILE, "Set the interop client ID and secret file")
    def test_token_endpoint_accepts_client_secret_basic(self):
        client = self.client()
        body = urlencode({"grant_type": "authorization_code", "code": "not-a-real-code",
                          "redirect_uri": "https://tailplan.example/auth/oidc/callback",
                          "code_verifier": "v" * 43}).encode()
        status, raw = client.fetch(client.metadata()["token_endpoint"], method="POST", body=body, headers={
            "Content-Type": "application/x-www-form-urlencoded",
            "Authorization": basic(CLIENT_ID, self.secret()),
        })
        # The provider accepts the client and rejects only the code.
        self.assertEqual(400, status, raw[:200])
        self.assertEqual("invalid_grant", json.loads(raw)["error"])


class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, *args, **kwargs):
        return None


def realm(secret: str, password: str) -> dict:
    return {
        "realm": "tailplan", "enabled": True,
        "groups": [{"name": "staff"}],
        "users": [{
            "username": "alice", "email": "alice@example.test", "emailVerified": True, "enabled": True,
            "firstName": "Alice", "lastName": "Example", "groups": ["/staff"],
            "credentials": [{"type": "password", "value": password, "temporary": False}],
        }],
        "clients": [{
            "clientId": "tailplan", "enabled": True, "publicClient": False, "secret": secret,
            "clientAuthenticatorType": "client-secret", "standardFlowEnabled": True,
            "directAccessGrantsEnabled": False, "redirectUris": ["*"],
            "attributes": {"pkce.code.challenge.method": "S256"},
            "protocolMappers": [{
                "name": "groups", "protocol": "openid-connect",
                "protocolMapper": "oidc-group-membership-mapper",
                "config": {"claim.name": "groups", "full.path": "false", "id.token.claim": "true",
                           "access.token.claim": "true", "userinfo.token.claim": "true"},
            }],
        }],
    }


@unittest.skipUnless(DOCKER and shutil.which("docker"), "Set TAILPLAN_INTEROP_DOCKER=1 to start Keycloak")
class KeycloakTests(AuthCase):
    """Sign in through a disposable Keycloak 26 with the real login form."""

    viewer_mode = "session"

    @classmethod
    def setUpClass(cls) -> None:
        cls.client_secret = secrets.token_urlsafe(24)
        cls.password = secrets.token_urlsafe(16)
        cls.realm_dir = tempfile.TemporaryDirectory()
        folder = Path(cls.realm_dir.name)
        folder.chmod(0o755)
        realm_file = folder / "tailplan-realm.json"
        realm_file.write_text(json.dumps(realm(cls.client_secret, cls.password)))
        realm_file.chmod(0o644)
        cls.container = subprocess.run(
            ["docker", "run", "-d", "--rm", "-p", "127.0.0.1::8080",
             "-e", "KC_BOOTSTRAP_ADMIN_USERNAME=admin", "-e", "KC_BOOTSTRAP_ADMIN_PASSWORD",
             "-v", f"{folder}:/opt/keycloak/data/import:ro", KEYCLOAK_IMAGE, "start-dev", "--import-realm"],
            # Docker copies the password from this environment, so it stays out of the arguments.
            env={**os.environ, "KC_BOOTSTRAP_ADMIN_PASSWORD": cls.password},
            check=True, capture_output=True, text=True, timeout=600).stdout.strip()
        try:
            port = subprocess.run(["docker", "port", cls.container, "8080/tcp"], check=True,
                                  capture_output=True, text=True, timeout=30).stdout.split(":")[-1].strip()
            cls.issuer = f"http://127.0.0.1:{port}/realms/tailplan"
            deadline = time.monotonic() + 180
            while True:
                try:
                    with urlopen(cls.issuer + "/.well-known/openid-configuration", timeout=5) as response:
                        if response.status == 200:
                            break
                except (URLError, ConnectionError, OSError):
                    pass
                if time.monotonic() > deadline:
                    raise RuntimeError("Keycloak did not start in time")
                time.sleep(2)
        except BaseException:
            cls.tearDownClass()
            raise

    @classmethod
    def tearDownClass(cls) -> None:
        subprocess.run(["docker", "rm", "-f", cls.container], capture_output=True, timeout=60)
        cls.realm_dir.cleanup()

    def make_client(self, **options) -> server.OIDCClient:
        # Keycloak refuses a scope that the client does not have, so leave out `groups`.
        # The client mapper still adds the groups claim.
        return server.OIDCClient(issuer=self.issuer, client_id="tailplan", client_secret=self.client_secret,
                                 scopes="openid profile email", allow_private=True, **options)

    def keycloak_login(self, next_path: str = "/dashboard") -> str:
        status, headers, _body = self.request("/auth/oidc/start?" + urlencode({"next": next_path}))
        self.assertEqual(302, status)
        flow_cookie = cookie_pair(headers["Set-Cookie"][0])
        browser = build_opener(HTTPCookieProcessor(CookieJar()), NoRedirect)
        with browser.open(headers["Location"], timeout=30) as response:
            page = response.read().decode()
        action = html.unescape(re.search(r'<form[^>]+id="kc-form-login"[^>]+action="([^"]+)"', page)[1])
        form = urlencode({"username": "alice", "password": self.password, "credentialId": ""}).encode()
        try:
            browser.open(Request(action, data=form), timeout=30)
            self.fail("Keycloak did not redirect after sign-in")
        except HTTPError as redirect:
            self.assertEqual(302, redirect.code, redirect.read()[:300])
            callback = urlparse(redirect.headers["Location"])
        self.assertEqual("/auth/oidc/callback", callback.path)
        status, headers, body = self.request(callback.path + "?" + callback.query, cookies=[flow_cookie])
        self.assertEqual(303, status, body[:300])
        self.assertEqual(next_path, headers["Location"])
        session = [value for value in headers["Set-Cookie"] if value.startswith("tailplan_session=")]
        return cookie_pair(session[0])

    def test_keycloak_sign_in_maps_groups_to_workspace_roles(self):
        cookie = self.keycloak_login()
        status, me = self.api("/api/me", cookies=[cookie])
        self.assertEqual(200, status, me)
        self.assertEqual("alice@example.test", me["email"])
        self.assertEqual({"team": "editor"}, me["workspaces"])
        login = self.events("auth.login")[-1]
        self.assertEqual("oidc", login["detail"]["method"])
        self.assertEqual("success", login["outcome"])

    def test_keycloak_account_is_keyed_on_issuer_and_subject(self):
        cookie = self.keycloak_login()
        _status, me = self.api("/api/me", cookies=[cookie])
        again = self.keycloak_login()
        _status, second = self.api("/api/me", cookies=[again])
        self.assertEqual(me["accountId"], second["accountId"])
        self.assertNotEqual(cookie, again)

    def test_keycloak_reader_can_open_a_workspace_draft(self):
        draft = self.upload(workspace="team", visibility="workspace")
        path = f"/d/{draft['draftId']}/"
        status, headers, _body = self.request(path, headers={"Accept": "text/html"})
        self.assertEqual(302, status)
        self.assertTrue(headers["Location"].startswith("/auth/sign-in?next="), headers["Location"])
        cookie = self.keycloak_login(path)
        status, _headers, body = self.request(path, cookies=[cookie])
        self.assertEqual(200, status)
        self.assertIn(b"<p>Body</p>", body)


if __name__ == "__main__":
    unittest.main()
