"""Run source-preservation, retrieval, corruption and optional reproducibility checks.

python -I scripts/check_story_pipeline.py --rebuild-twice
"""
import argparse
from copy import deepcopy
import hashlib
import importlib.util
import json
from pathlib import Path
import re
import sqlite3
import subprocess
import sys
import tempfile

import pymupdf


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def module_at(path, name):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--rebuild-twice", action="store_true")
    args = parser.parse_args()
    base = Path(__file__).resolve().parent.parent
    pdf = base.parent / "树碑立传：吴中古树名木的故事教育读物(1).pdf"
    def run(script, *arguments):
        result = subprocess.run([sys.executable, "-I", str(base / "scripts" / script), *map(str, arguments)],
                                cwd=base, text=True, capture_output=True)
        if result.returncode:
            raise RuntimeError(result.stdout + result.stderr)
        return result.stdout
    tracked = ["index_story.jsonl", "entries_story.jsonl", "chunks_story.jsonl"]
    report = {}
    # Validate current code against the baseline without rewriting protected data.
    with tempfile.TemporaryDirectory(prefix='story-baseline-') as directory:
        directory = Path(directory)
        run('build_story_chunks.py', base / 'pilot/stories.jsonl', base / 'pilot/index_story.jsonl',
            directory / 'chunks_story.jsonl', directory / 'entries_story.jsonl')
        for name in ('entries_story.jsonl', 'chunks_story.jsonl'):
            assert digest(directory / name) == digest(base / 'pilot' / name), f'Byte-level baseline drift: {name}'
        report['default_build_matches_baseline'] = True
    if args.rebuild_twice:
        snapshots, image_snapshots = [], []
        for iteration in [1, 2]:
            print(f"Reproducibility build {iteration}/2", flush=True)
            run("index_story.py", pdf, "pilot/index_story.jsonl", "pilot/images/story")
            run("build_story_chunks.py", "pilot/stories.jsonl", "pilot/index_story.jsonl",
                "pilot/chunks_story.jsonl", "pilot/entries_story.jsonl")
            snapshots.append({name: digest(base / "pilot" / name) for name in tracked})
            image_snapshots.append({p.name: digest(p) for p in sorted((base / "pilot/images/story").glob("*.jpg"))})
        assert snapshots[0] == snapshots[1], "JSONL changed between identical builds"
        assert image_snapshots[0] == image_snapshots[1], "JPEG bytes changed between identical builds"
        report["reproducibility"] = {"jsonl_equal": True, "images_equal": True, "sha256": snapshots[1],
            "image_manifest_sha256": hashlib.sha256(json.dumps(image_snapshots[1], sort_keys=True).encode()).hexdigest()}
        run("load_story_db.py", "schema/story_db.sql", "pilot/entries_story.jsonl", "pilot/chunks_story.jsonl", "pilot/story.db")
        run("render_story_review.py", "pilot/entries_story.jsonl", "pilot/chunks_story.jsonl", "pilot/review/story_assignments.html")
    run("validate_story.py", "schema/entry.schema.json", "schema/story_chunk.schema.json",
        "pilot/entries_story.jsonl", "pilot/chunks_story.jsonl", "pilot/stories.jsonl",
        "--report", "pilot/story_validation.json")
    run("retrieve_demo.py", "pilot/story.db", "--self-test", "--report", "pilot/story_smoke_tests.json")
    print("Schema, coverage, database and smoke checks passed", flush=True)
    baseline = json.loads((base / "pilot/story_baseline.json").read_text())
    for relative, data in baseline.items():
        assert digest(base.parent / relative) == data["sha256"], f"Protected input modified: {relative}"
    report["protected_inputs_unchanged"] = list(baseline)
    validator = module_at(base / "scripts/validate_story.py", "story_validator")
    retrieve = module_at(base / "scripts/retrieve_demo.py", "story_retriever")
    cards = validator.read_jsonl(base / "pilot/entries_story.jsonl")
    chunks = validator.read_jsonl(base / "pilot/chunks_story.jsonl")
    stories = validator.read_jsonl(base / "pilot/stories.jsonl")
    entry_schema = json.loads((base / "schema/entry.schema.json").read_text())
    chunk_schema = json.loads((base / "schema/story_chunk.schema.json").read_text())
    def problems(cc, ch):
        return validator.validate(entry_schema, chunk_schema, cc, ch, stories, base)[0]
    # Tamper with text while keeping both len and hash internally valid.
    bad = deepcopy(chunks)
    bad[0]["text"] += "X"
    bad[0]["n_chars"] = len(bad[0]["text"])
    bad[0]["content_hash"] = "sha256:" + hashlib.sha256((bad[0]["context_header"] + "\n" + bad[0]["text"]).encode()).hexdigest()[:16]
    assert any("source coverage" in p for p in problems(cards, bad)), "Source tampering went undetected"
    bad_cards = deepcopy(cards)
    bad_cards[0]["children"][0]["item_no"] = 999
    assert any("invalid item pointer" in p for p in problems(bad_cards, chunks)), "Invalid item pointer went undetected"
    bad = deepcopy(chunks)
    bad[0]["content_hash"] = "sha256:" + "0" * 16
    assert any("content_hash" in p for p in problems(cards, bad)), "Stale hash went undetected"
    report["corruption_checks"] = ["source tampering despite valid hash rejected", "invalid item reference rejected", "stale hash rejected"]
    db = sqlite3.connect((base / "pilot/story.db").as_uri() + "?mode=ro", uri=True)
    rows = {r[0]: r[1] for r in db.execute("SELECT chunk_id, chunk FROM chunks")}
    assert rows == {c["chunk_id"]: line for c, line in zip(chunks, (base / "pilot/chunks_story.jsonl").read_text().splitlines())}
    assert dict(db.execute("SELECT unit_id, card FROM entries")) == {
        c["unit_id"]: line for c, line in zip(cards, (base / "pilot/entries_story.jsonl").read_text().splitlines())}
    assert json.loads(db.execute("SELECT value FROM meta WHERE key='input_sha256'").fetchone()[0]) == {
        name: digest(base / "pilot" / name) for name in ["entries_story.jsonl", "chunks_story.jsonl"]}
    assert retrieve.retrieve(db, "！！！")["parents"] == []
    assert retrieve.retrieve(db, "树洞口和断口怎么清理", verified_only=True)["parents"] == []
    expanded = retrieve.retrieve(db, "树洞口和断口怎么清理", k_child=1, k_parent=1)
    minimal = deepcopy(expanded["parents"])
    minimal[0]["chunks"] = [c for c in minimal[0]["chunks"] if c["chunk_id"] in minimal[0]["hit_ids"]]
    min_budget = len(retrieve.render(minimal)[0]) + 20
    trimmed = retrieve.retrieve(db, "树洞口和断口怎么清理", k_child=1, k_parent=1, budget=min_budget)
    assert trimmed["dropped_unmatched"] and trimmed["n_chars"] <= min_budget
    assert set(expanded["parents"][0]["hit_ids"]).issubset(c["chunk_id"] for c in trimmed["parents"][0]["chunks"])
    assert [c["text"] for c in trimmed["parents"][0]["chunks"]] == [
        rows_c["text"] for rows_c in chunks if rows_c["chunk_id"] in {c["chunk_id"] for c in trimmed["parents"][0]["chunks"]}]
    try:
        retrieve.retrieve(db, "树洞口和断口怎么清理", budget=1)
    except ValueError:
        pass
    else:
        raise AssertionError("Impossible budget was silently exceeded")
    memory = sqlite3.connect(":memory:")
    db.backup(memory)
    memory.execute("UPDATE chunks SET review_status='rejected', chunk=json_set(chunk,'$.review_status','rejected') WHERE unit_id='shbl:story-026'")
    rejected = retrieve.retrieve(memory, "古树受伤了怎么保护")
    assert all(p["card"]["unit_id"] != "shbl:story-026" for p in rejected["parents"])
    memory.close()
    memory = sqlite3.connect(":memory:")
    db.backup(memory)
    # A rejected sibling must not leak in through parent expansion.
    memory.execute("UPDATE chunks SET review_status='rejected', chunk=json_set(chunk,'$.review_status','rejected') WHERE chunk_id='shbl:story-002:body-1'")
    sibling = retrieve.retrieve(memory, "三官殿的龙柏")
    assert all(c["chunk_id"] != "shbl:story-002:body-1" for p in sibling["parents"] for c in p["chunks"])
    memory.close()
    db.close()
    report["retrieval_checks"] = ["punctuation-only query empty", "pending excluded in verified-only mode",
                                  "rejected hits excluded", "rejected siblings excluded",
                                  "budget trimming preserves hit text", "impossible budget fails explicitly"]
    valid_ids = {c["chunk_id"] for c in chunks}
    external_refs = {}
    for path in [base / "RAG切块要素设计.md", base / "pilot/chunk_examples.jsonl"]:
        found = sorted(set(re.findall(r"shbl:story-[0-9]{3}:(?:body|fieldnote|interview|background)-[0-9]+", path.read_text())))
        assert set(found).issubset(valid_ids), f"Dangling references in {path.name}"
        external_refs[path.name] = found
    evidence = next(c for c in chunks if c["chunk_id"] == "shbl:story-018:body-1")
    assert "“邓禹手植”“乾隆赐名”等说法并无明确依据" in evidence["text"], "FAQ evidence no longer supports its claim"
    report["external_references"] = external_refs
    old_chunks = validator.read_jsonl(base / "pilot/chunks_story.old.jsonl")
    old_ids = {c["chunk_id"] for c in old_chunks}
    old_by_id = {c["chunk_id"]: c for c in old_chunks}
    report["id_migration"] = {"old_count": len(old_chunks), "new_count": len(chunks),
                             "removed": sorted(old_ids - valid_ids), "added": sorted(valid_ids - old_ids),
                             "text_changed_at_same_id": [c["chunk_id"] for c in chunks if c["chunk_id"] in old_ids
                                                        and c["text"] != old_by_id[c["chunk_id"]]["text"]]}
    files = sorted((base / "pilot/images/story").glob("*.jpg"))
    assert len(files) == 262
    for image in files:
        pix = pymupdf.Pixmap(image)
        assert pix.colorspace.n == 3 and pix.width > 0 and pix.height > 0, str(image)
    report["images"] = {"count": len(files), "all_rgb_and_decodable": True, "bytes": sum(p.stat().st_size for p in files)}
    report["passed"] = True
    out = base / "pilot/story_checks.json"
    out.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
