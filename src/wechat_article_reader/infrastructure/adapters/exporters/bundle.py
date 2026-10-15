"""Portable web downloads containing only the current export's manifest assets."""

import json
import os
from pathlib import Path
from tempfile import NamedTemporaryFile
from zipfile import ZIP_DEFLATED, ZipFile


def bundle_markdown(path: str) -> str:
    markdown = Path(path).resolve(strict=True)
    manifest = markdown.with_suffix(".assets") / "manifest.json"
    if not manifest.resolve(strict=True).is_relative_to(markdown.parent):
        raise ValueError("图片清单路径越界")
    data = json.loads(manifest.read_text(encoding="utf-8"))
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
