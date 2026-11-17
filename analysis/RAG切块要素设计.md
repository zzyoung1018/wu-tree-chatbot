# RAG 切块（chunk）要素设计

> 2026-11-04 实施说明：“树的故事”现按《树的故事父子块规则.md》v2.1 执行（正文优先、105张父卡、138个子块）。本文旧切分示例保留作设计背景；实际数据与块内容以生成的 entries_story.jsonl / chunks_story.jsonl 为准。现有 FAQ 证据引用 shbl:story-018:body-1 已核对，仍有效。

> 适用范围：《树碑立传》全书，以及以后加入的铭牌转录、现场调查、FAQ、媒体报道。
> 配套文件：
> - [schema/chunk.schema.json](schema/chunk.schema.json)：字段的正式定义，可以直接用来校验数据。
> - [scripts/build_story_chunks.py](scripts/build_story_chunks.py)：按本设计把"树的故事"切块。
> - [scripts/validate_chunks.py](scripts/validate_chunks.py)：校验脚本。
> - [pilot/chunks_story.jsonl](pilot/chunks_story.jsonl)：试验产出的 150 个真实块。
> - [pilot/chunk_examples.jsonl](pilot/chunk_examples.jsonl)：铭牌、文章、FAQ 三类示例。

---

## 0. 结论

每个 chunk 由五组要素构成：

1. **标识**：chunk_id、所属单元、序号、来源。ID 由结构决定（如 `shbl:story-021:interview-1`），重新解析后不变，评测集和用户反馈可以长期引用。
2. **内容**：原文 `text`、检索用的标题行 `context_header`、内容类型。原文一字不改；标题行单独存，向量化时拼在原文前面。
3. **出处**：章节、条目号、标题路径、PDF 页码、印刷页码。回答里的引用由它生成，例如"《树碑立传》第 92 页"。
4. **实体**：树（tree_ids）、地点、区县、树种、官方编号。用来过滤检索结果，不让向量去"猜"用户问的是哪棵树。
5. **可信度与约束**：来源层级、资料时间、核查状态、传说/史载/存疑标记、冲突、敏感类别、版权。生成答案和事后校验都靠这些字段。

P0（原型必须有）共 19 个字段，就是 schema 里的 required。试验中 150 个块的 P0 字段全部有值（tree_ids、site_ids 允许为空数组）。P1 是可靠性功能需要的字段，P2 以后再做。

三条原则：

- **原文不改写，检索增强单独存**：`text` 只做空白和数字间隙的规范化。为检索加的内容（标题行、补充检索词）放在别的字段，所以引用出来的永远是原文。
- **chunk 只记"这段话是什么"，不记"树现在怎样"**：树龄、坐标、保护等级、是否死亡这类会变化、需要裁决的事实放在 trees 表，chunk 用 tree_ids 关联。否则同一事实散落在几十个块里，改了一处会漏掉别处。
- **每个字段都要有用途**：§2 的总表为每个字段写明用在问答流程的哪一步（过滤、检索、提示词、校验、引用、运维）。找不到用途的字段不加。

**试验结果**：用这套设计把"树的故事"105 个条目切成 150 个块，分别是正文 128、走访记录 9、访谈 7、背景小栏 6。全部通过 schema 校验，原文逐字覆盖。块长中位数 339 字，90% 在 521 字以内。按这个密度推算，全书约 550 个块（上一份评估的估计是 400–600 个）。

---

## 1. chunk 在知识库中的位置

```
sources（来源表）
   │
   ├── chunks（可检索的原文片段，本文的主题）
   │      │ tree_ids / site_ids
   │      ▼
   │   trees / sites / species（实体表：树龄、坐标、等级、现状，每项都带来源）
   │      ▲
   └── claims（一条一事的知识条目，带可信度）── conflicts（互相冲突的说法及馆方口径）

FAQ（馆方审核过的问答）也存成 chunk，content_type = faq
```

上一份方案提出了分层回答。各层与数据的对应如下：

