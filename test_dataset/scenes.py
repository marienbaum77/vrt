"""Procedural UI scenes (component library) used to build the labelled dataset.

Every interactive-looking element carries a ``data-m`` hook that mutations target
(``text`` | ``num`` | ``block`` | ``btn`` | ``icon``).  Elements that model dynamic
content (clock, live counter, ad banner) carry ``data-vrt-ignore`` and are masked by the
capture selector, exactly like in a real project.
"""
from __future__ import annotations

import colorsys
import random
from dataclasses import dataclass
from typing import Callable, List

WORDS = (
    "amber basin cedar delta ember fjord glade harbor island jungle kernel lagoon meadow nectar orchid prairie "
    "quartz ridge summit tundra umber valley willow yonder analytics billing campaign dashboard export feedback "
    "growth insights journal keyword latency metrics network overview pipeline quality reports segments traffic "
    "uptime vendors workflow archive balance capacity deploy engine forecast gateway history inventory"
).split()
NAMES = "Ava Brooks|Liam Chen|Noah Patel|Emma Rossi|Olivia Kim|Lucas Silva|Mia Novak|Ethan Park|Sofia Haas|Leo Duarte|Zoe Marin|Max Weber".split("|")
STATUS = [("Paid", "#12805c"), ("Pending", "#b7791f"), ("Failed", "#c62828"), ("Refunded", "#5b6ad0")]
FONTS = ["Inter", "DejaVu Sans", "Carlito", "Poppins", "FreeSans"]
IGNORE_SELECTOR = "[data-vrt-ignore]"


def _hsl(h: float, s: float, l: float) -> str:
    r, g, b = colorsys.hls_to_rgb(h / 360.0, l / 100.0, s / 100.0)
    return "#%02x%02x%02x" % (round(r * 255), round(g * 255), round(b * 255))


def _title(rng: random.Random, n: int = 2) -> str:
    return " ".join(rng.sample(WORDS, n)).capitalize()


def _sentence(rng: random.Random, n: int) -> str:
    s = " ".join(rng.choice(WORDS) for _ in range(n))
    return s.capitalize() + "."


def _num(rng: random.Random, lo: int, hi: int, money: bool = False) -> str:
    v = rng.randint(lo, hi)
    return ("$" if money else "") + f"{v:,}"


@dataclass
class Scene:
    idx: int
    layout: str
    components: List[str]
    theme: dict
    html: str


def make_theme(rng: random.Random) -> dict:
    dark = rng.random() < 0.2
    hue = rng.choice([212, 262, 158, 24, 340, 190, 40])
    return {
        "dark": dark,
        "font": rng.choice(FONTS),
        "bg": "#14161b" if dark else rng.choice(["#ffffff", "#f6f7f9", "#fafaf7"]),
        "card": "#1d2027" if dark else "#ffffff",
        "fg": "#e8eaf0" if dark else "#1f2430",
        "mut": "#9aa3b2" if dark else "#6b7280",
        "line": "#2c313b" if dark else "#e3e6ec",
        "pri": _hsl(hue, 72, 58 if dark else 48),
        "pri2": _hsl((hue + 40) % 360, 70, 62 if dark else 56),
        "radius": rng.choice([4, 8, 12]),
        "gap": rng.choice([12, 16, 20]),
    }


