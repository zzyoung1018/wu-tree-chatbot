"""Structural scan of a PDF: metadata, outline, per-page text/image/font stats.

usage: python -I pdf_scan.py <file.pdf> <out.json>
"""
import json
import re
import sys
import unicodedata
from collections import Counter

import pymupdf

path, out = sys.argv[1], sys.argv[2]
doc = pymupdf.open(path)
print("pages:", doc.page_count, "| encrypted:", doc.is_encrypted, "| pdf version:", doc.metadata.get("format"))
print("metadata:", {k: v for k, v in doc.metadata.items() if v})
toc = doc.get_toc(simple=True)
print("outline entries:", len(toc))
for lvl, title, pno in toc[:80]:
    print("  " * (lvl - 1) + f"- {title}  (p{pno})")

CJK = re.compile(r"[一-鿿㐀-䶿]")
sizes = Counter()
fonts = Counter()
pages = []
for i, page in enumerate(doc):
    r = page.rect
    sizes[(round(r.width), round(r.height))] += 1
    text = page.get_text("text")
    d = page.get_text("dict")
    blocks = [b for b in d["blocks"] if b["type"] == 0]
    img_blocks = [b for b in d["blocks"] if b["type"] == 1]
    spans = [s for b in blocks for l in b["lines"] for s in l["spans"]]
    for s in spans:
        fonts[s["font"]] += len(s["text"])
    vertical = sum(1 for b in blocks for l in b["lines"] if abs(l["dir"][1]) > 0.9)
    imgs = page.get_images(full=True)
    img_area = 0.0
    for b in img_blocks:
        x0, y0, x1, y1 = b["bbox"]
        img_area += max(0, x1 - x0) * max(0, y1 - y0)
    n = len(text.strip())
    pua = sum(1 for c in text if unicodedata.category(c) == "Co")
    repl = text.count("�")
    cjk = len(CJK.findall(text))
    pages.append({
        "page": i + 1, "w": round(r.width), "h": round(r.height), "chars": n, "cjk": cjk,
        "text_blocks": len(blocks), "vertical_lines": vertical, "images": len(imgs),
        "img_cover": round(img_area / (r.width * r.height), 2) if r.width else 0,
        "pua": pua, "replacement": repl,
        "font_sizes": sorted({round(s["size"]) for s in spans}),
        "head": re.sub(r"\s+", " ", text.strip())[:90],
    })

print("page sizes (pt):", dict(sizes))
tot = sum(p["chars"] for p in pages)
print(f"total extracted chars: {tot}, CJK: {sum(p['cjk'] for p in pages)}")
print("pages with <20 chars:", sum(1 for p in pages if p["chars"] < 20))
print("pages with PUA/replacement chars:", sum(1 for p in pages if p["pua"] or p["replacement"]))
print("pages with vertical text lines:", sum(1 for p in pages if p["vertical_lines"]))
print("top fonts by chars:", fonts.most_common(15))
print()
for p in pages:
    print(f"p{p['page']:>3} {p['w']}x{p['h']} chars={p['chars']:>5} cjk={p['cjk']:>5} blk={p['text_blocks']:>3} "
          f"img={p['images']:>2} cover={p['img_cover']:.2f} vert={p['vertical_lines']} pua={p['pua']} "
          f"fs={p['font_sizes'][:8]} | {p['head'][:60]}")
json.dump({"toc": toc, "pages": pages, "fonts": fonts.most_common(), "metadata": doc.metadata},
          open(out, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
