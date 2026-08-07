#!/usr/bin/env python3
"""Render docs/FABRICATION_PROTOCOL.md into a branded, standalone HTML page.

Uses the same QSSC design system as the dashboard and the presentation, with
the hexagon logo inlined and print CSS so headless Chromium can export a clean
PDF. Run:

    python3 tools/build_protocol_html.py
"""
import sys
from pathlib import Path

import markdown

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "docs" / "FABRICATION_PROTOCOL.md"
OUT = ROOT / "docs" / "protocol.html"

LOGO = """<svg viewBox="0 0 64 64" width="56" height="56" xmlns="http://www.w3.org/2000/svg">
  <defs><linearGradient id="lg" x1="0" y1="0" x2="1" y2="1"><stop offset="0" stop-color="#22d3ee"/><stop offset="1" stop-color="#6366f1"/></linearGradient></defs>
  <rect width="64" height="64" rx="14" fill="#0b1020"/>
  <g stroke="url(#lg)" stroke-width="2.4" fill="none" stroke-linejoin="round"><polygon points="32,8 52,20 52,44 32,56 12,44 12,20"/></g>
  <g fill="url(#lg)"><circle cx="32" cy="8" r="3.2"/><circle cx="52" cy="20" r="3.2"/><circle cx="52" cy="44" r="3.2"/><circle cx="32" cy="56" r="3.2"/><circle cx="12" cy="44" r="3.2"/><circle cx="12" cy="20" r="3.2"/></g>
  <circle cx="32" cy="32" r="9" fill="none" stroke="#e0e7ff" stroke-width="1.4" opacity="0.7"/><circle cx="32" cy="32" r="4" fill="#f472b6"/>
</svg>"""

CSS = """
:root{--bg:#0b1020;--bg2:#121931;--panel:#141c38;--panel2:#1a2444;--border:#263056;--text:#e6ecff;--muted:#94a3c4;--dim:#6b78a0;--brand:#22d3ee;--brand2:#6366f1;--accent:#f472b6;--ok:#34d399;--warn:#fbbf24;--grad:linear-gradient(135deg,#67e8f9,#a5b4fc 52%,#f472b6);--head:"Space Grotesk",system-ui,sans-serif;--body:"Inter",system-ui,sans-serif;--mono:"JetBrains Mono",ui-monospace,monospace}
*{box-sizing:border-box}
html{scroll-behavior:smooth}
body{margin:0;background:radial-gradient(1200px 600px at 80% -10%,rgba(99,102,241,.16),transparent 60%),radial-gradient(900px 500px at 0% 100%,rgba(34,211,238,.10),transparent 55%),var(--bg);color:var(--text);font-family:var(--body);font-size:15.5px;line-height:1.7;-webkit-font-smoothing:antialiased}
.wrap{max-width:880px;margin:0 auto;padding:0 44px 90px}
header.brand{display:flex;align-items:center;gap:16px;padding:34px 0 18px;border-bottom:1px solid #16203f;margin-bottom:8px}
header.brand .t h1{font-family:var(--head);font-weight:600;font-size:25px;margin:0;letter-spacing:-.01em}
header.brand .t h1 .grad{color:#67e8f9;text-decoration:none}
header.brand .t .sub{color:var(--muted);font-size:13px;margin-top:3px}
header.brand .meta{margin-left:auto;text-align:right;font-family:var(--mono);font-size:11.5px;color:var(--dim);line-height:1.6}
h1,h2,h3,h4{font-family:var(--head);font-weight:600;letter-spacing:-.01em;scroll-margin-top:20px}
h2{font-size:23px;margin:42px 0 14px;padding-bottom:8px;border-bottom:1px solid #1a2444}
h3{font-size:17px;margin:26px 0 10px;color:#cfe0ff}
h4{font-size:14.5px;margin:18px 0 8px;color:var(--brand)}
p{margin:10px 0}
a{color:var(--brand)}
strong{color:#fff}
code{font-family:var(--mono);font-size:13px;background:#0a1128;border:1px solid var(--border);border-radius:5px;padding:1px 6px;color:#9be7ff}
pre{background:#0a1128;border:1px solid var(--border);border-radius:11px;padding:14px 16px;overflow-x:auto;font-size:12.5px;line-height:1.55}
pre code{background:none;border:none;padding:0;color:#cfe0ff;font-size:12.5px}
blockquote{margin:16px 0;padding:12px 16px;border-left:3px solid var(--accent);background:rgba(244,114,182,.07);border-radius:0 9px 9px 0;color:#f3c9dc}
blockquote strong{color:#fff}
ul,ol{padding-left:1.3em}
li{margin:5px 0}
hr{border:none;border-top:1px solid #1a2444;margin:30px 0}
table{width:100%;border-collapse:collapse;font-size:13.5px;margin:14px 0}
th,td{padding:9px 12px;text-align:left;border-bottom:1px solid #1a2444;vertical-align:top}
th{color:var(--muted);font-weight:500;font-size:12px;text-transform:uppercase;letter-spacing:.05em;background:var(--panel)}
td code{font-size:12px}
.toc{background:linear-gradient(180deg,var(--panel),var(--bg2));border:1px solid var(--border);border-radius:14px;padding:18px 22px;margin:18px 0 8px}
.toc h2{border:none;margin:0 0 10px;font-size:14px;text-transform:uppercase;letter-spacing:.08em;color:var(--muted)}
.toc ul{list-style:none;padding-left:0;margin:0}
.toc li{margin:5px 0}
.toc a{font-size:14px}
.toc ul ul{padding-left:18px;margin-top:4px}
.toc ul ul a{font-size:13px;color:var(--dim)}
.foot{margin-top:40px;padding-top:18px;border-top:1px solid #16203f;color:var(--dim);font-size:12px;text-align:center}
/* print / PDF — keep the dark theme (match the presentation PDF), no white frame */
@media print{
  @page{margin:0}
  *{-webkit-print-color-adjust:exact;print-color-adjust:exact}
  html,body{background:var(--bg);color:var(--text);font-size:11pt}
  .wrap{max-width:none;padding:0 56px}
  header.brand{break-after:avoid}
  h2,h3,h4{break-after:avoid}
  pre,blockquote,table,.toc,code,header.brand{break-inside:avoid}
  .toc{break-before:avoid}
}
"""

