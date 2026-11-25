"""Server-rendered HTML for the Tailplan web dashboard and sign-in pages."""
from __future__ import annotations

import html
import re
from urllib.parse import quote, urlparse

__all__ = [
    "WEB_CSS", "VISIBILITY_LABELS", "esc", "csrf_field", "web_page", "render_login",
    "external_link", "sharing_badges", "render_dashboard", "render_sharing",
    "render_activate", "render_detail", "render_keys", "render_key",
]

WEB_CSS = """
:root{color-scheme:light}*{box-sizing:border-box}body{margin:0;background:#f8fafc;color:#172033;font:16px/1.6 system-ui,sans-serif}
header,main{width:min(100% - 32px,1050px);margin:auto}header{display:flex;justify-content:space-between;gap:16px;flex-wrap:wrap;padding:20px 0;border-bottom:1px solid #dce3ec}
nav{display:flex;gap:20px;align-items:center}main{margin:36px auto 80px}h1{font-size:clamp(26px,5vw,36px);line-height:1.2;margin:0 0 20px}h2{font-size:18px;margin:30px 0 10px}
a{color:#245bc0;text-underline-offset:3px;overflow-wrap:anywhere}p{overflow-wrap:anywhere}.muted,small{color:#59687d}.row{padding:18px 20px;background:white;border:1px solid #dce3ec;border-radius:10px;margin:10px 0}
.row h3{margin:0;font-size:18px}.row p{margin:5px 0}.meta{font-size:14px;display:flex;gap:12px;flex-wrap:wrap;align-items:center}.meta form{margin:0;display:inline}.badge{font-size:12px;background:#fef3c7;border-radius:4px;padding:2px 6px}
button,.button{font:inherit;background:#172033;color:white;border:0;border-radius:7px;padding:9px 14px;cursor:pointer;text-decoration:none}button.secondary{background:#e5ebf4;color:#172033}button.star{background:none;color:#59687d;border:1px solid #dce3ec;border-radius:6px;padding:2px 8px;line-height:1.2}button.star.on{color:#b45309;border-color:#f59e0b;background:#fffbeb}
input,select{font:inherit;padding:10px;border:1px solid #b6c3d5;border-radius:6px;width:100%;max-width:600px;background:white;color:#172033}
.badge.scope{background:#e0ecff}.badge.ws{background:#e8f5e9}.card{padding:18px 20px;background:white;border:1px solid #dce3ec;border-radius:10px;margin:20px 0}.inline{display:flex;gap:10px;flex-wrap:wrap;align-items:end}.inline label{margin:0}.inline>*{flex:1 1 160px}.inline button{flex:0 0 auto}label{display:block;margin:12px 0 4px}form{margin:12px 0}header form{margin:0}header button{padding:4px 10px}
.actions{display:flex;gap:12px;flex-wrap:wrap}.actions form{margin:0}code{background:#eaf0f8;padding:2px 5px;border-radius:4px;overflow-wrap:anywhere}pre{white-space:pre-wrap;overflow-wrap:anywhere;background:white;padding:16px;border:1px solid #dce3ec;border-radius:8px}
.table-wrap{width:100%;overflow-x:auto}table{width:100%;border-collapse:collapse;background:white}th,td{text-align:left;padding:12px;border-bottom:1px solid #dce3ec;vertical-align:top}th{font-size:13px;color:#59687d}td{min-width:110px;max-width:430px;overflow-wrap:anywhere}
.narrow{max-width:620px}details{margin:10px 0}summary{cursor:pointer}dl{display:grid;grid-template-columns:minmax(90px,1fr) 3fr;gap:6px}dt{color:#59687d}dd{margin:0;overflow-wrap:anywhere}
@media(max-width:600px){header,main{width:calc(100% - 24px)}header{padding:12px 0}nav{gap:12px}.row{padding:14px}main{margin-top:24px}.table-wrap table,.table-wrap tbody,.table-wrap tr,.table-wrap td{display:block;width:100%}.table-wrap thead{position:absolute;width:1px;height:1px;overflow:hidden;clip-path:inset(50%)}.table-wrap tr{border:1px solid #dce3ec;border-radius:8px;margin-bottom:12px;background:white}.table-wrap td{max-width:none;min-width:0;border-bottom:0}.table-wrap td+td{border-top:1px solid #dce3ec}.table-wrap td[data-label]::before{content:attr(data-label);display:block;font-size:13px;font-weight:600;color:#59687d}}
""".strip()


