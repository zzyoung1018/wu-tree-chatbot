# 《树碑立传》RAG 与“树语”问答系统改进规划书 Ver 2（二轮返工施工规范）

> **角色说明**：本文档由规划/审查 Agent 在对第一轮改造结果进行全量代码审查与实测回放后输出，供下游实现 Agent 直接执行二轮精准返工与验收。
> **前置文档**：`/Users/young/项目repo/inf  Wu museum/rag workspace/improve.md`（第一版规范中的所有全局红线与不可违背约束在本版中 **100% 继续生效**）。
> **项目根目录**：`/Users/young/项目repo/inf  Wu museum`
> **当前基准状态**：后端 `pytest` 120 项通过、前端构建通过；`IMP-04`、`IMP-05`、`IMP-08`、`IMP-09`、`IMP-11`、`IMP-12` 已验收通过，**严禁在二轮修改中破坏已达标项与受保护数据资产**。

---

## 1. 一轮审查结论与二轮返工范围总览

第一轮实现虽然通过了合成单元测试，但在回放真实 `qwen3.7-text-rerank` 分数分布、全量 `story.db` 138 个子块文本以及反向代理恶意请求头时，暴露出 **5 项真实缺陷/退化**（其中 2 项为 P0 级线上失效或误判）：

| 任务编号 | 级别 | 对应一轮项 | 核心问题摘要 | 主要涉及文件 |
| --- | --- | --- | --- | --- |
| **IMP2-01** | **P0** | `IMP-03` | 自适应 `SELECT` 截断使用未校准绝对分阈值（`0.05` / `0.01`），在 21 个真实高置信问题上 **提前截断率 0/21**，且把并行批次拆成两轮串行等待，延迟不降反增 | `chatbot/backend/app/retrieval.py`、`config.py`、`tests/test_embeddings.py` |
| **IMP2-02** | **P0/P1** | `IMP-01`、`IMP-06` | 未锚定的中文后缀正则（`[\u4e00-\u9fff]{2,}(?:寺\|庙\|村\|园...)`）吞噬普通句子，破坏代词追问与“全书寺庙古树统计”路由；`named_place` 产生 `'13号'`、`'号杂院'` 等噪声切片；`这棵罗汉松` 丢失当前焦点地点 | `chatbot/backend/app/service.py`、`knowledge.py`、`tests/test_improvements.py` |
| **IMP2-03** | **P1** | `IMP-07` | `chinese_number` 将中文概数 `"一两棵"` 拼成 `12`、`"两三处"` 拼成 `23`；语法词 `"一个/一处"` 抽取数字 `1` 在 46/138 个无 `1` 子块上触发假阳性拒答；`"〇四版"` 漏做年份扩展 | `chatbot/backend/app/numbers.py`、`tests/test_improvements.py` |
| **IMP2-04** | **P1** | `IMP-02`、`IMP-10` | `X-Forwarded-For` 整串解析可被客户端左端垃圾字符投毒并回退至受信网关 IP（导致网关级全局锁死或绕过限流）；内层 `nginx.conf` 覆盖外层 `X-Forwarded-Host` 与 `X-Forwarded-Proto`；发布包混入 `.DS_Store` | `chatbot/backend/app/networking.py`、`deploy/nginx.conf`、`scripts/package_release.py`、`tests/test_improvements.py` |
| **IMP2-05** | **P2** | `IMP-06` | `conflict_required` 扫描全部候选 `cards` 而非实际引用来源，只要第 11 篇进入 `overview` 候选就禁用段落裁剪（`partial_answer=False`），导致整篇概览连坐拒答 | `chatbot/backend/app/service.py`、`tests/test_improvements.py` |

---

## 2. 全局红线重申（Must-Keep Invariants）

1. **检索路线红线**：继续保持纯向量召回（`qwen3.7-text-embedding`）+ 重排（`qwen3.7-text-rerank`），**严禁**引入关键词检索或向量前目录裁剪；首轮普通问答在向量检索前必须保持 **0 次 LLM 调用**。
2. **数据与向量只读红线**：**不得修改** `chatbot/data/knowledge/story.db`、`manifest.json`（138 条线上向量）及 `analysis/pilot/*` 基线数据文件。
3. **测试诚实性红线**：严禁为了迎合错误逻辑去构造脱离真实分布的假分数（例如一轮测试用 `tail=0.001` 掩盖了真实 Qwen Rerank 长尾普遍 `>= 0.05` 的事实）。单元测试必须覆盖真实边界分布与回归反例。

---

## 3. 二轮详细返工任务卡（IMP2-01 ~ IMP2-05）

### IMP2-01 [P0] 重构自适应 `SELECT` 截断判据，消除串行二次等待与全扫退化