| 层 | 回答什么 | 查什么 |
|---|---|---|
| 1 结构化事实 | 树龄、编号、等级、位置、现状 | trees 等实体表，模板输出 |
| 2 FAQ | 高频问题 | `content_type = faq` 的块。问题相似度足够高时，直接使用审核过的答案 |
| 3 讲述 | 故事、历史、文化 | 其余的块，混合检索后由模型生成 |
| 4 拒答 | 资料中没有依据 | — |

**不做成 chunk 的内容**：

| 内容 | 去处 | 原因 |
|---|---|---|
| 区县索引表的行（编号、树种、地点、树龄） | trees 表 | 数字和编号要精确查询；PDF 里这部分的数字字体还是坏的 |
| 树龄、坐标、等级、现状 | trees 表 | 会变化、会冲突，必须集中维护 |
| docx 里 AI 生成或没有出处的段落（E 级） | review_queue | 不能作为回答依据。找到正式出处后，按正式出处入库 |
| 文章的参考文献列表 | 所属文章块的 `essay.references` | 放进向量库只会干扰检索 |
| 目录、版权页、页眉页脚 | sources 表 | 没有检索价值 |
| 图片页、展览照片 | media 表；图注挂在块的 `figures` 上 | 文字太少 |
| 馆方年报 | 用户画像分析 | 运营数据，不是问答内容 |
| 同一段文字的副本（如 docx 抄自本书的段落） | 不入库，或标 `dup_of` | 同一内容只保留最上游的来源 |

---

## 2. 字段总表

"级别"一栏：P0 = 原型必须有；P1 = 可靠性功能需要；P2 = 以后再做。

### 2.1 标识

| 字段 | 示例 | 级别 | 怎么填 | 用在哪 |
|---|---|---|---|---|
| `chunk_id` | `shbl:story-021:interview-1` | P0 | 解析时按结构生成（规则见 §4） | 引用、日志、评测集 |
| `unit_id` | `shbl:story-021` | P0 | 同上 | 命中一个分段时，取同一单元的前后段补上下文 |
| `part` / `parts` | 1 / 2 | P0 | 同上 | 同上 |
| `source_id` | `SRC-SHBL` | P0 | 登记来源时确定 | 关联 sources 表（书名、出版者、层级、许可） |
| `content_hash` | `sha256:ef9d…` | P1 | 对"标题行 + 原文"自动计算 | 向量化输入变了才重算向量 |
| `pipeline` | `pilot_stories@3+build_story_chunks@1` | P1 | 自动 | 出错时追溯是哪个版本的解析器 |

### 2.2 内容

| 字段 | 示例 | 级别 | 怎么填 | 用在哪 |
|---|---|---|---|---|
| `content_type` | `story`、`interview`…… | P0 | 解析规则 | 决定提示词里的转述口吻（§3）；排序时按类型加权 |
| `text` | 原文 | P0 | 解析 | 生成的依据；引用时展示原文 |
| `context_header` | `《树碑立传》树的故事 第21条｜吴中区 光福石壁永慧禅寺｜石楠｜访谈：宽尘法师` | P0 | 由元数据拼接 | 向量化和关键词检索时拼在原文前面；在提示词里作为块的标题 |
| `lang` | `zh-Hans` | P0 | 默认值 | 以后加入英文版时过滤 |
| `n_chars` | 451 | P0 | 自动 | 控制提示词长度 |
| `search_terms` | `["吴中203"]` | P1 | 建索引时从实体表生成 | 只进关键词索引，补上原文里没有的编号、别名、学名。第 21 条正文没写"吴中203"，没有这个字段，按编号就搜不到它 |
| `figures` | `[{"caption": …, "printed_page": 92}]` | P2 | 解析 | 回答时配图；以后做图片检索 |

**向量化与关键词索引的输入**：向量化用 `context_header + 换行 + text`；关键词索引用 `context_header + text + search_terms`。实体 ID 不参与向量化，所以以后补关联、改关联时只更新元数据，不用重算向量。

### 2.3 出处（`locator`）

