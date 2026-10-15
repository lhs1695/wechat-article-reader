"""Bounded image acquisition, independent of presentation and article storage."""

from dataclasses import dataclass
from threading import Event
from typing import Protocol

ImageCropRegion = tuple[int, int, int, int]


@dataclass(frozen=True)
class ImageAsset:
    data: bytes
    mime_type: str
    extension: str
    frame_count: int = 1
    preview_data: bytes | None = None


class ImageAssetPort(Protocol):
    def load(
        self,
        url: str,
        *,
        download: bool = False,
        cancel_event: Event | None = None,
        crop_region: ImageCropRegion | None = None,
    ) -> ImageAsset: ...
