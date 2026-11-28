"""Cover Wave 1 sharing: non-owner draft pages, the scoped directory, smart grants, and Seen by."""
from __future__ import annotations

import json
import unittest
from unittest import mock

import tailplan_server as server
from auth_harness import TOKEN, AuthCase

POLICY = {
    "legacy_workspace": "owner",
    "default_workspace": "team",
    "rules": [
        {"match": {"email": "zach@example.test"}, "workspaces": {"zach": "admin", "collab": "editor"},
         "default_workspace": "zach", "display_name": "Zach Stone"},
        {"match": {"email": "collab@example.test"}, "workspaces": {"collab": "editor"},
         "display_name": "Casey Collab"},
        {"match": {"email": "viewer@example.test"}, "workspaces": {"collab": "viewer"},
         "display_name": "Vic Viewer"},
        {"match": {"email": "mom@family.test"}, "workspaces": {"family": "editor"}, "display_name": "Mom Family"},
        {"match": {"group": "family"}, "workspaces": {"family": "editor"}, "display_name": "Family"},
        {"match": {"group": "collabteam"}, "workspaces": {"collab": "editor"}, "display_name": "Collab team"},
        {"match": {"email": "boss@example.test"}, "instance_role": "admin", "display_name": "The Boss"},
        {"match": {"group": "outsiders"}},
    ],
}

NOT_FOUND_API = {"ok": False, "error": "Draft not found."}


class Wave1Case(AuthCase):
    policy = POLICY

    def setUp(self) -> None:
        super().setUp()
        self.zach = self.oidc_login(sub="zach", email="zach@example.test", name="Zach Stone", groups=[])
        self.zach_key = self.create_key(self.zach)
        self.collab = self.oidc_login(sub="collab", email="collab@example.test", name="Casey Collab", groups=[])
        self.collab_key = self.create_key(self.collab)
        self.viewer = self.oidc_login(sub="viewer", email="viewer@example.test", name="Vic Viewer", groups=[])
        self.viewer_key = self.create_key(self.viewer)
        self.mom = self.oidc_login(sub="mom", email="mom@family.test", name="Mom Family", groups=["family"])
        self.boss = self.oidc_login(sub="boss", email="boss@example.test", name="The Boss", groups=[])
        self.boss_key = self.create_key(self.boss)
        self.stranger = self.oidc_login(sub="stranger", email="stranger@example.test", groups=["outsiders"])
        self.draft = self.upload("<title>Roadmap</title><p>Plan</p>", token=self.zach_key,
                                 workspace="collab", visibility="workspace")
        self.draft_id = self.draft["draftId"]
        self.detail = f"/dashboard/drafts/{self.draft_id}"

    def sharing(self, key: str, method: str = "GET", payload: object = None,
                suffix: str = "") -> tuple[int, dict]:
        return self.api(f"/api/drafts/{self.draft_id}/sharing{suffix}", method, payload, token=key)

    def owner_markers(self, owner: bool) -> tuple[str, ...]:
        """Return page text that only the owner controls produce."""
        actions = ("tags", "favorite", "archive", "disable", "delete")
        return tuple(f'action="{self.base}{self.detail}/{action}' for action in actions
                     ) + ('aria-label="Tags"', "Delete draft")

    def audit_markers(self) -> tuple[str, ...]:
        return ("<summary>Audit</summary>", "<dt>sourceIp</dt>")

    def page(self, cookie: str, path: str | None = None) -> tuple[int, dict, str]:
        status, headers, body = self.request(path or self.detail, cookies=[cookie])
        return status, headers, body.decode()


