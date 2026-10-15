from __future__ import annotations

import base64
import io
import json
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from threading import Event
from types import SimpleNamespace
from urllib.parse import unquote
from zipfile import ZipFile

import httpx
import pytest
from bs4 import BeautifulSoup
from click.testing import CliRunner
from markdown_it import MarkdownIt
from PIL import Image

from wechat_article_reader.domain.entities import Article
from wechat_article_reader.domain.value_objects import ArticleContent, ArticleURL
from wechat_article_reader.features.article_reading import (
    ArticleReadingProjector,
    ArticleReadingService,
)
from wechat_article_reader.features.article_reading import projection as projection_module
from wechat_article_reader.features.article_reading.images import article_images
from wechat_article_reader.features.article_reading.service import ReadingRevisionChangedError
from wechat_article_reader.infrastructure.adapters.exporters import bundle as bundle_module
from wechat_article_reader.infrastructure.adapters.exporters import markdown as markdown_module
from wechat_article_reader.infrastructure.adapters.exporters.bundle import bundle_markdown
from wechat_article_reader.infrastructure.adapters.exporters.markdown import MarkdownExporter
from wechat_article_reader.infrastructure.adapters.image_assets import (
    CachedImageAssets,
    allowed_image_url,
    validate_image,
)
from wechat_article_reader.mcp.toolsets.reading_tools import register_reading_tools
from wechat_article_reader.presentation.cli.app import cli
from wechat_article_reader.shared.exceptions import (
    ExporterError,
    OperationCancelledError,
)

URL = "https://mmbiz.qpic.cn/mmbiz_png/test/0?wx_fmt=png"


def png() -> bytes:
    stream = io.BytesIO()
    Image.new("RGB", (8, 6), "red").save(stream, format="PNG")
    return stream.getvalue()


def article() -> Article:
    return Article(
        url=ArticleURL.from_string("https://mp.weixin.qq.com/s/images"),
        title="图文文章",
        content=ArticleContent.from_html(
            f'<p>之前<img src="{URL}" alt="图示">之后</p><blockquote><p>引用<img src="{URL}" alt="重复图"></p></blockquote>'
        ),
    )


class Assets:
    def __init__(self, fail=False):
        self.calls = []
        self.fail = fail

    def load(self, url, *, download=False, cancel_event=None):
        self.calls.append((url, download))
        if self.fail:
            raise ValueError("bad asset")
        return validate_image(png())


@pytest.mark.parametrize(
    "url",
    [
        "https://mmbiz.qpic.cn.evil.test/a",
        "http://mmbiz.qpic.cn/a",
        "https://127.0.0.1/a",
        "https://evil.test/a",
        "https://user@mmbiz.qpic.cn/a",
        "https://mmbiz.qpic.cn:8443/a",
        "https://[invalid",
        "file:///a",
    ],
)
def test_image_cdn_allowlist_rejects_untrusted_origins(url):
    assert not allowed_image_url(url)


def test_cached_image_download_is_explicit_and_validated(tmp_path, monkeypatch):
    calls = []

    def fetch(url, **kwargs):
        calls.append(kwargs)
        assert kwargs["allowed_redirect"](URL)
        assert not kwargs["allowed_redirect"]("https://evil.test/redirect")
        return httpx.Response(200, content=png(), request=httpx.Request("GET", url))

    monkeypatch.setattr(
        "wechat_article_reader.infrastructure.adapters.image_assets.safe_fetch_sync", fetch
    )
    cache = CachedImageAssets(tmp_path)
    with pytest.raises(ValueError, match="未缓存"):
        cache.load(URL)
    first = cache.load(URL, download=True)
    assert first.mime_type == "image/png" and first.data == png()
    assert cache.load(URL).data == first.data and len(calls) == 1
    assert calls[0]["max_response_bytes"] == 10 * 1024 * 1024


