"""Extract the unmodified photo-index headings and placed images from the book.

Run with python -I index_story.py book.pdf index_story.jsonl images/story.
No entry overrides are applied here. Images are rendered in RGB from the page.
"""
import argparse
from collections import Counter, defaultdict
import json
from pathlib import Path

import pymupdf


def spans_of(page):
    flags = pymupdf.TEXTFLAGS_DICT & ~pymupdf.TEXT_PRESERVE_IMAGES
    return [s for b in page.get_text('dict', flags=flags)['blocks']
            for line in b.get('lines', []) for s in line['spans'] if s['text'].strip()]


def half_of(x, width):
    return int(x >= width / 2)


def index_heads(page, spans):
    locations = [s for s in spans if s['color'] == 0x64706D and 7.5 <= s['size'] <= 8.5]
    species = [s for s in spans if s['color'] == 0x8F8166 and s['size'] >= 12]
    statuses = [s for s in spans if s['color'] == 0x8F8166 and s['size'] < 12]
    numbers = [s for s in spans if s['font'].startswith('Times') and s['size'] >= 14
               and s['color'] == 0xFFFFFF and s['text'].strip().isdigit()]
    heads = []
    for loc in locations:
        x, y = loc['bbox'][:2]
        tree = [s for s in species if abs(s['bbox'][0] - x) < 3 and 0 <= s['bbox'][1] - y <= 15]
        if not tree:
            continue
        if len(tree) != 1:
            raise ValueError(f'Ambiguous species on PDF page {page.number + 1}: {loc}')
        tree = tree[0]
        nums = [s for s in numbers if 0 <= x - s['bbox'][0] <= 60 and abs(s['bbox'][1] - y) < 12]
        if len(nums) > 1:
            raise ValueError('Ambiguous entry number')
        no = int(nums[0]['text']) if nums else None
        heads.append({'entry_no': no, 'location': loc['text'].strip(),
                      'species_raw': tree['text'].strip(), 'status_raw': '',
                      'origin': 'index_main' if no is not None else 'index_sub',
                      'pdf_page': page.number + 1,
                      'printed_page': 2 * page.number + half_of(x, page.rect.width),
                      'x0': min(x, nums[0]['bbox'][0]) if nums else x, 'y0': y,
                      '_species_bbox': tree['bbox']})
    # Each annotation belongs to the nearest preceding species on the same half-page.
    # A y-only comparison incorrectly spreads entry 29's note onto entries 26/27.
    for status in statuses:
        sx, sy = status['bbox'][:2]
        cand = [h for h in heads if h['printed_page'] == 2 * page.number + half_of(sx, page.rect.width)
                and h['_species_bbox'][2] <= sx + 3 and abs(h['_species_bbox'][1] - sy) < 12]
        if cand:
            h = min(cand, key=lambda h: sx - h['_species_bbox'][2])
            h['status_raw'] += status['text'].strip()
    return heads


def caption_for(image, spans):
    x0, _, x1, y1 = image['bbox']
    ss = [s for s in spans if s['font'].startswith('FZKTK')
          and 0 <= s['bbox'][1] - y1 <= 30 and x0 - 3 <= s['bbox'][0] < x1]
    lines = []
    for s in sorted(ss, key=lambda s: (round(s['bbox'][1]), s['bbox'][0])):
        if not lines or abs(lines[-1][0] - s['bbox'][1]) > 2:
            lines.append([s['bbox'][1], []])
        lines[-1][1].append(s['text'])
    return [''.join(parts).strip() for _, parts in lines]