def base_css(t: dict) -> str:
    return f"""
*{{box-sizing:border-box}}
html,body{{margin:0;background:{t['bg']};color:{t['fg']};font-family:'{t['font']}',sans-serif;font-size:14px;line-height:1.45}}
.app{{display:flex;min-height:720px}}.side{{width:200px;background:{t['card']};border-right:1px solid {t['line']};padding:18px 14px}}
.side a{{display:block;padding:8px 10px;border-radius:{t['radius']}px;color:{t['mut']}}}.side a.on{{background:{t['pri']};color:#fff}}
.main{{flex:1;padding:20px 24px;display:flex;flex-direction:column;gap:{t['gap']}px;min-width:0}}
.top{{display:flex;align-items:center;gap:24px;height:56px;padding:0 24px;border-bottom:1px solid {t['line']};background:{t['card']}}}
.brand{{font-weight:700;font-size:18px;color:{t['pri']}}}.top nav{{display:flex;gap:18px;flex:1;color:{t['mut']}}}
.clock{{display:inline-block;width:78px;text-align:right;font-variant-numeric:tabular-nums;color:{t['mut']}}}
.avatar{{width:32px;height:32px;border-radius:16px;background:{t['pri2']};color:#fff;display:flex;align-items:center;justify-content:center;font-weight:600;font-size:12px}}
.kpis{{display:flex;gap:{t['gap']}px}}.kpi{{flex:1;background:{t['card']};border:1px solid {t['line']};border-radius:{t['radius']}px;padding:14px 16px;position:relative}}
.kpi .lbl{{color:{t['mut']};font-size:12px}}.kpi .val{{font-size:26px;font-weight:700;margin:4px 0}}
.delta{{display:inline-block;padding:1px 8px;border-radius:99px;font-size:12px;background:{t['pri']};color:#fff}}
.panel{{background:{t['card']};border:1px solid {t['line']};border-radius:{t['radius']}px;padding:16px}}.panel h3{{margin:0 0 10px;font-size:15px}}
.bars{{display:flex;align-items:flex-end;gap:10px;height:150px;border-bottom:1px solid {t['line']}}}.bar{{width:30px;background:{t['pri']};border-radius:{t['radius']//2}px {t['radius']//2}px 0 0}}
table{{border-collapse:collapse;width:100%}}th{{text-align:left;color:{t['mut']};font-weight:500;font-size:12px}}td,th{{padding:7px 8px;border-bottom:1px solid {t['line']}}}
.pill{{display:inline-block;padding:1px 9px;border-radius:99px;color:#fff;font-size:12px}}
.article h1{{font-size:30px;margin:0 0 6px;line-height:1.2}}.article p{{max-width:680px;margin:0 0 10px;color:{t['fg']}}}
.article blockquote{{margin:6px 0;padding:10px 16px;border-left:4px solid {t['pri']};background:{t['card']};max-width:680px}}
.cards{{display:flex;gap:{t['gap']}px}}.card{{flex:1;background:{t['card']};border:1px solid {t['line']};border-radius:{t['radius']}px;overflow:hidden;position:relative}}
.thumb{{height:96px}}.card .body{{padding:12px 14px}}.card h4{{margin:0 0 4px;font-size:15px}}.price{{font-size:20px;font-weight:700;margin-bottom:8px}}
.btn{{display:inline-block;padding:8px 16px;border-radius:{t['radius']}px;background:{t['pri']};color:#fff;font-weight:600;font-size:13px}}
.form{{max-width:420px}}.form label{{display:block;font-size:12px;color:{t['mut']};margin:10px 0 4px}}
.input{{height:36px;border:1px solid {t['line']};border-radius:{t['radius']}px;background:{t['bg']};padding:8px 10px;color:{t['mut']}}}
.tabs{{display:flex;gap:22px;border-bottom:1px solid {t['line']}}}.tab{{padding:8px 2px;color:{t['mut']}}}.tab.on{{color:{t['pri']};border-bottom:2px solid {t['pri']}}}
.list .row{{display:flex;align-items:center;gap:12px;padding:8px 0;border-bottom:1px solid {t['line']}}}.row .sub{{color:{t['mut']};font-size:12px}}.row .ts{{margin-left:auto;color:{t['mut']};font-size:12px}}
.ad{{display:flex;align-items:center;justify-content:space-between;height:64px;padding:0 18px;border-radius:{t['radius']}px;color:#fff;background:linear-gradient(90deg,{t['pri']},{t['pri2']})}}
.ad span{{display:inline-block;width:360px}}.live{{display:inline-block;width:110px;color:{t['mut']}}}
.center{{display:flex;justify-content:center;padding:36px 24px;gap:{t['gap']}px;flex-direction:column;align-items:center}}.center>*{{width:100%;max-width:880px}}
"""


