# 树的故事：父子块切分规则（Agent 操作手册）

> **版本**：v2，2026-11-04。
>
> **适用范围**：《树碑立传》"树的故事"章节，共 105 个条目，PDF 第 6–81 页。
>
> **自足**：本文件是独立的。完成这项工作需要的规则、常量、数据格式、schema、建库语句和验收标准都写在这里，不需要参考其他文档。
>
> **数字**：文中所有数字都在 2026-11-04 用原型脚本对实际 PDF 量过，可以直接当验收标准。

---

## 0. 给 Agent 的说明

- **怎么做**：按 §8 的步骤顺序做，每步做完对照该步的"完成标准"自查，全部做完再跑 §9 的验收。
- **对不上就停**：规则写不清楚，或数字对不上时，停下来报告差异。不要为了凑数改规则或改数据。
- **特例**：§6 的覆盖表是已经确认要处理的特例，照做即可。§11 是需要人来决定的问题，不要自行决定。
- **目录和版本管理**：工作目录是 `analysis/`（下文路径都相对于它）。项目不是 git 仓库，不要 `git init`，也不要提交。
- **文字来源**：只读 PDF，不修改它。不调用大模型生成或改写文字，所有文字必须逐字来自书。
- **以本文为准**：仓库里已有的脚本可以作为起点，但规则以本文为准；两者不一致时按本文改脚本。

---

## 1. 方案一句话

- **父 = 条目卡**：每个条目一张，汇总头部信息、全部子块的清单和图片。条目卡不建索引。
- **子 = 正文块和小字块**：真正建索引、参与检索的单位。
- **检索时**：命中子块 → 按 `unit_id` 归并到条目 → 把整张条目卡交给模型 → 回答引用到子块所在的页码。

业界把这种做法叫 parent-child 或 small-to-big retrieval，参考 LangChain 的 `ParentDocumentRetriever` 和 LlamaIndex 的 small-to-big。本项目数据量小，自己实现，不引入框架。

```
条目卡 shbl:story-002（父，不建索引）
├─ 头部：第2条｜吴中区｜标题"香樟"｜官方编号 吴中009
├─ items（条目里的树，来自索引页）
│   ├─ 1  金庭镇爱国村东湾井场｜香樟
│   └─ 2  金庭镇三官殿｜龙柏            ← 索引页上没有编号的副标题
├─ 子块（建索引）
│   ├─ shbl:story-002:body-1   正文·香樟 → item 1
│   └─ shbl:story-002:body-2   正文·龙柏 → item 2，头部地点写三官殿
└─ 图片：索引页 3 张（item 1 一张，item 2 两张）
```

---

## 2. 环境、页码、输入、输出

### 2.1 环境

- **依赖**：Python ≥ 3.10（`sqlite3` 模块需带 FTS5，常见发行版都带）、pymupdf ≥ 1.24（实测 1.28.2）、jsonschema ≥ 4.18（实测 4.26）。
- **建环境**：没有现成环境时，在项目目录之外建一个，例如：

  ```bash
  uv venv --python 3.13 ~/.venvs/wu-museum
  ```

  ```bash
  uv pip install --python ~/.venvs/wu-museum --only-binary :all: pymupdf jsonschema
  ```

- **检查 FTS5**：

  ```bash
  python -I -c "import sqlite3; sqlite3.connect(':memory:').execute('create virtual table t using fts5(x)'); print('fts5 ok')"
  ```

- **运行方式**：所有脚本都用 `python -I` 运行（隔离模式，不从当前目录导入模块），路径用参数传入。

### 2.2 页码换算

- **换算公式**：PDF 第 n 页（从 1 起）是一个跨页，左半是印刷页 2(n−1)，右半是 2(n−1)+1。页脚印的页码与此一致。反过来，印刷页 p 在 PDF 第 `p // 2 + 1` 页。
- **索引**：PDF 6–37，即印刷页 10–73。每个条目有一个带编号圆圈的标题和 1–4 张照片。
- **正文**：PDF 38–81，即印刷页 74–161。
- **区县分隔页**：PDF 6、14、21、25、26、27、28、31、36、37。每页有一张区县地图（第 31 页两张），地图上印着本区县的全部条目编号。区县表（附录 A 的 `DISTRICTS`）就是按这些地图定的。

### 2.3 输入

| 文件 | 说明 |
|---|---|
| `../树碑立传：吴中古树名木的故事教育读物(1).pdf` | 原书，排版软件导出，文字可直接提取 |
| `pilot/stories.jsonl` | 正文页的结构化抽取结果，105 行，每行一个条目。由 `scripts/pilot_stories.py` 生成，本次不改，格式见下表 |

`stories.jsonl` 每行的字段（本手册只用到下面这些）：

| 字段 | 内容 |
|---|---|
| `entry_no` | 条目编号，1–105 |
| `location` / `title` | 正文页标题处印的地点和标题 |
| `blocks` | 正文，按阅读顺序排列。每项为 `{type: "para" 或 "subhead", text, printed_pages}`，`subhead` 是树种小标题 |
| `subheads` | 树种小标题列表 |
| `text` | 全部 `para` 的文本，用 `\n` 连接 |
| `chars` | `len(text)` |
| `sidebars` | 小字框列表，每项为 `{text, printed_pages}`，框内各行用 `\n` 连接 |
| `captions` | 图注列表，每项为 `{text, printed_pages}`，全书只有 3 条 |
| `status_gone` | 正文关键词判断的"已不存"。**不要使用**，状态规则见 R7 |

### 2.4 输出

| 文件 | 动作 | 内容 | 预期 |
|---|---|---|---|
| `scripts/index_story.py` | 新建 | 解析索引页头部和两类页面上的图片，导出图片 | — |
| `scripts/build_story_chunks.py` | 改写为 v2 | 切子块，生成条目卡 | — |
| `scripts/validate_story.py` | 新建 | 用两个 schema 校验，并做父子一致性检查 | 0 problems |
| `scripts/load_story_db.py` | 新建 | 把 JSONL 装进 SQLite 检索库 | — |
| `scripts/retrieve_demo.py` | 新建 | 在检索库上演示子块检索和按条目归并 | 8 个冒烟问题全部通过 |
| `schema/entry.schema.json` | 新建 | 条目卡 schema，按附录 B 原样保存 | — |
| `schema/story_chunk.schema.json` | 新建 | 子块 schema，按附录 C 原样保存 | — |
| `schema/story_db.sql` | 新建 | 检索库建表语句，按 §3.4 原样保存 | — |
| `pilot/index_story.jsonl` | 新输出 | 每个条目一行：索引头部和图片位置（未应用覆盖） | 105 行 |
| `pilot/entries_story.jsonl` | 新输出 | 条目卡 | 105 行 |
| `pilot/chunks_story.jsonl` | 覆盖旧文件 | 子块 | 138 行 |
| `pilot/images/story/*.jpg` | 新输出 | 按版面裁切渲染的图片 | 262 个文件，约 42 MB |
| `pilot/story.db` | 新输出 | 检索库，由 JSONL 生成，可随时重建 | — |
| `pilot/review/story_assignments.html` | 新输出 | 人工核对页，展示每个条目的图片和小字归属 | 105 节 |

---

## 3. 数据存储形式

### 3.1 三层结构

数据分三层，每一层都由上一层用脚本生成：

| 层 | 形式 | 作用 | 能否手改 |
|---|---|---|---|
| 原件 | PDF | 唯一的原始来源 | 不改 |
| 中间文件 | JSONL（每行一条 JSON 记录）和 JPG 图片 | **事实来源**：可以逐行比对、审阅、存档。条目卡和子块的正式版本在这里 | 不手改。要改就改脚本或覆盖表，然后重跑 |
| 检索库 | SQLite 单文件 `pilot/story.db` | 供问答服务查询：关键词索引、向量、按条目取卡 | 不手改。删掉后随时可以由中间文件重建 |

为什么这样分：

- JSONL 是纯文本，能用 schema 逐行校验，改动一目了然，agent 和人都容易处理。
- SQLite 是单个文件，不需要部署服务，Python 自带，内置 FTS5 全文检索和 JSON 函数。
- 全书切完约 550 个子块，问答时把向量全部读进内存算余弦就够快，不需要专门的向量数据库。

### 3.2 目录结构

```
analysis/
├─ schema/
│   ├─ entry.schema.json          条目卡 schema（附录 B）
│   ├─ story_chunk.schema.json    子块 schema（附录 C）
│   └─ story_db.sql               检索库建表语句（§3.4）
├─ scripts/
│   ├─ pilot_stories.py           已有，不改：PDF 正文页 → stories.jsonl
│   ├─ index_story.py             新建：PDF 索引页和图片 → index_story.jsonl、images/
│   ├─ build_story_chunks.py      v2：stories + index_story → 子块、条目卡
│   ├─ validate_story.py          新建：校验
│   ├─ load_story_db.py           新建：JSONL → story.db
│   └─ retrieve_demo.py           新建：检索演示
└─ pilot/
    ├─ stories.jsonl              输入，105 行
    ├─ index_story.jsonl          中间结果，105 行
    ├─ entries_story.jsonl        父：条目卡，105 行
    ├─ chunks_story.jsonl         子：子块，138 行
    ├─ images/story/*.jpg         图片，262 张
    ├─ story.db                   检索库
    └─ review/story_assignments.html
```

### 3.3 JSONL 约定

- **编码与格式**：UTF-8，无 BOM。每行一个 JSON 对象，用 `json.dumps(obj, ensure_ascii=False)` 写出（默认分隔符），文件以换行结尾。
- **字段顺序**：与 §4 字段表的顺序一致。
- **行的顺序**：条目卡和 `index_story` 按 `entry_no` 排；子块先按 `entry_no`，再按条目卡 `children` 的顺序排。
- **空字段**：可选字段为空（空数组、空字符串、null）时不写。必填的数组即使为空也要写，例如 `tree_ids`、`site_ids`。
- **可重复**：同样的输入必须产出逐字节相同的文件。

三种 JSONL 记录：

**`index_story.jsonl`**：只记录从 PDF 读到的原始结果，不应用 §6 的覆盖。第 2 条那一行：

