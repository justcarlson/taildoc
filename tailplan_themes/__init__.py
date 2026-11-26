"""Versioned document palettes, typography, selection policy, and reading layout."""
from __future__ import annotations

import hashlib
import html
import json
import os
import re
from pathlib import Path

from tailplan_ui import bundler as ui_bundler
from tailplan_ui.color import solve as ui_solve
from tailplan_ui.color import tokens as ui_tokens

from .brief import BriefError, brief_to_body, plain
from .markdown import markdown_to_body, title_from_content

ROOT = Path(__file__).resolve().parent
LAYOUTS = {"reading": {"version": 5, "css": ("reading.css",)},
           "brief": {"version": 2, "css": ("reading.css", "brief.css")}}
RENDER_ENGINES = ("ui", "legacy")
MODES = ("auto", "light", "dark")


class ThemeError(ValueError):
    """A theme or render request is invalid."""


def render_engine() -> str:
    """Return the active render engine: ui (default) or legacy."""
    raw = os.getenv("TAILPLAN_RENDER_ENGINE", "ui").strip().lower() or "ui"
    if raw not in RENDER_ENGINES:
        raise ThemeError("TAILPLAN_RENDER_ENGINE must be ui or legacy")
    return raw


def pigment_families() -> dict[str, dict]:
    """Return designer Pigment theme families, excluding wireframe-only presets."""
    return {
        ident: doc
        for ident, doc in sorted(ui_tokens.presets().items())
        if doc.get("kind") == "designer"
    }


def _solved_summary(preset_id: str, mode: str) -> dict[str, str]:
    """Return the nine document token roles solved from a Pigment preset."""
    solved = ui_tokens.mode_tokens("theme", preset_id, mode)
    return {
        "background": solved["background"],
        "text": solved["foreground"],
        "heading": solved["heading"],
        "accent": solved["primary-ink"],
        "surface": solved["card"],
        "border": solved["border"],
        "muted": solved["muted-foreground"],
        "code-background": solved["code-background"],
        "code-text": solved["code-foreground"],
    }


def _validate(value, schema, path="theme"):
    if "const" in schema and (type(value) is not int or value != schema["const"]):
        raise ThemeError(f"{path}: unsupported schema version")
    if "enum" in schema and value not in schema["enum"]:
        raise ThemeError(f"{path}: expected one of {schema['enum']}")
    kind = schema.get("type")
    if kind == "object":
        if not isinstance(value, dict):
            raise ThemeError(f"{path}: expected object")
        if set(schema.get("required", [])) - value.keys():
            raise ThemeError(f"{path}: missing required fields")
        if schema.get("additionalProperties") is False and value.keys() - schema["properties"].keys():
            raise ThemeError(f"{path}: unknown fields")
        for key, item in value.items():
            _validate(item, schema["properties"][key], f"{path}.{key}")
    elif kind == "string":
        if not isinstance(value, str) or not schema.get("minLength", 0) <= len(value) <= schema.get("maxLength", 10000):
            raise ThemeError(f"{path}: invalid string")
        if "pattern" in schema and re.fullmatch(schema["pattern"], value) is None:
            raise ThemeError(f"{path}: invalid value")
    elif kind == "integer" and (type(value) is not int or value < schema.get("minimum", 0)):
        raise ThemeError(f"{path}: invalid integer")


def contrast(first, second):
    def luminance(color):
        rgb = [int(color[i:i + 2], 16) / 255 for i in (1, 3, 5)]
        linear = [v / 12.92 if v <= 0.04045 else ((v + 0.055) / 1.055) ** 2.4 for v in rgb]
        return sum(v * w for v, w in zip(linear, (0.2126, 0.7152, 0.0722)))
    low, high = sorted((luminance(first), luminance(second)))
    return (high + 0.05) / (low + 0.05)


