"""Tests for the tag, favorite, archive, and list commands of the Tailplan CLI and its SSH client."""
from __future__ import annotations

import importlib.machinery
import importlib.util
import json
import os
import shlex
import subprocess
import sys
import tempfile
import threading
import unittest
from http.client import HTTPConnection
from pathlib import Path
from unittest.mock import patch

import pytest

import tailplan_server as server

ROOT = Path(__file__).resolve().parents[1]
TOKEN = "bootstrap-test"


def _load_script(name: str, path: Path):
    loader = importlib.machinery.SourceFileLoader(name, str(path))
    spec = importlib.util.spec_from_loader(loader.name, loader)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    loader.exec_module(module)
    return module


share = _load_script("tailplan_share_tags", ROOT / "bin" / "tailplan-share")
remote = _load_script("tailplan_share_remote_tags", ROOT / "bin" / "tailplan-share-remote")
guard = _load_script("tailplan_publish_guard_tags", ROOT / "bin" / "tailplan-publish-guard")
REMOTE_CLIENT = ROOT / "bin" / "tailplan-share-remote"


class CliServerCase(unittest.TestCase):
    """Run the real CLI against a real local server."""

    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.store = server.Store(self.root / "data")
        self.httpd, _ = server.create_servers(
            ("127.0.0.1", 0), None, store=self.store, token=TOKEN, base_url="", redirect_view_base_url="",
        )
        self.base = f"http://127.0.0.1:{self.httpd.server_port}"
        self.httpd.base_url = self.base
        thread = threading.Thread(target=self.httpd.serve_forever, daemon=True)
        thread.start()
        self.addCleanup(thread.join)
        self.addCleanup(self.httpd.server_close)
        self.addCleanup(self.httpd.shutdown)
        self.home = self.root / "home"
        (self.home / ".tailplan").mkdir(parents=True)
        token_path = self.home / ".tailplan" / "token"
        token_path.write_text(TOKEN + "\n", encoding="utf-8")
        token_path.chmod(0o600)
        self.work = self.root / "work"
        self.work.mkdir()
        self.env = {**os.environ, "HOME": str(self.home), "TAILPLAN_BASE_URL": self.base}
        for name in ("TAILPLAN_API_KEY", "TAILPLAN_TOKEN_FILE", "TAILPLAN_PROJECT", "TAILPLAN_TAGS"):
            self.env.pop(name, None)

    def cli(self, *args: str, env: dict | None = None, ok: bool = True) -> subprocess.CompletedProcess[str]:
        completed = subprocess.run(
            [sys.executable, str(ROOT / "bin" / "tailplan"), *args],
            cwd=self.work, env={**self.env, **(env or {})}, capture_output=True, text=True,
            timeout=20, check=False,
        )
        if ok:
            self.assertEqual(0, completed.returncode, completed.stdout + completed.stderr)
        return completed

    def write(self, name: str, title: str, body: str = "text") -> Path:
        path = self.work / name
        path.write_text(f"# {title}\n\n{body}\n", encoding="utf-8")
        return path

    def upload(self, name: str, title: str, *flags: str, env: dict | None = None, body: str = "text") -> dict:
        source = self.write(name, title, body)
        return json.loads(self.cli("upload", str(source), "--json", *flags, env=env).stdout)

    def get(self, path: str) -> tuple[int, bytes]:
        connection = HTTPConnection("127.0.0.1", self.httpd.server_port, timeout=5)
        try:
            connection.request("GET", path, headers={"Authorization": "Bearer " + TOKEN})
            response = connection.getresponse()
            return response.status, response.read()
        finally:
            connection.close()

    def draft(self, ref: str) -> dict:
        status, body = self.get("/api/drafts")
        self.assertEqual(200, status)
        return next(item for item in json.loads(body)["drafts"] if ref in (item["draftId"], item["slug"]))

    def listed(self, *flags: str) -> list[dict]:
        return json.loads(self.cli("list", "--json", *flags).stdout)


