"""问题反馈：后台用户提交问题和建议，管理员查看、标记处理、推送成 GitHub issue。

- 每个用户都能提交，看得到、删得掉自己提交的；别人的和不存在的一样（404）；
- 管理员（users.is_admin）看得到所有人的，能标记处理完、重新打开，能推到 GitHub；
- 推送用 GitHub REST 接口建 issue。token 和仓库取环境变量 XYM_GITHUB_TOKEN、XYM_GITHUB_REPO
  （服务器上写在 /etc/xym.env），token 只在后台用，不发给浏览器；
- 仓库是公开的时候 issue 谁都看得到：推送前管理员能改标题和正文，默认的正文不带提交人的用户名；
- 一条反馈只推一次，推过的记下 issue 编号和链接。删掉反馈不动 GitHub 上的 issue。
"""

from __future__ import annotations

import asyncio
import os
import re
import time
from collections import defaultdict
from dataclasses import dataclass
from datetime import datetime
from typing import Any
from zoneinfo import ZoneInfo

import httpx

from app.db import Database, Row

# 种类 → 页面上叫什么、推到 GitHub 时打什么标签（仓库自带的默认标签，不用另建）
KINDS = {"bug": "问题", "idea": "建议", "other": "其他"}
LABELS = {"bug": "bug", "idea": "enhancement", "other": "question"}
STATUSES = ("open", "done")
MAX_TITLE = 100
MAX_CONTENT = 5000
MAX_PAGE = 200
MAX_USER_AGENT = 500
# GitHub issue 的标题、正文上限（正文是 65536 字符），留点余地
MAX_ISSUE_TITLE = 256
MAX_ISSUE_BODY = 60000

API_ROOT = "https://api.github.com"
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
    issue_number: int | None
    issue_url: str | None
    pushed_at: float | None
    created_at: float
    updated_at: float

    @classmethod
    def from_row(cls, row: Row) -> Feedback:
        return cls(**{name: row[name] for name in cls.__dataclass_fields__})


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
    """推到 GitHub 的默认标题和正文。不带提交人：仓库公开时谁都看得到。"""
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
    """在一个仓库里建 issue。token 要有这个仓库的 Issues 读写权限。"""

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

    @property
    def missing(self) -> str | None:
        """没配好时缺什么，配好了是 None。"""
        if not self._token:
            return "后台没配 GitHub token（XYM_GITHUB_TOKEN）"
        if not self.repo:
            return "后台没配 GitHub 仓库（XYM_GITHUB_REPO，写成 owner/name）"
        return None

    async def create(self, title: str, body: str, labels: list[str]) -> tuple[int, str]:
        """建一个 issue，返回编号和网页地址。"""
        if self.missing:
            raise GitHubUnavailableError(self.missing)
        headers = {
            "Accept": "application/vnd.github+json",
            "Authorization": f"Bearer {self._token}",
            "X-GitHub-Api-Version": "2022-11-28",
            "User-Agent": "xianyu-manager",
        }
        payload = {"title": title, "body": body, "labels": labels}
        async with httpx.AsyncClient(
            base_url=API_ROOT, timeout=20.0, transport=self._transport
        ) as client:
            try:
                resp = await client.post(
                    f"/repos/{self.repo}/issues", json=payload, headers=headers
                )
            except httpx.HTTPError as exc:
                raise GitHubError(f"连不上 GitHub：{exc or type(exc).__name__}") from exc
        if resp.status_code == 201:
            data = resp.json()
            return int(data["number"]), str(data["html_url"])
        raise GitHubError(_github_error(resp, self.repo or ""))


def _github_error(resp: httpx.Response, repo: str) -> str:
    try:
        message = str(resp.json().get("message") or "")
    except ValueError:
        message = ""
    reason = {
        401: "GitHub token 不对或者过期了",
        403: "GitHub token 没有这个仓库的 Issues 写权限，或者请求太频繁",
        404: f"找不到仓库 {repo}，或者 token 看不到它",
        410: f"仓库 {repo} 关掉了 Issues",
        422: "GitHub 不收这个 issue",
    }.get(resp.status_code, f"GitHub 返回 {resp.status_code}")
    return f"{reason}（{message}）" if message else reason


class FeedbackService:
    """`user_id` 给了的只认这个用户提交的，None 是管理员，谁的都认。"""

    def __init__(self, db: Database, github: GitHubIssues, *, revision: str = ""):
        self._db = db
        self.github = github
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

    def create(
        self,
        user_id: int,
        *,
        kind: str,
        title: str,
        content: str,
        page: str = "",
        user_agent: str = "",
    ) -> Feedback:
        kind, title, content, page = _clean(kind, title, content, page)
        now = time.time()
        row = self._db.execute(
            "INSERT INTO feedback (user_id, kind, title, content, page, user_agent, revision,"
            " created_at, updated_at) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s) RETURNING id",
            (
                user_id,
                kind,
                title,
                content,
                page,
                user_agent.strip()[:MAX_USER_AGENT],
                self._revision,
                now,
                now,
            ),
        ).fetchone()
        assert row is not None
        return self.get(row["id"])

    def delete(self, feedback_id: int, user_id: int | None = None) -> None:
        self.get(feedback_id, user_id)
        self._db.execute("DELETE FROM feedback WHERE id = %s", (feedback_id,))

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
        self, feedback_id: int, *, title: str | None = None, body: str | None = None
    ) -> Feedback:
        """推到 GitHub 建一个 issue。标题、正文不给就用默认的（issue_draft）。"""
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
            number, url = await self.github.create(title, body, draft.labels)
            now = time.time()
            self._db.execute(
                "UPDATE feedback SET issue_number = %s, issue_url = %s, pushed_at = %s,"
                " updated_at = %s WHERE id = %s",
                (number, url, now, now, feedback_id),
            )
        return self.get(feedback_id)
