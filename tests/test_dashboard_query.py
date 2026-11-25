"""Tests for the schema v4 dashboard layer: tags, archive, FTS5 search, and bulk edits."""
from __future__ import annotations

import json
import re
import sqlite3
import tempfile
import unittest
from contextlib import closing
from pathlib import Path

import tailplan_server as server
from auth_harness import TOKEN, AuthCase

BASE = "https://tailplan.test"


def seed(store: server.Store, html_doc: str, **options) -> str:
    return store.upsert(html_doc, "plan.html", None, BASE, **options)["draftId"]


class StoreCase(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name) / "data"
        self.store = server.Store(self.root)

    def search(self, text: str) -> dict:
        return self.store.search_fts("local", server.parse_dashboard_query(text)["fts"])

    def query(self, text: str = "", **options) -> list[dict]:
        auth = server.AuthConfig(modes=("api_key",), policy=server.RBACPolicy(None))
        return server.query_dashboard(self.store, auth, "local", BASE, q=text, **options)["drafts"]


class MigrationTests(StoreCase):
    def test_v3_database_gains_v4_tables_and_backfills_search_from_html(self) -> None:
        first = seed(self.store, "<title>Alpha</title><p>quasar nebula</p>")
        seed(self.store, "<title>Beta</title><p>pulsar</p>")
        with closing(sqlite3.connect(self.store.db_path)) as connection:
            connection.execute("DROP TABLE draft_search")
            connection.execute("DROP TABLE draft_tags")
            connection.execute("ALTER TABLE drafts DROP COLUMN archived_at")
            connection.execute("PRAGMA user_version = 3")
            connection.commit()
        reopened = server.Store(self.root)
        with closing(sqlite3.connect(reopened.db_path)) as connection:
            self.assertEqual(4, connection.execute("PRAGMA user_version").fetchone()[0])
            self.assertEqual(2, connection.execute("SELECT count(*) FROM draft_search").fetchone()[0])
        hits = reopened.search_fts("local", server.parse_dashboard_query("quasar")["fts"])
        self.assertEqual([first], list(hits))

    def test_reopening_a_v4_database_keeps_one_search_row_per_draft(self) -> None:
        seed(self.store, "<title>Alpha</title><p>quasar</p>")
        reopened = server.Store(self.root)
        with closing(sqlite3.connect(reopened.db_path)) as connection:
            self.assertEqual(1, connection.execute("SELECT count(*) FROM draft_search").fetchone()[0])


class SearchTests(StoreCase):
    def test_rank_prefers_title_over_body(self) -> None:
        body_only = seed(self.store, "<title>Other</title><p>kestrel kestrel</p>")
        title_hit = seed(self.store, "<title>Kestrel</title><p>nothing</p>")
        hits = self.search("kestrel")
        self.assertEqual({body_only, title_hit}, set(hits))
        self.assertLess(hits[title_hit][0], hits[body_only][0])

    def test_snippet_escapes_html_and_marks_the_match(self) -> None:
        draft_id = seed(self.store, "<title>Safe</title><p>before &lt;script&gt;alert(1)&lt;/script&gt; needle after</p>")
        snippet = self.search("needle")[draft_id][1]
        self.assertIn("<mark>needle</mark>", snippet)
        self.assertNotIn("<script>", snippet)
        self.assertIn("&lt;script&gt;", snippet)

    def test_script_text_in_a_page_never_reaches_the_index(self) -> None:
        draft_id = seed(self.store, "<title>Safe</title><script>hiddenword()</script><p>shown</p>")
        self.assertEqual({}, self.search("hiddenword"))
        self.assertIn(draft_id, self.search("shown"))

    def test_malformed_input_never_raises(self) -> None:
        seed(self.store, "<title>Safe</title><p>text</p>")
        for text in ('"unclosed', "AND OR NOT () *", "(((", '""', "a" * 600, "tag:", "NEAR(a b)", "col:x"):
            self.query(text)
            self.search(text)

    def test_deleted_drafts_leave_the_index(self) -> None:
        draft_id = seed(self.store, "<title>Gone</title><p>vanish</p>")
        self.store.change_draft("local", draft_id, "delete", None)
        self.assertEqual({}, self.search("vanish"))

    def test_new_version_replaces_the_indexed_body(self) -> None:
        draft_id = seed(self.store, "<title>Plan</title><p>oldterm</p>")
        self.store.upsert("<title>Plan</title><p>newterm</p>", "plan.html", draft_id, BASE)
        self.assertEqual({}, self.search("oldterm"))
        self.assertIn(draft_id, self.search("newterm"))

    def test_tags_are_searchable(self) -> None:
        draft_id = seed(self.store, "<title>Plan</title><p>text</p>")
        self.store.set_tags("local", draft_id, ["roadmap"])
        self.assertIn(draft_id, self.search("roadmap"))
        self.store.set_tags("local", draft_id, [])
        self.assertEqual({}, self.search("roadmap"))