class UploadTagTests(CliServerCase):
    def test_tag_and_favorite_flags_reach_the_new_draft(self) -> None:
        result = self.upload("plan.md", "Plan", "--tag", "dawn", "--tag", "Epic", "--favorite")
        draft = self.draft(result["draftId"])
        self.assertEqual(["dawn", "epic"], draft["tags"])
        self.assertTrue(draft["favorite"])

    def test_tags_may_be_comma_separated(self) -> None:
        result = self.upload("plan.md", "Plan", "--tag", "dawn,epic", "--tag", "x.y_z-1")
        self.assertEqual(["dawn", "epic", "x.y_z-1"], self.draft(result["draftId"])["tags"])

    def test_invalid_tags_fail_before_any_upload(self) -> None:
        source = self.write("plan.md", "Plan")
        for bad in ("Bad Tag!", "-x", "a" * 33, ".dot"):
            completed = self.cli("upload", str(source), "--tag", bad, ok=False)
            self.assertEqual(2, completed.returncode, bad)
            self.assertIn("Invalid", completed.stdout + completed.stderr)
        too_many = ",".join(f"t{index}" for index in range(11))
        self.assertEqual(2, self.cli("upload", str(source), "--tag", too_many, ok=False).returncode)
        self.assertEqual([], self.listed())

    def test_project_and_tags_env_apply_to_a_new_draft(self) -> None:
        env = {"TAILPLAN_PROJECT": "My Project", "TAILPLAN_TAGS": "Dawn, epic,,bad tag"}
        result = self.upload("plan.md", "Plan", env=env)
        self.assertEqual(["bad-tag", "dawn", "epic", "my-project"], self.draft(result["draftId"])["tags"])

    def test_env_tags_join_explicit_tags(self) -> None:
        result = self.upload("plan.md", "Plan", "--tag", "dawn", env={"TAILPLAN_PROJECT": "taildoc"})
        self.assertEqual(["dawn", "taildoc"], self.draft(result["draftId"])["tags"])

    def test_the_working_folder_name_is_never_a_tag(self) -> None:
        result = self.upload("plan.md", "Plan")
        self.assertEqual([], self.draft(result["draftId"])["tags"])

    def test_reupload_without_tag_keeps_existing_tags(self) -> None:
        first = self.upload("plan.md", "Plan", "--tag", "dawn")
        self.cli("tag", first["draftId"], "+epic")
        second = self.upload("plan.md", "Plan", body="changed")
        self.assertEqual(first["draftId"], second["draftId"])
        self.assertEqual(2, second["versionNumber"])
        self.assertEqual(["dawn", "epic"], self.draft(first["draftId"])["tags"])

    def test_reupload_keeps_edited_tags_even_when_env_tags_are_set(self) -> None:
        env = {"TAILPLAN_PROJECT": "dawn"}
        first = self.upload("plan.md", "Plan", env=env)
        self.cli("tag", first["draftId"], "--set", "epic")
        self.upload("plan.md", "Plan", env=env, body="changed")
        self.assertEqual(["epic"], self.draft(first["draftId"])["tags"])

    def test_reupload_without_favorite_keeps_the_favorite(self) -> None:
        first = self.upload("plan.md", "Plan", "--favorite")
        self.upload("plan.md", "Plan", body="changed")
        self.assertTrue(self.draft(first["draftId"])["favorite"])

    def test_slug_update_without_a_binding_keeps_edited_tags(self) -> None:
        env = {"TAILPLAN_PROJECT": "dawn"}
        first = self.upload("a.md", "A", "--slug", "my-plan", "--tag", "keep")
        self.cli("tag", first["draftId"], "--set", "edited")
        second = self.upload("b.md", "B", "--slug", "my-plan", env=env)
        self.assertEqual(first["draftId"], second["draftId"])
        self.assertEqual(["edited"], self.draft(first["draftId"])["tags"])

    def test_env_tags_apply_to_a_new_slug(self) -> None:
        result = self.upload("a.md", "A", "--slug", "fresh-plan", env={"TAILPLAN_PROJECT": "dawn"})
        self.assertEqual(["dawn"], self.draft(result["draftId"])["tags"])

    def test_slug_that_names_another_draft_keeps_its_tags_on_retry(self) -> None:
        env = {"TAILPLAN_PROJECT": "dawn"}
        target = self.upload("target.md", "Target", "--slug", "target-plan", "--tag", "keep")
        self.cli("tag", target["draftId"], "--set", "edited")
        bound = self.upload("a.md", "A")
        self.assertEqual([], self.draft(bound["draftId"])["tags"])
        moved = self.upload("a.md", "A", "--slug", "target-plan", env=env, body="changed")
        self.assertEqual(target["draftId"], moved["draftId"])
        self.assertEqual(["edited"], self.draft(target["draftId"])["tags"])

    def test_reupload_with_tag_replaces_the_tags(self) -> None:
        first = self.upload("plan.md", "Plan", "--tag", "dawn")
        self.upload("plan.md", "Plan", "--tag", "epic", body="changed")
        self.assertEqual(["epic"], self.draft(first["draftId"])["tags"])


