"""Local FTS5 child retrieval, parent expansion, budget handling and source mapping.

Uses only the standard library. It does not call an embedding or language model.
"""
import argparse
import json
from pathlib import Path
import re
import sqlite3


def grams(text):
    return [segment[i:i + 2] for segment in re.findall(r"\w+", text)
            for i in range(max(1, len(segment) - 1))]


def search_children(db, query, k=20, verified_only=False):
    terms = list(dict.fromkeys(grams(query)))
    if not terms:
        return []
    match = " OR ".join('"' + term.replace('"', '""') + '"' for term in terms)
    condition = "c.review_status = 'verified'" if verified_only else "c.review_status != 'rejected'"
    rows = db.execute(
        f"""SELECT c.chunk_id, c.unit_id, bm25(chunks_fts) AS score
            FROM chunks_fts JOIN chunks c ON c.chunk_id = chunks_fts.chunk_id
            WHERE chunks_fts MATCH ? AND {condition}
            ORDER BY score, c.chunk_id LIMIT ?""", (match, k)).fetchall()
    return [{"chunk_id": r[0], "unit_id": r[1], "score": r[2]} for r in rows]


TYPE_LABELS = {"story": "故事", "fieldnote": "走访记录", "interview": "访谈", "background": "背景"}
MARK_LABELS = {"legend": "含传说", "recorded": "含史载", "hedged": "含存疑",
               "red_history": "含革命史", "religion": "含宗教场所", "personal_info": "含个人信息"}
CONFLICT_LABELS = {"cf-xuesong-planted": "雪松种植年份", "cf-shinan-age": "石楠树龄",
                   "cf-hanshan-arrival": "憨山到石壁的年份"}
STATUS_LABELS = {"extant": "正文未认定已不存", "gone": "正文记载已不存", "partial": "正文记载部分已不存"}


def render(picked):
    lines, sources = [], {}
    for group in picked:
        card, chunks, hits = group["card"], group["chunks"], group["hit_ids"]
        lines.append(f"【条目】《树碑立传》树的故事 第{card['entry_no']}条 · {card['title']}")
        lines.append("树：" + "；".join(
            f"{it['item_no']} {it['location']}｜{'、'.join(it['species'])}（{STATUS_LABELS[it['status']]}）"
            for it in card["items"]))
        lines.append(f"区县：{card['district']}　官方编号：{'、'.join(card['official_codes']) or '未载'}"
                     f"　状态：{STATUS_LABELS[card['status']]}（资料时间{card['as_of']}，非当前现场核实）")
        for flag in card.get("review_flags", []):
            if flag["code"] in ["location_conflict", "status_text_hint", "section_species_unmatched"]:
                lines.append("待核实：" + flag["note"])
        labels = {c["chunk_id"]: c["label"] for c in card["children"]}
        for c in chunks:
            tag = f"S{len(sources) + 1}"
            page_label = "、".join(map(str, c["locator"]["printed_pages"]))
            hit = "｜命中" if c["chunk_id"] in hits else ""
            lines.append(f"[{tag}] {labels[c['chunk_id']]}（第{page_label}页{hit}）")
            marks = [MARK_LABELS[m] for m in c.get("certainty_flags", []) + c.get("sensitivity", [])]
            marks += ["说法冲突：" + CONFLICT_LABELS.get(cid, cid) for cid in c.get("conflict_ids", [])]
            lines.append(f"类型：{TYPE_LABELS[c['content_type']]}｜来源：{c['source_tier']}｜资料时间：{c['as_of']}"
                         + ("｜标记：" + "、".join(marks) if marks else ""))
            lines.append(c["text"])
            sources[tag] = {"chunk_id": c["chunk_id"], "unit_id": c["unit_id"],
                            "printed_pages": c["locator"]["printed_pages"],
                            "citation": f"《树碑立传》第{page_label}页（树的故事·{card['entry_no']}）"}
        lines.append("")
    return "\n".join(lines).rstrip(), sources


