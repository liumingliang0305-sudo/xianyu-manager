"""提示词模板：按管理后台的用户保存名称和正文，提供增删改查。"""

from __future__ import annotations

import time
from dataclasses import dataclass

from app.db import Database, Row

MAX_NAME = 100
MAX_CONTENT = 20000
PREVIEW_LENGTH = 160


class PromptTemplateNotFoundError(LookupError):
    pass


@dataclass
class PromptTemplate:
    id: int
    name: str
    content: str
    created_at: float
    updated_at: float

    @classmethod
    def from_row(cls, row: Row) -> PromptTemplate:
        return cls(
            id=row["id"],
            name=row["name"],
            content=row["content"],
            created_at=row["created_at"],
            updated_at=row["updated_at"],
        )


@dataclass
class PromptTemplateSummary:
    id: int
    name: str
    preview: str
    content_length: int
    created_at: float
    updated_at: float


def _clean(name: str, content: str) -> tuple[str, str]:
    name, content = name.strip(), content.strip()
    if not name:
        raise ValueError("模板名称不能为空")
    if len(name) > MAX_NAME:
        raise ValueError(f"模板名称最多 {MAX_NAME} 字")
    if not content:
        raise ValueError("模板内容不能为空")
    if len(content) > MAX_CONTENT:
        raise ValueError(f"模板内容最多 {MAX_CONTENT} 字")
    return name, content


class PromptTemplateService:
    def __init__(self, db: Database):
        self._db = db

    def list(self, user_id: int) -> list[PromptTemplateSummary]:
        """列表只取正文的开头，完整内容在查看、编辑时读取。最近修改的在前。"""
        rows = self._db.execute(
            "SELECT id, name, left(content, %s) AS preview,"
            " char_length(content) AS content_length, created_at, updated_at"
            " FROM prompt_templates WHERE user_id = %s ORDER BY updated_at DESC, id DESC",
            (PREVIEW_LENGTH, user_id),
        )
        return [PromptTemplateSummary(**row) for row in rows]

    def get(self, user_id: int, template_id: int) -> PromptTemplate:
        row = self._db.execute(
            "SELECT * FROM prompt_templates WHERE user_id = %s AND id = %s",
            (user_id, template_id),
        ).fetchone()
        if row is None:
            raise PromptTemplateNotFoundError("找不到这个模板")
        return PromptTemplate.from_row(row)

    def find(self, user_id: int, template_id: int) -> PromptTemplate | None:
        """同 get，找不到是 None（AI 对话带的模板可能已经删掉了）。"""
        try:
            return self.get(user_id, template_id)
        except PromptTemplateNotFoundError:
            return None

    def create(self, user_id: int, *, name: str, content: str) -> PromptTemplate:
        name, content = _clean(name, content)
        now = time.time()
        row = self._db.execute(
            "INSERT INTO prompt_templates (user_id, name, content, created_at, updated_at)"
            " VALUES (%s, %s, %s, %s, %s) RETURNING *",
            (user_id, name, content, now, now),
        ).fetchone()
        assert row is not None
        return PromptTemplate.from_row(row)

    def update(self, user_id: int, template_id: int, *, name: str, content: str) -> PromptTemplate:
        name, content = _clean(name, content)
        row = self._db.execute(
            "UPDATE prompt_templates SET name = %s, content = %s, updated_at = %s"
            " WHERE user_id = %s AND id = %s RETURNING *",
            (name, content, time.time(), user_id, template_id),
        ).fetchone()
        if row is None:
            raise PromptTemplateNotFoundError("找不到这个模板")
        return PromptTemplate.from_row(row)

    def delete(self, user_id: int, template_id: int) -> None:
        cursor = self._db.execute(
            "DELETE FROM prompt_templates WHERE user_id = %s AND id = %s",
            (user_id, template_id),
        )
        if cursor.rowcount == 0:
            raise PromptTemplateNotFoundError("找不到这个模板")
