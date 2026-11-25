"""Cover OIDC sign-in, roles, the draft gate, device login, and audit events."""
from __future__ import annotations

import argparse
import json
import os
import re
import socket
import subprocess
import sys
import time
import unittest
from pathlib import Path
from unittest.mock import patch
from urllib.parse import parse_qs, quote, urlparse

import jwt

import tailplan_server as server
from auth_harness import SESSION_SECRET, TOKEN, AuthCase, cookie_pair
from fake_idp import CLIENT_ID, CLIENT_SECRET

ROOT = Path(__file__).resolve().parents[1]


class LazyImportTests(unittest.TestCase):
    """The stdlib install must start without PyJWT when OIDC is off."""

    def run_python(self, code: str) -> subprocess.CompletedProcess:
        env = {**os.environ, "PYTHONPATH": str(ROOT)}
        env.pop("TAILPLAN_OIDC_ISSUER", None)
        return subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, env=env, timeout=60)

    def test_server_imports_and_configures_without_pyjwt(self) -> None:
        result = self.run_python(
            "import sys\n"
            "sys.modules['jwt'] = None\n"
            "import tailplan_server as server\n"
            "args = server.parse_args([])\n"
            "auth = server.build_auth_config(args, None)\n"
            "assert auth.oidc is None and auth.modes == ('api_key', 'tailscale'), auth.modes\n"
            "print('ok')\n")
        self.assertEqual(0, result.returncode, result.stderr)
        self.assertEqual("ok", result.stdout.strip())

    def test_module_import_does_not_load_pyjwt(self) -> None:
        result = self.run_python("import sys, tailplan_server\nprint('jwt' in sys.modules)")
        self.assertEqual("False", result.stdout.strip(), result.stderr)

    def test_issuer_without_pyjwt_fails_with_install_hint(self) -> None:
        result = self.run_python(
            "import sys\n"
            "sys.modules['jwt'] = None\n"
            "import tailplan_server as server\n"
            "args = server.parse_args(['--oidc-issuer', 'https://idp.example.test', '--oidc-client-id', 'c'])\n"
            "import os; os.environ['TAILPLAN_OIDC_CLIENT_SECRET'] = 's'\n"
            "try:\n"
            "    server.build_auth_config(args, 'x' * 32)\n"
            "except ValueError as exc:\n"
            "    print(exc)\n")
        self.assertIn("tailplan[oidc]", result.stdout, result.stderr)


class ConfigurationTests(unittest.TestCase):
    def args(self, **values) -> argparse.Namespace:
        with patch.dict(os.environ, {}, clear=False):
            for name in [name for name in os.environ if name.startswith("TAILPLAN_")]:
                os.environ.pop(name)
            return server.parse_args([f"--{key.replace('_', '-')}={value}" for key, value in values.items()])

    def test_auth_modes_default_by_issuer(self) -> None:
        self.assertEqual(("api_key", "tailscale"), self.args().auth_modes)
        self.assertEqual(("oidc", "api_key"), self.args(oidc_issuer="https://idp.example.test").auth_modes)
        self.assertEqual(("api_key",), self.args(auth_modes="api_key").auth_modes)
        with self.assertRaises(SystemExit):
            self.args(auth_modes="oidc")
        with self.assertRaises(SystemExit):
            self.args(auth_modes="api_key,magic")

    def test_session_secret_split_is_required_for_oidc(self) -> None:
        args = self.args(oidc_issuer="https://idp.example.test", oidc_client_id="client")
        with patch.dict(os.environ, {"TAILPLAN_OIDC_CLIENT_SECRET": "secret"}):
            with self.assertRaisesRegex(ValueError, "dedicated session secret"):
                server.build_auth_config(args, None)
            auth = server.build_auth_config(args, "a" * 32)
        self.assertIsNotNone(auth.oidc)

    def test_client_secret_file_wins_and_must_not_be_empty(self) -> None:
        with self.subTest("file"), patch.dict(os.environ, {"TAILPLAN_OIDC_CLIENT_SECRET": "env"}):
            path = Path(self.id().replace(".", "_"))
            try:
                path.write_text("from-file\n")
                self.assertEqual("from-file", server.read_oidc_client_secret(str(path)))
                path.write_text("\n")
                with self.assertRaises(ValueError):
                    server.read_oidc_client_secret(str(path))
            finally:
                path.unlink(missing_ok=True)
        with patch.dict(os.environ, {"TAILPLAN_OIDC_CLIENT_SECRET": ""}):
            with self.assertRaises(ValueError):
                server.read_oidc_client_secret(None)

    def test_purpose_keys_differ(self) -> None:
        keys = {server.purpose_secret(SESSION_SECRET, purpose) for purpose in ("view", "oidc-flow")}
        self.assertEqual(2, len(keys))
        self.assertNotIn(SESSION_SECRET, keys)

    def test_policy_rejects_unknown_fields_and_roles(self) -> None:
        for data in ({"rulez": []}, {"rules": [{"match": {}}]},
                     {"rules": [{"match": {"group": "a"}, "instance_role": "root"}]},
                     {"rules": [{"match": {"group": "a"}, "workspaces": {"Team": "viewer"}}]},
                     {"rules": [{"match": {"group": "a"}, "workspaces": {"team": "owner"}}]}):
            with self.subTest(data=data), self.assertRaises(ValueError):
                server.RBACPolicy(data)


