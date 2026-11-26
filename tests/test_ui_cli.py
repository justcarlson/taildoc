"""Checks for the offline tailplan ui command line through every entry point."""
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

import tailplan_server as server
from tailplan_ui import catalog
from tailplan_ui.color import solve as S
from tailplan_ui.color import tokens as T

ROOT = Path(__file__).resolve().parents[1]
ENTRY_POINTS = {
    "module": [sys.executable, "-m", "tailplan_ui"],
    "tailplan-share": [sys.executable, str(ROOT / "bin" / "tailplan-share"), "ui"],
    "tailplan-share-remote": [sys.executable, str(ROOT / "bin" / "tailplan-share-remote"), "ui"],
}


@pytest.fixture
def no_ssh(tmp_path):
    """Put a fake ssh first on PATH. It records any call, so a test can prove ui never uses SSH."""
    fake = tmp_path / "fakebin"
    fake.mkdir()
    marker = tmp_path / "ssh-called"
    (fake / "ssh").write_text(f"#!/bin/sh\necho \"$@\" > {marker}\nexit 255\n", encoding="utf-8")
    (fake / "ssh").chmod(0o755)
    env = {**os.environ, "PATH": f"{fake}{os.pathsep}{os.environ['PATH']}",
           "TAILPLAN_SSH_TARGET": "offline.invalid", "NO_COLOR": "1", "PYTHONPATH": str(ROOT)}
    env.pop("TAILPLAN_URL", None)
    yield env, marker
    assert not marker.exists(), "tailplan ui called ssh"


def run(entry, args, env, *, cwd=None, stdin=None):
    return subprocess.run([*ENTRY_POINTS[entry], *args], capture_output=True, text=True, env=env,
                          cwd=cwd or ROOT, input=stdin, timeout=120)


@pytest.mark.parametrize("entry", ENTRY_POINTS)
def test_list_and_show(entry, no_ssh):
    env, _ = no_ssh
    result = run(entry, ["list", "--json"], env)
    assert result.returncode == 0, result.stderr
    doc = json.loads(result.stdout)
    assert doc["count"] == len(catalog()["items"]) == 58
    result = run(entry, ["list", "--category", "blocks"], env)
    assert result.returncode == 0 and "blocks (12)" in result.stdout and "primitives" not in result.stdout
    result = run(entry, ["show", "verdict", "--json"], env)
    assert json.loads(result.stdout)["slot"] == "verdict"
    result = run(entry, ["show", "verdct"], env)
    assert result.returncode == 2 and 'Did you mean "verdict"?' in result.stderr
    result = run(entry, ["show", "verdct", "--json"], env)
    assert result.returncode == 2 and json.loads(result.stdout)["suggestion"] == "verdict"


@pytest.mark.parametrize("entry", ENTRY_POINTS)
def test_init_bundle_and_verify(entry, no_ssh, tmp_path):
    env, _ = no_ssh
    page = tmp_path / "plan.html"
    result = run(entry, ["init", str(page), "--theme", "graphite", "--accent", "cobalt", "--viz", "okabe-ito",
                         "--mode", "dark", "--title", "Q4 & plan"], env)
    assert result.returncode == 0, result.stderr
    text = page.read_text(encoding="utf-8")
    assert '<meta name="tailplan-ui" content="1">' in text and 'data-slot="doc-header"' in text
    assert 'data-ui-theme="graphite" data-ui-accent="cobalt" data-ui-viz="okabe-ito" data-ui-mode="dark"' in text
    assert "<title>Q4 &amp; plan</title>" in text
    result = run(entry, ["init", str(page)], env)
    assert result.returncode == 1 and "--force" in result.stderr
    assert page.read_text(encoding="utf-8") == text
    assert run(entry, ["init", str(page), "--force"], env).returncode == 0

    result = run(entry, ["verify", str(page), "--strict", "--json"], env)
    report = json.loads(result.stdout)
    assert result.returncode == 0 and report["ok"] and not report["errors"] and not report["warnings"]
    assert report["optedIn"] and not report["bundled"] and report["slotCount"] > 20

    out = tmp_path / "out.html"
    result = run(entry, ["bundle", str(page), "-o", str(out), "--json"], env)
    meta = json.loads(result.stdout)
    assert result.returncode == 0 and meta["target"] == "standalone" and meta["output"] == str(out)
    assert 'data-tailplan-ui="' in out.read_text(encoding="utf-8")
    result = run(entry, ["verify", str(out), "--strict"], env)
    assert result.returncode == 0, result.stdout

    result = run(entry, ["bundle", "-", "--target", "t3-embed", "--json"], env, stdin=text)
    embed = json.loads(result.stdout)
    assert result.returncode == 0 and embed["target"] == "t3-embed" and embed["html"].startswith("<!doctype html>")
    assert 'name="theme-color"' not in embed["html"]

    copy = tmp_path / "copy.html"
    shutil.copy(page, copy)
    assert run(entry, ["bundle", str(copy), "--in-place"], env).returncode == 0
    assert copy.read_text(encoding="utf-8") == out.read_text(encoding="utf-8")
    stale = copy.read_text(encoding="utf-8").replace("Ship option A", "Ship option B")
    copy.write_text(stale.replace("--primary", "--primary-old", 1), encoding="utf-8")
    report = json.loads(run(entry, ["verify", str(copy), "--json"], env).stdout)
    assert "ui-stale-bundle" in {w["code"] for w in report["warnings"]}
    assert run(entry, ["bundle", str(copy), "--in-place", "-o", str(out)], env).returncode == 2
    assert run(entry, ["bundle", "-", "--in-place"], env, stdin=text).returncode == 2


