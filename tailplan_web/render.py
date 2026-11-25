"""Server-rendered HTML for the Tailplan web dashboard and sign-in pages.

Every page uses one inline Geist style sheet, so a page works without other requests.
"""
from __future__ import annotations

import html
import re
from datetime import UTC, datetime, timedelta
from urllib.parse import quote, urlencode, urlparse

from tailplan_web.assets import asset_url

__all__ = [
    "WEB_CSS", "VISIBILITY_LABELS", "THEMES", "esc", "csrf_field", "web_page", "render_login",
    "external_link", "sharing_badges", "render_dashboard", "render_sharing",
    "render_activate", "render_detail", "render_keys", "render_key",
]

THEMES = ("system", "light", "dark")

_LIGHT = (
    "--bg:#ffffff;--surface:#fafafa;--hover:#f5f5f5;--border:rgba(0,0,0,.08);--border-strong:rgba(0,0,0,.14);"
    "--text:#171717;--muted:#666666;--accent:#0070f3;--star:#d97706;--danger:#e5484d;"
    "--mark-bg:rgba(0,112,243,.14);--focus:rgba(0,112,243,.45);color-scheme:light"
)
_DARK = (
    "--bg:#0a0a0a;--surface:#111111;--hover:#181818;--border:rgba(255,255,255,.09);"
    "--border-strong:rgba(255,255,255,.16);--text:#ededed;--muted:#888888;--accent:#3291ff;"
    "--star:#f59e0b;--danger:#ff6166;--mark-bg:rgba(50,145,255,.22);--focus:rgba(50,145,255,.55);color-scheme:dark"
)

_BASE_CSS = """
:root{%(light)s;--sans:"Geist",ui-sans-serif,system-ui,-apple-system,"Segoe UI",sans-serif;--mono:"Geist Mono",ui-monospace,SFMono-Regular,Menlo,Consolas,monospace;--radius:8px}
@media(prefers-color-scheme:dark){:root:not([data-theme=light]){%(dark)s}}
:root[data-theme=dark]{%(dark)s}
*{box-sizing:border-box}
html{-webkit-text-size-adjust:100%%;text-size-adjust:100%%}
body{margin:0;background:var(--bg);color:var(--text);font:14px/1.5 var(--sans);-webkit-font-smoothing:antialiased;font-feature-settings:"ss01"}
a{color:inherit;text-decoration:none;overflow-wrap:anywhere}
a:hover{text-decoration:underline;text-underline-offset:3px}
p{overflow-wrap:anywhere;margin:0 0 12px}
h1,h2,h3{letter-spacing:-.03em;margin:0;font-weight:600}
h1{font-size:clamp(22px,4vw,28px);line-height:1.2}
h2{font-size:15px;line-height:1.3}
h3{font-size:14px;font-weight:500;letter-spacing:-.01em}
code,kbd,pre,.mono{font-family:var(--mono);font-variant-numeric:tabular-nums}
code{font-size:12.5px;background:var(--surface);border:1px solid var(--border);padding:1px 5px;border-radius:5px;overflow-wrap:anywhere}
pre{white-space:pre-wrap;overflow-wrap:anywhere;background:var(--surface);padding:16px;border:1px solid var(--border);border-radius:var(--radius)}
time,.num,.count{font-variant-numeric:tabular-nums}
:focus-visible{outline:2px solid var(--focus);outline-offset:2px}
.sr-only{position:absolute;width:1px;height:1px;padding:0;margin:-1px;overflow:hidden;clip-path:inset(50%%);white-space:nowrap;border:0}
.muted,small{color:var(--muted)}
.wrap{width:100%%;max-width:1200px;margin:0 auto;padding:0 24px}
.top{border-bottom:1px solid var(--border);background:var(--bg)}
.top .wrap{display:flex;align-items:center;gap:8px 20px;flex-wrap:wrap;min-height:56px;padding-top:8px;padding-bottom:8px}
.brand{display:flex;align-items:center;gap:8px;font-weight:600;letter-spacing:-.02em;font-size:15px}
.brand:hover{text-decoration:none}
.brand svg{width:22px;height:22px;flex:none}
.nav{display:flex;gap:4px;flex-wrap:wrap}
.nav a{color:var(--muted);padding:6px 10px;border-radius:6px}
.nav a:hover{color:var(--text);background:var(--hover);text-decoration:none}
.nav a[aria-current=page]{color:var(--text)}
.account{display:flex;align-items:center;gap:8px;flex-wrap:wrap;margin-left:auto;min-width:0}
.account form{margin:0;display:flex;align-items:center}
.who{color:var(--muted);font-size:13px;overflow-wrap:anywhere;min-width:0}
.theme-switch{display:inline-flex;border:1px solid var(--border);border-radius:999px;padding:2px;gap:2px}
.theme-switch button{background:none;border:0;color:var(--muted);width:28px;height:28px;padding:0;border-radius:999px;display:grid;place-items:center;cursor:pointer}
.theme-switch button:hover{color:var(--text)}
.theme-switch button[aria-pressed=true]{background:var(--hover);color:var(--text);box-shadow:inset 0 0 0 1px var(--border)}
.theme-switch svg{width:15px;height:15px}
main{display:block;width:100%%;max-width:1200px;margin:0 auto;padding:32px 24px 96px}
.narrow{max-width:560px}
.lead{margin-top:8px}.after{margin-top:12px}.flush{margin:0}.crumb{margin:0 0 12px}.pin-body{min-width:0}
button,.button{font:inherit;font-size:14px;font-weight:500;background:var(--text);color:var(--bg);border:1px solid var(--text);border-radius:6px;padding:0 14px;min-height:36px;cursor:pointer;text-decoration:none;display:inline-flex;align-items:center;justify-content:center;gap:6px;line-height:1}
button:hover,.button:hover{opacity:.88;text-decoration:none}
button.secondary,.button.secondary{background:var(--bg);color:var(--text);border-color:var(--border-strong)}
button.secondary:hover,.button.secondary:hover{background:var(--hover);opacity:1}
button.danger{background:var(--danger);border-color:var(--danger);color:#fff}
button.sm,.button.sm{min-height:28px;padding:0 10px;font-size:13px}
input,select{font:inherit;font-size:14px;padding:0 10px;height:36px;border:1px solid var(--border-strong);border-radius:6px;width:100%%;max-width:600px;background:var(--bg);color:var(--text)}
input:focus,select:focus{outline:none;border-color:var(--accent);box-shadow:0 0 0 3px var(--mark-bg)}
input[readonly]{font-family:var(--mono);font-size:13px}
label{display:block;margin:14px 0 6px;font-size:13px;font-weight:500}
form{margin:12px 0}
.card{padding:20px;background:var(--bg);border:1px solid var(--border);border-radius:var(--radius);margin:20px 0}
.card h2{margin-bottom:8px}
.inline{display:flex;gap:10px;flex-wrap:wrap;align-items:end}
.inline label{margin:0 0 6px}
.inline>*{flex:1 1 160px}
.inline>button{flex:0 0 auto}
.actions{display:flex;gap:8px;flex-wrap:wrap;align-items:center}
.actions form{margin:0}
.badge{display:inline-flex;align-items:center;font-size:12px;line-height:18px;padding:0 7px;border-radius:999px;border:1px solid var(--border);color:var(--muted);background:var(--bg);white-space:nowrap}
.badge.warn{color:var(--danger);border-color:var(--danger)}
.notice{padding:10px 12px;border:1px solid var(--danger);border-radius:var(--radius);color:var(--danger)}
.table-wrap{width:100%%;overflow-x:auto;border:1px solid var(--border);border-radius:var(--radius)}
table{width:100%%;border-collapse:collapse}
th,td{text-align:left;padding:10px 14px;border-bottom:1px solid var(--border);vertical-align:top}
tbody tr:last-child td{border-bottom:0}
th{font-size:12px;font-weight:500;color:var(--muted);background:var(--surface)}
td{min-width:110px;max-width:430px;overflow-wrap:anywhere;font-size:13px}
td.mono,td .mono{font-size:12.5px}
details{margin:12px 0}
summary{cursor:pointer;color:var(--muted)}
summary:hover{color:var(--text)}
dl{display:grid;grid-template-columns:minmax(90px,1fr) 3fr;gap:6px 12px;margin:12px 0}
dt{color:var(--muted)}
dd{margin:0;overflow-wrap:anywhere}
.pill{display:inline-flex;align-items:center;gap:4px;height:22px;padding:0 8px;border:1px solid var(--border);border-radius:999px;font-size:12px;color:var(--muted);background:var(--bg);white-space:nowrap;max-width:100%%}
a.pill:hover{color:var(--text);border-color:var(--border-strong);text-decoration:none}
.pill.on{color:var(--text);border-color:var(--text)}
.pill .num{color:var(--muted);font-size:11px}
.pill.dashed{border-style:dashed;cursor:pointer;font:inherit;font-size:12px;min-height:0;background:none;color:var(--muted)}
.pill.dashed:hover{color:var(--text);border-color:var(--border-strong);opacity:1}
.ver{font-family:var(--mono);font-size:12px;font-variant-numeric:tabular-nums;color:var(--muted);padding:0 6px;height:20px;line-height:18px;border:1px solid var(--border);border-radius:5px;white-space:nowrap}
.ver.multi{color:var(--text);border-color:var(--border-strong);font-weight:500}
.star{background:none;border:0;color:var(--muted);width:44px;height:44px;min-height:44px;padding:0;font-size:17px;border-radius:6px;display:grid;place-items:center}
.star:hover{color:var(--star);background:var(--hover);opacity:1}
.star.on{color:var(--star)}
.ghost{background:none;border:1px solid transparent;color:var(--muted);font-weight:400}
.ghost:hover{color:var(--text);background:var(--hover);border-color:var(--border);opacity:1}
.dot{width:6px;height:6px;border-radius:50%%;background:var(--muted);display:inline-block;flex:none}
.dot.vis-link{background:var(--star)}
.dot.vis-authenticated,.dot.vis-workspace{background:var(--accent)}
mark{background:var(--mark-bg);color:inherit;border-radius:3px;padding:0 1px}
kbd{font-size:11px;color:var(--muted);border:1px solid var(--border);border-radius:4px;padding:0 5px;line-height:18px;background:var(--surface)}
@media(max-width:600px){.wrap{padding:0 16px}main{padding:20px 16px 72px}.table-wrap{border:0;border-radius:0}.table-wrap table,.table-wrap tbody,.table-wrap tr,.table-wrap td{display:block;width:100%%}.table-wrap thead{position:absolute;width:1px;height:1px;overflow:hidden;clip-path:inset(50%%)}.table-wrap tr{border:1px solid var(--border);border-radius:var(--radius);margin-bottom:10px}.table-wrap td{max-width:none;min-width:0;border-bottom:0}.table-wrap td+td{border-top:1px solid var(--border)}.table-wrap td[data-label]::before{content:attr(data-label);display:block;font-size:12px;font-weight:500;color:var(--muted);font-family:var(--sans)}}
"""

