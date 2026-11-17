# 《树碑立传》RAG 与“树语”问答系统改进规划书（Agent 施工规范）

> **角色说明**：本文档由规划 Agent 输出，供下游实现 Agent 直接按序施工与验收。
> **项目根目录**：`/Users/young/项目repo/inf  Wu museum`
> **基准状态**：`chatbot` 现有 77 项后端单元测试通过；`analysis/pilot` 包含《树碑立传》“树的故事”章节 105 个条目、108 个树/树群项、138 个子块、262 张书内图片；`chatbot/data/knowledge` 为已确认（`confirmed`）的应用只读快照（含 138 条 `qwen3.7-text-embedding` 1024 维真实向量）。

---

## 1. 仓库拓扑与职责边界

| 目录 | 角色与职责 | 读写约束 |
| --- | --- | --- |
| `rag workspace/` | 规则手册（`树的故事父子块规则.md`）、原始 PDF 副本及本规划文档（`improve.md`） | 原始 PDF 只读，严禁修改 |
| `analysis/` | 离线 PDF 版式扫描、父子块切分、Schema 校验与一次性 SQLite 构建流水线 | `pilot/stories.jsonl` 等受 `story_baseline.json` 哈希保护的原件只读；除非执行 IMP-11 离线脚本改造，默认不重跑离线切块 |
| `chatbot/` | 线上 FastAPI 后端、React 19 前端、知识快照导出、向量构建、评测与 Docker/Nginx 部署配置 | 核心施工目录；`chatbot/data/knowledge/story.db` 在运行时通过 `?mode=ro&immutable=1` 只读打开，严禁业务请求写回知识库 |

---

## 2. 全局红线与不可违背约束（Must-Keep Invariants）

下游实现 Agent 在修改任何代码前，必须严格遵守以下既有业务与安全契约：

1. **检索路线红线**：普通问答与概览介绍必须保持 **纯向量召回（`qwen3.7-text-embedding`）+ 可选重排（`qwen3.7-text-rerank`）**。**严禁**重新启用 SQLite `FTS5` / BM25 / RRF 关键词回退，**严禁**在向量检索前用目录（树种/区县列表）预筛选或裁剪候选条目（`test_ordinary_questions_retrieve_original_text_before_any_model` 与删除 `chunks_fts` 表的测试必须继续通过）。
2. **向量资产保护**：当前 `chatbot/data/knowledge/story.db` 内已构建 138 条真实线上向量。除非明确执行全书扩充重建，在线侧改进**不得改变** `encoder_spec`（`version=1`、`context_header + "\n" + text`）的哈希校验格式，不得破坏已发布的 138 条向量有效性。
3. **事实与溯源底线**：
   - 正文标题与叙述为事实依据，索引仅作辅助定位；地点冲突（如第 11 条）须并列呈现，传说、访谈、史载、存疑须保留原有限定词。
   - 资料已确认为 `verified` / `confirmed`，页面和回答中**不得**重新引入“待馆方核实 / 待审核 / 未现场核实”等通用免责声明标签。
   - 模型只能引用服务端提供的 `S1..Sn` 真实 `source_id` 与 `available_images` 中的真实 `image_id`；原文引文、印刷页码、图片 URL 与图注署名必须全部由服务端从本地数据库绑定，绝不采信模型自造文本或外链。
   - “完整名单 / 精确计数”必须走参数化全量 SQL（`Knowledge.structured`），以“树／树群项”为统计口径，绝不用 Top-K 检索结果冒充完整名单。
4. **安全与隐私红线**：
   - 严禁将 `DEEPSEEK_API_KEY`、`EMBEDDING_API_KEY`、`ACCESS_PASSWORD` 写入前端构建变量、服务端日志、审计字段或 `release/wu-tree-chatbot.tar.gz` 发布包。
   - 严禁在日志或异常消息中打印模型 Prompt 正文、请求头或上游 API 原始错误响应体。
   - 项目默认不要求 `git init` 或自动提交（除非用户另行指定）。

---

## 3. 施工阶段与并行分工建议

建议按 **Phase 1（P0）→ Phase 2（P1）→ Phase 3（P2）** 顺序推进。若使用多 Agent 并行施工，请注意文件冲突域：

- **工作流 A（核心对话与检索逻辑）**：集中修改 `chatbot/backend/app/service.py`、`retrieval.py`、`knowledge.py`、`config.py` 及对应 `tests/test_service.py`、`tests/test_knowledge.py`、`tests/test_embeddings.py`（涵盖 IMP-01、IMP-03、IMP-04、IMP-05、IMP-06、IMP-07、IMP-08）。**建议由单个后端 Agent 串行完成，避免 `service.py` 合并冲突。**
- **工作流 B（网关、流式并发、前端与部署）**：修改 `chatbot/backend/app/main.py`、`store.py`、`chatbot/frontend/src/App.tsx`、`api.ts`、`types.ts`、`chatbot/deploy/nginx.conf`、`chatbot/compose.yaml`（涵盖 IMP-02、IMP-09、IMP-10、IMP-12 前端部分）。
- **工作流 C（离线脚本与快照工具）**：修改 `analysis/scripts/build_story_chunks.py`、`load_story_db.py`、`index_story.py` 及 `chatbot/scripts/export_knowledge.py`（涵盖 IMP-11、IMP-12 脚本部分）。