class TagCommandTests(CliServerCase):
    def test_add_and_remove_tokens_including_dash_prefixed_tags(self) -> None:
        ref = self.upload("plan.md", "Plan", "--tag", "dawn,old-tag,x")["draftId"]
        result = json.loads(self.cli("tag", ref, "+a", "-old-tag", "-x", "+b,c", "--json").stdout)
        self.assertEqual(["a", "b", "c", "dawn"], result["draft"]["tags"])
        self.assertEqual(["a", "b", "c", "dawn"], self.draft(ref)["tags"])

    def test_tokens_may_follow_options(self) -> None:
        ref = self.upload("plan.md", "Plan", "--tag", "x")["draftId"]
        completed = self.cli("tag", ref, "--json", "+a", "-x", "--base-url", self.base)
        self.assertEqual(["a"], json.loads(completed.stdout)["draft"]["tags"])

    def test_human_output_lists_the_tags(self) -> None:
        ref = self.upload("plan.md", "Plan")["draftId"]
        self.assertIn("a, b", self.cli("tag", ref, "+a", "+b").stdout)
        self.assertIn("none", self.cli("tag", ref, "--set", "").stdout)

    def test_set_replaces_and_empty_set_clears(self) -> None:
        ref = self.upload("plan.md", "Plan", "--tag", "dawn,epic")["draftId"]
        self.cli("tag", ref, "--set", "a,b")
        self.assertEqual(["a", "b"], self.draft(ref)["tags"])
        self.cli("tag", ref, "--set", "")
        self.assertEqual([], self.draft(ref)["tags"])

    def test_set_combines_with_add_and_remove_in_one_request(self) -> None:
        ref = self.upload("plan.md", "Plan", "--tag", "dawn")["draftId"]
        self.cli("tag", ref, "--set", "a,b", "+c", "-a")
        self.assertEqual(["b", "c"], self.draft(ref)["tags"])

    def test_tag_accepts_a_slug_and_a_url(self) -> None:
        result = self.upload("plan.md", "Plan", "--slug", "my-plan")
        self.cli("tag", "my-plan", "+a")
        self.cli("tag", result["publicUrl"], "+b")
        self.assertEqual(["a", "b"], self.draft("my-plan")["tags"])

    def test_tag_without_a_change_or_with_a_bad_tag_fails(self) -> None:
        ref = self.upload("plan.md", "Plan", "--tag", "dawn")["draftId"]
        none = self.cli("tag", ref, ok=False)
        self.assertEqual(1, none.returncode)
        self.assertIn("Give +tag", none.stdout + none.stderr)
        for bad in ("+Bad Tag", "+a" + "b" * 40):
            completed = self.cli("tag", ref, bad, ok=False)
            self.assertEqual(1, completed.returncode, bad)
        self.assertEqual(["dawn"], self.draft(ref)["tags"])

    def test_list_tag_must_be_exactly_one_valid_tag(self) -> None:
        self.upload("plan.md", "Plan", "--tag", "dawn")
        for bad in ("", " ", ",", "a,b", "-bad", "Bad Tag"):
            completed = self.cli("list", f"--tag={bad}", ok=False)
            output = completed.stdout + completed.stderr
            self.assertEqual(1, completed.returncode, repr(bad))
            self.assertIn("Invalid tag", output, repr(bad))
            self.assertNotIn("Traceback", output, repr(bad))
            self.assertNotIn("Request failed", output, repr(bad))
            as_json = self.cli("list", f"--tag={bad}", "--json", ok=False)
            report = json.loads(as_json.stdout)
            self.assertFalse(report["ok"])
            self.assertIn("Invalid tag", report["error"])

    def test_list_tag_accepts_one_tag_in_any_case(self) -> None:
        self.upload("plan.md", "Plan", "--tag", "dawn")
        self.assertEqual(1, len(self.listed("--tag", " DAWN ")))

    def test_tag_for_a_missing_draft_fails(self) -> None:
        self.assertEqual(1, self.cli("tag", "nosuchdraft", "+a", ok=False).returncode)


class ArchiveTests(CliServerCase):
    def test_archive_hides_the_draft_but_keeps_its_urls_live(self) -> None:
        result = self.upload("plan.md", "Plan", "--slug", "my-plan")
        ref = result["draftId"]
        self.assertIn("links still work", self.cli("archive", ref).stdout)
        self.assertEqual([], self.listed())
        archived = self.listed("--archived")
        self.assertEqual([ref], [item["draftId"] for item in archived])
        self.assertTrue(archived[0]["archived"])
        for path in (f"/d/{ref}", "/d/my-plan", "/d/my-plan/share", f"/d/{ref}/raw"):
            self.assertEqual(200, self.get(path)[0], path)

    def test_unarchive_brings_the_draft_back(self) -> None:
        ref = self.upload("plan.md", "Plan")["draftId"]
        self.cli("archive", ref)
        self.assertIn("Restored", self.cli("unarchive", ref).stdout)
        self.assertEqual([ref], [item["draftId"] for item in self.listed()])
        self.assertFalse(self.draft(ref)["archived"])
        self.assertEqual(200, self.get(f"/d/{ref}")[0])

    def test_archive_json_returns_the_updated_draft(self) -> None:
        ref = self.upload("plan.md", "Plan")["draftId"]
        result = json.loads(self.cli("archive", ref, "--json").stdout)
        self.assertTrue(result["draft"]["archived"])
        result = json.loads(self.cli("unarchive", ref, "--json").stdout)
        self.assertFalse(result["draft"]["archived"])

    def test_disable_still_turns_the_url_off(self) -> None:
        ref = self.upload("plan.md", "Plan")["draftId"]
        self.cli("disable", ref)
        self.assertNotEqual(200, self.get(f"/d/{ref}")[0])