_DASH_CSS = """
.page-head{display:flex;align-items:baseline;justify-content:space-between;gap:8px 16px;flex-wrap:wrap;margin-bottom:20px}
.page-head h1 .count{color:var(--muted);font-weight:400;font-size:.7em;margin-left:6px;letter-spacing:0}
.tabs{display:flex;flex-wrap:wrap;gap:4px 2px;border-bottom:1px solid var(--border);margin-bottom:16px}
.tab{display:inline-flex;align-items:center;gap:6px;padding:8px 10px;color:var(--muted);border-bottom:2px solid transparent;margin-bottom:-1px;font-size:14px}
.tab:hover{color:var(--text);text-decoration:none}
.tab[aria-current=page]{color:var(--text);border-bottom-color:var(--text);font-weight:500}
.tab .num{font-size:12px;color:var(--muted);font-weight:400}
.toolbar{position:sticky;top:0;z-index:5;background:var(--bg);display:flex;flex-wrap:wrap;gap:8px;align-items:center;margin:0 0 12px;padding:8px 0}
.search{position:relative;flex:1 1 280px;min-width:0;display:flex;align-items:center}
.search svg{position:absolute;left:10px;width:15px;height:15px;color:var(--muted);pointer-events:none}
.search input{max-width:none;padding-left:32px;padding-right:44px}
.search kbd{position:absolute;right:8px}
.field{display:inline-flex;align-items:center;gap:6px;margin:0;font-weight:400;color:var(--muted);font-size:13px}
.field select{width:auto;max-width:none;height:36px;padding:0 8px}
.dir{width:36px;padding:0;min-height:36px}
.clear{color:var(--muted);font-size:13px;padding:0 4px}
.facets{display:flex;flex-wrap:wrap;gap:6px;align-items:center;margin:0 0 20px}
.facets .label{font-size:12px;color:var(--muted);margin-right:2px}
.facets .label+.label,.facets .pill+.label{margin-left:10px}
.section-head{display:flex;align-items:baseline;justify-content:space-between;gap:12px;margin:24px 0 8px}
.section-head .num{color:var(--muted);font-weight:400;font-size:13px;margin-left:4px}
.section-head a{color:var(--muted);font-size:13px}
.shelf-grid{display:grid;grid-template-columns:1fr;gap:8px}
.pin{display:grid;grid-template-columns:44px minmax(0,1fr) auto;align-items:center;border:1px solid var(--border);border-radius:var(--radius);padding:4px 12px 4px 0;min-height:56px}
.pin:hover{border-color:var(--border-strong)}
.pin form{margin:0}
.pin-title{font-weight:500;display:-webkit-box;-webkit-line-clamp:1;-webkit-box-orient:vertical;overflow:hidden;overflow-wrap:anywhere}
.pin-meta{display:flex;gap:6px;align-items:center;font-size:12px;color:var(--muted);margin-top:2px;min-width:0;flex-wrap:wrap}
.pin .details{font-size:13px;color:var(--muted);padding-left:8px}
.list{border:1px solid var(--border);border-radius:var(--radius)}
.draft-row{display:grid;grid-template-columns:44px minmax(0,1fr) auto;align-items:center;gap:0 8px;padding:1px 12px 1px 0;min-height:46px;border-bottom:1px solid var(--border)}
.draft-row:last-child{border-bottom:0}
.draft-row:hover{background:var(--surface)}
.draft-row:target{background:var(--hover)}
.draft-row form{margin:0}
.draft-row.shared{padding-left:16px;grid-template-columns:minmax(0,1fr) auto}
.row-main{min-width:0;padding:6px 0}
.row-title{font-size:14px;font-weight:500;display:-webkit-box;-webkit-line-clamp:1;-webkit-box-orient:vertical;overflow:hidden;overflow-wrap:anywhere}
.row-desc{margin:1px 0 0;font-size:13px;color:var(--muted);display:-webkit-box;-webkit-line-clamp:1;-webkit-box-orient:vertical;overflow:hidden;overflow-wrap:anywhere}
.row-meta{display:flex;flex-wrap:wrap;align-items:center;justify-content:flex-end;gap:6px;font-size:13px;color:var(--muted);min-width:0;padding:6px 0}
.row-meta time{min-width:44px;text-align:right;font-size:12.5px}
.row-meta .details{color:var(--muted)}
.row-meta .details:hover{color:var(--text)}
.vis{display:inline-flex;align-items:center;gap:6px}
.vis .badge{border:0;padding:0;background:none}
.draft-row .badge.ws{display:none}
.row-select{width:16px;height:16px;margin:0 0 0 14px;accent-color:var(--text)}
.empty{border:1px dashed var(--border-strong);border-radius:var(--radius);padding:32px 20px;text-align:center;color:var(--muted)}
.empty p{margin:6px 0}
@media(min-width:720px){.shelf-grid{grid-template-columns:repeat(2,minmax(0,1fr))}}
@media(min-width:1100px){.shelf-grid{grid-template-columns:repeat(3,minmax(0,1fr))}.draft-row .badge.ws{display:inline-flex}}
@media(max-width:899px){.draft-row{grid-template-columns:44px minmax(0,1fr)}.draft-row .row-meta{grid-column:2;justify-content:flex-start;padding:0 0 8px;gap:4px 6px}.draft-row.shared{grid-template-columns:minmax(0,1fr)}.draft-row.shared .row-meta{grid-column:1}.row-meta time{text-align:left;min-width:0}.row-main{padding:8px 0 2px}}
@media(max-width:639px){.search input{font-size:16px}.search kbd{display:none}.search input{padding-right:10px}.toolbar{position:static}.field{flex:1 1 120px}.field select{flex:1;width:100%%}.draft-row .row-desc{display:none}.draft-row .pill.slug,.draft-row .vis,.draft-row form.archive{display:none}.tab{padding:8px 7px;font-size:13px;gap:4px}.toolbar .apply{flex:0 0 auto}}
@media(hover:none){.search kbd{display:none}}
.detail-head{display:grid;grid-template-columns:auto minmax(0,1fr);gap:4px 4px;align-items:start;margin-bottom:8px}
.detail-head h1{padding-top:6px;overflow-wrap:anywhere}
.detail-head form{margin:0}
.detail-sub{margin:0 0 16px 48px;color:var(--muted)}
.tag-editor{display:flex;flex-wrap:wrap;gap:6px;align-items:center;margin:0 0 20px 48px}
.tag-editor form{margin:0;display:inline-flex;gap:6px;align-items:center}
.tag-editor .pill button{background:none;border:0;color:var(--muted);padding:0 0 0 2px;min-height:0;font-size:13px;line-height:1}
.tag-editor .pill button:hover{color:var(--danger)}
.tag-editor input{width:140px;height:28px;font-size:13px}
.links{display:flex;gap:8px;flex-wrap:wrap;margin:0 0 8px 48px}
@media(max-width:600px){.detail-sub,.tag-editor,.links{margin-left:0}.tag-editor input{font-size:16px}}
"""