---

## 4. 详细改进任务卡（IMP-01 ~ IMP-12）

### IMP-01 [P0] 修复多轮对话“失忆”与指代消解缺陷

- **涉及文件**：
  - `chatbot/backend/app/service.py`（`ChatService.answer`，约第 188–235、290–330 行）
  - `chatbot/backend/app/main.py`（`send` / `generate`，约第 130–152 行）
  - `chatbot/backend/tests/test_service.py`
- **现状与问题**：
  1. `main.py` 查询并向 `service.answer(question, conversation, history, emit)` 传入了 `history`，但 `service.py` 全程从未使用 `history`。
  2. 多轮指代仅靠句首硬编码正则：`r'^(?:那|那么|请问|所以)?[，,\s]*(它|这棵树|那棵树|这株|这棵)(呢|的|在|还|多|有|是|怎么|什么|哪)'`。
  3. 句中代词（如 `state/select-ab` 实测 100% 失败的 `q02-t2`：“配上它的书内照片，并介绍一下”）、复数代词（如“它们真是邓禹种的吗？”、“这四棵树”）、其他谓语搭配（如“它真有1500岁吗？”）以及中文高频**零主语省略追问**（如“有多大年纪了？”、“为什么叫这个名字？”）全部漏检。
  4. 一旦 `pronoun` 为 `False`，不仅检索词未补充焦点，连传给 `SELECT` 的 `'current_focus': focus if pronoun else None` 都被置为 `None`，导致检索和生成直接漂移到无关树木（如从木渎罗汉松跳到银杏）。
- **改造方案**：
  1. **保留无上下文首轮护栏**：当 `not history and not focus` 时，若问题仅含无法独立指认对象的裸代词（如“它在哪里？”、“它们有多大？”），仍直接返回 `status='clarify'` 且不调用外部模型（保持 `test_unclear_pronoun_requires_clarification_without_model` 通过）；普通首轮问题在向量检索前仍不调用任何 LLM（保持 `test_ordinary_questions_retrieve_original_text_before_any_model` 通过）。
  2. **扩展指代与省略追问检测**：
     - 扩展代词正则覆盖句首/句中代词：`它|它们|这棵|那棵|这株|那株|这几棵|那几棵|这处|那处|该树|此树`；
     - 增加省略主语追问判定：当 `focus` 存在，且当前短问题（例如长度 ≤ 28 字）未出现任何已知区县名、目录树种名、官方编号或新地点特征，但包含属性/故事/配图追问词（如 `多少岁|多大|树龄|在哪|位置|照片|图片|配图|故事|传说|来历|现状|为什么|谁种`）时，判定为延续 `focus` 的追问。
  3. **检索词增补而非目录裁剪**：当命中延续 `focus` 条件时，用 `focus` 的 `{location}的{subject}：{question}` 构造 `route.query` 用于向量检索与重排，但仍保持 `nos=None` 全库检索，不锁死单一条目。
  4. **注入精简多轮历史**：从 `history` 中提取最近最多 3 轮已完成（`status == 'complete'`）的问答摘要（用户原问 + 助手回答前 200 字及引用主体），作为只读对话背景 `recent_history` 传入 `SELECT` 与 `ANSWER` 的输入 JSON 中，同时只要 `focus` 存在就始终向 `SELECT` 传入 `current_focus`（不再受 `if pronoun` 门控限制；提示词中已规定用户明确切换新对象时以新对象为准）。
  5. **防附带提及污染 `focus`**：确保 `answer_scope == 'body_mentions'` 时强制 `focus = None` 且 `clear_focus = True`（即使仅引用了单一父卡，也不得将第 66 条银杏设为杨树追问的 `focus`）。
- **验收标准**：
  - 用例 `["木渎的罗汉松在哪里？", "配上它的书内照片，并介绍一下。"]` 第二轮稳定回答古松园罗汉松，且明确不冒用同条目严家花园广玉兰的照片。
  - 复数追问（如问完司徒庙古柏后问“它们真是邓禹种的吗？”）与省略主语追问（如问完三官殿龙柏后问“具体在什么位置？”）均正确继承对象。
  - 首轮普通提问在向量检索前依旧 0 次 LLM 调用。

### IMP-02 [P0] 修复反向代理下登录限流“一人输错、全员封禁”与会话表膨胀

- **涉及文件**：
  - `chatbot/backend/app/main.py`（`login`、`session`、`headers_and_origin`，约第 63–125 行）
  - `chatbot/backend/app/config.py`
  - `chatbot/backend/app/store.py`
  - `chatbot/deploy/nginx.conf`
  - `chatbot/backend/tests/test_api.py`