class ListTests(CliServerCase):
    def setUp(self) -> None:
        super().setUp()
        self.alpha = self.upload("a.md", "Alpha", "--tag", "dawn", "--tag", "epic", "--favorite", body="quasar")
        self.beta = self.upload("b.md", "Beta", "--tag", "dusk", body="pulsar")
        self.gamma = self.upload("c.md", "Gamma", body="nebula")

    def ids(self, *flags: str) -> list[str]:
        return [item["draftId"] for item in self.listed(*flags)]

    def test_tag_filter(self) -> None:
        self.assertEqual([self.alpha["draftId"]], self.ids("--tag", "dawn"))
        self.assertEqual([self.alpha["draftId"]], self.ids("--tag", "DAWN"))
        self.assertEqual([], self.ids("--tag", "nothing"))

    def test_query_filter_and_operators(self) -> None:
        self.assertEqual([self.alpha["draftId"]], self.ids("--q", "is:fav"))
        self.assertEqual([self.beta["draftId"]], self.ids("--q", "tag:dusk"))
        self.assertEqual([self.gamma["draftId"]], self.ids("--q", "nebula"))

    def test_view_filter(self) -> None:
        self.assertEqual([self.alpha["draftId"]], self.ids("--view", "favorites"))
        self.cli("archive", self.beta["draftId"])
        self.assertEqual([self.beta["draftId"]], self.ids("--view", "archived"))
        self.assertEqual(3, len(self.ids("--view", "all")))

    def test_archived_flag_includes_archived_drafts(self) -> None:
        self.cli("archive", self.beta["draftId"])
        self.assertEqual(2, len(self.ids()))
        self.assertEqual(3, len(self.ids("--archived")))

    def test_sort_and_direction(self) -> None:
        by_title = [item["title"] for item in self.listed("--sort", "title", "--dir", "asc")]
        self.assertEqual(["Alpha", "Beta", "Gamma"], by_title)
        by_title = [item["title"] for item in self.listed("--sort", "title", "--dir", "desc")]
        self.assertEqual(["Gamma", "Beta", "Alpha"], by_title)

    def test_human_output_shows_tags_and_the_archived_marker(self) -> None:
        self.cli("archive", self.alpha["draftId"])
        text = self.cli("list", "--archived").stdout
        self.assertIn("Alpha [archived]", text)
        self.assertIn("#dawn #epic", text)
        self.assertEqual(1, text.count("[archived]"))

    def test_plain_list_still_works_and_empty_list_hints_at_upload(self) -> None:
        self.assertEqual(3, len(self.ids()))
        empty = self.cli("list", "--tag", "nothing")
        self.assertIn("No drafts yet", empty.stdout)

    def test_bad_filter_values_are_rejected(self) -> None:
        self.assertEqual(2, self.cli("list", "--view", "nope", ok=False).returncode)
        self.assertEqual(2, self.cli("list", "--dir", "sideways", ok=False).returncode)
        self.assertEqual(1, self.cli("list", "--tag", "Bad Tag", ok=False).returncode)


class HelperTests(unittest.TestCase):
    def test_parse_tags_normalizes_and_deduplicates(self) -> None:
        self.assertEqual(["a", "b-c"], share.parse_tags(["A, b-c", "a"]))
        self.assertEqual([], share.parse_tags(["", " , "]))

    def test_parse_tags_rejects_bad_values(self) -> None:
        for bad in ("-a", "a b", "a;b", "é", "a" * 33, "a/b"):
            with self.assertRaises(ValueError, msg=bad):
                share.parse_tags([bad])
        with self.assertRaises(ValueError):
            share.parse_tags([",".join(f"t{index}" for index in range(11))])

    def test_env_tags_skip_junk_and_cap_the_count(self) -> None:
        env = {"TAILPLAN_PROJECT": "  Silent Cartographer! ", "TAILPLAN_TAGS": "a,,,B,a,---"}
        self.assertEqual(["silent-cartographer", "a", "b"], share.default_tags_from_env(env))
        many = {"TAILPLAN_TAGS": ",".join(f"t{index}" for index in range(30))}
        self.assertEqual(10, len(share.default_tags_from_env(many)))
        self.assertEqual([], share.default_tags_from_env({}))

    def test_default_tags_join_only_new_drafts(self) -> None:
        self.assertEqual(["a", "b"], share.tags_for_upload(["a"], ["b"], new_draft=True))
        self.assertEqual(["a"], share.tags_for_upload(["a"], ["b"], new_draft=False))
        self.assertIsNone(share.tags_for_upload(None, ["b"], new_draft=False))
        self.assertEqual(["b"], share.tags_for_upload(None, ["b"], new_draft=True))
        self.assertIsNone(share.tags_for_upload(None, None, new_draft=True))

    def test_tag_tokens_become_options(self) -> None:
        rewritten = share.rewrite_tag_tokens(["tag", "ref", "+a", "-old", "--set", "-x", "--json", "-h"])
        self.assertEqual(["tag", "ref", "--add=a", "--remove=old", "--set", "-x", "--json", "-h"], rewritten)
        self.assertEqual(["list", "-x"], share.rewrite_tag_tokens(["list", "-x"]))


