from __future__ import annotations

from wechat_article_reader.shared.utils.html_safety import escape_html, sanitize_html


def test_sanitize_html_removes_executable_content() -> None:
    dirty = (
        '<script>alert(1)</script><svg onload="alert(2)"></svg>'
        '<a href="javascript:alert(3)" onclick="alert(4)">link</a>'
        '<img src="https://example.com/a.png" onerror="alert(5)"><p>safe</p>'
    )

    cleaned = sanitize_html(dirty)

    assert "script" not in cleaned
    assert "svg" not in cleaned
    assert "javascript:" not in cleaned
    assert "onclick" not in cleaned
    assert "onerror" not in cleaned
    assert "<p>safe</p>" in cleaned


def test_escape_html_handles_text_and_attributes() -> None:
    assert escape_html('"<unsafe>&') == "&quot;&lt;unsafe&gt;&amp;"
