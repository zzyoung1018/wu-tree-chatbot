"""Build RAG chunks (schema/chunk.schema.json) from the pilot story entries written by pilot_stories.py.

usage: python -I build_story_chunks.py <stories.jsonl> <out_chunks.jsonl>

Cutting rules (RAG切块要素设计.md §4):
  - an entry body is split at species subheads first, then at paragraph boundaries when a section exceeds 600 chars;
  - every sidebar becomes its own chunks; interviews are cut only before a question from the museum side;
  - captions are attached to the first body chunk of their entry instead of being indexed on their own.
"""
import hashlib
import json
import re
import sys

src, out = sys.argv[1], sys.argv[2]

SOURCE_ID, BOOK, AS_OF = "SRC-SHBL", "《树碑立传》", "2024-08"
PIPELINE = "pilot_stories@3+build_story_chunks@1"
MAX_WHOLE, TARGET, HARD_MAX, MIN_LEN = 600, 400, 900, 80

# district dividers in the photo index (PDF p6-37): first entry number of each district
DISTRICTS = [(1, "吴中区"), (30, "姑苏区"), (56, "吴江区"), (69, "虎丘区（高新区）"), (72, "相城区"),
             (74, "工业园区"), (75, "昆山市"), (85, "常熟市"), (101, "太仓市"), (105, "张家港市")]
# entries already matched to registry/trees.csv and sites.csv (数据分析与清理设计方案 §3.2)
LINKED = {
    18: (["stm-yuanbai"], ["S02"], ["吴中186", "吴中187", "吴中188", "吴中189"], "cross_ref",
         "地点、树种与 stm-yuanbai 一致；编号见古树与艺术章节《推演》（印刷页 174），四棵个体的对应待确认"),
    21: (["sbs-shinan"], ["S03"], ["吴中203"], "manual", "地点、树种与 sbs-shinan 一致；编号来自现场官方铭牌"),
    55: (["dlzb-xuesong"], ["S01"], [], "manual", "地点、树种与 dlzb-xuesong 一致；书中称尚未列入古树名录"),
}
# known contradictions until conflicts.csv exists: (conflict id, entry, pattern that marks the chunk)
CONFLICTS = [("cf-xuesong-planted", 55, r"1924"),
             ("cf-shinan-age", 21, r"800年前|元朝"),
             ("cf-hanshan-arrival", 21, r"憨山")]
# private people named in the sidebars; 张冀(?!牖) keeps the historical 张冀牖 out
PEOPLE = re.compile(r"罗星琳|周缘|王铭钰|张冀(?!牖)|黄林法|黄老|陈平|施雪荣|陈爱娣|萍萍阿姨")
SYNONYM = {"樟树": "香樟"}
REQUIRED_LISTS = {"tree_ids", "site_ids", "species"}   # kept even when empty; other empty fields are dropped

OFFICIAL_CODE = re.compile(r"(吴中|吴江|狮|沧|网|耦|天|拙|留|虎|常熟|昆|太|张|相|园区|姑苏|平|金|东)\s?(\d{3})")
LEGEND = re.compile(r"相传|传说|据传|传为|传闻|民间(?:盛|流)传")
RECORDED = re.compile(r"记载|记述|《[^》]{1,24}》(?:中|里)?(?:记|载|称|说|云|写)|志载|旧志")
HEDGED = re.compile(r"并无(?:明确)?依据|一说|或说|待考|存疑|无从考证|已不可考|说法不一")
RED = re.compile(r"中共|共产党|党支部|革命|烈士|红军|新四军|抗日")
RELIGION = re.compile(r"寺|庵|禅|法师|佛|道观|道士|观音|菩萨|僧|香客")
SPEAKER = re.compile(r"^([一-鿿A-Za-z·]{2,14})[：:]")
ROLE = re.compile(r"馆员|法师|志愿者|工作者|受访人|采访人|阿姨|老|先生|女士|师傅|经理")
ASKER = re.compile(r"馆员|志愿者|采访人")
INTERVIEW_HEAD = re.compile(r"受访人：(.+?)采访人：(.+?)时间：([\d.]+)")
DATE = re.compile(r"(\d{4})[年.](\d{1,2})[月.](\d{1,2})")
SENTENCE_END = re.compile(r"(?<=[。！？])")


def district(n):
    return [d for start, d in DISTRICTS if n >= start][-1]


def names(*labels):
    """Species names from titles and subheads such as '罗汉松、板栗' or '一棵枫杨的生与死'."""
    found = []
    for label in labels:
        m = re.match(r"一棵(.+?)的", label)
        for s in ([m.group(1)] if m else re.split(r"[、，,\s]+", label)):
            s = SYNONYM.get(s, s)
            if s and len(s) <= 5 and s not in found:
                found.append(s)
    return found


