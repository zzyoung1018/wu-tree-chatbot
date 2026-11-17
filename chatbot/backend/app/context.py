"""Render parent cards and cited child passages without performing retrieval."""

TYPE_LABELS = {"story": "故事", "fieldnote": "走访记录", "interview": "访谈", "background": "背景"}
TYPE_LABELS_EN = {"story": "Story", "fieldnote": "Field Note", "interview": "Interview", "background": "Background"}
MARK_LABELS = {"legend": "含传说", "recorded": "含史载", "hedged": "含存疑",
               "red_history": "含革命史", "religion": "含宗教场所", "personal_info": "含个人信息"}
CONFLICT_LABELS = {"cf-xuesong-planted": "雪松种植年份", "cf-shinan-age": "石楠树龄",
                   "cf-hanshan-arrival": "憨山到石壁的年份"}


def render(picked, lang='zh'):
    lines, sources = [], {}
    for group in picked:
        card, chunks, hits = group["card"], group["chunks"], group["hit_ids"]
        lines.append(f"[Entry {card['entry_no']}] Book of Ancient Trees · {card['title']}" if lang == 'en' else f"【条目】《树碑立传》树的故事 第{card['entry_no']}条 · {card['title']}")
        lines.append(("Trees: " if lang == 'en' else "树：") + "；".join(
            f"{it['item_no']} {it['location']}｜{'、'.join(it['species'])}"
            for it in card["items"]))
        lines.append(f"District: {card['district']} | Official codes: {', '.join(card['official_codes']) or 'Not recorded'}" if lang == 'en' else f"区县：{card['district']}　官方编号：{'、'.join(card['official_codes']) or '未载'}")
        for flag in card.get("review_flags", []):
            if flag["code"] == "location_conflict":
                lines.append(("Source discrepancy: " if lang == 'en' else "原文说明：") + flag["note"])
        labels = {c["chunk_id"]: c["label"] for c in card["children"]}
        for c in chunks:
            tag = f"S{len(sources) + 1}"
            page_label = "、".join(map(str, c["locator"]["printed_pages"]))
            hit = "｜命中" if c["chunk_id"] in hits else ""
            lines.append(f"[{tag}] {TYPE_LABELS_EN[c['content_type']]} (p. {page_label})" if lang == 'en' else f"[{tag}] {labels[c['chunk_id']]}（第{page_label}页{hit}）")
            marks = [MARK_LABELS[m] for m in c.get("certainty_flags", []) + c.get("sensitivity", [])]
            marks += ["说法冲突：" + CONFLICT_LABELS.get(cid, cid) for cid in c.get("conflict_ids", [])]
            lines.append((f"Type: {TYPE_LABELS_EN[c['content_type']]} | Source: {c['source_tier']} | As of: {c['as_of']} | Flags: {', '.join(c.get('certainty_flags', []) + c.get('conflict_ids', []))}" if lang == 'en' else
                          f"类型：{TYPE_LABELS[c['content_type']]}｜来源：{c['source_tier']}｜资料时间：{c['as_of']}"
                          + ("｜标记：" + "、".join(marks) if marks else "")))
            lines.append(c["text"])
            sources[tag] = {"chunk_id": c["chunk_id"], "unit_id": c["unit_id"],
                            "printed_pages": c["locator"]["printed_pages"],
                            "citation": f"《树碑立传》第{page_label}页（树的故事·{card['entry_no']}）"}
        lines.append("")
    return "\n".join(lines).rstrip(), sources
