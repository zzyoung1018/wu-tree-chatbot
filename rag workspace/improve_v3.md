# 《树碑立传》RAG 与“树语”问答系统改进规划书 Ver 3（极简三段式架构瘦身与体验优先规范）

> **角色说明**：本文档由规划 Agent 输出，供下游实现 Agent 直接执行架构瘦身、清理历史补丁并完成验收。
> **核心设计哲学**：**停止层层打正则补丁，用架构做减法从根源消除脆弱性与拒答感**。这是一个面向真实读者的博物馆文化导览 Chatbot，**流畅温暖的导览体验与合理响应速度是一等公民**；Verifier 只守最基本的真实性红线，绝不当挑剔文风或制造整篇拒答的“死板审计员”。
> **项目根目录**：`/Users/young/项目repo/inf  Wu museum`

---

## 1. 为什么必须重构？（旧架构臃肿与体验翻车复盘）

在真实提问 `“吴中区有哪些有意思的古树？”` 的线上轨迹（`state/app.sqlite`）中，旧架构暴露出连环结构性病灶：

1. **意图与指代靠海量中文正则硬猜**：`REFERENCE`、`FOLLOWUP`、`bare_reference`、`introduction_request`、`collection_request`、`single_site_count` 层层叠加，既漏掉了 `“有哪些有意思的古树”`（误送进 `ROUTER`），又逼着代码维护复杂的词表切片。
2. **重排后冗余的 7 批次 `SELECT` LLM 全库扫描**：实测 22 组真实 `qwen3.7-text-rerank` 结果中，具体问题的正确段落几乎全在 **Rank 1～12**（偶发附带提及在 Rank 24）；而遇到宽泛导览问题，`SELECT` 把吴中区 **29 个条目、59 段原文（2.8 万 tokens）** 全部放行塞进 `ANSWER`，再把 **5.18 万 tokens** 塞进 `VERIFY`，单次问答耗时 **33.3 秒、调用 11 次模型、消耗 18 万+ tokens**。
3. **机械数字正则（`numbers.py`）与严苛 `VERIFY` 制造毁灭性拒答**：导语写一句 `“下面介绍4处有意思的古树”` 或 `“这一年”` 就会触发 `unsupported_number`；`VERIFY` 用逐句司法蕴含标准挑刺导语和过渡段，且旧逻辑规定 `0 in unsupported`（首段不通过）直接连坐枪毙整篇回答，把模型写好的 1700 tokens 导览清零，只给用户返回一句冰冷的 `“原文核查没有通过，因此这次不采用模型生成的结论”`。

---

## 2. 目标极简架构：三步纯粹流水线（3-Stage Lean Pipeline）

彻底砍掉 `SELECT` 批次扫描循环、`numbers.py` 数字正则核查器及所有指代/意图猜测正则，将在线问答收敛为 **职责纯粹的 3 个阶段**（常规问答/导览全程仅 **3 次轻量模型调用：Understand → Answer → Minimal Verify**，端到端耗时降至 **6～10 秒**）：

