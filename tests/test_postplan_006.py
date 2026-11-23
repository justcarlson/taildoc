"""End-to-end tests for Postplan 0.0.6 upgrades integrated into Tailplan."""
from __future__ import annotations

import importlib.machinery
import importlib.util
import io
import json
import os
import re
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from http.client import HTTPConnection
from pathlib import Path
from unittest.mock import patch
from urllib.parse import urlencode

import tailplan_server as server

ROOT = Path(__file__).resolve().parents[1]


def _load_script(name: str, path: Path):
    loader = importlib.machinery.SourceFileLoader(name, str(path))
    spec = importlib.util.spec_from_loader(loader.name, loader)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    loader.exec_module(module)
    return module


share = _load_script("tailplan_share_006", ROOT / "bin" / "tailplan-share")
remote = _load_script("tailplan_share_remote_006", ROOT / "bin" / "tailplan-share-remote")
guard = _load_script("tailplan_publish_guard_006", ROOT / "bin" / "tailplan-publish-guard")


class Postplan006ServerTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        self.store = server.Store(self.root / "data")
        self.httpd, _ = server.create_servers(
            ("127.0.0.1", 0),
            None,
            store=self.store,
            token="bootstrap-test",
            base_url="",
            redirect_view_base_url="",
        )
        self.base = f"http://127.0.0.1:{self.httpd.server_port}"
        self.httpd.base_url = self.base
        self.thread = threading.Thread(target=self.httpd.serve_forever, daemon=True)
        self.thread.start()

    def tearDown(self) -> None:
        self.httpd.shutdown()
        self.httpd.server_close()
        self.thread.join()
        self.temporary.cleanup()

    def request(
        self,
        path: str,
        method: str = "GET",
        payload: object = None,
        token: str | None = "bootstrap-test",
        headers: dict[str, str] | None = None,
    ) -> tuple[int, dict[str, str], bytes]:
        req_headers = dict(headers or {})
        if token:
            req_headers["Authorization"] = "Bearer " + token
        body = None
        if payload is not None:
            body = payload if isinstance(payload, bytes) else json.dumps(payload).encode("utf-8")
            req_headers.setdefault("Content-Type", "application/json")
        conn = HTTPConnection("127.0.0.1", self.httpd.server_port, timeout=5)
        try:
            conn.request(method, path, body=body, headers=req_headers)
            resp = conn.getresponse()
            return resp.status, dict(resp.getheaders()), resp.read()
        finally:
            conn.close()

    def api(self, path: str, method: str = "GET", payload: object = None, **kwargs):
        status, headers, body = self.request(path, method, payload, **kwargs)
        return status, headers, json.loads(body)

    def login(self, token: str = "bootstrap-test") -> str:
        status, headers, body = self.request("/dashboard", token=None)
        self.assertEqual(200, status)
        csrf = re.search(rb'name="csrf" value="([^"]+)"', body)[1].decode()
        login_cookie = headers["Set-Cookie"].split(";", 1)[0]
        form = urlencode({"csrf": csrf, "next": "/dashboard", "token": token}).encode()
        status, headers, _ = self.request(
            "/auth/sign-in",
            "POST",
            form,
            token=None,
            headers={"Cookie": login_cookie, "Content-Type": "application/x-www-form-urlencoded"},
        )
        self.assertEqual(303, status)
        return headers["Set-Cookie"].split(";", 1)[0]

    def test_sandboxed_js_csp_validator_and_anonymous_restriction(self) -> None:
        doc = (
            '<!doctype html><html><head><meta name="viewport" content="width=device-width, initial-scale=1">'
            '<title>Interactive</title>'
            '<link rel="stylesheet" href="https://cdn.example.test/style.css">'
            '<script type="application/json">{"x":1}</script>'
            '<script type="module">localStorage.setItem("k","v");fetch("/x");</script>'
            '</head><body><button onclick="alert(1)">Go</button>'
            '<img src="local.png"></body></html>'
        )
        status, _, uploaded = self.api("/api/uploads", "POST", {"html": doc})
        self.assertEqual(201, status, uploaded)
        joined_warnings = "\n".join(uploaded["warnings"])
        self.assertIn("browser storage", joined_warnings)
        self.assertIn("network requests", joined_warnings)
        self.assertIn("alert/confirm/prompt", joined_warnings)
        self.assertIn("local.png", joined_warnings)

        status, headers, _ = self.request(f"/d/{uploaded['draftId']}", token=None)
        self.assertEqual(200, status)
        csp = headers["Content-Security-Policy"]
        self.assertIn("script-src 'unsafe-inline'", csp)
        self.assertIn("script-src-attr 'unsafe-inline'", csp)
        self.assertIn("sandbox allow-scripts allow-popups allow-popups-to-escape-sandbox", csp)
        self.assertNotIn("allow-same-origin", csp)

        # Data-only script stays script-free under strict CSP.
        data_only = (
            '<!doctype html><meta name="viewport" content="width=device-width, initial-scale=1">'
            '<title>Data</title><script type="application/json">{"a":1}</script>'
        )
        status, _, data_draft = self.api("/api/uploads", "POST", {"html": data_only})
        self.assertEqual(201, status, data_draft)
        status, headers, _ = self.request(f"/d/{data_draft['draftId']}", token=None)
        self.assertEqual(200, status)
        self.assertIn("script-src 'none'", headers["Content-Security-Policy"])
        self.assertNotIn("sandbox", headers["Content-Security-Policy"])

        # Anonymous scripted drafts never enable script execution.
        self.httpd.allow_anonymous_uploads = True
        status, _, anon = self.api(
            "/api/uploads",
            "POST",
            {"html": "<title>Anon</title><script>1+1</script>"},
            token=None,
        )
        self.assertEqual(201, status, anon)
        status, headers, _ = self.request(f"/d/{anon['draftId']}", token=None)
        self.assertEqual(200, status)
        self.assertIn("script-src 'none'", headers["Content-Security-Policy"])

        # Disallowed link rel, http link href, unsupported script type, and deep nesting fail.
        for bad in (
            '<title>Bad</title><link rel="preload" href="https://example.test/a.css">',
            '<title>Bad</title><link rel="stylesheet" href="http://example.test/a.css">',
            '<title>Bad</title><script type="text/vbscript">x</script>',
            "<title>Deep</title>" + ("<div>" * 513) + "x" + ("</div>" * 513),
        ):
            status, _, res = self.api("/api/uploads", "POST", {"html": bad})
            self.assertEqual(422, status, (bad[:60], res))
            self.assertTrue(res.get("issues"))

    def test_preflight_check_endpoint_and_slug_status(self) -> None:
        status, _, report = self.api("/api/check", "POST", {})
        self.assertEqual(200, status, report)
        self.assertTrue(report["ok"])
        self.assertEqual(server.MAX_HTML_BYTES, report["limits"]["maxBytes"])
        self.assertEqual(server.MAX_PAGES_PER_UPLOAD, report["limits"]["maxPages"])
        self.assertTrue(report["capabilities"]["customUrls"])

        status, _, bad = self.api(
            "/api/check",
            "POST",
            {"html": "<title>X</title><iframe src='https://x.test'></iframe>", "slug": "api"},
        )
        self.assertEqual(200, status)
        self.assertFalse(bad["ok"])
        self.assertEqual("unavailable", bad["slug"]["status"])
        self.assertTrue(any(i["code"] == "blocked-tag" for i in bad["issues"]))

        status, _, ok_slug = self.api("/api/check", "POST", {"slug": "my-plan"})
        self.assertEqual(200, status)
        self.assertTrue(ok_slug["ok"])
        self.assertEqual("available", ok_slug["slug"]["status"])

    def test_multipage_folder_upload_routing_and_redirects(self) -> None:
        files = [
            {
                "path": "index.html",
                "html": '<meta name="viewport" content="width=device-width, initial-scale=1"><title>Home</title><a href="guide">Guide</a>',
            },
            {
                "path": "guide.html",
                "html": '<meta name="viewport" content="width=device-width, initial-scale=1"><title>Guide</title><script>console.log(1)</script>',
            },
            {
                "path": "docs/index.html",
                "html": '<meta name="viewport" content="width=device-width, initial-scale=1"><title>Docs</title>',
            },
        ]
        status, _, created = self.api("/api/uploads", "POST", {"files": files, "slug": "site-demo"})
        self.assertEqual(201, status, created)
        self.assertEqual(3, len(created["pages"]))
        self.assertTrue(created["versionUrl"].endswith(f"/d/{created['draftId']}/v/1/"))

        # Root without slash redirects 302 to trailing slash.
        status, headers, _ = self.request("/d/site-demo", token=None)
        self.assertEqual(302, status)
        self.assertEqual("/d/site-demo/", headers["Location"])

        # /raw/ on multi-page redirects to /raw.
        status, headers, _ = self.request("/d/site-demo/raw/", token=None)
        self.assertEqual(302, status)
        self.assertEqual("/d/site-demo/raw", headers["Location"])

        # Index has no scripts -> script-src 'none'.
        status, headers, body = self.request("/d/site-demo/", token=None)
        self.assertEqual(200, status)
        self.assertIn(b"<title>Home</title>", body)
        self.assertIn("script-src 'none'", headers["Content-Security-Policy"])

        # Clean URL /guide resolves guide.html with its own sandboxed script CSP.
        status, headers, body = self.request("/d/site-demo/guide", token=None)
        self.assertEqual(200, status)
        self.assertIn(b"<title>Guide</title>", body)
        self.assertIn("sandbox allow-scripts", headers["Content-Security-Policy"])

        # Directory /docs redirects to /docs/ then serves docs/index.html.
        status, headers, _ = self.request("/d/site-demo/docs", token=None)
        self.assertEqual(302, status)
        self.assertEqual("/d/site-demo/docs/", headers["Location"])
        status, _, body = self.request("/d/site-demo/docs/", token=None)
        self.assertEqual(200, status)
        self.assertIn(b"<title>Docs</title>", body)

        # Missing index.html fails validation.
        status, _, missing = self.api(
            "/api/uploads", "POST", {"files": [{"path": "other.html", "html": "<title>X</title>"}]}
        )
        self.assertEqual(422, status)
        self.assertTrue(any(i["code"] == "missing-index" for i in missing["issues"]))

    def test_custom_slugs_rename_conflicts_and_delete_releases_slug(self) -> None:
        self.store.identity_account("other@example.test", "Other", "")
        other_id = server.sha256_text("tailscale:other@example.test")[:32]
        other_token = self.store.create_key(other_id, "Other")["token"]

        # Create with slug.
        status, _, first = self.api(
            "/api/uploads", "POST", {"html": "<title>V1</title>", "slug": "q4-plan"}
        )
        self.assertEqual(201, status, first)
        self.assertEqual("q4-plan", first["slug"])
        self.assertTrue(first["publicUrl"].endswith("/d/q4-plan"))
        self.assertTrue(first["versionUrl"].endswith(f"/d/{first['draftId']}/v/1"))

        # Portable update by slug alone updates the same draft.
        status, _, second = self.api(
            "/api/uploads", "POST", {"html": "<title>V2</title>", "slug": "q4-plan"}
        )
        self.assertEqual(200, status, second)
        self.assertEqual(first["draftId"], second["draftId"])
        self.assertEqual(2, second["versionNumber"])

        # Another account gets 409 on the same slug, and /api/check reports taken.
        status, _, conflict = self.api(
            "/api/uploads", "POST", {"html": "<title>Steal</title>", "slug": "q4-plan"}, token=other_token
        )
        self.assertEqual(409, status, conflict)
        status, _, check_other = self.api("/api/check", "POST", {"slug": "q4-plan"}, token=other_token)
        self.assertEqual("taken", check_other["slug"]["status"])
        self.assertFalse(check_other["ok"])

        # Rename via draftId + free slug.
        status, _, renamed = self.api(
            "/api/uploads",
            "POST",
            {"html": "<title>V2</title>", "draftId": first["draftId"], "slug": "q4-final"},
        )
        self.assertEqual(200, status, renamed)
        self.assertEqual("q4-final", renamed["slug"])
        self.assertTrue(renamed["unchanged"])
        self.assertEqual(2, renamed["versionNumber"])

        # Delete frees the slug so another account can claim it.
        status, _, _ = self.api("/api/drafts/q4-final", "DELETE")
        self.assertEqual(200, status)
        status, _, reclaimed = self.api(
            "/api/uploads", "POST", {"html": "<title>New</title>", "slug": "q4-final"}, token=other_token
        )
        self.assertEqual(201, status, reclaimed)

        # Invalid slugs rejected with 422.
        for bad_slug in ("ab", "ab--cd", "abcdefgh1234", "admin", "Bad_Slug"):
            status, _, _ = self.api("/api/uploads", "POST", {"html": "<title>X</title>", "slug": bad_slug})
            self.assertEqual(422, status, bad_slug)

    def test_content_hash_dedup_keeps_version_and_updated_at(self) -> None:
        doc = "<title>Same</title><p>Body</p>"
        status, _, first = self.api("/api/uploads", "POST", {"html": doc})
        self.assertEqual(201, status)
        before_updated = self.api("/api/drafts")[2]["drafts"][0]["updatedAt"]

        status, _, dup = self.api(
            "/api/uploads",
            "POST",
            {"html": doc, "draftId": first["draftId"], "description": "Added note"},
        )
        self.assertEqual(200, status)
        self.assertTrue(dup["unchanged"])
        self.assertEqual(1, dup["versionNumber"])
        draft = self.api("/api/drafts")[2]["drafts"][0]
        self.assertEqual(before_updated, draft["updatedAt"])
        self.assertEqual("Added note", draft["description"])

    def test_favorites_api_sorting_and_dashboard_toggle(self) -> None:
        _, _, first = self.api("/api/uploads", "POST", {"html": "<title>Older</title>", "slug": "older-plan"})
        _, _, second = self.api("/api/uploads", "POST", {"html": "<title>Newer</title>"})
        before = {d["draftId"]: d["updatedAt"] for d in self.api("/api/drafts")[2]["drafts"]}

        status, _, fav = self.api("/api/drafts/older-plan/favorite", "POST", {"favorite": True})
        self.assertEqual(200, status, fav)
        self.assertTrue(fav["favorite"])

        drafts = self.api("/api/drafts")[2]["drafts"]
        self.assertEqual(first["draftId"], drafts[0]["draftId"])
        self.assertTrue(drafts[0]["favorite"])
        self.assertEqual(before[first["draftId"]], drafts[0]["updatedAt"])

        # Dashboard renders Favorites section and CSRF-protected star toggle.
        cookie = self.login()
        status, _, body = self.request("/dashboard", token=None, headers={"Cookie": cookie})
        self.assertEqual(200, status)
        self.assertIn(b"<h2>Favorites</h2>", body)
        self.assertIn(b"/dashboard/drafts/" + first["draftId"].encode() + b"/unfavorite", body)
        csrf = re.search(rb'name="csrf" value="([^"]+)"', body)[1].decode()
        status, _, _ = self.request(
            f"/dashboard/drafts/{first['draftId']}/unfavorite",
            "POST",
            urlencode({"csrf": csrf}).encode(),
            token=None,
            headers={"Cookie": cookie, "Content-Type": "application/x-www-form-urlencoded"},
        )
        self.assertEqual(303, status)
        drafts_after = self.api("/api/drafts")[2]["drafts"]
        self.assertFalse(drafts_after[0]["favorite"])
        self.assertEqual(second["draftId"], drafts_after[0]["draftId"])

    def test_source_download_and_cli_roundtrip_plus_offline_check(self) -> None:
        home = self.root / "home"
        home.mkdir()
        work = self.root / "work"
        work.mkdir()
        token_path = home / ".tailplan" / "token"
        token_path.parent.mkdir(parents=True)
        token_path.write_text("bootstrap-test\n", encoding="utf-8")
        token_path.chmod(0o600)
        env = {**os.environ, "HOME": str(home), "TAILPLAN_BASE_URL": self.base}
        env.pop("TAILPLAN_API_KEY", None)
        env.pop("TAILPLAN_TOKEN_FILE", None)

        def run_cli(*args: str, cwd: Path = work) -> subprocess.CompletedProcess[str]:
            return subprocess.run(
                [sys.executable, str(ROOT / "bin" / "tailplan"), *args],
                cwd=cwd,
                env=env,
                capture_output=True,
                text=True,
                timeout=10,
                check=False,
            )

        # Upload a multi-page folder with a custom slug.
        site = work / "site"
        site.mkdir()
        (site / "index.html").write_text(
            '<meta name="viewport" content="width=device-width, initial-scale=1"><title>Site</title>',
            encoding="utf-8",
        )
        (site / "page2.html").write_text(
            '<meta name="viewport" content="width=device-width, initial-scale=1"><title>Page 2</title>',
            encoding="utf-8",
        )
        checked = run_cli("check", str(site), "--slug", "team-site", "--json")
        self.assertEqual(0, checked.returncode, checked.stdout + checked.stderr)
        self.assertTrue(json.loads(checked.stdout)["ok"])

        uploaded = run_cli("upload", str(site), "--slug", "team-site", "--json")
        self.assertEqual(0, uploaded.returncode, uploaded.stdout + uploaded.stderr)
        up_data = json.loads(uploaded.stdout)
        self.assertEqual("team-site", up_data["slug"])

        # Download twice to verify non-clobbering naming and per-draft binding.
        dl1 = run_cli("download", "team-site", "--json")
        self.assertEqual(0, dl1.returncode, dl1.stdout + dl1.stderr)
        d1 = json.loads(dl1.stdout)
        self.assertTrue(d1["owned"])
        self.assertEqual(["index.html", "page2.html"], d1["pages"])
        self.assertTrue(Path(d1["path"]).is_dir())
        self.assertNotIn("--new", d1["uploadCommand"])

        dl2 = run_cli("download", "team-site", "--json")
        self.assertEqual(0, dl2.returncode, dl2.stdout + dl2.stderr)
        d2 = json.loads(dl2.stdout)
        self.assertNotEqual(d1["path"], d2["path"])
        self.assertTrue(d2["path"].endswith("-2"))

        # Edit downloaded folder and upload back to the same draft via saved binding.
        (Path(d1["path"]) / "page2.html").write_text(
            '<meta name="viewport" content="width=device-width, initial-scale=1"><title>Updated 2</title>',
            encoding="utf-8",
        )
        reup = run_cli("upload", d1["path"], "--json")
        self.assertEqual(0, reup.returncode, reup.stdout + reup.stderr)
        reup_data = json.loads(reup.stdout)
        self.assertEqual(up_data["draftId"], reup_data["draftId"])
        self.assertEqual(2, reup_data["versionNumber"])

        # Offline check fallback when server is unreachable.
        offline = run_cli(
            "check",
            str(site / "index.html"),
            "--base-url",
            "http://127.0.0.1:1",
            "--json",
        )
        self.assertEqual(1, offline.returncode)
        off_data = json.loads(offline.stdout)
        self.assertTrue(off_data["offline"])
        self.assertTrue(off_data["markupOk"])

    def test_security_bypasses_anonymous_slugs_collisions_rebase_and_traversal(self) -> None:
        self.httpd.upload_ip_limit = 200
        self.httpd.upload_key_limit = 200
        # SL1-3 / SL2-1 / SL2-2 / C1-1 / C1-3 / F1-3: HTML5 tokenizer, CSS comment, srcset, and SMIL bypasses.
        bypasses = (
            "<title>Bad</title><style/>body{background:url(javascript:alert(1))}</style>",
            "<title>Bad</title><style/>body{background:url(/*<x>*/javascript:alert(1))}</style>",
            "<title>Bad</title><style/>body{background:url(/*<!---->*/javascript:alert(1))}</style>",
            "<title>Bad</title><svg><style>body{background:url(<!---->javascript:alert(1))}</style></svg>",
            '<title>Bad</title><style>body{background:url(/*x*/"javascript:alert(1)")}</style>',
            '<title>Bad</title><style>@import/*x*/"javascript:alert(1)";</style>',
            '<title>Bad</title><style>a: "\\2f*"; background: url("javascript:alert(1)"); b: "*\\2f";</style>',
            '<title>Bad</title><style>body{background:url("javascript:alert(1)///*"); content:"*/";}</style>',
            '<title>Bad</title><style>a{content:"/*";background:url(javascript:alert(1));content:"*/"}</style>',
            '<title>Bad</title><p style="width:exp/*x*/ression(alert(1))">x</p>',
            '<title>Bad</title><svg><script href="https://evil.test/x.js"></script></svg>',
            '<title>Bad</title><svg><script xlink:href="https://evil.test/x.js"></script></svg>',
            '<![CDATA[><meta http-equiv="refresh" content="0;url=https://evil.test">]]>',
            '<svg><title><meta http-equiv="refresh" content="0;url=https://evil.test"></title></svg>',
            '<svg><style><meta http-equiv="refresh" content="0;url=https://evil.test"></style></svg>',
            '<title>Bad</title><img srcset="javascript:alert(1) 1x">',
            '<title>Bad</title><img srcset="https://ok.test/a.png 1x,javascript:alert(1) 2x">',
            '<title>Bad</title><img srcset="data:image/png;base64,AAAA, javascript:alert(1) 2x">',
            '<title>Bad</title><svg><set attributeName="href" to="javascript:alert(1)"/></svg>',
            '<title>Bad</title>' + ('<div/>' * 520),
            '<title>Bad</title><style>.a\\" { background: url("javascript:alert(1)" /*x*/); }</style>',
            '<title>Bad</title><style>a: "\n; } body { background: url("javascript:alert(1)" /*x*/); }</style>',
            '<title>Bad</title><style>@layer a\\"; @import/*x*/"javascript:alert(1)";</style>',
            '<title>Bad</title><style>.a\\" { behavior/*x*/: url(x); }</style>',
            '<title>Bad</title><style>body { background: url("javascript:alert(1)" anonymous); }</style>',
            '<title>Bad</title><style>a{content:"\n}body{background:url(/*x*/javascript:alert(1))}</style>',
            '<title>Bad</title><style>a{foo:\\"}body{background:url(/*x*/javascript:alert(1))}</style>',
            '<title>Bad</title><svg><p><style/>body{background:url(javascript:alert(1))}</style>',
            '<title>Bad</title><svg><p/><style/>body{background:url(javascript:alert(1))}</style>',
            '<title>Bad</title><svg><foreignObject><style/>body{background:url(javascript:alert(1))}</style></foreignObject></svg>',
            '<title>Bad</title><div><svg></div><style/>body{background:url(javascript:alert(1))}</style>',
            '<title>Bad</title><svg><p>' + ('<div/>' * 520),
            '<title>Bad</title><svg></math><title><script src="https://evil.test/x.js"></script></title></svg>',
            '<title>Bad</title><svg></math><style><meta http-equiv="refresh" content="0;url=https://evil.test"></style></svg>',
            '<title>Bad</title><math></svg><style><meta http-equiv="refresh" content="0;url=https://evil.test"></style></math>',
            '<title>Bad</title><svg><foreignObject><p></svg></p></foreignObject><style><meta http-equiv="refresh" content="0;url=https://evil.test"></style></svg>',
            '<title>Bad</title><svg><foreignObject><style>a{b:url(/*<style>*/javascript:alert(1))}</style></foreignObject></svg>',
            '<title>Bad</title><svg><p><style>a{b:url(/*<style>*/javascript:alert(1))}</style></p></svg>',
            '<title>Bad</title><svg><math></svg><style><!--</style><meta http-equiv="refresh" content="0;url=https://evil.test">--></style>',
            '<title>Bad</title><svg><style><![CDATA[a>/*</style>*/body{background:url(javascript:alert(1))}]]></style></svg>',
            '<title>Bad</title><noscript><!--</noscript><meta http-equiv="refresh" content="0;url=https://evil.test">--></noscript>',
            '<title>Bad</title><noscript><style></noscript><meta http-equiv="refresh" content="0;url=https://evil.test"></style>',
            '<title>Bad</title><svg></br><style/>body{background:url(javascript:alert(1))}</style>',
            '<title>Bad</title><svg></p><style/>body{background:url(javascript:alert(1))}</style>',
            '<title>Bad</title><svg></p><style><!--</style><meta http-equiv="refresh" content="0;url=https://evil.test">--></style>',
            '<title>Bad</title><math><mi><mglyph><style><meta http-equiv="refresh" content="0;url=https://evil.test"></style></mglyph></mi></math>',
            '<title>Bad</title><math><mtext><mglyph><style><p><meta http-equiv="refresh" content="0;url=https://evil.test"></style></mglyph></mtext></math>',
            '<title>Bad</title><math><annotation-xml><svg><foreignObject><style/>body{background:url(javascript:alert(1))}</style></foreignObject></svg></annotation-xml></math>',
            '<title>Bad</title><math><annotation-xml><svg><desc><style><!--</style><iframe src="about:blank"></iframe>--></style></desc></svg></annotation-xml></math>',
            '<title>Bad</title><math><annotation-xml encoding=" text/html"><style><meta http-equiv="refresh" content="0;url=https://evil.test"></style></annotation-xml></math>',
            '<title>Bad</title><math><annotation-xml encoding="x" encoding="text/html"><style><meta http-equiv="refresh" content="0;url=https://evil.test"></style></annotation-xml></math>',
            '<title>Bad</title><svg><foreignObject><span></foreignObject><style/>body{background:url(javascript:alert(1))}</style></svg>',
            '<title>Bad</title><svg><foreignObject></svg></foreignObject><style><!--</style><iframe src="about:blank"></iframe>--></style>',
            '<title>Bad</title><svg><g><foreignObject></g><xmp></svg><iframe src="about:blank"></iframe></xmp>',
            '<title>Bad</title><div><table><tr><td><svg></div><xmp></svg><iframe src="about:blank"></iframe></xmp>',
            '<title>Bad</title><h1><svg></h2><style><!--</style><iframe src="about:blank"></iframe>--></style>',
            '<title>Bad</title><select><style></select><meta http-equiv="refresh" content="0;url=https://evil.test"></style>',
            '<title>Bad</title><script type="text/plain"><!--<script></script><style><!--</script><meta http-equiv="refresh" content="0;url=https://evil.test">--></style>',
            '<title>Bad</title><style>@import "javascript:alert(1)</style>',
            '<title>Bad</title><div><svg><foreignObject></div></foreignObject><style><p><iframe src="about:blank"></iframe></style>',
            '<title>Bad</title><div><svg><desc></div></desc><style><p><iframe src="about:blank"></iframe></style>',
            '<title>Bad</title><div><svg><title></div></title><style><p><a href="javascript:alert(1)">x</a></style>',
            '<title>Bad</title><div><math><mi></div></mi><style><p><iframe src="about:blank"></iframe></style>',
            '<title>Bad</title><div><math><annotation-xml></div><style><p><iframe src="about:blank"></iframe></style>',
            '<title>Bad</title><span><div><svg></span><style><p><iframe src="about:blank"></iframe></style>',
            '<title>Bad</title><ul><li><ul><svg></li><style><p><iframe src="about:blank"></iframe></style>',
            '<title>Bad</title><select><script>var a = "</select><style>";</script></select><iframe src="about:blank"></iframe></style>',
            '<title>Bad</title><select><script>var a = "</select><style>";</script></select><a href="javascript:alert(1)">x</a></style>',
            '<title>Bad</title><table><select></table><style><!--</style><iframe src="about:blank"></iframe>--></style>',
            '<title>Bad</title><table><tr><td><select></td><style><!--</style><iframe src="about:blank"></iframe>--></style>',
            '<title>Bad</title><table><tr><td><select><tr><style><!--</style><iframe src="about:blank"></iframe>--></style>',
            '<title>Bad</title><table><tr><td><select></td><style><!--</style><a href="javascript:alert(1)">x</a>--></style>',
            '<title>Bad</title><select><style><!--</style><iframe src="about:blank"></iframe>--></style>',
            '<title>Bad</title><select><style><!--</style><a href="javascript:alert(1)">x</a>--></style>',
            '<title>Bad</title><select><title><!--</title><iframe src="about:blank"></iframe>--></title>',
            '<title>Bad</title><select><noscript><!--</noscript><iframe src="about:blank"></iframe>--></noscript>',
            '<title>Bad</title><select><table><style></select><iframe src="about:blank"></iframe></style>',
            '<title>Bad</title><select><svg><foreignObject><style></select><iframe src="about:blank"></iframe></style>',
            '<title>Bad</title><table><tr><td><select></th><style></select><iframe src="about:blank"></iframe></style>',
            '<title>Bad</title><table><select></td><style></select><iframe src="about:blank"></iframe></style>',
            '<title>Bad</title><table><tr><td><select></caption><style></select><iframe src="about:blank"></iframe></style>',
            '<title>Bad</title><table><tr><td><svg><foreignObject><select></td></select></foreignObject><style><!--</style><iframe src="about:blank"></iframe>--></style>',
            '<title>Bad</title><table><tr><td><svg><foreignObject><select><tr></select></foreignObject><style><!--</style><iframe src="about:blank"></iframe>--></style>',
            '<title>Bad</title><table><tr><td><svg><foreignObject><select></table></select></foreignObject><style><!--</style><iframe src="about:blank"></iframe>--></style>',
            '<title>Bad</title><table><tr><td><math><mtext><select></td></select></mtext><style><!--</style><iframe src="about:blank"></iframe>--></style>',
            '<title>Bad</title><table><tr><td><svg><foreignObject></td></foreignObject><style><!--</style><iframe src="about:blank"></iframe>--></style>',
            '<title>Bad</title><table><tr><td><svg><foreignObject><tr></tr></foreignObject><style><!--</style><iframe src="about:blank"></iframe>--></style>',
            '<title>Bad</title><select><select><style><!--</style><iframe src="about:blank"></iframe>--></style>',
            '<title>Bad</title><table><thead><tr><td><svg><foreignObject></tbody></foreignObject><style><!--</style><title>--></style></svg><style><!--</style><iframe src="about:blank"></iframe>--></title>',
            '<title>Bad</title><table><tbody><tr><td></tbody><svg><foreignObject></tr></foreignObject><style><!--</style><title>--></style></svg><style><!--</style><iframe src="about:blank"></iframe>--></title>',
            '<title>Bad</title><table><tr><td><svg><td></td><style><!--</style><title>--></style></svg><style><!--</style><iframe src="about:blank"></iframe>--></title>',
            '<title>Bad</title><svg><style><!--</style><title>--></style></svg><table><svg><foreignObject><tr></tr></foreignObject><style><!--</style><iframe src="about:blank"></iframe>--></title>',
            '<title>Bad</title><svg><style><a title="</style><title>"></style></svg><table><td><svg><foreignObject></tr></foreignObject><style><a title="</style><iframe src=about:blank></iframe>"></style></title>',
            '<title>Bad</title><svg><style><a title="</style><title>"></style></svg><table><tbody><td><svg><foreignObject></tr></foreignObject><style><a title="</style><iframe src=about:blank></iframe>"></style></title>',
            '<title>Bad</title><svg><style><a title="</style><title>"></style></svg><table><svg><foreignObject><table></table></foreignObject><style><a title="</style><iframe src=about:blank></iframe>"></style></title>',
            '<title>Bad</title><svg><style><!--</style><title>--></style></svg><table><td><svg><foreignObject></tr></foreignObject><style><!--</style><style><!--</style><iframe src="about:blank"></iframe>--></style></title>',
            '<title>Bad</title><svg><style><!--</style><iframe src="about:blank"></iframe><iframe src="about:blank"></iframe>--></style></svg>',
        )
        for bad in bypasses:
            status, _, res = self.api("/api/uploads", "POST", {"html": bad})
            self.assertEqual(422, status, (bad[:80], res))
            srv = server.validate_upload([{"path": "index.html", "html": bad}])
            off = share.validate_upload_offline([{"path": "index.html", "html": bad}])
            self.assertFalse(off["ok"], bad[:80])
            self.assertEqual(
                [(i["code"], i["count"]) for i in srv["issues"] if i["level"] == "error"],
                [(i["code"], i["count"]) for i in off["issues"] if i["level"] == "error"],
                bad[:80],
            )

        # Self-closing <style/> inside <svg> must not leak style mode into body text.
        svg_self_close = (
            '<meta name="viewport" content="width=device-width, initial-scale=1">'
            '<title>SVG</title><svg><style/></svg><p>url(javascript:alert(1))</p>'
        )
        status, _, ok_svg = self.api("/api/uploads", "POST", {"html": svg_self_close})
        self.assertEqual(201, status, ok_svg)

        # Unclosed <script> still reports sandbox warnings.
        unclosed_script = (
            '<meta name="viewport" content="width=device-width, initial-scale=1">'
            '<title>Unclosed</title><script>localStorage.setItem("a","b")'
        )
        res_unclosed = server.validate_upload([{"path": "index.html", "html": unclosed_script}])
        self.assertTrue(any(i["code"] == "sandbox-storage" for i in res_unclosed["issues"]))
        off_unclosed = share.validate_upload_offline([{"path": "index.html", "html": unclosed_script}])
        self.assertTrue(any(i["code"] == "sandbox-storage" for i in off_unclosed["issues"]))

        # C1-2 / F1-1: new_id() is always 12 chars and slugs cannot collide with existing draft IDs.
        for _ in range(40):
            generated = server.new_id()
            self.assertEqual(12, len(generated))
        # Seed a legacy 8-char draft ID and verify slug claims against it fail.
        data = self.store.load_meta()
        status, _, seeded = self.api("/api/uploads", "POST", {"html": "<title>Seed</title>"})
        self.assertEqual(201, status)
        data = self.store.load_meta()
        draft_obj = data["drafts"].pop(seeded["draftId"])
        short_id = "shortid1"
        old_dir = self.store.drafts / seeded["draftId"]
        new_dir = self.store.drafts / short_id
        old_dir.rename(new_dir)
        draft_obj["draftId"] = short_id
        draft_obj["currentObject"] = str(new_dir / "v1.html")
        draft_obj["publicUrl"] = f"{self.base}/d/{short_id}"
        data["drafts"][short_id] = draft_obj
        self.store.save_meta(data)
        self.assertEqual(
            "unavailable",
            self.api("/api/check", "POST", {"slug": short_id})[2]["slug"]["status"],
        )
        self.assertEqual(
            409,
            self.api("/api/uploads", "POST", {"html": "<title>Collide</title>", "slug": short_id})[0],
        )

        # N1-7: legacy version with hasInlineScript=True but no allowInlineScript stays script-src 'none'.
        status, _, leg = self.api("/api/uploads", "POST", {"html": "<title>Leg</title><script>1+1</script>"})
        self.assertEqual(201, status)
        data = self.store.load_meta()
        data["drafts"][leg["draftId"]]["versions"]["1"].pop("allowInlineScript", None)
        self.store.save_meta(data)
        _, leg_headers, _ = self.request(f"/d/{leg['draftId']}", token=None)
        self.assertIn("script-src 'none'", leg_headers["Content-Security-Policy"])
        self.assertNotIn("sandbox", leg_headers["Content-Security-Policy"])

        # N1-4 / F1-10: Idempotency replay preserves unchanged=True.
        status, _, d_same = self.api("/api/uploads", "POST", {"html": "<title>Same</title>"})
        self.assertEqual(201, status)
        status, _, d_dup1 = self.api(
            "/api/uploads",
            "POST",
            {"html": "<title>Same</title>", "draftId": d_same["draftId"]},
            headers={"Idempotency-Key": "dedup-replay-1"},
        )
        self.assertEqual(200, status)
        self.assertTrue(d_dup1["unchanged"])
        status, _, d_dup2 = self.api(
            "/api/uploads",
            "POST",
            {"html": "<title>Same</title>", "draftId": d_same["draftId"]},
            headers={"Idempotency-Key": "dedup-replay-1"},
        )
        self.assertEqual(200, status)
        self.assertTrue(d_dup2["replayed"])
        self.assertTrue(d_dup2["unchanged"])

        # Server vs offline validator corpus parity (N1-10 / F1-5 / C1-10).
        corpus = [
            '<meta name="viewport" content="width=device-width, initial-scale=1"><title>Ok</title><p>Hi</p>',
            '<title>Srcdoc</title><div srcdoc="<p>x</p>"></div>',
            '<title>Js</title><a href="javascript:alert(1)">x</a>',
            '<title>Img</title><img srcset="https://ok.test/a.png 1x,local.png 2x">',
            '<title>Mod</title><script type="module">fetch("/x")</script>',
        ]
        for sample in corpus:
            srv = server.validate_upload([{"path": "index.html", "html": sample}])
            off = share.validate_upload_offline([{"path": "index.html", "html": sample}])
            self.assertEqual(srv["ok"], off["ok"], sample)
            self.assertEqual(
                [(i["code"], i.get("fix")) for i in srv["issues"]],
                [(i["code"], i.get("fix")) for i in off["issues"]],
                sample,
            )

        # SL1-7: flat HTML5 document with >512 unclosed <li> items passes depth check.
        flat_list = "<title>Flat</title><ul>" + ("<li>item" * 600) + "</ul>"
        status, _, ok_flat = self.api("/api/uploads", "POST", {"html": flat_list})
        self.assertEqual(201, status, ok_flat)
        self.assertTrue(share.validate_upload_offline([{"path": "index.html", "html": flat_list}])["ok"])

        # SL1-4: anonymous uploads cannot claim custom slugs.
        self.httpd.allow_anonymous_uploads = True
        status, _, anon_slug = self.api(
            "/api/uploads", "POST", {"html": "<title>A</title>", "slug": "anon-claim"}, token=None
        )
        self.assertEqual(403, status, anon_slug)
        status, _, anon_chk = self.api("/api/check", "POST", {"slug": "anon-claim"}, token=None)
        self.assertEqual(200, status)
        self.assertFalse(anon_chk["ok"])
        self.assertFalse(anon_chk["capabilities"]["customUrls"])
        self.assertEqual("unavailable", anon_chk["slug"]["status"])

        # SL1-6: case-insensitive duplicates and file/folder prefix collisions are rejected.
        for bad_files in (
            [
                {"path": "index.html", "html": "<title>I</title>"},
                {"path": "About.html", "html": "<title>A</title>"},
                {"path": "about.html", "html": "<title>B</title>"},
            ],
            [
                {"path": "index.html", "html": "<title>I</title>"},
                {"path": "foo.html", "html": "<title>F</title>"},
                {"path": "foo.html/bar.html", "html": "<title>B</title>"},
            ],
        ):
            status, _, res = self.api("/api/uploads", "POST", {"files": bad_files})
            self.assertEqual(422, status, res)
            self.assertFalse(share.validate_upload_offline(bad_files)["ok"])

        # SL1-9: multi-page + slugged draft survives Store reopen/rebase, check_ready, and idempotency across delete.
        mp_files = [
            {"path": "index.html", "html": "<title>MP</title>"},
            {"path": "sub/page.html", "html": "<title>Sub</title>"},
        ]
        status, _, mp = self.api(
            "/api/uploads",
            "POST",
            {"files": mp_files, "slug": "reopen-site"},
            headers={"Idempotency-Key": "mp-key-1"},
        )
        self.assertEqual(201, status, mp)
        reopened = server.Store(self.store.root)
        reopened.check_ready()

        # N2-7 / F2-3: idempotency replay refreshes pages[*].url after a slug rename.
        status, _, renamed_mp = self.api(
            "/api/uploads",
            "POST",
            {"files": mp_files, "draftId": mp["draftId"], "slug": "reopen-site-v2"},
        )
        self.assertEqual(200, status, renamed_mp)
        status, _, replayed_mp = self.api(
            "/api/uploads",
            "POST",
            {"files": mp_files, "slug": "reopen-site"},
            headers={"Idempotency-Key": "mp-key-1"},
        )
        self.assertEqual(200, status, replayed_mp)
        self.assertTrue(replayed_mp["replayed"])
        self.assertEqual("reopen-site-v2", replayed_mp["slug"])
        self.assertTrue(
            all(p["url"].startswith(f"{self.base}/d/reopen-site-v2/") for p in replayed_mp["pages"])
        )
        status, _, restored_mp = self.api(
            "/api/uploads",
            "POST",
            {"files": mp_files, "draftId": mp["draftId"], "slug": "reopen-site"},
        )
        self.assertEqual(200, status, restored_mp)

        # F2-2: bound file with --slug naming another draft retries via slug_names_other_draft code.
        cli_home = self.root / "cli-home"
        cli_home.mkdir()
        tok = cli_home / ".tailplan" / "token"
        tok.parent.mkdir(parents=True)
        tok.write_text("bootstrap-test\n", encoding="utf-8")
        tok.chmod(0o600)
        cli_env = {**os.environ, "HOME": str(cli_home), "TAILPLAN_BASE_URL": self.base}
        cli_env.pop("TAILPLAN_API_KEY", None)
        cli_env.pop("TAILPLAN_TOKEN_FILE", None)

        def run_local_cli(*args: str) -> subprocess.CompletedProcess[str]:
            return subprocess.run(
                [sys.executable, str(ROOT / "bin" / "tailplan"), *args],
                cwd=cli_home,
                env=cli_env,
                capture_output=True,
                text=True,
                timeout=10,
                check=False,
            )

        file_a = cli_home / "bound-a.html"
        file_a.write_text("<title>A</title><p>one</p>", encoding="utf-8")
        file_b = cli_home / "bound-b.html"
        file_b.write_text("<title>B</title><p>two</p>", encoding="utf-8")
        run_a = run_local_cli(str(file_a), "--slug", "slug-a", "--json")
        self.assertEqual(0, run_a.returncode, run_a.stderr)
        draft_a = json.loads(run_a.stdout)["draftId"]
        run_b = run_local_cli(str(file_b), "--slug", "slug-b", "--json")
        self.assertEqual(0, run_b.returncode, run_b.stderr)
        draft_b = json.loads(run_b.stdout)["draftId"]
        # Direct API 409 returns machine-readable code.
        status, _, conflict_body = self.api(
            "/api/uploads",
            "POST",
            {"html": "<title>B2</title>", "draftId": draft_b, "slug": "slug-a"},
        )
        self.assertEqual(409, status)
        self.assertEqual("slug_names_other_draft", conflict_body.get("code"))
        # Local CLI retries slug-only and updates draft_a.
        file_b.write_text("<title>B into A</title><p>updated</p>", encoding="utf-8")
        retry_res = run_local_cli(str(file_b), "--slug", "slug-a", "--json")
        self.assertEqual(0, retry_res.returncode, retry_res.stderr + retry_res.stdout)
        retry_data = json.loads(retry_res.stdout)
        self.assertEqual(draft_a, retry_data["draftId"])
        self.assertEqual("slug-a", retry_data["slug"])
        self.assertEqual(2, retry_data["versionNumber"])

        # N2-5: _write_new_file_nofollow leaves pre-existing files untouched on collision.
        existing = cli_home / "keep.html"
        existing.write_text("keep-me", encoding="utf-8")
        with self.assertRaises(FileExistsError):
            share._write_new_file_nofollow(existing, "overwrite")
        self.assertEqual("keep-me", existing.read_text(encoding="utf-8"))

        # Deep nesting aborts immediately at MAX_DEPTH instead of scanning quadratically.
        deep_payload = "<title>Deep</title>" + ("<span>" * 6000) + ("</x>" * 6000)
        start = time.monotonic()
        srv_deep = server.validate_upload([{"path": "index.html", "html": deep_payload}])
        off_deep = share.validate_upload_offline([{"path": "index.html", "html": deep_payload}])
        elapsed = time.monotonic() - start
        self.assertFalse(srv_deep["ok"])
        self.assertFalse(off_deep["ok"])
        self.assertTrue(any(i["code"] == "too-deep" for i in srv_deep["issues"]))
        self.assertTrue(any(i["code"] == "too-deep" for i in off_deep["issues"]))
        self.assertLess(elapsed, 1.0, f"deep nesting check took {elapsed:.2f}s")

        # Non-default download endpoints are preserved in uploadCommand.
        dl_base = run_local_cli("download", "slug-a", "--base-url", self.base, "--json")
        self.assertEqual(0, dl_base.returncode, dl_base.stderr + dl_base.stdout)
        dl_base_data = json.loads(dl_base.stdout)
        self.assertIn(f"--base-url {self.base}", dl_base_data["uploadCommand"])
        ssh_receipt = share.write_downloaded_source(
            {
                "draftId": "abc123456789",
                "slug": "ssh-plan",
                "versionNumber": 1,
                "owned": True,
                "pages": [{"path": "index.html", "html": "<title>S</title>"}],
            },
            base_url="ssh://staging-tailplan",
            state_dir=cli_home / ".tailplan",
            cwd=cli_home,
        )
        self.assertIn("--target staging-tailplan", ssh_receipt["uploadCommand"])

        # Viewer path traversal attempts return 404.
        for bad_route in (
            "/d/reopen-site/..",
            "/d/reopen-site/%2e%2e",
            "/d/reopen-site/sub%2fpage.html",
        ):
            status, _, _ = self.request(bad_route, token=None)
            self.assertEqual(404, status, bad_route)

        # /source returns owned=False without auth, 404 when disabled or deleted.
        status, _, unowned = self.api("/api/drafts/reopen-site/source", token=None)
        self.assertEqual(200, status)
        self.assertFalse(unowned["owned"])
        self.assertEqual(2, len(unowned["pages"]))
        self.api("/api/drafts/reopen-site/disable", "POST", {})
        self.assertEqual(404, self.api("/api/drafts/reopen-site/source", token=None)[0])
        self.api("/api/drafts/reopen-site/enable", "POST", {})
        self.api("/api/drafts/reopen-site", "DELETE")
        self.assertEqual(404, self.api("/api/drafts/reopen-site/source", token=None)[0])
        # Metadata with old slug idempotency receipt still validates after delete.
        server.Store(self.store.root).check_ready()