class NonOwnerRoutingTests(Wave1Case):
    def test_viewer_is_sent_to_the_share_page(self) -> None:
        status, headers, _body = self.page(self.viewer)
        self.assertEqual(303, status)
        self.assertEqual(f"{self.base}/d/{self.draft_id}/share", headers["Location"])

    def test_viewer_actions_answer_403(self) -> None:
        csrf = self.csrf(self.viewer)
        for suffix, form in (("/sharing", {"visibility": "link"}),
                             ("/sharing/grants", {"type": "email", "value": "x@example.test"}),
                             ("/sharing/revoke", {"type": "email", "value": "x@example.test"}),
                             ("/disable", {}), ("/delete", {}), ("/favorite", {}), ("/archive", {}),
                             ("/tags", {"add_tag": "x"})):
            with self.subTest(suffix):
                status, _headers, _body = self.request(self.detail + suffix, "POST", cookies=[self.viewer],
                                                       form={"csrf": csrf, **form})
                self.assertEqual(403, status)
        self.assertEqual([], self.sharing(self.zach_key)[1]["grants"])
        self.assertEqual("workspace", self.sharing(self.zach_key)[1]["visibility"])

    def test_editor_sees_a_safe_detail_page(self) -> None:
        status, _headers, body = self.page(self.collab)
        self.assertEqual(200, status)
        self.assertIn("Open Draft", body)
        self.assertIn('id="sharing-title"', body)
        self.assertIn("Version history", body)
        for hidden in self.owner_markers(owner=True) + self.audit_markers():
            with self.subTest(hidden):
                self.assertNotIn(hidden, body)

    def test_owner_sees_the_full_detail_page(self) -> None:
        status, _headers, body = self.page(self.zach)
        self.assertEqual(200, status)
        for shown in ("Open latest draft", *self.owner_markers(owner=True), *self.audit_markers()):
            with self.subTest(shown):
                self.assertIn(shown, body)

    def test_admin_sees_audit_but_no_owner_controls(self) -> None:
        status, _headers, body = self.page(self.boss)
        self.assertEqual(200, status)
        for shown in self.audit_markers():
            with self.subTest(shown):
                self.assertIn(shown, body)
        for hidden in self.owner_markers(owner=True):
            with self.subTest(hidden):
                self.assertNotIn(hidden, body)
        csrf = self.csrf(self.boss, "/dashboard")
        status, _headers, _body = self.request(self.detail + "/disable", "POST", cookies=[self.boss],
                                               form={"csrf": csrf})
        self.assertEqual(403, status)

    def test_editor_can_post_sharing_changes(self) -> None:
        csrf = self.csrf(self.collab)
        status, headers, _body = self.request(self.detail + "/sharing/grants", "POST", cookies=[self.collab],
                                              form={"csrf": csrf, "type": "email", "value": "pal@example.test"})
        self.assertEqual(303, status)
        self.assertEqual(self.base + self.detail, headers["Location"])
        self.assertEqual(["pal@example.test"], [grant["value"] for grant in self.sharing(self.zach_key)[1]["grants"]])
        status, headers, _body = self.request(self.detail + "/sharing/revoke", "POST", cookies=[self.collab],
                                              form={"csrf": csrf, "type": "email", "value": "pal@example.test"})
        self.assertEqual(303, status)
        self.assertEqual([], self.sharing(self.zach_key)[1]["grants"])
        status, headers, _body = self.request(self.detail + "/sharing", "POST", cookies=[self.collab],
                                              form={"csrf": csrf, "visibility": "link"})
        self.assertEqual(303, status)
        self.assertEqual(self.base + self.detail, headers["Location"])
        self.assertEqual("link", self.sharing(self.zach_key)[1]["visibility"])

    def test_editor_still_cannot_use_owner_actions(self) -> None:
        csrf = self.csrf(self.collab)
        for suffix, form in (("/disable", {}), ("/delete", {}), ("/favorite", {}), ("/tags", {"add_tag": "x"})):
            with self.subTest(suffix):
                status, _headers, _body = self.request(self.detail + suffix, "POST", cookies=[self.collab],
                                                       form={"csrf": csrf, **form})
                self.assertEqual(403, status)
        self.assertFalse(self.api(f"/api/drafts/{self.draft_id}/source", token=self.zach_key)[1].get("disabled"))

    def test_owner_actions_still_work_through_a_custom_url(self) -> None:
        status, _body = self.api(f"/api/drafts/{self.draft_id}/sharing", "POST", {"visibility": "workspace"},
                                 token=self.zach_key)
        self.assertEqual(200, status)
        slug_draft = self.upload("<title>Slug</title>", token=self.zach_key, slug="plan-slug",
                                 workspace="collab", visibility="workspace")
        path = "/dashboard/drafts/plan-slug"
        status, _headers, body = self.page(self.zach, path)
        self.assertEqual(200, status)
        self.assertIn(slug_draft["draftId"], body)
        status, headers, _body = self.page(self.viewer, path)
        self.assertEqual(303, status)
        self.assertEqual(f"{self.base}/d/plan-slug/share", headers["Location"])
        status, _headers, body = self.page(self.collab, path)
        self.assertEqual(200, status)
        self.assertIn("Open Draft", body)
        csrf = self.csrf(self.zach, path)
        status, _headers, _body = self.request(path + "/favorite", "POST", cookies=[self.zach],
                                               form={"csrf": csrf})
        self.assertEqual(303, status)

    def test_signed_out_reader_gets_no_draft_details(self) -> None:
        status, _headers, body = self.request(self.detail)
        self.assertNotIn(b"Roadmap", body)
        self.assertNotIn(b"Version history", body)
        self.assertIn(status, {200, 302, 303, 401})


