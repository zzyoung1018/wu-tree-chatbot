"""Build parent cards and verbatim child chunks; user policy 2026-11-04: story text takes precedence."""
import argparse
from collections import Counter
from copy import deepcopy
import hashlib
import json
import math
from pathlib import Path
import re

SOURCE_ID, BOOK, AS_OF = "SRC-SHBL", "《树碑立传》", "2024-08"
PIPELINE = "pilot_stories@3+index_story@1+build_story_chunks@2.1"
BODY_MAX, SIDE_MAX, MIN_SECTION = 1200, 900, 80

# 区县：每个区县第一个条目的编号，按索引分隔页地图上印的编号确定
DISTRICTS = [(1, "吴中区"), (30, "姑苏区"), (56, "吴江区"), (69, "虎丘区（高新区）"), (72, "相城区"),
             (74, "工业园区"), (75, "昆山市"), (85, "常熟市"), (100, "太仓市"), (105, "张家港市")]

# 已人工关联到项目实体表的条目：(tree_ids, site_ids, 官方编号, link.status, link.evidence)
LINKED = {
    18: (["stm-yuanbai"], ["S02"], ["吴中186", "吴中187", "吴中188", "吴中189"], "cross_ref",
         "地点、树种与 stm-yuanbai 一致；编号见古树与艺术章节《推演》（印刷页 174），四棵个体的对应待确认"),
    21: (["sbs-shinan"], ["S03"], ["吴中203"], "manual", "地点、树种与 sbs-shinan 一致；编号来自现场官方铭牌"),
    55: (["dlzb-xuesong"], ["S01"], [], "manual", "地点、树种与 dlzb-xuesong 一致；书中称尚未列入古树名录"),
}

# 已知的说法冲突：(冲突 ID, 条目号, 命中正则)
#   cf-xuesong-planted：雪松种植年份，书中 1924 年，另有来源为 1923 年
#   cf-shinan-age：石楠树龄，铭牌"800余年"，石刻"元朝年间"
#   cf-hanshan-arrival：憨山到石壁的年份，有 1569、1596 两说
CONFLICTS = [("cf-xuesong-planted", 55, r"1924"),
             ("cf-shinan-age", 21, r"800年前|元朝"),
             ("cf-hanshan-arrival", 21, r"憨山")]

SYNONYM = {"樟树": "香樟"}

# 书中出现的在世人员（志愿者、受访者等）；张冀(?!牖) 排除历史人物张冀牖
PEOPLE = re.compile(r"罗星琳|周缘|王铭钰|张冀(?!牖)|黄林法|黄老|陈平|施雪荣|陈爱娣|萍萍阿姨")

OFFICIAL_CODE = re.compile(r"(吴中|吴江|狮|沧|网|耦|天|拙|留|虎|常熟|昆|太|张|相|园区|姑苏|平|金|东)\s?(\d{3})")
LEGEND = re.compile(r"相传|传说|据传|传为|传闻|民间(?:盛|流)传")
RECORDED = re.compile(r"记载|记述|《[^》]{1,24}》(?:中|里)?(?:记|载|称|说|云|写)|志载|旧志")
HEDGED = re.compile(r"并无(?:明确)?依据|一说|或说|待考|存疑|无从考证|已不可考|说法不一")
RED = re.compile(r"中共|共产党|党支部|革命|烈士|红军|新四军|抗日")
RELIGION = re.compile(r"寺|庵|禅|法师|佛|道观|道士|观音|菩萨|僧|香客")
GONE = re.compile(r"今已?不存|已死亡|因死亡|被.{0,12}除名")      # 只用于 status_text_hint，不改状态
SPEAKER = re.compile(r"^([一-鿿A-Za-z·]{2,14})[：:]")
ROLE = re.compile(r"馆员|法师|志愿者|工作者|受访人|采访人|阿姨|老|先生|女士|师傅|经理")
ASKER = re.compile(r"馆员|志愿者|采访人")
INTERVIEW_HEAD = re.compile(r"受访人：(.+?)采访人：(.+?)时间：([\d.]+)")
DATE = re.compile(r"(\d{4})[年.](\d{1,2})[月.](\d{1,2})")           # 写成 YYYY-MM-DD

