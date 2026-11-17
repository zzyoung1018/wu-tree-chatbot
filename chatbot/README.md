# 树语 · 吴中古树问答

面向项目组内部验证的响应式网页。依据《树碑立传》“树的故事”正文回答问题，可以查看原文、印刷页码、相关古树图片，并保存对话和反馈。当前知识快照包含 105 个条目、108 个树／树群项、138 个子块、262 张书内图片。

前后端独立，使用同一域名访问。开发机运行 React + FastAPI，服务器运行 Nginx + FastAPI；同一份源码和知识快照可直接迁移，不依赖原项目的绝对路径，也不需要把 PDF 放到服务器。

## V5 修改与译文审校（2026-11-17）

V5 已实现并完成验收执行，真实端到端仍有报告中的失败。默认 `RERANK_ENRICHMENT=off`（保留rerank，去掉富化头）：冻结V4留出13个事实题各臂hit@1均13/13，lean/full净收益−1/0，未达到≥3门槛；off的20题rerank用量比full少32.07%。`THEME_POOL=off`，主题池未通过收益门槛。保留候选上限与原向量。

已加入35目录树种的一对多分组、SIP/SND大小写约束、15个有原文证据的主题、613条中英追问对象与会话去重。清册泛称按组并集计数、显示成员明细；模型只能选择本地追问ID。英文VERIFY以中文原文为唯一事实权威，三例真实模型译文变异测试通过。启动缺少sidecar默认失败。

逐段核对105条目、138块，279处编辑合并199字段补丁；138块ai_reviewed、0块human_approved。**这是AI逐块审校，非人工签审。** 数字/编号QA无硬错误，76条启发式复查说明保留；真人签审表在 `state/improve-v5/translation-review.csv`。V4旧追问补丁指向legacy归档，当前追问从证据对象派生。原中文地点/年代矛盾未擅自改写。

用户授权同一代理兼任Planner，补齐留出25题与封存12题，**均非独立盲测**。原始自动分数：留出V4 17/25→V5 20/25；中文zh27 23/27→26/27；v4_8 8/8→8/8；封存V4 10/12、V5 12/12，各跑一次。出题规范名称和关键词断言缺陷、各失败原因均列于报告；不以校正后的分数替代原分数。C首跑发现矛盾计划错误计数，修复后有理由地重跑60题，原结果保留。

208项后端测试、前端格式与构建、桌面/手机6次追问去重、原始数据流水线均通过。全量追问向量检查最终613/613；30条真实抽样首轮29/30，修复校园计数路由后仅失败题复测1/1。全部记录和账面用量见 `state/improve-v5/report.md`。

```bash
# 在chatbot目录执行；以下构建/检查默认不调用模型
.venv/bin/python scripts/build_enrichment.py
.venv/bin/python scripts/build_enrichment.py --check
.venv/bin/python scripts/build_interactions.py  # 保留并校验已编辑的元数据
.venv/bin/python scripts/translation_qa.py
.venv/bin/python scripts/export_translation_review.py
# 实际审阅者填写decision、reviewer、review_date、comments后导入
.venv/bin/python scripts/export_translation_review.py --import /path/to/reviewed.csv
```

`interaction_metadata.json`与译文修订清单均绑定源库；修改后重新构建，补丁失配或证据不精确即失败。重新生成互动元数据须显式 `build_interactions.py --force`，会调用模型并计入预算。评测只在明确的新版本/新协议下执行，不重写已有结果。

## V4 实现背景（历史说明，当前行为以V5为准）

`data/knowledge/glossary.json` 维护全库 35 个目录树种及补充俗名、10 个区县、地标、历史专名和主题锚点；`enrichment.json` 保存 105 条目、108 树项、138 子块和 262 图片的静态双语镜像。地点层级、附带树种、看点和追问在离线阶段计算。启动时校验完整覆盖、源库 SHA-256 和各子块原文哈希；资产不一致时拒绝启动。旧测试夹具缺少 sidecar 时才使用内存兼容构建。中文正文、原始 analysis 数据及 138 条既有向量不改写。

