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


def ui(capsys, *args, stdin=None, monkeypatch=None):
    """Run the CLI in this process. Return (exit code, stdout, stderr)."""
    from tailplan_ui import cli
    if stdin is not None:
        monkeypatch.setattr(sys, "stdin", stdin)
    code = cli.main(list(args), prog="tailplan ui")
    out, err = capsys.readouterr()
    return code, out, err


def test_init_json_stdout_and_symlink_guard(capsys, tmp_path):
    code, out, _ = ui(capsys, "init", "-", "--theme", "nord", "--json")
    doc = json.loads(out)
    assert code == 0 and doc["ok"] and doc["output"] == "-" and doc["html"].startswith("<!doctype html>")
    assert doc["attributes"] == {"data-ui-theme": "nord"}
    code, out, _ = ui(capsys, "init", "-", "--title", "Raw")
    assert code == 0 and out.startswith("<!doctype html>") and "<title>Raw</title>" in out

    page = tmp_path / "p.html"
    code, out, _ = ui(capsys, "init", str(page), "--json")
    assert code == 0 and json.loads(out)["output"] == str(page) and page.is_file()
    code, out, _ = ui(capsys, "init", str(page), "--json")
    assert code == 1 and not json.loads(out)["ok"] and "--force" in json.loads(out)["error"]

    target = tmp_path / "elsewhere.html"
    link = tmp_path / "link.html"
    link.symlink_to(target)
    code, _, err = ui(capsys, "init", str(link))
    assert code == 1 and "--force" in err and not target.exists()


def test_read_and_write_errors_are_usage_errors(capsys, monkeypatch, tmp_path):
    import io
    bad = io.TextIOWrapper(io.BytesIO(b"\xff\xfe<p>"), encoding="utf-8")
    code, _, err = ui(capsys, "verify", "-", stdin=bad, monkeypatch=monkeypatch)
    assert code == 2 and "cannot read -" in err
    missing = tmp_path / "no-such-dir" / "p.html"
    code, _, err = ui(capsys, "init", str(missing))
    assert code == 2 and "cannot write" in err
    src = tmp_path / "s.html"
    src.write_text('<meta name="tailplan-ui" content="1"><p>x</p>', encoding="utf-8")
    code, out, _ = ui(capsys, "bundle", str(src), "-o", str(missing), "--json")
    assert code == 2 and "cannot write" in json.loads(out)["error"]


HEAD_OK = '<!doctype html><html><head><meta name="tailplan-ui" content="1"><title>T</title></head><body>'


def test_verify_finds_stamps_by_parsing_not_by_text():
    from tailplan_ui import bundle
    from tailplan_ui.bundler import UI_VERSION
    from tailplan_ui.cli import verify_html
    sample = (HEAD_OK + '<code>data-tailplan-ui="1.0.0"</code><!-- data-tailplan-ui="1.0.0" -->'
              '<p data-slot="badge">x</p></body></html>')
    report = verify_html(sample)
    assert not report["bundled"] and "ui-stale-bundle" not in {w["code"] for w in report["warnings"]}

    page, _ = bundle(HEAD_OK + '<p data-slot="badge">x</p></body></html>')
    single = page.replace(f'data-tailplan-ui="{UI_VERSION}"', f"data-tailplan-ui='{UI_VERSION}'")
    assert single != page
    report = verify_html(single)
    assert report["bundled"] and report["optedIn"]


def test_verify_safety_parser_handles_rcdata_comments_and_obfuscated_urls():
    from tailplan_ui.cli import verify_html
    quiet = HEAD_OK + ('<textarea><script>x</script><form></form></textarea>'
                       '<!-- <script>y</script> --><a href="https://example.com">ok</a></body></html>')
    assert verify_html(quiet)["errors"] == []
    for href in ("java&#9;script:alert(1)", "&#x20;JavaScript:alert(1)", "vb\nscript:x", "\x01 javascript:x"):
        report = verify_html(HEAD_OK + f'<a href="{href}">x</a></body></html>')
        assert [e["code"] for e in report["errors"]] == ["ui-javascript-url"], href
    title = '<!doctype html><html><head><meta name="tailplan-ui" content="1"><title><script>t</script></title></head><body></body></html>'
    assert verify_html(title)["errors"] == []


def test_verify_reports_each_meta_key_and_hue_warning_once():
    from tailplan_ui.cli import verify_html
    page = ('<!doctype html><html><head><meta name="tailplan-ui" content="1; colour=red"><title>T</title>'
            '<style>.x { --tp-hue: 200 }</style></head><body><p class="x">x</p></body></html>')
    warnings = verify_html(page)["warnings"]
    meta = [w for w in warnings if w["code"] == "ui-meta-key"]
    assert len(meta) == 1 and "colour" in meta[0]["message"] and meta[0]["fix"]
    hue = [w for w in warnings if w["code"] == "ui-hue-var"]
    assert len(hue) == 1 and "--tp-hue" in hue[0]["message"]
    assert not [w for w in warnings if w["code"] == "ui-attribute"]


def test_show_and_init_use_the_active_prog(capsys, tmp_path):
    from tailplan_ui import cli
    assert cli.main(["show", "verdct"], prog="python3 -m tailplan_ui") == 2
    assert capsys.readouterr().err.startswith("python3 -m tailplan_ui: ")
    assert cli.main(["init", str(tmp_path / "a.html")], prog="python3 -m tailplan_ui") == 0
    assert "python3 -m tailplan_ui bundle" in capsys.readouterr().out


@pytest.mark.parametrize("layout", ["app-dir-env", "home-apps"])
def test_server_install_layout_runs_ui_without_pythonpath(no_ssh, tmp_path, layout):
    """Lay out the host as install.sh does. tailplan-share must find tailplan_ui with no PYTHONPATH."""
    env, _ = no_ssh
    home = tmp_path / "home"
    app = tmp_path / "app" if layout == "app-dir-env" else home / "apps" / "tailplan"
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    home.mkdir()
    shutil.copy(ROOT / "bin" / "tailplan-share", bin_dir / "tailplan-share")
    env = {k: v for k, v in env.items() if k not in ("PYTHONPATH", "TAILPLAN_SHARE_DIR", "TAILPLAN_APP_DIR")}
    env["HOME"] = str(home)
    # -I -S: no site-packages, no script directory, no PYTHONPATH. Only the share lookup can find tailplan_ui.
    command = [sys.executable, "-I", "-S", str(bin_dir / "tailplan-share"), "ui", "list", "--json"]
    result = subprocess.run(command, capture_output=True, text=True, env=env, cwd=tmp_path, timeout=120)
    if not Path("/opt/tailplan/tailplan_ui").is_dir():
        assert result.returncode == 2 and json.loads(result.stdout) == {
            "ok": False, "error": "the tailplan_ui package is missing. Run install-client.sh again."}
    shutil.copytree(ROOT / "tailplan_ui", app / "tailplan_ui", ignore=shutil.ignore_patterns("__pycache__"))
    if layout == "app-dir-env":
        env["TAILPLAN_APP_DIR"] = str(app)
    result = subprocess.run(command, capture_output=True, text=True, env=env, cwd=tmp_path, timeout=120)
    assert result.returncode == 0, result.stderr
    assert json.loads(result.stdout)["count"] == 58