class AntiEnumerationTests(Wave1Case):
    def not_found_page(self, cookie: str) -> tuple[int, bytes]:
        status, _headers, body = self.request("/dashboard/drafts/zzzzzz-missing", cookies=[cookie])
        return status, body

    def test_stranger_gets_the_same_404_as_a_missing_draft(self) -> None:
        missing_status, missing = self.not_found_page(self.stranger)
        status, _headers, body = self.request(self.detail, cookies=[self.stranger])
        self.assertEqual((404, 404), (missing_status, status))
        self.assertEqual(missing, body)
        self.assertNotIn(b"Roadmap", body)

    def test_private_draft_hides_from_the_workspace_editor(self) -> None:
        self.sharing(self.zach_key, "POST", {"visibility": "private"})
        missing_status, missing = self.not_found_page(self.collab)
        status, _headers, body = self.request(self.detail, cookies=[self.collab])
        self.assertEqual((404, missing), (status, body))
        self.assertEqual(404, missing_status)
        self.assertEqual(200, self.request(self.detail, cookies=[self.zach])[0])

    def test_deleted_draft_matches_a_missing_draft(self) -> None:
        csrf = self.csrf(self.zach)
        status, _headers, _body = self.request(self.detail + "/delete", "POST", cookies=[self.zach],
                                               form={"csrf": csrf})
        self.assertEqual(303, status)
        _missing_status, missing = self.not_found_page(self.zach)
        status, _headers, body = self.request(self.detail, cookies=[self.zach])
        self.assertEqual((404, missing), (status, body))

    def test_disabled_draft_is_a_404_for_everyone_except_owner_and_admin(self) -> None:
        csrf = self.csrf(self.zach)
        status, _headers, _body = self.request(self.detail + "/disable", "POST", cookies=[self.zach],
                                               form={"csrf": csrf, "reason": "pause"})
        self.assertEqual(303, status)
        _missing_status, missing = self.not_found_page(self.collab)
        for cookie in (self.collab, self.viewer, self.mom):
            with self.subTest(cookie=cookie[:20]):
                status, _headers, body = self.request(self.detail, cookies=[cookie])
                self.assertEqual((404, missing), (status, body))
        self.assertEqual(200, self.request(self.detail, cookies=[self.zach])[0])
        self.assertEqual(200, self.request(self.detail, cookies=[self.boss])[0])
        csrf = self.csrf(self.collab, "/dashboard")
        status, _headers, _body = self.request(self.detail + "/sharing", "POST", cookies=[self.collab],
                                               form={"csrf": csrf, "visibility": "link"})
        self.assertEqual(404, status)

    def test_json_clients_get_a_json_404(self) -> None:
        status, body = self.api("/dashboard/drafts/zzzzzz-missing", token=None, cookies=[self.stranger])
        self.assertEqual((404, NOT_FOUND_API), (status, body))
        status, body = self.api(self.detail, cookies=[self.stranger])
        self.assertEqual((404, NOT_FOUND_API), (status, body))
        status, headers, body = self.request("/dashboard/drafts/zzzzzz-missing", cookies=[self.stranger],
                                             headers={"Accept": "text/html"})
        self.assertEqual(404, status)
        self.assertIn(b"Draft not found", body)

    def test_misses_share_the_viewer_limiter_with_the_d_routes(self) -> None:
        self.primary.viewer_404_limit = 3
        stranger_key = self.create_key(self.stranger)
        self.assertEqual(404, self.request("/dashboard/drafts/zzzzzz-missing", cookies=[self.stranger])[0])
        self.assertEqual(404, self.api(f"/api/drafts/{self.draft_id}/sharing", token=stranger_key)[0])
        self.assertEqual(404, self.request("/d/zzzzzz-missing")[0])
        for path in ("/dashboard/drafts/zzzzzz-missing", self.detail, "/api/drafts/zzzzzz-missing/sharing",
                     "/d/zzzzzz-missing"):
            with self.subTest(path):
                status, headers, body = self.request(path, cookies=[self.stranger],
                                                     headers={"Accept": "application/json"})
                self.assertEqual(429, status)
                self.assertGreaterEqual(int(headers["Retry-After"]), 1)
                self.assertFalse(json.loads(body)["ok"])

    def test_owner_pages_do_not_count_as_misses(self) -> None:
        self.primary.viewer_404_limit = 2
        for _ in range(6):
            self.assertEqual(200, self.request(self.detail, cookies=[self.zach])[0])
            self.assertEqual(200, self.sharing(self.zach_key)[0])

    def test_reserved_action_names_are_not_draft_refs(self) -> None:
        for suffix in ("disable", "enable", "delete", "favorite", "unfavorite", "archive", "unarchive", "tags",
                       "sharing"):
            with self.subTest(suffix):
                status, _headers, _body = self.request(f"/dashboard/drafts/{suffix}", cookies=[self.zach])
                self.assertEqual(404, status)
        self.assertEqual(404, self.request("/dashboard/drafts/Bad_Ref", cookies=[self.zach])[0])


