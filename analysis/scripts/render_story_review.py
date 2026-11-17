"""Render a standalone, local-only HTML page for image and sidebar assignment review."""
import argparse
from collections import Counter
import html
import json
import os
from pathlib import Path

esc = lambda value: html.escape(str(value), quote=True)
STATUS = {"extant": "正文未认定已不存", "gone": "正文记载已不存", "partial": "部分已不存"}
TYPES = {"story": "正文", "fieldnote": "走访记录", "interview": "访谈", "background": "背景"}
KINDS = {"photo": "照片", "drawing": "线描", "painting": "画作", "map": "分布图", "document": "档案"}
FLAGS = {"location_conflict": "地点冲突", "status_text_hint": "状态证据提示",
         "third_party_image": "外馆藏品", "override": "已按规则调整归属",
         "section_species_unmatched": "树种归属待核实"}
# Suggestions are displayed for human review, never applied to the canonical JSONL.
KIND_CANDIDATES = {(7, 81): "drawing", (11, 84): "painting", (12, 85): "drawing",
                   (16, 90): "map", (18, 92): "document", (28, 103): "map"}


def candidate(card, image):
    if image["role"] != "story":
        return None
    # Entry 11 also has a photograph on the same page.
    if card["entry_no"] == 11 and not image["image_id"].endswith("-1"):
        return None
    if card["entry_no"] == 7 and image["image_id"] != "shbl-img-p081-5":
        return None
    return KIND_CANDIDATES.get((card["entry_no"], image["printed_page"]))


CSS = """
:root{color-scheme:light;--ink:#24382e;--muted:#647267;--line:#dce2d9;--paper:#f5f6f1;--accent:#40614a;--warn:#8a4d16}
*{box-sizing:border-box}body{margin:0;background:var(--paper);color:var(--ink);font:15px/1.7 -apple-system,BlinkMacSystemFont,"PingFang SC","Microsoft YaHei",sans-serif}
a{color:inherit}header{background:#e9eee3;border-bottom:1px solid var(--line);padding:38px max(24px,calc((100vw - 1260px)/2))}
.eyebrow{font-size:12px;letter-spacing:.14em;color:var(--muted)}h1{font-size:32px;line-height:1.3;margin:12px 0}
header p{max-width:900px;margin:12px 0}.stats{display:flex;gap:28px;flex-wrap:wrap;margin-top:24px}.stats strong{font-size:27px;margin-right:8px}.stats span{color:var(--muted)}
nav{position:sticky;top:0;z-index:2;background:#fffef8f2;border-bottom:1px solid var(--line);padding:14px 24px;display:flex;align-items:center;gap:12px;flex-wrap:wrap;backdrop-filter:blur(6px)}
input,select,button{font:inherit;padding:8px 12px;border:1px solid #bbc7b7;border-radius:7px;background:white;color:var(--ink)}
input[type=search]{flex:1;min-width:220px}button{cursor:pointer}button:hover{background:#eef2e8}#count{color:var(--muted);font-size:13px}
main{max-width:1308px;margin:28px auto;padding:0 24px}.entry{border:1px solid var(--line);border-radius:12px;padding:24px;margin-bottom:24px;background:#fff;scroll-margin-top:100px}
.entry[hidden]{display:none}.entry-head{display:flex;gap:16px;align-items:flex-start}.number{background:#45644d;color:#fff;font-size:21px;border-radius:50%;width:48px;height:48px;display:grid;place-items:center;flex:none}
h2{font-size:23px;line-height:1.4;margin:0 0 4px}h3{font-size:16px;margin:22px 0 12px}.meta{font-size:13px;color:var(--muted)}
.badge{display:inline-block;border-radius:4px;padding:2px 8px;font-size:12px;background:#eef3ea}.badge.gone,.badge.partial{background:#fff0de;color:#87501c}
.items{display:grid;grid-template-columns:repeat(auto-fit,minmax(280px,1fr));gap:12px;margin-top:18px}.item{background:#f7f8f3;border:1px solid #e4e9df;border-radius:7px;padding:12px 14px}.item p{margin:6px 0 0;font-size:13px;color:var(--muted)}
.flag{margin:10px 0;padding:9px 12px;border-left:3px solid #bc8640;background:#fff6e8;font-size:13px;color:#744811}
.gallery{display:grid;grid-template-columns:repeat(auto-fill,minmax(190px,1fr));gap:16px}.image{margin:0;border:1px solid var(--line);border-radius:7px;overflow:hidden;display:flex;flex-direction:column}
.image a{background:#f1f2ed;display:block}.image img{display:block;width:100%;height:220px;object-fit:contain}.image figcaption{padding:10px;font-size:12px;overflow-wrap:anywhere;line-height:1.6}.image .caption{white-space:pre-line;color:var(--muted);margin-top:5px}
.proposed{color:var(--warn);margin-top:5px;font-weight:600}.small-list{margin:8px 0;padding:0;list-style:none}.small-list li{padding:10px 0;border-bottom:1px solid #eef1eb}.small-list code{font-size:11px;color:var(--muted)}
details{margin-top:18px;border-top:1px solid var(--line);padding-top:12px}summary{cursor:pointer;color:var(--accent)}
.source{margin:16px 0}.source h4{margin:0 0 5px;font-size:14px}.source pre{white-space:pre-wrap;font-family:inherit;font-size:14px;line-height:1.9;margin:0;padding:12px;background:#f8f9f4}
.empty{color:var(--muted);font-size:13px}.notice{background:#fff8eb;border:1px solid #eadbbf;padding:16px;border-radius:8px}
footer{max-width:1260px;margin:30px auto;padding:0 24px 32px;font-size:13px;color:var(--muted)}
@media(max-width:600px){h1{font-size:26px}.entry{padding:17px}.gallery{grid-template-columns:repeat(2,minmax(0,1fr))}.image img{height:180px}.entry-head{gap:10px}.number{width:40px;height:40px;font-size:18px}.items{grid-template-columns:1fr}nav{position:static}.stats{gap:16px}h2{font-size:20px}}
@media print{nav{display:none}header{padding:20px}.entry{break-inside:avoid}details{display:none}body{background:#fff}}
"""


