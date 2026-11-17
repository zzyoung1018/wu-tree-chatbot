# 《树碑立传》RAG 与“树语”问答系统改进规划书 Ver 4（离线深度富化预处理与中英双语导览架构规范）

> **角色说明**：本文档由项目 Planner 输出，供下游实现 Agent 按序执行离线知识库富化、运行时正则清理及中英双语导览升级。
> **前置基准**：基于 `improve_v3.md` 已落地的“极简三段式流水线（`UNDERSTAND` → `RETRIEVE & CURATE` → `ANSWER & MINIMAL VERIFY`）”，当前后端单元测试与前端生产构建全部通过。
> **核心前提与设计哲学**：
> 1. **语料规模恒定（105 个条目、108 个树/树群项、138 个子块、262 张图片，不再扩容）**：既然知识库边界完全固定，**把算力、实体对齐、看点提炼与双语翻译 100% 前置到离线预处理阶段**，是消除线上脆弱正则、实现零延迟引导体验与高质量跨语言问答的最优解。
> 2. **外语输入、内部主键统一、双语静态绑定**：中英双语绝不搞“两套后端过滤代码”或“线上临时机翻引文”。线上通过双语权威词表将英文/拼音查询归一化为内部统一主键（复用全部既有 SQL 统计、区县过滤、附带提及与配图安全逻辑），而在生成与证据溯源阶段按语言静态绑定离线精译的英/中双语字段。
> **项目根目录**：`/Users/young/项目repo/inf  Wu museum`

---

## 1. 为什么在 V3 基础上推进 V4？（当前体验瓶颈与双语化挑战）

在 V3 完成“三段式架构瘦身”后，系统响应速度与拒答率已显著改善，但面向**深度文化导览体验**与**未来中英双语化（Bilingual Museum Guide）**目标，仍存在以下四大结构性短板：

1. **离线预处理过薄，导致线上仍残留近百行地名切分正则与特判**：
   - 离线构建脚本（`analysis/scripts/build_story_chunks.py`）仅搬运了原始地址字符串，迫使线上通过 `chatbot/backend/app/places.py` 中的 `AREA_PREFIX`、`LOCAL_PREFIX`（含 `r'^(.{2,}?(?:村|山(?!庄)|路|巷|街|弄))(.+)$'` 等补丁正则）、`GENERIC_PLACES` 黑名单及 `retrieval.py` 中的 `mentions_subject` 字符串替换，在运行时反解乡镇/村落/景点与附带提及树种。
2. **原文为散文叙事，缺乏“主题标签”与“一句话看点”，泛导览召回与破冰引导不足**：
   - 当用户提出体验式问题（如 *“有哪些造型奇特或死而复生的古树？”*、*“有哪些和帝王将相有关的树？”*、*“适合带孩子看哪些树？”*）时，纯原文表面字词未必包含“奇特”、“帝王”等抽象词；且回答结束后缺乏根据当前卡片内容定制的“下一步推荐追问（Follow-up Chips）”，观众容易冷场。
3. **古树名木与吴中文化专名存在严重的“跨语言翻译陷阱”**：
   - **植物学名与俗名假朋友**：如名木 **“孩儿莲”** 字面含 `Lotus`（莲），实为红山茶（`Camellia japonica`）；**“罗汉松”** 英文俗名为 `Buddhist Pine`（非松属 `Podocarpus`）；而 **“圆柏/龙柏”**（`Chinese Juniper`）、**“侧柏”**（`Oriental Arborvitae / Cypress`）、**“雪松”**（`Deodar Cedar`）在英语游客口中常混用 `cypress / juniper / cedar`。若仅靠线上机器直译 Query，必然出现大面积脱靶或张冠李戴。
   - **苏州地名与雅号的多译法分裂**：如“虎丘山”在英语世界通用意译 `"Tiger Hill"`，而“虎丘区（高新区）”则是 `"Huqiu District / Suzhou New District (SND)"`；司徒庙古柏雅号“清奇古怪”兼具拼音 `"Qing Qi Gu Guai"` 与意译 `"Clear, Peculiar, Ancient, and Quaint"`。
   - **中国历史纪年与官职认知断层**：英文读者面对直译的 `"Deng Yu"`、`"Qianlong"`、`"Dali era"` 缺乏时间坐标，需在译文与英文导览中内嵌公元纪年与身份同位语（如 `"Eastern Han Dynasty (25–220 AD)"`）。
