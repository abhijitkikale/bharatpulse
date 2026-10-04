"""One-off patch: insert the Risk Monitor tab (nav button, section, JS) into template.html."""
from pathlib import Path

root = Path(__file__).resolve().parent.parent
p = root / "template.html"
t = p.read_text(encoding="utf-8").replace("\r\n", "\n")
section = (root / "parts" / "risk_section.html").read_text(encoding="utf-8")
js = (root / "parts" / "risk.js").read_text(encoding="utf-8")


def rep(old, new):
    global t
    assert t.count(old) == 1, (t.count(old), old[:70])
    t = t.replace(old, new)


rep('<button data-tab="flows">Flows &amp; Valuation</button>',
    '<button data-tab="flows">Flows &amp; Valuation</button>\n  <button data-tab="risk">Risk Monitor</button>')
rep("</main>", section + "</main>")
rep("/* ---------- status footer ---------- */", js + "\n/* ---------- status footer ---------- */")
rep("flows:()=>{renderFlows();renderVal()}})", "flows:()=>{renderFlows();renderVal()},risk:renderRisk})")
p.write_text(t, encoding="utf-8", newline="\n")
print("patched")
