"""Versioned document palettes, selection policy, and reading layout."""
from __future__ import annotations

import hashlib
import html
import json
import os
import re
from pathlib import Path

from .markdown import markdown_to_body, title_from_content

ROOT = Path(__file__).resolve().parent


class ThemeError(ValueError):
    """A theme or render request is invalid."""


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
    _validate(theme, json.loads((ROOT / "schema.json").read_text()))
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
    value = json.loads((ROOT / "selection.json").read_text())
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


def catalog():
    themes = registry()
    return {"ok": True, "schemaVersion": 1, "themes": list(themes.values()),
            "selection": policy(themes), "layouts": [{"id": "reading", "version": 1}]}


def render(content, *, format="markdown", filename="document.md", theme="auto",
           document_type="document", layout="reading"):
    if not isinstance(content, str) or not content.strip():
        raise ThemeError("Content must be a nonempty string")
    if not isinstance(format, str) or format not in {"markdown", "text"}:
        raise ThemeError("Format must be markdown or text")
    if layout != "reading":
        raise ThemeError("Unknown layout. Supported layout: reading")
    if not isinstance(filename, str):
        raise ThemeError("Filename must be a string")
    palette = resolve(theme, document_type)
    css = (ROOT / "reading.css").read_text(encoding="utf-8")
    canonical = json.dumps(palette, sort_keys=True, separators=(",", ":"))
    frozen = {"id": palette["id"], "version": palette["version"], "schemaVersion": 1,
              "sha256": hashlib.sha256(canonical.encode()).hexdigest(),
              "tokens": dict(palette["tokens"]), "source": dict(palette["source"]),
              "layout": layout, "layoutVersion": 1,
              "layoutSha256": hashlib.sha256(css.encode()).hexdigest(),
              "rendererVersion": 1, "documentType": document_type, "selection": theme}
    variables = "; ".join(f"--{key}: {value}" for key, value in palette["tokens"].items())
    body = markdown_to_body(content) if format == "markdown" else '<div class="plain-text">' + html.escape(content) + '</div>'
    body = body.replace('<div class="table-wrap">', '<div class="table-wrap" tabindex="0" role="region" aria-label="Table">')
    body = body.replace("<pre>", '<pre tabindex="0" role="region" aria-label="Code block">')
    title = title_from_content(Path(filename), content)
    metadata = html.escape(json.dumps(frozen, sort_keys=True, separators=(",", ":")), quote=True)
    doc = ('<!doctype html>\n<html lang="en"><head><meta charset="utf-8">'
           '<meta name="viewport" content="width=device-width, initial-scale=1">'
           f'<meta name="tailplan-theme" content="{metadata}">'
           f'<title>{html.escape(title)}</title><style>'
           f':root {{ color-scheme: {palette["mode"]}; {variables}; }}\n{css}'
           '</style></head><body><main>'
           f'<div class="meta">{html.escape(filename)}</div>{body}</main></body></html>\n')
    return doc, frozen