4. **原文引文（Quotes）与图注的双语可信绑定难题**：
   - 系统红线要求前端展示的 `sources` 引文、页码与图注必须 100% 由服务端从本地可信知识库绑定，严禁线上模型自造引文。若底层只有中文原文，英文回答下挂载文言/方言中文引文会导致英文读者无法核对；若让模型在线临时翻译引文，则既慢又破坏“静态防篡改绑定”契约。

---

## 2. V4 总体架构设计（Offline Enrichment + Bilingual Dual-Track Pipeline）

通过引入 **非破坏性离线富化层（Sidecar Enrichment & Glossary）** 与 **跨语言归一化三阶段流水线**，在不破坏既有受保护基线（`analysis/pilot/*` 与 `story.db` 现有 138 条中文向量）的前提下，实现体验跃升与中英双语支持：

```text
================================================================================
                        【离线预处理与双语资产层 (Offline Layer)】
         （针对恒定 105 条目 / 138 子块一次性生成、人工审校、静态落盘至 data/knowledge/）
================================================================================
  1. 权威双语词表 (glossary.json)
     ├─ 30+ 树种三元组：中文标准名 ↔ 英文主名称 + 英文俗名/混用词列表 + 拉丁学名
     ├─ 10 大区县 & 核心古镇/寺庙地标：中文名 ↔ 英文标准名 + 拼音 + 缩写 (SND/SIP/Tiger Hill)
     └─ 古树雅号 & 历史人物/朝代注释规范：如 清奇古怪、孩儿莲、邓禹、乾隆、憨山大师 (带公元纪年)

  2. 深度富化与双语镜像包 (enrichment.json —— 按 unit_id / chunk_id 严格对齐)
     ├─ 结构化地理与实体字段 (替代线上正则)：
     │    • items_meta[(unit_id, item_no)]: {district, township, township_stem, site, aliases, location_en, species_en}
     │    • chunks_meta[chunk_id]: {incidental_species, nicknames, themes, hook_zh, hook_en, synthetic_queries}
     ├─ 卡片级零延迟引导追问：
     │    • cards_meta[unit_id]: {title_en, location_en, followups_zh: [2~3条], followups_en: [2~3条]}
     └─ 138 子块 & 262 图片英文精译镜像 (静态可信溯源)：
          • chunks_meta[chunk_id]: {context_header_en, text_en}
          • images_meta[image_id]: {caption_en, display_caption_en}

================================================================================
                        【在线中英双语三阶段流水线 (Online Layer)】
================================================================================
用户提问 (中文 / 英文 / 拼音 / 中英混输) + UI 语言偏好 (ui_lang)
   │
   ├── [零模型快路] 中英问候语 (你好 / hello / hi / good morning) ──► 按语言返回双语欢迎语与示例问题
   │
   ▼
[Stage 1] 跨语言统一理解与归一化 (UNDERSTAND) —— 1 次轻量 JSON 调用
   │  • 输出新增 response_lang: "zh" | "en"（默认跟随用户提问语言，无法判断时取 ui_lang）
   │  • 【核心设计】“外语输入、内部主键统一”：
   │      无论用户问 "Buddhist pine in Mudu" 还是 "木渎罗汉松"，结合注入的 Glossary，
   │      plan.subject 统一输出中文标准主键（"罗汉松"），plan.districts 统一输出标准区县（["吴中区"]），
   │      plan.filters 统一输出标准主键！
   │  • search_query：输出融合中文标准锚点 + 主题看点词的增强检索句（消除跨语言向量鸿沟）
   │
   ├── 若 mode == "clarify"   ──► 按 response_lang 返回温和澄清
   ├── 若 mode == "inventory" ──► 直接复用 Knowledge.structured(filters)，按 response_lang 渲染中/英清册！
   │
   ▼
[Stage 2] 富化语义检索 + Rerank + 父卡展开 (RETRIEVE & CURATE) —— 0 次生成模型调用
   │  1. 双语术语查询扩展：自动通过 Glossary 将问句中的英文树名/学名/地名/拼音补齐标准中文锚点
   │  2. 向量召回 (138条) + Qwen Rerank（送审文本融合离线预处理的 themes + hook 看点摘要）
   │  3. 确定性过滤（零正则）：直接查表 items_meta 与 chunks_meta.incidental_species 完成区县过滤与附带提及补召
   │  4. 展开 Top-K 父卡（explore=6, qa=8），根据 response_lang 组装上下文与配图候选
   │
   ▼
[Stage 3] 双语策展级生成 + 极简护栏 + 追问推荐 (ANSWER & VERIFY)
      1. ANSWER：按 response_lang 切换【中文树语讲解员】或【英文博物馆策展解说员 (Museum Docent)】指令
         （英文自动附带朝代公元纪年、苏州地理同位语注释与拉丁学名点缀）
      2. 静态溯源绑定：bind_sources() 与 public_card() 根据 response_lang 直接绑定本地审校好的
         text / text_en 与 caption / caption_en，100% 杜绝线上机翻引文幻觉！
      3. Minimal Verifier：跨语言核查三大事实红线，优雅降级交付
      4. 附带卡片级 suggested_questions（从被引用卡片的 followups_zh / followups_en 中提取 2~3 条返回前端）
```

