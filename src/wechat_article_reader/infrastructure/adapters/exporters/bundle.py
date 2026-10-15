"""Portable web downloads containing only the current export's manifest assets."""

import json
import os
import re
from hashlib import sha256
from pathlib import Path
from tempfile import NamedTemporaryFile
from zipfile import ZIP_DEFLATED, ZipFile

from .path_lock import export_path_lock

_SNAPSHOT_MARKER = re.compile(r"\n\n<!-- wechat-article-reader-export: ([^\r\n]+) -->\n?\Z")


def bundle_markdown(path: str) -> str:
    markdown = Path(path).resolve(strict=True)
    with export_path_lock(markdown):
        return _bundle_markdown_locked(markdown)


def _bundle_markdown_locked(markdown: Path) -> str:
    manifest = markdown.with_suffix(".assets") / "manifest.json"
    if not manifest.resolve(strict=True).is_relative_to(markdown.parent):
        raise ValueError("图片清单路径越界")
    data = json.loads(manifest.read_text(encoding="utf-8"))
    snapshot = data.get("snapshot")
    if not isinstance(snapshot, dict):
        raise ValueError("图片清单缺少导出快照")
    markdown_text = markdown.read_text(encoding="utf-8")
    marker = _SNAPSHOT_MARKER.search(markdown_text)
    if marker is None:
        raise ValueError("Markdown 缺少导出快照标记")
    try:
        marker_snapshot = json.loads(marker.group(1))
    except json.JSONDecodeError as exc:
        raise ValueError("Markdown 导出快照标记无效") from exc
    markdown_body = markdown_text[: marker.start()]
    if marker_snapshot != snapshot:
        raise ValueError("Markdown 与图片清单不属于同一导出快照")
    expected_id = snapshot.get("snapshot_id")
    snapshot_without_id = {key: value for key, value in snapshot.items() if key != "snapshot_id"}
    actual_id = sha256(
        json.dumps(
            snapshot_without_id, ensure_ascii=False, sort_keys=True, separators=(",", ":")
        ).encode("utf-8")
    ).hexdigest()
    if (
        expected_id != actual_id
        or snapshot.get("markdown_sha256") != sha256(markdown_body.encode("utf-8")).hexdigest()
        or data.get("article_id") != snapshot.get("article_id")
        or data.get("revision") != snapshot.get("revision")
    ):
        raise ValueError("Markdown 与图片清单的导出快照校验失败")
    files = [markdown, manifest]
    for image in data["images"]:
        if image["status"] == "downloaded":
            asset = (markdown.parent / image["local_path"]).resolve(strict=True)
            if not asset.is_relative_to(manifest.parent.resolve()) or not asset.is_file():
                raise ValueError("图片资源路径越界")
            files.append(asset)
    with NamedTemporaryFile(dir=markdown.parent, suffix=".zip", delete=False) as stream:
        temporary = Path(stream.name)
    try:
        with ZipFile(temporary, "w", compression=ZIP_DEFLATED) as archive:
            for file in dict.fromkeys(files):
                archive.write(file, file.relative_to(markdown.parent).as_posix())
        target = markdown.with_suffix(".zip")
        os.replace(temporary, target)
        return str(target)
    finally:
        temporary.unlink(missing_ok=True)