```json
{"entry_no": 2,
 "heads": [{"head_no": 1, "location": "金庭镇爱国村东湾井场", "species_raw": "香樟", "status_raw": "", "origin": "index_main", "pdf_page": 7, "printed_page": 12},
           {"head_no": 2, "location": "金庭镇三官殿", "species_raw": "龙柏", "status_raw": "", "origin": "index_sub", "pdf_page": 7, "printed_page": 13}],
 "images": [{"image_id": "shbl-img-p012-4", "role": "index", "head_no": 1, "pdf_page": 7, "printed_page": 12, "bbox": [188.9, 310.6, 335.5, 466.5], "native_px": [612, 650]},
            {"image_id": "shbl-img-p013-1", "role": "index", "head_no": 2, "pdf_page": 7, "printed_page": 13, "bbox": [564.1, 40.9, 708.7, 241.1], "native_px": [603, 835]},
            {"image_id": "shbl-img-p013-2", "role": "index", "head_no": 2, "pdf_page": 7, "printed_page": 13, "bbox": [401.5, 83.1, 558.4, 241.1], "native_px": [654, 659]},
            {"image_id": "shbl-img-p076-1", "role": "story", "pdf_page": 39, "printed_page": 76, "bbox": [134.9, 262.6, 376.4, 433.4], "native_px": [712, 1007]}]}
```

（为便于阅读，这里折了行；文件里是一行。最后那张正文页图片按 §6 会移到第 3 条。）

**`entries_story.jsonl`**：一行一张条目卡，完整示例见 §4.1。

**`chunks_story.jsonl`**：一行一个子块。第 2 条 body-2（`text` 截断显示，`n_chars` 和 `content_hash` 按全文计算）：

```json
{"chunk_id": "shbl:story-002:body-2", "unit_id": "shbl:story-002", "part": 2, "parts": 2, "source_id": "SRC-SHBL",
 "content_type": "story", "text": "本地树龄最长的一棵龙柏，坐落在金庭镇三官殿，……",
 "context_header": "《树碑立传》树的故事 第2条｜吴中区 金庭镇三官殿｜龙柏", "lang": "zh-Hans", "n_chars": 398,
 "locator": {"section": "树的故事", "entry_no": 2, "title": "香樟", "heading_path": ["树的故事", "2 金庭镇爱国村东湾井场·香樟", "龙柏"], "pdf_pages": [39], "printed_pages": [76]},
 "tree_ids": [], "site_ids": [], "site_name": "金庭镇三官殿", "district": "吴中区", "species": ["龙柏"], "official_codes": ["吴中009"],
 "link": {"status": "unlinked"}, "source_tier": "B", "as_of": "2024-08", "review_status": "pending", "sensitivity": ["religion"],
 "rights": {"holder": "吴文化博物馆", "display": "full", "note": "馆方尚未确认可否在 AI 应用中引用原文"},
 "content_hash": "sha256:d1f4bcaf47a5f985", "pipeline": "pilot_stories@3+index_story@1+build_story_chunks@2"}
```

### 3.4 检索库 `pilot/story.db`

| 表 | 一行是什么 | 行数 | 用途 |
|---|---|---|---|
| `entries` | 一张条目卡 | 105 | 命中子块后按 `unit_id` 取整张卡，`card` 列存条目卡整行 JSON |
| `items` | 条目里的一棵树或一组树 | 108 | 按地点、树种、状态查询，例如"哪些树今已不存" |
| `chunks` | 一个子块 | 138 | 检索结果的主体，`chunk` 列存子块整行 JSON |
| `chunks_fts` | 一个子块的关键词索引 | 138 | FTS5 全文检索 |
| `chunk_vectors` | 一个子块的向量 | 0，向量化后为 138 | 向量检索 |
| `images` | 一张图片的元数据 | 262 | 界面展示；图片文件在 `pilot/images/story/` |
| `meta` | 一项构建信息 | 3 | `schema_version`、`pipeline`、输入 JSONL 的 sha256 |

规则：

- **冗余列**：`card` 和 `chunk` 列保存 JSONL 整行，其余列是为了过滤和连接而抽出来的冗余列。冗余列加载时直接从 JSON 取值，不另行计算。
- **关键词索引**：SQLite 自带的分词器不切中文，会把一整句当成一个词。所以把 `context_header`、`text` 和 `search_terms` 拼起来，拆成相邻两个字的组合（字符二元组），用空格隔开存进 `grams` 列：用正则 `\w+` 找出连续片段，每段取所有相邻两个字，只有一个字的片段原样保留。例如"三官殿"拆成"三官 官殿"，"吴中203"拆成"吴中 中2 20 03"。查询时同样拆分，用 OR 连接，按 `bm25()` 排序。实测 8 个冒烟问题全部排第 1。
- **向量**：用 BLOB 存小端 float32、已归一化的向量。`content_hash` 与 `chunks` 表不一致时，这条向量作废，需要重算。向量化不在本手册的必做步骤里，表先建好、留空。
- **条目卡不进检索**：`entries` 不进 `chunks_fts`，也没有向量。

建表语句，原样保存为 `schema/story_db.sql`：

```sql
-- story.db：由 JSONL 生成的检索库，可随时删掉重建
PRAGMA foreign_keys = ON;

CREATE TABLE meta (
  key   TEXT PRIMARY KEY,
  value TEXT NOT NULL
);

-- 父：条目卡。不进关键词索引，也没有向量
CREATE TABLE entries (
  unit_id        TEXT PRIMARY KEY,                  -- shbl:story-002
  entry_no       INTEGER NOT NULL UNIQUE,
  title          TEXT NOT NULL,
  district       TEXT NOT NULL,
  location       TEXT NOT NULL,
  status         TEXT NOT NULL CHECK (status IN ('extant', 'gone', 'partial')),
  species        TEXT NOT NULL,                     -- JSON 数组
  official_codes TEXT NOT NULL,                     -- JSON 数组
  tree_ids       TEXT NOT NULL,                     -- JSON 数组
  site_ids       TEXT NOT NULL,                     -- JSON 数组
  review_status  TEXT NOT NULL,
  card           TEXT NOT NULL                      -- entries_story.jsonl 的整行，原样
);

CREATE TABLE items (
  unit_id       TEXT NOT NULL REFERENCES entries(unit_id),
  item_no       INTEGER NOT NULL,
  location      TEXT NOT NULL,
  species       TEXT NOT NULL,                      -- JSON 数组
  status        TEXT NOT NULL CHECK (status IN ('extant', 'gone')),
  status_source TEXT NOT NULL,
  PRIMARY KEY (unit_id, item_no)
);

-- 子：检索单位
CREATE TABLE chunks (
  chunk_id       TEXT PRIMARY KEY,                  -- shbl:story-002:body-2
  unit_id        TEXT NOT NULL REFERENCES entries(unit_id),
  seq            INTEGER NOT NULL,                  -- 在条目卡 children 里的位置，从 1 起
  item_no        INTEGER NOT NULL,
  content_type   TEXT NOT NULL CHECK (content_type IN ('story', 'fieldnote', 'interview', 'background')),
  context_header TEXT NOT NULL,
  text           TEXT NOT NULL,
  printed_pages  TEXT NOT NULL,                     -- JSON 数组
  review_status  TEXT NOT NULL,
  content_hash   TEXT NOT NULL,
  chunk          TEXT NOT NULL,                     -- chunks_story.jsonl 的整行，原样
  UNIQUE (unit_id, seq),
  FOREIGN KEY (unit_id, item_no) REFERENCES items(unit_id, item_no)
);

-- 关键词索引：grams 是 context_header、text、search_terms 的字符二元组，用空格隔开
CREATE VIRTUAL TABLE chunks_fts USING fts5(chunk_id UNINDEXED, grams, tokenize = 'unicode61');

-- 向量：向量化那一步再填；content_hash 与 chunks 表不一致的向量作废
CREATE TABLE chunk_vectors (
  chunk_id     TEXT PRIMARY KEY REFERENCES chunks(chunk_id),
  model        TEXT NOT NULL,                       -- 例如 bge-m3
  dim          INTEGER NOT NULL,                    -- bge-m3 为 1024
  content_hash TEXT NOT NULL,
  vec          BLOB NOT NULL                        -- float32 小端，已归一化
);

-- 图片：只存元数据，文件在 pilot/images/story/
CREATE TABLE images (
  image_id      TEXT PRIMARY KEY,                   -- shbl-img-p013-1
  unit_id       TEXT NOT NULL REFERENCES entries(unit_id),
  seq           INTEGER NOT NULL,                   -- 在条目卡 images 里的位置，从 1 起
  item_no       INTEGER,
  role          TEXT NOT NULL CHECK (role IN ('index', 'story')),
  file          TEXT NOT NULL,
  printed_page  INTEGER NOT NULL,
  kind          TEXT NOT NULL,
  caption       TEXT,
  credit        TEXT,
  rights_status TEXT NOT NULL,
  UNIQUE (unit_id, seq)
);
```

### 3.5 常用查询

关键词召回（`:q` 是问题拆成的二元组，每个加双引号，用 OR 连接）：

```sql
SELECT c.chunk_id, c.unit_id, bm25(chunks_fts) AS score
FROM chunks_fts JOIN chunks c ON c.chunk_id = chunks_fts.chunk_id
WHERE chunks_fts MATCH :q
ORDER BY score            -- bm25() 越小越相关
LIMIT 20;
```

命中后取整张条目卡：

```sql
SELECT card FROM entries WHERE unit_id = :unit;
SELECT chunk_id, content_type, context_header, text, printed_pages FROM chunks WHERE unit_id = :unit ORDER BY seq;
SELECT image_id, role, item_no, file, kind, caption, credit FROM images WHERE unit_id = :unit ORDER BY seq;
```

按树查询，例如列出今已不存的树：

```sql
SELECT e.entry_no, i.location, i.species, i.status_source
FROM items i JOIN entries e USING (unit_id)
WHERE i.status = 'gone' ORDER BY e.entry_no;
```

### 3.6 以后上线