| 字段 | 示例 | 级别 | 怎么填 | 用在哪 |
|---|---|---|---|---|
| `locator.section` | `树的故事` | P0 | 解析 | 引用 |
| `locator.entry_no` | 21 | P0（故事条目） | 解析 | 引用；按条目查找 |
| `locator.title`、`heading_path` | `["树的故事", "21 光福石壁永慧禅寺·石楠", "访谈"]` | P0 | 解析 | 引用；拼标题行 |
| `locator.printed_pages` | `[95]` | P0（书） | 由 PDF 页和左右半页算出：印刷页 = 2 ×（PDF 页 − 1）+ 半页（左 0 右 1），已用页脚页码核对 | 引用"第 95 页"，读者可以翻书核对 |
| `locator.pdf_pages` | `[48]` | P0（书） | 解析 | 内部定位、调试 |
| `locator.url` / `media_id` / `record` | — | 非书籍来源必填其一 | 网页链接、照片 ID、内部表的行（如 `faq:0001`） | 引用 |

schema 要求每个块至少有印刷页、链接、照片 ID、内部记录四者之一，保证任何一块都能追溯到原处。

### 2.4 实体

| 字段 | 示例 | 级别 | 怎么填 | 用在哪 |
|---|---|---|---|---|
| `tree_ids` | `["sbs-shinan"]` | P0 | 人工或规则关联（见 `link`）；尚未建档时为空数组 | 按树过滤；连接 trees 表取事实 |
| `site_ids` | `["S03"]` | P0 | 同上 | 按地点过滤（"这座寺里还有什么古树"） |
| `site_name` | `光福石壁永慧禅寺` | P0 | 书中印刷的地点原文 | 实体表建好之前的过滤字段；拼标题行 |
| `district` | `吴中区` | P0 | 由索引章节的区县分隔页推出（对应表见附录 B） | 按区县过滤 |
| `species` | `["石楠"]` | P0 | 取条目标题和树种小标题，同义词归一（樟树 → 香樟） | 按树种过滤（"苏州有哪些古银杏"） |
| `official_codes` | `["吴中203"]` | P1 | 正文中的编号（正则）加上关联得到的编号 | 编号精确匹配；生成 `search_terms` |
| `link.status`、`link.evidence` | `manual`，"地点、树种与 sbs-shinan 一致" | P1 | 建立关联时记录 | 标明关联有多可靠：`explicit`（文中写明编号）、`cross_ref`（由书中别处得到）、`manual`（人工比对）、`unlinked`（未关联） |
| `persons` | `[{"name": "沈复", "kind": "historical"}]` | P2 | 人名表加人工 | 回答"乾隆和哪些古树有关"；配合隐私规则 |

### 2.5 可信度

| 字段 | 示例 | 级别 | 怎么填 | 用在哪 |
|---|---|---|---|---|
| `source_tier` | `B` | P0 | 继承 sources 表，单块可以调整 | 提示词；排序加权。只允许 A–D，E 级不入库 |
| `as_of` | `2024-08`；`2023-04-15` | P0 | 默认取来源日期；写明日期的走访和访谈取当天 | 文中的"今年""目前"按它理解。例如第 55 条"今年恰是它的'百年寿辰'"指 2024 年，2026 年回答时不能照搬 |
| `review_status` | `pending` | P0 | 审核流程 | 线上只检索 `verified`；原型阶段可以放宽为"不是 `rejected`" |
| `review_note` | — | P1 | 审核人填写 | 记录驳回或保留的理由 |
| `certainty_flags` | `["legend", "recorded", "hedged"]` | P1 | 关键词规则初筛，再人工确认 | 提示词要求对应措辞（"相传""据《××》记载""一说"）；校验答案是否照做 |
| `claim_ids` | `["C-SBS-0003"]` | P1 | 由 claims 表反查生成 | 关键事实走 claims 里的逐条可信度 |
| `conflict_ids` | `["cf-hanshan-arrival"]` | P1 | 由 conflicts 表反查生成 | 命中时，把各个说法和馆方口径一起放进提示词 |
| `dup_of` | 另一个 chunk_id | P1 | 近似重复检测 | 检索结果去重，避免同一段内容占满前几名 |

