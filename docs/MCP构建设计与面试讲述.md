# MCP 构建设计与面试讲述

## MCP 在我理解里是什么

MCP 不是「把 Python 函数都挂成工具」。它是给模型的**受控能力边界**：有哪些动作、每个动作有没有联网/写盘、失败长什么样、返回值怎么续读。

外部 Agent 往往不能读我磁盘上的导出文件，也不该自己去抓微信。所以 MCP 在这里只做一件事：把已经想清楚的阅读服务，翻译成四个工具。业务仍在 `ArticleReadingService` / `ArticleWorkflowService`，不在 `mcp/` 里再解析一遍 HTML。

## 目的

```text
ingest_article(URL) → article_id + sections → read_article(section 或 cursor)
                                           → summarize_article(article_id)
```

Markdown 是主返回格式：标题、列表、代码、表格、链接能留下来，比原始 HTML 省上下文，人和模型都能读。

## 最小接口

| 工具 | 作用 |
| --- | --- |
| `ingest_article` | 抓取或命中缓存，返回 UUID、标题、字数、块数和章节（默认 H2，无则对齐最浅标题）；唯一的公众号联网写操作 |
| `get_cached_article` | 可选；ingest 已含章节时可跳过 |
| `read_article` | 按 `section` 或内容块 cursor 返回一页 Markdown；使用 `next_cursor` 续读 |
| `summarize_article` | 对已缓存文章执行一次 DeepSeek 摘要 |

没有全文 Resource、文章内检索、批量摘要、图片清单、结构化 block 或跨文章工具。失败通过 MCP isError 返回 `code: 说明`，避免 `{success:false}` 被当成成功。

## 关键取舍

- **读用 `article_id`，不用 URL**：缓存读取无网页副作用，重试可预测。摘要会访问 DeepSeek，但不会再抓公众号。
- **cursor + max_chars**：长文不一次塞满上下文，也不在服务端做不透明截断。`next_cursor` 是唯一续读位置。分页优先在标题前收束，连续列表尽量整组翻页。长文先按 `sections` / `section` 跳读；只有需要连续上下文才跟随 `next_cursor`。通读优先 `summarize_article`。
- **toc_level 默认 2**：有 H2 的长文避免 40+ 条 H3 打满 ingest；若全文只有 H3/H4，目录自动对齐到最浅一层。内部投影仍有全部 heading。
- **MCP 默认 `include_images=false`**：微信配图 alt 经常是「图片」，模型也看不见图。CLI/Web 仍可出图。cursor 不因藏图而跳跃错位。
- **不公开 content hash 与 block ID**：内部实现细节，不承诺长期兼容。
- **长文不 MapReduce**：超过单次摘要上限返回 `article_too_long`。调用方按任务自己读页，结果可核对原文。
- **网页不可信**：文章里的指令、链接、角色设定不构成对 MCP 的授权。
- **人机入口可以组合步骤**：CLI/Web 允许 URL 一次抓取并摘要；Agent 协议把副作用拆开。这是有意的不对称，不是三个入口行为混乱。

## 运行约束

- `ingest_article` 会联网并写 SQLite，可重试，但有副作用。
- `get_cached_article` 与 `read_article` 不联网；缺 UUID 或缓存为 `article_not_found`。
- `summarize_article` 未 ingest 为 `article_not_found`；未配置 DeepSeek 为 `summarizer_unavailable`。
- `read_article` 的 `max_chars` 默认 8000，范围 1000–20000；单块超过页大小但不超过 20000 整块返回，再大才 `page_too_large`。响应带 `block_count`、`word_count`、`section_title`、`chars`。`section` 与 `cursor` 同时传以章节为准。
- HTTP 用 Streamable HTTP，默认本机；stdio 仍是本地 Cursor 安装默认。

## 面试讲述（约 40 秒）

「我把 MCP 当成受控内容交付协议，而不是给每个函数套一层工具。抓取是唯一显式联网写缓存的动作，成功后返回 UUID 和目录；阅读只从 SQLite 按 Markdown 页交付，长文按节跳或跟 cursor。摘要也只接受已缓存 ID，超长就拒绝自动分块。Web、CLI、MCP 共用抓取和阅读服务，避免三个入口三份正文。」

更长的项目口述、追问和「不要夸大」见 [面试手册](面试手册.md)。为何做成交付服务而不是 Agent 平台见 [设计动机与经历](设计动机与经历.md)。