class FakeSsh:
    """A fake ssh and scp pair that logs each call and answers like a Tailplan host."""

    SCRIPT = '''#!/usr/bin/env python3
import json, os, sys
args = sys.argv[1:]
with open(os.environ["CLIENT_LOG"], "a") as output:
    output.write(json.dumps([os.path.basename(sys.argv[0])] + args) + "\\n")
if "mktemp" in args:
    print("/tmp/tailplan-upload.Ab12Cd34")
elif "check" in args:
    print(json.dumps({"ok": True, "slug": {"slug": "x", "status": os.environ.get("CHECK_STATUS", "available")}}))
elif "tailplan-share" in args:
    print(json.dumps({"ok": True, "draftId": "remote123", "versionNumber": 1,
        "publicUrl": "https://tailplan.example.test/d/remote123",
        "rawUrl": "https://tailplan.example.test/d/remote123/raw"}))
'''

    def __init__(self, tmp_path: Path) -> None:
        self.bin = tmp_path / "bin"
        self.bin.mkdir()
        self.log = tmp_path / "calls.jsonl"
        for name in ("ssh", "scp"):
            path = self.bin / name
            path.write_text(self.SCRIPT)
            path.chmod(0o755)
        self.env = {
            **{key: value for key, value in os.environ.items()
               if key not in {"TAILPLAN_PROJECT", "TAILPLAN_TAGS", "TAILPLAN_SSH_TARGET"}},
            "HOME": str(tmp_path), "PATH": str(self.bin) + os.pathsep + os.environ["PATH"],
            "CLIENT_LOG": str(self.log),
        }

    def run(self, *args: str, env: dict | None = None) -> subprocess.CompletedProcess[str]:
        return subprocess.run([sys.executable, str(REMOTE_CLIENT), *args], env={**self.env, **(env or {})},
                              capture_output=True, text=True, check=False)

    def calls(self) -> list[list[str]]:
        if not self.log.exists():
            return []
        return [json.loads(line) for line in self.log.read_text().splitlines()]

    def remote_words(self) -> list[str]:
        """Return the words that the remote shell receives for the last tailplan-share call."""
        call = [call for call in self.calls() if "tailplan-share" in call and "check" not in call][-1]
        return shlex.split(" ".join(call[call.index("tailplan-share") + 1:]))


@pytest.fixture
def fake_ssh(tmp_path: Path) -> FakeSsh:
    return FakeSsh(tmp_path)


def test_remote_upload_forwards_tags_and_favorite(tmp_path: Path, fake_ssh: FakeSsh) -> None:
    source = tmp_path / "plan.md"
    source.write_text("# Plan\n")
    completed = fake_ssh.run(str(source), "--new", "--tag", "Dawn", "--tag", "epic,x", "--favorite", "--json")
    assert completed.returncode == 0, completed.stderr
    words = fake_ssh.remote_words()
    tags = [words[index + 1] for index, word in enumerate(words) if word == "--tag"]
    assert tags == ["dawn", "epic", "x"]
    assert "--favorite" in words


def test_remote_upload_forwards_env_tags_only_for_a_new_draft(tmp_path: Path, fake_ssh: FakeSsh) -> None:
    source = tmp_path / "plan.md"
    source.write_text("# Plan\n")
    env = {"TAILPLAN_PROJECT": "Taildoc", "TAILPLAN_TAGS": "dawn,bad tag"}
    assert fake_ssh.run(str(source), "--json", env=env).returncode == 0
    words = fake_ssh.remote_words()
    assert [words[i + 1] for i, word in enumerate(words) if word == "--tag"] == ["taildoc", "dawn", "bad-tag"]
    assert fake_ssh.run(str(source), "--json", env=env).returncode == 0
    assert "--tag" not in fake_ssh.remote_words()
    assert "--draft" in fake_ssh.remote_words()


