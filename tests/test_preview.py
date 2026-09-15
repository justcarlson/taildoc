"""Social cards must not change the byte-preserving document endpoints."""
import struct
import unittest
from html.parser import HTMLParser
from http.client import HTTPConnection
from pathlib import Path
from tempfile import TemporaryDirectory
from threading import Thread

import tailplan_server as server


class Meta(HTMLParser):
    def __init__(self, text):
        super().__init__()
        self.tags = {}
        self.feed(text)

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        if tag == "meta":
            self.tags.setdefault(attrs.get("property", attrs.get("name")), []).append(
                attrs.get("content"))


class PreviewTests(unittest.TestCase):
    def setUp(self):
        self.temp = TemporaryDirectory()
        self.httpd = server.TailplanHTTPServer(("127.0.0.1", 0), server.Handler)
        self.httpd.store = server.Store(Path(self.temp.name))
        self.httpd.token = "test-token"
        self.httpd.base_url = "https://preview.example/tailplan"
        self.httpd.redirect_view_base_url = ""
        self.thread = Thread(target=self.httpd.serve_forever, daemon=True)
        self.thread.start()

    def tearDown(self):
        self.httpd.shutdown()
        self.httpd.server_close()
        self.thread.join()
        self.temp.cleanup()

    def get(self, path, method="GET", headers=None):
        conn = HTTPConnection(*self.httpd.server_address, timeout=10)
        conn.request(method, path, headers=headers or {})
        response = conn.getresponse()
        result = response.status, dict(response.getheaders()), response.read()
        conn.close()
        return result

    def upload(self, doc, draft_id=None):
        return self.httpd.store.upsert(doc, "report.html", draft_id, self.httpd.base_url)

    def test_share_page_has_automatic_png_and_original_routes_stay_exact(self):
        doc = '<!doctype html><html><head><title>Simple &amp; polished</title></head><body><h1>Report</h1></body></html>'
        result = self.upload(doc)
        route = f"/tailplan/d/{result['draftId']}"
        status, headers, body = self.get(route + "/share")
        self.assertEqual(200, status)
        meta = Meta(body.decode()).tags
        self.assertEqual(["Simple & polished"], meta["og:title"])
        self.assertEqual(["summary_large_image"], meta["twitter:card"])
        image_url = meta["og:image"][0]
        self.assertEqual(self.httpd.base_url + f"/d/{result['draftId']}/v/1/preview.png", image_url)
        self.assertIn(b"<h1>Report</h1>", body)
        self.assertIn("script-src 'none'", headers["Content-Security-Policy"])
        for suffix in ("", "/raw", "/content", "/v/1/raw"):
            self.assertEqual(doc.encode(), self.get(route + suffix)[2])
        status, headers, image = self.get(image_url.removeprefix("https://preview.example"))
        self.assertEqual(200, status)
        self.assertEqual("image/png", headers["Content-Type"])
        self.assertEqual(b"\x89PNG\r\n\x1a\n", image[:8])
        self.assertEqual((1200, 630), struct.unpack(">II", image[16:24]))
        self.assertLess(len(image), 150000)
        self.assertEqual("private, no-store", headers["Cache-Control"])
        self.assertEqual("nosniff", headers["X-Content-Type-Options"])

    def test_upload_api_exposes_share_url_without_changing_public_url(self):
        import json
        conn = HTTPConnection(*self.httpd.server_address, timeout=10)
        conn.request("POST", "/tailplan/api/uploads", json.dumps({"html": "<title>Automatic</title>"}),
                     {"Authorization": "Bearer test-token", "Content-Type": "application/json"})
        response = conn.getresponse()
        self.assertEqual(201, response.status)
        result = json.loads(response.read())
        conn.close()
        self.assertEqual(result["publicUrl"] + "/share", result.get("shareUrl"))
        listing = self.httpd.store.list_drafts("local", self.httpd.base_url)
        self.assertEqual(result["shareUrl"], listing[0]["shareUrl"])

    def test_history_head_missing_and_lifecycle(self):
        first = self.upload("<title>First</title>")
        route = f"/d/{first['draftId']}"
        old = self.get(route + "/v/1/preview.png")[2]
        self.upload("<title>Second</title>", first["draftId"])
        self.assertNotEqual(old, self.get(route + "/preview.png")[2])
        self.assertEqual(old, self.get(route + "/v/1/preview.png")[2])
        meta = Meta(self.get(route + "/v/1/share")[2].decode()).tags
        self.assertEqual(["First"], meta["og:title"])
        for suffix in ("/share", "/preview.png", "/v/1/share", "/v/1/preview.png"):
            status, headers, body = self.get(route + suffix, "HEAD")
            self.assertEqual(200, status)
            self.assertEqual(b"", body)
            self.assertEqual(int(headers["Content-Length"]), len(self.get(route + suffix)[2]))
        for suffix in ("/v/0/share", "/v/999/preview.png", "/v/999/share"):
            self.assertEqual(404, self.get(route + suffix)[0])
        self.assertEqual(404, self.get("/d/missing123/share")[0])
        for action in ("disable", "enable", "delete"):
            self.httpd.store.change_draft("local", first["draftId"], action, None)
            expected = 200 if action == "enable" else 404
            for suffix in ("/share", "/preview.png", "/v/1/share", "/v/1/preview.png"):
                self.assertEqual(expected, self.get(route + suffix)[0])

    def test_malformed_and_conflicting_metadata_is_escaped(self):
        documents = [
            '<TITLE>A &quot;quote&quot; &amp; résumé</TITLE><p>unchanged</p>',
            ('<!doctype html>\r\n<html>\r\n<head data-x="1 > 0"><title>A &quot;quote&quot; &amp; résumé</title>'
            '<meta property="og:image" content="https://untrusted.test/a">'
            '<meta NAME="twitter:card" content="summary"/></head><p>unchanged</p>'),
            '<!-- <head> -->\n<title>A &quot;quote&quot; &amp; résumé</title><p>unchanged</p>',
        ]
        for doc in documents:
            with self.subTest(doc=doc):
                result = self.upload(doc)
                body = self.get(f"/d/{result['draftId']}/share", headers={"Host": "evil.test"})[2]
                meta = Meta(body.decode()).tags
                self.assertEqual(['A "quote" & résumé'], meta["og:title"])
                self.assertEqual(1, len(meta["og:image"]))
                self.assertNotIn(b"evil.test", body)
                self.assertNotIn(b"https://untrusted.test/a", body)
                self.assertIn(b"<p>unchanged</p>", body)

    def test_titleless_documents_keep_latest_and_historical_filenames(self):
        store = self.httpd.store
        first = store.upsert('<p>First</p>', 'quarterly-report.html', None, self.httpd.base_url)
        route = f"/d/{first['draftId']}"
        image = self.get(route + '/v/1/preview.png')[2]
        store.upsert('<p>Second</p>', 'annual-report.html', first['draftId'], self.httpd.base_url)
        for suffix, expected in (('/share', 'annual-report.html'),
                                 ('/v/1/share', 'quarterly-report.html')):
            meta = Meta(self.get(route + suffix)[2].decode()).tags
            self.assertEqual([expected], meta['og:title'])
        self.assertEqual(image, self.get(route + '/v/1/preview.png')[2])
        self.assertNotEqual(image, self.get(route + '/preview.png')[2])

    def test_unicode_line_separators_do_not_shift_metadata_edits(self):
        doc = '<html>\u2028\n<head><title>Résumé</title><meta property="og:image" content="old"></head><body>Keep</body></html>'
        output = server.preview_document(doc, "Résumé", "https://example.test/image.png",
                                         "https://example.test/share")
        self.assertIn('<html>\u2028\n<head>\n<meta', output)
        self.assertIn('<title>Résumé</title>', output)
        self.assertEqual(["https://example.test/image.png"], Meta(output).tags["og:image"])

    def test_corrupt_object_does_not_serve_cached_private_image(self):
        result = self.upload("<title>Cached</title>")
        path = f"/d/{result['draftId']}/preview.png"
        self.assertEqual(200, self.get(path)[0])
        (self.httpd.store.drafts / result["draftId"] / "v1.html").write_text("corrupt")
        self.assertEqual(503, self.get(path)[0])

    def test_redirect_and_unicode_rendering_are_bounded(self):
        self.httpd.redirect_view_base_url = "https://other.example/tailplan"
        for suffix in ("/share", "/v/1/preview.png"):
            path = "/d/example123" + suffix
            for method in ("GET", "HEAD"):
                status, headers, body = self.get(path, method)
                self.assertEqual(308, status)
                self.assertEqual("https://other.example/tailplan" + path, headers["Location"])
                self.assertEqual(b"", body)
        for title in ("", "Résumé déjà vu", "漢字 🚀 “Quotes” — test", "long " * 1000, "x" * 140):
            first = server.preview_png(title, "example.test")
            server.preview_png.cache_clear()
            self.assertEqual(first, server.preview_png(title, "example.test"))
            self.assertLess(len(first), 150000)
        self.assertEqual(32, server.preview_png.cache_info().maxsize)


class PreviewClientTests(unittest.TestCase):
    def test_emit_prefers_share_url_and_keeps_raw_json_contract(self):
        import io
        import json
        import runpy
        client = runpy.run_path(str(Path(__file__).resolve().parents[1] / "bin/tailplan-share"))
        result = {"ok": True, "draftId": "example123", "versionNumber": 1,
                  "publicUrl": "https://example.test/d/example123",
                  "shareUrl": "https://example.test/d/example123/share"}
        for json_output in (False, True):
            output = io.StringIO()
            client["_emit_success"](result, json_output=json_output,
                                    stdout=output, stderr=io.StringIO())
            if json_output:
                self.assertEqual(result["shareUrl"], json.loads(output.getvalue()).get("shareUrl"))
                self.assertEqual(result["publicUrl"], json.loads(output.getvalue())["publicUrl"])
            else:
                self.assertIn("URL: " + result["shareUrl"] + "\n", output.getvalue())
        with self.assertRaises(client["ProtocolFailure"]):
            client["validate_upload_result"]({**result, "shareUrl": "https://evil.test"},
                                            "https://example.test")


if __name__ == "__main__":
    unittest.main()