def validate_theme(theme):
    _validate(theme, _json_asset(ROOT / "schema.json"))
    tokens = theme["tokens"]
    for foreground, background in (("text", "background"), ("heading", "background"),
                                   ("accent", "background"), ("muted", "background"),
                                   ("text", "surface"), ("accent", "surface"),
                                   ("code-text", "code-background")):
        if contrast(tokens[foreground], tokens[background]) < 4.5:
            raise ThemeError(f"{theme['id']}: contrast below 4.5 for {foreground}/{background}")
    return theme


def registry(extra_dir=None):
    """Discover built-in palettes and optional administrator palettes without overrides."""
    result = {}
    directories = [ROOT / "palettes"]
    extra = extra_dir if extra_dir is not None else os.getenv("TAILPLAN_THEME_DIR")
    if extra:
        directory = Path(extra)
        if not directory.is_dir():
            raise ThemeError("Theme directory does not exist")
        directories.append(directory)
    for directory in directories:
        for path in sorted(directory.glob("*.json")):
            try:
                theme = validate_theme(json.loads(path.read_text(encoding="utf-8")))
            except (OSError, ValueError, TypeError) as error:
                raise ThemeError(f"Invalid theme {path.name}: {error}") from error
            ident = theme["id"]
            if ident in result:
                raise ThemeError(f"Duplicate theme ID: {ident}")
            result[ident] = theme
    return dict(sorted(result.items()))


def policy(themes):
    value = _json_asset(ROOT / "selection.json")
    if value.get("schemaVersion") != 1 or set(value) != {"schemaVersion", "fallback", "documentTypes"}:
        raise ThemeError("Invalid theme selection policy")
    if not isinstance(value["documentTypes"], dict):
        raise ThemeError("Invalid document type policy")
    if any(ident not in themes for ident in [value["fallback"], *value["documentTypes"].values()]):
        raise ThemeError("Selection policy references an unknown theme")
    return value


def resolve(theme="auto", document_type="document", *, themes=None):
    themes = registry() if themes is None else themes
    if not isinstance(theme, str) or not isinstance(document_type, str) or not re.fullmatch(r"[a-z][a-z0-9-]{0,63}", document_type):
        raise ThemeError("Invalid theme or document type")
    rules = policy(themes)
    ident = rules["documentTypes"].get(document_type, rules["fallback"]) if theme == "auto" else theme
    if ident not in themes:
        raise ThemeError(f"Unknown theme: {ident}. List themes with tailplan themes --json")
    return themes[ident]


def _asset(path):
    try:
        return path.read_text(encoding="utf-8")
    except (OSError, UnicodeError) as error:
        raise ThemeError(f"Missing or unreadable theme asset: {path.name}") from error


def _json_asset(path):
    try:
        return json.loads(_asset(path))
    except json.JSONDecodeError as error:
        raise ThemeError(f"Invalid theme asset: {path.name}") from error


def validate_typography(preset):
    _validate(preset, _json_asset(ROOT / "typography-schema.json"), "typography")
    return preset


def typography_registry(extra_dir=None):
    """Discover font presets without permitting CSS declarations or external resources."""
    directory = ROOT / "typography"
    for name in ("serif-sans.json", "all-sans.json"):
        if not (directory / name).is_file():
            raise ThemeError(f"Missing typography asset: {name}")
    directories = [directory]
    extra = extra_dir if extra_dir is not None else os.getenv("TAILPLAN_TYPOGRAPHY_DIR")
    if extra:
        if not Path(extra).is_dir():
            raise ThemeError("Typography directory does not exist")
        directories.append(Path(extra))
    result = {}
    for directory in directories:
        for path in sorted(directory.glob("*.json")):
            preset = validate_typography(_json_asset(path))
            if preset["id"] in result:
                raise ThemeError(f"Duplicate typography ID: {preset['id']}")
            result[preset["id"]] = preset
    return dict(sorted(result.items()))


