# 《树碑立传》RAG 与“树语”问答系统改进规划书 Ver 5（先测量、再取舍：富化头对照评测与 V4 遗留问题修复）

> **角色说明**：本文档由 Planner 输出，供 worker Agent 按阶段实现。Planner 同时担任评测集出题人、译文审校人与验收人。
> **前置基准**：V4 已落地（2026-11-16，后端 179 项测试通过）。V4 的代码、脚本、词表、富化包与 `story.db` 已冻结快照在 `chatbot/state/improve-v5/baseline-v4/`，受保护文件哈希记录在 `chatbot/state/improve-v5/protected-sha256.before.json`。**V5 的一切“对比 V4”都以这份快照为准，不得修改或删除它。**
> **项目根目录**：`/Users/young/项目repo/inf  Wu museum`（目录名中有两个空格）

---

## 0. V5 要回答的问题

V4 一次加入了富化头、双语镜像、词表、主题标签、追问胶囊，但验收只有 8 个取自规划书自身示例的用例。V5 不再叠加功能，做三件事：

1. **测量**：给重排富化头加开关，在留出用例上做带/不带对照，用预先写好的规则决定去留。
2. **修复**：逐一解决 V4 复盘出的 6 个设计问题（见第 1 节）。
3. **留证**：评测集成为仓库内的数据文件，留出集与封存集分开，V4/V5 数字并排报告。

## 1. V4 复盘：问题与证据

| # | 问题 | 已核实的证据 | V5 任务 |
|---|---|---|---|
| 1 | 重排输入被模型生成文本主导，且没有前后对比 | 富化头平均 432 字，子块原文平均 403 字，138 块中 86 块头比正文长；候选上限 500 > 语料 138，每次全量重排；rerank 用量由约 4.6 万升至约 6.9 万 tokens/次；`state/vector-rerank-eval.json` 仍是 11-09 的结果 | V5-01 |
| 2 | “静态绑定”不等于“可信” | 105 条全部由 `deepseek-flash` 翻译并自审（约 3.96 万英文词），无人工签审；英文模式下 ANSWER 与 VERIFY 读的都是译文；复核提示词里硬编码了抽查到的事实性错译 | V5-02 |
| 3 | 词表一对一，泛称计数少算 | `camellia`→仅“山茶”（1/4 项）；`cypress`→仅“圆柏”（3/5 项）；`sip` 把 “Can I sip tea…” 识别为工业园区 | V5-03 |
| 4 | 追问胶囊重复、跑题、会循环 | 刚答完三官殿龙柏树龄又推荐同一问题（相似度 0.71 < 阈值 0.8）；问木渎罗汉松首推同条目广玉兰果实；问银杏总数首推澄湖黑陶罐；每条目仅约 3 条，不看历史 | V5-04 |
| 5 | 主题标签无受控词表 | 319 个不同标签共用 522 次，266 个只出现一次；最高频是“古树”(58)“古树名木”(39)；词表 5 个主题在富化数据中出现 0 次 | V5-05 |
| 6 | 正则与特判只是搬家 | 地名正则原样移入 `offline_enrichment.py`；提示词里写死 Buddhist pine / Hai'er Lian / Tiger Hill / Mudu / 司徒庙；代码里有字面集合 `{'清奇古怪','孩儿莲'}`；`nicknames`、`is_area`、`site` 生成后运行时不读；缺 sidecar 时无条件走兜底构建 | V5-06 |
| 7 | 验收不足以支撑结论 | 8 例中 4 例在提示词里有专门特判；检查项不看回答内容；V4 产物目录中没有 V3 那 27 例中文验收的重跑记录 | V5-07 |

## 2. 设计原则