def esc(value: object) -> str:
    return html.escape(str(value) if value is not None else "", quote=True)


def csrf_field(value: str) -> str:
    return f'<input type="hidden" name="csrf" value="{esc(value)}">'


def web_page(title: str, body: str, base_url: str, session: dict | None = None) -> str:
    base = esc(base_url.rstrip("/"))
    header = f'<header><nav><strong>Tailplan</strong><a href="{base}/dashboard">My drafts</a><a href="{base}/cli/auth">CLI setup</a></nav>'
    if session:
        header += f'<form method="post" action="{base}/auth/sign-out">{csrf_field(session["csrf"])}'
        header += f'<small>{esc(session["accountName"])}</small> <button class="secondary">Sign out</button></form>'
    header += '</header>'
    return ('<!doctype html><html lang="en"><head><meta charset="utf-8">'
            '<meta name="viewport" content="width=device-width,initial-scale=1">'
            f'<title>{esc(title)} · Tailplan</title><style>{WEB_CSS}</style></head>'
            f'<body>{header}<main>{body}</main></body></html>')


def render_login(base_url: str, csrf: str, next_path: str, tailscale: bool, *,
                 api_key: bool = True, oidc: bool = False) -> str:
    fields = csrf_field(csrf) + f'<input type="hidden" name="next" value="{esc(next_path)}">'
    base = esc(base_url.rstrip("/"))
    action = base + "/auth/sign-in"
    body = '<div class="narrow"><h1>Your drafts, in one place</h1><p>Sign in to manage drafts and connect your agents.</p>'
    if oidc:
        body += f'<p><a class="button" href="{base}/auth/oidc/start?next={esc(quote(next_path, safe="/"))}">Continue with single sign-on</a></p>'
    if tailscale:
        body += f'<form method="post" action="{action}">{fields}<input type="hidden" name="method" value="tailscale"><button>Continue with Tailscale</button></form>'
    if api_key:
        body += f'<form method="post" action="{action}">{fields}<label for="token">API key</label>'
        body += '<input id="token" name="token" type="password" autocomplete="current-password" required maxlength="512"><p><button>Sign in with API key</button></p></form>'
        body += '<p class="muted">Use your installed Tailplan token or a named API key.</p>'
    body += '</div>'
    return web_page("Sign in", body, base_url)


def external_link(url: object, label: str) -> str:
    try:
        parsed = urlparse(str(url))
        if parsed.scheme not in {"https", "http"} or not parsed.hostname or parsed.username:
            return esc(label)
    except ValueError:
        return esc(label)
    return f'<a href="{esc(url)}" target="_blank" rel="noopener noreferrer">{esc(label)}</a>'


def _render_dashboard_row(base_url: str, session: dict, draft: dict, *, show_repo: bool = False) -> str:
    badge = ' <span class="badge">disabled</span>' if draft["disabled"] else ""
    slug_badge = f' <span class="badge">{esc(draft["slug"])}</span>' if draft.get("slug") else ""
    scope_badges = sharing_badges(draft)
    row = f'<article class="row"><h3>{external_link(draft["shareUrl"], draft["title"])}{slug_badge}{badge}{scope_badges}</h3>'
    if draft["description"]:
        row += f'<p>{esc(draft["description"])}</p>'
    details = esc(base_url.rstrip("/")) + "/dashboard/drafts/" + draft["draftId"]
    is_fav = bool(draft.get("favorite"))
    action = "unfavorite" if is_fav else "favorite"
    star_label = "Remove from favorites" if is_fav else "Add to favorites"
    star_class = "star on" if is_fav else "star"
    star_glyph = "★" if is_fav else "☆"
    repo_span = ""
    if show_repo and draft.get("repoOrg") and draft.get("repoName"):
        repo_span = f'<span>{esc(draft["repoOrg"] + "/" + draft["repoName"])}</span>'
    row += (
        f'<div class="meta">'
        f'<form method="post" action="{details}/{action}">{csrf_field(session["csrf"])}'
        f'<button class="{star_class}" type="submit" aria-label="{star_label}" title="{star_label}">{star_glyph}</button></form>'
        f'{repo_span}'
        f'<a href="{details}">Details and history</a><span>v{draft["latestVersionNumber"]}</span>'
        f'<span>{draft["versionCount"]} versions</span><time>{esc(draft["updatedAt"][:16].replace("T", " "))} UTC</time></div></article>'
    )
    return row


