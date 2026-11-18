"""问题反馈：后台用户提交问题和建议，管理员查看、标记处理、推送成 GitHub issue。

- 每个用户都能提交，看得到、删得掉自己提交的；别人的和不存在的一样（404）；
- 管理员（users.is_admin）看得到所有人的，能标记处理完、重新打开，能推到 GitHub；
- 推送用 GitHub REST 接口建 issue。token 和仓库取环境变量 XYM_GITHUB_TOKEN、XYM_GITHUB_REPO
  （服务器上写在 /etc/xym.env），token 只在后台用，不发给浏览器；
- 仓库是公开的时候 issue 谁都看得到：推送前管理员能改标题和正文，默认的正文不带提交人的用户名；
- 一条反馈只推一次，推过的记下 issue 编号和链接。删掉反馈不动 GitHub 上的 issue。

截图：提交前先一张张传上来（转成 JPEG 存在 data/images，见 app/images.py），提交时带上 sha。
推送时管理员挑哪几张一起推，先传到 GitHub（uploads.github.com 的 user-attachments，和网页上
拖图片、gh 的 --attach 是同一个接口，要仓库的写权限），再把图片地址接在正文最后，最后才建 issue；
哪张传不上就整个不建。传上去但没建成 issue 的图片不挂在任何地方，匿名打不开。
"""

from __future__ import annotations

import asyncio
import json
import os
import re
import time
from collections import defaultdict
from collections.abc import Awaitable, Sequence
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

import httpx

from app.db import Database, Row
from app.images import ImageStore, StoredImage, drop_unused

# 种类 → 页面上叫什么、推到 GitHub 时打什么标签（仓库自带的默认标签，不用另建）
KINDS = {"bug": "问题", "idea": "建议", "other": "其他"}
LABELS = {"bug": "bug", "idea": "enhancement", "other": "question"}
STATUSES = ("open", "done")
MAX_TITLE = 100
MAX_CONTENT = 5000
MAX_PAGE = 200
MAX_USER_AGENT = 500
MAX_IMAGES = 6
# GitHub issue 的标题、正文上限（正文是 65536 字符），留点余地
MAX_ISSUE_TITLE = 256
MAX_ISSUE_BODY = 60000

API_ROOT = "https://api.github.com"
UPLOAD_URL = "https://uploads.github.com/user-attachments/assets"
# 服务器在国内，传一张几 MB 的截图要几秒到几十秒
UPLOAD_TIMEOUT = 120.0
# 服务器的时区不一定是东八区，issue 里的时间统一写北京时间
BEIJING = ZoneInfo("Asia/Shanghai")
_REPO = re.compile(r"[\w.-]+/[\w.-]+")


class FeedbackNotFoundError(LookupError):
    pass


class NotAdminError(PermissionError):
    pass


class AlreadyPushedError(Exception):
    """这条反馈已经推到 GitHub 了。"""


class GitHubUnavailableError(RuntimeError):
    """后台没配 GitHub 的 token 或仓库。"""


class GitHubError(RuntimeError):
    """GitHub 那边出错：token 不对、没权限、仓库不在、连不上。"""


@dataclass
class Feedback:
    id: int
    user_id: int
    username: str
    kind: str
    title: str
    content: str
    page: str  # 在哪个页面提交的（前端路由）
    user_agent: str
    revision: str  # 提交时后台的版本，没有是空
    status: str
    images: list[str]  # 截图的 sha，按添加的顺序
    issue_number: int | None
    issue_url: str | None
    pushed_at: float | None
    created_at: float
    updated_at: float

    @classmethod
    def from_row(cls, row: Row) -> Feedback:
        fields = {name: row[name] for name in cls.__dataclass_fields__}
        fields["images"] = json.loads(fields["images"])
        return cls(**fields)


@dataclass
class IssueDraft:
    title: str
    body: str
    labels: list[str]


def load_github() -> tuple[str | None, str | None]:
    """环境变量里的 GitHub token 和仓库（owner/name），没设是 None。"""
    token = os.environ.get("XYM_GITHUB_TOKEN", "").strip() or None
    repo = os.environ.get("XYM_GITHUB_REPO", "").strip() or None
    return token, repo


