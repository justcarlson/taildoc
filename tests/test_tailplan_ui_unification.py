"""Wave U3 checks: unified Pigment engines, stateless shell pages, and client packaging."""
from __future__ import annotations

import copy
import json
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

import tailplan_server as server
import tailplan_themes as themes
from tailplan_ui import lint
from tailplan_ui.color import tokens as ui_tokens
from tailplan_web import render as web_render
from test_markdown_rendering import KITCHEN_SINK, mod as share_mod
from test_remote_client import mod as remote_mod
from test_ssh_publisher import mod as guard_mod

ROOT = Path(__file__).resolve().parents[1]
BRIEF_FIXTURE = Path(__file__).parent / "fixtures" / "brief-example.md"
PIGMENT_FAMILIES = (
    "graphite", "warm-editorial", "flexoki", "radix-sand", "catppuccin",
    "tokyo-night", "dracula", "gruvbox", "nord", "solarized",
)
LEGACY_SUFFIXED = ("flexoki-light", "catppuccin-latte", "gruvbox-light", "solarized-light")


@pytest.mark.parametrize("engine", ("ui", "legacy"))
def test_markdown_and_brief_render_under_both_engines(monkeypatch, engine):
    """Both engines render valid reading and brief HTML with Pigment semantic tokens."""
    monkeypatch.setenv("TAILPLAN_RENDER_ENGINE", engine)
    assert themes.render_engine() == engine

    reading_doc, reading_meta = themes.render(KITCHEN_SINK, theme="warm-editorial")
    assert server.validate_html(reading_doc)[0]
    assert reading_meta["layout"] == "reading" and reading_meta["layoutVersion"] == 5
    assert reading_meta["engine"] == engine
    assert reading_meta["rendererVersion"] == (5 if engine == "ui" else 4)
    assert ("@layer tp.tokens" in reading_doc) == (engine == "ui")

    brief_doc, brief_meta = themes.render(BRIEF_FIXTURE.read_text(encoding="utf-8"), layout="brief")
    assert server.validate_html(brief_doc)[0]
    assert brief_meta["layout"] == "brief" and brief_meta["layoutVersion"] == 2
    assert brief_meta["engine"] == engine
    assert "--chart-1:" in brief_doc and "--success:" in brief_doc
    assert ("@layer tp.tokens" in brief_doc) == (engine == "ui")


def test_default_engine_is_ui_and_invalid_engine_fails_closed(monkeypatch, tmp_path):
    """Default render engine is ui. Unknown values fail render and server startup."""
    monkeypatch.delenv("TAILPLAN_RENDER_ENGINE", raising=False)
    assert themes.render_engine() == "ui"
    monkeypatch.setenv("TAILPLAN_RENDER_ENGINE", "bogus")
    with pytest.raises(themes.ThemeError, match="TAILPLAN_RENDER_ENGINE"):
        themes.render("# Test")
    with pytest.raises(themes.ThemeError, match="TAILPLAN_RENDER_ENGINE"):
        themes.catalog()
    store = server.Store(tmp_path / "data")
    with pytest.raises(themes.ThemeError, match="TAILPLAN_RENDER_ENGINE"):
        server.create_servers(("127.0.0.1", 0), None, store=store, token="t",
                              base_url="http://127.0.0.1", redirect_view_base_url="")


def test_catalog_exposes_pigment_families_and_rejects_wire():
    """catalog() lists every designer Pigment family and excludes wireframe-only wire."""
    ids = {entry["id"] for entry in themes.catalog()["themes"]}
    assert set(PIGMENT_FAMILIES) <= ids
    assert set(LEGACY_SUFFIXED) <= ids
    assert "wire" not in ids
    with pytest.raises(themes.ThemeError, match="Unknown theme: wire"):
        themes.render("# Wire", theme="wire")