TEMPLATE = """<!doctype html>
<html lang="en">
<head>
<meta charset="UTF-8" />
<meta name="viewport" content="width=device-width, initial-scale=1.0" />
<title>QSSC — Fabrication Protocol</title>
<link rel="preconnect" href="https://fonts.googleapis.com" />
<link rel="preconnect" href="https://fonts.gstatic.com" crossorigin />
<link href="https://fonts.googleapis.com/css2?family=Space+Grotesk:wght@400;500;600;700&family=Inter:wght@400;500;600&family=JetBrains+Mono:wght@400;500;700&display=swap" rel="stylesheet" />
<style>{css}</style>
</head>
<body>
<div class="wrap">
  <header class="brand">
    {logo}
    <div class="t">
      <h1><span class="grad">Quantum Solid State Chip</span> — Fabrication Protocol</h1>
      <div class="sub">Room-temperature hBN spin-defect (V_B⁻) quantum photonic chip · laboratory build, validated against published studies</div>
    </div>
    <div class="meta">QSSC v2.0.0<br/>Document v1.0<br/>Markdown source: FABRICATION_PROTOCOL.md</div>
  </header>
  {toc}
  {body}
  <div class="foot">QSSC — Quantum Solid State Chip (room temperature) · fabrication protocol · branded document · {bytes} source bytes</div>
</div>
</body>
</html>
"""


def main():
    md = SRC.read_text(encoding="utf-8")
    # table of contents from the H2/H3 headings
    toc_html = markdown.markdown(
        "\n".join(l for l in md.splitlines()
                  if l.startswith("## ") or l.startswith("### ")),
        extensions=["toc"],
    )
    body_html = markdown.markdown(
        md,
        extensions=["fenced_code", "tables", "sane_lists", "attr_list", "toc"],
        extension_configs={"toc": {"toc_depth": "2-3"}},
    )
    # Style the generated TOC list as our .toc card
    toc_html = toc_html.replace('<div class="toc">', '<div class="toc" id="toc">')
    html = TEMPLATE.format(
        css=CSS, logo=LOGO, toc=toc_html, body=body_html, bytes=len(md),
    )
    OUT.write_text(html, encoding="utf-8")
    print(f"wrote {OUT}  ({len(html)} bytes)")


if __name__ == "__main__":
    sys.exit(main())