def typography_policy(presets):
    rules = _json_asset(ROOT / "typography-selection.json")
    _validate(rules, {"type": "object", "additionalProperties": False,
                     "required": ["schemaVersion", "default"],
                     "properties": {"schemaVersion": {"const": 1},
                                    "default": {"type": "string"}}}, "typography selection")
    if rules["default"] not in presets:
        raise ThemeError("Typography policy references an unknown preset")
    return rules


def typography_catalog():
    presets = typography_registry()
    return {"ok": True, "schemaVersion": 1, "typography": list(presets.values()),
            "typographySelection": typography_policy(presets)}


def resolve_typography(typography=None):
    presets = typography_registry()
    rules = typography_policy(presets)
    ident = rules["default"] if typography is None else typography
    if not isinstance(ident, str) or ident not in presets:
        raise ThemeError("Unknown typography preset. List presets with tailplan typography --json")
    return presets[ident]


def catalog():
    engine = render_engine()
    themes = registry()
    families = pigment_families()
    for spec in LAYOUTS.values():
        for name in spec["css"]:
            _asset(ROOT / name)
    combined = {}
    for ident, palette in themes.items():
        entry = dict(palette)
        if ident in families:
            entry["modes"] = list(MODES)
        combined[ident] = entry
    for ident, preset in families.items():
        if ident in combined:
            continue
        combined[ident] = {
            "schemaVersion": 2,
            "id": ident,
            "version": preset["version"],
            "name": preset["name"],
            "variant": f"{preset['variants']['light']} / {preset['variants']['dark']}",
            "mode": "auto",
            "modes": list(MODES),
            "source": dict(preset["source"]),
            "tokens": _solved_summary(ident, ui_solve.LIGHT),
        }
    return {**typography_catalog(), "engine": engine,
            "themes": [combined[ident] for ident in sorted(combined)],
            "selection": policy(themes),
            "layouts": [{"id": ident, "version": spec["version"]} for ident, spec in LAYOUTS.items()]}


def _v1_pigment_tokens(palette: dict, *, with_viz: bool) -> dict[str, str]:
    """Adapt a v1 hex palette into Pigment semantic, status, and chart tokens."""
    t = palette["tokens"]
    mode = palette["mode"]
    bg = ui_solve.normalize_hex(t["background"])
    surface = ui_solve.normalize_hex(t["surface"])
    planes = [bg, surface]
    toward = ui_solve.DARK if mode == ui_solve.LIGHT else ui_solve.LIGHT
    solved_accent = ui_solve.solve_mode(t["accent"], bg, surface, mode=mode, muted=surface)
    muted_fg = ui_solve.solve_lightness(t["muted"], planes, ui_solve.TEXT_TARGET, toward=toward)
    out = {
        "background": bg,
        "foreground": ui_solve.normalize_hex(t["text"]),
        "card": surface,
        "card-foreground": ui_solve.normalize_hex(t["text"]),
        "popover": surface,
        "popover-foreground": ui_solve.normalize_hex(t["text"]),
        "muted": surface,
        "muted-foreground": muted_fg,
        "secondary": surface,
        "secondary-foreground": ui_solve.normalize_hex(t["text"]),
        "border": ui_solve.normalize_hex(t["border"]),
        "border-subtle": ui_solve.mix_oklab(t["border"], bg, 0.5),
        "border-strong": ui_solve.normalize_hex(t["border"]),
        "input": ui_solve.normalize_hex(t["border"]),
        "code-background": ui_solve.normalize_hex(t["code-background"]),
        "code-foreground": ui_solve.normalize_hex(t["code-text"]),
        "heading": ui_solve.normalize_hex(t["heading"]),
        "text": ui_solve.normalize_hex(t["text"]),
        "surface": surface,
        "code-text": ui_solve.normalize_hex(t["code-text"]),
        **solved_accent,
    }
    status_L = 0.52 if mode == ui_solve.LIGHT else 0.74
    for key, spec in ui_tokens.primitives()["status"].items():
        seed = ui_solve.oklch_hex(status_L, spec["chroma"], spec["hue"])
        soft = ui_solve.mix_oklab(bg, seed, 0.12 if mode == ui_solve.LIGHT else 0.2)
        out[f"{key}-surface"] = soft
        out[key] = ui_solve.solve_lightness(seed, [bg, surface, soft], ui_solve.TEXT_TARGET, toward=toward)
    if with_viz:
        viz = ui_tokens.viz_sets()[ui_tokens.DEFAULT_VIZ]
        charts = [ui_solve.solve_lightness(ui_solve.normalize_hex(c), planes, ui_solve.EDGE_TARGET, toward=toward)
                  for c in viz[mode]]
        charts = ui_tokens.spread(charts, planes)
        for index, color in enumerate(charts, 1):
            out[f"chart-{index}"] = color
    return out