# ----------------------------------------------------------------- components
def c_topbar(rng, t):
    links = "".join(f'<a data-m="text">{_title(rng, 1)}</a>' for _ in range(4))
    return (
        f'<header class=top><div class=brand data-m="text">{_title(rng, 1)}Hub</div><nav>{links}</nav>'
        f'<span class=clock data-vrt-ignore>{rng.randint(0,23):02d}:{rng.randint(0,59):02d}:{rng.randint(0,59):02d}</span>'
        f'<div class=avatar data-m="icon block">{rng.choice(NAMES)[:2].upper()}</div></header>'
    )


def c_kpis(rng, t):
    n = rng.choice([3, 4])
    cards = "".join(
        f'<div class=kpi data-m="block"><div class=lbl data-m="text">{_title(rng, 1)}</div>'
        f'<div class=val data-m="num text">{_num(rng, 120, 98000, money=rng.random() < .5)}</div>'
        f'<span class=delta data-m="icon block">{rng.choice(["+","-"])}{rng.randint(1,19)}.{rng.randint(0,9)}%</span></div>'
        for _ in range(n)
    )
    return f"<section class=kpis>{cards}</section>"


def c_chart(rng, t):
    bars = "".join(f'<div class=bar data-m="block" style="height:{rng.randint(30,140)}px"></div>' for _ in range(rng.randint(8, 14)))
    return f'<section class=panel><h3 data-m="text">{_title(rng, 2)}</h3><div class=bars>{bars}</div></section>'


def c_table(rng, t):
    rows = ""
    for _ in range(rng.randint(5, 7)):
        st, col = rng.choice(STATUS)
        rows += (
            f'<tr><td data-m="text">{rng.choice(NAMES)}</td><td><span class=pill data-m="icon block" style="background:{col}">{st}</span></td>'
            f'<td data-m="num text">{_num(rng, 20, 9900, money=True)}</td><td data-m="text">{rng.randint(1,28):02d} {rng.choice(["Jan","Feb","Mar","Apr","May","Jun"])} 2026</td></tr>'
        )
    return (
        f'<section class=panel><h3 data-m="text">{_title(rng, 2)}</h3><table><tr><th>Customer</th><th>Status</th><th>Amount</th><th>Date</th></tr>{rows}</table></section>'
    )


def c_article(rng, t):
    ps = "".join(f'<p data-m="text">{" ".join(_sentence(rng, rng.randint(9, 16)) for _ in range(2))}</p>' for _ in range(2))
    return (
        f'<article class=article><h1 data-m="text">{_title(rng, 4)}</h1><div style="color:{t["mut"]};margin-bottom:10px" data-m="text">By {rng.choice(NAMES)} &middot; {rng.randint(3,12)} min read</div>'
        f'{ps}<blockquote data-m="block text">{_sentence(rng, 12)}</blockquote></article>'
    )


def c_cards(rng, t):
    cards = ""
    for _ in range(3):
        h = rng.randint(0, 360)
        cards += (
            f'<div class=card data-m="block"><div class=thumb data-m="block" style="background:linear-gradient(135deg,{_hsl(h,70,60)},{_hsl((h+50)%360,70,52)})"></div>'
            f'<div class=body><h4 data-m="text">{_title(rng, 2)}</h4><div class=price data-m="num text">{_num(rng, 9, 499, money=True)}</div>'
            f'<span class=btn data-m="btn block">{rng.choice(["Add to cart","Buy now","Subscribe","Details"])}</span></div></div>'
        )
    return f"<section class=cards>{cards}</section>"