def sharing_badges(draft: dict) -> str:
    badges = ""
    if draft.get("workspace"):
        badges += f' <span class="badge ws" title="Workspace">{esc(draft["workspace"])}</span>'
    if draft.get("visibility"):
        badges += f' <span class="badge scope" title="Visibility">{esc(draft["visibility"])}</span>'
    return badges


def _render_shared_row(draft: dict) -> str:
    row = f'<article class="row"><h3>{external_link(draft["shareUrl"], draft["title"])}{sharing_badges(draft)}</h3>'
    if draft.get("description"):
        row += f'<p>{esc(draft["description"])}</p>'
    row += (f'<div class="meta"><span>v{draft["latestVersionNumber"]}</span>'
            f'<time>{esc(draft["updatedAt"][:16].replace("T", " "))} UTC</time></div></article>')
    return row


def render_dashboard(base_url: str, session: dict, drafts: list[dict], *, shared: list[dict] | None = None) -> str:
    favorites = [draft for draft in drafts if draft.get("favorite")]
    non_favorites = [draft for draft in drafts if not draft.get("favorite")]
    groups: dict[tuple, list] = {}
    for draft in non_favorites:
        key = tuple(draft.get(field) or "" for field in ("repoHost", "repoOrg", "repoName"))
        groups.setdefault(key, []).append(draft)
    body = f'<h1>My drafts <small>{len(drafts)}</small></h1>'
    if not drafts:
        body += '<p>No drafts yet. Publish one with <code>tailplan upload plan.html</code>.</p>'
    if favorites:
        body += '<section><h2>Favorites</h2>'
        for draft in favorites:
            body += _render_dashboard_row(base_url, session, draft, show_repo=True)
        body += '</section>'
    for key, members in sorted(groups.items(), key=lambda entry: not bool(entry[0][2])):
        host, org, name = key
        label = f"{org}/{name}" if org and name else "No repository"
        if host and re.fullmatch(r"[A-Za-z0-9.-]+", host) and org and name:
            label = external_link(f"https://{host}/{quote(org, safe='')}/{quote(name, safe='')}", label)
        else:
            label = esc(label)
        body += f'<section><h2>{label}</h2>'
        for draft in members:
            body += _render_dashboard_row(base_url, session, draft)
        body += '</section>'
    if shared:
        body += f'<section><h2>Shared with me <small>{len(shared)}</small></h2>'
        for draft in shared:
            body += _render_shared_row(draft)
        body += '</section>'
    return web_page("My drafts", body, base_url, session)


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
    body += (f'<p>Workspace <span class="badge ws">{esc(sharing["workspace"])}</span> · '
             f'Visibility <span class="badge scope">{esc(sharing["visibility"])}</span></p>')
    body += f'<form method="post" action="{action_base}" class="inline">{csrf}<div><label for="visibility">Who can read</label><select id="visibility" name="visibility">'
    for value in sharing["allowedVisibilities"]:
        selected = " selected" if value == sharing["visibility"] else ""
        body += f'<option value="{esc(value)}"{selected}>{esc(VISIBILITY_LABELS[value])}</option>'
    body += '</select></div><button class="secondary">Save visibility</button></form>'
    body += (f'<dl><dt>Share URL</dt><dd>{external_link(sharing["shareUrl"], sharing["shareUrl"])}</dd>'
             f'<dt>Direct URL</dt><dd>{external_link(sharing["publicUrl"], sharing["publicUrl"])}</dd></dl>')
    body += '<h3>People and groups</h3>'
    if sharing["grants"]:
        body += '<div class="table-wrap"><table><thead><tr><th>Type</th><th>Who</th><th>Role</th><th>Action</th></tr></thead><tbody>'
        for grant in sharing["grants"]:
            body += (f'<tr><td data-label="Type">{esc(grant["type"])}</td><td data-label="Who">{esc(grant["value"])}</td>'
                     f'<td data-label="Role">{esc(grant["role"])}</td><td data-label="Action">'
                     f'<form method="post" action="{action_base}/revoke">{csrf}'
                     f'<input type="hidden" name="type" value="{esc(grant["type"])}">'
                     f'<input type="hidden" name="value" value="{esc(grant["value"])}">'
                     f'<button class="secondary">Revoke</button></form></td></tr>')
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
                    result: str | None = None) -> str:
    base = esc(base_url.rstrip("/"))
    if result is not None:
        message = {
            "approved": ("CLI approved", "Return to your terminal. The CLI finishes sign-in."),
            "denied": ("CLI denied", "The CLI does not get a key."),
        }.get(result, ("Code not found", "The code is wrong, used, or expired. Start again in the terminal."))
        body = f'<div class="narrow"><h1>{esc(message[0])}</h1><p>{esc(message[1])}</p>'
        body += f'<p><a href="{base}/cli/auth">CLI setup</a></p></div>'
        return web_page(message[0], body, base_url, session)
    body = '<div class="narrow"><h1>Connect a CLI</h1>'
    if info is None:
        if code:
            body += '<p class="badge">Tailplan cannot find that code. It can be wrong or expired.</p>'
        body += f'<form method="get" action="{base}/cli/activate"><label for="code">Code from your terminal</label>'
        body += f'<input id="code" name="code" required maxlength="16" autocomplete="off" value="{esc(code or "")}">'
        body += '<p><button>Continue</button></p></form></div>'
        return web_page("Connect a CLI", body, base_url, session)
    body += (f'<p>A CLI asks for an API key on <strong>{esc(session["accountName"])}</strong>.</p>'
             f'<dl><dt>Code</dt><dd><code>{esc(info["userCode"])}</code></dd>'
             f'<dt>Client</dt><dd>{esc(info.get("clientName") or "tailplan CLI")}</dd>'
             f'<dt>Expires</dt><dd>{esc(info["expiresAt"][:16].replace("T", " "))} UTC</dd></dl>'
             '<p>Approve only when this code matches your terminal.</p><div class="actions">')
    for decision, label, css in (("approve", "Approve", ""), ("deny", "Deny", "secondary")):
        body += (f'<form method="post" action="{base}/cli/activate">{csrf_field(session["csrf"])}'
                 f'<input type="hidden" name="code" value="{esc(info["userCode"])}">'
                 f'<input type="hidden" name="decision" value="{decision}">'
                 f'<button class="{css}">{label}</button></form>')
    body += '</div></div>'
    return web_page("Connect a CLI", body, base_url, session)


