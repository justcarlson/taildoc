from __future__ import annotations

import hashlib
import unittest
from http.client import HTTPConnection
from pathlib import Path
from tempfile import TemporaryDirectory
from threading import Thread
from unittest.mock import patch

import tailplan_server as server
from tailplan_web import assets, render

MARK = Path(assets.STATIC_DIR) / "tailplan-mark.svg"
MOVED = (
    "WEB_CSS", "VISIBILITY_LABELS", "esc", "csrf_field", "web_page", "render_login",
    "external_link", "_render_dashboard_row", "sharing_badges", "_render_shared_row",
    "render_dashboard", "render_sharing", "render_activate", "render_detail",
    "render_keys", "render_key",
)


class AssetTable(unittest.TestCase):
    def test_hashed_name_uses_content_hash(self) -> None:
        body = MARK.read_bytes()
        sha8 = hashlib.sha256(body).hexdigest()[:8]
        self.assertEqual(
            f"https://t.example/tailplan/assets/tailplan-mark.{sha8}.svg",
            assets.asset_url("https://t.example/tailplan/", "tailplan-mark.svg"),
        )
        found = assets.lookup_asset(f"tailplan-mark.{sha8}.svg")
        self.assertEqual((body, "image/svg+xml", f'"{sha8}"'), found)

    def test_lookup_rejects_unknown_unhashed_and_traversal(self) -> None:
        for name in (
            "tailplan-mark.svg", "tailplan-mark.00000000.svg", "", "../tailplan_server.py",
            "%2e%2e/tailplan_server.py", "./tailplan-mark.svg", "/tailplan-mark.svg",
        ):
            self.assertIsNone(assets.lookup_asset(name), name)

    def test_unknown_asset_name_has_no_url(self) -> None:
        with self.assertRaises(KeyError):
            assets.asset_url("", "missing.svg")


class ReExports(unittest.TestCase):
    def test_server_reexports_every_moved_symbol(self) -> None:
        for name in MOVED:
            self.assertIs(getattr(render, name), getattr(server, name), name)

    def test_reserved_slugs_cover_asset_routes(self) -> None:
        self.assertLessEqual({"assets", "static"}, server.RESERVED_SLUGS)


class BuildId(unittest.TestCase):
    def test_build_id_is_deterministic_and_matches_server(self) -> None:
        self.assertRegex(server.BUILD_ID, r"^[a-f0-9]{12}$")
        self.assertEqual(server.BUILD_ID, assets.build_id(server.__file__))
        self.assertEqual(assets.build_id(server.__file__), assets.build_id(server.__file__))

    def test_build_id_changes_with_server_assets_and_web_sources(self) -> None:
        base = assets.build_id(server.__file__)
        with TemporaryDirectory() as td:
            other = Path(td) / "server.py"
            other.write_bytes(Path(server.__file__).read_bytes() + b"\n#\n")
            self.assertNotEqual(base, assets.build_id(other))
        with patch.object(assets, "assets_digest", return_value="0" * 64):
            self.assertNotEqual(base, assets.build_id(server.__file__))
        with TemporaryDirectory() as td:
            root = Path(td)
            for source in assets.ROOT.glob("*.py"):
                (root / source.name).write_bytes(source.read_bytes())
            (root / "render.py").write_bytes(b"# changed\n")
            with patch.object(assets, "ROOT", root):
                self.assertNotEqual(base, assets.build_id(server.__file__))


class AssetRoutes(unittest.TestCase):
    def serve(self, base_url: str):
        td = TemporaryDirectory()
        self.addCleanup(td.cleanup)
        primary, _ = server.create_servers(
            ("127.0.0.1", 0), None, store=server.Store(Path(td.name)), token="t",
            base_url=base_url, redirect_view_base_url="",
        )
        Thread(target=primary.serve_forever, daemon=True).start()
        self.addCleanup(primary.server_close)
        self.addCleanup(primary.shutdown)
        return primary.server_address

    def fetch(self, address, method: str, path: str):
        conn = HTTPConnection(*address, timeout=5)
        try:
            conn.request(method, path)
            response = conn.getresponse()
            return response.status, dict(response.getheaders()), response.read()
        finally:
            conn.close()

    def check_asset(self, address, prefix: str) -> None:
        body = MARK.read_bytes()
        sha8 = hashlib.sha256(body).hexdigest()[:8]
        path = f"{prefix}/assets/tailplan-mark.{sha8}.svg"
        for method in ("GET", "HEAD"):
            status, headers, payload = self.fetch(address, method, path)
            self.assertEqual(200, status, method)
            self.assertEqual("image/svg+xml", headers["Content-Type"])
            self.assertEqual("public, max-age=31536000, immutable", headers["Cache-Control"])
            self.assertEqual("nosniff", headers["X-Content-Type-Options"])
            self.assertEqual("default-src 'none'", headers["Content-Security-Policy"])
            self.assertEqual(f'"{sha8}"', headers["ETag"])
            self.assertEqual(str(len(body)), headers["Content-Length"])
            self.assertEqual(body if method == "GET" else b"", payload)

    def test_asset_get_and_head(self) -> None:
        self.check_asset(self.serve(""), "")

    def test_prefix_mounted_asset(self) -> None:
        self.check_asset(self.serve("https://tailplan.example.test/tailplan"), "/tailplan")

    def test_bad_asset_paths_return_404(self) -> None:
        address = self.serve("")
        for path in (
            "/assets/tailplan-mark.svg", "/assets/tailplan-mark.00000000.svg", "/assets/",
            "/assets/missing.12345678.css", "/assets/../tailplan_server.py",
            "/assets/%2e%2e/tailplan_server.py", "/assets/..%2ftailplan_server.py",
            "/assets/%2e%2e%2ftailplan_server.py", "/assets/./tailplan-mark.svg",
        ):
            for method in ("GET", "HEAD"):
                status, headers, payload = self.fetch(address, method, path)
                self.assertEqual(404, status, f"{method} {path}")
                self.assertNotIn(b"def ", payload)
                self.assertNotIn("immutable", headers.get("Cache-Control", ""))


if __name__ == "__main__":
    unittest.main()
