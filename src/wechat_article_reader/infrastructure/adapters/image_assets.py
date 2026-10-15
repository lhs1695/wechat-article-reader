"""Validated Tencent article images, cached by URL with no arbitrary URL API."""

import hashlib
import io
import os
import warnings
from pathlib import Path
from tempfile import NamedTemporaryFile
from threading import Event
from urllib.parse import urlsplit

from PIL import Image, UnidentifiedImageError

from ...application.ports.outbound.image_asset_port import ImageAsset, ImageCropRegion
from ...shared.exceptions import OperationCancelledError
from ...shared.operation import check_operation_deadline
from ...shared.utils.ssrf_protection import safe_fetch_sync

MAX_IMAGE_BYTES = 10 * 1024 * 1024
MAX_IMAGE_PIXELS = 25_000_000
MAX_IMAGE_FRAMES = 200
MAX_TOTAL_FRAME_PIXELS = 100_000_000
_FORMATS = {
    "PNG": ("image/png", ".png"),
    "JPEG": ("image/jpeg", ".jpg"),
    "GIF": ("image/gif", ".gif"),
    "WEBP": ("image/webp", ".webp"),
}
_HOSTS = frozenset({"mmbiz.qpic.cn", "mmbiz.qlogo.cn", "mmsns.qpic.cn"})


def allowed_image_url(url: str) -> bool:
    try:
        parts = urlsplit(url)
        return (
            len(url) <= 4096
            and parts.scheme == "https"
            and parts.hostname in _HOSTS
            and not parts.username
            and not parts.password
            and parts.port in (None, 443)
        )
    except ValueError:
        return False


def validate_image(data: bytes) -> ImageAsset:
    if not data or len(data) > MAX_IMAGE_BYTES:
        raise ValueError("图片为空或超过 10 MiB")
    try:
        with warnings.catch_warnings():
            warnings.simplefilter("error", Image.DecompressionBombWarning)
            with Image.open(io.BytesIO(data)) as image:
                if image.format not in _FORMATS or image.width * image.height > MAX_IMAGE_PIXELS:
                    raise ValueError("不支持的图片格式或图片像素过大")
                frame_count = getattr(image, "n_frames", 1)
                if (
                    frame_count > MAX_IMAGE_FRAMES
                    or image.width * image.height * frame_count > MAX_TOTAL_FRAME_PIXELS
                ):
                    raise ValueError("动画图片超过帧数或总像素限制")
                mime, extension = _FORMATS[image.format]
                image.verify()
            # Decode once to reject truncated files that only have a valid header.
            with Image.open(io.BytesIO(data)) as decoded:
                decoded.load()
                preview_data = None
                if frame_count > 1:
                    preview = decoded.convert("RGBA")
                    preview.thumbnail((2048, 2048))
                    buffer = io.BytesIO()
                    preview.save(buffer, format="PNG")
                    preview_data = buffer.getvalue()
                    if len(preview_data) > MAX_IMAGE_BYTES:
                        raise ValueError("动画首帧预览超过大小限制")
    except (
        UnidentifiedImageError,
        OSError,
        Image.DecompressionBombError,
        Image.DecompressionBombWarning,
    ) as exc:
        raise ValueError("图片校验失败") from exc
    return ImageAsset(data, mime, extension, frame_count, preview_data)


def atomic_write_bytes(path: Path, data: bytes, cancel_event: Event | None = None) -> None:
    temporary: Path | None = None
    try:
        if cancel_event is not None and cancel_event.is_set():
            raise OperationCancelledError()
        check_operation_deadline(cancel_event)
        with NamedTemporaryFile(dir=path.parent, prefix=".image-", delete=False) as stream:
            temporary = Path(stream.name)
            stream.write(data)
        if cancel_event is not None and cancel_event.is_set():
            raise OperationCancelledError()
        check_operation_deadline(cancel_event)
        os.replace(temporary, path)
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)


class CachedImageAssets:
    def __init__(self, cache_dir: Path):
        self.cache_dir = cache_dir

    def load(
        self,
        url: str,
        *,
        download: bool = False,
        cancel_event: Event | None = None,
        crop_region: ImageCropRegion | None = None,
    ) -> ImageAsset:
        if not allowed_image_url(url):
            raise ValueError("图片 URL 不在支持的微信图片 CDN 范围")
        if cancel_event is not None and cancel_event.is_set():
            raise OperationCancelledError()
        check_operation_deadline(cancel_event)
        target = self.cache_dir / (hashlib.sha256(url.encode()).hexdigest() + ".image")
        if target.is_file():
            if target.stat().st_size > MAX_IMAGE_BYTES:
                raise ValueError("缓存图片超过大小限制")
            asset = validate_image(target.read_bytes())
            return _crop_static_asset(asset, crop_region) if crop_region is not None else asset
        if not download:
            raise ValueError(
                "图片未缓存；请显式设置 download_image=true，或先用 download_images=true 导出"
            )
        response = safe_fetch_sync(
            url,
            timeout=20,
            max_redirects=3,
            allowed_redirect=allowed_image_url,
            max_response_bytes=MAX_IMAGE_BYTES,
            allowed_content_types={"image/png", "image/jpeg", "image/gif", "image/webp"},
            cancel_event=cancel_event,
        )
        response.raise_for_status()
        check_operation_deadline(cancel_event)
        asset = validate_image(response.content)
        check_operation_deadline(cancel_event)
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        atomic_write_bytes(target, asset.data, cancel_event)
        return _crop_static_asset(asset, crop_region) if crop_region is not None else asset

    def close(self) -> None:
        pass


def _crop_static_asset(asset: ImageAsset, region: ImageCropRegion) -> ImageAsset:
    if (
        not isinstance(region, tuple)
        or len(region) != 4
        or any(type(value) is not int for value in region)
    ):
        raise ValueError("crop_region 必须是四个整数百分比")
    if asset.frame_count != 1:
        raise ValueError("动画暂不支持局部裁剪；请按整张图片读取首帧预览")
    x, y, width, height = region
    if not (0 <= x < 100 and 0 <= y < 100 and 1 <= width <= 100 and 1 <= height <= 100):
        raise ValueError("crop_region 百分比范围无效")
    if x + width > 100 or y + height > 100:
        raise ValueError("crop_region 超出图片边界")

    try:
        with Image.open(io.BytesIO(asset.data)) as image:
            left = image.width * x // 100
            top = image.height * y // 100
            right = image.width * (x + width) // 100
            bottom = image.height * (y + height) // 100
            if right <= left or bottom <= top:
                raise ValueError("crop_region 在该图片尺寸上小于一个像素")
            cropped = image.crop((left, top, right, bottom)).convert("RGBA")
            buffer = io.BytesIO()
            cropped.save(buffer, format="PNG", optimize=True)
    except (UnidentifiedImageError, OSError) as exc:
        raise ValueError("图片局部裁剪失败") from exc
    data = buffer.getvalue()
    if len(data) > MAX_IMAGE_BYTES:
        raise ValueError("局部图片超过 10 MiB")
    return ImageAsset(data, "image/png", ".png")
