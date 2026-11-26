"""Checks for the Geist dashboard pages, the font assets, and web readiness."""
from __future__ import annotations

import json
import re
import unittest
from datetime import UTC, datetime, timedelta
from http.client import HTTPConnection
from pathlib import Path
from tempfile import TemporaryDirectory
from threading import Thread
from unittest.mock import patch

import tailplan_server as server
from tailplan_web import assets, render

BASE = "https://tailnet.example/tailplan"
SESSION = {"csrf": "c" * 40, "accountName": "someone@example.com"}


def stamp(**delta: float) -> str:
    return (datetime.now(UTC) - timedelta(**delta)).strftime("%Y-%m-%dT%H:%M:%SZ")


def draft(draft_id: str, title: str, **extra) -> dict:
    row = {
        "draftId": draft_id, "slug": None, "title": title, "description": "", "tags": [],
        "favorite": False, "favoritedAt": None, "archived": False, "versionCount": 1,
        "latestVersionNumber": 1, "updatedAt": stamp(hours=1), "disabled": False,
        "repoHost": None, "repoOrg": None, "repoName": None, "workspace": "team", "visibility": "private",
        "shareUrl": f"{BASE}/d/{draft_id}/share",
    }
    row.update(extra)
    return row


def state(drafts: list[dict], **extra) -> dict:
    value = {
        "view": "active", "q": "", "sort": "updated", "direction": "desc", "group": "time", "tag": "",
        "repo": "", "counts": {"active": len(drafts), "favorites": 0, "living": 0, "shared": 0, "archived": 0},
        "tags": [], "repos": [], "suggestedTags": {}, "favorites": [], "drafts": drafts,
    }
    value.update(extra)
    return value


def sections(page: str) -> list[str]:
    return re.findall(r'<div class="section-head"><h2>([^<]+)<span', page)


class ThemeShell(unittest.TestCase):
    def test_theme_attribute_follows_the_theme(self) -> None:
        for theme, attr in (("dark", ' data-theme="dark"'), ("light", ' data-theme="light"')):
            page = render.render_dashboard(BASE, SESSION, [], theme=theme)
            self.assertIn(f"<html lang=\"en\"{attr}>", page)
            self.assertIn(f'value="{theme}" aria-pressed="true"', page)
        page = render.render_dashboard(BASE, SESSION, [], theme="system")
        self.assertIn('<html lang="en">', page)
        self.assertIn('value="system" aria-pressed="true"', page)
        self.assertIn('<html lang="en">', render.render_dashboard(BASE, SESSION, [], theme="bogus"))

    def test_state_theme_applies_when_no_theme_argument(self) -> None:
        page = render.render_dashboard(BASE, SESSION, [], state=state([], theme="dark"))
        self.assertIn(' data-theme="dark"', page)

    def test_css_has_both_palettes_and_geist_fonts(self) -> None:
        css = render.WEB_CSS
        for token in ("#0a0a0a", "#ededed", "#3291ff", "#ffffff", "#171717", "#0070f3",
                      "prefers-color-scheme:dark", ":root[data-theme=dark]", ":root:not([data-theme=light])",
                      "tabular-nums", "-.03em"):
            self.assertIn(token, css)
        self.assertNotIn("gradient", css)
        page = render.web_page("T", "", BASE)
        for name in ("geist-sans.woff2", "geist-mono.woff2"):
            self.assertIn(assets.asset_url(BASE, name), page)
        self.assertEqual(2, page.count("font-display:swap"))

    def test_every_management_page_uses_the_shell(self) -> None:
        detail = {"draft": draft("d1", "Plan", rawUrl=f"{BASE}/d/d1/raw", disabledReason=""), "versions": []}
        pages = [
            render.render_dashboard(BASE, SESSION, [], theme="dark"),
            render.render_detail(BASE, SESSION, detail, theme="dark"),
            render.render_keys(BASE, SESSION, [], theme="dark"),
            render.render_key(BASE, SESSION, {"apiKey": {"name": "k"}, "token": "tp_x"}, theme="dark"),
            render.render_activate(BASE, SESSION, None, None, theme="dark"),
            render.render_login(BASE, "c" * 40, "/dashboard", True, theme="dark"),
        ]
        for page in pages:
            self.assertIn(' data-theme="dark"', page)
            self.assertIn('class="top"', page)
        for page in pages[:-1]:
            self.assertIn(">Sign out</button>", page)
        for page in pages[:3] + pages[4:5]:
            self.assertIn(f'action="{BASE}/dashboard/theme"', page)
        self.assertNotIn("Sign out", pages[-1])

    def test_one_time_key_page_has_no_theme_switch(self) -> None:
        page = render.render_key(BASE, SESSION, {"apiKey": {"name": "k"}, "token": "tp_secret"})
        self.assertIn('value="tp_secret"', page)
        self.assertNotIn("/dashboard/theme", page)
        self.assertIn(">Sign out</button>", page)

    def test_activation_theme_switch_keeps_the_code(self) -> None:
        page = render.render_activate(BASE, SESSION, "ABCD-EFGH", None)
        self.assertIn('name="next" value="/cli/activate?code=ABCD-EFGH"', page)
        page = render.render_activate(BASE, SESSION, "A&B", None)
        self.assertIn('name="next" value="/cli/activate?code=A%26B"', page)
        page = render.render_activate(BASE, SESSION, None, None)
        self.assertIn('name="next" value="/cli/activate"', page)

    def test_fonts_fall_back_when_an_asset_is_missing(self) -> None:
        with patch.dict(assets._SOURCE_TO_HASHED, clear=True):
            page = render.web_page("T", "", BASE)
        self.assertNotIn("@font-face", page)
        self.assertIn("<style>", page)

    def test_danger_colors_meet_wcag_aa(self) -> None:
        def luminance(hex_color: str) -> float:
            channels = [int(hex_color[i:i + 2], 16) / 255 for i in (1, 3, 5)]
            linear = [c / 12.92 if c <= 0.03928 else ((c + 0.055) / 1.055) ** 2.4 for c in channels]
            return 0.2126 * linear[0] + 0.7152 * linear[1] + 0.0722 * linear[2]

        def ratio(a: str, b: str) -> float:
            high, low = sorted((luminance(a), luminance(b)), reverse=True)
            return (high + 0.05) / (low + 0.05)

        for tokens, bg in ((render._LIGHT, "#ffffff"), (render._DARK, "#0a0a0a")):
            values = dict(part.split(":", 1) for part in tokens.split(";") if part.startswith("--"))
            self.assertGreaterEqual(ratio(values["--danger"], bg), 4.5)
            self.assertGreaterEqual(ratio(values["--danger-bg"], "#ffffff"), 4.5)