- **问题定位**：
  - `chatbot/backend/app/retrieval.py`（第 173–186 行）
  - `chatbot/backend/app/config.py`（第 25–28 行）
  - `chatbot/backend/tests/test_embeddings.py`（第 402–449 行）
- **实测根因**：
  1. 当前实现依赖两个绝对分数阈值：`prefix` 统计 `rerank_score >= 0.05` 的条数扩窗，随后又用 `any(h['rerank_score'] >= 0.01 for h in tail)` 决定是否补扫尾部。
  2. 回放 `chatbot/state/select-ab/20261110-select-ab/retrieval/` 中全部 **22 组真实 `qwen3.7-text-rerank` 分数分布**发现：Qwen Rerank 输出未经稀疏截断，单点事实问句（如 `"三官殿的龙柏有多少岁？"`、`top1=1.000`、`rank40=0.096`）仍有 60~100+ 个无关子块得分 `>= 0.05`，且第 138 名长尾得分仍普遍 `>= 0.01`。
  3. 结果：在 21 个 `top1 >= 0.60` 的真实查询上，**提前截断成功数为 `0/21`**（100% 仍扫满 138 条 / 7 个 batch）；更严重的是，代码先 `await scan(hits[:window_size])`，再**串行** `await scan(tail)`，把原本一轮并发（受 `semaphore=4` 限制为 2 波）劣化成了“首轮等完再起次轮”，端到端延迟反而恶化。
  4. 与此同时，宽泛/聚合类问句在真实分布中呈现出极明显的平缓长尾特征（如 `"给我介绍一些古杨树"`：`top1=0.927, rank40=0.739, ratio=1.25`；`"给我介绍吴中区一些树"`：`top1=0.998, rank40=0.939, ratio=1.06`），而明确指向特定树/特定典故的问句则呈现陡峭衰减（如 `"三官殿的龙柏有多少岁？"`：`top1=1.000, rank40=0.096, ratio=10.40`；`"木渎的罗汉松在哪里？"`：`top1=1.000, rank40=0.242, ratio=4.13`；`"吴江区有没有与杨树有关的传说？"`：`top1=0.963, rank40=0.279, ratio=3.45`）。
- **精确改造方案**：
  1. **废弃无效绝对长尾阈值，改用“Top-1 置信度 + Top-40 相对陡峭度”门控固定首轮窗口**：
     - 在 `Settings`（`config.py`）中：保留 `select_mode: Literal['full', 'adaptive'] = 'adaptive'` 与 `select_min_candidates: int = Field(default=40, ge=20, le=500)`；将失效的 `select_score_threshold` 替换为具有明确物理意义的：
       - `select_top1_min: float = Field(default=0.70, ge=0, le=1)`（Top-1 最低重排分）
       - `select_drop_ratio: float = Field(default=2.0, ge=1.0, le=100.0)`（陡峭度比值：`hits[0]['rerank_score'] / max(hits[initial - 1]['rerank_score'], 1e-6) >= select_drop_ratio`）
     - 计算初始窗口大小 `initial = min(len(hits), math.ceil(self.settings.select_min_candidates / 20) * 20)`（默认 40 条，即恰好 2 个并行 batch）。
     - 仅当同时满足以下条件时启用两阶段自适应首轮截断（否则直接一次性将全部 `hits` 送入 `scan(hits)` 单轮并发执行，绝不分两轮串行！）：
       - `self.settings.select_mode == 'adaptive'`
       - `audit['ranking'] == 'rerank'`
       - `intent == 'question'`（`overview` 概览请求始终一次性全量并发扫描）
       - `len(hits) > initial`
       - `hits[0]['rerank_score'] >= self.settings.select_top1_min`
       - `hits[0]['rerank_score'] / max(hits[initial - 1]['rerank_score'], 1e-6) >= self.settings.select_drop_ratio`
  2. **首轮早停与安全回退条件**：
     - 若未命中上述 `adaptive` 门控（例如 `"给我介绍一些古杨树"` 的 `ratio=1.25 < 2.0`，或 `"博物馆明天几点开门？"` 的 `top1=0.499 < 0.70`）：**直接调用一次 `await scan(hits)`**，所有 7 个 batch 在同一个 `gather` 池内由 `semaphore(4)` 调度，不产生额外的批次间串行停顿。
     - 若命中 `adaptive` 门控：先执行 `direct = await scan(hits[:initial])`（仅 2 次 `SELECT` 并发调用）：
       - 若 `direct` 为 `True`（前 40 条中已明确命中 `answer_scope == 'direct'` 的直接正文证据）：**立即停止扫描 `tail`**，记录 `audit['early_stopped_batches'] = math.ceil(len(hits[initial:]) / 20)`（节省 5 次 LLM 调用，约 71% `SELECT` 开销）。
       - 若 `direct` 为 `False`（前 40 条全空，或只命中了 `body_mentions` 附带提及，例如查询跨篇散落的冷门树种）：自动回退执行 `await scan(hits[initial:])` 补齐剩余全部候选，确保全书附带提及 0 漏召回！
     - **彻底删除** `any(h['rerank_score'] >= 0.01 for h in tail)` 与 `prefix >= select_score_threshold` 逻辑。