class NextAllowlistTests(unittest.TestCase):
    def test_safe_next_path(self) -> None:
        for value in ("/dashboard", "/dashboard/drafts/abc123", "/cli/auth", "/cli/activate",
                      "/d/abc123", "/d/abc123/page.html", "/d/my-slug/v/2/"):
            self.assertEqual(value, server.safe_next_path(value))
        for value in ("//evil.test", "https://evil.test/", "/\\evil.test", "/d/%2f%2fevil", "/d/../auth",
                      "/d/a/./b", "/api/me", "/", "dashboard", "/dashboard#x", "/d/a\r\nX: y",
                      "/d/%00", "/d/%5cevil", None, 7, "/d/" + "a" * 600, "/d/é"):
            self.assertEqual("/dashboard", server.safe_next_path(value), value)


class SignInFlowTests(AuthCase):
    def test_start_sets_flow_cookie_and_pkce(self) -> None:
        status, headers, _body = self.request("/auth/oidc/start?next=" + quote("//evil.test"))
        self.assertEqual(302, status)
        query = parse_qs(urlparse(headers["Location"]).query)
        self.assertEqual(["S256"], query["code_challenge_method"])
        self.assertEqual([CLIENT_ID], query["client_id"])
        self.assertIn("openid", query["scope"][0].split())
        cookie = headers["Set-Cookie"][0]
        self.assertIn("HttpOnly", cookie)
        self.assertIn("SameSite=Lax", cookie)
        self.assertIn("Path=/auth/oidc/", cookie)
        flow = server.read_signed(cookie_pair(cookie).split("=", 1)[1],
                                  server.purpose_secret(SESSION_SECRET, "oidc-flow"))
        self.assertEqual("/dashboard", flow["next"])
        self.assertLessEqual(flow["expires"] - time.time(), server.OIDC_FLOW_TTL)
        self.assertIsNone(server.read_signed(cookie_pair(cookie).split("=", 1)[1], SESSION_SECRET))

    def test_full_login_creates_revocable_session(self) -> None:
        cookie = self.oidc_login()
        token = self.idp.token_requests[-1]
        self.assertTrue(token["authorization"].startswith("Basic "))
        self.assertIn("code_verifier", token["form"])
        status, _headers, body = self.request("/dashboard", cookies=[cookie])
        self.assertEqual(200, status)
        self.assertIn(b"Test User", body)
        principal = self.store.principal(self.idp.issuer, "user-1")
        self.assertEqual({"team": "editor"}, principal["workspaces"])
        self.assertEqual(["auth.login"], self.event_names("auth.login"))
        self.assertNotIn("user@example.test", self.audit_lines.getvalue())
        self.assertEqual(16, len(self.events("auth.login")[0]["actor_hash"]))

    def test_state_mismatch_is_denied(self) -> None:
        status, headers, _body = self.request("/auth/oidc/start")
        flow_cookie = cookie_pair(headers["Set-Cookie"][0])
        status, _headers, _body = self.request("/auth/oidc/callback?code=x&state=wrong", cookies=[flow_cookie])
        self.assertEqual(403, status)
        status, _headers, _body = self.request("/auth/oidc/callback?code=x&state=wrong")
        self.assertEqual(403, status)
        self.assertEqual("state", self.events("auth.denied")[-1]["reason"])

    def test_wrong_pkce_verifier_is_denied(self) -> None:
        status, headers, _body = self.request("/auth/oidc/start")
        flow_cookie = cookie_pair(headers["Set-Cookie"][0])
        query = parse_qs(urlparse(headers["Location"]).query)
        self.idp.codes["c1"] = {**{key: values[0] for key, values in query.items()}, "code_challenge": "bad"}
        status, _headers, _body = self.request(
            f"/auth/oidc/callback?code=c1&state={query['state'][0]}", cookies=[flow_cookie])
        self.assertEqual(403, status)

    def test_provider_error_is_denied(self) -> None:
        status, headers, _body = self.request("/auth/oidc/start")
        flow_cookie = cookie_pair(headers["Set-Cookie"][0])
        state = parse_qs(urlparse(headers["Location"]).query)["state"][0]
        status, _headers, _body = self.request(f"/auth/oidc/callback?error=access_denied&state={state}",
                                               cookies=[flow_cookie])
        self.assertEqual(403, status)

    def test_deny_by_default_when_no_rule_matches(self) -> None:
        status, body = self.oidc_attempt(groups=["strangers"])
        self.assertEqual(403, status, body)
        denied = self.events("auth.denied")[-1]
        self.assertEqual(("error", "denied"), (denied["outcome"], denied["error_type"]))
        self.assertEqual("no_matching_rule", denied["reason"])
        self.assertIsNone(self.store.principal(self.idp.issuer, "user-1"))

    def test_rule_with_no_roles_signs_in_without_access(self) -> None:
        cookie = self.oidc_login(groups=["outsiders"])
        status, me = self.api("/api/me", cookies=[cookie])
        self.assertEqual(401, status)  # A browser session is not an API credential.
        status, _headers, _body = self.request("/dashboard", cookies=[cookie])
        self.assertEqual(200, status)

    def test_userinfo_fallback_supplies_groups(self) -> None:
        self.idp.claims.pop("groups")
        self.idp.userinfo = {"groups": ["ops"]}
        self.oidc_login()
        principal = self.store.principal(self.idp.issuer, "user-1")
        self.assertEqual({"ops": "admin"}, principal["workspaces"])
        self.assertIn("/userinfo", self.idp.requests)

    def test_dotted_groups_claim(self) -> None:
        self.auth.oidc.groups_claim = "realm_access.roles"
        self.idp.claims.pop("groups")
        self.oidc_login(realm_access={"roles": ["readers"]})
        self.assertEqual({"team": "viewer"}, self.store.principal(self.idp.issuer, "user-1")["workspaces"])

    def test_email_rule_needs_verified_email(self) -> None:
        status, _body = self.oidc_attempt(email="boss@example.test", email_verified=False, groups=[])
        self.assertEqual(403, status)
        cookie = self.oidc_login(email="boss@example.test", email_verified=True, groups=[])
        principal = self.store.principal(self.idp.issuer, "user-1")
        self.assertEqual("admin", principal["instanceRole"])
        self.assertTrue(cookie)

    def test_email_verified_must_be_boolean_true(self) -> None:
        status, _body = self.oidc_attempt(email="boss@example.test", email_verified="true", groups=[])
        self.assertEqual(403, status)