class OperatorTests(StoreCase):
    def setUp(self) -> None:
        super().setUp()
        self.old = seed(self.store, "<title>Old</title><p>x</p>")
        self.new = seed(self.store, "<title>New</title><p>y</p>")
        self.store.upsert("<title>New</title><p>y2</p>", "plan.html", self.new, BASE)
        self.store.set_tags("local", self.old, ["alpha"])
        self.store.set_favorite("local", self.new, True)

    def ids(self, text: str, **options) -> set[str]:
        return {draft["draftId"] for draft in self.query(text, **options)}

    def test_is_favorite_and_living(self) -> None:
        self.assertEqual({self.new}, self.ids("is:favorite"))
        self.assertEqual({self.new}, self.ids("is:living"))
        self.assertEqual({self.new}, self.ids("v:>1"))
        self.assertEqual({self.old}, self.ids("v:1"))

    def test_tag_operator_and_tag_filter_agree(self) -> None:
        self.assertEqual({self.old}, self.ids("tag:alpha"))
        self.assertEqual({self.old}, self.ids("", tag="alpha"))
        self.assertEqual(set(), self.ids("tag:missing"))

    def test_date_and_age_operators(self) -> None:
        self.assertEqual({self.old, self.new}, self.ids("after:2000-01-01"))
        self.assertEqual(set(), self.ids("before:2000-01-01"))
        self.assertEqual({self.old, self.new}, self.ids("age:<1d"))
        self.assertEqual(set(), self.ids("age:>1d"))

    def test_free_text_uses_the_index(self) -> None:
        self.assertEqual({self.old}, self.ids("x"))
        self.assertEqual({self.new}, self.ids("y2 is:favorite"))

    def test_bad_operator_value_falls_back_to_text(self) -> None:
        self.assertEqual(set(), self.ids("after:notadate"))


class SuggestedTests(StoreCase):
    def test_streams_need_two_drafts_and_are_never_applied(self) -> None:
        a = self.store.upsert("<title>Roadmap v1</title>", "a.html", None, BASE, metadata=None)["draftId"]
        b = self.store.upsert("<title>Roadmap v2</title>", "b.html", None, BASE)["draftId"]
        c = seed(self.store, "<title>Lonely</title>")
        auth = server.AuthConfig(modes=("api_key",), policy=server.RBACPolicy(None))
        state = server.query_dashboard(self.store, auth, "local", BASE)
        suggested = state["suggestedTags"]
        self.assertEqual({a, b}, set(suggested))
        self.assertEqual(1, len(set(suggested.values())))
        self.assertNotIn(c, suggested)
        for draft in state["drafts"]:
            self.assertEqual([], draft["tags"])