换成 PostgreSQL 时，表结构一一对应：

- JSON 列改用 `jsonb`；
- 向量列改用 pgvector 的 `vector(1024)`；
- 关键词索引改用支持二元组的全文检索（例如 pg_bigm 扩展）。

JSONL 仍然是事实来源，数据库照样由它生成。

---

## 4. 数据模型

### 4.1 父：条目卡（`pilot/entries_story.jsonl`）

完整定义见附录 B。各字段的来源：

| 字段 | 来源与规则 |
|---|---|
| `unit_id` | `shbl:story-{三位编号}`，与子块的 `unit_id` 相同。**这就是父子指针**，子块不需要另加字段 |
| `source_id` / `section` | 固定为 `SRC-SHBL` / `树的故事` |
| `entry_no` | 1–105 |
| `title` | 正文页标题，照印（stories.jsonl 的 `title`） |
| `district` | 按编号查附录 A 的 `DISTRICTS` |
| `location` | 正文页标题处印的地点（stories.jsonl 的 `location`）。第 23 条用覆盖值，见 §6 |
| `items[]` | 条目里的树或树组，规则见 R2 |
| `species` | 所有 items 树种的并集，按出现顺序去重 |
| `tree_ids` / `site_ids` / `link` | 查附录 A 的 `LINKED`，目前只有第 18、21、55 条有值。其余条目为空数组，不写 `link`。`tree_ids` 和 `site_ids` 是项目实体表（trees、sites）的主键 |
| `official_codes` | 本条所有子块 `official_codes` 的并集，排序去重 |
| `status` | 全部 item 都 gone 为 `gone`，部分 gone 为 `partial`，否则为 `extant` |
| `children[]` | 子块清单，按阅读顺序：正文块在前，小字块按书中顺序在后。每项含 `chunk_id`、`content_type`、`item_no`、`label` |
| `images[]` | 见 §4.3 |
| `captions[]` | stories.jsonl 的 `captions`，全书 3 条（第 6、18、28 条），格式 `{text, printed_page}`，`printed_page` 取第一页。没有时不写 |
| `pdf_pages` / `printed_pages` | 正文页范围：正文段落、小字和图注所在页的并集，升序。不含索引页，索引页记在图片上 |
| `n_chars_body` / `n_chars_small` | 正文字数（stories.jsonl 的 `chars`）和小字字数（按 §6 移动后计算） |
| `as_of` | `2024-08`（书的出版时间） |
| `review_status` | 一律 `pending` |
| `review_flags[]` | `{code, note}`，规则见 R9。没有时不写 |
| `pipeline` | `pilot_stories@3+index_story@1+build_story_chunks@2` |

`children[].label` 的写法与 R6 中头部的"附加"部分相同，正文块写成 `正文·{树种}`，例如"正文·龙柏""访谈：宽尘法师""背景：保圣寺"。

示例：第 2 条，应用 §6 覆盖之后。这个示例已经用附录 B 的 schema 校验通过。

```json
{
  "unit_id": "shbl:story-002",
  "source_id": "SRC-SHBL",
  "section": "树的故事",
  "entry_no": 2,
  "title": "香樟",
  "district": "吴中区",
  "location": "金庭镇爱国村东湾井场",
  "items": [
    {"item_no": 1, "location": "金庭镇爱国村东湾井场", "species": ["香樟"], "status": "extant", "status_source": "index", "origin": "index_main"},
    {"item_no": 2, "location": "金庭镇三官殿", "species": ["龙柏"], "status": "extant", "status_source": "index", "origin": "index_sub"}
  ],
  "species": ["香樟", "龙柏"],
  "tree_ids": [],
  "site_ids": [],
  "official_codes": ["吴中009"],
  "status": "extant",
  "children": [
    {"chunk_id": "shbl:story-002:body-1", "content_type": "story", "item_no": 1, "label": "正文·香樟"},
    {"chunk_id": "shbl:story-002:body-2", "content_type": "story", "item_no": 2, "label": "正文·龙柏"}
  ],
  "images": [
    {"image_id": "shbl-img-p012-4", "file": "pilot/images/story/shbl-img-p012-4.jpg", "role": "index", "item_no": 1,
     "pdf_page": 7, "printed_page": 12, "bbox": [188.9, 310.6, 335.5, 466.5], "native_px": [612, 650],
     "kind": "photo", "rights": {"status": "unknown"}},
    {"image_id": "shbl-img-p013-1", "file": "pilot/images/story/shbl-img-p013-1.jpg", "role": "index", "item_no": 2,
     "pdf_page": 7, "printed_page": 13, "bbox": [564.1, 40.9, 708.7, 241.1], "native_px": [603, 835],
     "kind": "photo", "rights": {"status": "unknown"}},
    {"image_id": "shbl-img-p013-2", "file": "pilot/images/story/shbl-img-p013-2.jpg", "role": "index", "item_no": 2,
     "pdf_page": 7, "printed_page": 13, "bbox": [401.5, 83.1, 558.4, 241.1], "native_px": [654, 659],
     "kind": "photo", "rights": {"status": "unknown"}}
  ],
  "pdf_pages": [39],
  "printed_pages": [76],
  "n_chars_body": 571,
  "n_chars_small": 0,
  "as_of": "2024-08",
  "review_status": "pending",
  "review_flags": [
    {"code": "override", "note": "印刷页76的小字框和插图讲的是第3条阴山岛古樟，已移到第3条"}
  ],
  "pipeline": "pilot_stories@3+index_story@1+build_story_chunks@2"
}
```

### 4.2 子：正文块和小字块（`pilot/chunks_story.jsonl`）

完整定义见附录 C。各字段的生成规则（正则和常量见附录 A）：

| 字段 | 规则 |
|---|---|
| `chunk_id` | `{unit_id}:{块类}-{序号}`，见 R5 |
| `unit_id` | `shbl:story-{三位编号}`，等于所属条目卡的 `unit_id` |
| `part` / `parts` | 同一条目、同一块类内的序号和总数 |
| `source_id` | `SRC-SHBL` |
| `content_type` | 正文为 `story`。小字按 R4 判断，为 `fieldnote`、`interview` 或 `background` |
| `text` | 原文，逐字取自 stories.jsonl：正文块是小节内的段落用 `\n` 连接；小字块是小字框文本或它的切分片段 |
| `context_header` | 见 R6 |
| `lang` | `zh-Hans` |
| `n_chars` | `len(text)` |
| `search_terms` | 见 R6 |
| `figures` | 只写在每条的 body-1 上：该条目全部图注，格式 `{caption, printed_page}` |
| `locator` | `section` 为"树的故事"；`entry_no`；`title` 为正文页标题；`heading_path` 见下；`printed_pages` 为文本所在的印刷页（升序去重）；`pdf_pages` 为 `{p // 2 + 1}`（升序） |
| `tree_ids` / `site_ids` / `link` | 查 `LINKED`。不在表里的，分别为 `[]`、`[]`、`{"status": "unlinked"}` |
| `site_name` | 所属 item 的地点 |
| `district` | 查 `DISTRICTS` |
| `species` | 见 R6 |
| `official_codes` | `LINKED` 里的编号，加上用 `OFFICIAL_CODE` 在 `text` 和 `figures` 图注里找到的编号（前缀加三位数字，去掉中间空格）。排序去重 |
| `source_tier` | `B`。来源层级：A 为官方或一手（如现场铭牌），B 为馆方出版物和学术文献，C 为媒体，D 为自媒体或百科；AI 生成或无出处的内容不入库 |
| `as_of` | 默认 `2024-08`。访谈或走访记录有日期时，用该日期（YYYY-MM-DD） |
| `review_status` | `pending` |
| `certainty_flags` | `text` 匹配 `LEGEND` 加 `legend`，匹配 `RECORDED` 加 `recorded`，匹配 `HEDGED` 加 `hedged` |
| `conflict_ids` | 查 `CONFLICTS`：条目号相同、且 `text` 匹配对应正则时加入 |
| `sensitivity` | `text` 匹配 `RED` 加 `red_history`；`text` 或 `site_name` 匹配 `RELIGION` 加 `religion`；`content_type` 为 `interview` 或 `text` 匹配 `PEOPLE` 加 `personal_info` |
| `rights` | `{"holder": "吴文化博物馆", "display": "full", "note": "馆方尚未确认可否在 AI 应用中引用原文"}` |
| `content_hash` | `"sha256:" + sha256((context_header + "\n" + text).encode("utf-8")).hexdigest()[:16]`。这也是向量化的输入，hash 变了才需要重算向量 |
| `pipeline` | `pilot_stories@3+index_story@1+build_story_chunks@2` |
| `interview` / `fieldnote` / `background` | 只出现在对应类型的块里，规则见 R4 |

`heading_path` 的写法：

- 前两项固定为 `["树的故事", "{编号} {正文页地点}·{标题}"]`。
- 正文块：小节标题与正文页标题不同时，再加小节标题。
- 小字块：再加一项，`走访记录`、`采访手记`、`访谈` 或 `背景`。

### 4.3 图片（条目卡的 `images[]`）

图片不是块：不建索引，不做向量，也不送进大模型，只用于界面展示和人工核对。

| 字段 | 规则 |
|---|---|
| `image_id` | `shbl-img-p{印刷页三位}-{序号}`。同一印刷页上的图按 `(round(y0/20), round(x0))` 排序后从 1 编号，区县地图跳过、不编号。ID 只看版面位置，所以按 §6 把图移到别的条目时不用改名 |
| `file` | `pilot/images/story/{image_id}.jpg` |
| `role` | `index`（索引页）或 `story`（正文页） |
| `item_no` | 索引图必填（归到哪个 item）。正文图不填 |
| `pdf_page` / `printed_page` / `bbox` | 图片在 PDF 上的位置，`bbox` 单位为 pt，保留 1 位小数 |
| `native_px` | PDF 里原始图片的像素宽高（`get_image_info` 的 `width`、`height`） |
| `kind` | `photo` / `painting` / `drawing` / `map` / `document`。默认 `photo`；索引图说明里含"藏"的设为 `painting`；§6 和 §11 列出了已知的例外 |
| `caption` / `credit` | 索引页画作的说明和收藏单位，见 R8 |
| `rights.status` | `credit` 写明其他机构收藏的设为 `third_party`，其余一律 `unknown`。在馆方确认之前，不要写 `museum` |