def c_form(rng, t):
    fields = "".join(
        f'<label data-m="text">{_title(rng, 1)}</label><div class=input data-m="block">{rng.choice(["name@example.com","Jane Doe","••••••••","+1 555 0100"])}</div>' for _ in range(3)
    )
    return (
        f'<section class="panel form"><h3 data-m="text">{_title(rng, 2)}</h3>{fields}'
        f'<div style="margin-top:16px"><span class=btn data-m="btn block">{rng.choice(["Sign in","Save changes","Continue"])}</span>'
        f' <span style="color:{t["pri"]};margin-left:12px" data-m="text">{rng.choice(["Forgot password?","Cancel","Need help?"])}</span></div></section>'
    )


def c_banner(rng, t):
    return (
        f'<div class=ad data-vrt-ignore><span>Sponsored: {_title(rng, 2)} &mdash; limited offer</span><b>{rng.randint(10,60)}% off</b></div>'
    )


def c_tabs(rng, t):
    tabs = "".join(f'<span class="tab{" on" if i == 0 else ""}" data-m="text">{_title(rng, 1)}</span>' for i in range(rng.choice([3, 4, 5])))
    return f"<div class=tabs>{tabs}</div>"


def c_list(rng, t):
    rows = ""
    for _ in range(rng.randint(4, 6)):
        name = rng.choice(NAMES)
        rows += (
            f'<div class=row><div class=avatar data-m="icon block" style="background:{_hsl(rng.randint(0,360),60,50)}">{name[:2].upper()}</div>'
            f'<div><div data-m="text">{name}</div><div class=sub data-m="text">{_sentence(rng, 4)}</div></div><div class=ts data-m="text">{rng.randint(1,59)} min ago</div></div>'
        )
    live = f'<div style="margin-bottom:6px;color:{t["mut"]}">Team activity &middot; <b class=live data-vrt-ignore>{_num(rng, 100, 9999)} online</b></div>'
    return f'<section class="panel list">{live}{rows}</section>'


COMPONENTS: dict[str, Callable] = {
    "kpis": c_kpis,
    "chart": c_chart,
    "table": c_table,
    "article": c_article,
    "cards": c_cards,
    "form": c_form,
    "banner": c_banner,
    "tabs": c_tabs,
    "list": c_list,
}


def build_scene(idx: int, seed: int = 2026) -> Scene:
    rng = random.Random(seed * 100003 + idx)
    t = make_theme(rng)
    layout = rng.choice(["sidebar", "top", "center"])
    pool = {
        "sidebar": ["kpis", "chart", "table", "list", "tabs", "banner", "cards"],
        "top": ["kpis", "cards", "table", "chart", "banner", "tabs", "list"],
        "center": ["article", "form", "cards", "banner"],
    }[layout]
    k = {"sidebar": 3, "top": 3, "center": 2}[layout]
    comps = rng.sample(pool, k)
    if layout != "center" and "banner" not in comps and rng.random() < 0.6:
        comps.insert(rng.randint(0, len(comps)), "banner")
    comps = comps[:4]
    body = "".join(COMPONENTS[c](rng, t) for c in comps)
    if layout == "sidebar":
        nav = "".join(f'<a class="{"on" if i == 0 else ""}" data-m="text">{_title(rng, 1)}</a>' for i in range(6))
        page = f'<div class=app><aside class=side><div class=brand style="margin-bottom:14px" data-m="text">{_title(rng,1)}Hub</div>{nav}</aside><main class=main>{body}</main></div>'
    elif layout == "top":
        page = f"{c_topbar(rng, t)}<main class=main style='padding-top:18px'>{body}</main>"
    else:
        page = f"{c_topbar(rng, t)}<main class=center>{body}</main>"
    html = f"<!doctype html><html><head><meta charset=utf-8><title>scene {idx}</title><style>{base_css(t)}</style></head><body>{page}</body></html>"
    return Scene(idx=idx, layout=layout, components=comps, theme=t, html=html)