def render_detail(base_url: str, session: dict, detail: dict, *, sharing: dict | None = None) -> str:
    draft = detail["draft"]
    body = f'<h1>{esc(draft["title"])}</h1><p>{esc(draft["description"])}</p>'
    body += f'<p>{external_link(draft["shareUrl"], "Open latest draft")} · {external_link(draft["rawUrl"], "Raw HTML")}</p>'
    if draft["disabled"]:
        body += f'<p class="badge">Disabled: {esc(draft["disabledReason"])}</p>'
    action_base = esc(base_url.rstrip("/")) + "/dashboard/drafts/" + draft["draftId"]
    body += '<div class="actions">'
    for action, label in (("enable", "Enable draft") if draft["disabled"] else ("disable", "Disable draft"),):
        body += f'<form method="post" action="{action_base}/{action}">{csrf_field(session["csrf"])}<button class="secondary">{label}</button></form>'
    body += '</div>'
    if sharing is not None:
        body += render_sharing(base_url, session, sharing)
    body += '<h2>Version history</h2><div class="table-wrap"><table><thead><tr><th>Version</th><th>Commit</th><th>Ref</th><th>Published</th><th>Source</th></tr></thead><tbody>'
    version_base = draft.get("versionBaseUrl") or f'{base_url.rstrip("/")}/d/{draft["draftId"]}'
    for version in detail["versions"]:
        number = version["versionNumber"]
        url = version_base + f"/v/{number}"
        dirty = ' <span class="badge">dirty</span>' if version.get("gitDirty") else ""
        body += f'<tr><td data-label="Version">{external_link(url, "v" + str(number))}<br>{external_link(url + "/raw", "Raw HTML")}</td>'
        body += f'<td data-label="Commit">{esc(version.get("gitCommitSubject"))}{dirty}</td><td data-label="Ref">{esc(version.get("gitBranch"))}<br><code>{esc((version.get("gitCommitSha") or "")[:12])}</code></td>'
        body += f'<td data-label="Published">{esc(version["createdAt"][:16].replace("T", " "))} UTC</td><td data-label="Source">{version["fileSize"]} bytes'
        if version.get("ciRunUrl"):
            body += "<br>" + external_link(version["ciRunUrl"], "CI run")
        body += '<details><summary>Audit</summary><dl>'
        for field in ("fileSha256", "requestId", "apiKeyId", "sourceIp", "cliVersion", "ciActor", "hasInlineScript", "externalImageHosts"):
            if version.get(field) is not None:
                body += f'<dt>{esc(field)}</dt><dd>{esc(version[field])}</dd>'
        body += '</dl></details></td></tr>'
    body += '</tbody></table></div><details><summary>Delete this draft</summary><p>Deletion removes this draft from the list and disables every version URL.</p>'
    body += f'<form method="post" action="{action_base}/delete">{csrf_field(session["csrf"])}<button>Delete draft</button></form></details>'
    return web_page(draft["title"], body, base_url, session)