WEB_CSS = (_BASE_CSS + _DASH_CSS).strip() % {"light": _LIGHT, "dark": _DARK}

_MARK_SVG = ('<svg viewBox="0 0 32 32" aria-hidden="true"><rect width="32" height="32" rx="7" fill="currentColor"/>'
             '<path d="M9 10.5h14M16 10.5v12" fill="none" stroke="var(--bg)" stroke-width="3" stroke-linecap="round"/></svg>')
_ICON = {
    "system": '<svg viewBox="0 0 16 16" fill="none" stroke="currentColor" stroke-width="1.5" aria-hidden="true">'
              '<rect x="1.75" y="2.75" width="12.5" height="8.5" rx="1.5"/><path d="M5.5 13.75h5M8 11.25v2.5"/></svg>',
    "light": '<svg viewBox="0 0 16 16" fill="none" stroke="currentColor" stroke-width="1.5" aria-hidden="true">'
             '<circle cx="8" cy="8" r="3"/><path d="M8 1v1.5M8 13.5V15M1 8h1.5M13.5 8H15M3.05 3.05l1.06 1.06'
             'M11.89 11.89l1.06 1.06M3.05 12.95l1.06-1.06M11.89 4.11l1.06-1.06"/></svg>',
    "dark": '<svg viewBox="0 0 16 16" fill="none" stroke="currentColor" stroke-width="1.5" aria-hidden="true">'
            '<path d="M13.5 9.5A5.5 5.5 0 0 1 6.5 2.5a5.5 5.5 0 1 0 7 7z"/></svg>',
    "search": '<svg viewBox="0 0 16 16" fill="none" stroke="currentColor" stroke-width="1.5" aria-hidden="true">'
              '<circle cx="7" cy="7" r="4.75"/><path d="m10.5 10.5 3.75 3.75"/></svg>',
}


_CURRENT_PAGE = ' aria-current="page"'
_CURRENT_TRUE = ' aria-current="true"'


def esc(value: object) -> str:
    return html.escape(str(value) if value is not None else "", quote=True)


def csrf_field(value: str) -> str:
    return f'<input type="hidden" name="csrf" value="{esc(value)}">'


def _font_faces(base_url: str) -> str:
    faces = ""
    for family, name in (("Geist", "geist-sans.woff2"), ("Geist Mono", "geist-mono.woff2")):
        faces += (f'@font-face{{font-family:"{family}";src:url("{esc(asset_url(base_url, name))}") format("woff2");'
                  'font-weight:100 900;font-style:normal;font-display:swap}')
    return faces


def _theme_form(base_url: str, session: dict, theme: str, next_path: str) -> str:
    base = esc(base_url.rstrip("/"))
    form = (f'<form method="post" action="{base}/dashboard/theme" class="theme-switch" aria-label="Color theme">'
            f'{csrf_field(session["csrf"])}<input type="hidden" name="next" value="{esc(next_path)}">')
    for value in THEMES:
        label = value.capitalize() + " theme"
        pressed = "true" if value == theme else "false"
        form += (f'<button type="submit" name="theme" value="{value}" aria-pressed="{pressed}" '
                 f'aria-label="{label}" title="{label}">{_ICON[value]}</button>')
    return form + '</form>'