def iso(m):
    return f"{m.group(1)}-{int(m.group(2)):02d}-{int(m.group(3)):02d}"


def sections(entry):
    """Body paragraphs grouped under their species subhead; tiny sections join the next one."""
    secs = []
    for b in entry["blocks"]:
        if b["type"] == "subhead":
            secs.append([b["text"], []])
        elif secs:
            secs[-1][1].append(b)
        else:
            secs.append([None, [b]])
    secs = [s for s in secs if s[1]]
    merged = []
    for head, paras in secs:
        if merged and sum(len(p["text"]) for p in merged[-1][1]) < MIN_LEN:
            merged[-1][0] = "、".join(h for h in (merged[-1][0], head) if h)
            merged[-1][1] += paras
        else:
            merged.append([head, paras])
    if len(merged) > 1 and sum(len(p["text"]) for p in merged[-1][1]) < MIN_LEN:
        head, paras = merged.pop()
        merged[-1][0] = "、".join(h for h in (merged[-1][0], head) if h)
        merged[-1][1] += paras
    return merged


def pack(paras):
    """Keep a section whole when short; otherwise cut at paragraph ends into parts of about TARGET chars."""
    pieces = []
    for p in paras:   # a paragraph that is too long on its own is cut at sentence ends
        if len(p["text"]) <= HARD_MAX:
            pieces.append(p)
            continue
        buf = ""
        for s in SENTENCE_END.split(p["text"]):
            if buf and len(buf) + len(s) > TARGET:
                pieces.append({"text": buf, "printed_pages": p["printed_pages"]})
                buf = ""
            buf += s
        if buf:
            pieces.append({"text": buf, "printed_pages": p["printed_pages"]})
    total = sum(len(p["text"]) for p in pieces)
    if total <= MAX_WHOLE:
        return [pieces]
    n = max(2, round(total / TARGET))
    goal, groups, size = total / n, [[]], 0
    for p in pieces:
        if groups[-1] and size + len(p["text"]) / 2 > goal and len(groups) < n:
            groups.append([])
            size = 0
        groups[-1].append(p)
        size += len(p["text"])
    return groups


def speakers_of(text):
    labels = [m.group(1) for ln in text.split("\n") if (m := SPEAKER.match(ln))]
    keep = []
    for s in labels:
        if (ROLE.search(s) or labels.count(s) >= 2) and s not in keep:
            keep.append(s)
    return keep


def interview_parts(text):
    """Cut a Q&A sidebar only before a museum-side question, so an answer is never separated from its question."""
    lines = text.split("\n")
    total = len(text)
    if total <= MAX_WHOLE:
        return [text]
    n = max(2, round(total / TARGET))
    goal, groups, size = total / n, [[]], 0
    for ln in lines:
        m = SPEAKER.match(ln)
        asks = bool(m and ASKER.search(m.group(1)))
        if groups[-1] and ((asks and size >= goal * 0.8) or size + len(ln) > HARD_MAX):
            groups.append([])
            size = 0
        groups[-1].append(ln)
        size += len(ln) + 1
    return ["\n".join(g) for g in groups]


def sidebar_kind(text):
    first = text.split("\n")[0]
    if first.startswith("关于") or (len(first) <= 8 and not re.search(r"[，。：]", first)):
        return "background"          # "关于保圣寺：……" or a short title line such as "明善堂"
    if INTERVIEW_HEAD.match(first) or len(speakers_of(text)) >= 2 or (speakers_of(text) and SPEAKER.match(first)):
        return "interview"
    if "志愿者" in text or DATE.search(text):
        return "fieldnote"
    return "background"


