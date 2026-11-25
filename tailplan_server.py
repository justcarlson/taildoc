#!/usr/bin/env python3
"""Tailplan: tailnet-only static HTML draft publisher."""
from __future__ import annotations

import argparse
import tailplan_themes
import base64
import hashlib
import hmac
import html
import http.client
import ipaddress
import json
import os
import re
import secrets
import socket
import sqlite3
import ssl
import stat
import string
import struct
import sys
import tempfile
import textwrap
import threading
import time
import unicodedata
import uuid
import zlib
from collections import OrderedDict, deque
from contextlib import contextmanager
from datetime import UTC, datetime
from functools import lru_cache
from html.parser import HTMLParser
from http import HTTPStatus
from http.cookies import CookieError, SimpleCookie
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, quote, unquote, urlencode, urlparse

from tailplan_web import assets as web_assets
from tailplan_web.render import (  # noqa: F401
    VISIBILITY_LABELS,
    WEB_CSS,
    _render_dashboard_row,
    _render_shared_row,
    csrf_field,
    esc,
    external_link,
    render_activate,
    render_dashboard,
    render_detail,
    render_key,
    render_keys,
    render_login,
    render_sharing,
    sharing_badges,
    web_page,
)

VIEWPORT_RE = re.compile(r"<meta\b[^>]*\bname\s*=\s*['\"]?viewport\b", re.IGNORECASE)
TITLE_RE = re.compile(
    r"<\s*title\b[^>]*>(.*?)<\s*/\s*title\s*>", re.IGNORECASE | re.DOTALL
)
MAX_HTML_BYTES = 512 * 1024
MAX_PAGES_PER_UPLOAD = 50
MAX_DEPTH = 512
MAX_PAGE_PATH_LENGTH = 200
MAX_PAGE_PATH_SEGMENTS = 6
MAX_REQUEST_BYTES = MAX_HTML_BYTES * 6 + 64 * 1024
MAX_VERSION_NUMBER = 999_999_999
DEFAULT_MAX_HANDLERS = 32
DEFAULT_VIEWER_404_LIMIT = 30
DEFAULT_READ_TIMEOUT = 10.0
DRAFT_ID_ALPHABET = string.ascii_lowercase + string.digits
DRAFT_ID_RE = re.compile(r"[a-z0-9]{6,32}")
GENERATED_DRAFT_ID_RE = re.compile(r"[a-z0-9]{12}")
SLUG_RE = re.compile(r"[a-z0-9][a-z0-9-]{1,38}[a-z0-9]")
DRAFT_REF_RE = re.compile(r"[a-z0-9][a-z0-9-]{1,39}")
PAGE_SEGMENT_RE = re.compile(r"[A-Za-z0-9_-][A-Za-z0-9._-]*")
SHA256_RE = re.compile(r"[a-f0-9]{64}")
IDEMPOTENCY_KEY_RE = re.compile(r"[A-Za-z0-9._~-]{1,128}")
SAFE_QUERY_RE = re.compile(r"[A-Za-z0-9._~!$&'()*+,;=:@/?%\[\]-]*")
MAX_IDEMPOTENCY_RECEIPTS = 4096

RESERVED_SLUGS = {
    "admin", "api", "app", "assets", "auth", "blog", "cdn", "cli", "d",
    "dashboard", "docs", "files", "help", "login", "mail", "postplan", "raw",
    "signin", "static", "status", "support", "taildoc", "tailplan", "www",
}
RESERVED_PAGE_ROOTS = {
    "v", "raw", "content", "share", "preview.png", "api", "healthz",
    "readyz", "favicon.ico", "favicon.png", "favicon.svg",
}

EMBED_FIX = "Link to the page instead of embedding it."
BLOCKED_TAGS_WITH_FIX = {
    "applet": EMBED_FIX,
    "base": "Remove the base tag.",
    "embed": EMBED_FIX,
    "form": "Forms cannot submit on Tailplan. Use inputs without a form wrapper.",
    "frame": EMBED_FIX,
    "iframe": EMBED_FIX,
    "noembed": "Remove obsolete noembed tags.",
    "noframes": "Remove obsolete noframes tags.",
    "object": EMBED_FIX,
    "plaintext": "Remove obsolete plaintext tags.",
    "xmp": "Use a pre and code block instead of xmp.",
}
BLOCKED_TAGS = set(BLOCKED_TAGS_WITH_FIX)
EXECUTABLE_SCRIPT_TYPES = {"", "text/javascript", "application/javascript", "module"}
DATA_SCRIPT_TYPES = {
    "application/json",
    "application/ld+json",
    "text/plain",
    "text/template",
    "text/html",
    "text/markdown",
    "text/csv",
}
ALLOWED_LINK_RELS = {
    "stylesheet",
    "alternate",
    "icon",
    "shortcut",
    "apple-touch-icon",
    "preconnect",
    "dns-prefetch",
}
EVENT_HANDLER_ATTRS = {
    f"on{event}"
    for event in (
        "abort afterprint animationcancel animationend animationiteration animationstart "
        "auxclick beforecopy beforecut beforeinput beforematch beforepaste beforeprint "
        "beforetoggle beforeunload beforexrselect begin blur cancel canplay "
        "canplaythrough change click close command contentvisibilityautostatechange "
        "contextlost contextmenu contextrestored copy cuechange cut dblclick devicemotion "
        "deviceorientation deviceorientationabsolute drag dragend dragenter dragexit "
        "dragleave dragover dragstart drop durationchange emptied encrypted end ended "
        "error focus focusin focusout formdata freeze fullscreenchange fullscreenerror "
        "gesturechange gestureend gesturestart gotpointercapture hashchange input "
        "invalid keydown keypress keyup languagechange load loadeddata loadedmetadata "
        "loadstart lostpointercapture message messageerror mousedown mouseenter "
        "mouseleave mousemove mouseout mouseover mouseup mousewheel offline online "
        "orientationchange pagehide pagereveal pageshow pageswap paste pause play "
        "playing pointercancel pointerdown pointerenter pointerleave pointermove "
        "pointerout pointerover pointerrawupdate pointerup popstate prerenderingchange "
        "progress ratechange readystatechange rejectionhandled repeat reset resize "
        "resume scroll scrollend scrollsnapchange scrollsnapchanging search "
        "securitypolicyviolation seeked seeking select selectionchange selectstart "
        "slotchange stalled storage submit suspend timeupdate toggle touchcancel "
        "touchend touchmove touchstart transitioncancel transitionend transitionrun "
        "transitionstart unhandledrejection unload visibilitychange volumechange waiting "
        "waitingforkey webkitanimationend webkitanimationiteration webkitanimationstart "
        "webkitfullscreenchange webkitfullscreenerror webkittransitionend wheel zoom"
    ).split()
}
EMBED_SRC_TAGS = {"img", "video", "audio", "source", "track"}
VOID_ELEMENTS = {
    "area", "base", "br", "col", "embed", "hr", "img", "input",
    "link", "meta", "param", "source", "track", "wbr",
}
SANDBOX_APIS = (
    (
        "sandbox-storage",
        re.compile(r"\b(?:localStorage|sessionStorage|indexedDB)\b"),
        "Scripts use browser storage, which throws in the Tailplan sandbox.",
        "Keep state in memory or in location.hash.",
    ),
    (
        "sandbox-cookie",
        re.compile(r"\bdocument\.cookie\b"),
        "Scripts use document.cookie, which throws in the Tailplan sandbox.",
        "Keep state in memory or in location.hash.",
    ),
    (
        "sandbox-network",
        re.compile(r"\bfetch\s*\(|\bXMLHttpRequest\b|\bWebSocket\b|\bEventSource\b"),
        "Scripts make network requests, which the Tailplan sandbox blocks.",
        'Inline the data in the page, for example in a <script type="application/json"> block.',
    ),
    (
        "sandbox-dialog",
        re.compile(r"\b(?:alert|confirm|prompt)\s*\("),
        "Scripts use alert/confirm/prompt, which do nothing in the Tailplan sandbox.",
        "Show messages in the page instead.",
    ),
    (
        "sandbox-worker",
        re.compile(r"\bnew\s+(?:Shared)?Worker\b"),
        "Scripts start workers, which the Tailplan sandbox blocks.",
        "Run the code on the main thread.",
    ),
)
SANDBOX_LIMITS = [
    "script src (external scripts)",
    "fetch, XHR, WebSocket, and EventSource",
    "localStorage, sessionStorage, indexedDB, and cookies",
    "alert, confirm, and prompt",
    "workers",
    "forms",
    "iframes, embeds, and objects",
    "meta refresh",
]
URL_ATTRIBUTES = {
    "action",
    "background",
    "cite",
    "data",
    "formaction",
    "href",
    "poster",
    "src",
    "xlink:href",
}
SMIL_ANIMATION_TAGS = {"animate", "animatemotion", "animatetransform", "set"}
FOREIGN_ROOT_TAGS = {"svg", "math"}
SVG_INTEGRATION_TAGS = {"desc", "foreignobject", "title"}
MATH_TEXT_INTEGRATION_TAGS = {"mi", "mn", "mo", "ms", "mtext"}
MATH_TEXT_FOREIGN_EXCEPTIONS = {"malignmark", "mglyph"}
FOREIGN_MODES = {"svg", "math"}
INTEGRATION_MODES = {"svg_html", "math_html", "math_text"}
HTML_SCOPE_BARRIERS = {
    "applet", "caption", "html", "marquee", "object", "select",
    "table", "td", "template", "th",
}
HEADING_TAGS = {"h1", "h2", "h3", "h4", "h5", "h6"}
HTML_SCOPED_CLOSE_TAGS = HEADING_TAGS | {
    "address", "article", "aside", "blockquote", "button", "caption", "center",
    "colgroup", "dd", "details", "dialog", "dir", "div", "dl", "dt", "fieldset",
    "figcaption", "figure", "footer", "form", "header", "hgroup", "listing",
    "main", "menu", "nav", "ol", "pre", "search", "section", "select",
    "summary", "table", "tbody", "td", "tfoot", "th", "thead", "tr", "ul",
}
HTML_SPECIAL_TAGS = HTML_SCOPE_BARRIERS | HTML_SCOPED_CLOSE_TAGS | {
    "area", "base", "basefont", "bgsound", "body", "br", "col", "embed",
    "frame", "frameset", "head", "hr", "iframe", "img", "input", "keygen",
    "li", "link", "meta", "noembed", "noframes", "noscript", "p", "param",
    "plaintext", "script", "source", "style", "textarea", "title", "track",
    "wbr", "xmp",
}
SELECT_CLOSING_TAGS = {"input", "keygen", "select", "textarea"}
SELECT_INNER_TAGS = {"hr", "optgroup", "option", "script", "template"}
TABLE_STRUCTURE_TAGS = {"caption", "table", "tbody", "td", "tfoot", "th", "thead", "tr"}
TABLE_CELL_CLOSERS = {"caption", "col", "colgroup", "tbody", "td", "tfoot", "th", "thead", "tr"}
TABLE_ROW_GROUPS = {"tbody", "tfoot", "thead"}
SELECT_RE = re.compile(r"<select\b", re.IGNORECASE)
DUAL_MODE_RE = re.compile(r"<(?:svg|math|select|noscript)\b", re.IGNORECASE)
RAWTEXT_CLOSE_IN_COMMENT_RE = re.compile(r"</(?:style|title|textarea|noscript)\b[^>]*>", re.IGNORECASE)
FOREIGN_BREAKOUT_TAGS = {
    "b", "big", "blockquote", "body", "br", "center", "code", "dd", "div",
    "dl", "dt", "em", "embed", "h1", "h2", "h3", "h4", "h5", "h6", "head",
    "hr", "i", "img", "li", "listing", "menu", "meta", "nobr", "ol", "p",
    "pre", "ruby", "s", "small", "span", "strong", "strike", "sub", "sup",
    "table", "tt", "u", "ul", "var",
}
NOSCRIPT_RE = re.compile(r"<noscript\b", re.IGNORECASE)
META_TAG_RAW_RE = re.compile(r"<meta\b[^>]*>", re.IGNORECASE | re.DOTALL)
META_REFRESH_ATTR_RE = re.compile(
    r"""(?:^|[\s/])http-equiv\s*=\s*(?:"([^"]*)"|'([^']*)'|([^\s/>]+))""",
    re.IGNORECASE | re.DOTALL,
)
SECURITY_SENSITIVE_ATTRIBUTES = URL_ATTRIBUTES | {
    "http-equiv", "imagesrcset", "rel", "srcdoc", "srcset", "style", "type",
}
BLOCKED_URL_SCHEMES = {"file", "javascript", "vbscript"}
CSS_URL_RE = re.compile(
    r"""url\s*\(\s*(?:"([^"]*)(?:"|$)|'([^']*)(?:'|$)|([^)"']+))""",
    re.IGNORECASE | re.DOTALL,
)
CSS_IMPORT_RE = re.compile(
    r"""@import\s*(?:"([^"]*)(?:"|$)|'([^']*)(?:'|$))""",
    re.IGNORECASE | re.DOTALL,
)
STORE_LOCK = threading.RLock()
BUILD_ID = web_assets.build_id(__file__)

CSS = """
*{box-sizing:border-box}html,body{width:100%;max-width:100%;margin:0;background:#fff;color:#111827;font-family:ui-sans-serif,system-ui,-apple-system,BlinkMacSystemFont,"Segoe UI",sans-serif}.home{width:min(100% - 32px,760px);margin:48px auto;padding:0;line-height:1.55}.home h1{margin:0 0 12px;font-size:clamp(2rem,8vw,40px);line-height:1.1}.home p{color:#374151;font-size:17px;overflow-wrap:anywhere}.home pre{max-width:100%;overflow-x:auto;padding:14px;border:1px solid #d1d5db;background:#fff;border-radius:6px}@media(max-width:640px){.home{width:min(100% - 24px,760px);margin:32px auto}}
""".strip()


# Copyright: Copyright (c) 2003 by Bitstream, Inc. All Rights Reserved.
#  Bitstream Vera is a trademark of Bitstream, Inc.
#  DejaVu changes are in public domain.
# License: bitstream-vera
#  Permission is hereby granted, free of charge, to any person obtaining a copy
#  of the fonts accompanying this license ("Fonts") and associated
#  documentation files (the "Font Software"), to reproduce and distribute the
#  Font Software, including without limitation the rights to use, copy, merge,
#  publish, distribute, and/or sell copies of the Font Software, and to permit
#  persons to whom the Font Software is furnished to do so, subject to the
#  following conditions:
#  .
#  The above copyright and trademark notices and this permission notice shall
#  be included in all copies of one or more of the Font Software typefaces.
#  .
#  The Font Software may be modified, altered, or added to, and in particular
#  the designs of glyphs or characters in the Fonts may be modified and
#  additional glyphs or characters may be added to the Fonts, only if the fonts
#  are renamed to names not containing either the words "Bitstream" or the word
#  "Vera".
#  .
#  This License becomes null and void to the extent applicable to Fonts or Font
#  Software that has been modified and is distributed under the "Bitstream
#  Vera" names.
#  .
#  The Font Software may be sold as part of a larger software package but no
#  copy of one or more of the Font Software typefaces may be sold by itself.
#  .
#  THE FONT SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS
#  OR IMPLIED, INCLUDING BUT NOT LIMITED TO ANY WARRANTIES OF MERCHANTABILITY,
#  FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT OF COPYRIGHT, PATENT,
#  TRADEMARK, OR OTHER RIGHT. IN NO EVENT SHALL BITSTREAM OR THE GNOME
#  FOUNDATION BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER LIABILITY, INCLUDING
#  ANY GENERAL, SPECIAL, INDIRECT, INCIDENTAL, OR CONSEQUENTIAL DAMAGES,
#  WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM, OUT OF
#  THE USE OR INABILITY TO USE THE FONT SOFTWARE OR FROM OTHER DEALINGS IN THE
#  FONT SOFTWARE.
#  .
#  Except as contained in this notice, the names of Gnome, the Gnome
#  Foundation, and Bitstream Inc., shall not be used in advertising or
#  otherwise to promote the sale, use or other dealings in this Font Software
#  without prior written authorization from the Gnome Foundation or Bitstream
#  Inc., respectively. For further information, contact: fonts at gnome dot
#  org.
# Generated with scripts/build_preview_font.py; DejaVu license in THIRD_PARTY_NOTICES.
PREVIEW_FONT = zlib.decompress(base64.b85decode(
    'c-ripb$}Pe|Nr;i*L`qz9Nk^gAxMe{h>}uLiiCosQYs;(AfSMh0s=}XD%~AQcXu8~+#Ou!{_g$Wo!89n%+8K}{e4'
    'uJ=O21I?+5#S@9gaC#48j<N=iyfN=iyfN=iyfO6nieeO1!{$w^Lflp=MmzYTX*=Q`VP+jQ;~8*YitmAB!((76a3Z'
    'lH!`IBd8M8h78W->9c?C+)Zr8n@Yw3(~m7wjEZ2i~GWsyW-*o%N|}n=l=vJB_$;##nvBsKU%}$SaOp4H*iu?Qc_Y'
    '<Qc_aSlsw{GqrNx~*U=_z4A_|Fs%f8WaviuobsePo)7*y|H{5klRXf<v_}+EeM>SmsL$rhOH0FqLeUjkmI$EP~FV'
    'kE_?USwAKZ<r8r1;U?0F4{zI;fx>>}CACU$l>EyAD3k4#v`$+e6nUiC(UwRT@{H=E`cHY}bw+xDJwiXzo3Y`_y$%'
    'T07Xq_<1X|kLtP(`e+BE@yG2|TlIb(IbGkAI}k0kgKrqMe2Z-5Gc>L(jTJLABW)ifGmPeXY1|msL4@`8!E*BE>X$'
    'Z+!D%b8iQcY*c^db+W!u}P9cjA)>A^IoxAGCLgM8Y-A;#DKO3o9zY6p{u&tq-x#fj&V(=<mbYT-I~RXbS3sO587G'
    'YWMbOwhRQG#0LXa!NbeuN@Vpxi;D-GhGM1DXxP%jIaHgY~>BKgC7{&NpjO|jCRnI#<FF)KDnSB?b5iCH21pp$z0c'
    'gSCZ=>it)8yYFtOx!Ash~N(OgC`zWvL;A8EeFO3DJyFR(99c|ILax~XO`(&Z^#*B9zJa&_V2Pr8jDYl%GiQfdd)q'
    '>m_L2j)ew@#2-FUW1=BOz~oyZ5g1otXLX<f_pv!q`-ig=xkCMCREqYSOQp57StK4fFcFV;Or;V&<l(LR>>tjQd@T'
    'tAL8rCs%d8e^=+K*?ImYn)^t_W<$7LRhaeE#OZ@m^;c8-%Q3f#fjgjc!))5=MV<S=hSSGG>TDYhSwB4q=W{dB_3j'
    'Jja&JJrMvXtj;rt#L@4~5O;cW0j>V{n!Uy8wbvu^NahOt)8s|+w>5^aVRx}DWr*3z?N_a65cAZrlq|L%So?8?0Eg'
    '6}MOV&txSaGvw~Q5bB`>;K$aJx1I@em_|RO(d?{^rvIcBjd*B>Yy*q#^=hZv<cm~o*op}1l>G$%q>zccJMH9-|1Y'
    'WLUO8l@3Z_Q*EU1vel&5;<~jBC@?oxiy!Je(=Z-YT(lswJuAL&p%}|86AKi_OdEi+VmBui~tMU0cGqtcV`;HatFl'
    'Fm`*NRx?{e$0k3gy3ScG<s=T-uq%Z4lsg2)+lm3%*yk3BISd3cmNZ2#zB*|A}#l2qz^a^_<J6%gQTp@mE%M_U2;k'
    'ZlX^9=9|`(V>0<Vm~3gXKH2!*m~4TyF~5e|GBK}f+VG=IP*B?&u0L-uFGgFEzU~?=<hp5X^uN^gQ8RLdZv~5Uy(u'
    '5)gBfi{<qC^?<oskeI@;sfwqkLQX)a!|T4F`tt3+9T?~ksZ-jrMl{F%jd)P^6ffpypw>#?S{wy$utbzor2%dS6f`'
    '<e*ygoHYgP~H)jrW#jmk-j3DWrxjK!>o9HqUk=vFtULmOZC~>Hd`d@ti5#yQMb{T=K4q*rzHQ>j2P?qc3X8VA>rE'
    '3-L!GKSDW?Pn(CqVvRSw9$}_Y<@!ii$SwlGno^nA+E1I}lLY^&t(`Lbc^bWB;RrzV!^ykjRlGadYzhm0>Eo*G;Bj'
    'VodXKnHPKu>)O#iQL1m!2eC`|TZKGqm&<n%B&&3B^%SvrDK#Bj15}zP5q$LnHisBQuE`G~W}C<o5LlVoisQGm9qU'
    'iW+`xy$n+WdY^ewZbw}o-s=<+*h*`+kC~OJxx73q=xX5F^$cNg+ET*X+JUx8zuRh{sHUK{f5GxWJ>&f3B0BoXbwY'
    'ouj^qM5TIu2fEC#Z8`}K_F_f8}GHf;lZ4~x^*L?T_g4I!sp2Ol!Kb#0a4R=c2p=I331#;_lfMwzDj*3Y6jeqzL|o'
    'rgX%G#4-Hr=SH)*Q0uJ-(;R0mR*iZys`S-0QvHll9G}lMsTl>+i)Q=F+JhInXO;<s%0@YiTLWVu>-%s#JWVA2ZBs'
    ')FumS%Lu59<=KOApalKVBZi65fk3LJCStryzAaB`P-KOt6Mqz<)j@P(1q7cH4@^4r;;<ocSw`84r<YB+6#+ht^h9'
    'T|{pUa)fS_fqa_IvJyaJkay2>h0R!dXz&O}O0qz}@A1bKp|q{(onExhD(_rI7`e|E8%yu}34>%uB4gC5%~XsTP{~'
    '5(9f1K3)NPaF?`ktnNM@>w1LHCP7MjWi0#?&2zi9b`WEDa64p8Cyv05SXJj|6^OO;^ZF-Cod>0o5O~@-oGYU*emi'
    'G7=mTx?(I39qX?a#%`}6m#Kl|y$XY`i64W7L6iqcm5^QWv27U;#x^sCPY7Eedqm2ui%qtLLLys4exn};gL@xyvdF'
    'T`uBKO-)woBj&|(ZHM>`75E3rn}=>W<H3H@Z_hNiF297p}j$M9=?<F4A@E6V!%XLhp!D>fMFM_q3KJa@HDm-G3A='
    'A&%vKUNo;F)`mRCY5i}hChWLsDy^06;7k_6xak|^6Z5K-B+nb?f0p(LZmtGOgYPa*jLui5;e1s3e{4!`G<I@Ll%}'
    ';W{oM=#-`HA~BdmWhn5DP{TlgXE5{G75`Weef>RdOM#@#kBw#-+zy-#@iQkQ|FhNl8huCF=JpIm!K#oRpN5l$4Z|'
    'l+-_|nL;baN-b?6CnY~0^S+>+?rWRV>5GktIr&(DB;Fb?mhNbqQG}1xQ1MzQPiXz~MX}SJu{j-Diyp|w%Ia%nDPe'
    'pLnf};V+<K$ErtW;M16m`sF+yIib36H%A6m`+gwKJ$!%KW_K3Y)r;&W(8{dYbVj20y4^0}AM&+E(QV8y>4pM$Lzn'
    '<fgIYd(AqZG$|rX?tkn;*t%AHtX!7;G$LRKlqp@x{N(G=R;dp2W>dCwtUKlLqSb1+i+-0B1REw^{SuX{VguoUS8&'
    'N4OFq{tU;(fFHppukHa?HT(r~W%jaG<ZlT!s2cHS{lKXAAcF1|fCQh_B+GOZybHcAwG9rq#O6ygfW*4CPH~oYk+H'
    'iU#7HsHdrEOwerBYS@g}>OH@CUsLi`j7blMMS`)*2)BK3BZYvwU$_;|;B&Nhx3l&am5mmXebCS1F&i?VVWE4xFwJ'
    'Iqf2J<j{vNz3fgn!Mtm2b;2aEw<?MPlTp!zE2a;ecG#V8jbK&<^#0vt!@1v8$pBrpRb%UTR~Eg0^V)!y#3lmDB;+'
    'mYCN|wtYuj8gveNk6=8KVK!&w^+Z1fB9xq7Nt=8Wuo(<Q9CVg_X{KS<Zx(k3Owx9E%W3cby3Yx-Tm3vR87r5<UDx'
    'DkA=KH|pnxfc=lrQl$D9G?R_q2YY4jo|h6X1W8>0-OI@d=89g@`#NmlO1A{N1Mr64z`E2^_<$KxqOVcf?}6pv;N|'
    'lpwh9;7<x&tmZ$32Rh#|_*+`A#V-=ANm(4>tnKHIxUoE<6)%jd2F)LYEkg%ENmCPjLHrjSU887g$h-9&4N&VdsYc'
    'ubN(8mYwicJOW#v8)p7bz*J|GGw7J9PZ}xk3l(u(`0ApOV~CRsFRX&3S(mtH$TRSUKlBCUtstBj?usx@p%(U(Jgi'
    '=tXd~bQ7Tzc(DheuuSUZR){OS5|t(6fIJ>O!H;>5%WpJtN(!2YEHrFN`3{X{S~RaNW3HNzi3t?7hq>pIYj&zJ=#r'
    '(YN|9yQ<MVoF>+w~Q9|`U)D=z0O<M*Cq`fUV{i>Iz*7xnu08T^P@FTWT(somecg+AEia+kmNSt1&s%*VsZAaKJt+'
    'XUUWyi3p}CE|z(`uh39_dSSqP-Tzq9Dt+alz0Bylqtcc_SgEVd7kuxs;(+Rw^OenF={-yFk9HV-H&<}pK~cGDJdx'
    '_DJiM{iu6n-$&!2SIQxtfy`M`?win6$Wll;;N=iyfN=iyf3R$XVse<JG0=EJ}o8{nPZjeuA3iosn+wCde-ER;X^N'
    'l0qsPu>*C(AQV%sB-a!`-uS0~)P!@7c7zeR@<oPd*l)?{{~xIh}5adDg~&5!vdsl8?WXl$4Z|)W3IVid9f@e>*27'
    'B_$;#B_$;#B_;J-2&dW@Ai4h#CnY5%CG{tyhnw1G$LU$Kn%iJGwa)sST^uew;Z`>rEKsupziqP_tbC}QZm<pJrMZ'
    'U31ln+K3k3~Q+_V^lNp@i#^$50e?CN;G=A~{IEaWAz*u==QzN!tTN2|1rH?~MSU1J+8ueLFN!tQiag)$o|*)%hR>'
    'o%zum61Y0coiSb=;D!0kX96MX_O7-&~j%d*hEBD-V_UVtsK_kh8DEJ8WKqXdD^0J<!vxM;owHQ)9Gm@>)T+3(uHD'
    '?p@8D8ZLlyctiUBZ3yv{jL0tW{;0BT2He7$90A7m64b3(u#D;rIJKbcvO40I#r`W{hQkn=x9o(hm*DGa%RTGQor>'
    'qi+d-eb6rE=Epr7}tEr4sb#UMhp^UMgDfy%@Ws=zsG|rNuw<QjwC9lA`7L>Q{U2C8R~4Th*Tv{?v2O9U|}RS}RN1'
    'lBZ31YsWKR|Fvj8(O((edYKr8-2**(@;%G+G^Jt%k7s+N#^*L-c@iyLSLYkKFL1WpJ_Q47`~rt5hG2ydre1xZMMI'
    'hZpdoNB#M?*jRk!`9;2g<83{c3l-WdKR8R_XJ?QwfUrHy$D!av4Aw&pN$`a+1;JGcz8Mia>=kiD?F9+!bBbcUaKP'
    '^zlaG3N)r1xJrOfQrcX`9nIR{B0sLH|P{9>;pxRi~AL7xRoV{nC1gNg^aWH8<n>?0=RNnzkhmU@*l5|7Y75q{J<4'
    'c++AOn(H!UN#*MlNaD`D~@2faRin<bUB?vp984wh9F2wv|oo^-t2d2-5e_v6hJ$x_xz8_fE>%P}@9j~*l*HC`}Uq'
    '_`Je1)vPX;7ufV)p~z-}cm~+t8q)y^zUx9+)021u_a5+g~Q#{ze-Tm|M4Jcs_13o)02fNg=41d{8!<@x0*+#{JlJ'
    '#`BBj_o{jXl{8}Z2au0PZKn4xdr`r8!&Doh>qQkZb(_X%^%UIy^(qih4Kl;4sm9>z`I0R9)9=`eOJ*gQCslsunH='
    'q*Q2q%6TgDni)8HEBG^4cXRg*r^VtVU2<Sy~PT+fLf@7Z+5zR1M%*bBc5EG$POQc_Y<)B<pgev8j%vfgWWN$-Uob'
    'C0DNy62LdHR9)sOWZ%bW3iK5?wu3!-mTzTuRp=%up}or$w}@X;R<rYj17~V4JRcfCG}h@<h7A2Pu_}4eRTQIqW3C'
    'WY*ZH+y4yIdxHdGLo}hHxk;&Lw=;vlXYIkXOgczq@ju7M2gC1g>F&P_&dlEu(x8`&zAM6oQzRwYh7@tlqX$g_owi'
    'm9;^#(3xL^i{fLGn5EF;UuiVm5Qt<FDphb9|_Z<@syG*b-r>sw$St8`7dZ<u^u19c`0iYmX`v;ssWzGfQx}O^DmT'
    '#b7Zzvoa4WhLSU?^LSVg%FnId;@vcljj+;MmxH;%BDi7MpAufO{)&f<z>CE^>m~fCtmE0FbU-Q9)t$U{(hM@e9N?'
    'Lx)P>#2!@(S^3fzX{**I8fNEvgA7p15$EM}kO#YV~l8x$9K`P#xDx6I|-94z=4^tbsqnBP8x-Q?wx_Sym)m_<04J'
    '8a`da&u>Q<Bqaxh;*mVH76w{CG~tOxZa@I8_z{PO@Dg(%%<_J!kJT)ma{IHW)^9`zD7?~eq;8ydrFFNYD#Z0j`HT'
    'S<T4wGaq1OMG0qq?(8)z_o%Bxq!r?xi;V*r119MwQZs9L|3~zGpslX-iO^LypMlH_evY|wU>P!6Fy&G{2_#Bwi*d'
    '(1tse4s!z)Hm{hBnh;X{0KWSKToPjb~FV6B>JDuRa6@`05ZA@&|Qy7rTjY=@Z$rbR(Cs-fEIUa>qFqkB0Nzm|sPR'
    '%hKL5Vw{@NM~vgS&w53?_#w7P$>3*m?+R0b%Y3h#nqaWghTmX@LMBN*=Zj*J#<}ysb%Ze;Pw)sg;&Y!O?oB>79dR'
    '~pO$QP8Iv1;wNj<<3J6luN9tw|0I?VWTW=)8{Z0Kb;8@|Jb6@qgvFKwCjZmr_E{M|#!zP&MnwaTB9Hylrl5aS5n2'
    'Of9QM~J&YuBupbdkkh@R@~F~Ox;1Lerq06)Y12uiwp(c7&&j_>ATU%8L_ucZ=Kw}h#ZkfNl8grY9Mr~lXzJEcrk9'
    'BD#W!@g}9JNF>byp#J!X$#CaL*kuA9~st{K)MTm3Lw*ju%a6R?q%x-)xr#?w&+k(&Ox!cz&d`^$w9+#ib1>A%oE|'
    '=2>XoubTTouiQInC*w#Nj-SYoC0f@Hu_M=$ap&E0jRG*jsa(489bWTqk|gE1b_|*PR-=D|}A(7uu=tx%ztYwc>ov'
    '=aN?VvBKxZX=P_TZ8*)uC-X%`h_lvm$#FSaErj}K$^EZ5DJdx_DU*_|-fi6^_b0f~g7qvpHaF+ppHAOTOT2erc84'
    'IrcAETMF~RnnGTuY5-R}z(d_K;G3O+~sJq6p<Btb5%l$pD|^n+K6hI;wtuK&qp0$gF>j(tMfsX02~;16lJc=xB^E'
    'shH?sWLFJ{*`~nr!Zn2uvAvi08n`>n6v|;XO!V{uyJ4$AnJjbA!AY4LGt2qut9j8i{*S`Kl{j1Hn8tf-zL6eSu3O'
    'YTl1Uvc_K{NCaG%rk*|6-EE4A9k*#p^&&YN~23MM7JvWNaE5^OA2ysgkA?_y+5iY|VIEBp}r$T!9HJN_SWG;}gxd'
    'nUAG`RlYxtq@4k4sODy|isw`%pznN=oWEQ{K*Vc3h81O^!OXV&scfwh@kwyA1v6lZEwIy=p^7a1uINmSZZzS%l`i'
    'er5Lh6t_eb;s&ZhTnQYoC1yeM;=#TJ8;m{3;8p{+5WhOg%}O$H*iE9pp*-Irqm{>EgiYk$tYGx(uJS{HwnwLH$G`'
    'bY(WTl%7QJN=wZ^1eOk*`#O$AL|`lFyaS?w~g)ptj3B|W{ee_A8z$D$xmk&4$4BAU&^_f72?h3PpLe$v<o&KSq0$'
    'X4zAX>)72TZTDgrFENhP`JWbr2d{!%}f#71oy}R&YT({{^iCduwumBCMH?BwNP=`B}~Tc_B!T1KxH-IA#DrXMQq1'
    '{=S{0n8IBW+ostM^I*7rv8;Z86i~v~v=!<Vc(MsURQ7hW~djciXfFlQxDAZFX6hhkRMDlKeyNy4!e4RjY$nr4Vjh'
    'aI+zxNUD`Cwx>52JNl!A08{HeP<n#%UWa`;;*~ZO*V<ssxi454uucW3^Oh2i4K-NXbrcL(T1~*j?sSARE}-Wz4P-'
    '>{oU)-WsoFc;tp!HG7j|me)}>_R#EfQ}_XMYC#*XmAS{5#j`rFh>d3Kx`oYa(>){=IovvOchUEMJo+FhBjLu5FB*'
    '9$Qc_Y<QkwqYjfXZpXWTD>Tqkaojzw5BciGkfL(v&xBY7ffC`+qoD{hu@fFCeFB?5S}$Me&ooTp;5GF6*3$YuyUt'
    '<Q3?Kp2ek<M8)_K~xx*JIvlQFbjO%Z9D_MhT#i0IRTE{{0aAT9l?-4FPGcSihMw;Fw8qzayf|18p$0ux!(~pL3s@'
    '-kL&u3J+yXlu?PtC@D`VYVQ&n77X@ZsGr3q@&aw-0xq<&U1}D;Scv*T6=1D<Z4#w^axmX3b3Vza#SqMAF#e8DK9>'
    '=!?A4dq#ypoF*&16h&8G^{aH@MkiJYbsgDfe^`N<Aa596(4(Nl94_Hr`oy^v2WFsPkLLzGD3Xe%Y|_^I&v1`(^o)'
    'c_bd)ih&is&KR?QNsolD+J~!hYPZEKpotM{VKrMgV|?jA(uj<Ob+dUFeCLA7^bRcsi^zIBeQ$_>Z^*{fk6KRjMQ4'
    'm5T|!Z=g*$jzxhwjoWEQBkba<uEOvcdt4@N+wD81|rq6{TW=)qV5B{O`jXAB@v9uCs+_zQZUBnP?<a`_akh2O*1+'
    'k-Wh@p^3DJ@Q9C?_<M#L)IxtP4zJk+MkLsj%9q%mPN*bIw4*Kdt9=bZxxPYHkVmcL(PATrqV~V1~3E73$44^j^#d'
    '`qm5qWOm}uikNed3Zl+Cwf;O-&BiTGj_P1D8R^K&puqXL+V)bm^`8vW_FwL+O3<)ugpde2@(1hTeCu3NDC75alq+'
    'JjU>l+gP7Hys!pqt-m=FBEL)oDy6xd8MFUxlI@Ow+R4)SMueIoCN$Ib=U(p6?D7Dgjl?G{c=tr%XH#u%_d!=zR@H'
    '<?=GY?V~4rFqI|lM}L*tj#6^K(EbRr&g%;v=;R(L&I|H%Vzm&jd~fZ?EsJdYWil+6bByMC)2*;rW0(&24Ho8!=B0'
    'tabu287jv3%KFonX7Fz3p-$3HYsyINRHY`p7^SbOSLT-w7+zt8DXNDfD&q@<o7<>|BZ*u&(s*sEJ6H?_9FslJZ1m'
    'wG&vJ^k?h#%xvfq&ou(VeK7!LoEb*9ALgasASON#5N&nq+4*0i~&0F%V^sh5ng%T!XBPq7zU^Cx*Vw$c>VzI!2s6'
    'M+K;Hj6HIpAVN4e?^3f9}Vtd9Ly#a~5<DO`&nu<61YoLEShWL)n^ce0<R)n5u^fF&B*6`&Mdcrl7wQLH3b<fi`jX'
    'WQik|aUVaF!Jrwy_DPmVd{xt`{#wcVw)cdtpz922}lJ6kttYVOH&vN^OjI5++$)>2+tcm4VxfVTeB#7jAzehgY6X'
    '1`lIAYR#9<+kpE3U)^s!j);L(IG)2169J_UW3t7Zp--T^Y~bP=8y>kqsm7Q>O(5JvCF3((jI_sGTLk|U%0Z>Td#g'
    '{~PfNPBbyP{>m&Dt2hG@ik!iKvmw1=RSMnA7NpZg4PrMZ|VTDG~$KOLBsP2pliAPKB7M&ajg8M@-!*O_7coEaRCc'
    'fku*t?|sdx|vhe=<mCi4fD)Z`NKo_{{5iP97FfA3vmh66cH}rRYiomSyd6>(&n)Cn!@@WG`t2M&$5hbm80X=+iyH'
    'cPkeal*HQJo<X}WfN=iyf>i?`{fdH<O`}c8D@_Cb-eBPv_q@<*z<akJOay%p@+v)!|&Ij9nHRo|PvFR1}Yd=ugn|'
    ')?2rDHNMby3VACpc5c#Y<_3A~Nv3lwbpTrA_P8^H!#pu;FftI0Hhym6QTDTyqPf2q`HkDXIUZ@{e9&4|%oSl%f7%'
    'N0t84Wx>qn0^UY8ay+j3{5W_QTXLZT43VVRoLi$6CJSs9m+#XXyvPBXJX>#|49;&)HouHZPL47|G2hlV*>d}L+h>'
    'N7er{<tW~@5*sW}kO_P5O`yaPTsMddCV#7L4|&jelhtR%C-3|!O<%lylji(Ar!<4g|RyZ~;)z~{R&rIHYrr3;@H7'
    '2>jVs6RWwc+k*`X49~(-Pw84>wKSJzG<skF)dJodLO1-4Hmr2H0Uq?HI;lWa(Y!3>D^%)6$0?`80P4$&}X-)_PmU'
    'FP3}#9Nwt4IT*@)zybzbAS_|WaI7JD3_n;7$rLxl>32|BdRl6-2B3zaVj9DRH0#Z^^Qc_Y<|2NgMNw0>+CC?itCE'
    'JT^FH%xcQc_Y<{|nV#-%=<<eAc++{ud>LxGe4I>L)m^i=ACXbo_UsZ#KbkV$u)w-2}&zS4QM!auq4(>GZX4uxiS`'
    '(}px^g?bV*hE4eUbO3~1oqbto;g6}TH!-u=>;4s|+%coa-V0%ojt1Ywn#jZ@?BK-StyU!qjyr?j*^wbQKFvM&nCL'
    'jR&J6)B;H`~mw&(MDeR-n&K3Ls((e~F{ak6>eAob^ZW~j&~)U7)^$Q)iH;sYvVM%;HbO;Av`EmTZ^-+P$$iM(6>l'
    'tksNm`eGbzYt4ht=QDo%l`SeF)E+^bCkT|6@lmN^ng(N=lAKn>H_`2{_g(v{Y%v3lJ@=8OB-5w%C~@&l$6vnA@4@'
    'xHe7s?9)IWb^1)Rtsfx3okgu>)eVD`GdURx~W3+u~F=kf5#QJ7#lSSIx%3_>))FH;HompJw&Ve;UJp#(~IS1T2no'
    'F7qF%v!EvXZ>b+$z$JH9Hu76gF~?+Zw-M3p*voII~ggGQ`#A-|9VLadtw{ST>G7*BQe%20^NUOhPiN@vwrCsz`>z'
    '+>jpaJkJCu4@A{{ScHS+gR{l*CRTYN67-WAyov+kpXjO_%zX#^Hm>pYFu=Gu8grnzR|0=-I9Mzl2IN-wosW5?F#<'
    'fZ?u20DtcbX>d=3Kf7808>t2zIj+`nD__WFBPI4LP9DXD*}O7vay``xGM3AgvoXl=QW7ChjbVUjYFxlryofU?hy>'
    'qBG34zs2y3*1duqd4|_X0{P)lPWavX#lPbS0*;j=j)y8#i7V0B9X{rRTNDGueO5umD`*8dg*k0r6|(ncZL=F;@b5'
    'mKuU~{EfU-{(TY+0<h%(~GHYGC4Jo|8(?fOA*74o61zPPOmv*gB^3Zi(NIQcz_4&-?IO@@8?y<;}y9Y)TP#oGaeu'
    'S%BixGMo&HL&?TSy%4_83pObl=gBbnFxIUQE4@gNJHgo2pmRr>tA&nuln9ADcC-v1-M6YYm{U3k$qu$-O|PksP9b'
    'aJ)lKuYB)ZBe_mm6L;0I+hskxvsPh#I|xqu;YStX($Avh<KRfl&365@ocvmxc(5<#ekC$eXqkRu@M?7Sbl`Rnd3u'
    'h!{@zO5;IqITAa3prD!0lnz#S!G4{Hs50>Lc=PJ3$mxk|N|$`c805pdc({6nJUu)lXR5z+Y==Q^}V%JW^ND>(J?1'
    'rbeqpjNFdfO{X;v*x;d+f`gm-;F>^0YUdL*GT(Q+o#Zz3N6t<->pPD2q%L^4?qh;?IOJLzJ0~eo^KM}SHN}4cB(('
    '+545j(*_wOPkZx$CaTzHQb9%PW{rWWyCp=>^Ti(Z<^1U&$(@SR{h<Zzf<X+TDFQE>>z0uqgQ7L!#4as5ZZFX4Y^f'
    '9S6m3YNMdVbWjmNs0d?pT)I&u$)w=J~&wgG`6bH>9M5L{kZ06eQ>WqodWkhQwYU<I3GTs%g<6_rQX6hHpxzvqOGl'
    'nUdv!aqZ`|SSxU(X(4Bg1xGD*yvmBcAKjm^`&DbM>79O{r$q`*_sRnn?ztJAk`i@s&-9K3<;Y4(>c67Th`8sx6uZ'
    '9@J;CeNSTJd;z)A3J$QpRwnww?lf~@n-8vRo(UnlvEUD^*;+&e}t%F3_-ZBd}DkMeBH?SXD>F<0jXvpJNW_$10Sw'
    'vo-DB;|8*qVH$tc-aE^Dz5QXtGyE;Um40?F`mVt;3|(`Sfw6faVR;;TD<(7!Lr&qf>Terqd8w|8q1E}m-X<-LY+f'
    '%ZIO+Zb2f`IkzAp<d1$ESJZNUU?tYETOqe~BowiR=iWoD~oVSWB)7!W`hBC)JHqYr$X16Hq5VH1}_6HrvROvVE7_'
    'w3fGjdf?xjsE}L;r4@ksE<Hlx`5ECQUZlrtL&VPrl~qo*@~vU$Uk1f+69~tJY2q$4PFAHJ3@+Rh{H`O|-{Yj!X_m'
    '?9_GSd12f%&z7u&Gw7jNYK((b^^gaqq{<3+RGZ4&2ZPh$hUXdCg2kexmDT2(t7(s6TA?T&Xb~jEn6h@9Dap-?=y<'
    '=e4K+3eeHw+1P3s$h=q-_|4S4rig{VS3gz8EL8wgvj=HwxeI><GPZmzPjH#!9w9M3q9zCuTH&0M%jrl2`zv7=5r?'
    'TnA4q@<*zXyr6#O@4Uo`0|h62(;rMq)c-mE=xPhi*agFGcit0Z6e00cLK#YVmTE!Jyn@^&IVsTVT?D-oKyL5UiXS'
    'T4i6*LE?iEzY#KURaX(WdJ2v+nDjmh=R-n@N`5a0Et=8mnFMySk7mo{rlzfvd>)9*EOXDB~abymm`x{?JsJ&dMDA'
    '4{g_Vn<uZ_n||Tlxw<A8|{n2|gd03xWjO{n2pC`tymXH5dmZJLt~6Z+%j!<xX}xfS9DQ{#?#+&77E#%_-x}@y#r_'
    'H&F3iK35qoIfTm<RZX)|3$C2`Nw?tY3UZK%TrJAwz;aXd<#8}QP_Oaor6$fTxY+7>3$56F9kE>ghERX1UUe7aT8Z'
    '_~v&7o{EO!y^W@~}xEp2;eFWz&Rk^K0|zNH^G36etzDJdx_hO)Joy7A)U<h1CUyXW^UWktRGi!u1=&9V6zRJlDuQ'
    'CJ40h(v2~%odC4^0ip>m-ob?#FR=D<93jq(Zj2Sc;u?me|HAOH6x}(gW%Oybg7xc6_?zIEN^i85B8Ww?P2^qVWhc'
    '<G6}G~+?#(<EKq$xz)o^c*9o|>Tn^u|np_TsQVH%{?xxt!bHKja&s+{KY);~GrGOjC<?zUsZ*YN!gl}`XI>2@OTh'
    '51nU*EswdPVCG-;ba8ebg=XNSo4DXfF=I_E&?;y#?GDMeO-F&i{ennWQ%2a-XYWnIX#K{-C?@lhBD){YwL~y#8&='
    '{wk$ERAQm;GtK?qPNM(2G_*>vN6t!px2OO0{#3MoR+JfnoMVn?RItG*Vw_TbuV|F9M!y;jZ?BKeZ*dE=x0<&3a&$'
    '^s%*{RX-xCNMA|)jy^&AL0ZD%ArzO-xU&{u<OI9M9m(>Fwfb6V+74He>4HTq34PF1J6iE$=x4Qmb?J=k!`MOoZvM'
    'e#3OcLEu0XT1gIl-qlR!+%9<PEjhxQLB?o4qF?jaXvf_3~ER>02cQu!+?&(dBmusC6gtGtv}K|xg6M`sHM1EOKLr'
    'q%~b-fH<!b!o1bzyAK<>?a!M*?*umx?V!<Ll2W{rZzvp~uV!zK9u6H_@%LUxWTn<l$dU3hdDrJwu<UXbioS58NSS'
    'Rq{ah_oAewf>yo2mwDA4_gCaIf(=*u+tvcyqW)@htl=Gv^@tmvR>URiT&0Um*Ic-^BXAq(Wj`PqF@VhFJeRMeP02'
    'M(BO?W%mF3`z<5!@#Wo1hsmuEDJdzbzouQho$MBp6Xu2q*0bdPHyo`2lKT^!Y*&(#{rz9&q@<*zr1)yV>rZz{PME'
    'tdSkIFC-*AkLE6M!{PPQw_N$xLmQc_Y<Qc8H+uQy+bPD#3ZYWcwGRuP<(=Iafa+ipx?CRV6+h~B84ONFCy8=cM?='
    'qxv3o<Fh&L?+Jd2UUpsR2AZCqziElGJ?*9Vb!yH1QcyK`!U6JBm9luh1&HU5M$zS{NNZv>bg983|vF(Xl(M=@cxz'
    'L7P71`G<o&Lf4e_|+mw&jMyjh+OdX~M0oQ@cf$O>&&gD)a>@pW~KvuU^TrPqgT!~USL3fS%f^HbtroI`MgSb2D%U'
    'VGQdja)LD5=lo@cEi@IsE(Da5;RvUAbIY^X7>Km&0skW5KzB3&BP%hr?Q3Gt}!YGq)W6X?`v@fbDEWaaDj@!{r=N'
    'V4qWh$0_rf&o9kkN1%*aJPtoeYGh#^M?~_v--yGx9VP02bH_s7o;e#Y&SWg~S4k7x5Ev=Caj;a7^IB<vC7M#@I3_'
    'Yf&7OXNe&Em2HeEwmIT0Oi?Kg(47!qvt&=-1l;emVc8A(w`mVQuL4o9S<q@-M8+DSvm%zBcO+<%i}N^-)SY*&(#+'
    '+XITq@<*zY_y0oEGa2DTMpD7c8>TJE;-3bPICWpPWJbI(|bWmN=izKBUW!ZT5`f1p-qyLoaFwq+)l)e<a3Y`ZwOJ'
    '?hYIwkJf>oOWogcd+$O9r)5d6ydb_HT8^Ma2m&K{6ACtL9b3k^S3_l~Ms#m*0PK$_xa5_WWV?C~I)ZErZ{XN3#eo'
    'iF7{*uA5R85RC#<sQQ8i;YGRJT^#N)IEKeZX0kW7Kd)jOQ1poHN;H&9Jg;E!b(&`g4&<8Hx8#tQu9{OAa%nq@<+m'
    '7Qu?dn(W@$oG)aGQ>XII_dQ@cJvbaBhE{KdbADe{2<4tsg>xNYkTHTtgHCe}oXcw_uU;Vaue#Th!@UOil<(%`)N^'
    '47S({Rmb3c5Kv!hv>IlL%0pLc(I;kUjwhpP(q1o!iB+Dj-@aN0|pcIDX2@wFhh&j9<L*G`*31t0b0aIw*Fx=OruP'
    'iU$jmz-aW!@+NDxQw!5oYD1w!>P5o9QJ?O$K?h?yksgLHOZ}1%7S(M8HXizE<jO!FuRw~+*7C9L=kW)xan=dX({X'
    'G!to$(zm^C5haLrS`@b*YgyXne*i&%&%E|3d$HED};c`JRPf07v?VmqlxvMcaU&tz$S&7@<<E$K8xSS_sjZ_<Q-y'
    'dCpJM6~e9C*y%mis=!ahNZ8aNk>pRnuh8f`k0*>ODUmr|brBIG3vhTx@nZs*sYBk`j}s^{V6~CppPU?m6RZLZ(U1'
    'B9xqzl$4Z|m5896L|W-iL{txoQzL*|$mKc#cZ<s{LRd+a%iTp>Up}Xz5F@K?xc*w1#}gZFtycD;YQx28`y!umI4y'
    'OUwp-xJvm3|ev^^PDg7JV<TtIP)t~&qwyR%4(aP)}<=ekwt&Im2jwV{Uv*9|N{&N%;gkkf+GtVL#_lyj@BIOVo$)'
    'lyM9Yr&E%IV}=e0cEkaWnszn)?*djb*+`VTXLmc|6ftY^}kJuC8s=a{{P_s-QvcY`^ELgb^Qifa|4LMh&PABIT4-'
    't>#aEl-ijR0<CG1+y~^c=!L~~fmxK21Oe)FYJW;a4>3mMvp&zui;YJZ14UZ!wIm!KtI4LP9DJdJh$~ON+F=cnroW'
    'bmv$ITGr9(#*%>YIYxMnNtkuNbF}5accia_UQh+yX%^F2Ih{jh4FGahLC)5iy^;FhTo&F*~j(44wI$vd8uRJMMN|'
    'cNk;yIsZ7_ILL-mzSHIk+3mPmgbf*wQ!c=$ozD%`{(QlX%ca>%ePze#>AfF$*>QT>_%?Q&r)zGeuD9cqshU|=ZaY'
    'q!4XVTJI7JKcebJWFEjDY}alu-6-*4?WJ@V_*_wBd_x;d#G=XQ;J{H!@s>~L!?e})*RY!&1>2y#BrVw^HdkgF`nD'
    'JKNE{(@X+iWsN-B*-<PIVmYADXHLg^A6lkOpCd+=HtrNRNlD>#OvT>XNxQ$edA60%?AoGD0fDTkjIpZfa11<wSyg'
    '3-OPQ+-s-+<N-R;%Dh$0Tp~X(i&2vM(jRv^<M4Nm86~0VlyG-I`=T(|*a*a5a8GbGpH(Wq|i0yzQG5`!<xS{?5w#'
    'C?m5xiEeyD^3m^-z%){OalWJA%?E8~3X4Pv0P}4Ze6AD(MLvnezpP9FI`R*Kj)x%?JK9vYB6t3Qmz6&Q^h(V@{)j'
    '4J22=<fWu2;B~vi(7J&esFy$ud`)tGz<s6_1VX_?L(9!HxeSE^_o<<+f!D#e+HoGnoYpCLtQKp>1%NYVsfMk(ANl'
    '8tGo4TL_laIFuD`Fi^@s1rV*dSL-`^Lg`@4?WUYY{;qPhJ&Ww*avRC~_DYR`{RNj3iSu^p9o!}B%`V}WVJ4%VN0e'
    'uevk+|a+ANA78FvicXIzxr95>(_Y7bQ>c2zvHd*`oAz4_kRZsZD^NRe=6KRTlV)k%<m6onvl2}^!w<08jF63^?vI'
    '|z26Fpyf066w#=%fgmn1!kEo>dCs)>eQbmqNq@<*zuwu#XHZ9@)*&Xu-*YmaEkP_Fm@f6{lL08&}aaltZFELJC>k'
    '#8KLnZ+(wU`*E&SP-S6?xBW#hZV57jsX&*_=zhGcnkR!x5t{^<yqq1GuGJ&hdm^uw!v}Sa^)jK`O4Zf6Mu>&C%|^'
    '<$A^HPvCxd1Q&g6${qgwWof=(`ztQSjT39n8{CDsW!@ss+l`I_&u{X^_FlaHAU*NH`JD?sY~aV~UtSgLuOuhAe=#'
    'Q|B_;K|>o4tpyd}E-vA-aLV(ouCszqV!{g0<r6|wcOJ7J30`kE<Yw)OtU%L;q_&%FO}Kw+*Y()%ATVvM{0K@3m1>'
    'z&b_g6pSsP?6342TaUt_CK~N)Os;<|Kp)zUVpaO|3FSecKaVSxa;G1|D(Nmz1?E}V}N=6-eUh_tl)mY48i>c{SMp'
    'if9RpV{<i&(MS{O?j^Oo9H`kxV{>KFK{aDPuA9nj6Z<=S47W*Hy&F#-(|06Hep8rq#A3IQq&Hl$^!RPlCvHl<d&m'
    '6h?AHOpDD|-K<3A_I@?|)n|ydA9fKi(GWpZ{<Z>hBZG$;#RL9~mv^_t9d;%2`L&`>lV1nQO#;5$2ztXyi`kuz0g('
    '>$Kp|gXHA<aC>rbU)Le$5qLQt7c2~q*0q9Muon2s-zv%l!R<5kZT^sAjDEVBHbW1$Wln6JeLygu18UkZH-W)Sj=e'
    '9*5uKFOGouS+Cz_e^^wzQ8CU-1g!-39xlwF8Z)hB};LY%7p<{`$ZKRCoV!+Vg&-SQUWh<Lu3bCvn!Y4~{*=6<x|T'
    'rVbUp?#mnDZ$Z{*%Fgez5=ccmupAQQ<z*m;F@u{1`GoZ7Kg`IrMTP-7;<><xNNc1@Q=l1#}m#jR-A9{hMzycOZ`4'
    'V?I5$EP`k=}SExPTY9{b}WE|?_FYvsbsxA2ZroAV|shLd#pYvP37F<?6dsX>KwU{uO@7oY;C=<lv6uf6OlFRu(@Y'
    'qNGJdSuchHyCt3>5GC@;K!kIN=9e&I|TU?|5_j0~~<2H<t^7=i`Pa_j#Md=qXuTP8i8vcIR=*d^!jTlPeCBrgIJ+'
    'rw4y<sx6nRj+fBfcpMQ|_YE$GXSRpAoN|geiIb9&l6rpSUVYH2<2RqCKDv5n;h+XSR;w;UZkp_>`h+pC_6(zo<Rv'
    'efD6a(;W0Mf$FI%e$ai6O~T;&WgZi6bs6@VpQ_1?I;5nf(lmAcHnL31DA=}-VJt+^!G#I1nM6XQHAY+|y3JBhfK='
    'E^U$DwsHe)vx^g`yWqFU6^`=xTbvWGFV2rb2&V@TISE=aMlm?c6U!}uC`(8Yn1g>pEeHIH@EX<bImD>@EN6;;CFO'
    'G+43@j^ZJElB3PUT9b*|yHL(lhlvNKiPWdHkrLp6AzSSZA@)kQN@O5?#m6&qhG`9$_sB@1NYYdCqA6VbV+>??pcb'
    '3;OiW3>(if|W2Z%}R`Y`xCj-Jxp}GH1-?KMc^9tmJdQAr5EknE3*OJ7#jnaqe}beHmiL@<QJ<n%on4G(6DAEvL2z'
    '^}UnW8AK2fzj?Tx^HX~)*XMMr8t&~GR(li?#gK@V_y=#Drk@yh+fRtgB$Lo^xK9)jF5NJ_R0@o_&BTsVi~R1d&bV'
    'Z@o80h*gL&f%wi~_j*!9P$=?V9a|2n>Th#ZedNl8g*s-W6vljL}uVR8nmjr=;7oa7`YImtaAoRpN5loW4eZW3AQ1'
    'LN!pl9Qa|B=;}jR@rbUll(Fp4h5R{miElZl0W4YGKmp+f^@$@K3)B(h;dH61bb|#1nhd^$nCk2;1YpKup<zWFy3('
    '$6+(b+1&p`uVvE?}@Q-^y84d+?82MoJOouWY?TOePj2&Ag*eh>4V&Cj2{w*H2IIA?I<>7lAyH|2s-u<X0*UIqfD)'
    'QI@n_*sLi(<!wptdY>G4{q|daz!bjW!s!Fz<~GwRpjXb^3b8<=E7W#QVoq4zKBuBMvDkDJiFNLkC*g^vM#VL+mxi'
    'ThOJ8##arz-DVlFV6Ye{={kNF9Ud}rFPj{hAthmoF|5uAvu7okBN@Hde?`TE&Ep)S;XAy&QSjfvCI?<qeI+1Eq3T'
    'Rik`|O>0wih6AZ;WjAZ`p4!CSr_!EL3zA;+Mi4>9HzgKP`1p-dyh{e-#VsO$(X3rMF|^WI|kt}91SA$GU~XW!NYH'
    '=Xt!`Q7Y&?o?~amc$cT=v*S}G=lO_u0)T*oVsN8h3}2H?gkF51Phr9iW#`K%|6hlX|ElpikXJ8{-{QOHa#=<jj_G'
    'zn0>L4envx7ZPWrT5#}%w1B`?3mFS<D4df~t>)|>wCNuX6?#O%n8|P6t+jE_4mPZUD>IM{ta$BT2$J(RO0UZOY0r'
    '?c7vEV_v+wx1*KkKXL`6IJ5h0(tq#GP0n+Kn4)3i1smoVHo^r|7xP3?lytW6+7~Vh<f+f)KKr8|sLsU6cqRhl-!e'
    '(K7{7UC@XgHQKxEj=wPUpZfn-SiB)n$CyskI7=%`!}bamTtAanpw)ofv;n=*>J8?2V&FzvzUnp_xH^{HND@o&vE='
    'HI+yOb1kdl&;`ZJ1Ugnq$V3=w#cPXarB`37(`T%-ThoXH)8z90D)_hfQcVOWSJ0i$@oKGRcaV+`VBlm}X(!a<%^v'
    '$zO6-U+uJ`aljf*QipTRNUgdpr+zsMk*`BM=PBhNW3IjV;?kSYm@c&9R2Td3=S=NRwMp?#jqw^$uQ<}hGXeqm|Y^'
    'f`(CaiJ(gLjbEJS$Vh?qG;EhcFwd?}AH_vr)UD!2msb~yY^gA*5c1s~fKV88x+n<<5Xn}RFXa{?oe}4(DeOD}$Xw'
    'ss8yY3!qn6$YjpYECDyi@n-h<E;tS4Wo<!|m4$Bj>D1V-7srxJJy3H8cLMc;?cG7?*@$zZGp&x-K67XpQ?Sa#?+Y'
    '_(h6RQ0Lm|Q~IP&&=lYu!?4#Q9i}eFT`ji0>u?=7DWl1SzDoVM;^Fcko)zd?zl~CqFUWhSpyBdjy_vtQoN@iX0II'
    'Q3#`TG<deeD9|GbT|+UUqUPLB?xOmtm_haPb13uT{iLfu364OiCd%l}&FtDVT-w}fdr?R8sU<#2Zm+@Cu11qeUW6'
    '#E7EHQJSxO*h81^46P$R$b+@N+!Gh8l*?QgU2dTE7-1IhN5>mdLvr{obGRs0=pRb@G-e8vz%xqTIoHa1V=q1z2CY'
    'UmrzLeZo~FMdDHF(k?@posMapS5a>!RNOf~~GbRu(`Db89tpLoS+%RXLgq;06Qq9W%3sh>mCrd|7m!T-VRK^+#c1'
    'ch4pyn)732rJX{2k`k39K1^fAeL)!(d!jX+f`MOje0LS4v7sO3GfNU>(XFJ#Z{+zB{x0`QBujA7s5;6O9`TU=y2B'
    '-oX6`fm8H-jJ-@dg&LV=m)Odg3@2GPIxil6nO*Xv*YDHuKs_f!n;Zzsso;6|4e?us!45pyS|1H1<ov+<s_Dg*H-Y'
    '*Fms&#5A#Jk>qDNjdxm|BJtol`h8hFigkIJQ!Ay*ElR_ocyY=|=Bp>%{gDMK&YqWu%KukS7Tb&NF-VhO?9J(^{J;'
    '7LbG>d$P5P0iQ^PmyIzYj63IX=3ZAc$>y`XG&Re*&Y$x*PLgH7zjS*MV@p&&2rOa2#+@#^RRkE_$y9m00)sxnv9='
    'hHQm*=l7g+UpeS44c;gUFpUoCyV?|R;_jfg$TTQjAhAgZh_H^gf4ZamWmQLr?O&=KY=EHHf9#C}?@{ouC5DLNTs1'
    '2HFFiiI+$G<72D5)h<4dL57Akx5ZMs7XqDQn9dI4iedN2)Otzi57@$hz@32k|3NQ7L$)dNDRM60z_4S9INE2V<$v'
    'h-d0CW&Jk8P$x)z&2>{cjkzK3an@8fo50x2xb*Xiv2I?2LvxgPi1&Qbk=32SZa=kMgl|$$IjoS9l9I9$45;aCbx('
    '*X6BkMZfW2iN7IueII8*z6!U*K3je4Nxe&QUH1;UHxl5qpRohf+F2u1`K=L7_zM8#?$MFQZJe~Nt3;D9%1-F7IHF'
    'oUT?qZg+C1IF4MSceg5?1nH#KoAlCF^(PTcb~G!ss;aaIBRSVxi7#f`5_s9$OqO-*J=f@&!B!}s8bxFOIxc46Z!|'
    'nz*OFM3~$#`nP%HHr1xQxSwbN!_3oPuYP?_$vrthAr(2mB5e8DJ1r!LzbC1q}i5un2JSn`vI}Dzama$@*;uBt@#<'
    'A})pOm*LlXWj9=W)fvS&i_!8nGK*R<o2#mIn1ky+vZztOJQT$Us@VIR7|UvU<gX!#U2XR>qk|!7}zke&jUi$1J&L'
    'iIb9&l9FO+6F<(=1<5gWf;S@DYa80sr9l|`mV*_?nXaw{a=vvxHjm&XX(F<Fea^v3;xMVFysR;I9N#jfJm++H-e;'
    'N}8C4oG45)iKzMAfb0E;WcIUSz9s_$^HvUt^f4F~f8*BA9c1c$@3a5{)Gq4Ka~>YSfh%JVp$r348rCS=s*U={GUP'
    'T_qk-X=NY&EatP<kSirtWp|tu5FeV&Z4lNH%S=}SUfK>Zx#Hlyj5v$Y%sK**Hz*)8IO3utgB&n?lv6E2R0atxf#v'
    'EJ4d&IIap1wj>>Gn!F*vM$l%mPsk>bOz*kL62a+>zQc_Y<&#q3GI=+zSgc~hbFOrj-<Rm9K$vunQ^YY%8l9GB>rH'
    '5HuX~(%~YlIm^?Z9pvS8!6En$ty6gdI2tn|E;)pgAwZ1lWQ4;^`neE{C=QaNE-k)V;yG*>Od+CCamIcHl~$8$Y2c'
    '8n@5(E^jv)d|Rq9*XW**wkDrh%8u*63Q)z6yB3ToC6680mk}+MB|ps|@2TxuZIu10yi1{4fTBomJMb$(Gw=dwECW'
    'P_meM}3OqFLwlw(M@_pfiq)wA=RK{{yk7q(PuGNb@Uy0vCn0Xwj#y*rLe-kSG#LZ}@$j2G~NP_X7j_R!A`oXig!1'
    '<I{$!Cm*T19d;Qj&_`GK6%{s=KUt%TjW$tll7>j_VR+2(664K{<c?7*MIEQquD3N1PZ=-2LElZp4o!0o+5wutEaN'
    ')t4B&o>KW4{)7nA%XPiSymYn1yCppPU?m6V1*Z01Zl+-h>@SB!_{z-Z41Vdx)D#_PGl=qU>ST}?Bg{6Ue)g(iQwb'
    'o)GC*roLXB*320o!Xf+s$mrSzdBRcL9Hc(lquC{<@;z(R`U&dar@p3^MyGN^Xd`v%$0io(nmar@7+YWP`v~6AhRb'
    'Do@gVO#HxQr7g)Jhwi8K;ObHA&v+t!FN*E+fXFW4K-Yg20y)*v-3M-!F#MQtDE5|awVy}#w!e_iR0jhx$qfu|edY'
    'XUI8~=zbld$7^1+mnl9G~=l9G~=l9G~=l9H18holN=(lf(JN&R`XO0x}~si~tL!X&Y*4cJ3pL_TY0e*6J9`7WVhq'
    '8p)lyqhOQ>_9zB++i;}a5kMXiYd46&FQo)<gVGUKC<;C@YGV~?RK{Vy|pZ3V`N*Fl9G~=l9G~=l9GCUm8<!u3$|W'
    '}j7`Z%d~jyt)b=cw%D|T2-Z8JGWNd$(vS6FTUUu14P>fUWmlfmG8^L0nDMF|<mlSU1Vs=jH-K=cx5O4S3vTtu=Cq'
    'JCLwx7|6OzDPFp6s-#c9i}u(Z)ga6LTtOo9RC{U@OTDG=EaK>-6=fBNNh0H8a5gWgmXtq6Wz%la4`8Eo+)s$nadj'
    'S*ybVh24JTt?4ZSuB$4<`QI1gS`&qrC%3AYBfz=JFr4BR0hQbXKM@ls4u`FMpI$p5xL8O`L@vDSHFKVDx;^ANN4%'
    'Ny0&WArwWF^$4pNK~3o%D&4(F$-1l(vw{Vm0r8}*0FV-%~N3G>-|{fl`8mh5+ic|Ss8#qOIjiuG|K&H108v1=1)P'
    'RV!Aj760g#^97ZCZpU_g%u{Jc=Wl9spyZr6bmlvQEE7L`<3YQxbw?8`^%S=l$6weTrEU0P)P1M<8ln%c0V=o*5(m'
    'Kc$~+_Fu=+DsemOHw1a#N$G&F4d7IwXX)V|sHE)P7%i<E~@x);VgJY?S1$U}lF8|7NA(5hbp3SY(QXRHUXQV+Uxa'
    '%ms%{VBU#^S*J#_K-)#hAO{&KPus(A-z}N0*1f`ZPz}C$|Sw!a~fE^GV2*Iga8|NVlZ<sB|vH-68KfB<5I2ahFJL'
    'B$eMBxKkuIm`coHbH0^=zi$D>B{Q%0E4u#32WKah)th-glqjmDwqVV&%hR(1%w2cT^V$qwM*D+EX<ql}N+r0fC{*'
    'AWC>q0jK34l^b2>b`EysS|PPWbIS8+~;%D9)@=eIe7yBkkodzk&pFlx3^j>$PpDV;LjwCMl*wvt#vTg(2`eFRMV)'
    'GZ}>y>)2c^|z8zAMT%2QI24wq@<*zo)wksyYx_GLVC=#ebYOITR}cOPZ?$l>)&KXer<l8dhMTUOYN0-e9at3nBT!'
    '(zo|mp5><%ns|s-iiIL0wX$|sw`xbg*<^!7hp4e%Dzz1IaADOtkcv@nJ*&O_Xk;7>R&EbZJ79)=!pWx%{yqlUMlb'
    '+<T)D*s!KemAkka7Hozq23#B)`$ELfj90ZX@Ep;d3zQwTZ0^qsUo&?ia+_g!_d(j9q*V_D-JggP1mF4ANQ3r3zj!'
    'Y$!Yk=5ja{q&Wg@Z$tYl3=J!j+{;mXXPcf6VuNc6L^XH3dCmMB6~{?K)yOIJ^LdAqYWB??#`8N&?D<vPeiP#qzwK'
    'h2;<H$cQ<RSP=>6KaSa&>Nz$F89pr>VM?iU6vKlUIgJ@)#+xjhQY*Oio%)H9@4sqEd7dp<aiH|8IUPK!IYurZhGg'
    '2RFxsbI<Z{Y1V`Q#x93-aAa8!`iU9dDOb(Ko-}S9u_1pILBF$to4f0l33sEp4YeRmnm#+Jrw?)%~eo8p2Y=0y6~9'
    '1deOTqt_35#`%wn>Ib35lD0_>+ZDC~O4oaiB>#V8Keu{Iz9E!~IFqh&&;ir%rX+tQk2ykS&oh}qt1~_uk)0*O7;X'
    'jpZLCrYd%RgU);P3Mjyj}&G!7JSQQ_hRs59KGp`wO;F2{@cyCq{dLs#1qQ*%1ck4YqZUbL&O#FrSYfaVC20o9o6g'
    'IXwMUuXM}fTV}Gw^M)&d|NJVo7#}nHmv1S%s*X&~*St^Lz@q;P++Z;3Y+>1-I{H4sy+=7-|Logk`GxrO*yFS7D^g'
    'NYQc_aSl0rMr-FG)WBk}%`9|x4Sq^kXrO1@mr_F~2b2%|S5F277;6>l=e5Sdicw(3tr+&Yv(kFAC!26qjQqn5*$6'
    'mq3`D4xbluOH@_t>OS|yHEIaV1x9d$e_j@CI@O=dd>D4k-QGfbCyBTMV8r3D4NqGptaF*P_KqMfXxI{+k;TSmj(_'
    '-#wcIna)t>SIYdR96EJ&AO-@cpO-)NnPtVA}hH2_<or|*x!lE0J1=s~^xoMsUSSV8C^4W3sH7=JR7b3{H!DzWA|0'
    '%o1Fco5HJ#(v!7Hn3m;dAxCJS;bt^LVUU#~Se*)E)kw4ps1;`4nBRssrGtFzzqrW+SKqBb{p*m{VpF3k-|8ln(O@'
    'ELf%UoHJV6BDlaCoZCTgO67PlPI);_j8iIK72}k^X(=q~@Y}xTDlIU{v`><GY&?7a(XGnh9}eD)PfvMx?dY;kTIG'
    '~)EGa3eXG4YhEZBGBX`0PCdW{{{>rI4uWvC)t?KD-0JE{tC)m1TWoFE6QSP8vz@j_d{o&Z1Ji2|$f)w!5E{HnZME'
    '(kdOt0?4e;-(9(J3epR-(Am=lbqxvCnY5%B_;LjEC0|fcaoFuZ5x)KM}>a}Q^vH}L5c!?(e;eeRKuFy)#4UhZPNn'
    'f?c7YRz$5eUe>0QY#F;P<+-s^3=fJxiNs~(Z7U*-E%{9Od_Aj~$avWuH*vbRBs}_i6aGo)!XdC>~!3?gVN*nU{$I'
    'x7Nh_6FTp?{>guTaTPM2&W#IjH2(1XqdX4x*Bd1m{n4U}w~XY#FH(cNdksL~_v-_lU{GP~3IF?>))7jsW^P_OR-u'
    'GR?t0)6WFgmF9-A?%Oh&dquDvgk^}eE9^c4;Sci1us9sBDz+xC-3Q_zXh{=G`sTNKj>uobeSSU9iE%hhD|Wz$VfI'
    '7q(@lm|^)33f*9{x7sU2{?R@c~17WfHo37)J@^ph`|d(@&IZ@ZJ67JXz^Bd2dj9351QB_B9Bx3^YmgNr;rdi~eTS'
    'PTv3na*B1k?E@Ss+zK(0b#~i1#eJwHFC~q0E<7URVc(guvq(5c=a(cnBtf+dTLp6(K&^<3{$FL3oi9-MT9#`-CFl'
    'LQ!bkK_V>Nz!Mp=G-Wa#+k6Y0x8A%V%ZJgFImmH8tJ>!Cik;5b>Ii2%rv-I4P)cYr9Hg@B2&B2UB{iBjKH{9Ti(a'
    '?(PWODB)#^xrN?W?x3xx3W6p$3z)dB2g|(ki~$UOSE_N*3G*ZTJTn>D5pshisHuGKWozs1c=45-MrW;$q;H2BAcI'
    'vbZf!v<4LoW^ps=@$N7d_Zh#bEi#*U*>S@pCppOpblFl;Qc{1j3c|?4M{@rm?m1CTdcf`@Ir)4@PICWuZjz0oP4D'
    'NFlai8>`k!cyg=?+k{@vVjq8oND;Lj;1pAX4NPI8nWu-Q~T2jMzC=W}aWR(=eDRbxLc77j*v{CuSY#X@tJ0dvR{Y'
    'kRN23Zt<_-X6hu=pZpJDNKx;pa^k?eT2BJfr<ckvkyDREauW5UEV#Rv8NnnNIee%|Jpf{liXk8g0V@`(^}S??{2`'
    'J)U-aI$69PsUd{S@-Eav0=qjAwhyBMNSK?d;&bIOJ1<v*M0qj9JPQA2;H+obVPW?3oM}x>xoO-SaAq(!7;M|Y0@Z'
    'P^ujC0>MfmhD0BE0*H&E*RzLfl40j5GL9@VEz|Vx0QDAg4CsbGN`($&bhF4%~<SoXp`i`zaMsJ4ml&$?fwdN2tf#'
    'xzAe|+~W`V&t*R_V=T<&+)uO37^&x|KHHj~etsbo@Z%g@E<d;t{K)TLMyV_(9Y+5G>jXCfxcyaY);a>K|Jw#W0@b'
    ')&8Sr5^>B;R+zcSfqT5%ze@H45n9HK}`Nj;0Iz<1qyZaJ*`l9Qa|B=;w|NW|G>YJe$EGd>4P*`4_uq{SG^=O7v24'
    'n7AN-qZM*?(mp?2iN)vueGb^x)$)Z2KTb&j}>UHlOX3fDaI+~V#PS6++8tF3I8%dh|8K@cAR_U##4JvN=iyfO6u8'
    'CjUVr(My=8YO?juBNldu<TSpwLV*qmG9;XL(neVA6Em8ccKMI&RysNxGsgDDlobokty@|{b^+;={u||%3;TvLW-E'
    '%W=)~YDT^)_;8-Tm^xh%LRBf9|FF-!qKdw^@JtBLDb>SqDD)!FnU-ihcF7e$d)^@T<OlYUC=BL%_1mgD><8HgfsM'
    'g9zt=p0*^($hiy?dg=!*dw~!119t=GN)gwcw9@ab9|X``Hx`#&kP8>&A_Tddl9Svs$4N;^Nl8gP5AtZS_^d1b)hp'
    'ciSB);gfc-#4Ypx%e2PUDu757G_VG58?gw2hO<l6lJca54<RAX|qzP93)SMwv|U){9ev{@4o|0)}kJD}Gsj(=5`$'
    'u-qWeEIROo`C5Gtn|Li;&9;AwcPkuI11Qu8UIRhlKW>lDJdx_DXC{!5$`NI^dKqy$(2o?)pJ-<)mD?i^6w4{U{WC'
    'q%tQOTZ!oZuml%_PNz}}zR1|ZzFvkRjS|vsvY2?;fO+PXWIV0Z0AFOMV)34-ku)tBz6HbEea1EJyBIsO@`$Fx3Ic'
    '%o_<Mte|t#gs_>n>x5fOhb2!OO-Uk{b}==^Ym+_k<=8#-5C^KNgi9M<w6}vy-d?Na>k|3}#?6Xd21+!VD7m&>TPo'
    'y$p4K9TlVO?l4(2wvRu|H3*e&jN%N2`XA^8(d<kMYt7yhcIWH=Cx4&l^{!xgB-8cx3DZo9z90Dh2C?q%3A(*>C)x'
    '`bcTJ(&-$mU1+S2WhXwRcjQTS~}d&bX4hF&!hzq6hXqLHg>8|UyXF%&O1TrfE0uD5)C>lo@j%&I^5Sge1UOLasgF'
    'Eg+&+!*~Iwa9U=1-n13zQ)j>4iB{Kd2{yo{>a1RjJT^?#@Bbq*Oio%l$6x7q|o=*-AYV}+B<<VKyE<{Y21fnQ{~M'
    'iEIGd|hHZ^D7Mx>)@l~DPmd$-+ey1mvV{v!ls3F207ROR^3+`0AT>h2k;%%B!Y;K)qUe*>5Dax_9>u8WM4vNOGIH'
    '(7&`}7xM?gkzxg2!%#FU@_0e{^{$tVDCKVa^TQVY|>=L84Z7QIjj+Qpmy5eCo&G`$pdN@z{LN%o%c|jYOp@Dee@>'
    'L2|qGg6CT;`1|PZO=e#2T)O@W5Zq2wIG%eylqhP4s|Cxa8sFdgG<O~Uz)$F%I->oxhV}<GkLOwK^eS3}7^Bj&Q}X'
    '#(t$B#FPGihx4A0xiwmJPO&cVB<=LMhNE(|V#5!lGV>R)D?v4pbBe#qP8p_10pqW=rpMPjkdE&EfC(cqGw`Ex<*e'
    'm83p^3nFFw4~cxKQ7AapLvqpf0L7vl9GBh<@?sM^Kt1h$G)k@qa5$yROAQB5}9Bx7_dOY3T4t87M#x><9j@{KAZd'
    '2JTAE3fyLFL#~J5Y+<wmZh2WYJ<E!p@ean8C!sc);@!z#ET>W?!=M6Kqn7n$?yDY95lzjwcM;Y8`7$jjEueTW7Pt'
    '?F$QG(KF?mR2h<$jujz+H_AZZ5_7!A~KL>4s2T1aRa=S{I5d0vtKIZ%uJ9@J=OLP&0}vBY3_dg1;|R@Om9EHGPF!'
    'e|Y1>=6-AyyuY7AdkKNEPK@?~hxbFE><EJkf`;%ow_fxP^ZCGi!G7(V>&7uTY>cR0>6XX0%w&t_&2d)j`BfUzAIC'
    'HMmwD8>#sDVgy~BuQwz25{{8kydwN6(3q@r}i$>5JyR;)S2qvdxeV$u^XENbf9ZL#KzlH9Y+Nl8gbJ!1;&u;|#M)'
    'cBk0Mwr97l0ks{ClK*scGa=sD&I7`w398k8u6+K*V^m@V!@qO#W?lPa979H&*dFhs?~}_9vAaA3~6(%w&rdd>UL0'
    ')1(#wB3akvZ;P{CHfJ@6Q#{Hp)aZ?4kzJgqPLGCp{uAv}TUy!ROIm!L+IVmYADJiLERGy7L{q^kKxb&xy=QqtBP}'
    '3roW}ayeOq&*ahft;firWWIZHM+*a(pK+1<2A&RfwydDaP#=ntCXC;B@!K)XC}X8By}}aa&Vp?rq5DKzLYW>#@(w'
    '!Hz{0V|hbU{)O2r=XHi=3Xbi;C?<WBFOO?OY%opa#QzM>F!=TS(4&|IxRSDgU(ky63(6$X_u|n+Mk=wlS$FMW2O8'
    '|0!XiJR!IGO;U^m8(tn%FxihCNtx@?b^#_ljprbk=VrAPITc3@*9(rtoP_WpS89mc)m5jWXRFq8%gPUqfWIl%<)Z'
    'D-Dv2G(meiNcVq8$YeS2T@WzmSw^h;wp(=J*HLJJpS$hCE6{6nz^A+98)vIxTL~j+$cqeGx>>FaPdRUej>$tuQac'
    'ws1Ii5W!`~YO@_|haQa?sYFhNo<3A2>=q_JaQc_Y<|DPpO>;5aaN!+Nllb%~nwin6$E4Wz<Hy@U!A#M?$`xJ3!_#'
    'C#nYQ^Ph;w`-|ssy@Oa^6W|&l?I{ZOb(f<Z!e%8_svV7^k>(IW5Fxsr38b?Y<J7l4-|DNl8gbNj+zRmuN{|k>n&N'
    'Ieq*o$OZ7Z@~9*)lS`*I9U3sX7*zNsDjCk?Zc(oAhnXBic{{BaysR=gaKYE}r{}!I=KA10)$zr>a&#qg{*YY0OzU'
    '+4l2dk=KfT4~>Y2HLY)&x+^*iig!FjDU_@v}iEI7pr`<-We7pPcrijuqkrfYGjQM*PLlP@AEDJdzb=SS_)n=#6`a'
    '@hltZfM7W$*91X76fc@zSIj~4bkAx;6N$-)Y^IpEU#`SxoTi3gnTW+(J(C8kKjI`!Vf9CQBf0ugNQb-LQ#K4pjmM'
    '2jrD@E{D?b?IdJ0rf%p#aBF_Uw-=HEgG}uTwQ0f(AIO0!mr4hFbl@z9eH@Sp@@;-DkkMz14xQVE^vR;5znA5T=%*'
    'xjjdw9gMZc*0o`?fKpZ*BDN-ZwR_8E9c#yF3&;HZzZjgK@Q%G91OgJ!o#M?vKc=fNbfkmgdqzGQ1K5nJrFn&Cy5s'
    'i19RIU%&^sR=<;VaDz&df`y_2tWcDaR3tJ&DA2+q2jq*q!FoJ{kil71h@U%+`5bRVA*sEPiC7&bhhih_u8$0Z*db'
    'qY=*D=d$Qp3IMF)anO{6fMK2igXWBeyx7F<Cix6HEVI%eS7T5_KlOfds2xw0g;meXaDvBkS{xX+2me_`@XBPAszB'
    '_)Lv#eLhB)8C#0P%xg$Yz__Z8Yolr*R2<KBoGF1hRQHGJ@V9DMXA8G0MSPv8x+M$585+=g}KFJ+XvllVlj&=pnqA'
    '%tiSba=!aNbW8%l_p@W~WIDMj%?6i<aflqR|KGCa54RsyqhB(KZf9Eo|P46*Go_3MuHM+M>AJ?!v-rxvvpNy}xjj'
    '-`~!7#cCF?e!M)ebh1V^sCN^9b43XnD=M5rbtMzbwB3$<_5d?iKZ^hSM+PluAy=z97CyQM}V2J%+z)K9P!??$_v9'
    '81&jaqaC(Wl)A1HFF_L^eK^?3*my?i_vWmtaXxh@Ug^fb$-`iL>&jX`FSqNkGzy@Vf?Ob)z}>m6&Frpeo+NEd!8P'
    ';f-T-deEi9_Ie&8(SV{?ns=TP}wbJ-|8FZN;7;&9?feU<-}3C{oiqK}VLy!6>_BjBcM1^u?`|J==$DW#e|W5S*+`'
    'u+LF<?d1gtrUDt+-Xgf^{B+Id9m|B9s!4{x`mjNrJLel*CEE`lZofx94?;d`iF!QPiKN7%%n886`W24Zxt!z?oMW'
    'Q!{e0Icu2<;9C7ZPY4yMu!Wr*A>2s`bC|F{6pdXw4h%TCw&uD^}%V<wy0V}qgl$4Z|l+^Ppf4rKv0hd<9rV#z~qA'
    'C!VlNkG8ONdRdeKG<E`$tQwuVeS)T<~uluwj8R?Rh|!J(Y~SpC1fN?rH1j{Q&#Ua4qeO4yGXxQ}~K$)pIfUj^-kV'
    'FX=T+l@oXsee|uO6i%UYdLY$AW3i0#ct_}<2VLFZtG?w$_zf(}3wKC+cZW(0VNfA+K!DX2H@b@{)N!~~i&$phnp('
    'D;5HkMREKU=vL!^7)I?8_qTn!%PiwCq5c-%Kc0=wGQSTpi+uLoIS5yo70-&=8J9|DVo`Mze`pvQSwSZQR(llTh)w'
    '=!HTypBWL424nh0GflQe#=jJAH259nz4rP_-9S`++q57(zL9Q{f=S&QJ*>cQ1l6&>o#c74<K}Oh9No`jzXYsY8C('
    '^rdv3+uu=rV{KkZvpDOi7z&s$6B(4Zw++;hVvd{1=1wXGiMQKcat)v#7ZIP1*oCsv6tFsG&@XG2M^4iL$Gs166Nl'
    '8gbNj<0Xfwa;~jUQx7HJT}X4Z~$Wg*|A{mMF`?u`zrR#3MSU7aU}oI)tW~=RL3(pGq1FL1{{;UJ?Mq5;9RWWKsAT'
    'iXb-EZj6Pc87+XEssXF4`Jk*9DvKoI)SRYA7BDaSg7_^1sDxBGU{q3?tb=58>3b^EA_-;+<IJO>-p1i1#2MC(bD;'
    '@AQiy?byJ{T&_p>sB8DWe|wAX5RHy66TC}(v++_Bq~$<G?{3<IoZ7tP7x{{d^eDtF{#@YDyp73^cQ|M7U50GSMXF'
    'u20#B)>w#O<+z{P@eUa3hXs>B(Qsg(R~9Z=l#euPq|^?;XTd7jj?#*%b0Rq6t?Obem8KtEW40*3|v?1l@4;I9OrM'
    '{Cy{A87I6BmY6SNZuai586OeE_L%go>F_+7RJn1v?bGZ=oAaCV&!ab=;5nCsa?HYF$kCRNy<zk-D@~U&WS>~Rb^%'
    '^PEZijg_$&8pM+*dutIE%>LK7#8mChl6e5O<@HBEV%Xw+>~9xQxd~r<9cg4Jj!pDTXR=;z9lK9CMVyF9Y`h2lK-#'
    'ue>Z8?yCX&$&JI|W&FK991L5foX^g|I>FL*WI+zr05*H#DsixK5O6iE2?xsy3*{~c3M&PlwjDh?<%Nej3ptqE4}f'
    'j<;BeSX|9Bt=gE^pjlb34c4cP37Db2xZz|wwlT@F_CAq;hR2~@(bsBB9hQ^SNgzdDoGFkv>R{^}5Fn7?xe#Ib_U+'
    'w;arNl8gP<I2%-+Qu`H327!XU-KYzO*(0vVTE;0HCl04U@xSqLfjfvhzm~_<FEnF_E!0P1cn?d+_+V@inTn=&_1-'
    'xYjZ2miqn1@4jGvIV#8fvcti8JMgMW`q9AuiV0cQ76!;5;I0yDLJ_iOUpYl26maV?U#qvR7iV8d|KRDvvvue$b;3'
    'BQ&C#W_*Z?x$lJK`_IEwh^L5M1ivDdj9LfZ#GtOsgQmIkhrWjN?oz93e$o&BZA_#&K^)9vHnpba263u%}p!-wwVM'
    '@nTj8|NhVTT##U-UL_h^6>@d!8R<9vEVsmlJ5Oz@vUS=Rr@>Na!Qz$;2d~S*d~Q11K9y2eX!`4#=l15TA7qNXe_{'
    '8}j;oO&#woqi#W<zLmP{c|QF3?rdh@vlNf~yWl$6x}Ni75yT_h(t$w^Lfl9Qa|B=>A^Qd0kWxkA)rro>)9@Wap=4'
    'jT?U{04alajLq<UyQS~-8FL=CB!)1`lKVI+L$MTo7@$Vv40YO9aC9>PMDX^y~~TVNeJT6*W+^~5cdk7D?kUAWh&>'
    'h#x>z{5r}Ke=dzpQ*;vR|aQ#_9cH!%6IJCc&94y!XC-OOP!byLh%LP5eu>l6w%wfGl*v`D*X@$0k6yp@XuD?VIao'
    'yHD`uo1OeTL9=gn-YsdaQrV9G2EZ;Qo`=lUvV_L2oqwz9D<Q_y8{EmL|3z>Mywe7RHJ`#e3dbJ|F!{UO}#yU^pwt'
    '%vhh#brIYzTZFhmT&yH=J4v%PJ9Gq98u^&=N3(hVeksVkAjT~h8y_4A5#b_7dh^FasZVYmTt4C@nO|N?N=iyf%4%'
    'ZS=ht(O(_&68d5f}bion^j%dN(<gybKM9=P@HqDi>VWRvmY{#Nh~&OJX9P1Ylh(Z=fcX0|Hm8&tZ}>J&VwuczAR|'
    '0j22dZVI#e)(Sg><oov3<B<Q8*GPG^?PI1?Hgz)STA#`aljJx3C@f6_)`e7P?Zk-?9u3}L(yqbC+4^GlTB4hN=iy'
    'fN=iyfN=iyfN=iyfN=oWKq5IaE10?r1a?hO}*~Y$d$s<!B`l$lB{$|;6mBhk0C{6V+PIg>(PNs5FdTzsHI}Z5?XS'
    '3m!iG{&{#CHvCxN}1B4-^F%x%HRNujC{r`+F&=XHAm?<4{QMFLQydzS?`oWrp+k=(lg@w4lnHY*jM%yv4Ao4Ox&u'
    '8_9H|u*$bt#xxYyFhLdJs>G`zT(%pk5C{2))u&(7a%TR%pSS7sTNGCb?3=cS;d*woId0-`YB&$W{csFuLt;508%8'
    'MizCjv-{`~ukGTkNm@wvy4x`NAr_4^<`SKDO$ZpF1hTuDCH&E#ce#X**YP(IgH@O-TWe_u_LH<lHbL$Ll}RR5C=h'
    'Z0%F`|!E0#>6F-Tp)VZZu;{3jX8$YC75&TV|1v>4~c5#c@td2edg!wQ+VDk_buhjfp|Zn?&#C>bLn|N?D<u4-w@-'
    'JqBq1iC1Q^lr#J@33UP`OI_j>GI^EO4P08)WQ5&zjdWV=(%LbH`@2!6WCnY8IKU2Z(iw|CjNsGO`Z(h%QmQ?NSM&'
    'Gb^m?qGEKT|z4Yl$G@uqwj&994z5iK-BnH${xYc>&ZFjUzn58cd63ac;N31RZ7E3;HIL=3aoajmL%cVkr*N5<elG'
    'lPh$v93-sd-`B0|oOF~<3p&R`Du;r64!nrN_#C{={RBCm|0q|`(#2uYbIm;~_xCyDUbXSt36UI^lai8>l9G~|!Lp'
    '5+G{{^sUCrponyB?LmP~dAUoi*GaTaD8778b5nWBx+tfpvlIU?PfkG?9JR9~t4&50*z4|a|yq)cVx$oAL6&&MBuD'
    'dBPbk5Pv2@Lp{(I51zZ#}6zjvlmqdj=40J&N6TD*V43~Xcys?zxPeo-$z+DWQeu^t>p;LrX`rE&EQ;~*hf$*;jl*'
    '*c1dBTI7VY!skdpax{JdzUzgi=63xBrs$G1nrM;(v%TzN>L0`5wUr?qo|MM5F^<u{L9&&zg291exKe0-t*I!G}pl'
    '#rVX$u{d8C<Zo8gaTqglGQuwYl{U2Ijtm(G2KdW1c@To7G1q%zKSR`x|S(d^hW@7}5)-?PDai^wHDaXLM2OeSP#v'
    'di24GMZvV|Yw{u2Fk{p!ExmP0Q*ueg*_bm-t=G+&mOS-!W7z^Vy%V({vp`KV2cB~*>_pe8V^MqA@k&WaNlCGUQ+Z'
    'MES`XOSjtf<SW6;5MbIiZaDj7s{lxmTC&Fu_vI)ymlX=75g*Wm=($P;hvzh>ZIC;zEtt`P)D!S<0@U20e26DT2vd'
    'z;{NC?XL)ArWo00wY%N$54h-ut8weCpf@S5X3`-bb>|GS&?5y!T=Y=VVno2BQ41T;e`5tG*>cvoHGIyC+2{ef=jV'
    'R!}P#MhRO<gYL%@Ta&gB5k`)kZUi%ApM5Jx@ePA86bQpwW^2X*#<mqadM{+$q%$66snB3*=st~8f3v%lJgv0W>YW'
    'Va$%V)VGiCyuu<lM+eSnW8SLt|nel$*yBXC-^t{Lw#EfB*b_%TjxY%Ej-6CHHJ|Qc_Y<QvYMgyg`za+<%volKr3L'
    'Bq#enDJiMHCa+f04%~d27JYr&%x(oODW6eM<Z9m4Nfnt`g>ym{ua#niEMv1piCNIj7PGVsND||~bvFImdf7d)m25'
    'ZVG?PP{FPYe|CGU`9bW*ulI=9W{P9UR+**4rqI(Nn<fnqbk{B@qN4>6sep9#Cz{9c^L`Jcs^DaP@Vs};D0@5B9G*'
    '7>rOh$F-uyV%AGE5F9*9lxKQ5^`iZl|N!eAdP(qrZxn5t)>}6p3G?<75J7oacXxX=D4nZmD4(<%qGH+r>%i&q!)j'
    'o9k#??tkF40Zk#DQX2>#?nJ94y6^&^ix}NhShyHnPT=W4tA$bocFl>MhFr0#_qs!}o%Vw}LdhS5qvJiFyx9Gpwuu'
    'W4c4uOyQYE7k_ez#2~Q!VjbxZE-88OeFYVms%}G<+A%c6bT-HtnRgbLu@{bR6dxu1$HjQP?`B$-i44qaXA2PF8I?'
    'd_LKxhjN~#@*4?oD(?Ypav6gk@Iar5@a2gGxP3!?sG@27-lhcV)Nk2zbIs8S!}*+hCO`2VQXzD{^5#ztO{j!#xnn'
    'uva+tZSTlVPJk6teo?(LbU%|6)Wbuc)FmMCIe9Wm|)MTpxUD8#K~U`ny!M~s<B|7dPixgkx*EIV{3HZ3Xg?1~S|$'
    'WfJ))U&5K#LT=N#}Qa^whq!#Qc_Y<Qc_Y<Qc_ZXSw(v<Jsg#k{^aWB&zo8Xck%3gh+NNk@?9w=){2VTn7NdWTN}K'
    'JwJsHFUY#$lo9G`HSwF9zM@YH$^B!sipBvU$UvRApW-cb;FM4*?)+rYfD{O^aQO7TuR^h!TxE88#c5FYFL(RG%>P'
    'I(oc`|?t^?c{ia@Nkni%$QJq_WyU9VfO$TaNZ=`^~XCN%1$2{q|8Ur-N_9RHs{w>ss8L4&S$3E;pVZMiP}W+F3Nn'
    '`IpP-^fmC<$+O>3c`nY)xwLr7dBRO-*C@wzmFASH&OhsBed=8BZj@Evt}Vo;%xM_r8CGZfJ>*N`Tw<T6D4nwYcgI'
    '<mn%74p+8y!i*H8PSi?%E~N5d7xwLOzri>jNjOxFZ=uO54|yz6vp8I^c3tGuZzXxNKcM+1pCsYW?HlKU#oTT?_q8'
    '(}XpxNoz{M=BZmHihOMUzWkmaQPrPBd!+CI{MMY)nst<UHLZdW&O7Xd~bIKx7zjlocE<GT*t2r?m||1GnYfsTm`w'
    'MXH=!IP-p!Wa#gqMerOvMx4}cIgYIwUj4gXd4_(|?fmpLiGwWsd$X;d8S#*D$F4*5H+P=xotp7XqtE~3oa?^08Kz'
    '|*%<L9`>;2Z~?n~To&=XTQBOq}cBc^U20<v7&=KOeiiUG4rp-mn_u-1}(i{PlbFXWg|``-`Xc9L+v<H#z>&;&!g?'
    's*dw+C7}Mu=Y$j7L~o|PkzkDYn%5P8@3N_l`h1>gbVJMPhFoCg5~6?c>Z)~3(+&SNpB@YEz=-;JeB6V|c9^H_k7k'
    'ZE{S?1Ntp4x8t8}wye36RIFfTi+<8|+_kEEi%DPq~*7won8(1Yakr&l*mZ0sdpe^OFXQvWr1)*1EFnW&_chgUX?t'
    '!D+f)mt1#z85Z9SPPV--bO!qh;eMABL?RhKZtN!*<8%Z4;mH9?&%d;sh?&gl)<o%D88vFVTt7Ry9;b)TbG())L{0'
    '9`T8w9d?%K$r5MkmDsIa(UkEGD+8{<2jyW{ei#0%?xtve5Pa?<G%k4}KFu;Nvs=M$98;)six5kw~$I~o0&9vwq9R'
    'F^?X+er2(P5cP3r@Ey&__0o?N-;T8wq~yI`CLvSufw{7E@#LdFJVS+PePO?#GP#VTzA*%Zdv;W(Zhobw3ovAE#rv'
    ')mpLUvYPb715qh47nXPM`xkaX7g+VT{J+OZNl8gbNl8gb{j++d1$k7ep9R(@ts8Ola_n>_<(Ov3^wld(WpV-Q$n{'
    '~zRBC-!pAMcR*O3F(s%)-mB$;6>-wBn?wMis1kyW{;vbo{<<TBMY({G)~jxOhg)|I1Nrr^W$f0W$Kh=uM(;isGld'
    '%PCvM|Z0mu@cDEf2nh7nSB@X;K^mg8t4Jt(p}T%;^-fJXIfgW^#9sB^8lZ!_mAJ%Hw<PNV>fo$sqA}p*|J0-Nh%^'
    'LL}h7F*;2ySk`|O!Bs)WrC0Qb}k9`})?EC%A=bq)Bd+%B9_m{rS=z0H9bI<4Hb3gaovpr{do^+HRYxuGvUf?nuZx'
    '+WvI2?rxTy%BAIl+Or+YuVeappnXeqZf0%kRT0jTyroU{iePDbsMe97WEpEpkkymnX)@XP#w>f+V2Mu1Gsra0~lB'
    'e2GiuOAk1vd=`=S+thP82Ahzn1Dz8Zq`WluTy;$pdO7Q!#FoFhmjvZc)9PJj%X5j}M4VRg09!voR*5(*-)c*Jn<3'
    ')t^>?VC{^n?ytM<Ts`vL-iKp+qZl>5~}<xX|?zmpT|3*d4U2LgdWAP@)y0)apv5C{YUf$kx}`Sbt9`E#;;C-ug@M'
    'V-*k;+{W`X0zl#H3Q_GKaaMaT{WD)a?YP4ZjcsG<vqi>>Ov#YasHfgIhtd5YDGWae*T>09$>sM+s~h~T%^IFQeZo'
    'OZsF<~&Ps8PTF;+bI3v>g_c*hy=g&>fztS|%v!xN&qSo{0_WZj|k0@EspIf<0<4uRA*7N7K?^|Ua5w@N`r@t?xy>'
    'Tk$AU#uJJ%7$TZ>Uj{-^ZB!{5g{^Mp(m)3W&3xKWFoAH*+@1e*T>0dVBs#G9JZx{=A!&^D*lDE1vSV+j9Q=Qn-cF'
    '#u$f|{59<*+xhd2z80>45ukH`)-v6G{`^y(o9p}IoafKKb2_hX7tcF?e%!X{F5t~9{`vE)j~UO|egjTP&Y$lb-=a'
    'jIPiXZ=7sB~-5C{YUxzc=2zfkrb=0G422m}IwKp;>ak#CPBf80tryWzQ_Qmiv|0+Tp7NJx22Vr_*z#YEg&R-@u%A'
    'wKqq-TK(*#lr%SC!t*rhXvmE7K$>+jZugELCr-|uJ;><XOHkWBal}#S#{`c{3~9>y(H^t*@OJUp;{rsX{b5r`JNy'
    'sV3aXEZC)D_tP7l6m@%bmE{eP(_&%>RtKZfAg5QTiz^x@$U4_q!9Iw8msq!yBTH)E;u3$j*Y3P0Q=Dy#d_V>Hhi('
    'e2HS+TR>qQd+Ui|s2RF3*phr{#G{kgVVb^j~q}PRhCUBZ@*>0Rn+QAP@)y$^{zbh%gOYF6H1{xr=k<P+syTmkR~;'
    '?Z5To00MzP{|W`x>N|Pmo^$c(8FA;M=k*O0abH*)(X@|>yW({HW@~qxo?Jt~U0(83{i1$F>%M^xO>E<FAADfOI4g'
    'PrfhANAKTzoG)2g<*FdUpk$N}ml)n4RI*}^DvtZJc{9IF*rRTS;)aDK(N8HY>s<Hh7yGZaDNXrvtLi<UHgE+oe$q'
    '9^@Qy1JkRtw6iWUzdMbsr~4P!^3i{`E_*Sq`Vv(o?&1M0;N;{3Jnt9NAVJd^F|tjgagT3%Yi^35C{YU{r!q;Giu@'
    'XKX0XEC0_gO%NMJQuwwREoR01|UH{O_9j8waaEWV1w=EOu7xBQ`@#x5SJ`NYLqi+DcsGH@^UeIC;?jaRc&-lKq2p'
    'QvFy!p&TN%S|5+Ov<l%-;o<`h)DpQEuPR!kQaS*E2pTsk!@oHyTRw#5bbtVl;%mQB0M84ekho4Dg1@F-k59{lq{O'
    'R~&H#R9ru_3yWia>>141brn%mh4maWUf0vNPwG`8BG{);<M(ishMru`p>H%-+=@Uq+@bFLCoI~0*qjZ&p17KnnQ&'
    '$QvX`ns!v_L^Kp+svqM##Wl%G$^EhOHB3QDm)7&{s+AJpL(*eB(4-~Rg$7E@aO5Hk^Wxq=+Sivc$jBcc*$@#A)FI'
    'p%}B_ryP_$l=Lo$)%YBYi<}GscjWlxG^rx=n5ql1OkCTAP@)y0)eEOO(xHW(ImQGXFhPi0r%f=&(Ne7q}&*qG*QZ'
    '#jwNPEx$kMxk5cY7@wV+BPm-4KIQ?;)@;FUd!{x4&F%n8$rYSL8PQU(4N&n(cU11jNo+&t?w3Fki?A_oFx#M<~O&'
    '4>!=tq~!AlDy!ZW#IOXeCYaTa7Pbi#p3otCbhh;<gVnmc+dVMaN}a`C)LdBmbUr*^Uo)$&GNyJ@1km>5?1el6%1='
    'H`*mP#wGWnOYS9?+*lZ6fIxpq-t|X+cj!Wry`8qXdJlT$B)?CvaxE^3`VI>>DNA?5$@(LP8_M7R{=JPWmEew>ue;'
    '*%CAs5z=<c}bF1gh%xnEpz`&@DxTymeg<i@$=>bc~+&%5KaXIygrC){z`eF^S3twW+aPOEj!9j6tTn&^ttw1^k?x'
    '#Bz(rTgT=Za7ax1`asjfCGU*AP@-TR;jwXnLFTe7Y71?Kp+qZ1OkCTAP~r+0%vlp=~rEVjk0t-yR!?>vp#>lkeVy'
    '%vF7Kx_`>fzA*>3jx%WL20|S&;z;Qv$Dy&z39Mzzdf3YsRjfsZoLT;L64S0k*F1w;TZh_{8%U<Ls$={ySHXXZ`o^'
    'ti4cWXg82Z1aKYw+0Q<@@4P{^c!>c5)#XaPyZ>U+q)(ewTjos{jYw-Q*hZHva|YJq`o{fj}S-=zpVHukARSeCNda'
    'QDtRRh41OeC39|=5NmHa@o=H40DCyy7AWLCE>`lE;{;&<kNd$nyqd(ps*xz_%|Z%w#csxKa>ijei1ufO^WfmU!hi'
    '{<(D_U3r>qL{NW08A34*?0iuKo?b#Se1kw%_F*CBSUH%h0oQ7zGRDLaS4y=PGu$h8qy)6NY;oLL|c=a{g;uGDCl7'
    '#wZO;R@{`l-__`<HHn=7$w+2E{*)L%D6o5%x84c;UYehSrL_;FPt293AY#-uRo)!r<@$l2>r(<!J(b;nbg}MoC34'
    'iS+1t>%3oBx9XrmCdeS@7iruLrs36@_S?pRJVTG$kPaEr5i>1zx>rVbTCOP5s<`*mEwif6(&p!Gh?{Ody2m}IwK%'
    'jp>@B6Y>Zwfa7q;6)H^LHN?jfzwCV@x!`-TKXDB2K^2gyHnGfg(;%dz|5P{Y@_cr|VO_7*78xNW|$YgBVWV>$C(8'
    '<8kXABa+Og`0BjF8Z1DZ2gG<{a{A1`R|%c$pw}Dtqj)16&tu8h3UNB3c?2Io&oq45)FX%RKk~JMHP7IW=K6T-<{k'
    'KIgCjC&*cJkMuKcjUuK(<~weM_;ccvj}#??7aey^~2aT+P$d}dj`?@7V;(cfo;o$;HCucAfIJImYiyxZ~B4>r%|J'
    '#qeJ7(TG-lKgw$+xkAHB5&e)oDsICKYkyX&j{Y{8IRX-AL3HtPCG$)*;RxsFK6!)l)vQxZ29}8sGyuqX3M?)MIgU'
    'Cr}nqhkEw$Caig)NzCA9eZ+}Et>Tgp){oQ134@4nE=eTTNKp+t4UQ&@p1K;>^Kbq{kD#k4F^>QH>asB6|uk@<jTE'
    '$^C01mjj$+hQ9C#U8WF1XdCtt%Bd5ZZCtF3&1Wl4O%p(Vp)g$P##xBP!K@!7<CPnB!zB4f)jS2`hc~yPd5&wY_Q%'
    'UwOsCByXGCI@oF`JowU@+t#(2b>kab!(;|Fe|3}9J=gVbN7eOl{G=fk=ECw}g7`kgDRXl1Ky#4VJk!3?bkMWU`uD'
    'kgEWwG2Uv;cVP2M}bt34Q}-_Np@D`s4)yW$?zU2*q$@+n!#75LL*T8>n5-+F+)?N!`Z!>rU)TuV;|?+;LMC9Zjls'
    'VGgs`59jHx@#)#L(hjZH5J$2u%G-?Tn*1c`^{3Ciu2g3*&Q_{$MvHamwPy8`!r425Z@~t2m}KA132Ko{s8;q@3B9'
    ';%XfbH;~gh%rDi2u+4tFqvYNpD7-V}h8yoQL4?Cx4y~6z!gPbq>IRTfrW^~IEfd#4!{T*i`??mpS-#kn=iTW%<;9'
    'ymqiO}7R#eCDPdpSkIdnM~Du*ho$_O$}b_bb`~SyYY%Z!@sOx^k=lT4zdbrO1*4XyLE59IJ}fu&yf}wGBGucucYR'
    'a0J>!yDn5p`7c68kKgo`V#Sev^IM7&;|)>dls#qT7+yP^=_ki3xZEsQC6{v`5Xe3t=<wplJ5SyAbdh_PjS#kFwQb'
    'dFOH+58p7pvrPS>9laB*Kh->OsrzsM%DQ&8{k!{KVK@8WA8ZAB>`9<_fpR9FYpZAj0z29|tiUYhWn24`F_$J+&$_'
    '*Fy6Jt?>S@%)+_PS;aD4Ab2Gz8HB3cfaDDzd*Y)&gN6*AIg|<%J+esaO_cXjoi$s<)|(i7AU#V26xK&L78ojG8>f'
    'Yq8@*#lPjB(vzsP#uT~_`C%olc^tgHxF6YpPnk()@A2-~tHiCge>){`5{_W(ol+5@`dp{XzsTyE^!1@QQf3O)0>m'
    'RWG@wajy5C{air!`3JX$-i#z(G0qYur<0c0j?6QLh9WB7v$8E4a1fIE)-Cn1VUQ3<8Z|dY=><Ot4?1STEw)AoD?~'
    '`IJGvPU`P<0JV&lc}cJ*vkCT%uk70>_O)#Cz5{j6uu}GIm1!u?l|B;8_W;2*`O6;lIf9*)yO%vpxHeL(7WI^(xqG'
    'dO;rDn^9P)OLa|^p|Y0Q6G)+ncW=h1KMi*0el9!{qrHHIdd6Inkv!|wRpFlSl&lnwMN)@sTTZg`&gqA0;OE;8O7='
    'L{(<3JWxjN2N}4PD1v+Zl^Mqm*u^b@M+gr>ilcPg%gh*r>;M7JuNBb*A>s!@PaYM-yv^u{4l<Xf}DuKqpC^Z!5J>'
    's<vL=2OCwd_1E`*)H&%fa(R%LX2o)HyADwCRRe||8D!f?d1x;ltWdRAthSYvojD@m}fIbj&-Pn45Ow7%-G#e#_+@'
    'GAF(HxbwHbcvV9k7+VCTd|;V1RfBtI5Z_a08gN0CQvb99a)LL;|O;<8v?JkLQz}c}ZONVcJIg@j#6n$Zg5P0`QSq'
    'y`qNqmFREsxYoq0nc2JV<#BK057%f~9kKw(J5Ty6{_vHY?v4yTdOESfWYE+i=VB{)bk0zXjMO|XugD>8&T`IwNKf'
    's#=tSOeIR|%!J&RqKjI|tox5|@mK!<zD!iHllWf<Ymiog<l6>DDs{F)RxbV(N->e*Un;zJG}*(Ofl?8D>xN&3sUE'
    'Ruh>*~+~_II^a@j`Zl(!};6Datuj+mHflDEEb$98kgwrI_FBcI>2(%#awm~hC3wY^u8umG+V+gHo1NhOph_Sk7b-'
    '*iE`wO`)zMt);~*9=w_C+Y#uZrso#;55hP{2r9Kl&DUJ-}SJ_`K5^+R8eA@Zm!PjZl1->_I#!Vxg@0g$+U5ZB6ua'
    'oO{Y&|FGCDPG=VJG3b2wDi;@Y0Z<cvsA}FNVz`+$_&k7jkvfUI$6Lsvq%Oy)TRiQG~ua&(ho<<idFp|CsnA5D0Xa'
    'L|UM@7E-J;Zq_f!ck@Ky9RYuomSRD8s$*wi`H#bm|93%h4EZh9=lRI7`%~5PNI43NF~&Mi=J6K`_6OJ>V1I!90rm'
    '&jA7Fog{Q&~yCY3Qa7{4aLLXR-btvF7m-zLkBX5mS-#klnyh!*YQ1WVZyP-`^VORIt!f|y7i_NdX>+%}Gj<@?G2_'
    'jAGmG%>o&!{qeGOGYEP)?uwwyy;0_VQ#Z-X)M~!1N#H)53oPL{s8*}><_R%!2SS%K!OE`h3C^QEhio*+IT8Z84lJ'
    'CkE^zz$o7v}!lRk0K!ejI4kn|rEY88=)vR}uM#09O)jynA$t+G2KG(?^KWBHz(o+%`uC8D><Vi2Rm10XcPwUyqcm'
    'eg%Bk)&l;w`BxWj5^=n|dfYVb3scSpNX~1MCm5KfwL~`vdF`us=W`kU;pz&~DG)IM}gX7C6`3y(F+d!2SUH1MCm5'
    'KfwL~`vdF`5GZ%4>N^K+rJi5)gw)%F7BH8zDW_%rzYFeS#=GLP6FwW+<~y^!h<o1J&R-RrfA=@Wf482A2&n&^%!N'
    'r?qtUKv{B8Jc{FTqW&NTUfp6|})XO3}z|9*V&jpu@Y;KjICeU{_yBSRh^@O=Y3yS_5{hvXYD&tWvp+XUlcqh-A{t'
    'f+t8ukb*XuQt~1dY91S!!V0oQAGHHi?Cm~NL*g}3;KJzKC%2hvA={%7H)(Ht8Oh<Yed{=E0-X01GAnO7Wn_o1(gU'
    'J0yjs>%mBP#nWNQp@gMv6+`IVWv1E9k#rHdWkd96^<G=dwu!w9rI+^Sos+oGs^z8Exan{P?Nbu+B%!s5ahr5hF^Y'
    '{WK-fuXLDQc3};BhhW0dD|@^dfN%>$&JlR&M=;*4rnZi&DDCf(s<0y`h#7=G)xH#q^7`9AE9Xb7Z7_8&xu5>1Bu8'
    'xWm>bq`tf#wPG}Q5RQ`JO~#lXTe$(ct!!?<*B7na=cM%}8&F^4`g`)aDjbc!z%#iyvKZeli#dz&y;))gFcBw$QYG'
    '9wuFNBgfizRbWt;Iej!5kf8lJqk-2V84qzsbVAD2nWa)teYk865eQ`jGP-lVR|{@B3uS2Ft}8Qo{A(*AhJV$+K4k'
    'M}%R9o6=S;l*mU+WyEcmUDF!><<tK1iHKOiWPO4R*UaG-5oc~CAZ8acitUWMt8?O=8{|BlEd?2Uus%3{<t<Tm&5U'
    '}{don~&7A0#ax?IV*;m2s#hWKJITwO`(oI%yZJ23p8HY`itO5#d3;O7wCg=Q;m{kY~_aHm`j<&tFZcgi?w1Qj38c'
    't%)>n2M1Lc!f<F?q!tsX}!W9IiqMJ_-(3p-q~cBUNaUm8&f>Egw>Ff-2<T{&2^IXS?IN>F&7cF1bDKxR4BY99cl@'
    'K@tS{H;I{imvOiXozvu;FRnt%72Jc4X&HNYLAN4RsJ?>3RVdk4&XFp#U6XU93QbdR!<@_XT#ocYMHSq6=SnL7c|j'
    'l$2!vFq+st);#-?W6irKz!cvTVA=G!#uu7WANMVcBroW1LA&VoRi?t=54$L*~h+#KB%_pI)UtD5YN+sb@&YWxEQe'
    'Ix6S+@9s&Ix&tEMpG{Z6+X9fX2zvH#HP2pVBu<*UpK@y__9o;PovmR(wA@UNNXp<%-DT$$M)tzlsrbW`N|qfIl91'
    '^BzH#_<L>8q(|v#Pn9t_M3b}v&s+1cm<?5-|(hSiZD~gcW%_!IJu6&>Q`-b0@=XINZ=KJ_c-bT?WhJ$jBIxb1_c3'
    'J)!NI7#ZVzTD0ejJehz!SP|wao;2N4ITK5~!xx9z0?FB<yWb`(oL7(DH(?y-FKNGqKz!+4lBt8`J)s7+XKow^-fb'
    'TQdLu?Wx=L*<7?#_8;@zahf(J%^j!J{K*}sX%BsuZlTUj5_$0~++!AAMBK6P*~*H}+OvdnHcZ{Ahg{47KXcLh8}2'
    '@vx%k3Nc1NE?p2e|@5G97J*jmERcP{B$GdwVA^i7)$YnL_pYEgIGKFt-E)y5TfyRYOqYdrOz-KTG7CY)I_!o_O?{'
    'rB=+Ox{Cz1xZd398|c#)rR4{+)GvW?uOTppH_j5(SGK%`&D37G?a-^7M2odXTwJ2q4H4FQooTG-)*#P!$3FSTyx`'
    'G<}+~r8_uaw!2K5-lq=w%z5jo6AQ0&8i>z43Hj-jJ$s~GJDOMQ=!#q@6Zkpqrm)k<+KMo%Q{xU$0;a&B!74a$?8S'
    '@+2FDr5+!Z=Hi{#f38pU{7R{Q>p|a8Ry*gMI<*4-hC<s{9Sz=0Sz4!i)!^>^?%we>+=mQ=5o5e9G~=!HLM~sl><7'
    'x=YpsFReOS|31w<@{Eq@#Tpw?LeZ|&-JF%0R)*p6TUm7FHNjY``PQ)vQq_o__=))iZH=YG*<gQw{Q=__us^{501o'
    'UAus=W`5Kl`{wTbrpO+0)Q1I{&f4+#1Xus^{501nC(aL_M+{Q&~yGI@W5t5<v#1vwFeNAoGb!PrsA7pi-A#4bBVs'
    '=x=(@=SVT6<86u^S>OS0weY#Bg9t)=0|5}r{`r8l&dPhDLJaXE?uAi2OMy~<#G<%`@7UHfIy(TCE@`*u7woqjQu2'
    'Dsv*UqZX@iE(o!r4JNw^RSpMTa#+$Vihu@z@*gUzz9qoSPW&Bk|j)WP<*)yNaqc0Tt53oOg0}eRgpkDy{0|d&ADs'
    'zQav)3e8=n<wq4iI8~o7j4rE{s%&9dE52h|a0U-N(l!pyMW!y|gMQ5Ks&^Zr`JZRdl<v=kI(d(B6KIAGs!m;c>VE'
    'zI@4WA-UFJtyDBld#nhi2`!DNP4mG10Q&>RFTeo@9M~UVe}F(Bp0M|9!@YG2T!+d6=bF101pNotAHV?z9B|Mtfc*'
    'gifk6Kwt*4D=R#NPV=+DQtFQDLvGIk6v=!VNt^2{(-oUY&M?T*v+nO^QV+j^9k)1P(8rB>i^lQb<Ps`*>T7;vqC^'
    'T_MjJ0@61%xPMk1j>CX<Fqc6O9+&4+Byn$mvZeX_koo2y-gB-lX4^uvaV<PNx4_(jjKwz?v(2)<?2#ynCFuBQVs7'
    'C#jH!bLN1VAzAxo88lY@}oJ%5?OXVCbGt2MJ_hndqpD_P|=s%k+<svCJQOcRY5TBNEJt@~k%1xwPB`LRtF1Y*3IK'
    'McOxKCQ1JJQ?El5!g;*ICAOrP!SS30F6fa*M_7fj8T})DW~+<(j^EoGBCEyKDba%DLl)x@%8AaMwOhch`SB;;KKI'
    'QTVU-Z&}H)C%1n-u0sJBM1Vm5KSeD(pLS_^YkbwlQ?JT!uy(jZ+fvcEG-AebDrW`S?<E@1cs=1bvN+NERx{OnW24'
    '|lJ>B3RPOM}Wr|p>Q<cy!QyJYDp2@F@)$-Pa|OPi6cmvElev;1)ZjS@%TuiO+Qm?HWG|6OeAq2z=;!@Tp@`!|P}m'
    'axNvaALHRV+YGp?gN)xY=ApX@9mOX=aS1R>5kK1amk%<$>~j9a!Xut3HekUK92Q-iaUAH_}@<|?)Y2i{}okS1@!+'
    'nRUD4qe9=e6J%Rpcui`>b&0VSD@D|V1!YZyY`fG%W(~g^_n3Nlf{;Z|qA~UI}Bjxb$`li2%!)DtfD$egZF{9<2HW'
    'L|@#Z}yW#PgDZ)Akw{e=50Q=&G@b3q=vS7pXYx?mhlF71s(~Zc}kyXV~f|=Cs!=6<W-d&T_|Tn_O~_y5s_Hx#P6C'
    'F1dOxIqjfJZlp^tEX^IKedUtt;N(Cc&|MPvzmh9g`@ah9^PgQ;lQV9gSUs_xm@2s-iRlgwJuZmMI6Tp24elz(p?o'
    '-N)Uiz+nAc~#xv7K)?c7WJsjf@52ro4<1T$yfS-EY(nYeZ=*2+vj_(-XIwPz&L>)kB(BTfDW6_f_170g7niKOF(<'
    'Ni3Q4{siG6iqaJIqoJ&asD)IBS|~!#TiPpAW8noj+tvsD?oGV4U%-fb0vYj@n(`TlyW6C&XD6KNtr`mr=|GVrr(('
    'v>BB77iE<5b%I_rMN0zHixqdi>#whyCQo1Sk0#2bhFo)%WDK{0@Y?|=4rR0+Fr2ZjFC?Yr;mqL;kq3aSHo7SU5+X'
    '`11%2~anTGv0s-<M$fKDWQm?em)X_r5g$%=huB{C)7>Z!O3B{efFv%!P<X_VO3cFMpAa@?3&fo?~h6BCj7?NJ>Dm'
    'b4f(YPjl<d|F-_RZV!sn_T?B^TkOqiUzqmlYh>#;i08FeZ2NbhXUULS@0so29!nXDcGsS|Zl8tieNlV=@j18uXyE'
    'LRKH)5sH0AYgBOLu(l$-wY@Z;i0#swc;wEJ30=ABbNyjmZIk|5Au68OKeE0_Df0{a8(53oPL{s8*}><_R%!2SS%@'
    '}j{1m17|#Px!wA`vdF`us^{50Q&>%53oPL{s4h;kKp_V=V|Y$^B-V;fc*jX2iPBAe}Me~_6OJ>AW&`*_|?7l{qKV'
    'P0rm&jA7Fog{Q>p|*dJhjfIuJ+2=o{A_E=|H*=>&X%eQs?WqVq=bNbmb4oufyvZh6O&Nf-Ww^5h-BAZtJJQ_-sab'
    'Om(`~@=RqG0$1+KG=LX%)=0k<Nds>!VRh#f#iQb<}z5;I`5wdOgva1u^u4QJPlyGQFDM%qt2q*m!5&n_!&4fWYR('
    'S;u(jW$qg;ea42pC@2c0Pg}4t!ZK6p6ob7WDO?T214PR}>?HGjBi&(Q(1wXX2gU~(EKUM4*r+O3gdl^xU@0#nMc8'
    'Ot`5Vc{@XM;m0}G#YzWNGg!Kdzvz2?g5+vUn;BDTz4;?&=ZmDt2toMLIT2Nx<>6KCkUqan(vTrrNJ`#Ze$D!Lpr<'
    'RB0Tlv|b09X9-2b7x|KUJHJ{l9YDk_q8w8N4vxBOVP8Y2Uw}dx$Ml(u>roC7I4^@+NsH6PsQ81{i9X1_t9S$%(*!'
    '4`Of*K_;=78YIX<n+ZO0z%`~xRCD?xRt`$?oSR&K2u9bY<Any3<P_I%UHD{!l3Co!*`I@GFkJdk$n2z`Rn^+hP1#'
    'yC8{S&+a-qSR(CrK(s{Y9qarySkFc#clJtucRK4?6XC$Fcq~gn8a~NMcQz^e`D1u&+9ra06*lar~~gTP7dL{=?~X'
    ';D-~&STmZeo_tH^SkZO06e~yOvwtHAKNy#O`)x0-59Y3tgpZBOspulxF^EgEOjAFAJlyItblXIAh|aDQ1>s-`djs'
    'snG~8n2e1QH;u43K>9||kZS}(1$>1fj$uOr8tV9b|LY*20UV%9&F*}rd`_9?Dt9@{R#_I-s-j>JqcHptT4UWhnz0'
    'b?fey{oS=Mz`6v<qj{(;U~OV%=*tK;e3v>y(i>D+g$&69&<;n7_MOE`bRI!ZLnaMu|E=X{o{VjEw*qSaq4q4C7f3'
    '*|3gaoha}|+Nom1gXJ~W$3rV6bzYCZ}Evp%AzG>YzeVH7h+zgu5$Fc0EX_Flfp=rBF(slDpqPY}!koart#oM!7(c'
    'II#0{!a;;~lxDY5r^lv#)<dn_o9m(wORIZ`2`Mf|))rBd<%a80vh+dy@o<GdEt3$1mdH@158UW|`z1DYIU$r`xa{'
    'tYe(8Z#+rcJ=s=fAxj?YXX|taC+Iiuf?A=T^LL-VlNon%%|y<Y@bbTOa@V(e!6*pyFBSOTl`B{K-<6BD%+uIZGrn'
    '4F;D7@TxLm@0qu{FJlobjN$F0#8k%aeHF2u1bYnsDG@yOi%My5eVI(962)RgY}q1_s_*x8Ao3633}=0?1MOn-dZd'
    '3up9o?I$yAAjd)2u(A$BJCp`;LuE(W-eKDVMg>*>WBK5?6Z}b^)Y{o?|F$W@Ra6i7U_H-!};tNay_g=_gDe;fvu0'
    'LeT|DPbez?U{Lx9>9>VK3ANtXHH1+b7FhK_!-ha;4lX2-;$ya|{IliH{1_FWp{{sKJcI9&ayI_9+2OMx<e}Me~_6'
    'OJ>AW&Wu_}_Ibq~r<zyI_9+2OMx<e}Me~_6OJ>AW&`*__ezC{a=Co0UU6^f&BsY2iPBAe}F)FSg`-fuDrYdY7%Wf'
    'hdb7%s4Jg@FfH^J?J4|h+!K})yRXu#!B))sPx8SX&nwJNyN(mq7TG+3ImNhsg<kh%F)w<0GY&<e$w&Qs9%c{fJxu'
    '@k>o{dBP3gjL)S>0AP%;9!?idQ-@HjS4N-xs1)-;WJq>L><(}L;A=&dBFG1ryS3pACSkfgoh4fa%3Aa%gA1|`%H_'
    ')>bFrq{9#;jUYk{sO35oi9C?RkKCTl;|nmERS0q>O}c!u#NLS&m0DiqJx}Kj&eARu7j4*{zJQTXC*cZ<%cEc$?g%'
    'Hg8dXduusRK7Xvt)-x<ev<5_M^8SEG``ExnnQ#Njxpy~~@V(0t?oX-j2fX=GNSnQ0Skn=vqaDyd{gXs)bKP5W>=Y'
    '5nnL>9NYImKAs?)Igbq5%h7p65Uy5D3H-^-$9uI4<W=M$*-zTNjRL9;D!ilGpU~b;EfEcRk#3y1vBE9jAZm<&Lv>'
    'nw4=W<plZXA6C8piWKhDqcfkQ_EPSauk3w<U*x#Z;k~yPI3^d5Gv}$7%F6*Y61T`XYXAN!=XR3BBXaHlbu}dCs1x'
    'EHQm&}!<4DR4b@;2|as^DQs=b6OzKvpufqc%dP}SZaGf`p}yDNX`mE3WY-IeF>y<KsOUDdY}-CWe)q*Xnn?LlVp^'
    '<!HXjcFDL?e4w8fj}V8J*t{dFWGlGHsj9O{hvKmQ3M4&b->aMZfwTI?*CKK_aZ&X3m~=Kaek&tCbyqnV8e<h3OaO'
    '~L&ABY;la-qteEK!@yzflA^wr=KPJD!;z4+TnHgj?p&{;4EFstEwEoRPW(L^DrWzAC?Oij?AGt}5^Wu$cO*fia6+'
    'F4khVo0d?<6TO=9E1tN2UkZvM8Y^<GPXwBNk}m-Vo%pjWd^tPRxJ0b72uzm;Bm2O>A%1$`NP5Uph*ag)2@LO`T`5'
    'EL?y5=S)q`k%xUo!4Y@SZz(wJX<EM)D(7hKt@e>}W|+kl0W!|t9INguD$D2h&3AjTv4jh~%$U|=#GKYLnJF&c1dG'
    'd68y4JGNXTim&obaI0YXj-V^0Xq6?2-;9Fxm_K+I{{DANn<8X3pfWm!dKoMt*~?l0y1&f1y@0cZLH{!GrbCdn)19'
    '9;pP=j0l_Y}#Q9Y$hDbwa|Bvn6RyB^VF5c>H4N2m4buo&7wU;T0TB!*9y%QmloxY8?CwGW@v7>#HaY>-lxYF#;tJ'
    'Dg7V_tI&#?OM{+vrb9?6YD+FT~5D4_YQjZMf<_V;Jcu#3^bp<#CH+x5e6(HGqvrzRL$MJSK@q_~^+$paDanHQZUj'
    '@z+jwP+~`h;tqIOVev*$pLBpdSjDvE4@n25fT7uI3U&c6n{drsG%Q(z24TA6YlEFVClR{_cw|+B@E?Ee|^!%FpMU'
    'dC_Ivar)Kj?l}Eis5_1g*)HW$irBf^+olieRK0k?0H4t6gEv#q;>7#CH6topfAJtPoY<zw_Elt=%TLbH{fIT)RX_'
    'a^mz*PTVAR0bYk$9zWS=3hT2zI{9~0!@hYaOCI$oI5Ohe-LrDP%EW3$?RC&8HIsDZjGF662^t|yat^1U8)istjw^'
    '6?ICDWy_}m^I=p2WQUEJja_GS?ZV_k7FCYpX{8U?p(;qX=f7coImB*xbS_P&r8>o{$Luy{Z7tY$ufgcOycC98Cv`'
    '(u3Wya^YVERa|U8$MgRO&MjYY3kAiXT-kbJ1cRAU~g&cHZXWn*lTB*x+>{{h<98N3olNCE0rExjU_ZcSq<*kYSnt'
    ';oxO0B1CIdv;D;n<4DLtr2T0)hT<1(6ndw>(J7Lb4^JpcEsW(a~@z7KKBXos5)X{`(LXBR`Ns;!a(zAjdkP5#5by'
    'a;yY8zHz&@!m>aIE8-ti<S>famE25$H8<?N)V2yN+&J2q(KR=gC9prh{s8*}><_R%!2SS%Kp^3CJ83Z=lXJ8?xFt'
    '0eYMUF5R=c^Y;?DT4sfs(}yRN98D7`bj>&^hJOP1`;=&mO{&O=5>?u_mli*|v}65bizHLWRb{J#_3c@n#0Leqlx^'
    '6y;BxGUpqI^wJ<x554Z`vdF`us^{50Q&>%4-g2Hr&MLh`Ls(b$f?^#aXBi(!MflUc{MsZ(A)EO5hoUvM7zU5p6ex'
    '^ewMS{=yRso-PEwafON}ASgL#}oW9{*C)eI^^Hi?HtIR+qo8t)0<2x$bGrTRmfirS3<ymYwh5I|~=xRI9#o6qf+}'
    'c1oya@-A6ZSptb8i<Hus^{50Q&>%53oPL{s4hMpuZr9v+{3`#{%{T*dJhjfc*jX2iPAV5a{nywRaB0ro^loEI*<g'
    '_!04Abh@<!E4Yhs=^~EB;j`Z2_E5O@-xe+Dir}QTpAi)P=F>$3>Z5-4MLyTcINpGUJ9Fqz+Np`2&&J^g_jokQ;*W'
    '6Vdbs-d;v2gUB^g&jeBZIS7rZ9EukjqWkiV^2lIQR<h6y)@6!EY7l`LP(SQ7Gl)_gX&dv997UtE=goa~)P%GH@Ri'
    'gNni!P{vURuA^ieN_nT53oPL{s8*}><_R%Kp+qZ1o|JS<dB8CucTz$I=HYi+GJLF!uC@MiKn)WWqbmJJ^vfqqt@2'
    '6BHx-u$gCjeFbw&UY*5S+nQwO>pVMPDTCYDBa1J$d$$jOLOAeyk`S&}N4Jg#sv^w-pEBlw3j{k0hx!sT8t$AK_sP'
    '&660L8=VSBwv*_G3oxWE6yA3U;tQF{5>uL%v`mn6*2rW?`;4`W@(G)deRv8FM(8B?{S^=H$pY62B}wQa3&OHXQfu'
    'AD5FwIfw%|dp-HPJg3nysfZ1#H)1%j2tBI8&^qQC-4)kDcgL-B#|5U6H{Y*JPBxLLsr>egZHYgahb)x!miNX8$I-'
    'luU%-0DNFNJ_IVZu(H;PW@y@%4@z?Wo(ZHb+mYBkr%fIF5M^A`7n=^TyHccG-pG%H@GSj98*bN<^4r?)WD%!uik-'
    'EkRtE>ZVzC2{(lVa5C^|A)!j>?Mst^VJ-4Jm*gjr=3*44uc!*NWQ@33fOIm{ngTWoYvYJraZlleZmWK+GA<#>`Z@'
    '+iA8Vd2yyt>z?|Q@*v?34Z$4e}492BQ%jtVBrKZHJev-7ZKHZiciO-BZ@y&A)(0zeGAP^|;tIF`t_g+cLynSZp?E'
    'XbWP~gzRY=@e$wzoGA>vGmH2Kb|wW39w@4tEey#fJHRue;(t(_L}Hbyr+jCbG=cS*=P31eNRh;dLkX3A55d1J(PF'
    'ylLY~n(=H#v+1GBtQ=kJa10Q~S_1UYkUX)4WiPcTjV&-s`tm!{+t!hCG$=`ex0IVpxS!;O?i<1_lXKJ-_(;xeCER'
    ';*jxJ!&m2=<PZDN7w8b-97qiZL3yrf(g`aWt%xk%mR1JebC>!DI^s(nmt+C`S~7exzJHoKOi1<kY74`zXDIvrPg_'
    'da8<N0m~U1?=f9O9d3H+UcW<occS?UH#R(zIVrI!CTyMTHvSdI8Ez!*|}c(81Iee8+F1$?HU>&dNZ{~e}SffeqAs'
    'mO3{`VzQjLM>5wP<dUg{Rpy$?yz17@E&o6AJ=DKrjV5dGdEPLU{5AB}Zs(etPhRaaS2)Xu_T>i!-_oO@Sh@UHNS8'
    '?%U2DP2={ozX~>9M~p>fi-q5m2sEHGF28jrPk`o!c1frvMw^<@R`m`&+F!p6>irwRfmp8gI)BM_ut2k1opg5lubC'
    'mQ;aHp#E@&@~AkTx|*uY@R!jnRx2flW{fWvQG)Lp;JVz{zJxu#VCSX8tmLb|E*~X!bNs-!mI=b6Pjd@IxYH0g$=R'
    'G%Cx+9PXTNU89=zjbTa{KWRx~WJY?t!gFPu9~tHB#*>q)a)hWiz&Gs@gM!|bavH-zjb4jz2op<r|8nCVQI`HzWfY'
    '$m)oy-8>G-4IJCiHuEZ(0t_&=1FeTncx`~?nRP39w&T35(Zef{&wSlu0y1!rlzH*XJlk%W@Rza)3#$SK`~8z3=h6'
    'k)7deIi%@Y_5LZgYU9}I|<y=9RoEM$;ZYZy1bU9Gb?ycpTsuE*C;Z2XO_@ow>a=x*gEpkMDqo1IE&!$=Mto<tnp4'
    'La1R*KnPEMP<Cm6_mKk}x^#LuL_j>40kCe)-GRe|-KCRJIr{|2fI)U^uN_qB~A&li-fi>YaASY58ZR@u+=+9o|mL'
    'OtaZ3*}uKXxBtEBk6E_oQetM>jWhdKyxOA}jE6uV&_Af<r1PyP#c;T7{pNjg^9DP)imNNdg7ChI1VvAaBjF~ulw-'
    'Z|(656WD~E=88QtZW-yUQ#s0=T(izfR?1=a!eej^lEaTGIroC5RSW?)ltXJG>S1MCm5KfwL~`vdF`5C{YkXbI=EC'
    'r5PUFS~^d3pMw~o(XYrSH+$2T~ifz#&<m&by9w3bl1zJw2C)ncSd(j^q5XfQpI;hcg=d(xagZLyfeD%^Z4o&(Vfv'
    '<=g{j#{5zw&t}z#8d^aj{8|)9TKfwL~`vdF`us^{50D(YxN5q|o=kGxLcVFOKbN@nt{Q>p|*dJhjfc*jX2M7cLS='
    '56##e7Nu_QTFPlB+AgA$Z_$G*|&XFYc498s~OQkNeF#<yGJe+;l6Q*5dP@GgX`yJ}%rz1^TQ+yVgpmKtJSlXS?#?'
    'Nx&upoSZkyUomHg8VDStvcLfc-2ac8rZDt|{YyCz2n5P~3L*|ocIT6FWRmqjK`BO@!5<BmVo`W9`edXO^WTT(FH6'
    'fGVkUMqsW<@N30X=vs>!hu=!E|5+6v1e8lAd-P?5vPVQF$R1=ifK_fp#`u<%R936`#TwY-7-0pmpAfCKvj><<tK^'
    'zW1(iJ0`5oTK5OZb?mr+U5fJYBzUP+<6jv{6l<aeAgBA6Qy^?cikDFb;*+58Qt}yXKTv{$(_+%V=EaKvxIj>cTH='
    'GuDrh!-5K4rKe-Iv%fE9i<F2on2@z*q?3e)i1MCmrfCKvj><<tK1j;MIA)bv`Wn|}XS>RlAAh0XI{s0a*us^{50D'
    '(ZDzbMY$rCKD4@l|pv(eKecyd#uPkY@2`BhXIx1kYZgXwFR*5pa&C)m)r{{^kTD3Z(J0-=YD-vUvftqHfz7=J+{$'
    'Z^I8~v($;_^9!<}fq959dwoYiB#y!(k1${MG9U74wIbE>)8fi-s3PO(4O2)2x}BV(B2~%nKQO$|MDi~talUZ1ajT'
    'E&Y1E^yu4guOa!vH?=*RJa$CBh&={Zj9vEj_-w{Ff8(R_WxKd|f&-6a^u58zUI#FvL}rKew6H=lgr1`gGDL$<Sve'
    '#B0NEa9JFwhecRiz0Jd{VazIqb*-x4<mVR7Y`zOGu(@k!3OilVdi1`<Qa{^{EK&-d4;BAm~W4+Zt8!MGgQ8VuQ;;'
    'C>pV%i&Kx{`mn71Vg!Q;<n*&MW4w6F8vtMG1F#XrpND2)G{{fq&^f-4mIY&=oaqEv;Y8DMQO$yCflF-!hj&70UN;'
    'u&<Nw5a(yI@bI$E<5wIGRi%%3UG}jVv6k_hftNA0)wYtSr<q;@(UW#;{y{k{U}=Xf(s6EJxgDm@yrPl9bDAOSasR'
    '`(}`GW;batecT>2jTZWiCigr&yl$4iiOvF3`X)(QY5v=YBwCzt)|8@wGj+DrdfRN4KWSPxq}prr+t{Cy^P`1_z*8'
    '$(-ch|20cSry*)5%mIfM6g<T%2}=AGuNxWkTJ?9A^%dhE6|EMvu6sVj|InbS~a-Z{B$N^@@w1OkC_tEwzHpLS^la'
    'lg|jE=Rn<aTjcWtVaF35sZTqMK6kWB5{K}*Pd}+DrXG+In$PIYFJ=^r(r+f#PX%MYVMJH+pXnFyc*B6_nc_Jo}pc'
    'KSx*nRfpQ;np4M~N<P`47aV*E}K^$&&PHvGQVZjLpk`wkl@AK~{Hoo0HJ$fho>cJ1&It;U=BxlEI+Cyf@-d%OX+='
    'wjZ{U)^(ab2ypMN)Y_H|2`0#H{0UXB<WA0WRm%91-`$1HpwlAFw-Ra&RcJONeRS6ykEbaNc(3xXEI<j>Lqc)uk_w'
    'yF+8|9^#F#D7Tp=t>F!VDQCt5|AKktPHrl<XgCwcyX3|J2OMyA4g>;${&A66sO)wMkT{{b7O4P<^Ty4p`4sGgN}r'
    '(mjd<EFvx$NuPN>coR)E+EmHxfz&e#bRjoBd-c0!d_R|N+DgjQ1WslWopxextw<r^%728d7O%HkFA<kG#@Q!?+IT'
    '0gagxI8?tDw7$PUmC~HoPv&ph)cX<_f<}CjDTql%ctr!uC*}V28M|obJ|Mn4<hp;%QCm#+x*~%4#k42jK69+kM&~'
    ';D1C-(?1(gS4+V#qTu<FGZZAqF*Z<`Fx|62K+s8R!96}v@4mSU-_i>U)R}wo=f4S_!*A$XUCkI~TxE9v52kF8KS+'
    'Drgv2tQqNYsL?g;VId#?zL(KWq0cOpQfr`PgmGL7MrkEgW4=Al3dBf4zcQTrau&zLEds?{oXSH2-GG^N+rdt@8I_'
    'zTX18_j}M;UYOR$`xr@_?ks<Qn&qz-XTicO&yz?}kqeyiY}St~oHY@@@ahLs$n`btiUw64Yfd*D_hU0*t+4)D9fg'
    'F8+JlMi+Lr~6hNuc}Mf6?H5_&<$B**1${Py(0)t2^je12ieTWrWDzurjAN;tjo&6cqK0Rn;kQTdT^&SCi={yox)M'
    '@TUo>svolQi?_59S#@DNilD{tLkcH`9r*h_Z`%bV@**jeW#ur3qw6%Vq-<ttw!6SS}U*@P|w~`fz>nm_RO9NEWgo'
    'vWj&Tlixb!%V1I!90rm&jA7FogKp+s`?g0N+g1dtME3iMn{s8*}><_R%!2SS%K%iVLI48n++JEIa5wJhN{s8*}><'
    '_R%!2SS%K%l={;C~nV^n(2X_6OJ>V1I!90rm$71OkCTciYI!Q9<Bx0hb#Ur>ivVU&%o|2ku^~=O7RW1OmBNiI-eq'
    '`3~yK0@Rq#kDe1X;r8V4K}+^rOUbx-a?K><Ump1~J7smMU;As;S^kBo1+|yapEs+pzhtgGTu1DDl#<kd!O9#Kjs7'
    'y&Gyi2`grmfP>`ng=Vq@7y_)LgxX0bMA`i3r%{^f?#)e1eC_2lp?UH=$`5{l3m6wI_T^L5j3%1HADcg%ny%ShU3U'
    'zE@ygZYItTm0f}zIIDrXP;y-;hNAKMT4cl{gx-D6V+o#N;JCu)HbzDwjVCFu78X|7l{<SA9K-`^?;V>8M1raAj#b'
    '^_b0o)Q2GX0A=+bq!Izo!iXi+9YrVBiB$<S6iDlM5@QXeX%B+99h&ejB-U54>(BnPvk?8uzaQpA;)Cx^hXB)qqu7'
    '3=&mx4ELu)dh-6ntCEt)is`PxTwK=fSkP5O*DKTJ!%;w0hN$mO!#N(bs~-SGE1?o#g$NVzF#I8ShQ!^IxasZ!g1<'
    '&=!x<l<JgwnOUyDD*zGnCG9oKeOLnJa2Tax=K3J(vg2*09(D-}tn{+E{;`)`Vi+i_zG<}?tXYbXdzQg!+!kWf*e2'
    '+bh{|SHSHtiE<c1&ZSWH_63&O?c*AT76YPLO%857JlLEdwW)N{p*_W6l<doCnq#-Cb0rI};NWpqvfx>eOhAJHo%t'
    '+}htq;W6kcR?T!QI8CccYmJx;jjzI)fM0n-0d9=R)ECy??TmY9LEV%z!i4=d#Ah##IAq$`K!QroP7;E5!b(+l;d>'
    'd`uAoD6^LE`ZdacDz^;EM=gHznJIN}tB;bGp4!1NexqNbtMo28l<>>w$8aB2Sm!ku18WeLpm!knI-y|vfxE!@2es'
    '>z|EVs}$Sy1depL>R;roUOqzvw`A#Lsf2vc-#5ET?U?e|nzJwXkz9@Hx%4sdS&Ofb(C&V#kYV0#5UvluQl$#rZWM'
    'r)kAUu0NBIel2=ZMHmNxK%jqKG_v$=d4vkm0UVGo7nA+BN5iF96pjIVQXX8yA9q?YrR5JX6K{{LAjdkPPUl87#X<'
    'p^e7s#-VOgNr+4u()IXoGeam^H1bHm<CZL7e-jUF|lYaT6bFrEYsIIus!{s8*}1Ooj_1^#zMcLo2uV1EDy9M~UVe'
    '}Me~0)asPfWTiB__YH2132Ko{s8*}><<tK1iGt&{Z|gb{;R)7fz++(=hhDjrCZM<&motI)+0rOZKJ6g^!iZ?x1J>'
    'bW*_COVX?3b`?=$%=-{O=!_m~VFf!Il;5kaA$C$sSNsCF+cIFMz+nR@m8qkBNrD)pIB<%`wB<OeBF<f?p#KyE4<?'
    'x+alnyc3;vbf3Pkojo?6KtO3VJjeCq(moJhi4L-_dm>K1QaeSQ`$n(*#7m1V)g{6}D3I3i;pz<MJnR+0MpAlIOeT'
    '$*vM)e0jm)Wpp+B4K$zLaunAt<fBhC#xrguXFEW~xbcDJuy!Y>{L}N?z08C9LwJoN`I7Hw!A&Z_t5BQik!kXEpK&'
    'W5Jzh>dHMHV#0XJ=9=yP8Dnmo(KiO!<kXFFvbC2AUev2fACCge$>-w3Nhm>5oaLWsCc`VYe`k+fY8FkDkvBX@{mr'
    '=*SH2%#fiTiZxZz-P((z=EVAZ<05{L&<IX$vLu7;ZBegYft?QOEEuM^5nhcTzkvS>qg4;F^$5(e8g@Hp7wLc2~M2'
    'axmB7g?o1I^+_`d^3oiSMLYj)pirqiGx&{LMOVrIV%mEI#u;+d_pPY8>r?+a$IG<O{LBfX0LM~)8+g2w%BH(;BTH'
    'F52UVLtXy~|0i!Q(C`JElc<@Hn2j2)M(2B17seFehFQ^SK{TkKfC5>0g7#oh45G-{1@b8II<I|5fLWP41j&<h<_i'
    'SH*Den?JfHO|0wWIy0sQb*eSM$(3btm6|kD9bB3>Z!-LS2X~P>;9uh4POuy;6Du9uVV0v-!&;Z$x60-3bKW<V`@9'
    'RB`Nzh2-R4+y;Jy#-nr`<&Vsf*2@3*CsJ8S+x8f8Q;#3_H&JC#3mY;}xRPEY5v%-5K+5%&7AD$q#kX?k>P%&%{Ud'
    'KC+<Ge1jr-d0|J2XMF~C$`U<*S^fNV@cJy?NGo^HeY?+1?^vfXcmj>B5Y56&2^CMugl8X=VC8!xt5V~VbjD4&~N>'
    '_I1nh$2=-OM{wrAjfb|bp|A6%mSpR_a4_N<z^^bc{{|Bpc1K<'
))


def now_iso() -> str:
    return datetime.now(UTC).isoformat().replace("+00:00", "Z")


def sha256_text(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _expired_iso(value: object) -> bool:
    """Return True when an ISO timestamp is now or in the past. None never expires."""
    if not isinstance(value, str):
        return False
    try:
        moment = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return True
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=UTC)
    return moment <= datetime.now(UTC)


def new_id() -> str:
    return "".join(secrets.choice(DRAFT_ID_ALPHABET) for _ in range(12))


def title_from_html(doc: str, filename: str | None = None) -> str:
    m = TITLE_RE.search(doc)
    if m:
        text = re.sub(r"\s+", " ", html.unescape(re.sub(r"<[^>]*>", "", m.group(1)))).strip()
        if text:
            return text[:140]
    return (filename or "Tailplan Draft")[:140]


SOURCE_ROUTE_RE = re.compile(r"/api/drafts/([a-z0-9][a-z0-9-]{1,39})/source")
PREVIEW_ROUTE_RE = re.compile(
    r"/d/([a-z0-9][a-z0-9-]{1,39})(?:/v/([1-9][0-9]{0,8}))?/(share|preview\.png)/?"
)


def preview_text(text: str) -> str:
    """Keep supported Latin glyphs; normalize typography, not document metadata."""
    text = text.translate(str.maketrans({"’": "'", "‘": "'", "“": '"', "”": '"',
                                      "–": "-", "—": "-", "…": "...", "\u00a0": " "}))
    text = unicodedata.normalize("NFC", text)
    return " ".join("".join(c if 32 <= ord(c) <= 126 or 160 <= ord(c) <= 255
                            else "?" for c in text[:280]).split())


@lru_cache(maxsize=32)
def preview_png(title: str, host: str) -> bytes:
    """Render a bounded, deterministic card without browsers or runtime dependencies."""
    width, height = 1200, 630
    pixels = bytearray([252]) * (width * height)

    def rect(x, y, w, h, shade):
        row = bytes([shade]) * w
        for yy in range(y, y + h):
            pixels[yy * width + x:yy * width + x + w] = row

    def text(value, x, y, size):
        gw, gh = 40 * size // 64, 80 * size // 64
        for char in value:
            code = ord(char)
            index = code - 32 if code < 127 else code - 160 + 95
            for dy in range(gh):
                yy = y + dy
                if yy >= height:
                    break
                source = index * 3200 + (dy * 64 // size) * 40
                for dx in range(gw):
                    xx = x + dx
                    if xx >= width:
                        break
                    alpha = PREVIEW_FONT[source + dx * 64 // size]
                    offset = yy * width + xx
                    pixels[offset] = pixels[offset] * (255 - alpha) // 255
            x += gw

    rect(106, 40, 1054, 503, 24)
    rect(110, 44, 1046, 495, 235)
    rect(72, 64, 1056, 504, 0)
    rect(76, 68, 1048, 496, 252)
    title = preview_text(title) or "Untitled document"
    for size in (64, 56, 48):
        lines = textwrap.wrap(title, width=960 // (40 * size // 64),
                              break_long_words=True, break_on_hyphens=False)
        if len(lines) * (size * 5 // 4) <= 350:
            break
    max_lines = 350 // (size * 5 // 4)
    if len(lines) > max_lines:
        lines = lines[:max_lines]
        lines[-1] = lines[-1][:-3] + "..."
    for row, line in enumerate(lines):
        text(line, 126, 98 + row * (size * 5 // 4), size)
    text("Taildoc", 128, 494, 28)
    host = preview_text(host)
    if len(host) > 55:
        host = host[:52] + "..."
    text(host, 1072 - len(host) * 13, 497, 22)

    def chunk(kind, data):
        return (struct.pack(">I", len(data)) + kind + data
                + struct.pack(">I", zlib.crc32(kind + data) & 0xffffffff))

    scanlines = b"".join(b"\0" + pixels[y * width:(y + 1) * width] for y in range(height))
    return (b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", width, height, 8, 0, 0, 0, 0))
            + chunk(b"IDAT", zlib.compress(scanlines, 6)) + chunk(b"IEND", b""))


class _PreviewHead(HTMLParser):
    """Locate real head tags without rewriting document bytes or matching script text."""
    def __init__(self, doc: str):
        super().__init__(convert_charrefs=False)
        self.offsets = [0] + [match.end() for match in re.finditer("\n", doc)]
        self.head = None
        self.fallback = 0
        self.remove = []

    def source_offset(self):
        line, column = self.getpos()
        return self.offsets[line - 1] + column

    def handle_decl(self, decl):
        if decl.lower().startswith("doctype"):
            self.fallback = self.source_offset() + len(decl) + 3

    def handle_starttag(self, tag, attrs):
        start = self.source_offset()
        end = start + len(self.get_starttag_text())
        if tag == "head" and self.head is None:
            self.head = end
        elif tag == "html":
            self.fallback = end
        elif tag == "meta" and any(
            key in {"name", "property"} and (value or "").lower().startswith(("og:", "twitter:"))
            for key, value in attrs
        ):
            self.remove.append((start, end))


def preview_document(doc: str, title: str, image_url: str, share_url: str) -> str:
    tags = {
        "og:type": "article", "og:site_name": "Taildoc", "og:title": title,
        "og:url": share_url, "og:image": image_url, "og:image:secure_url": image_url,
        "og:image:type": "image/png", "og:image:width": "1200", "og:image:height": "630",
        "og:image:alt": title, "twitter:card": "summary_large_image",
        "twitter:title": title, "twitter:image": image_url, "twitter:image:alt": title,
    }
    metadata = "\n" + "\n".join(
        f'<meta {"name" if key.startswith("twitter:") else "property"}="{key}" '
        f'content="{html.escape(value, quote=True)}">' for key, value in tags.items()
    ) + "\n"
    parser = _PreviewHead(doc)
    parser.feed(doc)
    parser.close()
    insert = parser.head if parser.head is not None else parser.fallback
    if parser.head is None:
        metadata = "<head>" + metadata + "</head>"
    edits = [(start, end, "") for start, end in parser.remove] + [(insert, insert, metadata)]
    pieces = []
    previous = 0
    for start, end, replacement in sorted(edits):
        pieces.extend((doc[previous:start], replacement))
        previous = end
    pieces.append(doc[previous:])
    return "".join(pieces)


def make_links_openable(doc: str) -> str:
    """Add safe external-link attributes without reparsing or corrupting markup."""
    rewriter = _AnchorRewriter(doc)
    rewriter.feed(doc)
    rewriter.close()
    pieces: list[str] = []
    previous_end = 0
    for start, end, replacement in sorted(rewriter.edits):
        pieces.extend((doc[previous_end:start], replacement))
        previous_end = end
    pieces.append(doc[previous_end:])
    return "".join(pieces)


def _start_tag_attributes(raw: str) -> tuple[list[tuple[str, int, int]], int]:
    spans: list[tuple[str, int, int]] = []
    index = 1
    while index < len(raw) and not raw[index].isspace() and raw[index] not in "/>":
        index += 1
    close_at = len(raw)
    while index < len(raw):
        while index < len(raw) and raw[index].isspace():
            index += 1
        if index >= len(raw):
            break
        if raw[index] == ">":
            close_at = index
            break
        if raw[index] == "/" and index + 1 < len(raw) and raw[index + 1] == ">":
            close_at = index
            break
        start = index
        while (
            index < len(raw)
            and not raw[index].isspace()
            and raw[index] not in "/=>"
        ):
            index += 1
        if index == start:
            index += 1
            continue
        name = raw[start:index].lower()
        while index < len(raw) and raw[index].isspace():
            index += 1
        if index < len(raw) and raw[index] == "=":
            index += 1
            while index < len(raw) and raw[index].isspace():
                index += 1
            if index < len(raw) and raw[index] in {'"', "'"}:
                quote = raw[index]
                index += 1
                while index < len(raw) and raw[index] != quote:
                    index += 1
                if index < len(raw):
                    index += 1
            else:
                while (
                    index < len(raw)
                    and not raw[index].isspace()
                    and raw[index] != ">"
                ):
                    index += 1
        spans.append((name, start, index))
    return spans, close_at


class _AnchorRewriter(HTMLParser):
    def __init__(self, doc: str) -> None:
        super().__init__(convert_charrefs=True)
        self.edits: list[tuple[int, int, str]] = []
        self._line_offsets = [0]
        self._line_offsets.extend(
            index + 1 for index, character in enumerate(doc) if character == "\n"
        )

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag.lower() != "a":
            return
        names = {name.lower() for name, _value in attrs}
        if "href" not in names:
            return
        raw = self.get_starttag_text()
        if raw is None:
            return
        spans, close_at = _start_tag_attributes(raw)
        rel_spans = [(start, end) for name, start, end in spans if name == "rel"]
        changes: list[tuple[int, int, str]] = []
        additions = ""
        if rel_spans:
            changes.append((*rel_spans[0], 'rel="noopener noreferrer"'))
            changes.extend((start, end, "") for start, end in rel_spans[1:])
        else:
            additions += ' rel="noopener noreferrer"'
        if "target" not in names:
            additions = ' target="_blank"' + additions
        if additions:
            changes.append((close_at, close_at, additions))
        parts: list[str] = []
        previous_end = 0
        for start, end, replacement in sorted(changes):
            parts.extend((raw[previous_end:start], replacement))
            previous_end = end
        parts.append(raw[previous_end:])
        replacement = "".join(parts)
        line, column = self.getpos()
        start = self._line_offsets[line - 1] + column
        self.edits.append((start, start + len(raw), replacement))


def _has_unsafe_scheme(value: str) -> bool:
    decoded = html.unescape(value)
    for _ in range(3):
        expanded = unquote(decoded)
        if expanded == decoded:
            break
        decoded = expanded
    compact = re.sub(r"[\x00-\x20\x7f]+", "", decoded)
    match = re.match(r"^([a-z][a-z0-9+.-]*):", compact, re.IGNORECASE)
    return bool(match and match.group(1).lower() in BLOCKED_URL_SCHEMES)


def _decode_css_escapes(value: str) -> str:
    """Decode CSS escapes before applying URL allow/block rules."""
    decoded: list[str] = []
    index = 0
    while index < len(value):
        if value[index] != "\\":
            decoded.append(value[index])
            index += 1
            continue
        index += 1
        if index == len(value):
            break
        if value[index] in "\n\f":
            index += 1
            continue
        if value[index] == "\r":
            index += 1
            if index < len(value) and value[index] == "\n":
                index += 1
            continue
        escape_start = index
        while index < len(value) and index - escape_start < 6 and value[index] in "0123456789abcdefABCDEF":
            index += 1
        if index > escape_start:
            codepoint = int(value[escape_start:index], 16)
            if index < len(value) and value[index] in " \t\n\r\f":
                index += 1
            if codepoint == 0 or codepoint > 0x10FFFF or 0xD800 <= codepoint <= 0xDFFF:
                decoded.append("\N{REPLACEMENT CHARACTER}")
            else:
                decoded.append(chr(codepoint))
            continue
        decoded.append(value[index])
        index += 1
    return "".join(decoded)


def _strip_css_comments(value: str) -> str:
    """Strip CSS comments outside quoted strings."""
    out: list[str] = []
    index = 0
    length = len(value)
    quote: str | None = None
    while index < length:
        ch = value[index]
        if quote is not None:
            if ch == "\\" and index + 1 < length:
                if value[index + 1] == "\r" and index + 2 < length and value[index + 2] == "\n":
                    out.append(value[index : index + 3])
                    index += 3
                else:
                    out.append(value[index : index + 2])
                    index += 2
                continue
            out.append(ch)
            if ch in "\n\r\f" or ch == quote:
                quote = None
            index += 1
            continue
        if ch == "\\" and index + 1 < length:
            if value[index + 1] == "\r" and index + 2 < length and value[index + 2] == "\n":
                out.append(value[index : index + 3])
                index += 3
            else:
                out.append(value[index : index + 2])
                index += 2
            continue
        if ch in {'"', "'"}:
            quote = ch
            out.append(ch)
            index += 1
            continue
        if ch == "/" and index + 1 < length and value[index + 1] == "*":
            end = value.find("*/", index + 2)
            index = length if end == -1 else end + 2
            continue
        out.append(ch)
        index += 1
    return "".join(out)


def _css_text_is_unsafe(text: str) -> bool:
    urls = [
        next(group for group in match.groups() if group is not None)
        for match in CSS_URL_RE.finditer(text)
    ]
    urls.extend(
        next(group for group in match.groups() if group is not None)
        for match in CSS_IMPORT_RE.finditer(text)
    )
    return bool(re.search(r"expression\s*\(|behavior\s*:", text, re.IGNORECASE)) or any(
        _has_unsafe_scheme(url) for url in urls
    )


def _css_has_unsafe_url(value: str) -> bool:
    unstripped = _decode_css_escapes(value)
    stripped = _decode_css_escapes(_strip_css_comments(value))
    return _css_text_is_unsafe(stripped) or _css_text_is_unsafe(unstripped)


def _parse_srcset_urls(value: str) -> list[str]:
    """Extract candidate URLs from an HTML srcset attribute."""
    urls: list[str] = []
    index = 0
    length = len(value)
    while index < length:
        while index < length and value[index] in " \t\n\r\f,":
            index += 1
        if index >= length:
            break
        start = index
        while index < length and not value[index].isspace():
            if value[index] == ",":
                candidate = value[start:index]
                if not candidate.lower().startswith("data:"):
                    break
            index += 1
        raw_url = value[start:index]
        ended_with_comma = raw_url.endswith(",")
        url = raw_url.rstrip(",")
        if url:
            urls.append(url)
        if ended_with_comma:
            continue
        while index < length and value[index] != ",":
            index += 1
        if index < length and value[index] == ",":
            index += 1
    return urls


def slug_problem(slug: object) -> str | None:
    """Return why a custom URL slug is invalid, or None when valid."""
    if not isinstance(slug, str) or SLUG_RE.fullmatch(slug) is None:
        return "Custom URLs are 3-40 characters of a-z, 0-9, and inner hyphens."
    if slug[2:4] == "--":
        return "Custom URLs cannot have hyphens as the third and fourth characters."
    if GENERATED_DRAFT_ID_RE.fullmatch(slug) is not None:
        return "Custom URLs cannot be 12 letters and numbers; that is the draft ID format."
    if slug in RESERVED_SLUGS:
        return f'"{slug}" is reserved.'
    return None


def normalize_slug(value: object) -> str | None:
    if value is None:
        return None
    return value.strip().lower() if isinstance(value, str) else ""


def page_path_problem(path: object) -> str | None:
    """Return why a multi-page relative path is invalid, or None when valid."""
    if not isinstance(path, str) or not path:
        return "Page path is missing."
    segments = path.split("/")
    if len(path) > MAX_PAGE_PATH_LENGTH or len(segments) > MAX_PAGE_PATH_SEGMENTS:
        return f'Page path "{path}" is too long.'
    if not path.endswith(".html") or not all(
        PAGE_SEGMENT_RE.fullmatch(segment) for segment in segments
    ):
        return (
            f'Page path "{path}" must be a relative .html path '
            'of letters, numbers, ".", "_", and "-".'
        )
    root = segments[0].removesuffix(".html").lower()
    if root in RESERVED_PAGE_ROOTS or segments[0].lower() in RESERVED_PAGE_ROOTS:
        return f'Page path "{path}" uses the reserved name "{root}".'
    return None


def format_bytes(size: int) -> str:
    if size >= 1024 * 1024:
        return f"{size / (1024 * 1024):.1f}".rstrip("0").rstrip(".") + " MiB"
    if size >= 1024:
        return f"{size / 1024:.1f}".rstrip("0").rstrip(".") + " KiB"
    return f"{size} bytes"


def format_issue(issue: dict) -> str:
    location = f"line {issue['line']}:{issue['column']}" if issue.get("line") else None
    where = " ".join(part for part in (issue.get("path"), location) if part)
    count = f" ({issue['count']} places)" if issue.get("count", 1) > 1 else ""
    fix = f" Fix: {issue['fix']}" if issue.get("fix") else ""
    prefix = f"{where}: " if where else ""
    return f"{prefix}{issue['message']}{count}{fix}"


def _is_local_reference(value: str) -> bool:
    raw = value.strip().lower()
    if not raw or raw.startswith("#"):
        return False
    return not re.match(r"^(?:https?:|data:|blob:|//)", raw)


def upload_content_hash(pages: list[dict]) -> str:
    """Hash a single page directly, or hash a sorted multi-page manifest."""
    if len(pages) == 1:
        return sha256_text(pages[0]["html"])
    manifest = "\n".join(
        sorted(f"{page['path']}\0{sha256_text(page['html'])}" for page in pages)
    )
    return sha256_text(manifest)


def draft_content_security_policy(*, allow_inline_scripts: bool = False) -> str:
    """Build the viewer CSP. Scripted drafts run in an opaque sandbox."""
    inline = "'unsafe-inline'" if allow_inline_scripts else "'none'"
    directives = [
        "default-src 'none'",
        f"script-src {inline}",
        f"script-src-attr {inline}",
        "style-src 'unsafe-inline' https:",
        "img-src https: data:",
        "media-src https: data:",
        "font-src https: data:",
        "connect-src 'none'",
        "worker-src 'none'",
        "object-src 'none'",
        "frame-src 'none'",
        "frame-ancestors 'none'",
        "base-uri 'none'",
        "form-action 'none'",
    ]
    if allow_inline_scripts:
        directives.append("sandbox allow-scripts allow-popups allow-popups-to-escape-sandbox")
    return "; ".join(directives)


class _IssueList:
    def __init__(self) -> None:
        self._by_key: dict[tuple[str, str, str], dict] = {}

    def add(
        self,
        level: str,
        code: str,
        message: str,
        *,
        fix: str | None = None,
        pos: tuple[int, int] | None = None,
    ) -> None:
        key = (level, code, message)
        existing = self._by_key.get(key)
        if existing is not None:
            existing["count"] += 1
            return
        entry: dict = {"level": level, "code": code, "message": message, "count": 1}
        if fix:
            entry["fix"] = fix
        if pos is not None:
            entry["line"] = pos[0]
            entry["column"] = pos[1] + 1
        self._by_key[key] = entry

    def error(
        self,
        code: str,
        message: str,
        *,
        fix: str | None = None,
        pos: tuple[int, int] | None = None,
    ) -> None:
        self.add("error", code, message, fix=fix, pos=pos)

    def warning(
        self,
        code: str,
        message: str,
        *,
        fix: str | None = None,
        pos: tuple[int, int] | None = None,
    ) -> None:
        self.add("warning", code, message, fix=fix, pos=pos)

    def merge(self, item: dict) -> None:
        pos = (item["line"], item["column"] - 1) if "line" in item else None
        key = (item["level"], item["code"], item["message"])
        existing = self._by_key.get(key)
        if existing is not None:
            existing["count"] += item.get("count", 1)
            return
        self.add(item["level"], item["code"], item["message"], fix=item.get("fix"), pos=pos)
        self._by_key[key]["count"] = item.get("count", 1)

    def items(self) -> list[dict]:
        return list(self._by_key.values())


OPTIONAL_CLOSE_SIBLINGS = {
    "dd": {"dd", "dt"},
    "dt": {"dd", "dt"},
    "li": {"li"},
    "optgroup": {"optgroup", "option"},
    "option": {"option"},
    "p": {"p"},
    "rp": {"rp", "rt"},
    "rt": {"rp", "rt"},
    "tbody": {"tbody", "tfoot", "thead"},
    "td": {"td", "th"},
    "tfoot": {"tbody", "tfoot", "thead"},
    "th": {"td", "th"},
    "thead": {"tbody", "tfoot", "thead"},
    "tr": {"td", "th", "tr"},
}
P_CLOSING_BLOCKS = {
    "address", "article", "aside", "blockquote", "details", "dialog", "dd",
    "div", "dl", "dt", "fieldset", "figcaption", "figure", "footer", "form",
    "h1", "h2", "h3", "h4", "h5", "h6", "header", "hgroup", "hr", "li", "main",
    "nav", "ol", "p", "pre", "section", "table", "ul",
}


def _is_foreign_breakout(tag_lower: str, attrs: list[tuple[str, str | None]]) -> bool:
    if tag_lower in FOREIGN_BREAKOUT_TAGS:
        return True
    if tag_lower == "font":
        return any(name.lower() in {"color", "face", "size"} for name, _ in attrs)
    return False


def _integration_mode(
    parent_mode: str,
    tag_lower: str,
    attrs: list[tuple[str, str | None]],
) -> str | None:
    if parent_mode == "svg":
        return "svg_html" if tag_lower in SVG_INTEGRATION_TAGS else None
    if parent_mode == "math":
        if tag_lower in MATH_TEXT_INTEGRATION_TAGS:
            return "math_text"
        if tag_lower == "annotation-xml":
            first_encoding = next(
                (value for name, value in attrs if name.lower() == "encoding"),
                None,
            )
            if isinstance(first_encoding, str) and first_encoding.lower() in {
                "text/html",
                "application/xhtml+xml",
            }:
                return "math_html"
    return None


def _script_ends_double_escaped(text: str) -> bool:
    """Return True when HTML5 script escaping stays double-escaped at </script>."""
    state = 0
    lower = text.lower()
    index = 0
    length = len(lower)
    while index < length:
        if state == 0:
            if lower.startswith("<!--", index):
                state = 1
                index += 4
                continue
        elif state == 1:
            if lower.startswith("-->", index):
                state = 0
                index += 3
                continue
            if lower.startswith("<script", index) and (
                index + 7 == length or lower[index + 7] in "\t\n\r\f />"
            ):
                state = 2
                index += 7
                continue
        else:
            if lower.startswith("-->", index):
                state = 0
                index += 3
                continue
            if lower.startswith("</script", index) and (
                index + 8 == length or lower[index + 8] in "\t\n\r\f />"
            ):
                state = 1
                index += 8
                continue
        index += 1
    return state == 2


def _has_raw_meta_refresh(doc: str) -> bool:
    """Context-free check for meta refresh regardless of parser state."""
    for match in META_TAG_RAW_RE.finditer(doc):
        tag_text = html.unescape(match.group(0))
        for attr in META_REFRESH_ATTR_RE.finditer(tag_text):
            value = next(group for group in attr.groups() if group is not None)
            if value.strip().lower() == "refresh":
                return True
    return False


class _NestingLimitExceeded(Exception):
    """Raised to abort HTML validation immediately once MAX_DEPTH is exceeded."""


class _HtmlValidator(HTMLParser):
    def __init__(
        self,
        *,
        scripting: bool = False,
        classic_select: bool = False,
        cdata_override: str | None = None,
        inspect_comment_tails: bool = False,
        tail_depth: int = 0,
    ) -> None:
        try:
            super().__init__(convert_charrefs=True, scripting=scripting)
        except TypeError:
            super().__init__(convert_charrefs=True)
            self.scripting = scripting
        if hasattr(self, "_set_support_cdata"):
            self._set_support_cdata(False)
        self._classic_select = classic_select
        self._cdata_override = cdata_override
        self._inspect_comment_tails = inspect_comment_tails
        self._tail_depth = tail_depth
        self.issues = _IssueList()
        self._style_stack: list[tuple[list[str], tuple[int, int]]] = []
        self._tag_stack: list[tuple[str, str]] = []
        self._last_start_in_foreign = False
        self.too_deep = False
        self.has_inline_script = False
        self.external_image_hosts: set[str] = set()
        self.script_sources: list[tuple[str, tuple[int, int]]] = []
        self._active_script: tuple[bool, list[str], tuple[int, int]] | None = None
        self._in_title = False
        self._title_parts: list[str] = []
        self.title: str | None = None

    @property
    def errors(self) -> list[str]:
        return [
            format_issue(issue)
            for issue in self.issues.items()
            if issue["level"] == "error"
        ]

    def _in_foreign(self) -> bool:
        return bool(self._tag_stack and self._tag_stack[-1][1] in FOREIGN_MODES)

    def _in_select(self) -> bool:
        for stack_tag, mode in reversed(self._tag_stack):
            if mode != "html":
                return False
            if stack_tag == "select":
                return True
            if stack_tag in HTML_SCOPE_BARRIERS:
                return False
        return False

    def _in_table(self) -> bool:
        for stack_tag, mode in reversed(self._tag_stack):
            if mode == "html" and stack_tag == "table":
                return True
            if mode == "html" and stack_tag in {"html", "template"}:
                return False
        return False

    def _in_table_scope(self, tag_lower: str) -> bool:
        for stack_tag, mode in reversed(self._tag_stack):
            if mode != "html":
                continue
            if stack_tag == tag_lower:
                return True
            if stack_tag in {"table", "template", "html"}:
                return False
        return False

    def _pop_table_tag(self, tag_lower: str) -> bool:
        for idx in range(len(self._tag_stack) - 1, -1, -1):
            stack_tag, mode = self._tag_stack[idx]
            if mode != "html":
                continue
            if stack_tag == tag_lower:
                del self._tag_stack[idx:]
                return True
            if stack_tag in {"table", "template", "html"}:
                return False
        return False

    def _clear_table_context(self, tag_lower: str) -> None:
        if tag_lower in {"td", "th"}:
            targets = {"tr", "tbody", "thead", "tfoot", "table"}
        elif tag_lower == "tr":
            targets = {"tbody", "thead", "tfoot", "table"}
        else:
            targets = {"table"}
        for idx in range(len(self._tag_stack) - 1, -1, -1):
            stack_tag, mode = self._tag_stack[idx]
            if mode != "html":
                continue
            if stack_tag in targets:
                del self._tag_stack[idx + 1 :]
                return
            if stack_tag in {"template", "html"}:
                return

    def _pop_select(self) -> None:
        for idx in range(len(self._tag_stack) - 1, -1, -1):
            stack_tag, mode = self._tag_stack[idx]
            if mode != "html":
                return
            if stack_tag == "select":
                del self._tag_stack[idx:]
                return
            if stack_tag in HTML_SCOPE_BARRIERS:
                return

    def _sync_cdata_support(self) -> None:
        if hasattr(self, "_set_support_cdata"):
            self._set_support_cdata(self._in_foreign())

    def _inspect_rawtext_tail(self, text: str) -> None:
        match = RAWTEXT_CLOSE_IN_COMMENT_RE.search(text)
        if not match:
            return
        tail = text[match.end() :]
        if "<" not in tail:
            return
        if self._tail_depth >= 8:
            self.issues.error(
                "unsafe-declaration",
                "Blocked deeply nested rawtext breakout.",
                fix="Remove nested closing rawtext tags inside comments or attributes.",
                pos=self.getpos(),
            )
            return
        sub = _HtmlValidator(
            cdata_override="disable_nonscript",
            inspect_comment_tails=True,
            tail_depth=self._tail_depth + 1,
        )
        try:
            sub.feed(tail)
            sub.close()
        except _NestingLimitExceeded:
            self.too_deep = True
        for item in sub.issues.items():
            if item["level"] == "error":
                self.issues.merge(item)

    def handle_comment(self, data: str) -> None:
        if data.lstrip().upper().startswith("[CDATA["):
            self.issues.error(
                "unsafe-declaration",
                "Blocked unsupported markup declaration.",
                fix="Remove CDATA or non-standard markup declarations.",
                pos=self.getpos(),
            )
        if self._inspect_comment_tails:
            self._inspect_rawtext_tail(data)

    def unknown_decl(self, data: str) -> None:
        self.issues.error(
            "unsafe-declaration",
            "Blocked unsupported markup declaration.",
            fix="Remove CDATA or non-standard markup declarations.",
            pos=self.getpos(),
        )

    def _flush_style(self) -> None:
        if not self._style_stack:
            return
        parts, pos = self._style_stack.pop()
        if _css_has_unsafe_url("".join(parts)):
            self.issues.error("unsafe-css", "Blocked unsafe CSS URL found.", pos=pos)

    def _flush_script(self) -> None:
        if self._active_script is None:
            return
        executable, parts, pos = self._active_script
        body = "".join(parts)
        if _script_ends_double_escaped(body):
            self.issues.error(
                "unsafe-script",
                "Blocked ambiguous nested script comment.",
                fix="Remove nested script tags inside HTML comments in a script block.",
                pos=pos,
            )
        if executable:
            self.script_sources.append((body, pos))
        self._active_script = None

    def _flush_title(self) -> None:
        if not self._in_title:
            return
        self._in_title = False
        if self.title is None:
            text = " ".join("".join(self._title_parts).split())[:140]
            if text:
                self.title = text
        self._title_parts = []

    def close(self) -> None:
        super().close()
        while self._style_stack:
            self._flush_style()
        self._flush_script()
        self._flush_title()

    def _push_tag(self, tag_lower: str, mode: str) -> None:
        if mode == "html":
            if (
                tag_lower in P_CLOSING_BLOCKS
                and self._tag_stack
                and self._tag_stack[-1] == ("p", "html")
            ):
                self._tag_stack.pop()
            closers = OPTIONAL_CLOSE_SIBLINGS.get(tag_lower)
            if closers:
                while (
                    self._tag_stack
                    and self._tag_stack[-1][1] == "html"
                    and self._tag_stack[-1][0] in closers
                ):
                    self._tag_stack.pop()
        self._tag_stack.append((tag_lower, mode))
        if len(self._tag_stack) > MAX_DEPTH:
            self.too_deep = True
            raise _NestingLimitExceeded()

    def set_cdata_mode(self, elem: str, *args: object, **kwargs: object) -> None:
        low = elem.lower()
        if self._cdata_override == "force_html":
            super().set_cdata_mode(elem, *args, **kwargs)
            return
        if self._cdata_override == "disable_nonscript" and low != "script":
            return
        if (
            self._last_start_in_foreign
            or self._in_foreign()
            or (self._classic_select and self._in_select() and low != "script")
        ):
            return
        super().set_cdata_mode(elem, *args, **kwargs)

    def _enter_cdata(self, tag_lower: str) -> None:
        if not hasattr(self, "set_cdata_mode"):
            return
        cdata_tags = set(getattr(self, "CDATA_CONTENT_ELEMENTS", ("script", "style")))
        rcdata_tags = set(getattr(self, "RCDATA_CONTENT_ELEMENTS", ("textarea", "title")))
        scripting = bool(getattr(self, "scripting", False))
        if tag_lower in cdata_tags or tag_lower == "plaintext" or (scripting and tag_lower == "noscript"):
            self.set_cdata_mode(tag_lower)
        elif tag_lower in rcdata_tags:
            try:
                self.set_cdata_mode(tag_lower, escapable=True)
            except TypeError:
                self.set_cdata_mode(tag_lower)

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        tag_lower = tag.lower()
        top_mode = self._tag_stack[-1][1] if self._tag_stack else "html"
        in_foreign = top_mode in FOREIGN_MODES or (
            top_mode == "math_text" and tag_lower in MATH_TEXT_FOREIGN_EXCEPTIONS
        )
        if in_foreign and _is_foreign_breakout(tag_lower, attrs):
            while self._tag_stack and self._tag_stack[-1][1] in FOREIGN_MODES:
                self._tag_stack.pop()
            in_foreign = False
        self._last_start_in_foreign = in_foreign
        if not in_foreign:
            if self._classic_select and self._in_select():
                if tag_lower == "select":
                    self._pop_select()
                    self._sync_cdata_support()
                    self._inspect_tag(tag_lower, attrs, self_closing=False, in_foreign=False)
                    return
                if tag_lower in SELECT_CLOSING_TAGS or (
                    self._in_table() and tag_lower in TABLE_STRUCTURE_TAGS
                ):
                    self._pop_select()
                elif tag_lower not in SELECT_INNER_TAGS:
                    self._sync_cdata_support()
                    self._inspect_tag(tag_lower, attrs, self_closing=False, in_foreign=False)
                    return
            if tag_lower == "table" and self._in_table() and not any(
                self._in_table_scope(cell) for cell in ("td", "th", "caption")
            ):
                self._pop_table_tag("table")
            if tag_lower in TABLE_CELL_CLOSERS and self._in_table():
                self._clear_table_context(tag_lower)
                if tag_lower in {"tr", "td", "th"} and not any(
                    self._in_table_scope(group) for group in TABLE_ROW_GROUPS
                ):
                    self._push_tag("tbody", "html")
                if tag_lower in {"td", "th"} and not self._in_table_scope("tr"):
                    self._push_tag("tr", "html")
            if tag_lower in FOREIGN_ROOT_TAGS:
                self._push_tag(tag_lower, tag_lower)
            elif tag_lower not in VOID_ELEMENTS:
                self._push_tag(tag_lower, "html")
            if bool(getattr(self, "scripting", False)) and tag_lower == "noscript":
                self._enter_cdata(tag_lower)
        else:
            parent_tag, raw_parent_mode = self._tag_stack[-1]
            parent_mode = "math" if raw_parent_mode == "math_text" else raw_parent_mode
            if parent_tag == "annotation-xml" and parent_mode == "math" and tag_lower == "svg":
                self._push_tag("svg", "svg")
            else:
                imode = _integration_mode(parent_mode, tag_lower, attrs)
                self._push_tag(tag_lower, imode or parent_mode)
        self._sync_cdata_support()
        self._inspect_tag(tag_lower, attrs, self_closing=False, in_foreign=in_foreign)

    def handle_startendtag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        tag_lower = tag.lower()
        top_mode = self._tag_stack[-1][1] if self._tag_stack else "html"
        in_foreign = top_mode in FOREIGN_MODES or (
            top_mode == "math_text" and tag_lower in MATH_TEXT_FOREIGN_EXCEPTIONS
        )
        if in_foreign and _is_foreign_breakout(tag_lower, attrs):
            while self._tag_stack and self._tag_stack[-1][1] in FOREIGN_MODES:
                self._tag_stack.pop()
            in_foreign = False
        if in_foreign or tag_lower in FOREIGN_ROOT_TAGS:
            if len(self._tag_stack) + 1 > MAX_DEPTH:
                self.too_deep = True
                raise _NestingLimitExceeded()
            self._sync_cdata_support()
            self._inspect_tag(tag_lower, attrs, self_closing=True, in_foreign=True)
            return
        self._last_start_in_foreign = False
        if self._classic_select and self._in_select():
            if tag_lower == "select":
                self._pop_select()
                self._sync_cdata_support()
                self._inspect_tag(tag_lower, attrs, self_closing=False, in_foreign=False)
                return
            if tag_lower in SELECT_CLOSING_TAGS or (
                self._in_table() and tag_lower in TABLE_STRUCTURE_TAGS
            ):
                self._pop_select()
            elif tag_lower not in SELECT_INNER_TAGS:
                self._sync_cdata_support()
                self._inspect_tag(tag_lower, attrs, self_closing=False, in_foreign=False)
                return
        if tag_lower == "table" and self._in_table() and not any(
            self._in_table_scope(cell) for cell in ("td", "th", "caption")
        ):
            self._pop_table_tag("table")
        if tag_lower in TABLE_CELL_CLOSERS and self._in_table():
            self._clear_table_context(tag_lower)
            if tag_lower in {"tr", "td", "th"} and not any(
                self._in_table_scope(group) for group in TABLE_ROW_GROUPS
            ):
                self._push_tag("tbody", "html")
            if tag_lower in {"td", "th"} and not self._in_table_scope("tr"):
                self._push_tag("tr", "html")
        if tag_lower not in VOID_ELEMENTS:
            self._push_tag(tag_lower, "html")
        self._enter_cdata(tag_lower)
        self._sync_cdata_support()
        self._inspect_tag(tag_lower, attrs, self_closing=False, in_foreign=False)

    def _close_html_tag(self, tag_lower: str, start_idx: int) -> None:
        if tag_lower == "li":
            barriers = HTML_SCOPE_BARRIERS | {"ol", "ul"}
        elif tag_lower == "p":
            barriers = HTML_SCOPE_BARRIERS | {"button"}
        elif tag_lower in HTML_SCOPED_CLOSE_TAGS:
            barriers = HTML_SCOPE_BARRIERS
        else:
            barriers = HTML_SPECIAL_TAGS
        for idx in range(start_idx, -1, -1):
            stack_tag, mode = self._tag_stack[idx]
            if mode != "html":
                break
            if stack_tag == tag_lower or (
                tag_lower in HEADING_TAGS and stack_tag in HEADING_TAGS
            ):
                del self._tag_stack[idx:]
                break
            if stack_tag in barriers:
                break

    def handle_endtag(self, tag: str) -> None:
        tag_lower = tag.lower()
        if self._in_foreign() and tag_lower in {"br", "p"}:
            while self._tag_stack and self._tag_stack[-1][1] in FOREIGN_MODES:
                self._tag_stack.pop()
        elif tag_lower not in VOID_ELEMENTS and self._tag_stack:
            top_mode = self._tag_stack[-1][1]
            if top_mode in FOREIGN_MODES:
                matched_idx: int | None = None
                boundary = len(self._tag_stack)
                crossed_barrier = False
                for idx in range(len(self._tag_stack) - 1, -1, -1):
                    stack_tag, mode = self._tag_stack[idx]
                    if mode == "html":
                        boundary = idx
                        break
                    if stack_tag == tag_lower:
                        matched_idx = idx
                        break
                    if mode in INTEGRATION_MODES or (stack_tag, mode) == ("annotation-xml", "math"):
                        crossed_barrier = True
                if matched_idx is not None:
                    del self._tag_stack[matched_idx:]
                elif tag_lower in TABLE_STRUCTURE_TAGS and self._in_table_scope(tag_lower):
                    self._pop_table_tag(tag_lower)
                elif (
                    not crossed_barrier
                    and tag_lower not in FOREIGN_ROOT_TAGS
                    and boundary < len(self._tag_stack)
                ):
                    self._close_html_tag(tag_lower, boundary)
            elif tag_lower in TABLE_STRUCTURE_TAGS and self._in_table_scope(tag_lower):
                if self._classic_select and self._in_select():
                    self._pop_select()
                self._pop_table_tag(tag_lower)
            elif top_mode in INTEGRATION_MODES:
                matched_idx = None
                for idx in range(len(self._tag_stack) - 1, -1, -1):
                    stack_tag, mode = self._tag_stack[idx]
                    if mode == "html":
                        break
                    if stack_tag == tag_lower:
                        matched_idx = idx
                        break
                if matched_idx is not None:
                    del self._tag_stack[matched_idx:]
            else:
                self._close_html_tag(tag_lower, len(self._tag_stack) - 1)
        self._sync_cdata_support()
        if tag_lower == "style":
            self._flush_style()
        if tag_lower == "script":
            self._flush_script()
        if tag_lower == "title":
            self._flush_title()

    def handle_data(self, data: str) -> None:
        for parts, _ in self._style_stack:
            parts.append(data)
        if self._active_script is not None:
            self._active_script[1].append(data)
        if self._in_title:
            self._title_parts.append(data)

    def _record_external_host(self, value: str) -> None:
        try:
            parsed = urlparse("https:" + value if value.startswith("//") else value)
            if parsed.scheme in {"http", "https"} and parsed.hostname:
                self.external_image_hosts.add(parsed.hostname.lower())
        except ValueError:
            pass

    def _inspect_tag(
        self,
        tag: str,
        attrs: list[tuple[str, str | None]],
        *,
        self_closing: bool,
        in_foreign: bool,
    ) -> None:
        pos = self.getpos()
        attr_values: dict[str, list[str]] = {}
        for name, value in attrs:
            attr_values.setdefault(name.lower(), []).append(value or "")
            if self._inspect_comment_tails and value:
                self._inspect_rawtext_tail(value)
        if tag == "style" and not self_closing:
            self._style_stack.append(([], pos))
        if tag == "title" and not self_closing and not in_foreign:
            self._in_title = True
            self._title_parts = []
        if tag in BLOCKED_TAGS_WITH_FIX:
            self.issues.error(
                "blocked-tag",
                f'Blocked "{tag}" tag.',
                fix=BLOCKED_TAGS_WITH_FIX[tag],
                pos=pos,
            )
        if tag == "script":
            if any(name in attr_values for name in ("src", "href", "xlink:href")):
                self.issues.error(
                    "external-script",
                    "External script sources are not allowed.",
                    fix="Inline the code in a script block.",
                    pos=pos,
                )
            script_types = attr_values.get("type", [""])
            executable = False
            for raw_type in script_types:
                script_type = raw_type.strip().lower()
                if script_type in EXECUTABLE_SCRIPT_TYPES:
                    executable = True
                    self.has_inline_script = True
                elif script_type not in DATA_SCRIPT_TYPES:
                    self.issues.error(
                        "script-type",
                        f'Unsupported script type "{script_type}".',
                        fix='Use a classic or type="module" inline script, or type="application/json" for data.',
                        pos=pos,
                    )
            if not self_closing:
                self._active_script = (executable, [], pos)
        if tag == "link":
            rel_values = attr_values.get("rel", [])
            rels = [token for value in rel_values for token in value.lower().split()]
            blocked_rel = next((rel for rel in rels if rel not in ALLOWED_LINK_RELS), None)
            href = (attr_values.get("href", [""])[0]).strip()
            if not rels or blocked_rel is not None:
                self.issues.error(
                    "link-rel",
                    f'link rel="{blocked_rel or ""}" is not allowed.',
                    fix="Only stylesheet, icon, and preconnect links are allowed.",
                    pos=pos,
                )
            elif not re.match(r"^https://", href, re.IGNORECASE):
                self.issues.error(
                    "link-href",
                    "Linked stylesheets and icons must use https URLs.",
                    fix="Inline the CSS in a style block, or use an https URL.",
                    pos=pos,
                )
        for srcset_attr in ("srcset", "imagesrcset"):
            for srcset in attr_values.get(srcset_attr, []):
                for candidate in _parse_srcset_urls(srcset):
                    if _has_unsafe_scheme(candidate):
                        self.issues.error(
                            "unsafe-url",
                            f'Blocked unsafe URL in "{srcset_attr}" attribute.',
                            fix="Use an https link, or an event listener in a script block.",
                            pos=pos,
                        )
                    elif tag in EMBED_SRC_TAGS:
                        self._record_external_host(candidate)
                        if _is_local_reference(candidate):
                            self.issues.warning(
                                "relative-asset",
                                f'"{candidate}" will not load. Tailplan only hosts HTML pages.',
                                fix="Host the file over https, or inline it as a data: URL.",
                                pos=pos,
                            )
        if tag in EMBED_SRC_TAGS:
            for attr_name in ("src", "poster"):
                for ref in attr_values.get(attr_name, []):
                    cleaned = ref.strip()
                    if cleaned:
                        self._record_external_host(cleaned)
                        if _is_local_reference(cleaned) and not _has_unsafe_scheme(cleaned):
                            self.issues.warning(
                                "relative-asset",
                                f'"{cleaned}" will not load. Tailplan only hosts HTML pages.',
                                fix="Host the file over https, or inline it as a data: URL.",
                                pos=pos,
                            )
        if tag in SMIL_ANIMATION_TAGS:
            for attr_name in ("to", "from", "by", "values"):
                for raw in attr_values.get(attr_name, []):
                    candidates = raw.split(";") if attr_name == "values" else [raw]
                    if any(_has_unsafe_scheme(item) for item in candidates):
                        self.issues.error(
                            "unsafe-url",
                            f'Blocked unsafe URL in "{attr_name}" attribute.',
                            fix="Use an https link, or an event listener in a script block.",
                            pos=pos,
                        )
        for name, values in attr_values.items():
            if name in EVENT_HANDLER_ATTRS:
                self.has_inline_script = True
                for value in values:
                    self.script_sources.append((value, pos))
            if name == "srcdoc":
                self.issues.error(
                    "srcdoc",
                    'Blocked "srcdoc" attribute.',
                    fix=EMBED_FIX,
                    pos=pos,
                )
        if any(
            len(values) > 1 and name in SECURITY_SENSITIVE_ATTRIBUTES
            for name, values in attr_values.items()
        ):
            self.issues.error(
                "duplicate-attr",
                "Blocked duplicate security-sensitive attribute found.",
                pos=pos,
            )
        if tag == "meta" and any(
            value.strip().lower() == "refresh"
            for value in attr_values.get("http-equiv", [])
        ):
            self.issues.error(
                "meta-refresh",
                "Blocked meta refresh tag found.",
                fix="Use a normal link.",
                pos=pos,
            )
        if any(_css_has_unsafe_url(value) for value in attr_values.get("style", [])):
            self.issues.error(
                "unsafe-css",
                "Blocked unsafe CSS URL found.",
                pos=pos,
            )
        for name, values in attr_values.items():
            if name not in URL_ATTRIBUTES:
                continue
            if any(_has_unsafe_scheme(value) for value in values):
                self.issues.error(
                    "unsafe-url",
                    f'Blocked unsafe URL in "{name}" attribute.',
                    fix="Use an https link, or an event listener in a script block.",
                    pos=pos,
                )


def validate_html_detailed(
    doc: object,
    *,
    max_bytes: int | None = MAX_HTML_BYTES,
) -> dict:
    """Validate one HTML page and return structured issues and signals."""
    issues = _IssueList()
    if not isinstance(doc, str) or not doc.strip():
        issues.error("empty", "HTML document is empty.")
        items = issues.items()
        return {
            "ok": False,
            "errors": [format_issue(i) for i in items if i["level"] == "error"],
            "warnings": [],
            "issues": items,
            "title": None,
            "hasScripts": False,
            "bytes": 0,
            "externalImageHosts": [],
        }
    try:
        encoded = doc.encode("utf-8")
    except UnicodeEncodeError:
        issues.error("invalid-unicode", "HTML document contains invalid Unicode scalar values.")
        items = issues.items()
        return {
            "ok": False,
            "errors": [format_issue(i) for i in items if i["level"] == "error"],
            "warnings": [],
            "issues": items,
            "title": None,
            "hasScripts": False,
            "bytes": 0,
            "externalImageHosts": [],
        }
    size = len(encoded)
    if max_bytes is not None and size > max_bytes:
        issues.error(
            "too-large",
            f"HTML document is {size} bytes ({format_bytes(size)}); "
            f"maximum is {max_bytes} bytes ({format_bytes(max_bytes)}).",
        )
    validator = _HtmlValidator()
    try:
        validator.feed(doc)
        validator.close()
    except _NestingLimitExceeded:
        validator.too_deep = True
    for item in validator.issues.items():
        issues.merge(item)
    too_deep = validator.too_deep
    has_scripts = validator.has_inline_script
    script_sources = list(validator.script_sources)
    external_hosts = set(validator.external_image_hosts)
    extra_passes: list[dict[str, object]] = []
    if not too_deep:
        has_noscript = bool(NOSCRIPT_RE.search(doc))
        has_select = bool(SELECT_RE.search(doc))
        has_dual = bool(DUAL_MODE_RE.search(doc))
        if has_noscript:
            extra_passes.append({"scripting": True, "classic_select": False})
        if has_select:
            extra_passes.append({"scripting": False, "classic_select": True})
        if has_noscript and has_select:
            extra_passes.append({"scripting": True, "classic_select": True})
        if has_dual:
            # Cover both HTML RAWTEXT/RCDATA and non-CDATA interpretations across tree-builder modes.
            extra_passes.append({"scripting": has_noscript, "cdata_override": "force_html"})
            extra_passes.append({
                "scripting": False,
                "cdata_override": "disable_nonscript",
                "inspect_comment_tails": True,
            })
    for opts in extra_passes:
        extra = _HtmlValidator(**opts)
        try:
            extra.feed(doc)
            extra.close()
        except _NestingLimitExceeded:
            extra.too_deep = True
        seen_keys = {(i["level"], i["code"], i["message"]) for i in issues.items()}
        for item in extra.issues.items():
            key = (item["level"], item["code"], item["message"])
            if item["level"] == "error" and key not in seen_keys:
                issues.merge(item)
                seen_keys.add(key)
        too_deep = too_deep or extra.too_deep
        has_scripts = has_scripts or extra.has_inline_script
        script_sources.extend(extra.script_sources)
        external_hosts.update(extra.external_image_hosts)
        if too_deep:
            break
    if _has_raw_meta_refresh(doc) and not any(i["code"] == "meta-refresh" for i in issues.items()):
        issues.error(
            "meta-refresh",
            "Meta refresh redirects are not allowed.",
            fix="Remove the refresh meta tag.",
        )
    if too_deep:
        issues.error("too-deep", f"HTML is nested more than {MAX_DEPTH} levels deep.")
    for code, pattern, message, fix in SANDBOX_APIS:
        matched = next(
            ((text, pos) for text, pos in script_sources if pattern.search(text)),
            None,
        )
        if matched is not None:
            issues.warning(code, message, fix=fix, pos=matched[1])
    if not TITLE_RE.search(doc):
        issues.warning("no-title", "No <title> found; Tailplan will use a generic title.")
    if not VIEWPORT_RE.search(doc):
        issues.warning(
            "no-viewport",
            "No viewport meta tag found; phones will show a shrunken desktop page. "
            'Add <meta name="viewport" content="width=device-width, initial-scale=1">.',
        )
    items = issues.items()
    errors = [format_issue(i) for i in items if i["level"] == "error"]
    warnings = [format_issue(i) for i in items if i["level"] == "warning"]
    return {
        "ok": not errors,
        "errors": errors,
        "warnings": warnings,
        "issues": items,
        "title": validator.title,
        "hasScripts": has_scripts,
        "bytes": size,
        "externalImageHosts": sorted(external_hosts),
    }


def validate_html(doc: object) -> tuple[bool, list[str], list[str]]:
    result = validate_html_detailed(doc, max_bytes=MAX_HTML_BYTES)
    return result["ok"], sorted(set(result["errors"])), sorted(set(result["warnings"]))


def validate_upload(
    pages: object,
    *,
    max_bytes: int = MAX_HTML_BYTES,
    max_pages: int = MAX_PAGES_PER_UPLOAD,
) -> dict:
    """Validate one page or a multi-page folder upload."""
    issues: list[dict] = []
    results: list[dict] = []
    if not isinstance(pages, list) or not pages:
        issues.append({"level": "error", "code": "empty", "message": "Upload has no HTML pages.", "count": 1})
        return {
            "ok": False,
            "errors": [format_issue(i) for i in issues],
            "warnings": [],
            "issues": issues,
            "pages": [],
            "totalBytes": 0,
            "hasScripts": False,
            "title": None,
            "contentHash": None,
        }
    multi_page = len(pages) > 1
    if len(pages) > max_pages:
        issues.append({
            "level": "error",
            "code": "too-many-pages",
            "message": f"Upload has {len(pages)} pages; the limit is {max_pages}.",
            "count": 1,
        })
        return {
            "ok": False,
            "errors": [format_issue(i) for i in issues],
            "warnings": [],
            "issues": issues,
            "pages": [],
            "totalBytes": 0,
            "hasScripts": False,
            "title": None,
            "contentHash": None,
        }
    seen: set[str] = set()
    seen_lower: set[str] = set()
    dir_prefixes_lower: set[str] = set()
    running_bytes = 0
    for entry in pages:
        if not isinstance(entry, dict):
            issues.append({"level": "error", "code": "page-path", "message": "Page entry must be an object.", "count": 1})
            continue
        page_path = entry.get("path")
        problem = page_path_problem(page_path)
        if problem:
            issues.append({"level": "error", "code": "page-path", "message": problem, "count": 1})
            continue
        assert isinstance(page_path, str)
        lowered = page_path.lower()
        if lowered in seen_lower:
            issues.append({
                "level": "error",
                "code": "page-path",
                "message": f'Page path "{page_path}" appears twice.',
                "count": 1,
            })
            continue
        seen.add(page_path)
        seen_lower.add(lowered)
        segments = lowered.split("/")
        for idx in range(1, len(segments)):
            dir_prefixes_lower.add("/".join(segments[:idx]))
        raw_html = entry.get("html")
        if isinstance(raw_html, str):
            try:
                page_size = len(raw_html.encode("utf-8"))
            except UnicodeEncodeError:
                page_size = 0
            running_bytes += page_size
            if running_bytes > max_bytes:
                results.append({"path": page_path, "html": raw_html, "ok": False, "errors": [], "warnings": [],
                                "issues": [], "title": None, "hasScripts": False, "bytes": page_size,
                                "externalImageHosts": []})
                continue
        checked = validate_html_detailed(raw_html, max_bytes=None)
        results.append({"path": page_path, "html": raw_html, **checked})
        for issue in checked["issues"]:
            issues.append({"path": page_path, **issue} if multi_page else issue)
    for page in results:
        if page["path"].lower() in dir_prefixes_lower:
            issues.append({
                "level": "error",
                "code": "page-path",
                "message": f'Page path "{page["path"]}" conflicts with a folder of the same name.',
                "count": 1,
            })
    if "index.html" not in seen:
        issues.append({
            "level": "error",
            "code": "missing-index",
            "message": "Uploads need an index.html page.",
            "count": 1,
        })
    total_bytes = sum(page["bytes"] for page in results)
    if total_bytes > max_bytes:
        issues.append({
            "level": "error",
            "code": "too-large",
            "message": (
                f"HTML is {total_bytes} bytes ({format_bytes(total_bytes)}); "
                f"maximum is {max_bytes} bytes ({format_bytes(max_bytes)})."
            ),
            "count": 1,
        })
    ok = not any(issue["level"] == "error" for issue in issues)
    index_page = next((p for p in results if p["path"] == "index.html"), results[0] if results else None)
    return {
        "ok": ok,
        "errors": [format_issue(i) for i in issues if i["level"] == "error"],
        "warnings": [format_issue(i) for i in issues if i["level"] == "warning"],
        "issues": issues,
        "pages": results,
        "totalBytes": total_bytes,
        "hasScripts": any(page["hasScripts"] for page in results),
        "title": index_page["title"] if index_page else None,
        "contentHash": upload_content_hash(results) if ok else None,
    }


class StorageError(RuntimeError):
    """Metadata or draft storage is unavailable or corrupt."""


class IdempotencyConflict(RuntimeError):
    """A request key was reused with a different upload payload."""


class ConflictError(RuntimeError):
    """A requested custom URL conflicts with an existing draft."""

    def __init__(self, message: str, *, code: str | None = None) -> None:
        super().__init__(message)
        self.code = code


class AccessDenied(Exception):
    """The caller is signed in but lacks the role for this action."""


class PolicyLimit(Exception):
    """The request asks for more than the server policy allows."""


class ListenerStartupError(RuntimeError):
    """A configured HTTP listener could not be created safely."""


def clean_text(value: object, limit: int = 255) -> str | None:
    if not isinstance(value, str):
        return None
    value.encode("utf-8")
    return " ".join(value.split())[:limit] or None


def upload_metadata(value: object) -> dict:
    if not isinstance(value, dict):
        raise TypeError("metadata must be an object.")
    result = {
        field: clean_text(value.get(field), limit)
        for field, limit in {
            "repoOrg": 255, "repoName": 255, "repoHost": 255,
            "gitBranch": 255, "gitCommitSha": 255, "gitCommitSubject": 1000,
            "ciRunUrl": 2048, "ciActor": 255, "ciProvider": 255, "cliVersion": 255,
        }.items()
    }
    result["gitDirty"] = value.get("gitDirty") if type(value.get("gitDirty")) is bool else None
    return result


TAG_RE = re.compile(r"[a-z0-9][a-z0-9._-]{0,31}")
MAX_TAGS_PER_DRAFT = 10
MAX_BULK_DRAFTS = 200
SEARCH_TEXT_LIMIT = 65536


def normalize_tags(value: object) -> list[str]:
    """Return sorted, unique, lowercase tags. Raise ValueError for a bad tag or list."""
    if value is None:
        return []
    if not isinstance(value, (list, tuple)):
        raise ValueError("tags must be a list.")
    tags: set[str] = set()
    for item in value:
        if not isinstance(item, str):
            raise ValueError("Each tag must be text.")
        tag = item.strip().lower()
        if TAG_RE.fullmatch(tag) is None:
            raise ValueError(
                "A tag uses 1 to 32 characters: letters, digits, dot, underscore, or hyphen."
            )
        tags.add(tag)
    if len(tags) > MAX_TAGS_PER_DRAFT:
        raise ValueError(f"A draft holds at most {MAX_TAGS_PER_DRAFT} tags.")
    return sorted(tags)


class _SearchTextParser(HTMLParser):
    """Collect visible text and skip script, style, noscript, and template content."""

    _SKIPPED = frozenset({"script", "style", "noscript", "template"})

    def __init__(self, limit: int):
        super().__init__(convert_charrefs=True)
        self.limit = limit
        self.size = 0
        self.parts: list[str] = []
        self._depth = 0

    @property
    def full(self) -> bool:
        return self.size >= self.limit

    def handle_starttag(self, tag: str, attrs: list) -> None:
        if tag in self._SKIPPED:
            self._depth += 1

    def handle_endtag(self, tag: str) -> None:
        if tag in self._SKIPPED and self._depth:
            self._depth -= 1

    def handle_data(self, data: str) -> None:
        if self._depth or self.full:
            return
        piece = " ".join(data.split())
        if piece:
            self.parts.append(piece)
            self.size += len(piece) + 1


def extract_searchable_text(html_doc: str, limit: int = SEARCH_TEXT_LIMIT) -> str:
    """Return the visible text of an HTML page, with collapsed spaces, capped at `limit`."""
    parser = _SearchTextParser(limit)
    try:
        parser.feed(html_doc)
        parser.close()
    except Exception:  # A broken page still gives the text that parsed before the error.
        pass
    return " ".join(parser.parts)[:limit]


def _validate_workspace_schema(data: dict) -> None:
    accounts = data.get("accounts", {})
    keys = data.get("apiKeys", {})
    if not isinstance(accounts, dict) or not isinstance(keys, dict):
        raise StorageError("Account storage has an invalid schema.")
    for account_id, account in accounts.items():
        if (not isinstance(account_id, str) or not isinstance(account, dict)
                or not isinstance(account.get("name"), str)):
            raise StorageError("Account storage has an invalid account.")
    for key_id, key in keys.items():
        if (not isinstance(key_id, str) or DRAFT_ID_RE.fullmatch(key_id) is None
                or not isinstance(key, dict)
                or not isinstance(key.get("accountId"), str)
                or key["accountId"] not in {"local", "anonymous", *accounts}
                or not isinstance(key.get("name"), str)
                or not isinstance(key.get("keyHash"), str)
                or SHA256_RE.fullmatch(key["keyHash"]) is None
                or not isinstance(key.get("expiresAt"), (str, type(None)))):
            raise StorageError("Account storage has an invalid API key.")
    all_draft_ids = set(data["drafts"])
    seen_slugs: set[str] = set()
    for draft in data["drafts"].values():
        if not isinstance(draft.get("accountId", "local"), str):
            raise StorageError("Draft account is invalid.")
        slug = draft.get("slug")
        if slug is not None:
            if not isinstance(slug, str) or SLUG_RE.fullmatch(slug) is None:
                raise StorageError("Draft custom URL is invalid.")
            if not draft.get("deletedAt"):
                if slug in seen_slugs or slug in all_draft_ids:
                    raise StorageError("Duplicate active custom URL.")
                seen_slugs.add(slug)
        favorited_at = draft.get("favoritedAt")
        if favorited_at is not None and (not isinstance(favorited_at, str) or not favorited_at):
            raise StorageError("Draft favorite timestamp is invalid.")
        archived_at = draft.get("archivedAt")
        if archived_at is not None and (not isinstance(archived_at, str) or not archived_at):
            raise StorageError("Draft archive timestamp is invalid.")
        tags = draft.get("tags")
        if tags is not None:
            try:
                tags_valid = isinstance(tags, list) and normalize_tags(tags) == tags
            except ValueError:
                tags_valid = False
            if not tags_valid:
                raise StorageError("Draft tags are invalid.")
        workspace = draft.get("workspace")
        if workspace is not None and (not isinstance(workspace, str) or WORKSPACE_RE.fullmatch(workspace) is None):
            raise StorageError("Draft workspace is invalid.")
        if draft.get("visibility") is not None and draft["visibility"] not in VISIBILITIES:
            raise StorageError("Draft visibility is invalid.")
        versions = draft.get("versions", {})
        if not isinstance(versions, dict):
            raise StorageError("Draft history is invalid.")
        for number, version in versions.items():
            if (not isinstance(number, str) or re.fullmatch(r"[1-9][0-9]{0,8}", number) is None
                    or int(number) > draft["latestVersionNumber"]
                    or not isinstance(version, dict)
                    or version.get("versionNumber") != int(number)
                    or not isinstance(version.get("fileSha256"), str)
                    or SHA256_RE.fullmatch(version["fileSha256"]) is None
                    or type(version.get("fileSize")) is not int
                    or not 0 < version["fileSize"] <= MAX_HTML_BYTES):
                raise StorageError("Draft history has an invalid version.")
            content_hash = version.get("contentHash")
            if content_hash is not None and (
                not isinstance(content_hash, str) or SHA256_RE.fullmatch(content_hash) is None
            ):
                raise StorageError("Draft history has an invalid content hash.")
            page_count = version.get("pageCount")
            if page_count is not None and (
                type(page_count) is not int or not 1 <= page_count <= MAX_PAGES_PER_UPLOAD
            ):
                raise StorageError("Draft history has an invalid page count.")
            pages = version.get("pages")
            if pages is not None:
                if not isinstance(pages, list) or len(pages) > MAX_PAGES_PER_UPLOAD:
                    raise StorageError("Draft history has invalid page records.")
                for page in pages:
                    if (
                        not isinstance(page, dict)
                        or page_path_problem(page.get("path")) is not None
                        or not isinstance(page.get("fileSha256"), str)
                        or SHA256_RE.fullmatch(page["fileSha256"]) is None
                        or type(page.get("fileSize")) is not int
                        or not 0 < page["fileSize"] <= MAX_HTML_BYTES
                    ):
                        raise StorageError("Draft history has an invalid page record.")
        latest = versions.get(str(draft["latestVersionNumber"]))
        if latest and latest["fileSha256"] != draft["fileSha256"]:
            raise StorageError("Current draft and history checksums differ.")


def _valid_public_url(value: object, draft_id: str, slug: str | None = None) -> bool:
    if not isinstance(value, str):
        return False
    parsed = urlparse(value)
    tails = [f"/d/{draft_id}"]
    if isinstance(slug, str) and SLUG_RE.fullmatch(slug) is not None:
        tails.append(f"/d/{slug}")
    return (
        parsed.scheme in {"http", "https"}
        and bool(parsed.netloc)
        and any(parsed.path.endswith(tail) for tail in tails)
        and not parsed.params
        and not parsed.query
        and not parsed.fragment
    )


def _validate_metadata_schema(
    data: object,
    root: Path,
    *,
    allow_legacy_current_objects: bool = False,
) -> dict:
    if not isinstance(data, dict) or not isinstance(data.get("drafts"), dict):
        raise StorageError("Metadata has an invalid schema.")
    required_draft_keys = {
        "draftId",
        "title",
        "filename",
        "latestVersionNumber",
        "currentObject",
        "fileSha256",
        "createdAt",
        "updatedAt",
        "publicUrl",
    }
    for draft_id, draft in data["drafts"].items():
        if (
            not isinstance(draft_id, str)
            or DRAFT_ID_RE.fullmatch(draft_id) is None
            or not isinstance(draft, dict)
            or not required_draft_keys.issubset(draft)
            or draft["draftId"] != draft_id
            or not isinstance(draft["title"], str)
            or not 1 <= len(draft["title"]) <= 140
            or (
                draft["filename"] is not None
                and not isinstance(draft["filename"], str)
            )
            or type(draft["latestVersionNumber"]) is not int
            or not 1 <= draft["latestVersionNumber"] <= MAX_VERSION_NUMBER
            or not isinstance(draft["currentObject"], str)
            or not isinstance(draft["fileSha256"], str)
            or SHA256_RE.fullmatch(draft["fileSha256"]) is None
            or not isinstance(draft["createdAt"], str)
            or not draft["createdAt"]
            or not isinstance(draft["updatedAt"], str)
            or not draft["updatedAt"]
            or not _valid_public_url(draft["publicUrl"], draft_id, draft.get("slug"))
        ):
            raise StorageError("Metadata has an invalid draft schema.")
        expected_object = root / "drafts" / draft_id / f"v{draft['latestVersionNumber']}.html"
        current_object_text = draft["currentObject"]
        if current_object_text != str(expected_object):
            current_object = Path(current_object_text)
            expected_tail = (
                "drafts",
                draft_id,
                f"v{draft['latestVersionNumber']}.html",
            )
            if (
                not allow_legacy_current_objects
                or not current_object.is_absolute()
                or any(part in {".", ".."} for part in current_object_text.split(os.sep))
                or current_object.parts[-3:] != expected_tail
            ):
                raise StorageError("Metadata draft object path is invalid.")
    idempotency = data.get("idempotency", {})
    if not isinstance(idempotency, dict):
        raise StorageError("Metadata has an invalid idempotency schema.")
    required_result_keys = {"draftId", "title", "versionNumber", "publicUrl"}
    for request_key, receipt in idempotency.items():
        if (
            not isinstance(request_key, str)
            or IDEMPOTENCY_KEY_RE.fullmatch(request_key) is None
            or not isinstance(receipt, dict)
            or not {"fingerprint", "result"}.issubset(receipt)
            or not isinstance(receipt["fingerprint"], str)
            or SHA256_RE.fullmatch(receipt["fingerprint"]) is None
            or not isinstance(receipt["result"], dict)
            or not required_result_keys.issubset(receipt["result"])
        ):
            raise StorageError("Metadata has an invalid idempotency schema.")
        result = receipt["result"]
        result_draft_id = result["draftId"]
        if (
            not isinstance(result_draft_id, str)
            or result_draft_id not in data["drafts"]
            or not isinstance(result["title"], str)
            or not 1 <= len(result["title"]) <= 140
            or type(result["versionNumber"]) is not int
            or not 1
            <= result["versionNumber"]
            <= data["drafts"][result_draft_id]["latestVersionNumber"]
            or not _valid_public_url(
                result["publicUrl"],
                result_draft_id,
                result.get("slug") or data["drafts"][result_draft_id].get("slug"),
            )
        ):
            raise StorageError("Metadata has an invalid idempotency result schema.")
    _validate_workspace_schema(data)
    return data


def _atomic_write_text(path: Path, text: str) -> None:
    fd, tmp_name = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp", dir=path.parent)
    tmp_path = Path(tmp_name)
    try:
        os.fchmod(fd, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8") as stream:
            stream.write(text)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(tmp_path, path)
        directory_fd = os.open(path.parent, os.O_RDONLY)
        try:
            os.fsync(directory_fd)
        finally:
            os.close(directory_fd)
    except Exception:
        try:
            os.close(fd)
        except OSError:
            pass
        tmp_path.unlink(missing_ok=True)
        raise


DB_FILENAME = "tailplan.db"
TAILPLAN_VERSION = "0.6.0"
VISIBILITIES = ("private", "workspace", "authenticated", "link")
WORKSPACE_RE = re.compile(r"[a-z0-9][a-z0-9._-]{0,62}")
DEVICE_CODE_TTL = 600
DEVICE_KEY_TTL = 90 * 86400
DEVICE_POLL_INTERVAL = 5
USER_CODE_ALPHABET = "BCDFGHJKLMNPQRSTVWXZ"
USER_CODE_RE = re.compile(r"[BCDFGHJKLMNPQRSTVWXZ]{4}-[BCDFGHJKLMNPQRSTVWXZ]{4}")


def normalize_user_code(value: object) -> str | None:
    """Return a device user code in XXXX-XXXX form, or None."""
    if not isinstance(value, str) or len(value) > 32:
        return None
    raw = re.sub(r"[\s-]", "", value).upper()
    if len(raw) != 8:
        return None
    code = f"{raw[:4]}-{raw[4:]}"
    return code if USER_CODE_RE.fullmatch(code) else None
LEGACY_METADATA_FILENAME = "metadata.json"
LEGACY_BACKUP_FILENAME = "metadata.json.pre-sqlite.bak"
SQLITE_BUSY_TIMEOUT_MS = 5000
# Each entry upgrades the database from the previous version.
# Add a new version here, such as 2, for later tables.
_SCHEMA_STATEMENTS: dict[int, tuple[str, ...]] = {
    1: (
        "CREATE TABLE meta_extra (key TEXT PRIMARY KEY, value TEXT NOT NULL)",
        """CREATE TABLE accounts (
            account_id TEXT PRIMARY KEY,
            name TEXT,
            doc TEXT NOT NULL
        )""",
        """CREATE TABLE drafts (
            draft_id TEXT PRIMARY KEY,
            account_id TEXT,
            slug TEXT,
            title TEXT,
            latest_version INTEGER,
            created_at TEXT,
            updated_at TEXT,
            deleted_at TEXT,
            disabled_at TEXT,
            favorited_at TEXT,
            doc TEXT NOT NULL
        )""",
        "CREATE INDEX drafts_account ON drafts (account_id, updated_at)",
        "CREATE INDEX drafts_slug ON drafts (slug) WHERE slug IS NOT NULL",
        """CREATE TABLE draft_versions (
            draft_id TEXT NOT NULL REFERENCES drafts (draft_id)
                ON DELETE CASCADE ON UPDATE CASCADE,
            version_number INTEGER NOT NULL,
            version_id TEXT,
            created_at TEXT,
            file_sha256 TEXT,
            doc TEXT NOT NULL,
            PRIMARY KEY (draft_id, version_number)
        ) WITHOUT ROWID""",
        """CREATE TABLE api_keys (
            key_id TEXT PRIMARY KEY,
            account_id TEXT,
            name TEXT,
            key_hash TEXT,
            created_at TEXT,
            last_used_at TEXT,
            revoked_at TEXT,
            doc TEXT NOT NULL
        )""",
        "CREATE INDEX api_keys_hash ON api_keys (key_hash)",
        """CREATE TABLE idempotency_keys (
            seq INTEGER PRIMARY KEY AUTOINCREMENT,
            request_key TEXT NOT NULL UNIQUE,
            fingerprint TEXT,
            doc TEXT NOT NULL
        )""",
    ),
    # Version 2 adds sign-in principals, revocable sessions, draft sharing,
    # device login, and the audit log. Draft workspace and visibility stay in
    # the draft document, so version 1 drafts load without a rewrite.
    2: (
        """CREATE TABLE principals (
            issuer TEXT NOT NULL,
            subject TEXT NOT NULL,
            account_id TEXT NOT NULL,
            email TEXT,
            email_verified INTEGER NOT NULL DEFAULT 0,
            display_name TEXT,
            groups_json TEXT NOT NULL DEFAULT '[]',
            instance_role TEXT,
            workspaces_json TEXT NOT NULL DEFAULT '{}',
            session_epoch INTEGER NOT NULL DEFAULT 0,
            last_login_at TEXT,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            PRIMARY KEY (issuer, subject)
        ) WITHOUT ROWID""",
        "CREATE INDEX principals_account ON principals (account_id)",
        """CREATE TABLE sessions (
            session_id TEXT PRIMARY KEY,
            account_id TEXT NOT NULL,
            principal_issuer TEXT,
            principal_subject TEXT,
            idp_session_id TEXT,
            api_key_id TEXT,
            csrf TEXT NOT NULL,
            auth_method TEXT NOT NULL,
            epoch INTEGER NOT NULL DEFAULT 0,
            expires_at INTEGER NOT NULL,
            revoked_at TEXT,
            created_at TEXT NOT NULL
        )""",
        "CREATE INDEX sessions_principal ON sessions (principal_issuer, principal_subject)",
        "CREATE INDEX sessions_idp ON sessions (principal_issuer, idp_session_id)",
        """CREATE TABLE draft_grants (
            draft_id TEXT NOT NULL,
            principal_type TEXT NOT NULL CHECK (principal_type IN ('email', 'group', 'workspace')),
            principal_value TEXT NOT NULL CHECK (principal_value = lower(principal_value)),
            role TEXT NOT NULL CHECK (role IN ('viewer', 'editor')),
            granted_by TEXT,
            created_at TEXT NOT NULL,
            PRIMARY KEY (draft_id, principal_type, principal_value)
        ) WITHOUT ROWID""",
        "CREATE INDEX draft_grants_principal ON draft_grants (principal_type, principal_value)",
        """CREATE TABLE device_codes (
            device_code_hash TEXT PRIMARY KEY,
            user_code TEXT NOT NULL UNIQUE,
            client_name TEXT,
            status TEXT NOT NULL CHECK (status IN ('pending', 'approved', 'denied', 'consumed')),
            account_id TEXT,
            issued_key TEXT,
            expires_at INTEGER NOT NULL,
            created_at TEXT NOT NULL
        )""",
        """CREATE TABLE audit_events (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            ts TEXT NOT NULL,
            event_type TEXT NOT NULL,
            actor_account_id TEXT,
            actor_email TEXT,
            target_type TEXT,
            target_id TEXT,
            outcome TEXT NOT NULL,
            detail_json TEXT NOT NULL DEFAULT '{}'
        )""",
        "CREATE INDEX audit_events_type ON audit_events (event_type, id)",
        # The audit log is append-only. These triggers refuse edits and deletes.
        """CREATE TRIGGER audit_events_no_update BEFORE UPDATE ON audit_events
            BEGIN SELECT RAISE(ABORT, 'audit events are append-only'); END""",
        """CREATE TRIGGER audit_events_no_delete BEFORE DELETE ON audit_events
            BEGIN SELECT RAISE(ABORT, 'audit events are append-only'); END""",
    ),
    # Version 3 adds Tailscale login grants. SQLite cannot change a CHECK
    # constraint in place, so the table is copied into a new one.
    3: (
        """CREATE TABLE draft_grants_v3 (
            draft_id TEXT NOT NULL,
            principal_type TEXT NOT NULL
                CHECK (principal_type IN ('email', 'group', 'workspace', 'tailscale_login')),
            principal_value TEXT NOT NULL CHECK (principal_value = lower(principal_value)),
            role TEXT NOT NULL CHECK (role IN ('viewer', 'editor')),
            granted_by TEXT,
            created_at TEXT NOT NULL,
            PRIMARY KEY (draft_id, principal_type, principal_value)
        ) WITHOUT ROWID""",
        # A legacy `email` grant with a dot-free domain, such as
        # `alice@github`, is a Tailscale login. It moves to that type, so it
        # still matches the same reader and never matches as an email.
        """INSERT OR IGNORE INTO draft_grants_v3
            (draft_id, principal_type, principal_value, role, granted_by, created_at)
            SELECT draft_id,
                   CASE WHEN principal_type = 'email'
                             AND substr(principal_value, instr(principal_value, '@') + 1) NOT LIKE '%.%'
                        THEN 'tailscale_login' ELSE principal_type END,
                   principal_value, role, granted_by, created_at
            FROM draft_grants""",
        "DROP TABLE draft_grants",
        "ALTER TABLE draft_grants_v3 RENAME TO draft_grants",
        "CREATE INDEX draft_grants_principal ON draft_grants (principal_type, principal_value)",
        # A Tailscale principal stored a dot-free login as a verified email.
        # It is not an email, so clear it.
        """UPDATE principals SET email = NULL, email_verified = 0
            WHERE issuer = 'tailscale' AND email IS NOT NULL
              AND substr(email, instr(email, '@') + 1) NOT LIKE '%.%'""",
    ),
    # Version 4 adds the dashboard: archive state, tags, and a full-text index.
    # The tag column holds lowercase text only. The index starts empty, and the
    # server fills it from the draft files when it opens the database.
    4: (
        "ALTER TABLE drafts ADD COLUMN archived_at TEXT",
        """CREATE TABLE draft_tags (
            draft_id TEXT NOT NULL REFERENCES drafts (draft_id)
                ON DELETE CASCADE ON UPDATE CASCADE,
            tag TEXT NOT NULL CHECK (tag = lower(tag)),
            PRIMARY KEY (draft_id, tag)
        ) WITHOUT ROWID""",
        "CREATE INDEX draft_tags_tag ON draft_tags (tag)",
        """CREATE VIRTUAL TABLE draft_search USING fts5(
            draft_id UNINDEXED, title, slug, description, tags, body,
            tokenize = 'unicode61 remove_diacritics 2'
        )""",
    ),
}
SCHEMA_VERSION = max(_SCHEMA_STATEMENTS)
_TOP_LEVEL_TABLE_KEYS = frozenset({"drafts", "accounts", "apiKeys", "idempotency"})


def _json_text(value: object) -> str:
    return json.dumps(value, separators=(",", ":"))


def _text_or_none(value: object) -> str | None:
    return value if isinstance(value, str) else None


def _int_or_none(value: object) -> int | None:
    return value if type(value) is int else None


def _store_form(data: dict) -> dict:
    """Return the shape that the database gives back for the same metadata."""
    result: dict = {}
    for key, value in data.items():
        if key in {"accounts", "apiKeys", "idempotency"} and not value:
            continue
        if key == "drafts":
            value = {
                draft_id: (
                    {k: v for k, v in draft.items() if k != "versions"}
                    if draft.get("versions") == {}
                    else draft
                )
                for draft_id, draft in value.items()
            }
        result[key] = value
    return result


def _atomic_write_bytes(path: Path, payload: bytes) -> None:
    fd, tmp_name = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp", dir=path.parent)
    tmp_path = Path(tmp_name)
    try:
        os.fchmod(fd, 0o600)
        with os.fdopen(fd, "wb") as stream:
            stream.write(payload)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(tmp_path, path)
        _fsync_directory(path.parent)
    except Exception:
        try:
            os.close(fd)
        except OSError:
            pass
        tmp_path.unlink(missing_ok=True)
        raise


def _fsync_directory(directory: Path) -> None:
    directory_fd = os.open(directory, os.O_RDONLY)
    try:
        os.fsync(directory_fd)
    finally:
        os.close(directory_fd)


# -- Audit wide events: the JSON lines that leave the host (docs/security.md, "Audit log") --

AUDIT_EVENT_VERSION = 1
AUDIT_ACTOR_KINDS = ("owner", "member", "local", "api_key", "anonymous", "system", "other")
AUDIT_TARGET_TYPES = ("draft", "account", "principal", "api_key", "device_code", "other")
AUDIT_ERROR_TYPES = ("denied", "failure")
AUDIT_METHODS = ("oidc", "tailscale", "tailscale_header", "api_key", "device", "backchannel", "sign-out", "bootstrap")
AUDIT_REASONS = ("state", "provider_error", "token", "no_matching_rule", "invalid_login")
AUDIT_ACTIONS = ("delete", "disable", "enable")
AUDIT_ID_RE = re.compile(r"[A-Za-z0-9_.:-]{1,80}")
AUDIT_NAME_RE = re.compile(r"[a-z][a-z0-9_]*(\.[a-z][a-z0-9_]*){0,3}")
AUDIT_BASE_KEYS = frozenset({
    "event", "event_version", "run_id", "started_at", "duration_ms", "outcome", "error_type", "release",
    "actor_hash", "actor_kind", "target_type", "target_id",
})
AUDIT_DETAIL_KEYS = frozenset({
    "client.address", "auth_method", "reason", "action", "created", "draft_version", "visibility",
    "workspace", "grant_count", "grant_type", "grant_role", "grant_hash", "session_count",
})
# The redact rules of the log gateway read these as secrets. See the `logs` standard, section 3.
AUDIT_SECRET_RE = re.compile(
    r"sk-[A-Za-z0-9_-]{16,}|gh[pousr]_[A-Za-z0-9]{20,}|github_pat_[A-Za-z0-9_]{20,}|xox[abeoprs]-[A-Za-z0-9-]{10,}"
    r"|(?:A3T[A-Z0-9]|AKIA|ASIA)[A-Z0-9]{16}|eyJ[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}"
    r"|(?i:bearer)\s+[A-Za-z0-9._~+/=-]{8,}")
AUDIT_VALUE_RE = re.compile(
    r"(?i)(?:pass|passwd|password|passphrase|pwd|secret|token|key|cookie|authorization)[\s\"']*[:=]|COMMAND=")
AUDIT_KEY_NAME_RE = re.compile(
    r"(?i)(?:pass|passwd|password|passphrase|pwd|secret|token|api[_-]?key|apikey)(?:[_.-]?key|_access_key)?$"
    r"|(?:cookie|authorization)$")
_audit_fallback_key = secrets.token_bytes(32)


def audit_hmac_key() -> bytes:
    """Return the key for `actor_hash`. Production sets TAILPLAN_AUDIT_HMAC_KEY; a process without it
    uses a random key, so hashes then differ after a restart."""
    configured = (os.getenv("TAILPLAN_AUDIT_HMAC_KEY") or "").strip()
    return configured.encode() if configured else _audit_fallback_key


def audit_hash(value: str) -> str:
    """Return 16 hex characters of the keyed hash of a value that must not leave the host."""
    return hmac.new(audit_hmac_key(), value.encode(), hashlib.sha256).hexdigest()[:16]


def _audit_keep(value, rx: re.Pattern = AUDIT_ID_RE) -> str | None:
    """Return the value when it fits the pattern and holds no secret shape, else None (`keep()` in the standard)."""
    if isinstance(value, str) and rx.fullmatch(value) and not AUDIT_SECRET_RE.search(value) \
            and not AUDIT_VALUE_RE.search(value):
        return value
    return None


def _audit_address(value) -> str | None:
    """Return a plain IP address. A scope id (`fe80::1%eth0`) is free text, so it fails."""
    if not isinstance(value, str) or "%" in value:
        return None
    try:
        return str(ipaddress.ip_address(value))
    except ValueError:
        return None


def _audit_count(value) -> int | None:
    return value if isinstance(value, int) and not isinstance(value, bool) and value >= 0 else None


def _audit_actor(actor: dict | None) -> tuple[str, str]:
    """Return the `actor_hash` and `actor_kind` of an audit actor."""
    actor = actor or {}
    account_id, email = actor.get("accountId"), actor.get("email")
    api_key_id = actor.get("apiKeyId")
    if isinstance(account_id, str) and account_id:
        identity = "account:" + account_id
    elif isinstance(email, str) and email.strip():
        identity = "email:" + email.strip().casefold()
    else:
        return "", "anonymous"
    if account_id == "local":
        kind = "local"
    elif account_id == "anonymous":
        kind = "anonymous"
    elif isinstance(api_key_id, str) and api_key_id and api_key_id not in {"oidc", "tailscale", "tailscale_header", "view",
                                                            "bootstrap", "anonymous"}:
        kind = "api_key"
    elif actor.get("instanceRole") == "admin":
        kind = "owner"
    else:
        kind = "member"
    return audit_hash(identity), kind


def _audit_detail(detail: dict) -> dict:
    """Flatten an audit detail into scalar keys. A key or value outside the lists is dropped."""
    def choice(value, allowed):
        return value if value in allowed else None

    grants = detail.get("grants")
    grant_value = detail.get("value")
    flat = {
        "client.address": _audit_address(detail.get("sourceIp")),
        "auth_method": choice(detail.get("method") or detail.get("authMethod"), AUDIT_METHODS),
        "reason": choice(detail.get("reason"), AUDIT_REASONS),
        "action": choice(detail.get("action"), AUDIT_ACTIONS),
        "created": detail["created"] if isinstance(detail.get("created"), bool) else None,
        "draft_version": _audit_count(detail.get("version")),
        "visibility": choice(detail.get("visibility"), VISIBILITIES),
        "workspace": _audit_keep(detail.get("workspace")),
        "grant_count": len(grants) if isinstance(grants, list) else None,
        "grant_type": choice(detail.get("type"), GRANT_TYPES),
        "grant_role": choice(detail.get("role"), WORKSPACE_ROLES),
        "grant_hash": audit_hash("grant:" + grant_value.strip().casefold())
        if isinstance(grant_value, str) and grant_value.strip() else None,
        "session_count": _audit_count(detail.get("sessions")),
    }
    return {key: value for key, value in flat.items() if value is not None}


AUDIT_DETAIL_CHECKS = {
    "client.address": lambda v: _audit_address(v) == v,
    "auth_method": lambda v: v in AUDIT_METHODS,
    "reason": lambda v: v in AUDIT_REASONS,
    "action": lambda v: v in AUDIT_ACTIONS,
    "created": lambda v: isinstance(v, bool),
    "draft_version": lambda v: _audit_count(v) == v and not isinstance(v, bool),
    "visibility": lambda v: v in VISIBILITIES,
    "workspace": lambda v: _audit_keep(v) == v,
    "grant_count": lambda v: _audit_count(v) == v and not isinstance(v, bool),
    "grant_type": lambda v: v in GRANT_TYPES,
    "grant_role": lambda v: v in WORKSPACE_ROLES,
    "grant_hash": lambda v: isinstance(v, str) and re.fullmatch(r"[0-9a-f]{16}", v) is not None,
    "session_count": lambda v: _audit_count(v) == v and not isinstance(v, bool),
}


def check_audit_row(row: dict) -> dict:
    """Check the row before it prints: key set, types, patterns, and secret shapes. Raise ValueError."""
    keys = set(row)
    if not AUDIT_BASE_KEYS <= keys or not keys - AUDIT_BASE_KEYS <= AUDIT_DETAIL_KEYS:
        raise ValueError("audit row: key set")
    if any(AUDIT_KEY_NAME_RE.search(key) for key in keys):
        raise ValueError("audit row: secret-shaped key")
    for key, value in row.items():
        if isinstance(value, str):
            if "@" in value or AUDIT_SECRET_RE.search(value) or AUDIT_VALUE_RE.search(value):
                raise ValueError(f"audit row: {key} value")
        elif not isinstance(value, (int, bool)):
            raise ValueError(f"audit row: {key} type")
    for key in keys & AUDIT_DETAIL_KEYS:
        if not AUDIT_DETAIL_CHECKS[key](row[key]):
            raise ValueError(f"audit row: {key} rule")
    text_keys = ("event", "run_id", "started_at", "outcome", "error_type", "release", "actor_hash", "actor_kind",
                 "target_type", "target_id")
    if not all(isinstance(row[key], str) for key in text_keys) or type(row["duration_ms"]) is not int:
        raise ValueError("audit row: base type")
    hashed = row["target_type"] == "principal"
    checks = (
        row["event"].startswith("tailplan.") and AUDIT_NAME_RE.fullmatch(row["event"][9:]) is not None,
        type(row["event_version"]) is int and row["event_version"] == AUDIT_EVENT_VERSION,
        re.fullmatch(r"[0-9a-f]{32}", row["run_id"]) is not None,
        re.fullmatch(r"\d{4}-\d\d-\d\dT\d\d:\d\d:\d\d\.\d{3}Z", row["started_at"]) is not None,
        row["duration_ms"] >= 0,
        (row["outcome"], row["error_type"]) == ("success", "") or
        (row["outcome"] == "error" and row["error_type"] in AUDIT_ERROR_TYPES),
        re.fullmatch(r"[A-Za-z0-9_.-]{1,64}", row["release"]) is not None,
        row["actor_hash"] == "" or re.fullmatch(r"[0-9a-f]{16}", row["actor_hash"]) is not None,
        row["actor_kind"] in AUDIT_ACTOR_KINDS,
        row["target_type"] in AUDIT_TARGET_TYPES,
        row["target_id"] == "" or re.fullmatch(r"[0-9a-f]{16}" if hashed else AUDIT_ID_RE.pattern,
                                               row["target_id"]) is not None,
    )
    if not all(checks):
        raise ValueError(f"audit row: rule {checks.index(False)}")
    return row


def _audit_row(event, actor, target_type, target_id, outcome, detail) -> dict:
    actor_hash, actor_kind = _audit_actor(actor)
    if outcome == "denied":
        actor_kind = "anonymous"  # A denied sign-in never became a member.
    kind = target_type if target_type in AUDIT_TARGET_TYPES else "other"
    if not target_id:
        target = ""
    elif kind == "principal" and isinstance(target_id, str):
        target = audit_hash("principal:" + target_id.strip().casefold())
    else:
        target = _audit_keep(target_id) or "other"
    failed = outcome != "success"
    return {
        "event": "tailplan." + (event if isinstance(event, str) and AUDIT_NAME_RE.fullmatch(event) else "other"),
        "event_version": AUDIT_EVENT_VERSION,
        "run_id": uuid.uuid4().hex,
        "started_at": datetime.now(UTC).isoformat(timespec="milliseconds").replace("+00:00", "Z"),
        "duration_ms": 0,
        "outcome": "error" if failed else "success",
        "error_type": ("denied" if outcome == "denied" else "failure") if failed else "",
        "release": TAILPLAN_VERSION,
        "actor_hash": actor_hash,
        "actor_kind": actor_kind,
        "target_type": kind,
        "target_id": target,
        **_audit_detail(detail if isinstance(detail, dict) else {}),
    }


def audit_wide_event(event: str, actor: dict | None, target_type: str | None, target_id: str | None,
                     outcome: str, detail: dict | None) -> dict:
    """Build the wide event that leaves the host for one audit event. It has no email address, no
    free text, and no nested value. The SQLite audit table keeps the full record. It never raises:
    a row that fails its own check is replaced by a bare row that records the drop."""
    try:
        return check_audit_row(_audit_row(event, actor, target_type, target_id, outcome, detail))
    except Exception:
        return check_audit_row({
            "event": "tailplan.audit.invalid", "event_version": AUDIT_EVENT_VERSION, "run_id": uuid.uuid4().hex,
            "started_at": datetime.now(UTC).isoformat(timespec="milliseconds").replace("+00:00", "Z"),
            "duration_ms": 0, "outcome": "error", "error_type": "failure", "release": "other", "actor_hash": "",
            "actor_kind": "other", "target_type": "other", "target_id": "",
        })


class Store:
    """Keep draft metadata in SQLite and draft pages as files under `drafts/`."""

    def __init__(self, root: Path):
        self.root = Path(os.path.abspath(root))
        self.drafts = self.root / "drafts"
        self.db_path = self.root / DB_FILENAME
        # `meta` and `backup` name the files that older versions wrote.
        self.meta = self.root / LEGACY_METADATA_FILENAME
        self.backup = self.root / LEGACY_BACKUP_FILENAME
        self.drafts.mkdir(parents=True, exist_ok=True)
        self.root.mkdir(parents=True, exist_ok=True)
        os.chmod(self.root, 0o700)
        os.chmod(self.drafts, 0o700)
        with STORE_LOCK:
            if not self.db_path.exists() and self.meta.exists():
                self._migrate_legacy_metadata_locked()
            else:
                self._open_database_locked()

    # -- SQLite plumbing -------------------------------------------------

    @staticmethod
    def _connect(path: Path) -> sqlite3.Connection:
        connection = sqlite3.connect(path, timeout=SQLITE_BUSY_TIMEOUT_MS / 1000, isolation_level=None)
        connection.execute(f"PRAGMA busy_timeout={SQLITE_BUSY_TIMEOUT_MS}")
        connection.execute("PRAGMA foreign_keys=ON")
        return connection

    @contextmanager
    def _connection(self, path: Path | None = None):
        try:
            connection = self._connect(path or self.db_path)
        except sqlite3.Error as exc:
            raise StorageError("Metadata database is unavailable.") from exc
        try:
            yield connection
        except sqlite3.Error as exc:
            raise StorageError("Metadata database is unavailable or corrupt.") from exc
        finally:
            connection.close()

    @staticmethod
    @contextmanager
    def _write_transaction(connection: sqlite3.Connection):
        connection.execute("BEGIN IMMEDIATE")
        try:
            yield
        except BaseException:
            if connection.in_transaction:
                connection.execute("ROLLBACK")
            raise
        connection.execute("COMMIT")

    def _prepare_database(self, connection: sqlite3.Connection) -> None:
        mode = connection.execute("PRAGMA journal_mode=WAL").fetchone()[0]
        if str(mode).lower() != "wal":
            raise StorageError("SQLite write-ahead logging is unavailable in the data directory.")
        version = connection.execute("PRAGMA user_version").fetchone()[0]
        if version > SCHEMA_VERSION:
            raise StorageError("Metadata database was written by a newer Tailplan version.")
        if version == SCHEMA_VERSION:
            return
        with self._write_transaction(connection):
            current = connection.execute("PRAGMA user_version").fetchone()[0]
            for target in range(current + 1, SCHEMA_VERSION + 1):
                for statement in _SCHEMA_STATEMENTS[target]:
                    connection.execute(statement)
                connection.execute(f"PRAGMA user_version = {target}")

    def _open_database_locked(self) -> None:
        with self._connection() as connection:
            self._prepare_database(connection)
            self._rebase_database_paths_locked(connection)
            self._backfill_search_locked(connection)
        os.chmod(self.db_path, 0o600)

    SEARCH_BACKFILL_BATCH = 100

    @staticmethod
    def _search_row(draft: dict, body: str) -> tuple:
        clean = Store._fts_text
        return (
            draft["draftId"], clean(draft.get("title") or ""), clean(draft.get("slug") or ""),
            clean(draft.get("description") or ""), " ".join(draft.get("tags") or []), clean(body),
        )

    @classmethod
    def _index_draft(cls, connection: sqlite3.Connection, draft: dict, body: str) -> None:
        """Replace the search row of one draft. Call it inside a write transaction."""
        connection.execute("DELETE FROM draft_search WHERE draft_id = ?", (draft["draftId"],))
        connection.execute(
            "INSERT INTO draft_search (draft_id, title, slug, description, tags, body)"
            " VALUES (?, ?, ?, ?, ?, ?)",
            cls._search_row(draft, body),
        )

    def _read_search_file(self, path: Path) -> str:
        """Read one stored page that lies inside the drafts folder. A bad file gives an empty text."""
        try:
            if path.is_symlink():
                return ""
            resolved = path.resolve(strict=True)
            if (not resolved.is_relative_to(self.drafts.resolve(strict=True)) or not resolved.is_file()
                    or resolved.stat().st_size > MAX_HTML_BYTES):
                return ""
            return resolved.read_bytes().decode("utf-8")
        except (OSError, UnicodeError, ValueError, RuntimeError):
            return ""

    def _read_search_body(self, draft: dict, page_paths: list[str] | None = None) -> str:
        """Read the text of every page of the latest version for the index.

        The index page comes first. The text has the same cap as at upload.
        """
        texts = [self._read_search_file(Path(str(draft.get("currentObject") or "")))]
        latest = draft.get("latestVersionNumber")
        for page_path in sorted(page_paths or []):
            if page_path != "index.html" and latest:
                texts.append(self._read_search_file(self.drafts / draft["draftId"] / f"v{latest}" / page_path))
        return extract_searchable_text(" ".join(text for text in texts if text))

    def _backfill_search_locked(self, connection: sqlite3.Connection) -> None:
        """Index live drafts that have no search row. Drop rows for drafts that are gone."""
        indexed = {row[0] for row in connection.execute("SELECT draft_id FROM draft_search")}
        live: dict[str, dict] = {}
        for draft_id, doc in connection.execute(
            "SELECT draft_id, doc FROM drafts WHERE deleted_at IS NULL ORDER BY rowid"
        ):
            live[draft_id] = json.loads(doc)
        stale = sorted(indexed - live.keys())
        missing = [draft_id for draft_id in live if draft_id not in indexed]
        if not stale and not missing:
            return
        if stale:
            with self._write_transaction(connection):
                for draft_id in stale:
                    connection.execute("DELETE FROM draft_search WHERE draft_id = ?", (draft_id,))
        for start in range(0, len(missing), self.SEARCH_BACKFILL_BATCH):
            batch = []
            for draft_id in missing[start:start + self.SEARCH_BACKFILL_BATCH]:
                draft = live[draft_id]
                row = connection.execute(
                    "SELECT doc FROM draft_versions WHERE draft_id = ? AND version_number = ?",
                    (draft_id, draft.get("latestVersionNumber")),
                ).fetchone()
                pages = json.loads(row[0]).get("pages") if row else None
                paths = [page["path"] for page in pages if isinstance(page, dict) and isinstance(
                    page.get("path"), str)] if isinstance(pages, list) else []
                batch.append((draft, self._read_search_body(draft, paths)))
            with self._write_transaction(connection):
                for draft, body in batch:
                    self._index_draft(connection, draft, body)

    def _rebase_database_paths_locked(self, connection: sqlite3.Connection) -> None:
        """Update stored object paths when a database moves to a new directory."""
        data = self._read_all(connection)
        drafts = data.get("drafts", {})
        if not drafts:
            return
        changed = False
        for draft_id, draft in drafts.items():
            latest = draft.get("latestVersionNumber")
            if type(latest) is not int or latest < 1:
                continue
            canonical = self.drafts / draft_id / f"v{latest}.html"
            if draft.get("currentObject") != str(canonical):
                self._verify_rebase_object(canonical, draft)
                draft["currentObject"] = str(canonical)
                changed = True
        if changed:
            _validate_metadata_schema(data, self.root)
            with self._write_transaction(connection):
                self._write_all(connection, data)

    @staticmethod
    def _remove_database_files(path: Path) -> None:
        for suffix in ("", "-wal", "-shm", "-journal"):
            Path(str(path) + suffix).unlink(missing_ok=True)

    def _migrate_legacy_metadata_locked(self) -> None:
        """Import `metadata.json` into `tailplan.db` once, without changing draft files."""
        try:
            original = self.meta.read_bytes()
            data = json.loads(original.decode("utf-8"))
        except (OSError, UnicodeError, json.JSONDecodeError) as exc:
            raise StorageError("Metadata is unreadable or malformed.") from exc
        _validate_metadata_schema(
            data,
            self.root,
            allow_legacy_current_objects=True,
        )
        for draft_id, draft in data["drafts"].items():
            canonical = self.drafts / draft_id / f"v{draft['latestVersionNumber']}.html"
            self._verify_rebase_object(canonical, draft)
            draft["currentObject"] = str(canonical)
        _validate_metadata_schema(data, self.root)
        staging = self.root / f".{DB_FILENAME}.migrating.tmp"
        self._remove_database_files(staging)
        try:
            with self._connection(staging) as connection:
                self._prepare_database(connection)
                with self._write_transaction(connection):
                    self._write_all(connection, data)
                self._verify_import(connection, data)
            os.chmod(staging, 0o600)
            descriptor = os.open(staging, os.O_RDONLY)
            try:
                os.fsync(descriptor)
            finally:
                os.close(descriptor)
            # Keep the original file before the database goes live, so a rollback stays lossless.
            _atomic_write_bytes(self.backup, original)
            os.replace(staging, self.db_path)
            _fsync_directory(self.root)
        except BaseException:
            self._remove_database_files(staging)
            raise
        self.meta.unlink(missing_ok=True)
        self._open_database_locked()

    def _verify_import(self, connection: sqlite3.Connection, data: dict) -> None:
        connection.execute("BEGIN")
        try:
            imported = self._read_all(connection)
            counts = {
                table: connection.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
                for table in ("accounts", "drafts", "draft_versions", "api_keys", "idempotency_keys")
            }
        finally:
            connection.execute("ROLLBACK")
        expected = _store_form(data)
        versions = sum(len(draft.get("versions", {})) for draft in data["drafts"].values())
        expected_counts = {
            "accounts": len(data.get("accounts", {})),
            "drafts": len(data["drafts"]),
            "draft_versions": versions,
            "api_keys": len(data.get("apiKeys", {})),
            "idempotency_keys": len(data.get("idempotency", {})),
        }
        if (
            counts != expected_counts
            or list(imported["drafts"]) != list(data["drafts"])
            or imported != expected
        ):
            raise StorageError("Metadata import did not match the original file.")

    @staticmethod
    def _read_all(connection: sqlite3.Connection) -> dict:
        data: dict = {}
        for key, value in connection.execute("SELECT key, value FROM meta_extra ORDER BY key"):
            data[key] = json.loads(value)
        drafts: dict = {}
        for draft_id, doc in connection.execute("SELECT draft_id, doc FROM drafts ORDER BY rowid"):
            drafts[draft_id] = json.loads(doc)
        for draft_id, number, doc in connection.execute(
            "SELECT draft_id, version_number, doc FROM draft_versions ORDER BY draft_id, version_number"
        ):
            drafts[draft_id].setdefault("versions", {})[str(number)] = json.loads(doc)
        data["drafts"] = drafts
        accounts = {
            account_id: json.loads(doc)
            for account_id, doc in connection.execute("SELECT account_id, doc FROM accounts ORDER BY rowid")
        }
        if accounts:
            data["accounts"] = accounts
        keys = {
            key_id: json.loads(doc)
            for key_id, doc in connection.execute("SELECT key_id, doc FROM api_keys ORDER BY rowid")
        }
        if keys:
            data["apiKeys"] = keys
        receipts = {
            request_key: json.loads(doc)
            for request_key, doc in connection.execute(
                "SELECT request_key, doc FROM idempotency_keys ORDER BY seq"
            )
        }
        if receipts:
            data["idempotency"] = receipts
        return data

    @staticmethod
    def _sync_table(
        connection: sqlite3.Connection,
        table: str,
        key_column: str,
        columns: tuple[str, ...],
        rows: dict,
    ) -> None:
        """Insert, update, or delete rows so the table matches `rows`.

        Each value is the derived column values followed by the JSON document.
        """
        existing = dict(connection.execute(f"SELECT {key_column}, doc FROM {table}"))
        names = ", ".join((key_column, *columns, "doc"))
        marks = ", ".join("?" * (len(columns) + 2))
        assignments = ", ".join(f"{name} = ?" for name in (*columns, "doc"))
        for key, values in rows.items():
            if key not in existing:
                connection.execute(f"INSERT INTO {table} ({names}) VALUES ({marks})", (key, *values))
            elif existing[key] != values[-1]:
                connection.execute(
                    f"UPDATE {table} SET {assignments} WHERE {key_column} = ?", (*values, key)
                )
        for key in existing.keys() - rows.keys():
            connection.execute(f"DELETE FROM {table} WHERE {key_column} = ?", (key,))

    def _write_all(self, connection: sqlite3.Connection, data: dict) -> None:
        connection.execute("DELETE FROM meta_extra")
        connection.executemany(
            "INSERT INTO meta_extra (key, value) VALUES (?, ?)",
            [(key, _json_text(value)) for key, value in data.items() if key not in _TOP_LEVEL_TABLE_KEYS],
        )
        self._sync_table(
            connection, "accounts", "account_id", ("name",),
            {
                account_id: (_text_or_none(account.get("name")), _json_text(account))
                for account_id, account in data.get("accounts", {}).items()
            },
        )
        drafts = data.get("drafts", {})
        self._sync_table(
            connection, "drafts", "draft_id",
            ("account_id", "slug", "title", "latest_version", "created_at", "updated_at",
             "deleted_at", "disabled_at", "favorited_at", "archived_at"),
            {
                draft_id: (
                    _text_or_none(draft.get("accountId", "local")),
                    _text_or_none(draft.get("slug")),
                    _text_or_none(draft.get("title")),
                    _int_or_none(draft.get("latestVersionNumber")),
                    _text_or_none(draft.get("createdAt")),
                    _text_or_none(draft.get("updatedAt")),
                    _text_or_none(draft.get("deletedAt")),
                    _text_or_none(draft.get("disabledAt")),
                    _text_or_none(draft.get("favoritedAt")),
                    _text_or_none(draft.get("archivedAt")),
                    _json_text({k: v for k, v in draft.items() if k != "versions"}),
                )
                for draft_id, draft in drafts.items()
            },
        )
        wanted_tags = {
            (draft_id, tag)
            for draft_id, draft in drafts.items() if not draft.get("deletedAt")
            for tag in draft.get("tags") or []
        }
        stored_tags = {(row[0], row[1]) for row in connection.execute("SELECT draft_id, tag FROM draft_tags")}
        for draft_id, tag in wanted_tags - stored_tags:
            connection.execute("INSERT INTO draft_tags (draft_id, tag) VALUES (?, ?)", (draft_id, tag))
        for draft_id, tag in stored_tags - wanted_tags:
            connection.execute("DELETE FROM draft_tags WHERE draft_id = ? AND tag = ?", (draft_id, tag))
        version_rows = {
            (draft_id, int(number)): (
                _text_or_none(version.get("versionId")),
                _text_or_none(version.get("createdAt")),
                _text_or_none(version.get("fileSha256")),
                _json_text(version),
            )
            for draft_id, draft in drafts.items()
            for number, version in (draft.get("versions") or {}).items()
        }
        existing = {
            (draft_id, number): doc
            for draft_id, number, doc in connection.execute(
                "SELECT draft_id, version_number, doc FROM draft_versions"
            )
        }
        for (draft_id, number), values in version_rows.items():
            if (draft_id, number) not in existing:
                connection.execute(
                    "INSERT INTO draft_versions (draft_id, version_number, version_id, created_at,"
                    " file_sha256, doc) VALUES (?, ?, ?, ?, ?, ?)",
                    (draft_id, number, *values),
                )
            elif existing[(draft_id, number)] != values[-1]:
                connection.execute(
                    "UPDATE draft_versions SET version_id = ?, created_at = ?, file_sha256 = ?,"
                    " doc = ? WHERE draft_id = ? AND version_number = ?",
                    (*values, draft_id, number),
                )
        for draft_id, number in existing.keys() - version_rows.keys():
            connection.execute(
                "DELETE FROM draft_versions WHERE draft_id = ? AND version_number = ?",
                (draft_id, number),
            )
        self._sync_table(
            connection, "api_keys", "key_id",
            ("account_id", "name", "key_hash", "created_at", "last_used_at", "revoked_at"),
            {
                key_id: (
                    _text_or_none(key.get("accountId")),
                    _text_or_none(key.get("name")),
                    _text_or_none(key.get("keyHash")),
                    _text_or_none(key.get("createdAt")),
                    _text_or_none(key.get("lastUsedAt")),
                    _text_or_none(key.get("revokedAt")),
                    _json_text(key),
                )
                for key_id, key in data.get("apiKeys", {}).items()
            },
        )
        self._sync_table(
            connection, "idempotency_keys", "request_key", ("fingerprint",),
            {
                request_key: (
                    _text_or_none(receipt.get("fingerprint")) if isinstance(receipt, dict) else None,
                    _json_text(receipt),
                )
                for request_key, receipt in data.get("idempotency", {}).items()
            },
        )

    def _replace_all_unchecked(self, data: dict) -> None:
        """Write metadata without schema checks. Tests use this to plant corrupt records."""
        with STORE_LOCK, self._connection() as connection:
            with self._write_transaction(connection):
                self._write_all(connection, data)

    def _verify_rebase_object(self, path: Path, draft: dict) -> None:
        fd = None
        try:
            if path.is_symlink():
                raise StorageError("Canonical draft object path is invalid.")
            resolved = path.resolve(strict=True)
            if not resolved.is_relative_to(self.drafts.resolve(strict=True)):
                raise StorageError("Canonical draft object path is invalid.")
            flags = os.O_RDONLY | getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NOFOLLOW", 0)
            fd = os.open(path, flags)
            object_stat = os.fstat(fd)
            if not stat.S_ISREG(object_stat.st_mode):
                raise StorageError("Canonical draft object path is invalid.")
            if object_stat.st_size > MAX_HTML_BYTES:
                raise StorageError("Canonical draft object size is invalid.")
            with os.fdopen(fd, "rb") as stream:
                fd = None
                payload = stream.read(MAX_HTML_BYTES + 1)
            if len(payload) != object_stat.st_size:
                raise StorageError("Canonical draft object size changed while reading.")
            doc = payload.decode("utf-8")
        except StorageError:
            raise
        except (OSError, UnicodeError, RuntimeError) as exc:
            raise StorageError(
                "Canonical draft object is unavailable or unreadable."
            ) from exc
        finally:
            if fd is not None:
                os.close(fd)
        if hashlib.sha256(payload).hexdigest() != draft["fileSha256"]:
            raise StorageError("Canonical draft object checksum does not match metadata.")
        valid, _errors, _warnings = validate_html(doc)
        if not valid:
            raise StorageError("Canonical draft object has an invalid HTML schema.")

    def load_meta(self) -> dict:
        with STORE_LOCK:
            try:
                with self._connection() as connection:
                    connection.execute("BEGIN")
                    try:
                        data = self._read_all(connection)
                    finally:
                        connection.execute("ROLLBACK")
            except (ValueError, KeyError) as exc:
                raise StorageError("Metadata is unreadable or malformed.") from exc
            return _validate_metadata_schema(data, self.root)

    def save_meta(self, data: dict, *, also=None) -> None:
        """Write the metadata. Run `also(connection)` in the same transaction."""
        with STORE_LOCK:
            _validate_metadata_schema(data, self.root)
            with self._connection() as connection:
                with self._write_transaction(connection):
                    self._write_all(connection, data)
                    if also is not None:
                        also(connection)

    def _read_page_object(self, path: Path, expected_sha256: str) -> str:
        try:
            if path.is_symlink():
                raise StorageError("Recorded draft object path is invalid.")
            resolved = path.resolve(strict=True)
            if not resolved.is_relative_to(self.drafts.resolve(strict=True)) or not resolved.is_file():
                raise StorageError("Recorded draft object path is invalid.")
            doc = resolved.read_bytes().decode("utf-8")
        except (OSError, UnicodeError, RuntimeError) as exc:
            raise StorageError("Recorded draft object is unavailable or unreadable.") from exc
        if sha256_text(doc) != expected_sha256:
            raise StorageError("Recorded draft object checksum does not match metadata.")
        return doc

    def _read_current_object(self, draft: dict) -> str:
        return self._read_page_object(Path(draft["currentObject"]), draft["fileSha256"])

    def _write_page_object(self, draft_id: str, version: int, page_path: str, content: str) -> Path:
        if page_path == "index.html":
            target = self.drafts / draft_id / f"v{version}.html"
            target.parent.mkdir(parents=True, exist_ok=True)
            _atomic_write_text(target, content)
            return target
        base = (self.drafts / draft_id / f"v{version}").resolve()
        target = (base / page_path).resolve()
        if not target.is_relative_to(base):
            raise StorageError("Draft page path escapes storage root.")
        target.parent.mkdir(parents=True, exist_ok=True)
        _atomic_write_text(target, content)
        return target

    @staticmethod
    def _resolve_ref(data: dict, ref: str) -> tuple[str | None, dict | None]:
        drafts = data.get("drafts", {})
        if ref in drafts:
            return ref, drafts[ref]
        lowered = ref.lower()
        if lowered in drafts:
            return lowered, drafts[lowered]
        for draft_id, draft in drafts.items():
            if not draft.get("deletedAt") and draft.get("slug") == lowered:
                return draft_id, draft
        return None, None

    @staticmethod
    def _find_by_slug(data: dict, slug: str) -> dict | None:
        for draft in data.get("drafts", {}).values():
            if not draft.get("deletedAt") and draft.get("slug") == slug:
                return draft
        return None

    def upsert(
        self,
        html_doc: str | None,
        filename: str | None,
        draft_id: str | None,
        base_url: str,
        request_key: str | None = None,
        *,
        account_id: str = "local",
        description: str | None = None,
        metadata: dict | None = None,
        audit: dict | None = None,
        files: list[dict] | None = None,
        slug: str | None = None,
        validation: dict | None = None,
        workspace: str | None = None,
        default_workspace: str | None = None,
        visibility: str | None = None,
        grants: list[dict] | None = None,
        tags: list[str] | None = None,
        favorite: bool | None = None,
    ) -> dict:
        tags = normalize_tags(tags) if tags is not None else None
        if favorite is not None and type(favorite) is not bool:
            raise ValueError("favorite must be true or false.")
        if validation is None:
            raw_pages = (
                [{"path": p["path"], "html": p["html"]} for p in files]
                if files is not None
                else [{"path": "index.html", "html": html_doc or ""}]
            )
            page_checks = {
                p["path"]: validate_html_detailed(p["html"], max_bytes=None)
                for p in raw_pages
            }
        else:
            page_checks = {p["path"]: p for p in validation["pages"]}
        with STORE_LOCK:
            return self._upsert_locked(
                html_doc, filename, draft_id, base_url, request_key,
                account_id, description, metadata or {}, audit or {},
                files=files, slug=slug, page_checks=page_checks,
                workspace=workspace, default_workspace=default_workspace,
                visibility=visibility, grants=grants, tags=tags, favorite=favorite,
            )

    def _upsert_locked(
        self,
        html_doc: str | None,
        filename: str | None,
        draft_id: str | None,
        base_url: str,
        request_key: str | None,
        account_id: str,
        description: str | None,
        metadata: dict,
        audit: dict,
        *,
        files: list[dict] | None = None,
        slug: str | None = None,
        page_checks: dict[str, dict] | None = None,
        workspace: str | None = None,
        default_workspace: str | None = None,
        visibility: str | None = None,
        grants: list[dict] | None = None,
        tags: list[str] | None = None,
        favorite: bool | None = None,
    ) -> dict:
        data = self.load_meta()
        data.setdefault("drafts", {})
        data.setdefault("idempotency", {})
        if slug is not None:
            if account_id == "anonymous":
                raise PermissionError("Custom URLs require an authenticated account.")
            problem = slug_problem(slug)
            if problem is not None:
                raise ValueError(problem)
            if slug in data["drafts"]:
                raise ConflictError(f'The custom URL "{slug}" conflicts with an existing draft ID.')
        pages = (
            [{"path": p["path"], "html": p["html"]} for p in files]
            if files is not None
            else [{"path": "index.html", "html": html_doc or ""}]
        )
        index_page = next(p for p in pages if p["path"] == "index.html")
        index_html = index_page["html"]
        extra_pages = [p for p in pages if p["path"] != "index.html"]
        content_hash = upload_content_hash(pages)
        total_bytes = sum(len(p["html"].encode("utf-8")) for p in pages)
        slug_draft = self._find_by_slug(data, slug) if slug else None
        if slug_draft is not None:
            if slug_draft.get("accountId", "local") != account_id:
                raise ConflictError(f'The custom URL "{slug}" belongs to another account.')
            if draft_id and slug_draft["draftId"] != draft_id:
                raise ConflictError(
                    f'The custom URL "{slug}" already names draft {slug_draft["draftId"]}. '
                    "Upload with --slug alone to update it.",
                    code="slug_names_other_draft",
                )
        resolved_draft_id = slug_draft["draftId"] if slug_draft is not None else draft_id
        if resolved_draft_id:
            self._owned_draft(data, resolved_draft_id, account_id)
        if request_key and account_id != "local":
            request_key = sha256_text(f"{account_id}:{request_key}")
        fingerprint_payload = {
            "html": index_html if not extra_pages else None,
            "contentHash": content_hash if extra_pages else None,
            "filename": filename,
            "draftId": draft_id,
            "slug": slug,
            "accountId": account_id,
            "description": description,
            "metadata": metadata,
            **({"grants": grants} if grants else {}),
            **({"workspace": workspace} if workspace is not None else {}),
            **({"visibility": visibility} if visibility is not None else {}),
            **({"tags": tags} if tags is not None else {}),
            **({"favorite": favorite} if favorite is not None else {}),
        }
        fingerprint = sha256_text(
            json.dumps(
                fingerprint_payload,
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
            )
        )
        if request_key and request_key in data["idempotency"]:
            receipt = data["idempotency"][request_key]
            current_draft = self._owned_draft(data, receipt["result"]["draftId"], account_id)
            legacy_match = False
            # Older receipts carry no sharing fields, so they match only an
            # upload that asks for no grants, workspace, or visibility.
            plain_sharing = (
                not grants and workspace is None and visibility is None
                and tags is None and favorite is None
            )
            if (
                plain_sharing
                and account_id == "local"
                and not extra_pages
                and slug is None
                and "versionId" not in receipt["result"]
                and description is None
                and all(value is None for value in metadata.values())
            ):
                legacy_match = receipt.get("fingerprint") == sha256_text(
                    json.dumps(
                        {"html": index_html, "filename": filename, "draftId": draft_id},
                        ensure_ascii=False,
                        sort_keys=True,
                        separators=(",", ":"),
                    )
                )
            if not legacy_match and plain_sharing and not extra_pages and slug is None:
                legacy_match = receipt.get("fingerprint") == sha256_text(
                    json.dumps(
                        {
                            "html": index_html,
                            "filename": filename,
                            "draftId": draft_id,
                            "accountId": account_id,
                            "description": description,
                            "metadata": metadata,
                        },
                        ensure_ascii=False,
                        sort_keys=True,
                        separators=(",", ":"),
                    )
                )
            if receipt.get("fingerprint") != fingerprint and not legacy_match:
                raise IdempotencyConflict("Request key was reused with a different payload.")
            replayed = dict(receipt["result"])
            current_slug = current_draft.get("slug")
            replayed["slug"] = current_slug
            replayed["publicUrl"] = f"{base_url.rstrip('/')}/d/{current_slug or current_draft['draftId']}"
            replayed["rawUrl"] = replayed["publicUrl"] + "/raw"
            if isinstance(replayed.get("pages"), list):
                replayed["pages"] = [
                    {**page, "url": f"{replayed['publicUrl']}/{page['path']}"}
                    for page in replayed["pages"]
                    if isinstance(page, dict) and isinstance(page.get("path"), str)
                ]
            return {
                **replayed,
                "created": False,
                "replayed": True,
                "unchanged": bool(receipt["result"].get("unchanged", False)),
            }
        creating = not resolved_draft_id
        if resolved_draft_id and resolved_draft_id not in data["drafts"]:
            raise KeyError("Draft not found.")
        target_draft_id = resolved_draft_id
        if not target_draft_id:
            target_draft_id = new_id()
            while (
                target_draft_id in data["drafts"]
                or self._find_by_slug(data, target_draft_id) is not None
            ):
                target_draft_id = new_id()
        draft = data["drafts"].get(target_draft_id, {})
        latest_version = int(draft.get("latestVersionNumber") or 0)
        versions = self._version_records(draft) if draft else {}
        latest_record = versions.get(str(latest_version)) if latest_version else None
        latest_hash = None
        if latest_record is not None:
            latest_hash = latest_record.get("contentHash") or (
                latest_record.get("fileSha256")
                if (latest_record.get("pageCount") or 1) == 1
                else None
            )
        elif draft:
            latest_hash = draft.get("fileSha256")
        unchanged = bool(not creating and latest_hash == content_hash)
        effective_slug = slug or draft.get("slug")
        ref_for_url = effective_slug or target_draft_id
        public_url = f"{base_url.rstrip('/')}/d/{ref_for_url}"
        multi_page = len(pages) > 1
        title = title_from_html(index_html, filename or draft.get("filename"))
        stamp = now_iso()

        if unchanged:
            version = latest_version
            version_id = (
                latest_record.get("versionId")
                if latest_record and latest_record.get("versionId")
                else f"legacy-{version}"
            )
            updated_draft = {
                **draft,
                "title": title,
                "slug": effective_slug,
                "publicUrl": public_url,
                "description": clean_text(description, 1000) or draft.get("description"),
                **({"workspace": workspace} if workspace is not None else {}),
                **({"visibility": visibility} if visibility is not None else {}),
                **{
                    field: metadata.get(field) or draft.get(field)
                    for field in ("repoHost", "repoOrg", "repoName")
                },
            }
            data["drafts"][target_draft_id] = updated_draft
        else:
            if latest_version >= MAX_VERSION_NUMBER:
                raise StorageError("Draft has reached the maximum version number.")
            version = latest_version + 1
            version_id = new_id()
            page_records = []
            external_hosts: set[str] = set()
            index_has_script = False
            authenticated = account_id != "anonymous"
            for page in pages:
                checked = (
                    page_checks[page["path"]]
                    if page_checks and page["path"] in page_checks
                    else validate_html_detailed(page["html"], max_bytes=None)
                )
                external_hosts.update(checked["externalImageHosts"])
                if page["path"] == "index.html":
                    index_has_script = checked["hasScripts"]
                page_records.append(
                    {
                        "path": page["path"],
                        "fileSha256": sha256_text(page["html"]),
                        "fileSize": checked["bytes"],
                        "title": checked["title"],
                        "hasInlineScript": checked["hasScripts"],
                        "allowInlineScript": bool(checked["hasScripts"] and authenticated),
                    }
                )
            version_entry = {
                **metadata,
                **audit,
                "versionId": version_id,
                "versionNumber": version,
                "createdAt": stamp,
                "fileSize": total_bytes,
                "fileSha256": sha256_text(index_html),
                "contentHash": content_hash,
                "filename": filename,
                "hasInlineScript": index_has_script,
                "allowInlineScript": bool(index_has_script and authenticated),
                "externalImageHosts": sorted(external_hosts),
                "pageCount": len(pages),
                "eventType": "draft.created" if creating else "draft.updated",
            }
            if multi_page:
                version_entry["pages"] = page_records
            versions[str(version)] = version_entry
            object_path = self._write_page_object(target_draft_id, version, "index.html", index_html)
            for page in extra_pages:
                self._write_page_object(target_draft_id, version, page["path"], page["html"])
            stored_workspace = workspace if workspace is not None else (default_workspace if creating else None)
            data["drafts"][target_draft_id] = {
                **draft,
                "draftId": target_draft_id,
                "slug": effective_slug,
                "title": title,
                "filename": filename,
                "latestVersionNumber": version,
                "currentObject": str(object_path),
                "fileSha256": sha256_text(index_html),
                "createdAt": draft.get("createdAt") or stamp,
                "updatedAt": stamp,
                "publicUrl": public_url,
                "accountId": account_id,
                "description": clean_text(description, 1000) or draft.get("description"),
                "versions": versions,
                **({"workspace": stored_workspace} if stored_workspace is not None else {}),
                **({"visibility": visibility} if visibility is not None else {}),
                **{
                    field: metadata.get(field) or draft.get(field)
                    for field in ("repoHost", "repoOrg", "repoName")
                },
            }

        stored_draft = data["drafts"][target_draft_id]
        if tags is not None:
            stored_draft["tags"] = tags
        if favorite is True:
            stored_draft["favoritedAt"] = stored_draft.get("favoritedAt") or stamp
        elif favorite is False:
            stored_draft["favoritedAt"] = None
        search_body = extract_searchable_text(
            " ".join(page["html"] for page in sorted(pages, key=lambda p: p["path"] != "index.html"))
        )

        version_url = f"{base_url.rstrip('/')}/d/{target_draft_id}/v/{version}{'/' if multi_page else ''}"
        result = {
            "draftId": target_draft_id,
            "slug": effective_slug,
            "title": title,
            "versionNumber": version,
            "publicUrl": public_url,
            "rawUrl": public_url + "/raw",
            "versionUrl": version_url,
            "versionId": version_id,
            "fileSha256": sha256_text(index_html),
            "contentHash": content_hash,
            "totalBytes": total_bytes,
            "unchanged": unchanged,
            "requestId": audit.get("requestId"),
            **({"pages": [{"path": p["path"], "url": f"{public_url}/{p['path']}"} for p in pages]} if multi_page else {}),
            **({"theme": metadata["theme"]} if "theme" in metadata else {}),
        }
        if request_key:
            data["idempotency"][request_key] = {"fingerprint": fingerprint, "result": result}
            while len(data["idempotency"]) > MAX_IDEMPOTENCY_RECEIPTS:
                del data["idempotency"][next(iter(data["idempotency"]))]

        def write_grants(connection: sqlite3.Connection) -> None:
            stamp_now = now_iso()
            for grant in grants or []:
                connection.execute(
                    """INSERT INTO draft_grants (draft_id, principal_type, principal_value, role,
                           granted_by, created_at)
                       VALUES (?, ?, ?, ?, ?, ?)
                       ON CONFLICT (draft_id, principal_type, principal_value)
                       DO UPDATE SET role = excluded.role, granted_by = excluded.granted_by""",
                    (target_draft_id, grant["type"], grant["value"].lower(), grant["role"],
                     account_id, stamp_now),
                )

        def write_extras(connection: sqlite3.Connection) -> None:
            self._index_draft(connection, data["drafts"][target_draft_id], search_body)
            if grants:
                write_grants(connection)

        # The draft, its search row, and its grants commit together, so a
        # reader never sees the draft without the grants that the upload asked for.
        self.save_meta(data, also=write_extras)
        return {**result, "created": creating, "replayed": False}

    def get(self, draft_id: str, version: int | None = None) -> tuple[dict | None, str | None]:
        draft, _ver, doc, _scripts, _redirect = self.get_page(draft_id, version, "")
        return draft, doc

    def get_page(
        self,
        ref: str,
        version: int | None = None,
        page_path: str = "",
    ) -> tuple[dict | None, int | None, str | None, bool, str | None]:
        """Resolve a draft page, script policy, or trailing-slash redirect."""
        with STORE_LOCK:
            data = self.load_meta()
            draft_id, draft = self._resolve_ref(data, ref)
            if not draft_id or not draft or draft.get("deletedAt") or draft.get("disabledAt"):
                return None, None, None, False, None
            target_version = draft["latestVersionNumber"] if version is None else version
            record = draft.get("versions", {}).get(str(target_version))
            multi_page = bool(record and (record.get("pageCount") or 1) > 1 and record.get("pages"))
            authenticated = draft.get("accountId", "local") != "anonymous"
            if page_path in {"", "index.html"}:
                if target_version == draft["latestVersionNumber"]:
                    doc = self._read_current_object(draft)
                else:
                    path = self.drafts / draft_id / f"v{target_version}.html"
                    if not path.exists():
                        return None, None, None, False, None
                    if record:
                        doc = self._read_page_object(path, record["fileSha256"])
                    else:
                        doc = self._read_legacy_version(path)
                allow_scripts = bool(record and record.get("allowInlineScript"))
                return draft, target_version, doc, bool(allow_scripts and authenticated), None
            if not multi_page or record is None:
                return None, None, None, False, None
            pages_by_path = {item["path"]: item for item in record["pages"]}
            if page_path.endswith("/"):
                candidates = [f"{page_path}index.html"]
            elif page_path.endswith(".html"):
                candidates = [page_path]
            else:
                candidates = [f"{page_path}.html", f"{page_path}/index.html"]
            matched = next((pages_by_path[c] for c in candidates if c in pages_by_path), None)
            if matched is None:
                return None, None, None, False, None
            if matched["path"] == f"{page_path}/index.html":
                return draft, target_version, None, False, "slash"
            if matched["path"] == "index.html":
                path = self.drafts / draft_id / f"v{target_version}.html"
            else:
                path = self.drafts / draft_id / f"v{target_version}" / matched["path"]
            if not path.exists():
                raise StorageError("Recorded draft page is unavailable.")
            doc = self._read_page_object(path, matched["fileSha256"])
            allow_scripts = bool(matched.get("allowInlineScript"))
            return draft, target_version, doc, bool(allow_scripts and authenticated), None

    def get_source(self, ref: str, *, account_id: str | None = None) -> dict | None:
        """Return every page of a draft's latest version for download."""
        with STORE_LOCK:
            data = self.load_meta()
            draft_id, draft = self._resolve_ref(data, ref)
            if not draft_id or not draft or draft.get("deletedAt") or draft.get("disabledAt"):
                return None
            latest = draft["latestVersionNumber"]
            record = draft.get("versions", {}).get(str(latest))
            file_specs = [("index.html", Path(draft["currentObject"]), draft["fileSha256"])]
            if record and record.get("pages"):
                for item in sorted(record["pages"], key=lambda entry: entry["path"]):
                    if item["path"] == "index.html":
                        continue
                    path = self.drafts / draft_id / f"v{latest}" / item["path"]
                    file_specs.append((item["path"], path, item["fileSha256"]))
            summary = {
                "ok": True,
                "draftId": draft_id,
                "slug": draft.get("slug"),
                "title": draft["title"],
                "versionNumber": latest,
                "owned": bool(account_id and draft.get("accountId", "local") == account_id),
            }
        pages = [
            {"path": rel_path, "html": self._read_page_object(obj_path, expected_sha)}
            for rel_path, obj_path, expected_sha in file_specs
        ]
        return {**summary, "pages": pages}

    def slug_status(self, slug: str, account_id: str) -> dict:
        problem = slug_problem(slug)
        if problem is not None:
            return {"slug": slug, "status": "unavailable", "message": problem}
        with STORE_LOCK:
            data = self.load_meta()
            if slug in data.get("drafts", {}):
                return {
                    "slug": slug,
                    "status": "unavailable",
                    "message": f'The custom URL "{slug}" conflicts with an existing draft ID.',
                }
            existing = self._find_by_slug(data, slug)
            if existing is None:
                return {"slug": slug, "status": "available"}
            if existing.get("accountId", "local") == account_id:
                return {"slug": slug, "status": "yours"}
            return {
                "slug": slug,
                "status": "taken",
                "message": f'The custom URL "{slug}" belongs to another account.',
            }

    def _read_legacy_version(self, path: Path) -> str:
        try:
            if (path.is_symlink() or not path.resolve(strict=True).is_relative_to(self.drafts)
                    or not path.is_file() or path.stat().st_size > MAX_HTML_BYTES):
                raise StorageError("Historical draft object path is invalid.")
            return path.read_bytes().decode("utf-8")
        except (OSError, UnicodeError, RuntimeError) as exc:
            raise StorageError("Historical draft object is unavailable.") from exc

    def _version_records(self, draft: dict) -> dict:
        records = dict(draft.get("versions", {}))
        for path in (self.drafts / draft["draftId"]).glob("v*.html"):
            match = re.fullmatch(r"v([1-9][0-9]{0,8})\.html", path.name)
            if not match or int(match[1]) > draft["latestVersionNumber"]:
                continue
            number = int(match[1])
            if str(number) in records:
                continue
            doc = self._read_legacy_version(path)
            records[str(number)] = {
                "versionNumber": number, "versionId": f"legacy-{number}",
                "createdAt": datetime.fromtimestamp(path.stat().st_mtime, UTC).isoformat(),
                "fileSize": len(doc.encode("utf-8")), "fileSha256": sha256_text(doc),
                "filename": draft.get("filename"), "legacy": True,
            }
        return records

    @classmethod
    def _owned_draft(cls, data: dict, ref: str, account_id: str) -> dict:
        _draft_id, draft = cls._resolve_ref(data, ref)
        if (not draft or draft.get("deletedAt")
                or draft.get("accountId", "local") != account_id):
            raise KeyError("Draft not found.")
        return draft

    def public_draft(self, draft: dict, base_url: str, *, version_count: int | None = None) -> dict:
        """Return the list form of one draft record."""
        draft_id = draft["draftId"]
        public_url = f"{base_url.rstrip('/')}/d/{draft.get('slug') or draft_id}"
        return {
            **{key: draft.get(key) for key in (
                "draftId", "slug", "title", "description", "repoHost", "repoOrg", "repoName",
                "latestVersionNumber", "createdAt", "updatedAt", "disabledReason", "favoritedAt",
                "archivedAt", "workspace", "visibility", "accountId",
            )},
            "favorite": bool(draft.get("favoritedAt")),
            "archived": bool(draft.get("archivedAt")),
            "tags": list(draft.get("tags") or []),
            "versionCount": len(self._version_records(draft)) if version_count is None else version_count,
            "latestVersionAt": draft["updatedAt"], "disabled": bool(draft.get("disabledAt")),
            "publicUrl": public_url, "rawUrl": public_url + "/raw",
            "shareUrl": public_url + "/share", "versionBaseUrl": f"{base_url.rstrip('/')}/d/{draft_id}",
        }

    @staticmethod
    def _row_version_count(draft: dict, count: int) -> int | None:
        """Trust the history rows only when they cover the latest version.

        A legacy draft with no rows, or only some rows, reads its files instead.
        """
        return count if count and count >= (draft.get("latestVersionNumber") or 0) else None

    def foreign_live_drafts(self, account_id: str) -> list[tuple[dict, int | None]]:
        """Return live drafts of other accounts with their version counts, from SQLite only."""
        sql = (
            "SELECT d.doc, (SELECT COUNT(*) FROM draft_versions v WHERE v.draft_id = d.draft_id)"
            " FROM drafts d WHERE COALESCE(d.account_id, 'local') <> ? AND d.deleted_at IS NULL"
            " AND d.disabled_at IS NULL ORDER BY d.rowid"
        )
        with STORE_LOCK, self._connection() as connection:
            try:
                return [(draft, self._row_version_count(draft, count))
                        for draft, count in ((json.loads(doc), count)
                                             for doc, count in connection.execute(sql, (account_id,)).fetchall())]
            except (ValueError, KeyError) as exc:
                raise StorageError("Metadata is unreadable or malformed.") from exc

    def list_drafts(self, account_id: str, base_url: str, *, include_archived: bool = False) -> list[dict]:
        """List owned live drafts from SQLite, favorites first, then newest first."""
        sql = (
            "SELECT d.doc, (SELECT COUNT(*) FROM draft_versions v WHERE v.draft_id = d.draft_id)"
            " FROM drafts d WHERE COALESCE(d.account_id, 'local') = ? AND d.deleted_at IS NULL"
            + ("" if include_archived else " AND d.archived_at IS NULL")
            + " ORDER BY (d.favorited_at IS NULL), d.updated_at DESC, d.rowid"
        )
        with STORE_LOCK, self._connection() as connection:
            rows = connection.execute(sql, (account_id,)).fetchall()
            try:
                result = []
                for doc, count in rows:
                    draft = json.loads(doc)
                    result.append(self.public_draft(draft, base_url, version_count=self._row_version_count(draft, count)))
            except (ValueError, KeyError) as exc:
                raise StorageError("Metadata is unreadable or malformed.") from exc
            return result

    def draft_detail(self, account_id: str, ref: str, base_url: str) -> dict:
        with STORE_LOCK:
            draft = self._owned_draft(self.load_meta(), ref, account_id)
            public = self.public_draft(draft, base_url)
            versions = sorted(self._version_records(draft).values(),
                              key=lambda version: version["versionNumber"], reverse=True)
            return {"ok": True, "draft": public, "versions": versions}

    def _edit_drafts(self, account_id: str, refs: list[str], apply, *, counts: bool = False
                     ) -> list[tuple[dict, int | None]]:
        """Change owned live drafts in one transaction, one row each.

        `apply(doc, stamp)` edits the draft record in place. A missing or foreign
        draft rolls back every change. The change never touches `updatedAt`.
        It returns each new record with its version count, or None unless `counts` is set.
        """
        def operation(connection: sqlite3.Connection) -> list[tuple[dict, int | None]]:
            stamp = now_iso()
            changed: dict[str, tuple[dict, int | None]] = {}
            for ref in refs:
                if not isinstance(ref, str):
                    raise ValueError("Each draft ID must be text.")
                row = connection.execute(
                    "SELECT draft_id, doc FROM drafts WHERE deleted_at IS NULL"
                    " AND COALESCE(account_id, 'local') = ? AND (draft_id = ? OR slug = ?)",
                    (account_id, ref.lower(), ref.lower()),
                ).fetchone()
                if row is None:
                    raise KeyError("Draft not found.")
                draft_id, doc_text = row
                if draft_id in changed:
                    continue
                doc = json.loads(doc_text)
                apply(doc, stamp)
                connection.execute(
                    "UPDATE drafts SET doc = ?, favorited_at = ?, archived_at = ? WHERE draft_id = ?",
                    (_json_text(doc), _text_or_none(doc.get("favoritedAt")),
                     _text_or_none(doc.get("archivedAt")), draft_id),
                )
                self._sync_draft_tags(connection, draft_id, doc.get("tags") or [])
                count = None
                if counts:
                    count = self._row_version_count(doc, connection.execute(
                        "SELECT COUNT(*) FROM draft_versions WHERE draft_id = ?", (draft_id,)).fetchone()[0])
                changed[draft_id] = (doc, count)
            return list(changed.values())

        return self._write(operation)

    @staticmethod
    def _sync_draft_tags(connection: sqlite3.Connection, draft_id: str, tags: list[str]) -> None:
        stored = {row[0] for row in connection.execute(
            "SELECT tag FROM draft_tags WHERE draft_id = ?", (draft_id,))}
        wanted = set(tags)
        if stored == wanted:
            return
        for tag in wanted - stored:
            connection.execute("INSERT INTO draft_tags (draft_id, tag) VALUES (?, ?)", (draft_id, tag))
        for tag in stored - wanted:
            connection.execute("DELETE FROM draft_tags WHERE draft_id = ? AND tag = ?", (draft_id, tag))
        connection.execute(
            "UPDATE draft_search SET tags = ? WHERE draft_id = ?", (" ".join(tags), draft_id))

    def set_favorite(self, account_id: str, ref: str, favorite: bool) -> str:
        """Pin or unpin an owned draft. It keeps updatedAt."""
        def apply(doc: dict, stamp: str) -> None:
            doc["favoritedAt"] = (doc.get("favoritedAt") or stamp) if favorite else None

        return self._edit_drafts(account_id, [ref], apply)[0][0]["draftId"]

    def set_archived(self, account_id: str, ref: str, archived: bool) -> str:
        """Archive or restore an owned draft. It keeps updatedAt and favoritedAt."""
        def apply(doc: dict, stamp: str) -> None:
            doc["archivedAt"] = (doc.get("archivedAt") or stamp) if archived else None

        return self._edit_drafts(account_id, [ref], apply)[0][0]["draftId"]

    def set_tags(self, account_id: str, ref: str, tags: list[str]) -> tuple[str, list[str]]:
        """Replace the tags of an owned draft. Raise ValueError for a bad tag."""
        wanted = normalize_tags(tags)

        def apply(doc: dict, stamp: str) -> None:
            doc["tags"] = wanted

        return self._edit_drafts(account_id, [ref], apply)[0][0]["draftId"], wanted

    def patch_draft(self, account_id: str, ref: str, base_url: str, *, tags: list[str] | None = None,
                    add_tags: list[str] | None = None, remove_tags: list[str] | None = None,
                    archived: bool | None = None, favorite: bool | None = None) -> dict:
        """Change tags, archive, and star of one owned draft in one transaction.

        `tags` replaces the list. `add_tags` and `remove_tags` then edit it.
        It returns the new public draft. A bad tag changes nothing.
        """
        replace = None if tags is None else normalize_tags(tags)
        added = normalize_tags(add_tags)
        removed = set(normalize_tags(remove_tags))

        def apply(doc: dict, stamp: str) -> None:
            if replace is not None or added or removed:
                current = list(doc.get("tags") or []) if replace is None else list(replace)
                doc["tags"] = normalize_tags([tag for tag in current + added if tag not in removed])
            if favorite is not None:
                doc["favoritedAt"] = (doc.get("favoritedAt") or stamp) if favorite else None
            if archived is not None:
                doc["archivedAt"] = (doc.get("archivedAt") or stamp) if archived else None

        doc, count = self._edit_drafts(account_id, [ref], apply, counts=True)[0]
        return self.public_draft(doc, base_url, version_count=count)

    BULK_ACTIONS = ("favorite", "unfavorite", "archive", "unarchive", "add_tag", "remove_tag")

    def bulk_update(self, account_id: str, draft_ids: list[str], *, action: str,
                    tag: str | None = None) -> list[dict]:
        """Apply one action to up to 200 owned drafts in one transaction.

        It returns the new state of each draft. A bad ID or tag changes nothing.
        """
        if action not in self.BULK_ACTIONS:
            raise ValueError("Unknown bulk action.")
        if not isinstance(draft_ids, list) or not draft_ids:
            raise ValueError("Send a list of draft IDs.")
        if len(draft_ids) > MAX_BULK_DRAFTS:
            raise ValueError(f"A bulk change takes at most {MAX_BULK_DRAFTS} drafts.")
        wanted_tag = None
        if action in {"add_tag", "remove_tag"}:
            wanted_tag = (normalize_tags([tag]) if isinstance(tag, str) else None)
            if not wanted_tag:
                raise ValueError("Send one valid tag.")
            wanted_tag = wanted_tag[0]

        def apply(doc: dict, stamp: str) -> None:
            if action == "favorite":
                doc["favoritedAt"] = doc.get("favoritedAt") or stamp
            elif action == "unfavorite":
                doc["favoritedAt"] = None
            elif action == "archive":
                doc["archivedAt"] = doc.get("archivedAt") or stamp
            elif action == "unarchive":
                doc["archivedAt"] = None
            elif action == "add_tag":
                doc["tags"] = normalize_tags([*(doc.get("tags") or []), wanted_tag])
            else:
                doc["tags"] = [item for item in doc.get("tags") or [] if item != wanted_tag]

        return [
            {"draftId": doc["draftId"], "favorite": bool(doc.get("favoritedAt")),
             "archived": bool(doc.get("archivedAt")), "tags": list(doc.get("tags") or [])}
            for doc, _count in self._edit_drafts(account_id, draft_ids, apply)
        ]

    def granted_draft_ids(self, account_id: str) -> set[str]:
        """Return the IDs of owned live drafts that have at least one sharing grant."""
        return {row[0] for row in self._rows(
            "SELECT DISTINCT g.draft_id FROM draft_grants g JOIN drafts d ON d.draft_id = g.draft_id"
            " WHERE COALESCE(d.account_id, 'local') = ? AND d.deleted_at IS NULL",
            (account_id,),
        )}

    @staticmethod
    def _fts_text(value: str) -> str:
        """Remove the two control characters that mark a match in a snippet."""
        return value.replace("\x01", " ").replace("\x02", " ")

    @staticmethod
    def safe_snippet(raw: str) -> str:
        """Escape a snippet, then turn the match markers into `<mark>` tags."""
        return html.escape(raw).replace("\x01", "<mark>").replace("\x02", "</mark>")

    SEARCH_ID_CHUNK = 500

    def search_fts(self, account_id: str, fts_query: str, *, draft_ids: set[str] | None = None,
                   exclude_tags: bool = False) -> dict[str, tuple[float, str]]:
        """Run a safe FTS5 query. Return `{draft_id: (rank, snippet_html)}`, best rank first.

        Without `draft_ids`, it searches the live drafts of `account_id`.
        With `draft_ids`, the caller has already checked read access to them,
        and the SQL reads only those rows. `exclude_tags` is for a reader who is not
        the owner: tags never match and never show in a snippet.
        """
        if not fts_query or (draft_ids is not None and not draft_ids):
            return {}
        match = "{title slug description body} : (" + fts_query + ")" if exclude_tags else fts_query
        mark = "char(1), char(2), '...', 16"
        if exclude_tags:
            snippets = ", ".join(f"snippet(draft_search, {column}, {mark})" for column in (5, 3, 1, 2))
        else:
            snippets = f"snippet(draft_search, -1, {mark})"
        base = (
            f"SELECT draft_search.draft_id, bm25(draft_search, 0.0, 10.0, 8.0, 4.0, 6.0, 1.0), {snippets}"
            " FROM draft_search JOIN drafts AS d ON d.draft_id = draft_search.draft_id"
            " WHERE draft_search MATCH ? AND d.deleted_at IS NULL"
        )
        if draft_ids is None:
            queries = [(base + " AND COALESCE(d.account_id, 'local') = ?", (match, account_id))]
        else:
            ids = sorted(draft_ids)
            queries = [
                (base + f" AND d.draft_id IN ({','.join('?' * len(chunk))})", (match, *chunk))
                for chunk in (ids[start:start + self.SEARCH_ID_CHUNK]
                              for start in range(0, len(ids), self.SEARCH_ID_CHUNK))
            ]
        rows: list[tuple] = []
        with STORE_LOCK, self._connection() as connection:
            try:
                for sql, params in queries:
                    rows.extend(connection.execute(sql, params).fetchall())
            except sqlite3.OperationalError:
                return {}
        rows.sort(key=lambda row: row[1])
        found: dict[str, tuple[float, str]] = {}
        for draft_id, rank, *texts in rows:
            # A snippet from the first column that holds a marked match.
            snippet = next((text for text in texts if text and "\x01" in text), texts[0] or "")
            found[draft_id] = (rank, self.safe_snippet(snippet))
        return found

    def change_draft(self, account_id: str, ref: str, action: str, reason: str | None) -> None:
        with STORE_LOCK:
            data = self.load_meta()
            draft = self._owned_draft(data, ref, account_id)
            stamp = now_iso()
            if action == "delete":
                draft["deletedAt"] = stamp
                draft.pop("tags", None)
                if draft.get("slug"):
                    old_slug = draft["slug"]
                    draft["slug"] = None
                    if draft["publicUrl"].endswith(f"/d/{old_slug}"):
                        draft["publicUrl"] = (
                            draft["publicUrl"][: -len(old_slug)] + draft["draftId"]
                        )
            elif action == "disable":
                draft.update(disabledAt=stamp, disabledReason=clean_text(reason, 1000) or "Disabled by owner.")
            elif action == "enable":
                draft.update(disabledAt=None, disabledReason=None)
            else:
                raise ValueError("Unknown draft action.")
            draft["updatedAt"] = stamp
            def drop_rows(connection: sqlite3.Connection) -> None:
                connection.execute("DELETE FROM draft_search WHERE draft_id = ?", (draft["draftId"],))
                connection.execute("DELETE FROM draft_tags WHERE draft_id = ?", (draft["draftId"],))

            self.save_meta(data, also=drop_rows if action == "delete" else None)

    def account(self, account_id: str) -> dict | None:
        if account_id == "local":
            return {"accountId": "local", "accountName": "Tailplan local"}
        if account_id == "anonymous":
            return {"accountId": "anonymous", "accountName": "Anonymous"}
        value = self.load_meta().get("accounts", {}).get(account_id)
        return {"accountId": account_id, "accountName": value["name"]} if value else None

    def identity_account(self, login: str, name: str, owner_login: str) -> dict:
        with STORE_LOCK:
            normalized = login.strip().lower()
            if owner_login and normalized == owner_login.strip().lower():
                return self.account("local")
            account_id = sha256_text("tailscale:" + normalized)[:32]
            data = self.load_meta()
            data.setdefault("accounts", {})[account_id] = {"name": clean_text(name) or login}
            self.save_meta(data)
            return self.account(account_id)

    def authenticate(self, token: str, bootstrap: str) -> dict | None:
        if token and hmac.compare_digest(token.encode(), bootstrap.encode()):
            return {**self.account("local"), "apiKeyId": "bootstrap", "apiKeyName": "Bootstrap"}
        if not token or len(token) > 512:
            return None
        digest = sha256_text(token)
        with STORE_LOCK:
            data = self.load_meta()
            for key_id, key in data.get("apiKeys", {}).items():
                if key.get("revokedAt") or not hmac.compare_digest(key["keyHash"], digest):
                    continue
                if _expired_iso(key.get("expiresAt")):
                    return None
                last = key.get("lastUsedAt")
                stamp = now_iso()
                if not last or last[:16] != stamp[:16]:
                    key["lastUsedAt"] = stamp
                    self.save_meta(data)
                return {**self.account(key["accountId"]), "apiKeyId": key_id, "apiKeyName": key["name"]}
        return None

    def create_key(self, account_id: str, name: str | None, ttl_seconds: int | None = None) -> dict:
        with STORE_LOCK:
            data = self.load_meta()
            if self.account(account_id) is None or account_id == "anonymous":
                raise KeyError("Account not found.")
            token = "tp_" + secrets.token_urlsafe(32)
            key_id = new_id()
            key = {"accountId": account_id, "name": clean_text(name) or "CLI API key",
                   "keyHash": sha256_text(token), "createdAt": now_iso(), "lastUsedAt": None}
            if ttl_seconds:
                key["expiresAt"] = datetime.fromtimestamp(time.time() + ttl_seconds, UTC).isoformat().replace(
                    "+00:00", "Z")
            data.setdefault("apiKeys", {})[key_id] = key
            self.save_meta(data)
            return {"ok": True, "apiKey": {"id": key_id, "name": key["name"],
                                           "expiresAt": key.get("expiresAt")}, "token": token}

    def list_keys(self, account_id: str) -> list[dict]:
        with STORE_LOCK:
            return sorted([
                {"id": key_id, **{field: key.get(field)
                                  for field in ("name", "createdAt", "lastUsedAt", "expiresAt")}}
                for key_id, key in self.load_meta().get("apiKeys", {}).items()
                if key["accountId"] == account_id and not key.get("revokedAt")
                and not _expired_iso(key.get("expiresAt"))
            ], key=lambda key: key["createdAt"], reverse=True)

    def revoke_key(self, account_id: str, key_id: str) -> None:
        with STORE_LOCK:
            data = self.load_meta()
            key = data.get("apiKeys", {}).get(key_id)
            if not key or key["accountId"] != account_id or key.get("revokedAt"):
                raise KeyError("API key not found.")
            stamp = now_iso()
            key["revokedAt"] = stamp
            self.save_meta(data)
            self._write(lambda connection: connection.execute(
                "UPDATE sessions SET revoked_at = ? WHERE account_id = ? AND api_key_id = ? AND revoked_at IS NULL",
                (stamp, account_id, key_id),
            ))

    def check_ready(self) -> None:
        """Check that metadata and storage work, without reading draft contents.

        The check runs a SQLite integrity probe, validates the records, confirms that
        each current draft object exists, and writes a probe file. It never hashes
        draft files, so the cost stays low with thousands of drafts.
        """
        with STORE_LOCK:
            with self._connection() as connection:
                if connection.execute("PRAGMA quick_check").fetchall() != [("ok",)]:
                    raise StorageError("Metadata database failed its integrity check.")
                if connection.execute("PRAGMA user_version").fetchone()[0] != SCHEMA_VERSION:
                    raise StorageError("Metadata database has an unexpected schema version.")
                with self._write_transaction(connection):
                    connection.execute(f"PRAGMA user_version = {SCHEMA_VERSION}")
            data = self.load_meta()
            for draft in data.get("drafts", {}).values():
                try:
                    current = os.lstat(draft["currentObject"])
                except OSError as exc:
                    raise StorageError("Recorded draft object is unavailable.") from exc
                if not stat.S_ISREG(current.st_mode):
                    raise StorageError("Recorded draft object path is invalid.")
            probe = self.root / f".ready-{secrets.token_hex(8)}"
            try:
                _atomic_write_text(probe, "ready\n")
            finally:
                probe.unlink(missing_ok=True)

    # -- Version 2 records: principals, sessions, sharing, devices, audit --

    # The audit wide events go to this stream as JSON lines. None means stdout, or stderr for a failure.
    audit_stream = None
    _audit_stream_lock = threading.Lock()

    def _write(self, operation):
        with STORE_LOCK, self._connection() as connection:
            with self._write_transaction(connection):
                return operation(connection)

    def _rows(self, sql: str, params: tuple = ()) -> list[sqlite3.Row]:
        with self._connection() as connection:
            connection.row_factory = sqlite3.Row
            return connection.execute(sql, params).fetchall()

    def audit(
        self,
        event: str,
        *,
        actor: dict | None = None,
        target_type: str | None = None,
        target_id: str | None = None,
        outcome: str = "success",
        detail: dict | None = None,
    ) -> dict:
        """Append one audit event to the database and print its wide event as one JSON line."""
        actor = actor or {}
        record = {
            "ts": now_iso(),
            "service": "tailplan",
            "event": event,
            "actor_account_id": actor.get("accountId"),
            "actor_email": actor.get("email"),
            "target_type": target_type,
            "target_id": target_id,
            "outcome": outcome,
            "detail": detail or {},
        }
        self._write(lambda connection: connection.execute(
            "INSERT INTO audit_events (ts, event_type, actor_account_id, actor_email, target_type,"
            " target_id, outcome, detail_json) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (record["ts"], event, record["actor_account_id"], record["actor_email"], target_type,
             target_id, outcome, _json_text(record["detail"])),
        ))
        row = audit_wide_event(event, actor, target_type, target_id, outcome, detail)
        line = json.dumps(row, separators=(",", ":"), sort_keys=True)
        with self._audit_stream_lock:
            # A failed event goes to stderr with no prefix: the Docker journald driver sets its priority.
            stream = self.audit_stream or (sys.stdout if row["outcome"] == "success" else sys.stderr)
            stream.write(line + "\n")
            stream.flush()
        return record

    def list_audit(self, *, limit: int = 100, before: int | None = None, event: str | None = None) -> list[dict]:
        clauses, params = [], []
        if before is not None:
            clauses.append("id < ?")
            params.append(before)
        if event:
            clauses.append("event_type = ?")
            params.append(event)
        where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
        rows = self._rows(f"SELECT * FROM audit_events {where} ORDER BY id DESC LIMIT ?", (*params, limit))
        return [{
            "id": row["id"], "ts": row["ts"], "event": row["event_type"],
            "actorAccountId": row["actor_account_id"], "actorEmail": row["actor_email"],
            "targetType": row["target_type"], "targetId": row["target_id"],
            "outcome": row["outcome"], "detail": json.loads(row["detail_json"] or "{}"),
        } for row in rows]

    def ensure_account(self, account_id: str, name: str) -> dict:
        """Create or rename a non-local account record."""
        if account_id in {"local", "anonymous"}:
            return self.account(account_id)
        with STORE_LOCK:
            data = self.load_meta()
            accounts = data.setdefault("accounts", {})
            if accounts.get(account_id, {}).get("name") != name:
                accounts[account_id] = {**accounts.get(account_id, {}), "name": name}
                self.save_meta(data)
            return self.account(account_id)

    @staticmethod
    def _principal_from_row(row: sqlite3.Row | None) -> dict | None:
        if row is None:
            return None
        workspaces = json.loads(row["workspaces_json"] or "{}")
        return {
            "issuer": row["issuer"], "subject": row["subject"], "accountId": row["account_id"],
            "email": row["email"], "emailVerified": bool(row["email_verified"]),
            "displayName": row["display_name"], "groups": json.loads(row["groups_json"] or "[]"),
            "instanceRole": row["instance_role"], "workspaces": workspaces.get("roles", {}),
            "defaultWorkspace": workspaces.get("default"),
            "sessionEpoch": row["session_epoch"], "lastLoginAt": row["last_login_at"],
        }

    def upsert_principal(
        self,
        *,
        issuer: str,
        subject: str,
        account_id: str,
        email: str | None,
        email_verified: bool,
        display_name: str | None,
        groups: list[str],
        instance_role: str | None,
        workspaces: dict[str, str],
        default_workspace: str | None = None,
    ) -> dict:
        stamp = now_iso()
        roles = {"roles": workspaces, "default": default_workspace}

        def operation(connection: sqlite3.Connection) -> None:
            connection.execute(
                """INSERT INTO principals (issuer, subject, account_id, email, email_verified,
                       display_name, groups_json, instance_role, workspaces_json, session_epoch,
                       last_login_at, created_at, updated_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 0, ?, ?, ?)
                   ON CONFLICT (issuer, subject) DO UPDATE SET
                       account_id = excluded.account_id, email = excluded.email,
                       email_verified = excluded.email_verified, display_name = excluded.display_name,
                       groups_json = excluded.groups_json, instance_role = excluded.instance_role,
                       workspaces_json = excluded.workspaces_json, last_login_at = excluded.last_login_at,
                       updated_at = excluded.updated_at""",
                (issuer, subject, account_id, email, int(bool(email_verified)), display_name,
                 _json_text(sorted(set(groups))), instance_role, _json_text(roles), stamp, stamp, stamp),
            )

        self._write(operation)
        return self.principal(issuer, subject)

    def principal(self, issuer: str, subject: str) -> dict | None:
        rows = self._rows("SELECT * FROM principals WHERE issuer = ? AND subject = ?", (issuer, subject))
        return self._principal_from_row(rows[0] if rows else None)

    def account_principal(self, account_id: str) -> dict | None:
        """Return the principal that signed in to the account most recently."""
        rows = self._rows(
            "SELECT * FROM principals WHERE account_id = ? ORDER BY last_login_at DESC LIMIT 1",
            (account_id,),
        )
        return self._principal_from_row(rows[0] if rows else None)

    def create_session(
        self,
        *,
        account_id: str,
        auth_method: str,
        csrf: str,
        ttl: int,
        issuer: str | None = None,
        subject: str | None = None,
        idp_session_id: str | None = None,
        api_key_id: str | None = None,
        epoch: int = 0,
    ) -> str:
        """Store a revocable browser session and return its secret ID."""
        session_id = secrets.token_urlsafe(32)
        self._write(lambda connection: connection.execute(
            """INSERT INTO sessions (session_id, account_id, principal_issuer, principal_subject,
                   idp_session_id, api_key_id, csrf, auth_method, epoch, expires_at, created_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (sha256_text(session_id), account_id, issuer, subject, idp_session_id, api_key_id,
             csrf, auth_method, epoch, int(time.time()) + ttl, now_iso()),
        ))
        return session_id

    def session_record(self, session_id: str) -> dict | None:
        """Return a live session, or None when it is unknown, revoked, expired, or stale."""
        if not isinstance(session_id, str) or not 16 <= len(session_id) <= 128:
            return None
        return self.session_by_hash(sha256_text(session_id))

    def session_by_hash(self, session_hash: str) -> dict | None:
        """Return a live session by the stored hash of its ID."""
        if not isinstance(session_hash, str) or SHA256_RE.fullmatch(session_hash) is None:
            return None
        rows = self._rows(
            """SELECT s.*, p.session_epoch AS principal_epoch FROM sessions s
               LEFT JOIN principals p ON p.issuer = s.principal_issuer AND p.subject = s.principal_subject
               WHERE s.session_id = ?""",
            (session_hash,),
        )
        if not rows:
            return None
        row = rows[0]
        if row["revoked_at"] or row["expires_at"] <= time.time():
            return None
        if row["principal_issuer"] is not None and row["principal_epoch"] != row["epoch"]:
            return None
        return {
            "sessionHash": row["session_id"], "accountId": row["account_id"], "csrf": row["csrf"],
            "authMethod": row["auth_method"], "apiKeyId": row["api_key_id"], "issuer": row["principal_issuer"],
            "subject": row["principal_subject"], "epoch": row["epoch"], "expiresAt": row["expires_at"],
        }

    def revoke_session(self, session_id: str) -> None:
        self._write(lambda connection: connection.execute(
            "UPDATE sessions SET revoked_at = ? WHERE session_id = ? AND revoked_at IS NULL",
            (now_iso(), sha256_text(session_id)),
        ))

    def revoke_principal_sessions(
        self, issuer: str, *, subject: str | None = None, idp_session_id: str | None = None
    ) -> int:
        """Revoke sessions for an IdP subject or IdP session. Return the count."""
        stamp = now_iso()

        def operation(connection: sqlite3.Connection) -> int:
            count = 0
            if subject is not None:
                connection.execute(
                    "UPDATE principals SET session_epoch = session_epoch + 1, updated_at = ?"
                    " WHERE issuer = ? AND subject = ?", (stamp, issuer, subject))
                count += connection.execute(
                    "UPDATE sessions SET revoked_at = ? WHERE principal_issuer = ? AND principal_subject = ?"
                    " AND revoked_at IS NULL", (stamp, issuer, subject)).rowcount
            if idp_session_id is not None:
                count += connection.execute(
                    "UPDATE sessions SET revoked_at = ? WHERE principal_issuer = ? AND idp_session_id = ?"
                    " AND revoked_at IS NULL", (stamp, issuer, idp_session_id)).rowcount
            return count

        return self._write(operation)

    # Draft sharing.

    def resolve_draft(self, ref: str) -> dict | None:
        """Return a live draft by ID or custom URL, or None."""
        with STORE_LOCK:
            _draft_id, draft = self._resolve_ref(self.load_meta(), ref)
        if not draft or draft.get("deletedAt"):
            return None
        return draft

    def list_grants(self, draft_id: str) -> list[dict]:
        rows = self._rows(
            "SELECT * FROM draft_grants WHERE draft_id = ? ORDER BY principal_type, principal_value",
            (draft_id,),
        )
        return [{"type": row["principal_type"], "value": row["principal_value"], "role": row["role"],
                 "grantedBy": row["granted_by"], "createdAt": row["created_at"]} for row in rows]

    def add_grant(self, draft_id: str, principal_type: str, value: str, role: str, granted_by: str) -> None:
        self._write(lambda connection: connection.execute(
            """INSERT INTO draft_grants (draft_id, principal_type, principal_value, role, granted_by, created_at)
               VALUES (?, ?, ?, ?, ?, ?)
               ON CONFLICT (draft_id, principal_type, principal_value)
               DO UPDATE SET role = excluded.role, granted_by = excluded.granted_by""",
            (draft_id, principal_type, value.lower(), role, granted_by, now_iso()),
        ))

    def remove_grant(self, draft_id: str, principal_type: str, value: str) -> bool:
        return bool(self._write(lambda connection: connection.execute(
            "DELETE FROM draft_grants WHERE draft_id = ? AND principal_type = ? AND principal_value = ?",
            (draft_id, principal_type, value.lower()),
        ).rowcount))

    def grants_matching(self, pairs: list[tuple[str, str]]) -> dict[str, list[dict]]:
        """Return grants for any (type, value) pair, keyed by draft ID."""
        result: dict[str, list[dict]] = {}
        if not pairs:
            return result
        clause = " OR ".join("(principal_type = ? AND principal_value = ?)" for _ in pairs)
        params = tuple(item for pair in pairs for item in pair)
        for row in self._rows(f"SELECT * FROM draft_grants WHERE {clause}", params):
            result.setdefault(row["draft_id"], []).append(
                {"type": row["principal_type"], "value": row["principal_value"], "role": row["role"]})
        return result

    def set_sharing(self, draft_id: str, *, workspace: str | None = None, visibility: str | None = None) -> dict:
        with STORE_LOCK:
            data = self.load_meta()
            draft = data["drafts"].get(draft_id)
            if not draft or draft.get("deletedAt"):
                raise KeyError("Draft not found.")
            if workspace is not None:
                draft["workspace"] = workspace
            if visibility is not None:
                draft["visibility"] = visibility
            self.save_meta(data)
            return dict(draft)

    def all_live_drafts(self) -> list[dict]:
        with STORE_LOCK:
            return [dict(draft) for draft in self.load_meta()["drafts"].values() if not draft.get("deletedAt")]

    # Device login.

    def start_device_login(self, client_name: str | None, ttl: int = DEVICE_CODE_TTL) -> dict:
        device_code = secrets.token_urlsafe(32)
        stamp = now_iso()
        for _attempt in range(8):
            raw = "".join(secrets.choice(USER_CODE_ALPHABET) for _ in range(8))
            user_code = f"{raw[:4]}-{raw[4:]}"
            try:
                self._write(lambda connection: (
                    connection.execute("DELETE FROM device_codes WHERE expires_at < ?", (int(time.time()) - 3600,)),
                    connection.execute(
                        "INSERT INTO device_codes (device_code_hash, user_code, client_name, status,"
                        " expires_at, created_at) VALUES (?, ?, ?, 'pending', ?, ?)",
                        (sha256_text(device_code), user_code, clean_text(client_name, 80), int(time.time()) + ttl,
                         stamp)),
                ))
            except StorageError:
                continue
            return {"deviceCode": device_code, "userCode": user_code, "expiresIn": ttl}
        raise StorageError("Could not allocate a device code.")

    def device_login(self, user_code: str) -> dict | None:
        code = normalize_user_code(user_code)
        if code is None:
            return None
        rows = self._rows("SELECT * FROM device_codes WHERE user_code = ?", (code,))
        if not rows or rows[0]["expires_at"] <= time.time() or rows[0]["status"] != "pending":
            return None
        return {"userCode": rows[0]["user_code"], "clientName": rows[0]["client_name"],
                "expiresAt": datetime.fromtimestamp(rows[0]["expires_at"], UTC).isoformat().replace("+00:00", "Z")}

    def decide_device_login(self, user_code: str, account_id: str, approve: bool) -> dict | None:
        """Approve or deny a pending device login. Return None when the code is not pending."""
        code = normalize_user_code(user_code)
        if code is None:
            return None
        status = "approved" if approve else "denied"
        changed = self._write(lambda connection: connection.execute(
            "UPDATE device_codes SET status = ?, account_id = ? WHERE user_code = ? AND status = 'pending'"
            " AND expires_at > ?", (status, account_id, code, int(time.time()))).rowcount)
        return {"status": status} if changed else None

    def poll_device_login(self, device_code: str) -> dict:
        """Return the device login state. The first approved poll mints the key and consumes the code."""
        if not isinstance(device_code, str) or not 16 <= len(device_code) <= 128:
            return {"status": "expired"}
        code_hash = sha256_text(device_code)
        with STORE_LOCK:
            rows = self._rows("SELECT * FROM device_codes WHERE device_code_hash = ?", (code_hash,))
            if not rows or rows[0]["status"] == "consumed" or rows[0]["expires_at"] <= time.time():
                return {"status": "expired"}
            row = rows[0]
            if row["status"] != "approved":
                return {"status": row["status"]}
            consumed = self._write(lambda connection: connection.execute(
                "UPDATE device_codes SET status = 'consumed' WHERE device_code_hash = ? AND status = 'approved'",
                (code_hash,)).rowcount)
            if not consumed:
                return {"status": "expired"}
            name = "Device login" + (f": {row['client_name']}" if row["client_name"] else "")
            created = self.create_key(row["account_id"], name, DEVICE_KEY_TTL)
            self._write(lambda connection: connection.execute(
                "UPDATE device_codes SET issued_key = ? WHERE device_code_hash = ?",
                (created["apiKey"]["id"], code_hash)))
            return {"status": "approved", "accountId": row["account_id"], "apiKey": created["token"],
                    "keyId": created["apiKey"]["id"], "expiresAt": created["apiKey"]["expiresAt"]}


class RateLimiter:
    """Keep bounded request counters shared by both listeners."""

    def __init__(self) -> None:
        self.entries: OrderedDict = OrderedDict()
        self.lock = threading.Lock()

    def allow(self, key: str, maximum: int, window: int = 60) -> int:
        with self.lock:
            now = time.monotonic()
            start, count = self.entries.get(key, (now, 0))
            if now - start >= window:
                start, count = now, 0
            self.entries[key] = (start, count + 1)
            self.entries.move_to_end(key)
            while len(self.entries) > 8192:
                self.entries.popitem(last=False)
            return max(1, int(window - (now - start)) + 1) if count >= maximum else 0


class MissLimiter:
    """Count recent misses per key in a sliding window, with bounded memory."""

    def __init__(self) -> None:
        self.entries: OrderedDict = OrderedDict()
        self.lock = threading.Lock()

    @staticmethod
    def _recent(hits: deque, now: float, window: int) -> None:
        while hits and now - hits[0] >= window:
            hits.popleft()

    def wait(self, key: str, maximum: int, window: int = 60) -> int:
        """Return the seconds to wait when the key is over its limit, else 0."""
        with self.lock:
            hits = self.entries.get(key)
            if not hits:
                return 0
            now = time.monotonic()
            self._recent(hits, now, window)
            if len(hits) < maximum:
                return 0
            return max(1, int(window - (now - hits[0])) + 1)

    def record(self, key: str, maximum: int, window: int = 60) -> None:
        with self.lock:
            now = time.monotonic()
            hits = self.entries.setdefault(key, deque())
            self._recent(hits, now, window)
            if len(hits) < maximum:
                hits.append(now)
            self.entries.move_to_end(key)
            while len(self.entries) > 8192:
                self.entries.popitem(last=False)


def parse_allowed_logins(value: str | None) -> frozenset[str]:
    """Normalize a comma-separated login list into lowercase, non-empty names."""
    return frozenset(item.strip().lower() for item in (value or "").split(",") if item.strip())


SESSION_SECRET_LABEL = b"tailplan-session-v1"


def derive_session_secret(upload_token: str) -> str:
    """Return a cookie signing key bound to, but not equal to, the upload token."""
    return hmac.new(upload_token.encode(), SESSION_SECRET_LABEL, hashlib.sha256).hexdigest()


def load_session_secret(path: str | None, value: str | None, upload_token: str) -> str | None:
    """Read the dedicated cookie signing secret, or None to derive it from the upload token."""
    if path:
        try:
            secret = Path(path).expanduser().read_text(encoding="utf-8").strip()
        except (OSError, UnicodeError) as exc:
            raise SystemExit(f"cannot read session secret file: {exc}") from exc
        origin = "session secret file"
    elif value is not None and value.strip():
        secret, origin = value.strip(), "TAILPLAN_SESSION_SECRET"
    else:
        return None
    if not secret:
        raise SystemExit("empty session secret file")
    if hmac.compare_digest(secret.encode(), upload_token.encode()):
        raise SystemExit(f"{origin} must differ from the upload token")
    return secret


def signed_value(payload: dict, secret: str) -> str:
    encoded = base64.urlsafe_b64encode(json.dumps(payload, separators=(",", ":")).encode()).decode().rstrip("=")
    return encoded + "." + hmac.new(secret.encode(), encoded.encode(), hashlib.sha256).hexdigest()


def read_signed(value: str, secret: str) -> dict | None:
    try:
        encoded, signature = value.rsplit(".", 1)
        expected = hmac.new(secret.encode(), encoded.encode(), hashlib.sha256).hexdigest()
        if not hmac.compare_digest(signature, expected):
            return None
        payload = json.loads(base64.urlsafe_b64decode(encoded + "=" * (-len(encoded) % 4)))
        if not isinstance(payload, dict) or type(payload.get("expires")) is not int:
            return None
        return payload if payload["expires"] > time.time() else None
    except (ValueError, UnicodeError, TypeError):
        return None


# -- Sign-in modes, roles, sharing, and OpenID Connect ---------------------

VISIBILITY_RANK = {name: rank for rank, name in enumerate(VISIBILITIES)}
ROLE_RANK = {"viewer": 1, "editor": 2, "admin": 3, "owner": 4}
INSTANCE_ROLES = ("admin", "auditor")
WORKSPACE_ROLES = ("viewer", "editor", "admin")
VIEWER_AUTH_MODES = ("network", "per-draft", "session")
AUTH_MODES = ("api_key", "tailscale", "oidc")
GRANT_TYPES = ("email", "group", "workspace", "tailscale_login")
GRANT_ROLES = ("viewer", "editor")
DEFAULT_SESSION_TTL = 7 * 86400
VIEW_TOKEN_TTL = 3600
OIDC_FLOW_TTL = 600
OIDC_ALGORITHMS = ("RS256", "PS256", "ES256", "ES384", "EdDSA")
OIDC_CLOCK_SKEW = 30
OIDC_MAX_RESPONSE_BYTES = 512 * 1024
OIDC_HTTP_TIMEOUT = 5.0
OIDC_JWKS_TTL = 3600
OIDC_JWKS_REFRESH_FLOOR = 60
OIDC_DEFAULT_SCOPES = "openid profile email groups"
BACKCHANNEL_LOGOUT_EVENT = "http://schemas.openid.net/event/backchannel-logout"
# An email needs a dotted domain. Dot-free logins such as `alice@github` or
# `bob@passkey` are Tailscale logins, not email addresses.
EMAIL_RE = re.compile(
    r"(?=[^@]{1,64}@)[^@\s.]+(?:\.[^@\s.]+)*"
    r"@(?=[^@]{1,255}$)[^@\s.]+(?:\.[^@\s.]+)+"
)
# A Tailscale login is printable ASCII: no spaces, zero-width or
# right-to-left characters, and no HTML or shell punctuation.
TAILSCALE_LOGIN_RE = re.compile(
    r"[A-Za-z0-9_%+'][A-Za-z0-9._%+'-]{0,63}@[A-Za-z0-9](?:[A-Za-z0-9.-]{0,251}[A-Za-z0-9])?"
)
CGNAT_NETWORK = ipaddress.ip_network("100.64.0.0/10")


def purpose_secret(secret: str, purpose: str) -> str:
    """Derive a separate signing key for one cookie or token type."""
    return hmac.new(secret.encode(), f"tailplan-{purpose}-v1".encode(), hashlib.sha256).hexdigest()


def safe_next_path(value: object, default: str = "/dashboard") -> str:
    """Return a relative path under a known Tailplan root, or the default.

    The check refuses schemes, `//` hosts, backslashes, control characters,
    encoded separators, and dot segments.
    """
    if not isinstance(value, str) or not value.isascii() or len(value) > 512:
        return default
    if not value.startswith("/") or value.startswith("//") or "\\" in value or "#" in value:
        return default
    if any(ord(character) <= 32 or ord(character) == 127 for character in value):
        return default
    path, _separator, query = value.partition("?")
    lowered = path.lower()
    if "%2f" in lowered or "%5c" in lowered or "%00" in lowered:
        return default
    if any(segment in {".", ".."} for segment in unquote(path).split("/")):
        return default
    if not (path in {"/dashboard", "/cli/auth", "/cli/activate"}
            or path.startswith(("/dashboard/", "/cli/auth/", "/d/"))):
        return default
    if query and not SAFE_QUERY_RE.fullmatch(query):
        return default
    return value


def _normalize_principal_value(value: str) -> str:
    return value.strip().lstrip("/").lower()


def normalize_grant(principal_type: object, value: object) -> str:
    """Return the stored form of a grant value. Raise ValueError when it is invalid."""
    if principal_type not in GRANT_TYPES:
        raise ValueError("Grant type must be email, group, workspace, or tailscale_login.")
    if not isinstance(value, str) or not value.strip() or len(value) > 255:
        raise ValueError("Grant value is required.")
    if any(ord(character) < 32 or ord(character) == 127 for character in value):
        raise ValueError("Grant value has control characters.")
    normalized = _normalize_principal_value(value) if principal_type == "group" else value.strip().lower()
    if principal_type == "email" and EMAIL_RE.fullmatch(normalized) is None:
        raise ValueError("Grant email is invalid.")
    if principal_type == "workspace" and WORKSPACE_RE.fullmatch(normalized) is None:
        raise ValueError("Grant workspace is invalid.")
    if principal_type == "tailscale_login" and TAILSCALE_LOGIN_RE.fullmatch(normalized) is None:
        raise ValueError("Grant Tailscale login is invalid.")
    if not normalized:
        raise ValueError("Grant value is required.")
    return normalized


def revoke_value(principal_type: object, value: object) -> str:
    """Return the stored form of a grant to revoke. Raise ValueError when it is invalid.

    Unlike normalize_grant, it skips the email and login patterns, so a
    legacy grant that the patterns now refuse can still be revoked.
    """
    if principal_type not in GRANT_TYPES:
        raise ValueError("Grant type must be email, group, workspace, or tailscale_login.")
    if not isinstance(value, str) or not value.strip() or len(value) > 255:
        raise ValueError("Grant value is required.")
    if any(ord(character) < 32 or ord(character) == 127 for character in value):
        raise ValueError("Grant value has control characters.")
    normalized = _normalize_principal_value(value) if principal_type == "group" else value.strip().lower()
    if not normalized:
        raise ValueError("Grant value is required.")
    return normalized


class RBACPolicy:
    """Map sign-in claims to an instance role and workspace roles. Deny by default."""

    RULE_FIELDS = {"match", "instance_role", "workspaces", "default_workspace"}

    def __init__(self, data: object = None) -> None:
        self.configured = data is not None
        if data is None:
            data = {}
        if not isinstance(data, dict):
            raise ValueError("RBAC policy must be a JSON object.")
        unknown = set(data) - {"legacy_workspace", "default_workspace", "rules"}
        if unknown:
            raise ValueError(f"RBAC policy has unknown fields: {', '.join(sorted(unknown))}.")
        self.legacy_workspace = self._workspace(data.get("legacy_workspace", "owner"), "legacy_workspace")
        self.default_workspace = self._workspace(
            data.get("default_workspace", self.legacy_workspace), "default_workspace")
        rules = data.get("rules", [])
        if not isinstance(rules, list):
            raise ValueError("RBAC policy rules must be a list.")
        self.rules = [self._rule(index, rule) for index, rule in enumerate(rules)]

    @staticmethod
    def _workspace(value: object, field: str) -> str:
        if not isinstance(value, str) or WORKSPACE_RE.fullmatch(value) is None:
            raise ValueError(f"RBAC policy {field} must be a lowercase workspace name.")
        return value

    def _rule(self, index: int, rule: object) -> dict:
        where = f"RBAC policy rule {index + 1}"
        if not isinstance(rule, dict) or set(rule) - self.RULE_FIELDS:
            raise ValueError(f"{where} must be an object with known fields.")
        match = rule.get("match")
        if (not isinstance(match, dict) or not match or set(match) - {"group", "email", "tailscale_login"}
                or not all(isinstance(value, str) and value.strip() for value in match.values())):
            raise ValueError(f"{where} needs a match with a group, an email, or a tailscale_login.")
        instance_role = rule.get("instance_role")
        if instance_role is not None and instance_role not in INSTANCE_ROLES:
            raise ValueError(f"{where} instance_role must be admin, auditor, or null.")
        workspaces = rule.get("workspaces", {})
        if not isinstance(workspaces, dict):
            raise ValueError(f"{where} workspaces must be an object.")
        for name, level in workspaces.items():
            self._workspace(name, f"rule {index + 1} workspace")
            if level not in WORKSPACE_ROLES:
                raise ValueError(f"{where} workspace roles must be admin, editor, or viewer.")
        default = rule.get("default_workspace")
        if default is not None:
            self._workspace(default, f"rule {index + 1} default_workspace")
        if "email" in match and EMAIL_RE.fullmatch(match["email"].strip().lower()) is None:
            raise ValueError(
                f"{where} email {match['email'].strip()!r} is not an email address with a dotted "
                "domain. For a Tailscale login such as alice@github, use tailscale_login."
            )
        if ("tailscale_login" in match
                and TAILSCALE_LOGIN_RE.fullmatch(match["tailscale_login"].strip().lower()) is None):
            raise ValueError(f"{where} tailscale_login is not a valid Tailscale login.")
        return {
            "group": _normalize_principal_value(match["group"]) if "group" in match else None,
            "email": match["email"].strip().lower() if "email" in match else None,
            "tailscaleLogin": (match["tailscale_login"].strip().lower()
                               if "tailscale_login" in match else None),
            "instanceRole": instance_role,
            "workspaces": dict(workspaces),
            "defaultWorkspace": default,
        }

    @classmethod
    def load(cls, path: str | None) -> RBACPolicy:
        if not path:
            return cls(None)
        try:
            data = json.loads(Path(path).expanduser().read_text(encoding="utf-8"))
        except (OSError, UnicodeError, json.JSONDecodeError) as exc:
            raise SystemExit(f"cannot read RBAC policy file: {exc}") from exc
        try:
            return cls(data)
        except ValueError as exc:
            raise SystemExit(str(exc)) from exc

    def resolve(self, *, email: str | None, email_verified: bool, groups: list[str],
                tailscale_login: str | None = None) -> dict | None:
        """Return roles for the claims, or None when no rule matches.

        An email rule matches only a verified email. A tailscale_login rule
        matches only the Tailscale login, so `alice@github` never meets an
        email rule.
        """
        email_key = email.strip().lower() if email and email_verified else None
        if email_key is not None and EMAIL_RE.fullmatch(email_key) is None:
            email_key = None
        login_key = tailscale_login.strip().lower() if tailscale_login else None
        group_keys = {_normalize_principal_value(group) for group in groups if isinstance(group, str)}
        matched = [
            rule for rule in self.rules
            if (rule["group"] is None or rule["group"] in group_keys)
            and (rule["email"] is None or rule["email"] == email_key)
            and (rule["tailscaleLogin"] is None or rule["tailscaleLogin"] == login_key)
        ]
        if not matched:
            return None
        instance_role = None
        workspaces: dict[str, str] = {}
        default = None
        for rule in matched:
            if rule["instanceRole"] == "admin" or (rule["instanceRole"] == "auditor" and instance_role is None):
                instance_role = rule["instanceRole"]
            for name, level in rule["workspaces"].items():
                if ROLE_RANK[level] > ROLE_RANK.get(workspaces.get(name, ""), 0):
                    workspaces[name] = level
            default = default or rule["defaultWorkspace"]
        return {"instanceRole": instance_role, "workspaces": workspaces,
                "defaultWorkspace": default or self.default_workspace}

    def open_roles(self) -> dict:
        """Return roles for a signed-in user when no policy file is set."""
        return {"instanceRole": None, "workspaces": {self.default_workspace: "viewer"},
                "defaultWorkspace": self.default_workspace}


class AuthConfig:
    """Hold the sign-in, role, and sharing settings that both listeners share."""

    def __init__(
        self,
        *,
        modes: tuple[str, ...] = ("api_key", "tailscale"),
        policy: RBACPolicy | None = None,
        viewer_mode: str = "network",
        max_visibility: str = "link",
        session_ttl: int = DEFAULT_SESSION_TTL,
        oidc: OIDCClient | None = None,
        link_emails: frozenset[str] = frozenset(),
    ) -> None:
        if viewer_mode not in VIEWER_AUTH_MODES:
            raise ValueError("viewer auth mode must be network, per-draft, or session")
        if max_visibility not in VISIBILITY_RANK:
            raise ValueError("max visibility must be link, authenticated, workspace, or private")
        if any(mode not in AUTH_MODES for mode in modes):
            raise ValueError("auth modes must be api_key, tailscale, or oidc")
        if "oidc" in modes and oidc is None:
            raise ValueError("the oidc auth mode needs TAILPLAN_OIDC_ISSUER")
        self.modes = tuple(modes)
        self.policy = policy or RBACPolicy(None)
        self.viewer_mode = viewer_mode
        self.max_visibility = max_visibility
        self.session_ttl = session_ttl
        self.oidc = oidc
        self.link_emails = frozenset(email.lower() for email in link_emails)

    @property
    def default_visibility(self) -> str:
        return "link" if self.viewer_mode == "network" else "workspace"

    def allowed_visibilities(self) -> list[str]:
        return [name for name in VISIBILITIES if VISIBILITY_RANK[name] <= VISIBILITY_RANK[self.max_visibility]]


def parse_auth_modes(value: str | None, *, oidc: bool) -> tuple[str, ...]:
    if not value or not value.strip():
        return ("oidc", "api_key") if oidc else ("api_key", "tailscale")
    modes = tuple(dict.fromkeys(item.strip().lower() for item in value.split(",") if item.strip()))
    unknown = [mode for mode in modes if mode not in AUTH_MODES]
    if unknown:
        raise ValueError(f"unknown auth modes: {', '.join(unknown)}")
    return modes


def effective_visibility(draft: dict, auth: AuthConfig) -> str:
    """Return the draft visibility, capped by the server maximum."""
    value = draft.get("visibility") or auth.default_visibility
    if value not in VISIBILITY_RANK:
        value = "private"
    return min(value, auth.max_visibility, key=VISIBILITY_RANK.__getitem__)


def draft_workspace(draft: dict, auth: AuthConfig) -> str:
    return draft.get("workspace") or auth.policy.legacy_workspace


def _best_role(*roles: str | None) -> str | None:
    ranked = [role for role in roles if role]
    return max(ranked, key=ROLE_RANK.__getitem__) if ranked else None


def shared_role(identity: dict, draft: dict, grants: list[dict], auth: AuthConfig) -> str | None:
    """Return the role from grants and workspace membership only."""
    email = identity.get("email") if identity.get("emailVerified") else None
    if not isinstance(email, str) or EMAIL_RE.fullmatch(email.strip().lower()) is None:
        email = None
    login = (identity.get("tailscaleLogin") or "").strip().lower() or None
    groups = {_normalize_principal_value(group) for group in identity.get("groups") or []}
    workspaces = identity.get("workspaces") or {}
    roles: list[str | None] = []
    for grant in grants:
        value = grant["value"]
        if ((grant["type"] == "email" and email and value == email.lower())
                or (grant["type"] == "tailscale_login" and login and value == login)
                or (grant["type"] == "group" and value in groups)
                or (grant["type"] == "workspace" and value in workspaces)):
            roles.append(grant["role"])
    member = workspaces.get(draft_workspace(draft, auth))
    if member == "admin":
        roles.append("editor")
    elif member and VISIBILITY_RANK[effective_visibility(draft, auth)] >= VISIBILITY_RANK["workspace"]:
        roles.append(member)
    return _best_role(*roles)


def draft_role(identity: dict | None, draft: dict, grants: list[dict], auth: AuthConfig) -> str | None:
    """Return viewer, editor, admin, or owner for the identity, or None for no access."""
    visibility = effective_visibility(draft, auth)
    if identity is None:
        return "viewer" if visibility == "link" and auth.viewer_mode != "session" else None
    if identity.get("passive"):
        return _passive_role(identity, draft, grants, auth, visibility)
    account_id = identity.get("accountId")
    if account_id not in (None, "anonymous") and draft.get("accountId", "local") == account_id:
        return "owner"
    if identity.get("instanceRole") == "admin":
        return "admin"
    open_role = "viewer" if VISIBILITY_RANK[visibility] >= VISIBILITY_RANK["authenticated"] else None
    if identity.get("policyMatched") is False:
        # A login that no RBAC rule names is a tailnet guest. It reads only
        # drafts shared with it by name or by link, never every
        # `authenticated` draft.
        open_role = "viewer" if visibility == "link" else None
    return _best_role(shared_role(identity, draft, grants, auth), open_role)


def _passive_role(identity: dict, draft: dict, grants: list[dict], auth: AuthConfig,
                  visibility: str) -> str | None:
    """Return viewer or None for a passive Tailscale header identity.

    The identity has no account, so it never owns a draft. The owner login
    reads the owner's drafts, and every role is capped at viewer.
    """
    if identity.get("ownerLogin") and draft.get("accountId", "local") == "local":
        return "viewer"
    open_role = "viewer" if VISIBILITY_RANK[visibility] >= VISIBILITY_RANK["authenticated"] else None
    if identity.get("policyMatched") is not True:
        open_role = "viewer" if visibility == "link" else None
    return "viewer" if _best_role(shared_role(identity, draft, grants, auth), open_role) else None


MAX_UPLOAD_GRANTS = 100


def upload_grants(value: object) -> list[dict] | str:
    """Return the normalized grants from an upload, or an error message."""
    if not isinstance(value, list):
        return "grants must be a list."
    if len(value) > MAX_UPLOAD_GRANTS:
        return f"An upload can carry at most {MAX_UPLOAD_GRANTS} grants."
    result: dict[tuple[str, str], dict] = {}
    for item in value:
        if not isinstance(item, dict) or set(item) - {"type", "value", "role"}:
            return "Each grant needs type, value, and an optional role."
        try:
            normalized = normalize_grant(item.get("type"), item.get("value"))
        except ValueError as exc:
            return str(exc)
        role = item.get("role") or "viewer"
        if role not in GRANT_ROLES:
            return "Grant role must be viewer or editor."
        result[(item["type"], normalized)] = {"type": item["type"], "value": normalized, "role": role}
    return list(result.values())


def workspace_allowed(identity: dict, workspace: str) -> bool:
    """Return True when the identity may place drafts in the workspace by name.

    Uploads without a workspace still land in the default workspace.
    """
    if identity.get("instanceRole") == "admin":
        return True
    return ROLE_RANK.get((identity.get("workspaces") or {}).get(workspace, ""), 0) >= ROLE_RANK["editor"]


def claim_path(claims: dict, path: str) -> object:
    """Return a claim by dotted path, such as `realm_access.roles`."""
    value: object = claims
    for part in path.split("."):
        if not isinstance(value, dict) or part not in value:
            return None
        value = value[part]
    return value


def groups_from_claim(value: object) -> list[str] | None:
    if value is None:
        return None
    if isinstance(value, str):
        return [value]
    if isinstance(value, list):
        return [item for item in value if isinstance(item, str)][:256]
    return []


class OIDCError(Exception):
    """The identity provider sent an unsafe, invalid, or failed answer."""


def _public_address(address: ipaddress.IPv4Address | ipaddress.IPv6Address) -> bool:
    if isinstance(address, ipaddress.IPv6Address) and address.ipv4_mapped:
        address = address.ipv4_mapped
    if isinstance(address, ipaddress.IPv4Address) and address in CGNAT_NETWORK:
        return False
    return address.is_global and not address.is_multicast


def resolve_public_addresses(host: str, port: int, allow_private: bool) -> list[str]:
    """Resolve a host. Refuse loopback, private, link-local, and CGNAT addresses unless allowed."""
    try:
        infos = socket.getaddrinfo(host, port, type=socket.SOCK_STREAM)
    except (socket.gaierror, UnicodeError) as exc:
        raise OIDCError("cannot resolve the identity provider host") from exc
    addresses: list[str] = []
    for info in infos:
        address = ipaddress.ip_address(info[4][0].split("%", 1)[0])
        if not allow_private and not _public_address(address):
            raise OIDCError("the identity provider resolves to a private or reserved address")
        addresses.append(str(address))
    if not addresses:
        raise OIDCError("cannot resolve the identity provider host")
    return addresses


class _PinnedHTTPConnection(http.client.HTTPConnection):
    def __init__(self, host: str, port: int, address: str, timeout: float) -> None:
        super().__init__(host, port, timeout=timeout)
        self._pinned_address = address

    def connect(self) -> None:
        self.sock = socket.create_connection((self._pinned_address, self.port), self.timeout)


class _PinnedHTTPSConnection(http.client.HTTPSConnection):
    def __init__(self, host: str, port: int, address: str, timeout: float, context: ssl.SSLContext) -> None:
        super().__init__(host, port, timeout=timeout, context=context)
        self._pinned_address = address

    def connect(self) -> None:
        sock = socket.create_connection((self._pinned_address, self.port), self.timeout)
        self.sock = self._context.wrap_socket(sock, server_hostname=self.host)


def url_origin(url: str) -> str:
    parsed = urlparse(url)
    port = parsed.port or (443 if parsed.scheme == "https" else 80)
    return f"{parsed.scheme}://{(parsed.hostname or '').lower()}:{port}"


class OIDCClient:
    """OpenID Connect relying party with lazy discovery and strict token checks.

    Discovery and key fetches run at the first sign-in, not at startup.
    Each fetch stays on the issuer origin, never follows a redirect, and has
    size and time limits.
    """

    def __init__(
        self,
        *,
        issuer: str,
        client_id: str,
        client_secret: str,
        scopes: str = OIDC_DEFAULT_SCOPES,
        groups_claim: str = "groups",
        allow_private: bool = False,
        timeout: float = OIDC_HTTP_TIMEOUT,
        max_bytes: int = OIDC_MAX_RESPONSE_BYTES,
        ssl_context: ssl.SSLContext | None = None,
    ) -> None:
        import jwt  # The `oidc` extra installs PyJWT. Import it only when OIDC is on.

        self.jwt = jwt
        parsed = urlparse(issuer)
        if (parsed.scheme not in {"https", "http"} or not parsed.hostname or parsed.username
                or parsed.password or parsed.query or parsed.fragment):
            raise ValueError("TAILPLAN_OIDC_ISSUER must be an absolute URL without credentials or query")
        if parsed.scheme == "http" and not allow_private:
            raise ValueError("TAILPLAN_OIDC_ISSUER must use HTTPS")
        if not client_id or not client_secret:
            raise ValueError("OIDC needs a client ID and a client secret")
        self.issuer = issuer
        self.origin = url_origin(issuer)
        self.client_id = client_id
        self.client_secret = client_secret
        self.scopes = " ".join(scopes.split()) or OIDC_DEFAULT_SCOPES
        if "openid" not in self.scopes.split():
            self.scopes = "openid " + self.scopes
        self.groups_claim = groups_claim or "groups"
        self.allow_private = allow_private
        self.timeout = timeout
        self.max_bytes = max_bytes
        self.ssl_context = ssl_context or ssl.create_default_context()
        self.lock = threading.Lock()
        self._metadata: dict | None = None
        self._metadata_at = 0.0
        self._jwks: list[dict] = []
        self._jwks_at = 0.0

    def fetch(self, url: str, *, method: str = "GET", body: bytes | None = None,
              headers: dict | None = None) -> tuple[int, bytes]:
        parsed = urlparse(url)
        if (parsed.scheme not in {"https", "http"} or not parsed.hostname or parsed.username
                or parsed.password or parsed.fragment):
            raise OIDCError("the identity provider sent an invalid endpoint URL")
        if parsed.scheme == "http" and not self.allow_private:
            raise OIDCError("identity provider endpoints must use HTTPS")
        if url_origin(url) != self.origin:
            raise OIDCError("identity provider endpoints must share the issuer origin")
        port = parsed.port or (443 if parsed.scheme == "https" else 80)
        address = resolve_public_addresses(parsed.hostname, port, self.allow_private)[0]
        if parsed.scheme == "https":
            connection: http.client.HTTPConnection = _PinnedHTTPSConnection(
                parsed.hostname, port, address, self.timeout, self.ssl_context)
        else:
            connection = _PinnedHTTPConnection(parsed.hostname, port, address, self.timeout)
        target = (parsed.path or "/") + (f"?{parsed.query}" if parsed.query else "")
        deadline = time.monotonic() + self.timeout
        try:
            connection.request(method, target, body=body, headers={
                "Accept": "application/json", "User-Agent": f"tailplan/{TAILPLAN_VERSION}", **(headers or {})})
            response = connection.getresponse()
            if 300 <= response.status < 400:
                raise OIDCError("the identity provider sent a redirect; Tailplan does not follow redirects")
            declared = response.getheader("Content-Length")
            if declared and declared.isdigit() and int(declared) > self.max_bytes:
                raise OIDCError("the identity provider response is too large")
            chunks: list[bytes] = []
            size = 0
            while True:
                if time.monotonic() > deadline:
                    raise OIDCError("the identity provider did not answer in time")
                chunk = response.read(65536)
                if not chunk:
                    break
                size += len(chunk)
                if size > self.max_bytes:
                    raise OIDCError("the identity provider response is too large")
                chunks.append(chunk)
            return response.status, b"".join(chunks)
        except (OSError, http.client.HTTPException) as exc:
            raise OIDCError(f"the identity provider request failed: {exc.__class__.__name__}") from exc
        finally:
            connection.close()

    def fetch_json(self, url: str, **options) -> dict:
        status, body = self.fetch(url, **options)
        if status != 200:
            raise OIDCError(f"the identity provider answered HTTP {status}")
        try:
            value = json.loads(body.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise OIDCError("the identity provider sent invalid JSON") from exc
        if not isinstance(value, dict):
            raise OIDCError("the identity provider sent a JSON value that is not an object")
        return value

    def metadata(self) -> dict:
        with self.lock:
            if self._metadata and time.monotonic() - self._metadata_at < OIDC_JWKS_TTL:
                return self._metadata
        document = self.fetch_json(self.issuer.rstrip("/") + "/.well-known/openid-configuration")
        if document.get("issuer") != self.issuer:
            raise OIDCError("the discovery issuer does not match TAILPLAN_OIDC_ISSUER exactly")
        for field in ("authorization_endpoint", "token_endpoint", "jwks_uri"):
            if not isinstance(document.get(field), str):
                raise OIDCError(f"the discovery document has no {field}")
        for field in ("authorization_endpoint", "token_endpoint", "jwks_uri", "userinfo_endpoint"):
            value = document.get(field)
            if value is not None and (not isinstance(value, str) or url_origin(value) != self.origin):
                raise OIDCError(f"the discovery {field} must share the issuer origin")
        with self.lock:
            self._metadata, self._metadata_at = document, time.monotonic()
        return document

    def _load_jwks(self) -> list[dict]:
        document = self.fetch_json(self.metadata()["jwks_uri"])
        keys = document.get("keys")
        if not isinstance(keys, list):
            raise OIDCError("the key set has no keys")
        usable = [key for key in keys if isinstance(key, dict) and key.get("kty") in {"RSA", "EC", "OKP"}
                  and key.get("use", "sig") == "sig"]
        with self.lock:
            self._jwks, self._jwks_at = usable, time.monotonic()
        return usable

    def signing_key(self, kid: object, algorithm: str) -> dict:
        def find(keys: list[dict]) -> dict | None:
            candidates = [key for key in keys if key.get("alg") in {None, algorithm}]
            if kid is not None:
                candidates = [key for key in candidates if key.get("kid") == kid]
            return candidates[0] if len(candidates) == 1 or (kid is not None and candidates) else None

        with self.lock:
            keys, loaded_at = self._jwks, self._jwks_at
        age = time.monotonic() - loaded_at
        if not keys or age >= OIDC_JWKS_TTL:
            keys = self._load_jwks()
            age = 0.0
        key = find(keys)
        if key is None and loaded_at and age >= OIDC_JWKS_REFRESH_FLOOR:
            key = find(self._load_jwks())
        if key is None:
            raise OIDCError("the token signing key is unknown")
        return key

    def verify(self, token: object, *, require: list[str], audience: str | None = None) -> dict:
        if not isinstance(token, str) or len(token) > 16384:
            raise OIDCError("the token is missing or too large")
        jwt = self.jwt
        try:
            header = jwt.get_unverified_header(token)
        except jwt.PyJWTError as exc:
            raise OIDCError("the token header is invalid") from exc
        algorithm = header.get("alg")
        if algorithm not in OIDC_ALGORITHMS:
            raise OIDCError("the token algorithm is not allowed")
        jwk = self.signing_key(header.get("kid"), algorithm)
        try:
            key = jwt.PyJWK(jwk, algorithm=algorithm).key
            claims = jwt.decode(
                token, key=key, algorithms=[algorithm], audience=audience or self.client_id,
                issuer=self.issuer, leeway=OIDC_CLOCK_SKEW, options={"require": require},
            )
        except (jwt.PyJWTError, ValueError, TypeError) as exc:
            raise OIDCError(f"the token is invalid: {exc.__class__.__name__}") from exc
        if not isinstance(claims, dict):
            raise OIDCError("the token claims are invalid")
        audiences = claims["aud"] if isinstance(claims.get("aud"), list) else [claims.get("aud")]
        azp = claims.get("azp")
        if (azp is not None or len(audiences) > 1) and azp != self.client_id:
            raise OIDCError("the token authorized party is not this client")
        return claims

    def verify_id_token(self, token: object, *, nonce: str) -> dict:
        claims = self.verify(token, require=["exp", "iat", "iss", "aud", "sub"])
        if not isinstance(claims.get("nonce"), str) or not hmac.compare_digest(claims["nonce"], nonce):
            raise OIDCError("the token nonce does not match")
        subject = claims.get("sub")
        if not isinstance(subject, str) or not subject or len(subject) > 255:
            raise OIDCError("the token subject is invalid")
        return claims

    def verify_logout_token(self, token: object) -> dict:
        claims = self.verify(token, require=["iat", "iss", "aud", "jti"])
        events = claims.get("events")
        if not isinstance(events, dict) or BACKCHANNEL_LOGOUT_EVENT not in events:
            raise OIDCError("the logout token has no back-channel logout event")
        if "nonce" in claims:
            raise OIDCError("a logout token must not have a nonce")
        if not isinstance(claims.get("sub"), str) and not isinstance(claims.get("sid"), str):
            raise OIDCError("the logout token needs sub or sid")
        return claims

    def authorization_url(self, *, state: str, nonce: str, challenge: str, redirect_uri: str) -> str:
        endpoint = self.metadata()["authorization_endpoint"]
        query = urlencode({
            "response_type": "code", "client_id": self.client_id, "redirect_uri": redirect_uri,
            "scope": self.scopes, "state": state, "nonce": nonce,
            "code_challenge": challenge, "code_challenge_method": "S256",
        })
        return endpoint + ("&" if "?" in endpoint else "?") + query

    def exchange(self, code: str, verifier: str, redirect_uri: str) -> dict:
        body = urlencode({"grant_type": "authorization_code", "code": code,
                          "redirect_uri": redirect_uri, "code_verifier": verifier}).encode()
        credentials = f"{quote(self.client_id, safe='')}:{quote(self.client_secret, safe='')}"
        tokens = self.fetch_json(self.metadata()["token_endpoint"], method="POST", body=body, headers={
            "Content-Type": "application/x-www-form-urlencoded",
            "Authorization": "Basic " + base64.b64encode(credentials.encode()).decode(),
        })
        if not isinstance(tokens.get("id_token"), str):
            raise OIDCError("the token endpoint sent no ID token")
        return tokens

    def userinfo(self, access_token: str, subject: str) -> dict:
        endpoint = self.metadata().get("userinfo_endpoint")
        if not endpoint:
            return {}
        info = self.fetch_json(endpoint, headers={"Authorization": f"Bearer {access_token}"})
        if info.get("sub") != subject:
            raise OIDCError("the userinfo subject does not match the ID token")
        return info


def oidc_account_id(issuer: str, subject: str) -> str:
    """Return the stable account ID for one (issuer, subject) pair."""
    return sha256_text(f"oidc:{issuer}\n{subject}")[:32]


def pkce_challenge(verifier: str) -> str:
    return base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).decode().rstrip("=")


def add_view_token(href: str, token: str) -> str | None:
    """Add a view token to a relative sibling link. Return None for other links."""
    value = href.strip()
    if not value or value.startswith(("/", "#", "?")) or "\\" in value:
        return None
    if re.match(r"^[a-zA-Z][a-zA-Z0-9+.-]*:", value):
        return None
    path, hash_mark, fragment = value.partition("#")
    separator = "&" if "?" in path else "?"
    return f"{path}{separator}tv={token}" + (f"#{fragment}" if hash_mark else "")


class _ViewLinkRewriter(HTMLParser):
    def __init__(self, doc: str, token: str) -> None:
        super().__init__(convert_charrefs=True)
        self.token = token
        self.edits: list[tuple[int, int, str]] = []
        self._line_offsets = [0]
        self._line_offsets.extend(index + 1 for index, character in enumerate(doc) if character == "\n")

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag.lower() != "a":
            return
        href = next((value for name, value in attrs if name.lower() == "href"), None)
        raw = self.get_starttag_text()
        if href is None or raw is None:
            return
        updated = add_view_token(href, self.token)
        if updated is None:
            return
        spans, _close_at = _start_tag_attributes(raw)
        span = next(((start, end) for name, start, end in spans if name == "href"), None)
        if span is None:
            return
        line, column = self.getpos()
        offset = self._line_offsets[line - 1] + column
        self.edits.append((offset + span[0], offset + span[1], f'href="{html.escape(updated, quote=True)}"'))


def add_view_tokens(doc: str, token: str) -> str:
    """Carry a view token on relative links, for browsers that block third-party cookies."""
    rewriter = _ViewLinkRewriter(doc, token)
    rewriter.feed(doc)
    rewriter.close()
    pieces: list[str] = []
    previous = 0
    for start, end, replacement in sorted(rewriter.edits):
        pieces.extend((doc[previous:start], replacement))
        previous = end
    pieces.append(doc[previous:])
    return "".join(pieces)


class TailplanHTTPServer(ThreadingHTTPServer):
    """Threaded HTTP server with bounded admission and finite socket reads."""

    store: Store
    token: str
    base_url: str
    redirect_view_base_url: str
    auth: AuthConfig

    def __init__(
        self,
        server_address: tuple[str, int],
        request_handler_class: type[BaseHTTPRequestHandler],
        *,
        max_handlers: int = DEFAULT_MAX_HANDLERS,
        read_timeout: float = DEFAULT_READ_TIMEOUT,
    ) -> None:
        if max_handlers <= 0:
            raise ValueError("max_handlers must be positive")
        if read_timeout <= 0:
            raise ValueError("read_timeout must be positive")
        self.read_timeout = read_timeout
        self.limiter = RateLimiter()
        self.viewer_limiter = MissLimiter()
        self.viewer_404_limit = DEFAULT_VIEWER_404_LIMIT
        self.session_secret: str | None = None
        self.trust_tailscale_identity = False
        # Passive Tailscale viewer: read-only draft access from the Serve header,
        # with no account, session, or cookie. Only the proxy listener sets it.
        self.tailscale_viewer = False
        self.owner_login = ""
        self.allowed_logins: frozenset[str] = frozenset()
        self.allow_anonymous_uploads = False
        self.upload_ip_limit = 60
        self.upload_key_limit = 30
        self.auth = AuthConfig()
        self._handler_slots = threading.BoundedSemaphore(max_handlers)
        super().__init__(server_address, request_handler_class)

    def get_request(self):
        request, client_address = super().get_request()
        request.settimeout(self.read_timeout)
        return request, client_address

    def process_request(self, request, client_address) -> None:
        if not self._handler_slots.acquire(blocking=False):
            self.shutdown_request(request)
            return
        try:
            super().process_request(request, client_address)
        except BaseException:
            self._handler_slots.release()
            raise

    def process_request_thread(self, request, client_address) -> None:
        try:
            super().process_request_thread(request, client_address)
        finally:
            self._handler_slots.release()


DASHBOARD_VIEWS = ("active", "favorites", "living", "shared", "archived", "all")
DASHBOARD_SORTS = ("updated", "living", "favorited", "created", "title", "relevance")
DASHBOARD_GROUPS = ("time", "tag", "repo", "none")
DASHBOARD_THEMES = ("light", "dark", "system")
MAX_QUERY_CHARS = 500
MAX_FTS_TERMS = 12
_QUERY_TOKEN_RE = re.compile(r'(?:([A-Za-z]+):)?(?:"([^"]*)"|([^\s"]+))')
_QUERY_IS_FLAGS = {
    "fav": "favorite", "favorite": "favorite", "starred": "favorite", "living": "living", "archived": "archived",
    "disabled": "disabled", "shared": "shared", "untagged": "untagged",
}
_QUERY_VERSION_RE = re.compile(r"(>=|<=|>|<)?([0-9]{1,9})")
_QUERY_AGE_RE = re.compile(r"([<>])([0-9]{1,5})d")
_QUERY_DATE_RE = re.compile(r"[0-9]{4}-[0-9]{2}-[0-9]{2}")
_STEM_SUFFIX_RE = re.compile(r"(?:-(?:v?[0-9]+|draft|final|copy|wip|old|new|rev[0-9]*))+$")


def _query_operator(parsed: dict, key: str, value: str) -> bool:
    """Record one `key:value` operator. Return False when the pair is not an operator."""
    value = value.strip()
    if key == "is" and value.lower() in _QUERY_IS_FLAGS:
        parsed["is"].add(_QUERY_IS_FLAGS[value.lower()])
    elif key == "v" and (match := _QUERY_VERSION_RE.fullmatch(value)):
        parsed["versions"].append((match[1] or "=", int(match[2])))
    elif key == "tag" and TAG_RE.fullmatch(value.lower()):
        parsed["tags"].append(value.lower())
    elif key == "repo" and 0 < len(value) <= 100:
        parsed["repos"].append(value.lower())
    elif key == "vis" and value.lower() in VISIBILITIES:
        parsed["vis"].append(value.lower())
    elif key == "ws" and WORKSPACE_RE.fullmatch(value.lower()):
        parsed["ws"].append(value.lower())
    elif key in {"after", "before"} and _QUERY_DATE_RE.fullmatch(value):
        try:
            datetime.strptime(value, "%Y-%m-%d")
        except ValueError:
            return False
        parsed[key] = value
    elif key == "age" and (match := _QUERY_AGE_RE.fullmatch(value.lower())):
        parsed["ages"].append((match[1], int(match[2])))
    else:
        return False
    return True


def parse_dashboard_query(q: str) -> dict:
    """Split a dashboard search into operators and a safe FTS5 query.

    Free words and quoted phrases become quoted FTS5 terms, so no input can
    break the MATCH syntax. An unknown operator is plain text.
    """
    parsed: dict = {
        "is": set(), "versions": [], "tags": [], "repos": [], "vis": [], "ws": [],
        "after": None, "before": None, "ages": [], "text": [], "fts": "",
        "repoFacets": [], "blank": False,
    }
    terms: list[str] = []
    for match in _QUERY_TOKEN_RE.finditer(q[:MAX_QUERY_CHARS] if isinstance(q, str) else ""):
        key, quoted, word = match.groups()
        value = quoted if quoted is not None else word
        if key and _query_operator(parsed, key.lower(), value):
            continue
        phrase = key is None and quoted is not None
        words = re.findall(r"[^\W_]+", value if key is None else match[0])
        if not words:
            parsed["blank"] = True  # The token held no letters or digits, so it can match nothing.
            continue
        if len(terms) >= MAX_FTS_TERMS:
            continue
        parsed["text"].append(" ".join(words))
        terms.append('"' + " ".join(words) + ('"' if phrase else '"*'))
    parsed["fts"] = " ".join(terms)
    return parsed


def _draft_stem(draft: dict) -> str | None:
    base = re.sub(r"[^a-z0-9]+", "-", (draft.get("slug") or draft.get("title") or "").lower()).strip("-")
    stem = _STEM_SUFFIX_RE.sub("", base).strip("-")[:32].rstrip("._-")
    # "Tailplan Draft" is the title of an untitled draft, so it names no stream.
    return stem if len(stem) >= 3 and stem != "tailplan" and TAG_RE.fullmatch(stem) else None


def infer_suggested_tags(drafts: list[dict]) -> dict[str, str]:
    """Suggest one tag per untagged draft from a name stem that two or more drafts share.

    A stem is the slug or title without version, date, and draft suffixes.
    The result maps draft IDs to tags. The server never applies a suggestion.
    """
    groups: dict[str, list[dict]] = {}
    for draft in drafts:
        stem = _draft_stem(draft)
        if stem:
            groups.setdefault(stem, []).append(draft)
    return {
        draft["draftId"]: stem
        for stem, members in groups.items() if len(members) >= 2
        for draft in members if not draft.get("tags")
    }


def _parse_stamp(value: object) -> datetime | None:
    try:
        stamp = datetime.fromisoformat(value) if isinstance(value, str) and value else None
    except ValueError:
        return None
    return stamp.replace(tzinfo=UTC) if stamp is not None and stamp.tzinfo is None else stamp


def _repo_label(draft: dict) -> str | None:
    org, name = draft.get("repoOrg"), draft.get("repoName")
    return f"{org}/{name}" if org and name else (name or org or None)


def _matches_query(draft: dict, parsed: dict, *, granted: set[str], shared_view: bool, now: datetime) -> bool:
    flags = parsed["is"]
    if "favorite" in flags and not draft["favorite"]:
        return False
    if "living" in flags and draft["versionCount"] <= 1:
        return False
    if "disabled" in flags and not draft["disabled"]:
        return False
    if "untagged" in flags and draft["tags"]:
        return False
    if "shared" in flags and not shared_view and not (
        draft["draftId"] in granted or draft["visibility"] != "private"
    ):
        return False
    count = draft["versionCount"]
    compare = {"=": count.__eq__, ">": count.__gt__, ">=": count.__ge__, "<": count.__lt__, "<=": count.__le__}
    if any(not compare[op](number) for op, number in parsed["versions"]):
        return False
    if any(tag not in draft["tags"] for tag in parsed["tags"]):
        return False
    label = (_repo_label(draft) or "").lower()
    for wanted in parsed["repos"]:
        if wanted == "none":
            if label:
                return False
        elif not label or wanted not in f"{draft.get('repoHost') or ''}/{label}".lower():
            return False
    if any(label != facet for facet in parsed["repoFacets"]):
        return False
    if parsed["vis"] and draft["visibility"] not in parsed["vis"]:
        return False
    if parsed["ws"] and str(draft["workspace"]).lower() not in parsed["ws"]:
        return False
    updated = draft["updatedAt"] or ""
    if parsed["after"] and updated[:10] < parsed["after"]:
        return False
    if parsed["before"] and updated[:10] >= parsed["before"]:
        return False
    if parsed["ages"]:
        stamp = _parse_stamp(updated)
        if stamp is None:
            return False
        days = (now - stamp).total_seconds() / 86400
        if any((days >= number) if op == "<" else (days <= number) for op, number in parsed["ages"]):
            return False
    return True


def query_dashboard(
    store: Store,
    auth: AuthConfig,
    account_id: str,
    base_url: str,
    *,
    view: str = "active",
    q: str = "",
    sort: str = "",
    direction: str = "",
    group: str = "time",
    tag: str = "",
    repo: str = "",
    shared_drafts: list[dict] | None = None,
) -> dict:
    """Return the dashboard state: the drafts for one view, search, and sort, plus facets."""
    view = view if view in DASHBOARD_VIEWS else "active"
    group = "none" if group == "flat" else group
    group = group if group in DASHBOARD_GROUPS else "time"
    parsed = parse_dashboard_query(q)
    # A tag facet that is not a valid tag, or text with no words, can match nothing.
    invalid_tag = bool(tag.strip()) and not TAG_RE.fullmatch(tag.strip().lower())
    if tag.strip() and not invalid_tag:
        parsed["tags"].append(tag.strip().lower())
    if repo.strip():
        parsed["repoFacets"].append(repo.strip().lower()[:100])
    no_match = invalid_tag or (
        parsed["blank"] and not parsed["fts"] and not any(
            parsed[key] for key in ("is", "versions", "tags", "repos", "vis", "ws", "after", "before", "ages")
        )
    )
    own = store.list_drafts(account_id, base_url, include_archived=True)
    for draft in own:
        draft["workspace"] = draft_workspace(draft, auth)
        draft["visibility"] = effective_visibility(draft, auth)
    # A reader sees neither the owner's tags nor the owner's archive state.
    shared = [
        {**draft, "tags": [], "archived": False, "archivedAt": None, "favorite": False, "favoritedAt": None}
        for draft in shared_drafts or []
    ]
    counts = {
        "active": sum(not draft["archived"] for draft in own),
        "favorites": sum(draft["favorite"] and not draft["archived"] for draft in own),
        "living": sum(draft["versionCount"] > 1 and not draft["archived"] for draft in own),
        "shared": len(shared),
        "archived": sum(draft["archived"] for draft in own),
        "all": len(own),
    }
    archive_scope = "only" if view == "archived" or "archived" in parsed["is"] else (
        "any" if view == "all" else "none")

    def in_view(draft: dict) -> bool:
        return not (
            (archive_scope == "only" and not draft["archived"])
            or (archive_scope == "none" and draft["archived"])
            or (view == "favorites" and not draft["favorite"])
            or (view == "living" and draft["versionCount"] <= 1)
        )

    shared_view = view == "shared"
    pool = shared if shared_view else [draft for draft in own if in_view(draft)]
    granted = store.granted_draft_ids(account_id) if "shared" in parsed["is"] and not shared_view else set()
    now = datetime.now(UTC)
    matched = [] if no_match else [
        draft for draft in pool
        if _matches_query(draft, parsed, granted=granted, shared_view=shared_view, now=now)
    ]
    ranked = bool(parsed["fts"])
    if ranked:
        hits = store.search_fts(
            account_id, parsed["fts"],
            draft_ids={draft["draftId"] for draft in pool} if shared_view else None,
            exclude_tags=shared_view,
        )
        matched = [{**draft, "rank": hits[draft["draftId"]][0], "snippet": hits[draft["draftId"]][1]}
                   for draft in matched if draft["draftId"] in hits]
    sort = sort if sort in DASHBOARD_SORTS else ""
    if sort == "relevance" and not ranked:
        sort = "updated"
    sort = sort or ("relevance" if ranked else "updated")
    direction = direction if direction in {"asc", "desc"} else ("asc" if sort == "title" else "desc")
    reverse = direction == "desc"
    matched.sort(key=lambda draft: draft["updatedAt"] or "", reverse=True)
    if sort == "favorited":
        starred = sorted((d for d in matched if d["favoritedAt"]), key=lambda d: d["favoritedAt"], reverse=reverse)
        matched = starred + [draft for draft in matched if not draft["favoritedAt"]]
    elif sort != "updated" or not reverse:
        keys = {
            "updated": lambda d: d["updatedAt"] or "",
            "living": lambda d: d["versionCount"],
            "created": lambda d: d["createdAt"] or "",
            "title": lambda d: (d["title"] or "").casefold(),
            "relevance": lambda d: -d.get("rank", 0.0),
        }
        matched.sort(key=keys[sort], reverse=reverse)
    tag_counts: dict[str, int] = {}
    repo_counts: dict[str, int] = {}
    for draft in pool:
        for name in draft["tags"]:
            tag_counts[name] = tag_counts.get(name, 0) + 1
        if label := _repo_label(draft):
            repo_counts[label] = repo_counts.get(label, 0) + 1
    favorites = sorted((d for d in own if d["favorite"] and not d["archived"]),
                       key=lambda d: d["updatedAt"] or "", reverse=True)
    return {
        "view": view, "q": q if isinstance(q, str) else "", "sort": sort, "direction": direction,
        "group": group, "tag": tag.strip().lower(), "repo": repo.strip(),
        "counts": counts,
        "tags": [{"tag": name, "count": count}
                 for name, count in sorted(tag_counts.items(), key=lambda item: (-item[1], item[0]))],
        "repos": [{"repo": name, "count": count}
                  for name, count in sorted(repo_counts.items(), key=lambda item: (-item[1], item[0]))],
        "suggestedTags": infer_suggested_tags([draft for draft in own if not draft["archived"]]),
        "favorites": favorites,
        "drafts": matched,
    }


class Handler(BaseHTTPRequestHandler):
    server_version = "Tailplan/1.0"

    @property
    def store(self) -> Store:
        return self.server.store  # type: ignore[attr-defined]

    @property
    def token(self) -> str:
        return self.server.token  # type: ignore[attr-defined]

    @property
    def session_secret(self) -> str:
        """Key for browser cookies: the dedicated secret, else one derived from the upload token."""
        configured = getattr(self.server, "session_secret", None)
        return configured or derive_session_secret(self.token)

    @property
    def auth(self) -> AuthConfig:
        return self.server.auth  # type: ignore[attr-defined]

    def purpose_key(self, purpose: str) -> str:
        return purpose_secret(self.session_secret, purpose)

    @property
    def base_url(self) -> str:
        configured = self.server.base_url  # type: ignore[attr-defined]
        if configured:
            return configured
        host, port = self.server.server_address[:2]
        return f"http://{host}:{port}"

    def log_message(self, fmt: str, *args: object) -> None:
        sys.stderr.write(f"{self.log_date_time_string()} {fmt % args}\n")

    def send_json(self, status: int, payload: dict, headers: dict | None = None) -> None:
        body = json.dumps(payload).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Content-Length", str(len(body)))
        for name, value in (headers or {}).items():
            self.send_header(name, value)
        self.end_headers()
        if self.command != "HEAD":
            self.wfile.write(body)

    def send_html(self, status: int, body: str, *, web: bool = False, cookie: str | list[str] | None = None) -> None:
        raw = body.encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        form_policy = "'self'" if web else "'none'"
        self.send_header("Content-Security-Policy", "default-src 'none'; script-src 'none'; "
                         "style-src 'unsafe-inline'; img-src https: data:; base-uri 'none'; "
                         f"frame-ancestors 'none'; form-action {form_policy}")
        self.send_header("Referrer-Policy", "same-origin" if web else "no-referrer")
        self.send_header("X-Frame-Options", "DENY")
        for value in [cookie] if isinstance(cookie, str) else cookie or []:
            self.send_header("Set-Cookie", value)
        self.send_header("Content-Length", str(len(raw)))
        self.end_headers()
        if self.command != "HEAD":
            self.wfile.write(raw)

    def serve_asset(self, rel_path: str) -> None:
        """Serve one hashed static asset. Unknown paths get the standard 404."""
        asset = web_assets.lookup_asset(rel_path)
        if asset is None:
            self.send_not_found()
            return
        body, content_type, etag = asset
        self.send_response(200)
        self.send_header("Content-Type", content_type)
        self.send_header("Cache-Control", "public, max-age=31536000, immutable")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Content-Security-Policy", "default-src 'none'")
        self.send_header("ETag", etag)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        if self.command != "HEAD":
            self.wfile.write(body)

    def send_document(
        self,
        body: str,
        *,
        head_only: bool = False,
        draft_id: str = "",
        version: int = 1,
        allow_inline_scripts: bool = False,
        cookies: list[str] | None = None,
        view_token: str | None = None,
    ) -> None:
        served = add_view_tokens(body, view_token) if view_token else body
        raw = served.encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Referrer-Policy", "no-referrer")
        self.send_header("X-Frame-Options", "DENY")
        self.send_header("X-Tailplan-Draft-Id", draft_id)
        self.send_header("X-Tailplan-Draft-Version", str(version))
        self.send_header("X-Postplan-Draft-Id", draft_id)
        self.send_header("X-Postplan-Draft-Version", str(version))
        self.send_header("X-Tailplan-Content-SHA256", sha256_text(body))
        self.send_header(
            "Content-Security-Policy",
            draft_content_security_policy(allow_inline_scripts=allow_inline_scripts),
        )
        for cookie in cookies or []:
            self.send_header("Set-Cookie", cookie)
        self.send_header("Content-Length", str(len(raw)))
        self.end_headers()
        if not head_only:
            self.wfile.write(raw)

    def send_draft_redirect(self, target_path: str, query: str) -> None:
        prefix = urlparse(self.base_url).path.rstrip("/")
        location = f"{prefix}{target_path}" if prefix else target_path
        if query and SAFE_QUERY_RE.fullmatch(query):
            location = f"{location}?{query}"
        self.send_response(HTTPStatus.FOUND)
        self.send_header("Location", location)
        self.send_header("Cache-Control", "no-store")
        self.send_header("Content-Length", "0")
        self.end_headers()

    def send_view_redirect(self, path: str, query: str) -> None:
        location = f"{self.server.redirect_view_base_url}{path}"  # type: ignore[attr-defined]
        if query and SAFE_QUERY_RE.fullmatch(query):
            location = f"{location}?{query}"
        self.send_response(HTTPStatus.PERMANENT_REDIRECT)
        self.send_header("Location", location)
        self.send_header("Cache-Control", "no-store")
        self.send_header("Content-Length", "0")
        self.end_headers()

    def unauthorized(self) -> None:
        self.send_json(HTTPStatus.UNAUTHORIZED, {"ok": False, "error": "Missing or invalid API token."})

    def is_authorized(self) -> bool:
        return self.api_identity() is not None

    def api_identity(self) -> dict | None:
        header = self.headers.get("Authorization", "")
        match = re.fullmatch(r"Bearer\s+(\S+)", header, re.IGNORECASE)
        return self.store.authenticate(match[1], self.token) if match else None

    def route_path(self) -> str:
        path = urlparse(self.path).path
        prefix = urlparse(self.base_url).path.rstrip("/")
        if prefix and (path == prefix or path.startswith(prefix + "/")):
            path = path[len(prefix):] or "/"
        return path

    def source_ip(self) -> str:
        if self.server.trust_tailscale_identity and self.client_address[0] in {"127.0.0.1", "::1"}:
            value = self.headers.get("X-Forwarded-For", "").split(",")[-1].strip()
            try:
                return str(ipaddress.ip_address(value))
            except ValueError:
                pass
        return self.client_address[0]

    @staticmethod
    def parse_draft_route(path: str) -> dict | None:
        if not path.startswith("/d/"):
            return None
        raw_segments = path.split("/")[1:]
        segments: list[str] = []
        for raw in raw_segments:
            try:
                decoded = unquote(raw, errors="strict")
            except UnicodeDecodeError:
                return None
            if "/" in decoded or "\\" in decoded or "\x00" in decoded:
                return None
            if decoded in {".", ".."}:
                return None
            segments.append(decoded)
        if len(segments) < 2 or segments[0] != "d" or not DRAFT_REF_RE.fullmatch(segments[1]):
            return None
        ref = segments[1].lower()
        parts = segments[2:]
        version = None
        if parts and parts[0] == "v":
            if len(parts) < 2 or not re.fullmatch(r"[1-9][0-9]{0,8}", parts[1]):
                return None
            version = int(parts[1])
            parts = parts[2:]
        raw_alias = False
        raw_with_slash = False
        if parts and parts[0] in {"raw", "content"} and (
            len(parts) == 1 or (len(parts) == 2 and parts[1] == "")
        ):
            raw_alias = True
            raw_with_slash = len(parts) == 2 and parts[1] == ""
            parts = [""]
        return {
            "ref": ref,
            "version": version,
            "parts": parts,
            "raw_alias": raw_alias,
            "raw_with_slash": raw_with_slash,
            "page_path": "/".join(parts),
        }

    def serve_viewer(
        self,
        route: dict,
        *,
        path: str,
        query: str,
        head_only: bool = False,
    ) -> None:
        redirect_base = self.server.redirect_view_base_url  # type: ignore[attr-defined]
        if redirect_base:
            self.send_view_redirect(path, query)
            return
        try:
            gate = self.gate_draft(route["ref"], api=False)
            if gate is None:
                return
            draft, version_num, doc, allow_scripts, redirect_kind = self.store.get_page(
                route["ref"], route["version"], route["page_path"]
            )
        except (StorageError, OSError) as exc:
            self.log_message("storage error: %r", exc)
            self.send_json(503, {"ok": False, "error": "Storage unavailable."})
            return
        if not draft or version_num is None:
            self.send_not_found()
            return
        record = draft.get("versions", {}).get(str(version_num))
        multi_page = bool(record and (record.get("pageCount") or 1) > 1 and record.get("pages"))
        if route["raw_with_slash"] and multi_page:
            self.send_draft_redirect(path[:-1], query)
            return
        if route["page_path"] == "" and not route["parts"] and multi_page:
            self.send_draft_redirect(path + "/", query)
            return
        if redirect_kind == "slash":
            self.send_draft_redirect(path + "/", query)
            return
        if doc is None:
            self.send_not_found()
            return
        _gated, identity, source = gate
        cookies, view_token = self.view_credentials(identity, source, draft["draftId"], multi_page)
        if identity is not None and not head_only:
            self.audit_view(identity, source, draft["draftId"], version_num,
                            route["page_path"] or "index.html")
        self.send_document(
            doc,
            head_only=head_only,
            draft_id=draft["draftId"],
            version=version_num,
            allow_inline_scripts=allow_scripts,
            cookies=cookies,
            view_token=view_token,
        )

    def audit_view(self, identity: dict, source: str, draft_id: str, version: int, path: str) -> None:
        """Record a `draft.view` event for a reader."""
        detail: dict = {"version": version, "path": path}
        actor = identity
        if source == "tailscale_header":
            actor = {"accountId": None, "email": identity.get("email") or identity["tailscaleLogin"]}
            detail = {"authMethod": "tailscale_header", "actorLogin": identity["tailscaleLogin"], **detail}
        self.audit_event("draft.view", actor, "draft", draft_id, detail=detail)

    def serve_preview(self, match: re.Match[str], *, head_only: bool = False) -> None:
        if self.server.redirect_view_base_url:
            self.send_view_redirect(self.route_path(), urlparse(self.path).query)
            return
        version = int(match.group(2)) if match.group(2) else None
        try:
            gate = self.gate_draft(match.group(1), api=False)
            if gate is None:
                return
            draft, resolved_version, doc, allow_scripts, _redirect = self.store.get_page(
                match.group(1), version, ""
            )
        except (StorageError, OSError) as exc:
            self.log_message("preview storage error: %r", exc)
            self.send_json(503, {"ok": False, "error": "Storage unavailable."})
            return
        if not draft or doc is None or resolved_version is None:
            self.send_not_found()
            return
        version = resolved_version
        _gated, identity, source = gate
        if identity is not None and not head_only:
            self.audit_view(identity, source, draft["draftId"], version, match.group(3))
        record = draft.get("versions", {}).get(str(version), {})
        title = (draft["title"] if version == draft["latestVersionNumber"]
                 else title_from_html(doc, record.get("filename")))
        if match.group(3) == "preview.png":
            raw = preview_png(title, urlparse(self.base_url).hostname or "Taildoc")
            self.send_response(200)
            for name, value in {
                "Content-Type": "image/png", "Content-Length": str(len(raw)),
                "Cache-Control": "private, no-store", "X-Content-Type-Options": "nosniff",
                "Referrer-Policy": "no-referrer", "Content-Security-Policy": "default-src 'none'",
                "X-Tailplan-Draft-Id": draft["draftId"], "X-Tailplan-Draft-Version": str(version),
            }.items():
                self.send_header(name, value)
            self.end_headers()
            if not head_only:
                self.wfile.write(raw)
            return
        ref_for_url = draft.get("slug") or draft["draftId"]
        root = f"{self.base_url.rstrip('/')}/d/{ref_for_url}"
        id_root = f"{self.base_url.rstrip('/')}/d/{draft['draftId']}"
        image_url = f"{id_root}/v/{version}/preview.png"
        share_url = f"{id_root}/v/{version}/share" if match.group(2) else f"{root}/share"
        self.send_document(
            preview_document(doc, title, image_url, share_url),
            head_only=head_only,
            draft_id=draft["draftId"],
            version=version,
            allow_inline_scripts=allow_scripts,
        )

    def serve_source(self, ref: str) -> None:
        if not self.rate_allowed("source-ip:" + self.source_ip(), self.server.upload_ip_limit):
            return
        try:
            if self.headers.get("Authorization") and self.api_identity() is None:
                self.unauthorized()
                return
            gate = self.gate_draft(ref, api=True)
        except (StorageError, OSError):
            self.send_json(503, {"ok": False, "error": "Storage unavailable."})
            return
        if gate is None:
            return
        _draft, identity, _source = gate
        try:
            source = self.store.get_source(
                ref,
                account_id=identity["accountId"] if identity else None,
            )
        except (StorageError, OSError) as exc:
            self.log_message("source storage error: %r", exc)
            self.send_json(503, {"ok": False, "error": "Storage unavailable."})
            return
        if source is None:
            self.send_json(404, {"ok": False, "error": "Draft not found."})
            return
        if identity is not None:
            self.audit_event("draft.source", identity, "draft", source["draftId"])
        self.send_json(200, source)

    def method_enabled(self, method: str | None) -> bool:
        mode = {"bootstrap": "api_key", "api_key": "api_key", "tailscale": "tailscale", "oidc": "oidc"}.get(
            method or "", "api_key")
        return mode in self.auth.modes

    def with_roles(self, identity: dict | None) -> dict | None:
        """Add the instance role, workspace roles, and verified email to an identity."""
        if identity is None:
            return None
        policy = self.auth.policy
        account_id = identity.get("accountId")
        roles: dict = {"instanceRole": None, "workspaces": {}, "defaultWorkspace": policy.default_workspace,
                       "email": None, "emailVerified": False, "groups": []}
        principal = self.store.account_principal(account_id) if account_id != "anonymous" else None
        ts_login = None
        if identity.get("apiKeyId") == "tailscale" and isinstance(identity.get("login"), str):
            ts_login = identity["login"].strip().lower()
        elif principal and principal["issuer"] == "tailscale":
            ts_login = principal["subject"].strip().lower()
        if ts_login:
            roles["tailscaleLogin"] = ts_login
        # A stored email counts only when it is a real email address. Older
        # Tailscale principals stored dot-free logins such as `bob@passkey`.
        stored_email = principal["email"] if principal else None
        valid_email = (stored_email.strip().lower() if isinstance(stored_email, str)
                       and EMAIL_RE.fullmatch(stored_email.strip().lower()) else None)
        if principal:
            roles.update(
                instanceRole=principal["instanceRole"], workspaces=principal["workspaces"],
                defaultWorkspace=principal["defaultWorkspace"] or policy.default_workspace,
                email=valid_email, emailVerified=valid_email is not None and principal["emailVerified"],
                groups=principal["groups"],
            )
        elif account_id not in {"local", "anonymous"} and not policy.configured:
            roles.update(policy.open_roles())
        if account_id == "anonymous":
            roles["policyMatched"] = False
        elif account_id == "local" or not policy.configured:
            roles["policyMatched"] = True
        else:
            # Check the live policy on every request, so a rule removed from
            # the policy file takes the roles away at once.
            resolved = policy.resolve(
                email=valid_email,
                email_verified=valid_email is not None and bool(principal and principal["emailVerified"]),
                groups=principal["groups"] if principal else [],
                tailscale_login=ts_login,
            )
            roles["policyMatched"] = resolved is not None
            if resolved is None:
                roles.update(instanceRole=None, workspaces={}, defaultWorkspace=policy.default_workspace)
            else:
                roles.update(instanceRole=resolved["instanceRole"], workspaces=dict(resolved["workspaces"]),
                             defaultWorkspace=resolved["defaultWorkspace"])
        merged = {**identity, **roles}
        if account_id == "local":
            merged["instanceRole"] = "admin"
            merged["workspaces"] = {**merged["workspaces"], policy.legacy_workspace: "admin"}
        return merged

    def view_identity(self, draft_id: str) -> tuple[dict | None, str]:
        """Return the identity from the view cookie or the `tv` view token."""
        key = self.purpose_key("view")
        candidates: list[tuple[str, dict]] = []
        cookie = read_signed(self.cookie_value("tailplan_view"), key)
        if cookie and cookie.get("t") == "cookie":
            candidates.append(("cookie", cookie))
        if self.command in {"GET", "HEAD"}:
            tokens = parse_qs(urlparse(self.path).query).get("tv", [])
            token = read_signed(tokens[0], key) if len(tokens) == 1 else None
            if token and token.get("t") == "tv" and token.get("d") == draft_id:
                candidates.append(("token", token))
        for source, payload in candidates:
            record = self.store.session_by_hash(payload.get("h"))
            if record is None or not self.method_enabled(record["authMethod"]):
                continue
            if record["authMethod"] == "api_key":
                key_id = record.get("apiKeyId")
                if not key_id or not any(key["id"] == key_id for key in self.store.list_keys(record["accountId"])):
                    continue
            elif record["authMethod"] == "bootstrap" and record["accountId"] != "local":
                continue
            elif record["authMethod"] == "tailscale" and not self.server.trust_tailscale_identity:
                continue
            account = self.store.account(record["accountId"])
            if account is None:
                continue
            return self.with_roles({**account, "apiKeyId": "view", "csrf": "",
                                    "sessionHash": record["sessionHash"]}), source
        return None, ""

    def draft_viewer(self, draft: dict, *, allow_tailscale_header: bool = False) -> tuple[dict | None, str]:
        """Find the reader: session, view cookie or token, Bearer key, then Tailscale header.

        The Tailscale header counts only when the caller allows it, for a
        page read. API and management routes never pass it.
        """
        session = self.session()
        if session is not None:
            return session, "session"
        identity, source = self.view_identity(draft["draftId"])
        if identity is not None:
            return identity, source
        if self.headers.get("Authorization"):
            # A Bearer key always wins over the passive header.
            api = self.with_roles(self.api_identity())
            return (api, "bearer") if api is not None else (None, "")
        if allow_tailscale_header:
            passive = self.passive_tailscale_identity()
            if passive is not None:
                return passive, "tailscale_header"
        return None, ""

    def passive_tailscale_identity(self) -> dict | None:
        """Return a stateless read-only identity from the Tailscale Serve header.

        This needs `--trust-tailscale-identity` and `--tailscale-viewer` on a
        loopback listener, and a GET or HEAD request. It writes nothing: no
        account, principal, session, cookie, or view token.
        """
        server = self.server
        if not (server.trust_tailscale_identity and server.tailscale_viewer):
            return None
        if self.client_address[0] not in {"127.0.0.1", "::1"} or self.command not in {"GET", "HEAD"}:
            return None
        # TAILPLAN_AUTH_MODES does not gate it: that setting controls
        # interactive sign-in, and this identity never signs in.
        found = self.tailscale_login()
        if found is None:
            return None
        login, name = found
        login_key = login.strip().lower()
        if TAILSCALE_LOGIN_RE.fullmatch(login_key) is None or not self.login_allowed(login_key):
            return None
        email = login_key if EMAIL_RE.fullmatch(login_key) else None
        owner = server.owner_login
        is_owner = bool(owner) and login_key == owner.strip().lower()
        policy = self.auth.policy
        roles = policy.resolve(email=email, email_verified=email is not None, groups=[],
                               tailscale_login=login_key)
        matched = roles is not None or is_owner
        if roles is None and not policy.configured:
            roles, matched = policy.open_roles(), True
        roles = roles or {"instanceRole": None, "workspaces": {}, "defaultWorkspace": policy.default_workspace}
        return {
            "accountId": None,
            "accountName": name,
            "apiKeyId": "tailscale_header",
            "authMethod": "tailscale_header",
            "passive": True,
            "login": login,
            "tailscaleLogin": login_key,
            "email": email,
            "emailVerified": email is not None,
            "groups": [],
            "instanceRole": None,
            "workspaces": dict(roles["workspaces"]),
            "defaultWorkspace": roles["defaultWorkspace"],
            "ownerLogin": is_owner,
            "policyMatched": matched,
        }

    def gate_draft(self, ref: str, *, api: bool) -> tuple[dict, dict | None, str] | None:
        """Check read access to a draft. Send the refusal and return None when access fails.

        A signed-out browser goes to sign-in, and a signed-out API call gets 401.
        A signed-in reader without access gets 404, so the draft stays hidden.
        """
        draft = self.store.resolve_draft(ref)
        if draft is None or draft.get("disabledAt"):
            if api:
                self.send_json(404, {"ok": False, "error": "Draft not found."})
            else:
                self.send_not_found()
            return None
        public = draft_role(None, draft, [], self.auth) is not None
        if public and not api:
            return draft, None, ""
        # Only a page read (api=False) may use the passive Tailscale header.
        identity, source = self.draft_viewer(draft, allow_tailscale_header=not api)
        if public:
            return draft, identity, source
        grants = self.store.list_grants(draft["draftId"]) if identity else []
        if draft_role(identity, draft, grants, self.auth) is not None:
            return draft, identity, source
        if identity is None:
            if self.wants_html() and not self.headers.get("Authorization"):
                self.send_sign_in_redirect()
            else:
                self.send_json(401, {"ok": False, "error": "Sign in to read this draft."})
            return None
        if api:
            self.send_json(404, {"ok": False, "error": "Draft not found."})
        else:
            self.send_not_found()
        return None

    def view_credentials(self, identity: dict | None, source: str, draft_id: str,
                         multi_page: bool) -> tuple[list[str], str | None]:
        """Return the view cookie and the link token for a signed-in reader."""
        session_hash = (identity or {}).get("sessionHash")
        if not session_hash:
            return [], None
        key = self.purpose_key("view")
        expires = int(time.time()) + VIEW_TOKEN_TTL
        cookies = []
        if source != "cookie":
            cookies.append(self.view_cookie_header(
                signed_value({"t": "cookie", "h": session_hash, "expires": expires}, key), VIEW_TOKEN_TTL))
        token = signed_value({"t": "tv", "d": draft_id, "h": session_hash, "expires": expires}, key)
        return cookies, token if multi_page else None

    def view_cookie_header(self, value: str, age: int) -> str:
        prefix = urlparse(self.base_url).path.rstrip("/")
        return f"tailplan_view={value}; Path={prefix}/d/; Max-Age={age}; HttpOnly; Secure; SameSite=None"

    def relative_target(self) -> str:
        """Return this request path and query, without a view token, for a sign-in return."""
        query = "&".join(part for part in urlparse(self.path).query.split("&")
                         if part and not part.startswith("tv="))
        return self.route_path() + (f"?{query}" if query else "")

    def wants_html(self) -> bool:
        return "text/html" in self.headers.get("Accept", "")

    def send_not_found(self) -> None:
        """Send the 404 page, with a sign-in link for a reader who is not signed in."""
        signed_in = bool(self.cookie_value("tailplan_session"))
        self.send_html(404, not_found(None if signed_in else self.base_url.rstrip("/") + "/auth/sign-in"))

    def send_sign_in_redirect(self) -> None:
        location = self.base_url.rstrip("/") + "/auth/sign-in?next=" + quote(self.relative_target(), safe="/")
        self.send_response(HTTPStatus.FOUND)
        self.send_header("Location", location)
        self.send_header("Cache-Control", "no-store")
        self.send_header("Content-Length", "0")
        self.end_headers()

    def audit_event(self, event: str, actor: dict | None, target_type: str | None = None,
                    target_id: str | None = None, *, outcome: str = "success", detail: dict | None = None) -> None:
        try:
            self.store.audit(event, actor=actor, target_type=target_type, target_id=target_id,
                             outcome=outcome, detail={"sourceIp": self.source_ip(), **(detail or {})})
        except (StorageError, OSError) as exc:
            self.log_message("audit error: %r", exc)

    def viewer_miss_key(self) -> str:
        identity = self.tailscale_login()
        if identity:
            return "viewer-404:login:" + identity[0].casefold()
        return "viewer-404:ip:" + self.source_ip()

    def send_response(self, code, message=None) -> None:
        self.response_status = int(code)
        super().send_response(code, message)

    def do_GET(self) -> None:
        route = self.route_path()
        if not route.startswith("/d/") and not SOURCE_ROUTE_RE.fullmatch(route):
            self.route_get()
            return
        key = self.viewer_miss_key()
        limit = self.server.viewer_404_limit
        wait = self.server.viewer_limiter.wait(key, limit)
        if wait:
            self.send_json(429, {"ok": False, "error": "Too many missing drafts. Try again later."},
                           {"Retry-After": str(wait)})
            return
        self.response_status = 0
        self.route_get()
        if self.response_status == 404:
            self.server.viewer_limiter.record(key, limit)

    def route_get(self) -> None:
        preview = PREVIEW_ROUTE_RE.fullmatch(self.route_path())
        if preview:
            self.serve_preview(preview, head_only=self.command == "HEAD")
            return
        parsed_target = urlparse(self.path)
        path = self.route_path()
        if path.startswith("/assets/"):
            self.serve_asset(path[len("/assets/"):])
            return
        source_match = SOURCE_ROUTE_RE.fullmatch(path)
        if source_match:
            self.serve_source(source_match.group(1))
            return
        if path in {"/api/themes", "/api/typography"}:
            try:
                self.send_json(200, tailplan_themes.catalog() if path == "/api/themes"
                               else tailplan_themes.typography_catalog())
            except tailplan_themes.ThemeError as error:
                self.send_json(503, {"ok": False, "error": str(error)})
            return
        if path == "/healthz":
            self.send_json(
                200,
                {"ok": True, "service": "tailplan", "build": BUILD_ID, "time": now_iso()},
            )
            return
        if path == "/readyz":
            try:
                self.store.check_ready()
                tailplan_themes.catalog()
            except (StorageError, OSError, tailplan_themes.ThemeError) as exc:
                self.log_message("readiness storage error: %r", exc)
                self.send_json(
                    503,
                    {"ok": False, "service": "tailplan", "build": BUILD_ID},
                )
                return
            self.send_json(
                200,
                {"ok": True, "service": "tailplan", "build": BUILD_ID},
            )
            return
        if path.startswith(("/api/", "/dashboard", "/cli/auth", "/auth/")) or path == "/cli/activate":
            self.management_request("GET", path)
            return
        route = self.parse_draft_route(path)
        if route is not None:
            self.serve_viewer(
                route,
                path=path,
                query=parsed_target.query,
                head_only=self.command == "HEAD",
            )
            return
        if path == "/" or path == "":
            self.send_html(200, home(self.base_url))
            return
        self.send_not_found()

    def do_HEAD(self) -> None:
        self.do_GET()

    def _read_large_json_body(self, *, allow_empty: bool = False) -> dict | None:
        if self.headers.get("Transfer-Encoding"):
            self.send_json(400, {"ok": False, "error": "Transfer-Encoding is not supported."})
            return None
        length_headers = self.headers.get_all("Content-Length") or []
        if len(length_headers) > 1:
            self.send_json(
                400,
                {"ok": False, "error": "Multiple Content-Length headers are not allowed."},
            )
            return None
        length_header = length_headers[0] if length_headers else None
        if length_header is None:
            if allow_empty:
                return {}
            self.send_json(411, {"ok": False, "error": "Content-Length is required."})
            return None
        if re.fullmatch(r"[0-9]+", length_header) is None:
            self.send_json(
                400,
                {"ok": False, "error": "Content-Length must contain only ASCII decimal digits."},
            )
            return None
        if len(length_header) > len(str(MAX_REQUEST_BYTES)):
            self.send_json(413, {"ok": False, "error": "Upload body too large."})
            return None
        length = int(length_header)
        if length == 0:
            if allow_empty:
                return {}
            self.send_json(400, {"ok": False, "error": "Content-Length must be positive."})
            return None
        if length > MAX_REQUEST_BYTES:
            self.send_json(413, {"ok": False, "error": "Upload body too large."})
            return None
        body = self.rfile.read(length)
        if len(body) != length:
            self.send_json(400, {"ok": False, "error": "Upload body was incomplete."})
            return None
        payload = json.loads(body.decode("utf-8"))
        if not isinstance(payload, dict):
            self.send_json(400, {"ok": False, "error": "JSON payload must be an object."})
            return None
        return payload

    def _extract_upload_pages(self, payload: dict) -> tuple[list[dict], dict | None]:
        frozen_theme = None
        if "content" in payload:
            if "html" in payload or "files" in payload:
                raise tailplan_themes.ThemeError("Send content, html, or files, not multiple.")
            html_doc, frozen_theme = tailplan_themes.render(
                payload["content"],
                format=payload.get("format", "markdown"),
                filename=payload.get("filename", "document.md"),
                theme=payload.get("theme", "auto"),
                document_type=payload.get("documentType", "document"),
                layout=payload.get("layout", "reading"),
                typography=payload.get("typography"),
            )
            return [{"path": "index.html", "html": html_doc}], frozen_theme
        if any(key in payload for key in ("theme", "documentType", "layout", "format", "typography")):
            raise tailplan_themes.ThemeError("Theme options require Markdown or text content")
        if "files" in payload:
            if "html" in payload:
                raise ValueError("Send html or files, not both.")
            files = payload["files"]
            if not isinstance(files, list):
                raise ValueError("files must be a list.")
            return [
                {"path": item.get("path"), "html": item.get("html")}
                if isinstance(item, dict)
                else item
                for item in files
            ], None
        return [{"path": "index.html", "html": payload.get("html")}], None

    def handle_check(self) -> None:
        try:
            identity = self.api_identity()
        except (StorageError, OSError):
            self.send_json(503, {"ok": False, "error": "Storage unavailable."})
            return
        if identity is None and self.server.allow_anonymous_uploads and not self.headers.get("Authorization"):
            identity = {
                "accountId": "anonymous",
                "accountName": "Anonymous",
                "apiKeyId": "anonymous",
                "apiKeyName": "Anonymous",
            }
        if identity is None:
            self.unauthorized()
            return
        if not self.rate_allowed("check-ip:" + self.source_ip(), self.server.upload_ip_limit):
            return
        if not self.rate_allowed("check-key:" + identity["apiKeyId"], self.server.upload_key_limit):
            return
        try:
            payload = self._read_large_json_body(allow_empty=True)
            if payload is None:
                return
            report: dict = {
                "ok": True,
                "account": {
                    "id": identity["accountId"],
                    "name": identity.get("accountName", identity["accountId"]),
                    "apiKeyName": identity.get("apiKeyName", identity["apiKeyId"]),
                },
                "flags": [],
                "limits": {"maxBytes": MAX_HTML_BYTES, "maxPages": MAX_PAGES_PER_UPLOAD},
                "capabilities": {
                    "inlineScripts": identity["accountId"] != "anonymous",
                    "multiPage": MAX_PAGES_PER_UPLOAD > 1,
                    "customUrls": identity["accountId"] != "anonymous",
                    "externalAssets": "https images, video, audio, fonts, and stylesheets",
                    "blockedInSandbox": SANDBOX_LIMITS,
                },
            }
            if any(key in payload for key in ("html", "files", "content")):
                try:
                    pages, _theme = self._extract_upload_pages(payload)
                    validation = validate_upload(pages)
                    report.update(
                        {
                            "ok": validation["ok"],
                            "totalBytes": validation["totalBytes"],
                            "pages": [
                                {
                                    "path": page["path"],
                                    "bytes": page["bytes"],
                                    "title": page["title"],
                                    "hasScripts": page["hasScripts"],
                                }
                                for page in validation["pages"]
                            ],
                            "issues": validation["issues"],
                            "errors": validation["errors"],
                            "warnings": validation["warnings"],
                        }
                    )
                except (tailplan_themes.ThemeError, ValueError) as exc:
                    message = str(exc)
                    report.update(
                        {
                            "ok": False,
                            "issues": [{"level": "error", "code": "invalid-input", "message": message, "count": 1}],
                            "errors": [message],
                            "warnings": [],
                        }
                    )
            if "slug" in payload and payload["slug"] is not None:
                slug_value = normalize_slug(payload["slug"]) or ""
                if identity["accountId"] == "anonymous":
                    status = {
                        "slug": slug_value,
                        "status": "unavailable",
                        "message": "Custom URLs require an authenticated account.",
                    }
                else:
                    status = self.store.slug_status(slug_value, identity["accountId"])
                report["slug"] = status
                if status["status"] in {"unavailable", "taken"}:
                    report["ok"] = False
            self.send_json(200, report)
        except (UnicodeDecodeError, json.JSONDecodeError):
            self.send_json(400, {"ok": False, "error": "Request body must be valid UTF-8 JSON."})
        except (StorageError, OSError) as exc:
            self.log_message("check storage error: %r", exc)
            self.send_json(503, {"ok": False, "error": "Storage unavailable."})

    def do_POST(self) -> None:
        path = self.route_path()
        if path == "/api/check":
            self.handle_check()
            return
        if path != "/api/uploads":
            self.management_request("POST", path)
            return
        try:
            identity = self.api_identity()
        except (StorageError, OSError):
            self.send_json(503, {"ok": False, "error": "Storage unavailable."})
            return
        if identity is None and self.server.allow_anonymous_uploads and not self.headers.get("Authorization"):
            identity = {"accountId": "anonymous", "apiKeyId": "anonymous"}
        if identity is None:
            self.unauthorized()
            return
        if not self.rate_allowed("upload-ip:" + self.source_ip(), self.server.upload_ip_limit):
            return
        if not self.rate_allowed("upload-key:" + identity["apiKeyId"], self.server.upload_key_limit):
            return
        try:
            payload = self._read_large_json_body()
            if payload is None:
                return
            try:
                pages, frozen_theme = self._extract_upload_pages(payload)
            except ValueError as error:
                self.send_json(422, {"ok": False, "error": str(error)})
                return
            validation = validate_upload(pages)
            if not validation["ok"]:
                self.send_json(
                    422,
                    {
                        "ok": False,
                        "errors": validation["errors"],
                        "warnings": validation["warnings"],
                        "issues": validation["issues"],
                    },
                )
                return
            slug = normalize_slug(payload.get("slug")) if "slug" in payload else None
            if slug is not None:
                if identity["accountId"] == "anonymous":
                    self.send_json(403, {"ok": False, "error": "Custom URLs require an authenticated account."})
                    return
                problem = slug_problem(slug)
                if problem is not None:
                    self.send_json(422, {"ok": False, "error": problem})
                    return
            filename = payload.get("filename")
            draft_id = payload.get("draftId")
            if filename is not None and not isinstance(filename, str):
                self.send_json(400, {"ok": False, "error": "filename must be a string."})
                return
            if isinstance(filename, str):
                try:
                    filename.encode("utf-8")
                except UnicodeEncodeError:
                    self.send_json(
                        400,
                        {"ok": False, "error": "filename contains invalid Unicode."},
                    )
                    return
            if draft_id is not None and (
                not isinstance(draft_id, str) or DRAFT_ID_RE.fullmatch(draft_id) is None
            ):
                self.send_json(400, {"ok": False, "error": "draftId is invalid."})
                return
            roles = self.with_roles(identity) or {}
            creating_new = draft_id is None and (slug is None or self.store.resolve_draft(slug) is None)
            sharing = self.upload_sharing(identity, payload, roles=roles, creating_new=creating_new)
            if sharing is None:
                return
            request_key = self.headers.get("Idempotency-Key")
            if request_key is not None and (
                IDEMPOTENCY_KEY_RE.fullmatch(request_key) is None
            ):
                self.send_json(400, {"ok": False, "error": "Idempotency-Key is invalid."})
                return
            tags = favorite = None
            if payload.get("tags") is not None:
                try:
                    tags = normalize_tags(payload["tags"])
                except ValueError as error:
                    self.send_json(422, {"ok": False, "error": str(error)})
                    return
            if payload.get("favorite") is not None:
                if type(payload["favorite"]) is not bool:
                    self.send_json(400, {"ok": False, "error": "favorite must be true or false."})
                    return
                favorite = payload["favorite"]
            index_html = next(p["html"] for p in pages if p["path"] == "index.html")
            result = self.store.upsert(
                index_html,
                filename,
                draft_id,
                self.base_url,
                request_key,
                account_id=identity["accountId"],
                description=clean_text(payload.get("description"), 1000),
                metadata={**upload_metadata(payload.get("metadata", {})),
                          **({"theme": frozen_theme} if frozen_theme else {})},
                audit={"apiKeyId": identity["apiKeyId"], "sourceIp": self.source_ip(),
                       "userAgent": clean_text(self.headers.get("User-Agent")),
                       "requestId": secrets.token_hex(16)},
                files=pages if len(pages) > 1 else None,
                slug=slug,
                validation=validation,
                workspace=sharing.get("workspace"),
                default_workspace=sharing.get("defaultWorkspace"),
                visibility=sharing.get("visibility"),
                grants=sharing.get("grants"),
                tags=tags,
                favorite=favorite,
            )
            created = result.pop("created")
            status = 201 if created else 200
            stored = self.store.resolve_draft(result["draftId"]) or {}
            audit_sharing = {k: v for k, v in sharing.items() if k != "defaultWorkspace"}
            if "workspace" not in audit_sharing and sharing.get("defaultWorkspace") and created:
                audit_sharing["workspace"] = sharing["defaultWorkspace"]
            self.audit_event("draft.upload", roles, "draft", result["draftId"],
                             detail={"created": created, "version": result.get("versionNumber"), **audit_sharing})
            if not result.get("replayed"):
                for grant in sharing.get("grants") or []:
                    self.audit_event("sharing.grant", roles, "draft", result["draftId"],
                                     detail={"type": grant["type"], "value": grant["value"],
                                             "role": grant["role"]})
            self.send_json(
                status,
                {
                    "ok": True,
                    **result,
                    "workspace": draft_workspace(stored, self.auth),
                    "visibility": effective_visibility(stored, self.auth),
                    "viewerAuthMode": self.auth.viewer_mode,
                    "warnings": validation["warnings"],
                    "shareUrl": result["publicUrl"] + "/share",
                },
            )
        except tailplan_themes.ThemeError as error:
            self.send_json(422, {"ok": False, "error": str(error)})
        except (UnicodeDecodeError, json.JSONDecodeError):
            self.send_json(400, {"ok": False, "error": "Request body must be valid UTF-8 JSON."})
        except (ValueError, TypeError, UnicodeEncodeError):
            self.send_json(400, {"ok": False, "error": "Upload metadata is invalid."})
        except (IdempotencyConflict, ConflictError) as exc:
            body = {"ok": False, "error": str(exc)}
            if getattr(exc, "code", None):
                body["code"] = exc.code
            self.send_json(409, body)
        except KeyError as e:
            self.send_json(404, {"ok": False, "error": str(e).strip("'")})
        except (StorageError, OSError) as e:
            self.send_json(503, {"ok": False, "error": "Storage unavailable."})
            self.log_message("storage error: %r", e)
        except Exception as e:  # noqa: BLE001 - The request handler returns JSON for unexpected upload errors.
            self.log_message("unexpected upload error: %r", e)
            self.send_json(500, {"ok": False, "error": "Internal server error."})

    def upload_sharing(
        self,
        identity: dict,
        payload: dict,
        *,
        roles: dict | None = None,
        creating_new: bool = False,
    ) -> dict | None:
        """Check the upload workspace and visibility. Send the error and return None when they fail."""
        roles = roles if roles is not None else (self.with_roles(identity) or {})
        sharing: dict = {}
        for field in ("workspace", "visibility", "grants"):
            if payload.get(field) is None:
                continue
            if identity["accountId"] == "anonymous":
                self.send_json(403, {"ok": False, "error": "Sharing options require an authenticated account."})
                return None
            sharing[field] = payload[field]
        visibility = sharing.get("visibility")
        if visibility is not None:
            if visibility not in VISIBILITY_RANK:
                self.send_json(422, {"ok": False, "error": "visibility must be private, workspace, authenticated, or link."})
                return None
            if VISIBILITY_RANK[visibility] > VISIBILITY_RANK[self.auth.max_visibility]:
                self.send_json(422, {"ok": False, "error": f"visibility exceeds the server maximum ({self.auth.max_visibility})."})
                return None
        workspace = sharing.get("workspace")
        if workspace is not None:
            if not isinstance(workspace, str) or WORKSPACE_RE.fullmatch(workspace) is None:
                self.send_json(422, {"ok": False, "error": "workspace must be a lowercase workspace name."})
                return None
            if not workspace_allowed(roles, workspace):
                self.send_json(403, {"ok": False, "error": "You cannot publish to that workspace."})
                return None
        elif creating_new:
            default_ws = roles.get("defaultWorkspace") or self.auth.policy.default_workspace
            if self.auth.policy.configured and identity["accountId"] != "anonymous":
                if not workspace_allowed(roles, default_ws):
                    self.send_json(403, {"ok": False, "error": "You cannot publish to that workspace."})
                    return None
            sharing["defaultWorkspace"] = default_ws
        if "grants" in sharing:
            grants = upload_grants(sharing["grants"])
            if isinstance(grants, str):
                self.send_json(422, {"ok": False, "error": grants})
                return None
            sharing["grants"] = grants
        return sharing

    def rate_allowed(self, key: str, maximum: int, window: int = 60) -> bool:
        wait = self.server.limiter.allow(key, maximum, window)
        if wait:
            self.send_json(429, {"ok": False, "error": "Rate limit exceeded."},
                           {"Retry-After": str(wait)})
        return not wait

    def read_control_body(self) -> dict:
        lengths = self.headers.get_all("Content-Length") or []
        if (len(lengths) != 1 or not re.fullmatch(r"[0-9]{1,6}", lengths[0])
                or self.headers.get("Transfer-Encoding")):
            raise ValueError("One valid Content-Length is required.")
        size = int(lengths[0])
        if size > 65536:
            raise ValueError("Request body is too large.")
        body = self.rfile.read(size)
        if len(body) != size:
            raise ValueError("Request body was incomplete.")
        if self.headers.get_content_type() == "application/x-www-form-urlencoded":
            return {key: values[-1] for key, values in parse_qs(body.decode("utf-8"),
                    keep_blank_values=True, max_num_fields=20).items()}
        value = json.loads(body or b"{}")
        if not isinstance(value, dict):
            raise TypeError("Request body must be an object.")
        return value

    def cookie_value(self, name: str) -> str:
        try:
            cookies = SimpleCookie(self.headers.get("Cookie", ""))
            return cookies[name].value if name in cookies else ""
        except CookieError:
            return ""

    def cookie_header(self, name: str, value: str, age: int) -> str:
        cookies = SimpleCookie()
        cookies[name] = value
        cookies[name]["path"] = (urlparse(self.base_url).path.rstrip("/") or "") + "/"
        cookies[name]["httponly"] = True
        cookies[name]["samesite"] = "Lax"
        cookies[name]["max-age"] = age
        if urlparse(self.base_url).scheme == "https":
            cookies[name]["secure"] = True
        return cookies[name].OutputString()

    def session(self) -> dict | None:
        value = read_signed(self.cookie_value("tailplan_session"), self.session_secret)
        if not value or not all(isinstance(value.get(field), str)
                                for field in ("accountId", "apiKeyId", "csrf")):
            return None
        account = self.store.account(value["accountId"])
        if not account:
            return None
        key_id = value["apiKeyId"]
        if "sid" in value or key_id == "oidc":
            record = self.store.session_record(value.get("sid"))
            if (record is None or record["accountId"] != value["accountId"]
                    or record["epoch"] != value.get("epoch")
                    or not hmac.compare_digest(record["csrf"].encode(), value["csrf"].encode())):
                return None
            value = {**value, "sessionHash": record["sessionHash"]}
        method = key_id if key_id in {"bootstrap", "tailscale", "oidc"} else "api_key"
        if not self.method_enabled(method):
            return None
        if key_id == "oidc":
            return self.with_roles({**value, **account})
        if key_id == "bootstrap" and value["accountId"] != "local":
            return None
        if key_id == "tailscale":
            if not self.server.trust_tailscale_identity:
                return None
            if value["accountId"] == "local" and value.get("login", "").casefold() != self.server.owner_login.casefold():
                return None
            if not self.login_allowed(str(value.get("login", ""))):
                return None
        elif key_id != "bootstrap":
            keys = self.store.list_keys(value["accountId"])
            if not any(key["id"] == key_id for key in keys):
                return None
        return self.with_roles({**value, **account})

    def redirect(self, path: str, cookie: str | list[str] | None = None) -> None:
        self.send_response(303)
        self.send_header("Location", self.base_url.rstrip("/") + path)
        self.send_header("Cache-Control", "no-store")
        self.send_header("Content-Length", "0")
        for value in [cookie] if isinstance(cookie, str) else cookie or []:
            self.send_header("Set-Cookie", value)
        self.end_headers()

    def start_session(self, identity: dict, method: str, next_path: str, *, extra_cookies: list[str] | None = None,
                      issuer: str | None = None, subject: str | None = None,
                      idp_session_id: str | None = None, epoch: int = 0) -> None:
        """Store a revocable session, set its cookie, and send the browser to `next_path`."""
        ttl = self.auth.session_ttl
        csrf = secrets.token_hex(24)
        session_id = self.store.create_session(
            account_id=identity["accountId"], auth_method=method, csrf=csrf, ttl=ttl,
            issuer=issuer, subject=subject, idp_session_id=idp_session_id,
            api_key_id=identity.get("apiKeyId") if method == "api_key" else None, epoch=epoch)
        cookie_fields = {key: identity[key] for key in ("accountId", "apiKeyId", "login") if key in identity}
        session = {**cookie_fields, "csrf": csrf, "sid": session_id, "epoch": epoch, "expires": int(time.time()) + ttl}
        self.audit_event("auth.login", self.with_roles(identity), "account", identity["accountId"],
                         detail={"method": method})
        self.redirect(safe_next_path(next_path), [
            self.cookie_header("tailplan_session", signed_value(session, self.session_secret), ttl),
            *(extra_cookies or []),
        ])

    def oidc_cookie_header(self, value: str, age: int) -> str:
        prefix = urlparse(self.base_url).path.rstrip("/")
        secure = "; Secure" if urlparse(self.base_url).scheme == "https" else ""
        return f"tailplan_oidc={value}; Path={prefix}/auth/oidc/; Max-Age={age}; HttpOnly; SameSite=Lax{secure}"

    def oidc_request(self, method: str, path: str) -> None:
        client = self.auth.oidc
        if client is None:
            self.send_not_found()
        elif method == "POST" and path == "/auth/oidc/backchannel-logout":
            self.oidc_backchannel_logout(client)
        elif "oidc" not in self.auth.modes:
            self.send_not_found()
        elif method == "GET" and path == "/auth/oidc/start":
            self.oidc_start(client)
        elif method == "GET" and path == "/auth/oidc/callback":
            self.oidc_callback(client)
        else:
            self.send_not_found()

    def oidc_redirect_uri(self) -> str:
        return self.base_url.rstrip("/") + "/auth/oidc/callback"

    def oidc_start(self, client: OIDCClient) -> None:
        if not self.rate_allowed("login:" + self.source_ip(), 20):
            return
        query = parse_qs(urlparse(self.path).query)
        flow = {
            "state": secrets.token_urlsafe(24), "nonce": secrets.token_urlsafe(24),
            "verifier": secrets.token_urlsafe(48),
            "next": safe_next_path((query.get("next") or [""])[0]),
            "expires": int(time.time()) + OIDC_FLOW_TTL,
        }
        try:
            location = client.authorization_url(state=flow["state"], nonce=flow["nonce"],
                                                challenge=pkce_challenge(flow["verifier"]),
                                                redirect_uri=self.oidc_redirect_uri())
        except OIDCError as exc:
            self.log_message("oidc discovery error: %s", exc)
            self.send_html(502, web_page("Sign-in unavailable", "<h1>Sign-in is unavailable</h1>"
                                         "<p>Tailplan cannot reach the identity provider. Try again later.</p>",
                                         self.base_url), web=True)
            return
        self.send_response(HTTPStatus.FOUND)
        self.send_header("Location", location)
        self.send_header("Cache-Control", "no-store")
        self.send_header("Set-Cookie", self.oidc_cookie_header(
            signed_value(flow, self.purpose_key("oidc-flow")), OIDC_FLOW_TTL))
        self.send_header("Content-Length", "0")
        self.end_headers()

    def oidc_denied(self, reason: str, *, email: str | None = None, subject: str | None = None) -> None:
        self.audit_event("auth.denied", {"email": email}, "principal", subject, outcome="denied",
                         detail={"method": "oidc", "reason": reason})
        body = ("<h1>Sign-in failed</h1><p>Tailplan cannot sign you in with this account.</p>"
                f'<p><a href="{esc(self.base_url.rstrip("/"))}/auth/sign-in">Back to sign-in</a></p>')
        self.send_html(403, web_page("Sign-in failed", body, self.base_url), web=True,
                       cookie=self.oidc_cookie_header("", 0))

    def oidc_callback(self, client: OIDCClient) -> None:
        if not self.rate_allowed("login:" + self.source_ip(), 20):
            return
        query = {key: values[-1] for key, values in parse_qs(urlparse(self.path).query).items()}
        flow = read_signed(self.cookie_value("tailplan_oidc"), self.purpose_key("oidc-flow"))
        state = query.get("state", "")
        if (not flow or not all(isinstance(flow.get(key), str) for key in ("state", "nonce", "verifier", "next"))
                or not hmac.compare_digest(state.encode(), flow["state"].encode())):
            self.oidc_denied("state")
            return
        code = query.get("code", "")
        if "error" in query or not code or len(code) > 4096:
            self.oidc_denied("provider_error")
            return
        try:
            tokens = client.exchange(code, flow["verifier"], self.oidc_redirect_uri())
            claims = client.verify_id_token(tokens["id_token"], nonce=flow["nonce"])
            info: dict = {}
            access_token = tokens.get("access_token")
            if (claim_path(claims, client.groups_claim) is None or "email" not in claims) and isinstance(
                    access_token, str):
                info = client.userinfo(access_token, claims["sub"])
        except OIDCError as exc:
            self.log_message("oidc callback error: %s", exc)
            self.oidc_denied("token")
            return
        groups = groups_from_claim(claim_path(claims, client.groups_claim))
        if groups is None:
            groups = groups_from_claim(claim_path(info, client.groups_claim)) or []
        merged = {**info, **claims}
        email = merged.get("email") if isinstance(merged.get("email"), str) else None
        email = email.strip().lower() if email and EMAIL_RE.fullmatch(email.strip()) else None
        verified = bool(email) and merged.get("email_verified") is True
        subject = claims["sub"]
        roles = self.auth.policy.resolve(email=email, email_verified=verified, groups=groups)
        linked = bool(verified and email in self.auth.link_emails)
        if roles is None and not linked:
            self.oidc_denied("no_matching_rule", email=email, subject=subject)
            return
        roles = roles or {"instanceRole": None, "workspaces": {},
                          "defaultWorkspace": self.auth.policy.default_workspace}
        display = clean_text(merged.get("name") or merged.get("preferred_username") or email or subject) or subject
        if linked:
            account = self.store.account("local")
            instance_role = "admin"
        else:
            account = self.store.ensure_account(oidc_account_id(client.issuer, subject), display)
            instance_role = roles["instanceRole"]
        principal = self.store.upsert_principal(
            issuer=client.issuer, subject=subject, account_id=account["accountId"], email=email,
            email_verified=verified, display_name=display, groups=groups, instance_role=instance_role,
            workspaces=roles["workspaces"], default_workspace=roles["defaultWorkspace"],
        )
        idp_session = claims.get("sid") if isinstance(claims.get("sid"), str) else None
        self.start_session({**account, "apiKeyId": "oidc"}, "oidc", flow["next"],
                           extra_cookies=[self.oidc_cookie_header("", 0)], issuer=client.issuer,
                           subject=subject, idp_session_id=idp_session, epoch=principal["sessionEpoch"])

    def oidc_backchannel_logout(self, client: OIDCClient) -> None:
        if not self.rate_allowed("backchannel:" + self.source_ip(), 60):
            return
        try:
            payload = self.read_control_body()
            claims = client.verify_logout_token(payload.get("logout_token"))
        except (ValueError, TypeError, UnicodeError, OIDCError) as exc:
            self.log_message("back-channel logout rejected: %s", exc)
            self.send_json(400, {"error": "invalid_request"})
            return
        subject = claims.get("sub") if isinstance(claims.get("sub"), str) else None
        idp_session = claims.get("sid") if isinstance(claims.get("sid"), str) else None
        count = self.store.revoke_principal_sessions(client.issuer, subject=subject, idp_session_id=idp_session)
        self.audit_event("auth.logout", None, "principal", subject,
                         detail={"method": "backchannel", "sessions": count})
        self.send_json(200, {"ok": True})

    def tailscale_login(self) -> tuple[str, str] | None:
        if not self.server.trust_tailscale_identity or self.client_address[0] not in {"127.0.0.1", "::1"}:
            return None
        login = clean_text(self.headers.get("Tailscale-User-Login"))
        if not login:
            return None
        name = clean_text(unquote(self.headers.get("Tailscale-User-Name", ""))) or login
        return login, name

    def tailscale_identity(self, login: str, name: str) -> dict | None:
        """Return the account for a Tailscale login, or None when the RBAC policy denies it.

        The account is always keyed to the `tailscale` issuer. It never joins
        an OIDC account with the same email: that account can hold roles that
        need the IdP's MFA and back-channel logout.
        """
        owner = self.server.owner_login
        email = login.strip().lower()
        if TAILSCALE_LOGIN_RE.fullmatch(email) is None:
            self.audit_event("auth.denied", {"email": email}, "principal", email, outcome="denied",
                             detail={"method": "tailscale", "reason": "invalid_login"})
            return None
        is_owner = bool(owner) and email == owner.strip().lower()
        verified = EMAIL_RE.fullmatch(email) is not None
        policy = self.auth.policy
        roles = policy.resolve(email=email if verified else None, email_verified=verified, groups=[],
                               tailscale_login=email)
        if roles is None and policy.configured and not is_owner:
            self.audit_event("auth.denied", {"email": email}, "principal", email, outcome="denied",
                             detail={"method": "tailscale", "reason": "no_matching_rule"})
            return None
        account = self.store.identity_account(login, name, owner)
        if not is_owner:
            roles = roles or policy.open_roles()
            self.store.upsert_principal(
                issuer="tailscale", subject=email, account_id=account["accountId"],
                email=email if verified else None, email_verified=verified, display_name=name, groups=[],
                instance_role=roles["instanceRole"], workspaces=roles["workspaces"],
                default_workspace=roles["defaultWorkspace"],
            )
        return {**account, "apiKeyId": "tailscale", "login": login}

    def login_allowed(self, login: str) -> bool:
        allowed = self.server.allowed_logins
        return not allowed or login.strip().lower() in allowed

    def sign_in_page(self, next_path: str = "/dashboard") -> None:
        state = {"csrf": secrets.token_hex(24), "expires": int(time.time()) + 600}
        cookie = self.cookie_header("tailplan_login", signed_value(state, self.session_secret), 600)
        identity = self.tailscale_login()
        offer_tailscale = bool(
            identity
            and TAILSCALE_LOGIN_RE.fullmatch(identity[0].strip().lower())
            and self.login_allowed(identity[0])
            and "tailscale" in self.auth.modes
        )
        body = render_login(self.base_url, state["csrf"], safe_next_path(next_path), offer_tailscale,
                            api_key="api_key" in self.auth.modes,
                            oidc="oidc" in self.auth.modes and self.auth.oidc is not None)
        self.send_html(200, body, web=True, cookie=cookie)

    def valid_form(self, payload: dict, state: dict | None) -> bool:
        origin = self.headers.get("Origin")
        base = urlparse(self.base_url)
        if origin and origin != f"{base.scheme}://{base.netloc}":
            return False
        supplied = payload.get("csrf")
        return bool(state and isinstance(supplied, str) and hmac.compare_digest(
            supplied.encode(), str(state.get("csrf", "")).encode()
        ))

    def do_DELETE(self) -> None:
        self.management_request("DELETE", self.route_path())

    def do_PATCH(self) -> None:
        self.management_request("PATCH", self.route_path())

    def wants_json(self) -> bool:
        """True when the caller sent JSON or asked for JSON, so it wants no redirect."""
        return (self.headers.get_content_type() == "application/json"
                or "application/json" in self.headers.get("Accept", ""))

    def management_request(self, method: str, path: str) -> None:
        try:
            if path.startswith("/auth/oidc/"):
                self.oidc_request(method, path)
            elif path.startswith("/api/"):
                self.management_api(method, path)
            else:
                self.management_web(method, path)
        except AccessDenied as exc:
            self.send_json(403, {"ok": False, "error": str(exc) or "Forbidden"})
        except PolicyLimit as exc:
            self.send_json(422, {"ok": False, "error": str(exc)})
        except (json.JSONDecodeError, UnicodeError, TypeError):
            self.send_json(400, {"ok": False, "error": "Request fields or body are invalid."})
        except ValueError as exc:
            self.send_json(400, {"ok": False, "error": str(exc) or "Request fields or body are invalid."})
        except KeyError as exc:
            self.send_json(404, {"ok": False, "error": str(exc).strip("'")})
        except (StorageError, OSError) as exc:
            self.log_message("management storage error: %r", exc)
            self.send_json(503, {"ok": False, "error": "Storage unavailable."})

    def management_api(self, method: str, path: str) -> None:
        if method == "POST" and path in {"/api/auth/cli/start", "/api/auth/cli/poll"}:
            self.device_api(path)
            return
        identity = self.with_roles(self.api_identity())
        if identity is None:
            self.unauthorized()
            return
        account_id = identity["accountId"]
        sharing = re.fullmatch(r"/api/drafts/([a-z0-9][a-z0-9-]{1,39})/sharing(/grants)?", path)
        if method == "GET" and path == "/api/me":
            self.send_json(200, {"ok": True, **{key: identity.get(key) for key in (
                "accountId", "accountName", "apiKeyId", "apiKeyName", "instanceRole", "workspaces",
                "defaultWorkspace", "email")}, "scope": "tailnet"})
        elif method == "GET" and path == "/api/audit":
            if identity.get("instanceRole") not in INSTANCE_ROLES:
                raise AccessDenied("Admin or auditor role required.")
            query = {key: values[-1] for key, values in parse_qs(urlparse(self.path).query).items()}
            limit = min(max(int(query.get("limit", "100")), 1), 500)
            before = int(query["before"]) if query.get("before") else None
            event = query.get("event") or None
            self.send_json(200, {"ok": True, "events": self.store.list_audit(limit=limit, before=before, event=event)})
        elif sharing:
            ref, grants = sharing.groups()
            if method == "GET" and not grants:
                draft, _role = self.sharing_target(identity, ref)
                self.send_json(200, self.sharing_payload(draft))
            elif method == "POST":
                self.send_json(200, self.change_sharing(identity, ref, "grant" if grants else "update",
                                                        self.read_control_body()))
            elif method == "DELETE" and grants:
                self.send_json(200, self.change_sharing(identity, ref, "revoke", self.read_control_body()))
            else:
                self.send_json(404, {"ok": False, "error": "Not found."})
        elif method == "GET" and path == "/api/drafts":
            query = {key: values[-1] for key, values in parse_qs(urlparse(self.path).query).items()}
            if not query.keys() & {"view", "q", "sort", "dir", "tag", "archived", "group", "repo"}:
                self.send_json(200, {"ok": True, "drafts": self.store.list_drafts(account_id, self.base_url)})
                return
            view = query.get("view") or ("all" if query.get("archived", "").lower() in {"1", "true", "yes"}
                                         else "active")
            result = query_dashboard(
                self.store, self.auth, account_id, self.base_url, view=view, q=query.get("q", ""),
                sort=query.get("sort", ""), direction=query.get("dir", ""), group=query.get("group", "time"),
                tag=query.get("tag", ""), repo=query.get("repo", ""), shared_drafts=self.shared_drafts(identity))
            result.pop("favorites")
            self.send_json(200, {"ok": True, **result})
        elif method == "GET" and path == "/api/api-keys":
            self.send_json(200, {"ok": True, "apiKeys": self.store.list_keys(account_id)})
        elif method == "POST" and path == "/api/api-keys":
            if self.rate_allowed("keys:" + account_id, 10, 3600):
                payload = self.read_control_body()
                created = self.store.create_key(account_id, payload.get("name"))
                self.audit_event("key.create", identity, "api_key", created["apiKey"]["id"])
                self.send_json(201, created)
        elif (match := re.fullmatch(r"/api/api-keys/([a-z0-9]{6,32})/revoke", path)) and method == "POST":
            self.store.revoke_key(account_id, match[1])
            self.audit_event("key.revoke", identity, "api_key", match[1])
            self.send_json(200, {"ok": True})
        elif match := re.fullmatch(
            r"/api/drafts/([a-z0-9][a-z0-9-]{1,39})(?:/(versions|disable|enable|favorite))?",
            path,
        ):
            draft_ref, action = match.groups()
            if method == "PATCH" and action is None:
                self.send_json(200, self.patch_draft(identity, draft_ref, self.read_control_body()))
            elif method == "GET" and action in {None, "versions"}:
                self.send_json(200, self.store.draft_detail(account_id, draft_ref, self.base_url))
            elif method == "DELETE" and action is None:
                self.store.change_draft(account_id, draft_ref, "delete", None)
                self.audit_event("draft.change", identity, "draft", draft_ref, detail={"action": "delete"})
                self.send_json(200, {"ok": True})
            elif method == "POST" and action in {"disable", "enable"}:
                payload = self.read_control_body()
                self.store.change_draft(account_id, draft_ref, action, payload.get("reason"))
                self.audit_event("draft.change", identity, "draft", draft_ref, detail={"action": action})
                self.send_json(200, {"ok": True})
            elif method == "POST" and action == "favorite":
                payload = self.read_control_body()
                favorite = payload.get("favorite")
                if type(favorite) is not bool:
                    self.send_json(400, {"ok": False, "error": "favorite must be true or false."})
                    return
                draft_id = self.store.set_favorite(account_id, draft_ref, favorite)
                self.send_json(200, {"ok": True, "draftId": draft_id, "favorite": favorite})
            else:
                self.send_json(404, {"ok": False, "error": "Not found."})
        else:
            self.send_json(404, {"ok": False, "error": "Not found."})

    @staticmethod
    def _form_tag_edit(payload: dict) -> tuple[list | None, list | None, list | None]:
        """Read the tags, add_tag, and remove_tag fields of a dashboard tag action."""
        tags = add = remove = None
        if "tags" in payload:
            tags = payload["tags"]
            if isinstance(tags, str):
                tags = [part for part in re.split(r"[\s,]+", tags) if part]
            if not isinstance(tags, list) or not all(isinstance(item, str) for item in tags):
                raise ValueError("tags must be a list of text.")
        for key in ("add_tag", "remove_tag"):
            if payload.get(key) is not None and not isinstance(payload[key], str):
                raise ValueError(f"{key} must be text.")
        add = [payload["add_tag"]] if payload.get("add_tag") else None
        remove = [payload["remove_tag"]] if payload.get("remove_tag") else None
        if tags is None and add is None and remove is None:
            raise ValueError("Send tags, add_tag, or remove_tag.")
        return tags, add, remove

    def _apply_draft_edit(self, identity: dict, ref: str, *, tags: list | None = None, add: list | None = None,
                          remove: list | None = None, archived: bool | None = None,
                          favorite: bool | None = None) -> dict:
        """Run one atomic draft edit, then write its audit events. Return the public draft.

        The audit log keeps the action and tags. It never keeps draft content.
        """
        draft = self.store.patch_draft(identity["accountId"], ref, self.base_url, tags=tags, add_tags=add,
                                       remove_tags=remove, archived=archived, favorite=favorite)
        if archived is not None:
            self.audit_event("draft." + ("archive" if archived else "unarchive"), identity, "draft",
                             draft["draftId"])
        if tags is not None or add or remove:
            self.audit_event("draft.tag", identity, "draft", draft["draftId"], detail={"tags": draft["tags"]})
        return draft

    def draft_edit(self, identity: dict, ref: str, payload: dict, *, actions: tuple[str, ...]) -> dict:
        """Apply a dashboard row action to one owned draft. Return the public draft."""
        edit: dict = {}
        for action in actions:
            if action in {"favorite", "unfavorite"}:
                edit["favorite"] = action == "favorite"
            elif action in {"archive", "unarchive"}:
                edit["archived"] = action == "archive"
            elif action == "tags":
                edit["tags"], edit["add"], edit["remove"] = self._form_tag_edit(payload)
        return self._apply_draft_edit(identity, ref, **edit)

    def patch_draft(self, identity: dict, ref: str, payload: dict) -> dict:
        """Handle PATCH /api/drafts/<ref> with tags, addTags, removeTags, archived, and favorite."""
        if not payload.keys() & {"tags", "addTags", "removeTags", "archived", "favorite"}:
            raise ValueError("Send tags, addTags, removeTags, archived, or favorite.")
        for key in ("archived", "favorite"):
            if key in payload and type(payload[key]) is not bool:
                raise ValueError(f"{key} must be true or false.")
        for key in ("tags", "addTags", "removeTags"):
            value = payload.get(key)
            if key in payload and not (isinstance(value, list) and all(isinstance(item, str) for item in value)):
                raise ValueError(f"{key} must be a list of text.")
        draft = self._apply_draft_edit(
            identity, ref, tags=payload.get("tags"), add=payload.get("addTags"),
            remove=payload.get("removeTags"), archived=payload.get("archived"), favorite=payload.get("favorite"))
        return {"ok": True, "draft": draft}

    def device_api(self, path: str) -> None:
        """Start or poll a CLI device login. These calls need no credentials."""
        start = path.endswith("/start")
        if not self.rate_allowed(("device-start:" if start else "device-poll:") + self.source_ip(),
                                 10 if start else 60):
            return
        payload = self.read_control_body()
        if start:
            started = self.store.start_device_login(payload.get("clientName"))
            uri = self.base_url.rstrip("/") + "/cli/activate"
            self.send_json(200, {"ok": True, **started, "verificationUri": uri,
                                 "verificationUriComplete": f"{uri}?code={started['userCode']}",
                                 "interval": DEVICE_POLL_INTERVAL})
            return
        result = self.store.poll_device_login(payload.get("deviceCode"))
        status = result["status"]
        if status == "approved":
            account = self.store.account(result["accountId"]) or {"accountId": result["accountId"]}
            self.audit_event("key.create", self.with_roles(account), "api_key", result["keyId"],
                             detail={"method": "device"})
            self.send_json(200, {"ok": True, "status": "approved", "apiKey": result["apiKey"],
                                 "keyId": result["keyId"], "expiresAt": result["expiresAt"]})
        elif status == "pending":
            self.send_json(200, {"ok": False, "status": "pending", "interval": DEVICE_POLL_INTERVAL})
        elif status == "denied":
            self.send_json(403, {"ok": False, "status": "denied", "error": "The device login was denied."})
        else:
            self.send_json(410, {"ok": False, "status": "expired", "error": "The device code expired."})

    def sharing_target(self, identity: dict, ref: str) -> tuple[dict, str]:
        """Return a draft and the caller role. Sharing changes need editor or higher."""
        draft = self.store.resolve_draft(ref)
        role = draft_role(identity, draft, self.store.list_grants(draft["draftId"]), self.auth) if draft else None
        if role is None:
            raise KeyError("Draft not found.")
        if ROLE_RANK[role] < ROLE_RANK["editor"]:
            raise AccessDenied("Editor role required.")
        return draft, role

    def sharing_payload(self, draft: dict) -> dict:
        public_url = f"{self.base_url.rstrip('/')}/d/{draft.get('slug') or draft['draftId']}"
        return {
            "ok": True, "draftId": draft["draftId"], "workspace": draft_workspace(draft, self.auth),
            "visibility": effective_visibility(draft, self.auth), "maxVisibility": self.auth.max_visibility,
            "allowedVisibilities": self.auth.allowed_visibilities(), "viewerAuthMode": self.auth.viewer_mode,
            "shareUrl": public_url + "/share", "publicUrl": public_url,
            "grants": self.store.list_grants(draft["draftId"]),
        }

    def change_sharing(self, identity: dict, ref: str, action: str, payload: dict) -> dict:
        draft, role = self.sharing_target(identity, ref)
        draft_id = draft["draftId"]
        if action == "update":
            visibility = payload.get("visibility") or None
            workspace = payload.get("workspace") or None
            if visibility is None and workspace is None:
                raise ValueError("Send visibility or workspace.")
            if visibility is not None:
                if visibility not in VISIBILITY_RANK:
                    raise ValueError("visibility is invalid.")
                if VISIBILITY_RANK[visibility] > VISIBILITY_RANK[self.auth.max_visibility]:
                    raise PolicyLimit(f"visibility exceeds the server maximum ({self.auth.max_visibility}).")
            if workspace is not None:
                if not isinstance(workspace, str) or WORKSPACE_RE.fullmatch(workspace) is None:
                    raise ValueError("workspace is invalid.")
                if workspace == draft_workspace(draft, self.auth):
                    workspace = None
                elif role not in {"owner", "admin"} or not workspace_allowed(identity, workspace):
                    raise AccessDenied("You cannot move this draft to that workspace.")
            draft = self.store.set_sharing(draft_id, workspace=workspace, visibility=visibility)
            self.audit_event("sharing.update", identity, "draft", draft_id,
                             detail={"visibility": visibility, "workspace": workspace})
        elif action == "grant":
            principal_type = payload.get("type")
            value = normalize_grant(principal_type, payload.get("value"))
            grant_role = payload.get("role") or "viewer"
            if grant_role not in GRANT_ROLES:
                raise ValueError("role must be viewer or editor.")
            self.store.add_grant(draft_id, principal_type, value, grant_role, identity["accountId"])
            self.audit_event("sharing.grant", identity, "draft", draft_id,
                             detail={"type": principal_type, "value": value, "role": grant_role})
        else:
            principal_type = payload.get("type")
            value = revoke_value(principal_type, payload.get("value"))
            if not self.store.remove_grant(draft_id, principal_type, value):
                raise KeyError("Grant not found.")
            self.audit_event("sharing.revoke", identity, "draft", draft_id,
                             detail={"type": principal_type, "value": value})
        return self.sharing_payload(draft)

    def annotate_drafts(self, drafts: list[dict]) -> list[dict]:
        for draft in drafts:
            draft["workspace"] = draft_workspace(draft, self.auth)
            draft["visibility"] = effective_visibility(draft, self.auth)
        return drafts

    def shared_drafts(self, session: dict) -> list[dict]:
        """Return drafts that other accounts shared with this session."""
        pairs: list[tuple[str, str]] = []
        if session.get("emailVerified") and session.get("email"):
            pairs.append(("email", session["email"].lower()))
        if session.get("tailscaleLogin"):
            pairs.append(("tailscale_login", session["tailscaleLogin"].lower()))
        pairs += [("group", _normalize_principal_value(group)) for group in session.get("groups") or []]
        pairs += [("workspace", name) for name in session.get("workspaces") or {}]
        grants = self.store.grants_matching(pairs[:500])
        shared = []
        for draft, count in self.store.foreign_live_drafts(session["accountId"]):
            if shared_role(session, draft, grants.get(draft["draftId"], []), self.auth):
                public = self.store.public_draft(draft, self.base_url, version_count=count)
                # The owner's tags, archive state, and star are private to the owner.
                public.update(tags=[], archived=False, archivedAt=None, favorite=False, favoritedAt=None)
                shared.append(public)
        shared.sort(key=lambda item: item["updatedAt"] or "", reverse=True)
        return self.annotate_drafts(shared)

    def management_web(self, method: str, path: str) -> None:
        known = path in {"/dashboard", "/dashboard/bulk", "/dashboard/theme", "/cli/auth", "/cli/auth/keys",
                         "/cli/activate", "/auth/sign-in", "/auth/sign-out"}
        draft_match = re.fullmatch(
            r"/dashboard/drafts/([a-z0-9]{6,32})"
            r"(?:/(disable|enable|delete|favorite|unfavorite|archive|unarchive|tags"
            r"|sharing|sharing/grants|sharing/revoke))?",
            path,
        )
        key_match = re.fullmatch(r"/cli/auth/keys/([a-z0-9]{6,32})/revoke", path)
        if not known and not draft_match and not key_match:
            self.send_not_found()
            return
        if method == "GET" and path == "/auth/sign-in":
            query = parse_qs(urlparse(self.path).query)
            self.sign_in_page(safe_next_path((query.get("next") or [""])[0]))
            return
        if method == "POST" and path == "/auth/sign-in":
            if not self.rate_allowed("login:" + self.source_ip(), 20):
                return
            payload = self.read_control_body()
            state = read_signed(self.cookie_value("tailplan_login"), self.session_secret)
            if not self.valid_form(payload, state):
                self.send_json(403, {"ok": False, "error": "Sign-in expired. Reload and retry."})
                return
            if payload.get("method") == "tailscale" and (login := self.tailscale_login()):
                if not self.login_allowed(login[0]) or "tailscale" not in self.auth.modes:
                    self.send_json(403, {"ok": False, "error": "Forbidden"})
                    return
                identity = self.tailscale_identity(*login)
                if identity is None:
                    self.send_json(403, {"ok": False, "error": "Forbidden"})
                    return
                method_name = "tailscale"
            else:
                if "api_key" not in self.auth.modes:
                    self.send_json(403, {"ok": False, "error": "API key sign-in is off."})
                    return
                token = payload.get("token")
                identity = self.store.authenticate(token, self.token) if isinstance(token, str) else None
                method_name = "bootstrap" if identity and identity["apiKeyId"] == "bootstrap" else "api_key"
            if identity is None:
                self.audit_event("auth.denied", None, outcome="denied", detail={"method": "api_key"})
                self.unauthorized()
                return
            self.start_session(identity, method_name, payload.get("next"))
            return
        session = self.session()
        if session is None:
            if method == "GET":
                self.sign_in_page(self.relative_target())
            else:
                self.unauthorized()
            return
        account_id = session["accountId"]
        if method == "POST":
            payload = self.read_control_body()
            if "csrf" not in payload and self.headers.get("X-CSRF-Token"):
                payload["csrf"] = self.headers["X-CSRF-Token"]
            if not self.valid_form(payload, session):
                self.send_json(403, {"ok": False, "error": "Invalid form token. Reload and retry."})
                return
            if path == "/dashboard/bulk":
                ids = payload.get("draftIds", payload.get("ids"))
                if not isinstance(ids, list):
                    raise ValueError("draftIds must be a list.")
                action, tag = payload.get("action"), payload.get("tag")
                results = self.store.bulk_update(account_id, ids, action=action, tag=tag)
                detail: dict = {"action": action, "count": len(results)}
                if action in {"add_tag", "remove_tag"}:
                    detail["tag"] = normalize_tags([tag])[0]  # Valid, or bulk_update raised.
                self.audit_event("draft.bulk", session, detail=detail)
                self.send_json(200, {"ok": True, "count": len(results), "drafts": results})
            elif path == "/dashboard/theme":
                theme = payload.get("theme")
                if theme not in DASHBOARD_THEMES:
                    raise ValueError("theme is invalid.")
                base_path = (urlparse(self.base_url).path.rstrip("/") or "") + "/"
                cookie = f"tailplan_theme={theme}; Path={base_path}; Max-Age=31536000; SameSite=Lax"
                if urlparse(self.base_url).scheme == "https":
                    cookie += "; Secure"
                if self.wants_json():
                    self.send_json(200, {"ok": True, "theme": theme}, {"Set-Cookie": cookie})
                else:
                    self.redirect(safe_next_path(str(payload.get("next") or "").split("#")[0]), cookie)
            elif path == "/auth/sign-out":
                if session.get("sid"):
                    self.store.revoke_session(session["sid"])
                self.audit_event("auth.logout", session, "account", account_id, detail={"method": "sign-out"})
                self.redirect("/", [self.cookie_header("tailplan_session", "", 0),
                                    self.view_cookie_header("", 0)])
            elif path == "/cli/activate":
                approve = payload.get("decision") == "approve"
                result = self.store.decide_device_login(str(payload.get("code", "")), account_id, approve)
                if result is not None:
                    self.audit_event("device.approve" if approve else "device.deny", session, "device_code",
                                     normalize_user_code(payload.get("code")))
                self.send_html(200 if result else 404, render_activate(
                    self.base_url, session, None, None, result=result["status"] if result else "missing"), web=True)
            elif path == "/cli/auth/keys":
                if self.rate_allowed("keys:" + account_id, 10, 3600):
                    result = self.store.create_key(account_id, payload.get("name"))
                    self.audit_event("key.create", session, "api_key", result["apiKey"]["id"])
                    self.send_html(200, render_key(self.base_url, session, result), web=True)
            elif key_match:
                self.store.revoke_key(account_id, key_match[1])
                self.audit_event("key.revoke", session, "api_key", key_match[1])
                self.redirect("/cli/auth")
            elif draft_match and draft_match[2] and draft_match[2].startswith("sharing"):
                action = {"sharing": "update", "sharing/grants": "grant", "sharing/revoke": "revoke"}[draft_match[2]]
                self.change_sharing(session, draft_match[1], action, payload)
                self.redirect(f"/dashboard/drafts/{draft_match[1]}")
            elif draft_match and draft_match[2]:
                if draft_match[2] in {"favorite", "unfavorite", "archive", "unarchive", "tags"}:
                    row = self.draft_edit(session, draft_match[1], payload, actions=(draft_match[2],))
                    draft_id = row["draftId"]
                    if self.wants_json():
                        self.send_json(200, {"ok": True, "draftId": draft_id, "favorite": row["favorite"],
                                             "archived": row["archived"], "tags": row["tags"]})
                    else:
                        target = safe_next_path(str(payload.get("next") or "").split("#")[0])
                        self.redirect(f"{target}#d-{draft_id}")
                else:
                    self.store.change_draft(account_id, draft_match[1], draft_match[2], payload.get("reason"))
                    self.audit_event("draft.change", session, "draft", draft_match[1],
                                     detail={"action": draft_match[2]})
                    target = "/dashboard" if draft_match[2] == "delete" else f"/dashboard/drafts/{draft_match[1]}"
                    self.redirect(target)
            else:
                self.send_not_found()
            return
        if method != "GET":
            self.send_not_found()
        elif path == "/dashboard":
            query = {key: values[-1] for key, values in parse_qs(urlparse(self.path).query).items()}
            shared = self.shared_drafts(session)
            state = query_dashboard(
                self.store, self.auth, account_id, self.base_url, view=query.get("view", "active"),
                q=query.get("q", ""), sort=query.get("sort", ""), direction=query.get("dir", ""),
                group=query.get("group", "time"), tag=query.get("tag", ""), repo=query.get("repo", ""),
                shared_drafts=shared)
            theme = self.cookie_value("tailplan_theme")
            state["theme"] = theme if theme in DASHBOARD_THEMES else "system"
            if query.get("format") == "json" or "application/json" in self.headers.get("Accept", ""):
                self.send_json(200, {"ok": True, **state})
            else:
                owned = [] if state["view"] == "shared" else state["drafts"]
                self.send_html(200, render_dashboard(self.base_url, session, owned,
                                                     shared=shared, state=state), web=True)
        elif path == "/cli/activate":
            code = (parse_qs(urlparse(self.path).query).get("code") or [""])[0]
            info = self.store.device_login(code) if code else None
            self.send_html(200, render_activate(self.base_url, session, normalize_user_code(code), info), web=True)
        elif path == "/cli/auth":
            self.send_html(200, render_keys(self.base_url, session,
                           self.store.list_keys(account_id)), web=True)
        elif draft_match and draft_match[2] is None:
            detail = self.store.draft_detail(account_id, draft_match[1], self.base_url)
            self.annotate_drafts([detail["draft"]])
            stored = self.store.resolve_draft(draft_match[1]) or {}
            sharing = self.sharing_payload({**stored, "draftId": detail["draft"]["draftId"]})
            self.send_html(200, render_detail(self.base_url, session, detail, sharing=sharing), web=True)
        else:
            self.send_not_found()



def page(title: str, body: str) -> str:
    return f"<!doctype html><html lang=\"en\"><head><meta charset=\"utf-8\"><meta name=\"viewport\" content=\"width=device-width, initial-scale=1\"><title>{html.escape(title)}</title><style>{CSS}</style></head><body>{body}</body></html>"


def home(base_url: str) -> str:
    base = html.escape(base_url.rstrip("/"))
    return page("Tailplan", f'<main class="home"><h1>Tailplan</h1>'
                '<p>Publish a draft. Keep its URL. Track every version.</p>'
                '<pre>tailplan upload ./plan.md</pre>'
                f'<p><a href="{base}/dashboard">My drafts</a> · '
                f'<a href="{base}/cli/auth">CLI setup</a></p>'
                f'<p>Health: <a href="{base}/healthz">/healthz</a></p></main>')


def not_found(sign_in_url: str | None = None) -> str:
    """Return the 404 page. Every 404 is the same, so it never shows that a draft exists."""
    sign_in = f'<p><a href="{html.escape(sign_in_url, quote=True)}">Sign in</a></p>' if sign_in_url else ""
    return page("Draft not found", "<main class=\"home\"><h1>Draft not found</h1>"
                "<p>The requested Tailplan draft is unavailable.</p>" + sign_in + "</main>")


def create_servers(
    primary_address: tuple[str, int],
    proxy_address: tuple[str, int] | None,
    *,
    store: Store,
    token: str,
    base_url: str,
    redirect_view_base_url: str,
    session_secret: str | None = None,
    auth: AuthConfig | None = None,
) -> tuple[TailplanHTTPServer, TailplanHTTPServer | None]:
    proxy = None
    if proxy_address is not None:
        try:
            proxy = TailplanHTTPServer(proxy_address, Handler)
        except OSError as exc:
            raise ListenerStartupError(
                f"failed to bind proxy listener at {proxy_address[0]}:{proxy_address[1]}: {exc}"
            ) from exc
    try:
        primary = TailplanHTTPServer(primary_address, Handler)
    except BaseException as exc:
        if proxy is not None:
            proxy.server_close()
        if isinstance(exc, OSError):
            raise ListenerStartupError(
                f"failed to bind primary listener at {primary_address[0]}:{primary_address[1]}: {exc}"
            ) from exc
        raise
    configured_base_url = base_url.rstrip("/")
    if not configured_base_url:
        host, port = primary.server_address[:2]
        configured_base_url = f"http://{host}:{port}"
    auth = auth or AuthConfig()
    primary.store = store
    primary.token = token
    primary.auth = auth
    primary.session_secret = session_secret
    primary.base_url = configured_base_url
    primary.redirect_view_base_url = redirect_view_base_url
    if proxy is not None:
        proxy.store = store
        proxy.token = token
        proxy.auth = auth
        proxy.session_secret = session_secret
        proxy.base_url = configured_base_url
        proxy.redirect_view_base_url = ""
        proxy.limiter = primary.limiter
        proxy.viewer_limiter = primary.viewer_limiter
    return primary, proxy


def run_servers(
    primary: TailplanHTTPServer,
    proxy: TailplanHTTPServer | None = None,
) -> None:
    proxy_thread = None
    proxy_thread_started = False
    try:
        if proxy is not None:
            proxy_thread = threading.Thread(
                target=proxy.serve_forever,
                name="tailplan-proxy",
            )
            proxy_thread.start()
            proxy_thread_started = True
        primary.serve_forever()
    finally:
        if proxy is not None:
            if (
                proxy_thread_started
                and proxy_thread is not None
                and proxy_thread.is_alive()
            ):
                proxy.shutdown()
            if proxy_thread_started and proxy_thread is not None:
                proxy_thread.join()
            proxy.server_close()
        primary.server_close()


def _tcp_port(value: str) -> int:
    try:
        port = int(value)
    except ValueError as exc:
        raise argparse.ArgumentTypeError("must be an integer TCP port") from exc
    if not 1 <= port <= 65535:
        raise argparse.ArgumentTypeError("must be between 1 and 65535")
    return port


def _redirect_base_url(value: str) -> str:
    if any(character.isspace() or ord(character) < 32 for character in value):
        raise argparse.ArgumentTypeError("must not contain whitespace or control characters")
    parsed = urlparse(value)
    try:
        _ = parsed.port
    except ValueError as exc:
        raise argparse.ArgumentTypeError("contains an invalid port") from exc
    if (
        parsed.scheme != "https"
        or not parsed.hostname
        or parsed.username is not None
        or parsed.password is not None
        or parsed.params
        or parsed.query
        or parsed.fragment
    ):
        raise argparse.ArgumentTypeError(
            "must be an absolute HTTPS URL without credentials, query, or fragment"
        )
    return value.rstrip("/")


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    ap = argparse.ArgumentParser()
    ap.add_argument("--host", default=os.getenv("TAILPLAN_HOST", "127.0.0.1"))
    ap.add_argument("--port", type=_tcp_port, default=os.getenv("TAILPLAN_PORT", "9127"))
    ap.add_argument("--proxy-host", default=os.getenv("TAILPLAN_PROXY_HOST"))
    ap.add_argument("--proxy-port", type=_tcp_port, default=os.getenv("TAILPLAN_PROXY_PORT"))
    ap.add_argument("--data-dir", default=os.getenv("TAILPLAN_DATA_DIR", str(Path.home() / ".tailplan")))
    ap.add_argument("--token-file", default=os.getenv("TAILPLAN_TOKEN_FILE", str(Path.home() / ".tailplan" / "token")))
    ap.add_argument("--session-secret-file", default=os.getenv("TAILPLAN_SESSION_SECRET_FILE") or None,
                    help="file with the secret that signs browser cookies; "
                         "default derives a key from the upload token")
    ap.add_argument("--base-url", default=os.getenv("TAILPLAN_BASE_URL", ""))
    ap.add_argument("--trust-tailscale-identity", action="store_true",
                    default=os.getenv("TAILPLAN_TRUST_TAILSCALE_IDENTITY") == "1")
    ap.add_argument("--tailscale-viewer", action="store_true",
                    default=os.getenv("TAILPLAN_TAILSCALE_VIEWER") == "1",
                    help="let the proxy listener show drafts to the Tailscale Serve login without "
                         "a sign-in; read-only, stateless, and needs --trust-tailscale-identity")
    ap.add_argument("--owner-login", default=os.getenv("TAILPLAN_OWNER_LOGIN", ""))
    ap.add_argument("--allowed-logins", type=parse_allowed_logins,
                    default=os.getenv("TAILPLAN_ALLOWED_LOGINS", ""),
                    help="comma-separated Tailscale logins allowed to sign in; empty allows any")
    ap.add_argument("--viewer-404-limit", type=int,
                    default=os.getenv("TAILPLAN_VIEWER_404_LIMIT", str(DEFAULT_VIEWER_404_LIMIT)),
                    help="missing-draft responses per key per 60 seconds before 429")
    ap.add_argument("--allow-anonymous-uploads", action="store_true",
                    default=os.getenv("TAILPLAN_ALLOW_ANONYMOUS_UPLOADS") == "1")
    ap.add_argument("--upload-ip-limit", type=int, default=os.getenv("TAILPLAN_UPLOAD_IP_LIMIT", "60"))
    ap.add_argument("--upload-key-limit", type=int, default=os.getenv("TAILPLAN_UPLOAD_KEY_LIMIT", "30"))
    ap.add_argument(
        "--redirect-view-base-url",
        type=_redirect_base_url,
        default=os.getenv("TAILPLAN_REDIRECT_VIEW_BASE_URL") or None,
    )
    ap.add_argument("--auth-modes", default=os.getenv("TAILPLAN_AUTH_MODES", ""),
                    help="comma-separated sign-in methods: api_key, tailscale, oidc")
    ap.add_argument("--oidc-issuer", default=os.getenv("TAILPLAN_OIDC_ISSUER", ""))
    ap.add_argument("--oidc-client-id", default=os.getenv("TAILPLAN_OIDC_CLIENT_ID", ""))
    ap.add_argument("--oidc-client-secret-file", default=os.getenv("TAILPLAN_OIDC_CLIENT_SECRET_FILE") or None)
    ap.add_argument("--oidc-scopes", default=os.getenv("TAILPLAN_OIDC_SCOPES", OIDC_DEFAULT_SCOPES))
    ap.add_argument("--oidc-groups-claim", default=os.getenv("TAILPLAN_OIDC_GROUPS_CLAIM", "groups"))
    ap.add_argument("--oidc-allow-private-issuer", action="store_true",
                    default=os.getenv("TAILPLAN_OIDC_ALLOW_PRIVATE_ISSUER") == "1")
    ap.add_argument("--oidc-link-emails", default=os.getenv("TAILPLAN_OIDC_LINK_EMAILS", ""),
                    help="comma-separated verified emails that sign in as the local owner account")
    ap.add_argument("--session-ttl-seconds", type=int,
                    default=os.getenv("TAILPLAN_SESSION_TTL_SECONDS", str(DEFAULT_SESSION_TTL)))
    ap.add_argument("--rbac-policy-file", default=os.getenv("TAILPLAN_RBAC_POLICY_FILE") or None)
    ap.add_argument("--viewer-auth-mode", choices=VIEWER_AUTH_MODES,
                    default=os.getenv("TAILPLAN_VIEWER_AUTH_MODE", "network"))
    ap.add_argument("--max-visibility", choices=VISIBILITIES,
                    default=os.getenv("TAILPLAN_MAX_VISIBILITY", "link"))
    args = ap.parse_args(argv)
    args.redirect_view_base_url = args.redirect_view_base_url or ""
    if not 300 <= args.session_ttl_seconds <= 90 * 86400:
        ap.error("session TTL must be between 300 seconds and 90 days")
    try:
        args.auth_modes = parse_auth_modes(args.auth_modes, oidc=bool(args.oidc_issuer))
    except ValueError as exc:
        ap.error(str(exc))
    if "oidc" in args.auth_modes and not args.oidc_issuer:
        ap.error("the oidc auth mode needs TAILPLAN_OIDC_ISSUER")
    if args.upload_ip_limit < 1 or args.upload_key_limit < 1:
        ap.error("upload limits must be positive")
    if args.viewer_404_limit < 1:
        ap.error("viewer 404 limit must be positive")
    if args.trust_tailscale_identity and args.proxy_host not in {"127.0.0.1", "::1"}:
        ap.error("Tailscale identity requires a loopback proxy listener")
    if (args.proxy_host is None) != (args.proxy_port is None):
        ap.error("--proxy-host and --proxy-port must be supplied together")
    if args.tailscale_viewer and not (args.trust_tailscale_identity and args.proxy_port is not None):
        ap.error("--tailscale-viewer (TAILPLAN_TAILSCALE_VIEWER) requires --trust-tailscale-identity "
                 "and --proxy-port: only the loopback proxy behind Tailscale Serve reads the header")
    if args.proxy_host == "":
        ap.error("--proxy-host must not be empty")
    if args.proxy_host is not None and (args.proxy_host, args.proxy_port) == (
        args.host,
        args.port,
    ):
        ap.error("proxy listener must differ from primary listener")
    return args


def read_oidc_client_secret(path: str | None) -> str:
    if path:
        try:
            value = Path(path).expanduser().read_text(encoding="utf-8").strip()
        except (OSError, UnicodeError) as exc:
            raise ValueError(f"cannot read the OIDC client secret file: {exc}") from exc
    else:
        value = (os.getenv("TAILPLAN_OIDC_CLIENT_SECRET") or "").strip()
    if not value:
        raise ValueError("OIDC needs TAILPLAN_OIDC_CLIENT_SECRET_FILE or TAILPLAN_OIDC_CLIENT_SECRET")
    return value


def build_auth_config(args: argparse.Namespace, session_secret: str | None) -> AuthConfig:
    """Build the shared sign-in settings. Import PyJWT only when an OIDC issuer is set."""
    def option(name: str, default: object = None) -> object:
        return getattr(args, name, default)

    issuer = option("oidc_issuer", "")
    policy = RBACPolicy.load(option("rbac_policy_file"))
    oidc = None
    if issuer:
        if not session_secret:
            raise ValueError("OIDC needs a dedicated session secret (TAILPLAN_SESSION_SECRET_FILE)")
        if not option("oidc_client_id"):
            raise ValueError("OIDC needs TAILPLAN_OIDC_CLIENT_ID")
        try:
            oidc = OIDCClient(
                issuer=issuer, client_id=option("oidc_client_id"),
                client_secret=read_oidc_client_secret(option("oidc_client_secret_file")),
                scopes=option("oidc_scopes", OIDC_DEFAULT_SCOPES),
                groups_claim=option("oidc_groups_claim", "groups"),
                allow_private=bool(option("oidc_allow_private_issuer", False)),
            )
        except ImportError as exc:
            raise ValueError("OIDC needs PyJWT. Install tailplan[oidc].") from exc
    links = frozenset(item.strip().lower() for item in str(option("oidc_link_emails", "")).split(",")
                      if item.strip())
    return AuthConfig(modes=option("auth_modes", None) or parse_auth_modes(None, oidc=bool(issuer)),
                      policy=policy, viewer_mode=option("viewer_auth_mode", "network"),
                      max_visibility=option("max_visibility", "link"),
                      session_ttl=option("session_ttl_seconds", DEFAULT_SESSION_TTL),
                      oidc=oidc, link_emails=links)


def main() -> int:
    args = parse_args()
    token_path = Path(args.token_file).expanduser()
    token = token_path.read_text().strip()
    if not token:
        raise SystemExit("empty token file")
    session_secret = load_session_secret(
        getattr(args, "session_secret_file", None),
        os.getenv("TAILPLAN_SESSION_SECRET"),
        token,
    )
    try:
        auth = build_auth_config(args, session_secret)
    except ValueError as exc:
        raise SystemExit(str(exc)) from exc
    store = Store(Path(args.data_dir).expanduser())
    proxy_address = (
        (args.proxy_host, args.proxy_port) if args.proxy_host is not None else None
    )
    try:
        primary, proxy = create_servers(
            (args.host, args.port),
            proxy_address,
            store=store,
            token=token,
            base_url=args.base_url,
            redirect_view_base_url=args.redirect_view_base_url,
            session_secret=session_secret,
            auth=auth,
        )
    except ListenerStartupError as exc:
        raise SystemExit(str(exc)) from exc
    for listener in (primary, proxy):
        if listener is not None:
            listener.allow_anonymous_uploads = args.allow_anonymous_uploads
            listener.upload_ip_limit = args.upload_ip_limit
            listener.upload_key_limit = args.upload_key_limit
            listener.viewer_404_limit = args.viewer_404_limit
    if proxy is not None:
        proxy.trust_tailscale_identity = args.trust_tailscale_identity
        # Only the loopback proxy behind Tailscale Serve reads the passive header.
        proxy.tailscale_viewer = args.tailscale_viewer and args.trust_tailscale_identity
        proxy.owner_login = args.owner_login
        proxy.allowed_logins = args.allowed_logins
    primary_host, primary_port = primary.server_address[:2]
    print(f"Tailplan listening on http://{primary_host}:{primary_port}", flush=True)
    if proxy is not None:
        proxy_host, proxy_port = proxy.server_address[:2]
        print(
            f"Tailplan proxy backend listening on http://{proxy_host}:{proxy_port}",
            flush=True,
        )
    try:
        run_servers(primary, proxy)
    except KeyboardInterrupt:
        pass
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