def test_frozen_metadata_tracks_mode_overrides_and_engine():
    """Frozen metadata reflects solved tokens and sha256 for the rendered mode."""
    light_doc, light_meta = themes.render("# Mode", theme="warm-editorial", mode="light")
    dark_doc, dark_meta = themes.render("# Mode", theme="warm-editorial", mode="dark")
    auto_doc, auto_meta = themes.render("# Mode", theme="warm-editorial", mode="auto")
    assert light_meta["mode"] == "light" and dark_meta["mode"] == "dark" and auto_meta["mode"] == "auto"
    assert light_meta["engine"] == dark_meta["engine"] == auto_meta["engine"] == "ui"
    assert dark_meta["tokens"]["background"] == ui_tokens.mode_tokens("theme", "warm-editorial", "dark")["background"]
    assert dark_meta["tokens"]["background"] != light_meta["tokens"]["background"]
    assert dark_meta["sha256"] != light_meta["sha256"]
    assert auto_meta["sha256"] != light_meta["sha256"]
    assert auto_meta["darkTokens"]["background"] == dark_meta["tokens"]["background"]
    assert dark_meta["tokens"]["background"] in dark_doc
    assert "light-dark(" in auto_doc and "light-dark(" not in light_doc


@pytest.mark.parametrize("engine", ("ui", "legacy"))
def test_custom_v1_theme_dir_renders_reading_and_brief(tmp_path, monkeypatch, engine):
    """Custom v1 hex palettes in TAILPLAN_THEME_DIR validate and render under both engines."""
    palette = copy.deepcopy(themes.registry()["warm-editorial"])
    palette.update(id="admin-custom", name="Admin Custom")
    (tmp_path / "admin-custom.json").write_text(json.dumps(palette), encoding="utf-8")
    monkeypatch.setenv("TAILPLAN_THEME_DIR", str(tmp_path))
    monkeypatch.setenv("TAILPLAN_RENDER_ENGINE", engine)

    doc, meta = themes.render("# Custom", theme="admin-custom")
    assert meta["id"] == "admin-custom" and meta["engine"] == engine
    assert server.validate_html(doc)[0]
    assert palette["tokens"]["background"] in doc

    brief_doc, brief_meta = themes.render(BRIEF_FIXTURE.read_text(encoding="utf-8"),
                                          theme="admin-custom", layout="brief")
    assert brief_meta["id"] == "admin-custom"
    assert "--chart-1:" in brief_doc and "--success-surface:" in brief_doc
    assert server.validate_html(brief_doc)[0]


@pytest.mark.parametrize("family", PIGMENT_FAMILIES)
def test_pigment_families_render_dual_mode_and_explicit_modes(family):
    """Unsuffixed Pigment families support dual-mode light-dark() and explicit light/dark modes."""
    auto_doc, _ = themes.render("# Family", theme=family, mode="auto")
    assert "color-scheme:light dark" in auto_doc
    assert "light-dark(" in auto_doc
    assert 'media="(prefers-color-scheme: light)"' in auto_doc
    assert 'media="(prefers-color-scheme: dark)"' in auto_doc
    assert server.validate_html(auto_doc)[0]

    for pinned in ("light", "dark"):
        pinned_doc, _ = themes.render("# Family", theme=family, mode=pinned)
        assert f"color-scheme:{pinned}" in pinned_doc
        assert "light-dark(" not in pinned_doc
        assert server.validate_html(pinned_doc)[0]


def test_new_pigment_families_default_to_auto_and_suffixed_aliases_stay_single_mode():
    """Pigment-only families default to dual-mode. Legacy suffixed aliases stay single-mode."""
    for family in ("graphite", "flexoki", "radix-sand", "catppuccin", "gruvbox", "solarized"):
        doc, meta = themes.render("# Auto", theme=family)
        assert meta["mode"] == "auto"
        assert "color-scheme:light dark" in doc and "light-dark(" in doc

    for alias in LEGACY_SUFFIXED:
        doc, _ = themes.render("# Single", theme=alias)
        assert "color-scheme:light" in doc and "light-dark(" not in doc
        auto_doc, _ = themes.render("# Single", theme=alias, mode="auto")
        assert "color-scheme:light" in auto_doc and "light-dark(" not in auto_doc