class PerDraftBindingAndGuardTests(unittest.TestCase):
    def test_per_draft_bindings_isolate_apis_and_move_corrupt_files_aside(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            state_dir = root / ".tailplan"
            source = root / "plan.html"
            source.write_text("<title>Plan</title>", encoding="utf-8")

            # Legacy drafts.json works as read-only fallback and stays untouched on save.
            legacy_path = state_dir / "drafts.json"
            legacy_path.parent.mkdir(parents=True)
            legacy_content = json.dumps({"files": {str(source.resolve()): {"draftId": "legacy123"}}})
            legacy_path.write_text(legacy_content, encoding="utf-8")
            self.assertEqual(
                "legacy123",
                share.find_draft_binding(state_dir, "https://one.example.test", source)["draftId"],
            )

            p1 = share.save_draft_binding(
                state_dir,
                "https://one.example.test",
                source,
                {"draftId": "draftone1", "publicUrl": "https://one.example.test/d/draftone1", "versionNumber": 1},
            )
            self.assertEqual(legacy_content, legacy_path.read_text(encoding="utf-8"))
            p2 = share.save_draft_binding(
                state_dir,
                "https://two.example.test",
                source,
                {"draftId": "drafttwo2", "publicUrl": "https://two.example.test/d/drafttwo2", "versionNumber": 3},
            )
            self.assertNotEqual(p1, p2)
            self.assertEqual(
                "draftone1",
                share.find_draft_binding(state_dir, "https://one.example.test", source)["draftId"],
            )
            self.assertEqual(
                "drafttwo2",
                share.find_draft_binding(state_dir, "https://two.example.test", source)["draftId"],
            )

            # Corrupt binding file is moved aside instead of silently lost.
            p1.write_text("{corrupt", encoding="utf-8")
            stderr = io.StringIO()
            fallback = share.find_draft_binding(state_dir, "https://one.example.test", source, stderr=stderr)
            self.assertEqual("legacy123", fallback["draftId"])
            self.assertFalse(p1.exists())
            moved = list(p1.parent.glob(f"{p1.name}.corrupt-*"))
            self.assertEqual(1, len(moved))
            self.assertEqual("{corrupt", moved[0].read_text(encoding="utf-8"))
            self.assertIn("Moved it to", stderr.getvalue())

            # SL1-10: valid JSON with invalid schema in per-draft binding is also moved aside.
            p2.write_text(json.dumps({"draftId": "bad!"}), encoding="utf-8")
            stderr2 = io.StringIO()
            self.assertEqual(
                "legacy123",
                share.find_draft_binding(state_dir, "https://two.example.test", source, stderr=stderr2)["draftId"],
            )
            self.assertFalse(p2.exists())
            self.assertIn("invalid state", stderr2.getvalue())

    def test_download_safety_blocks_traversal_and_broken_symlinks(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            cwd = root / "cwd"
            cwd.mkdir()
            state_dir = root / "state"

            # Malicious draftId, slug, and versionNumber are rejected.
            for bad in (
                {"draftId": "../../evil", "slug": None, "versionNumber": 1, "owned": False, "pages": [{"path": "index.html", "html": "<title>X</title>"}]},
                {"draftId": "valid123", "slug": "../../evil", "versionNumber": 1, "owned": False, "pages": [{"path": "index.html", "html": "<title>X</title>"}]},
                {"draftId": "valid123", "slug": "good-slug", "versionNumber": "/../../x", "owned": False, "pages": [{"path": "index.html", "html": "<title>X</title>"}]},
            ):
                with self.assertRaises(share.ConfigurationFailure):
                    share.write_downloaded_source(bad, base_url="https://tailplan.test", state_dir=state_dir, cwd=cwd)

            # Broken symlink at default target is skipped cleanly without following it.
            broken = cwd / "safe-slug-v1.html"
            outside = root / "outside.txt"
            broken.symlink_to(outside)
            receipt = share.write_downloaded_source(
                {
                    "draftId": "valid123",
                    "slug": "safe-slug",
                    "title": "Safe",
                    "versionNumber": 1,
                    "owned": False,
                    "pages": [{"path": "index.html", "html": "<title>Safe</title>"}],
                },
                base_url="https://tailplan.test",
                state_dir=state_dir,
                cwd=cwd,
            )
            self.assertFalse(outside.exists())
            self.assertTrue(receipt["path"].endswith("safe-slug-v1-2.html"))
            self.assertIn("--new", receipt["uploadCommand"])
            self.assertIsNone(share.find_draft_binding(state_dir, "https://tailplan.test", Path(receipt["path"])))

    def test_remote_offline_check_and_url_normalization(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            html_file = root / "plan.html"
            html_file.write_text(
                '<meta name="viewport" content="width=device-width, initial-scale=1"><title>P</title>',
                encoding="utf-8",
            )
            fake_ssh = root / "bin" / "ssh"
            fake_ssh.parent.mkdir()
            fake_ssh.write_text("#!/bin/sh\nexit 255\n", encoding="utf-8")
            fake_ssh.chmod(0o755)
            env = {**os.environ, "PATH": str(fake_ssh.parent) + os.pathsep + os.environ["PATH"]}
            completed = subprocess.run(
                [sys.executable, str(ROOT / "bin" / "tailplan-share-remote"), "check", str(html_file), "--slug", "bad_slug", "--json"],
                env=env,
                capture_output=True,
                text=True,
                check=False,
            )
            self.assertEqual(1, completed.returncode)
            bad_report = json.loads(completed.stdout)
            self.assertFalse(bad_report["ok"])
            self.assertEqual("unavailable", bad_report["slug"]["status"])

            # N1-5 / N2-4 / SL3-4: parse_draft_ref validates host/port and allows subpath URLs without base_url.
            with self.assertRaises(share.ConfigurationFailure):
                share.parse_draft_ref("https://other.example/d/my-plan", "https://tailplan.test")
            with self.assertRaises(share.ConfigurationFailure):
                share.parse_draft_ref("https://tailplan.test:bad/d/my-plan", "https://tailplan.test")
            with self.assertRaises(share.ConfigurationFailure):
                share.parse_draft_ref("https://tailplan.test:bad/d/my-plan")
            self.assertEqual(
                "my-plan",
                share.parse_draft_ref("https://tailplan.test/d/my-plan/share", "https://tailplan.test"),
            )
            self.assertEqual(
                "my-plan",
                share.parse_draft_ref("https://tailplan.test/tailplan/d/my-plan/share"),
            )

            completed = subprocess.run(
                [sys.executable, str(ROOT / "bin" / "tailplan-share-remote"), "check", str(html_file), "--slug", "good-slug", "--json"],
                env=env,
                capture_output=True,
                text=True,
                check=False,
            )
            self.assertEqual(1, completed.returncode)
            report = json.loads(completed.stdout)
            self.assertFalse(report["ok"])
            self.assertTrue(report["offline"])
            self.assertTrue(report["markupOk"])
            self.assertEqual("unchecked", report["slug"]["status"])

            # SL1-8 / N2-4: subpath URL ref is normalized before SSH management commands.
            echo_ssh = root / "bin2" / "ssh"
            echo_ssh.parent.mkdir()
            echo_ssh.write_text('#!/usr/bin/env python3\nimport json,sys\nprint(json.dumps(sys.argv[1:]))\n', encoding="utf-8")
            echo_ssh.chmod(0o755)
            env2 = {**os.environ, "PATH": str(echo_ssh.parent) + os.pathsep + os.environ["PATH"]}
            fav_run = subprocess.run(
                [sys.executable, str(ROOT / "bin" / "tailplan-share-remote"), "favorite", "https://tailplan.test/tailplan/d/My-Plan"],
                env=env2,
                capture_output=True,
                text=True,
                check=True,
            )
            self.assertEqual(["tailplan-server", "tailplan-share", "favorite", "my-plan"], json.loads(fav_run.stdout))

            # C2-4: online remote check rewrites the staged remote path to the local source path.
            online_bin = root / "bin3"
            online_bin.mkdir()
            (online_bin / "scp").write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
            (online_bin / "scp").chmod(0o755)
            (online_bin / "ssh").write_text(
                "#!/usr/bin/env python3\n"
                "import json, sys\n"
                "args = sys.argv[2:]\n"
                "if args[:2] == ['mktemp', '-d']:\n"
                "    print('/tmp/tailplan-upload.Ab12Cd34')\n"
                "elif args[:2] == ['tailplan-share', 'check']:\n"
                "    print(json.dumps({'ok': True, 'path': args[2], 'pages': [], 'issues': [], 'errors': [], 'warnings': []}))\n",
                encoding="utf-8",
            )
            (online_bin / "ssh").chmod(0o755)
            env3 = {**os.environ, "PATH": str(online_bin) + os.pathsep + os.environ["PATH"]}
            online_chk = subprocess.run(
                [sys.executable, str(ROOT / "bin" / "tailplan-share-remote"), "check", str(html_file), "--json"],
                env=env3,
                capture_output=True,
                text=True,
                check=True,
            )
            self.assertEqual(str(html_file.resolve()), json.loads(online_chk.stdout)["path"])

            # C3-3 / F3-3: remote check error envelope forwards error text instead of "Not ready.".
            err_bin = root / "bin3_err"
            err_bin.mkdir()
            (err_bin / "scp").write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
            (err_bin / "scp").chmod(0o755)
            (err_bin / "ssh").write_text(
                "#!/usr/bin/env python3\n"
                "import json, sys\n"
                "args = sys.argv[2:]\n"
                "if args[:2] == ['mktemp', '-d']:\n"
                "    print('/tmp/tailplan-upload.Ef56Gh78')\n"
                "elif args[:2] == ['tailplan-share', 'check']:\n"
                "    print(json.dumps({'ok': False, 'category': 'authentication', 'error': 'Token rejected.'}))\n"
                "    sys.exit(1)\n",
                encoding="utf-8",
            )
            (err_bin / "ssh").chmod(0o755)
            env3_err = {**os.environ, "PATH": str(err_bin) + os.pathsep + os.environ["PATH"]}
            err_txt = subprocess.run(
                [sys.executable, str(ROOT / "bin" / "tailplan-share-remote"), "check", str(html_file)],
                env=env3_err,
                capture_output=True,
                text=True,
                check=False,
            )
            self.assertEqual(1, err_txt.returncode)
            self.assertIn("Token rejected.", err_txt.stderr)
            self.assertNotIn("Not ready.", err_txt.stdout)
            err_json = subprocess.run(
                [sys.executable, str(ROOT / "bin" / "tailplan-share-remote"), "check", str(html_file), "--json"],
                env=env3_err,
                capture_output=True,
                text=True,
                check=False,
            )
            self.assertEqual(1, err_json.returncode)
            err_payload = json.loads(err_json.stdout)
            self.assertEqual("Token rejected.", err_payload["error"])
            self.assertNotIn("path", err_payload)

            # F2-2: remote CLI retries slug-only on slug_names_other_draft conflict code.
            retry_bin = root / "bin4"
            retry_bin.mkdir()
            (retry_bin / "scp").write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
            (retry_bin / "scp").chmod(0o755)
            calls_log = root / "ssh-calls.jsonl"
            (retry_bin / "ssh").write_text(
                "#!/usr/bin/env python3\n"
                "import json, sys\n"
                f"log = {str(calls_log)!r}\n"
                "args = sys.argv[2:]\n"
                "if args[:2] == ['mktemp', '-d']:\n"
                "    print('/tmp/tailplan-upload.Cd34Ef56')\n"
                "elif args and args[0] == 'tailplan-share':\n"
                "    with open(log, 'a', encoding='utf-8') as f:\n"
                "        f.write(json.dumps(args) + '\\n')\n"
                "    if '--draft' in args:\n"
                "        print(json.dumps({'ok': False, 'category': 'conflict', 'code': 'slug_names_other_draft', 'error': 'different wording'}))\n"
                "        sys.exit(1)\n"
                "    print(json.dumps({'ok': True, 'draftId': 'target123456', 'slug': 'target-slug', 'publicUrl': 'https://tailplan.test/d/target-slug', 'versionNumber': 2, 'warnings': []}))\n",
                encoding="utf-8",
            )
            (retry_bin / "ssh").chmod(0o755)
            fake_home = root / "home"
            fake_home.mkdir()
            share.save_draft_binding(
                fake_home / ".tailplan",
                "ssh://tailplan-server",
                html_file,
                {
                    "draftId": "olddraft1234",
                    "publicUrl": "https://tailplan.test/d/olddraft1234",
                    "versionNumber": 1,
                },
            )
            env4 = {**os.environ, "HOME": str(fake_home), "PATH": str(retry_bin) + os.pathsep + os.environ["PATH"]}
            remote_retry = subprocess.run(
                [sys.executable, str(ROOT / "bin" / "tailplan-share-remote"), str(html_file), "--slug", "target-slug", "--json"],
                env=env4,
                capture_output=True,
                text=True,
                check=True,
            )
            self.assertEqual("target123456", json.loads(remote_retry.stdout)["draftId"])
            logged = [json.loads(line) for line in calls_log.read_text(encoding="utf-8").splitlines()]
            self.assertEqual(2, len(logged))
            self.assertIn("--draft", logged[0])
            self.assertNotIn("--draft", logged[1])

    def test_publish_guard_allows_new_safe_commands_and_rejects_abuse(self) -> None:
        for args in (
            ["favorite", "my-plan"],
            ["unfavorite", "my-plan"],
            ["download", "my-plan", "--print-source"],
            ["check", "--slug", "my-plan", "--json"],
        ):
            with (
                patch.object(guard, "configured_share_command", return_value="/publisher"),
                patch.object(guard.os, "execv", side_effect=SystemExit(0)) as execute,
                self.assertRaises(SystemExit) as exited,
            ):
                guard.execute(["tailplan-share", *args])
            self.assertEqual(0, exited.exception.code, args)
            execute.assert_called_once_with("/publisher", ["/publisher", *args])

        # Staged --files-json, --slug, and symlinked parent directory checks using isolated tmp dirs (F1-15).
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            staged = root / "tailplan-upload.Zz12Yy34"
            staged.mkdir(mode=0o700)
            pages_json = staged / "pages.json"
            pages_json.write_text('{"files":[{"path":"index.html","html":"<title>X</title>"}]}', encoding="utf-8")
            esc_root = re.escape(str(root))
            patched_file_re = re.compile(rf"({esc_root}/tailplan-upload\.[A-Za-z0-9]{{8}})/([A-Za-z0-9_][A-Za-z0-9_.-]{{0,254}})")
            with (
                patch.object(guard, "FILE_RE", patched_file_re),
                patch.object(guard, "configured_share_command", return_value="/publisher"),
                patch.object(guard.os, "execv", side_effect=SystemExit(0)) as execute,
                self.assertRaises(SystemExit) as exited,
            ):
                guard.execute(["tailplan-share", "--files-json", str(pages_json), "--slug", "my-site", "--json"])
            self.assertEqual(0, exited.exception.code)
            execute.assert_called_once()

            with (
                patch.object(guard, "FILE_RE", patched_file_re),
                self.assertRaises(SystemExit) as exited,
            ):
                guard.execute(["tailplan-share", "--files-json", str(pages_json), "--slug", "my-site", "--new"])
            self.assertEqual(126, exited.exception.code)

            # SL1-2: symlinked staging directory is denied.
            real_dir = root / "real"
            real_dir.mkdir()
            (real_dir / "secret.html").write_text("<title>S</title>", encoding="utf-8")
            link_dir = root / "tailplan-upload.Sym12345"
            link_dir.symlink_to(real_dir)
            with patch.object(guard, "FILE_RE", patched_file_re):
                for cmd in (
                    ["tailplan-share", f"{link_dir}/secret.html"],
                    ["tailplan-share", "check", f"{link_dir}/secret.html"],
                ):
                    with self.assertRaises(SystemExit) as exited:
                        guard.execute(cmd)
                    self.assertEqual(126, exited.exception.code, cmd)

        for bad in (
            ["download", "my-plan"],
            ["download", "../secret", "--print-source"],
            ["favorite", "-bad"],
            ["check", "/etc/passwd"],
            ["check", "--slug", "BAD"],
        ):
            with self.assertRaises(SystemExit) as exited:
                guard.execute(["tailplan-share", *bad])
            self.assertEqual(126, exited.exception.code, bad)

    def test_share_public_resolves_slug_urls_and_multipage(self) -> None:
        public_mod = _load_script("tailplan_share_public_006", ROOT / "bin" / "tailplan-share-public")
        self.assertEqual(
            "secret-plan-v2",
            public_mod.draft_ref_from_value("https://host.test/tailplan/d/secret-plan-v2/share"),
        )
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            meta_file = root / "metadata.json"
            draft_dir = root / "drafts" / "abc123456789"
            v1_html = draft_dir / "v1.html"
            sub_html = draft_dir / "v1" / "sub" / "page.html"
            sub_html.parent.mkdir(parents=True)
            v1_html.write_text("<title>..</title><p>Home</p>", encoding="utf-8")
            sub_html.write_text("<title>Sub</title><p>Subpage</p>", encoding="utf-8")
            meta_file.write_text(
                json.dumps(
                    {
                        "drafts": {
                            "abc123456789": {
                                "draftId": "abc123456789",
                                "slug": "secret-plan-v2",
                                "title": "..",
                                "currentObject": str(v1_html),
                                "latestVersionNumber": 1,
                                "versions": {
                                    "1": {
                                        "pageCount": 2,
                                        "pages": [
                                            {"path": "index.html"},
                                            {"path": "sub/page.html"},
                                        ],
                                    }
                                },
                            }
                        }
                    }
                ),
                encoding="utf-8",
            )
            with patch.object(public_mod, "TAILPLAN_META", meta_file):
                loaded = public_mod.load_draft("secret-plan-v2")
            self.assertEqual("abc123456789", loaded["draftId"])

            # C1-4 / C2-3: multi-page folder export handles dot-only titles and copies subpages.
            captured: dict[str, object] = {}

            def fake_run(cmd: list[str], **_kwargs):
                target_dir = Path(cmd[2])
                captured["name"] = target_dir.name
                captured["files"] = sorted(
                    p.relative_to(target_dir).as_posix()
                    for p in target_dir.rglob("*.html")
                )
                return subprocess.CompletedProcess(cmd, 0, stdout="https://postplan.test/p/ok\n", stderr="")

            with (
                patch.object(public_mod, "TAILPLAN_META", meta_file),
                patch.object(public_mod.shutil, "which", return_value="/usr/bin/postplan"),
                patch.object(public_mod.subprocess, "run", side_effect=fake_run),
                patch.object(
                    public_mod.sys,
                    "argv",
                    ["tailplan-share-public", "https://host.test/tailplan/d/secret-plan-v2", "--i-approve-public-sharing"],
                ),
            ):
                rc = public_mod.main()
            self.assertEqual(0, rc)
            self.assertEqual("abc123456789", captured["name"])
            self.assertEqual(["index.html", "sub/page.html"], captured["files"])


if __name__ == "__main__":
    unittest.main()