1. **证据门控**：凡是“可能有用”的机制（富化头、主题池检索、caveats 数据）先上开关和评测，按本文预先写明的判定规则决定默认值。没有净收益就选更简单、更便宜的方案。
2. **模型只选 ID，服务端绑定文本**：沿用引文与配图的做法，追问、主题同样如此。模型输出里出现的任何展示文本都不直接给用户。
3. **个例进数据，不进提示词**：新发现的失败用词表、富化数据或通用规则修。向提示词添加针对具体条目的文字须经 Planner 批准。
4. **中文原文是唯一事实权威**：译文是给读者看的镜像，不是核查依据。
5. **在线调用数不增加**：常规问答仍是 understand → answer → verify 三次模型调用，加一次查询向量与一次 rerank。

## 3. 不变契约

1. `analysis/pilot/*`、`chatbot/data/knowledge/story.db`、`manifest.json` 与 138 条向量只读；`analysis/scripts/check_story_pipeline.py` 继续通过。
2. 完整名单/计数走 `Knowledge.structured()`；普通问答走向量召回 + rerank + 父卡展开。不引入 FTS/BM25。
3. 前端展示的引文、页码、图注、追问文本全部来自本地知识库；模型不能生成或改写。
4. 中文路径的既有行为不变，除非本文明确写出变更（V5-03 的泛称计数、V5-04 的追问）。
5. API 向后兼容：只新增可选字段。
6. **文件所有权**：以下文件由 Planner 维护，worker 只读不改：
   - `rag workspace/improve_v5.md`
   - `chatbot/data/knowledge/translation_overrides.json`
   - `chatbot/backend/tests/evalsets/holdout_v5.jsonl`、`sealed_v5.jsonl`
   - `chatbot/state/improve-v5/planner/`（worker **不得读取**此目录）

## 4. 评测集与留出协议

### 4.1 用例文件

评测用例是 JSONL，放在 `chatbot/backend/tests/evalsets/`：

| 文件 | 来源 | 用途 |
|---|---|---|
| `dev16.jsonl` | worker 从现有 `scripts/retrieval_eval.py` 的 `CASES` 迁移 | 开发集，可随意调 |
| `zh27.jsonl` | worker 从 `scripts/live_eval.py` 的 `CASES` 迁移（保留原检查逻辑） | 中文回归 |
| `v4_8.jsonl` | worker 从 `scripts/live_eval_v4.py` 迁移 | V4 示例回归 |
| `holdout_v5.jsonl` | Planner 依据中文原文出题，Phase A 交付 | 留出集：做去留判定与问题诊断 |
| `sealed_v5.jsonl` | Planner 出题，Phase C 才交付 | 封存集：全部完工后只跑一次 |

每行一个用例，字段如下（除 `id`、`set`、`lang`、`question` 外均可省略）：

```json
{
  "id": "en-fact-007", "set": "holdout_en", "lang": "en", "ui_lang": "en",
  "question": "…",
  "mode": "qa",
  "gold_entries": [57],
  "gold_chunks": ["shbl:story-057:body-1"],
  "theme_gold": false,
  "answer_any": [["1,300", "1300"], ["Tang"]],
  "answer_none": ["Qianlong planted"],
  "qualifier": true,
  "expect": {"status": "answered", "route": "qa", "item_count": 5, "districts": [], "species": ["圆柏", "龙柏"]},
  "note": "出题依据与金标来源"
}
```

- `set` 取值：`dev_zh`、`holdout_zh`、`holdout_en`、`holdout_theme`、`holdout_inventory`、`holdout_guard`，封存集对应 `sealed_*`。
- `gold_entries`：事实类用例是唯一正确条目；`theme_gold: true` 时是“可接受条目集合”。
- `answer_any`：每个内层数组至少命中一个子串（大小写不敏感）；`answer_none`：都不得出现；`qualifier: true`：回答须含传说/存疑限定词（中英文标记表由 worker 在评测脚本内维护并写入报告）。
- `expect`：只检查给出的键。`item_count` 对应 `structured.item_count`；`species` 是 inventory 应使用的目录树种集合。

