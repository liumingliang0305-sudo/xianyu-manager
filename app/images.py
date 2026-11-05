"""草稿和拼多多货源的图片存在本机：data/images/<sha256>.jpg。

传进来的图片统一转成 JPEG（按 EXIF 摆正、透明的地方铺白、长边超过 2048 缩小），文件名是转好以后的
SHA-256，同一张图只存一份。发布时再传到闲鱼，传过的地址记在草稿里，下次发布不用再传。
草稿（app/drafts.py）和拼多多货源（app/pdd.py）共用这些文件，删的时候两边都没在用才删（drop_unused）。
"""

from __future__ import annotations

import hashlib
import io
import os
import uuid
from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING

from PIL import Image, ImageOps, UnidentifiedImageError

if TYPE_CHECKING:
    from app.db import Database

DEFAULT_DIR = Path("data/images")
MAX_BYTES = 20 * 1024 * 1024  # 传进来的原图最大 20 MB
MAX_SIDE = 2048
QUALITY = 90
FORMATS = {"JPEG", "PNG", "WEBP", "GIF", "BMP"}

# 超过这么多像素的图不解，防止有人传一张解压后几十 GB 的图
Image.MAX_IMAGE_PIXELS = 60_000_000


@dataclass(frozen=True)
class StoredImage:
    sha: str
    width: int
    height: int


class ImageStore:
    def __init__(self, root: str | Path = DEFAULT_DIR):
        self.root = Path(root)

    def save(self, data: bytes) -> StoredImage:
        """转成 JPEG 存下来。不是图片、格式不支持、太大时抛 ValueError。"""
        content, width, height = normalize(data)
        sha = hashlib.sha256(content).hexdigest()
        path = self.path(sha)
        if not path.exists():
            self.root.mkdir(parents=True, exist_ok=True)
            # 临时文件名每次不一样：两张内容一样的图同时存（下载拼多多图片时在线程里存）不会互相踩
            tmp = path.with_name(f"{sha}.{uuid.uuid4().hex}.tmp")
            tmp.write_bytes(content)
            os.replace(tmp, path)
        return StoredImage(sha=sha, width=width, height=height)

    def read(self, sha: str) -> bytes:
        return self.path(sha).read_bytes()

    def path(self, sha: str) -> Path:
        if len(sha) != 64 or not all(c in "0123456789abcdef" for c in sha):
            raise ValueError("图片编号不对")
        return self.root / f"{sha}.jpg"

    def delete(self, sha: str) -> None:
        self.path(sha).unlink(missing_ok=True)


def drop_unused(db: Database, store: ImageStore, shas: Iterable[str]) -> None:
    """草稿和拼多多货源都没用到的图片文件删掉。两边都把 sha 存在 JSON 文本里，按 "sha" 找。"""
    for sha in set(shas):
        pattern = f'%"{sha}"%'
        used = db.execute(
            "SELECT 1 FROM drafts WHERE images LIKE %s"
            " UNION ALL SELECT 1 FROM pdd_goods WHERE data LIKE %s LIMIT 1",
            (pattern, pattern),
        ).fetchone()
        if not used:
            store.delete(sha)


def normalize(data: bytes) -> tuple[bytes, int, int]:
    """任意支持的图片 → (JPEG 内容, 宽, 高)。"""
    if len(data) > MAX_BYTES:
        raise ValueError(f"图片太大，最大 {MAX_BYTES // 1024 // 1024} MB")
    try:
        with Image.open(io.BytesIO(data)) as img:
            if img.format not in FORMATS:
                raise ValueError("只支持 JPG、PNG、WEBP、GIF、BMP 图片")
            img = ImageOps.exif_transpose(img)
            img.thumbnail((MAX_SIDE, MAX_SIDE), Image.Resampling.LANCZOS)
            img = _flatten(img)
            out = io.BytesIO()
            img.save(out, "JPEG", quality=QUALITY, optimize=True)
            return out.getvalue(), img.width, img.height
    except (UnidentifiedImageError, Image.DecompressionBombError, OSError) as exc:
        raise ValueError("不是能识别的图片") from exc


def _flatten(img: Image.Image) -> Image.Image:
    """透明的部分铺白，转成 RGB。"""
    if img.mode in ("RGBA", "LA") or (img.mode == "P" and "transparency" in img.info):
        rgba = img.convert("RGBA")
        background = Image.new("RGB", rgba.size, (255, 255, 255))
        background.paste(rgba, mask=rgba.getchannel("A"))
        return background
    return img.convert("RGB")