```text
用户提问 + 最近最多 3 轮对话摘要 + 当前焦点卡片
   │
   ├── [零模型快路] 纯问候语（你好/hello）──► 直接返回欢迎语（0 次 LLM 调用）
   │
   ▼
[Stage 1] 统一轻量理解与改写 (UNDERSTAND) —— 1 次极快小 JSON 调用 (~300 prompt tokens)
   │  职责：自然语言指代消解 + 独立检索词改写 + 四选一模式分流（严禁在此阶段拒答任何古树问题！）
   │  输出 Plan：
   │    • mode: "qa" (具体问答/单点棵数/比较) | "explore" (导览/推荐/泛聊/故事浏览)
   │            | "inventory" (明确要求完整名单/精确总数) | "clarify" (仅无上下文裸代词或不支持的全量统计口径)
   │    • search_query: 消解代词与上下文后的独立语义检索句
   │    • subject: 目标树种或古树专名（去掉“古/老”修饰，宽泛导览时为空串）
   │    • districts: 用户正面限定的标准区县列表（如 ["吴中区"]；“吴中以外”等否定语境为空列表）
   │    • keep_focus: bool（是否仍在延续追问 current_focus）
   │    • filters: {district, species, location, status}（仅 mode="inventory" 使用）
   │
   ├── 若 mode == "clarify"   ──► 直接温和追问澄清（不调检索、不报错）
   ├── 若 mode == "inventory" ──► 走 Knowledge.structured() 参数化全量 SQL 返回精确清册
   │
   ▼
[Stage 2] 纯向量召回 + Qwen Rerank + Top-K 父卡精选 (RETRIEVE & CURATE) —— 0 次 LLM 调用！
   │  1. 用 search_query（可附带官方编号/唯一索引别名提示）执行 Qwen Embedding + Qwen Rerank
   │  2. 若 plan.districts 非空，按 supports_requested_district 过滤候选（保留正文跨区提及）
   │  3. 直接按 Rerank 排序精选 Top-K 不同父条目展开完整父卡（彻底移除 SELECT 模型循环！）：
   │       • mode == "explore"（导览/推荐）：精选前 6 个不同父条目（约 3500~5000 字，天然控量、快且准）
   │       • mode == "qa"（具体问答）：精选前 8 个不同父条目（约 4500~7000 字）
   │  4. 附带提及零成本兜底：若 plan.subject 非空，且前 K 个父条目均不含该词，直接在内存已排好序的
   │     ranked_hits 中顺延挑出最多 3 个正文含 plan.subject（且符合区县约束）的子块并入上下文
   │
   ▼
[Stage 3] 导览生成 + 极简底线护栏 (ANSWER & MINIMAL GUARDRAIL) —— 1 次生成 + 1 次底线核查
      1. ANSWER：阅读精选后的 6~8 个父条目原文（S1..Sn）与配图候选，生成自然流畅的图文回答
      2. 确定性软绑定（不做机械数字拒答）：
           • 校验并保留合法的 S1..Sn 引用角标与树项匹配的 image_id（剔除越界 ID，不因个别角标瑕疵整篇抛错）
      3. Minimal Verifier（精简输入，仅审“三大底线红线”）：
           • 仅传入精简后的候选来源（严禁重复序列化 evidence_text，输入稳定在 ~3000 tokens）
           • 只抓三大硬伤：① 张冠李戴/捏造不存在的古树；② 捏造核心树龄/历史年份；③ 把明确“传说/无依据”当确凿史实
           • 完全放行：导语总起、结语感悟、“有意思/奇特”等导览呼应词、跨段归纳、“以下介绍4处”等结构性数字
      4. 优雅降级交付（Never Blank-Reject）：
           • 废除“第 0 段不过就整篇清零”的连坐规则！只要有任意合格段落，直接剔除硬伤段（若有）并交付合格回答
           • 仅当全部或过半核心段落硬伤时最多触发 1 次修订；即便极端情况下修订仍未全过，也降级输出基于命中卡片的客观导读，彻底废除“原文核查没有通过，不采用模型结论”的生硬拒答文案！
```

---

## 3. 明确保留的不变红线与明确解绑的旧包袱

### 3.1 继续严格遵守的红线
1. **纯向量 + Rerank 检索底线不变**：不引入 SQLite FTS5 / BM25 关键词检索；`chatbot/data/knowledge/story.db`、`manifest.json`（138 条线上向量）及 `analysis/pilot/*` 原件**只读不改**。
2. **完整名单/计数底线不变**：`mode == "inventory"` 依然走 `Knowledge.structured()` 全量 SQL，绝不用 Top-K 冒充完整清册。
3. **溯源与配图真实性不变**：页面展示的引文正文、印刷页码、图片 URL 与署名 100% 由服务端从本地 `story.db` 绑定；附带提及树种（如第 66 条银杏正文里提到的杨树）不冒用银杏主图，也不污染对话焦点 `focus`。
4. **安全与网关红线不变**：保留一、二轮已修好的 `networking.py`（受信代理逐跳 IP 提取、LRU 限流、Origin 校验）、`main.py`（SSE `Condition` 唤醒与 `/cancel` 取消）、`nginx.conf`（`map` 透传转发头）及无密钥泄露约束。