---

## 3. 全局红线与不变契约（Must-Keep Invariants）

1. **受保护原件与既有向量 100% 兼容**：
   - `analysis/pilot/stories.jsonl`、`entries_story.jsonl`、`chunks_story.jsonl`、`story_baseline.json` 保持只读，`analysis/scripts/check_story_pipeline.py` 必须继续 100% 通过。
   - `chatbot/data/knowledge/story.db` 内的 `chunks.context_header`、`chunks.text`、`chunks.content_hash` 及 `chunk_vectors` 中已构建的 138 条线上向量**保持原样不破坏**。所有新增的离线预处理属性与中英双语镜像统一存放在结构化旁路资产 `chatbot/data/knowledge/glossary.json` 与 `chatbot/data/knowledge/enrichment.json` 中，由 `Knowledge.__init__` 在启动时校验加载并合并到内存视图。
2. **检索与统计底线不变**：
   - 普通问答与导览保持“向量召回 + Qwen Rerank + Top-K 父卡直出”，完整名单/计数保持走 `Knowledge.structured()` 全量确定性查询。
3. **引文与图片 100% 服务端静态绑定**：
   - 无论中文还是英文模式，返回给前端的 `sources[].quote`、`sources[].text`、`cards[].images[].caption` 必须全部取自本地知识库（`story.db` + `enrichment.json`），绝不允许把模型实时生成的译文当作原文证据展示。

---

## 4. 分模块施工规范（Task Breakdown：V4-01 ~ V4-05）

### V4-01 [离线词表与富化引擎] 构建《古树名木中英双语权威词表》与离线深度预处理包

- **涉及文件**：
  - 新建：`/Users/young/项目repo/inf  Wu museum/chatbot/data/knowledge/glossary.json`
  - 新建：`/Users/young/项目repo/inf  Wu museum/chatbot/data/knowledge/enrichment.json`
  - 新建：`/Users/young/项目repo/inf  Wu museum/chatbot/scripts/build_enrichment.py`（支持确定性基础构建 + 可选调用 LLM 刷新看点与双语译文）