### 4.2 留出协议

1. `holdout_v5.jsonl` 的出题不看富化头内容（看点、合成问句），并避开提示词、既有测试与开发集中出现过的条目：1、2、11、14、16、18、21、22、23、26、28、29、34、38、66、71。
2. **首跑先于修复**：worker 拿到留出集后，先在 V4 快照上跑端到端基线、在加了开关的代码上跑三臂检索对照，结果落盘之后才允许看失败并动手修。首跑数字写入报告，不覆盖。
3. 首跑之后留出集即视为开发集。V5 完工时的留出集数字标注为“修复后”，与首跑并列。
4. `sealed_v5.jsonl` 在 Phase C 才交付，V4 快照与 V5 终版各跑一次。封存集暴露的失败只进报告的遗留问题清单，本轮不再修。

## 5. 分模块施工规范

### V5-01 富化头开关与对照评测（Phase A）

- **涉及文件**：`chatbot/backend/app/config.py`、`retrieval.py`、`service.py`（仅抽取函数）、`chatbot/scripts/retrieval_eval.py`、新建 `chatbot/backend/tests/evalsets/`
- **开关**：
  1. `Settings.rerank_enrichment: Literal['off','lean','full'] = 'full'`，环境变量 `RERANK_ENRICHMENT`。Phase A 期间默认值保持 `full`（即 V4 行为），判定后再改。
  2. `VectorRetrieval.reorder(query, dense, enrichment=None)` 与 `search(..., rerank_enrichment=None)` 接受显式覆盖，供评测脚本切臂；`audit['rerank']['enrichment']` 记录实际取值。
  3. 三臂定义：
     - `off`：送审文档 = `context_header + '\n' + text`，与 V3 逐字节一致。
     - `full`：与 V4 逐字节一致（themes + hook_zh + hook_en + synthetic_queries_zh + synthetic_queries_en）。
     - `lean`：只保留中文部分（themes + hook_zh + synthetic_queries_zh）。理由：进入 rerank 的查询总是 UNDERSTAND 改写后的中文句，英文头是纯开销。
  4. 向量输入哈希与返回给用户的原文不受开关影响（保留并扩展现有测试）。
- **`service.py` 纯重构**（不改行为，先于其他改动完成并保证 179 项测试原样通过）：把 `answer()` 中“UNDERSTAND + Plan 归一化”抽成 `async def plan_question(question, conversation, history, ui_lang)`，把“拼接编号对应与索引地点后的最终检索句”抽成一个函数。评测脚本与 `answer()` 调用同一份代码。
- **评测脚本 `scripts/retrieval_eval.py` 升级**：
  1. 参数：`--cases a.jsonl,b.jsonl`、`--arms off,lean,full`、`--query-mode planned|raw`、`--directory <knowledge dir>`、`--out <json>`。
  2. `planned`（主口径，贴近线上）：每个用例调用一次 `plan_question`，按“UNDERSTAND 提示词哈希 + 问题 + ui_lang”缓存到 `state/improve-v5/plans.json`；各臂复用同一份 Plan 的检索句、`districts`、`subject`、`mode`。`mode` 为 inventory/clarify 的用例不进检索对照，单独列出。
  3. `raw`：沿用旧脚本口径（原问句 + 编号映射），仅用于 `dev16`，与 11-09 的旧结果对账。
  4. Phase A 的对照一律使用 `--directory state/improve-v5/baseline-v4/data/knowledge`，保证被测的是 V4 的富化内容。
  5. 每臂每例记录：金标父条目在重排序列中的名次（按不同父条目去重计）、金标子块名次（若给出）、是否进入上下文（区县过滤 + Top-K 之后，K 取该 mode 的 `top_entries`）、向量原始名次、rerank tokens 与耗时。主题用例记录 `precision@K = |入选 ∩ 金标| / min(K, |金标|)`。
  6. 汇总：各集合的 hit@1、hit@3、in-context 率、MRR、主题 precision@K；两两配对的胜/负/平；“进入/掉出上下文”的用例清单；每臂 rerank tokens 合计。
  7. **噪声底**：`off` 臂在 `dev16` 上跑两遍，报告名次不一致的用例数。
  8. 附带分析（只报告，不改默认值）：金标子块的向量名次分布，给出“候选上限降到多少仍不丢金标”的建议。
