"""CLI主应用 - 基于Click和Rich"""

from __future__ import annotations

import sys
from datetime import UTC
from importlib.util import find_spec
from pathlib import Path

import click
from rich.console import Console
from rich.panel import Panel
from rich.progress import Progress, ProgressColumn, SpinnerColumn, TextColumn
from rich.table import Table

from ...infrastructure.config import get_container, get_settings
from ...shared.constants import DEFAULT_MCP_HTTP_PORT, VERSION
from ...shared.utils import setup_logger

console = Console()
EXPORT_CHOICES = ("html", "markdown")


def _configure_utf8_standard_streams() -> None:
    """Use UTF-8 at the CLI boundary without assuming a particular terminal."""

    for stream in (sys.stdout, sys.stderr):
        reconfigure = getattr(stream, "reconfigure", None)
        if callable(reconfigure):
            try:
                reconfigure(encoding="utf-8", errors="backslashreplace")
            except (OSError, ValueError):
                # Embedded interpreters and test capture streams may not permit
                # reconfiguration. Their existing encoding remains usable.
                continue


def _console_supports_unicode_progress(target_console: Console) -> bool:
    """Detect whether the active console can safely render Rich spinners."""

    encoding = getattr(target_console.file, "encoding", None) or sys.stdout.encoding or ""
    normalized = encoding.lower().replace("_", "-")
    return normalized.startswith("utf") or normalized == "cp65001"


def _create_single_progress(target_console: Console | None = None) -> Progress:
    """Build a fetch progress view that degrades safely on legacy consoles."""

    active_console = target_console or console
    columns: list[ProgressColumn] = [TextColumn("[progress.description]{task.description}")]

    if _console_supports_unicode_progress(active_console):
        columns.insert(0, SpinnerColumn())

    return Progress(*columns, console=active_console)


def _console_safe_text(text: str, target_console: Console | None = None) -> str:
    """Best-effort text sanitization for legacy console encodings."""

    active_console = target_console or console
    encoding = getattr(active_console.file, "encoding", None) or sys.stdout.encoding or "utf-8"

    try:
        text.encode(encoding)
        return text
    except UnicodeEncodeError:
        return text.encode(encoding, errors="replace").decode(encoding)


def _process_single(
    url: str,
    no_summary: bool = False,
    export: str | None = None,
    output: str | None = None,
) -> None:
    """抓取并处理单篇文章（CLI/别名命令复用）"""
    container = get_container()

    with _create_single_progress() as progress:
        task = progress.add_task("正在处理文章...", total=None)
        try:
            result = container.article_workflow_service.process(
                url,
                summarize=not no_summary,
                target=export,
                path=output,
                continue_on_summary_error=True,
            )
        except Exception as e:
            console.print(f"[red]处理失败: {e}")
            sys.exit(1)

        if result.summary_error:
            console.print(f"[yellow]摘要生成失败: {result.summary_error}")
        if result.export_path:
            progress.update(task, description=f"[green]已导出: {result.export_path}")
        else:
            progress.update(task, description="[green]处理完成")

    # 显示结果
    _display_article(result)


@click.group()
@click.version_option(VERSION, prog_name="wechat-article-reader")
@click.option("--debug", is_flag=True, help="启用调试模式")
def cli(debug: bool):
    """微信公众号文章阅读与摘要服务 - 命令行工具"""
    _configure_utf8_standard_streams()
    log_level = "DEBUG" if debug else "INFO"
    setup_logger(level=log_level)


@cli.group(name="db")
def db_commands() -> None:
    """管理 SQLite Schema。"""


@db_commands.command(name="upgrade")
@click.option("--revision", default="head", show_default=True)
def db_upgrade(revision: str) -> None:
    """升级数据库 Schema。"""
    from ...infrastructure.persistence import upgrade_database

    try:
        upgrade_database(revision=revision)
    except Exception as exc:
        raise click.ClickException(f"数据库升级失败: {exc}") from exc
    console.print(f"[green]数据库已升级到 {revision}[/green]")


@db_commands.command(name="current")
def db_current() -> None:
    """显示当前数据库版本。"""
    from ...infrastructure.persistence import current_revision

    revision = current_revision()
    console.print(revision or "未初始化")


@db_commands.command(name="downgrade")
@click.option("--revision", default="-1", show_default=True)
def db_downgrade(revision: str) -> None:
    """回滚数据库 Schema；默认回滚一个版本。"""
    from ...infrastructure.persistence import downgrade_database

    try:
        downgrade_database(revision=revision)
    except Exception as exc:
        raise click.ClickException(f"数据库回滚失败: {exc}") from exc
    console.print(f"[green]数据库已回滚到 {revision}[/green]")


