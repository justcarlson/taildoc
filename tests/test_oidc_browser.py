"""Opt-in browser checks for single sign-on, the view token fallback, and the Tailscale viewer.

CI runs this file on Chromium and WebKit. The token test removes every cookie
from draft requests, as a browser that blocks third-party cookies does.
"""
from __future__ import annotations

import os
import re
from urllib.parse import parse_qs, urlparse

import pytest

from auth_harness import AuthCase
from test_tailscale_viewer import ViewerCase

pytestmark = pytest.mark.skipif(not os.getenv("TAILPLAN_BROWSER_TESTS"),
                                reason="Set TAILPLAN_BROWSER_TESTS=1 for browser checks")

PAGES = {
    "index.html": '<title>Book</title><h1>Book</h1><a id="next" href="two.html">Two</a>',
    "two.html": '<title>Two</title><h1>Second</h1><a id="back" href="index.html#top">Back</a>',
}


class BrowserCase(AuthCase):
    viewer_mode = "session"

    def runTest(self) -> None:  # unittest needs a method name; pytest drives this case.
        pass


@pytest.fixture
def tailplan():
    case = BrowserCase()
    case.setUp()
    try:
        case.draft = case.upload(files=[{"path": path, "html": doc} for path, doc in PAGES.items()],
                                 workspace="team", visibility="workspace")
        yield case
    finally:
        case.doCleanups()


def sign_in(page, tailplan) -> str:
    draft_url = f"{tailplan.base}/d/{tailplan.draft['draftId']}/"
    page.goto(draft_url)
    assert urlparse(page.url).path == "/auth/sign-in"
    assert parse_qs(urlparse(page.url).query)["next"] == [f"/d/{tailplan.draft['draftId']}/"]
    page.get_by_role("link", name="Continue with single sign-on").click()
    page.wait_for_url(draft_url)
    assert page.locator("h1").inner_text() == "Book"
    return draft_url


def test_single_sign_on_returns_to_the_draft(page, tailplan):
    sign_in(page, tailplan)
    names = {cookie["name"]: cookie for cookie in page.context.cookies()}
    assert names["tailplan_session"]["httpOnly"]
    assert names["tailplan_session"]["sameSite"] == "Lax"
    assert "tailplan_oidc_flow" not in names or names["tailplan_oidc_flow"]["value"] == ""
    assert tailplan.events("auth.login")[-1]["auth_method"] == "oidc"


def test_view_token_carries_reads_when_cookies_are_blocked(page, tailplan):
    sign_in(page, tailplan)
    href = page.locator("#next").get_attribute("href")
    assert re.fullmatch(r"two\.html\?tv=[A-Za-z0-9_.-]+", href)
    # Drop every cookie before each step, as a browser that blocks third-party cookies does.
    page.context.clear_cookies()
    page.locator("#next").click()
    page.wait_for_url(re.compile(r".*/two\.html\?tv="))
    assert page.locator("h1").inner_text() == "Second"
    page.context.clear_cookies()
    back = page.locator("#back").get_attribute("href")
    assert re.fullmatch(r"index\.html\?tv=[A-Za-z0-9_.-]+#top", back)
    page.locator("#back").click()
    page.wait_for_url(re.compile(r".*/index\.html\?tv=.*#top"))
    assert page.locator("h1").inner_text() == "Book"

    # The token is the only credential here. Without it, the reader must sign in.
    page.context.clear_cookies()
    page.goto(f"{tailplan.base}/d/{tailplan.draft['draftId']}/two.html")
    assert urlparse(page.url).path == "/auth/sign-in"


def test_view_token_stops_after_sign_out(page, tailplan):
    draft_url = sign_in(page, tailplan)
    href = page.locator("#next").get_attribute("href")
    page.goto(f"{tailplan.base}/dashboard")
    page.get_by_role("button", name="Sign out").click()
    page.wait_for_url(re.compile(r".*/$"))
    page.context.clear_cookies()
    page.goto(draft_url + href)
    assert urlparse(page.url).path == "/auth/sign-in"


class PassiveViewerCase(ViewerCase):
    def runTest(self) -> None:  # unittest needs a method name; pytest drives this case.
        pass


@pytest.fixture
def tailnet():
    case = PassiveViewerCase()
    case.setUp()
    try:
        yield case
    finally:
        case.tearDown()


def test_tailscale_header_renders_a_shared_draft_with_zero_clicks(page, tailnet):
    shared = tailnet.draft("Shared plan")
    unshared = tailnet.draft("Owner only")
    tailnet.store.add_grant(shared, "email", "zachvivier@gmail.com", "viewer", "local")
    base = f"http://127.0.0.1:{tailnet.proxy.server_port}"
    # Tailscale Serve adds this header for a tailnet user or a sharee.
    page.set_extra_http_headers({"Tailscale-User-Login": "zachvivier@gmail.com"})
    response = page.goto(f"{base}/d/{shared}")
    assert response.status == 200
    assert urlparse(page.url).path == f"/d/{shared}"
    assert page.locator("h1").inner_text() == "Shared plan"
    assert page.context.cookies() == []
    assert page.goto(f"{base}/d/{unshared}").status == 404
    # The header never opens the dashboard. It shows the usual sign-in form.
    page.goto(f"{base}/dashboard")
    assert "Owner only" not in page.content()
    assert "tailplan_session" not in {cookie["name"] for cookie in page.context.cookies()}
    assert tailnet.state() == (0, 0, 0)


def test_tagged_node_without_the_header_falls_back_to_sign_in(page, tailnet):
    draft_id = tailnet.draft("Shared plan")
    page.goto(f"http://127.0.0.1:{tailnet.proxy.server_port}/d/{draft_id}")
    assert urlparse(page.url).path == "/auth/sign-in"
