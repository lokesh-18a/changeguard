import html
W, H = 1800, 1075
C = lambda i: 410 + i * 188           # column x
CW = 160
S = {"ai": ("#e8f0fe", "#1a73e8", 2), "code": ("#ffffff", "#5f6368", 1.3), "human": ("#e6f4ea", "#188038", 2),
     "store": ("#f1f3f4", "#5f6368", 1.3), "ext": ("#fef7e0", "#b06000", 1.5)}
out = []
def box(x, y, w, h, kind, name, lines=(), rx=8, cyl=False):
    f, s, sw = S[kind]
    if cyl:
        out.append(f'<path d="M{x} {y+10} a{w/2} 10 0 0 0 {w} 0 a{w/2} 10 0 0 0 {-w} 0 v{h-20} a{w/2} 10 0 0 0 {w} 0 v{-(h-20)}" fill="{f}" stroke="{s}" stroke-width="{sw}"/>')
        out.append(f'<path d="M{x} {y+10} a{w/2} 10 0 0 0 {w} 0" fill="none" stroke="{s}" stroke-width="{sw}"/>')
    else:
        out.append(f'<rect x="{x}" y="{y}" width="{w}" height="{h}" rx="{rx}" fill="{f}" stroke="{s}" stroke-width="{sw}"/>')
    n = 1 + len(lines); top = y + h / 2 - (n - 1) * 8 + 5 + (4 if cyl else 0)
    out.append(f'<text x="{x+w/2}" y="{top}" class="n" text-anchor="middle">{html.escape(name)}</text>')
    for i, l in enumerate(lines):
        out.append(f'<text x="{x+w/2}" y="{top + 18 + i*16}" class="b" text-anchor="middle">{html.escape(l)}</text>')
def arrow(pts, dash=False, label=None, lx=None, ly=None, anchor="middle"):
    d = "M" + " L".join(f"{a} {b}" for a, b in pts)
    out.append(f'<path d="{d}" fill="none" stroke="#5f6368" stroke-width="1.4" {"stroke-dasharray=\"5 4\"" if dash else ""} marker-end="url(#a)"/>')
    if label:
        out.append(f'<text x="{lx}" y="{ly}" class="l" text-anchor="{anchor}">{html.escape(label)}</text>')
def panel(x, y, w, h, title, sub=None, dashed=False, size=15):
    out.append(f'<rect x="{x}" y="{y}" width="{w}" height="{h}" rx="12" fill="none" stroke="#9aa0a6" stroke-width="1.2" {"stroke-dasharray=\"6 5\"" if dashed else ""}/>')
    out.append(f'<text x="{x+14}" y="{y+24}" class="p" style="font-size:{size}px">{html.escape(title)}</text>')
    if sub: out.append(f'<text x="{x+14}" y="{y+42}" class="b" text-anchor="start">{html.escape(sub)}</text>')

# title + legend
out.append('<text x="20" y="34" class="t">ChangeGuard architecture: AI reads and writes, code decides, a person signs off</text>')
lx = 1090
for i, (k, lab) in enumerate([("ai", "AI agent (Amazon Nova)"), ("code", "Deterministic code"), ("human", "Person"), ("store", "Data store"), ("ext", "External")]):
    f, s, sw = S[k]; xx = lx + [0, 190, 340, 420, 520][i]
    out.append(f'<rect x="{xx}" y="22" width="14" height="14" rx="3" fill="{f}" stroke="{s}" stroke-width="{sw}"/><text x="{xx+20}" y="34" class="b" text-anchor="start">{lab}</text>')

# columns
panel(15, 55, 235, 1005, "INPUT"); panel(265, 55, 1275, 1005, "PROCESSING"); panel(1555, 55, 230, 1005, "OUTPUT")

# ---- Lane A
panel(395, 90, 1130, 215, "A · Knowledge build", "once per product, from real insurer and regulator documents", dashed=True, size=14)
ya, ha = 175, 90
box(C(0), ya, CW, ha, "code", "Ingestor", ["PDF to text,", "page-anchored"])
box(C(1), ya, CW, ha, "code", "Segmenter", ["clauses and", "tables"])
box(C(2), ya, CW, ha, "ai", "Extractor", ["clause to rules,", "with verbatim quote"])
box(C(3), ya, CW, ha, "code", "Grounding verifier", ["quote exists, numbers", "match, schema valid"])
box(C(4), ya, CW, ha, "code", "Reconciler", ["compares insurers,", "flags contradictions"])
box(C(5), ya, CW, ha, "human", "Analyst review", ["approves the", "rule catalog"])
for i in range(5):
    arrow([(C(i) + CW, ya + ha/2), (C(i + 1), ya + ha/2)])
arrow([(C(3) + CW/2, ya + ha), (C(3) + CW/2, 285), (C(2) + CW/2, 285), (C(2) + CW/2, ya + ha + 2)], dash=True,
      label="rejected with reason: 1 retry", lx=(C(2) + C(3)) / 2 + CW/2, ly=279)

# ---- knowledge stores
ys, hs = 325, 78
box(C(1), ys, CW, hs, "store", "Clause index", ["SQLite FTS5"], cyl=True)
box(C(2), ys, CW * 2 + 28, hs, "store", "Rule catalog", ["versioned, every rule with its source quote"], cyl=True)
arrow([(C(1) + CW/2, ya + ha), (C(1) + CW/2, ys)])
arrow([(C(5) + CW/2, ya + ha), (C(5) + CW/2, ys + hs/2), (C(2) + CW*2 + 28, ys + hs/2)])