中英文输入通过同一套中文内部主键查询。`UNDERSTAND` 结合词表提示确定 `lang`、主题、地点和模式；主题导览不会丢掉“造型奇特／死而复生”等条件后变成全区清册。Qwen rerank 文档附带主题、看点和合成检索短语，文档向量的输入哈希不变。英文卡片和来源来自离线镜像，来源另含 `text_zh`；模型不能生成或替换引文、图注、页码。图片匹配和对话焦点始终使用中文树种主键。回答附带最多三条已引用卡片的推荐追问。

前端右上角 `中 / EN` 切换界面，偏好保存在 `tree-chat-lang`，目录随语言刷新。回答语言默认跟随问题，编号输入跟随界面偏好。API 向后兼容：消息体新增 `lang: "zh" | "en"`（默认 `zh`）；`/api/catalog` 与 `/api/entries/{no}` 支持 `?lang=en`；回答新增 `lang`、`suggested_questions`，卡片新增 `hook`、`followups`。历史回答保持生成时的语言。

```bash
# 在 chatbot 目录执行；默认构建不调用模型。
.venv/bin/python scripts/build_enrichment.py
.venv/bin/python scripts/build_enrichment.py --check
# 仅在需要刷新离线译文时显式调用已配置的 DeepSeek。
.venv/bin/python scripts/build_enrichment.py --refresh-llm --force --entries 18,23
# 逐条对照原文检查译文、限定词、历史年代和简短追问；断点可续。
.venv/bin/python scripts/build_enrichment.py --review-llm
```

离线生成和复核只提交书中文字、元数据与词表，不提交图片或用户对话。检查点写入 `state/enrichment-v4`，所有条目通过结构与覆盖校验后才原子发布 `enrichment.json`。译文经过模型复核和编辑抽查，不标记为已获人工逐条签审。原文若包含矛盾，译文和看点保留差异；例如第 71 条的标题地点与正文地点不合并。规划示例中的孩儿莲编号已按真实语料使用第 16 条，第 37 条为沧浪亭枫杨。

V4 验证入口：`backend/tests/test_bilingual.py`；真实接口评测 `scripts/live_eval_v4.py`；桌面和手机交互回归 `scripts/browser_bilingual.mjs`（先在 8124 端口启动 `backend/tests/browser_app.py` 的 `create_test_app` 工厂，也可用 `TEST_BASE_URL` 指定隔离测试服务）。V4 历史输出位于 `state/improve-v4/`，当前浏览器输出位于 `state/improve-v5/browser/`，均不进入发布包。发布包会包含静态 sidecar、修订清单与离线构建/审校脚本。

## 本地运行

要求 Python 3.13、Node.js 24。也可以使用兼容 Vite 的其他 Node 版本。

```bash
# 在 chatbot 目录执行；已有本地 .env 时不要覆盖。
python3 -m venv .venv
.venv/bin/pip install -r backend/requirements-dev.txt
cd frontend
npm ci
cd ..
cp .env.example .env
chmod 600 .env
# 编辑 .env，填写 DEEPSEEK_API_KEY、EMBEDDING_API_KEY 与 EMBEDDING_BASE_URL
# 若使用附带的向量快照，模型、维度与 API Host 应与建库配置一致。
bash scripts/dev.sh
```

网页：<http://127.0.0.1:5173>；健康检查：<http://127.0.0.1:8000/api/health>；开发环境 API 文档：<http://127.0.0.1:8000/api/docs>。关闭启动终端或按 Ctrl+C 会停止两个服务。接口和前端依赖已分别锁定在 requirements.txt 与 package-lock.json。

本地默认只绑定回环地址。手机同网段测试时，先在 `.env` 设置至少 16 位的 `ACCESS_PASSWORD`，重启后端，再以 `npm run dev -- --host 0.0.0.0` 启动前端，使用电脑的局域网地址访问。浏览器统一请求相对路径 `/api`，后端无需向局域网暴露端口。

实际密钥保存在本机 `.env` 或服务器 `.env.server`，文件权限应为 0600，并且不进入 Git。示例配置不包含密钥。前端没有密钥，不需要任何 `VITE_*` 密钥变量。修改密钥或模型后重启后端生效。

## 架构与目录