- **预先登记的判定规则**（只用 `holdout_v5.jsonl` 的 planned 口径；`dev16` 不参与判定）：
  1. 定义某臂相对 `off` 的净收益 = （该臂进上下文而 `off` 没进的用例数）−（`off` 进而该臂没进的用例数），主题用例按“入选金标条目数 +1 及以上记一次收益、−1 及以上记一次损失”计入。
  2. 采用某个非 `off` 臂须同时满足：`holdout_en` + `holdout_theme` 合计净收益 ≥ 3；`holdout_zh` 没有任何用例掉出上下文；事实类用例的 hit@1 相对 `off` 最多少 1 例。
  3. `lean` 与 `full` 都达标时选 `lean`，除非 `full` 的净收益比 `lean` 多 2 例及以上。
  4. 都不达标或持平时选 `off`。
  5. 判定结果写入 `state/improve-v5/ab-report.md`，随后把默认值、`.env.example`、README 改成判定结果。Planner 有权复核改判。
- **交付**：`state/improve-v5/ab-dev16.json`、`ab-holdout.json`、`ab-report.md`。

### V5-02 译文可信链（Phase B）

- **涉及文件**：`chatbot/scripts/build_enrichment.py`、`backend/app/enrichment.py`、`knowledge.py`、`service.py`、`frontend/src/App.tsx`、`i18n.ts`、`types.ts`，新建 `scripts/translation_qa.py`、`scripts/export_translation_review.py`
- **(a) 审校修订清单**：Planner 逐块对照中文原文审校，修订写在 `data/knowledge/translation_overrides.json`：
  ```json
  {
    "version": 1,
    "database_sha256": "<story.db sha256>",
    "review": {"reviewer": "…", "method": "…", "date": "2026-11-17"},
    "reviewed_chunks": ["shbl:story-001:body-1", "…"],
    "edits": [
      {"scope": "chunks", "key": "shbl:story-050:body-1", "field": "text_en",
       "find": "原译文中的精确子串", "replace": "修订后的子串",
       "severity": "fact|omission|term|hedge|name|style", "reason": "对照原文的依据"}
    ]
  }
  ```
  - `scope` ∈ `chunks`、`entries`、`items`、`images`、`glossary`；`key` 与 `enrichment.json` 对应节的键一致（`glossary` 用 `节名/条目名`）。数组型字段用 `field: "followups_en[1]"` 的写法。
  - `build_enrichment.py` 在检查点合并之后、`validate_enrichment` 之前应用修订。`find` 在目标字段中必须恰好出现 1 次，否则构建失败（防止译文重生成后修订悄悄失效）；若目标字段已含 `replace` 且不含 `find`，视为已应用，保证可重复执行。
  - `--check` 逐条确认 `replace` 已在发布的 `enrichment.json` 中。
