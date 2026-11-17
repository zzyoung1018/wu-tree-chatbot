"""Deterministic offline translation checks; warnings require editorial judgement.

Hard errors: missing/added Arabic values after documented numeric normalization,
plus missing/added official tree identifiers. Number words and approved dynasty
appositions are normalized; this is a screening tool, not a semantic verifier.
"""
import argparse
import hashlib
import json
import re
import sys
from decimal import Decimal
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'backend'))
from app.enrichment import read_corpus

# Codes retain their source spelling; numbers in these codes are checked separately.
CODES = re.compile(r'(?:吴中|吴江|姑苏|相城|昆山|常熟|太仓|张家港|高新|园区|吴|沧|昆|常|太|张|苏|相|平|金|虎|狮|网)\d{3,}')
ARABIC = re.compile(r'(?<!\w)\d+(?:,\d{3})*(?:\.\d+)?|(?<=[\u4e00-\u9fff])\d+(?:\.\d+)?')
CN_DIGITS = dict(zip('零〇一二两三四五六七八九', (0,0,1,2,2,3,4,5,6,7,8,9)))
CN_UNITS = {'十':10, '百':100, '千':1000, '万':10000, '亿':100000000}
CN_PATTERN = re.compile(r'[零〇一二两三四五六七八九十百千万亿]+')
QUANTITY = re.compile(r'([零〇一二两三四五六七八九十百千万亿]+)[余多]?(?:年|岁|棵|株|座|米|颗|人|处|首|代|层|间|次|个|月|日)')
EN_SMALL = dict(zip(('zero one two three four five six seven eight nine ten eleven twelve thirteen fourteen fifteen sixteen seventeen eighteen nineteen').split(),range(20)))
EN_SMALL.update(dict(zip(('twenty thirty forty fifty sixty seventy eighty ninety').split(),range(20,100,10))))
EN_SMALL.update({'first':1,'second':2,'third':3,'fourth':4,'fifth':5,'sixth':6,'seventh':7,'eighth':8,'ninth':9,'tenth':10,'twelfth':12,'twentieth':20})
EN_UNITS = {'hundred':100,'thousand':1000,'million':1000000}
EN_WORDS = re.compile(r'\b(?:'+'|'.join([*EN_SMALL,*EN_UNITS])+r')(?:[\s-]+(?:and[\s-]+)?(?:'+'|'.join([*EN_SMALL,*EN_UNITS])+r'))*\b',re.I)
ZH_HEDGES = ('相传','传说','据说','可能','似乎','或许','推测','猜测','存疑','未必','难以','无从','不知','据传','采访','回忆')
EN_HEDGES = re.compile(r'\b(?:legend|said to|reportedly|perhaps|possibly|possible|probably|may|might|seems?|suspect|guess|uncertain|unclear|difficult to|hard to|cannot|could not|according to|recall|interview|wonder)\b',re.I)
# Quoted characters/place-name comparisons intentionally retained in the mirror.
HAN_ALLOW = set('楸秋梓枫宋柏元柏清奇古怪吴中园区常昆沧狮网菉葭巷陆家镇之')


def number(value):
    return format(Decimal(str(value).replace(',', '')).normalize(), 'f')


def chinese_number(text):
    if not any(c in CN_UNITS for c in text):
        return int(''.join(str(CN_DIGITS[c]) for c in text))
    total, section, digit = 0, 0, 0
    for c in text:
        if c in CN_DIGITS:
            digit = CN_DIGITS[c]
        else:
            unit = CN_UNITS[c]
            if unit < 10000:
                section += (digit or 1) * unit
            else:
                total += (section + digit or 1) * unit
                section = 0
            digit = 0
    return total + section + digit


def english_numbers(text):
    values = set()
    for match in EN_WORDS.finditer(text):
        total, section = 0, 0
        words = re.findall(r'[a-z]+',match[0].lower())
        for word in words:
            if word in EN_SMALL:
                section += EN_SMALL[word]
            elif word == 'hundred':
                section = (section or 1) * 100
            elif word in EN_UNITS:
                total += (section or 1) * EN_UNITS[word]
                section = 0
        values.add(number(total + section))
    return values


def arabic_values(text):
    text = re.sub(r'(\d{4})\.(\d{1,2})\.(\d{1,2})', r'\1 \2 \3', text)
    return {number(m[0]) for m in ARABIC.finditer(CODES.sub('',text))}


def source_numbers(text):
    values = arabic_values(text) | {number(chinese_number(m[0])) for m in CN_PATTERN.finditer(text)}
    # Source abbreviations such as “90版” and “上世纪50年代” are expanded in English.
    for match in re.finditer(r'(\d{2})(版|年代)',text):
        base = 2000 if match[2] == '版' and int(match[1]) <= 26 else 1900
        values.add(number(base + int(match[1])))
    for match in re.finditer(r'上世纪([一二三四五六七八九十]+)年代', text):
        values.add(number(1900 + chinese_number(match[1])))
    # Chinese month numerals often become English month names.
    return values