class SubjectKeyingTests(AuthCase):
    link_emails = frozenset({"owner@example.test"})

    def test_accounts_are_keyed_on_issuer_and_subject(self) -> None:
        self.oidc_login(sub="alice", email="shared@example.test")
        self.oidc_login(sub="alice", email="renamed@example.test")
        self.oidc_login(sub="mallory", email="shared@example.test")
        alice = self.store.principal(self.idp.issuer, "alice")
        mallory = self.store.principal(self.idp.issuer, "mallory")
        self.assertEqual(server.oidc_account_id(self.idp.issuer, "alice"), alice["accountId"])
        self.assertEqual("renamed@example.test", alice["email"])
        self.assertNotEqual(alice["accountId"], mallory["accountId"])

    def test_unverified_link_email_does_not_link_to_local(self) -> None:
        cookie = self.oidc_login(sub="intruder", email="owner@example.test", email_verified=False)
        principal = self.store.principal(self.idp.issuer, "intruder")
        self.assertNotEqual("local", principal["accountId"])
        self.assertIsNone(principal["instanceRole"])
        status, _headers, body = self.request("/dashboard", cookies=[cookie])
        self.assertNotIn(b"Tailplan local", body)

    def test_verified_link_email_links_to_local_admin(self) -> None:
        local_draft = self.upload()
        cookie = self.oidc_login(sub="owner-sub", email="Owner@Example.test", email_verified=True,
                                 groups=["strangers"])
        principal = self.store.principal(self.idp.issuer, "owner-sub")
        self.assertEqual("local", principal["accountId"])
        status, _headers, body = self.request("/dashboard", cookies=[cookie])
        self.assertIn(local_draft["draftId"].encode(), body)