class ArchiveStoreTests(StoreCase):
    def test_archive_hides_from_active_and_keeps_stamps(self) -> None:
        draft_id = seed(self.store, "<title>Plan</title>")
        self.store.set_favorite("local", draft_id, True)
        before = self.store.draft_detail("local", draft_id, BASE)["draft"]
        self.store.set_archived("local", draft_id, True)
        self.assertEqual([], self.query())
        self.assertEqual([draft_id], [d["draftId"] for d in self.query(view="archived")])
        after = self.store.draft_detail("local", draft_id, BASE)["draft"]
        self.assertEqual(before["updatedAt"], after["updatedAt"])
        self.assertEqual(before["favoritedAt"], after["favoritedAt"])
        self.assertTrue(after["archived"])
        self.store.set_archived("local", draft_id, False)
        self.assertEqual([draft_id], [d["draftId"] for d in self.query()])

    def test_tag_rules(self) -> None:
        draft_id = seed(self.store, "<title>Plan</title>")
        self.assertEqual(["a", "b"], self.store.set_tags("local", draft_id, [" B ", "a", "b"])[1])
        for bad in (["bad tag"], ["x" * 40], [str(n) for n in range(11)], ["-lead"]):
            with self.assertRaises(ValueError):
                self.store.set_tags("local", draft_id, bad)

    def test_upload_accepts_tags_and_favorite(self) -> None:
        draft_id = seed(self.store, "<title>Plan</title>", tags=["Ops"], favorite=True)
        draft = self.store.draft_detail("local", draft_id, BASE)["draft"]
        self.assertEqual(["ops"], draft["tags"])
        self.assertTrue(draft["favorite"])


class DashCase(AuthCase):
    use_oidc = False

    def setUp(self) -> None:
        super().setUp()
        self.cookie = self.bootstrap_session()
        self.token = self.csrf(self.cookie)

    def post(self, path: str, payload: dict | None = None, *, as_json: bool = False, csrf: bool = True,
             headers: dict | None = None, cookie: str | None = None) -> tuple[int, dict, bytes]:
        fields = {**(payload or {})}
        if csrf:
            fields["csrf"] = self.token
        request_headers = dict(headers or {})
        if as_json:
            request_headers["Accept"] = "application/json"
            return self.request(path, "POST", fields, cookies=[cookie or self.cookie], headers=request_headers)
        return self.request(path, "POST", cookies=[cookie or self.cookie], headers=request_headers,
                            form={key: str(value) for key, value in fields.items()})

    def draft(self, ref: str) -> dict:
        status, body = self.api(f"/api/drafts/{ref}", token=TOKEN)
        self.assertEqual(200, status, body)
        return body["draft"]