- **现状与问题**：
  1. 容器部署架构为 `浏览器 -> (可选外部 HTTPS 网关) -> web (Nginx) -> api (FastAPI)`。`main.py` 登录限流直接使用 `request.client.host`，在容器网络内拿到的始终是 `web` 容器的单一内网 IP。任何一人连续输错 10 次密码，所有用户都会被全局封禁 5 分钟（HTTP 429）。
  2. `limits` 与 `login_limits`（`defaultdict(deque)`）只清理当前访问 key 的过期记录，长期运行下不活跃 key 永不释放。
  3. 当 `ACCESS_PASSWORD` 为空（本地免密模式）时，任何不带 Cookie 的客户端每次请求 `GET /api/session` 都会在 SQLite `sessions` 表新建一条 14 天有效期的会话。
- **改造方案**：
  1. **受信代理客户端 IP 提取**：
     - 在 `Settings` 中新增 `trusted_proxies: list[str] = ['127.0.0.1', '::1', '10.0.0.0/8', '172.16.0.0/12', '192.168.0.0/16']`（可通过环境变量 `TRUSTED_PROXIES` 覆盖）。
     - `nginx.conf` 在 `location /api/` 中同时传递 `proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;` 与 `proxy_set_header X-Forwarded-Proto $scheme;`。
     - `main.py` 实现 `client_ip(request)` 辅助函数：仅当直连 `request.client.host` 属于 `trusted_proxies` 网段时，从右向左解析 `X-Forwarded-For` 中第一个非受信代理 IP（回退到 `X-Real-IP`，再回退到 `request.client.host`）；若直连 IP 不在受信网段，则忽略伪造的转发头，直接取 `request.client.host`。
  2. **有界限流器（Bounded Rate Limiter）**：封装轻量内存滑动窗口限流器，每次写入时淘汰过期窗口，并限制最大键数量（如 4096 个 key，超出时按 LRU 淘汰最旧条目），同时应用于 `login_limits` 与提问 `limits`。
  3. **会话表容量保护**：在 `Store.new_session()` 中，除清理已过期会话外，若总会话数超过上限（如 5000 条）则按最早过期时间裁剪旧记录。
- **验收标准**：
  - 新增单元测试：模拟来自同一受信代理 IP（如 `172.18.0.2`）携带不同 `X-Forwarded-For: 203.0.113.10` 与 `203.0.113.11` 请求 `/api/login`，前者输错 10 次触发 429 时，后者仍可正常登录；模拟非受信直连 IP 伪造 `X-Forwarded-For` 无法绕过限流。

### IMP-03 [P0] 优化检索阅读架构：支持自适应候选截断以支撑全书扩展

- **涉及文件**：
  - `chatbot/backend/app/config.py`
  - `chatbot/backend/app/retrieval.py`（`VectorRetrieval.search`，约第 95–155 行）
  - `chatbot/backend/app/provider.py`
  - `chatbot/backend/app/service.py`
  - `chatbot/backend/tests/test_embeddings.py`、`chatbot/backend/tests/test_service.py`
- **现状与问题**：
  - 当前每次普通提问都将全部 138 个子块送入 Rerank，并无条件拆为 7 批调用 7 次 DeepSeek `SELECT`（每次消耗 ~5.5 万 prompt tokens，P90 延迟近 27s）。
  - “树的故事”仅占全书字数的 27.5%；后续接入其余 72.5% 章节（约 400–600 子块）后，单次提问将触发 20–30 次 `SELECT` 调用，在 `max_model_concurrency=4` 下会严重排队阻塞。
- **改造方案**：
  1. **保留全量向量召回 + Rerank 能力，优化 `SELECT` 阅读窗口策略**：
     - 在 `Settings` 中新增可配置参数：
       - `select_mode: Literal['full', 'adaptive'] = 'adaptive'`
       - `select_min_candidates: int = 40`（自适应模式下至少送审前 40 段，确保如第 66 条杨树这类排在第 24 名的附带提及仍 100% 落入首批窗口）
       - `select_score_threshold: float = 0.05`（Rerank 生效时，高于该相关性分数的候选全部保留送审）
  2. **自适应分层规则（当 `select_mode == 'adaptive'` 且 Rerank 成功应用时）**：
     - 若问题为宽泛概览/跨条目主题（`route.intent == 'overview'`），或前排最高 `rerank_score < 0.35`，或首批（前 `select_min_candidates` 段）经 `SELECT` 后返回空结果（`chunk_ids == []`），则自动继续扫描剩余批次（保证高召回与零误拒）；
     - 若问题为明确单点问题（`route.intent == 'question'`），且 Rerank Top-1 显著高分（如 `>= 0.60`），则先对 `max(select_min_candidates, 满足 rerank_score >= select_score_threshold 的前缀)` 执行 `SELECT`；若已命中有效直接来源（`answer_scope == 'direct'`），则无需对尾部 `rerank_score < 0.01` 的百余段无关文本继续发起 LLM 调用。
     - 当 Rerank 未启用或降级为 `cosine` 时，回退为扫描全部召回候选（最多 `retrieval_candidate_limit`）。
  3. **审计透明化**：在 `retrieval_audit` 中明确记录 `select_mode`、`relevance_checked_count` 与 `early_stopped_batches`，并确保 `test_relevance_check_can_find_later_reranked_body_without_catalog_gate`（可显式传 `select_mode='full'` 或覆盖回退全扫路径）与新增的自适应截断测试双双通过。
  4. **分离并发信号量**：区分全局上游信号量（`max_model_concurrency`，默认可调优至 6–8）与单次请求内部 `SELECT` 批次并发度（每请求最多 4 个并发 batch），防止单用户的多批次占满全局配额饿死其他会话。