@pytest.mark.parametrize(
    "data",
    [b"", b"<svg><script>alert(1)</script></svg>", b"<html>error</html>", b"\x89PNG\r\n\x1a\n"],
)
def test_file_content_is_validated_instead_of_trusting_url_or_header(data):
    with pytest.raises(ValueError):
        validate_image(data)


def test_animation_is_downloadable_but_has_explicit_static_preview(monkeypatch):
    stream = io.BytesIO()
    first = Image.new("RGB", (4, 4), "red")
    first.save(
        stream, format="GIF", save_all=True, append_images=[Image.new("RGB", (4, 4), "blue")]
    )
    asset = validate_image(stream.getvalue())
    assert asset.frame_count == 2 and asset.mime_type == "image/gif"
    assert validate_image(asset.preview_data).mime_type == "image/png"
    monkeypatch.setattr(
        "wechat_article_reader.infrastructure.adapters.image_assets.MAX_IMAGE_FRAMES", 1
    )
    with pytest.raises(ValueError, match="帧数"):
        validate_image(stream.getvalue())
    monkeypatch.setattr(
        "wechat_article_reader.infrastructure.adapters.image_assets.MAX_IMAGE_PIXELS", 1
    )
    with pytest.raises(ValueError, match="像素"):
        validate_image(png())


def test_cancelled_download_never_starts_network(tmp_path, monkeypatch):
    signal = Event()
    signal.set()
    monkeypatch.setattr(
        "wechat_article_reader.infrastructure.adapters.image_assets.safe_fetch_sync",
        lambda *a, **k: pytest.fail("must not fetch"),
    )
    with pytest.raises(OperationCancelledError):
        CachedImageAssets(tmp_path).load(URL, download=True, cancel_event=signal)
    assert not list(tmp_path.iterdir())


@pytest.mark.parametrize(
    "display,download", [(False, False), (True, False), (False, True), (True, True)]
)
def test_display_and_download_are_independent(tmp_path, display, download):
    assets = Assets()
    target = tmp_path / "图文 文章.md"
    exporter = MarkdownExporter(image_assets=assets)
    item = article()
    exporter.export(item, str(target), include_images=display, download_images=download)
    dom = BeautifulSoup(
        MarkdownIt("commonmark", {"html": True}).render(target.read_text(encoding="utf-8")),
        "html.parser",
    )
    images = dom.find_all("img")
    assert len(images) == (2 if display else 0)
    assert len(assets.calls) == (1 if download else 0)
    manifest = target.with_suffix(".assets") / "manifest.json"
    assert manifest.exists() == download
    if download:
        data = json.loads(manifest.read_text(encoding="utf-8"))
        assert data["revision"] == item.revision and not data["image_content_read"]
        assert [entry["image_id"] for entry in data["images"]] == ["image-001", "image-002"]
        assert all(entry["status"] == "downloaded" for entry in data["images"])
        for entry in data["images"]:
            assert (tmp_path / entry["local_path"]).read_bytes() == png()
        for image in images:
            assert (tmp_path / unquote(image["src"])).is_file()
    elif display:
        assert all(image["src"] == URL for image in images)


def test_failed_download_is_visible_and_does_not_claim_offline_completeness(tmp_path):
    target = tmp_path / "article.md"
    assets = Assets(fail=True)
    MarkdownExporter(image_assets=assets).export(
        article(), str(target), include_images=True, download_images=True
    )
    content = target.read_text(encoding="utf-8")
    assert "离线包不完整" in content and "image-001" in content
    data = json.loads((target.with_suffix(".assets") / "manifest.json").read_text(encoding="utf-8"))
    assert all(entry["status"] == "failed" for entry in data["images"])
    assert URL in content
    assert len(assets.calls) == 1


