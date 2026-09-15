#!/usr/bin/env python3
"""Tailplan: tailnet-only static HTML draft publisher."""
from __future__ import annotations

import argparse
import base64
import hashlib
import hmac
import html
import ipaddress
import json
import os
import re
import secrets
import stat
import struct
import sys
import tempfile
import textwrap
import threading
import time
import unicodedata
import zlib
from collections import OrderedDict
from datetime import UTC, datetime
from functools import lru_cache
from html.parser import HTMLParser
from http import HTTPStatus
from http.cookies import CookieError, SimpleCookie
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, quote, unquote, urlparse

TITLE_RE = re.compile(
    r"<\s*title\b[^>]*>(.*?)<\s*/\s*title\s*>", re.IGNORECASE | re.DOTALL
)
MAX_HTML_BYTES = 512 * 1024
MAX_REQUEST_BYTES = MAX_HTML_BYTES * 6 + 64 * 1024
MAX_VERSION_NUMBER = 999_999_999
DEFAULT_MAX_HANDLERS = 32
DEFAULT_READ_TIMEOUT = 10.0
DRAFT_ID_RE = re.compile(r"[a-z0-9]{6,32}")
SHA256_RE = re.compile(r"[a-f0-9]{64}")
IDEMPOTENCY_KEY_RE = re.compile(r"[A-Za-z0-9._~-]{1,128}")
VIEWER_ROUTE_RE = re.compile(
    r"/d/([a-z0-9]{6,32})(?:/v/([1-9][0-9]{0,8}))?(?:/(?:content|raw))?/?"
)
SAFE_QUERY_RE = re.compile(r"[A-Za-z0-9._~!$&'()*+,;=:@/?%\[\]-]*")
MAX_IDEMPOTENCY_RECEIPTS = 4096

BLOCKED_TAGS = {"applet", "base", "embed", "form", "frame", "iframe", "link", "object"}
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
SECURITY_SENSITIVE_ATTRIBUTES = URL_ATTRIBUTES | {"http-equiv", "srcdoc", "style", "type"}
BLOCKED_URL_SCHEMES = {"file", "javascript", "vbscript"}
CSS_URL_RE = re.compile(
    r"url\s*\(\s*(['\"]?)(.*?)\1\s*\)", re.IGNORECASE | re.DOTALL
)
CSS_IMPORT_RE = re.compile(
    r"@import\s+(['\"])(.*?)\1", re.IGNORECASE | re.DOTALL
)
STORE_LOCK = threading.RLock()
BUILD_ID = hashlib.sha256(Path(__file__).read_bytes()).hexdigest()[:12]

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


def new_id() -> str:
    return secrets.token_urlsafe(9).replace("-", "").replace("_", "")[:12].lower()


def title_from_html(doc: str, filename: str | None = None) -> str:
    m = TITLE_RE.search(doc)
    if m:
        text = re.sub(r"\s+", " ", html.unescape(re.sub(r"<[^>]*>", "", m.group(1)))).strip()
        if text:
            return text[:140]
    return (filename or "Tailplan Draft")[:140]


