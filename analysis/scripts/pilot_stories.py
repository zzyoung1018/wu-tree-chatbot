"""Pilot v3: rebuild the per-tree stories (树的故事) from the InDesign PDF using typography rules.

Each entry keeps its body as an ordered list of blocks (paragraphs and species subheads) together with the
printed page numbers they sit on, so chunks built from it can cite the exact page of the book.

usage: python -I pilot_stories.py <book.pdf> <first_pdf_page> <last_pdf_page> <out.jsonl>
"""
import json
import re
import sys

import pymupdf

path, first, last, out = sys.argv[1], int(sys.argv[2]), int(sys.argv[3]), sys.argv[4]
doc = pymupdf.open(path)

GREEN, GREY, WHITE = 0x65905B, 0x64706D, 0xFFFFFF
CJK = re.compile(r"[　-〿一-鿿＀-￯“”‘’—…·]")
OFFICIAL_CODE = re.compile(r"(吴中|吴江|狮|沧|网|耦|天|拙|留|虎|常熟|昆|太|张|相|园区|姑苏|平|金|东)\s?(\d{3})")
AGE = re.compile(r"(?:树龄|已有|超过|约)\s*(?:约)?\s*(\d{2,4})\s*(?:余|多)?\s*年")
GONE = re.compile(r"今已?不存|已死亡|因死亡|被.{0,12}除名")
SPEAKER = re.compile(r"^([一-鿿A-Za-z·]{2,14})[：:]")


def classify(s, page_h):
    size, font, color, y = s["size"], s["font"], s["color"], s["bbox"][1]
    if y > page_h - 40 and size <= 8.6:
        return "footer"
    if font.startswith("Times") and size >= 16 and color == WHITE:
        return "entry_no"
    if size >= 13 and color == GREEN:
        return "title"
    if color == GREY and 7.5 <= size <= 8.5:
        return "location"
    if color == GREEN and 8 <= size <= 9:
        return "subhead"
    if size <= 7.5:
        return "small"          # caption or sidebar, decided later by block size
    return "body"


def columns(page):
    """Yield (lines, column x0, printed page) for each text column (2 per printed page) in reading order."""
    W, H = page.rect.width, page.rect.height
    halves = [(0, W)] if W < 500 else [(0, W / 2), (W / 2, W)]
    spans = [s for b in page.get_text("dict")["blocks"] if b["type"] == 0
             for l in b["lines"] for s in l["spans"] if s["text"].strip()]
    for half, (hx0, hx1) in enumerate(halves):
        printed = 2 * page.number + half     # matches the footers: PDF p2 -> 002/003, p38 -> 074/075, p189 -> 376
        mid = (hx0 + hx1) / 2
        for cx0, cx1 in ((hx0, mid), (mid, hx1)):
            cs = [s for s in spans if cx0 <= s["bbox"][0] < cx1]
            for s in cs:
                s["kind"] = classify(s, H)
                s["yc"] = (s["bbox"][1] + s["bbox"][3]) / 2
            cs.sort(key=lambda s: (s["yc"], s["bbox"][0]))
            lines = []
            for s in cs:   # cluster by vertical centre; inline Garamond digits sit ~1pt off the CJK baseline
                if lines and abs(lines[-1]["yc"] - s["yc"]) < 0.45 * min(lines[-1]["size"], s["size"]) \
                        and lines[-1]["kind"] == s["kind"]:
                    lines[-1]["spans"].append(s)
                else:
                    lines.append({"yc": s["yc"], "size": s["size"], "kind": s["kind"], "spans": [s]})
            for ln in lines:
                ln["spans"].sort(key=lambda s: s["bbox"][0])
                ln["text"] = "".join(s["text"] for s in ln["spans"]).strip()
                ln["x0"] = ln["spans"][0]["bbox"][0]
                ln["top"] = min(s["bbox"][1] for s in ln["spans"])
            # header cluster: the grey location line sits a few pt above the number circle
            for i, ln in enumerate(lines):
                if ln["kind"] == "location":
                    for j in range(i + 1, min(i + 4, len(lines))):
                        if lines[j]["kind"] == "entry_no" and lines[j]["top"] - ln["top"] < 25:
                            lines.insert(i, lines.pop(j))
                            break
            if lines:
                xs = [ln["x0"] for ln in lines if ln["kind"] in ("body", "small")]
                col_x0 = min(xs) if xs else cx0
                yield lines, col_x0, printed


def join(prev, nxt):
    if prev and nxt and not CJK.match(prev[-1]) and not CJK.match(nxt[0]):
        return prev + " " + nxt
    return prev + nxt


def add_line(blocks, ln, col_x0, page):
    """Append a line; a first-line indent (or a preceding subhead) starts a new paragraph."""
    indent = ln["x0"] - col_x0 > 8
    if indent or not blocks or blocks[-1]["type"] != "para":
        blocks.append({"type": "para", "text": ln["text"], "printed_pages": [page]})
    else:
        blocks[-1]["text"] = join(blocks[-1]["text"], ln["text"])
        if page not in blocks[-1]["printed_pages"]:
            blocks[-1]["printed_pages"].append(page)