def test_export_caps_distinct_download_attempts(tmp_path):
    item = article()
    item.update_content(
        ArticleContent.from_html(
            "".join(f'<img src="https://mmbiz.qpic.cn/{index}.png">' for index in range(101))
        )
    )
    assets = Assets()
    target = tmp_path / "budget.md"
    MarkdownExporter(image_assets=assets).export(item, str(target), download_images=True)
    assert len(assets.calls) == 100
    data = json.loads((target.with_suffix(".assets") / "manifest.json").read_text(encoding="utf-8"))
    assert data["images"][-1]["status"] == "failed"


def test_animation_vision_returns_first_frame_with_explicit_scope():
    stream = io.BytesIO()
    Image.new("RGB", (4, 4), "red").save(
        stream, format="GIF", save_all=True, append_images=[Image.new("RGB", (4, 4), "blue")]
    )
    asset = validate_image(stream.getvalue())
    item = article()
    service = ArticleReadingService(
        SimpleNamespace(),
        SimpleNamespace(get=lambda _: item),
        SimpleNamespace(load=lambda *a, **k: asset),
    )
    metadata, provided = service.read_image(
        item.id, "image-001", expected_reading_revision=f"rp1:{item.revision}", send_to_model=True
    )
    assert metadata["frame_count"] == 2 and metadata["visual_scope"] == "first_frame"
    assert provided.mime_type == "image/png" and provided.data == asset.preview_data


def test_bundle_contains_only_manifest_assets_and_rejects_traversal(tmp_path):
    target = tmp_path / "article.md"
    MarkdownExporter(image_assets=Assets()).export(
        article(), str(target), include_images=True, download_images=True
    )
    (target.with_suffix(".assets") / "unrelated.txt").write_text(
        "not part of export", encoding="utf-8"
    )
    zip_path = bundle_markdown(str(target))
    with ZipFile(zip_path) as archive:
        assert (
            "article.md" in archive.namelist()
            and "article.assets/manifest.json" in archive.namelist()
        )
        assert len(archive.namelist()) == 3
        assert not any("unrelated" in name for name in archive.namelist())
    manifest = target.with_suffix(".assets") / "manifest.json"
    data = json.loads(manifest.read_text(encoding="utf-8"))
    data["images"][0]["local_path"] = "article.md"
    manifest.write_text(json.dumps(data), encoding="utf-8")
    with pytest.raises(ValueError, match="越界"):
        bundle_markdown(str(target))


def test_bundle_rejects_markdown_and_manifest_from_different_snapshots(tmp_path):
    target = tmp_path / "article.md"
    MarkdownExporter(image_assets=Assets()).export(article(), str(target), download_images=True)
    manifest_path = target.with_suffix(".assets") / "manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["snapshot"]["revision"] = "stale-revision"
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")

    with pytest.raises(ValueError, match="同一导出快照"):
        bundle_markdown(str(target))


def test_export_cancelled_after_manifest_preparation_keeps_previous_pair(tmp_path, monkeypatch):
    target = tmp_path / "article.md"
    exporter = MarkdownExporter(image_assets=Assets())
    original_article = article()
    exporter.export(original_article, str(target), download_images=True)
    previous_markdown = target.read_bytes()
    manifest = target.with_suffix(".assets") / "manifest.json"
    previous_manifest = manifest.read_bytes()

    signal = Event()
    original_generate = exporter._generate_markdown

    def cancel_after_manifest_preparation(item, **options):
        result = original_generate(item, **options)
        signal.set()
        return result

    monkeypatch.setattr(exporter, "_generate_markdown", cancel_after_manifest_preparation)
    with pytest.raises(OperationCancelledError):
        exporter.export(article(), str(target), download_images=True, cancel_event=signal)

    assert target.read_bytes() == previous_markdown
    assert manifest.read_bytes() == previous_manifest