def chunk(e, block, n, parts, text, ctype, species, pages, heading, extra=None, more=None):
    no = e["entry_no"]
    unit = f"shbl:story-{no:03d}"
    trees, sites, codes, how, why = LINKED.get(no, ([], [], [], "unlinked", ""))
    mentioned = [a + b for a, b in OFFICIAL_CODE.findall(text + " ".join(f["caption"] for f in (more or {}).get("figures", [])))]
    codes = sorted(set(codes) | set(mentioned))
    header = "｜".join(x for x in (f"{BOOK}树的故事 第{no}条", f"{district(no)} {e['location']}",
                                    "、".join(species) or e["title"], extra) if x)
    flags = [f for f, rx in (("legend", LEGEND), ("recorded", RECORDED), ("hedged", HEDGED)) if rx.search(text)]
    sens = [s for s, hit in (("red_history", RED.search(text)), ("religion", RELIGION.search(text + e["location"])),
                             ("personal_info", ctype == "interview" or PEOPLE.search(text))) if hit]
    c = {
        "chunk_id": f"{unit}:{block}-{n}", "unit_id": unit, "part": n, "parts": parts,
        "source_id": SOURCE_ID, "content_type": ctype,
        "text": text, "context_header": header, "lang": "zh-Hans", "n_chars": len(text),
        "locator": {"section": "树的故事", "entry_no": no, "title": e["title"],
                    "heading_path": ["树的故事", f"{no} {e['location']}·{e['title']}"] + heading,
                    "pdf_pages": sorted({p // 2 + 1 for p in pages}), "printed_pages": pages},
        "tree_ids": trees, "site_ids": sites, "site_name": e["location"], "district": district(no),
        "species": species, "official_codes": codes,
        "link": {"status": how, "evidence": why} if why else {"status": how},
        "source_tier": "B", "as_of": AS_OF, "review_status": "pending",
        "certainty_flags": flags,
        "conflict_ids": [cid for cid, entry, rx in CONFLICTS if entry == no and re.search(rx, text)],
        "sensitivity": sens,
        "rights": {"holder": "吴文化博物馆", "display": "full", "note": "馆方尚未确认可否在 AI 应用中引用原文"},
        "search_terms": [x for x in codes if x not in text],
        "content_hash": "sha256:" + hashlib.sha256(f"{header}\n{text}".encode("utf-8")).hexdigest()[:16],
        "pipeline": PIPELINE,
    }
    c.update(more or {})
    for sub in ("interview", "fieldnote"):   # a dated sidebar reflects its own date, not the book's
        if c.get(sub, {}).get("observed_on"):
            c["as_of"] = c[sub]["observed_on"]
    return {k: v for k, v in c.items() if k in REQUIRED_LISTS or v not in ([], None, "")}


def pages_of(items):
    return sorted({p for it in items for p in it["printed_pages"]})


chunks = []
for line in open(src, encoding="utf-8"):
    e = json.loads(line)
    entry_species = names(e["title"], *e["subheads"])
    figures = [{"caption": c["text"], "printed_page": c["printed_pages"][0]} for c in e["captions"]]
    body = []
    for head, paras in sections(e):
        species = names(head) if head else entry_species
        for group in pack(paras):
            body.append((head, species, group))
    for i, (head, species, group) in enumerate(body, 1):
        text = "\n".join(p["text"] for p in group)
        more = {"figures": figures} if i == 1 and figures else None
        heading = [head] if head and head != e["title"] else []
        chunks.append(chunk(e, "body", i, len(body), text, "story", species, pages_of(group), heading, more=more))
    side_parts = []
    for sb in e["sidebars"]:
        kind = sidebar_kind(sb["text"])
        texts = interview_parts(sb["text"]) if kind == "interview" else ["\n".join(p["text"] for p in g)
                                                                         for g in pack([{"text": t, "printed_pages": sb["printed_pages"]} for t in sb["text"].split("\n")])]
        side_parts += [(kind, t, sb["printed_pages"], sb["text"]) for t in texts]
    counters = {}
    totals = {k: sum(1 for p in side_parts if p[0] == k) for k in ("interview", "fieldnote", "background")}
    for kind, text, pages, whole in side_parts:
        counters[kind] = counters.get(kind, 0) + 1
        if kind == "interview":      # speakers come from the whole sidebar, so every part names them
            head = INTERVIEW_HEAD.match(whole.split("\n")[0])
            if head:
                spk = [{"label": head.group(1), "side": "answer"}, {"label": head.group(2), "side": "ask"}]
                when = DATE.search(head.group(3))
            else:
                spk = [{"label": s, "side": "ask" if ASKER.search(s) else "answer"} for s in speakers_of(whole)]
                when = None
            answerers = "、".join(s["label"] for s in spk if s["side"] == "answer")
            more = {"interview": {"speakers": spk, **({"observed_on": iso(when)} if when else {})}}
            extra, heading = f"访谈：{answerers}" if answerers else "访谈", ["访谈"]
        elif kind == "fieldnote":
            when = DATE.search(text)
            more = {"fieldnote": {"author_role": "志愿者", **({"observed_on": iso(when)} if when else {})}}
            extra, heading = "志愿者走访记录", ["走访记录"]
        else:
            first = text.split("\n")[0]
            topic = re.sub(r"^关于(.+?)：.*", r"\1", first) if first.startswith("关于") else (first if len(first) <= 10 else e["location"])
            more = {"background": {"topic": topic}}
            extra, heading = f"背景：{topic}", ["背景"]
        chunks.append(chunk(e, kind, counters[kind], totals[kind], text, kind, entry_species, pages, heading, extra, more))

with open(out, "w", encoding="utf-8") as f:
    for c in chunks:
        f.write(json.dumps(c, ensure_ascii=False) + "\n")
print(f"wrote {len(chunks)} chunks from {src}")