class ScopedDirectoryTests(Wave1Case):
    def names(self, body: dict) -> tuple[set[str], set[str]]:
        return ({item["value"] for item in body["directory"]["people"]},
                {item["value"] for item in body["directory"]["groups"]})

    def test_admin_sees_the_whole_directory(self) -> None:
        status, body = self.sharing(self.boss_key)
        self.assertEqual(200, status, body)
        people, groups = self.names(body)
        self.assertTrue({"collab@example.test", "viewer@example.test", "mom@family.test"} <= people)
        self.assertNotIn("zach@example.test", people)  # The draft owner is never a grant target.
        self.assertTrue({"family", "collabteam"} <= groups)
        self.assertNotIn("boss@example.test", people)  # The caller never lists themself.
        self.assertEqual({"collab", "family", "zach", "team", "owner"}, {item["name"] for item in
                                                                         body["movableWorkspaces"]})
        self.assertTrue(body["canMoveWorkspace"])
        self.assertNotIn("seenBy", body)

    def test_owner_sees_workspace_mates_and_movable_workspaces(self) -> None:
        status, body = self.sharing(self.zach_key)
        self.assertEqual(200, status)
        people, groups = self.names(body)
        self.assertEqual({"collab@example.test", "viewer@example.test"}, people)
        self.assertEqual({"collabteam"}, groups)
        self.assertEqual({"collab", "zach"}, {item["name"] for item in body["movableWorkspaces"]})
        card = {item["name"]: item for item in body["movableWorkspaces"]}["collab"]
        self.assertEqual({"name": "collab", "memberCount": 3, "groups": ["collabteam"]}, card)
        self.assertTrue(body["canMoveWorkspace"])
        self.assertEqual("Zach Stone", next(item["displayName"] for item in server_people(self, "zach@example.test")))
        self.assertEqual([], body["seenBy"])

    def test_editor_gets_no_movable_workspaces_and_no_foreign_entries(self) -> None:
        status, body = self.sharing(self.collab_key)
        self.assertEqual(200, status)
        self.assertEqual([], body["movableWorkspaces"])
        self.assertFalse(body["canMoveWorkspace"])
        people, groups = self.names(body)
        self.assertEqual({"viewer@example.test"}, people)  # The owner is skipped. Collab is the caller.
        self.assertEqual({"collabteam"}, groups)
        dump = json.dumps(body)
        for secret in ("mom@family.test", "family", "boss@example.test", "The Boss", "Mom Family", "zach@example.test"):
            with self.subTest(secret):
                self.assertNotIn(secret, dump)
        self.assertNotIn("seenBy", body)

    def test_directory_entries_carry_display_names_and_shared_workspaces(self) -> None:
        _status, body = self.sharing(self.collab_key)
        person = next(item for item in body["directory"]["people"] if item["value"] == "viewer@example.test")
        self.assertEqual({"type": "email", "value": "viewer@example.test", "displayName": "Vic Viewer",
                          "workspaces": ["collab"]}, person)
        group = body["directory"]["groups"][0]
        self.assertEqual({"value": "collabteam", "displayName": "Collab team", "workspaces": ["collab"]}, group)

    def test_family_member_sees_only_the_family_workspace(self) -> None:
        mom_key = self.create_key(self.mom)
        mine = self.upload("<title>Family</title>", token=mom_key, workspace="family", visibility="private")
        status, body = self.api(f"/api/drafts/{mine['draftId']}/sharing", token=mom_key)
        self.assertEqual(200, status)
        people, groups = self.names(body)
        self.assertEqual(set(), people)
        self.assertEqual({"family"}, groups)
        self.assertEqual(["family"], [item["name"] for item in body["movableWorkspaces"]])
        dump = json.dumps(body)
        for secret in ("collab", "zach", "viewer@example.test"):
            with self.subTest(secret):
                self.assertNotIn(secret, dump)

    def test_verified_principals_join_the_directory_when_they_share_a_workspace(self) -> None:
        self.oidc_login(sub="newbie", email="newbie@example.test", name="New Bie", groups=["collabteam"])
        self.oidc_login(sub="liar", email="liar@example.test", email_verified=False, name="Liar",
                        groups=["collabteam"])
        self.oidc_login(sub="loner", email="loner@example.test", email_verified=True, name="Loner",
                        groups=["outsiders"])
        _status, body = self.sharing(self.zach_key)
        people = {item["value"]: item["displayName"] for item in body["directory"]["people"]}
        self.assertEqual("New Bie", people["newbie@example.test"])
        self.assertNotIn("liar@example.test", people)  # An unverified email never enters the list.
        self.assertNotIn("loner@example.test", people)  # No shared workspace, so no entry.
        _status, admin = self.sharing(self.boss_key)
        names = {item["value"] for item in admin["directory"]["people"]}
        self.assertIn("loner@example.test", names)
        self.assertNotIn("liar@example.test", names)

    def test_tailscale_logins_are_listed_only_when_the_provider_is_tailscale(self) -> None:
        self.store.upsert_principal(issuer="tailscale", subject="pat@github", account_id="acct-pat",
                                    email=None, email_verified=False, display_name="Pat",
                                    groups=["collabteam"], instance_role=None, workspaces={"collab": "editor"},
                                    default_workspace="collab")
        self.store.upsert_principal(issuer="https://idp.test", subject="spoof@github", account_id="acct-spoof",
                                    email=None, email_verified=False, display_name="Spoof",
                                    groups=["collabteam"], instance_role=None, workspaces={"collab": "editor"},
                                    default_workspace="collab")
        _status, body = self.sharing(self.zach_key)
        logins = {item["value"] for item in body["directory"]["people"] if item["type"] == "tailscale_login"}
        self.assertEqual({"pat@github"}, logins)

    def test_viewer_and_signed_out_callers_get_nothing(self) -> None:
        self.assertEqual(403, self.sharing(self.viewer_key)[0])
        status, _body = self.api(f"/api/drafts/{self.draft_id}/sharing")
        self.assertEqual(401, status)
        status, _body = self.api(f"/api/drafts/{self.draft_id}/sharing", token="not-a-key")
        self.assertEqual(401, status)
        empty = {"people": [], "groups": [], "workspaces": []}
        handler = bare_handler(self)
        for identity in (None, {"passive": True, "accountId": None, "workspaces": {"collab": "editor"}},
                         {"accountId": "anonymous", "workspaces": {"collab": "editor"}}):
            with self.subTest(identity=identity):
                self.assertEqual(empty, handler.sharing_directory(identity))

    def test_workspace_move_uses_the_movable_list(self) -> None:
        status, body = self.sharing(self.zach_key, "POST", {"workspace": "zach"})
        self.assertEqual((200, "zach"), (status, body["workspace"]))
        status, body = self.sharing(self.zach_key, "POST", {"workspace": "family"})
        self.assertEqual(403, status)
        status, _body = self.sharing(self.zach_key, "POST", {"workspace": "collab"})
        self.assertEqual(200, status)