- **(b) 审校状态**：每个子块写入 `translation_status`，取值 `machine`（未审）、`ai_reviewed`（在 `reviewed_chunks` 中）、`human_approved`（见 (e)）。`Knowledge.source()` 英文模式返回该字段，前端来源面板按状态显示不同标注，不得把 AI 审校说成人工签审。`enrichment.json` 顶层 `translation_review` 改为结构化对象（各状态的块数、审校人、日期）。
- **(c) 离线确定性质检** `scripts/translation_qa.py`（只在离线运行，不进在线链路）：阿拉伯数字对齐、官方编号逐字保留、常见中文数词（“三百年”“五棵”等带量词的数词）对齐、限定词标记数量对比、词表术语一致性（原文出现词表中文词时，译文须出现其英文或拼音形式）、残留汉字白名单、段落数一致、长度比离群。编号与阿拉伯数字不一致判失败；其余输出复查清单到 `state/improve-v5/translation-qa.json`。
- **(d) 英文模式用中文原文核查**：`verification_sources()` 在英文模式为被引来源附带 `text_zh`。`VERIFY_EN` 改为：中文原文为准，英文是其译文；与中文原文矛盾的陈述即使译文支持也判 unsupported，并在 `translation_conflicts: [{"source_id","note"}]` 中报告。服务端把它记入 `validation.translation_conflicts`（只进 audit，不展示）。
  - **变异测试**（真实模型，3 例）：在临时知识库目录里把某块 `text_en` 的树龄、人物、传说限定各改错一处，确认对应回答段落被判 unsupported 且冲突被记录。
- **(e) 人工签审工具**：`scripts/export_translation_review.py` 导出逐块中英对照 CSV（chunk_id、条目、中文、英文、状态、修订理由）；`--import` 读回审校人填写的“通过/修改意见”，把通过的块标为 `human_approved` 并记录审校人与日期。本轮只交付工具，不代签。

### V5-03 词表一对多与别名收紧（Phase B）

- **涉及文件**：`data/knowledge/glossary.json`、`backend/app/bilingual.py`、`knowledge.py`、`service.py`、`media.py`、`retrieval.py`
- **树种组**：`glossary.json` 新增 `species_groups`。每组含中英文组名、`members`（目录树种）、可选 `related`（俗称相近但不属于该组的目录树种）、中英文泛称 `aliases`。示例：
  ```json
  "cypress_juniper": {"zh": "柏类", "en": "junipers and cypresses", "members": ["圆柏", "龙柏"],
                      "aliases": ["cypress", "cypresses", "juniper", "junipers", "柏树", "古柏"]},
  "pine": {"zh": "松类", "en": "pines", "members": ["白皮松", "马尾松"], "related": ["罗汉松", "雪松"],
           "aliases": ["pine", "pines", "松树", "古松"]}
  ```
  worker 依据 35 个目录树种列出全部分组（至少覆盖山茶类、玉兰类、柏类、松类），交 Planner 确认。
- **解析规则**：
  1. 泛称只能解析到组，不能落到单一成员。把泛称（`camellia`、`cypress`、`juniper`、`cedar` 以外的类名等）从单个树种的别名中移走；“最长匹配优先”保留（`yulan magnolia` → 玉兰，`magnolia` → 玉兰类）。
  2. `resolve()` 新增 `matched_species_groups`；`canonical('species', x)` 对泛称返回组。
  3. `InventoryFilters.species` 支持 `str | list[str]`；`structured()` 对列表做并集。清册导语用静态模板列出“计入了哪些目录树种、各多少项”；有 `related` 时加一句静态说明（如“罗汉松、雪松另计”）。中英文模板各一份。
  4. 所有“subject 是否匹配某树项树种”的判断收敛到一个函数（接受单一树种或组），替换 `media.py`、`service.py`、`retrieval.py` 里分散的 `subject in species or species in subject`。
  5. 中文泛称同样生效：“书里柏树一共多少项”现在走 clarify，V5 改为按组计数并列出明细。
- **别名收紧**：缩写类别名标 `"cased": true`（`SIP`、`SND`），只在原文为大写且有词边界时匹配；删除其小写形式。逐一检查 ≤4 字母的别名。
- **测试**：组计数 = 各成员 `structured()` 计数之和（不要写死数字）；`"Can I sip tea under an old tree?"` 不产生区县；`camellia`/`magnolia`/`cypress`/`pine` 的英文计数与中文“山茶/玉兰类/柏树/松树”计数一致。

### V5-04 追问胶囊重做（Phase B）