def check_chunk(chunk, meta, glossary):
    zh, en = chunk['text'], meta['text_en']
    errors, warnings = [], []
    zcodes, ecodes = set(CODES.findall(zh)), set(CODES.findall(en))
    if zcodes != ecodes:
        errors.append({'check':'official_codes','missing':sorted(zcodes-ecodes),'added':sorted(ecodes-zcodes)})
    en_numbers = arabic_values(en) | english_numbers(en)
    months = ('january february march april may june july august september october november december').split()
    en_numbers |= {number(i+1) for i,m in enumerate(months) if re.search(r'\b'+m+r'\b',en,re.I)}
    missing = arabic_values(zh) - en_numbers
    for match in re.finditer(r'(\d{2})(版|年代)',zh):
        base = 2000 if match[2] == '版' and int(match[1]) <= 26 else 1900
        if number(base+int(match[1])) in en_numbers:
            missing.discard(number(match[1]))
    for match in re.finditer(r'(\d{2})世纪(\d{2})年代', zh):
        if number((int(match[1])-1)*100+int(match[2])) in en_numbers:
            missing.discard(number(match[1]))
            missing.discard(number(match[2]))
    if missing:
        errors.append({'check':'arabic_numbers_missing','values':sorted(missing)})
    # V4 explicitly allowed glossary-based date appositions. Report those separately.
    permitted_dates = set()
    for key, term in glossary.get('culture',{}).items():
        if key in zh:
            permitted_dates |= arabic_values(term['en'])
    for label, dates in [('北宋',{960,1127}),('南宋',{1127,1279}),('元',{1271,1368}),('雍正',{1723,1735}),('永乐',{1403,1424})]:
        if label in zh:
            permitted_dates |= {number(x) for x in dates}
    added = arabic_values(en) - source_numbers(zh) - permitted_dates
    if added:
        errors.append({'check':'arabic_numbers_added','values':sorted(added)})
    quantities = [{'source':m[0], 'value':chinese_number(m[1])} for m in QUANTITY.finditer(zh)
                  if number(chinese_number(m[1])) not in en_numbers]
    if quantities:
        warnings.append({'check':'chinese_quantities','items':quantities})
    zhedges = sum(zh.count(x) for x in ZH_HEDGES)
    ehedges = len(EN_HEDGES.findall(en))
    if zhedges > ehedges:
        warnings.append({'check':'qualifiers','chinese_markers':zhedges,'english_markers':ehedges})
    missing_terms = []
    for key, term in glossary['species'].items():
        if key in zh and not any(t.casefold() in en.casefold() for t in [term['en'],term['latin'],*term.get('aliases_en',[])]):
            missing_terms.append(key)
    if missing_terms:
        warnings.append({'check':'terminology','terms':missing_terms})
    han = sorted(set(re.findall(r'[\u4e00-\u9fff]',CODES.sub('',en))) - HAN_ALLOW)
    if han:
        warnings.append({'check':'remaining_han','characters':''.join(han)})
    paragraphs = lambda t: len([x for x in re.split(r'\n+',t.strip()) if x.strip()])
    if paragraphs(zh) != paragraphs(en):
        warnings.append({'check':'paragraph_count','zh':paragraphs(zh),'en':paragraphs(en)})
    ratio = len(en)/max(1,len(zh))
    if not 1.8 <= ratio <= 5.5:
        warnings.append({'check':'length_ratio','ratio':round(ratio,3)})
    return {'chunk_id':chunk['chunk_id'],'source_sha256':meta['source_sha256'],
            'text_en_sha256':hashlib.sha256(en.encode()).hexdigest(),'errors':errors,'review_items':warnings}


def report(directory):
    _, chunks = read_corpus(directory)
    asset = json.loads((directory/'enrichment.json').read_text())
    glossary = json.loads((directory/'glossary.json').read_text())
    rows = [check_chunk(c,asset['chunks'][c['chunk_id']],glossary) for c in chunks]
    return {'passed':not any(r['errors'] for r in rows), 'chunks':len(rows),
            'error_count':sum(len(r['errors']) for r in rows),
            'review_item_count':sum(len(r['review_items']) for r in rows),
            'policy':{'hard_fail':['official_codes','arabic_numbers_missing','arabic_numbers_added'],
                      'numeric_normalization':'commas, decimals, dotted dates, English number words/months, Chinese numerals, century/decade phrases, abbreviated editions (00–26 => 20xx; 27–99 => 19xx), glossary dynasty ranges',
                      'han_allowlist':''.join(sorted(HAN_ALLOW)),
                      'note':'Warnings are review prompts, not automatic mistranslation verdicts. Literary number metaphors and historical date conversion need review.'},
            'results':rows}


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--directory',type=Path,default=ROOT/'data/knowledge')
    parser.add_argument('--out',type=Path,default=ROOT/'state/improve-v5/translation-qa.json')
    args=parser.parse_args()
    data=report(args.directory)
    args.out.parent.mkdir(parents=True,exist_ok=True)
    args.out.write_text(json.dumps(data,ensure_ascii=False,indent=2)+'\n')
    print(json.dumps({k:v for k,v in data.items() if k not in ('results','policy')}))
    raise SystemExit(0 if data['passed'] else 1)
