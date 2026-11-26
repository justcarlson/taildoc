"""The Tailplan UI slot catalog.

registry.json lists the primitives and blocks. When tailplan_ui/wireframes/registry_items.json
exists, its items join the catalog. The color lists come from the color package.
"""
from __future__ import annotations

import copy
import json
from functools import lru_cache
from pathlib import Path

from . import icons
from .bundler import UI_VERSION
from .color import tokens as T

UI_DIR = Path(__file__).resolve().parent
REGISTRY = UI_DIR / "registry.json"
EXTRA = UI_DIR / "wireframes" / "registry_items.json"
CATEGORIES = ("primitives", "blocks", "wireframes")


def _read(path: Path):
    return json.loads(path.read_text(encoding="utf-8"))


def _extra_items(path: Path) -> list[dict]:
    doc = _read(path)
    items = doc.get("items", []) if isinstance(doc, dict) else doc
    return [i for i in items if isinstance(i, dict) and isinstance(i.get("slot"), str)]


def load(ui_dir: Path = UI_DIR) -> dict:
    """Read the registry and merge the wireframes items. A slot that is already listed stays as it is."""
    doc = _read(ui_dir / "registry.json")
    items = list(doc["items"])
    seen = {i["slot"] for i in items}
    extra = ui_dir / "wireframes" / "registry_items.json"
    if extra.is_file():
        for item in _extra_items(extra):
            if item["slot"] not in seen:
                seen.add(item["slot"])
                items.append(item)
    return {**doc, "items": items}


@lru_cache(maxsize=1)
def _catalog() -> dict:
    doc = load()
    presets = T.presets()
    return {
        "version": UI_VERSION,
        "items": doc["items"],
        "icons": doc["icons"],
        "presets": sorted(presets),
        "surfaces": sorted(T.surfaces()),
        "accents": sorted(T.accents()),
        "viz": sorted(T.viz_sets()),
    }


def catalog() -> dict:
    """Return the catalog: items, icons, presets, surfaces, accents, and viz sets."""
    return copy.deepcopy(_catalog())


BASE_SLOTS = ("prose", "device-screen", "kbd-hint")  # Used by the base and density layers.


def known_slots() -> frozenset[str]:
    """Return every data-slot value that the catalog knows, subslots and base slots included."""
    slots = set(BASE_SLOTS)
    for item in _catalog()["items"]:
        slots.add(item["slot"])
        slots.update(item.get("subslots", ()))
    return frozenset(slots)


def get_item(slot: str) -> dict | None:
    """Return the catalog item for a slot or a subslot, or None."""
    for item in _catalog()["items"]:
        if slot == item["slot"] or slot in item.get("subslots", ()):
            return copy.deepcopy(item)
    return None


def icon_sprite(names=None) -> str:
    """Return the hidden SVG sprite for the named icons, or for every icon."""
    return icons.icon_sprite(names)