def bare_handler(case: AuthCase) -> server.Handler:
    """Return a handler with a store and auth settings but no socket, to call directory helpers."""
    handler = case.primary.RequestHandlerClass.__new__(case.primary.RequestHandlerClass)
    handler.server = case.primary
    return handler


def server_people(case: AuthCase, email: str) -> list[dict]:
    roster, _groups = bare_handler(case).sharing_roster()
    return [{"displayName": roster[("email", email)]["displayName"]}]


class SmartGrantTests(Wave1Case):
    def grant(self, value: object, **extra) -> tuple[int, dict]:
        return self.sharing(self.zach_key, "POST", {"value": value, **extra}, "/grants")

    def test_value_decides_the_type(self) -> None:
        for value, expected in (("Friend@Example.test", ("email", "friend@example.test")),
                                ("CollabTeam", ("group", "collabteam")),
                                ("Sam@GitHub", ("tailscale_login", "sam@github")),
                                ("  collabteam  ", ("group", "collabteam"))):
            with self.subTest(value):
                status, body = self.grant(value)
                self.assertEqual(200, status, body)
                self.assertIn(expected, [(grant["type"], grant["value"]) for grant in body["grants"]])
        status, body = self.grant("lee@example.test", type="auto", role="editor")
        self.assertEqual(200, status)
        self.assertIn(("email", "lee@example.test", "editor"),
                      [(grant["type"], grant["value"], grant["role"]) for grant in body["grants"]])

    def test_email_wins_over_a_group_with_the_same_text(self) -> None:
        status, body = self.grant("mom@family.test")
        self.assertEqual("email", next(grant["type"] for grant in body["grants"] if grant["value"] == "mom@family.test"))

    def test_an_explicit_type_is_unchanged(self) -> None:
        status, body = self.grant("collabteam", type="tailscale_login")
        self.assertEqual(400, status, body)  # A bare word is not a login. No guessing happens.
        status, body = self.grant("sam@github", type="tailscale_login")
        self.assertEqual(200, status)
        status, body = self.grant("friend@example.test", type="group")
        self.assertEqual(200, status)  # An explicit type is stored as given, not re-guessed.
        self.assertIn(("group", "friend@example.test"), [(grant["type"], grant["value"]) for grant in body["grants"]])
        status, body = self.grant("friend@example.test", type="email")
        self.assertEqual(200, status)
        self.assertIn(("email", "friend@example.test"), [(grant["type"], grant["value"]) for grant in body["grants"]])

    def test_values_that_fit_nothing_are_rejected(self) -> None:
        for value in ("nobody", "!!", "", None, 5, "a b"):
            with self.subTest(value):
                status, _body = self.grant(value)
                self.assertEqual(400, status)

    def test_dashboard_form_accepts_a_missing_type(self) -> None:
        csrf = self.csrf(self.zach, self.detail)
        status, _headers, _body = self.request(self.detail + "/sharing/grants", "POST", cookies=[self.zach],
                                               form={"csrf": csrf, "value": "form@example.test"})
        self.assertEqual(303, status)
        grants = self.sharing(self.zach_key)[1]["grants"]
        self.assertEqual([("email", "form@example.test")], [(grant["type"], grant["value"]) for grant in grants])

    def test_grant_audit_rows_stay_clean(self) -> None:
        self.grant("pii@example.test")
        self.grant("collabteam")
        self.sharing(self.zach_key, "POST", {"visibility": "private"})
        self.sharing(self.zach_key, "DELETE", {"type": "email", "value": "pii@example.test"}, "/grants")
        rows = [event for event in self.events() if event["event"] in
                {"tailplan.sharing.grant", "tailplan.sharing.update", "tailplan.sharing.revoke"}]
        self.assertEqual({"tailplan.sharing.grant", "tailplan.sharing.update", "tailplan.sharing.revoke"},
                         {row["event"] for row in rows})
        for row in rows:
            self.assertIs(row, server.check_audit_row(row))
        text = self.audit_lines.getvalue()
        self.assertNotIn("pii@example.test", text)
        self.assertNotIn("@", text)


