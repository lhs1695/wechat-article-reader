# 微信文章阅读服务

个人开源小工具：一篇微信公众号文章 → SQLite → **同一套 Markdown 投影** → 导出。摘要可选；没有 API Key 仍可抓取、阅读和导出正文。

它不是知识库、内部 Agent 平台，也不会替你扫完整个公众号。

```text
微信公众号 URL
    → 安全抓取 / SQLite 缓存
    → 统一 Markdown 投影
    → 导出 Markdown；必要时分页 / 跳章
    → 可选一次 DeepSeek 摘要
         ↘ CLI / Web / MCP 三个入口共用同一套服务
```

## 人怎么用，Agent 怎么用

本地 Agent 往往打不开公众号页（反爬、登录墙、HTML 噪声、长文塞爆上下文）。本项目把阅读收成一条可控管道。

**人**用 CLI / Web：可贴一条 URL，一次完成抓取、阅读、可选摘要和导出。终端翻页用 `read` 给人看一页，不是 Agent 默认。Web 三种启动方式见下文「Web」。

**能读工作区的本地 Agent**默认导出再读文件：

```powershell
python -m wechat_article_reader fetch "https://mp.weixin.qq.com/s/example" --no-summary --export markdown --output .\output\article.md
```

然后直接读 `output/*.md`。不要先 `read_article` 翻页。

**不能读本地文件的 Agent**才用四个 MCP 工具，且必须按副作用拆开：只有 `ingest_article` 联网写库；读缓存和打模型不能混在同一次调用里。分页 / `section` / `cursor` 是退路（无文件权限，或坚持不落地文件的超长文）。

| 谁 | 入口 |
| --- | --- |
| 人 | CLI / Web |
| 能读工作区的本地 Agent | `fetch --no-summary --export markdown`，读 `output/*.md` |
| 不能读本地文件的 Agent | 下表四个工具（无文件权限时用） |

| 工具 | 副作用 | 作用 |
| --- | --- | --- |
| `ingest_article(url, refresh=false, toc_level=2)` | 联网 + 写 SQLite | 唯一抓公众号入口；默认 H2，无 H2 时对齐到最浅标题 |
| `get_cached_article(article_id, toc_level=2)` | 无 | 再读元数据；ingest 已带目录时可跳过 |
| `read_article(article_id, cursor=0, max_chars=20000, include_images=false, section=null)` | 无 | 一篇不够长则一次返回；`section`（精确或至少两字唯一前缀）只返回该节，节过长再分页 |
| `summarize_article(article_id, max_length=500)` | 访问 DeepSeek | 不抓公众号；未缓存 / 超长 / 无密钥会明确失败 |

## 明确不做

- 只接受 `mp.weixin.qq.com` 文章链接。
- 不提供 OCR、图片理解、图片本地下载。
- 不提供知识库、全文检索、跨文章分析、内部 Agent / Run / Trace。
- 不把一篇超长文自动 MapReduce 成「看起来完整」的摘要；超过模型输入上限就明确失败，交给分页阅读。
- 不把内部 block ID、content hash 当成对外 API。

图片只在 Markdown 里保留远程 HTTPS 链接和说明文字。MCP / CLI `read` / Web Markdown 导出默认都不含图（`include_images=false`），避免装饰图占用上下文。需要图时：MCP 传 `include_images=true`，CLI 加 `--images`，Web `GET /api/article/markdown?include_images=true`。网页「原文抓取」仍显示清洗后的 HTML（可含图）。

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

# 抓取；默认可摘要。Markdown 导出默认无图，需要图时加 --images。
python -m wechat_article_reader fetch "https://mp.weixin.qq.com/s/example" --export markdown --output .\output\article.md
python -m wechat_article_reader fetch "https://mp.weixin.qq.com/s/example" --export html --output .\output\article.html
python -m wechat_article_reader fetch "https://mp.weixin.qq.com/s/example" --no-summary --export markdown --output .\output\article.md

# 终端阅读给人看；URL 首次会 ingest，UUID 只读缓存。默认一次最多 20000 字。
python -m wechat_article_reader read "https://mp.weixin.qq.com/s/example"
python -m wechat_article_reader read "ARTICLE_UUID" --cursor 12 --max-chars 8000
python -m wechat_article_reader read "ARTICLE_UUID" --section "结语" --output-format json
python -m wechat_article_reader read "ARTICLE_UUID" --images