def _clean(kind: str, title: str, content: str, page: str) -> tuple[str, str, str, str]:
    if kind not in KINDS:
        raise ValueError("反馈种类只能是问题、建议、其他")
    title, content = title.strip(), content.strip()
    if not title:
        raise ValueError("标题不能为空")
    if len(title) > MAX_TITLE:
        raise ValueError(f"标题最多 {MAX_TITLE} 字")
    if not content:
        raise ValueError("请写一下具体情况")
    if len(content) > MAX_CONTENT:
        raise ValueError(f"具体情况最多 {MAX_CONTENT} 字")
    return kind, title, content, page.strip()[:MAX_PAGE]


def issue_draft(feedback: Feedback) -> IssueDraft:
    """推到 GitHub 的默认标题和正文。不带提交人：仓库公开时谁都看得到。截图推送时才接在最后。"""
    lines = [
        feedback.content,
        "",
        "---",
        f"- 种类：{KINDS[feedback.kind]}",
    ]
    if feedback.page:
        lines.append(f"- 页面：`{feedback.page}`")
    if feedback.revision:
        lines.append(f"- 版本：`{feedback.revision}`")
    if feedback.user_agent:
        lines.append(f"- 浏览器：`{feedback.user_agent}`")
    submitted = datetime.fromtimestamp(feedback.created_at, BEIJING).strftime("%Y-%m-%d %H:%M")
    lines.append(f"- 提交时间：{submitted}（北京时间）")
    lines.append(f"- 来自管理后台的问题反馈 #{feedback.id}")
    return IssueDraft(title=feedback.title, body="\n".join(lines), labels=[LABELS[feedback.kind]])


class GitHubIssues:
    """在一个仓库里建 issue、传 issue 里的图片。token 要有这个仓库的 Issues 读写权限，
    传图片还要仓库的写权限（细粒度 token 给了 Issues 读写就够，2026-11 在服务器上试过）。"""

    def __init__(
        self,
        token: str | None,
        repo: str | None,
        *,
        transport: httpx.AsyncBaseTransport | None = None,
    ):
        self._token = token
        self.repo = repo if repo and _REPO.fullmatch(repo) else None
        self._transport = transport  # 测试注入 httpx.MockTransport
        self._repo_id: int | None = None  # 传图片要仓库的数字 id，查一次记住

    @property
    def missing(self) -> str | None:
        """没配好时缺什么，配好了是 None。"""
        if not self._token:
            return "后台没配 GitHub token（XYM_GITHUB_TOKEN）"
        if not self.repo:
            return "后台没配 GitHub 仓库（XYM_GITHUB_REPO，写成 owner/name）"
        return None

    def _client(self) -> httpx.AsyncClient:
        if self.missing:
            raise GitHubUnavailableError(self.missing)
        headers = {
            "Accept": "application/vnd.github+json",
            "Authorization": f"Bearer {self._token}",
            "X-GitHub-Api-Version": "2022-11-28",
            "User-Agent": "xianyu-manager",
        }
        return httpx.AsyncClient(
            base_url=API_ROOT, headers=headers, timeout=20.0, transport=self._transport
        )

    async def create(self, title: str, body: str, labels: list[str]) -> tuple[int, str]:
        """建一个 issue，返回编号和网页地址。"""
        payload = {"title": title, "body": body, "labels": labels}
        async with self._client() as client:
            resp = await _send(client.post(f"/repos/{self.repo}/issues", json=payload))
        if resp.status_code == 201:
            data = resp.json()
            return int(data["number"]), str(data["html_url"])
        raise GitHubError(_github_error(resp, self.repo or ""))

    async def upload_images(self, images: Sequence[tuple[str, bytes]]) -> list[str]:
        """把 (文件名, JPEG 内容) 一张张传到 GitHub，返回能写进 Markdown 的图片地址。"""
        urls: list[str] = []
        async with self._client() as client:
            repo_id = await self._repository_id(client)
            for index, (name, content) in enumerate(images, 1):
                resp = await _send(
                    client.post(
                        UPLOAD_URL,
                        params={
                            "name": name,
                            "content_type": "image/jpeg",
                            "repository_id": repo_id,
                        },
                        content=content,
                        headers={"Content-Type": "application/octet-stream"},
                        timeout=UPLOAD_TIMEOUT,
                    )
                )
                url = _field(resp, "url") if resp.status_code in (200, 201) else ""
                if not url:
                    raise GitHubError(f"第 {index} 张截图没传上：{_upload_error(resp, self.repo)}")
                urls.append(url)
        return urls

    async def _repository_id(self, client: httpx.AsyncClient) -> int:
        if self._repo_id is None:
            resp = await _send(client.get(f"/repos/{self.repo}"))
            if resp.status_code != 200:
                raise GitHubError(_github_error(resp, self.repo or ""))
            self._repo_id = int(resp.json()["id"])
        return self._repo_id