- **涉及文件**：`scripts/build_enrichment.py`、`backend/app/enrichment.py`、`knowledge.py`、`service.py`、`frontend/src/types.ts`（如需）
- **离线数据**：`enrichment.json` 的 `entries[no].followups` 改为对象数组，旧的 `followups_zh`/`followups_en` 由它派生以保持兼容：
  ```json
  {"id": "f023-4", "zh": "…", "en": "…", "item_no": 2, "chunk_id": "shbl:story-023:body-2",
   "evidence": "锚定子块中文原文里能回答该问题的精确子串", "kind": "fact|story|person|form|history|survey"}
  ```
  - 每条目 4–6 条；多树项条目每个树项至少 2 条并填 `item_no`。
  - `evidence` 必须是 `chunk_id` 对应中文原文的精确子串（构建时确定性校验，不通过即重试/失败）。这同时保证“问题在书里有答案”。
  - 现有追问可保留并补元数据，不足部分离线生成。长度限制沿用 V4。
- **桥接追问**（静态模板，零模型）：由结构化数据生成，引向其他条目，避免在同一条目里打转：同树种/同组的其他地点、同乡镇的其他古树、同主题（V5-05 的受控主题）的其他古树。模板中英文各一份，文本里只用词表与富化包里已有的名称。只有当对应集合里确实还有别的条目时才生成。
- **在线选择**（确定性为主）：
  1. 候选范围：只取本次**被引用**卡片的追问；有 `focus.item_no` 时优先该树项，`item_no` 不同的排后。
  2. 排除：本会话已展示或已被提问过的追问（按 ID；`answer()` 收到的 `history` 是完整消息列表，据此算出已见 ID，结果里新增 `suggested_question_ids` 与 `suggested_questions` 平行）。
  3. 排除“刚回答过的”：把候选以 `followup_candidates: [{"id","text"}]` 传给 ANSWER，`Draft` 新增可选 `followup_ids`（至多 3 个，语义是“本次回答没讲到、最自然的下一步”）。服务端只接受候选内的 ID；模型未给或给错时回退到确定性顺序，并用字符二元组重合度过滤与本次检索句高度重合的候选。
  4. 组合：至多 2 条条目追问 + 至多 1 条桥接追问，共 ≤3 条。
  5. 路由差异：inventory 回答给 1 条“在这个范围里挑几棵讲讲”的桥接 + 至多 2 条来自清册内树项、且树种与筛选一致的追问；clarify、insufficient、welcome 只给静态起步问题，不再从候选卡片取。
- **校验与测试**：
  1. 三个已知失败做成回归测试：答完三官殿龙柏树龄不再推荐树龄；问木渎罗汉松不推荐广玉兰果实；问吴中区银杏总数不推荐澄湖黑陶罐。
  2. 同一会话连续点击追问 6 次不出现重复。
  3. 全部中文追问做一次仅向量的可答性抽检（锚定条目进向量前 5），不达标的列清单修；另随机抽 30 条（中英各 15）跑端到端，要求 answered 且引用锚定条目。

### V5-05 受控主题词表（Phase B）