`source_tier` 说的是来源，`certainty_flags` 说的是这段文字本身。《树碑立传》整体是 B 级（馆方出版的文献），但书里的传说、口述、存疑说法要靠 `content_type` 和 `certainty_flags` 区分。

### 2.6 使用约束

| 字段 | 示例 | 级别 | 怎么填 | 用在哪 |
|---|---|---|---|---|
| `sensitivity` | `["religion", "personal_info"]` | P1 | 关键词规则，再人工确认 | `red_history`：只复述原文事实，不扩写、不评价；`religion`：措辞客观、尊重；`personal_info`：默认只说身份（"一位志愿者"），能否说出姓名由馆方决定 |
| `rights.holder`、`rights.display` | `吴文化博物馆`，`full` | P1 | 登记来源时确定 | 能否展示原文：`full`（可展示全文）、`excerpt`（只能节选）、`link_only`（只给链接） |
| `audience` | `["general"]` | P2 | 按章节给默认值，再人工调整 | 按观众类型调整排序，与年报中的观众画像对应 |

按类型的专属字段（如访谈的说话人、文章的作者）只在对应类型的块里出现，见 §3。

---

## 3. 内容类型

| content_type | 来源 | 切分规则 | 专属字段 | 回答时的口吻 | 默认层级 |
|---|---|---|---|---|---|
| `story` | 树的故事正文 | 先按树种小标题分，再在段落处切 | — | "据《树碑立传》……" | B |
| `background` | 条目侧栏里的"关于××"等介绍 | 段落 | `background.topic` | "《树碑立传》介绍××时提到……" | B |
| `interview` | 条目侧栏或文章里的访谈 | 只在提问前切，问和答不分开 | `interview.speakers`（标明提问方、回答方）、`observed_on` | "宽尘法师在接受采访时说……"（口述，归于说话人） | B |
| `fieldnote` | 志愿者走访记录 | 段落 | `fieldnote.observed_on`、`author_role` | "志愿者 2023 年 4 月走访时记录……" | B |
| `art` | 古树与艺术 | 作品信息进字段；艺术家自述、策展人评述各成一块 | `art.work_title`、`artist`、`medium`、`year`、`statement_by` | "艺术家黄硕在作品《推演》中……"（是观点，不当作事实） | B |
| `essay` | 专栏文章、研究文章 | 按小节，再按段落，约 400 字一块 | `essay.article_title`、`author`、`subheading`、`references` | "栾峦在《曲径通幽花照影，禅房古木绿沉香》一文中写道……" | B |
| `intro` | 前言、导论 | 段落 | — | "据《树碑立传》前言……" | B |
| `sign` | 现场铭牌、介绍牌、石刻 | 一块牌子一块 | `sign.sign_type`、`issuer`、`year`、`media_id` | "现场的古树铭牌标明……" | 官方铭牌 A；捐赠石碑、介绍牌视立牌者而定 |
| `survey` | 现场调查采集表 | 一次调查一块，由结构化记录按模板生成文字（不用大模型） | `survey.survey_id`、`observed_on` | "2026 年 9 月的现场调查记录……" | A |
| `faq` | 馆方审核的问答 | 一问一块 | `faq.question`、`alt_questions`、`evidence_chunk_ids`、`owner_role` | 直接使用审核过的答案 | 取其依据中最低的层级 |
| `media_article` | 新闻报道、机构稿 | 段落；只存可以引用的片段 | `media_article.publisher`、`url`、`published_at` | "据澎湃新闻报道……" | C |

`content_type` 是提示词里最直接的控制手段：同一句"1569 年"，出自文章就写"栾峦在文中写道"，出自访谈就写"宽尘法师说"，模型不会把口述当成定论。

---

## 4. 切分规则

