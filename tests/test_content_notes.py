from __future__ import annotations

from pathlib import Path
from uuid import uuid4

from wechat_article_reader.domain.entities import Article, ArticleSource
from wechat_article_reader.domain.value_objects import ArticleContent, ArticleURL, ContentNote
from wechat_article_reader.features.article_reading import (
    ArticleReadingService,
    SummarySourceRenderer,
)
from wechat_article_reader.infrastructure.adapters.exporters.markdown import MarkdownExporter
from wechat_article_reader.infrastructure.persistence.database import Database
from wechat_article_reader.infrastructure.persistence.migration import upgrade_database
from wechat_article_reader.infrastructure.persistence.storage import SqlAlchemyStorage


def _article() -> Article:
    return Article(
        id=uuid4(),
        url=ArticleURL.from_string("https://mp.weixin.qq.com/s/known-limits"),
        title="已知限制样本",
        content=ArticleContent.from_html(
            '<p>可读正文。</p><img src="https://mmbiz.qpic.cn/a.png" alt="图注">'
        ),
        source=ArticleSource.wechat(
            "测试公众号",
            content_notes=(
                ContentNote(
                    "embedded_media_omitted",
                    "正文中的 SVG 或嵌入式媒体未纳入安全阅读内容",
                    "article",
                ),
            ),
        ),
    )


def test_capture_notes_survive_cache_and_match_read_and_summary_scope(tmp_path: Path) -> None:
    database_path = tmp_path / "notes.sqlite3"
    upgrade_database(database_path)
    storage = SqlAlchemyStorage(Database(database_path))
    original = _article()
    storage.save(original)
    restored = storage.get(original.id)
    assert restored is not None

    service = ArticleReadingService(workflow=None, storage=storage)  # type: ignore[arg-type]
    metadata = service.get_metadata(original.id)
    page = service.read(original.id)
    prompt_source = SummarySourceRenderer().render(restored)

    assert [note.code for note in metadata.content_notes] == [
        "embedded_media_omitted",
        "images_not_read",
    ]
    assert page.content_notes[0].to_dict() == metadata.content_notes[0].to_dict()
    assert page.content_notes[-1].code == "images_not_read"
    assert "已知采集限制" in prompt_source
    assert "图片没有被视觉分析" in prompt_source


def test_markdown_export_reports_capture_and_image_limits(tmp_path: Path) -> None:
    output = tmp_path / "limits.md"
    MarkdownExporter().export(_article(), str(output))

    markdown = output.read_text(encoding="utf-8")
    assert "## 内容范围与限制" in markdown
    assert "正文中的 SVG 或嵌入式媒体" in markdown
    assert "图片内容未被读取或视觉分析" in markdown
    assert "不代表对全文完整性的判断" in markdown
