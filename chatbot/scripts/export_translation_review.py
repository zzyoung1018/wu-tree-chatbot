"""Export a bilingual review CSV; import decisions actually entered by a reviewer.

Export never approves a passage. Import requires an explicit reviewer/date on each
approved row and exact source/translation hashes and texts. Changed translations
must be applied through overrides and re-exported before they can be signed off.
"""
import argparse
import csv
import hashlib
import json
import sys
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'backend'))
from app.enrichment import read_corpus, translation_review_state, validate_enrichment
from eval_common import dump

FIELDS = ['chunk_id','entry_no','title_zh','location_zh','text_zh','text_en',
          'translation_status','revision_reasons','source_sha256','text_en_sha256',
          'decision','reviewer','review_date','comments']


def csv_safe(text):
    text = str(text)
    return "'" + text if text.startswith(('=','+','-','@','\t','\r')) else text


def inputs(directory):
    cards, chunks = read_corpus(directory)
    asset = json.loads((directory/'enrichment.json').read_text())
    overrides_path = directory/'translation_overrides.json'
    overrides = json.loads(overrides_path.read_text()) if overrides_path.exists() else {}
    return cards, chunks, asset, overrides


def export_review(directory, output):
    cards, chunks, asset, overrides = inputs(directory)
    card_map = {c['unit_id']:c for c in cards}
    output.parent.mkdir(parents=True, exist_ok=True)
    with output.open('w',encoding='utf-8-sig',newline='') as f:
        writer = csv.DictWriter(f,fieldnames=FIELDS)
        writer.writeheader()
        for chunk in chunks:
            key = chunk['chunk_id']
            card, meta = card_map[chunk['unit_id']],asset['chunks'][key]
            reasons = '；'.join(e['reason'] for e in overrides.get('edits',[])
                               if e['scope']=='chunks' and e['key']==key)
            row = dict(chunk_id=key,entry_no=card['entry_no'],title_zh=card['title'],
                       location_zh=card['location'],text_zh=chunk['text'],text_en=meta['text_en'],
                       translation_status=meta.get('translation_status','machine'),
                       revision_reasons=reasons,source_sha256=meta['source_sha256'],
                       text_en_sha256=hashlib.sha256(meta['text_en'].encode()).hexdigest())
            writer.writerow({k:csv_safe(row.get(k,'')) for k in FIELDS})
    return {'exported':len(chunks),'path':str(output),'approved':0}


def import_review(directory, source):
    cards, chunks, asset, overrides = inputs(directory)
    chunk_map = {c['chunk_id']:c for c in chunks}
    approval_path = directory/'translation_approvals.json'
    approvals = json.loads(approval_path.read_text()) if approval_path.exists() else {
        'version':1,'database_sha256':asset['database_sha256'],'chunks':{}}
    if approvals.get('version')!=1 or approvals.get('database_sha256')!=asset['database_sha256']:
        raise ValueError('Review approvals belong to a different source database')
    seen, decisions = set(), []
    with source.open(encoding='utf-8-sig',newline='') as f:
        reader = csv.DictReader(f)
        if not set(FIELDS) <= set(reader.fieldnames or []):
            raise ValueError('Incomplete review CSV columns')
        for row in reader:
            key, decision = row['chunk_id'],row['decision'].strip().casefold()
            if key not in chunk_map or key in seen:
                raise ValueError('Unknown/duplicate review chunk: '+key)
            seen.add(key)
            if not decision:
                continue
            if decision not in {'通过','approved','修改','revise'}:
                raise ValueError('Decision must be 通过/approved or 修改/revise: '+key)
            meta = asset['chunks'][key]
            digest = hashlib.sha256(meta['text_en'].encode()).hexdigest()
            if (row['source_sha256']!=meta['source_sha256'] or row['text_en_sha256']!=digest
                    or row['text_zh']!=csv_safe(chunk_map[key]['text']) or row['text_en']!=csv_safe(meta['text_en'])):
                raise ValueError('Review text changed or stale; apply corrections and re-export: '+key)
            reviewer, day = row['reviewer'].strip(),row['review_date'].strip()
            if not reviewer or not day:
                raise ValueError('Reviewer and YYYY-MM-DD date are required: '+key)
            if date.fromisoformat(day)>date.today():
                raise ValueError('Review date cannot be in the future: '+key)
            approved = decision in {'通过','approved'}
            if not approved and not row['comments'].strip():
                raise ValueError('Revision requests require comments: '+key)
            decisions.append((key,dict(decision='approved' if approved else 'revision_requested',
                                      reviewer=reviewer,date=day,comments=row['comments'],
                                      source_sha256=meta['source_sha256'],text_en_sha256=digest)))
    for key, decision in decisions:
        approvals['chunks'][key]=decision
    translation_review_state(asset,overrides,approvals)
    glossary=json.loads((directory/'glossary.json').read_text())
    validate_enrichment(asset,glossary,cards,chunks,asset['database_sha256'])
    # All rows must validate before either file changes.
    dump(approval_path,approvals)
    dump(directory/'enrichment.json',asset)
    return {'imported_decisions':len(decisions),'translation_review':asset['translation_review']}


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--directory',type=Path,default=ROOT/'data/knowledge')
    parser.add_argument('--out',type=Path,default=ROOT/'state/improve-v5/translation-review.csv')
    parser.add_argument('--import',dest='import_path',type=Path)
    args=parser.parse_args()
    result=import_review(args.directory,args.import_path) if args.import_path else export_review(args.directory,args.out)
    print(json.dumps(result,ensure_ascii=False))