- **验收标准**：
  - 明确单点问题（如“三官殿的龙柏有多少岁？”）的 `SELECT` 模型调用从 7 次降至 2 次（40 段），延迟与 Token 消耗显著下降；
  - 附带记载问题（如“介绍古杨树的故事”，Rerank 约第 24 名）和全书主题综合问题仍 100% 召回，不丢证据。

### IMP-04 [P1] 对齐 `search_terms` 索引地点别名与结构化统计、目录及查询解析

- **涉及文件**：
  - `chatbot/backend/app/knowledge.py`（`__init__`、`public_card`、`catalog`、`structured`）
  - `chatbot/backend/app/service.py`
  - `chatbot/backend/tests/test_knowledge.py`、`chatbot/backend/tests/test_service.py`
- **现状与问题**：
  - 离线切块将索引页独有的镇/山名前缀写入了 `chunk['search_terms']`（如第 24 条 `瑶盛村瑶盛港水东浜` 的 `search_terms` 含 `甪直镇瑶盛村瑶盛港水东浜`，第 29 条 `海云院` 含 `穹窿山海云院`），而正文与 `items.location` 中均不含“甪直”或“穹窿山”。
  - 移除 FTS5 改纯向量后，`search_terms` 未进向量，`Knowledge.structured()` 也仅用 `instr(i.location, ?)` 匹配 `items.location`，导致“列出甪直镇所有古树”漏掉第 24 条，搜“穹窿山”缺少精确锚点。
- **改造方案（无需重算现有 138 条向量）**：
  1. **`Knowledge` 加载期聚合别名**：在 `Knowledge.__init__` 读取 `chunks` 表时，提取每个 `(unit_id, item_no)` 关联子块的 `search_terms`（剔除已在 `official_codes` 中的编号项），构建内存映射 `item_aliases[(unit_id, item_no)]` 与条目级 `entry_aliases[entry_no]`。
  2. **修复 `Knowledge.structured()` 地点过滤**：当 `filters.get('location')` 存在时，在取出满足 `district / species / status` 的 `items` 后，同时匹配 `filters['location'] in item['location']` 或 `any(filters['location'] in alias for alias in item_aliases[(unit_id, item_no)])`。
  3. **修复目录检索与公开卡片元数据**：在 `public_card()` 中导出 `official_codes` 与 `location_aliases`；在 `Knowledge.catalog()` 中将 `search_terms` 别名一并纳入关键字匹配。
  4. **在线查询别名补充**：在 `ChatService.answer()` 中，若用户问题出现某个条目的独有索引地点别名（如“穹窿山海云院”或“甪直”+对应村名），在 `resolved_question`（`route.query`）中增补对应正文标准地点（如“海云院”），不篡改原 `question`。
- **验收标准**：
  - `Knowledge.structured({'location': '甪直'})` 完整返回第 24 条与第 25 条（共 2 项），不再遗漏第 24 条；
  - 现有 138 条向量与 `manifest.json` 哈希校验无需重建即可直接运行。

### IMP-05 [P1] 修复区县准入逻辑 `supports_requested_district` 的简称与括号全称漏洞

- **涉及文件**：
  - `chatbot/backend/app/service.py`（`supports_requested_district`，第 77–86 行，及结构化区县过滤第 244–252 行）
  - `chatbot/backend/tests/test_service.py`
- **现状与问题**：
  - 数据库存储的 10 个区县为：`['吴中区', '姑苏区', '吴江区', '虎丘区（高新区）', '相城区', '工业园区', '昆山市', '常熟市', '太仓市', '张家港市']`。
  - `supports_requested_district` 仅用 `d in question` 和 `d in candidate['text']` 做全称匹配：
    1. 用户问“吴江古杨树的故事”、“常熟的古树”、“高新区有哪些树”时，`requested` 为空列表，区县约束完全失效；
    2. 用户问“吴江区有什么杨树传说”时，若跨区条目的正文按自然习惯写“吴江震泽”而未带“区”字，`any('吴江区' in text)` 为 `False`，真实跨区证据被误杀。
- **改造方案**：
  1. 建立标准区县别名映射表 `DISTRICT_ALIASES`：
     - 普通区市映射全称与去后缀简称（如 `'吴中区': ('吴中区', '吴中')`，`'常熟市': ('常熟市', '常熟')`）；
     - 复合名称单独展开：`'虎丘区（高新区）': ('虎丘区（高新区）', '虎丘区', '高新区', '苏州高新区')`（避免裸词“虎丘”与姑苏区“虎丘山”景点混淆），`'工业园区': ('工业园区', '苏州工业园区')`。
  2. **问题端识别**：匹配问题中提及的目标标准区县集合 `requested_districts`（保留 `以外|之外|除了|不在|不是|不属于|并非` 否定豁免，并支持“吴中区和吴江区”多区并集）。
  3. **证据端核验**：只要 `candidate['district'] in requested_districts`，或者候选块的 `context_header + '\n' + text` 包含目标区县的任一规范名称或简称（如 `'吴江'`），即判定支持。
  4. 同步将 `DISTRICT_ALIASES` 复用于结构化路由（第 245 行）对模型返回区县简称的归一化（例如模型返回 `'高新区'` 自动归一化为 `'虎丘区（高新区）'`）。