class Dashboard(unittest.TestCase):
    def test_default_state_tolerates_minimal_drafts(self) -> None:
        minimal = {"draftId": "d1", "title": "Minimal", "description": "", "shareUrl": f"{BASE}/d/d1/share",
                   "disabled": False, "latestVersionNumber": 1, "versionCount": 1, "updatedAt": "2026-11-14T10:00:00Z"}
        page = render.render_dashboard(BASE, SESSION, [minimal])
        self.assertIn('id="d-d1"', page)
        self.assertIn('id="dashboard-root"', page)
        self.assertIn("<h1>My drafts", page)

    def test_view_tabs_carry_counts_and_keep_sort_and_group(self) -> None:
        value = state([], sort="title", direction="asc", group="repo",
                      counts={"active": 3, "favorites": 1, "living": 2, "shared": 4, "archived": 5})
        page = render.render_dashboard(BASE, SESSION, [], state=value)
        for label, count in (("Active", 3), ("Favorites", 1), ("Living", 2), ("Shared", 4), ("Archived", 5)):
            self.assertRegex(page, rf'>{label}<span class="num">{count}</span></a>')
        self.assertIn(f'href="{BASE}/dashboard?view=archived&amp;sort=title&amp;group=repo"', page)
        self.assertIn(f'href="{BASE}/dashboard?sort=title&amp;group=repo" aria-current="page"', page)

    def test_time_groups_skip_empty_buckets(self) -> None:
        rows = [draft("a1", "Now", updatedAt=stamp(seconds=0)), draft("b1", "Old", updatedAt=stamp(days=90))]
        page = render.render_dashboard(BASE, SESSION, rows, state=state(rows))
        self.assertEqual(["Today", "Earlier"], sections(page))

    def test_tag_repo_and_flat_groups(self) -> None:
        rows = [draft("a1", "A", tags=["dawn"]), draft("b1", "B", tags=["dawn", "ops"]),
                draft("c1", "C", tags=["ops"], repoOrg="org", repoName="repo"), draft("e1", "E")]
        page = render.render_dashboard(BASE, SESSION, rows, state=state(rows, group="tag"))
        self.assertEqual(["dawn", "ops", "Untagged"], sections(page))
        page = render.render_dashboard(BASE, SESSION, rows, state=state(rows, group="repo"))
        self.assertEqual(["org/repo", "No repository"], sections(page))
        page = render.render_dashboard(BASE, SESSION, rows, state=state(rows, group="none"))
        self.assertEqual([], sections(page))
        self.assertEqual(4, page.count('class="draft-row'))
        page = render.render_dashboard(BASE, SESSION, rows, state=state(rows))
        self.assertNotIn("No repository", page)

    def test_row_data_attributes_and_owner_actions(self) -> None:
        row = draft("a1", "Plan <b>", tags=["dawn"], favorite=True, versionCount=3, latestVersionNumber=3,
                    slug="plan")
        page = render.render_dashboard(BASE, SESSION, [row], state=state([row], q="plan", sort="relevance"))
        self.assertIn('data-draft-id="a1" data-title="Plan &lt;b&gt;" data-tags="dawn" data-favorite="1"', page)
        self.assertIn('data-s="plan &lt;b&gt; plan dawn"', page)
        self.assertIn('class="row-select"', page)
        self.assertIn(f'data-base="{BASE}" data-csrf="{"c" * 40}" data-view="active" data-sort="relevance"', page)
        self.assertIn(f'action="{BASE}/dashboard/drafts/a1/unfavorite"', page)
        self.assertIn(f'action="{BASE}/dashboard/drafts/a1/archive"', page)
        self.assertIn('name="next" value="/dashboard?q=plan&amp;sort=relevance"', page)
        self.assertIn('<span class="ver multi" title="3 versions">v3</span>', page)
        self.assertIn('target="_blank" rel="noopener noreferrer">Plan &lt;b&gt;</a>', page)
        self.assertNotIn("versions</span>", page.replace('title="3 versions"', ""))
        self.assertIn('class="badge ws" title="Workspace">team', page)
        self.assertIn('class="badge scope" title="Visibility">private', page)

    def test_archived_rows_offer_unarchive(self) -> None:
        row = draft("a1", "Old", archived=True)
        page = render.render_dashboard(BASE, SESSION, [row], state=state([row], view="archived"))
        self.assertIn(f'action="{BASE}/dashboard/drafts/a1/unarchive"', page)
        self.assertIn(">Unarchive</button>", page)

    def test_favorites_shelf_only_on_unfiltered_active_view(self) -> None:
        rows = [draft(f"f{i}x", f"Fav {i}", favorite=True) for i in range(10)]
        value = state(rows, favorites=rows, counts={"active": 10, "favorites": 10})
        page = render.render_dashboard(BASE, SESSION, rows, state=value)
        self.assertIn("<h2>Favorites</h2>", page)
        self.assertIn(f'href="{BASE}/dashboard?view=favorites">View all <span class="num">10</span>', page)
        self.assertEqual(8, page.count('<article class="pin" '))
        for change in ({"q": "x"}, {"view": "favorites"}, {"tag": "dawn"}):
            self.assertNotIn("<h2>Favorites</h2>", render.render_dashboard(BASE, SESSION, rows,
                                                                            state={**value, **change}))

    def test_suggested_tag_accept_form(self) -> None:
        rows = [draft("a1", "Dawn notes"), draft("b1", "Tagged", tags=["x"])]
        value = state(rows, suggestedTags={"a1": "dawn-notes", "b1": "never"})
        page = render.render_dashboard(BASE, SESSION, rows, state=value)
        self.assertIn(f'action="{BASE}/dashboard/drafts/a1/tags"', page)
        self.assertIn('name="add_tag" value="dawn-notes"', page)
        self.assertIn(">+ dawn-notes</button>", page)
        self.assertNotIn("never", page)

    def test_snippet_html_is_kept_and_description_is_escaped(self) -> None:
        rows = [draft("a1", "A", description="<i>desc</i>", snippet="a &lt;b&gt; <mark>hit</mark>"),
                draft("b1", "B", description="<i>desc</i>")]
        page = render.render_dashboard(BASE, SESSION, rows, state=state(rows, q="hit"))
        self.assertIn('<p class="row-desc">a &lt;b&gt; <mark>hit</mark></p>', page)
        self.assertIn('<p class="row-desc">&lt;i&gt;desc&lt;/i&gt;</p>', page)
        self.assertIn('<option value="relevance"', page)
        self.assertIn(f'href="{BASE}/dashboard">Clear</a>', page)

    def test_shared_view_has_no_owner_forms(self) -> None:
        rows = [draft("s1", "Roadmap", visibility="workspace")]
        value = state(rows, view="shared", counts={"shared": 1})
        page = render.render_dashboard(BASE, SESSION, [], shared=rows, state=value)
        self.assertIn('class="draft-row shared"', page)
        self.assertIn("Roadmap", page)
        for owner_only in ("/favorite", "/archive", "/tags", 'class="facets"'):
            self.assertNotIn(owner_only, page)

    def test_active_view_lists_shared_with_me(self) -> None:
        shared = [draft("s1", "Roadmap")]
        page = render.render_dashboard(BASE, SESSION, [], shared=shared)
        self.assertIn("Shared with me", page)
        self.assertIn("Roadmap", page)

    def test_compact_views_menu_keeps_the_current_view_in_the_third_slot(self) -> None:
        page = render.render_dashboard(BASE, SESSION, [], state=state([]))
        self.assertIn('<details class="views-more"><summary aria-label="More views"', page)
        self.assertIn('class="tab" data-view="shared" href="', page)
        self.assertRegex(page, r'data-view="shared" href="[^"]*" data-more>')
        self.assertRegex(page, r'data-view="archived" href="[^"]*" data-more>')
        self.assertNotRegex(page, r'data-view="living" href="[^"]*" data-more>')
        self.assertIn('<a class="more-view" data-more-view="shared"', page)
        page = render.render_dashboard(BASE, SESSION, [], state=state([], view="archived"))
        self.assertRegex(page, r'data-view="archived" href="[^"]*" aria-current="page">')
        self.assertRegex(page, r'data-view="living" href="[^"]*" data-more>')
        self.assertIn('<a class="more-view" data-more-view="living"', page)
        self.assertNotIn('data-more-view="archived"', page)
        for view in ("active", "favorites", "living", "shared", "archived"):
            self.assertEqual(1, page.count(f'class="tab" data-view="{view}"'))

    def test_filters_menu_wraps_the_controls_and_counts_active_filters(self) -> None:
        page = render.render_dashboard(BASE, SESSION, [], state=state([]))
        self.assertIn('<details class="filters-menu"><summary aria-label="Filters" title="Filters"', page)
        self.assertNotIn('class="filters-count"', page)
        sheet = page.split('<div class="filters-sheet" id="dash-filters">', 1)[1].split('</form>', 1)[0]
        for hook in ('name="sort"', 'name="group"', 'class="button secondary dir"', '<button class="apply" type="submit">'):
            self.assertIn(hook, sheet)
        tags = [{"tag": "dawn", "count": 2}]
        page = render.render_dashboard(BASE, SESSION, [], state=state(
            [], tags=tags, tag="dawn", group="tag", sort="title", direction="desc"))
        self.assertIn('aria-label="Filters, 4 active"', page)
        self.assertIn('<span class="filters-count">4</span>', page)
        self.assertIn('<div class="sheet-facets">', page)
        self.assertEqual(1, page.count('<div class="facets">'))
        page = render.render_dashboard(BASE, SESSION, [], state=state([], q="x", sort="relevance"))
        self.assertNotIn('class="filters-count"', page)

    def test_theme_color_matches_the_page_background(self) -> None:
        self.assertIn("html{-webkit-text-size-adjust:100%;text-size-adjust:100%;background:var(--bg)", render.WEB_CSS)
        page = render.render_dashboard(BASE, SESSION, [], theme="system")
        self.assertIn('<meta name="theme-color" media="(prefers-color-scheme: light)" content="#ffffff">', page)
        self.assertIn('<meta name="theme-color" media="(prefers-color-scheme: dark)" content="#0a0a0a">', page)
        for theme, color in (("light", "#ffffff"), ("dark", "#0a0a0a")):
            page = render.render_dashboard(BASE, SESSION, [], theme=theme)
            self.assertEqual(1, page.count('name="theme-color"'))
            self.assertIn(f'<meta name="theme-color" content="{color}">', page)
        self.assertIn("24px calc(64px + env(safe-area-inset-bottom, 0px))}", render.WEB_CSS)

    def test_search_toolbar_is_a_get_form(self) -> None:
        page = render.render_dashboard(BASE, SESSION, [], state=state([], tag="dawn", view="living"))
        self.assertIn(f'<form class="toolbar" method="get" action="{BASE}/dashboard" role="search">', page)
        self.assertIn('<input type="hidden" name="view" value="living">', page)
        self.assertIn('<input type="hidden" name="tag" value="dawn">', page)
        self.assertIn("<kbd", page)
        self.assertIn('<option value="none">Flat</option>', page)
        self.assertIn('class="button secondary dir" href="https://tailnet.example/tailplan/dashboard?view=living&amp;tag=dawn&amp;dir=asc"', page)
        self.assertNotIn('name="dir"', page)

    def test_updated_sort_is_empty_without_a_query(self) -> None:
        page = render.render_dashboard(BASE, SESSION, [], state=state([]))
        self.assertIn('<option value="" selected>Updated</option>', page)
        self.assertNotIn('value="relevance"', page)
        page = render.render_dashboard(BASE, SESSION, [], state=state([], q="x", sort="updated"))
        self.assertIn('<option value="updated" selected>Updated</option>', page)
        self.assertIn('<option value="relevance">Relevance</option>', page)

    def test_one_dir_value_per_submit(self) -> None:
        page = render.render_dashboard(BASE, SESSION, [], state=state([], direction="asc"))
        self.assertEqual(1, page.count('name="dir"'))
        self.assertIn(f'<a class="button secondary dir" href="{BASE}/dashboard" ', page)
        self.assertNotIn('<button class="secondary dir"', page)

    def test_external_link_refuses_credentials_and_escapes_class(self) -> None:
        for url in ("https://:secret@host.example/p", "https://user@host.example/p",
                    "https://user:pw@host.example/p", "javascript:alert(1)"):
            self.assertEqual("label", render.external_link(url, "label"))
        link = render.external_link("https://host.example/p", "x", css='a" onclick="b')
        self.assertIn('class="a&quot; onclick=&quot;b"', link)

    def test_snippet_with_raw_html_is_escaped(self) -> None:
        rows = [draft("a1", "A", snippet='<img src=x onerror=alert(1)> <mark>hit</mark>')]
        page = render.render_dashboard(BASE, SESSION, rows, state=state(rows, q="hit"))
        self.assertIn('&lt;img src=x onerror=alert(1)&gt; &lt;mark&gt;hit&lt;/mark&gt;', page)
        self.assertNotIn("<img src=x", page)

    def test_favorite_cards_carry_draft_ids(self) -> None:
        rows = [draft("f1x", "Fav", favorite=True)]
        page = render.render_dashboard(BASE, SESSION, rows, state=state(rows, favorites=rows))
        self.assertIn('<article class="pin" data-draft-id="f1x" data-favorite="1" data-title="Fav"', page)
        self.assertIn(f'data-share-url="{BASE}/d/f1x/share" data-detail-url="{BASE}/dashboard/drafts/f1x">', page)
        self.assertIn('<form class="star-form" data-draft-id="f1x" method="post"', page)

    def test_script_hooks_on_rows_tabs_and_forms(self) -> None:
        rows = [draft("a1", "Alpha", versionCount=3, tags=[]), draft("b2", "Beta", archived=True)]
        page = render.render_dashboard(BASE, SESSION, rows, state=state(rows, suggestedTags={"a1": "roadmap"}))
        self.assertIn('data-versions="3"', page)
        self.assertIn('<a class="tab" data-view="favorites"', page)
        self.assertIn('<form class="suggest-form" method="post"', page)
        self.assertIn('<form class="archive archive-form" method="post"', page)

    def test_relevance_needs_a_ranked_query(self) -> None:
        page = render.render_dashboard(BASE, SESSION, [], state=state([], q="tag:x", ranked=False))
        self.assertNotIn("Relevance", page)
        self.assertIn('<option value="" selected>Updated</option>', page)
        page = render.render_dashboard(BASE, SESSION, [], state=state([], q="x", sort="relevance", ranked=True))
        self.assertIn('<option value="relevance" selected>Relevance</option>', page)

    def test_shared_rows_search_without_tags(self) -> None:
        shared = [draft("s1", "Shared plan", tags=["ownertag"])]
        page = render.render_dashboard(BASE, SESSION, [], shared=shared, state=state(shared, view="shared"))
        self.assertIn('data-s="shared plan"', page)
        self.assertNotIn("ownertag", page)

    def test_script_tag_needs_a_nonce_and_the_asset(self) -> None:
        url = assets.asset_url(BASE, "dashboard.js")
        page = render.render_dashboard(BASE, SESSION, [], script_nonce="n0nce-_")
        self.assertIn(f'<script type="module" nonce="n0nce-_" src="{url}" defer></script></head>', page)
        self.assertNotIn("<script", render.render_dashboard(BASE, SESSION, []))
        for page in (render.render_keys(BASE, SESSION, [], script_nonce="n1"),
                     render.render_activate(BASE, SESSION, None, None, script_nonce="n1")):
            self.assertIn('nonce="n1"', page)
        with patch.dict(assets._SOURCE_TO_HASHED, clear=True):
            self.assertNotIn("<script", render.render_dashboard(BASE, SESSION, [], script_nonce="n2"))

    def test_base_url_is_escaped_once(self) -> None:
        page = render.render_dashboard("https://h.example/a&b", SESSION, [], state=state([], q="x"))
        self.assertIn('href="https://h.example/a&amp;b/dashboard">Clear</a>', page)
        self.assertNotIn("&amp;amp;", page)