def test_remote_upload_never_tags_from_the_working_folder(tmp_path: Path, fake_ssh: FakeSsh) -> None:
    source = tmp_path / "plan.md"
    source.write_text("# Plan\n")
    assert fake_ssh.run(str(source), "--new", "--json").returncode == 0
    assert "--tag" not in fake_ssh.remote_words()
    assert "--favorite" not in fake_ssh.remote_words()


@pytest.mark.parametrize("tag", ["Bad Tag", "-x", "a;id", "$(id)", "a" * 33, "a`id`", "é"])
def test_remote_upload_rejects_unsafe_tags_before_ssh(tmp_path: Path, fake_ssh: FakeSsh, tag: str) -> None:
    source = tmp_path / "plan.md"
    source.write_text("# Plan\n")
    completed = fake_ssh.run(str(source), "--new", "--tag", tag)
    assert completed.returncode != 0
    assert fake_ssh.calls() == []


def test_remote_upload_rejects_more_than_ten_tags(tmp_path: Path, fake_ssh: FakeSsh) -> None:
    source = tmp_path / "plan.md"
    source.write_text("# Plan\n")
    completed = fake_ssh.run(str(source), "--new", "--tag", ",".join(f"t{index}" for index in range(11)))
    assert completed.returncode == 2
    assert fake_ssh.calls() == []


def test_remote_tag_forwards_atomic_changes(fake_ssh: FakeSsh) -> None:
    completed = fake_ssh.run("tag", "https://plans.example.test/d/abc123/share", "+Dawn", "-old-tag",
                             "+a,b", "--set", "x,y", "--json")
    assert completed.returncode == 0, completed.stderr
    call = fake_ssh.calls()[-1]
    assert call[:4] == ["ssh", "tailplan-server", "tailplan-share", "tag"]
    assert fake_ssh.remote_words() == ["tag", "abc123", "--add", "dawn", "--add", "a", "--add", "b",
                                       "--remove", "old-tag", "--set", "x,y", "--json"]


def test_remote_tag_can_clear_all_tags(fake_ssh: FakeSsh) -> None:
    assert fake_ssh.run("tag", "abc123", "--set", "").returncode == 0
    assert fake_ssh.remote_words() == ["tag", "abc123", "--set", ""]


def test_remote_tag_accepts_a_target(fake_ssh: FakeSsh) -> None:
    assert fake_ssh.run("--target", "publisher-host", "tag", "abc123", "+a").returncode == 0
    assert fake_ssh.calls()[-1][:3] == ["ssh", "publisher-host", "tailplan-share"]


@pytest.mark.parametrize("arguments", [
    ["abc123"], ["abc123", "+Bad Tag"], ["abc123", "+a;id"], ["abc123", "-$(id)"], ["abc123", "+a`id`"],
    ["abc123", "--set", "a,b c"], ["abc123", "--add", "-x"], ["../etc", "+a"], ["-bad", "+a"],
    ["abc123", "--unknown", "x"], ["abc123", "+a" + "b" * 40],
    ["abc123", "--set", ",".join(f"t{index}" for index in range(11))],
])
def test_remote_tag_rejects_unsafe_arguments_before_ssh(fake_ssh: FakeSsh, arguments: list[str]) -> None:
    completed = fake_ssh.run("tag", *arguments)
    assert completed.returncode != 0
    assert fake_ssh.calls() == []


@pytest.mark.parametrize("command", ["archive", "unarchive"])
def test_remote_archive_forwards_one_parsed_ref(fake_ssh: FakeSsh, command: str) -> None:
    completed = fake_ssh.run(command, "https://plans.example.test/d/my-plan/share", "--json")
    assert completed.returncode == 0, completed.stderr
    assert fake_ssh.remote_words() == [command, "my-plan", "--json"]


@pytest.mark.parametrize("command", ["archive", "unarchive"])
def test_remote_archive_rejects_unsafe_refs(fake_ssh: FakeSsh, command: str) -> None:
    for ref in ("$(id)", "a;b", "../x", "A B"):
        assert fake_ssh.run(command, ref).returncode != 0, ref
    assert fake_ssh.calls() == []


@pytest.mark.parametrize("arguments", [
    ["--base-url=https://attacker.test", "my-plan"], ["my-plan", "--base-url=https://attacker.test"],
    ["my-plan", "--base-url", "https://attacker.test"], ["my-plan", "extra"], ["-bad"], [], ["--force", "my-plan"],
    ["my-plan", "--json", "--reason", "x"], ["a", "b"],
])
@pytest.mark.parametrize("command", ["archive", "unarchive"])
def test_remote_archive_accepts_only_a_ref_and_json(fake_ssh: FakeSsh, command: str, arguments: list[str]) -> None:
    completed = fake_ssh.run(command, *arguments)
    assert completed.returncode != 0
    assert fake_ssh.calls() == []