def web_page(title: str, body: str, base_url: str, session: dict | None = None, *, theme: str = "system",
             nav: str = "", next_path: str = "/dashboard", main_attrs: str = "") -> str:
    """Wrap `body` in the shared Geist page shell with fonts, header, and theme."""
    theme = theme if theme in THEMES else "system"
    base = esc(base_url.rstrip("/"))
    links = (("drafts", "/dashboard", "Drafts"), ("cli", "/cli/auth", "CLI setup"))
    nav_html = "".join(
        f'<a href="{base}{href}"{_CURRENT_PAGE if key == nav else ""}>{label}</a>'
        for key, href, label in links
    )
    header = (f'<header class="top"><div class="wrap"><a class="brand" href="{base}/dashboard">{_MARK_SVG}'
              f'<span>Tailplan</span></a><nav class="nav" aria-label="Main">{nav_html}</nav>')
    if session:
        header += f'<div class="account">{_theme_form(base_url, session, theme, next_path)}'
        header += f'<span class="who">{esc(session["accountName"])}</span>'
        header += (f'<form method="post" action="{base}/auth/sign-out">{csrf_field(session["csrf"])}'
                   '<button class="secondary sm">Sign out</button></form></div>')
    header += '</div></header>'
    data_theme = f' data-theme="{theme}"' if theme != "system" else ""
    return (f'<!doctype html><html lang="en"{data_theme}><head><meta charset="utf-8">'
            '<meta name="viewport" content="width=device-width,initial-scale=1">'
            '<meta name="color-scheme" content="light dark">'
            f'<title>{esc(title)} · Tailplan</title><style>{_font_faces(base_url)}{WEB_CSS}</style></head>'
            f'<body>{header}<main{main_attrs}>{body}</main></body></html>')


def render_login(base_url: str, csrf: str, next_path: str, tailscale: bool, *,
                 api_key: bool = True, oidc: bool = False, theme: str = "system") -> str:
    fields = csrf_field(csrf) + f'<input type="hidden" name="next" value="{esc(next_path)}">'
    base = esc(base_url.rstrip("/"))
    action = base + "/auth/sign-in"
    body = ('<div class="narrow"><h1>Your drafts, in one place</h1>'
            '<p class="muted lead">Sign in to manage drafts and connect your agents.</p>'
            '<div class="card">')
    if oidc:
        body += (f'<p><a class="button" href="{base}/auth/oidc/start?next={esc(quote(next_path, safe="/"))}">'
                 'Continue with single sign-on</a></p>')
    if tailscale:
        body += (f'<form method="post" action="{action}">{fields}<input type="hidden" name="method" value="tailscale">'
                 '<button>Continue with Tailscale</button></form>')
    if api_key:
        body += f'<form method="post" action="{action}">{fields}<label for="token">API key</label>'
        body += ('<input id="token" name="token" type="password" autocomplete="current-password" required maxlength="512">'
                 '<p class="after"><button>Sign in with API key</button></p></form>')
        body += '<p class="muted">Use your installed Tailplan token or a named API key.</p>'
    body += '</div></div>'
    return web_page("Sign in", body, base_url, theme=theme)


def external_link(url: object, label: str, *, css: str = "") -> str:
    try:
        parsed = urlparse(str(url))
        if parsed.scheme not in {"https", "http"} or not parsed.hostname or parsed.username:
            return esc(label)
    except ValueError:
        return esc(label)
    css_attr = f' class="{css}"' if css else ""
    return f'<a href="{esc(url)}"{css_attr} target="_blank" rel="noopener noreferrer">{esc(label)}</a>'


def sharing_badges(draft: dict) -> str:
    badges = ""
    if draft.get("workspace"):
        badges += f' <span class="badge ws" title="Workspace">{esc(draft["workspace"])}</span>'
    if draft.get("visibility"):
        badges += f' <span class="badge scope" title="Visibility">{esc(draft["visibility"])}</span>'
    return badges


# Time helpers. Every stamp is UTC.

def _stamp(value: object) -> datetime | None:
    if not isinstance(value, str) or not value:
        return None
    try:
        stamp = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    return stamp.replace(tzinfo=UTC) if stamp.tzinfo is None else stamp.astimezone(UTC)


def _exact(value: object) -> str:
    return str(value or "")[:16].replace("T", " ") + " UTC" if value else ""


def _relative(stamp: datetime | None, now: datetime) -> str:
    if stamp is None:
        return ""
    seconds = (now - stamp).total_seconds()
    if seconds < 60:
        return "now"
    if seconds < 3600:
        return f"{int(seconds // 60)}m"
    if seconds < 86400:
        return f"{int(seconds // 3600)}h"
    if seconds < 7 * 86400:
        return f"{int(seconds // 86400)}d"
    label = stamp.strftime("%b ") + str(stamp.day)
    return label if stamp.year == now.year else f"{label}, {stamp.year}"


def _time_tag(value: object, now: datetime) -> str:
    stamp = _stamp(value)
    if stamp is None:
        return ""
    return (f'<time datetime="{esc(stamp.strftime("%Y-%m-%dT%H:%M:%SZ"))}" title="{esc(_exact(value))}">'
            f'{esc(_relative(stamp, now))}</time>')


def _time_bucket(value: object, now: datetime) -> str:
    stamp = _stamp(value)
    if stamp is None:
        return "Earlier"
    if stamp.date() >= now.date():
        return "Today"
    if now - stamp < timedelta(days=7):
        return "This week"
    if now - stamp < timedelta(days=30):
        return "This month"
    return "Earlier"


# Dashboard state and URLs.

def _repo_label(draft: dict) -> str:
    org, name = draft.get("repoOrg"), draft.get("repoName")
    return f"{org}/{name}" if org and name else (name or org or "")


def _facet_counts(drafts: list[dict], key) -> list[tuple[str, int]]:
    counts: dict[str, int] = {}
    for draft in drafts:
        for value in key(draft):
            counts[value] = counts.get(value, 0) + 1
    return sorted(counts.items(), key=lambda item: (-item[1], item[0]))


def _default_state(drafts: list[dict], shared: list[dict]) -> dict:
    """Build a dashboard state from plain draft lists, for callers without a query state."""
    live = [draft for draft in drafts if not draft.get("archived")]
    return {
        "view": "active", "q": "", "sort": "updated", "direction": "desc", "group": "time",
        "tag": "", "repo": "",
        "counts": {
            "active": len(live),
            "favorites": sum(bool(draft.get("favorite")) for draft in live),
            "living": sum((draft.get("versionCount") or 0) > 1 for draft in live),
            "shared": len(shared),
            "archived": len(drafts) - len(live),
        },
        "tags": [{"tag": name, "count": count}
                 for name, count in _facet_counts(live, lambda d: d.get("tags") or [])],
        "repos": [{"repo": name, "count": count}
                  for name, count in _facet_counts(live, lambda d: [_repo_label(d)] if _repo_label(d) else [])],
        "suggestedTags": {},
        "favorites": [draft for draft in live if draft.get("favorite")],
        "drafts": live,
    }


_DEFAULTS = {"view": "active", "sort": "updated", "group": "time"}


def _params(state: dict, **changes: str) -> dict[str, str]:
    """Return the query fields of the current dashboard state, with `changes` applied."""
    current = {
        "view": state.get("view") or "active", "q": state.get("q") or "", "tag": state.get("tag") or "",
        "repo": state.get("repo") or "", "sort": state.get("sort") or "updated",
        "dir": state.get("direction") or "", "group": state.get("group") or "time",
    }
    current.update(changes)
    if current["dir"] == _default_dir(current["sort"]):
        current["dir"] = ""
    if current["sort"] == "relevance" and not current["q"]:
        current["sort"] = ""
    return {key: value for key, value in current.items() if value and _DEFAULTS.get(key) != value}


def _default_dir(sort: str) -> str:
    return "asc" if sort == "title" else "desc"


def _dash_path(state: dict, **changes: str) -> str:
    query = urlencode(_params(state, **changes))
    return "/dashboard" + (f"?{query}" if query else "")


# Dashboard parts.