class Ids(unittest.TestCase):
    def test_sharing_and_key_ids_are_escaped(self) -> None:
        sharing = {"draftId": 'd"1', "workspace": "team", "visibility": "private", "allowedVisibilities": ["private"],
                   "shareUrl": f"{BASE}/d/x/share", "publicUrl": f"{BASE}/d/x", "grants": []}
        self.assertIn("/dashboard/drafts/d&quot;1/sharing", render.render_sharing(BASE, SESSION, sharing))
        key = {"id": 'k"1', "name": "n", "createdAt": "2026-11-14T10:00:00Z", "lastUsedAt": None}
        self.assertIn("/cli/auth/keys/k&quot;1/revoke", render.render_keys(BASE, SESSION, [key]))

    def test_no_keys_shows_a_note_not_a_table(self) -> None:
        page = render.render_keys(BASE, SESSION, [])
        self.assertIn('<p class="muted">No active API keys.</p>', page)
        self.assertNotIn("<table", page)


class Detail(unittest.TestCase):
    def test_detail_has_star_archive_and_tag_editor(self) -> None:
        row = draft("d1", "Plan", tags=["dawn"], rawUrl=f"{BASE}/d/d1/raw", disabledReason="")
        version = {"versionNumber": 1, "createdAt": "2026-11-14T10:00:00Z", "fileSize": 10}
        page = render.render_detail(BASE, SESSION, {"draft": row, "versions": [version]})
        for part in ("/dashboard/drafts/d1/favorite", "/dashboard/drafts/d1/archive",
                     'name="remove_tag" value="dawn"', 'name="add_tag"', "Version history",
                     'data-label="Version"', 'name="next" value="/dashboard/drafts/d1"'):
            self.assertIn(part, page)