async def _send(request: Awaitable[httpx.Response]) -> httpx.Response:
    try:
        return await request
    except httpx.HTTPError as exc:
        raise GitHubError(f"连不上 GitHub：{exc or type(exc).__name__}") from exc


def _github_error(resp: httpx.Response, repo: str) -> str:
    message = _field(resp, "message")
    reason = {
        401: "GitHub token 不对或者过期了",
        403: "GitHub token 没有这个仓库的 Issues 写权限，或者请求太频繁",
        404: f"找不到仓库 {repo}，或者 token 看不到它",
        410: f"仓库 {repo} 关掉了 Issues",
        422: "GitHub 不收这个 issue",
    }.get(resp.status_code, f"GitHub 返回 {resp.status_code}")
    return f"{reason}（{message}）" if message else reason


def _upload_error(resp: httpx.Response, repo: str | None) -> str:
    # 上传接口在 token 写不了仓库时回 404 而不是 403（gh 的源码里也是这么说的）
    if resp.status_code == 404:
        return f"token 没有仓库 {repo} 的写权限"
    if resp.status_code == 429:
        return "传得太频繁，等一会儿再推"
    message = _field(resp, "message")
    reason = {401: "GitHub token 不对或者过期了", 422: "GitHub 不收这张图"}.get(
        resp.status_code, f"GitHub 返回 {resp.status_code}"
    )
    return f"{reason}（{message}）" if message else reason


def _field(resp: httpx.Response, name: str) -> str:
    """GitHub 返回的 JSON 里的一个字段，不是 JSON、没有这个字段都是空串。"""
    try:
        return str(resp.json().get(name) or "")
    except (ValueError, AttributeError):
        return ""