@cli.command()
@click.argument("url")
@click.option("--no-summary", is_flag=True, help="不生成摘要")
@click.option(
    "--export",
    "-e",
    type=click.Choice(EXPORT_CHOICES),
    help="导出格式",
)
@click.option("--output", "-o", type=click.Path(), help="输出文件路径")
def fetch(url: str, no_summary: bool, export: str | None, output: str | None):
    """
    抓取并处理单篇文章

    示例:
        wechat-article-reader fetch https://mp.weixin.qq.com/s/xxx
        wechat-article-reader fetch URL -e markdown -o output.md
    """
    _process_single(url, no_summary=no_summary, export=export, output=output)


@cli.command(name="read")
@click.argument("source")
@click.option("--refresh", is_flag=True, help="URL 输入时重新抓取并更新缓存")
@click.option("--cursor", type=click.IntRange(min=0), default=0, show_default=True)
@click.option("--section", default=None, help="按章节标题跳读；与 --cursor 同时出现时以本节为准")
@click.option(
    "--max-chars",
    type=click.IntRange(min=1_000, max=20_000),
    default=8_000,
    show_default=True,
    help="单页 Markdown 最大字符数",
)
@click.option(
    "--output-format",
    type=click.Choice(["text", "json"]),
    default="text",
    show_default=True,
)
def read_article_command(
    source: str,
    refresh: bool,
    cursor: int,
    section: str | None,
    max_chars: int,
    output_format: str,
) -> None:
    """从 URL 或缓存文章 UUID 输出适合 Agent 阅读的 Markdown。"""
    import json as json_lib
    from uuid import UUID

    service = get_container().article_reading_service
    is_url = source.startswith(("https://", "http://"))
    if refresh and not is_url:
        raise click.UsageError("--refresh 仅适用于 URL 输入")

    try:
        if is_url:
            article_id = service.ingest(source, refresh=refresh).article_id
        else:
            article_id = str(UUID(source))

        page = service.read(article_id, cursor=cursor, max_chars=max_chars, section=section)
        if output_format == "json":
            payload = {"success": True, **page.to_dict()}
            click.echo(json_lib.dumps(payload, ensure_ascii=True, indent=2))
            return
        click.echo(_console_safe_text(page.content_markdown))
        if page.has_more:
            click.echo(
                f"\n---\n继续读取: wechat-article-reader read {article_id} "
                f"--cursor {page.next_cursor} --max-chars {max_chars}"
            )
    except Exception as exc:
        raise click.ClickException(str(exc)) from exc


@cli.command()
def web():
    """启动 Web 界面"""
    try:
        from ..web import run

        run()
    except ImportError as e:
        console.print(f"[red]Web 启动失败（请安装 fastapi uvicorn）: {e}")
        sys.exit(1)