- **具体规范**：
  1. **`glossary.json` 数据契约（覆盖全库全部树种、区县、高频古镇地标与文化专名）**：
     ```json
     {
       "version": 1,
       "districts": {
         "吴中区": {"en": "Wuzhong District", "short_en": "Wuzhong", "aliases": ["吴中", "wuzhong"]},
         "姑苏区": {"en": "Gusu District", "short_en": "Gusu", "aliases": ["姑苏", "gusu"]},
         "吴江区": {"en": "Wujiang District", "short_en": "Wujiang", "aliases": ["吴江", "wujiang"]},
         "虎丘区（高新区）": {
           "en": "Huqiu District (Suzhou New District)",
           "short_en": "Suzhou New District (SND)",
           "aliases": ["虎丘区", "高新区", "苏州高新区", "huqiu district", "suzhou new district", "snd"]
         },
         "相城区": {"en": "Xiangcheng District", "short_en": "Xiangcheng", "aliases": ["相城", "xiangcheng"]},
         "工业园区": {
           "en": "Suzhou Industrial Park (SIP)",
           "short_en": "SIP",
           "aliases": ["苏州工业园区", "园区", "suzhou industrial park", "sip"]
         },
         "昆山市": {"en": "Kunshan City", "short_en": "Kunshan", "aliases": ["昆山", "kunshan"]},
         "常熟市": {"en": "Changshu City", "short_en": "Changshu", "aliases": ["常熟", "changshu"]},
         "太仓市": {"en": "Taicang City", "short_en": "Taicang", "aliases": ["太仓", "taicang"]},
         "张家港市": {"en": "Zhangjiagang City", "short_en": "Zhangjiagang", "aliases": ["张家港", "zhangjiagang"]}
       },
       "species": {
         "银杏": {"en": "Ginkgo", "latin": "Ginkgo biloba", "aliases_en": ["ginkgo", "maidenhair tree", "gingko"]},
         "香樟": {"en": "Camphor Tree", "latin": "Cinnamomum camphora", "aliases_en": ["camphor", "camphorurel"]},
         "圆柏": {"en": "Chinese Juniper", "latin": "Juniperus chinensis", "aliases_en": ["juniper", "chinese juniper", "cypress", "ancient cypress"]},
         "龙柏": {"en": "Dragon Juniper", "latin": "Juniperus chinensis 'Kaizuka'", "aliases_en": ["dragon juniper", "kaizuka juniper", "twisted cypress"]},
         "侧柏": {"en": "Chinese Arborvitae", "latin": "Platycladus orientalis", "aliases_en": ["oriental arborvitae", "platycladus", "biota"]},
         "罗汉松": {"en": "Buddhist Pine", "latin": "Podocarpus macrophyllus", "aliases_en": ["buddhist pine", "podocarpus", "yew plum pine"]},
         "雪松": {"en": "Deodar Cedar", "latin": "Cedrus deodara", "aliases_en": ["deodar cedar", "himalayan cedar", "cedar"]},
         "广玉兰": {"en": "Southern Magnolia", "latin": "Magnolia grandiflora", "aliases_en": ["southern magnolia", "bull bay", "magnolia"]},
         "白玉兰": {"en": "Yulan Magnolia", "latin": "Magnolia denudata", "aliases_en": ["yulan magnolia", "jade orchid", "lily tree"]},
         "红山茶": {"en": "Red Camellia", "latin": "Camellia japonica", "aliases_en": ["camellia", "red camellia", "japanese camellia"]},
         "石楠": {"en": "Chinese Photinia", "latin": "Photinia serratifolia", "aliases_en": ["photinia", "chinese photinia", "red-tip photinia"]},
         "榉树": {"en": "Chinese Zelkova", "latin": "Zelkova schneideriana", "aliases_en": ["zelkova", "chinese zelkova", "schneider zelkova"]},
         "朴树": {"en": "Chinese Hackberry", "latin": "Celtis sinensis", "aliases_en": ["hackberry", "chinese hackberry"]},
         "黄杨": {"en": "Chinese Boxwood", "latin": "Buxus sinica", "aliases_en": ["boxwood", "chinese boxwood"]},
         "雀舌黄杨": {"en": "Bodhi Boxwood", "latin": "Buxus bodinieri", "aliases_en": ["bodinier boxwood", "small-leaf boxwood"]},
         "瓜子黄杨": {"en": "Small-leaved Boxwood", "latin": "Buxus sinica var. parvifolia", "aliases_en": ["melon-seed boxwood"]},
         "枫杨": {"en": "Chinese Wingnut", "latin": "Pterocarya stenoptera", "aliases_en": ["wingnut", "chinese wingnut"]},
         "杨树": {"en": "Poplar", "latin": "Populus", "aliases_en": ["poplar", "cottonwood"]},
         "桂花": {"en": "Sweet Osmanthus", "latin": "Osmanthus fragrans", "aliases_en": ["osmanthus", "sweet osmanthus", "fragrant olive"]}
       },
       "special_terms": {
         "清奇古怪": {
           "target_species": "圆柏",
           "entry_no": 18,
           "en": "The Four 'Qing, Qi, Gu, Guai' (Clear, Peculiar, Ancient, and Quaint) Junipers",
           "aliases": ["qing qi gu guai", "qingqiguguai", "clear peculiar ancient quaint", "four han cypresses"]
         },
         "孩儿莲": {
           "target_species": "红山茶",
           "entry_no": 37,
           "en": "'Hai'er Lian' (Baby-Lotus) Camellia",
           "aliases": ["haierlian", "hai'er lian", "baby lotus camellia", "baby-face lotus camellia"]
         },
         "虎丘山": {
           "target_location": "虎丘山",
           "en": "Tiger Hill (Huqiu Mountain)",
           "aliases": ["tiger hill", "huqiu mountain", "huqiu pagoda"]
         }
       }
     }
     ```
     *注：`glossary.json` 必须覆盖 `knowledge.species` 中的全部树种（全库共 30 余种），不能有任何遗漏。*

  2. **`enrichment.json` 数据契约（覆盖全部 105 条目、108 树项、138 子块）**：
     - **`entries`（按 `str(entry_no)` 索引，共 105 项）**：
       - `title_en`: 条目标题英文（如 `"Camphor Tree"`、`"Southern Magnolia & Buddhist Pine"`）
       - `location_en`: 条目标准地点英文（如 `"Dongwan Well Yard, Aiguo Village, Jinting Town"`）
       - `themes`: 主题标签列表（如 `["名木奇观", "东汉传说", "雷击重生"]`）
       - `hook_zh`: 一句话中文导览看点（≤ 60 字，提炼该树最独特的外貌、树龄、人物故事或历史转折）
       - `hook_en`: 一句话英文导览看点（≤ 35 词）
       - `followups_zh`: 2～3 个紧扣本条目正文细节的中文推荐追问（如 `["“清奇古怪”这四个名字是怎么来的？", "这四棵古柏真的有两千年历史吗？"]`）
       - `followups_en`: 2～3 个对应的英文推荐追问（如 `["How did the four trees get the names 'Qing, Qi, Gu, Guai'?", "Were they really planted by General Deng Yu 2,000 years ago?"]`）
       - `review_flags_en`: 若条目含 `location_conflict` 等标记，提供对应的英文客观说明
     - **`items`（按 `f"{unit_id}:{item_no}"` 索引，共 108 项）**：
       - `hierarchy`: `{"district": "吴中区", "township": "金庭镇", "township_stem": "金庭", "village": "爱国村", "site": "三官殿", "place_terms": ["金庭镇三官殿", "金庭镇", "金庭", "三官殿"], "is_area": false}` —— **离线一次性算好并固化，彻底替代线上运行时正则拆解**！
       - `location_en`: 树项地点英文（如 `"Sanguan Hall, Jinting Town"`）
       - `species_en`: 树项树种英文列表（如 `["Dragon Juniper"]`）
     - **`chunks`（按 `chunk_id` 索引，共 138 项）**：
       - `incidental_species`: 正文中顺带提及但非本条目主树种的树种列表（如 `shbl:story-066:body-1` 明确标注 `["杨树"]`）
       - `nicknames`: 单树雅号列表（如 `["清奇古怪"]`、`["孩儿莲"]`）
       - `synthetic_queries_zh`: 2～3 个针对该子块核心事实的口语检索短语
       - `synthetic_queries_en`: 2～3 个英文口语检索短语
       - `context_header_en`: 英文上下文头（如 `"Book of Ancient Trees - Story #2 | Wuzhong District, Sanguan Hall in Jinting Town | Dragon Juniper"`）
       - `text_en`: 子块英文精译文本（遵循 `glossary.json` 术语表，带朝代公元纪年括号注）
     - **`images`（按 `image_id` 索引）**：
       - `caption_en`: 原图注英译（若有）
       - `display_caption_en`: 标准展示图注英文（如 `"Sanguan Hall, Jinting Town · Dragon Juniper · Book Photo"`）

