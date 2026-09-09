"""API 端点 — 所有业务操作"""

from __future__ import annotations

from pathlib import Path
from typing import Annotated, cast
from urllib.parse import urlencode

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi.responses import FileResponse

from ....infrastructure.config import Container, get_settings
from ....shared.utils.html_safety import escape_html, sanitize_html
from .models import (
    BatchRequest,
    ExportRequest,
    FetchRequest,
    FetchResponse,
    HealthResponse,
    HistoryItem,
    StatusResponse,
    SummarizeRequest,
    SummarizeResponse,
)

router = APIRouter()


def _get_container(request: Request) -> Container:
    return cast(Container, request.app.state.container)


ContainerDependency = Annotated[Container, Depends(_get_container)]


def _output_root() -> Path:
    return Path(get_settings().export.default_output_dir).resolve()


def _resolve_output_file(file: str) -> Path:
    if not file.strip():
        raise HTTPException(status_code=400, detail="文件标识不能为空")

    relative = Path(file)
    if relative.is_absolute() or ".." in relative.parts:
        raise HTTPException(status_code=400, detail="文件标识无效")

    root = _output_root()
    candidate = root.joinpath(relative)
    try:
        resolved = candidate.resolve(strict=True)
    except (FileNotFoundError, OSError) as exc:
        raise HTTPException(status_code=404, detail="文件不存在") from exc

    if not resolved.is_relative_to(root):
        raise HTTPException(status_code=400, detail="文件标识越界")
    if not resolved.is_file():
        raise HTTPException(status_code=400, detail="文件标识必须指向文件")
    return resolved


def _public_output_id(path: str | Path) -> str:
    root = _output_root()
    candidate = Path(path)
    resolved = candidate.resolve()
    if not resolved.is_relative_to(root):
        raise ValueError("导出结果不在输出目录内")
    return resolved.relative_to(root).as_posix()


@router.post("/article/fetch", response_model=FetchResponse)
def fetch_article(
    req: FetchRequest,
    container: ContainerDependency,
) -> FetchResponse:
    try:
        payload = container.article_workflow_service.fetch(req.url, content_limit=None)
        return FetchResponse(
            success=True,
            title=payload.title,
            author=str(payload.author or ""),
            account_name=str(payload.account_name or ""),
            word_count=payload.word_count,
            publish_time=payload.publish_time,
            content_html=sanitize_html(payload.content_html),
        )
    except Exception:
        raise


@router.get("/article/markdown")
def read_article_markdown(
    url: str = Query(..., min_length=1, max_length=2048),
    cursor: int = Query(0, ge=0),
    max_chars: int = Query(20_000, ge=1_000, le=20_000),
    container: ContainerDependency = None,  # type: ignore[assignment]
) -> dict[str, object]:
    """Return the same paginated Markdown projection used by the MCP reader."""
    FetchRequest(url=url)
    article_id = container.article_reading_service.ingest(url).article_id
    return {
        "success": True,
        **container.article_reading_service.read(
            article_id, cursor=cursor, max_chars=max_chars
        ).to_dict(),
    }


@router.post("/article/summarize", response_model=SummarizeResponse)
def summarize_article(
    req: SummarizeRequest,
    container: ContainerDependency,
) -> SummarizeResponse:
    try:

        def _render(md: str) -> str:
            try:
                from markdown_it import MarkdownIt

                rendered = MarkdownIt("commonmark", {"html": False}).render(md)
                return sanitize_html(rendered)
            except ImportError:
                return f"<pre>{escape_html(md)}</pre>"

        payload = container.article_workflow_service.summarize(
            req.url,
            max_length=req.max_length,
        )
        return SummarizeResponse(
            success=True,
            summary=payload.summary.overview,
            summary_html=_render(payload.summary.overview),
            key_points=list(payload.summary.key_points),
            tags=list(payload.summary.tags),
            one_sentence=payload.summary.one_sentence,
        )
    except Exception:
        raise


@router.post("/article/export", response_model=FetchResponse)
def export_article(
    req: ExportRequest,
    container: ContainerDependency,
) -> FetchResponse:
    try:
        payload = container.article_workflow_service.export(
            req.url,
            target=req.target,
            skip_summary=req.skip_summary,
            summary_content=req.summary_content,
            key_points=req.key_points,
            include_body=req.include_body,
        )
        file_id = _public_output_id(payload.export_path)
        return FetchResponse(
            success=True,
            title=f"已导出: {file_id}",
            word_count=payload.article.word_count,
            export_path=file_id,
            download_url=f"/api/article/download?{urlencode({'file': file_id})}",
        )
    except Exception:
        raise


@router.get("/article/download")
def download_article(file: str = Query(..., min_length=1)):
    """下载导出的文件"""
    from loguru import logger

    logger.info(f"下载请求: {file}")
    file_path = _resolve_output_file(file)
    return FileResponse(path=str(file_path), filename=file_path.name)


@router.post("/batch/process")
def batch_process(
    req: BatchRequest,
    container: ContainerDependency,
) -> list[dict]:
    payload = container.article_workflow_service.batch_export(
        req.urls,
        target=req.export_target,
        skip_summary=req.skip_summary,
        include_body=req.include_body,
    )
    results = []
    for item in payload.results:
        if item.success and item.export_path is not None:
            file_id = _public_output_id(item.export_path)
            results.append(
                {
                    "url": item.url,
                    "success": True,
                    "title": item.title,
                    "export_path": file_id,
                    "download_url": f"/api/article/download?{urlencode({'file': file_id})}",
                }
            )
        else:
            results.append({"url": item.url, "success": False, "error": item.error or "处理失败"})
    return results


@router.get("/history/list", response_model=list[HistoryItem])
def history_list(
    container: ContainerDependency,
) -> list[HistoryItem]:
    articles = container.storage.list_recent(50) if container.storage is not None else []
    return [
        HistoryItem(
            title=article.title,
            url=str(article.url),
            word_count=article.word_count,
            cached_at=article.updated_at.isoformat(),
            summarized=article.summary is not None,
        )
        for article in articles
    ]


@router.get("/status", response_model=StatusResponse)
def get_status(
    container: ContainerDependency,
) -> StatusResponse:
    stats = container.storage.get_stats() if container.storage else None
    return StatusResponse(
        summarizers=[n for n, s in container.summarizers.items() if s.is_available()],
        exporters=[n for n, e in container.exporters.items() if e.is_available()],
        total_cached=stats.total_entries if stats else 0,
    )


def _health_response(container: Container) -> HealthResponse:
    settings = get_settings()
    cache_ok = container.storage is not None
    deepseek_configured = bool(settings.deepseek.api_key.get_secret_value())
    return HealthResponse(
        status="ok" if cache_ok else "degraded",
        deepseek_api=deepseek_configured,
        cache=cache_ok,
        optional_components={
            "deepseek": "configured" if deepseek_configured else "not_configured",
        },
    )


@router.get("/health", response_model=HealthResponse)
def health_check(
    container: ContainerDependency,
) -> HealthResponse:
    """兼容健康检查；不访问外部网络。"""
    return _health_response(container)


@router.get("/health/live")
def health_live() -> dict[str, str]:
    return {"status": "live"}


@router.get("/health/ready", response_model=HealthResponse)
def health_ready(
    container: ContainerDependency,
) -> HealthResponse:
    response = _health_response(container)
    if not response.cache:
        raise HTTPException(status_code=503, detail="核心本地存储未就绪")
    return response