@cli.command()
@click.argument("urls", nargs=-1, required=False)
@click.option("--no-summary", is_flag=True, help="不生成摘要")
@click.option(
    "--export",
    "-e",
    type=click.Choice(EXPORT_CHOICES),
    help="导出格式",
)
@click.option("--output-dir", "-o", type=click.Path(), help="输出目录")
@click.option(
    "--input-file", "-f", type=click.Path(exists=True), help="从文件读取URL列表（每行一个URL）"
)
@click.option("--from-clipboard", is_flag=True, help="从剪贴板读取URL")
@click.option(
    "--output-format", type=click.Choice(["text", "json"]), default="text", help="输出格式"
)
@click.option("--quiet", "-q", is_flag=True, help="静默模式")
def batch(
    urls: tuple[str, ...],
    no_summary: bool,
    export: str | None,
    output_dir: str | None,
    input_file: str | None,
    from_clipboard: bool,
    output_format: str,
    quiet: bool,
):
    """
    批量处理多篇文章

    示例:
        wechat-article-reader batch URL1 URL2 URL3
        wechat-article-reader batch -f urls.txt -e markdown -o ./output
        wechat-article-reader batch --from-clipboard
    """
    import json as json_lib

    # 收集 URLs
    url_list: list[str] = list(urls) if urls else []

    # 从文件读取
    if input_file:
        with open(input_file, encoding="utf-8") as f:
            file_urls = [line.strip() for line in f if line.strip() and not line.startswith("#")]
            url_list.extend(file_urls)

    # 从剪贴板读取（跨平台）
    if from_clipboard:
        try:
            import platform
            import subprocess

            system = platform.system()
            if system == "Windows":
                cmd = ["powershell", "-command", "Get-Clipboard"]
            elif system == "Darwin":
                cmd = ["pbpaste"]
            else:
                # Linux 优先使用 xclip，回退 xsel
                cmd = ["xclip", "-selection", "clipboard", "-o"]

            clipboard_result = subprocess.run(cmd, capture_output=True, text=True)
            if clipboard_result.returncode == 0:
                clipboard_urls = [
                    line.strip()
                    for line in clipboard_result.stdout.split("\n")
                    if line.strip() and ("http://" in line or "https://" in line)
                ]
                url_list.extend(clipboard_urls)
        except Exception as e:
            if not quiet:
                console.print(f"[yellow]读取剪贴板失败: {e}[/yellow]")

    if not url_list:
        console.print("[red]没有提供 URL，请通过参数、--input-file 或 --from-clipboard 提供[/red]")
        sys.exit(1)

    container = get_container()

    if not quiet:
        console.print(f"[bold]开始批量处理 {len(url_list)} 篇文章...[/bold]")

    success_count = 0
    failed_count = 0
    results_data = []
    exported_files = []

    with Progress(console=console, disable=quiet) as progress:
        task = progress.add_task("处理中...", total=len(url_list))
        batch_payload = container.article_workflow_service.batch_process(
            url_list,
            summarize=not no_summary,
            target=export,
            path=output_dir,
            continue_on_summary_error=True,
        )

        for item in batch_payload.results:
            url = item.url
            if item.success and item.result is not None:
                payload = item.result
                success_count += 1

                result_entry: dict[str, object] = {
                    "url": url,
                    "title": payload.article.title,
                    "success": True,
                    "word_count": payload.article.word_count,
                    "author": payload.article.author,
                    "account_name": payload.article.account_name,
                    "publish_time": payload.article.publish_time,
                }
                if payload.summary:
                    result_entry["summary"] = payload.summary.overview
                    result_entry["key_points"] = list(payload.summary.key_points)
                    result_entry["tags"] = list(payload.summary.tags)
                if payload.summary_error:
                    result_entry["summary_error"] = payload.summary_error
                if payload.export_path:
                    exported_files.append(payload.export_path)
                results_data.append(result_entry)

                if not quiet:
                    console.print(f"[green]OK[/green] {payload.article.title[:40]}...")
            else:
                failed_count += 1
                results_data.append(
                    {
                        "url": url,
                        "success": False,
                        "error": item.error or "未知错误",
                    }
                )
                if not quiet:
                    console.print(f"[red]ERR[/red] {url[:50]}... - {item.error}")

            progress.advance(task)

    # 输出结果
    if output_format == "json":
        from datetime import datetime

        output_data = {
            "timestamp": datetime.now(UTC).isoformat(),
            "success_count": success_count,
            "failed_count": failed_count,
            "total": len(url_list),
            "results": results_data,
            "exported_files": exported_files,
        }
        console.print(json_lib.dumps(output_data, ensure_ascii=False, indent=2))
    elif not quiet:
        console.print(f"\n[bold]处理完成:[/bold] 成功 {success_count}, 失败 {failed_count}")


@cli.command()
@click.option("--json", "output_json", is_flag=True, help="以 JSON 格式输出")
def config(output_json: bool):
    """显示当前配置"""
    import json as json_lib

    settings = get_settings()

    config_data = {
        "调试模式": settings.debug,
        "日志级别": settings.log_level,
        "DeepSeek模型": settings.deepseek.model,
        "默认输出目录": settings.export.default_output_dir,
    }

    if output_json:
        console.print(json_lib.dumps(config_data, ensure_ascii=False, indent=2))
        return

    table = Table(title="当前配置")
    table.add_column("配置项", style="cyan")
    table.add_column("值", style="green")

    for key, value in config_data.items():
        table.add_row(key, str(value))

    console.print(table)


@cli.command(name="config-init")
def config_init():
    """交互式配置向导"""
    from pathlib import Path

    console.print("[bold]🔧 配置向导[/bold]\n")

    console.print("[cyan]1. DeepSeek 配置（可选）[/cyan]")
    deepseek_key = click.prompt(
        "DeepSeek API Key",
        default="",
        show_default=False,
        hide_input=True,
    )

    # 输出目录
    console.print("\n[cyan]2. 导出配置[/cyan]")
    output_dir = click.prompt(
        "默认输出目录",
        default="./output",
        show_default=True,
    )

    # 生成 .env 文件
    env_content = f"""# WeChat Article Reader 配置
# DeepSeek
WECHAT_ARTICLE_READER_DEEPSEEK__API_KEY={deepseek_key}

# 导出
WECHAT_ARTICLE_READER_EXPORT__DEFAULT_OUTPUT_DIR={output_dir}
"""

    env_path = Path(".env")
    if env_path.exists() and not click.confirm("\n.env 文件已存在，是否覆盖？"):
        console.print("[已取消]")
        return

    env_path.write_text(env_content, encoding="utf-8")
    console.print(f"\n[green]OK 配置已保存到 {env_path.absolute()}[/green]")
    console.print("[dim]提示: 重新运行命令以应用新配置[/dim]")