class TokenValidationTests(AuthCase):
    def setUp(self) -> None:
        super().setUp()
        self.client = self.make_client()

    def claims(self, **overrides) -> dict:
        now = int(time.time())
        claims = {"iss": self.idp.issuer, "aud": CLIENT_ID, "iat": now, "exp": now + 300, "sub": "s",
                  "nonce": "n", **overrides}
        return {key: value for key, value in claims.items() if value is not None}

    def test_valid_token(self) -> None:
        self.assertEqual("s", self.client.verify_id_token(self.idp.sign(self.claims()), nonce="n")["sub"])

    def test_only_asymmetric_algorithms(self) -> None:
        symmetric = jwt.encode(self.claims(), CLIENT_SECRET * 2, algorithm="HS256", headers={"kid": self.idp.kid})
        unsigned = jwt.encode(self.claims(), None, algorithm="none")
        for token in (symmetric, unsigned):
            with self.subTest(token=token[:20]), self.assertRaisesRegex(server.OIDCError, "algorithm"):
                self.client.verify_id_token(token, nonce="n")
        self.assertEqual(("RS256", "PS256", "ES256", "ES384", "EdDSA"), tuple(server.OIDC_ALGORITHMS))

    def test_rejects_bad_claims(self) -> None:
        now = int(time.time())
        cases = {
            "issuer": self.claims(iss=self.idp.issuer + "/"),
            "audience": self.claims(aud="someone-else"),
            "azp": self.claims(aud=[CLIENT_ID, "other"], azp="other"),
            "azp-missing": self.claims(aud=[CLIENT_ID, "other"]),
            "expired": self.claims(exp=now - 60),
            "not-before": self.claims(nbf=now + 60),
            "no-exp": self.claims(exp=None),
            "no-sub": self.claims(sub=None),
        }
        for name, claims in cases.items():
            with self.subTest(name), self.assertRaises(server.OIDCError):
                self.client.verify_id_token(self.idp.sign(claims), nonce="n")
        with self.assertRaisesRegex(server.OIDCError, "nonce"):
            self.client.verify_id_token(self.idp.sign(self.claims()), nonce="other")

    def test_clock_skew_of_thirty_seconds(self) -> None:
        now = int(time.time())
        self.client.verify_id_token(self.idp.sign(self.claims(exp=now - 20)), nonce="n")
        self.client.verify_id_token(self.idp.sign(self.claims(nbf=now + 20)), nonce="n")

    def test_unknown_key_refreshes_after_floor_only(self) -> None:
        self.client.verify_id_token(self.idp.sign(self.claims()), nonce="n")
        fetches = self.idp.requests.count("/jwks")
        token = self.idp.sign(self.claims(), headers={"kid": "rotated"})
        with self.assertRaises(server.OIDCError):
            self.client.verify_id_token(token, nonce="n")
        self.assertEqual(fetches, self.idp.requests.count("/jwks"))
        self.client._jwks_at -= server.OIDC_JWKS_REFRESH_FLOOR + 1
        self.idp.kid = "rotated"
        self.client.verify_id_token(token, nonce="n")
        self.assertEqual(fetches + 1, self.idp.requests.count("/jwks"))

    def test_logout_token_rules(self) -> None:
        self.assertEqual("user-1", self.client.verify_logout_token(self.idp.logout_token())["sub"])
        for overrides in ({"events": None}, {"nonce": "n"}, {"sub": None}, {"jti": None}):
            with self.subTest(overrides), self.assertRaises(server.OIDCError):
                self.client.verify_logout_token(self.idp.logout_token(**overrides))


class SSRFGuardTests(AuthCase):
    def test_endpoints_must_share_the_issuer_origin(self) -> None:
        self.idp.mode = "cross-origin"
        with self.assertRaisesRegex(server.OIDCError, "origin"):
            self.make_client().metadata()
        with self.assertRaisesRegex(server.OIDCError, "origin"):
            self.make_client().fetch("http://127.0.0.2:1/x")

    def test_redirects_are_not_followed(self) -> None:
        self.idp.mode = "redirect"
        with self.assertRaisesRegex(server.OIDCError, "redirect"):
            self.make_client().metadata()

    def test_issuer_must_match_exactly(self) -> None:
        self.idp.mode = "wrong-issuer"
        with self.assertRaisesRegex(server.OIDCError, "issuer"):
            self.make_client().metadata()

    def test_response_size_cap(self) -> None:
        self.idp.mode = "large"
        with self.assertRaisesRegex(server.OIDCError, "too large"):
            self.make_client().metadata()

    def test_response_time_cap(self) -> None:
        with self.assertRaisesRegex(server.OIDCError, "time"):
            self.make_client(timeout=0.5).fetch(self.idp.issuer + "/slow")

    def test_private_addresses_are_denied_unless_allowed(self) -> None:
        for host in ("127.0.0.1", "10.1.2.3", "192.168.1.1", "169.254.169.254", "100.100.100.100", "::1",
                     "::ffff:10.0.0.1", "fd00::1"):
            with self.subTest(host), self.assertRaisesRegex(server.OIDCError, "private"):
                server.resolve_public_addresses(host, 443, False)
        self.assertEqual(["127.0.0.1"], server.resolve_public_addresses("127.0.0.1", 443, True))
        client = server.OIDCClient(issuer=self.idp.issuer.replace("http://", "https://"), client_id="c",
                                   client_secret="s")
        with self.assertRaisesRegex(server.OIDCError, "private"):
            client.metadata()
        with self.assertRaisesRegex(ValueError, "HTTPS"):
            server.OIDCClient(issuer=self.idp.issuer, client_id="c", client_secret="s")

    def test_failed_discovery_answers_502(self) -> None:
        self.idp.mode = "redirect"
        status, _headers, _body = self.request("/auth/oidc/start")
        self.assertEqual(502, status)