---

### V4-02 [清理运行时正则] 升级 `Knowledge` 与 `places.py`，用离线结构化字典替代启发式切词

- **涉及文件**：
  - `/Users/young/项目repo/inf  Wu museum/chatbot/backend/app/knowledge.py`
  - `/Users/young/项目repo/inf  Wu museum/chatbot/backend/app/places.py`
  - `/Users/young/项目repo/inf  Wu museum/chatbot/backend/app/retrieval.py`
- **具体改造**：
  1. **`Knowledge` 启动加载富化层与双语词表**：
     - 在 `Knowledge.__init__` 中加载 `directory / 'glossary.json'` 与 `directory / 'enrichment.json'`（若在旧测试临时目录中不存在，则调用 `places.py` 的兜底构建函数自动生成内存结构，保证所有旧单测 fixtures 无缝兼容）。
     - 直接从 `enrichment['items']` 加载 `self.item_place_terms`、`self.place_terms`、`self.area_place_terms`；从 `enrichment['chunks']` 加载 `self.chunk_incidental_species` 与 `self.chunk_enrichment`。
  2. **跨语言查询术语归一化与锚点扩展（`Knowledge.resolve_bilingual_terms(text)`）**：
     - 遍历 `glossary.json` 中的 `districts`、`species`、`special_terms` 及 `enrichment.json` 中的英文地名/拼音别名：
       - 当用户输入英文或拼音（如 `"Buddhist pine"`、`"Podocarpus"`、`"Camellia"`、`"Tiger Hill"`、`"Situ Temple"`、`"Qingqiguguai"`、`"SND"`、`"Mudu"`）时，确定性识别出对应的中文标准实体：
         - `matched_districts`: 如 `["虎丘区（高新区）"]`
         - `matched_species`: 如 `["罗汉松"]`
         - `matched_anchors`: 如 `["光福镇司徒庙", "圆柏", "清奇古怪"]`
     - 此方法供 Stage 1 `UNDERSTAND` 作为 `glossary_hints` 输入，并在 Stage 2 检索前自动将中文锚点补充至 `search_query`，确保即使只用现有的 138 条中文 `qwen3.7-text-embedding` 向量，英文提问也能实现 **100% 跨语言精确召回**！
  3. **升级 `VectorRetrieval.search` 与 `reorder`（`retrieval.py`）**：
     - **Rerank 文档富化**：在 `reorder()` 构造送入 `qwen3.7-text-rerank` 的 `documents` 时，若该子块存在离线富化的 `themes`、`hook_zh`、`hook_en`，将其拼入文档头部（注意：仅用于 Rerank 相关性打分，**绝不修改 `VectorIndex` 的 `input_hash` 校验和返回给用户的原文 `text`**！）。
     - **附带提及确定性补召**：在 `retrieval.py` 第 161 行检查附带提及树种时，优先检查 `subject in self.knowledge.chunk_incidental_species.get(chunk_id, ())`（同时兼容原有的 `mentions_subject` 兜底），消除靠字符串替换猜树种的脆弱性。
  4. **支持按语言导出卡片与引文（`public_card(card, lang='zh')` & `source(chunk, sid, card, lang='zh')`）**：
     - 当 `lang == 'en'` 时：
       - `public_card` 返回英文 `title`（保留中文副标，如 `"Dragon Juniper (龙柏)"`）、英文 `location`、英文 `items[].location` 与 `species`、英文 `images[].caption`，以及英文推荐追问 `followups: card_enrichment['followups_en']`；
       - `source` 返回英文 `title`、`location`、`type_label`（`Story / Field Note / Interview / Background`）、`citation`（`"Book of Ancient Trees, p. 76"`），并将 `text` 设为离线审校好的 `text_en`（同时附带 `text_zh` 供双语对照）。
     - 当 `lang == 'zh'` 时：保持现有全部中文字段格式 100% 不变，并在 `public_card` 中新增 `followups: card_enrichment['followups_zh']` 与 `hook: card_enrichment['hook_zh']`。