def _star_form(base: str, draft: dict, csrf: str, next_path: str) -> str:
    is_fav = bool(draft.get("favorite"))
    action = "unfavorite" if is_fav else "favorite"
    label = "Remove from favorites" if is_fav else "Add to favorites"
    return (f'<form method="post" action="{base}/dashboard/drafts/{esc(draft["draftId"])}/{action}">'
            f'{csrf_field(csrf)}<input type="hidden" name="next" value="{esc(next_path)}">'
            f'<button class="star{" on" if is_fav else ""}" type="submit" aria-label="{label}" '
            f'aria-pressed="{"true" if is_fav else "false"}" title="{label}">{"★" if is_fav else "☆"}</button></form>')


def _version_chip(draft: dict) -> str:
    count = draft.get("versionCount") or 1
    number = draft.get("latestVersionNumber") or count
    noun = "version" if count == 1 else "versions"
    return f'<span class="ver{" multi" if count > 1 else ""}" title="{count} {noun}">v{esc(number)}</span>'


def _search_text(draft: dict) -> str:
    parts = [draft.get("title"), draft.get("description"), draft.get("slug"), _repo_label(draft),
             " ".join(draft.get("tags") or [])]
    return " ".join(str(part) for part in parts if part).lower()


def _render_dashboard_row(base_url: str, session: dict, draft: dict, *, show_repo: bool = True,
                          state: dict | None = None, now: datetime | None = None, next_path: str = "/dashboard",
                          suggestion: str | None = None) -> str:
    """Render one owned draft as a dense list row with owner actions."""
    state = state or {}
    now = now or datetime.now(UTC)
    base = esc(base_url.rstrip("/"))
    draft_id = esc(draft["draftId"])
    detail_url = f"{base}/dashboard/drafts/{draft_id}"
    csrf = session["csrf"]
    tags = list(draft.get("tags") or [])
    archived = bool(draft.get("archived"))
    classes = "draft-row" + (" fav" if draft.get("favorite") else "") + (" archived" if archived else "")
    row = (f'<article class="{classes}" id="d-{draft_id}" data-draft-id="{draft_id}" data-title="{esc(draft.get("title"))}"'
           f' data-tags="{esc(" ".join(tags))}" data-favorite="{"1" if draft.get("favorite") else "0"}"'
           f' data-archived="{"1" if archived else "0"}" data-share-url="{esc(draft.get("shareUrl"))}"'
           f' data-detail-url="{detail_url}" data-s="{esc(_search_text(draft))}">')
    row += (f'<input type="checkbox" class="row-select" name="ids" value="{draft_id}" hidden '
            f'aria-label="Select {esc(draft.get("title"))}">')
    row += _star_form(base, draft, csrf, next_path)
    row += f'<div class="row-main"><h3 class="row-title">{external_link(draft.get("shareUrl"), draft.get("title") or "Untitled")}</h3>'
    if draft.get("snippet"):
        # The server escaped the snippet and added only <mark> tags.
        row += f'<p class="row-desc">{draft["snippet"]}</p>'
    elif draft.get("description"):
        row += f'<p class="row-desc">{esc(draft["description"])}</p>'
    row += '</div><div class="row-meta">'
    for tag in tags[:3]:
        row += f'<a class="pill" href="{base}{esc(_dash_path(state, tag=tag))}">{esc(tag)}</a>'
    if len(tags) > 3:
        row += f'<span class="pill num">+{len(tags) - 3}</span>'
    if not tags and suggestion and not archived:
        row += (f'<form method="post" action="{detail_url}/tags">{csrf_field(csrf)}'
                f'<input type="hidden" name="next" value="{esc(next_path)}">'
                f'<input type="hidden" name="add_tag" value="{esc(suggestion)}">'
                f'<button class="pill dashed" type="submit" title="Add the suggested tag {esc(suggestion)}">'
                f'+ {esc(suggestion)}</button></form>')
    if show_repo and (repo := _repo_label(draft)):
        row += f'<a class="pill" href="{base}{esc(_dash_path(state, repo=repo))}">{esc(repo)}</a>'
    if draft.get("slug"):
        row += f'<span class="pill slug mono">{esc(draft["slug"])}</span>'
    if draft.get("disabled"):
        row += '<span class="badge warn">disabled</span>'
    visibility = str(draft.get("visibility") or "")
    if visibility or draft.get("workspace"):
        row += (f'<span class="vis"><span class="dot vis-{esc(visibility)}" aria-hidden="true"></span>'
                f'{sharing_badges(draft)}</span>')
    row += _version_chip(draft) + _time_tag(draft.get("updatedAt"), now)
    action, label = ("unarchive", "Unarchive") if archived else ("archive", "Archive")
    row += (f'<form class="archive" method="post" action="{detail_url}/{action}">{csrf_field(csrf)}'
            f'<input type="hidden" name="next" value="{esc(next_path)}">'
            f'<button class="ghost sm" type="submit">{label}</button></form>')
    row += f'<a class="details" href="{detail_url}">Details</a></div></article>'
    return row


def _render_shared_row(draft: dict, *, now: datetime | None = None) -> str:
    """Render one draft that another account shared. It has no owner actions."""
    now = now or datetime.now(UTC)
    row = (f'<article class="draft-row shared" data-draft-id="{esc(draft.get("draftId"))}"'
           f' data-title="{esc(draft.get("title"))}" data-share-url="{esc(draft.get("shareUrl"))}"'
           f' data-s="{esc(_search_text(draft))}">')
    row += f'<div class="row-main"><h3 class="row-title">{external_link(draft.get("shareUrl"), draft.get("title") or "Untitled")}</h3>'
    if draft.get("snippet"):
        row += f'<p class="row-desc">{draft["snippet"]}</p>'
    elif draft.get("description"):
        row += f'<p class="row-desc">{esc(draft["description"])}</p>'
    visibility = str(draft.get("visibility") or "")
    row += '</div><div class="row-meta">'
    if visibility or draft.get("workspace"):
        row += (f'<span class="vis"><span class="dot vis-{esc(visibility)}" aria-hidden="true"></span>'
                f'{sharing_badges(draft)}</span>')
    row += _version_chip(draft) + _time_tag(draft.get("updatedAt"), now) + '</div></article>'
    return row


def _group_drafts(drafts: list[dict], group: str, now: datetime) -> list[tuple[str, list[dict]]]:
    """Split drafts into labeled sections. Empty sections are left out."""
    if group == "none":
        return [("", drafts)]
    sections: dict[str, list[dict]] = {}
    if group == "tag":
        for draft in drafts:
            tags = draft.get("tags") or []
            sections.setdefault(tags[0] if tags else "", []).append(draft)
        order = sorted((key for key in sections if key),
                       key=lambda key: max(d.get("updatedAt") or "" for d in sections[key]), reverse=True)
        order.sort(key=lambda key: -len(sections[key]))
        return [(key, sections[key]) for key in order] + ([("Untagged", sections[""])] if "" in sections else [])
    if group == "repo":
        for draft in drafts:
            sections.setdefault(_repo_label(draft), []).append(draft)
        order = sorted((key for key in sections if key), key=lambda key: (-len(sections[key]), key))
        return [(key, sections[key]) for key in order] + (
            [("No repository", sections[""])] if "" in sections else [])
    for draft in drafts:
        sections.setdefault(_time_bucket(draft.get("updatedAt"), now), []).append(draft)
    return [(label, sections[label]) for label in ("Today", "This week", "This month", "Earlier") if label in sections]