class LazyDiscoveryTests(AuthCase):
    def test_startup_and_readyz_never_contact_the_provider(self) -> None:
        """/readyz stays up with no network, as under `docker run --network none`."""
        self.assertEqual([], self.idp.requests)

        class NoNetwork:
            """Stand in for the socket module inside the server only."""

            def __getattr__(self, name: str) -> object:
                return getattr(socket, name)

            def getaddrinfo(self, *_args: object, **_kwargs: object) -> list:
                raise socket.gaierror("network is unreachable")

            def create_connection(self, *_args: object, **_kwargs: object) -> socket.socket:
                raise OSError("network is unreachable")

        with patch.object(server, "socket", NoNetwork()):
            for path in ("/readyz", "/healthz", "/auth/sign-in"):
                status, _headers, _body = self.request(path)
                self.assertEqual(200, status, path)
            status, _headers, _body = self.request("/auth/oidc/start")
            self.assertEqual(502, status)
        self.assertEqual([], self.idp.requests)


class BackchannelLogoutTests(AuthCase):
    def test_logout_token_revokes_sessions_now(self) -> None:
        cookie = self.oidc_login(sid="idp-session-1")
        other = self.oidc_login(sid="idp-session-2")
        status, _headers, _body = self.request("/dashboard", cookies=[cookie])
        self.assertEqual(200, status)
        status, _headers, _body = self.request(
            "/auth/oidc/backchannel-logout", "POST",
            form={"logout_token": self.idp.logout_token(sub=None, sid="idp-session-1")})
        self.assertEqual(200, status)
        status, _headers, body = self.request("/dashboard", cookies=[cookie])
        self.assertIn(b"Sign in", body)
        status, _headers, body = self.request("/dashboard", cookies=[other])
        self.assertNotIn(b"Sign in with", body)
        self.request("/auth/oidc/backchannel-logout", "POST", form={"logout_token": self.idp.logout_token()})
        status, _headers, body = self.request("/dashboard", cookies=[other])
        self.assertIn(b"Sign in", body)
        self.assertEqual("backchannel", self.events("auth.logout")[-1]["auth_method"])

    def test_invalid_logout_token_is_rejected(self) -> None:
        for token in ("", "x.y.z", self.idp.logout_token(events=None)):
            status, _headers, _body = self.request("/auth/oidc/backchannel-logout", "POST",
                                                   form={"logout_token": token})
            self.assertEqual(400, status)

    def test_sign_out_revokes_the_server_session(self) -> None:
        cookie = self.oidc_login()
        csrf = self.csrf(cookie)
        status, headers, _body = self.request("/auth/sign-out", "POST", cookies=[cookie], form={"csrf": csrf})
        self.assertEqual(303, status)
        self.assertTrue(any(value.startswith("tailplan_view=;") for value in headers["Set-Cookie"]))
        status, _headers, body = self.request("/dashboard", cookies=[cookie])
        self.assertIn(b"Sign in", body)


class LegacyCompatibilityTests(AuthCase):
    def test_bootstrap_token_is_admin(self) -> None:
        status, me = self.api("/api/me", token=TOKEN)
        self.assertEqual("admin", me["instanceRole"])
        status, events = self.api("/api/audit", token=TOKEN)
        self.assertEqual(200, status)

    def test_api_key_inherits_account_roles(self) -> None:
        cookie = self.oidc_login(groups=["readers"])
        key = self.create_key(cookie)
        status, me = self.api("/api/me", token=key)
        self.assertEqual(200, status)
        self.assertEqual({"team": "viewer"}, me["workspaces"])
        self.assertIsNone(me["instanceRole"])
        status, _body = self.api("/api/audit", token=key)
        self.assertEqual(403, status)

    def test_api_key_sign_in_can_be_turned_off(self) -> None:
        self.auth.modes = ("oidc",)
        status, headers, body = self.request("/auth/sign-in")
        self.assertNotIn(b'name="token"', body)
        self.assertIn(b"single sign-on", body)
        login = cookie_pair(headers["Set-Cookie"][0])
        csrf = server.read_signed(login.split("=", 1)[1], SESSION_SECRET)["csrf"]
        status, _headers, _body = self.request("/auth/sign-in", "POST", cookies=[login],
                                               form={"csrf": csrf, "token": TOKEN})
        self.assertEqual(403, status)
        status, _me = self.api("/api/me", token=TOKEN)
        self.assertEqual(200, status)  # Bearer keys keep working for agents.

    def test_turning_off_oidc_ends_oidc_sessions(self) -> None:
        cookie = self.oidc_login()
        self.auth.modes = ("api_key",)
        status, _headers, body = self.request("/dashboard", cookies=[cookie])
        self.assertIn(b"Sign in", body)