class ArchiveHttpTests(DashCase):
    def test_archive_is_not_disable(self) -> None:
        result = self.upload("<title>Plan</title><p>text</p>", slug="plan-one")
        draft_id = result["draftId"]
        before = self.draft(draft_id)
        status, _headers, _body = self.post(f"/dashboard/drafts/{draft_id}/favorite")
        self.assertEqual(303, status)
        status, _headers, _body = self.post(f"/dashboard/drafts/{draft_id}/archive")
        self.assertEqual(303, status)
        _status, listing = self.api("/api/drafts", token=TOKEN)
        self.assertEqual([], listing["drafts"])
        _status, listing = self.api("/api/drafts?view=archived", token=TOKEN)
        self.assertEqual([draft_id], [d["draftId"] for d in listing["drafts"]])
        _status, listing = self.api("/api/drafts?archived=true", token=TOKEN)
        self.assertEqual([draft_id], [d["draftId"] for d in listing["drafts"]])
        for path in (f"/d/{draft_id}", f"/d/{draft_id}/share"):
            status, _headers, _body = self.request(path, headers={"Cookie": self.cookie})
            self.assertEqual(200, status, path)
        after = self.draft(draft_id)
        self.assertEqual(before["updatedAt"], after["updatedAt"])
        self.assertTrue(after["favoritedAt"])
        self.assertTrue(after["archived"])
        self.post(f"/dashboard/drafts/{draft_id}/unarchive")
        self.assertEqual([draft_id], [d["draftId"] for d in self.api("/api/drafts", token=TOKEN)[1]["drafts"]])

    def test_row_actions_answer_json_or_redirect_to_the_row(self) -> None:
        draft_id = self.upload("<title>Plan</title>")["draftId"]
        status, headers, _body = self.post(f"/dashboard/drafts/{draft_id}/favorite", {"next": "/dashboard?view=all"})
        self.assertEqual(303, status)
        self.assertTrue(headers["Location"].endswith(f"/dashboard?view=all#d-{draft_id}"), headers["Location"])
        status, headers, _body = self.post(f"/dashboard/drafts/{draft_id}/unfavorite", {"next": "https://evil.test/x"})
        self.assertEqual(303, status)
        self.assertTrue(headers["Location"].endswith(f"/dashboard#d-{draft_id}"), headers["Location"])
        status, _headers, body = self.post(f"/dashboard/drafts/{draft_id}/archive", as_json=True)
        self.assertEqual(200, status)
        result = json.loads(body)
        self.assertEqual({"ok": True, "draftId": draft_id, "favorite": False, "archived": True, "tags": []},
                         {key: result[key] for key in ("ok", "draftId", "favorite", "archived", "tags")})

    def test_tag_actions(self) -> None:
        draft_id = self.upload("<title>Plan</title>")["draftId"]
        self.post(f"/dashboard/drafts/{draft_id}/tags", {"tags": "beta, alpha"})
        self.assertEqual(["alpha", "beta"], self.draft(draft_id)["tags"])
        self.post(f"/dashboard/drafts/{draft_id}/tags", {"add_tag": "gamma"})
        self.post(f"/dashboard/drafts/{draft_id}/tags", {"remove_tag": "alpha"})
        self.assertEqual(["beta", "gamma"], self.draft(draft_id)["tags"])
        status, _headers, _body = self.post(f"/dashboard/drafts/{draft_id}/tags", {"add_tag": "bad tag"})
        self.assertEqual(400, status)

    def test_row_actions_need_csrf_and_a_matching_origin(self) -> None:
        draft_id = self.upload("<title>Plan</title>")["draftId"]
        status, _headers, _body = self.post(f"/dashboard/drafts/{draft_id}/archive", csrf=False)
        self.assertEqual(403, status)
        status, _headers, _body = self.post(f"/dashboard/drafts/{draft_id}/archive",
                                            headers={"Origin": "https://evil.test"})
        self.assertEqual(403, status)
        self.assertFalse(self.draft(draft_id)["archived"])

    def test_another_account_draft_is_not_found(self) -> None:
        status, _headers, _body = self.post("/dashboard/drafts/zzzzzzzz/archive")
        self.assertEqual(404, status)