def test_markdown_publish_failure_rolls_back_manifest(tmp_path, monkeypatch):
    target = tmp_path / "article.md"
    exporter = MarkdownExporter(image_assets=Assets())
    exporter.export(article(), str(target), download_images=True)
    previous_markdown = target.read_bytes()
    manifest = target.with_suffix(".assets") / "manifest.json"
    previous_manifest = manifest.read_bytes()
    real_replace = markdown_module.os.replace

    def fail_markdown_replace(source, destination):
        if Path(destination) == target:
            raise PermissionError("injected Windows destination replacement failure")
        return real_replace(source, destination)

    monkeypatch.setattr(markdown_module.os, "replace", fail_markdown_replace)
    with pytest.raises(ExporterError, match="Markdown导出失败"):
        exporter.export(article(), str(target), download_images=True)

    assert target.read_bytes() == previous_markdown
    assert manifest.read_bytes() == previous_manifest


def test_same_target_concurrent_exports_publish_one_consistent_snapshot(tmp_path):
    target = tmp_path / "article.md"
    exporter = MarkdownExporter(image_assets=Assets())
    items = [article(), article()]
    items[0].update_content(ArticleContent.from_html(f'<p>甲</p><img src="{URL}">'))
    items[1].update_content(ArticleContent.from_html(f'<p>乙</p><img src="{URL}">'))
    exporter.export(items[0], str(target), download_images=True)
    from threading import Barrier

    barrier = Barrier(3)

    def publish(item):
        barrier.wait(timeout=5)
        return exporter.export(item, str(target), download_images=True)

    def bundle():
        barrier.wait(timeout=5)
        return bundle_markdown(str(target))

    with ThreadPoolExecutor(max_workers=3) as executor:
        first = executor.submit(publish, items[0])
        second = executor.submit(publish, items[1])
        bundled = executor.submit(bundle)
        results = [first.result(), second.result()]
        zip_path = Path(bundled.result())

    assert results == [str(target), str(target)]
    manifest = json.loads(
        (target.with_suffix(".assets") / "manifest.json").read_text(encoding="utf-8")
    )
    text = target.read_text(encoding="utf-8")
    assert manifest["snapshot"]["revision"] == manifest["revision"]
    assert manifest["snapshot"]["article_id"] == manifest["article_id"]
    assert ("甲" in text) != ("乙" in text)
    assert (tmp_path / manifest["images"][0]["local_path"]).is_file()
    with ZipFile(zip_path) as archive:
        assert "article.md" in archive.namelist()
        assert "article.assets/manifest.json" in archive.namelist()
        archived_manifest = json.loads(archive.read("article.assets/manifest.json").decode("utf-8"))
        archived_markdown = archive.read("article.md").decode("utf-8")
        marker = next(
            line
            for line in archived_markdown.splitlines()
            if line.startswith("<!-- wechat-article-reader-export: ")
        )
        marker_snapshot = json.loads(
            marker.removeprefix("<!-- wechat-article-reader-export: ")[:-4]
        )
        assert marker_snapshot == archived_manifest["snapshot"]


def test_zip_failure_preserves_previous_bundle(tmp_path, monkeypatch):
    target = tmp_path / "article.md"
    MarkdownExporter(image_assets=Assets()).export(article(), str(target), download_images=True)
    zip_path = Path(bundle_markdown(str(target)))
    previous = zip_path.read_bytes()
    real_write = ZipFile.write

    def fail_write(self, *args, **kwargs):
        raise OSError("injected ZIP write failure")

    monkeypatch.setattr(ZipFile, "write", fail_write)
    with pytest.raises(OSError, match="ZIP write failure"):
        bundle_module.bundle_markdown(str(target))

    assert zip_path.read_bytes() == previous
    monkeypatch.setattr(ZipFile, "write", real_write)


def test_image_inventory_includes_complex_tables_and_nested_containers():
    item = article()
    item.update_content(
        ArticleContent.from_html(
            f'<table><tr><td colspan="2"><img src="{URL}" alt="表内图"></td></tr></table>'
        )
    )
    images = article_images(ArticleReadingProjector().project(item))
    assert len(images) == 1 and images[0].alt == "表内图"