class FontAssets(unittest.TestCase):
    def test_fonts_have_flat_hashed_urls(self) -> None:
        for name in ("geist-sans.woff2", "geist-mono.woff2"):
            url = assets.asset_url("", name)
            self.assertRegex(url, rf"^/assets/{name[:-6]}\.[0-9a-f]{{8}}\.woff2$")
            body, content_type, _etag = assets.lookup_asset(url[len("/assets/"):])
            self.assertEqual("font/woff2", content_type)
            self.assertEqual(b"wOF2", body[:4])
            self.assertIsNone(assets.lookup_asset("fonts/" + url[len("/assets/"):]))
            self.assertEqual(url, assets.asset_url("", "fonts/" + name))

    def test_check_ready_reports_missing_assets(self) -> None:
        assets.check_ready()
        with patch.dict(assets._SOURCE_TO_HASHED, clear=True):
            with self.assertRaises(assets.WebAssetError):
                assets.check_ready()


class WebRoutes(unittest.TestCase):
    def setUp(self) -> None:
        td = TemporaryDirectory()
        self.addCleanup(td.cleanup)
        primary, _ = server.create_servers(("127.0.0.1", 0), None, store=server.Store(Path(td.name)), token="t",
                                           base_url="", redirect_view_base_url="")
        Thread(target=primary.serve_forever, daemon=True).start()
        self.addCleanup(primary.server_close)
        self.addCleanup(primary.shutdown)
        self.address = primary.server_address

    def fetch(self, path: str):
        conn = HTTPConnection(*self.address, timeout=5)
        try:
            conn.request("GET", path)
            response = conn.getresponse()
            return response.status, dict(response.getheaders()), response.read()
        finally:
            conn.close()

    def test_font_asset_route(self) -> None:
        url = assets.asset_url("", "geist-sans.woff2")
        status, headers, body = self.fetch(url)
        self.assertEqual(200, status)
        self.assertEqual("font/woff2", headers["Content-Type"])
        self.assertEqual("public, max-age=31536000, immutable", headers["Cache-Control"])
        self.assertEqual(b"wOF2", body[:4])

    def test_sign_in_page_allows_self_fonts_and_uses_theme_cookie(self) -> None:
        conn = HTTPConnection(*self.address, timeout=5)
        try:
            conn.request("GET", "/auth/sign-in", headers={"Cookie": "tailplan_theme=dark"})
            response = conn.getresponse()
            body, csp = response.read(), response.getheader("Content-Security-Policy")
        finally:
            conn.close()
        self.assertIn("font-src 'self'", csp)
        self.assertIn("script-src 'none'", csp)
        self.assertIn(b' data-theme="dark"', body)

    def test_dashboard_script_asset_route(self) -> None:
        status, headers, body = self.fetch(assets.asset_url("", "dashboard.js"))
        self.assertEqual(200, status)
        self.assertEqual("text/javascript; charset=utf-8", headers["Content-Type"])
        self.assertIn(b"js-ready", body)
        self.assertIn("dashboard.js", assets.REQUIRED)

    def test_readyz_checks_web_assets(self) -> None:
        status, _headers, body = self.fetch("/readyz")
        self.assertEqual(200, status)
        self.assertTrue(json.loads(body)["ok"])
        with patch.object(server.web_assets, "check_ready", side_effect=assets.WebAssetError("missing")):
            status, _headers, body = self.fetch("/readyz")
        self.assertEqual(503, status)
        self.assertFalse(json.loads(body)["ok"])