- **验收标准**：
  - “介绍吴中古杨树的故事”（不带“区”字）被正确识别为限定吴中区，不会把吴江区第 66 条双杨村当作吴中区答案；
  - “介绍高新区一些古树”能正确匹配 `虎丘区（高新区）` 条目；
  - 跨区正文中写“吴江”而问题写“吴江区”时，证据不再被误删。

### IMP-06 [P1] 收紧全库统计路由触发条件，优化核查重试配额与段落级裁剪策略

- **涉及文件**：
  - `chatbot/backend/app/service.py`（第 207–260 行、第 322–400 行）
  - `chatbot/backend/tests/test_service.py`
- **现状与问题**：
  1. `collection_request` 正则（`共有|多少[棵株项个种]|几[棵株项个种]|有哪些|哪些`）会把针对**具体地点/单一条目**的事实问题（如“司徒庙清奇古怪一共有几棵树？”、“雕花楼里有哪些古树？”）误送进 `ROUTER`；而 `ROUTER` 遇到“几棵”单株口径会返回 `clarify` 拒答，或按 `items` 表统计为 1 项（司徒庙圆柏在 `items` 表仅占 1 个树群项，正文实为 4 棵）。
  2. `for attempt in range(2)` 将“JSON Schema / 无效 `source_id` 格式修复”与“语义 `VERIFY` / 数字核查修复”混用同一次循环配额。一旦首次因小格式错误消耗重试，后续核查发现轻微瑕疵便直接失败（见 `state/select-ab` 报告 `r2-q11-B-t1`）。
  3. 核查后处理中，仅 `route.intent == 'overview'` 或 `body_mentions` 允许剔除 `unsupported` 段落保留其余有效段落；当 `route.intent == 'question'` 时，即使前两段已完整准确回答核心问题，只要末尾补充段被判偏题（见 `r2-q18-B-t1` 马尾松案例），整条回答就会被全部丢弃返回 `insufficient`。
- **改造方案**：
  1. **精确化 `ROUTER` 分流规则**：
     - 在 `ROUTER` 系统提示词与后置校验中明确：若用户问题限定在某一具体寺庙/园子/村落/古树个体询问棵数或树种（如“司徒庙有几棵柏树”），`ROUTER` 必须返回 `intent="question"` 交由正文阅读作答；仅当面向**全书或整个区县/某类状态/某树种全集**要求完整清单或总数统计时，才返回 `list` / `count` / `clarify`。
     - 扩展 `introduction_request` 正则覆盖 `有哪些.*故事|有什么.*故事|讲讲|说说|聊聊`。
  2. **拆分格式修复与实质修订配额**：
     - 允许最多 1 次轻量 Schema / `invalid_source` 格式修正，**不占用** `VERIFY` 语义与数字核查后的 1 次实质内容修订（`REVISE`）机会（总生成上限控制在最多 3 次以内，防止死循环）。
  3. **改进段落裁剪策略**：
     - 修订重试后，若仍有部分段落位于 `unsupported`，只要**第 0 段（首段核心回应）通过核查**且剩余有效段落数 `>= 1`、同时不违反冲突并列披露要求，允许剔除 `unsupported` 附属段落并记录到 `validation['omitted_claims']`，而不是将整篇合格回答直接清零为 `insufficient`。
- **验收标准**：
  - 提问“司徒庙的清奇古怪一共有几棵古柏？”走正文检索回答“四棵”，不再触发单株统计拒答；
  - 格式修正一次后仍可正常触发一次 `VERIFY` 语义修订；核心段合格而末段冗余偏题时可安全裁剪末段并返回有效回答。

### IMP-07 [P1] 升级数字校验 `validate_claims`：兼容中文数词、年代换算与简写年份

- **涉及文件**：
  - `chatbot/backend/app/service.py`（`validate_claims`，约第 89–113 行）
  - `chatbot/backend/tests/test_knowledge.py`、`chatbot/backend/tests/test_service.py`
- **现状与问题**：
  - 现有逻辑仅用 `re.findall(r'\d+(?:\.\d+)?', ...)` 提取阿拉伯数字做集合包含判断。
  - 原文写“三百五十年”（第 96 条）、“四棵”（第 18 条）、“两株”（第 66 条），模型若写成 `350年`、`4棵`、`2株` 会被误判为 `unsupported_number`；
  - 原文写简写年份 `04版《横扇镇志》`（第 62 条），模型写成 `2004版` 会被误判；原文写 `1939年`，模型写 `20世纪30年代` 也会被误判；
  - 反之，若模型凭空捏造中文数字（如“树龄八百余年”而原文是“五百余年”），现有校验完全漏检。