# User's 2026-11-04 decision supersedes the old index-only status rule.
# Status evidence is object-specific and quoted verbatim, never keyword inference.
TEXT_STATUS = {
    (1, 2): ("gone", "后因“松干蚧”虫害而未能存活下来。"),
    (14, 1): ("gone", "目前这棵龙柏因死亡被官方所统计的古树名木列表除名。"),
    (26, 1): ("extant", "庙前的古树是一棵200多年的榉树，在春天崭露出迷人的新绿"),
    (27, 1): ("extant", "所以它总体健康状况良好"),
    (29, 1): ("gone", "（今不存）"),
    (34, 1): ("gone", "（今不存）"),
    (74, 1): ("extant", "所以仅余本文所述的两棵古银杏。"),
}
OVERRIDES = {
    2: {"move_sidebars": {0: 3}, "move_story_images": {0: (3, "drawing")},
        "note": "印刷页76的小字框和插图讲的是第3条阴山岛古樟，已移到第3条"},
    11: {"location_conflict_detail": "；正文叙述也提到灵源古寺。正文内部有冲突，暂照录标题，待馆方核实"},
    14: {"note": "按正文，该龙柏因死亡被官方古树名录除名；索引未标注"},
    23: {"location": "木渎古镇严家花园、木渎古镇古松园",
         "items": [{"location": "木渎古镇严家花园", "species": ["广玉兰"]},
                   {"location": "木渎古镇古松园", "species": ["罗汉松"]}],
         "split_body_before": ("古松园是", 2),
         "note": "正文页标题印了两个地点，索引只印严家花园；按正文拆成两个item"},
    74: {"status_hint_note": "误报：正文“已死亡注销”指园区另一棵银杏，本条两棵仍在"},
}


def read_jsonl(path):
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line]


def write_jsonl(path, rows):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("".join(json.dumps(r, ensure_ascii=False) + "\n" for r in rows), encoding="utf-8")


def names(*labels):
    out = []
    for label in labels:
        match = re.match(r"一棵(.+?)的", label or "")
        for name in ([match.group(1)] if match else re.split(r"[、，,\s/]+", label or "")):
            name = SYNONYM.get(name, name)
            if name and len(name) <= 5 and name not in out:
                out.append(name)
    return out


def district(no):
    return [name for start, name in DISTRICTS if no >= start][-1]


def pages_of(parts):
    return sorted({page for part in parts for page in part["printed_pages"]})


def iso(match):
    return f"{match.group(1)}-{int(match.group(2)):02d}-{int(match.group(3)):02d}"


def normalized_location(text):
    return re.sub(r"\s|[（(][^）)]*[）)]|[镇村禅]", "", text)


def sections(entry):
    if entry["entry_no"] == 23:
        groups = [["广玉兰", []], ["罗汉松", []]]
        at = 0
        for block in entry["blocks"]:
            if block["type"] == "para":
                if block["text"].startswith(OVERRIDES[23]["split_body_before"][0]):
                    at = 1
                groups[at][1].append(block)
        if not all(g[1] for g in groups):
            raise ValueError("Entry 23 split boundary missing")
        return groups
    groups = []
    for block in entry["blocks"]:
        if block["type"] == "subhead":
            groups.append([block["text"], []])
        elif groups:
            groups[-1][1].append(block)
        else:
            groups.append(["", [block]])
    merged = []
    for label, paras in (g for g in groups if g[1]):
        if merged and sum(len(p["text"]) for p in merged[-1][1]) < MIN_SECTION:
            merged[-1][0] = "、".join(s for s in [merged[-1][0], label] if s)
            merged[-1][1] += paras
        else:
            merged.append([label, paras])
    if len(merged) > 1 and sum(len(p["text"]) for p in merged[-1][1]) < MIN_SECTION:
        label, paras = merged.pop()
        merged[-1][0] = "、".join(s for s in [merged[-1][0], label] if s)
        merged[-1][1] += paras
    return merged


def balanced_parts(paras, limit):
    total = len("\n".join(p["text"] for p in paras))
    if total <= limit:
        return [paras]
    n = math.ceil(total / limit)
    goal, groups, size = total / n, [[]], 0
    for para in paras:
        if groups[-1] and size + len(para["text"]) / 2 > goal and len(groups) < n:
            groups.append([])
            size = 0
        groups[-1].append(para)
        size += len(para["text"]) + 1
    if any(len("\n".join(p["text"] for p in g)) > limit for g in groups):
        raise ValueError("A paragraph-boundary split still exceeds the limit; review required")
    return groups


