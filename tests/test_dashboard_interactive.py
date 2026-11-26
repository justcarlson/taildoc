"""Opt-in browser checks for the dashboard script: instant actions, search, keys, and the CSP nonce.

CI runs this file on Chromium and WebKit with TAILPLAN_BROWSER_TESTS=1.
"""
from __future__ import annotations

import os
import re

import pytest
from playwright.sync_api import expect

from auth_harness import TOKEN, AuthCase

pytestmark = pytest.mark.skipif(not os.getenv("TAILPLAN_BROWSER_TESTS"),
                                reason="Set TAILPLAN_BROWSER_TESTS=1 for browser checks")

DRAFTS = [
    ("alpha-plan", "Alpha plan", "Notes on the rollout of the quokka migration."),
    ("bravo-plan", "Bravo plan", "A budget for the next quarter."),
    ("charlie-plan", "Charlie plan", "Hiring steps and interview loops."),
    ("delta-plan", "Delta plan", "Incident review for the cache outage."),
]


class DashboardCase(AuthCase):
    use_oidc = False

    def runTest(self) -> None:  # unittest needs a method name; pytest drives this case.
        pass


@pytest.fixture
def dash(page):
    case = DashboardCase()
    case.setUp()
    try:
        case.ids = {}
        for slug, title, text in DRAFTS:
            case.ids[slug] = case.upload(f"<title>{title}</title><h1>{title}</h1><p>{text}</p>", slug=slug)["draftId"]
        cookie = case.bootstrap_session()
        name, value = cookie.split("=", 1)
        page.context.add_cookies([{"name": name, "value": value, "url": case.base}])
        case.errors = []
        page.on("pageerror", lambda error: case.errors.append(str(error)))
        page.on("console", lambda message: message.type == "error" and case.errors.append(message.text))
        yield case
    finally:
        case.doCleanups()


def open_dashboard(page, dash, query: str = ""):
    response = page.goto(f"{dash.base}/dashboard{query}")
    # The page CSP blocks eval, so wait on selectors, not on wait_for_function strings.
    page.wait_for_selector("html.js-ready", state="attached")
    page.evaluate("window.__marker = 1")
    return response


def row(page, dash, slug):
    return page.locator(f'section.group .draft-row[data-draft-id="{dash.ids[slug]}"]')


def api_draft(dash, slug) -> dict:
    status, body = dash.api(f"/api/drafts/{slug}", token=TOKEN)
    assert status == 200, body
    return body["draft"]


def same_page(page) -> bool:
    return page.evaluate("window.__marker") == 1


def test_script_loads_under_a_nonce_without_errors(page, dash):
    response = open_dashboard(page, dash)
    csp = response.headers["content-security-policy"]
    nonce = re.search(r"script-src 'nonce-([A-Za-z0-9_-]+)'", csp)[1]
    assert "connect-src 'self'" in csp and "font-src 'self'" in csp
    assert "unsafe-inline" not in csp.split("script-src", 1)[1].split(";", 1)[0]
    script = page.locator("script[type=module]")
    assert script.count() == 1
    assert script.evaluate("(s) => s.nonce") == nonce
    assert re.search(r"/assets/dashboard\.[0-9a-f]{8}\.js$", script.get_attribute("src"))
    assert page.locator(".draft-row .row-select:visible").count() == len(DRAFTS)
    # Each page load gets a new nonce.
    again = page.reload()
    assert nonce not in again.headers["content-security-policy"]
    viewer = page.request.get(f"{dash.base}/d/{dash.ids['alpha-plan']}/")
    assert "script-src 'none'" in viewer.headers["content-security-policy"]
    assert "nonce-" not in viewer.headers["content-security-policy"]
    assert not dash.errors