---

## 5. 切分与归属规则

### R1 父：一个条目一张卡

共 105 张，编号 1–105 各一张。注意条目不等于一棵树，可能是：

- 同一地点的几种树（第 1 条：枫香、罗汉松、马尾松）；
- 两个地点（第 2 条、第 23 条）；
- 一组几棵（第 18 条：四棵古柏）。

### R2 items：从索引页读出条目里有哪些树

1. **识别头部**：在 PDF 6–37 页上，把下面几种文字组合成一个"头部"：

   | 部件 | 识别条件 | 位置关系 |
   |---|---|---|
   | 编号 | 字体名以 `Times` 开头、白色、字号 ≥ 14 | 在地点左侧 0–60pt，y0 相差 < 12pt |
   | 地点 | 颜色 `#64706D`、字号 7.5–8.5 | 头部的锚点 |
   | 树种 | 颜色 `#8F8166`、字号 ≥ 12 | x0 与地点相差 < 3pt，y0 在地点下方 0–15pt |
   | 状态 | 颜色 `#8F8166`、字号 < 12，如"（今已不存）" | 与树种同一行（y 相差 < 12pt），在树种右侧 |

   头部的 `x0` 取编号和地点中较小的 x，`y0` 取地点的 y。

2. **主副之分**：有编号的头部是该条目的主 item（`origin: index_main`）。没有编号的头部是副 item（`index_sub`），归给阅读顺序上前一个有编号的条目。阅读顺序为：印刷页 → `round(y0/60)` → x0。
   - 全书有 2 个副 item：第 1 条"天平山｜马尾松（今已不存）"，第 2 条"金庭镇三官殿｜龙柏"。
3. **树种拆分**：树种按 `、，, / 空格` 拆开，再用 `SYNONYM` 归一。
4. **状态**：状态文字含"不存"时设为 `gone`（`status_source: index`），否则为 `extant`。
5. **编号**：item 按阅读顺序从 1 编号，主 item 总是 1。
6. **覆盖**：第 23 条的 items 用 §6 的覆盖值。

**完成标准**：

- 共 107 个头部，其中 105 个带编号（1–105 各一次）、2 个副 item。
- 覆盖之后 items 共 108 个。

### R3 正文子块：一个树种小节一块，不按长度切

1. **分小节**：按顺序遍历 stories.jsonl 的 `blocks`：
   - 遇到 `subhead` 就开一个新小节，标题为小标题；
   - `para` 归入当前小节；第一个小标题之前的段落自成一节，标题为空。
   - 丢掉没有段落的小节。
2. **合并过短的小节**：依次处理每个小节，如果前一个（已合并的）小节的段落总字数 < 80，就把本节并进去，标题用"、"连接（空标题跳过）。全部处理完后，如果小节多于一个、且最后一节 < 80 字，把它并入前一节。字数只算段落文字，不算换行。
3. **成块**：每个小节成为一个正文块（`content_type: story`），**不按长度切**。
4. **长度兜底**：设安全阈值 `BODY_MAX = 1200`，小节超过 1200 字时才在段落边界切开，切法见 R4 第 3 条的均分规则。目前没有这种情况：最长的第 82 条 1108 字、第 62 条 1001 字，都保留整块。
5. **小节归属**：用 `names(小节标题)` 得到小节的树种。如果它是某个 item 树种的子集，小节就归那个 item。没有标题的小节归 item 1。匹配不到的也归 item 1，同时加 flag `section_species_unmatched`（目前 0 例）。
   - `names(label)`：标签形如"一棵X的……"时取 X；否则按 `、，, 空格` 拆开。每项用 `SYNONYM` 归一，只保留不超过 5 个字的，按出现顺序去重。
6. **数量**：多小节的条目共 9 个，其中第 1 条 3 节，第 2、6、12、16、28、30、35、36 条各 2 节。再加上第 23 条的覆盖切分，正文块共 **116** 个。

### R4 小字子块：一个小字框一块

1. **判断类型**：一个小字框（stories.jsonl 的 `sidebars` 中的一项）成为一个块。设框内第一行为 `first`，按顺序判断：
   1. `first` 以"关于"开头，或 `first` 不超过 8 个字且不含"，""。""："，判为 `background`；
   2. 否则，`first` 匹配 `INTERVIEW_HEAD`，或框内说话人有 2 个以上，或有说话人且 `first` 以说话人开头，判为 `interview`；
   3. 否则，框内含"志愿者"或匹配 `DATE`，判为 `fieldnote`；
   4. 其余判为 `background`。

   **说话人**的找法：取每行开头匹配 `SPEAKER` 的标签，保留匹配 `ROLE` 或出现 2 次以上的，按首次出现的顺序去重。

2. **采访手记**：访谈框里以"采访手记："开头的行，连同其后的内容另起一块，类型为 `fieldnote`。目前只有第 26 条：1135 字的访谈框切成访谈 857 字和采访手记 277 字。

3. **超长再切**：仍超过 `SIDE_MAX = 900` 字的块再切。
   - **访谈**：按行处理，份数 `n = ceil(长度 / 900)`，目标长度 `goal = 长度 / n`。遇到以下两种情况之一，就在这一行之前开新的一份：
     - 这一行以馆方提问人开头（说话人匹配 `ASKER`），且当前份已有 `0.8 × goal` 字；
     - 当前份加上这一行会超过 900 字。

     每行计入"行长 + 1"。
   - **走访记录和背景**：在段落（行）边界均分：份数 `n = ceil(长度 / 900)`，目标长度 `goal = 长度 / n`。当前份不空、加上下一段的一半会超过 `goal`、且份数还没到 `n` 时，开新的一份。
   - 目前切完后没有超过 900 字的块。

4. **类型专属字段**：
   - **`interview.speakers`**：
     - 框的第一行匹配 `INTERVIEW_HEAD` 时，写成 `[{label: 受访人, side: answer}, {label: 采访人, side: ask}]`，`observed_on` 取"时间"里的日期。
     - 否则用整框的说话人，标签匹配 `ASKER` 的 `side` 为 `ask`，其余为 `answer`，不写 `observed_on`。
     - 同一框切出的各块，写同样的说话人。
   - **`fieldnote`**：`author_role` 为"志愿者"，`observed_on` 取本块文本里第一个 `DATE`（没有就不写）。采访手记块的 `author_role` 为"采访人"，`observed_on` 继承该访谈的日期（有的话）。
   - **`background.topic`**：`first` 以"关于"开头时，取"关于"与第一个"："之间的文字；否则 `first` 不超过 10 个字时取 `first`；再否则取正文页地点。

5. **数量**：21 个小字框共切出 **22** 个块：走访记录 10、访谈 6、背景 6。共有 15 个条目带小字，都在吴中区。

6. **图注不成块**：图注挂在本条 body-1 的 `figures` 上，同时写进条目卡的 `captions`。

### R5 ID

- **格式**：`{unit_id}:{块类}-{序号}`，块类为 `body` / `fieldnote` / `interview` / `background`。
- **序号**：在同一条目、同一块类内，按阅读顺序从 1 编起。`part` 是这个序号，`parts` 是同一条目同一块类的总数。
- **稳定性**：ID 只由结构决定，不用流水号或内容哈希，重跑结果不变。
- **引用检查**：旧的切法下，`body-2`、`body-3` 等 ID 会消失或换成别的内容。动手前先在仓库里搜索 `shbl:story-`，如果有文件引用了块 ID（例如 FAQ 的证据），要同步核对。

### R6 context_header 和实体字段

**格式**：

```
《树碑立传》树的故事 第{编号}条｜{区县} {item 地点}｜{树种}[（今已不存）]｜{附加}
```

- **树种**：
  - 正文块用小节的树种，没有小标题时用 item 的树种。
  - 小字块用 item 1 的树种，有覆盖时以覆盖为准。
  - 多个树种用"、"连接。
- **（今已不存）**：所属 item 的 status 为 gone 时才加。
- **附加**：
  - 正文块不加；
  - 走访记录加 `志愿者走访记录`；
  - 采访手记加 `采访手记`；
  - 访谈加 `访谈：{回答方}`（回答方用"、"连接，没有时只写"访谈"）；
  - 背景加 `背景：{topic}`。
- **其他字段**：
  - `site_name` 等于 item 地点；
  - `species` 与头部使用的树种一致；
  - `search_terms` 先放 `official_codes` 里不出现在 `text` 中的编号。如果子块属于主 item（`origin: index_main`），且正文页地点的写法与 item 地点不同，再加上正文页地点（例如"光福石壁永慧禅寺"）。
- **示例**：
  - `《树碑立传》树的故事 第2条｜吴中区 金庭镇三官殿｜龙柏`
  - `《树碑立传》树的故事 第1条｜吴中区 天平山｜马尾松（今已不存）`
  - `《树碑立传》树的故事 第26条｜吴中区 东山镇杨湾村湖沙庙｜榉树（今已不存）｜采访手记`

### R7 状态：只认索引标注

- **自动判定**：只有索引页上的"（今已不存）"会自动把状态设为 `gone`。
- **正文不自动改状态**：正文里出现"死亡""除名""不存"等字样，**不能**自动改状态。例如第 74 条的"已死亡注销"说的是另一棵树。正文证据只能通过 §6 的覆盖表进入，此时 `status_source` 记为 `text` 或 `index+text`。
- **当前结果**：
  - gone 的 item 共 6 个：
    - 按索引：第 1 条马尾松、第 26、27、29、34 条。其中第 29、34 条正文也写了"今不存"，记 `index+text`。
    - 按覆盖表：第 14 条。
  - 条目卡：`gone` 5 张（第 14、26、27、29、34 条），`partial` 1 张（第 1 条），其余 99 张为 `extant`。
- **extant 的含义**：书（2024-08）没有标注这棵树已不存，并不代表到现场核实过。