class SessionViewerGateTests(AuthCase):
    viewer_mode = "session"

    def setUp(self) -> None:
        super().setUp()
        pages = {"index.html": '<title>Book</title><a href="two.html">Two</a> <a href="https://x.test/">X</a>',
                 "two.html": "<title>Two</title><p>Second</p>"}
        self.draft = self.upload(files=[{"path": path, "html": doc} for path, doc in pages.items()],
                                 workspace="team", visibility="workspace")
        self.draft_path = f"/d/{self.draft['draftId']}/"

    def test_signed_out_browser_goes_to_sign_in(self) -> None:
        for path in (self.draft_path, f"/api/drafts/{self.draft['draftId']}/source"):
            status, headers, _body = self.request(path + "?tv=junk&x=1", headers={"Accept": "text/html"})
            self.assertEqual(302, status, path)
            self.assertEqual(self.base + "/auth/sign-in?next=" + quote(path + "?x=1", safe="/"),
                             headers["Location"])

    def test_signed_out_api_gets_401(self) -> None:
        for path in (self.draft_path, f"/api/drafts/{self.draft['draftId']}/source"):
            status, _headers, _body = self.request(path, headers={"Accept": "application/json"})
            self.assertEqual(401, status, path)

    def test_member_reads_and_gets_view_credentials(self) -> None:
        cookie = self.oidc_login(groups=["readers"])
        status, headers, body = self.request(self.draft_path, cookies=[cookie])
        self.assertEqual(200, status)
        view_cookie = [value for value in headers["Set-Cookie"] if value.startswith("tailplan_view=")][0]
        for attribute in ("Path=/d/", "HttpOnly", "Secure", "SameSite=None"):
            self.assertIn(attribute, view_cookie)
        self.assertRegex(body.decode(), r'href="two\.html\?tv=[^"]+"')
        self.assertIn('href="https://x.test/"', body.decode())
        self.assertEqual(self.draft["draftId"], self.events("draft.view")[-1]["target_id"])
        status, _headers, source = self.request(f"/api/drafts/{self.draft['draftId']}/source", cookies=[cookie])
        self.assertEqual(200, status)
        self.assertEqual(["draft.source"], self.event_names("draft.source"))

    def test_view_token_works_without_cookies_and_only_for_reads(self) -> None:
        cookie = self.oidc_login(groups=["readers"])
        _status, _headers, body = self.request(self.draft_path, cookies=[cookie])
        href = re.search(r'href="two\.html\?tv=([^"]+)"', body.decode())
        token = href[1].replace("&amp;", "&")
        status, _headers, body = self.request(self.draft_path + "two.html?tv=" + token)
        self.assertEqual(200, status)
        self.assertIn(b"Second", body)
        other = self.upload("<title>Other</title>", workspace="team", visibility="workspace")
        status, _headers, _body = self.request(f"/d/{other['draftId']}/?tv={token}",
                                               headers={"Accept": "text/html"})
        self.assertEqual(302, status)
        status, _headers, _body = self.request(self.draft_path + "?tv=" + token, "POST")
        self.assertNotEqual(200, status)
        status, _headers, _body = self.request("/dashboard", cookies=[f"tailplan_session={token}"])
        self.assertNotIn(b"Book", _body)

    def test_view_token_dies_with_the_session(self) -> None:
        cookie = self.oidc_login(groups=["readers"])
        _status, headers, body = self.request(self.draft_path, cookies=[cookie])
        token = re.search(r'\?tv=([^"]+)"', body.decode())[1]
        view_cookie = cookie_pair([value for value in headers["Set-Cookie"] if value.startswith("tailplan_view=")][0])
        self.request("/auth/oidc/backchannel-logout", "POST", form={"logout_token": self.idp.logout_token()})
        for options in ({"path": self.draft_path + "two.html?tv=" + token},
                        {"path": self.draft_path + "two.html", "cookies": [view_cookie]}):
            status, _headers, _body = self.request(options["path"], cookies=options.get("cookies"),
                                                   headers={"Accept": "text/html"})
            self.assertEqual(302, status)

    def test_signed_in_without_access_gets_404(self) -> None:
        cookie = self.oidc_login(groups=["ops"])
        for path in (self.draft_path, f"/api/drafts/{self.draft['draftId']}/source"):
            status, _headers, _body = self.request(path, cookies=[cookie])
            self.assertEqual(404, status, path)

    def test_denied_reads_count_toward_the_viewer_404_limit(self) -> None:
        self.primary.viewer_404_limit = 3
        cookie = self.oidc_login(groups=["ops"])
        statuses = [self.request(f"/api/drafts/{self.draft['draftId']}/source", cookies=[cookie])[0]
                    for _ in range(5)]
        self.assertEqual([404, 404, 404, 429, 429], statuses)

    def test_link_drafts_still_need_sign_in_in_session_mode(self) -> None:
        link = self.upload("<title>Link</title>", visibility="link")
        status, _headers, _body = self.request(f"/d/{link['draftId']}", headers={"Accept": "text/html"})
        self.assertEqual(302, status)

    def test_bearer_key_reads_source(self) -> None:
        cookie = self.oidc_login(groups=["readers"])
        key = self.create_key(cookie)
        status, _source = self.api(f"/api/drafts/{self.draft['draftId']}/source", token=key)
        self.assertEqual(200, status)
        status, _source = self.api(f"/api/drafts/{self.draft['draftId']}/source", token="tp_wrong")
        self.assertEqual(401, status)


