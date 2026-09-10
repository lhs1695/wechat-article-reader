# 微信文章阅读服务

[![CI](https://github.com/lhs1695/wechat-article-reader/actions/workflows/ci.yml/badge.svg)](https://github.com/lhs1695/wechat-article-reader/actions/workflows/ci.yml)

一篇微信公众号文章 → SQLite → **同一套 Markdown 投影** → 导出。摘要可选，没有 API Key 也能导出正文。

```text
微信公众号 URL
    → 安全抓取 / SQLite 缓存
    → 统一 Markdown 投影
    → 导出 Markdown；必要时分页 / 跳章
    → 可选一次 DeepSeek 摘要
         ↘ CLI / Web / MCP 三个入口共用同一套服务
```

## 谁用哪个入口

| 谁 | 入口 |
| --- | --- |
| 人 | CLI / Web |
| 能读工作区的本地 Agent | `fetch --no-summary --export markdown`，读 `output/*.md` |
| 不能读本地文件的 Agent | 四个 MCP 工具（无文件权限时的退路） |

## 一次真实的 `read_article` 返回

2026-09-10 对公开文 [`https://mp.weixin.qq.com/s/Qgq2wrRjbJLyOpsW6zwJFQ`](https://mp.weixin.qq.com/s/Qgq2wrRjbJLyOpsW6zwJFQ) 的实调（`max_chars=1000`，以便看见 `next_cursor`；默认 20000 时这篇一次读完）。`word_count` / `chars` 是本页投影的真实长度；`content_markdown` 只留开头并标明已截断，不贴全文。

```json
{
  "success": true,
  "source_trust": "untrusted_web_content",
  "article_id": "158e0f0a-d669-4406-8045-6afc6232a655",
  "cursor": 0,
  "next_cursor": 25,
  "has_more": true,
  "content_markdown": "# 一文讲透 AI Agent 生产级执行全流程：三阶段、六泳道与 30 个核心节点 · 智能体AI\n\n决定一个 Agent 能不能上线的，从来不是 模型 有多强，而是围绕模型的这条 执行闭环 有没有搭完整。\n\n…（已截断）",
  "block_count": 136,
  "word_count": 940,
  "section_title": "正文",
  "chars": 992,
  "section_end_cursor": 13
}
```

`ingest_article` 先返回 `article_id`、`title`、`word_count`、`block_count`、`sections`（默认 H2；这篇没有 H2，目录对齐到 H3）。失败不走这种成功体，见下「为什么这么设计」。

## 工具表与明确不做

| 工具 | 副作用 | 作用 |
| --- | --- | --- |
| `ingest_article(url, refresh=false, toc_level=2)` | 联网 + 写 SQLite | 唯一抓公众号入口；默认 H2，无 H2 时对齐到最浅标题 |
| `get_cached_article(article_id, toc_level=2)` | 无 | 再读元数据；ingest 已带目录时可跳过 |
| `read_article(article_id, cursor=0, max_chars=20000, include_images=false, section=null)` | 无 | 一篇不够长则一次返回；`section`（精确或至少两字唯一前缀）只返回该节，节过长再分页 |
| `summarize_article(article_id, max_length=1200)` | 访问 DeepSeek | 不抓公众号；未缓存 / 超长 / 无密钥会明确失败 |

**明确不做**

- 只接受 `mp.weixin.qq.com` 文章链接。
- 不提供 OCR、图片理解、图片本地下载。
- 不提供知识库、全文检索、跨文章分析、内部 Agent / Run / Trace。
- 不把一篇超长文自动 MapReduce 成「看起来完整」的摘要；超过模型输入上限就明确失败。
- 不把内部 block ID、content hash 当成对外 API。

图片只在 Markdown 里保留远程 HTTPS 链接和说明文字。三个入口默认都不含图，需要时显式打开：MCP `include_images=true`，CLI `--images`，Web `GET /api/article/markdown?include_images=true`。

阅读时先看这四条，其余在 [用法](docs/用法.md)：

- 不传 `section` 且全文不超过 `max_chars`（默认 20000）就一次返回，不要先翻页。
- `section` 只返回该节；与 `cursor` 同时出现时在该节内续读。
- `cursor` 是内容块索引；只保存响应里的 `next_cursor` 做续读。
- 失败是 MCP **isError**（`code: 说明`），不是 `{success:false}`。四个工具成功体都带 `source_trust: "untrusted_web_content"`。

## 为什么这么设计

- **四个工具按副作用拆开。** 只有 `ingest_article` 联网写库；读缓存和打模型不混在一次调用里。对照那种「一个 `read_url` 什么都干」的做法，调用方能从工具名看出这次会不会写库、会不会烧 token；代价是 Agent 必须先 ingest 再 read。
- **超长文明确失败，不自动 MapReduce。** 超过 `DEEPSEEK__MAX_INPUT_CHARS` 就 `article_too_long`，交给分页阅读。代价是没有一份「看起来完整」的自动摘要，也不服务端偷偷截断再假装读完。
- **三个入口默认不含图。** 装饰图不该占 Agent 上下文；需要时显式打开。代价是默认页只留说明文字，看不到配图。
- **失败走 MCP isError，而不是 `{success:false}` 假成功体。** 宿主能把失败和正文分开，模型不该在成功通道里解析错误对象。代价是客户端必须按协议处理 isError。
- **`cursor` 是内容块索引，不是字符偏移。** 只保存响应里的 `next_cursor`。内部 block ID / content hash 不作为对外 API。代价是不能按「从第 N 个字接着读」对接。
- **CLI / Web / MCP 共用同一套服务与同一份 Markdown 投影。** 一处改分页或无图默认，三个入口一起变。代价是入口层变薄，不能为某个入口偷偷换一套 HTML 解析。
- **能读工作区的 Agent 应该导出再读文件。** MCP 分页是没有文件权限时的退路。代价是有文件权限时多一步 `fetch --export`，但整篇落盘后不必在工具调用里翻页。

## 怎么跑

Python **3.12**（`requires-python = ">=3.12,<3.13"`）。Windows / macOS / Linux 均可；命令以 PowerShell 为例。没有 DeepSeek Key 仍可抓取、阅读、导出正文。

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -e ".[web,mcp,dev]"
copy .env.example .env
python -m wechat_article_reader check
```

需要摘要时再装 `.[full,dev]`，并填写 `WECHAT_ARTICLE_READER_DEEPSEEK__API_KEY`。运行时在 `.runtime/`，导出默认在 `output/`，都不要提交。变量名与抓取/摘要上限见 [`.env.example`](.env.example) 和 [用法](docs/用法.md)。

```powershell
python -m wechat_article_reader fetch "https://mp.weixin.qq.com/s/example" --no-summary --export markdown --output .\output\article.md
python -m wechat_article_reader read "ARTICLE_UUID"
python -m wechat_article_reader web
python -m wechat_article_reader mcp-server
```

Web 三种启动（都监听 `http://127.0.0.1:8000`）：`python -m wechat_article_reader web`、Windows 双击 `run_web.pyw`（无控制台）或 `run_web.cmd`（有控制台）。MCP 给不能读本地导出文件的 Agent 用；Cursor 默认 **stdio**，也支持本机 Streamable HTTP。CLI 全量命令、Web 页面与 MCP HTTP 鉴权见 [用法](docs/用法.md)。

## 验证与 CI

[![CI](https://github.com/lhs1695/wechat-article-reader/actions/workflows/ci.yml/badge.svg)](https://github.com/lhs1695/wechat-article-reader/actions/workflows/ci.yml)

CI 跑 pytest，以及 ruff check、ruff format、mypy、pip-audit、build。

```powershell
.\.venv\Scripts\python.exe -m pytest -q
.\.venv\Scripts\ruff.exe check src tests
.\.venv\Scripts\ruff.exe format --check src tests
.\.venv\Scripts\mypy.exe src
python -m wechat_article_reader check
```

改阅读/MCP 时优先跑：`tests/test_article_reading.py`、`tests/test_mcp_reading_tools.py`。

## 文档

| 文档 | 内容 |
| --- | --- |
| [用法](docs/用法.md) | 阅读约定、CLI 全量、Web、MCP HTTP |
| [架构设计](docs/架构设计.md) | 分层、端口、缓存、阅读投影、安全 |
| [模块说明](docs/模块说明.md) | 目录与职责 |
| [diagrams](docs/diagrams/) | Archify 交互图（JSON 规格 + 交付 HTML） |

License：[MIT](LICENSE)。