### R8 图片：找出来、归给条目、导出

**索引页**

1. **取图**：用 `page.get_image_info(xrefs=True)` 取所有图片位置，丢掉面积小于 2000 pt² 的。
2. **归属**：每张图归给同一印刷页上、位于图片左上方的最近头部。
   - 候选头部满足 `head.x0 ≤ img.x0 + 10` 且 `head.y0 ≤ img.y0 + 10`。
   - 距离 = `(img.y0 − head.y0) + |img.x0 − head.x0|`，取最小者。
   - 找不到候选头部的图是区县地图，共 11 张，跳过。
3. **说明文字**：图下方 30pt 内、字体名以 `FZKTK` 开头（方正楷体）的文字是画作说明，写进 `caption`。含"藏"的，`kind` 设为 `painting`，`credit` 取"……藏"那一行，`rights.status` 设为 `third_party`。目前有 2 张：
   - 第 1 条马尾松：明代沈周《两江名胜图·天平山》，上海博物馆藏；
   - 第 29 条：明代吴宽《游西山记》，中国国家博物馆藏。
4. **完成标准**：共 188 张照片，105 个条目每条至少 1 张：1 张的 35 条，2 张的 58 条，3 张的 11 条，4 张的 1 条。

**正文页**

1. **取图**：取图方法同上（面积 ≥ 2000 pt²）。
2. **按阅读顺序归属**：
   - 先按 `(印刷页, 栏, y0)` 排序，栏的取法是：图片 x0 在半页左半边为 0，右半边为 1（分界为半页起点 + 半页宽 / 2 − 2）。
   - 每张图归给排在它前面的最后一个条目编号圆圈（`Times` 字体、字号 ≥ 16、白色）。
3. **完成标准**：共 74 张，分属 45 个条目。其中第 2 条那张（印刷页 76）按 §6 改归第 3 条，改归之后仍是 45 个条目。

**导出**

1. **不导出原始图片流**：PDF 里有 244 张 CMYK JPEG，浏览器显示会偏色，而且原图没有按版面裁切。
2. **渲染导出**：用 `page.get_pixmap(clip=bbox, dpi=min(300, 原始分辨率))` 渲染版面上实际可见的部分，再用 `pix.tobytes("jpg", jpg_quality=85)` 存盘。
   - 原始分辨率 = `native_px` 宽 ÷ (bbox 宽 ÷ 72)。
   - 实测 262 张约 9 秒，共约 42 MB。
3. **分辨率有限**：正文页配图短边中位数 437 px，索引照片短边中位数 650 px，只够做界面缩略图。正式展示要向馆方要原图。

### R9 review flags（自动生成，写进条目卡）

| code | 条件 | 当前应命中 |
|---|---|---|
| `location_conflict` | 正文页地点与主 item 地点做归一化（去掉空白、括号及其内容，以及"镇""村""禅"三个字）后，两者互不包含 | 第 11 条 |
| `status_text_hint` | 正文或小字匹配 `GONE`，但索引没有标注不存 | 第 14、74 条（第 74 条的 note 写明"误报"，见 §6） |
| `third_party_image` | 有 `rights.status = third_party` 的图 | 第 1、29 条 |
| `override` | 用了 §6 的覆盖，或接收了别的条目移来的内容 | 第 2、3、14、23 条 |
| `section_species_unmatched` | 见 R3 | 无 |

---

## 6. 人工覆盖表（已确认，照做）

在 `build_story_chunks.py` 里写成 `OVERRIDES`，在切块之前生效：

| 条目 | 问题 | 处理 |
|---|---|---|
| 2 → 3 | 印刷页 76 右栏下方有一幅插图和一段 67 字的小字（开头是"每次抵达阴山岛"）。它们排在第 2 条的版面上，内容讲的却是第 3 条的阴山岛古樟 | 小字框移到第 3 条（成为 `shbl:story-003:fieldnote-1`）。正文页那张图（`shbl-img-p076-1`）也移到第 3 条，`kind` 设为 `drawing`。两条都加 `override` flag |
| 11 | 正文页标题处的地点印成"东山镇北望岭下村"，与第 10 条相同；但索引写的是"东山镇灵源古寺"，正文内容讲的也是灵源古寺 | 数据不改：`location` 照印，item 地点用索引的"东山镇灵源古寺"。自动生成的 `location_conflict` flag 留给馆方确认 |
| 14 | 索引没有标注不存，但正文写明这棵龙柏因死亡被官方古树名录除名（推测在 2017–2021 年间） | item 1 设为 `gone`，`status_source: text`，`status_note` 写明依据 |
| 23 | 正文页标题印了两个地点，解析时被拼成"木渎古镇严家花园木渎古镇古松园"；索引只印了严家花园 | `location` 改为"木渎古镇严家花园、木渎古镇古松园"。items 改为两个：严家花园｜广玉兰、古松园｜罗汉松（`origin: override`）。正文在以"古松园是"开头的段落前切成两块，540 字和 158 字，分别归 item 1 和 item 2；第二块的小节标题记为"罗汉松" |
| 74 | 正文"其中一棵银杏已死亡注销"说的是园区的另一棵银杏，本条的两棵仍在 | 状态不改。`status_text_hint` flag 的 note 写"误报：指另一棵树" |

可以直接粘贴的写法：

```python
OVERRIDES = {
    2: {"move_sidebars": {0: 3},                      # 第2条第1个小字框 → 第3条
        "move_story_images": {0: (3, "drawing")},     # 第2条第1张正文页图 → 第3条，kind=drawing
        "note": "印刷页76的小字框和插图讲的是第3条阴山岛古樟，已移到第3条"},
    14: {"item_status": {1: ("gone", "text", "正文：该龙柏因死亡被官方古树名录除名，推测死亡时间2017–2021年；索引未标注")}},
    23: {"location": "木渎古镇严家花园、木渎古镇古松园",
         "items": [{"location": "木渎古镇严家花园", "species": ["广玉兰"]},
                   {"location": "木渎古镇古松园", "species": ["罗汉松"]}],
         "split_body_before": ("古松园是", 2),          # 从这个段落起归 item 2
         "note": "正文页标题印了两个地点，索引只印严家花园；按正文拆成两个 item"},
    74: {"status_hint_note": "误报：正文“已死亡注销”指园区另一棵银杏，本条两棵仍在"},
}
```

---

## 7. 检索与回答（写问答服务时照此实现）

### 7.1 检索流程

1. **建索引**：只对子块建索引：关键词索引（§3.4 的 `chunks_fts`）和向量（输入为 `context_header + "\n" + text`）。条目卡和图片都不进索引。
2. **过滤**：线上只用 `review_status = verified` 的子块；原型阶段放宽为"不是 `rejected`"。
3. **召回**：关键词和向量各取前 20，用 RRF（reciprocal rank fusion，k = 60）合并，有重排模型时再重排，取前 `K_CHILD = 20` 个子块。没有向量时只用关键词。
4. **归并**：按 `unit_id` 分组，每组的名次取组内最好子块的名次，取前 `K_PARENT = 3` 组（最多 4 组）。
5. **组装**：每组取整张条目卡，按 `children` 的顺序列出全部子块，命中的子块标上"命中"。
6. **预算**：总字数上限 `CHAR_BUDGET = 6000`。超出时，从排名最低的卡开始删：先删未命中的小字块，再删未命中的正文块。命中的子块永远保留。作为参考，单张卡最长 2253 字（第 26 条），带小字的条目中位数 839 字，全部条目中位数 419 字。
7. **图片**：回答界面按 `images` 展示，先展示索引照片。图片不送给模型。

参考实现：

```python
def retrieve(db, query, k_child=20, k_parent=3, budget=6000):
    hits = search_children(db, query, k=k_child)        # [(chunk_id, unit_id)]，已融合、重排，按名次排好
    groups = {}
    for rank, (cid, unit) in enumerate(hits):
        groups.setdefault(unit, {"best": rank, "hit": set()})["hit"].add(cid)
    units = sorted(groups, key=lambda u: groups[u]["best"])[:k_parent]
    picked = [(load_card(db, u), groups[u]["hit"]) for u in units]   # 用 §3.5 的三条 SQL
    return render(trim(picked, budget))                  # trim 按第 6 步删减，render 输出 7.2 的格式
```

### 7.2 送给模型的格式

每个子块给一个短编号 [S1]、[S2]……，由代码映射回 `chunk_id`，不让模型自己写 ID。正文用省略号代替：

```
【条目】《树碑立传》树的故事 第2条 · 香樟
树：① 金庭镇爱国村东湾井场｜香樟　② 金庭镇三官殿｜龙柏
区县：吴中区　官方编号：吴中009　状态：书中未标注不存（2024年）
[S1] 正文·香樟（第76页）
     类型：故事｜来源：B｜资料时间：2024-08
     ……
[S2] 正文·龙柏（第76页｜命中）
     类型：故事｜来源：B｜资料时间：2024-08｜标记：含宗教场所
     ……
```

- 每个子块的说明行写：类型、来源层级、资料时间（`as_of`），以及有值的标记：
  - `certainty_flags`：含传说、含史载、含存疑；
  - `conflict_ids`：冲突的内容，见附录 A 的 `CONFLICTS`；
  - `sensitivity`：含宗教场所、含革命史、含个人信息。

### 7.3 回答的口吻

| content_type | 口吻 |
|---|---|
| `story` | "据《树碑立传》……" |
| `fieldnote` | "志愿者 2023 年 4 月走访时记录……"（有日期时写日期） |
| `interview` | "宽尘法师在接受采访时说……"（口述，归于说话人，不当作定论） |
| `background` | "《树碑立传》介绍保圣寺时提到……" |

文中的"今年""目前"按该块的 `as_of` 理解，不能照搬成回答当天的时间。

### 7.4 输出校验（用代码检查，不依赖模型自觉）