def test_remote_slug_update_keeps_edited_tags(tmp_path: Path, fake_ssh: FakeSsh) -> None:
    source = tmp_path / "plan.md"
    source.write_text("# Plan\n")
    env = {"TAILPLAN_PROJECT": "dawn", "CHECK_STATUS": "yours"}
    assert fake_ssh.run(str(source), "--slug", "my-plan", "--json", env=env).returncode == 0
    assert "--tag" not in fake_ssh.remote_words()
    assert any("check" in call for call in fake_ssh.calls())


def test_remote_new_slug_gets_env_tags(tmp_path: Path, fake_ssh: FakeSsh) -> None:
    source = tmp_path / "plan.md"
    source.write_text("# Plan\n")
    env = {"TAILPLAN_PROJECT": "dawn", "CHECK_STATUS": "available"}
    assert fake_ssh.run(str(source), "--slug", "my-plan", "--json", env=env).returncode == 0
    words = fake_ssh.remote_words()
    assert [words[i + 1] for i, word in enumerate(words) if word == "--tag"] == ["dawn"]


def test_remote_slug_with_unknown_status_keeps_tags_off(tmp_path: Path, fake_ssh: FakeSsh) -> None:
    source = tmp_path / "plan.md"
    source.write_text("# Plan\n")
    env = {"TAILPLAN_PROJECT": "dawn", "CHECK_STATUS": "unchecked"}
    assert fake_ssh.run(str(source), "--slug", "my-plan", "--json", env=env).returncode == 0
    assert "--tag" not in fake_ssh.remote_words()


def test_remote_slug_without_env_tags_skips_the_check(tmp_path: Path, fake_ssh: FakeSsh) -> None:
    source = tmp_path / "plan.md"
    source.write_text("# Plan\n")
    assert fake_ssh.run(str(source), "--slug", "my-plan", "--json").returncode == 0
    assert not any("check" in call for call in fake_ssh.calls())


@pytest.mark.parametrize("arguments", [
    ["upload", "{file}", "--new", "--tag", "Bad Tag"],
    ["tag", "abc123", "+Bad Tag"],
    ["list", "--tag=a,b"],
    ["list", "--tag="],
    ["list", "--view", "bogus"],
    ["archive", "--base-url=https://attacker.test", "my-plan"],
    ["unarchive", "a", "b"],
])
def test_remote_validation_failures_print_json_with_json_flag(
    tmp_path: Path, fake_ssh: FakeSsh, arguments: list[str],
) -> None:
    source = tmp_path / "plan.md"
    source.write_text("# Plan\n")
    words = [word.replace("{file}", str(source)) for word in arguments]
    completed = fake_ssh.run(*words, "--json")
    assert completed.returncode == 2
    report = json.loads(completed.stdout)
    assert report["ok"] is False
    assert report["category"] == "usage"
    assert report["error"]
    assert fake_ssh.calls() == []


def test_remote_validation_failures_stay_plain_without_json(fake_ssh: FakeSsh) -> None:
    completed = fake_ssh.run("list", "--tag=a,b")
    assert completed.returncode == 2
    assert completed.stdout == ""
    assert "Invalid tag" in completed.stderr


def test_remote_list_forwards_each_filter_quoted(fake_ssh: FakeSsh) -> None:
    completed = fake_ssh.run("list", "--tag", "Dawn", "--view", "all", "--q", "is:fav tag:dawn v:>1; $(id)",
                             "--sort", "title", "--dir", "desc", "--repo", "justcarlson/taildoc",
                             "--archived", "--json")
    assert completed.returncode == 0, completed.stderr
    assert fake_ssh.remote_words() == [
        "list", "--json", "--tag", "dawn", "--view", "all", "--sort", "title", "--dir", "desc",
        "--q", "is:fav tag:dawn v:>1; $(id)", "--repo", "justcarlson/taildoc", "--archived",
    ]


def test_remote_plain_list_is_unchanged(fake_ssh: FakeSsh) -> None:
    assert fake_ssh.run("list", "--json").returncode == 0
    assert fake_ssh.calls() == [["ssh", "tailplan-server", "tailplan-share", "list", "--json"]]


@pytest.mark.parametrize("arguments", [
    ["--tag", "Bad Tag"], ["--tag", "a;id"], ["--tag", "-x"], ["--tag="], ["--tag= "], ["--tag=,"],
    ["--tag=a,b"], ["--tag=-bad"], ["--view", "bogus"], ["--sort", "random"],
    ["--dir", "up"], ["--repo", "a b"], ["--repo", "-x"], ["--repo", "a;id"], ["--q", "bad\x01text"],
    ["--q", "x" * 201], ["--q", "-flag"], ["--unknown"], ["extra"], ["--tag", "a", "--base-url", "http://x"],
])
def test_remote_list_rejects_unsafe_arguments_before_ssh(fake_ssh: FakeSsh, arguments: list[str]) -> None:
    completed = fake_ssh.run("list", *arguments)
    assert completed.returncode != 0
    assert fake_ssh.calls() == []