class AudienceSummaryTests(Wave1Case):
    def summary(self, key: str | None = None) -> str:
        status, body = self.sharing(key or self.zach_key)
        self.assertEqual(200, status, body)
        return body["audienceSummary"]

    def test_summary_follows_visibility_and_grants(self) -> None:
        self.sharing(self.zach_key, "POST", {"visibility": "private"})
        self.assertEqual("Only you can open this", self.summary())
        self.sharing(self.zach_key, "POST", {"type": "email", "value": "a@example.test"}, "/grants")
        self.assertEqual("You and 1 person can view", self.summary())
        self.sharing(self.zach_key, "POST", {"value": "b@example.test"}, "/grants")
        self.assertEqual("You and 2 people can view", self.summary())
        self.sharing(self.zach_key, "POST", {"type": "workspace", "value": "collab", "role": "editor"}, "/grants")
        self.assertEqual("Collab workspace can edit; You and 2 people can view", self.summary())

    def test_workspace_editor_grant_alone_reads_plainly(self) -> None:
        self.sharing(self.zach_key, "POST", {"visibility": "private"})
        self.sharing(self.zach_key, "POST", {"type": "workspace", "value": "collab", "role": "editor"}, "/grants")
        self.assertEqual("Collab workspace can edit", self.summary())

    def test_open_visibility_is_named(self) -> None:
        self.assertEqual("Members of the collab workspace can open this", self.summary())
        self.sharing(self.zach_key, "POST", {"visibility": "link"})
        self.assertEqual("Anyone with the link can open this", self.summary())

    def test_non_owner_reads_the_owner_in_third_person(self) -> None:
        self.sharing(self.zach_key, "POST", {"visibility": "private"})
        self.sharing(self.zach_key, "POST", {"type": "email", "value": "collab@example.test", "role": "editor"},
                     "/grants")
        self.assertEqual("The owner and 1 person can edit", self.summary(self.collab_key))

    def test_pure_function_cases(self) -> None:
        summary = server.audience_summary
        grants = [{"type": "group", "value": "staff", "role": "viewer"},
                  {"type": "workspace", "value": "ops", "role": "viewer"}]
        self.assertEqual("Ops workspace and staff group can view", summary("owner", "private", "team", grants))
        self.assertEqual("Everyone signed in can open this", summary("owner", "authenticated", "team", []))
        self.assertEqual("Only the owner can open this", summary("editor", "private", "team", []))


class SeenByTests(Wave1Case):
    def open_draft(self, cookie: str) -> None:
        status, _headers, _body = self.request(f"/d/{self.draft_id}/", cookies=[cookie])
        self.assertEqual(200, status)

    def test_owner_sees_the_latest_view_per_reader(self) -> None:
        self.open_draft(self.collab)
        self.open_draft(self.collab)
        self.open_draft(self.viewer)
        self.open_draft(self.zach)  # The owner never lists themself.
        status, body = self.sharing(self.zach_key)
        self.assertEqual(200, status)
        seen = body["seenBy"]
        self.assertEqual(["Vic Viewer", "Casey Collab"], [item["displayName"] for item in seen])
        self.assertEqual({"displayName", "viewer", "version", "viewedAt"}, set(seen[0]))
        self.assertEqual([1, 1], [item["version"] for item in seen])
        self.assertTrue(all(item["viewedAt"] for item in seen))

    def test_only_the_owner_gets_seen_by(self) -> None:
        self.open_draft(self.viewer)
        self.assertNotIn("seenBy", self.sharing(self.collab_key)[1])
        self.assertNotIn("seenBy", self.sharing(self.boss_key)[1])
        status, _headers, body = self.page(self.collab)
        self.assertNotIn("Seen by", body)
        status, _headers, body = self.page(self.zach)
        self.assertIn("Seen by", body)
        self.assertIn("Vic Viewer", body)

    def test_seen_by_holds_at_most_ten_readers(self) -> None:
        for number in range(12):
            self.primary.limiter.entries.clear()  # Sign-in has its own rate limit.
            cookie = self.oidc_login(sub=f"reader{number}", email=f"reader{number}@example.test",
                                     name=f"Reader {number}", groups=["collabteam"])
            self.open_draft(cookie)
        _status, body = self.sharing(self.zach_key)
        self.assertEqual(10, len(body["seenBy"]))
        self.assertEqual("Reader 11", body["seenBy"][0]["displayName"])

    def test_seen_by_ignores_other_drafts_and_adds_no_stdout_pii(self) -> None:
        other = self.upload("<title>Other</title>", token=self.zach_key, workspace="collab", visibility="workspace")
        self.request(f"/d/{other['draftId']}/", cookies=[self.collab])
        self.assertEqual([], self.sharing(self.zach_key)[1]["seenBy"])
        self.assertNotIn("collab@example.test", self.audit_lines.getvalue())


class RosterPolicyTests(unittest.TestCase):
    def test_display_name_is_optional_and_checked(self) -> None:
        rule = {"match": {"group": "staff"}, "workspaces": {"team": "editor"}}
        self.assertIsNone(server.RBACPolicy({"rules": [rule]}).rules[0]["displayName"])
        named = server.RBACPolicy({"rules": [{**rule, "display_name": "  Staff Team "}]})
        self.assertEqual("Staff Team", named.rules[0]["displayName"])
        for bad in ("", "   ", 5, "x" * 81, "bad\nname"):
            with self.subTest(bad), self.assertRaises(ValueError):
                server.RBACPolicy({"rules": [{**rule, "display_name": bad}]})


