#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
Build assets/data/articles-index.json from docs/0.4.md and docs/0.5.md.

Usage:
    python tools/build_articles_index.py
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Dict, List, Optional

ROOT = Path(__file__).resolve().parents[1]
DOCS = [
    ("0.4", ROOT / "docs" / "0.4.md"),
    ("0.5", ROOT / "docs" / "0.5.md"),
]
OUTPUT = ROOT / "assets" / "data" / "articles-index.json"

_DIGIT_TRANS = str.maketrans(
    "۰۱۲۳۴۵۶۷۸۹٠١٢٣٤٥٦٧٨٩",
    "01234567890123456789",
)

ARTICLE_HEADING = re.compile(
    r"""
    ^\s*
    (?:\#{1,6}\s*)?
    (?:\*\*)?
    ماده
    (?:\s*ٔ|\s*‌ی)?
    \s+
    (?P<id>[۰-۹٠-٩0-9]+(?:\s*[-–—−]\s*[۰-۹٠-٩0-9]+)?)
    \s*
    (?P<sep>[ـ\-–—−:：.]|\*\*)?
    \s*
    (?P<title>.*?)
    (?:\*\*)?
    \s*$
    """,
    re.MULTILINE | re.VERBOSE,
)

CHAPTER_HEADING = re.compile(
    r"^\s*##\s+(فصل\s+[^\n]+)\s*$",
    re.MULTILINE,
)

HTML_ANCHOR = re.compile(r'^\s*<a\s+id=["\']([^"\']+)["\']\s*></a>\s*$', re.I)


def normalize_digits(value: str) -> str:
    return value.translate(_DIGIT_TRANS)


def normalize_article_id(value: str) -> str:
    value = normalize_digits(value)
    value = value.replace("–", "-").replace("—", "-").replace("−", "-")
    value = re.sub(r"\s*-\s*", "-", value)
    return value.strip()


def strip_markdown(value: str) -> str:
    value = re.sub(r"`([^`]+)`", r"\1", value)
    value = re.sub(r"\*\*([^*]+)\*\*", r"\1", value)
    value = re.sub(r"\*([^*]+)\*", r"\1", value)
    value = re.sub(r"\[([^\]]+)\]\([^)]+\)", r"\1", value)
    value = re.sub(r"<[^>]+>", " ", value)
    value = re.sub(r"\s+", " ", value)
    return value.strip(" \t\r\n-–—ـ:：.")


def find_current_chapter(content: str, pos: int) -> str:
    chapter = ""
    for match in CHAPTER_HEADING.finditer(content, 0, pos):
        chapter = strip_markdown(match.group(1))
    return chapter


def article_anchor(article_id: str) -> str:
    return f"article-{article_id}"


def parse_document(version: str, path: Path) -> List[Dict[str, object]]:
    if not path.is_file():
        raise FileNotFoundError(path)

    content = path.read_text(encoding="utf-8", errors="strict")
    matches = list(ARTICLE_HEADING.finditer(content))
    records: List[Dict[str, object]] = []

    for idx, match in enumerate(matches):
        article_id = normalize_article_id(match.group("id"))
        title = strip_markdown(match.group("title") or "")
        start = match.end()
        end = matches[idx + 1].start() if idx + 1 < len(matches) else len(content)
        body = content[start:end]

        # stop before next chapter divider if the parser reaches one
        chapter_break = re.search(r"^\s*##\s+فصل\s+", body, re.MULTILINE)
        if chapter_break:
            body = body[:chapter_break.start()]

        body_lines = []
        for raw_line in body.splitlines():
            line = raw_line.strip()
            if not line:
                continue
            if line == "---":
                continue
            if HTML_ANCHOR.match(line):
                continue
            if line.startswith("## "):
                break
            body_lines.append(strip_markdown(line))

        text = " ".join(x for x in body_lines if x)
        text = re.sub(r"\s+", " ", text).strip()

        chapter = find_current_chapter(content, match.start())
        label = f"ماده {article_id}"
        if title:
            label += f" ـ {title}"

        records.append({
            "id": f"{version}:{article_id}",
            "version": version,
            "articleId": article_id,
            "title": title,
            "label": label,
            "chapter": chapter,
            "text": text,
            "excerpt": text[:360],
            "sourceFile": f"docs/{path.name}",
            "url": f"../docs/{path.name}#{article_anchor(article_id)}",
            "type": "article",
        })

    return records


def main() -> int:
    all_articles: List[Dict[str, object]] = []
    versions: Dict[str, int] = {}

    for version, path in DOCS:
        records = parse_document(version, path)
        versions[version] = len(records)
        all_articles.extend(records)

    payload = {
        "schema": 1,
        "generatedBy": "tools/build_articles_index.py",
        "versions": versions,
        "total": len(all_articles),
        "articles": all_articles,
    }

    OUTPUT.parent.mkdir(parents=True, exist_ok=True)
    OUTPUT.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )

    print(f"✅ articles index written: {OUTPUT}")
    print(f"   total: {len(all_articles)}")
    for version, count in versions.items():
        print(f"   v{version}: {count}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
