"""HTML sanitization shared by scraping, Web rendering, and exporters."""

from __future__ import annotations

from html import escape

import nh3

_ALLOWED_TAGS = {
    "a",
    "blockquote",
    "br",
    "code",
    "del",
    "div",
    "em",
    "figcaption",
    "figure",
    "h1",
    "h2",
    "h3",
    "h4",
    "h5",
    "h6",
    "hr",
    "img",
    "li",
    "ol",
    "p",
    "pre",
    "section",
    "span",
    "strong",
    "table",
    "tbody",
    "td",
    "th",
    "thead",
    "tr",
    "u",
    "ul",
}

_ALLOWED_ATTRIBUTES = {
    "*": {"class", "title"},
    "a": {"href", "title"},
    "blockquote": {"cite"},
    "img": {"alt", "height", "src", "title", "width"},
    "ol": {"start"},
    "td": {"colspan", "rowspan"},
    "th": {"colspan", "rowspan", "scope"},
}


def sanitize_html(value: str) -> str:
    """Return display-safe HTML while retaining common article markup."""
    if not value:
        return ""
    return nh3.clean(
        value,
        tags=_ALLOWED_TAGS,
        attributes=_ALLOWED_ATTRIBUTES,
        clean_content_tags={"iframe", "object", "script", "style", "svg", "template"},
        url_schemes={"http", "https", "mailto"},
        link_rel="noopener noreferrer",
    )


def escape_html(value: str) -> str:
    """Escape plain text for an HTML text or attribute context."""
    return escape(value, quote=True)
