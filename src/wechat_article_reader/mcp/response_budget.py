"""Bound complete MCP text responses and page cached article metadata."""

from __future__ import annotations

import base64
import json
from dataclasses import dataclass
from typing import Any

import pydantic_core
from mcp.types import CallToolResult, TextContent

from ..features.article_reading.projection import PROJECTION_FORMAT_VERSION
from ..shared.exceptions import ArticleVersionChangedError
from .input_validator import MCPValidationError

MAX_TOOL_RESPONSE_BYTES = 128 * 1024
DEFAULT_METADATA_PAGE_SIZE = 20
MAX_METADATA_PAGE_SIZE = 100
MAX_METADATA_CURSOR_LENGTH = 2048


@dataclass(frozen=True)
class MetadataPosition:
    offset: int
    scope_start: int
    scope_end: int


def serialized_tool_result_size(payload: dict[str, Any]) -> int:
    """Measure the full FastMCP text plus structured result envelope in UTF-8 bytes."""
    text = pydantic_core.to_json(payload, fallback=str, indent=2).decode("utf-8")
    result = CallToolResult(
        content=[TextContent(type="text", text=text)],
        structuredContent=payload,
    )
    return len(result.model_dump_json().encode("utf-8"))


def decode_metadata_cursor(
    token: str | None,
    *,
    article_id: str,
    article_revision: str,
    kind: str,
    total_items: int,
    toc_level: int = 2,
) -> MetadataPosition:
    if token is None:
        return MetadataPosition(0, 0, total_items)
    if not isinstance(token, str) or not 1 <= len(token) <= MAX_METADATA_CURSOR_LENGTH:
        raise MCPValidationError("元数据游标无效；请重新调用 get_cached_article")
    try:
        prefix, encoded = token.split(".", 1)
        if prefix != "m1":
            raise ValueError
        raw = base64.urlsafe_b64decode(encoded + "=" * (-len(encoded) % 4))
        data = json.loads(raw)
    except (ValueError, UnicodeDecodeError, json.JSONDecodeError):
        raise MCPValidationError("元数据游标无效；请重新调用 get_cached_article") from None
    if not isinstance(data, dict) or set(data) != {"a", "r", "p", "k", "o", "s", "e", "t"}:
        raise MCPValidationError("元数据游标无效；请重新调用 get_cached_article")
    if data["a"] != article_id or data["k"] != kind:
        raise MCPValidationError("元数据游标不属于此文章或清单；请重新获取元数据")
    if kind == "sections" and data["t"] != toc_level:
        raise MCPValidationError("章节目录深度已变化；请重新获取 metadata 后续取")
    if data["r"] != article_revision or data["p"] != PROJECTION_FORMAT_VERSION:
        raise ArticleVersionChangedError(article_revision)
    values = (data["o"], data["s"], data["e"])
    if any(type(value) is not int for value in values):
        raise MCPValidationError("元数据游标位置无效；请重新获取元数据")
    offset, scope_start, scope_end = values
    if not 0 <= scope_start <= offset <= scope_end <= total_items:
        raise MCPValidationError("元数据游标范围无效；请重新获取元数据")
    return MetadataPosition(offset, scope_start, scope_end)


def encode_metadata_cursor(
    *,
    article_id: str,
    article_revision: str,
    kind: str,
    offset: int,
    scope_start: int,
    scope_end: int,
    toc_level: int = 2,
) -> str:
    raw = json.dumps(
        {
            "a": article_id,
            "r": article_revision,
            "p": PROJECTION_FORMAT_VERSION,
            "k": kind,
            "o": offset,
            "s": scope_start,
            "e": scope_end,
            "t": toc_level,
        },
        ensure_ascii=True,
        separators=(",", ":"),
    ).encode("ascii")
    return "m1." + base64.urlsafe_b64encode(raw).decode("ascii").rstrip("=")