- **验收标准**：
  - 用真实重排分特征构造测试：当 `top1=0.98, rank40=0.24, tail=0.08`（注意 `tail=0.08` 远大于旧阈值 `0.01`！）且前 40 条返回 `answer_scope='direct'` 时，`relevance_checked_count` **必须等于 40**，`early_stopped_batches == 5`。
  - 当 `top1=0.93, rank40=0.74`（平缓分布，如多树种/多条目宽泛问题）时，必须在**单次 `scan(hits)`** 中一次性并发检查全部 138 条，不拆成 `40 + 98` 两段串行。
  - 当前 40 条仅返回 `body_mentions` 或空列表时，必须补扫剩余 98 条（总计 138 条）。

---

### IMP2-02 [P0/P1] 根治无边界地点正则误判，修复代词焦点继承与全书统计路由

- **问题定位**：
  - `chatbot/backend/app/service.py`（第 96–115 行、第 243–261 行）
  - `chatbot/backend/app/knowledge.py`（建议新增规范化已知地点词表属性）
  - `chatbot/backend/tests/test_improvements.py`
- **实测根因**：
  1. `explicit_object`（`service.py:107`）与 `local_place_hint`（`service.py:113`）使用了无分词边界的宽泛正则 `r'[\u4e00-\u9fff]{2,}(?:寺|庙|村|园|山|镇|殿|楼)'`：
     - 在无空格的中文句子里，`"[\u4e00-\u9fff]{2,}"` 会吞掉前文动词与介词！
     - **多轮追问失忆回归**：用户追问 `"它是怎么保护全村的？"`（命中 `"保护全村"`）、`"它在园子什么位置？"`（命中 `"它在园"`）、`"为什么种在庙前？"`（命中 `"为什么种在庙"`）时，`explicit_object` 误判为 `True` → `reference=False, pronoun=False`，导致彻底丢掉 `focus` 注入！
     - **全书统计路由旁路回归**：用户提问 `"书里种在寺庙里的古树一共有多少项？"`（命中 `"书里种在寺庙"`）或 `"全书在古镇里的银杏有多少棵？"` 时，`local_place_hint` 误判为 `True` → `local_question=True` → 强行令 `collection_request=False`，跳过了 `ROUTER` 的 `clarify` 统计口径澄清保护，直接把 Top-K 检索结果当成统计回答！
  2. **“指示代词 + 当前焦点树种”追问丢失地点消歧**：
     - 当 `focus` 为 `{entry_no: 23, item_no: 2, subject: '罗汉松'}`（木渎古松园）时，用户追问 `"这棵罗汉松有多大年纪了？"` 或 `"那棵罗汉松是谁种的？"`。因为句子含 `'罗汉松'`（命中 `knowledge.species`），`explicit_object` 返回 `True` → `reference` 被置为 `False`，检索词未补充 `"古松园"`，导致全书多处罗汉松混入重排前排。
  3. **`named_place` 任意后缀切片污染**：
     - `service.py:99` 使用 `place[start:] for start in range(..., len(place) - 2)` 对所有地点做机械后缀截断，切出了 `'13号'`、`'3 号'`、`'公堂）'`、`'公墓前'`、`'号杂院'` 等大量非地名碎片，极易在普通问句中误触发。
