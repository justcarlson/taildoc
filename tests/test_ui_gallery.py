"""HTTP checks for the public /ui gallery. Browser checks live in test_ui_gallery_browser.py."""
import json
import re
import unittest
from http.client import HTTPConnection
from pathlib import Path
from tempfile import TemporaryDirectory
from threading import Thread

import tailplan_server as server
from tailplan_ui import bundler, catalog, lint
from tailplan_ui.color import tokens as T
from tailplan_web import ui_gallery

STRICT_CSP = ("default-src 'none'; script-src 'none'; style-src 'unsafe-inline'; img-src https: data:; "
              "base-uri 'none'; frame-ancestors 'none'; form-action 'none'")


class GalleryPage(unittest.TestCase):
    def test_page_is_bundled_zero_js_and_lint_clean(self) -> None:
        page = ui_gallery.render_gallery("")
        self.assertIsNone(re.search(r"<script\b", page, re.I))
        self.assertIsNone(re.search(r"<form\b", page, re.I))
        self.assertIsNone(re.search(r"\son[a-z]+\s*=", page, re.I))
        self.assertIn(f'data-tailplan-ui="{bundler.UI_VERSION}"', page)
        self.assertEqual([], lint.lint(page))
        self.assertEqual([], bundler.bundle(ui_gallery.source_html(""))[1]["warnings"])
        self.assertIs(page, ui_gallery.render_gallery(""))

    def test_icon_cards_have_their_sprite(self) -> None:
        page = ui_gallery.render_gallery("")
        self.assertIn('<symbol id="tp-icon-check"', page)
        self.assertLess(page.index('id="tp-icon-check"'), page.index('href="#tp-icon-check"'))

    def test_every_catalog_item_has_a_card_with_markup(self) -> None:
        page = ui_gallery.render_gallery("")
        items = catalog()["items"]
        self.assertEqual(58, len(items))
        for item in items:
            self.assertIn(f'data-gallery-slot="{item["slot"]}"', page, item["slot"])
            if item["category"] != "wireframes":
                self.assertIn(item["markup"], page, item["slot"])
        self.assertEqual(58, page.count("<summary>Markup</summary>"))

    def test_mixer_covers_every_preset_accent_viz_mode_and_contrast(self) -> None:
        page = ui_gallery.render_gallery("")
        for preset in T.presets():
            self.assertIn(f'id="mx-t-{preset}"', page)
            self.assertIn(f'data-mixer-stage="{preset}"', page)
        for accent in T.accents():
            self.assertIn(f'id="mx-a-{accent}"', page)
        for viz in T.viz_sets():
            self.assertIn(f'id="mx-v-{viz}"', page)
        for value in ("auto", "light", "dark"):
            self.assertIn(f'id="mx-m-{value}"', page)
        for value in T.CONTRASTS:
            self.assertIn(f'id="mx-c-{value}"', page)
        self.assertEqual(11, page.count('class="mx-stage '))
        self.assertIn('&lt;html data-ui-theme="', page)

    def test_wireframes_show_the_duo_frames_and_a_mockup_stage(self) -> None:
        page = ui_gallery.render_gallery("")
        for device in ("duo-folded", "duo-landscape", "duo-wide"):
            self.assertIn(f'data-example="{device}"', page)
        self.assertIn('data-slot="mockup-stage"', page)
        for ctl in ("fidelity", "mode", "viewport", "view"):
            self.assertIn(f'data-ctl="{ctl}"', page)
        self.assertIn('data-variant="wire" data-ui-theme="wire"', page)
        self.assertIn('data-variant="graphite" data-ui-theme="graphite"', page)


class GalleryRoutes(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = TemporaryDirectory()
        self.addCleanup(self.temp_dir.cleanup)
        self.httpd = server.TailplanHTTPServer(("127.0.0.1", 0), server.Handler)
        self.httpd.store = server.Store(Path(self.temp_dir.name))
        self.httpd.token = "secret-token"
        self.httpd.base_url = "https://configured.example/tailplan"
        self.httpd.redirect_view_base_url = ""
        Thread(target=self.httpd.serve_forever, daemon=True).start()
        self.addCleanup(self.httpd.server_close)
        self.addCleanup(self.httpd.shutdown)

    def request(self, method, path, body=None, headers=None):
        conn = HTTPConnection(*self.httpd.server_address, timeout=10)
        try:
            conn.request(method, path, body=body, headers=headers or {})
            response = conn.getresponse()
            return response.status, dict(response.getheaders()), response.read()
        finally:
            conn.close()

    def test_ui_is_public_with_a_strict_csp(self) -> None:
        for path in ("/ui", "/ui/", "/tailplan/ui"):
            status, headers, body = self.request("GET", path)
            self.assertEqual(200, status, path)
            self.assertEqual("text/html; charset=utf-8", headers["Content-Type"])
            self.assertEqual(STRICT_CSP, headers["Content-Security-Policy"])
            self.assertEqual("DENY", headers["X-Frame-Options"])
            self.assertNotIn("Set-Cookie", headers)
            page = body.decode("utf-8")
            self.assertNotIn("<script", page)
            self.assertNotIn("<form", page)
            self.assertIn('href="https://configured.example/tailplan/"', page)
        status, headers, body = self.request("HEAD", "/ui")
        self.assertEqual((200, b""), (status, body))
        self.assertGreater(int(headers["Content-Length"]), 100_000)
        self.assertEqual(404, self.request("GET", "/ui/extra")[0])

    def test_home_links_the_gallery(self) -> None:
        status, _, body = self.request("GET", "/")
        self.assertEqual(200, status)
        self.assertIn('href="https://configured.example/tailplan/ui"', body.decode("utf-8"))

    def test_upload_cannot_claim_the_ui_slug(self) -> None:
        self.assertIn("ui", server.RESERVED_SLUGS)
        payload = json.dumps({"html": "<title>Taken</title><p>x</p>", "slug": "ui"}).encode()
        status, _, body = self.request("POST", "/api/uploads", payload,
                                       {"Authorization": "Bearer secret-token", "Content-Type": "application/json"})
        self.assertEqual(422, status)
        self.assertIn("Custom URL", json.loads(body)["error"])
        status, _, _ = self.request("GET", "/ui")
        self.assertEqual(200, status)


if __name__ == "__main__":
    unittest.main()