```text
浏览器 React / TypeScript
    │ 同源 /api，HttpOnly 会话 Cookie
    ▼
Vite 代理（开发）或 Nginx（服务器）
    ▼
FastAPI
    ├─ 纯问候 → 本地欢迎语（零模型调用）
    ├─ UNDERSTAND：问题 + 最近三轮摘要 + 当前焦点 → 四种模式与独立检索句
    │    ├─ clarify → 温和澄清
    │    ├─ inventory → 全量参数化 SQL → 完整名单与树／树群项计数
    │    └─ qa / explore
    │         → Qwen Embedding + Rerank（零阅读模型调用）
    │         → 区县后验过滤 → 精选 8 / 6 个父条目，必要时补召附带提及
    │         → ANSWER 图文生成 → 引用／图片软绑定 → Minimal VERIFY
    └─ 保留合格段落；核心硬伤最多修订一次，仍不足时附上原文导读
         ├─ data/knowledge/story.db  只读知识库
         ├─ data/knowledge/images/  原书图片
         └─ state/app.sqlite        对话、会话、反馈及回答审计
```

| 路径 | 职责 |
| --- | --- |
| `backend/app/main.py` | HTTP、身份会话、SSE、任务生命周期、限流 |
| `backend/app/service.py` | 统一理解与改写、图文生成、最小事实护栏、优雅降级 |
| `backend/app/knowledge.py` | 只读资料、目录、全量查询、来源映射 |
| `backend/app/context.py` | 父卡与子块的原文、标记和来源渲染 |
| `backend/app/provider.py` | DeepSeek 适配层、超时、错误处理 |
| `backend/app/embeddings.py` | Qwen 文档／查询向量、超时、短期查询缓存 |
| `backend/app/retrieval.py` | 向量有效性校验、余弦召回、重排和父卡展开 |
| `backend/app/reranking.py` | Qwen rerank 适配、原始序号校验、安全失败处理 |
| `backend/app/indexing.py` | 离线增量建库、批次检查点、快照发布 |
| `backend/app/store.py` | 应用数据库与幂等请求 |
| `frontend/src/` | 对话、古树目录、原文与图片、反馈界面 |
| `compose.yaml`、`deploy/` | 容器部署及同源反向代理 |
| `scripts/` | 启动、知识快照、实测、浏览器检查、打包 |

当前按用户要求使用**纯向量召回 + rerank**，已移除 BM25、RRF 及关键词回退代码。模型为 `qwen3.7-text-embedding`，维度为 1024。138 个子块分别以 `context_header + "\n" + text` 为输入，使用 `text_type=document`；父卡、辅助检索词和图片不做向量化。向量归一化后以小端 float32 写入应用快照 `story.db` 的 `chunk_vectors` 表，原 analysis 数据不变。

在线问答使用 V4 双语三段式流程，正常情况为 `understand → answer → verify` 三次语言模型调用，另有一次查询向量和一次 rerank 请求。`UNDERSTAND` 一次输出 `Plan`：`lang`、`mode`、`search_query`、`subject`、`districts`、`keep_focus` 和仅统计时使用的 `filters`。纯问候不调用模型；无上下文裸代词、歧义单树追问或不支持的全量统计口径才澄清。目录中的树种和区县只是理解提示，不作为普通问答或导览的准入条件；杨树、孩儿莲和未知地名都可以进入正文检索。

独立检索句包含自然语言消解后的地点与树名；官方编号、唯一索引别名只补充查询文字，不裁剪候选。使用 `text_type=query` 生成查询向量，在全部可用子块中按余弦相似度召回。`RETRIEVAL_CANDIDATE_LIMIT` 默认 500，当前 138 个子块全部进入 Qwen rerank。重排仅接受原候选序号与分数，原文、ID、页码仍由本地数据库映射；分数只表示本次排序，不代表事实概率。

重排后按 `Plan.districts` 做后验过滤：父条目区县符合，或正文／上下文明确提及所限区县，才保留候选。否定地域限定不会变成正面区县过滤；裸词“虎丘／虎丘山”不等同于虎丘区。随后按排名取不同父条目，`QA_TOP_ENTRIES=8`、`EXPLORE_TOP_ENTRIES=6`。如果这些父条目的树种和完整正文均未包含 `subject`，在同一排序结果内顺延补入最多 3 个正文含该词、且满足区县约束的子块。整个精选阶段不调用语言模型，无批次正文扫描、分数门槛或额外的相关性模型。