def test_star_does_not_navigate_or_jump_and_persists(page, dash):
    page.set_viewport_size({"width": 1280, "height": 500})
    open_dashboard(page, dash)
    target = row(page, dash, "delta-plan")
    target.scroll_into_view_if_needed()
    top = target.evaluate("(r) => r.getBoundingClientRect().top")
    favorites = page.locator('.tab[data-view="favorites"] .num')
    before = int(favorites.inner_text() or 0)
    target.locator(".star-form button").click()
    page.wait_for_selector(f'.pin[data-draft-id="{dash.ids["delta-plan"]}"]')
    assert target.get_attribute("data-favorite") == "1"
    assert int(favorites.inner_text()) == before + 1
    assert abs(target.evaluate("(r) => r.getBoundingClientRect().top") - top) <= 1
    assert same_page(page)
    page.wait_for_timeout(200)
    assert page.locator(".toast.error").count() == 0
    assert api_draft(dash, "delta-plan")["favorite"]
    page.reload()
    assert row(page, dash, "delta-plan").get_attribute("data-favorite") == "1"
    assert not dash.errors


def test_archive_then_undo(page, dash):
    open_dashboard(page, dash)
    target = row(page, dash, "bravo-plan")
    active = page.locator('.tab[data-view="active"] .num')
    before = int(active.inner_text())
    target.locator(".archive-form button").click()
    page.wait_for_selector(".toast button:has-text('Undo')")
    assert target.is_hidden()
    assert int(active.inner_text()) == before - 1
    assert api_draft(dash, "bravo-plan")["archived"]
    page.locator("body").press("z")
    page.wait_for_selector(".toast:has-text('Restored')")
    assert target.is_visible()
    assert int(active.inner_text()) == before
    assert not api_draft(dash, "bravo-plan")["archived"]
    assert same_page(page)
    assert not dash.errors


def test_search_filters_marks_and_updates_the_url(page, dash):
    open_dashboard(page, dash)
    page.locator("#dash-q").fill("quokka")
    expect(page).to_have_url(re.compile(r".*[?&]q=quokka"))
    page.wait_for_selector(".draft-row .row-desc mark")
    assert page.locator(".draft-row .row-desc mark").first.inner_text().lower() == "quokka"
    assert row(page, dash, "alpha-plan").is_visible()
    assert row(page, dash, "bravo-plan").is_hidden()
    assert page.locator(".shelf:visible").count() == 0
    # Esc clears the search, and every row comes back.
    page.locator("#dash-q").press("Escape")
    expect(page).not_to_have_url(re.compile(r".*[?&]q="))
    assert row(page, dash, "bravo-plan").is_visible()
    assert same_page(page)
    assert not dash.errors


@pytest.mark.parametrize("modifier", ["Control", "Meta"])
def test_palette_opens_with_k(page, dash, modifier):
    open_dashboard(page, dash)
    page.keyboard.press(f"{modifier}+k")
    palette = page.locator("dialog.palette")
    assert palette.evaluate("(d) => d.open")
    page.keyboard.type("charlie")
    options = palette.locator("li[role=option]")
    assert options.first.inner_text().startswith("Charlie plan")
    page.keyboard.press("Escape")
    assert not palette.evaluate("(d) => d.open")
    page.keyboard.press(f"{modifier}+k")
    page.keyboard.type("view archived")
    page.keyboard.press("Enter")
    page.wait_for_url(re.compile(r".*view=archived"))
    assert not dash.errors


def test_keys_move_star_and_show_help(page, dash):
    open_dashboard(page, dash)
    rows = page.locator("section.group .draft-row")
    page.keyboard.press("j")
    assert rows.nth(0).get_attribute("data-cursor") == "1"
    page.keyboard.press("j")
    assert rows.nth(1).get_attribute("data-cursor") == "1"
    assert rows.nth(0).get_attribute("data-cursor") is None
    page.keyboard.press("k")
    assert rows.nth(0).get_attribute("data-cursor") == "1"
    draft_id = rows.nth(0).get_attribute("data-draft-id")
    page.keyboard.press("f")
    page.wait_for_selector(f'.pin[data-draft-id="{draft_id}"]')
    assert rows.nth(0).get_attribute("data-favorite") == "1"
    page.keyboard.press("?")
    assert page.locator("dialog.shortcuts").evaluate("(d) => d.open")
    page.keyboard.press("Escape")
    page.keyboard.press("/")
    assert page.evaluate("document.activeElement.id") == "dash-q"
    assert same_page(page)
    assert not dash.errors