### 3.2 明确批准删除的臃肿模块与旧束缚（用户已拍板确认）
1. **批准解绑旧测试 `test_ordinary_questions_retrieve_original_text_before_any_model`**：允许所有非纯问候请求先经过轻量级 `UNDERSTAND`（Stage 1）完成指代改写与模式分流，但**严禁 `UNDERSTAND` 根据目录树种列表在检索前拒绝普通问答或导览问题**（如问“杨树”、“孩儿莲”或“火星村的树”仍必须正常放行去跑向量检索）。
2. **直接删除以下文件与死代码**：
   - **删除** `/Users/young/项目repo/inf  Wu museum/chatbot/backend/app/numbers.py`（彻底移除 `chinese_number`、`draft_numbers`、`evidence_numbers` 及 `validate_claims` 中的数字比对逻辑）；
   - **删除** `/Users/young/项目repo/inf  Wu museum/chatbot/scripts/replay_select_v2.py`（以及 `select_ab_*` 若不再被引用可保留归档但不再作为门禁）；
   - **删除** `service.py` 中的 `SELECT` 提示词、`ROUTER` 旧提示词（合并入统一的 `UNDERSTAND`）、`Selection` 模型、`REFERENCE` / `FOLLOWUP` / `bare_reference` / `introduction_request` / `collection_request` / `explicit_inventory` / `single_site_count` 正则分支；
   - **删除** `retrieval.py` 中的 `selector` 参数、`scan` / `read` 批次并发循环、`candidates()` 方法，以及 `config.py` / `compose.yaml` / `.env*.example` 中的 `select_mode`、`select_min_candidates`、`select_top1_min`、`select_drop_ratio`、`select_batch_concurrency` 五个废弃配置项！

---

## 4. 分模块施工规范（Task Breakdown）

### V3-01 [核心瘦身] 实现 Stage 1 统一轻量理解器 `UNDERSTAND`（替代 `ROUTER` + 全部指代/分流正则）

- **涉及文件**：`/Users/young/项目repo/inf  Wu museum/chatbot/backend/app/service.py`
- **数据契约（Pydantic `Plan` 模型）**：
  ```python
  class InventoryFilters(BaseModel):
      district: str | list[str] | None = None
      species: str | None = None
      location: str | None = None
      status: Literal['extant', 'gone', 'partial'] | None = None

  class Plan(BaseModel):
      mode: Literal['qa', 'explore', 'inventory', 'clarify'] = 'qa'
      search_query: str = ''
      subject: str = ''
      districts: list[str] = Field(default_factory=list)
      keep_focus: bool = False
      filters: InventoryFilters = Field(default_factory=InventoryFilters)
      clarification: str = ''
  ```
- **前处理与输入构造**：
  1. **纯问候零调用快路**：`re.fullmatch(r'[你好您好嗨哈喽helloHi\s！!。，,.]+', question, re.I)` 仍直接返回 `status='welcome'`。
  2. **无上下文纯裸代词零调用护栏（可选轻量保留，5 行以内）**：若 `not history and not focus` 且问题极短（如 `“它在哪里？”`、`“这棵树多大？”` 这类仅含代词+疑问词且无任何名词实体的句子），可直接返回 `status='clarify'`，也可交由 `UNDERSTAND` 返回 `mode='clarify'`。
  3. **构造精简输入 JSON**（控制总输入在 ~350 tokens 内）：
     - `question`: 用户原话
     - `current_focus`: 若会话有 `focus`，附上 `{entry_no, item_no, location, subject, multi_item_ambiguous: focus.get('item_no') is None}`
     - `recent_history`: `recent_history(history)`（最近最多 3 轮已完成问答摘要）
     - `code_hints`: 若 `knowledge.code_matches(question)` 命中，附上 `[{"code": code, "location": card["location"], "title": card["title"]}]`
     - `districts`: `knowledge.districts`
     - `species`: `knowledge.species`
