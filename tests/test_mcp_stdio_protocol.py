from __future__ import annotations

import asyncio
import base64
import hashlib
import io
import os
import sys
from pathlib import Path
from uuid import UUID

import pytest
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client
from PIL import Image

from wechat_article_reader.domain.entities import Article
from wechat_article_reader.domain.value_objects import ArticleContent, ArticleURL
from wechat_article_reader.infrastructure.persistence import Database, SqlAlchemyStorage
from wechat_article_reader.infrastructure.persistence.migration import upgrade_database
from wechat_article_reader.mcp.response_budget import MAX_TOOL_RESPONSE_BYTES

_ARTICLE_URL = "https://mp.weixin.qq.com/s/c2-stdio-fixture"
_IMAGE_URL = "https://mmbiz.qpic.cn/mmbiz_png/c2-fixture/0?wx_fmt=png"
_ARTICLE_ID = UUID("00000000-0000-0000-0000-000000000001")


def _seed_runtime(runtime: Path) -> bytes:
    runtime.mkdir(parents=True)
    content = ArticleContent.from_html(
        f"<h2>验收章节</h2><p>用于新进程 MCP 协议验收的正文。</p>"
        f'<p><img src="{_IMAGE_URL}" alt="合成测试图"></p>'
    )
    article = Article(
        id=_ARTICLE_ID,
        url=ArticleURL.from_string(_ARTICLE_URL),
        title="stdio 冷启动验收文章",
        content=content,
    )
    database_path = runtime / "wechat_article_reader.db"
    upgrade_database(database_path)
    database = Database(database_path)
    try:
        SqlAlchemyStorage(database).save(article)
    finally:
        database.dispose()

    image = Image.new("RGB", (8, 6), "#3278a0")
    stream = io.BytesIO()
    image.save(stream, format="PNG")
    image_data = stream.getvalue()
    cache_path = runtime / "images" / (hashlib.sha256(_IMAGE_URL.encode()).hexdigest() + ".image")
    cache_path.parent.mkdir(parents=True)
    cache_path.write_bytes(image_data)
    return image_data


@pytest.mark.asyncio
async def test_fresh_stdio_server_exercises_all_tools_and_image_content(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, request: pytest.FixtureRequest
) -> None:
    runtime = tmp_path / "runtime"
    monkeypatch.setenv("WECHAT_ARTICLE_READER_RUNTIME_DIR", str(runtime))
    image_data = _seed_runtime(runtime)

    async def exercise() -> None:
        env = os.environ.copy()
        env.pop("WECHAT_ARTICLE_READER_SECRETS_FILE", None)
        env["WECHAT_ARTICLE_READER_RUNTIME_DIR"] = str(runtime)
        server = StdioServerParameters(
            command=sys.executable,
            args=["-m", "wechat_article_reader.mcp", "--transport", "stdio"],
            cwd=Path(__file__).resolve().parents[1],
            env=env,
        )
        async with (
            stdio_client(server) as (read_stream, write_stream),
            ClientSession(read_stream, write_stream) as session,
        ):
            await session.initialize()
            listed = await session.list_tools()
            assert {tool.name for tool in listed.tools} == {
                "ingest_article",
                "get_cached_article",
                "read_article",
                "export_article",
                "read_article_image",
                "summarize_article",
            }

            article_id = str(_ARTICLE_ID)
            ingest = await session.call_tool("ingest_article", {"url": _ARTICLE_URL})
            assert ingest.isError is False
            assert ingest.structuredContent["article_id"] == article_id

            metadata = await session.call_tool("get_cached_article", {"article_id": article_id})
            assert metadata.isError is False
            reading_revision = metadata.structuredContent["reading_revision"]

            page = await session.call_tool(
                "read_article",
                {"article_id": article_id, "expected_reading_revision": reading_revision},
            )
            assert page.isError is False
            assert "用于新进程 MCP 协议验收的正文" in page.structuredContent["content_markdown"]
            image_id = page.structuredContent["images"]["items"][0]["image_id"]

            exported_path = tmp_path / "stdio-export.md"
            exported = await session.call_tool(
                "export_article", {"article_id": article_id, "path": str(exported_path)}
            )
            assert exported.isError is False
            assert exported_path.is_file()
            assert "content_markdown" not in exported.structuredContent

            image_result = await session.call_tool(
                "read_article_image",
                {
                    "article_id": article_id,
                    "image_id": image_id,
                    "expected_reading_revision": reading_revision,
                    "send_to_model": True,
                },
            )
            assert image_result.isError is False
            image_block = next(block for block in image_result.content if block.type == "image")
            assert base64.b64decode(image_block.data) == image_data

            summary = await session.call_tool(
                "summarize_article",
                {"article_id": "00000000-0000-0000-0000-000000000002"},
            )
            assert summary.isError
            assert summary.content

            invalid_ingest = await session.call_tool(
                "ingest_article", {"url": "https://example.com/not-a-wechat-article"}
            )
            assert invalid_ingest.isError
            assert invalid_ingest.content

            responses = (ingest, metadata, page, exported, image_result, summary, invalid_ingest)
            response_sizes = [len(result.model_dump_json().encode("utf-8")) for result in responses]
            assert max(response_sizes) <= MAX_TOOL_RESPONSE_BYTES
            request.node.user_properties.append(("max_mcp_result_bytes", str(max(response_sizes))))

    await asyncio.wait_for(exercise(), timeout=30)