精选父卡按原顺序展开故事、走访、访谈和背景。`RETRIEVAL_CONTEXT_CHARS` 默认 100000 字符，是安全上限；实际输入由父条目上限控制。超预算时先移除低排名父卡的未命中附带块，命中原文不会静默截断。当前语料在内存计算余弦，无 FTS5／BM25 或关键词回退路径。完整名单与精确总数始终单独查询完整树项表，不把 Top-K 当全部。

接入遵循 [百炼文本向量接口](https://help.aliyun.com/zh/model-studio/text-embedding-synchronous-api) 和 [文本排序接口](https://help.aliyun.com/zh/model-studio/text-rerank-api)。配置中可填控制台的 OpenAI Base URL，后端在同一 API Host 上使用 DashScope 原生接口。向量建库每批最多 20 条，在线重排最多 500 个候选，当前实际发送 138 个。

三个提示词位于 `backend/app/service.py`：`UNDERSTAND` 负责模式与指代，`ANSWER` 组织导览，`VERIFY` 只拦树木身份／地点错配、核心树龄／历史年份失实、传说当史实或明确地点冲突被隐瞒。导语、感悟、趣味评价、合理归纳和结构性计数正常放行。附带记载可回答，但不继承主树的照片或对话焦点。查询向量使用的固定检索指令：

```text
Given a question about ancient trees in Wuzhong, retrieve relevant passages from the book describing the trees, their history, stories, field visits, or conservation interviews.
```

重排使用的固定指令：

```text
Given a question about ancient trees in Wuzhong, rank the passages by whether they provide evidence that answers the question. Preserve distinctions between tree species, locations, legends, historical records, and interviews.
```

已登录的 `/api/session` 返回向量模型、维度、有效向量数、就绪状态及重排配置。问答和导览的 `audit.retrieval.mode=vector`，`candidate_entries=[]` 表示未按目录预裁剪；`ranking` 记录实际使用 rerank 还是 cosine。审计包含 `Plan`、全部重排名次、`top_entries`、精选条目、补召子块、区县过滤量、原文字符量及各次模型用量。完整名单标记为 `structured`。理解 JSON 失败最多重试一次；回答的格式修复与事实修订共用一次重试配额。

`RETRIEVAL_MODE` 仅接受 `vector`。`RERANK_ENABLED=true` 为默认值；设为 `false` 可只按向量相似度排序。`RERANK_MODEL=qwen3.7-text-rerank`，`RERANK_TIMEOUT=20` 秒。更换重排模型不需要重新生成文档向量。

索引记录向量模型、维度、接入地址指纹及每个子块输入的完整 SHA-256。正文、向量模型、维度或接入地址不匹配时，不混用旧向量。向量未配置、索引失效或调用失败时，具体问题返回明确错误，不退回关键词或直接展开猜测条目。重排失败时保留原有向量顺序，并在该次回答下提示“本次重排服务暂不可用，已按向量相似度检索”。查询向量缓存仅在后端内存，最多 256 条、5 分钟，审计可区分缓存与新调用。

## 资料和回答规则

- 正文标题与叙述是事实依据，索引仅用于辅助定位；正文内部矛盾保留并提示，不自行“纠正”。例如第 11 条会同时显示标题地点与叙述地点。
- 直接依据已核实正文回答，沿用原文树龄、时间及事实，不自动增加年份。页面不显示资料审核、存亡分类或现场核实标签，回答不附加此类通用提示。
- 传说、史载、访谈、存疑保留原有限定。回答每段绑定服务器映射的来源 ID、印刷页码与原文，不采用模型生成的页码。
- 模型组织正文并标注来源 ID，服务端从本地来源逐字绑定引文与页码。无效 ID 单独剔除，短导语可借用首个主要来源；只有整篇无合法引用才触发格式修复。核查只读取实际引用父条目的来源，保留同条目其他原文供修正漏引；输入只包含来源 ID、地点、树名、类型、正文、官方编号与冲突标记，正文仅序列化一次，不重复传送 evidence_text、quotes 或 citation。核查可修正合法引用，不能引入库外来源。
- 名单／计数遍历完整的树项表。例如“已不存”会包含部分已不存条目中的马尾松，得到 4 个树／树群项，而非只返回搜索前三名。树项数量不等于单株棵数；按树龄等未结构化条件的全量统计会要求改用可支持的口径。
- “吴中区有哪些有意思的古树？”等导览从精选的 6 个父条目中介绍 3～5 处代表性古树，写自然导语并配书内图片；普通问答从 8 个父条目中回应具体事实。需要完整名单时走全量 SQL。
- 目录没有主树种不代表正文没有记载。例如杨树的地名传说出现在第 66 条“银杏”正文中。此类回答明确限定为相关记载，保留传说性质，不把附带提及的杨树当作该条目的银杏，也不继承银杏的对话焦点。完整名单和计数仍要求可精确对应的结构化筛选条件。
- 回答使用可选小标题、自然段及段后配图。普通导览约 350～500 字，单一事实通常 1～2 段，明确要求详细时可继续展开；模型可跨来源综合。
- 核查只处理严重事实硬伤，移除机械数字匹配。局部硬伤直接省略并记录 omitted_claims，首段没有一票否决权。多数核心段或唯一核心段不合格时最多修订一次，格式修复也消耗同一配额，总计最多两次 ANSWER。仍无合格核心内容时，以本地卡片生成客观导读，或温和说明具体结论未记载并附上原文。
- 追问保存条目、树项与主体，由 UNDERSTAND 统一消解句中代词、复数指代和省略主语，显式换对象则清除旧焦点。最近最多 3 轮已完成问答仅作理解背景。单个多树项卡片无法确定具体树项时保留歧义标记，下一次单数追问可澄清；附带提及始终不建立主树焦点。
- “吴中203”等编号及唯一索引别名只补充 resolved_question，各阶段保留用户原始 question。目录支持子项地点和正文别名：“三官殿”命中第 2 条，“灵源寺”命中第 11 条，“清奇古怪”命中第 18 条。乡镇统计按地点词干归一化镇／古镇／乡／街道后缀：木渎 2 项、同里 1 项、甪直 3 项、震泽 4 项、虞山 6 项、镇湖 3 项。镇湖第 71 条依据正文中的“镇湖西京村”“万佛塔”加入应用层别名；数据库原有标题地点仍保留。
- 用户已确认现有资料核实完成。应用快照的 105 张卡片和 138 个子块记录为 confirmed／verified，已取消审核模式开关。重新导出快照时同步这一确认；原 analysis 文件不改写。现有正文、引用页码、图片及 138 条向量保持原样，不重新向量化。
- 模型从对应语言的本地图片元数据中选择真实 `image_id`，服务端按引用条目／树项核对并解析本地图片地址，在正文段落后展示，可点击放大。图注、印刷页码、原有署名直接取自数据库，不接受模型外链或自造图片。没有对应树项照片时不拿另一树项代替；附带提及的杨树也不会配成主条目银杏的照片。无树项标注的正文插图只标作条目配图。当前没有发送图像内容给模型，也没有新增视觉识别或 AI 生图。
- 新字段兼容旧对话；原始文件、图片与知识库不被模型写回。长回答完成后页面定位到开头，桌面和手机都可阅读图文、查看原文和放大图片。

## 会话、故障与数据流

本版使用共享入口口令及浏览器独立会话，不是具名团队账号系统。Cookie 为 HttpOnly、SameSite=Lax，14 天过期；一个浏览器不能读取另一个浏览器的对话。清除 Cookie 或退出后，旧历史不会自动绑定到新会话。迁移状态数据库可保留原会话，但更换域名后浏览器 Cookie 也需重新建立。

后台校验同源写请求，限制输入长度和提问频率，单个对话同时处理一个问题，模型并发默认 6。每次请求携带幂等编号，重复编号不会重复生成。关闭页面不会自动取消已接受的模型请求；重新打开原浏览器可查看结果。生成期间可点击停止按钮，服务端取消上游工作并释放会话锁。SSE 以条件通知推送进度、每 5 秒发送心跳，不再每 250 毫秒查库；前端仅在无活跃 SSE 时轮询恢复状态。服务重启会将未完成回答标记为中断，可重新提问。取消接口也会结束没有内存任务的孤立 processing 消息。发送前断网、未收到 accepted，或恢复轮询发现服务端未保存该问题时，前端恢复草稿并保留用户已有输入。反馈与对话保存在独立应用数据库中。

用户已明确授权使用 DeepSeek 和 Qwen 测试 API。向量建库发送子块正文至指定百炼空间；在线向量与重排发送独立检索句和候选原文。DeepSeek 理解阶段只读取问题、少量历史摘要、焦点和筛选词表；生成读取精选父卡原文及图片候选元数据；最小核查读取回答与精简来源。不会发送 PDF 或图像文件，不上传托管索引。服务端不记录密钥、请求头、模型请求正文或上游错误正文。在线会话、反馈与回答审计保存在运行服务的机器上，不进入公开仓库。

测试密钥失效后，页面、目录和历史仍可使用，新的模型问答会显示密钥失效提示。模型当前配置为 `deepseek-flash`，使用 JSON 输出与关闭思考的普通请求。接口约定参考 [DeepSeek Chat Completion](https://api-docs.deepseek.com/api/create-chat-completion/) 和 [JSON Output](https://api-docs.deepseek.com/guides/json_mode/)。

## 迁移到服务器

目标服务器安装 Docker Engine 与 Compose。

```bash
# 解压 release/wu-tree-chatbot.tar.gz 后进入 wu-tree-chatbot 目录。
cp .env.server.example .env.server
chmod 600 .env.server
# 编辑 .env.server：填写两个 API 密钥、embedding 接入地址及至少 16 位 ACCESS_PASSWORD
docker compose --env-file .env.server up -d --build
docker compose --env-file .env.server ps
curl http://127.0.0.1:8080/api/health
```

登录限流仅从受信直连代理解析转发 IP；部署命令通过 `--no-proxy-headers` 保留真实直连 IP，由应用统一处理信任链。X-Forwarded-For 从右向左逐跳解析，遇到第一个非受信 IP 即返回，遇到非法跳立即停止；X-Real-IP 回退也不接受内部受信代理 IP。`TRUSTED_PROXIES` 使用 JSON 数组，生产环境应填写实际代理网段。`ALLOWED_ORIGINS` 使用精确 HTTP(S) origin 数组（含非标准端口、无路径），用于外部 HTTPS 网关；默认校验 Host 及受信代理传递的 X-Forwarded-Host。前置网关应覆盖客户端提供的转发头，按实际客户端 IP 构造转发链；内层 Nginx 保留上游 X-Forwarded-Proto／Host，仅在缺失时使用本机值。两类内存限流各最多 4096 个键，会话表最多保留 5000 条有效会话。Nginx 使用 Docker DNS 每 10 秒刷新 API 地址，保留原始 `/api/...` URI。

服务器可通过 `.env.server` 调整 `MODEL_TIMEOUT=90`、`MAX_MODEL_CONCURRENCY=6`、`QA_TOP_ENTRIES=8` 和 `EXPLORE_TOP_ENTRIES=6`。

默认端口仅在服务器回环地址 `127.0.0.1:8080` 开放，可接现有 HTTPS 网关。外部使用 HTTPS 时将 `COOKIE_SECURE=true` 并重建 api 容器；网关应保留原始 Host、关闭 `/api` 响应缓冲、允许至少 240 秒读超时。需要局域网直接访问时，可将 `BIND_ADDRESS` 改为对应网卡地址。

部署到 `https://zzyoung.xyz/ancient_tree/` 时，在 `.env.server` 设置 `APP_BASE_PATH=/ancient_tree/`、`WEB_PORT=18080`、`BIND_ADDRESS=127.0.0.1` 和 `COOKIE_SECURE=true`，重新构建 web 镜像。前端 API、静态资源和书内图片统一使用该前缀；后端接口仍保留内部 `/api` 路径。将 `deploy/ancient-tree-location.conf` 包含在现有 HTTPS server 块内，外层 Nginx 去除请求前缀，并把会话 Cookie 的 Path 改为 `/ancient_tree/`。HTTP server 中仅将该路径重定向到 HTTPS。变更前备份现有站点配置，执行 `nginx -t` 成功后 reload。

本地验证同一路径可用 `APP_BASE_PATH=/ancient_tree/ npm run dev`，浏览器访问 `http://127.0.0.1:5173/ancient_tree/`。`APP_BASE_PATH` 是构建参数，改动后需要重新构建；默认 `/` 保持根目录部署兼容。

知识目录只读挂载；应用状态使用名为 `wu-tree-chatbot_app_state` 的持久卷。API 不映射宿主机端口，后端使用非 root 用户与只读根文件系统。此版运行一个 API worker，适合当前内部验证规模；会话锁和限流为进程内实现，增加 worker 或多副本前应迁移任务锁／限流至共享存储，应用数据库可再迁往 PostgreSQL。

停止服务不会删除聊天记录：

```bash
docker compose --env-file .env.server down
```

需要保留数据时，不要使用 `down -v`。升级或迁移前对应用库做一致性备份，并一同保留知识快照和 manifest。本地可以使用 SQLite backup API：

```bash
.venv/bin/python - <<'PY'
import sqlite3
with sqlite3.connect('state/app.sqlite') as src, sqlite3.connect('state/app.backup.sqlite') as dst:
    src.backup(dst)
PY
```

容器状态可在停止 API 后备份持久卷。目标服务器仍需完成部署环境验收；资料按用户确认的已核实版本使用。

## 检查、更新与交付

```bash
# 不调用外部模型的回归检查
.venv/bin/python -m pytest -q
cd frontend
npm run check:format
npm run build
cd ..

# 以下检查实际调用本地 .env 中的 Qwen embedding、Qwen rerank／DeepSeek 测试账户
.venv/bin/python scripts/retrieval_eval.py
.venv/bin/python scripts/live_eval.py
# 保持本地服务启动；使用已安装的 Chrome，或先安装 Playwright Chromium
node scripts/browser_check.mjs

# 从原分析结果重新导出快照；在停止应用后执行，然后重启
.venv/bin/python scripts/export_knowledge.py --source ../analysis/pilot
# 导出自动保留哈希与编码器一致的向量；有正文变化时增量补建后再启动服务
.venv/bin/python scripts/build_embeddings.py

# 生成可迁移包，排除密钥、运行环境、聊天记录及测试输出
.venv/bin/python scripts/package_release.py
```

导出和离线建库在停止 API 后执行，二者共用发布锁。导出会逐条核对完整输入哈希、模型、维度和向量有效性，保留未变更向量及编码器元数据；有缺失向量时拒绝在线使用不完整索引，并提示增量建库。成功批次检查点保存在 `state/embedding-build.sqlite`；重试复用相同模型、维度、接入地址和输入内容的向量。API 失败时不会覆盖已发布的知识快照。运行报告在 `state/embedding-build.json`。若只是更换同一接入地址下的密钥，不需要重建；更换地域／业务空间地址或模型、维度则先重建。服务器可直接使用打包后的向量库，不必重复向量化；更新资料时在工作机重建后整体迁移 `data/knowledge/` 并重启。

`live_eval.py --only sanguan_age` 可局部复测并合并既有报告；不带参数时完整重跑。浏览器检查支持 `TEST_BASE_URL` 和 `CHROME_PATH`，默认复用本机 Chrome；没有 Chrome 时先在 frontend 中运行 `npx playwright install chromium`。

测试报告与截图位于 `state/`，迁移包位于 `release/`。迁移包内含源码、依赖锁、部署配置及知识快照；没有 API 密钥、Cookie、聊天记录或测试截图。`.env`、`.env.server`、`state/` 等已加入 Git 忽略规则；整个项目的提交与同步约定见根目录 `AGENTS.md`。

Docker 镜像构建与目标服务器的 HTTPS／权限／卷挂载需要在有 Docker 的环境执行；本机没有 Docker Engine，本地验收使用真实 FastAPI、前端构建产物及浏览器。

### 本次本地验收（2026-11-10）

- 77 项后端回归检查通过，覆盖向量／重排接口、无 FTS 依赖、读完全部 138 段且不提前停止、展开超过三个条目、全书上下文预算、引用补全和原文绑定、图片树项归属、伪造图片／来源拦截、署名保留、回答修正、连接中断恢复，以及身份隔离、CSRF、幂等、反馈和重启恢复。
- 真实 DeepSeek + Qwen embedding／rerank 的 22 个问答／追问用例通过，覆盖树龄、传说、访谈、辅助编号、地点冲突、历史状态、未知对象、统计口径、区域概览和完整名单、自然描述找树、古杨树附带记载及区县反例，新增带图的详细故事。介绍类回答可综合多个条目；资料有限的附带故事不会为凑字数扩写。最近运行结果见 `state/live-eval.json`。
- React／TypeScript 生产构建及格式检查通过。桌面 1440×1000 和手机 390×844 浏览器通过 9 组检查，包含较长回答、小标题、正文图片、图片与引用树项对应、点击放大、刷新恢复、原文、反馈、目录和断网恢复；没有横向溢出、破图或前端外部调用，也覆盖缺少 randomUUID 的手机 HTTP 场景。
- 知识库包含 138 条真实向量；正文、上下文头、内容哈希、向量、树项、页码和 262 张图片保持原样。应用快照仅更新确认状态，并删除整理备注中的复核请求。前端没有模型调用，两个密钥仅在后端配置。
- Compose YAML 与关键挂载／端口配置通过静态检查；Docker Engine 不可用，因此没有声称已完成容器构建或服务器验收。

### 纯向量与重排开发评估

`state/vector-rerank-eval.json` 保存此前固定 16 题、前 20 个向量候选的“相似度排序／同一向量候选加重排”历史对比。它不包含本次新增的正文相关性模型，也不是当前完整流程的评估。两组均先执行同样的辅助编号精确匹配，再用相同候选比较；属于开发检查，不是独立盲测，不代表线上准确率。当前流程的实测见 `state/live-eval.json`。

| 方法 | 正确子块前 1 | 正确子块前 3 | 正确子块前 20 | 正确父卡前 3 |
| --- | --- | --- | --- | --- |
| 纯向量相似度 | 14/16 | 16/16 | 16/16 | 16/16 |
| 同一向量候选 + Qwen rerank | 16/16 | 16/16 | 16/16 | 16/16 |

此次重排改善了前排顺序，没有增加召回候选。原始 `state/retrieval-eval.json` 是先前版本的历史结果，其“吴中203”未执行精确编号定位，口径与本表不同，不能把两张表直接当作重排提升量。仍需项目组提供独立问题集来评估泛化表现。

### improve_v3.md 验收

- 旧数字检查模块与旧批次回放脚本已删除，旧 A/B 实验脚本移至 `state/improve-v3/archive/`，不再作为当前门禁，也不进入发布包。
- 后端 **151 项测试通过**，覆盖三阶段调用、完整清册、Top-K 父卡、区县后验过滤、Rank 24 附带提及补召、引用修复、首段降级、照片与焦点安全、地点变体及孤立取消；日志位于 `state/improve-v3/tests.log`。
- 前端 Prettier、TypeScript 与生产构建通过；桌面／手机 **18 组浏览器检查通过**。浏览器检查使用隔离临时 SQLite 和确定性回答替身，覆盖桌面／手机目录、正常 SSE、停止／恢复、旧会话隔离，以及未接受请求和恢复轮询时的草稿保护；证据位于 `state/improve-v3/browser/`。
- 真实模型共 **7 个问答／追问用例通过**。最终导览实测 **9.87 秒、3 次语言模型调用、6 个精选父条目、3 张配图**，核查输入 **3047 tokens**；木渎定位与追问均保持第 23 条第 2 树项，杨树附带记载、区县限制、虚构对象与单点棵数也通过。各次运行及此前失败记录保留在 `state/improve-v3/`，最新汇总为 `live-eval.json`；单次速度不代表固定延迟保证。
- 配图核查确认第 23 条当前只有广玉兰的两张照片，未提供古松园罗汉松的独立照片。该追问会说明缺图并继续介绍，服务端禁止借用另一树项或其他地点的照片。
- 数据只读校验记录在 `state/improve-v3/protected-before.json` 和最终 `validation.json`，包括知识库、manifest 与 analysis/pilot 的 278 个文件；不重新建库或生成向量。

```bash
.venv/bin/python -m pytest -q
(cd frontend && npm run check:format && npm run build)
.venv/bin/python scripts/live_eval.py --only district_fun split_item --output state/improve-v3/live-eval.json
.venv/bin/python scripts/package_release.py

# 隔离浏览器测试，另一个终端运行浏览器脚本
.venv/bin/python -m uvicorn browser_app:create_test_app --factory --app-dir backend/tests --host 127.0.0.1 --port 8124 --no-access-log --no-proxy-headers
TEST_BASE_URL=http://127.0.0.1:8124 node scripts/browser_improvements.mjs
```