@pytest.mark.parametrize("entry", ENTRY_POINTS)
def test_verify_rejects_script_form_handlers_and_warns_on_lint(entry, no_ssh, tmp_path):
    env, _ = no_ssh
    bad = tmp_path / "bad.html"
    bad.write_text(
        '<!doctype html><html><head><meta name="tailplan-ui" content="1"><title>B</title></head><body>'
        '<script>1</script><form></form><a href="javascript:void 0" onclick="x()">x</a></body></html>',
        encoding="utf-8")
    result = run(entry, ["verify", str(bad), "--json"], env)
    codes = {e["code"] for e in json.loads(result.stdout)["errors"]}
    assert result.returncode == 1
    assert codes == {"ui-script", "ui-form", "ui-event-handler", "ui-javascript-url"}

    warn = tmp_path / "warn.html"
    warn.write_text(
        '<!doctype html><html data-ui-theme="nope"><head><meta name="tailplan-ui" content="1"><title>W</title>'
        '<style>@media (max-width: 600px) { main { padding: 0 } }</style></head>'
        '<body><main><div data-slot="buton">x</div></main></body></html>', encoding="utf-8")
    result = run(entry, ["verify", str(warn), "--json"], env)
    report = json.loads(result.stdout)
    codes = {w["code"] for w in report["warnings"]}
    assert result.returncode == 0 and not report["errors"]
    assert {"ui-unknown-slot", "ui-breakpoint", "ui-attribute"} <= codes
    assert run(entry, ["verify", str(warn), "--strict"], env).returncode == 1
    plain = tmp_path / "plain.html"
    plain.write_text("<!doctype html><title>P</title><p>Hi</p>", encoding="utf-8")
    report = json.loads(run(entry, ["verify", str(plain), "--json"], env).stdout)
    assert "ui-not-opted-in" in {w["code"] for w in report["warnings"]}
    assert run(entry, ["verify", str(tmp_path / "missing.html")], env).returncode == 2


@pytest.mark.parametrize("entry", ENTRY_POINTS)
def test_color_commands(entry, no_ssh):
    env, _ = no_ssh
    result = run(entry, ["color", "presets", "--json"], env)
    doc = json.loads(result.stdout)
    assert result.returncode == 0
    assert [p["id"] for p in doc["presets"]] == sorted(T.presets()) and len(doc["presets"]) == 11
    assert {a["id"] for a in doc["accents"]} == set(T.accents())
    assert {v["id"] for v in doc["viz"]} == set(T.viz_sets())
    assert all(p["passes"] for p in doc["presets"] + doc["surfaces"] + doc["accents"] + doc["viz"])
    assert "Presets (11)" in run(entry, ["color", "presets"], env).stdout

    for seed in ("#ffd400", "#00e5ff"):
        result = run(entry, ["color", "seed", seed, "--json"], env)
        report = json.loads(result.stdout)
        assert result.returncode == 0 and report["passes"]
        for mode in S.MODES:
            tokens = report["modes"][mode]["tokens"]
            # A vivid yellow or cyan button takes dark ink, not white.
            assert S.relative_luminance(tokens["primary-foreground"]) < 0.1, (seed, mode, tokens)
            assert report["modes"][mode]["contrast"]["primary-foreground/primary"] >= 4.5
            assert report["modes"][mode]["contrast"]["primary-ink/background"] >= 4.5
            assert set(tokens) >= {"primary", "primary-foreground", "primary-ink", "primary-edge", "ring"}
    result = run(entry, ["color", "seed", "210", "--surface", "slate", "--contrast", "more", "--json"], env)
    report = json.loads(result.stdout)
    assert result.returncode == 0 and report["context"] == {"kind": "surface", "id": "slate"}
    assert all(m["contrast"]["primary-ink/background"] >= 7 for m in report["modes"].values())
    text = run(entry, ["color", "seed", "#2563eb"], env).stdout
    assert "--primary-foreground" in text and ":1" in text
    result = run(entry, ["color", "seed", "blue"], env)
    assert result.returncode == 2 and "hex color" in result.stderr
    assert run(entry, ["color", "seed", "#2563eb", "--surface", "slat"], env).returncode == 2

    result = run(entry, ["color", "check", "--preset", "nord", "--json"], env)
    report = json.loads(result.stdout)
    assert result.returncode == 0 and report["ok"] and report["contexts"] == ["theme:nord"]
    result = run(entry, ["color", "check", "--surface", "slate", "--seed", "#ffd400"], env)
    assert result.returncode == 0 and result.stdout.startswith("pass:")
    assert run(entry, ["color", "check", "--preset", "nope"], env).returncode == 2