- **精确改造方案**：
  1. **在 `Knowledge` 初始化时构建干净的确定性地点词表 `self.place_terms`（完全替代 `named_place` 切片与泛化后缀正则）**：
     - 数据源遍历所有 `cards.values()` 的 `card['location']`、`items[].location`、`entry_aliases` 以及条目/树项专属古树群雅号（如 `'清奇古怪'`）。
     - 清洗规则：
       1. 按括号与分隔符 `[（()）、，,\s/]+` 拆分为独立片段；
       2. 剥离行政区前缀（`DISTRICT_ALIASES` 中的区县名，注意保留具名山镇如 `虎丘山`）；
       3. 剥离门牌号及方位尾缀（如 `\d+(?:-\d+)?号.*$`，以及末尾孤立的 `内|前|后|旁|侧|东侧|西侧|南侧|北侧|院内|门口`）；
       4. 从清洗后的片段中提取：
          - 完整清洗后地名（长度 `>= 2`，且不含纯数字/英文，不等于泛化词如 `'工业园区'`、`'古树'`、`'杂院'`）；
          - 若片段含镇/街道/山/村/路/巷前缀 + 具体寺庙/园林/场馆地标（如 `'金庭镇三官殿'`、`'木渎古镇严家花园'`、`'甪直镇保圣寺'`、`'光福镇司徒庙'`），同时提取镇级地名（如 `'金庭镇'`、`'木渎古镇'`、`'木渎'`、`'甪直镇'`、`'甪直'`、`'光福镇'`、`'东山镇'`）与院落级地标（如 `'三官殿'`、`'严家花园'`、`'保圣寺'`、`'司徒庙'`、`'古松园'`、`'石壁永慧禅寺'`、`'灵源寺'`、`'灵源古寺'`）。
     - 过滤黑名单：排除任何长度 `< 2` 的词，排除行政区名本身（行政区由 `DISTRICT_ALIASES` 单独管理），排除通用泛称（`{'寺庙', '古寺', '古镇', '村落', '全村', '园子', '花园', '公园', '古园', '山林', '殿堂', '祠堂', '杂院', '书院', '学院', '医院', '小学', '中学'}`）。
     - 将提取结果按长度降序存为 `tuple[str, ...]`（`knowledge.place_terms`），并提供辅助区分：
       - **乡镇/片区级大地点**（以 `镇|街道|乡|岛` 结尾或其简称如 `木渎|甪直|东山|光福|金庭|同里|黎里|震泽|盛泽|千灯|锦溪`）：用户问 `"东山镇有哪些古树？"` 属于区域枚举，**仍应触发 `collection_request`** 走 `ROUTER` 全量 SQL 查询！
       - **具体院落/寺庙/古迹/单点地标**（如 `司徒庙`、`三官殿`、`严家花园`、`留园`、`古松园`、`保圣寺`、`明月湾`、`清奇古怪` 等非乡镇级具体地点）：当问题不含 `全部|所有|完整|名单|清单` 等显式清点词，仅问单点棵数（如 `"司徒庙的清奇古怪有几棵？"`、`"保圣寺有几棵古银杏？"`）时，才判定为 `single_site_count` 走正文阅读。
  2. **重构 `service.py` 中的对象识别、指代消解与统计分流**：
     - **彻底删除** `explicit_object` 与 `local_place_hint` 中的 `r'[\u4e00-\u9fff]{2,}(?:寺|庙|村|园|山|镇|殿|楼)'` 正则，**彻底删除** `place[start:]` 机械后缀切片！
     - 定义 `matched_places = [p for p in self.knowledge.place_terms if p in question]`、`matched_species = [s for s in self.knowledge.species if s in question]`、`matched_districts = requested_districts(question, self.knowledge.districts)`、`matched_codes = self.knowledge.code_matches(question)`。
     - **精准判定指代追问 `pronoun`**：
       - 情况 A（含显式指示词/代词 `REFERENCE.search(question)`）：
         - 若无 `focus` 且无 `history`：当 `bare_reference(question)` 为真（且无具体地名/编号/树种）时，直接返回 `status='clarify'`（保持首轮裸代词不调模型）。
         - 若有 `focus`：只要问题**未引入新的具体地点/区县/官方编号**，且（未提及树种，**或提及的树种本身就属于当前 `focus` 对应的树种集合**，例如 `focus` 是古松园罗汉松时追问 `"这棵罗汉松有多大年纪了？"`），均判定为延续当前 `focus`（`pronoun = True`）！反之，若用户问 `"那司徒庙的圆柏呢？"` 或 `"这棵银杏有多大？"`（切换了新地点或新树种），则 `pronoun = False`，以新对象为准。
       - 情况 B（零主语省略追问）：`focus` 存在、问题长度 `<= 28`、无任何 `matched_places / matched_species / matched_districts / matched_codes` 且命中 `FOLLOWUP`，判定为 `pronoun = True`。
     - **精准判定 `collection_request`**：
       - 当问题匹配统计/枚举词（`全部|所有|完整|名单|清单|有哪些|哪些|统计|数量|总共|总计|共有|多少[棵株项个种]|几[棵株项个种]`）时：
         - 若属于宽泛导览（`introduction_request` 且不含 `完整|全部|所有|名单|清单|统计|总数`），`collection_request = False`；
         - 若属于延续单树 `focus` 的追问（`pronoun and focus`），`collection_request = False`；
         - 若命中**具体单点院落/古迹地标**（非乡镇级大地点）且不含 `全部|所有|完整|名单|清单|统计|总数`（例如 `"司徒庙的清奇古怪有几棵古柏？"`），`collection_request = False`（读正文答具体株数）；
         - **其余情况（包括 `"东山镇有哪些古树？"`、`"书里种在寺庙里的古树一共有多少项？"`、`"吴中区有多少棵银杏？"`）必须保持 `collection_request = True`，进入 `ROUTER` 进行精确 SQL 统计或口径澄清（`clarify`）！**