def render_keys(base_url: str, session: dict, keys: list[dict]) -> str:
    base = esc(base_url.rstrip("/")) + "/cli/auth/keys"
    body = '<h1>Connect your CLI</h1><p>Run <code>tailplan auth login</code>, then paste a new key into the terminal.</p>'
    body += f'<form method="post" action="{base}">{csrf_field(session["csrf"])}<label for="name">Key name</label>'
    body += '<input id="name" name="name" maxlength="255" placeholder="Laptop agent" required><p><button>Generate API key</button></p></form>'
    body += '<h2>Active keys</h2><div class="table-wrap"><table><thead><tr><th>Name</th><th>Created</th><th>Last used</th><th>Action</th></tr></thead><tbody>'
    for key in keys:
        body += f'<tr><td data-label="Name">{esc(key["name"])}</td><td data-label="Created">{esc(key["createdAt"][:16])}</td><td data-label="Last used">{esc((key["lastUsedAt"] or "Never")[:16])}</td>'
        body += f'<td data-label="Action"><form method="post" action="{base}/{key["id"]}/revoke">{csrf_field(session["csrf"])}<button class="secondary">Revoke</button></form></td></tr>'
    body += '</tbody></table></div>'
    return web_page("CLI setup", body, base_url, session)


def render_key(base_url: str, session: dict, result: dict) -> str:
    body = '<h1>Your new API key</h1><p>Copy this key now. Tailplan shows each key once.</p>'
    body += f'<label for="key">{esc(result["apiKey"]["name"])}</label><input id="key" readonly value="{esc(result["token"])}">'
    body += '<p>Paste the key into <code>tailplan auth login</code>.</p>'
    body += f'<p><a href="{esc(base_url.rstrip("/"))}/cli/auth">Back to CLI setup</a></p>'
    return web_page("New API key", body, base_url, session)
