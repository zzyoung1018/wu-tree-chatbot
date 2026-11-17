"""Rebuild the disposable SQLite retrieval database from the canonical JSONL files."""
import argparse
import hashlib
import json
from pathlib import Path
import re
import sqlite3


def grams(text):
    return [segment[i:i + 2] for segment in re.findall(r"\w+", text)
            for i in range(max(1, len(segment) - 1))]


def build(sql_path, entries_path, chunks_path, target, *, strict_counts=True):
    entry_lines = entries_path.read_text(encoding="utf-8").splitlines()
    chunk_lines = chunks_path.read_text(encoding="utf-8").splitlines()
    cards = [json.loads(line) for line in entry_lines]
    chunks = [json.loads(line) for line in chunk_lines]
    if not cards or not chunks or any(not c['items'] or not c['children'] for c in cards):
        raise ValueError('Empty corpus or parent')
    if any(not c['text'] or c['content_hash'] != 'sha256:' + hashlib.sha256(
            (c['context_header'] + '\n' + c['text']).encode()).hexdigest()[:16] for c in chunks):
        raise ValueError('Empty chunk or inconsistent content hash')
    refs = {child["chunk_id"]: (card["unit_id"], seq, child["item_no"])
            for card in cards for seq, child in enumerate(card["children"], 1)}
    if (set(refs) != {c["chunk_id"] for c in chunks} or len(refs) != len(chunks)
        or len(refs) != sum(len(c['children']) for c in cards)):
        raise ValueError("Parent and child IDs disagree")
    pipelines = {r["pipeline"] for r in cards + chunks}
    if len(pipelines) != 1:
        raise ValueError("Mixed pipeline versions")
    target.parent.mkdir(parents=True, exist_ok=True)
    pending = target.with_suffix(target.suffix + ".tmp")
    pending.unlink(missing_ok=True)
    j = lambda value: json.dumps(value, ensure_ascii=False)
    db = sqlite3.connect(pending)
    try:
        db.executescript(sql_path.read_text(encoding="utf-8"))
        with db:
            for card, line in zip(cards, entry_lines):
                db.execute("INSERT INTO entries VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
                           (card["unit_id"], card["entry_no"], card["title"], card["district"], card["location"],
                            card["status"], j(card["species"]), j(card["official_codes"]), j(card["tree_ids"]),
                            j(card["site_ids"]), card["review_status"], line))
                for item in card["items"]:
                    db.execute("INSERT INTO items VALUES (?,?,?,?,?,?)",
                               (card["unit_id"], item["item_no"], item["location"], j(item["species"]),
                                item["status"], item["status_source"]))
            for chunk, line in zip(chunks, chunk_lines):
                unit, seq, item_no = refs[chunk["chunk_id"]]
                if unit != chunk["unit_id"]:
                    raise ValueError("Wrong child parent")
                db.execute("INSERT INTO chunks VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                           (chunk["chunk_id"], unit, seq, item_no, chunk["content_type"], chunk["context_header"],
                            chunk["text"], j(chunk["locator"]["printed_pages"]), chunk["review_status"],
                            chunk["content_hash"], line))
                terms = " ".join([chunk["context_header"], chunk["text"], *chunk.get("search_terms", [])])
                db.execute("INSERT INTO chunks_fts (chunk_id, grams) VALUES (?,?)", (chunk["chunk_id"], " ".join(grams(terms))))
            for card in cards:
                for seq, im in enumerate(card["images"], 1):
                    db.execute("INSERT INTO images VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                               (im["image_id"], card["unit_id"], seq, im.get("item_no"), im["role"], im["file"],
                                im["printed_page"], im["kind"], im.get("caption"), im.get("credit"), im["rights"]["status"]))
            inputs = {entries_path.name: hashlib.sha256(entries_path.read_bytes()).hexdigest(),
                      chunks_path.name: hashlib.sha256(chunks_path.read_bytes()).hexdigest()}
            db.executemany("INSERT INTO meta VALUES (?,?)",
                           [("schema_version", "1"), ("pipeline", next(iter(pipelines))), ("input_sha256", j(inputs))])
        integrity, foreign_keys = db.execute("PRAGMA integrity_check").fetchall(), db.execute("PRAGMA foreign_key_check").fetchall()
        if integrity != [("ok",)] or foreign_keys:
            raise ValueError(f"Database checks failed: {integrity}, {foreign_keys}")
        counts = {table: db.execute(f"SELECT count(*) FROM {table}").fetchone()[0]
                  for table in ["entries", "items", "chunks", "chunks_fts", "images", "chunk_vectors", "meta"]}
        if strict_counts and counts != {"entries": 105, "items": 108, "chunks": 138, "chunks_fts": 138, "images": 262, "chunk_vectors": 0, "meta": 3}:
            raise ValueError(f"Unexpected database counts: {counts}")
    except BaseException:
        db.close()
        pending.unlink(missing_ok=True)
        raise
    db.close()
    pending.replace(target)
    print(json.dumps({"rows": counts, "integrity_check": "ok", "foreign_key_check": []}, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ["sql", "entries", "chunks", "database"]:
        parser.add_argument(name, type=Path)
    parser.add_argument('--no-strict-counts', action='store_true')
    args = parser.parse_args()
    build(args.sql, args.entries, args.chunks, args.database, strict_counts=not args.no_strict_counts)