def _resolve_target(theme: str, document_type: str, mode: str | None) -> dict:
    """Resolve a theme name and optional mode to a v1 palette or Pigment family."""
    if mode is not None and (not isinstance(mode, str) or mode not in MODES):
        raise ThemeError("Mode must be auto, light, or dark")
    if not isinstance(theme, str) or not isinstance(document_type, str) or not re.fullmatch(r"[a-z][a-z0-9-]{0,63}", document_type):
        raise ThemeError("Invalid theme or document type")
    themes = registry()
    rules = policy(themes)
    ident = rules["documentTypes"].get(document_type, rules["fallback"]) if theme == "auto" else theme
    families = pigment_families()

    if ident in themes:
        palette = themes[ident]
        if ident in ui_tokens.LEGACY_ALIASES:
            preset_id, legacy_mode = ui_tokens.LEGACY_ALIASES[ident]
            if ident in ui_tokens.THEME_ALIASES:
                pinned = legacy_mode if mode in (None, "auto") else mode
            elif mode is None:
                pinned = legacy_mode
            elif mode == "auto":
                pinned = None
            else:
                pinned = mode
            return {"kind": "preset", "id": ident, "preset": preset_id, "pinned": pinned, "v1": palette}
        if mode is not None and mode != "auto" and mode != palette["mode"]:
            raise ThemeError(f"Custom theme {ident} supports only {palette['mode']} mode")
        return {"kind": "v1", "id": ident, "preset": None, "pinned": palette["mode"], "v1": palette}

    if ident in families:
        pinned = None if mode in (None, "auto") else mode
        return {"kind": "preset", "id": ident, "preset": ident, "pinned": pinned, "v1": None}

    raise ThemeError(f"Unknown theme: {ident}. List themes with tailplan themes --json")


def _ui_base_css() -> str:
    """Return the shared Tailplan UI layers, reset, density, and base chunks."""
    out = []
    for name, ident, condition, css in ui_bundler.chunks():
        if name in ui_bundler.CSS_FILES and condition in ("always", "standalone"):
            out.append(css)
    return "".join(out)


def _theme_color_metas(target: dict) -> str:
    """Return theme-color metadata tags for pinned or dual-mode themes."""
    if target["pinned"] is not None:
        if target["v1"] is not None and target["pinned"] == target["v1"]["mode"]:
            color = target["v1"]["tokens"]["background"]
        else:
            color = ui_tokens.neutral_tokens("theme", target["preset"], target["pinned"])["background"]
        return f'<meta name="theme-color" content="{color}">'
    colors = ui_tokens.theme_color("theme", target["preset"])
    return ('<meta name="color-scheme" content="light dark">'
            f'<meta name="theme-color" media="(prefers-color-scheme: light)" content="{colors["light"]}">'
            f'<meta name="theme-color" media="(prefers-color-scheme: dark)" content="{colors["dark"]}">')