PREVIEW_ROUTE_RE = re.compile(
    r"/d/([a-z0-9]{6,32})(?:/v/([1-9][0-9]{0,8}))?/(share|preview\.png)/?"
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


def _css_has_unsafe_url(value: str) -> bool:
    normalized = _decode_css_escapes(value)
    urls = [match.group(2) for match in CSS_URL_RE.finditer(normalized)]
    urls.extend(match.group(2) for match in CSS_IMPORT_RE.finditer(normalized))
    return bool(re.search(r"expression\s*\(|behavior\s*:", normalized, re.IGNORECASE)) or any(
        _has_unsafe_scheme(url) for url in urls
    )


class _HtmlValidator(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.errors: list[str] = []
        self._style_depth = 0
        self.has_inline_script = False
        self.external_image_hosts: set[str] = set()

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        self._inspect_tag(tag, attrs)

    def handle_startendtag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        self._inspect_tag(tag, attrs)

    def handle_endtag(self, tag: str) -> None:
        if tag.lower() == "style" and self._style_depth:
            self._style_depth -= 1

    def handle_data(self, data: str) -> None:
        if self._style_depth and _css_has_unsafe_url(data):
            self.errors.append("Blocked unsafe CSS URL found.")

    def _inspect_tag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        tag = tag.lower()
        attr_values: dict[str, list[str]] = {}
        for name, value in attrs:
            attr_values.setdefault(name.lower(), []).append(value or "")
        if tag == "style":
            self._style_depth += 1
        if tag in BLOCKED_TAGS:
            self.errors.append("Blocked active/embedding tag found.")
        if tag == "script":
            self.has_inline_script = True
            if "src" in attr_values:
                self.errors.append("External script sources are not allowed.")
            if any(value.strip().lower() not in {"", "text/javascript", "application/javascript"}
                   for value in attr_values.get("type", [])):
                self.errors.append("Only inline classic JavaScript is accepted.")
        if tag == "img":
            for value in attr_values.get("src", []):
                try:
                    parsed = urlparse("https:" + value if value.startswith("//") else value)
                    if parsed.scheme in {"http", "https"} and parsed.hostname:
                        self.external_image_hosts.add(parsed.hostname.lower())
                except ValueError:
                    pass
        if any(name.startswith("on") or name == "srcdoc" for name in attr_values):
            self.errors.append("Blocked inline event handler or srcdoc attribute found.")
        if any(
            len(values) > 1 and name in SECURITY_SENSITIVE_ATTRIBUTES
            for name, values in attr_values.items()
        ):
            self.errors.append("Blocked duplicate security-sensitive attribute found.")
        if tag == "meta" and any(
            value.strip().lower() == "refresh"
            for value in attr_values.get("http-equiv", [])
        ):
            self.errors.append("Blocked meta refresh tag found.")
        if any(_css_has_unsafe_url(value) for value in attr_values.get("style", [])):
            self.errors.append("Blocked unsafe CSS URL found.")
        for name, values in attr_values.items():
            if name not in URL_ATTRIBUTES:
                continue
            if any(_has_unsafe_scheme(value) for value in values):
                self.errors.append("Blocked unsafe URL protocol found.")


def validate_html(doc: object) -> tuple[bool, list[str], list[str]]:
    errors: list[str] = []
    warnings: list[str] = []
    if not isinstance(doc, str) or not doc.strip():
        return False, ["HTML document is empty."], []
    try:
        encoded = doc.encode("utf-8")
    except UnicodeEncodeError:
        return False, ["HTML document contains invalid Unicode scalar values."], []
    size = len(encoded)
    if size > MAX_HTML_BYTES:
        errors.append(f"HTML document is {size} bytes; maximum is {MAX_HTML_BYTES} bytes.")
    validator = _HtmlValidator()
    validator.feed(doc)
    validator.close()
    errors.extend(validator.errors)
    if not TITLE_RE.search(doc):
        warnings.append("No <title> found; Tailplan will use a generic title.")
    return not errors, sorted(set(errors)), sorted(set(warnings))


class StorageError(RuntimeError):
    """Metadata or draft storage is unavailable or corrupt."""


class IdempotencyConflict(RuntimeError):
    """A request key was reused with a different upload payload."""


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
                or SHA256_RE.fullmatch(key["keyHash"]) is None):
            raise StorageError("Account storage has an invalid API key.")
    for draft in data["drafts"].values():
        if not isinstance(draft.get("accountId", "local"), str):
            raise StorageError("Draft account is invalid.")
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
        latest = versions.get(str(draft["latestVersionNumber"]))
        if latest and latest["fileSha256"] != draft["fileSha256"]:
            raise StorageError("Current draft and history checksums differ.")


def _valid_public_url(value: object, draft_id: str) -> bool:
    if not isinstance(value, str):
        return False
    parsed = urlparse(value)
    return (
        parsed.scheme in {"http", "https"}
        and bool(parsed.netloc)
        and parsed.path.endswith(f"/d/{draft_id}")
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
            or not _valid_public_url(draft["publicUrl"], draft_id)
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
            or not _valid_public_url(result["publicUrl"], result_draft_id)
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


class Store:
    def __init__(self, root: Path):
        self.root = Path(os.path.abspath(root))
        self.drafts = self.root / "drafts"
        self.meta = self.root / "metadata.json"
        self.backup = self.root / "metadata.json.bak"
        self.drafts.mkdir(parents=True, exist_ok=True)
        self.root.mkdir(parents=True, exist_ok=True)
        os.chmod(self.root, 0o700)
        os.chmod(self.drafts, 0o700)
        with STORE_LOCK:
            self._rebase_metadata_paths_locked()

    def _rebase_metadata_paths_locked(self) -> None:
        if not self.meta.exists():
            return
        try:
            original_text = self.meta.read_text(encoding="utf-8")
            data = json.loads(original_text)
        except (OSError, UnicodeError, json.JSONDecodeError) as exc:
            raise StorageError("Metadata is unreadable or malformed.") from exc
        _validate_metadata_schema(
            data,
            self.root,
            allow_legacy_current_objects=True,
        )
        changed: list[tuple[dict, Path]] = []
        for draft_id, draft in data["drafts"].items():
            canonical = (
                self.drafts
                / draft_id
                / f"v{draft['latestVersionNumber']}.html"
            )
            self._verify_rebase_object(canonical, draft)
            if draft["currentObject"] != str(canonical):
                changed.append((draft, canonical))
        if not changed:
            return
        for draft, canonical in changed:
            draft["currentObject"] = str(canonical)
        _validate_metadata_schema(data, self.root)
        replacement = json.dumps(data, indent=2) + "\n"
        _atomic_write_text(self.backup, original_text)
        _atomic_write_text(self.meta, replacement)

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
            if not self.meta.exists():
                return {"drafts": {}}
            try:
                data = json.loads(self.meta.read_text(encoding="utf-8"))
            except (OSError, UnicodeError, json.JSONDecodeError) as exc:
                raise StorageError("Metadata is unreadable or malformed.") from exc
            return _validate_metadata_schema(data, self.root)

    def save_meta(self, data: dict) -> None:
        with STORE_LOCK:
            _validate_metadata_schema(data, self.root)
            if self.meta.exists():
                previous = self.load_meta()
                _atomic_write_text(
                    self.backup, json.dumps(previous, indent=2) + "\n"
                )
            _atomic_write_text(self.meta, json.dumps(data, indent=2) + "\n")

    def _read_current_object(self, draft: dict) -> str:
        path = Path(draft["currentObject"])
        try:
            if path.is_symlink():
                raise StorageError("Recorded draft object path is invalid.")
            resolved = path.resolve(strict=True)
            if not resolved.is_relative_to(self.drafts.resolve(strict=True)) or not resolved.is_file():
                raise StorageError("Recorded draft object path is invalid.")
            doc = resolved.read_bytes().decode("utf-8")
        except (OSError, UnicodeError, RuntimeError) as exc:
            raise StorageError("Recorded draft object is unavailable or unreadable.") from exc
        if sha256_text(doc) != draft["fileSha256"]:
            raise StorageError("Recorded draft object checksum does not match metadata.")
        return doc

    def upsert(
        self,
        html_doc: str,
        filename: str | None,
        draft_id: str | None,
        base_url: str,
        request_key: str | None = None,
        *,
        account_id: str = "local",
        description: str | None = None,
        metadata: dict | None = None,
        audit: dict | None = None,
    ) -> dict:
        with STORE_LOCK:
            return self._upsert_locked(
                html_doc, filename, draft_id, base_url, request_key,
                account_id, description, metadata or {}, audit or {},
            )

    def _upsert_locked(
        self,
        html_doc: str,
        filename: str | None,
        draft_id: str | None,
        base_url: str,
        request_key: str | None,
        account_id: str,
        description: str | None,
        metadata: dict,
        audit: dict,
    ) -> dict:
        data = self.load_meta()
        data.setdefault("drafts", {})
        data.setdefault("idempotency", {})
        if draft_id:
            self._owned_draft(data, draft_id, account_id)
        if request_key and account_id != "local":
            request_key = sha256_text(f"{account_id}:{request_key}")
        fingerprint = sha256_text(
            json.dumps(
                {"html": html_doc, "filename": filename, "draftId": draft_id,
                 "accountId": account_id, "description": description, "metadata": metadata},
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
            )
        )
        if request_key and request_key in data["idempotency"]:
            receipt = data["idempotency"][request_key]
            self._owned_draft(data, receipt["result"]["draftId"], account_id)
            legacy_match = False
            if (account_id == "local" and "versionId" not in receipt["result"]
                    and description is None and all(value is None for value in metadata.values())):
                legacy_match = receipt.get("fingerprint") == sha256_text(json.dumps(
                    {"html": html_doc, "filename": filename, "draftId": draft_id},
                    ensure_ascii=False, sort_keys=True, separators=(",", ":"),
                ))
            if receipt.get("fingerprint") != fingerprint and not legacy_match:
                raise IdempotencyConflict("Request key was reused with a different payload.")
            return {**receipt["result"], "created": False, "replayed": True}
        creating = not draft_id
        if draft_id and draft_id not in data["drafts"]:
            raise KeyError("Draft not found.")
        if not draft_id:
            draft_id = new_id()
            while draft_id in data["drafts"]:
                draft_id = new_id()
        draft = data["drafts"].get(draft_id, {})
        latest_version = int(draft.get("latestVersionNumber") or 0)
        if latest_version >= MAX_VERSION_NUMBER:
            raise StorageError("Draft has reached the maximum version number.")
        version = latest_version + 1
        title = title_from_html(html_doc, filename)
        versions = self._version_records(draft) if draft else {}
        stamp = now_iso()
        version_id = new_id()
        signals = _HtmlValidator()
        signals.feed(html_doc)
        signals.close()
        versions[str(version)] = {
            **metadata, **audit,
            "versionId": version_id, "versionNumber": version, "createdAt": stamp,
            "fileSize": len(html_doc.encode("utf-8")), "fileSha256": sha256_text(html_doc),
            "filename": filename, "hasInlineScript": signals.has_inline_script,
            "externalImageHosts": sorted(signals.external_image_hosts),
            "eventType": "draft.created" if creating else "draft.updated",
        }
        object_path = self.drafts / draft_id / f"v{version}.html"
        object_path.parent.mkdir(parents=True, exist_ok=True)
        _atomic_write_text(object_path, html_doc)
        data["drafts"][draft_id] = {
            **draft,
            "draftId": draft_id,
            "title": title,
            "filename": filename,
            "latestVersionNumber": version,
            "currentObject": str(object_path),
            "fileSha256": sha256_text(html_doc),
            "createdAt": draft.get("createdAt") or stamp,
            "updatedAt": stamp,
            "publicUrl": f"{base_url.rstrip('/')}/d/{draft_id}",
            "accountId": account_id,
            "description": clean_text(description, 1000) or draft.get("description"),
            "versions": versions,
            **{field: metadata.get(field) or draft.get(field)
               for field in ("repoHost", "repoOrg", "repoName")},
        }
        result = {
            "draftId": draft_id,
            "title": title,
            "versionNumber": version,
            "publicUrl": data["drafts"][draft_id]["publicUrl"],
            "rawUrl": data["drafts"][draft_id]["publicUrl"] + "/raw",
            "versionId": version_id,
            "fileSha256": sha256_text(html_doc),
            "requestId": audit.get("requestId"),
        }
        if request_key:
            data["idempotency"][request_key] = {"fingerprint": fingerprint, "result": result}
            while len(data["idempotency"]) > MAX_IDEMPOTENCY_RECEIPTS:
                del data["idempotency"][next(iter(data["idempotency"]))]
        self.save_meta(data)
        return {**result, "created": creating, "replayed": False}

    def get(self, draft_id: str, version: int | None = None) -> tuple[dict | None, str | None]:
        with STORE_LOCK:
            data = self.load_meta()
            draft = data.get("drafts", {}).get(draft_id)
            if not draft or draft.get("deletedAt") or draft.get("disabledAt"):
                return None, None
            if version is None or version == draft["latestVersionNumber"]:
                return draft, self._read_current_object(draft)
            path = Path(draft["currentObject"])
            if version is not None:
                path = self.drafts / draft_id / f"v{version}.html"
            if not path.exists():
                return None, None
            record = draft.get("versions", {}).get(str(version))
            if record:
                return draft, self._read_current_object({
                    "currentObject": str(path), "fileSha256": record["fileSha256"],
                })
            return draft, self._read_legacy_version(path)

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

    @staticmethod
    def _owned_draft(data: dict, draft_id: str, account_id: str) -> dict:
        draft = data["drafts"].get(draft_id)
        if (not draft or draft.get("deletedAt")
                or draft.get("accountId", "local") != account_id):
            raise KeyError("Draft not found.")
        return draft

    def list_drafts(self, account_id: str, base_url: str) -> list[dict]:
        with STORE_LOCK:
            data = self.load_meta()
            drafts = []
            for draft_id, draft in data["drafts"].items():
                if (draft.get("accountId", "local") != account_id or draft.get("deletedAt")):
                    continue
                public_url = f"{base_url.rstrip('/')}/d/{draft_id}"
                drafts.append({
                    **{key: draft.get(key) for key in (
                        "draftId", "title", "description", "repoHost", "repoOrg", "repoName",
                        "latestVersionNumber", "createdAt", "updatedAt", "disabledReason",
                    )},
                    "versionCount": len(self._version_records(draft)),
                    "latestVersionAt": draft["updatedAt"], "disabled": bool(draft.get("disabledAt")),
                    "publicUrl": public_url, "rawUrl": public_url + "/raw",
                    "shareUrl": public_url + "/share",
                })
            return sorted(drafts, key=lambda draft: draft["updatedAt"], reverse=True)

    def draft_detail(self, account_id: str, draft_id: str, base_url: str) -> dict:
        with STORE_LOCK:
            draft = self._owned_draft(self.load_meta(), draft_id, account_id)
            public = next(d for d in self.list_drafts(account_id, base_url) if d["draftId"] == draft_id)
            versions = sorted(self._version_records(draft).values(),
                              key=lambda version: version["versionNumber"], reverse=True)
            return {"ok": True, "draft": public, "versions": versions}

    def change_draft(self, account_id: str, draft_id: str, action: str, reason: str | None) -> None:
        with STORE_LOCK:
            data = self.load_meta()
            draft = self._owned_draft(data, draft_id, account_id)
            stamp = now_iso()
            if action == "delete":
                draft["deletedAt"] = stamp
            elif action == "disable":
                draft.update(disabledAt=stamp, disabledReason=clean_text(reason, 1000) or "Disabled by owner.")
            elif action == "enable":
                draft.update(disabledAt=None, disabledReason=None)
            else:
                raise ValueError("Unknown draft action.")
            draft["updatedAt"] = stamp
            self.save_meta(data)

    def account(self, account_id: str) -> dict | None:
        if account_id == "local":
            return {"accountId": "local", "accountName": "Tailplan local"}
        if account_id == "anonymous":
            return {"accountId": "anonymous", "accountName": "Anonymous"}
        value = self.load_meta().get("accounts", {}).get(account_id)
        return {"accountId": account_id, "accountName": value["name"]} if value else None

    def identity_account(self, login: str, name: str, owner_login: str) -> dict:
        with STORE_LOCK:
            if owner_login and login.casefold() == owner_login.casefold():
                return self.account("local")
            account_id = sha256_text("tailscale:" + login.casefold())[:32]
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
                last = key.get("lastUsedAt")
                stamp = now_iso()
                if not last or last[:16] != stamp[:16]:
                    key["lastUsedAt"] = stamp
                    self.save_meta(data)
                return {**self.account(key["accountId"]), "apiKeyId": key_id, "apiKeyName": key["name"]}
        return None

    def create_key(self, account_id: str, name: str | None) -> dict:
        with STORE_LOCK:
            data = self.load_meta()
            if self.account(account_id) is None or account_id == "anonymous":
                raise KeyError("Account not found.")
            token = "tp_" + secrets.token_urlsafe(32)
            key_id = new_id()
            key = {"accountId": account_id, "name": clean_text(name) or "CLI API key",
                   "keyHash": sha256_text(token), "createdAt": now_iso(), "lastUsedAt": None}
            data.setdefault("apiKeys", {})[key_id] = key
            self.save_meta(data)
            return {"ok": True, "apiKey": {"id": key_id, "name": key["name"]}, "token": token}

    def list_keys(self, account_id: str) -> list[dict]:
        with STORE_LOCK:
            return sorted([
                {"id": key_id, **{field: key.get(field) for field in ("name", "createdAt", "lastUsedAt")}}
                for key_id, key in self.load_meta().get("apiKeys", {}).items()
                if key["accountId"] == account_id and not key.get("revokedAt")
            ], key=lambda key: key["createdAt"], reverse=True)

    def revoke_key(self, account_id: str, key_id: str) -> None:
        with STORE_LOCK:
            data = self.load_meta()
            key = data.get("apiKeys", {}).get(key_id)
            if not key or key["accountId"] != account_id or key.get("revokedAt"):
                raise KeyError("API key not found.")
            key["revokedAt"] = now_iso()
            self.save_meta(data)

    def check_ready(self) -> None:
        with STORE_LOCK:
            data = self.load_meta()
            for draft in data.get("drafts", {}).values():
                self._read_current_object(draft)
            probe = self.root / f".ready-{secrets.token_hex(8)}"
            try:
                _atomic_write_text(probe, "ready\n")
            finally:
                probe.unlink(missing_ok=True)


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


class TailplanHTTPServer(ThreadingHTTPServer):
    """Threaded HTTP server with bounded admission and finite socket reads."""

    store: Store
    token: str
    base_url: str
    redirect_view_base_url: str

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
        self.trust_tailscale_identity = False
        self.owner_login = ""
        self.allow_anonymous_uploads = False
        self.upload_ip_limit = 60
        self.upload_key_limit = 30
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


class Handler(BaseHTTPRequestHandler):
    server_version = "Tailplan/1.0"

    @property
    def store(self) -> Store:
        return self.server.store  # type: ignore[attr-defined]

    @property
    def token(self) -> str:
        return self.server.token  # type: ignore[attr-defined]

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

    def send_html(self, status: int, body: str, *, web: bool = False, cookie: str | None = None) -> None:
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
        if cookie:
            self.send_header("Set-Cookie", cookie)
        self.send_header("Content-Length", str(len(raw)))
        self.end_headers()
        if self.command != "HEAD":
            self.wfile.write(raw)

    def send_document(self, body: str, *, head_only: bool = False,
                      draft_id: str = "", version: int = 1) -> None:
        raw = body.encode("utf-8")
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
            "default-src 'none'; script-src 'none'; style-src 'unsafe-inline'; "
            "img-src https: data:; font-src https: data:; connect-src 'none'; "
            "object-src 'none'; frame-src 'none'; frame-ancestors 'none'; "
            "base-uri 'none'; form-action 'none'",
        )
        self.send_header("Content-Length", str(len(raw)))
        self.end_headers()
        if not head_only:
            self.wfile.write(raw)

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

    def serve_viewer(
        self,
        match: re.Match[str],
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
            draft, doc = self.store.get(
                match.group(1), int(match.group(2)) if match.group(2) else None
            )
        except (StorageError, OSError) as exc:
            self.log_message("storage error: %r", exc)
            self.send_json(503, {"ok": False, "error": "Storage unavailable."})
            return
        if not draft or doc is None:
            self.send_html(404, not_found())
            return
        self.send_document(doc, head_only=head_only, draft_id=draft["draftId"],
                           version=int(match.group(2)) if match.group(2) else draft["latestVersionNumber"])

    def serve_preview(self, match: re.Match[str], *, head_only: bool = False) -> None:
        if self.server.redirect_view_base_url:
            self.send_view_redirect(self.route_path(), urlparse(self.path).query)
            return
        version = int(match.group(2)) if match.group(2) else None
        try:
            draft, doc = self.store.get(match.group(1), version)
        except (StorageError, OSError) as exc:
            self.log_message("preview storage error: %r", exc)
            self.send_json(503, {"ok": False, "error": "Storage unavailable."})
            return
        if not draft or doc is None:
            self.send_html(404, not_found())
            return
        version = version or draft["latestVersionNumber"]
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
        root = f"{self.base_url.rstrip('/')}/d/{draft['draftId']}"
        image_url = f"{root}/v/{version}/preview.png"
        share_url = f"{root}/v/{version}/share" if match.group(2) else f"{root}/share"
        self.send_document(preview_document(doc, title, image_url, share_url),
                           head_only=head_only, draft_id=draft["draftId"], version=version)

    def do_GET(self) -> None:
        preview = PREVIEW_ROUTE_RE.fullmatch(self.route_path())
        if preview:
            self.serve_preview(preview, head_only=self.command == "HEAD")
            return
        parsed_target = urlparse(self.path)
        path = self.route_path()
        if path == "/healthz":
            self.send_json(
                200,
                {"ok": True, "service": "tailplan", "build": BUILD_ID, "time": now_iso()},
            )
            return
        if path == "/readyz":
            try:
                self.store.check_ready()
            except (StorageError, OSError) as exc:
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
        if path.startswith(("/api/", "/dashboard", "/cli/auth", "/auth/")):
            self.management_request("GET", path)
            return
        m = VIEWER_ROUTE_RE.fullmatch(path)
        if m:
            self.serve_viewer(m, path=path, query=parsed_target.query)
            return
        if path == "/" or path == "":
            self.send_html(200, home(self.base_url))
            return
        self.send_html(404, not_found())

    def do_HEAD(self) -> None:
        parsed_target = urlparse(self.path)
        path = self.route_path()
        match = VIEWER_ROUTE_RE.fullmatch(path)
        if match:
            self.serve_viewer(
                match,
                path=path,
                query=parsed_target.query,
                head_only=True,
            )
            return
        self.do_GET()

    def do_POST(self) -> None:
        path = self.route_path()
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
        if self.headers.get("Transfer-Encoding"):
            self.send_json(400, {"ok": False, "error": "Transfer-Encoding is not supported."})
            return
        length_headers = self.headers.get_all("Content-Length") or []
        if len(length_headers) > 1:
            self.send_json(
                400,
                {
                    "ok": False,
                    "error": "Multiple Content-Length headers are not allowed.",
                },
            )
            return
        length_header = length_headers[0] if length_headers else None
        if length_header is None:
            self.send_json(411, {"ok": False, "error": "Content-Length is required."})
            return
        if re.fullmatch(r"[0-9]+", length_header) is None:
            self.send_json(
                400,
                {
                    "ok": False,
                    "error": "Content-Length must contain only ASCII decimal digits.",
                },
            )
            return
        if len(length_header) > len(str(MAX_REQUEST_BYTES)):
            self.send_json(413, {"ok": False, "error": "Upload body too large."})
            return
        length = int(length_header)
        if length == 0:
            self.send_json(400, {"ok": False, "error": "Content-Length must be positive."})
            return
        if length > MAX_REQUEST_BYTES:
            self.send_json(413, {"ok": False, "error": "Upload body too large."})
            return
        try:
            body = self.rfile.read(length)
            if len(body) != length:
                self.send_json(400, {"ok": False, "error": "Upload body was incomplete."})
                return
            payload = json.loads(body.decode("utf-8"))
            if not isinstance(payload, dict):
                self.send_json(400, {"ok": False, "error": "JSON payload must be an object."})
                return
            html_doc = payload.get("html")
            ok, errors, warnings = validate_html(html_doc)
            if not ok:
                self.send_json(422, {"ok": False, "errors": errors, "warnings": warnings})
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
            request_key = self.headers.get("Idempotency-Key")
            if request_key is not None and (
                IDEMPOTENCY_KEY_RE.fullmatch(request_key) is None
            ):
                self.send_json(400, {"ok": False, "error": "Idempotency-Key is invalid."})
                return
            result = self.store.upsert(
                html_doc, filename, draft_id, self.base_url, request_key,
                account_id=identity["accountId"],
                description=clean_text(payload.get("description"), 1000),
                metadata=upload_metadata(payload.get("metadata", {})),
                audit={"apiKeyId": identity["apiKeyId"], "sourceIp": self.source_ip(),
                       "userAgent": clean_text(self.headers.get("User-Agent")),
                       "requestId": secrets.token_hex(16)},
            )
            created = result.pop("created")
            status = 201 if created else 200
            self.send_json(status, {"ok": True, **result, "warnings": warnings,
                                    "shareUrl": result["publicUrl"] + "/share"})
        except (UnicodeDecodeError, json.JSONDecodeError):
            self.send_json(400, {"ok": False, "error": "Request body must be valid UTF-8 JSON."})
        except (ValueError, TypeError, UnicodeEncodeError):
            self.send_json(400, {"ok": False, "error": "Upload metadata is invalid."})
        except IdempotencyConflict as exc:
            self.send_json(409, {"ok": False, "error": str(exc)})
        except KeyError as e:
            self.send_json(404, {"ok": False, "error": str(e).strip("'")})
        except (StorageError, OSError) as e:
            self.send_json(503, {"ok": False, "error": "Storage unavailable."})
            self.log_message("storage error: %r", e)
        except Exception as e:  # noqa: BLE001 - The request handler returns JSON for unexpected upload errors.
            self.log_message("unexpected upload error: %r", e)
            self.send_json(500, {"ok": False, "error": "Internal server error."})

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
        value = read_signed(self.cookie_value("tailplan_session"), self.token)
        if not value or not all(isinstance(value.get(field), str)
                                for field in ("accountId", "apiKeyId", "csrf")):
            return None
        account = self.store.account(value["accountId"])
        if not account:
            return None
        key_id = value["apiKeyId"]
        if key_id == "bootstrap" and value["accountId"] != "local":
            return None
        if key_id == "tailscale":
            if not self.server.trust_tailscale_identity:
                return None
            if value["accountId"] == "local" and value.get("login", "").casefold() != self.server.owner_login.casefold():
                return None
        elif key_id != "bootstrap":
            keys = self.store.list_keys(value["accountId"])
            if not any(key["id"] == key_id for key in keys):
                return None
        return {**value, **account}

    def redirect(self, path: str, cookie: str | None = None) -> None:
        self.send_response(303)
        self.send_header("Location", self.base_url.rstrip("/") + path)
        self.send_header("Cache-Control", "no-store")
        self.send_header("Content-Length", "0")
        if cookie:
            self.send_header("Set-Cookie", cookie)
        self.end_headers()

    def tailscale_login(self) -> tuple[str, str] | None:
        if not self.server.trust_tailscale_identity or self.client_address[0] not in {"127.0.0.1", "::1"}:
            return None
        login = clean_text(self.headers.get("Tailscale-User-Login"))
        if not login:
            return None
        name = clean_text(unquote(self.headers.get("Tailscale-User-Name", ""))) or login
        return login, name

    def sign_in_page(self, next_path: str = "/dashboard") -> None:
        state = {"csrf": secrets.token_hex(24), "expires": int(time.time()) + 600}
        cookie = self.cookie_header("tailplan_login", signed_value(state, self.token), 600)
        body = render_login(self.base_url, state["csrf"], next_path, bool(self.tailscale_login()))
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

    def management_request(self, method: str, path: str) -> None:
        try:
            if path.startswith("/api/"):
                self.management_api(method, path)
            else:
                self.management_web(method, path)
        except (ValueError, TypeError, UnicodeError):
            self.send_json(400, {"ok": False, "error": "Request fields or body are invalid."})
        except KeyError as exc:
            self.send_json(404, {"ok": False, "error": str(exc).strip("'")})
        except (StorageError, OSError) as exc:
            self.log_message("management storage error: %r", exc)
            self.send_json(503, {"ok": False, "error": "Storage unavailable."})

    def management_api(self, method: str, path: str) -> None:
        identity = self.api_identity()
        if identity is None:
            self.unauthorized()
            return
        account_id = identity["accountId"]
        if method == "GET" and path == "/api/me":
            self.send_json(200, {"ok": True, **identity, "scope": "tailnet"})
        elif method == "GET" and path == "/api/drafts":
            self.send_json(200, {"ok": True, "drafts": self.store.list_drafts(account_id, self.base_url)})
        elif method == "GET" and path == "/api/api-keys":
            self.send_json(200, {"ok": True, "apiKeys": self.store.list_keys(account_id)})
        elif method == "POST" and path == "/api/api-keys":
            if self.rate_allowed("keys:" + account_id, 10, 3600):
                payload = self.read_control_body()
                self.send_json(201, self.store.create_key(account_id, payload.get("name")))
        elif (match := re.fullmatch(r"/api/api-keys/([a-z0-9]{6,32})/revoke", path)) and method == "POST":
            self.store.revoke_key(account_id, match[1])
            self.send_json(200, {"ok": True})
        elif match := re.fullmatch(r"/api/drafts/([a-z0-9]{6,32})(?:/(versions|disable|enable))?", path):
            draft_id, action = match.groups()
            if method == "GET" and action in {None, "versions"}:
                self.send_json(200, self.store.draft_detail(account_id, draft_id, self.base_url))
            elif method == "DELETE" and action is None:
                self.store.change_draft(account_id, draft_id, "delete", None)
                self.send_json(200, {"ok": True})
            elif method == "POST" and action in {"disable", "enable"}:
                payload = self.read_control_body()
                self.store.change_draft(account_id, draft_id, action, payload.get("reason"))
                self.send_json(200, {"ok": True})
            else:
                self.send_json(404, {"ok": False, "error": "Not found."})
        else:
            self.send_json(404, {"ok": False, "error": "Not found."})

    def management_web(self, method: str, path: str) -> None:
        known = path in {"/dashboard", "/cli/auth", "/cli/auth/keys", "/auth/sign-in", "/auth/sign-out"}
        draft_match = re.fullmatch(r"/dashboard/drafts/([a-z0-9]{6,32})(?:/(disable|enable|delete))?", path)
        key_match = re.fullmatch(r"/cli/auth/keys/([a-z0-9]{6,32})/revoke", path)
        if not known and not draft_match and not key_match:
            self.send_html(404, not_found())
            return
        if method == "GET" and path == "/auth/sign-in":
            self.sign_in_page()
            return
        if method == "POST" and path == "/auth/sign-in":
            if not self.rate_allowed("login:" + self.source_ip(), 20):
                return
            payload = self.read_control_body()
            state = read_signed(self.cookie_value("tailplan_login"), self.token)
            if not self.valid_form(payload, state):
                self.send_json(403, {"ok": False, "error": "Sign-in expired. Reload and retry."})
                return
            if payload.get("method") == "tailscale" and (login := self.tailscale_login()):
                identity = {**self.store.identity_account(*login, self.server.owner_login),
                            "apiKeyId": "tailscale", "login": login[0]}
            else:
                token = payload.get("token")
                identity = self.store.authenticate(token, self.token) if isinstance(token, str) else None
            if identity is None:
                self.unauthorized()
                return
            session = {**identity, "csrf": secrets.token_hex(24), "expires": int(time.time()) + 30 * 86400}
            next_path = payload.get("next")
            if next_path != "/cli/auth" and not re.fullmatch(r"/dashboard(?:/drafts/[a-z0-9]{6,32})?", str(next_path)):
                next_path = "/dashboard"
            self.redirect(next_path, self.cookie_header("tailplan_session", signed_value(session, self.token), 30 * 86400))
            return
        session = self.session()
        if session is None:
            if method == "GET":
                self.sign_in_page(path)
            else:
                self.unauthorized()
            return
        account_id = session["accountId"]
        if method == "POST":
            payload = self.read_control_body()
            if not self.valid_form(payload, session):
                self.send_json(403, {"ok": False, "error": "Invalid form token. Reload and retry."})
                return
            if path == "/auth/sign-out":
                self.redirect("/", self.cookie_header("tailplan_session", "", 0))
            elif path == "/cli/auth/keys":
                if self.rate_allowed("keys:" + account_id, 10, 3600):
                    result = self.store.create_key(account_id, payload.get("name"))
                    self.send_html(200, render_key(self.base_url, session, result), web=True)
            elif key_match:
                self.store.revoke_key(account_id, key_match[1])
                self.redirect("/cli/auth")
            elif draft_match and draft_match[2]:
                self.store.change_draft(account_id, draft_match[1], draft_match[2], payload.get("reason"))
                target = "/dashboard" if draft_match[2] == "delete" else f"/dashboard/drafts/{draft_match[1]}"
                self.redirect(target)
            else:
                self.send_html(404, not_found())
            return
        if method != "GET":
            self.send_html(404, not_found())
        elif path == "/dashboard":
            self.send_html(200, render_dashboard(self.base_url, session,
                           self.store.list_drafts(account_id, self.base_url)), web=True)
        elif path == "/cli/auth":
            self.send_html(200, render_keys(self.base_url, session,
                           self.store.list_keys(account_id)), web=True)
        elif draft_match and draft_match[2] is None:
            detail = self.store.draft_detail(account_id, draft_match[1], self.base_url)
            self.send_html(200, render_detail(self.base_url, session, detail), web=True)
        else:
            self.send_html(404, not_found())



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


def not_found() -> str:
    return page("Draft not found", "<main class=\"home\"><h1>Draft not found</h1><p>The requested Tailplan draft is unavailable.</p></main>")


WEB_CSS = """
:root{color-scheme:light}*{box-sizing:border-box}body{margin:0;background:#f8fafc;color:#172033;font:16px/1.6 system-ui,sans-serif}
header,main{width:min(100% - 32px,1050px);margin:auto}header{display:flex;justify-content:space-between;gap:16px;flex-wrap:wrap;padding:20px 0;border-bottom:1px solid #dce3ec}
nav{display:flex;gap:20px;align-items:center}main{margin:36px auto 80px}h1{font-size:clamp(26px,5vw,36px);line-height:1.2;margin:0 0 20px}h2{font-size:18px;margin:30px 0 10px}
a{color:#245bc0;text-underline-offset:3px;overflow-wrap:anywhere}p{overflow-wrap:anywhere}.muted,small{color:#59687d}.row{padding:18px 20px;background:white;border:1px solid #dce3ec;border-radius:10px;margin:10px 0}
.row h3{margin:0;font-size:18px}.row p{margin:5px 0}.meta{font-size:14px;display:flex;gap:12px;flex-wrap:wrap}.badge{font-size:12px;background:#fef3c7;border-radius:4px;padding:2px 6px}
button,.button{font:inherit;background:#172033;color:white;border:0;border-radius:7px;padding:9px 14px;cursor:pointer;text-decoration:none}button.secondary{background:#e5ebf4;color:#172033}
input{font:inherit;padding:10px;border:1px solid #b6c3d5;border-radius:6px;width:100%;max-width:600px}label{display:block;margin:12px 0 4px}form{margin:12px 0}header form{margin:0}header button{padding:4px 10px}
.actions{display:flex;gap:12px;flex-wrap:wrap}.actions form{margin:0}code{background:#eaf0f8;padding:2px 5px;border-radius:4px;overflow-wrap:anywhere}pre{white-space:pre-wrap;overflow-wrap:anywhere;background:white;padding:16px;border:1px solid #dce3ec;border-radius:8px}
.table-wrap{width:100%;overflow-x:auto}table{width:100%;border-collapse:collapse;background:white}th,td{text-align:left;padding:12px;border-bottom:1px solid #dce3ec;vertical-align:top}th{font-size:13px;color:#59687d}td{min-width:110px;max-width:430px;overflow-wrap:anywhere}
.narrow{max-width:620px}details{margin:10px 0}summary{cursor:pointer}dl{display:grid;grid-template-columns:minmax(90px,1fr) 3fr;gap:6px}dt{color:#59687d}dd{margin:0;overflow-wrap:anywhere}
@media(max-width:600px){header,main{width:calc(100% - 24px)}header{padding:12px 0}nav{gap:12px}.row{padding:14px}main{margin-top:24px}table{min-width:650px}}
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


def render_login(base_url: str, csrf: str, next_path: str, tailscale: bool) -> str:
    fields = csrf_field(csrf) + f'<input type="hidden" name="next" value="{esc(next_path)}">'
    action = esc(base_url.rstrip("/")) + "/auth/sign-in"
    body = '<div class="narrow"><h1>Your drafts, in one place</h1><p>Sign in to manage drafts and connect your agents.</p>'
    if tailscale:
        body += f'<form method="post" action="{action}">{fields}<input type="hidden" name="method" value="tailscale"><button>Continue with Tailscale</button></form>'
    body += f'<form method="post" action="{action}">{fields}<label for="token">API key</label>'
    body += '<input id="token" name="token" type="password" autocomplete="current-password" required maxlength="512"><p><button>Sign in with API key</button></p></form>'
    body += '<p class="muted">Use your installed Tailplan token or a named API key.</p></div>'
    return web_page("Sign in", body, base_url)


def external_link(url: object, label: str) -> str:
    try:
        parsed = urlparse(str(url))
        if parsed.scheme not in {"https", "http"} or not parsed.hostname or parsed.username:
            return esc(label)
    except ValueError:
        return esc(label)
    return f'<a href="{esc(url)}" target="_blank" rel="noopener noreferrer">{esc(label)}</a>'


def render_dashboard(base_url: str, session: dict, drafts: list[dict]) -> str:
    groups: dict[tuple, list] = {}
    for draft in drafts:
        key = tuple(draft.get(field) or "" for field in ("repoHost", "repoOrg", "repoName"))
        groups.setdefault(key, []).append(draft)
    body = f'<h1>My drafts <small>{len(drafts)}</small></h1>'
    if not drafts:
        body += '<p>No drafts yet. Publish one with <code>tailplan upload plan.html</code>.</p>'
    for key, members in sorted(groups.items(), key=lambda entry: not bool(entry[0][2])):
        host, org, name = key
        label = f"{org}/{name}" if org and name else "No repository"
        if host and re.fullmatch(r"[A-Za-z0-9.-]+", host) and org and name:
            label = external_link(f"https://{host}/{quote(org, safe='')}/{quote(name, safe='')}", label)
        else:
            label = esc(label)
        body += f'<section><h2>{label}</h2>'
        for draft in members:
            badge = ' <span class="badge">disabled</span>' if draft["disabled"] else ""
            body += f'<article class="row"><h3>{external_link(draft["shareUrl"], draft["title"])}{badge}</h3>'
            if draft["description"]:
                body += f'<p>{esc(draft["description"])}</p>'
            details = esc(base_url.rstrip("/")) + "/dashboard/drafts/" + draft["draftId"]
            body += f'<div class="meta"><a href="{details}">Details and history</a><span>v{draft["latestVersionNumber"]}</span>'
            body += f'<span>{draft["versionCount"]} versions</span><time>{esc(draft["updatedAt"][:16].replace("T", " "))} UTC</time></div></article>'
        body += '</section>'
    return web_page("My drafts", body, base_url, session)


def render_detail(base_url: str, session: dict, detail: dict) -> str:
    draft = detail["draft"]
    body = f'<h1>{esc(draft["title"])}</h1><p>{esc(draft["description"])}</p>'
    body += f'<p>{external_link(draft["shareUrl"], "Open latest draft")} · {external_link(draft["rawUrl"], "Raw HTML")}</p>'
    if draft["disabled"]:
        body += f'<p class="badge">Disabled: {esc(draft["disabledReason"])}</p>'
    action_base = esc(base_url.rstrip("/")) + "/dashboard/drafts/" + draft["draftId"]
    body += '<div class="actions">'
    for action, label in (("enable", "Enable draft") if draft["disabled"] else ("disable", "Disable draft"),):
        body += f'<form method="post" action="{action_base}/{action}">{csrf_field(session["csrf"])}<button class="secondary">{label}</button></form>'
    body += '</div><h2>Version history</h2><div class="table-wrap"><table><thead><tr><th>Version</th><th>Commit</th><th>Ref</th><th>Published</th><th>Source</th></tr></thead><tbody>'
    for version in detail["versions"]:
        number = version["versionNumber"]
        url = draft["publicUrl"] + f"/v/{number}"
        dirty = ' <span class="badge">dirty</span>' if version.get("gitDirty") else ""
        body += f'<tr><td>{external_link(url, "v" + str(number))}<br>{external_link(url + "/raw", "Raw HTML")}</td>'
        body += f'<td>{esc(version.get("gitCommitSubject"))}{dirty}</td><td>{esc(version.get("gitBranch"))}<br><code>{esc((version.get("gitCommitSha") or "")[:12])}</code></td>'
        body += f'<td>{esc(version["createdAt"][:16].replace("T", " "))} UTC</td><td>{version["fileSize"]} bytes'
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
        body += f'<tr><td>{esc(key["name"])}</td><td>{esc(key["createdAt"][:16])}</td><td>{esc((key["lastUsedAt"] or "Never")[:16])}</td>'
        body += f'<td><form method="post" action="{base}/{key["id"]}/revoke">{csrf_field(session["csrf"])}<button class="secondary">Revoke</button></form></td></tr>'
    body += '</tbody></table></div>'
    return web_page("CLI setup", body, base_url, session)


def render_key(base_url: str, session: dict, result: dict) -> str:
    body = '<h1>Your new API key</h1><p>Copy this key now. Tailplan shows each key once.</p>'
    body += f'<label for="key">{esc(result["apiKey"]["name"])}</label><input id="key" readonly value="{esc(result["token"])}">'
    body += '<p>Paste the key into <code>tailplan auth login</code>.</p>'
    body += f'<p><a href="{esc(base_url.rstrip("/"))}/cli/auth">Back to CLI setup</a></p>'
    return web_page("New API key", body, base_url, session)


def create_servers(
    primary_address: tuple[str, int],
    proxy_address: tuple[str, int] | None,
    *,
    store: Store,
    token: str,
    base_url: str,
    redirect_view_base_url: str,
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
    primary.store = store
    primary.token = token
    primary.base_url = configured_base_url
    primary.redirect_view_base_url = redirect_view_base_url
    if proxy is not None:
        proxy.store = store
        proxy.token = token
        proxy.base_url = configured_base_url
        proxy.redirect_view_base_url = ""
        proxy.limiter = primary.limiter
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
    ap.add_argument("--base-url", default=os.getenv("TAILPLAN_BASE_URL", ""))
    ap.add_argument("--trust-tailscale-identity", action="store_true",
                    default=os.getenv("TAILPLAN_TRUST_TAILSCALE_IDENTITY") == "1")
    ap.add_argument("--owner-login", default=os.getenv("TAILPLAN_OWNER_LOGIN", ""))
    ap.add_argument("--allow-anonymous-uploads", action="store_true",
                    default=os.getenv("TAILPLAN_ALLOW_ANONYMOUS_UPLOADS") == "1")
    ap.add_argument("--upload-ip-limit", type=int, default=os.getenv("TAILPLAN_UPLOAD_IP_LIMIT", "60"))
    ap.add_argument("--upload-key-limit", type=int, default=os.getenv("TAILPLAN_UPLOAD_KEY_LIMIT", "30"))
    ap.add_argument(
        "--redirect-view-base-url",
        type=_redirect_base_url,
        default=os.getenv("TAILPLAN_REDIRECT_VIEW_BASE_URL") or None,
    )
    args = ap.parse_args(argv)
    args.redirect_view_base_url = args.redirect_view_base_url or ""
    if args.upload_ip_limit < 1 or args.upload_key_limit < 1:
        ap.error("upload limits must be positive")
    if args.trust_tailscale_identity and args.proxy_host not in {"127.0.0.1", "::1"}:
        ap.error("Tailscale identity requires a loopback proxy listener")
    if (args.proxy_host is None) != (args.proxy_port is None):
        ap.error("--proxy-host and --proxy-port must be supplied together")
    if args.proxy_host == "":
        ap.error("--proxy-host must not be empty")
    if args.proxy_host is not None and (args.proxy_host, args.proxy_port) == (
        args.host,
        args.port,
    ):
        ap.error("proxy listener must differ from primary listener")
    return args


def main() -> int:
    args = parse_args()
    token_path = Path(args.token_file).expanduser()
    token = token_path.read_text().strip()
    if not token:
        raise SystemExit("empty token file")
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
        )
    except ListenerStartupError as exc:
        raise SystemExit(str(exc)) from exc
    for listener in (primary, proxy):
        if listener is not None:
            listener.allow_anonymous_uploads = args.allow_anonymous_uploads
            listener.upload_ip_limit = args.upload_ip_limit
            listener.upload_key_limit = args.upload_key_limit
    if proxy is not None:
        proxy.trust_tailscale_identity = args.trust_tailscale_identity
        proxy.owner_login = args.owner_login
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