---

### V4-03 [双语理解与策展生成] 升级 `service.py` 支持中英双语全链路问答与推荐追问交付

- **涉及文件**：
  - `/Users/young/项目repo/inf  Wu museum/chatbot/backend/app/service.py`
  - `/Users/young/项目repo/inf  Wu museum/chatbot/backend/app/context.py`
  - `/Users/young/项目repo/inf  Wu museum/chatbot/backend/app/media.py`
- **具体改造**：
  1. **扩展 `Plan` 数据契约**：
     ```python
     class Plan(BaseModel):
         lang: Literal['zh', 'en'] = 'zh'
         mode: Literal['qa', 'explore', 'inventory', 'clarify'] = 'qa'
         search_query: str = Field(default='', max_length=500)
         subject: str = Field(default='', max_length=60)
         districts: list[str] = Field(default_factory=list)
         keep_focus: bool = False
         filters: InventoryFilters = Field(default_factory=InventoryFilters)
         clarification: str = Field(default='', max_length=250)
     ```
  2. **双语零调用问候快路**：
     - 若匹配中文问候（`你好/您好/嗨`）且未显式指定 `ui_lang='en'`，返回中文欢迎语；
     - 若匹配纯英文问候（`hello / hi / hey / good morning / good afternoon`）或 `ui_lang == 'en'` 下发送问候，直接返回英文欢迎语：
       `"Hello! I am Shuyu, your guide to the ancient trees of Suzhou's Wuzhong region. Ask me about a tree's story, search the records, or explore the catalog."`
  3. **升级 Stage 1 `UNDERSTAND` 提示词与输入**：
     - 在 `understand_input` 中传入 `ui_lang`（前端当前界面语言，默认 `'zh'`）与 `bilingual_hints`（由 `knowledge.resolve_bilingual_terms(question)` 提取出的中英术语对齐提示）。
     - **`UNDERSTAND` 双语准则**：
       - **语言判定（`lang`）**：若 `question` 主要为英文，输出 `lang="en"`；若主要为中文，输出 `lang="zh"`；若仅含编号（如 `"吴中203"`）则跟随 `ui_lang`。
       - **内部主键统一（极重要）**：无论 `lang` 是 `"zh"` 还是 `"en"`，`search_query` 必须包含**中文检索句**（因为底层向量库为中文原文向量，可附带英文关键词），`subject` 必须输出**中文标准树种名或雅号**（如用户问 `"Camellia"` 或 `"Hai'er Lian"`，输出 `subject="孩儿莲"` 或 `"红山茶"`；问 `"Buddhist pine"`，输出 `subject="罗汉松"`），`districts` 与 `filters` 中的 `district / species / location` 全部输出**中文标准值**！
       - `clarification` 按 `lang` 输出对应语言的温和追问。
  4. **升级 `mode == 'inventory'` 双语输出**：
     - 内部调用完全相同的 `self.knowledge.structured(filters, lang=plan.lang)`。
     - 当 `plan.lang == 'en'` 时，生成英文统计导语：
       `f"Based on the complete records, there are {count} tree/grove entries across {no_count} chapters matching your criteria (counted by cataloged items rather than individual trunks)."`
  5. **升级 Stage 3 `ANSWER` 与 `VERIFY` 双语策展提示词**：
     - 当 `plan.lang == 'en'` 时，向 `ANSWER` 注入英文博物馆策展人规范（Museum Docent Persona）：
       - 使用自然、典雅、富有画面感的英文撰写 `claims[].heading` 与 `claims[].text`；
       - **文化专名同位语解释（Cultural Glossing）**：首次提及中国历史朝代、帝王将相或苏州水乡古镇时，自然融入简短背景（如 *"during the reign of Emperor Qianlong in the Qing Dynasty (18th century)"*、*"in Mudu, a historic water town in Suzhou"*）；
       - **植物学名点缀**：首次介绍核心古树时，可自然点缀标准英文名与拉丁学名或中文雅号解释（如 *"the four ancient Chinese junipers, known as 'Qing, Qi, Gu, Guai' (Clear, Peculiar, Ancient, and Quaint)"*）；
       - 严格保留传说限定词（`"According to local legend..."`, `"As recalled by interviewees..."`, `"Historical gazetteers record that..."`）。
     - `VERIFY` 同样支持核查英文 `claims` 与双语 `sources`，遵循相同的三条底线红线。
  6. **组装 `suggested_questions`（卡片级推荐追问）**：
     - 在 `done(result)` 返回前，从本次最终引用的 `used_cards`（若为空则从候选 `cards[:2]`）中提取离线预置的 `followups_en`（当 `lang=='en'`）或 `followups_zh`（当 `lang=='zh'`），去重并排除与当前 `question` 高度重合的问题，取前 **3** 条放入返回结果的 `suggested_questions: list[str]` 字段中。