- **涉及文件**：`data/knowledge/glossary.json`、`scripts/build_enrichment.py`、`backend/app/enrichment.py`、`bilingual.py`、`retrieval.py`、`service.py`
- **词表**：`glossary.json` 的 `themes` 改为 ≤20 个受控主题，每个含 `zh`、`en`、一句定义、中英文别名。worker 以现有 319 个自由标签的聚类为基础起草，交 Planner 确认。候选方向：形态奇观、枯木重生、帝王传说、名人手植、寺观古树、园林名木、村落与地名、民间信仰、战争与革命记忆、诗文书画中的树、古树保护与修复、树种辨识、连理与共生、花果物产、田野走访、口述访谈。“亲子”“有意思”这类主观诉求不作标签，映射到若干客观主题的并集。
- **打标**：离线逐条目分类，每条目 ≤3 个主题，每个主题必须附 `{"theme","chunk_id","evidence"}`，`evidence` 是该子块中文原文的精确子串（确定性校验）。原有自由标签改名 `keywords` 保留，不再叫 themes。
- **用途**：
  1. 富化头（若 V5-01 判定保留）中的主题改用受控标签。
  2. 追问桥接（V5-04）。
  3. **主题池检索（证据门控）**：UNDERSTAND 输入增加受控主题清单，`Plan` 新增 `themes: list[str]`（只能取清单内的键）。explore 且 `themes` 非空时，候选限定为带这些主题的条目（与区县取交集）；交集不足 3 个条目时不限定。以开关 `THEME_POOL=off|on` 实现，默认 `off`。
     - 判定规则：在 `holdout_theme` 上，`on` 的平均 precision@K 比 V5-01 选定臂高 0.15 及以上，且没有用例的入选金标条目数下降，才把默认值改为 `on`。
  4. 探索型回答仍不声称完整，主题不提供计数。
- **校验**：每个受控主题至少 3 个条目，否则并入相邻主题；报告各主题条目数；用 Planner 在留出集中给出的主题金标集合，报告标签本身的查准/查全。

### V5-06 特判数据化与清理（Phase B）

- **涉及文件**：`backend/app/service.py`、`knowledge.py`、`places.py`、`offline_enrichment.py`、`enrichment.py`、`scripts/build_enrichment.py`
1. **UNDERSTAND 去重**：删掉提示词里与 `bilingual_hints` 重复的术语对（Buddhist pine、Hai'er Lian、Tiger Hill、Mudu），“虎丘山是景点而非虎丘区”改为词表数据（如 `special_terms.虎丘山` 带 `not_district`）经 hints 传入。删除后 `v4_8` 与留出集结果不退步才保留删除。
2. **字面集合改读数据**：`service.py` 两处 `{'清奇古怪','孩儿莲'}` 与 `offline_enrichment.py` 的同类字面量，改为读词表 `special_terms` 与富化包 `nicknames`。
3. **条目级提示词示例暂不动**（司徒庙、第 11 条地点冲突、杨树非枫杨）：先用留出集里的传说类与冲突类用例衡量通用规则是否足够。只有这些用例失败时，才实现通用的 `caveats` 数据（离线为含传说/存疑标记的子块列出“必须带限定的说法”及原文依据，随证据传给 ANSWER 与 VERIFY），而不是再加一条提示词。
4. **死字段**：`nicknames`、`is_area`、`site`、`township` 等逐一处理——接入运行时或从契约删除。新增测试：sidecar 的每个字段要么被运行时代码读取，要么在“仅离线使用”白名单中。
5. **兜底构建收口**：`Knowledge(directory, allow_fallback=False)`；缺 `enrichment.json` 时默认启动失败，只有测试夹具显式传 `True` 才走 `fallback_enrichment`。
6. **层级字段校验**：`merge_entry` 接受模型给的 `township/village/site` 前，校验其为该树项地点或别名的子串，否则保留确定性结果。

### V5-07 评测、回归与文档（Phase A 首跑 + Phase C 终跑）

- **涉及文件**：新建 `chatbot/scripts/live_eval_v5.py`，更新 `README.md`、`.env.example`、`.env.server.example`
1. `live_eval_v5.py`：读取用例文件跑端到端，按 4.1 的字段做确定性检查，输出每例的回答全文、Plan、入选条目、调用用量与耗时。支持 `--code-root state/improve-v5/baseline-v4`，以子进程方式用快照代码与快照知识库运行（环境变量取自 `chatbot/.env`，不复制密钥文件）。
2. **Phase A 首跑**：V4 快照在 `holdout_v5.jsonl` 上的端到端基线 → `state/improve-v5/e2e-v4-holdout.json`。
3. **Phase C 终跑**（V5 终版）：`holdout_v5`、`zh27`、`v4_8` 各一次；封存集在 V4 快照与 V5 终版上各一次。
4. `state/improve-v5/report.md`：V4 与 V5 并排的各集合通过率、检索指标、平均耗时、每问 rerank 与模型 tokens；首跑与修复后分列；失败用例逐条列出原因；封存集遗留问题清单。
5. 浏览器回归沿用 `scripts/browser_bilingual.mjs`，补充追问不重复与译文状态标注两项检查。
6. README 新增 V5 小节，如实写明：富化头判定结果与数据、译文审校状态（AI 逐块审校，非人工签审）、评测集的性质与规模。