def metadata_page(
    *,
    items: list[dict[str, Any]],
    items_offset: int = 0,
    position: MetadataPosition,
    limit: int,
    article_id: str,
    article_revision: str,
    kind: str,
    toc_level: int,
    sections_limit: int,
    images_limit: int,
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    end = min(position.offset + limit, position.scope_end)
    local_start = position.offset - items_offset
    local_end = end - items_offset
    page_items = items[local_start:local_end]
    next_value: dict[str, Any] | None = None
    if end < position.scope_end:
        cursor = encode_metadata_cursor(
            article_id=article_id,
            article_revision=article_revision,
            kind=kind,
            offset=end,
            scope_start=position.scope_start,
            scope_end=position.scope_end,
            toc_level=toc_level,
        )
        arguments: dict[str, Any] = {
            "article_id": article_id,
            "toc_level": toc_level,
            "sections_limit": sections_limit,
            "images_limit": images_limit,
            f"{kind}_cursor": cursor,
        }
        next_value = {"tool": "get_cached_article", "arguments": arguments}
    return (
        {
            "items": page_items,
            "total_items": position.scope_end - position.scope_start,
            "range_start": position.offset - position.scope_start,
            "range_end": end - position.scope_start,
            "next": next_value,
        },
        page_items,
    )


def fit_metadata_response(
    payload: dict[str, Any],
    *,
    page_states: list[tuple[str, str, list[dict[str, Any]], MetadataPosition]],
    article_id: str,
    article_revision: str,
    toc_level: int,
    sections_limit: int,
    images_limit: int,
    max_bytes: int = MAX_TOOL_RESPONSE_BYTES,
) -> dict[str, Any]:
    """Shrink page sizes until the exact modeled MCP result stays within budget."""
    while serialized_tool_result_size(payload) > max_bytes:
        choices = [state for state in page_states if state[2]]
        if not choices:
            raise MCPValidationError("文章元数据的固定字段已超过 MCP 响应预算；请缩短源字段后重试")
        # Remove the page whose final item saves the most UTF-8 JSON bytes.
        kind, page_key, visible, position = max(
            choices,
            key=lambda state: len(
                json.dumps(state[2][-1], ensure_ascii=False, separators=(",", ":")).encode("utf-8")
            ),
        )
        del visible[-1]
        page = payload[page_key]
        page["range_end"] -= 1
        end = position.scope_start + page["range_end"]
        page["next"] = None
        if end < position.scope_end:
            cursor = encode_metadata_cursor(
                article_id=article_id,
                article_revision=article_revision,
                kind=kind,
                offset=end,
                scope_start=position.scope_start,
                scope_end=position.scope_end,
                toc_level=toc_level,
            )
            page["next"] = {
                "tool": "get_cached_article",
                "arguments": {
                    "article_id": article_id,
                    "toc_level": toc_level,
                    "sections_limit": sections_limit,
                    "images_limit": images_limit,
                    f"{kind}_cursor": cursor,
                },
            }
    return payload


def fit_read_response(
    payload: dict[str, Any],
    *,
    article_id: str,
    article_revision: str,
    image_position: MetadataPosition,
    toc_level: int,
    sections_limit: int,
    images_limit: int,
    max_bytes: int = MAX_TOOL_RESPONSE_BYTES,
) -> dict[str, Any]:
    """Trim inline metadata before asking the caller to read fewer characters."""
    while serialized_tool_result_size(payload) > max_bytes:
        images = payload["images"]["items"]
        sections = payload["sections_in_page"]
        candidates: list[tuple[str, dict[str, Any]]] = []
        if images:
            candidates.append(("images", images[-1]))
        if sections:
            candidates.append(("sections", sections[-1]))
        if not candidates:
            return payload
        kind, _ = max(
            candidates,
            key=lambda item: len(
                json.dumps(item[1], ensure_ascii=False, separators=(",", ":")).encode("utf-8")
            ),
        )
        if kind == "sections":
            sections.pop()
            payload["omitted_sections"] = payload.get("omitted_sections", 0) + 1
            continue

        images.pop()
        page = payload["images"]
        page["range_end"] -= 1
        next_offset = image_position.scope_start + page["range_end"]
        page["next"] = None
        if next_offset < image_position.scope_end:
            cursor = encode_metadata_cursor(
                article_id=article_id,
                article_revision=article_revision,
                kind="images",
                offset=next_offset,
                scope_start=image_position.scope_start,
                scope_end=image_position.scope_end,
                toc_level=toc_level,
            )
            page["next"] = {
                "tool": "get_cached_article",
                "arguments": {
                    "article_id": article_id,
                    "toc_level": toc_level,
                    "sections_limit": sections_limit,
                    "images_limit": images_limit,
                    "images_cursor": cursor,
                },
            }
    return payload


def truncate_utf8(value: str, max_bytes: int) -> tuple[str, bool]:
    encoded = value.encode("utf-8")
    if len(encoded) <= max_bytes:
        return value, False
    suffix = "…"
    prefix = encoded[: max(0, max_bytes - len(suffix.encode("utf-8")))].decode(
        "utf-8", errors="ignore"
    )
    return prefix + suffix, True