def cache_clean(clean_all: bool, expired: bool):
    """清理本地缓存"""
    container = get_container()
    storage = container.storage

    if storage is None:
        console.print("[yellow]缓存存储不可用[/yellow]")
        return

    if clean_all:
        clear_all = getattr(storage, "clear_all", None)
        if not callable(clear_all):
            console.print("[yellow]当前缓存存储不支持 clear_all[/yellow]")
            return
        count = clear_all()
        console.print(f"[green]已清理 {count} 条缓存[/green]")
    else:
        cleanup_expired = getattr(storage, "cleanup_expired", None)
        if not callable(cleanup_expired):
            console.print("[yellow]当前缓存存储不支持 cleanup_expired[/yellow]")
            return
        count = cleanup_expired()
        if count > 0:
            console.print(f"[green]已清理 {count} 条过期缓存[/green]")
        else:
            console.print("[dim]没有过期缓存需要清理[/dim]")


@cli.command(name="cache-stats")
def cache_stats():
    """显示缓存统计信息"""
    container = get_container()
    storage = container.storage

    if storage is None:
        console.print("[yellow]缓存存储不可用[/yellow]")
        return

    stats = storage.get_stats()

    table = Table(title="缓存统计")
    table.add_column("统计项", style="cyan")
    table.add_column("值", style="green")

    table.add_row("文章数", str(stats.total_entries))
    table.add_row("摘要条数", str(stats.summary_entries))

    size_mb = stats.total_size_bytes / (1024 * 1024)
    if size_mb >= 1:
        size_str = f"{size_mb:.2f} MB"
    else:
        size_kb = stats.total_size_bytes / 1024
        size_str = f"{size_kb:.2f} KB"
    table.add_row("库文件大小", size_str)

    oldest = stats.created_at_min.strftime("%Y-%m-%d") if stats.created_at_min else "无"
    newest = stats.created_at_max.strftime("%Y-%m-%d") if stats.created_at_max else "无"
    table.add_row("最早 created_at", oldest)
    table.add_row("最晚 created_at", newest)

    console.print(table)


@cli.command(name="mcp-server")
@click.option(
    "--transport",
    "-t",
    type=click.Choice(["stdio", "http"]),
    default="stdio",
    help="传输协议 (stdio 用于本地客户端, http 为 Streamable HTTP)",
)
@click.option(
    "--port",
    "-p",
    default=DEFAULT_MCP_HTTP_PORT,
    show_default=True,
    help="HTTP 模式端口（避免与 Web 的 8000 冲突）",
)
def mcp_server(transport: str, port: int):
    """
    启动 MCP (Model Context Protocol) 服务器

    供 AI Agent (如 Claude Desktop、Cursor) 调用本工具能力。

    示例:
        wechat-article-reader mcp-server                   # stdio 模式
        wechat-article-reader mcp-server -t http -p 9000   # HTTP 模式
    """
    try:
        from ...mcp import run_mcp_server

        # stdio transport reserves stdout for MCP protocol messages. Keep
        # startup information on stderr for both transports.
        status_console = Console(stderr=True)
        status_console.print(f"[bold green]启动 MCP 服务器[/bold green] (transport={transport})")
        status_console.print(
            "[dim]提供工具: ingest_article, get_cached_article, read_article, summarize_article[/dim]"
        )

        if transport == "http":
            status_console.print(
                f"[cyan]MCP Streamable HTTP 端点: http://localhost:{port}/mcp[/cyan]"
            )

        run_mcp_server(transport=transport, port=port)
    except ImportError as e:
        console.print(f"[red]MCP 服务不可用: {e}[/red]")
        console.print("请安装 MCP 依赖: pip install 'wechat-article-reader[mcp]'")
        sys.exit(1)