def test_bulk_archive_of_two(page, dash):
    open_dashboard(page, dash)
    row(page, dash, "alpha-plan").locator(".row-select").check()
    row(page, dash, "charlie-plan").locator(".row-select").check()
    bar = page.locator(".bulk-bar")
    assert bar.is_visible()
    assert bar.locator(".count").inner_text() == "2 selected"
    bar.get_by_role("button", name="Archive").click()
    page.wait_for_selector(".toast:has-text('Archived 2 drafts')")
    assert row(page, dash, "alpha-plan").is_hidden() and row(page, dash, "charlie-plan").is_hidden()
    assert bar.is_hidden()
    assert api_draft(dash, "alpha-plan")["archived"] and api_draft(dash, "charlie-plan")["archived"]
    assert not api_draft(dash, "bravo-plan")["archived"]
    assert same_page(page)
    assert not dash.errors


def test_theme_changes_without_reload_and_persists(page, dash):
    open_dashboard(page, dash)
    page.locator('.theme-switch button[value="dark"]').click()
    assert page.evaluate("document.documentElement.dataset.theme") == "dark"
    assert same_page(page)
    page.wait_for_timeout(200)
    page.reload()
    assert page.evaluate("document.documentElement.dataset.theme") == "dark"
    page.locator('.theme-switch button[value="system"]').click()
    page.wait_for_timeout(200)
    page.reload()
    assert page.evaluate("document.documentElement.dataset.theme") is None
    assert not dash.errors


def test_fits_a_phone_with_the_bulk_bar(page, dash):
    page.set_viewport_size({"width": 390, "height": 800})
    open_dashboard(page, dash)
    row(page, dash, "alpha-plan").locator(".row-select").check()
    assert page.locator(".bulk-bar").is_visible()
    assert page.evaluate("document.documentElement.scrollWidth") <= 390
    box = page.locator(".bulk-bar").bounding_box()
    assert box["x"] >= 0 and box["x"] + box["width"] <= 390
    page.keyboard.press("Control+k")
    assert page.evaluate("document.documentElement.scrollWidth") <= 390
    assert not dash.errors


def favorite(dash, slug, on=True):
    status, body = dash.api(f"/api/drafts/{slug}", "PATCH", {"favorite": on}, token=TOKEN)
    assert status == 200, body


def pin(page, dash, slug):
    return page.locator(f'.pin[data-draft-id="{dash.ids[slug]}"]')


def fail_route(page, pattern):
    page.route(pattern, lambda route: route.fulfill(status=500, content_type="application/json",
                                                    body='{"ok": false, "error": "Test failure."}'))


def real_errors(dash):
    # A forced 500 logs "Failed to load resource" in the console. Other errors are bugs.
    return [error for error in dash.errors if "Failed to load resource" not in error]