1. **边界**：不跨条目、文章、侧栏。条目里有树种小标题时按小标题切，让一个块尽量只讲一棵树或一个树种。访谈只在提问前切。不在句子中间切。
2. **长度**：不超过 600 字的整块保留；超过时在段落处切成约 400 字的几段；单个段落超过 900 字时才在句末切。不足 80 字的小节并入相邻小节。
3. **不重叠**：每段都带完整的 `context_header`。检索命中某一段后，按 `unit_id` 和 `part` 取前后段补上下文（小块检索、大块阅读）。不使用重叠窗口，避免同一句话被引用两次。
4. **ID 规则**：`{来源简码}:{单元}:{块类}-{序号}`。
   - 书中有编号的用编号：`shbl:story-021`。
   - 没有编号的文章、作品用起始印刷页：`shbl:essay-p308`、`shbl:art-p174`。
   - 其他来源：`sign:sbs-shinan-plate`、`faq:0001`、`survey:dlzb-2026-09-12`。
   - ID 只由结构决定，不用流水号，也不用内容哈希，所以重新解析后同一段的 ID 不变。
5. **实体继承**：侧栏块继承所在条目的 `tree_ids`、`site_ids` 和 `species`，以后可以人工收窄。
6. **图注**：挂在所在条目第一个正文块的 `figures` 上，不单独成块。

---

## 5. 示例

### 5.1 故事正文：第 18 条司徒庙古柏

以下是试验输出的原样，只把 `text` 截短了。

```json
{
  "chunk_id": "shbl:story-018:body-1",
  "unit_id": "shbl:story-018", "part": 1, "parts": 1,
  "source_id": "SRC-SHBL",
  "content_type": "story",
  "text": "柏是苏州常见的古树科属，分圆柏、刺柏、龙柏等。……",
  "context_header": "《树碑立传》树的故事 第18条｜吴中区 光福司徒庙｜圆柏",
  "lang": "zh-Hans", "n_chars": 506,
  "locator": {"section": "树的故事", "entry_no": 18, "title": "圆柏",
              "heading_path": ["树的故事", "18 光福司徒庙·圆柏"],
              "pdf_pages": [47], "printed_pages": [92]},
  "tree_ids": ["stm-yuanbai"], "site_ids": ["S02"],
  "site_name": "光福司徒庙", "district": "吴中区", "species": ["圆柏"],
  "official_codes": ["吴中186", "吴中187", "吴中188", "吴中189"],
  "link": {"status": "cross_ref",
           "evidence": "地点、树种与 stm-yuanbai 一致；编号见古树与艺术章节《推演》（印刷页 174），四棵个体的对应待确认"},
  "source_tier": "B", "as_of": "2024-08", "review_status": "pending",
  "certainty_flags": ["legend", "recorded", "hedged"],
  "sensitivity": ["religion"],
  "rights": {"holder": "吴文化博物馆", "display": "full", "note": "馆方尚未确认可否在 AI 应用中引用原文"},
  "search_terms": ["吴中186", "吴中187", "吴中188", "吴中189"],
  "figures": [{"caption": "吴县文管会保留的清奇古怪古柏档案。", "printed_page": 92}],
  "content_hash": "sha256:…", "pipeline": "pilot_stories@3+build_story_chunks@1"
}
```

要点：

- `tree_ids` 用登记表的 ID `stm-yuanbai`（"清奇古怪"四棵古柏的群组），官方编号放在 `official_codes`。正文没有写编号，编号来自艺术章节《推演》的作品说明，所以 `link.status` 是 `cross_ref`。四棵个体与 186–189 的对应关系还要馆方确认。
- 有了 `search_terms`，"吴中187 是哪棵树"这样的提问也能用关键词检索到这一块。
- `certainty_flags` 三个标记都有：正文写了"传说"，引了《浮生六记》，还明确说"邓禹手植""乾隆赐名"并无明确依据。回答时这三种措辞都要保留。

### 5.2 访谈：第 21 条石楠的侧栏

与 5.1 不同的字段：

