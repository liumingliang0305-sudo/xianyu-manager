"""第三方货源：粘贴公开网页链接，认出是哪个网站、用哪个解析方案，提取正文和内容图片，按后台用户保存。

识别与各网站的解析方案在 app/source_parsers.py，请求网页的安全边界在 app/webpage.py。
图片复用草稿的 ImageStore，查看时用本地副本，不依赖原站。
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import time
from collections import defaultdict
from collections.abc import Sequence
from dataclasses import asdict, dataclass, field

import httpx

from app.db import Database, Row
from app.drafts import pick_images
from app.images import MAX_BYTES, ImageStore, drop_unused
from app.source_parsers import Detection, SourceImage, detect, parse
from app.webpage import fetch, public_client
from xianyu_core.publish import MAX_IMAGES

log = logging.getLogger(__name__)

DOWNLOAD_CONCURRENCY = 6
COLUMNS = (
    "source_id, url, title, content, platform, author, images, parser, imported_at, updated_at"
)

__all__ = ["Source", "SourceImage", "SourceNotFoundError", "SourceService"]


class SourceNotFoundError(LookupError):
    pass


@dataclass
class Source:
    source_id: str
    url: str
    title: str
    content: str
    platform: str = "网页"  # 哪个网站
    author: str = ""
    images: list[SourceImage] = field(default_factory=list)
    parser: str = "generic"  # 用哪个解析方案取的
    imported_at: float = 0.0
    updated_at: float = 0.0

    def shas(self) -> set[str]:
        return {img.sha for img in self.images if img.sha}

    def missing(self) -> int:
        return sum(not img.sha for img in self.images)

    def draft_images(self, shas: Sequence[str] | None = None) -> list[SourceImage]:
        """建草稿用的图。`shas` 是用户挑的，按挑的顺序；不传是下载下来的前 9 张。"""
        if shas is not None:
            return pick_images(self.images, shas)
        return [img for img in self.images if img.sha][:MAX_IMAGES]

    def images_json(self) -> str:
        return json.dumps([asdict(img) for img in self.images], ensure_ascii=False)

    @classmethod
    def from_row(cls, row: Row) -> Source:
        return cls(**{**row, "images": [SourceImage(**img) for img in json.loads(row["images"])]})


class SourceService:
    def __init__(self, db: Database, images: ImageStore, *, transport=None):
        self._db = db
        self._images = images
        self._transport = transport
        self._locks: defaultdict[tuple[int, str], asyncio.Lock] = defaultdict(asyncio.Lock)

    def list(self, user_id: int) -> list[Source]:
        rows = self._db.execute(
            f"SELECT {COLUMNS} FROM third_party_sources WHERE user_id = %s"
            " ORDER BY updated_at DESC, source_id",
            (user_id,),
        )
        return [Source.from_row(row) for row in rows]

    def get(self, user_id: int, source_id: str) -> Source:
        source = self.find(user_id, source_id)
        if source is None:
            raise SourceNotFoundError("找不到这个第三方货源")
        return source

    def find(self, user_id: int, source_id: str) -> Source | None:
        row = self._db.execute(
            f"SELECT {COLUMNS} FROM third_party_sources WHERE user_id = %s AND source_id = %s",
            (user_id, source_id),
        ).fetchone()
        return Source.from_row(row) if row else None

    def client(self) -> httpx.AsyncClient:
        return public_client(self._transport)

    async def detect(self, url: str) -> Detection:
        """只识别不保存：哪个网站、什么链接、该用哪个解析方案。"""
        try:
            async with self.client() as client:
                return await detect(client, url)
        except httpx.HTTPError as exc:
            raise ValueError("打不开这个链接，请检查链接或稍后重试") from exc

    async def ingest(
        self, user_id: int, url: str, parser: str | None = None
    ) -> tuple[Source, bool]:
        """parser 为空或 auto 时按识别结果选方案，否则用手动选的方案。"""
        try:
            async with self.client() as client:
                detection = await detect(client, url)
                parsed = await parse(
                    client, detection, None if parser in (None, "", "auto") else parser
                )
        except httpx.HTTPError as exc:
            raise ValueError("读取货源网页失败，请检查链接或稍后重试") from exc
        source = Source(
            hashlib.sha256(parsed.url.encode()).hexdigest()[:32],
            parsed.url,
            parsed.title,
            parsed.content,
            parsed.site,
            parsed.author,
            parsed.images,
            parsed.parser,
        )
        async with self._locks[(user_id, source.source_id)]:
            old = self.find(user_id, source.source_id)
            if old:
                saved = {img.url: img for img in old.images if img.sha}
                source.images = [saved.get(img.url, img) for img in source.images]
            now = time.time()
            source.imported_at = old.imported_at if old else now
            source.updated_at = now
            self._db.execute(
                "INSERT INTO third_party_sources (user_id, source_id, url, title, content,"
                " platform, author, images, parser, imported_at, updated_at)"
                " VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)"
                " ON CONFLICT (user_id, source_id) DO UPDATE SET title = excluded.title,"
                " content = excluded.content, platform = excluded.platform,"
                " author = excluded.author, images = excluded.images,"
                " parser = excluded.parser, updated_at = excluded.updated_at",
                (
                    user_id,
                    source.source_id,
                    source.url,
                    source.title,
                    source.content,
                    source.platform,
                    source.author,
                    source.images_json(),
                    source.parser,
                    source.imported_at,
                    now,
                ),
            )
            if old:
                drop_unused(self._db, self._images, old.shas() - source.shas())
            source = await self._download(user_id, source)
        return source, old is None

    async def fetch_images(self, user_id: int, source_id: str) -> Source:
        async with self._locks[(user_id, source_id)]:
            return await self._download(user_id, self.get(user_id, source_id))

    async def delete(self, user_id: int, source_id: str) -> None:
        async with self._locks[(user_id, source_id)]:
            source = self.get(user_id, source_id)
            self._db.execute(
                "DELETE FROM third_party_sources WHERE user_id = %s AND source_id = %s",
                (user_id, source_id),
            )
            drop_unused(self._db, self._images, source.shas())

    def image_path(self, user_id: int, source_id: str, sha: str):
        source = self.get(user_id, source_id)
        if sha not in source.shas():
            raise SourceNotFoundError("这个货源里没有这张图片")
        path = self._images.path(sha)
        if not path.exists():
            raise SourceNotFoundError("图片文件不存在，请重新导入货源")
        return path

    async def _download(self, user_id: int, source: Source) -> Source:
        gate = asyncio.Semaphore(DOWNLOAD_CONCURRENCY)
        async with self.client() as client:

            async def download(img: SourceImage):
                if img.sha:
                    return
                async with gate:
                    try:
                        image = await fetch(
                            client, img.url, kind="image", max_bytes=MAX_BYTES, referer=source.url
                        )
                        stored = await asyncio.to_thread(self._images.save, image.data or b"")
                    except (httpx.HTTPError, ValueError, OSError) as exc:
                        # 超时类异常没有文字，记下类型才看得出是慢还是被拒
                        log.warning("下载第三方货源图片失败 %s：%r", img.url, exc)
                        return
                    img.sha, img.width, img.height = stored.sha, stored.width, stored.height

            await asyncio.gather(*(download(img) for img in source.images))
        self._db.execute(
            "UPDATE third_party_sources SET images = %s WHERE user_id = %s AND source_id = %s",
            (source.images_json(), user_id, source.source_id),
        )
        return source