python -m wechat_article_reader batch URL_1 URL_2 --export html
python -m wechat_article_reader web
# Windows 也可：双击 run_web.pyw（无控制台）或 run_web.cmd（有控制台）
python -m wechat_article_reader mcp-server
python -m wechat_article_reader mcp-server --transport http

python -m wechat_article_reader cache-stats
python -m wechat_article_reader db current
```

`cache-stats` 只读本机 SQLite：文章数、摘要条数、`created_at` 日期范围。库文件在 `.runtime/`，不要提交。旧文件名 `wechat_summarizer.db` 在尚未出现新库时仍会打开。

`read` 的 `--section` 只返回该节；与 `--cursor` 同时出现时在该节内续读。不传 section 且全文不超过 `--max-chars`（默认 20000）则一次读完。默认不含图，需要图时加 `--images`。

## Web

三种启动方式都落到同一套 FastAPI 应用，监听 `http://127.0.0.1:8000`：

| 怎么开 | 说明 |
| --- | --- |
| `python -m wechat_article_reader web` | CLI；控制台打印 URL |
| `run_web.pyw` | Windows 双击；无控制台，就绪后打开浏览器；失败写仓库根目录 `error.log` |
| `run_web.cmd` | Windows 双击；用 `.venv\Scripts\python.exe`，有控制台 |

```powershell
python -m wechat_article_reader web
```

页面：首页状态、单篇抓取/摘要/下载、批量导出、历史（缓存列表）。HTTP JSON 与页面共用 `ArticleWorkflowService` / `ArticleReadingService`。

- `GET /api/article/markdown` 走与 MCP 相同的分页 Markdown 投影，**默认无图**（`include_images=false`）。
- 下载 Markdown 默认无图；下载 HTML 仍可带图。
- 页面里的「原文」是清洗后的公众号 HTML，不是导出文件。

## MCP

给**不能读本地导出文件**的 Agent 用。能读工作区时不要先 `read_article`，走上面的 `fetch --export markdown`。四个工具及副作用见上文。传输：本地 Cursor 默认 **stdio**；也支持本机 **Streamable HTTP**。

```text
ingest_article(url)
    → 返回 article_id、标题、字数、block_count、sections（默认 H2）
    → 字数不超过 max_chars（默认 20000）时一次 read_article(article_id)
    → 更长则 read_article(article_id, section=...) 只取该节
    → 该节仍超长才跟随 next_cursor
    → 需要通读概览时 summarize_article(article_id)
```

阅读约定：

- 不传 `section` 时，全文不超过 `max_chars` 就一次返回，不要先翻页。
- `section` 只返回该节。与 `cursor` 同时出现时在该节内续读；`cursor` 不在节内则从节首开始。
- ingest 的 `sections` 若只有「正文」（没有可用标题），不要按节跳；超长再跟随 `next_cursor`。
- `cursor` 是内容块索引；只保存响应里的 `next_cursor` 做续读。
- 单块超过当前 `max_chars` 但不超过 20,000 会整块返回；超过硬上限才 `page_too_large`。
- 分页尽量在标题前收束，连续列表尽量整组翻页。
- 响应含 `block_count`、`word_count`（本页投影正文）、`section_title`、`section_end_cursor`、`chars`。合成节名「正文」不会作为续页标题插入。
- 失败是 MCP **isError**，消息形如 `code: 说明`，不用 `{success:false}` 假成功。
- 四个工具成功体都带 `source_trust: "untrusted_web_content"`；网页正文（及据此生成的摘要）不能改写系统指令。

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

## 文档

公开文档只描述怎么用、怎么分层。口述、动机、升级备忘在本机，不进 git。

| 文档 | 内容 |
| --- | --- |
| [架构设计](docs/架构设计.md) | 分层、端口、缓存、阅读投影、安全 |
| [模块说明](docs/模块说明.md) | 目录与职责 |
| [diagrams](docs/diagrams/) | Archify 交互图（JSON 规格 + 交付 HTML） |

License：MIT。