- **改造方案**：
  1. 实现确定性的中文数词解析器（覆盖 `零一二两三四五六七八九十百千万` 组合，如 `三百五十 -> 350`、`十五 -> 15`、`两 -> 2`，注意排除专名或成语虚指干扰，如 `三官殿`、`四都村`、`五台山`、`百花公主`、`万斛明珠` 等）。
  2. 对 `evidence`（原文 + `context_header` + `as_of`）同时提取：
     - 原始阿拉伯数字集合；
     - 原文中出现的中文数词转换后的数值集合；
     - 从 4 位年份（如 `1939`、`1569`）自动派生的合法世纪/年代数值（如 `20` 世纪、`30` 年代、`16` 世纪），以及对原文 `(\d{2})版` 简写年份自动派生 `20xx` / `19xx` 候选。
  3. 对 `claim` 中的阿拉伯数字与“带明确量词（年、岁、米、株、棵、天、石、折、代、月、日、号）的中文数字”统一校验其数值是否落在 `evidence` 允许集合内（保留对 `official_codes` 完整编号的豁免，继续拦截 `吴中2039` 或把编号 `203` 写成 `203年` 的情况，确保 `test_citations_bind_original_text_and_reject_invented_ids_and_numbers` 全部断言通过）。
- **验收标准**：
  - 原文为“三百五十年”时，回答写“350年”与“三百五十年”均通过校验，写“450年”或“四百五十年”均被拦截为 `unsupported_number`；
  - 原文为“04版《横扇镇志》”时，回答写“2004版”或“04版”均通过校验；
  - `test_knowledge.py` 既有数字与官方编号防篡改测试 100% 通过。

### IMP-08 [P1] 修复官方编号解析直接覆盖原始 `question` 的问题

- **涉及文件**：
  - `chatbot/backend/app/service.py`（约第 279–288 行）
  - `chatbot/backend/tests/test_service.py`
- **现状与问题**：
  - 当问题包含官方编号（如 `吴中203`、`吴中186`）时，现有代码执行 `answer_question = ''.join(question.split())` 删除全部空白字符，并把 `answer_question` 中的编号直接替换为 `{location}的{title}`，随后把被篡改的 `answer_question` 作为 `question` 字段发给 `SELECT`、`ANSWER` 与 `VERIFY`。
  - 若用户问“吴中186和吴中187都在哪里？”或“吴中203是哪棵树的编号？”，模型看到的 `question` 变成了“光福司徒庙的圆柏和光福司徒庙的圆柏都在哪里？”，丢失了用户询问编号本身的意图。
- **改造方案**：
  - 保留用户原始 `question` 原封不动传给 `SELECT`、`ANSWER`、`VERIFY` 的 `question` 字段（仅 `.strip()`，不删内部空格，不替换文本）；
  - 将官方编号映射结果组装为解析文本写入 `route.query`（即 `resolved_question`），例如：`f"{question}（编号对应：{mapping_desc}）"`，用于增强向量检索、Rerank 与模型消歧。
- **验收标准**：
  - 提问“吴中203是什么树？”与“吴中186和吴中187是同一处吗？”时，`SELECT` / `ANSWER` 收到的 `question` 仍包含原始编号字样，`resolved_question` 包含对应地点与树种。

### IMP-09 [P2] 重构 SSE 事件通知机制、消除前端双重轮询竞态并支持中止生成

- **涉及文件**：
  - `chatbot/backend/app/main.py`（`jobs`、`queues`、`generate`、`send`，约第 50–185 行）
  - `chatbot/backend/app/store.py`
  - `chatbot/frontend/src/App.tsx`、`chatbot/frontend/src/api.ts`
  - `chatbot/backend/tests/test_api.py`
- **现状与问题**：
  1. 后端 `stream()` 每 `0.25s` 在异步协程中同步调用 `store.message(mid)` 打开并关闭一次 SQLite 连接，15 秒的回答产生 60 次同步查库与 JSON 反序列化，阻塞事件循环。
  2. 前端 `App.tsx`（第 172–192 行）在 SSE 流正常连接时，仍同时运行每 4 秒一次的 `GET /conversations/${cid}/messages` 轮询；且 `send()` 第 296 行流结束后未检查 `active.current === id` 就直接 `setMessages(...)`，若轮询先将 `busy` 置为 `false`、用户立即切换或新建会话，旧会话消息会串台覆盖新会话。
  3. 用户发错问题或遇到上游慢响应时无法停止生成，整个对话处于 `processing` 锁定状态无法继续提问或删除。
- **改造方案**：
  1. **后端事件驱动替代高频查库**：为每个运行中的 `mid` 维护内存状态对象（包含 `stage: str`、变更通知 `asyncio.Condition` 或 `asyncio.Event`）。`emit(stage)` 更新 `stage` 并唤醒监听者；`generate()` 结束写入 `store.finish()` 后唤醒完成状态。SSE `stream()` 通过 `asyncio.wait_for(..., timeout=5.0)` 等待状态变更或发送 `: heartbeat`，仅在连接初始化时和任务完成时读取 `store.message(mid)`。
  2. **新增取消接口**：增加 `POST /api/conversations/{cid}/cancel`（校验 `owner` 归属），调用对应 `jobs[mid].cancel()`，在取消分支将消息更新为“已停止本次回答，可重新提问”，释放会话锁；前端在 `busy` 状态下提供“停止”按钮。
  3. **消除前端双重轮询与串台竞态**：
     - 新增 `streamingRef` 标记当前是否有活跃 SSE 流；`4000ms` 轮询定时器**仅在 `busy && !streamingRef.current`（即刷新页面恢复等待或 SSE 断线重连态）**时启动，正常流式接收期间不发冗余轮询请求。
     - `send()` 中所有异步回调更新 `messages` / `sources` / `cards` 前，严格检查 `active.current === id`。
