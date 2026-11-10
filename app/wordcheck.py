"""本地违规词检查：按 app/forbidden_words.toml 的词表查一段文字，不请求闲鱼。

只做提醒，不拦发布（拦不拦只看平台的 badwords.prepubcheck，见 app/drafts.py）。
词表的写法见数据文件开头的说明，收词的依据见 docs/forbidden-words-research.md 第三节。

匹配前先把文字统一成小写、半角（一个字一个字地转，转完不是一个字的保持原样），
所以命中的位置和原文一一对应，页面可以直接按位置截出上下文。
"""

from __future__ import annotations

import re
import tomllib
import unicodedata
from dataclasses import dataclass
from functools import cache
from pathlib import Path
from typing import Any

WORDS_PATH = Path(__file__).with_name("forbidden_words.toml")

LEVELS = {"strong": "强提醒", "weak": "弱提醒", "info": "告知"}
BASES = {"official": "官方", "law": "法律", "opensource": "开源", "experience": "经验"}

# 词组的字中间允许夹的符号（"微 信"、"V.X"），最多两个
_SEP = r"[\s·•.\-_*/|~]{0,2}"
# 组合规则的两个词之间不跨这些
_BREAK = r"\n。！？!?；;"
_RULE_KEYS = {"category", "label", "words", "pattern", "combo", "gap", "level", "basis"}
_RULE_KEYS |= {"suggest", "exclude"}
_CATEGORY_KEYS = {"name", "level", "basis", "note"}


@dataclass(frozen=True)
class Category:
    key: str
    name: str
    level: str
    basis: str
    note: str | None


@dataclass(frozen=True)
class Rule:
    category: Category
    label: str
    regex: re.Pattern[str]
    level: str
    basis: str
    suggest: str | None
    exclude: tuple[str, ...]


@dataclass(frozen=True)
class Hit:
    """一处命中。`text` 是原文里命中的那段，`start`/`end` 是它在原文里的位置。"""

    text: str
    start: int
    end: int
    rule: str  # 规则名：词组规则是词表里的那个词，正则和组合规则是 label
    category: str
    category_name: str
    level: str
    basis: str
    suggest: str | None
    note: str | None


def normalize(text: str) -> str:
    """小写、全角转半角（NFKC），一个字一个字地转，长度不变。"""
    out = []
    for ch in text:
        mapped = unicodedata.normalize("NFKC", ch).lower()
        out.append(mapped if len(mapped) == 1 else ch)
    return "".join(out)


def check(text: str, rules: list[Rule] | None = None) -> list[Hit]:
    """查一段文字，按位置排好返回命中的地方。同一类里被别的命中包住的不重复给。"""
    if not text:
        return []
    rules = load_rules() if rules is None else rules
    norm = normalize(text)
    found: list[Hit] = []
    for rule in rules:
        excluded = [
            (m.start(), m.end()) for phrase in rule.exclude for m in _find_all(norm, phrase)
        ]
        for m in rule.regex.finditer(norm):
            start, end = m.span()
            if start == end or any(s <= start and end <= e for s, e in excluded):
                continue
            category = rule.category
            found.append(
                Hit(
                    text=text[start:end],
                    start=start,
                    end=end,
                    rule=rule.label,
                    category=category.key,
                    category_name=category.name,
                    level=rule.level,
                    basis=rule.basis,
                    suggest=rule.suggest,
                    note=category.note,
                )
            )
    found.sort(key=lambda h: (h.start, h.start - h.end))
    kept: list[Hit] = []
    for hit in found:
        if any(
            k.category == hit.category and k.start <= hit.start and hit.end <= k.end for k in kept
        ):
            continue
        kept.append(hit)
    return kept


@cache
def load_rules(path: Path = WORDS_PATH) -> list[Rule]:
    """读词表。写错了（不认识的键、类别、档位，正则编译不过）直接报错，测试会跑一遍。"""
    with path.open("rb") as f:
        data = tomllib.load(f)
    categories = {}
    for key, raw in data.get("categories", {}).items():
        _check_keys(raw, _CATEGORY_KEYS, f"类别 {key}")
        categories[key] = Category(
            key=key,
            name=raw["name"],
            level=_one_of(raw["level"], LEVELS, f"类别 {key} 的档位"),
            basis=_one_of(raw["basis"], BASES, f"类别 {key} 的依据"),
            note=raw.get("note"),
        )
    rules: list[Rule] = []
    for index, raw in enumerate(data.get("rules", []), 1):
        where = f"第 {index} 条规则"
        _check_keys(raw, _RULE_KEYS, where)
        category = categories.get(raw.get("category", ""))
        if category is None:
            raise ValueError(f"{where}：不认识的类别 {raw.get('category')}")
        kinds = [k for k in ("words", "pattern", "combo") if k in raw]
        if len(kinds) != 1:
            raise ValueError(f"{where}：words、pattern、combo 要写且只写一个")
        common: dict[str, Any] = {
            "category": category,
            "level": _one_of(raw.get("level", category.level), LEVELS, f"{where}的档位"),
            "basis": _one_of(raw.get("basis", category.basis), BASES, f"{where}的依据"),
            "suggest": raw.get("suggest"),
            "exclude": tuple(normalize(p) for p in raw.get("exclude", [])),
        }
        if "words" in raw:
            # 一个词一条，页面上显示命中的是哪个词
            for word in raw["words"]:
                rules.append(Rule(label=word, regex=re.compile(_word(word)), **common))
            continue
        label = raw.get("label")
        if not label:
            raise ValueError(f"{where}：正则、组合规则要写 label")
        if "pattern" in raw:
            regex = re.compile(raw["pattern"])
        else:
            first, then = raw["combo"]
            gap = int(raw.get("gap", 2))
            regex = re.compile(f"{_any(first)}[^{_BREAK}]{{0,{gap}}}?{_any(then)}")
        rules.append(Rule(label=label, regex=regex, **common))
    return rules


def _word(word: str) -> str:
    """词组规则的一个词。不扫单字（组合规则里的单字，比如"加"，不受这个限制）。"""
    if len(normalize(word.strip())) < 2:
        raise ValueError(f"不扫单字：{word}")
    return _term(word)


def _any(words: list[str]) -> str:
    return f"(?:{'|'.join(_term(w) for w in words)})"


def _term(word: str) -> str:
    """一个词 → 正则：字中间可以夹符号；英文数字开头结尾的，挨着英文数字不算。"""
    norm = normalize(word.strip())
    body = _SEP.join(re.escape(ch) for ch in norm)
    if _ascii_alnum(norm[0]):
        body = f"(?<![a-z0-9]){body}"
    if _ascii_alnum(norm[-1]):
        body = f"{body}(?![a-z0-9])"
    return body


def _ascii_alnum(ch: str) -> bool:
    return ch.isascii() and ch.isalnum()


def _find_all(text: str, phrase: str):
    return re.finditer(re.escape(phrase), text) if phrase else iter(())


def _check_keys(raw: dict[str, Any], allowed: set[str], where: str) -> None:
    unknown = set(raw) - allowed
    if unknown:
        raise ValueError(f"{where}：不认识的键 {', '.join(sorted(unknown))}")


def _one_of(value: str, allowed: dict[str, str], what: str) -> str:
    if value not in allowed:
        raise ValueError(f"{what}只能是 {'、'.join(allowed)}，不是 {value}")
    return value