class BulkTests(DashCase):
    def test_batch_star_tag_and_archive(self) -> None:
        ids = [self.upload(f"<title>Plan {n}</title>")["draftId"] for n in range(3)]
        for action, tag in (("favorite", None), ("add_tag", "ship"), ("archive", None)):
            status, _headers, body = self.post("/dashboard/bulk", {"draftIds": ids, "action": action, "tag": tag},
                                               as_json=True)
            self.assertEqual(200, status, body)
            self.assertEqual(3, json.loads(body)["count"])
        for draft_id in ids:
            draft = self.draft(draft_id)
            self.assertTrue(draft["favorite"] and draft["archived"])
            self.assertEqual(["ship"], draft["tags"])
        status, _headers, _body = self.post("/dashboard/bulk", {"draftIds": ids, "action": "remove_tag", "tag": "ship"},
                                            as_json=True)
        self.assertEqual([], self.draft(ids[0])["tags"])
        events = self.events("draft.bulk")
        self.assertEqual(4, len(events))
        self.assertEqual({"action": "add_tag", "count": 3, "tag": "ship"},
                         {key: events[1]["detail"][key] for key in ("action", "count", "tag")})

    def test_csrf_can_travel_in_a_header(self) -> None:
        ids = [self.upload("<title>Plan</title>")["draftId"]]
        status, _headers, _body = self.request(
            "/dashboard/bulk", "POST", {"draftIds": ids, "action": "favorite"}, cookies=[self.cookie],
            headers={"X-CSRF-Token": self.token, "Accept": "application/json"})
        self.assertEqual(200, status)

    def test_csrf_and_origin_are_enforced(self) -> None:
        ids = [self.upload("<title>Plan</title>")["draftId"]]
        payload = {"draftIds": ids, "action": "archive"}
        status, _headers, _body = self.post("/dashboard/bulk", payload, as_json=True, csrf=False)
        self.assertEqual(403, status)
        status, _headers, _body = self.post("/dashboard/bulk", payload, as_json=True,
                                            headers={"Origin": "https://evil.test"})
        self.assertEqual(403, status)
        status, _headers, _body = self.request("/dashboard/bulk", "POST", payload)
        self.assertEqual(401, status)
        self.assertFalse(self.draft(ids[0])["archived"])

    def test_cap_and_bad_input_change_nothing(self) -> None:
        draft_id = self.upload("<title>Plan</title>")["draftId"]
        status, _headers, _body = self.post(
            "/dashboard/bulk", {"draftIds": [draft_id] * 201, "action": "archive"}, as_json=True)
        self.assertIn(status, {400, 422})
        status, _headers, _body = self.post(
            "/dashboard/bulk", {"draftIds": [draft_id, "missing1"], "action": "archive"}, as_json=True)
        self.assertEqual(404, status)
        status, _headers, _body = self.post(
            "/dashboard/bulk", {"draftIds": [draft_id], "action": "explode"}, as_json=True)
        self.assertEqual(400, status)
        status, _headers, _body = self.post(
            "/dashboard/bulk", {"draftIds": [draft_id], "action": "add_tag", "tag": "bad tag"}, as_json=True)
        self.assertEqual(400, status)
        self.assertFalse(self.draft(draft_id)["archived"])

    def test_two_hundred_drafts_fit(self) -> None:
        ids = [seed(self.store, f"<title>Plan {n}</title>") for n in range(200)]
        status, _headers, body = self.post("/dashboard/bulk", {"draftIds": ids, "action": "favorite"}, as_json=True)
        self.assertEqual(200, status, body[:200])
        self.assertEqual(200, json.loads(body)["count"])


class ApiTests(DashCase):
    def test_patch_updates_tags_archive_and_favorite(self) -> None:
        draft_id = self.upload("<title>Plan</title>", slug="patch-me")["draftId"]
        status, body = self.api("/api/drafts/patch-me", "PATCH",
                                {"tags": ["B", "a"], "archived": True, "favorite": True}, token=TOKEN)
        self.assertEqual(200, status, body)
        draft = body["draft"]
        self.assertEqual(draft_id, draft["draftId"])
        self.assertEqual(["a", "b"], draft["tags"])
        self.assertTrue(draft["archived"] and draft["favorite"])
        status, body = self.api("/api/drafts/patch-me", "PATCH", {"archived": False}, token=TOKEN)
        self.assertFalse(body["draft"]["archived"])
        names = [event["event"] for event in self.events()]
        for name in ("draft.archive", "draft.unarchive", "draft.tag"):
            self.assertIn(name, names)

    def test_patch_rejects_bad_input(self) -> None:
        self.upload("<title>Plan</title>", slug="patch-me")
        for payload in ({}, {"archived": "yes"}, {"tags": ["bad tag"]}, {"tags": "alpha"}, {"favorite": 1}):
            status, body = self.api("/api/drafts/patch-me", "PATCH", payload, token=TOKEN)
            self.assertEqual(400, status, (payload, body))
        status, _body = self.api("/api/drafts/nothere1", "PATCH", {"archived": True}, token=TOKEN)
        self.assertEqual(404, status)

    def test_api_needs_a_bearer_token_not_a_cookie(self) -> None:
        self.upload("<title>Plan</title>", slug="patch-me")
        status, _body = self.api("/api/drafts/patch-me", "PATCH", {"archived": True})
        self.assertEqual(401, status)
        status, _body = self.api("/api/drafts/patch-me", "PATCH", {"archived": True}, cookies=[self.cookie])
        self.assertEqual(401, status)
        status, _body = self.api("/api/drafts?view=all", cookies=[self.cookie])
        self.assertEqual(401, status)

    def test_listing_filters_and_default_order(self) -> None:
        older = self.upload("<title>Older</title><p>zebra</p>", slug="older-one", tags=["keep"], favorite=True)
        newer = self.upload("<title>Newer</title><p>giraffe</p>")
        _status, body = self.api("/api/drafts", token=TOKEN)
        self.assertEqual([older["draftId"], newer["draftId"]], [d["draftId"] for d in body["drafts"]])
        _status, body = self.api("/api/drafts?q=zebra", token=TOKEN)
        self.assertEqual([older["draftId"]], [d["draftId"] for d in body["drafts"]])
        self.assertIn("<mark>zebra</mark>", body["drafts"][0]["snippet"])
        _status, body = self.api("/api/drafts?tag=keep&sort=title&dir=asc", token=TOKEN)
        self.assertEqual([older["draftId"]], [d["draftId"] for d in body["drafts"]])
        self.assertEqual(2, body["counts"]["active"])
        self.assertEqual([{"tag": "keep", "count": 1}], body["tags"])

    def test_upload_rejects_bad_tags_and_favorite(self) -> None:
        status, _body = self.api("/api/uploads", "POST", {"html": "<title>A</title>", "tags": ["bad tag"]}, token=TOKEN)
        self.assertEqual(422, status)
        status, _body = self.api("/api/uploads", "POST", {"html": "<title>A</title>", "favorite": "yes"}, token=TOKEN)
        self.assertEqual(400, status)