- **验收标准**：
  - `"它是怎么保护全村的？"`、`"它在园子什么位置？"`、`"这棵罗汉松有多大年纪了？"`（在 `focus=古松园罗汉松` 下）均准确命中 `pronoun=True`，`route.query` 包含 `"古松园的罗汉松"`。
  - `"书里种在寺庙里的古树一共有多少项？"` 必须进入 `ROUTER`（`collection_request=True`），绝不被误判成单点局部问题跳过 `ROUTER`。
  - `"东山镇有哪些古树？"` 必须进入 `ROUTER` 走 `Knowledge.structured({'location': '东山镇'})` 返回完整条目列表；而 `"司徒庙的清奇古怪一共有几棵古柏？"` 继续走正文阅读（`SELECT -> ANSWER`）。

---

### IMP2-03 [P1] 修复中文概数连写错算、语法量词 `1` 假阳性拒答与汉字年份扩展

- **问题定位**：
  - `chatbot/backend/app/numbers.py`（第 5–42 行）
  - `chatbot/backend/tests/test_improvements.py`（第 22–46 行）
- **实测根因**：
  1. **中文相邻概数被拼成多位整数**：
     - `chinese_number(value)` 第 15 行写有 `if all(c in DIGITS for c in value): return int(''.join(str(DIGITS[c]) for c in value))`。这原本是为了解析年份如 `"一九三九"` → `1939`，但对于现代汉语高频相邻概数：
       - `"一两棵"` → 被算成 **`12`**（原文只有 `1` 或 `2` 时直接触发 `unsupported_number` 假阳性；更危险的是若模型幻觉写出 `"12棵"` 反而在原文含 `"一两棵"` 时被放行！）；
       - `"两三处"` → 被算成 **`23`**；`"三四米"` → **`34`**；`"五六百年"` → `chinese_number('五六百')` 因 `digit` 覆盖只算出 `{600}` 漏掉 `500`！
  2. **语法虚指词 `"一个 / 一处 / 一株 / 百年"` 导致大面积误杀**：
     - 经扫描 `chatbot/data/knowledge/story.db` 全部 138 个子块，**有 46 个子块的 `evidence_numbers` 中根本不包含数字 `1`**！
     - 当模型在生成回答时写出极自然的中文连接语（如 `"这一个故事记录了……"`、`"作为一处古迹"`、`"每一代守护者"`、`"百年古树"`），`QUANTIFIED` 正则抽取出 `1` 或 `100`，在核验阶段直接触发 `unsupported_number` 打回重写甚至整段拒答！
  3. **汉字数码简写年份漏扩展**：
     - `evidence_numbers` 第 40 行 `r'(?<!\d)(\d{2})版'` 只匹配阿拉伯数字 `"04版"` → `{1904, 2004}`，若原文写作 `"〇四版"` 或 `"九八年"` 则未展开。