def extract(pdf, output, image_dir, *, strict_counts=True):
    records = {n: {'entry_no': n, 'heads': [], 'images': []} for n in range(1, 106)} if strict_counts else {}
    headings, found_images, maps = [], [], []
    with pymupdf.open(pdf) as doc:
        for pn in range(6, 38):
            page = doc[pn - 1]
            spans = spans_of(page)
            heads = index_heads(page, spans)
            headings.extend(heads)
            for info in page.get_image_info(xrefs=True):
                box = pymupdf.Rect(info['bbox'])
                if box.get_area() < 2000:
                    continue
                printed = 2 * page.number + half_of(box.x0, page.rect.width)
                candidates = [h for h in heads if h['printed_page'] == printed
                              and h['x0'] <= box.x0 + 10 and h['y0'] <= box.y0 + 10]
                if not candidates:
                    maps.append((pn, tuple(box)))
                    continue
                h = min(candidates, key=lambda h: box.y0 - h['y0'] + abs(box.x0 - h['x0']))
                found_images.append({'role': 'index', 'pdf_page': pn, 'printed_page': printed,
                                     '_head': h, '_info': info, '_caption': caption_for(info, spans)})
        previous = None
        for h in sorted(headings, key=lambda h: (h['printed_page'], round(h['y0'] / 60), h['x0'])):
            if h['entry_no'] is not None:
                previous = h['entry_no']
                if not strict_counts:
                    records.setdefault(previous, {'entry_no': previous, 'heads': [], 'images': []})
            if previous is None or previous not in records:
                raise ValueError('Invalid index order')
            h['_unit'] = previous
            h['head_no'] = len(records[previous]['heads']) + 1
            records[previous]['heads'].append({k: h[k] for k in
                ['head_no', 'location', 'species_raw', 'status_raw', 'origin', 'pdf_page', 'printed_page']})
        last_entry = None
        for pn in range(38, 82):
            page = doc[pn - 1]
            half_width = page.rect.width / 2
            def reading_key(x, y):
                half = half_of(x, page.rect.width)
                col = int(x >= half * half_width + half_width / 2 - 2)
                return (2 * page.number + half, col, y)
            events = []
            for s in spans_of(page):
                if s['font'].startswith('Times') and s['size'] >= 16 and s['color'] == 0xFFFFFF:
                    events.append((reading_key(*s['bbox'][:2]), 'head', int(s['text'])))
            for info in page.get_image_info(xrefs=True):
                if pymupdf.Rect(info['bbox']).get_area() >= 2000:
                    events.append((reading_key(*info['bbox'][:2]), 'image', info))
            for key, kind, data in sorted(events, key=lambda event: event[0]):
                if kind == 'head':
                    last_entry = data
                else:
                    if last_entry is None:
                        raise ValueError('Story image precedes first entry')
                    found_images.append({'role': 'story', 'pdf_page': pn, 'printed_page': key[0],
                                         '_unit': last_entry, '_info': data})
        by_page = defaultdict(list)
        for img in found_images:
            by_page[img['printed_page']].append(img)
        for printed, images in sorted(by_page.items()):
            images.sort(key=lambda im: (round(im['_info']['bbox'][1] / 20), round(im['_info']['bbox'][0])))
            for seq, img in enumerate(images, 1):
                info = img['_info']
                record = {'image_id': f'shbl-img-p{printed:03d}-{seq}', 'role': img['role']}
                if img['role'] == 'index':
                    record['head_no'] = img['_head']['head_no']
                record.update({'pdf_page': img['pdf_page'], 'printed_page': printed,
                               'bbox': [round(v, 1) for v in info['bbox']],
                               'native_px': [info['width'], info['height']]})
                if img.get('_caption'):
                    record['caption'] = '\n'.join(img['_caption'])
                    credits = [ln for ln in img['_caption'] if '藏' in ln]
                    if credits:
                        record['credit'] = '\n'.join(credits)
                unit = img['_head']['_unit'] if img['role'] == 'index' else img['_unit']
                records[unit]['images'].append(record)
                img['_record'] = record
        main_numbers = [h['entry_no'] for h in headings if h['entry_no'] is not None]
        summary = {'entries': len(records), 'heads': len(headings), 'main_heads': len(main_numbers),
                   'index_images': sum(i['role'] == 'index' for i in found_images),
                   'story_images': sum(i['role'] == 'story' for i in found_images), 'maps_skipped': len(maps),
                   'story_entries': len({i['_unit'] for i in found_images if i['role'] == 'story'}),
                   'index_image_distribution': dict(sorted(Counter(sum(i['role'] == 'index' for i in r['images'])
                                                                   for r in records.values()).items())),
                   'credits': [i['_record'] for i in found_images if i['_record'].get('credit')],
                   'gone_headings': [(h['_unit'], h['head_no'], h['status_raw']) for h in headings if '不存' in h['status_raw']]}
        print(json.dumps(summary, ensure_ascii=False, indent=2), flush=True)
        expected = {'entries': 105, 'heads': 107, 'main_heads': 105, 'index_images': 188,
                    'story_images': 74, 'maps_skipped': 11, 'story_entries': 45}
        if strict_counts and (sorted(main_numbers) != list(range(1, 106)) or any(summary[k] != v for k, v in expected.items())):
            raise ValueError('Index measurements differ from the manual; no output written')
        if strict_counts and (summary['index_image_distribution'] != {1: 35, 2: 58, 3: 11, 4: 1} or len(summary['credits']) != 2):
            raise ValueError('Image distribution or credits differ from the manual')
        if not main_numbers or len(main_numbers) != len(set(main_numbers)) or not found_images:
            raise ValueError('Missing or duplicate entry headings/images')
        image_dir.mkdir(parents=True, exist_ok=True)
        for img in found_images:
            info, rec = img['_info'], img['_record']
            box = pymupdf.Rect(info['bbox'])
            dpi = max(1, round(min(300, info['width'] / (box.width / 72))))
            pix = doc[rec['pdf_page'] - 1].get_pixmap(clip=box, dpi=dpi, colorspace=pymupdf.csRGB, alpha=False)
            target = image_dir / (rec['image_id'] + '.jpg')
            target.write_bytes(pix.tobytes('jpg', jpg_quality=85))
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(''.join(json.dumps(r, ensure_ascii=False) + '\n' for r in records.values()), encoding='utf-8')
    print(f'Wrote {len(records)} records and {len(found_images)} RGB JPEGs.')


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('pdf', type=Path)
    parser.add_argument('output', type=Path)
    parser.add_argument('image_dir', type=Path)
    parser.add_argument('--no-strict-counts', action='store_true')
    args = parser.parse_args()
    extract(args.pdf, args.output, args.image_dir, strict_counts=not args.no_strict_counts)
