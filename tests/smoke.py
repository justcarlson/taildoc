#!/usr/bin/env python3
from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PORT = int(os.environ.get("TAILPLAN_TEST_PORT", "19127"))
TOKEN = "test-token"


def urlopen(
    url: str,
    *,
    data: dict | None = None,
    token: str | None = None,
    idempotency_key: str | None = None,
) -> tuple[int, dict[str, str], str]:
    body = (
        None
        if data is None
        else json.dumps(data, ensure_ascii=False).encode("utf-8")
    )
    headers = {}
    if data is not None:
        headers["Content-Type"] = "application/json"
    if token is not None:
        headers["Authorization"] = f"Bearer {token}"
    if idempotency_key is not None:
        headers["Idempotency-Key"] = idempotency_key
    request = urllib.request.Request(
        url,
        data=body,
        headers=headers,
        method="POST" if data is not None else "GET",
    )
    try:
        with urllib.request.urlopen(request, timeout=5) as response:
            return response.status, dict(response.headers.items()), response.read().decode()
    except urllib.error.HTTPError as exc:
        return exc.code, dict(exc.headers.items()), exc.read().decode()


def assert_document_headers(headers: dict[str, str]) -> None:
    assert headers["Cache-Control"] == "no-store", headers
    assert headers["X-Content-Type-Options"] == "nosniff", headers
    assert headers["Referrer-Policy"] == "no-referrer", headers
    assert headers["X-Frame-Options"] == "DENY", headers
    csp = headers["Content-Security-Policy"]
    for directive in (
        "default-src 'none'",
        "script-src 'none'",
        "style-src 'unsafe-inline'",
        "img-src https: data:",
        "connect-src 'none'",
        "frame-src 'none'",
        "frame-ancestors 'none'",
        "base-uri 'none'",
        "form-action 'none'",
    ):
        assert directive in csp, (directive, csp)


with tempfile.TemporaryDirectory() as td:
    tmp = Path(td)
    token_file = tmp / "token"
    data_dir = tmp / "data"
    token_file.write_text(TOKEN + "\n")
    token_file.chmod(0o600)
    data_dir.mkdir()
    proc = subprocess.Popen(
        [
            sys.executable,
            str(ROOT / "tailplan_server.py"),
            "--host",
            "127.0.0.1",
            "--port",
            str(PORT),
            "--data-dir",
            str(data_dir),
            "--token-file",
            str(token_file),
            "--base-url",
            f"http://127.0.0.1:{PORT}",
        ],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    try:
        base = f"http://127.0.0.1:{PORT}"
        last_error: urllib.error.URLError | None = None
        for _ in range(50):
            try:
                status, _headers, text = urlopen(base + "/healthz")
                if status == 200 and '"ok": true' in text:
                    break
            except urllib.error.URLError as exc:
                last_error = exc
            if proc.poll() is not None:
                stderr = proc.stderr.read() if proc.stderr is not None else ""
                raise RuntimeError(f"server exited before becoming healthy: {stderr}")
            time.sleep(0.1)
        else:
            raise RuntimeError("server did not become healthy") from last_error

        unicode_payload = "🚀" * 50_000
        first_document = (
            "<!doctype html><html><head><title>Tailplan Smoke 一</title></head><body>"
            f"<h1>Version One</h1><p>{unicode_payload}</p>"
            '<a title="1 > 0" href="https://example.com/path?q=1>0">Example</a>'
            "</body></html>"
        )
        first_upload = {"html": first_document, "filename": "smoke-一.html"}
        status, _headers, text = urlopen(
            base + "/api/uploads",
            data=first_upload,
            token=TOKEN,
            idempotency_key="smoke-create-1",
        )
        assert status == 201, (status, text)
        first_result = json.loads(text)
        draft_id = first_result["draftId"]
        assert first_result["versionNumber"] == 1, first_result
        assert first_result["replayed"] is False, first_result

        status, _headers, text = urlopen(
            base + "/api/uploads",
            data=first_upload,
            token=TOKEN,
            idempotency_key="smoke-create-1",
        )
        replay = json.loads(text)
        assert status == 200, (status, text)
        assert replay["draftId"] == draft_id, replay
        assert replay["versionNumber"] == 1, replay
        assert replay["replayed"] is True, replay

        status, _headers, conflict = urlopen(
            base + "/api/uploads",
            data={"html": "<title>Different</title>"},
            token=TOKEN,
            idempotency_key="smoke-create-1",
        )
        assert status == 409, (status, conflict)

        second_document = (
            "<!doctype html><html><head><title>Tailplan Smoke Two</title></head>"
            "<body><h1>Version Two</h1></body></html>"
        )
        status, _headers, text = urlopen(
            base + "/api/uploads",
            data={
                "html": second_document,
                "filename": "smoke-two.html",
                "draftId": draft_id,
            },
            token=TOKEN,
        )
        assert status == 200, (status, text)
        assert json.loads(text)["versionNumber"] == 2, text

        routes = {
            f"/d/{draft_id}": "Version Two",
            f"/d/{draft_id}/content": "Version Two",
            f"/d/{draft_id}/v/1": "Version One",
            f"/d/{draft_id}/v/1/content": "Version One",
        }
        views = {}
        for route, expected_text in routes.items():
            status, headers, view = urlopen(base + route)
            assert status == 200, (route, status)
            assert view.startswith("<!doctype html>"), route
            assert expected_text in view, route
            assert "<iframe" not in view.lower(), route
            assert "srcdoc" not in view.lower(), route
            assert_document_headers(headers)
            views[route] = view

        assert "Version One" not in views[f"/d/{draft_id}"], "latest route is stale"
        historical_view = views[f"/d/{draft_id}/v/1"]
        assert "🚀" in historical_view
        assert 'title="1 > 0"' in historical_view
        assert 'href="https://example.com/path?q=1>0"' in historical_view
        assert 'target="_blank"' in historical_view
        assert 'rel="noopener noreferrer"' in historical_view

        status, _headers, bad = urlopen(
            base + "/api/uploads",
            data={"html": "<script>alert(1)</script>", "filename": "bad.html"},
            token=TOKEN,
        )
        assert status == 422, (status, bad)
        print(f"smoke ok: draft {draft_id}; 4 direct viewer routes verified")
    finally:
        proc.terminate()
        try:
            proc.wait(timeout=2)
        except subprocess.TimeoutExpired:
            proc.kill()
            proc.wait(timeout=2)