class NetworkViewerGateTests(AuthCase):
    def test_link_drafts_stay_public_and_private_drafts_hide(self) -> None:
        public = self.upload()
        self.assertEqual("link", public["visibility"])
        status, _headers, _body = self.request(f"/d/{public['draftId']}")
        self.assertEqual(200, status)
        status, _headers, _body = self.request(f"/api/drafts/{public['draftId']}/source")
        self.assertEqual(200, status)
        private = self.upload(visibility="private")
        status, _headers, _body = self.request(f"/d/{private['draftId']}", headers={"Accept": "text/html"})
        self.assertEqual(302, status)
        status, _headers, _body = self.request(f"/api/drafts/{private['draftId']}/source")
        self.assertEqual(401, status)
        status, _source = self.api(f"/api/drafts/{private['draftId']}/source", token=TOKEN)
        self.assertEqual(200, status)

    def test_authenticated_visibility_admits_any_signed_in_user(self) -> None:
        draft = self.upload(visibility="authenticated")
        cookie = self.oidc_login(groups=["outsiders"])
        status, _headers, _body = self.request(f"/d/{draft['draftId']}", cookies=[cookie])
        self.assertEqual(200, status)


class DeviceLoginTests(AuthCase):
    def start(self) -> dict:
        status, started = self.api("/api/auth/cli/start", "POST", {"clientName": "laptop"})
        self.assertEqual(200, status)
        return started

    def test_device_flow_mints_a_ninety_day_key_once(self) -> None:
        started = self.start()
        self.assertEqual(self.base + "/cli/activate", started["verificationUri"])
        self.assertRegex(started["userCode"], r"^[A-Z0-9]{4}-[A-Z0-9]{4}$")
        self.assertEqual({"deviceCode", "userCode", "verificationUri", "verificationUriComplete", "expiresIn",
                          "interval", "ok"}, set(started))
        status, pending = self.api("/api/auth/cli/poll", "POST", {"deviceCode": started["deviceCode"]})
        self.assertEqual((200, "pending"), (status, pending["status"]))
        cookie = self.oidc_login()
        status, _headers, body = self.request("/cli/activate?code=" + started["userCode"].lower(), cookies=[cookie])
        self.assertIn(b"laptop", body)
        csrf = re.search(rb'name="csrf" value="([^"]+)"', body)[1].decode()
        status, _headers, body = self.request("/cli/activate", "POST", cookies=[cookie], form={
            "csrf": csrf, "code": started["userCode"], "decision": "approve"})
        self.assertEqual(200, status)
        self.assertIn(b"CLI approved", body)
        status, approved = self.api("/api/auth/cli/poll", "POST", {"deviceCode": started["deviceCode"]})
        self.assertEqual(200, status)
        self.assertTrue(approved["ok"])
        self.assertTrue(approved["apiKey"].startswith("tp_"))
        expires = server.datetime.fromisoformat(approved["expiresAt"].replace("Z", "+00:00"))
        days = (expires - server.datetime.now(server.UTC)).days
        self.assertIn(days, {89, 90})
        status, me = self.api("/api/me", token=approved["apiKey"])
        self.assertEqual(server.oidc_account_id(self.idp.issuer, "user-1"), me["accountId"])
        status, again = self.api("/api/auth/cli/poll", "POST", {"deviceCode": started["deviceCode"]})
        self.assertEqual(410, status)
        self.assertIn("device.approve", self.event_names())
        self.assertIn("device", [event.get("auth_method") for event in self.events("key.create")])

    def test_denied_and_expired_codes(self) -> None:
        started = self.start()
        cookie = self.oidc_login()
        csrf = self.csrf(cookie, "/cli/activate")
        self.request("/cli/activate", "POST", cookies=[cookie],
                     form={"csrf": csrf, "code": started["userCode"], "decision": "deny"})
        status, denied = self.api("/api/auth/cli/poll", "POST", {"deviceCode": started["deviceCode"]})
        self.assertEqual((403, "denied"), (status, denied["status"]))
        status, _unknown = self.api("/api/auth/cli/poll", "POST", {"deviceCode": "nope"})
        self.assertEqual(410, status)
        expired = self.store.start_device_login("old", ttl=-1)
        status, _expired = self.api("/api/auth/cli/poll", "POST", {"deviceCode": expired["deviceCode"]})
        self.assertEqual(410, status)

    def test_activate_needs_session_and_csrf(self) -> None:
        started = self.start()
        status, _headers, body = self.request("/cli/activate?code=" + started["userCode"])
        self.assertIn(b"Sign in", body)
        self.assertIn(b"next", body)
        cookie = self.oidc_login()
        status, _headers, _body = self.request("/cli/activate", "POST", cookies=[cookie], form={
            "csrf": "wrong", "code": started["userCode"], "decision": "approve"})
        self.assertEqual(403, status)