def test_simple_table_images_survive_download_and_display(tmp_path):
    item = article()
    item.update_content(
        ArticleContent.from_html(
            f'<table><tr><td>图</td><td><img src="{URL}" alt="表内图"></td></tr></table>'
        )
    )
    service = ArticleReadingService(SimpleNamespace(), SimpleNamespace(get=lambda _: item))
    assert len(service.read(item.id).images) == 1
    assert "<img" not in service.read(item.id).content_markdown
    target = tmp_path / "table.md"
    MarkdownExporter(image_assets=Assets()).export(
        item, str(target), include_images=True, download_images=True
    )
    dom = BeautifulSoup(target.read_text(encoding="utf-8"), "html.parser")
    assert (tmp_path / unquote(dom.img["src"])).is_file()


def test_response_cancelled_before_cache_commit_leaves_no_image(tmp_path, monkeypatch):
    signal = Event()

    def fetch(url, **kwargs):
        signal.set()
        return httpx.Response(200, content=png(), request=httpx.Request("GET", url))

    monkeypatch.setattr(
        "wechat_article_reader.infrastructure.adapters.image_assets.safe_fetch_sync", fetch
    )
    with pytest.raises(OperationCancelledError):
        CachedImageAssets(tmp_path).load(URL, download=True, cancel_event=signal)
    assert not list(tmp_path.iterdir())


def test_export_cancellation_preserves_existing_markdown(tmp_path):
    signal = Event()
    assets = Assets()
    original_load = assets.load

    def load(*args, **kwargs):
        result = original_load(*args, **kwargs)
        signal.set()
        return result

    assets.load = load
    target = tmp_path / "article.md"
    target.write_text("previous export", encoding="utf-8")
    with pytest.raises(OperationCancelledError):
        MarkdownExporter(image_assets=assets).export(
            article(), str(target), download_images=True, cancel_event=signal
        )
    assert target.read_text(encoding="utf-8") == "previous export"
    assert not list(target.with_suffix(".assets").rglob("*.png"))


def test_image_byte_limit_is_enforced_before_decode(monkeypatch):
    monkeypatch.setattr(
        "wechat_article_reader.infrastructure.adapters.image_assets.MAX_IMAGE_BYTES", 1
    )
    with pytest.raises(ValueError, match="10 MiB"):
        validate_image(png())


def test_visual_provision_is_explicit_and_revision_bound():
    item, assets = article(), Assets()
    service = ArticleReadingService(SimpleNamespace(), SimpleNamespace(get=lambda _: item), assets)
    page = service.read(item.id)
    assert len(page.images) == 2 and not page.image_content_read
    reading_revision = f"rp1:{item.revision}"
    metadata, content = service.read_image(
        item.id, "image-001", expected_reading_revision=reading_revision
    )
    assert content is None and not assets.calls and not metadata["image_content_provided"]
    metadata, content = service.read_image(
        item.id, "image-001", expected_reading_revision=reading_revision, download_image=True
    )
    assert content is None and not metadata["image_content_provided"]
    metadata, content = service.read_image(
        item.id, "image-001", expected_reading_revision=reading_revision, send_to_model=True
    )
    assert content.data == png() and metadata["image_content_provided"]
    assert assets.calls[-1][1] is False
    with pytest.raises(ReadingRevisionChangedError):
        service.read_image(
            item.id, "image-001", expected_reading_revision="old", send_to_model=True
        )
    with pytest.raises(ValueError, match="image_id"):
        service.read_image(
            item.id, "../image", expected_reading_revision=reading_revision, download_image=True
        )