```json
{
  "chunk_id": "shbl:story-021:interview-1", "part": 1, "parts": 2,
  "content_type": "interview",
  "context_header": "《树碑立传》树的故事 第21条｜吴中区 光福石壁永慧禅寺｜石楠｜访谈：宽尘法师",
  "locator": {"heading_path": ["树的故事", "21 光福石壁永慧禅寺·石楠", "访谈"], "pdf_pages": [48], "printed_pages": [95]},
  "tree_ids": ["sbs-shinan"], "site_ids": ["S03"], "official_codes": ["吴中203"], "search_terms": ["吴中203"],
  "link": {"status": "manual", "evidence": "地点、树种与 sbs-shinan 一致；编号来自现场官方铭牌"},
  "certainty_flags": ["legend"],
  "conflict_ids": ["cf-hanshan-arrival"],
  "sensitivity": ["religion", "personal_info"],
  "interview": {"speakers": [{"label": "吴博馆员王铭钰", "side": "ask"},
                             {"label": "宽尘法师", "side": "answer"},
                             {"label": "志愿者周缘", "side": "ask"}]}
}
```

- 宽尘法师说寺庙始于"明朝隆庆年间"，与专栏文章中的 1569 年一致，与同一篇文章里的 1596 年冲突，所以挂上 `cf-hanshan-arrival`。
- `legend` 来自"据传以前还有金镶玉竹"。
- 第 27 条的访谈写明了"时间：2023.4.8"，那一块的 `as_of` 和 `interview.observed_on` 都是 `2023-04-08`。

### 5.3 第 55 条雪松

- `sensitivity: ["red_history"]`：涉及中共苏州独立支部。回答只复述原文事实，不扩写、不评价。
- `conflict_ids: ["cf-xuesong-planted"]`：书中写 1924 年种下，澎湃新闻稿推算为 1923 年。
- `as_of: "2024-08"`：正文"今年恰是它的'百年寿辰'"中的"今年"指 2024 年。

### 5.4 其他类型

完整内容见 [pilot/chunk_examples.jsonl](pilot/chunk_examples.jsonl)，三条都通过了校验。

- **官方铭牌** `sign:sbs-shinan-plate:text-1`：`source_tier` 为 A，出处用 `locator.media_id = sbs-shinan_07_02.jpg`（铭牌照片）。铭牌写"800余年"，与"元朝年间"的说法冲突，挂 `cf-shinan-age`。
- **专栏文章** `shbl:essay-p308:body-2`：ID 用文章起始印刷页 308，`essay` 字段记篇名和作者栾峦。这段引《光福志》写隆庆三年（1569 年），又写"一说"嘉靖二年，因此标记 `recorded` 和 `hedged`，并挂 `cf-hanshan-arrival`。同一篇文章写 1596 年的那一块也挂同一个冲突。
- **FAQ** `faq:0001:qa-1`："清奇古怪真是邓禹种的、乾隆赐的名吗？"。`faq.evidence_chunk_ids` 指向 `shbl:story-018:body-1`，校验脚本会检查被引用的块是否存在。上线前必须由馆方审核为 `verified`。

### 5.5 冲突表

`conflict_ids` 引用的是这张表。每行另有 `museum_position`（馆方口径；为空时回答并列各说）和 `status` 两个字段。

| conflict_id | 问题 | 各说法及出处 | 涉及的块 |
|---|---|---|---|
| `cf-xuesong-planted` | 雪松的种植年份 | 1924 年（《树碑立传》第 122 页）；1923 年（澎湃新闻稿推算） | `shbl:story-055:body-1` |
| `cf-shinan-age` | 石楠的树龄 | 800 余年（2023 年官方铭牌；书第 94 页"从800年前"）；元朝年间（寺内石碑，书第 94 页转述） | `shbl:story-021:body-1`、`sign:sbs-shinan-plate:text-1` |
| `cf-hanshan-arrival` | 憨山复创石壁精舍的年份 | 隆庆三年即 1569 年（栾峦文，第 309 页，引《光福志》；宽尘法师访谈"明朝隆庆年间"）；1596 年（同一篇文章，第 310 页） | `shbl:essay-p308` 的两块、`shbl:story-021:interview-1` |