class FeedbackService:
    """`user_id` 给了的只认这个用户提交的，None 是管理员，谁的都认。"""

    def __init__(
        self, db: Database, github: GitHubIssues, images: ImageStore, *, revision: str = ""
    ):
        self._db = db
        self.github = github
        self._images = images
        self._revision = revision
        # 同一条反馈同时只推一次，免得连点两下建出两个 issue
        self._pushing: defaultdict[int, asyncio.Lock] = defaultdict(asyncio.Lock)

    def _select(self, where: str, params: tuple[Any, ...], user_id: int | None) -> list[Feedback]:
        if user_id is not None:
            where, params = f"{where} AND f.user_id = %s", (*params, user_id)
        rows = self._db.execute(
            "SELECT f.*, u.username FROM feedback f JOIN users u ON u.id = f.user_id"
            f" WHERE {where} ORDER BY f.id DESC",
            params,
        )
        return [Feedback.from_row(row) for row in rows]

    def list(self, user_id: int | None) -> list[Feedback]:
        """新提交的在前。"""
        return self._select("TRUE", (), user_id)

    def get(self, feedback_id: int, user_id: int | None = None) -> Feedback:
        found = self._select("f.id = %s", (feedback_id,), user_id)
        if not found:
            raise FeedbackNotFoundError("找不到这条反馈")
        return found[0]

    def save_image(self, data: bytes) -> StoredImage:
        """先把一张截图存下来，提交反馈时带上返回的 sha。不是图片、太大时抛 ValueError。"""
        return self._images.save(data)

    def image_path(self, feedback_id: int, sha: str, user_id: int | None = None) -> Path:
        """这条反馈里的一张截图的文件。看不到这条反馈、反馈里没这张图都当不存在。"""
        feedback = self.get(feedback_id, user_id)
        path = self._images.path(sha) if sha in feedback.images else None
        if path is None or not path.exists():
            raise FeedbackNotFoundError("找不到这张截图")
        return path

    def _clean_images(self, images: Sequence[str]) -> list[str]:
        shas = list(dict.fromkeys(images))  # 去重，保留顺序
        if len(shas) > MAX_IMAGES:
            raise ValueError(f"截图最多 {MAX_IMAGES} 张")
        for sha in shas:
            if not self._images.path(sha).exists():
                raise ValueError("有截图没传上，删掉重新加一下")
        return shas

    def create(
        self,
        user_id: int,
        *,
        kind: str,
        title: str,
        content: str,
        page: str = "",
        user_agent: str = "",
        images: Sequence[str] = (),
    ) -> Feedback:
        kind, title, content, page = _clean(kind, title, content, page)
        shas = self._clean_images(images)
        now = time.time()
        row = self._db.execute(
            "INSERT INTO feedback (user_id, kind, title, content, page, user_agent, revision,"
            " images, created_at, updated_at)"
            " VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s) RETURNING id",
            (
                user_id,
                kind,
                title,
                content,
                page,
                user_agent.strip()[:MAX_USER_AGENT],
                self._revision,
                json.dumps(shas),
                now,
                now,
            ),
        ).fetchone()
        assert row is not None
        return self.get(row["id"])

    def delete(self, feedback_id: int, user_id: int | None = None) -> None:
        feedback = self.get(feedback_id, user_id)
        self._db.execute("DELETE FROM feedback WHERE id = %s", (feedback_id,))
        drop_unused(self._db, self._images, feedback.images)

    def set_status(self, feedback_id: int, status: str) -> Feedback:
        """open 待处理，done 处理完了。"""
        if status not in STATUSES:
            raise ValueError("状态只能是待处理、已处理")
        cursor = self._db.execute(
            "UPDATE feedback SET status = %s, updated_at = %s WHERE id = %s",
            (status, time.time(), feedback_id),
        )
        if cursor.rowcount == 0:
            raise FeedbackNotFoundError("找不到这条反馈")
        return self.get(feedback_id)

    async def push(
        self,
        feedback_id: int,
        *,
        title: str | None = None,
        body: str | None = None,
        images: Sequence[str] | None = None,
    ) -> Feedback:
        """推到 GitHub 建一个 issue。标题、正文不给就用默认的（issue_draft）；
        `images` 是一起推的截图，不给就是全部，按反馈里的顺序接在正文最后。"""
        async with self._pushing[feedback_id]:
            feedback = self.get(feedback_id)
            if feedback.issue_number is not None:
                raise AlreadyPushedError(f"这条反馈已经推到 GitHub 了：#{feedback.issue_number}")
            draft = issue_draft(feedback)
            title = draft.title if title is None else title.strip()
            body = draft.body if body is None else body.strip()
            if not title:
                raise ValueError("issue 标题不能为空")
            if len(title) > MAX_ISSUE_TITLE:
                raise ValueError(f"issue 标题最多 {MAX_ISSUE_TITLE} 字")
            if len(body) > MAX_ISSUE_BODY:
                raise ValueError(f"issue 正文最多 {MAX_ISSUE_BODY} 字")
            chosen = set(feedback.images if images is None else images)
            if chosen - set(feedback.images):
                raise ValueError("要推的截图不在这条反馈里")
            # 截图编号按反馈里的顺序，和页面上的「截图 1、截图 2」对得上
            numbered = [(n, sha) for n, sha in enumerate(feedback.images, 1) if sha in chosen]
            if numbered:
                files = [
                    (f"feedback-{feedback.id}-{n}.jpg", self._images.read(sha))
                    for n, sha in numbered
                ]
                urls = await self.github.upload_images(files)
                pictures = [
                    f"![截图 {n}]({url})" for (n, _), url in zip(numbered, urls, strict=True)
                ]
                body = "\n\n".join([body, *pictures] if body else pictures)
            number, url = await self.github.create(title, body, draft.labels)
            now = time.time()
            self._db.execute(
                "UPDATE feedback SET issue_number = %s, issue_url = %s, pushed_at = %s,"
                " updated_at = %s WHERE id = %s",
                (number, url, now, now, feedback_id),
            )
        return self.get(feedback_id)