---

### V4-04 [前端双语体验与追问胶囊] 升级 React 前端支持 `中 / EN` 切换与一键追问

- **涉及文件**：
  - `/Users/young/项目repo/inf  Wu museum/chatbot/backend/app/main.py`
  - `/Users/young/项目repo/inf  Wu museum/chatbot/frontend/src/types.ts`
  - `/Users/young/项目repo/inf  Wu museum/chatbot/frontend/src/api.ts`
  - `/Users/young/项目repo/inf  Wu museum/chatbot/frontend/src/App.tsx`
- **具体改造**：
  1. **API 协议轻量扩展（100% 向后兼容）**：
     - `POST /api/conversations/{cid}/messages` 请求体支持可选字段 `lang: 'zh' | 'en' = 'zh'`（透传给 `service.answer(..., ui_lang=lang)`）；
     - `GET /api/catalog` 支持可选查询参数 `?lang=zh|en`，返回对应语言视图的古树目录卡片与树种筛选项；
     - `Answer` 响应 JSON 新增可选字段 `lang?: 'zh' | 'en'` 与 `suggested_questions?: string[]`；`Card` 新增可选字段 `hook?: string` 与 `followups?: string[]`。
  2. **前端双语状态与 UI 字典（`App.tsx`）**：
     - 新增状态 `const [lang, setLang] = useState<"zh" | "en">(() => (localStorage.getItem("tree-chat-lang") === "en" ? "en" : "zh"))`；
     - 在顶部导航栏右侧提供清爽的 `中 / EN` 语言切换胶囊按钮；切换时同步更新 `localStorage` 并刷新 `/api/catalog?lang=...`；
     - 提供精炼的 UI 文案字典（覆盖：新建对话、古树目录、原文依据、预设问题 `suggestions`、输入框 placeholder、停止生成、反馈弹窗、进度阶段文案）。
  3. **渲染推荐追问气泡（Suggested Follow-up Chips）**：
     - 在最后一条已完成（`status === 'complete'`）的助手回答下方，若 `result.suggested_questions?.length` 非空，渲染一排轻盈的引导追问胶囊按钮（带小箭头图标）；
     - 用户点击任意追问胶囊时，若当前非 `busy`，直接调用 `send(questionText)` 发起追问！
     - 在右侧“古树卡片”详情或目录卡片中，若卡片带有离线提炼的 `hook`（一句话看点），在卡片副标题处优雅展示。