def test_mode_option_transport_and_guard(tmp_path):
    """CLI remote transport and publish guard accept valid --mode values and reject bad ones."""
    source = tmp_path / "doc.md"
    source.write_text("# Mode test", encoding="utf-8")
    calls = []

    def run(command, **kwargs):
        calls.append(command)
        return subprocess.CompletedProcess(command, 0, "/tmp/tailplan-upload.Ab12Cd34\n", "")

    remote_mod.share_file(source, target="publisher", new=True, draft=None, run=run,
                          theme="graphite", mode="auto")
    publish = next(c for c in calls if c[:3] == ["ssh", "publisher", "tailplan-share"])
    assert "--mode" in publish and publish[publish.index("--mode") + 1] == "auto"

    with pytest.raises(ValueError, match="Mode"):
        remote_mod.share_file(source, target="publisher", new=True, draft=None, run=run, mode="neon")

    with pytest.raises(SystemExit) as exited:
        guard_mod.execute(["tailplan-share", "/tmp/tailplan-upload.Ab12Cd34/doc.md", "--mode", "neon"])
    assert exited.value.code == 126


def test_brief_css_has_no_hardcoded_hex_colors():
    """brief.css has no hardcoded topic or verdict hex colors."""
    css = (themes.ROOT / "brief.css").read_text(encoding="utf-8")
    assert not re.search(r"#[0-9a-fA-F]{3,6}", css)
    for token in ("--chart-1", "--chart-8", "--success", "--warning", "--destructive"):
        assert token in css


def test_server_home_and_stateless_not_found_use_tailplan_ui_graphite():
    """tailplan_server deletes inline CSS/page() and renders stateless home/404 via graphite."""
    assert not hasattr(server, "CSS")
    assert not hasattr(server, "page")

    home_doc = server.home("https://tailplan.example")
    assert 'data-ui-theme="graphite"' in home_doc
    assert 'id="tp-ui"' in home_doc
    assert "@layer tp.tokens" in home_doc
    assert "light-dark(" in home_doc
    assert server.validate_html(home_doc)[0]
    assert lint.lint(home_doc) == []

    first_404 = server.not_found()
    second_404 = server.not_found()
    assert first_404 == second_404
    assert 'data-ui-theme="graphite"' in first_404
    assert 'id="tp-ui"' in first_404
    assert "Draft not found" in first_404
    assert "Sign in" not in first_404
    assert server.validate_html(first_404)[0]
    assert lint.lint(first_404) == []

    signed_out_404 = server.not_found("https://tailplan.example/auth/sign-in")
    assert 'href="https://tailplan.example/auth/sign-in"' in signed_out_404
    assert server.validate_html(signed_out_404)[0]
    assert lint.lint(signed_out_404) == []


def test_web_css_unifies_onto_pigment_graphite_without_hardcoded_hexes():
    """tailplan_web/render.py derives WEB_CSS and THEME_COLORS from Pigment graphite."""
    source = (ROOT / "tailplan_web" / "render.py").read_text(encoding="utf-8")
    assert not re.search(r"#[0-9a-fA-F]{3,6}", source)
    assert web_render.THEME_COLORS == ui_tokens.theme_color("theme", "graphite")
    for role in ("--background:", "--foreground:", "--card:", "--muted-foreground:",
                 "--border:", "--primary:", "--destructive:", "--warning:", "--info:"):
        assert role in web_render.WEB_CSS


def test_install_sh_stages_tailplan_ui_first_and_imports_from_outside_repo(tmp_path):
    """install.sh stages tailplan_ui before tailplan_themes and tailplan_web."""
    script = (ROOT / "install.sh").read_text(encoding="utf-8")
    ui_pos = script.index('cp -a "$SCRIPT_DIR/tailplan_ui" "$STAGE_DIR/tailplan_ui"')
    themes_pos = script.index('cp -a "$SCRIPT_DIR/tailplan_themes" "$STAGE_DIR/tailplan_themes"')
    web_pos = script.index('cp -a "$SCRIPT_DIR/tailplan_web" "$STAGE_DIR/tailplan_web"')
    assert ui_pos < themes_pos < web_pos

    stage = tmp_path / "stage"
    stage.mkdir()
    for pkg in ("tailplan_ui", "tailplan_themes", "tailplan_web"):
        shutil.copytree(ROOT / pkg, stage / pkg, ignore=shutil.ignore_patterns("__pycache__", "*.pyc"))

    check = subprocess.run(
        [
            sys.executable, "-I", "-S", "-c",
            "import pathlib, sys; stage = pathlib.Path(sys.argv[1]).resolve(); sys.path.append(str(stage)); "
            "import tailplan_ui, tailplan_themes, tailplan_web.assets as a, tailplan_web.render as r; "
            "assert pathlib.Path(tailplan_ui.__file__).resolve().is_relative_to(stage); "
            "assert pathlib.Path(tailplan_themes.__file__).resolve().is_relative_to(stage); "
            "assert pathlib.Path(r.__file__).resolve().is_relative_to(stage); "
            "tailplan_themes.catalog(); a.check_ready()",
            str(stage),
        ],
        cwd="/tmp",
        capture_output=True,
        text=True,
    )
    assert check.returncode == 0, check.stdout + check.stderr