def render(entries_path, chunks_path, output):
    cards = [json.loads(s) for s in entries_path.read_text(encoding="utf-8").splitlines()]
    chunks = {c["chunk_id"]: c for c in map(json.loads, chunks_path.read_text(encoding="utf-8").splitlines())}
    base = entries_path.resolve().parent.parent
    statuses = Counter(c["status"] for c in cards)
    sections = []
    for card in cards:
        no, flags = card["entry_no"], card.get("review_flags", [])
        review = bool(flags) or any(candidate(card, im) for im in card["images"])
        text = [f'<section class="entry" id="entry-{no:03d}" data-district="{esc(card["district"])}" data-review="{int(review)}">',
                f'<div class="entry-head"><a class="number" href="#entry-{no:03d}">{no:02d}</a><div>',
                f'<h2>{esc(card["title"])}</h2><div>{esc(card["location"])}</div>',
                f'<div class="meta">{esc(card["district"])} · 正文第{esc("、".join(map(str, card["printed_pages"])))}页 · {esc(card["unit_id"])} · 待审核</div>',
                '</div></div><div class="items">']
        for item in card["items"]:
            text.append(f'<div class="item"><strong>树／树组 {item["item_no"]} · {esc("、".join(item["species"]))}</strong>'
                        f'<div>{esc(item["location"])}</div><span class="badge {item["status"]}">{STATUS[item["status"]]}</span>'
                        f'<p>{esc(item.get("status_note", ""))}</p></div>')
        text.append('</div>')
        for flag in flags:
            text.append(f'<div class="flag"><strong>{FLAGS[flag["code"]]}</strong>　{esc(flag["note"])}</div>')
        for role, title in [("index", "索引图片"), ("story", "正文图片")]:
            images = [im for im in card["images"] if im["role"] == role]
            if not images:
                continue
            text.append(f'<h3>{title} · {len(images)}</h3><div class="gallery">')
            for im in images:
                relative = Path(os.path.relpath(base / im["file"], output.resolve().parent)).as_posix()
                alt = f'第{no}条 {im["image_id"]}'
                proposed = candidate(card, im)
                text.append(f'<figure class="image"><a href="{esc(relative)}" target="_blank" rel="noopener">'
                            f'<img src="{esc(relative)}" alt="{esc(alt)}" loading="lazy"></a><figcaption>'
                            f'<strong>{esc(im["image_id"])}</strong><br>第{im["printed_page"]}页 · 当前：{KINDS[im["kind"]]}'
                            + (f' · 树／树组{im["item_no"]}' if "item_no" in im else '')
                            + (f'<div class="proposed">类型待确认：建议 {KINDS[proposed]}（尚未写入数据）</div>' if proposed else '')
                            + (f'<div class="caption">{esc(im["caption"])}</div>' if im.get("caption") else '')
                            + '<div class="meta">权利状态：' + ('其他机构，授权待核实' if im["rights"]["status"] == "third_party" else '待核实')
                            + '</div></figcaption></figure>')
            text.append('</div>')
        small = [child for child in card["children"] if child["content_type"] != "story"]
        text.append(f'<h3>小字块 · {len(small)}</h3>')
        if small:
            text.append('<ul class="small-list">')
            for child in small:
                c = chunks[child["chunk_id"]]
                excerpt = c["text"][:40] + ('…' if len(c["text"]) > 40 else '')
                text.append(f'<li><span class="badge">{TYPES[c["content_type"]]}</span> {esc(excerpt)}'
                            f'<br><code>{esc(c["chunk_id"])}</code> <span class="meta">· {c["n_chars"]}字 · 第'
                            f'{esc("、".join(map(str, c["locator"]["printed_pages"])))}页</span></li>')
            text.append('</ul>')
        else:
            text.append('<p class="empty">本条无独立小字块。</p>')
        text.append(f'<details><summary>展开全部子块原文与出处（{len(card["children"])}块）</summary>')
        for child in card["children"]:
            c = chunks[child["chunk_id"]]
            text.append(f'<div class="source"><h4>{esc(child["label"])} · 第{esc("、".join(map(str, c["locator"]["printed_pages"])))}页</h4>'
                        f'<div class="meta">{esc(c["chunk_id"])} · 资料时间 {esc(c["as_of"])}</div><pre>{esc(c["text"])}</pre></div>')
        text.append('</details></section>')
        sections.append("".join(text))
    districts = list(dict.fromkeys(c["district"] for c in cards))
    doc = '<!doctype html><html lang="zh-CN"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">'
    doc += '<title>树的故事 · 图片与小字归属核对</title><style>' + CSS + '</style></head><body>'
    doc += '<header><div class="eyebrow">吴文化博物馆 · 本地资料核对</div><h1>树的故事<br>图片与小字归属核对</h1>'
    doc += '<p>按正文优先口径整理。原文逐字保留，索引用于辅助定位与图片归属。所有条目尚待人工审核；图片类型建议只显示在本页，未改写正式数据。</p>'
    doc += f'<div class="stats"><div><strong>{len(cards)}</strong><span>条目</span></div><div><strong>{len(chunks)}</strong><span>子块</span></div>'
    doc += f'<div><strong>{sum(len(c["images"]) for c in cards)}</strong><span>图片</span></div></div></header>'
    doc += '<nav aria-label="核对筛选"><input id="search" type="search" placeholder="搜索地点、树种、编号或原文…" aria-label="搜索条目">'
    doc += '<select id="district" aria-label="区县"><option value="">全部区县</option>' + ''.join(
        f'<option>{esc(d)}</option>' for d in districts) + '</select>'
    doc += '<label><input id="flagged" type="checkbox"> 只看提示项</label><button id="reset" type="button">清除筛选</button><span id="count" aria-live="polite"></span></nav><main>'
    doc += f'<div class="notice">正文记载已不存：{statuses["gone"]}条；部分已不存：{statuses["partial"]}条；正文未认定已不存：{statuses["extant"]}条。'
    doc += '这些状态反映书中资料时间，不代表当前现场核实。旧规则误记第26、27条索引标注，已核对PDF纠正。第11条仍有正文标题与叙述地点冲突。</div><br>'
    doc += "".join(sections) + '</main><footer>仅在本地核对使用。全部图片通过相对路径读取，无外部字体、脚本或网络资源。原文与图像未上传。图片权利及正文展示授权仍待馆方确认。</footer>'
    doc += """<script>
const rows=[...document.querySelectorAll('.entry')], search=document.querySelector('#search'), district=document.querySelector('#district'), flagged=document.querySelector('#flagged');
const texts=rows.map(row=>row.textContent.toLowerCase());
function filter(){const q=search.value.trim().toLowerCase();let n=0;rows.forEach((row,i)=>{const show=(!q||texts[i].includes(q))&&(!district.value||row.dataset.district===district.value)&&(!flagged.checked||row.dataset.review==='1');row.hidden=!show;if(show)n++;});document.querySelector('#count').textContent='显示 '+n+' / '+rows.length+' 条';}
search.addEventListener('input',filter);district.addEventListener('change',filter);flagged.addEventListener('change',filter);
document.querySelector('#reset').addEventListener('click',()=>{search.value='';district.value='';flagged.checked=false;filter();});filter();
</script></body></html>"""
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(doc, encoding="utf-8")
    print(f"Wrote {output}: {len(cards)} sections, {sum(len(c['images']) for c in cards)} images")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ["entries", "chunks", "output"]:
        parser.add_argument(name, type=Path)
    args = parser.parse_args()
    render(args.entries, args.chunks, args.output)