# ---- Lane B
panel(395, 425, 1130, 360, "B · Change pipeline", "per change request, about a minute", dashed=True, size=14)
y1, y2, hb = 495, 650, 92
box(C(0), y1, CW, hb, "code", "FastAPI + MCP", ["typed Pydantic", "contracts"])
box(C(1), y1, CW, hb, "ai", "Planner", ["request to a", "structured change"])
box(C(2), y1, CW, hb, "code", "Retriever", ["rules and the source", "clauses to cite"])
box(C(3), y1, CW, hb, "code", "Impact analyst", ["graph walk, a path", "for every rule"])
box(C(4), y1, CW, hb, "code", "Conflict checker", ["generic checks for", "hidden gaps"])
box(C(5), y1, CW, hb, "code", "Test designer", ["boundary and", "equivalence cases"])
for i in range(5):
    arrow([(C(i) + CW, y1 + hb/2), (C(i + 1), y1 + hb/2)])
box(C(5), y2, CW, hb, "code", "Oracle and runner", ["exact expected", "versus actual"])
box(C(4), y2, CW, hb, "code", "Mutation auditor", ["how many planted", "bugs the tests catch"])
box(C(3), y2, CW, hb, "ai", "Writer", ["verdict and report", "with citations"])
box(C(2), y2, CW, hb, "code", "Critic", ["every claim cites a real", "ID, every number matches"])
arrow([(C(5) + CW/2, y1 + hb), (C(5) + CW/2, y2)])
for i in (5, 4, 3):
    arrow([(C(i), y2 + hb/2 - 12), (C(i - 1) + CW, y2 + hb/2 - 12)])
arrow([(C(2) + CW, y2 + hb/2 + 14), (C(3), y2 + hb/2 + 14)], dash=True)
out.append(f'<text x="{C(2)+CW+14}" y="{y2+hb+18}" class="l" text-anchor="middle">critic fails: rewrite</text>')
# store reads
arrow([(C(1) + CW/2, ys + hs), (C(1) + CW/2, 455), (C(2) + 50, 455), (C(2) + 50, y1)])
arrow([(C(2) + CW + 14, ys + hs), (C(2) + CW + 14, 470), (C(2) + 110, 470), (C(2) + 110, y1)])
# fallback note
out.append(f'<text x="{C(1)+CW/2}" y="{y2+30}" class="l" text-anchor="middle">invalid change:</text><text x="{C(1)+CW/2}" y="{y2+46}" class="l" text-anchor="middle">model told why, retries once,</text><text x="{C(1)+CW/2}" y="{y2+62}" class="l" text-anchor="middle">then rule-based fallback</text>')
arrow([(C(1) + CW/2, y2 + 12), (C(1) + CW/2, y1 + hb + 2)], dash=True)

# ---- bottom stores
yb, hbm = 905, 82
box(C(0), yb, CW * 2 + 28, hbm, "store", "SQLite", ["runs, audit log with hashes, token ledger"], cyl=True)
box(C(4), yb, CW * 2 + 28, hbm, "ext", "Amazon Bedrock", ["Amazon Nova, called only by the", "3 blue agents; swappable provider"])
arrow([(C(0) + CW/2, y1 + hb), (C(0) + CW/2, yb)], label="every step logged", lx=C(0) + CW/2 + 8, ly=850, anchor="start")

# ---- INPUT
box(30, 190, 205, 100, "ext", "Real source documents", ["policy and add-on wordings,", "IRDAI circulars, claim forms"])
box(30, 400, 205, 58, "human", "Flarre delivery analyst", rx=29)
box(30, 505, 205, 70, "code", "Web UI", ["Streamlit"])
box(30, 610, 205, 70, "code", "External agents", ["any MCP client"])
arrow([(235, 240), (C(0), 240)])
arrow([(132, 458), (132, 505)])
arrow([(235, 528), (C(0), 528)], label="REST", lx=322, ly=520)
arrow([(235, 645), (330, 645), (330, 565), (C(0), 565)], label="MCP", lx=340, ly=602, anchor="start")

# ---- OUTPUT
box(1570, 640, 200, 70, "code", "Release verdict", ["with cited report"])
box(1570, 735, 200, 80, "code", "Evidence pack", ["Excel: impact, conflicts,", "tests, traceability"])
box(1570, 905, 200, 82, "store", "Audit trail", ["hashed and", "reproducible"])
box(1570, 840, 200, 42, "human", "Analyst signs off", rx=21)
arrow([(C(2) + CW/2, y2 + hb), (C(2) + CW/2, 805), (1540, 805), (1540, 675), (1570, 675)])
arrow([(1540, 775), (1570, 775)])
arrow([(C(0) + CW + 14, yb + hbm), (C(0) + CW + 14, 1035), (1670, 1035), (1670, yb + hbm)])
arrow([(1670, 815), (1670, 840)])
out.append(f'<path d="M{C(0)+CW*2+28+60} {yb+hbm/2} L{C(0)+CW*2+28+60} {yb+hbm/2}" />')

svg = f'''<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {W} {H}" width="{W}" height="{H}">
<defs><marker id="a" viewBox="0 0 10 10" refX="9" refY="5" markerWidth="7" markerHeight="7" orient="auto-start-reverse"><path d="M0 0L10 5L0 10z" fill="#5f6368"/></marker></defs>
<style>text{{font-family:Inter,Arial,sans-serif;fill:#202124}} .n{{font-size:14px;font-weight:600}} .b{{font-size:12px;fill:#3c4043}} .l{{font-size:11.5px;fill:#5f6368}} .p{{font-weight:700;letter-spacing:.5px;fill:#3c4043}} .t{{font-size:20px;font-weight:700}}</style>
<rect width="{W}" height="{H}" fill="#ffffff"/>
{''.join(out)}</svg>'''
open("arch.svg", "w").write(svg)
open("arch.html", "w").write(f"<html><body style='margin:0'>{svg}</body></html>")