---

## 6. 字段在问答流程里的用法

1. **识别实体**：用 trees、sites、species 表里的名称、别名、编号匹配问题（如"吴中203""睡龙""司徒庙"），得到 tree_ids、site_ids、species。
2. **过滤**：`review_status`（线上为 verified）、`lang`，以及上一步识别出的实体。识别不出实体时不过滤，只靠检索。
3. **混合检索**：关键词检索（BM25 / SQLite FTS5）和向量检索各取一批，合并后用重排模型排序。两种索引的输入见 §2.2。
4. **去重与补全**：按 `dup_of` 去重；命中分段时按 `unit_id` 和 `part` 补前后段。
5. **拼提示词**：每块用短编号 [S1]、[S2]……，由代码映射回 chunk_id，减少模型编造 ID。可信度相关的字段写成块头，命中树的事实卡（取自 trees 表）一起放进去：

   ```
   [S1] 《树碑立传》树的故事 第18条｜吴中区 光福司徒庙｜圆柏
        类型：故事｜来源：B｜资料时间：2024-08｜标记：含传说、含史载、含存疑
        柏是苏州常见的古树科属……
   [S2] 《树碑立传》树的故事 第21条｜吴中区 光福石壁永慧禅寺｜石楠｜访谈：宽尘法师
        类型：访谈（口述）｜来源：B｜资料时间：2024-08｜冲突：憨山到石壁的年份有 1569、1596 两说
        吴博馆员王铭钰：能向我们简单地介绍下永慧禅寺吗？……
   ```

6. **校验**（用代码实现，不依赖模型自觉）：
   - 答案引用的编号必须在本次提供的块中。
   - 答案里的年份、数字、编号，必须能在被引用的块或事实卡中找到。
   - 引用了 `legend` 块时，答案必须出现"相传""传说""据说"等词；引用了 `hedged` 块时，不能把存疑的说法写成定论。
   - 引用了带 `conflict_ids` 的块时，答案必须给出各个说法，或者采用馆方口径。
   - 引用了 `red_history` 块时，答案中的事实句要能在原文中找到对应。
7. **展示引用**：由 `locator` 生成，例如"《树碑立传》第 92 页（树的故事·18）""现场铭牌照片""澎湃新闻（日期，链接）"。
8. **日志与评测**：记录每次命中的 chunk_id 和分数。评测集的标准答案也用 chunk_id 标注依据，所以 ID 必须稳定。

---

## 7. 试验结果

| 项目 | 结果 |
|---|---|
| 输入 | "树的故事"（PDF 第 38–81 页）105 个条目 |
| 输出 | 150 个块：story 128、fieldnote 9、interview 7、background 6；3 条图注挂在 `figures` 上 |
| 校验 | 全部通过 schema 和附加规则（ID 唯一、part ≤ parts、PDF 页与印刷页一致等）。另外用故意改坏的数据测试过，校验脚本能报出每一处错误 |
| 原文覆盖 | 105 个条目的正文和侧栏全部逐字落入块中，没有遗漏，也没有重复 |
| 块长 | 最短 46 字，中位数 339 字，90% 不超过 521 字，最长 857 字（一段很长的访谈回答） |
| 分块情况 | 84 个条目整条一块，19 个分成 2 块，2 个分成 3 块 |
| 字段填写 | P0 字段 150 块全部有值；species、district、印刷页 150/150；tree_ids 只有 6 块有值（本项目的三棵树） |
| 标记 | legend 42 块、recorded 38 块、hedged 3 块；religion 83 块、personal_info 13 块、red_history 2 块（第 55 条、第 84 条） |
| 冲突 | 3 个已知冲突自动挂到 3 个块上 |

**已知问题**：