def test_unstar_from_the_shelf_removes_the_pin_and_holds_the_page(page, dash):
    favorite(dash, "alpha-plan")
    page.set_viewport_size({"width": 1280, "height": 420})
    open_dashboard(page, dash)
    assert pin(page, dash, "alpha-plan").is_visible()
    # Scroll so the shelf is at the top, then hold the first list section in place.
    page.evaluate("window.scrollTo(0, document.querySelector('.shelf').offsetTop - 8)")
    below = page.locator("section.group").first
    top = below.evaluate("(n) => n.getBoundingClientRect().top")
    count = page.locator('.tab[data-view="favorites"] .num')
    pin(page, dash, "alpha-plan").locator(".star-form button").click()
    expect(pin(page, dash, "alpha-plan")).to_have_count(0)
    expect(page.locator(".shelf")).to_be_hidden()
    assert row(page, dash, "alpha-plan").get_attribute("data-favorite") == "0"
    assert count.inner_text() == "0"
    assert abs(below.evaluate("(n) => n.getBoundingClientRect().top") - top) <= 1
    page.wait_for_timeout(200)
    assert not api_draft(dash, "alpha-plan")["favorite"]
    # A new star puts the pin back on the shelf.
    row(page, dash, "alpha-plan").locator(".star-form button").click()
    expect(pin(page, dash, "alpha-plan")).to_be_visible()
    assert same_page(page)
    assert not dash.errors


def test_a_failed_first_star_removes_the_new_pin(page, dash):
    open_dashboard(page, dash)
    fail_route(page, "**/favorite")
    row(page, dash, "charlie-plan").locator(".star-form button").click()
    page.wait_for_selector(".toast.error")
    expect(pin(page, dash, "charlie-plan")).to_have_count(0)
    expect(page.locator(".shelf")).to_be_hidden()
    assert row(page, dash, "charlie-plan").get_attribute("data-favorite") == "0"
    assert page.locator('.tab[data-view="favorites"] .num').inner_text() == "0"
    assert not real_errors(dash)


def test_unstar_on_favorites_hides_the_row_and_a_failure_restores_it(page, dash):
    favorite(dash, "alpha-plan")
    favorite(dash, "bravo-plan")
    open_dashboard(page, dash, "?view=favorites")
    alpha, bravo = row(page, dash, "alpha-plan"), row(page, dash, "bravo-plan")
    alpha.locator(".star-form button").click()
    expect(alpha).to_be_hidden()
    page.wait_for_timeout(200)
    assert not api_draft(dash, "alpha-plan")["favorite"]
    fail_route(page, "**/unfavorite")
    bravo.locator(".star-form button").click()
    page.wait_for_selector(".toast.error")
    expect(bravo).to_be_visible()
    assert bravo.get_attribute("data-favorite") == "1"
    assert api_draft(dash, "bravo-plan")["favorite"]
    assert same_page(page)
    assert not real_errors(dash)


def test_each_undo_button_undoes_its_own_archive(page, dash):
    open_dashboard(page, dash)
    bravo, charlie = row(page, dash, "bravo-plan"), row(page, dash, "charlie-plan")
    bravo.locator(".archive-form button").click()
    page.wait_for_selector(".toast:has-text('Archived “Bravo plan”')")
    charlie.locator(".archive-form button").click()
    page.wait_for_selector(".toast:has-text('Archived “Charlie plan”')")
    page.locator(".toast", has_text="Bravo plan").get_by_role("button", name="Undo").click()
    page.wait_for_selector(".toast:has-text('Restored “Bravo plan”')")
    expect(bravo).to_be_visible()
    expect(charlie).to_be_hidden()
    assert not api_draft(dash, "bravo-plan")["archived"]
    assert api_draft(dash, "charlie-plan")["archived"]
    # The z key still undoes the latest archive.
    page.locator("body").press("z")
    page.wait_for_selector(".toast:has-text('Restored “Charlie plan”')")
    expect(charlie).to_be_visible()
    assert not api_draft(dash, "charlie-plan")["archived"]
    assert not dash.errors


def test_archive_key_moves_the_cursor(page, dash):
    open_dashboard(page, dash)
    ids = page.locator("section.group .draft-row").evaluate_all("(rows) => rows.map((r) => r.dataset.draftId)")
    cursor = page.locator('.draft-row[data-cursor="1"]')
    page.keyboard.press("j")
    page.keyboard.press("e")
    expect(cursor).to_have_attribute("data-draft-id", ids[1])
    # On the last row, the cursor goes to the row above.
    for _ in ids:
        page.keyboard.press("j")
    expect(cursor).to_have_attribute("data-draft-id", ids[-1])
    page.keyboard.press("e")
    expect(cursor).to_have_attribute("data-draft-id", ids[-2])
    page.wait_for_timeout(200)
    assert api_draft(dash, next(s for s, i in dash.ids.items() if i == ids[0]))["archived"]
    assert not dash.errors