---

### V4-05 [验收与评测集扩展] 新增离线富化完整性校验与中英双语端到端回归测试

- **涉及文件**：
  - `/Users/young/项目repo/inf  Wu museum/chatbot/backend/tests/test_knowledge.py`
  - `/Users/young/项目repo/inf  Wu museum/chatbot/backend/tests/test_service.py`
  - `/Users/young/项目repo/inf  Wu museum/chatbot/backend/tests/test_improvements.py`
- **新增测试覆盖要求**：
  1. **离线富化与词表完整性测试（`test_knowledge.py`）**：
     - 断言 `glossary.json` 100% 覆盖 `knowledge.districts`（10 个区县）与 `knowledge.species`（全部树种），且每个树种均含非空 `en` 与 `latin`；
     - 断言 `enrichment.json` 100% 覆盖 105 个条目、108 个树项、138 个子块；每个条目均含非空的 `hook_zh`、`hook_en`、`followups_zh`、`followups_en`，每个子块均含非空的 `text_en` 与 `context_header_en`；
     - 断言 `story.db` 哈希与 138 条线上向量 `VectorIndex.status()['ready'] == True` 保持不变。
  2. **跨语言术语解析与检索测试（`test_improvements.py`）**：
     - 验证 `resolve_bilingual_terms("Where is the Buddhist pine in Mudu?")` 能准确识别 `species=['罗汉松']`、地点锚点 `['木渎', '木渎古镇']`；
     - 验证 `resolve_bilingual_terms("Tell me about the four cypresses Qingqiguguai at Situ Temple")` 能准确映射到第 18 条 `圆柏`、`清奇古怪`、`光福镇司徒庙`；
     - 验证 `resolve_bilingual_terms("Are there any ancient trees on Tiger Hill?")` 识别为景点 `虎丘山` 而非誤判为行政区 `虎丘区（高新区）`；
     - 验证 `resolve_bilingual_terms("Tell me about the Hai'er Lian camellia")` 能准确映射到 `孩儿莲` / `红山茶`。
  3. **双语问答、结构化清册与推荐追问全链路测试（`test_service.py`）**：
     - 验证英文提问 `"How many ginkgo trees are recorded in Wuzhong District?"` 经 `UNDERSTAND` 归一化为 `mode='inventory', lang='en', districts=['吴中区'], filters={'species': '银杏'}`，返回英文清册导语与英文卡片；
     - 验证英文问答 `"How old is the Dragon Juniper at Sanguan Hall?"` 返回 `lang='en'`，其 `sources[0].quote` 静态绑定自 `enrichment.json` 的 `text_en`（绝非模型临时自造），且响应包含非空的英文 `suggested_questions`；
     - 验证中文问答同样返回与当前卡片绑定的中文 `suggested_questions`。

---

## 5. 施工顺序与最终验收清单（Final Verification Checklist）

建议下游实现 Agent 严格按 **`V4-01` → `V4-02` → `V4-03` → `V4-04` → `V4-05`** 顺序推进，完成后执行以下全部检查：

1. **离线原始管线无损核验**：
   ```bash
   cd "/Users/young/项目repo/inf  Wu museum/analysis"
   python3 -I scripts/check_story_pipeline.py
   ```
   - 确认输出 `"passed": true`，受保护基线零漂移。
2. **富化包构建与校验脚本核验**：
   ```bash
   cd "/Users/young/项目repo/inf  Wu museum/chatbot"
   .venv/bin/python scripts/build_enrichment.py --check
   ```
   - 确认 105 条目、108 树项、138 子块富化率与双语覆盖率均为 100%，且 `story.db` 138 条向量 `ready=True`。
3. **后端全量单元测试通过**：
   ```bash
   cd "/Users/young/项目repo/inf  Wu museum/chatbot"
   .venv/bin/python -m pytest -q
   ```
4. **前端格式检查与生产构建通过**：
   ```bash
   cd "/Users/young/项目repo/inf  Wu museum/chatbot/frontend"
   npm run check:format
   npm run build
   ```
5. **发布包重打包与洁净度检查**：
   ```bash
   cd "/Users/young/项目repo/inf  Wu museum/chatbot"
   .venv/bin/python scripts/package_release.py
   tar -tzf release/wu-tree-chatbot.tar.gz | (! grep -E '\.DS_Store|/\._|\.env$')
   ```