class AuditTests(AuthCase):
    def test_jsonl_has_exact_fields_and_api_is_admin_only(self) -> None:
        draft = self.upload()
        cookie = self.oidc_login(groups=["auditors"])
        for event in self.events():
            self.assertEqual(server.AUDIT_BASE_KEYS, server.AUDIT_BASE_KEYS & set(event))
            self.assertLessEqual(set(event) - server.AUDIT_BASE_KEYS, server.AUDIT_DETAIL_KEYS)
            self.assertEqual(event, server.check_audit_row(event))
            self.assertEqual(sorted(event), list(event))
            self.assertNotIn("ts", event)
            self.assertNotIn("service", event)
        upload = self.events("draft.upload")[0]
        self.assertEqual(("draft", draft["draftId"], "local"),
                         (upload["target_type"], upload["target_id"], upload["actor_kind"]))
        key = self.create_key(cookie)
        status, listed = self.api("/api/audit?limit=2", token=key)
        self.assertEqual(200, status)
        self.assertEqual(2, len(listed["events"]))
        status, filtered = self.api("/api/audit?event=draft.upload", token=TOKEN)
        self.assertEqual(["draft.upload"], [event["event"] for event in filtered["events"]])
        reader = self.create_key(self.oidc_login(sub="reader", groups=["readers"]))
        status, _body = self.api("/api/audit", token=reader)
        self.assertEqual(403, status)

    def test_key_and_draft_changes_are_recorded(self) -> None:
        draft = self.upload()
        status, created = self.api("/api/api-keys", "POST", {"name": "ci"}, token=TOKEN)
        self.api(f"/api/api-keys/{created['apiKey']['id']}/revoke", "POST", {}, token=TOKEN)
        self.api(f"/api/drafts/{draft['draftId']}/disable", "POST", {"reason": "x"}, token=TOKEN)
        names = self.event_names()
        for name in ("key.create", "key.revoke", "draft.change"):
            self.assertIn(name, names)

    def test_events_survive_in_the_database(self) -> None:
        self.upload()
        stored = self.store.list_audit(limit=10)
        self.assertEqual("draft.upload", stored[0]["event"])


class InteropGateDocumentation(unittest.TestCase):
    def test_entrypoint_and_compose_pass_every_new_setting(self) -> None:
        compose = (ROOT / "compose.yaml").read_text()
        entrypoint = (ROOT / "docker-entrypoint.py").read_text()
        source = (ROOT / "tailplan_server.py").read_text()
        for name in ("TAILPLAN_AUTH_MODES", "TAILPLAN_OIDC_ISSUER", "TAILPLAN_OIDC_CLIENT_ID",
                     "TAILPLAN_OIDC_CLIENT_SECRET_FILE", "TAILPLAN_OIDC_SCOPES", "TAILPLAN_OIDC_GROUPS_CLAIM",
                     "TAILPLAN_OIDC_ALLOW_PRIVATE_ISSUER", "TAILPLAN_OIDC_LINK_EMAILS",
                     "TAILPLAN_SESSION_TTL_SECONDS", "TAILPLAN_RBAC_POLICY_FILE", "TAILPLAN_VIEWER_AUTH_MODE",
                     "TAILPLAN_MAX_VISIBILITY", "TAILPLAN_TAILSCALE_VIEWER"):
            self.assertIn(name + ": ${" + name + ":-}", compose)
            self.assertIn(f'"{name}"', entrypoint)
            self.assertIn(f'"{name}"', source)
        self.assertIn("oidc-client-secret", entrypoint)
        self.assertIn('".[oidc]"', (ROOT / "Dockerfile").read_text())
        self.assertEqual(['PyJWT[crypto]>=2.10,<3'],
                         json.loads(json.dumps(__import__("tomllib").loads(
                             (ROOT / "pyproject.toml").read_text())["project"]["optional-dependencies"]["oidc"])))


if __name__ == "__main__":
    unittest.main()