def test_the_bulk_bar_follows_the_filter(page, dash):
    open_dashboard(page, dash)
    row(page, dash, "alpha-plan").locator(".row-select").check()
    bar = page.locator(".bulk-bar")
    expect(bar).to_be_visible()
    page.locator("#dash-q").fill("bravo")
    expect(bar).to_be_hidden()
    page.locator("#dash-q").fill("")
    expect(bar).to_be_visible()
    expect(bar.locator(".count")).to_have_text("1 selected")
    assert not dash.errors


def test_is_archived_finds_a_draft_archived_on_this_page(page, dash):
    open_dashboard(page, dash)
    row(page, dash, "bravo-plan").locator(".archive-form button").click()
    page.wait_for_selector(".toast:has-text('Archived')")
    page.locator("#dash-q").fill("is:archived")
    page.wait_for_url(re.compile(r".*q=is%3Aarchived"))
    expect(row(page, dash, "bravo-plan")).to_be_visible()
    expect(row(page, dash, "alpha-plan")).to_have_count(0)
    assert not dash.errors


def test_search_restores_plain_descriptions_and_syncs_links(page, dash):
    dash.upload("<title>Echo plan</title><p>A wombat count.</p>", slug="echo-plan", description="Field notes.",
                tags=["fauna"])
    # A page loaded with ?q= shows a snippet. A tag search has no snippet, so the row shows its description.
    open_dashboard(page, dash, "?q=wombat")
    echo = page.locator('section.group .draft-row[data-title="Echo plan"]')
    expect(echo.locator(".row-desc mark")).to_have_count(1)
    page.locator("#dash-q").fill("tag:fauna")
    expect(page).to_have_url(re.compile(r".*[?&]q=tag%3Afauna"))
    expect(echo.locator(".row-desc mark")).to_have_count(0)
    expect(echo.locator(".row-desc")).to_have_text("Field notes.")
    assert same_page(page)
    open_dashboard(page, dash)
    expect(page.locator("a.clear")).to_have_count(0)
    page.locator("#dash-q").fill("budget")
    expect(page).to_have_url(re.compile(r".*[?&]q=budget"))
    expect(page.locator("a.clear")).to_be_visible()
    assert "q=budget" in page.locator("a.dir").get_attribute("href")
    assert all("q=budget" in href for href in page.locator(".facets a.pill").evaluate_all("(a) => a.map((n) => n.href)"))
    page.locator("#dash-q").press("Escape")
    expect(page).not_to_have_url(re.compile(r".*[?&]q="))
    expect(page.locator("a.clear")).to_be_hidden()
    assert "q=" not in page.locator("a.dir").get_attribute("href")
    assert not dash.errors


def test_palette_labels_facets_with_a_repo_filter(page, dash):
    dash.upload("<title>Echo plan</title><p>Echo.</p>", slug="echo-plan", tags=["ops"],
                metadata={"repoOrg": "acme", "repoName": "web"})
    open_dashboard(page, dash, "?repo=acme%2Fweb")
    assert page.locator('.facets a.pill[data-facet="tag"]').count() >= 1
    page.keyboard.press("Control+k")
    labels = page.locator("dialog.palette li[role=option] > span").all_inner_texts()
    assert "Tag: ops" in labels and "Repo: acme/web" in labels
    assert "Repo: ops" not in labels
    page.keyboard.type("zzzz-no-match")
    assert page.locator("#pal-list").evaluate("() => document.querySelector('dialog.palette input').hasAttribute('aria-activedescendant')") is False
    assert not dash.errors
