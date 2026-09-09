# AGENTS.md

- 个人学习项目，服务 Python Agent 面试讲解，不是简历主力；不要按生产平台或代表作去扩功能、堆故事，话术必须对得上代码。
- 包名 `wechat_article_reader`（GitHub：`wechat-article-reader`）。主链路：抓取 → SQLite 缓存 → Markdown 阅读 → 可选摘要 → HTML/Markdown 导出。依赖 `domain → application/features → infrastructure → presentation`；CLI、Web、MCP 复用 `ArticleWorkflowService` 与 `ArticleReadingService`。
- 仅 `mp.weixin.qq.com`。不做内部 Agent、知识库、OCR、跨文章、MapReduce 长文摘要；不要恢复已删除的 Run/Trace/FTS/多模型。
- MCP：`ingest_article` 唯一抓公众号并写库；`read_article` 只读缓存；`summarize_article` 只打 DeepSeek。
- 改前看已有变更，只改需求相关内容；行为要有测试，不跳过测试、不吞异常。验证先跑定向测试，核心链路或风险不够覆盖时再全量。
- 密钥、正文、Trace 不得提交或泄漏；外部写入须明确授权，未知状态不得自动重放。