- **`UNDERSTAND` 提示词核心准则（精炼、清晰、无歧义）**：
  - **模式四选一**：
    - `explore`：开放式导览、推荐、趣味盘点、主题漫谈、多处故事介绍（如“吴中区有哪些有意思的古树？”、“给我介绍几棵银杏”、“讲讲古树与村民保护的故事”）。
    - `qa`：具体某棵/某处古树的事实、树龄、位置、来历、真伪核实、两树对比、按外貌找树、或**具体单座寺庙/园子/古迹内的棵数与树种**（如“司徒庙清奇古怪有几棵”、“雕花楼里有哪些古树”、“这棵树有多大”）。
    - `inventory`：**仅当**用户明确索要全书、某区县、某乡镇片区、某树种或存亡状态的**完整名单、全部条目或精确总数量**（如“书中哪些树已经不存了”、“东山镇有哪些古树”、“列出工业园区所有古树”、“吴中区共有多少项银杏”）。
    - `clarify`：**仅限三种情形**——① 无上下文且代词指代不明（或 `current_focus.multi_item_ambiguous` 为真但追问单数“这棵树”未指明哪一棵）；② 用户明确要求 `inventory` 全量统计，但统计条件是未结构化的树龄区间、单株总棵数、“种在寺庙里”等类别条件。**绝不允许对 `qa` 或 `explore` 问题返回 `clarify`！即使所问树种（如“杨树”）或地名不在列表中，也必须正常输出 `qa` 或 `explore` 去检索正文！**
  - **`search_query` 改写准则**：
    - 若当前问题是追问（含“它/它们/这棵/那棵”或省略主语如“有多大年纪了”、“配上它的书内照片介绍一下”），结合 `current_focus` 或 `recent_history` 改写为包含具体地点与树名的完整检索句，并设 `keep_focus=true`；
    - 若用户已明确切换到新地点、新树种、新区县或新编号，以新对象生成 `search_query`，设 `keep_focus=false`；
    - 若命中 `code_hints`，在 `search_query` 中融入对应地点与树名；
    - 首轮独立提问可保留原意或适度补充同义检索词（如将 `“吴中区有哪些有意思的古树？”` 改写为 `“吴中区形态奇特、传说有趣或故事生动的古树”`）。
  - **`districts` 准则**：仅当用户正面限定了某个或某几个行政区（含简称如“吴中”、“高新区”、“园区”）时填入规范区县全称数组；若说“吴中以外”则填 `[]`；注意裸词“虎丘/虎丘山”是姑苏区景点而非“虎丘区（高新区）”。
  - **输出预算与容错**：`max_tokens=400`，若 JSON 校验失败允许 1 次轻量重试；审计 stage 记为 `'understand'`。

---

### V3-02 [核心瘦身] 重构 Stage 2 检索与父卡精选（砍掉 `SELECT`，用 Rerank Top-K 直出）

- **涉及文件**：
  - `/Users/young/项目repo/inf  Wu museum/chatbot/backend/app/retrieval.py`
  - `/Users/young/项目repo/inf  Wu museum/chatbot/backend/app/config.py`
  - `/Users/young/项目repo/inf  Wu museum/chatbot/backend/app/service.py`
- **具体改造**：
  1. **简化 `config.py`**：
     - 删除 `select_mode`、`select_min_candidates`、`select_top1_min`、`select_drop_ratio`、`select_batch_concurrency`。
     - 新增两个直观的父卡精选上限配置：
       - `qa_top_entries: int = Field(default=8, ge=1, le=20)`
       - `explore_top_entries: int = Field(default=6, ge=1, le=15)`
  2. **简化 `VectorRetrieval.search`（`retrieval.py`）**：
     - 签名收敛为：`async def search(self, query, nos=None, *, candidate_limit=None, top_entries=8, districts=(), subject='')`
     - 步骤：
       1. 执行原有的 `embeddings.query(query)` 向量召回 + `reorder(query, dense)` Qwen 重排，得到全量排序列表 `ranked_hits`（当前 138 条）。
       2. **区县后验过滤（不作向量前裁剪）**：若 `districts` 非空，按顺序保留满足 `supports_district(chunk, districts)`（条目 `district in districts` 或 `context_header + text` 明确提及该区县别名）的 `hits`。
       3. **按父条目去重取 Top-K**：按 `hits` 排名先后收集前 `top_entries` 个不同 `unit_id` 对应的命中子块（`explore` 取 6 个父条目，`qa` 取 8 个父条目）。
       4. **罕见树种附带提及内存补召（0 次模型调用，5 行代码）**：若 `subject` 非空，检查已选中的父条目（其 `species` 与全部子块正文）是否至少有一处包含 `subject`；若完全未出现，则在过滤后的 `hits` 剩余部分中按排名顺序查找正文含 `subject` 的子块（最多补入前 **3** 个），避免像 `Rank 24` 的“双杨村杨树”在 Top 8 父卡外漏掉！
       5. 调用原有的 `self.expand(selected_hits, budget=self.settings.retrieval_context_chars)` 与 `self.knowledge.pack(parents)` 生成 `(context, sources, cards)`。
  3. **确定性判断附带提及与配图/焦点安全（无需 `SELECT` 模型）**：
     - 当 `plan.subject` 非空（且不属于地点词或单树雅号如 `清奇古怪 / 孩儿莲`），对于某张候选父卡 `card`：若 `plan.subject` 不在 `card['species']` 中（且没有任何主树种名称包含 `plan.subject` 或被其包含），则该 `card` 属于**附带提及（incidental mention）**：
       - 将其 `unit_id` 加入 `excluded_units` 传给 `image_candidates(...)`，严禁把第 66 条银杏的照片配给附带提及的杨树；
       - 在回答完成计算 `focus` 时，凡仅引用了附带提及单元的回答，强制 `focus = None, clear_focus = True`。
  4. **保留第 11 条单点位置冲突直出快路**：
     - 若 `plan.mode == 'qa'`、问句询问所在地（`在哪|位置|地点|地址|什么地方`）、排第一的父卡带 `location_conflict` 且问句（或延续的 `focus`）指向该冲突地名（如“灵源古寺的罗汉松在哪里？”），继续直接返回已记录的并列冲突说明，准确又快速。