def _tabs(base: str, state: dict) -> str:
    counts = state.get("counts") or {}
    current = state.get("view") or "active"
    html_tabs = '<nav class="tabs" aria-label="Views">'
    for view, label in (("active", "Active"), ("favorites", "Favorites"), ("living", "Living"),
                        ("shared", "Shared"), ("archived", "Archived")):
        href = base + _dash_path({"sort": state.get("sort"), "direction": state.get("direction"),
                                  "group": state.get("group")}, view=view)
        aria = ' aria-current="page"' if view == current else ""
        html_tabs += (f'<a class="tab" href="{esc(href)}"{aria}>{label}'
                      f'<span class="num">{int(counts.get(view, 0) or 0)}</span></a>')
    return html_tabs + '</nav>'


def _toolbar(base: str, state: dict) -> str:
    q = state.get("q") or ""
    sort = state.get("sort") or "updated"
    direction = state.get("direction") or _default_dir(sort)
    group = state.get("group") or "time"
    form = f'<form class="toolbar" method="get" action="{base}/dashboard" role="search">'
    for key in ("view", "tag", "repo"):
        if state.get(key) and _DEFAULTS.get(key) != state.get(key):
            form += f'<input type="hidden" name="{key}" value="{esc(state[key])}">'
    form += (f'<div class="search">{_ICON["search"]}<label class="sr-only" for="dash-q">Search drafts</label>'
             f'<input id="dash-q" type="search" name="q" value="{esc(q)}" autocomplete="off" maxlength="500"'
             ' placeholder="Search drafts or try is:living, tag:dawn…"><kbd aria-hidden="true">⌘K</kbd></div>')
    sorts = [("updated", "Updated"), ("living", "Living"), ("favorited", "Favorited"),
             ("created", "Created"), ("title", "Title")]
    if q:
        sorts.insert(0, ("relevance", "Relevance"))
    form += '<label class="field" for="dash-sort">Sort<select id="dash-sort" name="sort">'
    form += "".join(f'<option value="{value}"{" selected" if value == sort else ""}>{label}</option>'
                    for value, label in sorts)
    form += '</select></label>'
    if direction != _default_dir(sort):
        form += f'<input type="hidden" name="dir" value="{esc(direction)}">'
    flipped = "asc" if direction == "desc" else "desc"
    arrow, word = ("↓", "descending") if direction == "desc" else ("↑", "ascending")
    form += (f'<button class="secondary dir" type="submit" name="dir" value="{flipped}" '
             f'title="Sorted {word}. Select to reverse." aria-label="Sorted {word}. Reverse order">{arrow}</button>')
    form += '<label class="field" for="dash-group">Group<select id="dash-group" name="group">'
    form += "".join(f'<option value="{value}"{" selected" if value == group else ""}>{label}</option>'
                    for value, label in (("time", "Time"), ("tag", "Tag"), ("repo", "Repo"), ("none", "Flat")))
    form += '</select></label><button class="apply" type="submit">Apply</button>'
    if q or state.get("tag") or state.get("repo"):
        form += f'<a class="clear" href="{esc(base + _dash_path(state, q="", tag="", repo=""))}">Clear</a>'
    return form + '</form>'


def _facets(base: str, state: dict) -> str:
    if (state.get("view") or "active") == "shared":
        return ""
    strips = ""
    for key, label, items in (("tag", "Tags", state.get("tags") or []), ("repo", "Repos", state.get("repos") or [])):
        if not items:
            continue
        strips += f'<span class="label">{label}</span>'
        selected = state.get(key) or ""
        for item in items[:12]:
            value = str(item.get(key) or "")
            on = value.lower() == selected.lower()
            href = base + _dash_path(state, **{key: "" if on else value})
            strips += (f'<a class="pill{" on" if on else ""}" href="{esc(href)}"'
                       f'{_CURRENT_TRUE if on else ""}>{esc(value)}'
                       f'<span class="num">{int(item.get("count") or 0)}</span></a>')
    return f'<div class="facets">{strips}</div>' if strips else ""


def _favorites_shelf(base: str, session: dict, state: dict, next_path: str) -> str:
    favorites = state.get("favorites") or []
    if (state.get("view") or "active") != "active" or not favorites or state.get("q") or state.get("tag") \
            or state.get("repo"):
        return ""
    count = (state.get("counts") or {}).get("favorites") or len(favorites)
    shelf = ('<section class="shelf" aria-label="Favorites"><div class="section-head"><h2>Favorites</h2>'
             f'<a href="{esc(base + _dash_path(state, view="favorites"))}">View all <span class="num">{int(count)}</span></a>'
             '</div><div class="shelf-grid">')
    for draft in favorites[:8]:
        label = (draft.get("tags") or [None])[0] or _repo_label(draft)
        shelf += (f'<article class="pin">{_star_form(base, draft, session["csrf"], next_path)}<div class="pin-body">'
                  f'<div class="pin-title">{external_link(draft.get("shareUrl"), draft.get("title") or "Untitled")}</div>'
                  f'<div class="pin-meta">{_version_chip(draft)}{f"<span>{esc(label)}</span>" if label else ""}</div></div>'
                  f'<a class="details" href="{base}/dashboard/drafts/{esc(draft["draftId"])}">Details</a></article>')
    return shelf + '</div></section>'


def render_dashboard(base_url: str, session: dict, drafts: list[dict], *, shared: list[dict] | None = None,
                     state: dict | None = None, theme: str = "system") -> str:
    """Render the dashboard from the query state. Without a state, show the active drafts."""
    shared = shared or []
    if state is None:
        state = _default_state(drafts, shared)
    theme = theme if theme != "system" or not state.get("theme") else state["theme"]
    base = esc(base_url.rstrip("/"))
    view = state.get("view") or "active"
    group = state.get("group") or "time"
    next_path = _dash_path(state)
    now = datetime.now(UTC)
    rows_source = state.get("drafts") if state.get("drafts") is not None else drafts
    suggestions = state.get("suggestedTags") or {}
    counts = state.get("counts") or {}
    total = sum(int(counts.get(key) or 0) for key in ("active", "archived"))
    filtered = bool(state.get("q") or state.get("tag") or state.get("repo"))

    body = (f'<div class="page-head"><h1>My drafts <span class="count">{total}</span></h1>'
            '<span class="muted">Publish with <code>tailplan upload plan.html</code></span></div>')
    body += _tabs(base, state) + _toolbar(base, state) + _facets(base, state)
    body += _favorites_shelf(base, session, state, next_path)

    if not rows_source:
        if filtered:
            body += (f'<div class="empty"><p>No drafts match this search.</p>'
                     f'<p><a href="{esc(base + _dash_path(state, q="", tag="", repo=""))}">Clear filters</a></p></div>')
        elif view == "shared":
            body += '<div class="empty"><p>No one has shared a draft with you yet.</p></div>'
        elif view == "active" and not total:
            body += ('<div class="empty"><p>No drafts yet.</p>'
                     '<p>Publish one with <code>tailplan upload plan.html</code>.</p></div>')
        else:
            body += f'<div class="empty"><p>No {esc(view)} drafts.</p></div>'
    elif view == "shared":
        for label, members in _group_drafts(rows_source, "none" if group == "tag" else group, now):
            body += _section(label, "".join(_render_shared_row(draft, now=now) for draft in members), len(members))
    else:
        for label, members in _group_drafts(rows_source, group, now):
            rows = "".join(
                _render_dashboard_row(base_url, session, draft, state=state, now=now, next_path=next_path,
                                      show_repo=group != "repo", suggestion=suggestions.get(draft.get("draftId")))
                for draft in members
            )
            body += _section(label, rows, len(members))

    if view == "active" and shared and not filtered:
        body += _section("Shared with me", "".join(_render_shared_row(draft, now=now) for draft in shared[:5]),
                         len(shared), link=(base + _dash_path(state, view="shared"), "View all"))

    main_attrs = (f' id="dashboard-root" data-base="{base}" data-csrf="{esc(session["csrf"])}"'
                  f' data-view="{esc(view)}" data-sort="{esc(state.get("sort") or "updated")}"'
                  f' data-group="{esc(group)}" data-dir="{esc(state.get("direction") or "")}"')
    return web_page("My drafts", body, base_url, session, theme=theme, nav="drafts", next_path=next_path,
                    main_attrs=main_attrs)