def speakers_of(text):
    labels = [m.group(1) for line in text.split("\n") if (m := SPEAKER.match(line))]
    return list(dict.fromkeys(label for label in labels if ROLE.search(label) or labels.count(label) >= 2))


def sidebar_kind(text):
    first = text.split("\n")[0]
    if first.startswith("关于") or (len(first) <= 8 and not re.search(r"[，。：]", first)):
        return "background"
    speakers = speakers_of(text)
    if INTERVIEW_HEAD.match(first) or len(speakers) >= 2 or (speakers and SPEAKER.match(first)):
        return "interview"
    return "fieldnote" if "志愿者" in text or DATE.search(text) else "background"


def interview_parts(text):
    if len(text) <= SIDE_MAX:
        return [text]
    n = math.ceil(len(text) / SIDE_MAX)
    goal, groups, size = len(text) / n, [[]], 0
    for line in text.split("\n"):
        match = SPEAKER.match(line)
        asks = match and ASKER.search(match.group(1))
        if groups[-1] and ((asks and size >= goal * 0.8) or size + len(line) + 1 > SIDE_MAX):
            groups.append([])
            size = 0
        groups[-1].append(line)
        size += len(line) + 1
    parts = ["\n".join(g) for g in groups]
    if max(map(len, parts)) > SIDE_MAX:
        raise ValueError("Single interview turn exceeds limit")
    return parts


def small_parts(entry):
    parts = []
    for sidebar_no, sidebar in enumerate(entry["sidebars"]):
        whole = sidebar["text"]
        kind = sidebar_kind(whole)
        segments = [(kind, whole, False)]
        lines = whole.split("\n")
        notes = [i for i, line in enumerate(lines) if line.startswith("采访手记：")]
        if kind == "interview" and notes:
            if len(notes) != 1:
                raise ValueError("Multiple interview notes in one sidebar")
            at = notes[0]
            segments = [("interview", "\n".join(lines[:at]), False),
                        ("fieldnote", "\n".join(lines[at:]), True)]
        for ctype, text, is_note in segments:
            texts = interview_parts(text) if ctype == "interview" else [
                "\n".join(p["text"] for p in g) for g in balanced_parts(
                    [{"text": line, "printed_pages": sidebar["printed_pages"]} for line in text.split("\n")], SIDE_MAX)]
            for fragment in texts:
                parts.append({"kind": ctype, "text": fragment, "whole": whole,
                              "printed_pages": sidebar["printed_pages"], "is_note": is_note,
                              "sidebar_no": sidebar_no})
    return parts


def item_records(entry, index):
    no = entry["entry_no"]
    if no == 23:
        raw = [dict(h, origin="override") for h in OVERRIDES[23]["items"]]
    else:
        raw = [{"location": h["location"], "species": names(h["species_raw"]), "origin": h["origin"]}
               for h in index["heads"]]
        # The index defines the grouping; facts and their wording come from the story.
        secondary = {name for h in raw[1:] for name in h["species"]}
        body_species = [name for name in names(entry["title"], *entry["subheads"]) if name not in secondary]
        if not body_species:
            raise ValueError(f"No primary species in story {no}")
        raw[0]["location"] = entry["location"]
        raw[0]["species"] = body_species
        for h in raw[1:]:
            if h["location"] not in entry["location"] + entry["text"]:
                raise ValueError(f"Secondary location lacks story evidence: {no}")
    items = []
    for item_no, h in enumerate(raw, 1):
        status, quote = TEXT_STATUS.get((no, item_no), ("extant", ""))
        if quote and quote not in entry["text"]:
            raise ValueError(f"Status quote missing in entry {no}")
        item = {"item_no": item_no, "location": h["location"], "species": h["species"],
                "status": status, "status_source": "text"}
        item["status_note"] = ("正文：" + quote) if quote else "正文未认定该对象已不存；仅反映书中资料时间，非当前现场核实"
        item["origin"] = h["origin"]
        items.append(item)
    return items


def image_record(raw):
    record = {"image_id": raw["image_id"], "file": f"pilot/images/story/{raw['image_id']}.jpg", "role": raw["role"]}
    if raw["role"] == "index":
        record["item_no"] = raw["head_no"]
    record.update({k: raw[k] for k in ["pdf_page", "printed_page", "bbox", "native_px"]})
    record["kind"] = raw.get("_kind", "painting" if raw.get("credit") else "photo")
    for key in ["caption", "credit"]:
        if raw.get(key):
            record[key] = raw[key]
    record["rights"] = {"status": "third_party" if raw.get("credit") else "unknown"}
    return record