- **精确改造方案**：
  1. **拆分中文相邻概数（Approximate Adjacent Numerals）**：
     - 保留 4 位纯数字串（如 `一九三九`、`二〇〇四`）按逐位年份转写为 `1939`、`2004`；
     - 对 2 位相邻非零中文数字（匹配 `r'^[一二两三四五六七八九]{2}$'`，如 `一二`、`一两`、`两三`、`三四`、`四五`、`五六`、`六七`、`七八`、`八九`）：**严禁拼成十位数**，应返回两个独立数值集合 `{Decimal(DIGITS[a]), Decimal(DIGITS[b])}`（例如 `"一两棵"` → `{Decimal(1), Decimal(2)}`，`"三四米"` → `{Decimal(3), Decimal(4)}`）！
     - 对带十进制单位的相邻概数（匹配 `r'^([一二两三四五六七八九])([一二两三四五六七八九])([十百千万])$'`，如 `二三百`、`三四十`、`四五千`）：返回 `{Decimal(DIGITS[a] * UNITS[u]), Decimal(DIGITS[b] * UNITS[u])}`（例如 `"二三百年"` → `{Decimal(200), Decimal(300)}`）！
     - 对形如 `"百余年"` / `"百多年"`（单位开头无前导数字，`value == '百'` / `'千'` / `'万'`）仍正常解析为 `100` / `1000` / `10000`。
  2. **区分证据数字提取与草稿硬性校验，消除语法虚指词 `1` / `100` 假阳性**：
     - 在草稿侧提取待校验数字时（`draft_numbers(text)` 或给 `numbers(text, *, strict=True)` 增加模式区分）：
       - 豁免修辞泛指词：无前导系数且无 `余/多/来` 的裸 `"百年"`、`"千年"`（如 `"百年古树"`、`"千年沧桑"` 不校验 `100`/`1000`；但 `"一百年"`、`"三百年"`、`"百余年"`、`"100年"` **必须继续严格校验**）；
       - 豁免语法虚指词 `"一"`：当中文 `"一"` 作为泛指冠词/连接词出现时（如 `"一个"`、`"一处"`、`"一种"`、`"一些"`、`"一代"`、`"一生"`、`"一起"`、`"一旁"`、`"一般"`、`"一直"`、`"一定"`、`"一致"`，以及带指示限定词的 `"这一个/每一处/同一株/另一棵"`，或非强调性的单数冠词 `"一株/一棵"`），在草稿侧不强制要求原文逐字出现 `"一"`（单株与多株的归属核对已由 `VERIFY` 语义蕴含完成，且阿拉伯数字 `"1棵"` / `"1株"` 仍由 `ARABIC` 正常拦截！）；而当中文 `"一"` 修饰精确物理度量与日期编号（如 `"一米"`、`"一岁"`、`"一年"`、`"一月"`、`"一日"`、`"一号"`、`"一石"`、`"一折"`）时，草稿侧仍提取 `Decimal(1)` 校验。
       - 在证据侧 `evidence_numbers(text)` 中，继续提取全部中文 `"一棵/一株/一个/一处"` 的 `Decimal(1)`，确保原文写 `"一棵古银杏"` 时，草稿写 `"1棵古银杏"` 能够合法通过！
  3. **统一汉字数码年份扩展**：
     - 在 `evidence_numbers(text)` 中，先将连续中文数码串（如 `一九三九`、`二〇〇四`、`〇四`）归一化映射出一份阿拉伯数字副本，再执行 `([12]\d{3})(?=年|[.月/-]|版)`（世纪/年代扩展）与 `(?<!\d)(\d{2})版`（`19xx` / `20xx` 扩展），使 `"〇四版"` 与 `"04版"` 等价支持 `2004`。
- **验收标准**：
  - `evidence_numbers("树高三四米，有一两棵分支，历经二三百年")` 包含 `{1, 2, 3, 4, 200, 300}`，且**绝不包含 `12`、`34`**！
  - 原文为 `"银杏树龄三百五十年，枝叶繁茂。"`（不含数字 `1` 与 `100`）时，草稿写 `"这是一株历经沧桑的百年古树，树龄350年，是当地的一个文化符号。"` 校验 **0 错误通过**；而草稿编造 `"树龄100年"`、`"树高1米"`、`"一百年"`、`"共有2棵"`、`"12棵"` 时，**必须精准拦截并返回 `['unsupported_number']`**。
  - 原文含 `"〇四版《横扇镇志》"` 时，草稿写 `"2004版《横扇镇志》"` 校验通过。

---

### IMP2-04 [P1] 加固代理链客户端 IP 溯源、修复内层 Nginx 转发头覆盖与打包洁净度

- **问题定位**：
  - `chatbot/backend/app/networking.py`（第 17–35 行、第 49–56 行）
  - `chatbot/deploy/nginx.conf`（第 1–25 行）
  - `chatbot/scripts/package_release.py`（第 10–18 行）
  - `chatbot/backend/tests/test_improvements.py`
- **实测根因**：
  1. **`X-Forwarded-For` 左端投毒与网关 IP 封禁漏洞（`networking.py:21-33`）**：
     - 当前代码执行 `chain = [str(ip_address(value.strip())) for value in forwarded.split(',')]`。
     - 在真实双层代理拓扑中，外部攻击者发送请求头 `X-Forwarded-For: garbage`；外层网关与内层 Nginx 依次追加真实 IP 后，后端收到的头为：`X-Forwarded-For: garbage, 198.51.100.99, 172.18.0.1`，同时内层 Nginx 设置 `X-Real-IP: 172.18.0.1`（外层网关的内网 IP）。
     - `ip_address('garbage')` 抛出 `ValueError`，导致**整条 `chain` 被清空为 `[]`**！随后代码走到第 31 行 `return str(ip_address(request.headers.get('x-real-ip', '')))`，**未检查该 IP 是否属于 `trusted_proxies`**，直接把外层网关 IP `172.18.0.1` 当成了客户端 IP！
     - 后果：攻击者只需带 `X-Forwarded-For: garbage` 故意输错 10 次密码，即可将外层网关 IP `172.18.0.1` 封禁，瞬间造成后续异常头或无 XFF 流量连坐被锁，或通过切换头部绕过单 IP 限流桶。
  2. **内层 Nginx 覆盖外层 `X-Forwarded-Host` 与 `X-Forwarded-Proto`（`deploy/nginx.conf:18-19`）**：
     - `proxy_set_header X-Forwarded-Host $http_host;` 无条件覆盖了外层反代传来的 `X-Forwarded-Host`；
     - `proxy_set_header X-Forwarded-Proto $scheme;` 在内层 HTTP 8080 容器里恒为 `http`，覆盖了外层 HTTPS 网关传入的 `https`。
  3. **发布包混入 macOS 隐藏元数据文件**：
     - 实测检查 `chatbot/release/wu-tree-chatbot.tar.gz` 发现其中打包进了 `wu-tree-chatbot/backend/.DS_Store`。