@cli.command()
def check() -> None:
    """检查组件与本地运行环境；不会显示密钥。"""
    container = get_container()
    settings = get_settings()

    console.print("[bold]检查组件状态...[/bold]\n")

    # 检查抓取器
    console.print("[cyan]抓取器:[/cyan]")
    for scraper in container.scrapers:
        console.print(f"  - {scraper.name}: [green]可用[/green]")

    # 检查摘要器
    console.print("\n[cyan]摘要器:[/cyan]")
    for name, summarizer in container.summarizers.items():
        status = "[green]可用[/green]" if summarizer.is_available() else "[red]不可用[/red]"
        console.print(f"  - {name}: {status}")

    # 检查导出器
    console.print("\n[cyan]导出器:[/cyan]")
    for name, exporter in container.exporters.items():
        status = "[green]可用[/green]" if exporter.is_available() else "[red]不可用[/red]"
        console.print(f"  - {name}: {status}")

    # 检查缓存/存储
    console.print("\n[cyan]缓存存储:[/cyan]")
    storage_status = "[green]可用[/green]" if container.storage is not None else "[red]不可用[/red]"
    console.print(f"  - sqlite: {storage_status}")

    console.print("\n[cyan]运行环境:[/cyan]")
    output_dir = Path(settings.export.default_output_dir)
    output_parent = output_dir if output_dir.exists() else output_dir.parent
    output_status = (
        "[green]可写路径[/green]"
        if output_parent.exists()
        else "[yellow]将在首次导出时创建[/yellow]"
    )
    console.print(f"  - 导出目录: {output_dir} ({output_status})")
    console.print(
        "  - DeepSeek 配置: "
        + (
            "[green]已配置[/green]"
            if settings.deepseek.api_key.get_secret_value()
            else "[yellow]未配置[/yellow]"
        )
    )
    console.print(
        "  - Web 依赖: "
        + ("[green]已安装[/green]" if find_spec("fastapi") else "[yellow]未安装[/yellow]")
    )
    console.print(
        "  - MCP 依赖: "
        + ("[green]已安装[/green]" if find_spec("mcp") else "[yellow]未安装[/yellow]")
    )


def _display_article(article):
    """显示文章信息"""
    metadata = getattr(article, "article", article)
    content_text = getattr(article, "content", getattr(article, "content_text", ""))
    summary = getattr(article, "summary", None)
    title = _console_safe_text(str(metadata.title))
    account_name = _console_safe_text(str(metadata.account_name or "未知"))
    article_url = _console_safe_text(str(metadata.url))

    # 对非 UTF 控制台降级为纯文本输出，避免 Rich Panel/边框字符触发编码错误。
    if not _console_supports_unicode_progress(console):
        console.print(f"标题: {title}")
        console.print(f"公众号: {account_name}")
        console.print(f"字数: {metadata.word_count}")
        console.print(f"URL: {article_url}")

        if summary:
            summary_text = _console_safe_text(str(summary.overview))
            console.print(f"\n摘要:\n{summary_text}")

            if summary.key_points:
                key_points = "\n".join(
                    f"  - {_console_safe_text(str(getattr(point, 'text', point)))}"
                    for point in summary.key_points
                )
                console.print(f"\n关键要点:\n{key_points}")

            if summary.tags:
                tags = ", ".join(_console_safe_text(str(tag)) for tag in summary.tags)
                console.print(f"\n标签: {tags}")

        preview = content_text[:500] + "..." if len(content_text) > 500 else content_text
        console.print(f"\n内容预览:\n{_console_safe_text(preview)}")
        return

    # 文章信息面板
    info_text = f"""[bold]标题:[/bold] {title}
[bold]公众号:[/bold] {account_name}
[bold]字数:[/bold] {metadata.word_count}
[bold]URL:[/bold] {article_url}"""

    console.print(Panel(info_text, title="文章信息", border_style="blue"))

    # 摘要面板
    if summary:
        summary_text = _console_safe_text(str(summary.overview))

        if summary.key_points:
            summary_text += "\n\n[bold]关键要点:[/bold]\n"
            summary_text += "\n".join(
                f"  - {_console_safe_text(str(getattr(point, 'text', point)))}"
                for point in summary.key_points
            )

        if summary.tags:
            tags = ", ".join(_console_safe_text(str(tag)) for tag in summary.tags)
            summary_text += f"\n\n[bold]标签:[/bold] {tags}"

        console.print(Panel(summary_text, title="文章摘要", border_style="green"))

    # 内容预览
    preview = content_text[:500] + "..." if len(content_text) > 500 else content_text
    console.print(Panel(_console_safe_text(preview), title="内容预览", border_style="dim"))


def run_cli():
    """运行CLI"""
    _configure_utf8_standard_streams()
    cli()


if __name__ == "__main__":
    run_cli()
