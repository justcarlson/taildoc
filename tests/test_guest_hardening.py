"""Cover the login allowlist, the viewer 404 limit, and container wiring."""
from __future__ import annotations

import importlib.util
import json
import os
import re
import tempfile
import threading
import tomllib
import unittest
import unittest.mock
from http.client import HTTPConnection
from pathlib import Path
from urllib.parse import urlencode

import yaml

import tailplan_server as server

ROOT = Path(__file__).resolve().parents[1]
OWNER = "owner@example.test"
GUEST = "guest@example.test"
STRANGER = "stranger@example.test"


class ListenerCase(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.store = server.Store(Path(self.temporary.name) / "data")
        self.primary, self.proxy = server.create_servers(
            ("127.0.0.1", 0), ("127.0.0.1", 0), store=self.store, token="bootstrap-test",
            base_url="", redirect_view_base_url="",
        )
        self.proxy.trust_tailscale_identity = True
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

    def request(self, path, method="GET", payload=None, token=None, headers=None, proxy=True):
        request_headers = dict(headers or {})
        if token:
            request_headers["Authorization"] = "Bearer " + token
        body = None
        if payload is not None:
            body = payload if isinstance(payload, bytes) else json.dumps(payload).encode()
            request_headers.setdefault("Content-Type", "application/json")
        listener = self.proxy if proxy else self.primary
        connection = HTTPConnection("127.0.0.1", listener.server_port, timeout=5)
        try:
            connection.request(method, path, body=body, headers=request_headers)
            response = connection.getresponse()
            return response.status, dict(response.headers), response.read()
        finally:
            connection.close()

    def accounts(self) -> dict:
        return self.store.load_meta().get("accounts", {})

    def sign_in(self, login: str, *, method="tailscale"):
        headers = {"Tailscale-User-Login": login, "Tailscale-User-Name": "Name"}
        status, response_headers, body = self.request("/dashboard", headers=headers)
        self.assertEqual(200, status)
        csrf = re.search(rb'name="csrf" value="([^"]+)"', body)[1].decode()
        form = {"csrf": csrf, "next": "/dashboard", "method": method}
        return self.request(
            "/auth/sign-in", "POST", urlencode(form).encode(),
            headers={**headers, "Cookie": response_headers["Set-Cookie"].split(";", 1)[0],
                     "Content-Type": "application/x-www-form-urlencoded"},
        ), body


class ParseAllowedLoginsTests(unittest.TestCase):
    def test_normalizes_case_space_and_empty_items(self) -> None:
        self.assertEqual(frozenset(), server.parse_allowed_logins(None))
        self.assertEqual(frozenset(), server.parse_allowed_logins(""))
        self.assertEqual(frozenset(), server.parse_allowed_logins(" , ,"))
        self.assertEqual(
            frozenset({"a@example.test", "b@example.test"}),
            server.parse_allowed_logins(" A@Example.test,,b@example.TEST , a@example.test"),
        )
        self.assertIsInstance(server.parse_allowed_logins("a@example.test"), frozenset)

    def test_arguments_read_environment_and_flags(self) -> None:
        with unittest.mock.patch.dict("os.environ", {"TAILPLAN_ALLOWED_LOGINS": "A@x.test, b@x.test",
                                                       "TAILPLAN_VIEWER_404_LIMIT": "7"}):
            args = server.parse_args([])
            self.assertEqual(frozenset({"a@x.test", "b@x.test"}), args.allowed_logins)
            self.assertEqual(7, args.viewer_404_limit)
            args = server.parse_args(["--allowed-logins", "C@x.test", "--viewer-404-limit", "9"])
            self.assertEqual(frozenset({"c@x.test"}), args.allowed_logins)
            self.assertEqual(9, args.viewer_404_limit)

    def test_arguments_default_to_open_and_30(self) -> None:
        with unittest.mock.patch.dict("os.environ"):
            for name in ("TAILPLAN_ALLOWED_LOGINS", "TAILPLAN_VIEWER_404_LIMIT"):
                os.environ.pop(name, None)
            args = server.parse_args([])
        self.assertEqual(frozenset(), args.allowed_logins)
        self.assertEqual(30, args.viewer_404_limit)

    def test_non_positive_viewer_limit_is_rejected(self) -> None:
        for value in ("0", "-1"):
            with self.subTest(value=value), self.assertRaises(SystemExit):
                server.parse_args(["--viewer-404-limit", value])


class AllowedLoginsTests(ListenerCase):
    def test_unset_allowlist_keeps_legacy_behavior(self) -> None:
        (status, headers, _), page = self.sign_in(STRANGER)
        self.assertEqual(303, status)
        self.assertIn(b"Continue with Tailscale", page)
        self.assertEqual(1, len(self.accounts()))

    def test_allowed_login_signs_in_and_gets_an_account(self) -> None:
        self.proxy.allowed_logins = server.parse_allowed_logins(f"{OWNER}, {GUEST.upper()}")
        (status, headers, _), page = self.sign_in(GUEST)
        self.assertEqual(303, status)
        self.assertIn(b"Continue with Tailscale", page)
        self.assertEqual(1, len(self.accounts()))
        cookie = headers["Set-Cookie"].split(";", 1)[0]
        status, _, body = self.request("/dashboard", headers={"Cookie": cookie})
        self.assertEqual(200, status)
        self.assertIn(b"Sign out", body)

    def test_owner_in_the_list_maps_to_the_local_account(self) -> None:
        self.proxy.allowed_logins = server.parse_allowed_logins(OWNER)
        (status, headers, _), _page = self.sign_in(OWNER.upper())
        self.assertEqual(303, status)
        self.assertEqual({}, self.accounts())

    def test_disallowed_login_gets_403_and_no_account(self) -> None:
        self.proxy.allowed_logins = server.parse_allowed_logins(OWNER)
        before = self.store.load_meta()
        (status, headers, body), page = self.sign_in(STRANGER)
        self.assertEqual(403, status)
        self.assertEqual("Forbidden", json.loads(body)["error"])
        self.assertNotIn("Set-Cookie", headers)
        self.assertEqual({}, self.accounts())
        self.assertEqual(before, self.store.load_meta())
        self.assertNotIn(b"Continue with Tailscale", page)
        self.assertIn(b"Sign in with API key", page)

    def test_disallowed_login_does_not_call_identity_account(self) -> None:
        self.proxy.allowed_logins = server.parse_allowed_logins(OWNER)
        with unittest.mock.patch.object(server.Store, "identity_account") as identity:
            (status, _, _), _page = self.sign_in(STRANGER)
        self.assertEqual(403, status)
        identity.assert_not_called()

    def test_session_is_rejected_after_the_login_leaves_the_list(self) -> None:
        self.proxy.allowed_logins = server.parse_allowed_logins(f"{OWNER},{GUEST}")
        (status, headers, _), _page = self.sign_in(GUEST)
        self.assertEqual(303, status)
        cookie = headers["Set-Cookie"].split(";", 1)[0]
        self.assertIn(b"Sign out", self.request("/dashboard", headers={"Cookie": cookie})[2])
        self.proxy.allowed_logins = server.parse_allowed_logins(OWNER)
        _, _, body = self.request("/dashboard", headers={"Cookie": cookie})
        self.assertNotIn(b"Sign out", body)
        self.assertIn(b"Sign in", body)

    def test_cli_auth_requires_an_allowed_login(self) -> None:
        self.proxy.allowed_logins = server.parse_allowed_logins(OWNER)
        headers = {"Tailscale-User-Login": STRANGER}
        status, _, body = self.request("/cli/auth", headers=headers)
        self.assertEqual(200, status)
        self.assertNotIn(b"Continue with Tailscale", body)
        self.assertEqual({}, self.accounts())

    def test_bearer_tokens_ignore_the_allowlist(self) -> None:
        self.proxy.allowed_logins = server.parse_allowed_logins(OWNER)
        headers = {"Tailscale-User-Login": STRANGER}
        status, _, body = self.request("/api/me", token="bootstrap-test", headers=headers)
        self.assertEqual(200, status)
        self.assertEqual("local", json.loads(body)["accountId"])
        key = self.store.create_key("local", "CLI")["token"]
        self.assertEqual(200, self.request("/api/me", token=key, headers=headers)[0])
        status, _, body = self.request("/api/uploads", "POST", {"html": "<title>T</title><p>x</p>"},
                                       token="bootstrap-test", headers=headers)
        self.assertIn(status, {200, 201}, body)

    def test_api_key_sign_in_ignores_the_allowlist(self) -> None:
        self.proxy.allowed_logins = server.parse_allowed_logins(OWNER)
        key = self.store.create_key("local", "Browser")["token"]
        (status, _, _), _page = self.sign_in(STRANGER, method="token")
        self.assertEqual(401, status)
        headers = {"Tailscale-User-Login": STRANGER}
        _, response_headers, body = self.request("/dashboard", headers=headers)
        csrf = re.search(rb'name="csrf" value="([^"]+)"', body)[1].decode()
        status, _, _ = self.request(
            "/auth/sign-in", "POST", urlencode({"csrf": csrf, "token": key, "next": "/dashboard"}).encode(),
            headers={**headers, "Cookie": response_headers["Set-Cookie"].split(";", 1)[0],
                     "Content-Type": "application/x-www-form-urlencoded"},
        )
        self.assertEqual(303, status)

    def test_viewer_and_health_routes_do_not_use_the_allowlist(self) -> None:
        self.proxy.allowed_logins = server.parse_allowed_logins(OWNER)
        self.primary.allowed_logins = self.proxy.allowed_logins
        draft = self.upload()
        headers = {"Tailscale-User-Login": STRANGER}
        for proxy in (True, False):
            for path in (f"/d/{draft['draftId']}", "/healthz", "/readyz", "/api/themes", "/api/typography"):
                with self.subTest(path=path, proxy=proxy):
                    self.assertEqual(200, self.request(path, headers=headers, proxy=proxy)[0])

    def upload(self) -> dict:
        status, _, body = self.request("/api/uploads", "POST", {"html": "<title>Shared</title><p>body</p>"},
                                       token="bootstrap-test")
        self.assertIn(status, {200, 201}, body)
        return json.loads(body)


class ViewerMissLimitTests(ListenerCase):
    def test_misses_return_429_with_retry_after_for_that_caller(self) -> None:
        self.proxy.viewer_404_limit = 3
        for _ in range(3):
            self.assertEqual(404, self.request("/d/missing-draft")[0])
        status, headers, body = self.request("/d/missing-draft")
        self.assertEqual(429, status)
        self.assertGreaterEqual(int(headers["Retry-After"]), 1)
        self.assertFalse(json.loads(body)["ok"])

    def test_every_viewer_route_counts_and_is_blocked(self) -> None:
        self.proxy.viewer_404_limit = 2
        self.assertEqual(404, self.request("/d/missing-draft/v/3")[0])
        self.assertEqual(404, self.request("/d/missing-draft/share")[0])
        for path in ("/d/other-draft", "/d/missing-draft/v/1/preview.png", "/d/%zz"):
            with self.subTest(path=path):
                self.assertEqual(429, self.request(path)[0])
                self.assertEqual(429, self.request(path, "HEAD")[0])

    def test_health_routes_are_not_blocked(self) -> None:
        self.proxy.viewer_404_limit = 1
        self.assertEqual(404, self.request("/d/missing-draft")[0])
        self.assertEqual(429, self.request("/d/missing-draft")[0])
        for path in ("/healthz", "/readyz", "/api/themes", "/api/typography", "/dashboard"):
            with self.subTest(path=path):
                self.assertEqual(200, self.request(path)[0])

    def test_existing_drafts_are_still_served_after_misses_below_the_limit(self) -> None:
        self.proxy.viewer_404_limit = 3
        status, _, body = self.request("/api/uploads", "POST", {"html": "<title>Hi</title><p>x</p>"},
                                       token="bootstrap-test")
        draft_id = json.loads(body)["draftId"]
        for _ in range(2):
            self.assertEqual(404, self.request("/d/missing-draft")[0])
        for _ in range(10):
            self.assertEqual(200, self.request(f"/d/{draft_id}")[0])

    def test_successful_views_never_count(self) -> None:
        self.proxy.viewer_404_limit = 1
        _, _, body = self.request("/api/uploads", "POST", {"html": "<title>Hi</title><p>x</p>"},
                                  token="bootstrap-test")
        draft_id = json.loads(body)["draftId"]
        for _ in range(5):
            self.assertEqual(200, self.request(f"/d/{draft_id}")[0])
        self.assertEqual(404, self.request("/d/missing-draft")[0])

    def test_logins_do_not_share_a_limit(self) -> None:
        self.proxy.viewer_404_limit = 2
        guest = {"Tailscale-User-Login": GUEST}
        other = {"Tailscale-User-Login": OWNER}
        for _ in range(2):
            self.assertEqual(404, self.request("/d/missing-draft", headers=guest)[0])
        self.assertEqual(429, self.request("/d/missing-draft", headers=guest)[0])
        self.assertEqual(429, self.request("/d/missing-draft", headers={"Tailscale-User-Login": GUEST.upper()})[0])
        self.assertEqual(404, self.request("/d/missing-draft", headers=other)[0])
        self.assertEqual(404, self.request("/d/missing-draft")[0])

    def test_forwarded_clients_do_not_share_a_limit(self) -> None:
        self.proxy.viewer_404_limit = 1
        first = {"X-Forwarded-For": "203.0.113.5"}
        second = {"X-Forwarded-For": "203.0.113.6"}
        self.assertEqual(404, self.request("/d/missing-draft", headers=first)[0])
        self.assertEqual(429, self.request("/d/missing-draft", headers=first)[0])
        self.assertEqual(404, self.request("/d/missing-draft", headers=second)[0])

    def test_untrusted_identity_headers_do_not_pick_the_key(self) -> None:
        self.proxy.trust_tailscale_identity = False
        self.proxy.viewer_404_limit = 1
        self.assertEqual(404, self.request("/d/missing-draft", headers={"Tailscale-User-Login": "a@x.test"})[0])
        self.assertEqual(429, self.request("/d/missing-draft", headers={"Tailscale-User-Login": "b@x.test"})[0])

    def test_listeners_share_the_counter_for_one_address(self) -> None:
        self.primary.viewer_404_limit = self.proxy.viewer_404_limit = 1
        self.assertEqual(404, self.request("/d/missing-draft", proxy=False)[0])
        self.assertEqual(429, self.request("/d/missing-draft", proxy=True)[0])

    def test_window_slides(self) -> None:
        limiter = server.MissLimiter()
        with unittest.mock.patch.object(server.time, "monotonic", side_effect=[0, 0, 10, 61, 62]):
            limiter.record("k", 2)
            limiter.record("k", 2)
            self.assertGreaterEqual(limiter.wait("k", 2), 1)
            self.assertEqual(0, limiter.wait("k", 2))
            limiter.record("k", 2)
        self.assertEqual(1, len(limiter.entries["k"]))

    def test_tracked_keys_are_bounded(self) -> None:
        limiter = server.MissLimiter()
        for index in range(9000):
            limiter.record(str(index), 1)
        self.assertEqual(8192, len(limiter.entries))


class ContainerTests(unittest.TestCase):
    @staticmethod
    def entrypoint():
        spec = importlib.util.spec_from_file_location("tailplan_entrypoint", ROOT / "docker-entrypoint.py")
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        return module

    def test_entrypoint_drops_only_empty_optional_settings(self) -> None:
        module = self.entrypoint()
        environment = {
            "TAILPLAN_PROXY_HOST": "127.0.0.1", "TAILPLAN_PROXY_PORT": "9128",
            "TAILPLAN_TRUST_TAILSCALE_IDENTITY": "1", "TAILPLAN_OWNER_LOGIN": OWNER,
            "TAILPLAN_ALLOWED_LOGINS": "", "TAILPLAN_VIEWER_404_LIMIT": " ",
            "TAILPLAN_REDIRECT_VIEW_BASE_URL": "", "TAILPLAN_BASE_URL": "",
            "TAILPLAN_HOST": "127.0.0.1",
        }
        module.drop_empty_optional_environment(environment)
        self.assertEqual({
            "TAILPLAN_PROXY_HOST": "127.0.0.1", "TAILPLAN_PROXY_PORT": "9128",
            "TAILPLAN_TRUST_TAILSCALE_IDENTITY": "1", "TAILPLAN_OWNER_LOGIN": OWNER,
            "TAILPLAN_BASE_URL": "", "TAILPLAN_HOST": "127.0.0.1",
        }, environment)

    def test_entrypoint_settings_all_have_server_options(self) -> None:
        module = self.entrypoint()
        source = (ROOT / "tailplan_server.py").read_text()
        for name in module.OPTIONAL_ENVIRONMENT:
            self.assertIn(f'"{name}"', source)

    def test_dual_listener_environment_parses_to_the_container_layout(self) -> None:
        environment = {
            "TAILPLAN_HOST": "127.0.0.1", "TAILPLAN_PORT": "9127",
            "TAILPLAN_PROXY_HOST": "127.0.0.1", "TAILPLAN_PROXY_PORT": "9128",
            "TAILPLAN_TRUST_TAILSCALE_IDENTITY": "1", "TAILPLAN_OWNER_LOGIN": OWNER,
            "TAILPLAN_ALLOWED_LOGINS": f"{OWNER},{GUEST}", "TAILPLAN_VIEWER_404_LIMIT": "12",
            "TAILPLAN_BASE_URL": "https://tailplan.example.test",
            "TAILPLAN_REDIRECT_VIEW_BASE_URL": "https://view.example.test",
        }
        with unittest.mock.patch.dict("os.environ", environment):
            args = server.parse_args([])
        self.assertEqual(("127.0.0.1", 9127, "127.0.0.1", 9128),
                         (args.host, args.port, args.proxy_host, args.proxy_port))
        self.assertTrue(args.trust_tailscale_identity)
        self.assertEqual(OWNER, args.owner_login)
        self.assertEqual(frozenset({OWNER, GUEST}), args.allowed_logins)
        self.assertEqual(12, args.viewer_404_limit)
        self.assertEqual("https://tailplan.example.test", args.base_url)
        self.assertEqual("https://view.example.test", args.redirect_view_base_url)

    def test_dockerfile_health_check_follows_the_primary_port(self) -> None:
        text = (ROOT / "Dockerfile").read_text()
        self.assertIn("TAILPLAN_PORT=9127", text)
        self.assertIn("http://127.0.0.1:%s/readyz' % os.environ.get('TAILPLAN_PORT', '9127')", text)
        self.assertNotIn("TAILPLAN_PROXY_HOST=", text)  # Single-listener by default.

    def test_compose_keeps_the_single_container_default_and_passes_options(self) -> None:
        compose = yaml.safe_load((ROOT / "compose.yaml").read_text())
        service = compose["services"]["tailplan"]
        version = tomllib.loads((ROOT / "pyproject.toml").read_text())["project"]["version"]
        self.assertEqual("0.5.0", version)
        self.assertEqual(f"tailplan:{version}", service["image"])
        environment = service["environment"]
        self.assertEqual("0.0.0.0", environment["TAILPLAN_HOST"])
        for name in ("TAILPLAN_PROXY_HOST", "TAILPLAN_PROXY_PORT", "TAILPLAN_TRUST_TAILSCALE_IDENTITY",
                     "TAILPLAN_OWNER_LOGIN", "TAILPLAN_ALLOWED_LOGINS", "TAILPLAN_VIEWER_404_LIMIT",
                     "TAILPLAN_REDIRECT_VIEW_BASE_URL"):
            self.assertRegex(str(environment[name]), r"^\$\{" + name + r":-\}$")


class ContainerWorkflowTests(unittest.TestCase):
    WORKFLOW = ROOT / ".github" / "workflows" / "container.yml"

    def workflow(self) -> dict:
        return yaml.load(self.WORKFLOW.read_text(), Loader=yaml.BaseLoader)

    def test_triggers_and_permissions(self) -> None:
        workflow = self.workflow()
        self.assertEqual({"branches": ["main"], "tags": ["v*"]}, workflow["on"]["push"])
        self.assertIn("workflow_dispatch", workflow["on"])
        self.assertEqual({"contents": "read"}, workflow["permissions"])
        job = workflow["jobs"]["publish"]
        self.assertEqual({"contents": "read", "packages": "write", "id-token": "write",
                          "attestations": "write"}, job["permissions"])
        self.assertLessEqual(int(job["timeout-minutes"]), 20)

    def test_every_action_is_pinned_to_a_full_commit_with_a_version_comment(self) -> None:
        lines = [line for line in self.WORKFLOW.read_text().splitlines() if "uses:" in line]
        self.assertGreaterEqual(len(lines), 6)
        for line in lines:
            self.assertRegex(line, r"uses: [\w./-]+@[0-9a-f]{40} # v\d+\.\d+\.\d+$")

    def test_image_tags_platform_and_attestation(self) -> None:
        text = self.WORKFLOW.read_text()
        self.assertIn("ghcr.io/justcarlson/tailplan", text)
        self.assertIn("type=sha,prefix=sha-,format=short", text)
        self.assertIn("type=raw,value=latest,enable={{is_default_branch}}", text)
        self.assertIn("type=semver,pattern={{version}}", text)
        self.assertIn("type=raw,value=${{ github.ref_name }},enable=${{ startsWith(github.ref, 'refs/tags/v') }}", text)
        self.assertIn("platforms: linux/amd64", text)
        steps = self.workflow()["jobs"]["publish"]["steps"]
        names = [step.get("uses", "").split("@")[0] for step in steps]
        self.assertLess(names.index("docker/build-push-action"), names.index("actions/attest-build-provenance"))
        attest = next(step for step in steps if step.get("uses", "").startswith("actions/attest-build-provenance@"))
        self.assertEqual("${{ steps.push.outputs.digest }}", attest["with"]["subject-digest"])
        self.assertEqual("true", attest["with"]["push-to-registry"])
        checkout = next(step for step in steps if step.get("uses", "").startswith("actions/checkout@"))
        self.assertEqual("false", checkout["with"]["persist-credentials"])


if __name__ == "__main__":
    unittest.main()