- **精确改造方案**：
  1. **修复 `networking.py:client_ip` 为严格“从右向左逐跳解析”**：
     - 当 `trusted(peer, networks)` 为真时，对 `forwarded.split(',')` **从右向左（`reversed`）逐项剥离空白并单独尝试 `ip_address(raw)`**：
       - 若某跳不是合法 IP：说明已越过右侧受信代理链进入左侧客户端伪造区，立即停止扫描 `X-Forwarded-For`；
       - 若是合法 IP 且 `trusted(addr, networks)` 为真：说明这一跳仍是内部受信代理，继续向左扫描上一跳；
       - 若是合法 IP 且 `not trusted(addr, networks)`：**立即返回该规范化 IP 字符串**（完全无视更左侧客户端伪造的 `garbage` 或假 IP）！
     - 若 `X-Forwarded-For` 未能解析出非受信客户端 IP，回退检查 `X-Real-IP` 时，**仅当 `X-Real-IP` 是合法 IP 且 `not trusted(real_ip, networks)` 时才采用**；否则最终回退返回直连 `peer`。
  2. **修复 `deploy/nginx.conf` 保留外层代理转发头**：
     - 在 `nginx.conf` 顶层（`server` 块外）使用 Nginx `map` 指令优先继承上游网关传入的 `X-Forwarded-Proto` 与 `X-Forwarded-Host`，为空时再回退本机 `$scheme` / `$http_host`：
       ```nginx
       map $http_x_forwarded_proto $forwarded_proto {
           default $http_x_forwarded_proto;
           ""      $scheme;
       }
       map $http_x_forwarded_host $forwarded_host {
           default $http_x_forwarded_host;
           ""      $http_host;
       }
       ```
     - 在 `location /api/` 中改为：
       ```nginx
       proxy_set_header X-Forwarded-Proto $forwarded_proto;
       proxy_set_header X-Forwarded-Host $forwarded_host;
       ```
  3. **净化 `scripts/package_release.py`**：
     - 在文件过滤条件中排除 `.DS_Store`、`Thumbs.db` 及 `._*` AppleDouble 隐藏文件（例如 `p.name not in {'.DS_Store', 'Thumbs.db'} and not p.name.startswith('._')`），重新运行脚本更新干净的 `release/wu-tree-chatbot.tar.gz` 与 `SHA256SUMS`。
- **验收标准**：
  - 测试用例：直连 `client=('172.18.0.2', 1234)`，请求头为 `{'X-Forwarded-For': 'garbage!!, 198.51.100.77, 172.18.0.1', 'X-Real-IP': '172.18.0.1'}` 时，`client_ip` **必须精准返回 `'198.51.100.77'`**，绝不回退成 `'172.18.0.1'` 或 `'172.18.0.2'`。
  - 当请求头为 `{'X-Forwarded-For': 'garbage!!, 172.18.0.1', 'X-Real-IP': '172.18.0.1'}`（无任何非受信客户端 IP）时，回退结果为直连 `peer`（`'172.18.0.2'`），不会误采信 `X-Real-IP` 中的内部代理地址或抛异常。
  - `tar -tzf chatbot/release/wu-tree-chatbot.tar.gz | grep -E '\.DS_Store|/\._'` 输出为空。

---

### IMP2-05 [P2] 收窄 `conflict_required` 作用域，恢复宽泛概览（`overview`）的局部段落裁剪能力

- **问题定位**：
  - `chatbot/backend/app/service.py`（第 500–505 行）
  - `chatbot/backend/tests/test_improvements.py`