- `tree_ids` 只关联了本项目的三棵树。其余 102 个条目要等全书的 trees 表建好；建好后只更新元数据，不用重新切块，也不用重算向量。
- 关键词规则会误标，也会漏标。`religion` 只要出现"寺""庵"就标，偏宽；`certainty_flags` 只能初筛，换一种写法的存疑说法会漏掉。上线前需要人工检查带标记的块。
- 还有个别解析瑕疵：第 11 条的一行图注混进了侧栏；第 23 条的地点是两个地点连在一起；第 26 条访谈的第 3 段其实是采访手记。
- 文章、艺术、走访三类的解析器还没写。§3 的规则和 chunk_examples.jsonl 中的示例是按版式设计的，要等解析器做完再验证。

---

## 8. 需要决定的事

**团队**：

1. **全书的 tree_id 命名**：建议沿用"站点简称-树种拼音"（如 `sbs-shinan`），配一张别名表；同一地点同一树种有多棵时加序号。另一种做法是用流水号，稳定但不好读。
2. **原型阶段能否检索 `pending` 的块**：建议允许，界面上标注"资料待馆方审核"。

**馆方**：

1. 能否在 AI 回答中直接展示书中原文（决定 `rights.display`）。
2. AI 能否说出志愿者、受访者的姓名（决定 `personal_info` 的处理方式）。
3. 三个冲突采用哪种口径。
4. 吴中186–189 与"清、奇、古、怪"四棵个体的对应关系。

---

## 附录 A：与之前文档的差异

与[难度评估](RAG数据库化难度评估.md) §4.2 示例相比：

| 原来 | 现在 | 原因 |
|---|---|---|
| `tree_ids` 写官方编号 | `tree_ids` 写登记表 ID，编号放 `official_codes` | 有的树没有编号（如雪松），编号也可能随普查变化 |
| `tree_ids_via` | `link.status` + `link.evidence` | 关联方式需要分级 |
| `site` | `site_ids` + `site_name` | 实体表建好之前也能过滤 |
| `status: "alive"` | 删除，放在 trees 表 | 树的现状会变 |
| `flags` | `certainty_flags` + `conflict_ids` + `sensitivity` | 三种标记的用途不同 |
| `printed_page`、`pdf_page`（单个值） | `locator.printed_pages`、`pdf_pages`（数组） | 一个块可能跨页 |
| `story-018-01` | `shbl:story-018:body-1` | 加上来源简码和块类，多个来源时不会冲突 |
| `content_type` 里的 `oral` | 并入 `interview`；新增 `background`、`sign`、`survey`、`faq`、`media_article` | 覆盖书以外的来源 |

与[数据分析与清理设计方案](数据分析与清理设计方案.md)相比：

- 当时把寺院人文内容放在伪树木 ID `sbs-site` 下，现在改为 `site_ids = ["S03"]`。
- `knowledge/passages/` 改为 `chunks.jsonl`；claims 通过证据块 ID 关联到 chunk。

## 附录 B：用法与对照表

```bash
python -I scripts/pilot_stories.py "../树碑立传：吴中古树名木的故事教育读物(1).pdf" 38 81 pilot/stories.jsonl
python -I scripts/build_story_chunks.py pilot/stories.jsonl pilot/chunks_story.jsonl
python -I scripts/validate_chunks.py schema/chunk.schema.json pilot/chunks_story.jsonl pilot/chunk_examples.jsonl
```

在 `analysis/` 目录下运行。依赖：pymupdf（解析）、jsonschema（校验）。`pilot_stories.py` 这次加上了每段的印刷页码，并保留了树种小标题在正文中的位置；重新运行后，条目数、字数等统计与上一版完全一致。

**区县与条目号**：由索引章节（PDF 第 6–37 页）的区县分隔页得出。

| 区县 | 条目 | 区县 | 条目 |
|---|---|---|---|
| 吴中区 | 1–29 | 工业园区 | 74 |
| 姑苏区 | 30–55 | 昆山市 | 75–84 |
| 吴江区 | 56–68 | 常熟市 | 85–100 |
| 虎丘区（高新区） | 69–71 | 太仓市 | 101–104 |
| 相城区 | 72–73 | 张家港市 | 105 |