@pytest.mark.asyncio
async def test_real_mcp_result_contains_image_content_not_json_encoded_bytes(monkeypatch):
    from mcp.server.fastmcp import FastMCP

    item, assets = article(), Assets()
    service = ArticleReadingService(SimpleNamespace(), SimpleNamespace(get=lambda _: item), assets)
    monkeypatch.setattr(
        "wechat_article_reader.infrastructure.config.get_container",
        lambda: SimpleNamespace(article_reading_service=service),
    )
    mcp = FastMCP("image test")
    register_reading_tools(mcp)
    result = await mcp.call_tool(
        "read_article_image",
        {
            "article_id": str(item.id),
            "image_id": "image-001",
            "expected_reading_revision": f"rp1:{item.revision}",
            "send_to_model": True,
        },
    )
    blocks = result[0] if isinstance(result, tuple) else result
    image = next(block for block in blocks if block.type == "image")
    assert base64.b64decode(image.data) == png() and image.mimeType == "image/png"
    result = await mcp.call_tool(
        "read_article_image",
        {
            "article_id": str(item.id),
            "image_id": "image-001",
            "expected_reading_revision": f"rp1:{item.revision}",
        },
    )
    blocks = result[0] if isinstance(result, tuple) else result
    assert all(block.type == "text" for block in blocks)


def test_old_image_reference_is_rejected_after_projection_change(monkeypatch) -> None:
    item = article()
    service = ArticleReadingService(SimpleNamespace(), SimpleNamespace(get=lambda _: item))
    old_revision = f"rp1:{item.revision}"
    monkeypatch.setattr(projection_module, "PROJECTION_FORMAT_VERSION", 2)

    with pytest.raises(ReadingRevisionChangedError):
        service.read_image(item.id, "image-001", expected_reading_revision=old_revision)


def test_cli_download_requires_markdown_export():
    result = CliRunner().invoke(
        cli, ["fetch", "https://mp.weixin.qq.com/s/images", "--download-images"]
    )
    assert result.exit_code == 2 and "--export markdown" in result.output


def test_cli_passes_display_and_download_options_independently(monkeypatch):
    calls = []
    monkeypatch.setattr(
        "wechat_article_reader.presentation.cli.app._process_single",
        lambda *a, **k: calls.append(k),
    )
    result = CliRunner().invoke(
        cli,
        ["fetch", "https://mp.weixin.qq.com/s/images", "--export", "markdown", "--download-images"],
    )
    assert result.exit_code == 0
    assert calls[0]["download_images"] is True and calls[0]["include_images"] is None


def test_web_offline_export_downloads_complete_zip(tmp_path, monkeypatch):
    from fastapi.testclient import TestClient

    from wechat_article_reader.presentation.web.app import create_app

    item, seen = article(), []
    exporter = MarkdownExporter(image_assets=Assets())
    target = tmp_path / "article.md"

    def export(url, **kwargs):
        seen.append(kwargs)
        exporter.export(
            item,
            str(target),
            include_images=kwargs["include_images"],
            download_images=kwargs["download_images"],
        )
        return SimpleNamespace(
            export_path=str(target), article=SimpleNamespace(word_count=item.word_count)
        )

    monkeypatch.setattr(
        "wechat_article_reader.presentation.web.api.endpoints._output_root", lambda: tmp_path
    )
    app = create_app(
        container=SimpleNamespace(article_workflow_service=SimpleNamespace(export=export)),
        prewarm=False,
    )
    client = TestClient(app)
    response = client.post(
        "/api/article/export",
        json={
            "url": str(item.url),
            "skip_summary": True,
            "download_images": True,
            "include_images": False,
        },
    )
    assert response.status_code == 200 and seen[0]["include_images"] is False
    assert response.json()["export_path"] == "article.zip"
    downloaded = client.get(response.json()["download_url"])
    assert downloaded.status_code == 200
    with ZipFile(io.BytesIO(downloaded.content)) as archive:
        assert (
            "article.md" in archive.namelist()
            and "article.assets/manifest.json" in archive.namelist()
        )
        assert len(archive.namelist()) == 3