class Round1ReviewTests(Wave1Case):
    """Regression tests for the Round 1 review findings F1 to F8."""

    def disable_draft(self) -> None:
        csrf = self.csrf(self.zach)
        status, _headers, _body = self.request(self.detail + "/disable", "POST", cookies=[self.zach],
                                               form={"csrf": csrf})
        self.assertEqual(303, status)

    def test_f1_disabled_draft_is_gone_from_the_sharing_api_for_non_owners(self) -> None:
        self.disable_draft()
        for name, key in (("editor", self.collab_key), ("viewer", self.viewer_key)):
            for method, suffix, payload in (("GET", "", None), ("POST", "", {"visibility": "link"}),
                                            ("POST", "/grants", {"value": "x@example.test"}),
                                            ("DELETE", "/grants", {"type": "email", "value": "x@example.test"})):
                with self.subTest(name=name, method=method, suffix=suffix):
                    status, body = self.sharing(key, method, payload, suffix)
                    self.assertEqual((404, NOT_FOUND_API), (status, body))
        self.assertEqual(200, self.sharing(self.zach_key)[0])
        self.assertEqual(200, self.sharing(self.boss_key)[0])

    def test_f2_hidden_policy_groups_cannot_be_probed(self) -> None:
        status, hidden = self.sharing(self.collab_key, "POST", {"value": "family"}, "/grants")
        status_none, unknown = self.sharing(self.collab_key, "POST", {"value": "nogroup"}, "/grants")
        self.assertEqual((400, 400), (status, status_none))
        self.assertEqual(hidden, unknown)  # A hidden group and an unknown word look the same.
        status, body = self.sharing(self.collab_key, "POST", {"value": "collabteam"}, "/grants")
        self.assertEqual(200, status)
        self.assertIn(("group", "collabteam"), [(grant["type"], grant["value"]) for grant in body["grants"]])
        status, body = self.sharing(self.collab_key, "POST", {"value": "family", "type": "group"}, "/grants")
        self.assertEqual(200, status)  # An explicit type stays unchanged.
        status, body = self.sharing(self.boss_key, "POST", {"value": "outsiders", "type": "auto"}, "/grants")
        self.assertEqual(200, status)  # An admin sees every policy group.
        self.assertIn(("group", "outsiders"), [(grant["type"], grant["value"]) for grant in body["grants"]])

    def test_f4_post_miss_answers_json_when_the_caller_wants_json(self) -> None:
        csrf = self.csrf(self.stranger)
        status, headers, body = self.request("/dashboard/drafts/zzzzzz-missing/sharing", "POST",
                                             cookies=[self.stranger], headers={"Accept": "application/json"},
                                             form={"csrf": csrf, "visibility": "link"})
        self.assertEqual((404, NOT_FOUND_API), (status, json.loads(body)))
        self.assertIn("application/json", headers["Content-Type"])
        status, headers, body = self.request("/dashboard/drafts/zzzzzz-missing/disable", "POST",
                                             cookies=[self.stranger], form={"csrf": csrf})
        self.assertEqual(404, status)
        self.assertIn("text/html", headers["Content-Type"])
        status, _headers, same = self.request("/dashboard/drafts/zzzzzz-missing", cookies=[self.stranger])
        self.assertEqual(body, same)  # The HTML miss is the same on GET and POST.

    def test_f6_write_misses_share_the_viewer_limiter(self) -> None:
        self.primary.viewer_404_limit = 2
        stranger_key = self.create_key(self.stranger)
        for _ in range(2):
            self.assertEqual(404, self.api("/api/drafts/zzzzzz-missing/sharing", "POST", {"visibility": "link"},
                                           token=stranger_key)[0])
        for method, path in (("POST", "/api/drafts/zzzzzz-missing/sharing/grants"),
                             ("DELETE", "/api/drafts/zzzzzz-missing/sharing/grants"),
                             ("GET", "/d/zzzzzz-missing")):
            with self.subTest(method=method, path=path):
                status, headers, _body = self.request(path, method, {"value": "x@example.test"},
                                                      token=stranger_key, headers={"Accept": "application/json"})
                self.assertEqual(429, status)
                self.assertGreaterEqual(int(headers["Retry-After"]), 1)
        status, headers, _body = self.request("/dashboard/drafts/zzzzzz-missing/disable", "POST",
                                              cookies=[self.stranger], form={"csrf": "x"})
        self.assertEqual(429, status)

    def test_f6_owner_writes_do_not_count_as_misses(self) -> None:
        self.primary.viewer_404_limit = 2
        for _ in range(5):
            self.assertEqual(200, self.sharing(self.zach_key, "POST", {"visibility": "workspace"})[0])

    def test_f7_reserved_action_names_are_neither_refs_nor_slugs(self) -> None:
        for name in ("disable", "enable", "delete", "favorite", "unfavorite", "archive", "unarchive", "tags",
                     "sharing"):
            with self.subTest(name):
                self.assertIsNone(server.DASHBOARD_DRAFT_RE.fullmatch(f"/dashboard/drafts/{name}"))
                self.assertIsNone(server.DASHBOARD_DRAFT_RE.fullmatch(f"/dashboard/drafts/{name}/delete"))
                self.assertIn(name, server.RESERVED_SLUGS)
                self.assertIsNotNone(server.slug_problem(name))
        for path, ref, action in (("/dashboard/drafts/plan-slug", "plan-slug", None),
                                  ("/dashboard/drafts/plan-slug/tags", "plan-slug", "tags"),
                                  ("/dashboard/drafts/tags-plan/sharing/grants", "tags-plan", "sharing/grants"),
                                  (self.detail + "/disable", self.draft_id, "disable")):
            with self.subTest(path):
                match = server.DASHBOARD_DRAFT_RE.fullmatch(path)
                self.assertEqual((ref, action), (match[1], match[2]))

    def test_f8_sharing_roster_runs_once_per_payload(self) -> None:
        original = server.Handler.sharing_roster
        for name, key in (("owner", self.zach_key), ("admin", self.boss_key), ("editor", self.collab_key)):
            with self.subTest(name), mock.patch.object(server.Handler, "sharing_roster", autospec=True,
                                                       side_effect=original) as roster:
                self.assertEqual(200, self.sharing(key)[0])
                self.assertEqual(1, roster.call_count)

    def test_f8_owner_private_fields_are_scrubbed_for_non_owners(self) -> None:
        csrf = self.csrf(self.zach)
        status, _headers, _body = self.request(self.detail + "/favorite", "POST", cookies=[self.zach],
                                               form={"csrf": csrf, "next": "/dashboard"})
        self.assertEqual(303, status)
        seen = {}
        for name, cookie in (("owner", self.zach), ("editor", self.collab), ("admin", self.boss)):
            with mock.patch.object(server, "render_detail", wraps=server.render_detail) as render:
                self.assertEqual(200, self.page(cookie)[0])
                seen[name] = render.call_args.args[2]["draft"]
        self.assertTrue(seen["owner"]["favorite"])
        for name in ("editor", "admin"):
            with self.subTest(name):
                draft = seen[name]
                self.assertEqual(([], False, None, False, None),
                                 (draft["tags"], draft["archived"], draft["archivedAt"], draft["favorite"],
                                  draft["favoritedAt"]))