- **验收标准**：
  - 正常一次问答期间，前端 Network 面板只有 1 条 `/messages` SSE 流，不再每 4 秒伴随额外 GET 请求；
  - 生成途中点击停止，后台任务在 1 秒内取消并释放对话锁，可立即发送新问题。

### IMP-10 [P2] 加固 Docker / Nginx 生产部署配置与同源 Origin 校验兼容性

- **涉及文件**：
  - `chatbot/deploy/nginx.conf`
  - `chatbot/compose.yaml`
  - `chatbot/.env.server.example`
  - `chatbot/backend/app/config.py`
  - `chatbot/backend/app/main.py`（`headers_and_origin`，第 63–70 行）
- **现状与问题**：
  1. `nginx.conf` 写死 `proxy_pass http://api:8000;`。Docker 环境下若 `api` 容器单独重启更换了容器 IP，Nginx 缓存旧 IP 不重新解析，将持续返回 `502 Bad Gateway`。
  2. `compose.yaml` 与 `.env.server.example` 漏掉了 `MODEL_TIMEOUT` 与 `MAX_MODEL_CONCURRENCY` 环境变量映射，生产容器无法通过 `.env.server` 调整模型超时和并发上限。
  3. `main.py` 校验 `urlparse(origin).netloc != request.headers.get('host')`。若服务器前置 HTTPS 反向代理使用了 `proxy_set_header Host $host;`（不含非标端口）或独立域名转发，会导致所有 POST/DELETE 请求被 `403 请求来源不匹配` 误拦。
- **改造方案**：
  1. **Nginx 动态 DNS 解析**：在 `nginx.conf` 的 `server` 块中加入 Docker 内置 DNS `resolver 127.0.0.11 valid=10s ipv6=off;`，在 `location /api/` 中使用变量 `set $api_upstream http://api:8000; proxy_pass $api_upstream;`（不带尾部斜杠，确保原始 `/api/...` URI 完整透传）。
  2. **补齐生产环境变量透传**：在 `compose.yaml` 的 `api.environment` 和 `.env.server.example` 中补齐 `MODEL_TIMEOUT: ${MODEL_TIMEOUT:-90}`、`MAX_MODEL_CONCURRENCY: ${MAX_MODEL_CONCURRENCY:-6}`、`TRUSTED_PROXIES` 与 `ALLOWED_ORIGINS`。
  3. **增强 Origin 允许逻辑**：在 `Settings` 新增可选 `allowed_origins: list[str] = []`；在 `headers_and_origin` 中：
     - 若配置了 `allowed_origins`，允许 `origin` 精确匹配白名单；
     - 默认同源比对时，同时兼容 `Host` 与受信代理透传的 `X-Forwarded-Host`。
- **验收标准**：
  - 配置检查确认 `compose.yaml` 包含超时、并发及代理白名单变量；
  - 单元测试覆盖 `ALLOWED_ORIGINS` 白名单与 `X-Forwarded-Host` 同源放行及跨站拦截。

### IMP-11 [P2] 解耦离线构建脚本中的特例硬编码，为全书其余章节扩充铺路

- **涉及文件**：
  - `analysis/scripts/build_story_chunks.py`（约第 279 行、第 286 行、第 320 行）
  - `analysis/scripts/load_story_db.py`（约第 62 行）
  - `analysis/scripts/index_story.py`（约第 143 行）
- **现状与问题**：
  1. `build_story_chunks.py` 第 286 行在通用循环的 `location_conflict` 分支中硬编码了第 11 条专属文案：`f"正文标题地点：{entry['location']}；索引地点：{alias}；正文叙述也提到灵源古寺。..."`。一旦任何其他条目触发地点不一致，都会被错误打上“灵源古寺”文案。
  2. `build_story_chunks.py` 第 279 行将所有侧栏小字块（`interview` / `fieldnote` / `background`）无条件挂在 `items[0]`（`item_no=1`）下，未支持多树项条目的侧栏树项归属覆盖。
  3. `load_story_db.py`、`build_story_chunks.py`、`index_story.py` 在主流程中硬编码断言了精确结构常数（`entries=105, items=108, chunks=138, images=262`），后续调整任意单条切分或扩充《树碑立传》其余章节（古树与艺术、志愿者走访、研究专栏）时脚本会直接抛异常中止。
