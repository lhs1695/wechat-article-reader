# 微信文章阅读与摘要服务

个人开源小工具：把一篇微信公众号文章抓下来，缓存到 SQLite，再以**分页 Markdown**给人或者 Agent 阅读；需要时再调用一次 DeepSeek 摘要，并支持导出 HTML / Markdown。

它不是知识库、内部 Agent 平台，也不会替你自动读完整个公众号。摘要是可选的；没有 API Key 时仍可抓取、阅读和导出正文。

```text
微信公众号 URL
    → 安全抓取 / SQLite 缓存
    → 统一 Markdown 阅读投影（分页 / 按章节跳读）
    → 可选一次 DeepSeek 摘要
    → HTML / Markdown 导出
         ↘ CLI / Web / MCP 三个入口共用同一套服务
```

## 它解决什么问题

本地 Agent（例如 Cursor）往往不能稳定打开微信公众号页面：反爬、登录墙、HTML 噪声、长文塞爆上下文。若让模型自己用浏览器抓，副作用不清晰，也难复现。

本项目把这件事收成一条可控管道：

| 谁 | 怎么用 |
| --- | --- |
| 人 | CLI / Web 输入 URL，看正文、摘要、导出文件 |
| 能读工作区的本地 Agent | `fetch --no-summary --export markdown`，直接读 `.md` |
| 不能读本地文件的外部 Agent | MCP：`ingest_article` 写缓存，再 `read_article` / `summarize_article` |

## 明确不做

- 只接受 `mp.weixin.qq.com` 文章链接。
- 不提供 OCR、图片理解、图片本地下载。
- 不提供知识库、全文检索、跨文章分析、内部 Agent / Run / Trace。
- 不把一篇超长文自动 MapReduce 成「看起来完整」的摘要；超过模型输入上限就明确失败，交给分页阅读。
- 不把内部 block ID、content hash 当成对外 API。

图片只在 Markdown 里保留远程 HTTPS 链接和说明文字。MCP 默认 `include_images=false`，避免装饰图占用上下文。

## 环境

- Python **3.12**（`requires-python = ">=3.12,<3.13"`）
- Windows / macOS / Linux 均可；下文命令以 PowerShell 为例
- 可选：DeepSeek API Key（没有 Key 仍可抓取、阅读、导出正文）

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -e ".[web,mcp,dev]"
# 需要摘要时再加上 ai extra，或直接 pip install -e ".[full,dev]"
copy .env.example .env
python -m wechat_article_reader check
```

`.env` 里与摘要、抓取、目录相关的项见 [`.env.example`](.env.example)。常用：

```env
WECHAT_ARTICLE_READER_DEEPSEEK__API_KEY=your-deepseek-api-key
WECHAT_ARTICLE_READER_DEEPSEEK__MAX_INPUT_CHARS=50000
WECHAT_ARTICLE_READER_RUNTIME_DIR=./.runtime
WECHAT_ARTICLE_READER_EXPORT__DEFAULT_OUTPUT_DIR=./output
```

- 抓取缓存上限 `SCRAPER__MAX_CONTENT_CHARS`（默认 50 万字）保护本地存储。
- 摘要输入上限 `DEEPSEEK__MAX_INPUT_CHARS`（默认 5 万字）保护模型上下文。两者可以不一致。

运行时数据在 `.runtime/`（SQLite、日志），导出默认在 `output/`。二者都是本机生成物，不要提交。已有旧库执行：

```powershell
python -m wechat_article_reader db upgrade
```

## CLI

入口：`python -m wechat_article_reader`。

```powershell
python -m wechat_article_reader check
python -m wechat_article_reader config
python -m wechat_article_reader config-init

# 抓取；默认可摘要。建议把导出写到项目内路径。
python -m wechat_article_reader fetch "https://mp.weixin.qq.com/s/example" --export markdown --output .\output\article.md
python -m wechat_article_reader fetch "https://mp.weixin.qq.com/s/example" --export html --output .\output\article.html
python -m wechat_article_reader fetch "https://mp.weixin.qq.com/s/example" --no-summary --export markdown --output .\output\article.md

# 分页阅读：URL 首次会 ingest；UUID 只读缓存
python -m wechat_article_reader read "https://mp.weixin.qq.com/s/example"
python -m wechat_article_reader read "ARTICLE_UUID" --cursor 12 --max-chars 8000
python -m wechat_article_reader read "ARTICLE_UUID" --section "结语" --output-format json