def test_full_color_check_passes_and_legacy_commands_work(no_ssh, tmp_path):
    env, _ = no_ssh
    result = run("module", ["color", "check", "--json"], env)
    report = json.loads(result.stdout)
    assert result.returncode == 0 and report["ok"] and len(report["contexts"]) == 19
    solved = json.loads(run("module", ["solve", "#2563eb"], env).stdout)
    assert set(solved) == {"light", "dark"}
    page = tmp_path / "p.html"
    page.write_text('<!doctype html><head><meta name="tailplan-ui" content="1"></head><p data-slot="badge">x</p>',
                    encoding="utf-8")
    result = run("module", ["bundle", str(page), "-o", str(tmp_path / "o.html"), "--metadata"], env)
    assert json.loads(result.stdout)["uiVersion"]
    assert "solve" not in run("module", ["--help"], env).stdout


def test_help_lists_ui():
    result = subprocess.run([sys.executable, str(ROOT / "bin" / "tailplan-share"), "--help"],
                            capture_output=True, text=True, timeout=60)
    assert " ui." in " ".join(result.stdout.split())


def test_ui_slug_is_reserved_in_the_cli_and_server(no_ssh, tmp_path):
    env, _ = no_ssh
    assert "ui" in server.RESERVED_SLUGS
    doc = tmp_path / "plan.md"
    doc.write_text("# Plan\n", encoding="utf-8")
    result = subprocess.run([sys.executable, str(ROOT / "bin" / "tailplan-share"), str(doc), "--slug", "ui", "--json"],
                            capture_output=True, text=True, env=env, timeout=60)
    assert result.returncode == 2 and json.loads(result.stdout)["category"] == "usage"
    assert server.slug_problem("ui") is not None


def test_installed_client_runs_ui_offline(no_ssh, tmp_path):
    """Lay out the client as install-client.sh does, then run tailplan ui from outside the checkout."""
    env, _ = no_ssh
    bin_dir = tmp_path / "bin"
    share = tmp_path / "share"
    bin_dir.mkdir()
    shutil.copytree(ROOT / "tailplan_ui", share / "tailplan_ui", ignore=shutil.ignore_patterns("__pycache__"))
    shutil.copy(ROOT / "bin" / "tailplan-share-remote", bin_dir / "tailplan-share")
    shutil.copy(ROOT / "bin" / "tailplan-share", bin_dir / "tailplan-publisher")
    shutil.copy(ROOT / "bin" / "tailplan", bin_dir / "tailplan")
    (bin_dir / ".tailplan-share-dir").write_text(f"{share}\n", encoding="utf-8")
    env = {k: v for k, v in env.items() if k != "PYTHONPATH"}
    work = tmp_path / "work"
    work.mkdir()
    page = work / "p.html"
    for args in (["list", "--category", "primitives"], ["color", "seed", "#ffd400"], ["init", str(page)],
                 ["verify", str(page), "--strict"], ["bundle", str(page), "--target", "t3-embed", "-o", "out.html"]):
        result = subprocess.run([sys.executable, str(bin_dir / "tailplan"), "ui", *args],
                                capture_output=True, text=True, env=env, cwd=work, timeout=120)
        assert result.returncode == 0, (args, result.stderr)
    assert 'data-tailplan-ui="' in (work / "out.html").read_text(encoding="utf-8")
