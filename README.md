# 树语 · 吴中古树问答

依据《树碑立传》“树的故事”构建的中英双语问答应用。支持古树目录、原文引证、书内图片、对话历史和反馈。

当前版本为 **V5.0**。线上入口：[zzyoung.xyz/ancient_tree/](https://zzyoung.xyz/ancient_tree/)；访问口令由项目维护者单独提供。

前端使用 React、TypeScript 和 Vite；后端使用 Python FastAPI。普通问答经过问题理解、向量检索、重排、回答生成与事实核验；完整名单及统计使用结构化数据库查询。生产环境由 Nginx 提供同源入口，Docker Compose 管理前后端服务。

| 目录 | 内容 |
| --- | --- |
| [`chatbot/`](chatbot/) | 可运行的前后端、只读知识快照、部署配置和测试 |
| [`analysis/`](analysis/) | 书籍资料处理、结构化输入、schema 与离线检查 |
| [`rag workspace/`](rag%20workspace/) | 各版本改进规划与切块规则 |
| [`AGENTS.md`](AGENTS.md) | 项目修改、验证、提交与同步约定 |

知识快照包含 105 个条目、108 个树／树群项、138 个子块及 262 张书内图片，保留正文来源、页码和图注署名。

运行、配置及部署说明见 [`chatbot/README.md`](chatbot/README.md)。需要 Python 3.13、Node.js 24，以及自行配置的 DeepSeek 和 Qwen API 密钥。配置示例为 [`chatbot/.env.example`](chatbot/.env.example) 和 [`chatbot/.env.server.example`](chatbot/.env.server.example)。

原始 PDF、摄影压缩包、办公附件、API 密钥、访问口令、会话数据库、本地评测输出及发布包不进入 Git。已有服务器的私密配置和会话数据独立保存。

V5 译文经过 AI 逐块审校，尚非真人签审；留出集与封存集由同一代理兼任 Planner 编写，属于非独立盲测。相关规则与结果摘要见应用说明。