def flush(block, col_x0, page_h, first_in_column, page):
    """Store a run of small-type lines as caption or sidebar; merge sidebars that continue across columns."""
    global open_tail
    if cur is None or not block:
        return
    paras = []
    for ln in block:
        add_line(paras, ln, col_x0, page)
    text = "\n".join(p["text"] for p in paras)
    if first_in_column and open_tail is not None and open_tail[0] is cur:
        _, kind, idx = open_tail
        prev = cur[kind].pop(idx)
        continues_para = block[0]["x0"] - col_x0 <= 8
        prev["text"] = join(prev["text"], text) if continues_para else prev["text"] + "\n" + text
        if page not in prev["printed_pages"]:
            prev["printed_pages"].append(page)
        cur["sidebars"].append(prev)
        kind = "sidebars"
    else:
        kind = "sidebars" if len(block) >= 3 else "captions"
        cur[kind].append({"text": text, "printed_pages": [page]})
    ends_low = block[-1]["yc"] > page_h - 70
    open_tail = (cur, kind, len(cur[kind]) - 1) if ends_low else None


entries, cur, orphan, open_tail = [], None, [], None
for pno in range(first, last + 1):
    page = doc[pno - 1]
    H = page.rect.height
    for lines, col_x0, printed in columns(page):
        small_block, last_small_y, seen = [], None, False
        for ln in lines:
            k, t = ln["kind"], ln["text"]
            if k == "footer":
                continue
            if k == "small":
                if last_small_y is not None and ln["yc"] - last_small_y > 2.2 * ln["size"]:
                    flush(small_block, col_x0, H, not seen, printed)
                    small_block, seen = [], True
                small_block.append(ln)
                last_small_y = ln["yc"]
                continue
            if small_block:
                flush(small_block, col_x0, H, not seen, printed)
                small_block = []
            open_tail = None
            seen = True
            last_small_y = None
            if k == "entry_no":
                cur = {"entry_no": int(re.sub(r"\D", "", t) or 0), "location": "", "title": "",
                       "blocks": [], "captions": [], "sidebars": [], "subheads": [],
                       "pdf_pages": [pno], "printed_pages": [printed]}
                entries.append(cur)
                continue
            if cur is None:
                orphan.append(t)
                continue
            if pno not in cur["pdf_pages"]:
                cur["pdf_pages"].append(pno)
            if printed not in cur["printed_pages"]:
                cur["printed_pages"].append(printed)
            if k == "location":
                cur["location"] = join(cur["location"], t)
            elif k == "title":
                cur["title"] = join(cur["title"], t)
            elif k == "subhead":
                cur["subheads"].append(t)
                cur["blocks"].append({"type": "subhead", "text": t, "printed_pages": [printed]})
            else:
                add_line(cur["blocks"], ln, col_x0, printed)
        if small_block:
            flush(small_block, col_x0, H, not seen, printed)

DIGIT_GAP = [(re.compile(r"(?<=\d)[  ]+(?=[一-鿿])"), ""),
             (re.compile(r"(?<=[一-鿿（])[  ]+(?=\d)"), "")]
ROLE = re.compile(r"馆员|法师|志愿者|工作者|受访人|阿姨|老|先生|女士|师傅|经理")


def norm(t):
    for rx, rep in DIGIT_GAP:
        t = rx.sub(rep, t)
    return t


with open(out, "w", encoding="utf-8") as f:
    for e in entries:
        for item in e["blocks"] + e["sidebars"] + e["captions"]:
            item["text"] = norm(item["text"])
            item["printed_pages"].sort()
        e["printed_pages"].sort()
        e["text"] = "\n".join(b["text"] for b in e["blocks"] if b["type"] == "para")
        e["location"] = norm(e["location"])
        alltext = e["text"] + "\n" + "\n".join(s["text"] for s in e["sidebars"])
        e["official_codes"] = sorted({a + b for a, b in OFFICIAL_CODE.findall(alltext)})
        e["ages_mentioned"] = sorted({int(a) for a in AGE.findall(e["text"])})
        e["status_gone"] = bool(GONE.search(e["text"] + e["location"] + e["title"]))
        labels = [m.group(1) for sb in e["sidebars"] for p in sb["text"].split("\n") if (m := SPEAKER.match(p))]
        e["speakers"] = sorted({s for s in labels if ROLE.search(s) or labels.count(s) >= 2})
        e["chars"] = len(e["text"])
        e["sidebar_chars"] = sum(len(s["text"]) for s in e["sidebars"])
        f.write(json.dumps(e, ensure_ascii=False) + "\n")

nums = [e["entry_no"] for e in entries]
print(f"entries: {len(entries)} | unique numbers: {len(set(nums))} | missing: {sorted(set(range(1, 106)) - set(nums))}")
print("orphan lines before first entry:", orphan[:5])
print("no location:", [e["entry_no"] for e in entries if not e["location"]])
print("body chars:", sum(e["chars"] for e in entries), "| sidebar chars:", sum(e["sidebar_chars"] for e in entries),
      "| captions:", sum(len(e["captions"]) for e in entries), "| sidebars:", sum(len(e["sidebars"]) for e in entries))
print("entries with official codes in text:", sum(1 for e in entries if e["official_codes"]),
      "| marked gone:", [e["entry_no"] for e in entries if e["status_gone"]])
print("speakers seen in sidebars:", sorted({s for e in entries for s in e["speakers"]}))
for e in entries:
    print(f'{e["entry_no"]:>3} {e["location"][:18]:<18} {e["title"][:10]:<10} body={e["chars"]:>4} '
          f'side={e["sidebar_chars"]:>4} cap={len(e["captions"])} pp={e["printed_pages"]} '
          f'codes={",".join(e["official_codes"])[:24]}')
