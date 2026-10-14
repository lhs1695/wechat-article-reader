"""HTML sanitization shared by scraping, Web rendering, and exporters."""

from __future__ import annotations

import re
from html import escape

import nh3
from bs4 import BeautifulSoup

_ALLOWED_TAGS = {
    "a",
    "b",
    "i",
    "s",
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
    "sub",
    "sup",
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
    "code": {"data-lang"},
    "pre": {"data-lang"},
    "td": {"colspan", "rowspan"},
    "th": {"colspan", "rowspan", "scope"},
}


def preserve_style_semantics(soup: BeautifulSoup) -> None:
    """Represent known text styles with semantic tags before stripping CSS."""
    for tag in soup.find_all(style=True):
        style = str(tag.get("style", "")).lower()
        wrappers = []
        weight = re.search(r"font-weight\s*:\s*(bold|[6-9]00)\b", style)
        if weight and tag.name not in {"strong", "b"}:
            wrappers.append("strong")
        if re.search(r"font-style\s*:\s*italic\b", style) and tag.name not in {"em", "i"}:
            wrappers.append("em")
        if re.search(r"text-decoration(?:-line)?\s*:[^;]*line-through", style):
            wrappers.append("del")
        if re.search(r"text-decoration(?:-line)?\s*:[^;]*underline", style):
            wrappers.append("u")
        for name in wrappers:
            wrapper = soup.new_tag(name)
            for child in list(tag.contents):
                wrapper.append(child.extract())
            tag.append(wrapper)


def sanitize_html(value: str) -> str:
    """Return display-safe HTML while retaining common article markup."""
    if not value:
        return ""
    soup = BeautifulSoup(value, "html.parser")
    preserve_style_semantics(soup)
    return nh3.clean(
        str(soup),
        tags=_ALLOWED_TAGS,
        attributes=_ALLOWED_ATTRIBUTES,
        clean_content_tags={"iframe", "object", "script", "style", "svg", "template"},
        url_schemes={"http", "https", "mailto"},
        link_rel="noopener noreferrer",
    )


def escape_html(value: str) -> str:
    """Escape plain text for an HTML text or attribute context."""
    return escape(value, quote=True)