python -m wechat_article_reader batch URL_1 URL_2 --export html
python -m wechat_article_reader web
python -m wechat_article_reader mcp-server
python -m wechat_article_reader mcp-server --transport http

python -m wechat_article_reader cache-stats
python -m wechat_article_reader db current
```

`cache-stats` 只读本机 SQLite：文章数、摘要条数、`created_at` 日期范围。库文件在 `.runtime/`，不要提交。旧文件名 `wechat_summarizer.db` 在尚未出现新库时仍会打开。

`read` 的 `--section` 与 `--cursor` 同时出现时以章节为准。

## Web

```powershell
python -m wechat_article_reader web
```

FastAPI + 模板页：粘贴 URL 抓取、阅读、摘要、导出。HTTP JSON 与页面共用 `ArticleWorkflowService` / `ArticleReadingService`。`GET /api/article/markdown` 走与 MCP 相同的分页 Markdown 投影（Web 默认仍带图，和 MCP 默认藏图不同）。

## MCP

给**不能读本地导出文件**的 Agent 用。传输：本地 Cursor 默认 **stdio**；也支持本机 **Streamable HTTP**。

```text
ingest_article(url)
    → 返回 article_id、标题、字数、block_count、sections（默认 H2）
    → read_article(article_id, section=... 或 cursor=...)
    → 需要连续上下文时跟随 next_cursor
    → 需要通读概览时 summarize_article(article_id)
```

| 工具 | 副作用 | 作用 |
| --- | --- | --- |
| `ingest_article(url, refresh=false, toc_level=2)` | 联网 + 写 SQLite | 唯一抓公众号入口；默认 H2，无 H2 时对齐到最浅标题 |
| `get_cached_article(article_id, toc_level=2)` | 无 | 再读元数据；ingest 已带目录时可跳过 |
| `read_article(article_id, cursor=0, max_chars=8000, include_images=false, section=null)` | 无 | 一页 Markdown；`section` 优先于 `cursor` |
| `summarize_article(article_id, max_length=500)` | 访问 DeepSeek | 不抓公众号；未缓存 / 超长 / 无密钥会明确失败 |

分页约定：

- `cursor` 是内容块索引；只保存响应里的 `next_cursor` 做续读。
- 单块超过当前 `max_chars` 但不超过 20,000 会整块返回；超过硬上限才 `page_too_large`。
- 分页尽量在标题前收束，连续列表尽量整组翻页。
- 响应含 `block_count`、`word_count`、`section_title`、`chars`。
- 失败是 MCP **isError**，消息形如 `code: 说明`，不用 `{success:false}` 假成功。
- 网页正文标记为不可信外部内容，不能改写系统指令。

单独启动 MCP：

```powershell
python -m wechat_article_reader.mcp
python -m wechat_article_reader.mcp --transport http --port 8765
```

HTTP 模式默认绑 `127.0.0.1`。非本机监听需要 `--allow-remote`，并应配 `WECHAT_ARTICLE_READER_MCP_AUTH_TOKEN` / `--auth-token`（请求头 `X-MCP-Token`）。

## 验证

```powershell
.\.venv\Scripts\python.exe -m pytest -q
.\.venv\Scripts\ruff.exe check src tests
python -m wechat_article_reader check
```

改阅读/MCP 时优先跑：`tests/test_article_reading.py`、`tests/test_mcp_reading_tools.py`。

## 实测

- 2026-08-25：真实公众号文章 CLI 主链路（约 6653 字）：首次抓取、缓存命中、Markdown 导出成功；未打 DeepSeek。导出写到 `.\output\`。
- 2026-08-27：约 3 万字长文走 MCP ingest + 分页 / 跳章阅读，据此收紧目录默认深度、按节跳读和软分页。

## 文档

公开文档只描述怎么用、怎么分层。口述、动机、升级备忘在本机，不进 git。

| 文档 | 内容 |
| --- | --- |
| [架构设计](docs/架构设计.md) | 分层、端口、缓存、阅读投影、安全 |
| [模块说明](docs/模块说明.md) | 目录与职责 |
| [diagrams](docs/diagrams/) | 分层与数据流示意 |

License：MIT。