def _token_decls(target: dict, *, with_viz: bool) -> list[str]:
    """Return Pigment CSS token declarations for the resolved theme target."""
    if target["kind"] == "v1":
        tokens = _v1_pigment_tokens(target["v1"], with_viz=with_viz)
        return [f"color-scheme:{target['pinned']}"] + [f"--{k}:{v}" for k, v in tokens.items()]
    pinned = target["pinned"]
    decls = ui_tokens.context_decls("theme", target["preset"], pinned=pinned, with_viz=with_viz)
    if pinned is None:
        decls.insert(0, "color-scheme:light dark")
    return decls


def _legacy_variables(target: dict, fonts: dict, *, with_viz: bool) -> tuple[str, str]:
    """Return color-scheme and CSS declarations for TAILPLAN_RENDER_ENGINE=legacy."""
    if target["v1"] is not None and target["pinned"] == target["v1"]["mode"]:
        palette = target["v1"]
        adapted = _v1_pigment_tokens(palette, with_viz=with_viz)
        merged = {**adapted, **palette["tokens"], **fonts["tokens"]}
        merged["foreground"] = palette["tokens"]["text"]
        merged["card"] = palette["tokens"]["surface"]
        merged["muted-foreground"] = adapted["muted-foreground"]
        merged["muted"] = palette["tokens"]["surface"]
        merged["code-foreground"] = palette["tokens"]["code-text"]
        merged["primary-ink"] = palette["tokens"]["accent"]
        merged["primary-edge"] = palette["tokens"]["accent"]
        merged["ring"] = palette["tokens"]["accent"]
        return palette["mode"], "; ".join(f"--{k}: {v}" for k, v in merged.items())
    decls = _token_decls(target, with_viz=with_viz) + [f"--{k}:{v}" for k, v in fonts["tokens"].items()]
    scheme = target["pinned"] or "light dark"
    return scheme, "; ".join(d for d in decls if not d.startswith("color-scheme:"))


def _frozen_metadata(target: dict, fonts: dict, layout: str, css: str,
                     document_type: str, selection: str, engine: str) -> dict:
    font_json = json.dumps(fonts, sort_keys=True, separators=(",", ":"))
    renderer_version = 5 if engine == "ui" else 4
    rendered_mode = target["pinned"] or "auto"
    use_v1 = (
        target["kind"] == "v1"
        or (engine == "legacy" and target["v1"] is not None and target["pinned"] == target["v1"]["mode"])
    )
    if use_v1:
        palette = target["v1"]
        canonical = json.dumps(palette, sort_keys=True, separators=(",", ":"))
        return {
            "id": palette["id"], "version": palette["version"], "schemaVersion": 1,
            "sha256": hashlib.sha256(canonical.encode()).hexdigest(),
            "mode": palette["mode"], "engine": engine,
            "tokens": dict(palette["tokens"]), "source": dict(palette["source"]),
            "typography": {**fonts, "sha256": hashlib.sha256(font_json.encode()).hexdigest()},
            "layout": layout, "layoutVersion": LAYOUTS[layout]["version"],
            "layoutSha256": hashlib.sha256(css.encode()).hexdigest(),
            "rendererVersion": renderer_version, "documentType": document_type, "selection": selection,
        }

    preset = ui_tokens.presets()[target["preset"]]
    mode_key = target["pinned"] or ui_solve.LIGHT
    solved_tokens = _solved_summary(target["preset"], mode_key)
    digest_input = {
        "id": target["id"], "preset": preset, "mode": rendered_mode,
        "tokens": solved_tokens,
    }
    if target["pinned"] is None:
        digest_input["darkTokens"] = _solved_summary(target["preset"], ui_solve.DARK)
    canonical = json.dumps(digest_input, sort_keys=True, separators=(",", ":"))
    source = dict(target["v1"]["source"]) if target["v1"] is not None else dict(preset["source"])
    version = target["v1"]["version"] if target["v1"] is not None else preset["version"]
    schema_version = 1 if (target["v1"] is not None and target["pinned"] == target["v1"]["mode"]) else 2
    frozen = {
        "id": target["id"], "version": version, "schemaVersion": schema_version,
        "sha256": hashlib.sha256(canonical.encode()).hexdigest(),
        "mode": rendered_mode, "engine": engine,
        "tokens": solved_tokens,
        "source": source,
        "typography": {**fonts, "sha256": hashlib.sha256(font_json.encode()).hexdigest()},
        "layout": layout, "layoutVersion": LAYOUTS[layout]["version"],
        "layoutSha256": hashlib.sha256(css.encode()).hexdigest(),
        "rendererVersion": renderer_version, "documentType": document_type, "selection": selection,
    }
    if target["pinned"] is None:
        frozen["darkTokens"] = digest_input["darkTokens"]
    return frozen


