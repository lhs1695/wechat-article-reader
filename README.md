# 微信文章阅读 MCP

[![CI](https://github.com/lhs1695/wechat-article-reader/actions/workflows/ci.yml/badge.svg)](https://github.com/lhs1695/wechat-article-reader/actions/workflows/ci.yml)

给 Agent 的微信公众号阅读 MCP（个人项目）。一篇公众号 URL → SQLite → **同一套 Markdown 投影** → 导出。摘要可选，没有 API Key 也能导出正文。

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
| 本地 Agent（要整篇 md） | MCP：`ingest_article` → `export_article`（写到绝对路径，不回传正文） |
| 本地 / 远程 Agent（只要进上下文） | MCP：`read_article` 按页或按节；通读概览用 `summarize_article` |

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

`ingest_article` 返回 `article_id`、`title`、`word_count`、`block_count`，以及默认 H2 的 `sections_page` / `images_page`（本地目录没有 H2 时对齐到 H3）。两份清单默认各返回 20 项；大清单按各自的 `next` 参数调用 `get_cached_article` 续取。失败不走这种成功体，见下「为什么这么设计」。

以上是历史实调。当前响应含 `revision`、`next_read`、有界的 `sections_in_page`、分页图片引用及图片阅读状态。正文字符数不等于传输预算：完整 FastMCP 结果按 UTF-8 序列化最多 128 KiB，超预算会缩小当前页；续读请直接使用 `next_read` 中的完整参数。完整契约见 [用法](docs/用法.md)。

## 工具表与明确不做

| 工具 | 副作用 | 作用 |
| --- | --- | --- |
| `ingest_article(url, refresh=false, toc_level=2, sections_limit=20, images_limit=20)` | 联网 + 写 SQLite | 唯一抓公众号入口；章节和图片清单分别分页 |
| `get_cached_article(article_id, toc_level=2, sections_cursor=null, images_cursor=null, sections_limit=20, images_limit=20)` | 无 | 只读元数据；按章节和图片游标独立续取 |
| `read_article(article_id, cursor=0, max_chars=20000, include_images=false, section=null, section_start_cursor=null, expected_revision=null)` | 无 | 把一页或一节读进模型上下文；完整 MCP 响应最多 128 KiB，必要时缩小正文页；续读原样使用 `next_read` |
| `export_article(article_id, path, include_images=false, download_images=false)` | 写盘；显式下载时联网 | 导出 Markdown；可同时导出 `.assets` 图片和清单，成功体不含正文 |
| `read_article_image(article_id, image_id, expected_revision, download_image=false, send_to_model=false)` | 显式下载时联网/写图片缓存；显式提供时返回图像 | 默认仅元数据，图片编号来自阅读页/元数据；客户端需支持视觉 |
| `summarize_article(article_id, max_length=1200)` | 访问 DeepSeek | 不抓公众号；未缓存 / 超长 / 无密钥会明确失败 |

**明确不做**

- 只接受 `mp.weixin.qq.com` 文章链接。
- 不自动调用视觉模型或执行 OCR；可以下载配图，并显式向支持视觉的 Agent 提供图片内容。
- 不提供知识库、全文检索、跨文章分析、内部 Agent / Run / Trace。
- 不把一篇超长文自动 MapReduce 成「看起来完整」的摘要；超过模型输入上限就明确失败。
- 不把内部 block ID、content hash 当成对外 API。

图片只在 Markdown 里保留远程 HTTPS 链接和说明文字。三个入口默认都不含图，需要时显式打开：MCP `include_images=true`，CLI `--images`，Web `GET /api/article/markdown?include_images=true`。

Markdown 导出保留代码缩进、空行、语言标记、嵌套列表、引用与行内格式。普通长段落的内部分块会合并；代码和表格保持完整。目标格式为 GFM；合并单元格等复杂表格保留经过清洗的 HTML，阅读器需要支持 HTML 才能显示这些布局。

阅读时先看这几条，其余在 [用法](docs/用法.md)：

- 要整篇 md 落盘：`ingest_article` 之后 `export_article`，不要用 `read_article` 翻页拼接。
- 不传 `section` 且全文不超过 `max_chars`（默认 20000）就一次返回，不要先翻页。
- `section` 返回该章及其子章节；重名标题用目录中的 `start_cursor` 作为 `section_start_cursor`。
- `cursor` 是内容块索引；续读原样传响应中的 `next_read`（包含 `expected_revision`）。文章变化返回 `article_changed`，重新获取目录后阅读。
- 失败是 MCP **isError**（`code: 说明`），不是 `{success:false}`。工具成功体均标记 `source_trust: "untrusted_web_content"`；图片工具用文本元数据加可选图像内容块。

## 为什么这么设计

- **工具按副作用拆开。** `ingest_article` 抓文章写库；`read_article` 只读文字；导出和图片工具仅在显式允许时下载配图；`summarize_article` 调用文本摘要模型。
- **超长文明确失败，不自动 MapReduce。** 超过 `DEEPSEEK__MAX_INPUT_CHARS` 就 `article_too_long`，交给分页阅读或 `export_article`。代价是没有一份「看起来完整」的自动摘要，也不服务端偷偷截断再假装读完。
- **三个入口默认不含图。** 装饰图不该占 Agent 上下文；需要时显式打开。代价是默认页只留说明文字，看不到配图。
- **失败走 MCP isError，而不是 `{success:false}` 假成功体。** 宿主能把失败和正文分开，模型不该在成功通道里解析错误对象。代价是客户端必须按协议处理 isError。
- **`cursor` 是内容块索引。** 与不透明 `revision` 绑定；续读使用 `next_read`。内部 block ID / content hash 不作为对外 API。
- **CLI / Web / MCP 共用同一套服务与同一份 Markdown 投影。** 一处改分页或无图默认，三个入口一起变。代价是入口层变薄，不能为某个入口偷偷换一套 HTML 解析。
- **整篇 md 走 `export_article`，分页是读接口。** 本地 Agent 要文件时 MCP 写盘并只返回路径，避免把全文塞进 tool 结果。`read_article` 管进上下文的一页/一节。人用 CLI `fetch --export` 仍然可用。
- **缓存摘要绑定正文版本。** 刷新文章后，旧正文的摘要不会再显示为当前摘要；需要时重新调用 `summarize_article`。
- **图片链接不代表读过图片。** 阅读页报告图片总数、省略数及 `image_content_read=false`，需要图片信息时另行查看原图。
- **显示、下载、提供给视觉模型独立。** `include_images` 控制 Markdown 标记，`download_images` 控制离线资源，`send_to_model` 控制 MCP 图像内容。全部默认关闭；下载失败明确标记，完整图文阅读须核对图片清单。示例见 [用法](docs/用法.md)。
- **保留原文结尾。** 不再按短数字、大写词或推荐墙猜测删除正文；疑似推荐内容通过 `content_notes` 提示。

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

Web 三种启动（都监听 `http://127.0.0.1:8000`）：`python -m wechat_article_reader web`、Windows 双击 `run_web.pyw`（无控制台）或 `run_web.cmd`（有控制台）。MCP 给 Agent：整篇 md 用 `export_article`，进上下文用 `read_article`。Cursor 默认 **stdio**，也支持本机 Streamable HTTP。CLI 全量命令、Web 页面与 MCP HTTP 鉴权见 [用法](docs/用法.md)。

远程 MCP HTTP 监听必须显式启用并配置令牌；远程 `export_article` 只能写到配置的导出目录内。普通文章文字会作为 Markdown 字面文本输出，只有真实 HTML 链接会生成 Markdown 链接。

## 验证与 CI

CI 两 job：`test` 跑 pytest；`quality` 是八道门——依赖 hash 锁定、ruff check、ruff format、mypy、`pip check`、pip-audit、构建、安装冒烟。

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