---

### V3-03 [体验核心] 重构 Stage 3 极简底线护栏（Minimal Verifier）与零白屏优雅交付

- **涉及文件**：`/Users/young/项目repo/inf  Wu museum/chatbot/backend/app/service.py`
- **彻底删除**：
  - 删除 `numbers.py` 及其所有导入、`check_numbers` 参数、`numeric_problems` 计算与 `unsupported_number` 分支。
- **优化 `ANSWER` 提示词**：
  - 明确区分 `mode == "explore"` 与 `mode == "qa"`：
    - 当 `intent == "explore"`（导览/推荐/“有哪些有意思的古树”）时：鼓励以热情、有画面感的博物馆讲解员口吻，从提供的候选古树中挑选 **3～5 棵最有故事或形态最奇特的代表性古树** 娓娓道来，开头写简短自然的导语呼应用户问题（无需声明“非完整名单”之类的机械套话，自然说“不妨来看看这几棵颇有故事的古树”即可），每段扎实绑定对应来源 `S1..Sn` 并挑选合适书内配图。
    - 当证据中确实找不到用户所问对象（如问“吴中区的杨树”但证据里只有其他树，或问“博物馆几点开门”）时，才输出 `status="insufficient"`。
- **软化确定性引用清洗（不再因单个角标瑕疵整篇重来）**：
  - 在 `validate_claims(draft, sources)` 中：
    - 对每段 `citations` 过滤保留存在于 `source_map` 中的合法 `source_id`。
    - 若某段是紧挨着下文的简短总起导语（如第 0 段 ≤ 80 字且未填有效 `source_id`，而第 1 段有合法 `source_id`），自动将该导语并入第 1 段或借用全文首个主要 `source_id`，**绝不因此把整篇 1500 字打回重写**！
    - 仅当整篇草稿过滤后**没有任何一段拥有合法 `source_id`** 时，才触发 1 次格式修复。