## 6. 阶段门与施工顺序

| 阶段 | 内容 | 出门条件 |
|---|---|---|
| **Phase A** | `service.py` 纯重构 → V5-01 开关 → 评测脚本与用例迁移 → `dev16` 三臂 + 噪声底 → 留出集到位后：V4 快照端到端基线、三臂检索对照、判定 | 向 Planner 提交 `ab-report.md`；Planner 确认富化头默认值 |
| **Phase B** | V5-06(1,2,4,5,6) → V5-03 → V5-05 → V5-04 → V5-02 → 重建富化包 | 后端测试、前端格式与构建通过；`build_enrichment.py --check` 通过；向 Planner 提交变更摘要 |
| **Phase C** | 应用 Planner 的终版 `translation_overrides.json` 并重建 → V5-07 终跑 → 封存集 → 报告、README、发布包 | 第 8 节清单全部完成 |

- Phase A 中留出集尚未交付时，先完成不依赖它的部分，然后给 Planner 发消息并等待，不要自行出题替代。
- Phase B 的顺序可按依赖微调，但每完成一个任务先跑后端测试再进入下一个。

## 7. 预算与停止条件

- 预估：检索对照约 1,000–1,100 万 rerank tokens；端到端评测合计约 800–900 万 rerank tokens、约 200 万模型 tokens；离线重生成（主题、追问）约 150 万模型 tokens。
- 所有真实调用的用量累计写入 `state/improve-v5/usage.json`（按脚本、按日期）。
- **硬上限**：rerank 累计 3,000 万 tokens 或模型累计 600 万 tokens。触及任一上限立即停止调用并向 Planner 报告。
- 不得为了凑通过率反复重跑同一评测；每次重跑须在报告里写明原因。

## 8. 最终验收清单

1. 受保护文件零漂移：`analysis/scripts/check_story_pipeline.py` 通过；`story.db`、`manifest.json` 哈希与 `protected-sha256.before.json` 一致。
2. `cd chatbot && .venv/bin/python scripts/build_enrichment.py --check` 通过，含修订清单逐条已应用、追问与主题的 `evidence` 子串校验。
3. `cd chatbot && .venv/bin/python -m pytest -q` 全部通过（含三臂开关、树种组、追问回归、死字段、兜底收口的新测试）。
4. `cd chatbot/frontend && npm run check:format && npm run build` 通过。
5. `state/improve-v5/` 下有：`ab-dev16.json`、`ab-holdout.json`、`ab-report.md`、`e2e-v4-holdout.json`、终跑结果、`translation-qa.json`、`usage.json`、`report.md`。
6. `scripts/package_release.py` 重新打包，洁净度检查通过。
7. README 的 V5 小节与实际默认值、实际数字一致。

## 9. 给 worker 的工作约定

- 改动保持与现有代码风格一致，范围限于本文列出的文件；发现本文之外的问题只记录到报告，不顺手修。
- 不提交 git、不改 `.env`、日志与报告里不出现密钥。
- 任何让测试通过的做法如果改变了本文的口径（放宽断言、跳过用例、修改用例文件），必须在报告里单列说明。
- 报告里区分“已验证”和“未验证”；评测跑失败就如实写失败。