- **实测根因**：
  - 当前第 500 行写为：
    ```python
    conflict_required = any(f['code'] == 'location_conflict' for card in cards for f in card.get('review_flags', []))
    partial_answer = bool(unsupported and 0 not in unsupported and not conflict_required)
    ```
  - 这里的 `cards` 是本次检索召回并打包进上下文的**全部候选父卡**，而非模型草稿实际引用的卡片，也未区分 `route.intent`。
  - 当用户提问宽泛概览（如 `"给我介绍吴中区一些树"` 或 `"介绍几棵罗汉松的故事"`，`route.intent == 'overview'`）时，只要第 11 篇（含 `location_conflict`）恰好出现在候选 `cards` 中（哪怕模型根本没写第 11 篇，或者第 11 篇写得完全正确而第 3 段另一棵树有轻微瑕疵），`partial_answer` 就会被一刀切置为 `False`，导致原本只需裁掉瑕疵副段落就能正常交付的概览回答整篇被拒答（相比一轮修改前 `overview` 始终支持裁剪发生了功能退化）。
- **精确改造方案**：
  1. **将冲突保留约束精确绑定到实际涉及冲突条目的段落**：
     - 找出所有带 `location_conflict` 标记的 `unit_id` 集合：`conflict_units = {card['unit_id'] for card in cards if any(f['code'] == 'location_conflict' for f in card.get('review_flags', []))}`。
     - 通过 `source_map = {s['id']: s for s in sources}`，找出草稿 `claims` 中实际引用了 `conflict_units` 对应来源的段落下标集合：
       `conflict_claims = {i for i, c in enumerate(claims) if any(source_map[sid]['unit_id'] in conflict_units for sid in c['source_ids'] if sid in source_map)}`。
     - 定义冲突拦截条件 `conflict_blocked`：
       - 若存在冲突段本身未通过核验（`bool(conflict_claims & set(unsupported))`），禁止裁剪（绝不允许把写错或漏写冲突限定的第 11 篇段落静默删掉蒙混过关）；
       - 或者当前为单点聚焦冲突条目的问答（`route.intent != 'overview'` 且草稿引用的全部 `unit_id` 均属于 `conflict_units`），同样禁止裁剪副段，避免将第 11 篇拆在第二段的地点冲突说明裁掉。
     - 将 `partial_answer` 更新为：`partial_answer = bool(unsupported and 0 not in unsupported and not conflict_blocked)`。
- **验收标准**：
  - 新增单元测试：当候选 `cards` 同时包含第 11 篇（带 `location_conflict`）和第 2 篇（三官殿龙柏），在 `overview` 回答中第 0 段正常介绍其他古树（校验通过）、第 1 段另一无关树种出现瑕疵（`unsupported=[1]`，未引用第 11 篇）时，系统正常裁剪第 1 段并返回 `status == 'answered'`、`omitted_claims == [1]`；
  - 当针对第 11 篇单点提问（第 0 段写树貌、第 1 段写地点说明且 `unsupported=[1]`），或 `overview` 中引用第 11 篇的段落本身在 `unsupported` 中时，`partial_answer` 为 `False`，拒绝交付缺失冲突说明的残缺回答。

---

## 4. 施工顺序与统一验收清单（Final Verification Checklist）

建议实现 Agent 按 **`IMP2-01` → `IMP2-02` → `IMP2-03` → `IMP2-04` → `IMP2-05`** 顺序单线程推进，全部完成后依次执行以下验证命令并确认全绿：

1. **受保护资产哈希核验**（确认未触碰只读知识库与基线）：
   ```bash
   cd "/Users/young/项目repo/inf  Wu museum/analysis"
   python3 -I scripts/check_story_pipeline.py
   ```
2. **后端全量单元测试（须在原 120 项基础上新增二轮回归测试并 100% 通过）**：
   ```bash
   cd "/Users/young/项目repo/inf  Wu museum/chatbot"
   .venv/bin/python -m pytest -q
   ```
3. **真实重排分布回放验证（验证 `IMP2-01` 真实早停效果）**：
   - 编写或运行一段本地验证脚本，读取 `chatbot/state/select-ab/20261110-select-ab/retrieval/*.json` 中的 22 组真实 Qwen Rerank 分数分布，统计 `IMP2-01` 门控在 `question` 模式下的命中率，确认单点高置信查询（如三官殿龙柏、木渎罗汉松、司徒庙古柏等）首轮仅需扫描 **40 条（2 个 batch）**，且宽泛查询一次性并发全扫不拆段。
4. **前端格式检查与构建验证**：
   ```bash
   cd "/Users/young/项目repo/inf  Wu museum/chatbot/frontend"
   npm run check:format
   npm run build
   ```
5. **发布包重打包与洁净度验证**：
   ```bash
   cd "/Users/young/项目repo/inf  Wu museum/chatbot"
   .venv/bin/python scripts/package_release.py
   tar -tzf release/wu-tree-chatbot.tar.gz | (! grep -E '\.DS_Store|/\._|\.env$')
   ```