def test_share_cli_retires_legacy_css_and_client_installer_packages_tailplan_ui(tmp_path):
    """install-client.sh packages tailplan_ui and lets isolated python3 -I -S import it."""
    assert not hasattr(share_mod, "CSS")
    bin_dir = tmp_path / "bin"
    skills_root = tmp_path / "skills"
    share_dir = tmp_path / "custom-share"
    install_env = {
        **os.environ,
        "HOME": str(tmp_path),
        "TAILPLAN_BIN_DIR": str(bin_dir),
        "TAILPLAN_SKILLS_ROOT": str(skills_root),
        "TAILPLAN_SHARE_DIR": str(share_dir),
    }

    subprocess.run(["bash", str(ROOT / "install-client.sh")], cwd=ROOT, env=install_env, check=True, capture_output=True)
    ui_dir = share_dir / "tailplan_ui"
    assert (ui_dir / "__init__.py").is_file()
    assert (ui_dir / "bundler.py").is_file()
    assert (ui_dir / "palettes" / "graphite.json").is_file()
    assert (bin_dir / ".tailplan-share-dir").read_text(encoding="utf-8").strip() == str(share_dir)

    # Run isolated python3 -I -S WITHOUT TAILPLAN_SHARE_DIR so the marker file must resolve ui_dir.
    run_env = {k: v for k, v in os.environ.items() if k not in ("PYTHONPATH", "TAILPLAN_SHARE_DIR")}
    run_env["HOME"] = str(tmp_path)
    check = subprocess.run(
        [
            sys.executable, "-I", "-S", "-c",
            "import pathlib, runpy, sys; "
            "ns = runpy.run_path(sys.argv[1]); "
            "doc = ns['html_document']('T', '<p>Hi</p>', 'a.md'); "
            "assert 'id=\"tp-ui\"' in doc and '#2563eb' not in doc and '640px' not in doc; "
            "import tailplan_ui; "
            "assert pathlib.Path(tailplan_ui.__file__).resolve().is_relative_to(pathlib.Path(sys.argv[2]).resolve()); "
            "assert tailplan_ui.catalog()['items']",
            str(bin_dir / "tailplan-publisher"),
            str(ui_dir),
        ],
        cwd=tmp_path,
        env=run_env,
        capture_output=True,
        text=True,
    )
    assert check.returncode == 0, check.stdout + check.stderr

    # Removing ui_dir must make html_document() fail closed with ConfigurationFailure.
    shutil.rmtree(ui_dir)
    broken = subprocess.run(
        [
            sys.executable, "-I", "-S", "-c",
            "import runpy, sys\nns = runpy.run_path(sys.argv[1])\n"
            "try:\n"
            "    ns['html_document']('T', '<p>Hi</p>', 'a.md')\n"
            "except ns['ConfigurationFailure'] as exc:\n"
            "    assert 'tailplan_ui' in str(exc)\n"
            "else:\n"
            "    raise SystemExit('expected ConfigurationFailure')",
            str(bin_dir / "tailplan-publisher"),
        ],
        cwd=tmp_path,
        env=run_env,
        capture_output=True,
        text=True,
    )
    assert broken.returncode == 0, broken.stdout + broken.stderr

    subprocess.run(["bash", str(ROOT / "install-client.sh"), "--uninstall"],
                   cwd=ROOT, env=install_env, check=True, capture_output=True)
    assert not ui_dir.exists()
    assert not (bin_dir / ".tailplan-share-dir").exists()