- **重写 `VERIFY` 为“极简底线护栏（Minimal Guardrail）”**：
  1. **输入瘦身 90%**：传给 `VERIFY` 的 `sources` 列表只序列化精简字段：`[{'id': s['id'], 'location': s['location'], 'title': s['title'], 'type': s['type_label'], 'text': s['text'], 'official_codes': s['official_codes'], 'review_flags': s['review_flags']}]`（**严禁把 `evidence_text`、`quotes`、`citation` 等冗余重复文本塞进 `VERIFY` JSON！**）。
  2. **极简 `VERIFY` 提示词规则**：
     ```text
     你是《树碑立传》问答的底线事实护栏。仅输出 JSON：{"unsupported":[段落序号],"citation_fixes":[{"index":段落序号,"source_ids":["S1"]}],"reason":"简短原因"}。
     注意：这是一个面向公众的文化导览对话助手，请保持宽容，只拦截严重事实硬伤！
     【完全放行，严禁判为 unsupported】：
     1. 开篇导语、结尾感悟、过渡句，以及对“有意思、奇特、动人、代表性”等主观导览评价的呼应；
     2. 对多段原文的合理概括、释义、比较、“下面介绍四处古树”等结构性计数；
     3. 段落事实在提供的 sources 中有依据，只是漏标或错标了 source_id —— 此时必须放入 citation_fixes 补齐正确 source_id，绝不列入 unsupported！
     【仅当某段触犯以下 3 条底线红线之一时，才列入 unsupported】：
     1. 张冠李戴或无中生有：把 A 树的树龄、地点、人物故事安到 B 树上，或编造了 sources 中完全不存在的树木/地点；
     2. 硬核数据失实：编造了与原文矛盾或原文完全未提及的具体树龄、历史年份，或违背了用户明确限定的树种/区县（如问杨树却拿枫杨充当杨树）；
     3. 传说当史实 / 隐瞒冲突：原文明确写“传说/相传/无明确依据”的内容被断言为确凿史实，或专门询问冲突条目所在地时隐瞒了 review_flags 中的地点差异。
     全部无硬伤则输出 unsupported=[]。
     ```
  3. **零白屏优雅交付策略（Graceful Degradation）**：
     - 应用 `VERIFY` 返回的合法 `citation_fixes`。
     - **情况 1（全部通过 `not unsupported`）**：直接交付！
     - **情况 2（局部瑕疵，且剩余合格段落 `>= 1` 段并足以回应问题，例如 `explore` 写了 5 段有 4 段合格，或 `qa` 有核心段落合格）**：
       - **废除 `0 not in unsupported` 首段一票否决规则！** 无论瑕疵段在第 0 段还是最后一段（只要不违反单点位置冲突披露），**直接剔除 `unsupported` 段落，保留其余全部合格段落立即返回给用户**（记录 `omitted_claims`，甚至无需再耗费 10 秒重调 `ANSWER`）！
     - **情况 3（全部段落或唯一核心段被标 `unsupported`）**：
       - 若尚未修订过（`attempt == 0`），带上 `VERIFY` 的 `reason` 让 `ANSWER` 修订 **1 次**（最多总计 2 次 `ANSWER` 调用，不再搞 3 轮死循环）。
       - 若修订后依然有合格段落，剔除剩余不合格段后交付。
       - **终极兜底**：若修订后仍然 `claims` 为空，判断原因：若是用户问了书中确实没有的对象（如“香樟的1500年树龄”核查为假且无真实匹配，或 `qa` 事实被证伪），返回温和说明 `“书中相关段落尚未记载这一具体结论，已为你附上最相近的原文供参考。”`（附带 `sources` 与 `cards`）；彻底移除刺眼的 `“原文核查没有通过，因此这次不采用模型生成的结论”`！

---

### V3-04 [数据与交互收口] 修复乡镇变体统计、目录搜索子地点与离线草稿保护

把二轮深挖出的 3 个确定性体验细节一并干净收口：

1. **乡镇/片区后缀归一化匹配（`knowledge.py` & `places.py`）**：
   - 在 `Knowledge.structured({'location': loc})` 中，除了匹配子串，还要对乡镇/街道级地名做词干对齐（例如去除末尾 `古镇|街道|镇|乡` 后的区域词干匹配 `self.item_place_terms`）：
     - 确保查询 `'木渎镇'` 命中 `'木渎古镇'`（2 项）；
     - 查询 `'同里镇'` / `'同里古镇'` 命中 `'同里'`（1 项）；
     - 查询 `'甪直古镇'` 命中 `'甪直镇'`（3 项）；
     - 查询 `'震泽镇'` / `'震泽古镇'` 完整命中震泽全部 **4 项**（不再漏掉写成“震泽古镇”的 1 项）；
     - 查询 `'虞山镇'` / `'虞山街道'` 完整命中虞山全部 **6 项**，`'镇湖街道'` 命中镇湖全部 **3 项**。
2. **古树目录支持搜索多树项子地点与冲突别名（`knowledge.py` & `frontend/src/App.tsx`）**：
   - 在 `Knowledge.public_card()` 的 `location_aliases` 中并入子项专属别名与 `ITEM_PLACE_NAMES`（如 `'灵源寺'`、`'东山镇灵源古寺'`、`'清奇古怪'`）；
   - 在 `Knowledge.catalog()`（`knowledge.py:70`）与前端 `filteredCatalog`（`App.tsx:475`）的匹配字段中加入 `c.items.map(i => i.location)`，确保用户在目录搜 **`“三官殿”`** 命中第 2 条、搜 **`“灵源寺”`** 命中第 11 条、搜 **`“清奇古怪”`** 命中第 18 条！