class DashboardPageTests(DashCase):
    def test_dashboard_json_and_html(self) -> None:
        self.upload("<title>Plan</title><p>walrus</p>", tags=["x"])
        status, headers, body = self.request("/dashboard?format=json&q=walrus", cookies=[self.cookie])
        self.assertEqual(200, status)
        state = json.loads(body)
        self.assertEqual(1, len(state["drafts"]))
        self.assertEqual("system", state["theme"])
        self.assertEqual("relevance", state["sort"])
        status, _headers, body = self.request("/dashboard", cookies=[self.cookie],
                                              headers={"Accept": "application/json"})
        self.assertEqual("active", json.loads(body)["view"])
        status, _headers, body = self.request("/dashboard?q=walrus", cookies=[self.cookie])
        self.assertEqual(200, status)
        self.assertIn(b"<h1>My drafts", body)

    def test_theme_cookie(self) -> None:
        status, headers, _body = self.post("/dashboard/theme", {"theme": "dark"}, as_json=True)
        self.assertEqual(200, status)
        cookie = headers["Set-Cookie"][0]
        for part in ("tailplan_theme=dark", "Path=/", "SameSite=Lax", "Max-Age=31536000"):
            self.assertIn(part, cookie)
        self.assertNotIn("HttpOnly", cookie)
        status, _headers, body = self.request("/dashboard?format=json", cookies=[self.cookie, "tailplan_theme=dark"])
        self.assertEqual("dark", json.loads(body)["theme"])
        status, _headers, _body = self.post("/dashboard/theme", {"theme": "neon"}, as_json=True)
        self.assertEqual(400, status)
        status, _headers, _body = self.post("/dashboard/theme", {"theme": "dark"}, csrf=False)
        self.assertEqual(403, status)

    def test_audit_never_holds_body_or_query_text(self) -> None:
        draft_id = self.upload("<title>Plan</title><p>secretbodytext</p>")["draftId"]
        self.request("/dashboard?format=json&q=secretquerytext", cookies=[self.cookie])
        self.api("/api/drafts?q=secretquerytext", token=TOKEN)
        self.post(f"/dashboard/drafts/{draft_id}/archive")
        self.post("/dashboard/bulk", {"draftIds": [draft_id], "action": "add_tag", "tag": "ship"}, as_json=True)
        logged = self.audit_lines.getvalue()
        self.assertNotIn("secretbodytext", logged)
        self.assertNotIn("secretquerytext", logged)
        self.assertTrue(re.search(r'"event": ?"draft\.archive"', logged))


if __name__ == "__main__":
    unittest.main()
