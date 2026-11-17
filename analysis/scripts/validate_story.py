"""Validate story schemas, source coverage, relations, and the body-first policy."""
import argparse
from collections import Counter, defaultdict
from copy import deepcopy
import hashlib
import json
from pathlib import Path
import re

from jsonschema import Draft202012Validator


def read_jsonl(path):
    raw = path.read_bytes()
    if raw.startswith(b"\xef\xbb\xbf") or not raw.endswith(b"\n"):
        raise ValueError(f"JSONL encoding / terminal newline: {path}")
    rows = [json.loads(line) for line in raw.decode("utf-8").splitlines()]
    expected = "".join(json.dumps(row, ensure_ascii=False) + "\n" for row in rows).encode("utf-8")
    if raw != expected:
        raise ValueError(f"Non-canonical JSONL format: {path}")
    return rows


def validate(entry_schema, chunk_schema, cards, chunks, stories, base):
    problems = []
    def check(condition, message):
        if not condition:
            problems.append(message)
    for schema, rows, label in [(entry_schema, cards, "entry"), (chunk_schema, chunks, "chunk")]:
        Draft202012Validator.check_schema(schema)
        validator = Draft202012Validator(schema)
        for row in rows:
            for error in validator.iter_errors(row):
                problems.append(f"{label} {row.get('unit_id')} / {list(error.path)}: {error.message}")
    # Stop unsafe traversal after schema failures, but still print all schema errors.
    if problems:
        return problems, {}
    check(len(cards) == 105, "Expected 105 cards")
    check([c["entry_no"] for c in cards] == list(range(1, 106)), "Entry sequence must be 1..105")
    check(len({c["unit_id"] for c in cards}) == len(cards), "Duplicate card unit_id")
    check(len({c["chunk_id"] for c in chunks}) == len(chunks), "Duplicate chunk_id")
    check(all(c["unit_id"] == f"shbl:story-{c['entry_no']:03d}" for c in cards), "Card unit_id mismatch")
    by_id = {c["chunk_id"]: c for c in chunks}
    source = {e["entry_no"]: deepcopy(e) for e in stories}
    source[3]["sidebars"].append(source[2]["sidebars"].pop(0))
    child_order = [child["chunk_id"] for card in cards for child in card["children"]]
    check(child_order == [c["chunk_id"] for c in chunks], "Children and JSONL order/coverage disagree")
    check(len(set(child_order)) == len(child_order), "Child referenced by multiple cards")
    images_seen, class_groups = [], defaultdict(list)
    for c in chunks:
        cid = c["chunk_id"]
        check(cid.startswith(c["unit_id"] + ":"), f"{cid}: parent prefix")
        kind = "body" if c["content_type"] == "story" else c["content_type"]
        check(cid == f"{c['unit_id']}:{kind}-{c['part']}", f"{cid}: structured ID")
        check(c["part"] <= c["parts"], f"{cid}: part > parts")
        check(c["n_chars"] == len(c["text"]), f"{cid}: n_chars mismatch")
        loc = c["locator"]
        check(loc["printed_pages"] == sorted(set(loc["printed_pages"])), f"{cid}: printed page order")
        check(loc["pdf_pages"] == sorted({p // 2 + 1 for p in loc["printed_pages"]}), f"{cid}: PDF page conversion")
        digest = "sha256:" + hashlib.sha256((c["context_header"] + "\n" + c["text"]).encode()).hexdigest()[:16]
        check(c["content_hash"] == digest, f"{cid}: content_hash mismatch")
        prefix = f"《树碑立传》树的故事 第{loc['entry_no']}条｜{c['district']} {c['site_name']}｜"
        check(c["context_header"].startswith(prefix), f"{cid}: context prefix")
        check(c["review_status"] == "pending", f"{cid}: unreviewed material marked verified")
        check(c["n_chars"] <= (1200 if kind == "body" else 900), f"{cid}: size limit")
        if kind in ["interview", "fieldnote"] and c[kind].get("observed_on"):
            check(c["as_of"] == c[kind]["observed_on"], f"{cid}: dated note as_of")
        class_groups[(c["unit_id"], kind)].append(c)
    for key, group in class_groups.items():
        check([c["part"] for c in group] == list(range(1, len(group) + 1)), f"{key}: non-contiguous part")
        check(all(c["parts"] == len(group) for c in group), f"{key}: parts count")
    for card in cards:
        no, unit = card["entry_no"], card["unit_id"]
        entry = source[no]
        check(card["title"] == entry["title"], f"{unit}: title changed")
        expected_location = "木渎古镇严家花园、木渎古镇古松园" if no == 23 else entry["location"]
        check(card["location"] == expected_location, f"{unit}: source title location changed")
        check(card["n_chars_body"] == entry["chars"], f"{unit}: body char count")
        check(card["n_chars_small"] == sum(len(s["text"]) for s in entry["sidebars"]), f"{unit}: small char count")
        check([it["item_no"] for it in card["items"]] == list(range(1, len(card["items"]) + 1)), f"{unit}: item numbering")
        check(card["review_status"] == "pending", f"{unit}: review_status")
        own = []
        for child in card["children"]:
            cid = child["chunk_id"]
            check(cid in by_id, f"{unit}: missing child {cid}")
            if cid not in by_id:
                continue
            c = by_id[cid]
            own.append(c)
            check(c["unit_id"] == unit and c["locator"]["entry_no"] == no, f"{cid}: wrong parent")
            check(c["content_type"] == child["content_type"], f"{cid}: content_type mismatch")
            valid_item = 1 <= child["item_no"] <= len(card["items"])
            check(valid_item, f"{cid}: invalid item pointer")
            if valid_item:
                item = card["items"][child["item_no"] - 1]
                check(c["site_name"] == item["location"], f"{cid}: wrong location")
                check(set(c["species"]).issubset(item["species"]), f"{cid}: wrong species")
                check(("（今已不存）" in c["context_header"]) == (item["status"] == "gone"), f"{cid}: wrong status header")
            check(set(c["locator"]["printed_pages"]).issubset(card["printed_pages"]), f"{cid}: pages outside parent")
        check("\n".join(c["text"] for c in own if c["content_type"] == "story") == entry["text"],
              f"{unit}: body source coverage mismatch")
        check("\n".join(c["text"] for c in own if c["content_type"] != "story") ==
              "\n".join(s["text"] for s in entry["sidebars"]), f"{unit}: sidebar source coverage mismatch")
        expected_pages = sorted({p for b in entry["blocks"] + entry["sidebars"] + entry["captions"] for p in b["printed_pages"]})
        check(card["printed_pages"] == expected_pages, f"{unit}: card page union")
        check(card["pdf_pages"] == sorted({p // 2 + 1 for p in expected_pages}), f"{unit}: card PDF pages")
        check(card["official_codes"] == sorted({code for c in own for code in c.get("official_codes", [])}), f"{unit}: official code union")
        check(card["species"] == list(dict.fromkeys(s for it in card["items"] for s in it["species"])), f"{unit}: species union")
        statuses = [it["status"] for it in card["items"]]
        expected_status = "gone" if all(s == "gone" for s in statuses) else ("partial" if "gone" in statuses else "extant")
        check(card["status"] == expected_status, f"{unit}: status aggregation")
        for item in card["items"]:
            check(item["status_source"] == "text", f"{unit}: status not sourced from story")
            check(item.get("status_note", "").strip() != "", f"{unit}: missing status explanation")
        check(any(im["role"] == "index" for im in card["images"]), f"{unit}: no index image")
        for im in card["images"]:
            iid = im["image_id"]
            images_seen.append(iid)
            check(im["pdf_page"] == im["printed_page"] // 2 + 1, f"{iid}: page conversion")
            check(im["file"] == f"pilot/images/story/{iid}.jpg", f"{iid}: file naming")
            check((base / im["file"]).is_file(), f"{iid}: file missing")
            if "item_no" in im:
                check(1 <= im["item_no"] <= len(card["items"]), f"{iid}: invalid item pointer")
            if im.get("credit"):
                check(im["rights"]["status"] == "third_party", f"{iid}: credit rights")
        figures = [{"caption": cap["text"], "printed_page": cap["printed_pages"][0]} for cap in entry["captions"]]
        check((own[0].get("figures", []) if own else []) == figures, f"{unit}: figures not on body-1")
        check(all(not c.get("figures") for c in own[1:]), f"{unit}: figures duplicated")
        check(card.get("captions", []) == [{"text": f["caption"], "printed_page": f["printed_page"]} for f in figures],
              f"{unit}: caption mismatch")
    check(len(images_seen) == len(set(images_seen)), "Duplicate image IDs")
    types = dict(Counter(c["content_type"] for c in chunks))
    flags = {code: [c["entry_no"] for c in cards if any(f["code"] == code for f in c.get("review_flags", []))]
             for code in ["location_conflict", "status_text_hint", "third_party_image", "override", "section_species_unmatched"]}
    summary = {"cards": len(cards), "items": sum(len(c["items"]) for c in cards), "chunks": len(chunks),
               "types": types, "images": dict(Counter(i["role"] for c in cards for i in c["images"])),
               "status": dict(Counter(c["status"] for c in cards)),
               "gone_entries": [c["entry_no"] for c in cards if c["status"] == "gone"],
               "partial_entries": [c["entry_no"] for c in cards if c["status"] == "partial"],
               "gone_items": [(c["entry_no"], it["item_no"]) for c in cards for it in c["items"] if it["status"] == "gone"],
               "small_entries": [c["entry_no"] for c in cards if c["n_chars_small"]],
               "flags": flags, "body_max": max(c["n_chars"] for c in chunks if c["content_type"] == "story"),
               "small_max": max(c["n_chars"] for c in chunks if c["content_type"] != "story")}
    check(summary["items"] == 108, "Expected 108 items")
    check(types == {"story": 116, "fieldnote": 10, "interview": 6, "background": 6}, "Unexpected chunk counts")
    check(summary["images"] == {"index": 188, "story": 74}, "Unexpected image counts")
    check(summary["gone_entries"] == [14, 29, 34] and summary["partial_entries"] == [1], "Body-first status mismatch")
    check(summary["gone_items"] == [(1, 2), (14, 1), (29, 1), (34, 1)], "Body-first gone item mismatch")
    check(summary["small_entries"] == [3, 5, 6, 7, 11, 12, 14, 16, 17, 21, 22, 25, 26, 27, 28], "Sidebar assignment mismatch")
    check(flags == {"location_conflict": [11], "status_text_hint": [14, 74], "third_party_image": [1, 29],
                    "override": [2, 3, 14, 23], "section_species_unmatched": []}, "Review flag mismatch")
    check(summary["body_max"] == 1108, "Unexpected longest body")
    check(sorted(c["locator"]["entry_no"] for c in chunks if c["content_type"] == "story" and c["n_chars"] > 900) == [62, 82],
          "Unexpected long-body entries")
    # Known semantic regressions not covered by JSON Schema.
    check([c["n_chars"] for c in chunks if c["unit_id"] == "shbl:story-023"] == [540, 158], "Entry 23 split lengths")
    moved = [i for c in cards for i in c["images"] if i["image_id"] == "shbl-img-p076-1"]
    check(len(moved) == 1 and moved[0]["kind"] == "drawing" and
          any(i["image_id"] == "shbl-img-p076-1" for i in cards[2]["images"]), "Transferred image assignment")
    check(cards[99]["district"] == "太仓市", "Entry 100 district regression")
    return problems, summary


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ["entry_schema", "chunk_schema", "entries", "chunks", "stories"]:
        parser.add_argument(name, type=Path)
    parser.add_argument("--report", type=Path)
    args = parser.parse_args()
    problems, summary = validate(json.loads(args.entry_schema.read_text()), json.loads(args.chunk_schema.read_text()),
                                 read_jsonl(args.entries), read_jsonl(args.chunks), read_jsonl(args.stories),
                                 args.entries.resolve().parent.parent)
    report = {"problems": problems, "summary": summary}
    if args.report:
        args.report.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False, indent=2))
    print(f"{len(problems)} problems")
    raise SystemExit(bool(problems))


if __name__ == "__main__":
    main()