if __name__ == "__main__":
    unittest.main()


class DashboardScriptHooksTests(unittest.TestCase):
    def test_share_url_attribute_allows_only_http_and_https(self) -> None:
        session = {"csrf": "c"}
        for url, kept in (("javascript:alert(1)", False), ("data:text/html,x", False), ("//evil.test/x", False),
                          ("https://tailnet.example/d/a/share", True), ("http://127.0.0.1:8/d/a/share", True)):
            draft = {"draftId": "abc", "title": "T", "shareUrl": url}
            for html in (render._render_dashboard_row(BASE, session, draft), render._render_shared_row(draft)):
                attr = re.search(r'data-share-url="([^"]*)"', html)[1]
                self.assertEqual(render.esc(url) if kept else "", attr, url)

    def test_snippet_rows_keep_the_plain_description(self) -> None:
        draft = {"draftId": "abc", "title": "T", "description": "Plain <text>", "snippet": "a <mark>hit</mark>"}
        html = render._render_dashboard_row(BASE, {"csrf": "c"}, draft)
        self.assertIn('data-desc="Plain &lt;text&gt;"', html)
        self.assertNotIn("data-desc", render._render_dashboard_row(BASE, {"csrf": "c"}, {**draft, "snippet": ""}))

    def test_facet_pills_name_their_kind(self) -> None:
        html = render._facets(BASE, {"view": "active", "repo": "acme/web", "tags": [{"tag": "ops", "count": 1}],
                                     "repos": [{"repo": "acme/web", "count": 1}]})
        self.assertRegex(html, r'data-facet="tag" href="[^"]*repo=acme')
        self.assertIn('data-facet="repo"', html)