def _section(label: str, rows: str, count: int, *, link: tuple[str, str] | None = None) -> str:
    if not label:
        return f'<section class="group"><h2 class="sr-only">Drafts</h2><div class="list">{rows}</div></section>'
    extra = f'<a href="{esc(link[0])}">{esc(link[1])}</a>' if link else ""
    return (f'<section class="group"><div class="section-head"><h2>{esc(label)}<span class="num">{count}</span></h2>'
            f'{extra}</div><div class="list">{rows}</div></section>')


VISIBILITY_LABELS = {
    "private": "Private: only you and people you add",
    "workspace": "Workspace: members of this workspace",
    "authenticated": "Signed in: any signed-in account",
    "link": "Link: anyone with the URL",
}


def render_sharing(base_url: str, session: dict, sharing: dict) -> str:
    action_base = esc(base_url.rstrip("/")) + "/dashboard/drafts/" + sharing["draftId"] + "/sharing"
    csrf = csrf_field(session["csrf"])
    body = '<section class="card" aria-labelledby="sharing-title"><h2 id="sharing-title">Sharing</h2>'
    body += (f'<p class="muted">Workspace <span class="badge ws">{esc(sharing["workspace"])}</span> · '
             f'Visibility <span class="badge scope">{esc(sharing["visibility"])}</span></p>')
    body += (f'<form method="post" action="{action_base}" class="inline">{csrf}<div><label for="visibility">'
             'Who can read</label><select id="visibility" name="visibility">')
    for value in sharing["allowedVisibilities"]:
        selected = " selected" if value == sharing["visibility"] else ""
        body += f'<option value="{esc(value)}"{selected}>{esc(VISIBILITY_LABELS[value])}</option>'
    body += '</select></div><button class="secondary">Save visibility</button></form>'
    body += (f'<dl><dt>Share URL</dt><dd>{external_link(sharing["shareUrl"], sharing["shareUrl"])}</dd>'
             f'<dt>Direct URL</dt><dd>{external_link(sharing["publicUrl"], sharing["publicUrl"])}</dd></dl>')
    body += '<h3>People and groups</h3>'
    if sharing["grants"]:
        body += ('<div class="table-wrap after"><table><thead><tr><th>Type</th><th>Who</th>'
                 '<th>Role</th><th>Action</th></tr></thead><tbody>')
        for grant in sharing["grants"]:
            body += (f'<tr><td data-label="Type">{esc(grant["type"])}</td><td data-label="Who">{esc(grant["value"])}</td>'
                     f'<td data-label="Role">{esc(grant["role"])}</td><td data-label="Action">'
                     f'<form method="post" action="{action_base}/revoke" class="flush">{csrf}'
                     f'<input type="hidden" name="type" value="{esc(grant["type"])}">'
                     f'<input type="hidden" name="value" value="{esc(grant["value"])}">'
                     f'<button class="secondary sm">Revoke</button></form></td></tr>')
        body += '</tbody></table></div>'
    else:
        body += '<p class="muted">No one else has access yet.</p>'
    body += (f'<form method="post" action="{action_base}/grants" class="inline">{csrf}'
             '<div><label for="grant-type">Type</label><select id="grant-type" name="type">'
             '<option value="email">Email</option><option value="group">Group</option>'
             '<option value="workspace">Workspace</option>'
             '<option value="tailscale_login">Tailscale login</option></select></div>'
             '<div><label for="grant-value">Email, login, or name</label>'
             '<input id="grant-value" name="value" required maxlength="255"></div>'
             '<div><label for="grant-role">Role</label><select id="grant-role" name="role">'
             '<option value="viewer">Viewer</option><option value="editor">Editor</option></select></div>'
             '<button>Add access</button></form></section>')
    return body


def render_activate(base_url: str, session: dict, code: str | None, info: dict | None, *,
                    result: str | None = None, theme: str = "system") -> str:
    base = esc(base_url.rstrip("/"))
    page = {"theme": theme, "nav": "cli", "next_path": "/cli/activate"}
    if result is not None:
        message = {
            "approved": ("CLI approved", "Return to your terminal. The CLI finishes sign-in."),
            "denied": ("CLI denied", "The CLI does not get a key."),
        }.get(result, ("Code not found", "The code is wrong, used, or expired. Start again in the terminal."))
        body = f'<div class="narrow"><h1>{esc(message[0])}</h1><p class="muted lead">{esc(message[1])}</p>'
        body += f'<p><a class="button secondary" href="{base}/cli/auth">CLI setup</a></p></div>'
        return web_page(message[0], body, base_url, session, **page)
    body = '<div class="narrow"><h1>Connect a CLI</h1><div class="card">'
    if info is None:
        if code:
            body += '<p class="notice">Tailplan cannot find that code. It can be wrong or expired.</p>'
        body += f'<form method="get" action="{base}/cli/activate"><label for="code">Code from your terminal</label>'
        body += (f'<input id="code" name="code" class="mono" required maxlength="16" autocomplete="off" '
                 f'value="{esc(code or "")}">')
        body += '<p class="after"><button>Continue</button></p></form></div></div>'
        return web_page("Connect a CLI", body, base_url, session, **page)
    body += (f'<p>A CLI asks for an API key on <strong>{esc(session["accountName"])}</strong>.</p>'
             f'<dl><dt>Code</dt><dd><code>{esc(info["userCode"])}</code></dd>'
             f'<dt>Client</dt><dd>{esc(info.get("clientName") or "tailplan CLI")}</dd>'
             f'<dt>Expires</dt><dd><time>{esc(_exact(info["expiresAt"]))}</time></dd></dl>'
             '<p class="muted">Approve only when this code matches your terminal.</p><div class="actions">')
    for decision, label, css in (("approve", "Approve", ""), ("deny", "Deny", "secondary")):
        body += (f'<form method="post" action="{base}/cli/activate">{csrf_field(session["csrf"])}'
                 f'<input type="hidden" name="code" value="{esc(info["userCode"])}">'
                 f'<input type="hidden" name="decision" value="{decision}">'
                 f'<button class="{css}">{label}</button></form>')
    body += '</div></div></div>'
    return web_page("Connect a CLI", body, base_url, session, **page)