def render(content, *, format="markdown", filename="document.md", theme="auto",
           document_type="document", layout="reading", typography=None, mode=None):
    if not isinstance(content, str) or not content.strip():
        raise ThemeError("Content must be a nonempty string")
    if not isinstance(format, str) or format not in {"markdown", "text"}:
        raise ThemeError("Format must be markdown or text")
    if not isinstance(layout, str) or layout not in LAYOUTS:
        raise ThemeError("Unknown layout. Supported layouts: " + ", ".join(LAYOUTS))
    if layout == "brief" and format != "markdown":
        raise ThemeError("The brief layout requires Markdown content")
    if not isinstance(filename, str):
        raise ThemeError("Filename must be a string")
    engine = render_engine()
    target = _resolve_target(theme, document_type, mode)
    layout_css = "\n".join(_asset(ROOT / name) for name in LAYOUTS[layout]["css"])
    fonts = resolve_typography(typography)
    with_viz = layout == "brief"
    frozen = _frozen_metadata(target, fonts, layout, layout_css, document_type, theme, engine)

    title = title_from_content(Path(filename), content)
    body_mode = target["pinned"] or "auto"
    if layout == "brief":
        try:
            body, brief_title = brief_to_body(content)
        except BriefError as error:
            raise ThemeError(str(error)) from error
        title = (html.unescape(plain(brief_title)) if brief_title else title)[:100]
        body_class, file_label = f' class="brief" data-mode="{body_mode}"', ""
    else:
        body = markdown_to_body(content) if format == "markdown" else '<div class="plain-text">' + html.escape(content) + '</div>'
        body_class, file_label = "", f'<div class="meta">{html.escape(filename)}</div>'
    body = body.replace('<div class="table-wrap">', '<div class="table-wrap" tabindex="0" role="region" aria-label="Table">')
    body = body.replace("<pre>", '<pre tabindex="0" role="region" aria-label="Code block">')

    if engine == "ui":
        token_rule = "@layer tp.tokens{" + ui_tokens.rule(":root", _token_decls(target, with_viz=with_viz)) + "}"
        font_decls = "; ".join(f"--{k}: {v}" for k, v in fonts["tokens"].items())
        style_css = (
            _ui_base_css() + token_rule + "\n"
            f"@layer tp.blocks {{ :root {{ {font_decls}; }} body {{ padding-bottom: 0; }}\n{layout_css}\n}}"
        )
    else:
        scheme, variables = _legacy_variables(target, fonts, with_viz=with_viz)
        style_css = f":root {{ color-scheme: {scheme}; {variables}; }}\n{layout_css}"

    metadata = html.escape(json.dumps(frozen, sort_keys=True, separators=(",", ":")), quote=True)
    doc = ('<!doctype html>\n<html lang="en"><head><meta charset="utf-8">'
           '<meta name="viewport" content="width=device-width, initial-scale=1">'
           f'{_theme_color_metas(target)}'
           f'<meta name="tailplan-theme" content="{metadata}">'
           f'<title>{html.escape(title)}</title><style>{style_css}</style></head>'
           f'<body{body_class}><main>{file_label}{body}</main></body></html>\n')
    return doc, frozen