def chunk(entry, item, kind, part, parts, text, species, printed_pages, label="", extra=None, figures=None):
    no = entry["entry_no"]
    unit = f"shbl:story-{no:03d}"
    trees, sites, codes, how, evidence = LINKED.get(no, ([], [], [], "unlinked", ""))
    figures = figures or []
    codes = sorted(set(codes) | {a + b for a, b in OFFICIAL_CODE.findall(
        text + " ".join(f["caption"] for f in figures))})
    suffix = "（今已不存）" if item["status"] == "gone" else ""
    header = f"{BOOK}树的故事 第{no}条｜{district(no)} {item['location']}｜{'、'.join(species)}{suffix}"
    if extra:
        header += "｜" + extra
    search_terms = [code for code in codes if code not in text]
    path = ["树的故事", f"{no} {entry['location']}·{entry['title']}"]
    if label and (kind != "story" or label != entry["title"]):
        path.append(label)
    block_type = "body" if kind == "story" else kind
    c = {"chunk_id": f"{unit}:{block_type}-{part}", "unit_id": unit, "part": part, "parts": parts,
         "source_id": SOURCE_ID, "content_type": kind, "text": text, "context_header": header,
         "lang": "zh-Hans", "n_chars": len(text)}
    if search_terms:
        c["search_terms"] = search_terms
    if figures:
        c["figures"] = figures
    c.update({"locator": {"section": "树的故事", "entry_no": no, "title": entry["title"],
                         "heading_path": path, "pdf_pages": sorted({p // 2 + 1 for p in printed_pages}),
                         "printed_pages": sorted(set(printed_pages))},
              "tree_ids": trees, "site_ids": sites, "site_name": item["location"], "district": district(no),
              "species": species})
    if codes:
        c["official_codes"] = codes
    c.update({"link": {"status": how, **({"evidence": evidence} if evidence else {})},
              "source_tier": "B", "as_of": AS_OF, "review_status": "pending"})
    flags = [f for f, rx in [("legend", LEGEND), ("recorded", RECORDED), ("hedged", HEDGED)] if rx.search(text)]
    conflicts = [cid for cid, entry_no, pattern in CONFLICTS if no == entry_no and re.search(pattern, text)]
    sensitivity = [s for s, yes in [("red_history", RED.search(text)),
                    ("religion", RELIGION.search(text + item["location"])),
                    ("personal_info", kind == "interview" or PEOPLE.search(text))] if yes]
    for key, value in [("certainty_flags", flags), ("conflict_ids", conflicts), ("sensitivity", sensitivity)]:
        if value:
            c[key] = value
    c["rights"] = {"holder": "吴文化博物馆", "display": "full", "note": "馆方尚未确认可否在 AI 应用中引用原文"}
    c["content_hash"] = "sha256:" + hashlib.sha256((header + "\n" + text).encode("utf-8")).hexdigest()[:16]
    c["pipeline"] = PIPELINE
    return c


def build(stories_path, index_path, chunks_path, entries_path, *, strict_counts=True):
    stories = {e["entry_no"]: e for e in deepcopy(read_jsonl(stories_path))}
    indexes = {e["entry_no"]: e for e in deepcopy(read_jsonl(index_path))}
    if not stories or set(indexes) != set(stories) or (strict_counts and set(stories) != set(range(1, 106))):
        raise ValueError("Expected entries 1 through 105 in both inputs")
    transferred = {}
    for no, override in OVERRIDES.items():
        if no not in stories:
            continue
        for at, target in sorted(override.get("move_sidebars", {}).items(), reverse=True):
            stories[target]["sidebars"].append(stories[no]["sidebars"].pop(at))
            transferred[target] = override["note"]
        story_images = [im for im in indexes[no]["images"] if im["role"] == "story"]
        for at, (target, kind) in override.get("move_story_images", {}).items():
            img = story_images[at]
            indexes[no]["images"].remove(img)
            img["_kind"] = kind
            indexes[target]["images"].append(img)
        if "location" in override:
            stories[no]["location"] = override["location"]
    all_chunks, cards = [], []
    for no, entry in sorted(stories.items()):
        index, flags, children, chunks = indexes[no], [], [], []
        items = item_records(entry, index)
        unit = f"shbl:story-{no:03d}"
        body_parts = []
        for label, paragraphs in sections(entry):
            species = names(label) if label else items[0]["species"]
            matches = [it for it in items if set(species).issubset(it["species"])] if label else items[:1]
            item = matches[0] if matches else items[0]
            if not matches:
                flags.append({"code": "section_species_unmatched", "note": label})
            for group in balanced_parts(paragraphs, BODY_MAX):
                body_parts.append((label, species, item, group))
        figures = [{"caption": c["text"], "printed_page": c["printed_pages"][0]} for c in entry["captions"]]
        for i, (label, species, item, group) in enumerate(body_parts, 1):
            c = chunk(entry, item, "story", i, len(body_parts), "\n".join(p["text"] for p in group),
                      species, pages_of(group), label=label, figures=figures if i == 1 else None)
            children.append({"chunk_id": c["chunk_id"], "content_type": "story",
                             "item_no": item["item_no"], "label": "正文·" + "、".join(species)})
            chunks.append(c)
        small = small_parts(entry)
        totals, counts = Counter(p["kind"] for p in small), Counter()
        for p in small:
            kind, whole, text = p["kind"], p["whole"], p["text"]
            first = whole.split("\n")[0]
            interview_head = INTERVIEW_HEAD.match(first)
            interview_date = DATE.search(interview_head.group(3)) if interview_head else None
            if kind == "interview":
                if interview_head:
                    speakers = [{"label": interview_head.group(1), "side": "answer"},
                                {"label": interview_head.group(2), "side": "ask"}]
                else:
                    speakers = [{"label": s, "side": "ask" if ASKER.search(s) else "answer"} for s in speakers_of(whole)]
                metadata = {"speakers": speakers}
                if interview_date:
                    metadata["observed_on"] = iso(interview_date)
                answerers = "、".join(s["label"] for s in speakers if s["side"] == "answer")
                extra, label = "访谈" + (f"：{answerers}" if answerers else ""), "访谈"
            elif kind == "fieldnote":
                when = interview_date if p["is_note"] else DATE.search(text)
                metadata = {"author_role": "采访人" if p["is_note"] else "志愿者"}
                if when:
                    metadata["observed_on"] = iso(when)
                extra = "采访手记" if p["is_note"] else "志愿者走访记录"
                label = "采访手记" if p["is_note"] else "走访记录"
            else:
                topic = first[2:].split("：", 1)[0] if first.startswith("关于") else (
                    first if len(first) <= 10 else entry["location"])
                metadata, extra, label = {"topic": topic}, "背景：" + topic, "背景"
            counts[kind] += 1
            # Keys are zero-based sidebar indices, shared by all its fragments.
            item_no = OVERRIDES.get(no, {}).get('sidebar_items', {}).get(p['sidebar_no'], 1)
            item = next((item for item in items if item['item_no'] == item_no), None)
            if item is None:
                raise ValueError(f'Invalid sidebar item pointer: {no}/{item_no}')
            c = chunk(entry, item, kind, counts[kind], totals[kind], text, item["species"],
                      p["printed_pages"], label=label, extra=extra)
            c[kind] = metadata
            if metadata.get("observed_on"):
                c["as_of"] = metadata["observed_on"]
            children.append({"chunk_id": c["chunk_id"], "content_type": kind, "item_no": item_no, "label": extra})
            chunks.append(c)
        # Keep safe index aliases searchable without replacing the authoritative story wording.
        alias = index["heads"][0]["location"]
        normal_alias, normal_story = normalized_location(alias), normalized_location(entry["location"])
        if normal_alias not in normal_story and normal_story not in normal_alias:
            flags.append({"code": "location_conflict", "note":
                f"正文标题地点：{entry['location']}；索引地点：{alias}" + OVERRIDES.get(no, {}).get('location_conflict_detail', '。')})
        elif alias != entry["location"]:
            for child, c in zip(children, chunks):
                if child["item_no"] == 1 and items[0]["origin"] == "index_main" and alias not in c["text"]:
                    c.setdefault("search_terms", []).append(alias)
        images = [image_record(im) for im in index["images"]]
        images.sort(key=lambda im: (0 if im["role"] == "index" else 1, im["printed_page"],
                                    int(im["image_id"].rsplit("-", 1)[1])))
        full_text = entry["text"] + "\n" + "\n".join(s["text"] for s in entry["sidebars"])
        if GONE.search(full_text) and not any("不存" in h["status_raw"] for h in index["heads"]):
            flags.append({"code": "status_text_hint", "note": OVERRIDES.get(no, {}).get(
                "status_hint_note", "正文存在不存或死亡表述，已按对象核对；不依关键词自动判定")})
        if any(im["rights"]["status"] == "third_party" for im in images):
            flags.append({"code": "third_party_image", "note": "含其他机构藏品图像，展示授权待馆方确认"})
        if OVERRIDES.get(no, {}).get("note") or no in transferred:
            flags.append({"code": "override", "note": OVERRIDES.get(no, {}).get("note") or transferred[no]})
        statuses = [it["status"] for it in items]
        status = "gone" if all(s == "gone" for s in statuses) else ("partial" if "gone" in statuses else "extant")
        trees, sites, _, how, evidence = LINKED.get(no, ([], [], [], "unlinked", ""))
        card = {"unit_id": unit, "source_id": SOURCE_ID, "section": "树的故事", "entry_no": no,
                "title": entry["title"], "district": district(no), "location": entry["location"], "items": items,
                "species": list(dict.fromkeys(sp for it in items for sp in it["species"])),
                "tree_ids": trees, "site_ids": sites}
        if how != "unlinked":
            card["link"] = {"status": how, "evidence": evidence}
        card.update({"official_codes": sorted({code for c in chunks for code in c.get("official_codes", [])}),
                     "status": status, "children": children, "images": images})
        if figures:
            card["captions"] = [{"text": f["caption"], "printed_page": f["printed_page"]} for f in figures]
        pages = pages_of(entry["blocks"] + entry["sidebars"] + entry["captions"])
        card.update({"pdf_pages": sorted({p // 2 + 1 for p in pages}), "printed_pages": pages,
                     "n_chars_body": entry["chars"], "n_chars_small": sum(len(s["text"]) for s in entry["sidebars"]),
                     "as_of": AS_OF, "review_status": "pending"})
        if flags:
            card["review_flags"] = flags
        card["pipeline"] = PIPELINE
        cards.append(card)
        all_chunks.extend(chunks)
    counts = dict(Counter(c["content_type"] for c in all_chunks))
    summary = {"cards": len(cards), "items": sum(len(c["items"]) for c in cards), "chunks": len(all_chunks),
               "types": counts, "status": dict(Counter(c["status"] for c in cards)),
               "flags": {code: [c["entry_no"] for c in cards if any(f["code"] == code for f in c.get("review_flags", []))]
                         for code in ["location_conflict", "status_text_hint", "third_party_image", "override", "section_species_unmatched"]}}
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    if strict_counts and (len(cards) != 105 or sum(len(c["items"]) for c in cards) != 108 or counts != {
            "story": 116, "fieldnote": 10, "interview": 6, "background": 6}):
        raise ValueError("Unexpected structural counts; no JSONL written")
    if not all_chunks or any(not c['text'] or c['content_hash'] != 'sha256:' + hashlib.sha256(
            (c['context_header'] + '\n' + c['text']).encode()).hexdigest()[:16] for c in all_chunks):
        raise ValueError('Empty chunk or inconsistent content hash')
    refs = [child['chunk_id'] for card in cards for child in card['children']]
    if len(refs) != len(set(refs)) or set(refs) != {c['chunk_id'] for c in all_chunks}:
        raise ValueError('Duplicate or missing parent/child references')
    field_order = [
        "chunk_id", "unit_id", "part", "parts", "source_id", "content_type", "text", "context_header",
        "lang", "n_chars", "search_terms", "figures", "locator", "tree_ids", "site_ids", "site_name",
        "district", "species", "official_codes", "link", "source_tier", "as_of", "review_status",
        "review_note", "certainty_flags", "conflict_ids", "sensitivity", "rights", "content_hash",
        "pipeline", "interview", "fieldnote", "background",
    ]
    all_chunks = [{key: c[key] for key in field_order if key in c} for c in all_chunks]
    write_jsonl(chunks_path, all_chunks)
    write_jsonl(entries_path, cards)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ["stories", "index", "chunks", "entries"]:
        parser.add_argument(name, type=Path)
    parser.add_argument('--no-strict-counts', action='store_true', help='Allow a different corpus size; retain integrity checks')
    args = parser.parse_args()
    build(args.stories, args.index, args.chunks, args.entries, strict_counts=not args.no_strict_counts)