def test_remote_share_file_builds_quoted_tag_words(tmp_path: Path) -> None:
    source = tmp_path / "plan.md"
    source.write_text("# Plan\n")
    calls: list[list[str]] = []

    def run(command, **kwargs):
        calls.append(command)
        return subprocess.CompletedProcess(command, 0, "/tmp/tailplan-upload.Ab12Cd34\n" if "mktemp" in command else "", "")

    remote.share_file(source, target="tailplan-server", new=True, draft=None, run=run,
                      tags=["dawn", "epic"], favorite=True)
    published = next(call for call in calls if call[:3] == ["ssh", "tailplan-server", "tailplan-share"])
    assert published[-5:] == ["--tag", "dawn", "--tag", "epic", "--favorite"]
    calls.clear()
    with pytest.raises(ValueError):
        remote.share_file(source, target="tailplan-server", new=True, draft=None, run=run, tags=["a;id"])
    assert calls == []


def run_guard(argv: list[str]) -> int | None:
    """Run the SSH guard on a command. Return its exit code, or None when it hands over to the publisher."""
    with (
        patch.object(guard, "configured_share_command", return_value="/publisher"),
        patch.object(guard, "_verify_staged_file"),
        patch.object(guard.os, "execv", side_effect=SystemExit(0)),
    ):
        try:
            guard.execute(["tailplan-share", *argv])
        except SystemExit as exited:
            return exited.code
    return None


@pytest.mark.parametrize("argv", [
    ["archive", "my-plan"], ["unarchive", "my-plan", "--json"],
    ["tag", "my-plan", "--add", "dawn"], ["tag", "my-plan", "--remove", "old-tag", "--add", "a", "--json"],
    ["tag", "my-plan", "--set", "a,b"], ["tag", "my-plan", "--set", ""],
    ["list", "--json"], ["list", "--tag", "dawn", "--view", "archived", "--sort", "title", "--dir", "asc"],
    ["list", "--q", "is:fav tag:dawn v:>1", "--repo", "org/name", "--archived", "--json"],
])
def test_guard_allows_the_tag_archive_and_list_commands(argv: list[str]) -> None:
    assert run_guard(argv) == 0


@pytest.mark.parametrize("argv", [
    ["archive", "-bad"], ["archive", "my-plan", "--force"], ["tag", "my-plan"], ["tag", "my-plan", "--json"],
    ["tag", "my-plan", "--add", "-x"], ["tag", "my-plan", "--add", "A"], ["tag", "my-plan", "--add", "a;id"],
    ["tag", "my-plan", "--set", "a,"], ["tag", "my-plan", "--set", "a", "--set", "b"], ["tag", "-x", "--add", "a"],
    ["tag", "my-plan", "--add"], ["tag", "my-plan", "+a"], ["tag", "../x", "--add", "a"],
    ["tag", "my-plan", "--json", "--json", "--add", "a"],
    ["list", "--view", "bogus"], ["list", "--sort", "random"], ["list", "--tag", "A"], ["list", "--tag"],
    ["list", "--repo", "-x"], ["list", "--q", "-flag"], ["list", "--q", "a\nb"], ["list", "--archived", "--archived"],
    ["list", "--unknown"], ["list", "extra"],
])
def test_guard_rejects_unsafe_tag_archive_and_list_commands(argv: list[str]) -> None:
    assert run_guard(argv) == 126


def test_guard_limits_tag_words_on_add_and_remove() -> None:
    adds = [word for index in range(11) for word in ("--add", f"t{index}")]
    assert run_guard(["tag", "my-plan", *adds]) == 126
    assert run_guard(["tag", "my-plan", *adds[:20]]) == 0


def test_guard_allows_tag_and_favorite_on_publish() -> None:
    source = "/tmp/tailplan-upload.Ab12Cd34/plan.md"
    assert run_guard([source, "--new", "--tag", "dawn", "--tag", "epic", "--favorite", "--json"]) == 0
    many = [word for index in range(10) for word in ("--tag", f"t{index}")]
    assert run_guard([source, "--new", *many, "--favorite"]) == 0


@pytest.mark.parametrize("options", [
    ["--tag"], ["--tag", "Bad"], ["--tag", "-x"], ["--tag", "a;id"], ["--tag", "a" * 33],
    ["--favorite", "--favorite"], ["--favorite", "yes"],
    [word for index in range(11) for word in ("--tag", f"t{index}")],
])
def test_guard_rejects_unsafe_tag_options_on_publish(options: list[str]) -> None:
    assert run_guard(["/tmp/tailplan-upload.Ab12Cd34/plan.md", "--new", *options]) == 126