class CompoundRuleCase(Wave1Case):
    policy = {**POLICY, "rules": [*POLICY["rules"],
                                  {"match": {"group": "pairs", "email": "pair@example.test"},
                                   "workspaces": {"collab": "editor"}, "display_name": "Pair Person"}]}


class Round1CompoundRuleTests(CompoundRuleCase):
    def test_f3_a_compound_rule_is_an_and_not_an_or(self) -> None:
        status, body = self.sharing(self.zach_key)
        people = {item["value"] for item in body["directory"]["people"]}
        groups = {item["value"] for item in body["directory"]["groups"]}
        self.assertNotIn("pair@example.test", people)  # No sign-in yet, and the rule needs both predicates.
        self.assertNotIn("pairs", groups)
        status, admin = self.sharing(self.boss_key)
        self.assertIn("pairs", {item["value"] for item in admin["directory"]["groups"]})  # Admin sees all.
        self.oidc_login(sub="pair", email="pair@example.test", name="Pair Person", groups=["pairs"])
        status, body = self.sharing(self.zach_key)
        self.assertIn("pair@example.test", {item["value"] for item in body["directory"]["people"]})
        self.assertNotIn("pairs", {item["value"] for item in body["directory"]["groups"]})

    def test_f2_a_compound_group_is_not_inferred_for_non_admins(self) -> None:
        status, body = self.sharing(self.collab_key, "POST", {"value": "pairs"}, "/grants")
        self.assertEqual(400, status, body)
        status, body = self.sharing(self.boss_key, "POST", {"value": "pairs"}, "/grants")
        self.assertEqual(200, status, body)


class Round1SeenByTests(Wave1Case):
    def view(self, actor: dict, detail: dict | None = None) -> None:
        self.store.audit("draft.view", actor=actor, target_type="draft", target_id=self.draft_id,
                         detail={"version": 1, "path": "index.html", **(detail or {})})

    def passive(self, login: str) -> None:
        self.view({"accountId": None, "email": login},
                  {"authMethod": "tailscale_header", "actorLogin": login})

    def test_f5_passive_owner_views_are_not_listed(self) -> None:
        self.passive("zach@example.test")
        self.passive("peer@example.test")
        status, body = self.sharing(self.zach_key)
        self.assertEqual(["peer@example.test"], [item["viewer"] for item in body["seenBy"]])

    def test_f5_a_passive_and_a_signed_in_view_count_once(self) -> None:
        self.assertEqual(200, self.request(f"/d/{self.draft_id}/", cookies=[self.viewer])[0])
        self.passive("viewer@example.test")
        status, body = self.sharing(self.zach_key)
        self.assertEqual(["viewer@example.test"], [item["viewer"] for item in body["seenBy"]])

    def test_f5_a_tailscale_login_matches_its_account_hash(self) -> None:
        account = server.sha256_text("tailscale:pal@github")[:32]
        self.view({"accountId": account, "email": None}, {"actorLogin": None})
        self.passive("pal@github")
        status, body = self.sharing(self.zach_key)
        self.assertEqual(["pal@github"], [item["viewer"] for item in body["seenBy"]])

    def test_f5_the_scan_reads_only_the_newest_view_rows(self) -> None:
        self.passive("old@example.test")
        self.passive("new@example.test")
        for _ in range(3):
            self.store.audit("draft.view", actor={"accountId": None, "email": "other@example.test"},
                             target_type="draft", target_id="someotherdraft", detail={"version": 1})
        with mock.patch.object(server, "DRAFT_VIEW_SCAN", 4):
            people = [item["viewer"] for item in self.store.draft_viewers(self.draft_id, "x", 10)]
        self.assertEqual(["new@example.test"], people)
        people = [item["viewer"] for item in self.store.draft_viewers(self.draft_id, "x", 10)]
        self.assertEqual(["new@example.test", "old@example.test"], people)

    def test_f5_account_name_reads_one_row(self) -> None:
        account = self.store.account_principal  # Principal rows exist for signed-in readers.
        self.assertIsNotNone(account)
        self.assertIsNone(self.store.account_name("nosuchaccount"))
        accounts = self.store.load_meta()["accounts"]
        known = next(iter(accounts))
        self.assertEqual(accounts[known]["name"], self.store.account_name(known))


if __name__ == "__main__":
    unittest.main()
