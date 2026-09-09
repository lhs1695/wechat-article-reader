from __future__ import annotations

from wechat_article_reader.domain.entities import Article
from wechat_article_reader.domain.value_objects import ArticleContent, ArticleURL
from wechat_article_reader.features.article_reading import SummarySourceRenderer


def test_summary_source_renderer_preserves_structure_without_remote_urls() -> None:
    article = Article(
        url=ArticleURL.from_string("https://mp.weixin.qq.com/s/summary-source"),
        title="技术文章",
        content=ArticleContent.from_html(
            "<h2>架构</h2><p>阅读<a href='https://example.com/docs'>文档</a></p>"
            "<pre><code>print('ok')</code></pre>"
            "<table><tr><th>项</th></tr><tr><td>值</td></tr></table>"
            "<img src='https://mmbiz.qpic.cn/diagram.png' alt='架构图'>"
        ),
    )

    rendered = SummarySourceRenderer().render(article)

    assert "<!-- block_id:" not in rendered
    assert "## 架构" in rendered
    assert "```" in rendered
    assert "| 项 |" in rendered
    assert "[图片说明：架构图]" in rendered
    assert "https://example.com/docs" not in rendered
    assert "https://mmbiz.qpic.cn/diagram.png" not in rendered
    assert "untrusted_web_content" in rendered