def retrieve(db, query, k_child=20, k_parent=3, budget=6000, verified_only=False):
    if not 1 <= k_parent <= 4:
        raise ValueError("k_parent must be between 1 and 4")
    if k_child < 1 or budget < 1:
        raise ValueError("k_child and budget must be positive")
    hits = search_children(db, query, k_child, verified_only)
    grouped = {}
    for rank, hit in enumerate(hits):
        group = grouped.setdefault(hit["unit_id"], {"best_rank": rank + 1, "hit_ids": []})
        group["hit_ids"].append(hit["chunk_id"])
    picked = []
    for unit, info in sorted(grouped.items(), key=lambda pair: pair[1]["best_rank"])[:k_parent]:
        card = json.loads(db.execute("SELECT card FROM entries WHERE unit_id = ?", (unit,)).fetchone()[0])
        allowed = "= 'verified'" if verified_only else "!= 'rejected'"
        chunks = [json.loads(row[0]) for row in db.execute(
            f"SELECT chunk FROM chunks WHERE unit_id = ? AND review_status {allowed} ORDER BY seq", (unit,))]
        picked.append({"card": card, "chunks": chunks, **info})
    dropped = []
    context, sources = render(picked)
    # Count the entire rendered prompt, including headings and provenance.
    for group in reversed(picked):
        for body in [False, True]:
            for c in list(group["chunks"]):
                if len(context) <= budget:
                    break
                if c["chunk_id"] not in group["hit_ids"] and (c["content_type"] == "story") == body:
                    group["chunks"].remove(c)
                    dropped.append(c["chunk_id"])
                    context, sources = render(picked)
    if len(context) > budget:
        raise ValueError(f"命中块及必要出处共{len(context)}字，超过预算{budget}；未截断命中原文，未生成上下文。")
    return {"query": query, "hits": hits, "parents": picked, "dropped_unmatched": dropped,
            "context": context, "source_map": sources, "n_chars": len(context), "budget": budget}


SMOKE_CASES = [
    ("树洞口和断口怎么清理", 26, "interview", None),
    ("天平山的马尾松还在吗", 1, "story", "shbl:story-001:body-3"),
    ("三官殿的龙柏", 2, "story", "shbl:story-002:body-2"),
    ("吴中203", 21, None, None),
    ("石楠花开时志愿者去做了什么", 21, "fieldnote", None),
    ("灵源古寺的罗汉松", 11, "story", None),
    ("古树受伤了怎么保护", 26, "interview", None),
    ("湖沙庙的榉树", 26, "story", None),
]


def self_test(db):
    results = []
    for query, no, kind, exact in SMOKE_CASES:
        result = retrieve(db, query)
        unit = f"shbl:story-{no:03d}"
        parent = next((p for p in result["parents"] if p["card"]["unit_id"] == unit), None)
        rank = result["parents"].index(parent) + 1 if parent else None
        matching = [] if not parent else [
            c for c in parent["chunks"] if c["chunk_id"] in parent["hit_ids"]
            and (kind is None or c["content_type"] == kind) and (exact is None or c["chunk_id"] == exact)]
        passed = rank is not None and rank <= 3 and bool(matching)
        if no == 1:
            passed = passed and any("（今已不存）" in c["context_header"] for c in matching)
        if query == "吴中203":
            passed = passed and any("吴中203" in c.get("search_terms", []) for c in matching)
        results.append({"query": query, "expected": unit, "rank": rank,
                        "matched": [c["chunk_id"] for c in matching], "passed": bool(passed),
                        "context_chars": result["n_chars"]})
    report = {"passed": sum(r["passed"] for r in results), "total": len(results), "results": results}
    return report


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("database", type=Path)
    parser.add_argument("query", nargs="?")
    parser.add_argument("--self-test", action="store_true")
    parser.add_argument("--report", type=Path)
    parser.add_argument("--json", action="store_true")
    parser.add_argument("--verified-only", action="store_true")
    parser.add_argument("--budget", type=int, default=6000)
    args = parser.parse_args()
    with sqlite3.connect(args.database.resolve().as_uri() + "?mode=ro", uri=True) as db:
        if args.self_test:
            report = self_test(db)
            text = json.dumps(report, ensure_ascii=False, indent=2)
            print(text)
            if args.report:
                args.report.write_text(text + "\n", encoding="utf-8")
            raise SystemExit(report["passed"] != report["total"])
        if not args.query:
            parser.error("Supply a query or --self-test")
        result = retrieve(db, args.query, budget=args.budget, verified_only=args.verified_only)
        if args.json:
            print(json.dumps(result, ensure_ascii=False, indent=2))
        else:
            for rank, parent in enumerate(result["parents"], 1):
                print(f"{rank}. {parent['card']['unit_id']}　命中：{', '.join(parent['hit_ids'])}")
            print("\n" + (result["context"] or "未找到符合审核条件的子块。"))