3. **前端断网草稿保护与后端 `/cancel` 孤立状态兜底**：
   - `chatbot/frontend/src/App.tsx`：在 `send()` 中记录 `let accepted = false`（收到 `event === "accepted"` 置为 `true`）；若请求中断且未收到 `accepted`，或恢复轮询后发现服务端最后一条用户消息并非本次 `question`，保留 `setDraft((prev) => prev || question)`，绝不弄丢用户刚输入的文字。
   - `chatbot/backend/app/main.py`：`cancel()` 中若 `mid` 存在但不在内存 `jobs` 字典中，补 `elif mid: store.finish(mid, {'status': 'error', 'text': '已停止本次回答，可重新提问。'}, status='error')` 防止孤立锁死。
   - `chatbot/README.md`：同步更新架构图与检索章节说明，删去过时的 `SELECT` 7 批次、`0.05/0.01` 阈值及 `numbers.py` 描述，与 V3 三段式极简架构保持一致。

---

## 5. 单元测试迁移对照表（Test Suite Migration Guide）

由于本版主动做减法删除了 `SELECT` 与 `numbers.py`，实现 Agent 应同步清理过时的旧测试，替换为验证新三段式架构的干净测试：

| 测试文件 | 需删除/替换的旧测试 | 替换/新增的 V3 测试断言 |
| --- | --- | --- |
| `tests/test_service.py` | `test_ordinary_questions_retrieve_original_text_before_any_model`、`test_empty_later_batch_does_not_replace_body_subject`、`test_model_cannot_select_unretrieved_source_or_focus` 及旧 `selection(...)` 桩调用 | 1. 验证 `UNDERSTAND` 对普通问答/罕见树种/虚构地名均放行进入向量检索（不基于目录拒答）；<br>2. 验证 `explore` 模式（如 `“吴中区有哪些有意思的古树？”`）自动精选 `<= 6` 个父条目，导语段即使带主观趣味词或被 `VERIFY` 标记 `unsupported=[0]`，仍保留后续合格段落正常返回 `status='answered'`！ |
| `tests/test_embeddings.py` | `test_relevance_check_can_find_later_reranked_body_without_catalog_gate`、`test_relevance_selection_cannot_introduce_unretrieved_source`、`test_adaptive_reading_and_safe_full_scan_fallbacks` | 验证 `VectorRetrieval.search` 在 `0` 次 LLM 调用下：按 `top_entries` 精选父卡、支持 `districts` 后验过滤，且当 `subject='杨树'` 排在第 24 位时能通过内存顺延补召第 66 条！ |
| `tests/test_improvements.py` | 所有测试 `numbers.py` 的用例（第 26–105 行）及绑定旧 `SELECT` 输入结构的断言 | 1. 验证多轮追问（句中代词、复数指代、省略主语、“这棵罗汉松”）经 `UNDERSTAND` 改写 `search_query` 并保持 `focus`；<br>2. 验证 `structured()` 对 `木渎镇 / 同里镇 / 震泽镇 / 虞山街道` 返回完整无遗漏项数；<br>3. 验证 `catalog('三官殿') == [2]`、`catalog('灵源寺') == [11]`、`catalog('清奇古怪') == [18]`。 |

---

## 6. 最终交付与验收清单（Final Verification Checklist）

1. **代码净减量核查**：
   - 确认 `chatbot/backend/app/numbers.py` 与 `chatbot/scripts/replay_select_v2.py` 已删除；
   - 确认 `SELECT` 批次循环与无界意图正则已从 `service.py` / `retrieval.py` 彻底清除。
2. **受保护数据资产零改动核查**：
   - 确认 `chatbot/data/knowledge/story.db` 与 `manifest.json` 哈希未变，138 条线上向量 `ready=True`。
3. **后端全量单元测试通过**：
   ```bash
   cd "/Users/young/项目repo/inf  Wu museum/chatbot"
   .venv/bin/python -m pytest -q
   ```
4. **前端格式与生产构建通过**：
   ```bash
   cd "/Users/young/项目repo/inf  Wu museum/chatbot/frontend"
   npm run check:format
   npm run build
   ```
5. **真实线上体验冒烟实测（若 `.env` 密钥有可用额度）**：
   - 实测提问 **`“吴中区有哪些有意思的古树？”`**：确认模型调用次数 `<= 3` 次（`understand` + `answer` + `verify`），总耗时显著下降（目标 `< 12s`），返回图文并茂的多棵古树导览，绝不再出现“原文核查没有通过”；
   - 实测多轮追问 **`“木渎的罗汉松在哪里？”` → `“配上它的书内照片，并介绍一下。”`**；
   - 实测发布包重打包：`python scripts/package_release.py` 无密钥、无 `.DS_Store` 混入。