def render_detail(base_url: str, session: dict, detail: dict, *, sharing: dict | None = None,
                  theme: str = "system") -> str:
    draft = detail["draft"]
    base = esc(base_url.rstrip("/"))
    draft_id = esc(draft["draftId"])
    action_base = f"{base}/dashboard/drafts/{draft_id}"
    next_path = f"/dashboard/drafts/{draft['draftId']}"
    csrf = csrf_field(session["csrf"])
    hidden_next = f'<input type="hidden" name="next" value="{esc(next_path)}">'
    body = (f'<p class="crumb"><a class="muted" href="{base}/dashboard">← Drafts</a></p>'
            f'<div class="detail-head">{_star_form(base, draft, session["csrf"], next_path)}'
            f'<h1>{esc(draft["title"])}</h1></div>')
    meta = [_version_chip(draft)]
    if draft.get("updatedAt"):
        meta.append(f'<span>Updated {_time_tag(draft["updatedAt"], datetime.now(UTC))}</span>')
    if _repo_label(draft):
        meta.append(f'<span class="pill">{esc(_repo_label(draft))}</span>')
    if draft.get("archived"):
        meta.append('<span class="badge">archived</span>')
    body += f'<div class="detail-sub actions">{"".join(meta)}</div>'
    if draft.get("description"):
        body += f'<p class="detail-sub">{esc(draft["description"])}</p>'
    body += '<div class="tag-editor" aria-label="Tags">'
    for tag in draft.get("tags") or []:
        body += (f'<span class="pill">{esc(tag)}<form method="post" action="{action_base}/tags">{csrf}{hidden_next}'
                 f'<input type="hidden" name="remove_tag" value="{esc(tag)}">'
                 f'<button type="submit" aria-label="Remove tag {esc(tag)}" title="Remove tag">×</button></form></span>')
    body += (f'<form method="post" action="{action_base}/tags">{csrf}{hidden_next}'
             '<label class="sr-only" for="add-tag">Add tag</label>'
             '<input id="add-tag" name="add_tag" maxlength="32" placeholder="Add tag" autocomplete="off" required>'
             '<button class="secondary sm" type="submit">Add</button></form></div>')
    body += (f'<div class="links">{external_link(draft["shareUrl"], "Open latest draft", css="button sm")}'
             f'{external_link(draft["rawUrl"], "Raw HTML", css="button secondary sm")}')
    archive_action, archive_label = ("unarchive", "Unarchive") if draft.get("archived") else ("archive", "Archive")
    body += (f'<form method="post" action="{action_base}/{archive_action}" class="flush">{csrf}{hidden_next}'
             f'<button class="secondary sm">{archive_label}</button></form>')
    toggle = ("enable", "Enable draft") if draft["disabled"] else ("disable", "Disable draft")
    body += (f'<form method="post" action="{action_base}/{toggle[0]}" class="flush">{csrf}'
             f'<button class="secondary sm">{toggle[1]}</button></form></div>')
    if draft["disabled"]:
        body += f'<p class="notice">Disabled: {esc(draft["disabledReason"])}</p>'
    if sharing is not None:
        body += render_sharing(base_url, session, sharing)
    body += ('<div class="section-head"><h2>Version history</h2></div><div class="table-wrap"><table><thead><tr>'
             '<th>Version</th><th>Commit</th><th>Ref</th><th>Published</th><th>Source</th></tr></thead><tbody>')
    version_base = draft.get("versionBaseUrl") or f'{base_url.rstrip("/")}/d/{draft["draftId"]}'
    for version in detail["versions"]:
        number = version["versionNumber"]
        url = version_base + f"/v/{number}"
        dirty = ' <span class="badge warn">dirty</span>' if version.get("gitDirty") else ""
        body += (f'<tr><td data-label="Version" class="mono">{external_link(url, "v" + str(number))}<br>'
                 f'{external_link(url + "/raw", "Raw HTML")}</td>')
        body += (f'<td data-label="Commit">{esc(version.get("gitCommitSubject"))}{dirty}</td>'
                 f'<td data-label="Ref" class="mono">{esc(version.get("gitBranch"))}<br>'
                 f'<code>{esc((version.get("gitCommitSha") or "")[:12])}</code></td>')
        body += (f'<td data-label="Published" class="mono"><time>{esc(_exact(version["createdAt"]))}</time></td>'
                 f'<td data-label="Source"><span class="num">{esc(version["fileSize"])}</span> bytes')
        if version.get("ciRunUrl"):
            body += "<br>" + external_link(version["ciRunUrl"], "CI run")
        body += '<details><summary>Audit</summary><dl>'
        for field in ("fileSha256", "requestId", "apiKeyId", "sourceIp", "cliVersion", "ciActor",
                      "hasInlineScript", "externalImageHosts"):
            if version.get(field) is not None:
                body += f'<dt>{esc(field)}</dt><dd class="mono">{esc(version[field])}</dd>'
        body += '</dl></details></td></tr>'
    body += ('</tbody></table></div><details><summary>Delete this draft</summary>'
             '<p>Deletion removes this draft from the list and disables every version URL.</p>')
    body += f'<form method="post" action="{action_base}/delete">{csrf}<button class="danger">Delete draft</button></form></details>'
    return web_page(draft["title"], body, base_url, session, theme=theme, nav="drafts", next_path=next_path)


def render_keys(base_url: str, session: dict, keys: list[dict], *, theme: str = "system") -> str:
    base = esc(base_url.rstrip("/")) + "/cli/auth/keys"
    body = ('<h1>Connect your CLI</h1><p class="muted lead">Run <code>tailplan auth login</code>, '
            'then paste a new key into the terminal.</p>')
    body += (f'<form method="post" action="{base}" class="card inline">{csrf_field(session["csrf"])}'
             '<div><label for="name">Key name</label>'
             '<input id="name" name="name" maxlength="255" placeholder="Laptop agent" required></div>'
             '<button>Generate API key</button></form>')
    body += ('<div class="section-head"><h2>Active keys</h2></div><div class="table-wrap"><table><thead><tr><th>Name</th>'
             '<th>Created</th><th>Last used</th><th>Action</th></tr></thead><tbody>')
    for key in keys:
        body += (f'<tr><td data-label="Name">{esc(key["name"])}</td>'
                 f'<td data-label="Created" class="mono"><time>{esc(key["createdAt"][:16])}</time></td>'
                 f'<td data-label="Last used" class="mono"><time>{esc((key["lastUsedAt"] or "Never")[:16])}</time></td>')
        body += (f'<td data-label="Action"><form method="post" action="{base}/{key["id"]}/revoke" class="flush">'
                 f'{csrf_field(session["csrf"])}<button class="secondary sm">Revoke</button></form></td></tr>')
    body += '</tbody></table></div>'
    return web_page("CLI setup", body, base_url, session, theme=theme, nav="cli", next_path="/cli/auth")


def render_key(base_url: str, session: dict, result: dict, *, theme: str = "system") -> str:
    body = ('<div class="narrow"><h1>Your new API key</h1>'
            '<p class="muted lead">Copy this key now. Tailplan shows each key once.</p>')
    body += (f'<div class="card"><label for="key">{esc(result["apiKey"]["name"])}</label>'
             f'<input id="key" readonly value="{esc(result["token"])}">')
    body += '<p class="after">Paste the key into <code>tailplan auth login</code>.</p></div>'
    body += f'<p><a href="{esc(base_url.rstrip("/"))}/cli/auth">← Back to CLI setup</a></p></div>'
    return web_page("New API key", body, base_url, session, theme=theme, nav="cli", next_path="/cli/auth")
