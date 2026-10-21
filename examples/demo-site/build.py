"""Generate the demo site in two versions: v1 (baseline) and v2 (contains deliberate visual regressions).

    python build.py      # writes v1/ and v2/
"""
from pathlib import Path

CSS = """
*{box-sizing:border-box}body{margin:0;font-family:'DejaVu Sans',sans-serif;color:#1f2430;background:#f6f7f9;font-size:15px}
header{display:flex;align-items:center;gap:24px;height:60px;padding:0 32px;background:#fff;border-bottom:1px solid #e3e6ec}
.brand{font-weight:700;font-size:20px;color:#2b5fd9}nav{display:flex;gap:20px;flex:1;color:#6b7280}
.clock{width:90px;text-align:right;color:#6b7280;font-variant-numeric:tabular-nums}
main{max-width:1000px;margin:0 auto;padding:32px}h1{font-size:34px;margin:0 0 8px}p.lead{color:#4b5563;max-width:640px}
.cards{display:flex;gap:20px;margin-top:28px}.card{flex:1;background:#fff;border:1px solid #e3e6ec;border-radius:10px;padding:20px}
.card h3{margin:0 0 6px}.price{font-size:28px;font-weight:700;margin:8px 0 14px}
.btn{display:inline-block;padding:10px 18px;border-radius:8px;background:%(btn)s;color:#fff;font-weight:600}
.ad{margin-top:28px;height:70px;border-radius:10px;background:linear-gradient(90deg,#f59e0b,#ef4444);color:#fff;display:flex;align-items:center;justify-content:center;font-weight:600}
.spinner{display:inline-block;width:14px;height:14px;border:3px solid #cbd5e1;border-top-color:#2b5fd9;border-radius:50%%;animation:spin 1s linear infinite;vertical-align:middle}
@keyframes spin{to{transform:rotate(360deg)}}
"""

def page(title, heading, lead, cards, version):
    v2 = version == "v2"
    btn = "#d9422b" if v2 else "#2b5fd9"
    cards_html = "".join(
        f'<div class="card"><h3>{n}</h3><div class="price">{p}</div><p>{d}</p><span class="btn">{b}</span></div>' for n, p, d, b in cards
    )
    shift = "margin-top:6px;" if v2 else ""
    return f"""<!doctype html><html><head><meta charset="utf-8"><title>{title}</title><style>{CSS % {'btn': btn}}</style></head>
<body><header><div class="brand">Acme</div><nav><span>Product</span><span>Pricing</span><span>Docs</span></nav>
<span class="clock" data-vrt-ignore id="clock">12:00:00</span><span class="spinner" data-vrt-ignore></span></header>
<main><h1 style="{shift}">{heading}</h1><p class="lead">{lead}</p><div class="cards">{cards_html}</div>
<div class="ad" data-vrt-ignore id="ad">Sponsored: spring sale</div></main>
<script>
  // dynamic content: the clock ticks and the banner rotates -> must be masked (data-vrt-ignore)
  setInterval(function(){{document.getElementById('clock').textContent=new Date().toLocaleTimeString('en-GB')}},250);
  var ads=['Sponsored: spring sale','Sponsored: new arrivals','Sponsored: free shipping'];var i=0;
  setInterval(function(){{document.getElementById('ad').textContent=ads[++i%ads.length]}},300);
</script></body></html>"""

for ver in ("v1", "v2"):
    out = Path(__file__).parent / ver
    out.mkdir(exist_ok=True)
    v2 = ver == "v2"
    (out / "index.html").write_text(page(
        "Acme", "Ship faster with Acme",
        "Acme turns visual regressions into reviewable diffs." if not v2 else "Acme turns visual regressions into reviewable changes.",
        [("Starter", "$9", "For side projects", "Choose"), ("Team", "$29" if not v2 else "$39", "For growing teams", "Choose"), ("Scale", "$99", "For organisations", "Contact us")], ver))
    (out / "pricing.html").write_text(page(
        "Pricing", "Simple pricing", "Pay for what you use. Cancel any time.",
        [("Monthly", "$12", "Billed monthly", "Subscribe"), ("Yearly", "$120", "Two months free", "Subscribe")], ver))
print("ok")
