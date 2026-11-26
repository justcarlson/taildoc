"""Twenty-four 24 by 24 stroke icons, written for Tailplan, and the sprite that carries them.

Use an icon with <svg data-slot="icon"><use href="#tp-icon-check"/></svg>.
The primitives CSS gives the stroke, width, and color.
"""
from __future__ import annotations

ICON_PREFIX = "tp-icon-"
VIEW_BOX = "0 0 24 24"

# Each glyph is the inner markup of one symbol. Strokes use currentColor from the CSS.
ICONS: dict[str, str] = {
    "check": '<path d="M20 6 9 17l-5-5"/>',
    "x": '<path d="M18 6 6 18M6 6l12 12"/>',
    "alert": '<path d="M12 3.5 21 19.5H3z"/><path d="M12 10v4M12 17h.01"/>',
    "info": '<circle cx="12" cy="12" r="9"/><path d="M12 11v5M12 8h.01"/>',
    "chevron-down": '<path d="m6 9 6 6 6-6"/>',
    "chevron-right": '<path d="m9 6 6 6-6 6"/>',
    "arrow-right": '<path d="M5 12h14M13 6l6 6-6 6"/>',
    "external-link": '<path d="M14 4h6v6M20 4l-9 9M18 14v5a1 1 0 0 1-1 1H5a1 1 0 0 1-1-1V7a1 1 0 0 1 1-1h5"/>',
    "copy": '<rect x="9" y="9" width="11" height="11" rx="2"/><path d="M5 15V6a1 1 0 0 1 1-1h9"/>',
    "search": '<circle cx="11" cy="11" r="6.5"/><path d="m16 16 4.5 4.5"/>',
    "filter": '<path d="M3 5h18l-7 8v6l-4 2v-8z"/>',
    "sliders": '<path d="M4 8h16M9 5v6M4 16h16M15 13v6"/>',
    "sun": '<circle cx="12" cy="12" r="4"/><path d="M12 2v2M12 20v2M2 12h2M20 12h2M4.9 4.9l1.4 1.4M17.7 17.7l1.4 1.4M4.9 19.1l1.4-1.4M17.7 6.3l1.4-1.4"/>',
    "moon": '<path d="M20 14.5A8.5 8.5 0 0 1 9.5 4 8.5 8.5 0 1 0 20 14.5z"/>',
    "monitor": '<rect x="3" y="4" width="18" height="12" rx="2"/><path d="M8 20h8M12 16v4"/>',
    "smartphone": '<rect x="7" y="2.5" width="10" height="19" rx="2"/><path d="M11 18.5h2"/>',
    "tablet": '<rect x="5" y="2.5" width="14" height="19" rx="2"/><path d="M11 18.5h2"/>',
    "layers": '<path d="M12 3 3 8l9 5 9-5-9-5zM3 13l9 5 9-5"/>',
    "palette": '<path d="M12 3a9 9 0 1 0 0 18c1.2 0 2-.8 2-1.8 0-.5-.2-.9-.5-1.3-.3-.4-.5-.8-.5-1.3 0-1 .8-1.6 1.8-1.6H17a4 4 0 0 0 4-4c0-4.4-4-8-9-8z"/><path d="M7.5 11h.01M10 7.5h.01M14.5 7.5h.01"/>',
    "code": '<path d="m8 7-5 5 5 5M16 7l5 5-5 5"/>',
    "terminal": '<path d="m4 7 5 5-5 5M12 17h8"/>',
    "file-text": '<path d="M14 3H7a2 2 0 0 0-2 2v14a2 2 0 0 0 2 2h10a2 2 0 0 0 2-2V8zM14 3v5h5M9 13h6M9 17h6"/>',
    "link": '<path d="M10 14a4 4 0 0 0 5.7 0l3-3a4 4 0 0 0-5.7-5.7l-1 1M14 10a4 4 0 0 0-5.7 0l-3 3a4 4 0 0 0 5.7 5.7l1-1"/>',
    "sparkles": '<path d="m12 3 1.8 5.2L19 10l-5.2 1.8L12 17l-1.8-5.2L5 10l5.2-1.8zM19 16l.8 2.2L22 19l-2.2.8L19 22l-.8-2.2L16 19l2.2-.8z"/>',
}


def icon_names() -> list[str]:
    """Return the icon names in catalog order."""
    return list(ICONS)


def icon_sprite(names=None) -> str:
    """Return a hidden SVG sprite with one symbol for each requested icon.

    With no names, the sprite holds every icon. An unknown name raises KeyError.
    """
    chosen = icon_names() if names is None else list(dict.fromkeys(names))
    for name in chosen:
        if name not in ICONS:
            raise KeyError(name)
    symbols = "".join(
        f'<symbol id="{ICON_PREFIX}{name}" viewBox="{VIEW_BOX}">{ICONS[name]}</symbol>' for name in chosen)
    return f'<svg xmlns="http://www.w3.org/2000/svg" width="0" height="0" aria-hidden="true" hidden>{symbols}</svg>'