- **改造方案**：
  1. 将第 11 条“正文叙述也提到灵源古寺”移入 `OVERRIDES[11]["location_conflict_detail"]`，通用分支仅生成客观的 `正文标题地点：{entry['location']}；索引地点：{alias}`。
  2. 支持在 `OVERRIDES` 中配置侧栏到具体 `item_no` 的映射（默认仍为 `1`，保证当前 105 条输出字节级不变）。
  3. 为 `load_story_db.py` 等脚本增加 `--no-strict-counts` 或外部基线配置参数（默认仍严格校验“树的故事”章节基线常数，开启扩展模式时校验外键完整性、非空性与哈希一致性）。
- **验收标准**：
  - 在默认参数下运行 `analysis/scripts/check_story_pipeline.py`，生成的 `entries_story.jsonl` 与 `chunks_story.jsonl` 与现有基线 SHA-256 **100% 字节级一致**，不产生任何意外数据漂移。

### IMP-12 [P2] 完善前端古树目录搜索体验、知识导出向量复用与状态同步

- **涉及文件**：
  - `chatbot/frontend/src/App.tsx`（目录过滤与反馈状态，约第 340–375 行、第 900–960 行）
  - `chatbot/frontend/src/types.ts`
  - `chatbot/scripts/export_knowledge.py`
- **现状与问题**：
  1. 前端古树目录搜索仅在本地拼接 `${c.location}${c.title}${c.species.join("")}` 做区分大小写的 `.includes(catalogQuery)` 匹配：不支持搜索官方编号（如 `吴中203`、`吴中009`），不支持搜索索引页地点别名（如 `甪直`、`穹窿山`），没有树种下拉筛选（后端 `/api/catalog` 已返回 `species` 列表但前端丢弃未用），且输入首尾空格或大小写会导致搜不到。
  2. 前端对某条回答先提交“差评 + 备注”，再改点“好评（ThumbsUp）”时，本地 `messages` state 只更新了 `rating: "good"`，未清空本地缓存的 `reason` 和 `note`（与后端数据库已清空的行为不一致）。
  3. `chatbot/scripts/export_knowledge.py` 每次从 `analysis/pilot/story.db` 导出快照时直接整库覆盖 `chatbot/data/knowledge/story.db`，会把已构建好的 `chunk_vectors` 与 `meta['embedding_index']` 清空；若仅更新了非正文的卡片元数据或图片说明，在没有 `state/embedding-build.sqlite` 的机器上会被迫重新调用百炼 API 全量向量化。
- **改造方案**：
  1. **前端目录增强**：在 `Card` 类型接收 `official_codes?: string[]` 与 `location_aliases?: string[]`；目录过滤对 `catalogQuery.trim().toLowerCase()` 匹配标题、地点、地点别名、树种及官方编号，并在区县筛选旁增加“全部树种”下拉筛选。
  2. **反馈状态同步**：`rate(message, "good")` 成功后，同步将本地该消息的 `reason` 和 `note` 置空。
  3. **`export_knowledge.py` 增量保留有效向量**：导出覆盖前，先从旧目标库 `data/knowledge/story.db`（若存在）读取 `chunk_vectors` 与 `meta['embedding_index']`；备份并执行 `confirm_metadata` 后，逐条核对新库 `chunks` 的 `chunk_id` 与 `content_hash`（`context_header + "\n" + text` 未变），若全部匹配则自动迁回原有向量与 `embedding` manifest 元数据并打印复用报告；若有子块正文变更则仅保留未变更向量并提示运行 `build_embeddings.py` 补齐增量。
- **验收标准**：
  - 在前端古树目录搜索 `吴中203`、空白包裹的 ` 银杏 `、`穹窿山`、`甪直` 均能准确筛出对应古树卡片；
  - 执行 `export_knowledge.py` 后，在正文未变的情况下 `data/knowledge/story.db` 中的 138 条向量与 `VectorIndex` 就绪状态完整保留，无需重调外部 API。

---

## 5. 最终交付与统一验收清单（Final Verification Checklist）

下游实现 Agent 完成修改后，必须依次执行并通过以下检查，方可宣告完工：

1. **离线管线无损检查**（若修改了 `analysis/`）：
   ```bash
   cd "../analysis"
   python3 -I scripts/check_story_pipeline.py
   ```
2. **后端全量单元测试（无外部网络调用）**：
   ```bash
   cd "../chatbot"
   .venv/bin/python -m pytest -q
   ```
   - 原有 77 项测试及本次针对 IMP-01 ~ IMP-12 新增的单元测试必须全部通过，无失败、无密钥泄露告警。
3. **前端类型检查、格式检查与生产构建**：
   ```bash
   cd "../chatbot/frontend"
   npm run check:format
   npm run build
   ```
4. **线上真实模型回归抽检（若本地 `.env` 配置了有效测试密钥）**：
   ```bash
   cd "../chatbot"
   .venv/bin/python scripts/live_eval.py --only sanguan_age
   ```
   - 重点加测多轮追问（如木渎罗汉松配图追问、司徒庙复数代词追问）、区县简称正反例（“介绍吴中古杨树” vs “吴江杨树传说”）、单条目棵数提问（“司徒庙清奇古怪有几棵”）。
5. **发布包安全审计**（若重新打包）：
   ```bash
   cd "../chatbot"
   .venv/bin/python scripts/package_release.py
   ```
   - 确认输出无 `.env`、无密钥明文、无 `state/` 会话数据混入。