1. **引用有效**：答案引用的 [S#] 必须在本次提供的块中。
2. **数字有据**：答案里的年份、数字、编号，必须能在被引用的块中找到。
3. **传说和存疑**：引用了 `legend` 块时，答案必须出现"相传""传说""据说"等词；引用了 `hedged` 块时，不能把存疑的说法写成定论。
4. **冲突**：引用了带 `conflict_ids` 的块时，答案必须给出各个说法，或者采用馆方口径。
5. **革命史**：引用了 `red_history` 块时，答案中的事实句必须能在原文中找到对应，只复述，不扩写、不评价。
6. **宗教和个人信息**：涉及 `religion` 时措辞客观、尊重。涉及 `personal_info` 时默认只说身份（例如"一位志愿者"），不说姓名，除非馆方同意。
7. **展示出处**：由 `locator` 生成，例如"《树碑立传》第 76 页（树的故事·2）"。

---

## 8. 操作步骤

### 步骤 0：准备

```bash
cd analysis && shasum -a 256 pilot/stories.jsonl pilot/chunks_story.jsonl
```

1. 重跑 `python -I scripts/pilot_stories.py "../树碑立传：吴中古树名木的故事教育读物(1).pdf" 38 81 pilot/_stories_check.jsonl`，确认输出与 `pilot/stories.jsonl` 逐字节相同，然后删掉 `_stories_check.jsonl`。
2. 把旧的 `pilot/chunks_story.jsonl` 复制一份为 `pilot/chunks_story.old.jsonl`，作为对照。

**完成标准**：stories.jsonl 可以复现；旧子块已备份。

### 步骤 1：新建 `scripts/index_story.py`

```bash
python -I scripts/index_story.py "../树碑立传：吴中古树名木的故事教育读物(1).pdf" pilot/index_story.jsonl pilot/images/story
```

- **范围**：实现 R2 的头部识别和 R8 的图片找取、归属和导出。只记录原始结果，**不在这里应用 §6 的覆盖**。
- **输出**：格式见 §3.3。图片的 `head_no` 指向本行 `heads` 的序号，`build_story_chunks.py` 据此确定 `item_no`。
- **运行结束打印摘要**：头部数、图片数（索引 / 正文 / 跳过的地图）、每条图片数分布、画作说明。

**完成标准**：

- 105 行；107 个头部（105 个带编号，2 个副 item）；
- 索引照片 188 张，每个条目至少 1 张；跳过地图 11 张；
- 正文页图片 74 张，分属 45 个条目；
- 生成 262 个 jpg；画作说明 2 条。

### 步骤 2：把 `scripts/build_story_chunks.py` 改写为 v2

```bash
python -I scripts/build_story_chunks.py pilot/stories.jsonl pilot/index_story.jsonl pilot/chunks_story.jsonl pilot/entries_story.jsonl
```

按 §4、§5、§6 实现：

1. 先应用 `OVERRIDES` 里小字框和图片的移动；
2. 生成 items；
3. 切正文块和小字块，并把它们归到 item；
4. 生成子块的全部字段；
5. 组装条目卡，生成 review flags。

常量和正则用附录 A。

**完成标准**：

- 子块 138 个：story 116、fieldnote 10、interview 6、background 6；
- 条目卡 105 张。

### 步骤 3：保存两个 schema 和建表语句

把附录 B 存为 `schema/entry.schema.json`，附录 C 存为 `schema/story_chunk.schema.json`，§3.4 的建表语句存为 `schema/story_db.sql`。都原样保存，不要改动。

### 步骤 4：新建 `scripts/validate_story.py`

```bash
python -I scripts/validate_story.py schema/entry.schema.json schema/story_chunk.schema.json pilot/entries_story.jsonl pilot/chunks_story.jsonl pilot/stories.jsonl
```

逐项检查，任何一项不通过都计为一个 problem，有 problem 时退出码为 1：

1. **schema**：每张卡、每个子块都通过各自的 schema 校验（Draft 2020-12）。
2. **条目齐全**：正好 105 张卡；`entry_no` 为 1–105 各一次；`unit_id == f"shbl:story-{entry_no:03d}"`。
3. **子块自身**：
   - `chunk_id` 不重复，且以 `unit_id + ":"` 开头；
   - `part ≤ parts`；
   - `n_chars == len(text)`；
   - `pdf_pages == sorted({p // 2 + 1 for p in printed_pages})`；
   - `content_hash` 按 §4.2 的公式重算后一致。
4. **父子对应**：
   - 所有卡的 `children` 的 chunk_id 集合，等于子块文件中的 chunk_id 集合；
   - 每个子块的 `unit_id` 等于所属卡的 `unit_id`；
   - 每个 child 的 `content_type` 与对应子块一致。
5. **引用有效**：`children[].item_no` 和 `images[].item_no` 都不超过 items 的个数；`image_id` 全局唯一；`file` 指向的文件存在。
6. **原文覆盖**：先应用 §6 的移动，再检查：
   - 每个条目的正文块文本按 children 顺序用 `"\n"` 连接后，等于 stories.jsonl 的 `text`；
   - 小字块文本用 `"\n"` 连接后，等于该条目全部小字框文本用 `"\n"` 连接的结果。
7. **头部**：每个子块的 `context_header` 以 `《树碑立传》树的故事 第{编号}条｜{区县} {site_name}｜` 开头。
8. **页码**：每张卡的 `printed_pages` 包含其所有子块的 `printed_pages`。
9. **打印摘要**，用来对照 §9：卡数、items 数、子块分类型数、图片分角色数、status 分布、flags 分 code 的条目列表、正文块和小字块的最长字数。

**完成标准**：0 problems，摘要与 §9 一致。

### 步骤 5：新建 `scripts/load_story_db.py`

```bash
python -I scripts/load_story_db.py schema/story_db.sql pilot/entries_story.jsonl pilot/chunks_story.jsonl pilot/story.db
```

1. 如果 `pilot/story.db` 已存在，先删掉，再执行 `schema/story_db.sql` 建表。
2. 在一个事务里依次插入：
   - `entries`、`items`（按 `entry_no`）；
   - `chunks`：按卡的 `children` 顺序，`seq` 从 1 起，`item_no` 取自卡；
   - `chunks_fts`：按 §3.4 的规则生成 `grams`；
   - `images`：按卡的 `images` 顺序，`seq` 从 1 起。
3. `meta` 写 3 行：`schema_version = 1`、`pipeline`、两个输入 JSONL 的 sha256（用 JSON 对象存）。不写构建时间。
4. 最后执行 `PRAGMA integrity_check` 和 `PRAGMA foreign_key_check`。

**完成标准**：

- 两项检查都通过；
- 行数为 entries 105、items 108、chunks 138、chunks_fts 138、images 262、chunk_vectors 0、meta 3。

### 步骤 6：新建 `scripts/retrieve_demo.py`

```bash
python -I scripts/retrieve_demo.py pilot/story.db --self-test
```

- **检索**：只用标准库，在 `story.db` 上用 §3.5 的关键词召回，然后按 §7.1 归并、组装。
- **用法**：带问题参数时，打印前 3 张卡、各自命中的子块和按 §7.2 组装的文本；带 `--self-test` 时跑下面 8 个问题。

| 问题 | 期望的条目（前 3 名内） | 应命中的子块 |
|---|---|---|
| 树洞口和断口怎么清理 | shbl:story-026 | 访谈 |
| 天平山的马尾松还在吗 | shbl:story-001 | 马尾松那块（头部带"今已不存"） |
| 三官殿的龙柏 | shbl:story-002 | body-2 |
| 吴中203 | shbl:story-021 | （通过 `search_terms` 命中） |
| 石楠花开时志愿者去做了什么 | shbl:story-021 | 走访记录 |
| 灵源古寺的罗汉松 | shbl:story-011 | 正文 |
| 古树受伤了怎么保护 | shbl:story-026 | 访谈 |
| 湖沙庙的榉树 | shbl:story-026 | 正文 |

这 8 个问题在原型（同样的 FTS5 二元组检索）上都排第 1，验收标准放宽为前 3 名内。没通过时报告结果，**不要为了通过去调问题或参数**。

**完成标准**：8/8 通过。

### 步骤 7：生成人工核对页 `pilot/review/story_assignments.html`

- **形式**：单个静态 HTML 文件，不引用任何外部资源，图片用相对路径。
- **内容**：每个条目一节，依次展示：
  - 编号、区县、正文页地点；
  - items 及其状态；
  - 索引照片和正文页图片的缩略图，标出 image_id 和 kind；
  - 每个小字块的类型和前 40 字；
  - review_flags，用醒目的颜色。
- **作用**：馆方或组员用这一页核对图片和小字的归属（自动规则在第 2 条就错过一次），以及 §11 的第 3 项。

**完成标准**：本地浏览器能打开，有 105 节，缩略图都能显示。

### 步骤 8：记录结果

在本文件末尾的"变更记录"追加一行：日期、实际数字，以及与 §9 不一致的地方。

---

## 9. 验收清单

| 项目 | 预期 |
|---|---|
| 条目卡 | 105 张，编号 1–105 |
| items | 108 个：105 个主 item，2 个副 item（第 1、2 条），第 23 条由 1 个改为 2 个 |
| 子块 | 138 个：story 116、fieldnote 10、interview 6、background 6 |
| 带小字的条目 | 15 个：第 3、5、6、7、11、12、14、16、17、21、22、25、26、27、28 条（第 2 条的小字已移到第 3 条） |
| 正文块长度 | 最长 1108 字（第 82 条），超过 900 字的只有第 62、82 条，没有超过 1200 字的 |
| 小字块长度 | 都不超过 900 字 |
| 区县 | 第 1–29 条吴中区，30–55 姑苏区，56–68 吴江区，69–71 虎丘区（高新区），72–73 相城区，74 工业园区，75–84 昆山市，85–99 常熟市，100–104 太仓市，105 张家港市 |
| 图片 | 262 张：索引 188 张，正文 74 张；跳过地图 11 张；每张卡至少 1 张索引照片 |
| 外馆藏画 | 2 张（第 1、29 条），`rights.status = third_party` |
| status | gone 5 张（第 14、26、27、29、34 条），partial 1 张（第 1 条），extant 99 张 |
| flags | location_conflict：第 11 条；status_text_hint：第 14、74 条；third_party_image：第 1、29 条；override：第 2、3、14、23 条 |
| 校验 | `validate_story.py` 0 problems |
| 检索库 | 行数符合步骤 5；完整性检查和外键检查通过 |
| 冒烟测试 | `retrieve_demo.py --self-test` 8/8 通过 |
| 可重复 | 连续跑两次，`index_story.jsonl`、`entries_story.jsonl` 和 `chunks_story.jsonl` 的 sha256 不变（`story.db` 不要求逐字节相同） |

---

## 10. 不要做的事

- 不给条目卡建索引或做向量；不把图片送进模型。
- 不用大模型补写、改写或摘要任何文字。以后如果要做"假设问题"多向量，生成的问题必须单独存放、标明是生成的，并且不能被引用。
- 不根据正文关键词自动改 status。
- 不合并或删除 items：一个条目里的每棵树都要能被单独指到。
- 不在句子中间切；不跨条目、跨小字框拼块（§6 的移动除外）。
- 不手改 JSONL 或 `story.db`；要改就改脚本或覆盖表，然后重跑。
- 不改 PDF 和 `stories.jsonl`。
- 不把图片或书中原文上传、发布到外部服务。

---

## 11. 需要人决定的问题（不要自行处理）

1. **第 11 条的地点**：以正文页的"东山镇北望岭下村"为准，还是以索引的"东山镇灵源古寺"为准？
2. **状态核实**：
   - 第 14 条正文说已从名录除名，是否以正文为准？
   - 第 26、27 条索引标了"今已不存"而正文没有提到，是否需要核实现场？
3. **正文页里不是照片的图片**：
   - 第 7 条（印刷页 81，白皮松植物线描）和第 16 条（印刷页 90，雕花楼古树分布平面图）已目视确认；
   - 第 18 条（印刷页 92）和第 28 条（印刷页 103）根据图注推测是档案和分布图，尚未目视确认。
   - 请在核对页上确认，再改 `kind`。
4. **图片版权**：书中照片和插图的权利人是谁？两幅外馆藏画能否在应用中展示？
5. **原图**：是否向馆方要原图？现有分辨率只够做缩略图。
6. **小字的小瑕疵**（目前不处理，不影响检索）：
   - 第 11 条的小字框开头混进了一行图注；
   - 第 16 条那段 77 字的"背景"其实是平面图的图注，含 6 个官方编号；
   - 第 22 条那段 46 字的"背景"是照片说明。

---

## 附录 A：常量与正则

`build_story_chunks.py` 直接使用以下定义：

```python
SOURCE_ID, BOOK, AS_OF = "SRC-SHBL", "《树碑立传》", "2024-08"
PIPELINE = "pilot_stories@3+index_story@1+build_story_chunks@2"
BODY_MAX, SIDE_MAX, MIN_SECTION = 1200, 900, 80

# 区县：每个区县第一个条目的编号，按索引分隔页地图上印的编号确定
DISTRICTS = [(1, "吴中区"), (30, "姑苏区"), (56, "吴江区"), (69, "虎丘区（高新区）"), (72, "相城区"),
             (74, "工业园区"), (75, "昆山市"), (85, "常熟市"), (100, "太仓市"), (105, "张家港市")]

# 已人工关联到项目实体表的条目：(tree_ids, site_ids, 官方编号, link.status, link.evidence)
LINKED = {
    18: (["stm-yuanbai"], ["S02"], ["吴中186", "吴中187", "吴中188", "吴中189"], "cross_ref",
         "地点、树种与 stm-yuanbai 一致；编号见古树与艺术章节《推演》（印刷页 174），四棵个体的对应待确认"),
    21: (["sbs-shinan"], ["S03"], ["吴中203"], "manual", "地点、树种与 sbs-shinan 一致；编号来自现场官方铭牌"),
    55: (["dlzb-xuesong"], ["S01"], [], "manual", "地点、树种与 dlzb-xuesong 一致；书中称尚未列入古树名录"),
}

# 已知的说法冲突：(冲突 ID, 条目号, 命中正则)
#   cf-xuesong-planted：雪松种植年份，书中 1924 年，另有来源为 1923 年
#   cf-shinan-age：石楠树龄，铭牌"800余年"，石刻"元朝年间"
#   cf-hanshan-arrival：憨山到石壁的年份，有 1569、1596 两说
CONFLICTS = [("cf-xuesong-planted", 55, r"1924"),
             ("cf-shinan-age", 21, r"800年前|元朝"),
             ("cf-hanshan-arrival", 21, r"憨山")]

SYNONYM = {"樟树": "香樟"}

# 书中出现的在世人员（志愿者、受访者等）；张冀(?!牖) 排除历史人物张冀牖
PEOPLE = re.compile(r"罗星琳|周缘|王铭钰|张冀(?!牖)|黄林法|黄老|陈平|施雪荣|陈爱娣|萍萍阿姨")

OFFICIAL_CODE = re.compile(r"(吴中|吴江|狮|沧|网|耦|天|拙|留|虎|常熟|昆|太|张|相|园区|姑苏|平|金|东)\s?(\d{3})")
LEGEND = re.compile(r"相传|传说|据传|传为|传闻|民间(?:盛|流)传")
RECORDED = re.compile(r"记载|记述|《[^》]{1,24}》(?:中|里)?(?:记|载|称|说|云|写)|志载|旧志")
HEDGED = re.compile(r"并无(?:明确)?依据|一说|或说|待考|存疑|无从考证|已不可考|说法不一")
RED = re.compile(r"中共|共产党|党支部|革命|烈士|红军|新四军|抗日")
RELIGION = re.compile(r"寺|庵|禅|法师|佛|道观|道士|观音|菩萨|僧|香客")
GONE = re.compile(r"今已?不存|已死亡|因死亡|被.{0,12}除名")      # 只用于 status_text_hint，不改状态
SPEAKER = re.compile(r"^([一-鿿A-Za-z·]{2,14})[：:]")
ROLE = re.compile(r"馆员|法师|志愿者|工作者|受访人|采访人|阿姨|老|先生|女士|师傅|经理")
ASKER = re.compile(r"馆员|志愿者|采访人")
INTERVIEW_HEAD = re.compile(r"受访人：(.+?)采访人：(.+?)时间：([\d.]+)")
DATE = re.compile(r"(\d{4})[年.](\d{1,2})[月.](\d{1,2})")           # 写成 YYYY-MM-DD
```

---

## 附录 B：`schema/entry.schema.json`

```json
{
  "$schema": "https://json-schema.org/draft/2020-12/schema",
  "$id": "entry.schema.json",
  "title": "树的故事条目卡（父块）",
  "description": "一个条目一张卡，不建索引。子块的 unit_id 等于本卡的 unit_id。",
  "type": "object",
  "additionalProperties": false,
  "required": ["unit_id", "source_id", "section", "entry_no", "title", "district", "location", "items", "species",
               "tree_ids", "site_ids", "official_codes", "status", "children", "images", "pdf_pages", "printed_pages",
               "n_chars_body", "n_chars_small", "as_of", "review_status", "pipeline"],
  "properties": {
    "unit_id": {"type": "string", "pattern": "^shbl:story-[0-9]{3}$"},
    "source_id": {"const": "SRC-SHBL"},
    "section": {"const": "树的故事"},
    "entry_no": {"type": "integer", "minimum": 1, "maximum": 105},
    "title": {"type": "string", "minLength": 1, "description": "正文页标题，照印"},
    "district": {"enum": ["吴中区", "姑苏区", "吴江区", "虎丘区（高新区）", "相城区", "工业园区", "昆山市", "常熟市", "太仓市", "张家港市"]},
    "location": {"type": "string", "minLength": 1, "description": "正文页标题处印的地点，可能与索引写法不同"},
    "items": {
      "type": "array", "minItems": 1,
      "items": {
        "type": "object", "additionalProperties": false,
        "required": ["item_no", "location", "species", "status", "status_source", "origin"],
        "properties": {
          "item_no": {"type": "integer", "minimum": 1},
          "location": {"type": "string", "minLength": 1, "description": "索引页写法"},
          "species": {"type": "array", "minItems": 1, "items": {"type": "string", "minLength": 1}},
          "status": {"enum": ["extant", "gone"]},
          "status_source": {"enum": ["index", "text", "index+text"]},
          "status_note": {"type": "string"},
          "origin": {"enum": ["index_main", "index_sub", "override"]}
        }
      }
    },
    "species": {"type": "array", "minItems": 1, "uniqueItems": true, "items": {"type": "string"}},
    "tree_ids": {"type": "array", "items": {"type": "string"}},
    "site_ids": {"type": "array", "items": {"type": "string"}},
    "link": {
      "type": "object", "additionalProperties": false, "required": ["status"],
      "properties": {"status": {"enum": ["explicit", "cross_ref", "manual", "unlinked"]}, "evidence": {"type": "string"}}
    },
    "official_codes": {"type": "array", "uniqueItems": true, "items": {"type": "string"}},
    "status": {"enum": ["extant", "gone", "partial"]},
    "children": {
      "type": "array", "minItems": 1,
      "items": {
        "type": "object", "additionalProperties": false,
        "required": ["chunk_id", "content_type", "item_no", "label"],
        "properties": {
          "chunk_id": {"type": "string", "pattern": "^shbl:story-[0-9]{3}:(body|fieldnote|interview|background)-[0-9]+$"},
          "content_type": {"enum": ["story", "fieldnote", "interview", "background"]},
          "item_no": {"type": "integer", "minimum": 1},
          "label": {"type": "string", "minLength": 1}
        }
      }
    },
    "images": {
      "type": "array", "minItems": 1,
      "items": {
        "type": "object", "additionalProperties": false,
        "required": ["image_id", "file", "role", "pdf_page", "printed_page", "bbox", "native_px", "kind", "rights"],
        "properties": {
          "image_id": {"type": "string", "pattern": "^shbl-img-p[0-9]{3}-[0-9]+$"},
          "file": {"type": "string", "pattern": "^pilot/images/story/shbl-img-p[0-9]{3}-[0-9]+\\.jpg$"},
          "role": {"enum": ["index", "story"]},
          "item_no": {"type": "integer", "minimum": 1},
          "pdf_page": {"type": "integer", "minimum": 6, "maximum": 81},
          "printed_page": {"type": "integer", "minimum": 10, "maximum": 161},
          "bbox": {"type": "array", "minItems": 4, "maxItems": 4, "items": {"type": "number"}},
          "native_px": {"type": "array", "minItems": 2, "maxItems": 2, "items": {"type": "integer", "minimum": 1}},
          "kind": {"enum": ["photo", "painting", "drawing", "map", "document"]},
          "caption": {"type": "string"},
          "credit": {"type": "string"},
          "rights": {
            "type": "object", "additionalProperties": false, "required": ["status"],
            "properties": {"status": {"enum": ["unknown", "museum", "third_party"]}, "note": {"type": "string"}}
          }
        },
        "allOf": [{"if": {"properties": {"role": {"const": "index"}}}, "then": {"required": ["item_no"]}}]
      }
    },
    "captions": {
      "type": "array",
      "items": {
        "type": "object", "additionalProperties": false, "required": ["text", "printed_page"],
        "properties": {"text": {"type": "string", "minLength": 1}, "printed_page": {"type": "integer"}}
      }
    },
    "pdf_pages": {"type": "array", "minItems": 1, "items": {"type": "integer", "minimum": 38, "maximum": 81}},
    "printed_pages": {"type": "array", "minItems": 1, "items": {"type": "integer", "minimum": 74, "maximum": 161}},
    "n_chars_body": {"type": "integer", "minimum": 1},
    "n_chars_small": {"type": "integer", "minimum": 0},
    "as_of": {"type": "string", "pattern": "^[0-9]{4}(-[0-9]{2})?$"},
    "review_status": {"enum": ["pending", "verified", "rejected"]},
    "review_flags": {
      "type": "array",
      "items": {
        "type": "object", "additionalProperties": false, "required": ["code", "note"],
        "properties": {
          "code": {"enum": ["location_conflict", "status_text_hint", "third_party_image", "override", "section_species_unmatched"]},
          "note": {"type": "string"}
        }
      }
    },
    "pipeline": {"type": "string"}
  }
}
```

---

## 附录 C：`schema/story_chunk.schema.json`

```json
{
  "$schema": "https://json-schema.org/draft/2020-12/schema",
  "$id": "story_chunk.schema.json",
  "title": "树的故事子块",
  "description": "检索单位。unit_id 等于所属条目卡的 unit_id。空的可选字段不写。",
  "type": "object",
  "additionalProperties": false,
  "required": ["chunk_id", "unit_id", "part", "parts", "source_id", "content_type", "text", "context_header", "lang",
               "n_chars", "locator", "tree_ids", "site_ids", "site_name", "district", "species", "link",
               "source_tier", "as_of", "review_status", "rights", "content_hash", "pipeline"],
  "properties": {
    "chunk_id": {"type": "string", "pattern": "^shbl:story-[0-9]{3}:(body|fieldnote|interview|background)-[0-9]+$"},
    "unit_id": {"type": "string", "pattern": "^shbl:story-[0-9]{3}$"},
    "part": {"type": "integer", "minimum": 1},
    "parts": {"type": "integer", "minimum": 1},
    "source_id": {"const": "SRC-SHBL"},
    "content_type": {"enum": ["story", "fieldnote", "interview", "background"]},
    "text": {"type": "string", "minLength": 1},
    "context_header": {"type": "string", "pattern": "^《树碑立传》树的故事 第[0-9]+条｜"},
    "lang": {"const": "zh-Hans"},
    "n_chars": {"type": "integer", "minimum": 1},
    "search_terms": {"type": "array", "minItems": 1, "uniqueItems": true, "items": {"type": "string", "minLength": 1}},
    "figures": {
      "type": "array", "minItems": 1,
      "items": {
        "type": "object", "additionalProperties": false, "required": ["caption", "printed_page"],
        "properties": {"caption": {"type": "string", "minLength": 1}, "printed_page": {"type": "integer"}}
      }
    },
    "locator": {
      "type": "object", "additionalProperties": false,
      "required": ["section", "entry_no", "title", "heading_path", "pdf_pages", "printed_pages"],
      "properties": {
        "section": {"const": "树的故事"},
        "entry_no": {"type": "integer", "minimum": 1, "maximum": 105},
        "title": {"type": "string", "minLength": 1},
        "heading_path": {"type": "array", "minItems": 2, "items": {"type": "string", "minLength": 1}},
        "pdf_pages": {"type": "array", "minItems": 1, "items": {"type": "integer", "minimum": 38, "maximum": 81}},
        "printed_pages": {"type": "array", "minItems": 1, "items": {"type": "integer", "minimum": 74, "maximum": 161}}
      }
    },
    "tree_ids": {"type": "array", "uniqueItems": true, "items": {"type": "string", "pattern": "^[a-z0-9-]+$"}},
    "site_ids": {"type": "array", "uniqueItems": true, "items": {"type": "string", "pattern": "^S[0-9]{2,}$"}},
    "site_name": {"type": "string", "minLength": 1},
    "district": {"enum": ["吴中区", "姑苏区", "吴江区", "虎丘区（高新区）", "相城区", "工业园区", "昆山市", "常熟市", "太仓市", "张家港市"]},
    "species": {"type": "array", "minItems": 1, "uniqueItems": true, "items": {"type": "string", "minLength": 1}},
    "official_codes": {"type": "array", "minItems": 1, "uniqueItems": true, "items": {"type": "string"}},
    "link": {
      "type": "object", "additionalProperties": false, "required": ["status"],
      "properties": {"status": {"enum": ["explicit", "cross_ref", "manual", "unlinked"]}, "evidence": {"type": "string"}}
    },
    "source_tier": {"const": "B"},
    "as_of": {"type": "string", "pattern": "^[0-9]{4}-[0-9]{2}(-[0-9]{2})?$"},
    "review_status": {"enum": ["pending", "verified", "rejected"]},
    "review_note": {"type": "string"},
    "certainty_flags": {"type": "array", "minItems": 1, "uniqueItems": true, "items": {"enum": ["legend", "recorded", "hedged"]}},
    "conflict_ids": {"type": "array", "minItems": 1, "uniqueItems": true, "items": {"type": "string", "pattern": "^cf-[a-z0-9-]+$"}},
    "sensitivity": {"type": "array", "minItems": 1, "uniqueItems": true, "items": {"enum": ["red_history", "religion", "personal_info"]}},
    "rights": {
      "type": "object", "additionalProperties": false, "required": ["holder", "display"],
      "properties": {"holder": {"type": "string"}, "display": {"enum": ["full", "excerpt", "link_only"]}, "note": {"type": "string"}}
    },
    "content_hash": {"type": "string", "pattern": "^sha256:[0-9a-f]{16}$"},
    "pipeline": {"type": "string", "minLength": 1},
    "interview": {
      "type": "object", "additionalProperties": false, "required": ["speakers"],
      "properties": {
        "speakers": {
          "type": "array", "minItems": 1,
          "items": {
            "type": "object", "additionalProperties": false, "required": ["label", "side"],
            "properties": {"label": {"type": "string", "minLength": 1}, "side": {"enum": ["ask", "answer"]}}
          }
        },
        "observed_on": {"type": "string", "pattern": "^[0-9]{4}-[0-9]{2}-[0-9]{2}$"}
      }
    },
    "fieldnote": {
      "type": "object", "additionalProperties": false, "required": ["author_role"],
      "properties": {
        "author_role": {"enum": ["志愿者", "采访人"]},
        "observed_on": {"type": "string", "pattern": "^[0-9]{4}-[0-9]{2}-[0-9]{2}$"}
      }
    },
    "background": {
      "type": "object", "additionalProperties": false, "required": ["topic"],
      "properties": {"topic": {"type": "string", "minLength": 1}}
    }
  },
  "allOf": [
    {"if": {"properties": {"content_type": {"const": "interview"}}}, "then": {"required": ["interview"]}, "else": {"not": {"required": ["interview"]}}},
    {"if": {"properties": {"content_type": {"const": "fieldnote"}}}, "then": {"required": ["fieldnote"]}, "else": {"not": {"required": ["fieldnote"]}}},
    {"if": {"properties": {"content_type": {"const": "background"}}}, "then": {"required": ["background"]}, "else": {"not": {"required": ["background"]}}}
  ]
}
```

---

## 附录 D：为什么这样定（改规则之前先看）

- **为什么不是整条一块**：没有小字的 90 个条目本来就是"一条一块"。问题出在有小字的 15 个条目上：
  - 长访谈会稀释整条的向量；
  - 通用知识就搜不到了，例如第 26 条讲伤口处理的访谈，提问的人根本不会提到榉树或湖沙庙。
- **为什么小字要单独建索引，命中后又要带回整张卡**：
  - 单独建索引，小字本身才能被搜到。
  - 小字单独看常常不知道说的是哪棵树，带回整张卡，模型才知道地点和树种。条目都很短，带回整张卡的代价很小。
- **为什么树种小节要分块**：避免把一棵树的树龄、典故安到同一条目的另一棵树上（第 1 条有三种树，第 2 条有两个地点）。
- **为什么状态只认索引**：正文关键词会误报（第 74 条），状态又是用户最常问、最不能答错的事实之一。
- **为什么区县以地图为准**：第 100 条的索引照片排在太仓市分隔页之前，按照片顺序推会误判为常熟市；分隔页地图上它属于太仓市。
- **以后再考虑的**：
  - 多向量（给每块生成假设问题），等评测显示召回不够时再加；
  - GraphRAG，目前用 `tree_ids` 关联实体表就够了；
  - 用大模型做 contextual retrieval，`context_header` 已经是它的规则版。

---

## 变更记录

| 日期 | 内容 |
|---|---|
| 2026-11-04 | v1：初版规则，数字来自原型实测 |
| 2026-11-04 | v2：改为独立文件，写入全部规则、常量、子块 schema 和数据存储形式（JSONL 加 SQLite 检索库）；区县表第 100 条改为太仓市；`search_terms` 只对主 item 加正文页地点 |
