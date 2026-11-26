"""Wave U3 checks: unified Pigment engines, stateless shell pages, and client packaging."""
from __future__ import annotations

import copy
import json
import os
import re
import subprocess
import sys
from pathlib import Path
from unittest.mock import patch

import pytest

import tailplan_server as server
import tailplan_themes as themes
import tailplan_ui
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
    assert ("@layer tp.tokens" in reading_doc) == (engine == "ui")

    brief_doc, brief_meta = themes.render(BRIEF_FIXTURE.read_text(encoding="utf-8"), layout="brief")
    assert server.validate_html(brief_doc)[0]
    assert brief_meta["layout"] == "brief" and brief_meta["layoutVersion"] == 2
    assert "--chart-1:" in brief_doc and "--success:" in brief_doc
    assert ("@layer tp.tokens" in brief_doc) == (engine == "ui")


def test_default_engine_is_ui_and_invalid_engine_fails_closed(monkeypatch):
    """Default render engine is ui. Unknown values raise ThemeError."""
    monkeypatch.delenv("TAILPLAN_RENDER_ENGINE", raising=False)
    assert themes.render_engine() == "ui"
    monkeypatch.setenv("TAILPLAN_RENDER_ENGINE", "bogus")
    with pytest.raises(themes.ThemeError, match="TAILPLAN_RENDER_ENGINE"):
        themes.render("# Test")


@pytest.mark.parametrize("engine", ("ui", "legacy"))
def test_custom_v1_theme_dir_renders_reading_and_brief(tmp_path, monkeypatch, engine):
    """Custom v1 hex palettes in TAILPLAN_THEME_DIR validate and render under both engines."""
    palette = copy.deepcopy(themes.registry()["warm-editorial"])
    palette.update(id="admin-custom", name="Admin Custom")
    (tmp_path / "admin-custom.json").write_text(json.dumps(palette), encoding="utf-8")
    monkeypatch.setenv("TAILPLAN_THEME_DIR", str(tmp_path))
    monkeypatch.setenv("TAILPLAN_RENDER_ENGINE", engine)

    doc, meta = themes.render("# Custom", theme="admin-custom")
    assert meta["id"] == "admin-custom"
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


def test_share_cli_retires_legacy_css_and_client_installer_packages_tailplan_ui(tmp_path):
    """install-client.sh packages tailplan_ui and lets installed CLI bundle locally outside the repo."""
    assert not hasattr(share_mod, "CSS")
    bin_dir = tmp_path / "bin"
    skills_root = tmp_path / "skills"
    share_dir = tmp_path / "share"
    env = {
        **os.environ,
        "HOME": str(tmp_path),
        "TAILPLAN_BIN_DIR": str(bin_dir),
        "TAILPLAN_SKILLS_ROOT": str(skills_root),
        "TAILPLAN_SHARE_DIR": str(share_dir),
    }

    subprocess.run(["bash", str(ROOT / "install-client.sh")], cwd=ROOT, env=env, check=True, capture_output=True)
    ui_dir = share_dir / "tailplan_ui"
    assert (ui_dir / "__init__.py").is_file()
    assert (ui_dir / "bundler.py").is_file()
    assert (ui_dir / "palettes" / "graphite.json").is_file()

    # Load installed tailplan-publisher from an isolated cwd outside the repo checkout.
    check = subprocess.run(
        [
            sys.executable, "-c",
            "import runpy; "
            f"ns = runpy.run_path({str(bin_dir / 'tailplan-publisher')!r}); "
            "doc = ns['html_document']('T', '<p>Hi</p>', 'a.md'); "
            "assert 'id=\"tp-ui\"' in doc and '#2563eb' not in doc and '640px' not in doc; "
            "import tailplan_ui; assert tailplan_ui.catalog()['items']",
        ],
        cwd=tmp_path,
        env=env,
        capture_output=True,
        text=True,
    )
    assert check.returncode == 0, check.stdout + check.stderr

    subprocess.run(["bash", str(ROOT / "install-client.sh"), "--uninstall"],
                   cwd=ROOT, env=env, check=True, capture_output=True)
    assert not ui_dir.exists()
