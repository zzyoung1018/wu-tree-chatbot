"""Check chunk files against schema/chunk.schema.json and the rules JSON Schema cannot express, then summarise them.

usage: python -I validate_chunks.py <chunk.schema.json> <chunks.jsonl> [<chunks.jsonl> ...]
dependency: pip install jsonschema
"""
import json
import statistics
import sys
from collections import Counter

import jsonschema

schema = json.load(open(sys.argv[1], encoding="utf-8"))
validator = jsonschema.Draft202012Validator(schema)
errors, chunks = [], []
for path in sys.argv[2:]:
    for i, line in enumerate(open(path, encoding="utf-8"), 1):
        c = json.loads(line)
        chunks.append(c)
        where = f"{path}:{i} {c.get('chunk_id')}"
        for err in validator.iter_errors(c):
            errors.append(f"{where}: /{'/'.join(map(str, err.path))} {err.message}")
        if not c.get("chunk_id", "").startswith(c.get("unit_id", "?") + ":"):
            errors.append(f"{where}: chunk_id does not start with unit_id")
        if c.get("part", 1) > c.get("parts", 1):
            errors.append(f"{where}: part > parts")
        if c.get("n_chars") != len(c.get("text", "")):
            errors.append(f"{where}: n_chars != len(text)")
        loc = c.get("locator", {})
        if "printed_pages" in loc and "pdf_pages" in loc \
                and sorted({p // 2 + 1 for p in loc["printed_pages"]}) != loc["pdf_pages"]:
            errors.append(f"{where}: pdf_pages do not match printed_pages")
        if c.get("conflict_ids") and c.get("review_status") == "verified" and not c.get("review_note"):
            errors.append(f"{where}: verified chunk with open conflicts needs a review_note")

ids = Counter(c.get("chunk_id") for c in chunks)
errors += [f"duplicate chunk_id {k} x{v}" for k, v in ids.items() if v > 1]
known = set(ids)
for c in chunks:
    for ref in c.get("faq", {}).get("evidence_chunk_ids", []) + ([c["dup_of"]] if c.get("dup_of") else []):
        if ref not in known:
            errors.append(f"{c['chunk_id']}: refers to unknown chunk {ref}")

print(f"{len(chunks)} chunks, {len(errors)} problems")
for e in errors[:50]:
    print("  " + e)
sizes = sorted(c["n_chars"] for c in chunks)
print("by type:", dict(Counter(c["content_type"] for c in chunks)))
print(f"n_chars: min {sizes[0]}, median {statistics.median(sizes):.0f}, p90 {sizes[int(len(sizes) * 0.9)]}, max {sizes[-1]}")
print("flags:", dict(Counter(f for c in chunks for f in c.get("certainty_flags", []))),
      "| sensitivity:", dict(Counter(s for c in chunks for s in c.get("sensitivity", []))))
print("link status:", dict(Counter(c.get("link", {}).get("status", "-") for c in chunks)),
      "| with conflicts:", [c["chunk_id"] for c in chunks if c.get("conflict_ids")])
sys.exit(1 if errors else 0)
