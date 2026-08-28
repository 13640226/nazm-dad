#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
Nazm Dad — Project Status & Documentation Tool
نظم داد — ابزار وضعیت، اعتبارسنجی و مستندسازی پروژه

Version: 2.5.1

ویژگی‌های نسخه 2.5.1
-------------------
- پشتیبانی کامل از فایل پیکربندی (.nazm-dad-config.json)
- اولویت: CLI > config file > default
- گزارش پیشرفت برای عملیات طولانی
- خروجی HTML با escaping امن
- خروجی Markdown
- خروجی JSON
- بررسی Git
- بررسی اسناد
- بررسی لینک‌ها و ارجاعات
- تحلیل شماره‌گذاری و ترتیب مواد
- استخراج آمار اسناد
- Health Score
- پشتیبانی از docs_path خارج از مخزن
- پشتیبانی از health=None در HTML
- جستجوی خودکار config کنار مخزن
"""

from __future__ import annotations

import argparse
import difflib
import html as html_lib
import json
import os
import re
import subprocess
import sys

from dataclasses import asdict, dataclass, field
from datetime import datetime
from enum import Enum
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Sequence, Set, Tuple


# ============================================================
# Version
# ============================================================

TOOL_VERSION = "2.5.1"


# ============================================================
# Exit Codes
# ============================================================

class ExitCode(int, Enum):
    OK = 0
    VALIDATION_FAILED = 1
    RUNTIME_ERROR = 2
    USAGE_ERROR = 3


# ============================================================
# Status Enums
# ============================================================

class DocumentStatus(str, Enum):
    COMPLETE = "کامل و authoritative"
    PUBLISHED = "منتشر شده"
    PLACEHOLDER = "placeholder (در انتظار محتوای نهایی)"
    INVALID = "نامعتبر (ساختار یا محتوا اشتباه است)"
    MISSING = "وجود ندارد"


class LinkStatus(str, Enum):
    OK = "ok"
    BROKEN = "broken"
    SKIPPED = "skipped"


# ============================================================
# Configuration
# ============================================================

@dataclass
class ProjectConfig:
    repo_path: Optional[str] = None
    output_dir: Optional[str] = None
    docs_path: Optional[str] = None

    expected_articles_v04: int = 61
    expected_articles_v05: int = 73

    expected_v05_additional: List[str] = field(
        default_factory=lambda: [
            "23-1",
            "32-1",
            "32-2",
            "32-3",
            "37-1",
            "43-1",
            "46-1",
            "48-1",
            "52-1",
            "52-2",
            "54-1",
            "54-2",
        ]
    )

    total_real_changes: int = 21
    total_noop_changes: int = 2

    health_threshold_ok: int = 90

    @classmethod
    def from_file(cls, path: Path) -> "ProjectConfig":
        if not path.exists():
            return cls()

        try:
            raw = path.read_text(encoding="utf-8")
            data = json.loads(raw)

            if not isinstance(data, dict):
                raise TypeError(
                    "ریشه فایل پیکربندی باید یک JSON object باشد."
                )

            valid_keys = set(cls.__dataclass_fields__.keys())

            filtered_data = {
                key: value
                for key, value in data.items()
                if key in valid_keys
            }

            return cls(**filtered_data)

        except (json.JSONDecodeError, TypeError, OSError) as exc:
            print(
                f"⚠️ خطا در فایل پیکربندی '{path}':\n{exc}",
                file=sys.stderr,
            )
            print(
                "⚠️ استفاده از مقادیر پیش‌فرض.",
                file=sys.stderr,
            )
            return cls()

    def to_dict(self) -> Dict[str, Any]:
        return {
            key: value
            for key, value in asdict(self).items()
            if value is not None
        }


# ============================================================
# Progress Reporter
# ============================================================

class ProgressReporter:
    def __init__(
        self,
        enabled: bool = True,
        desc: str = "",
        total: int = 0,
    ):
        self.enabled = enabled
        self.desc = desc
        self.total = total
        self.current = 0
        self._tqdm = None

        if enabled and total > 0:
            try:
                from tqdm import tqdm

                self._tqdm = tqdm(
                    total=total,
                    desc=desc,
                    unit="item",
                    ncols=80,
                    bar_format=(
                        "{l_bar}{bar}| "
                        "{n_fmt}/{total_fmt} "
                        "[{elapsed}<{remaining}]"
                    ),
                )

            except ImportError:
                self._tqdm = None
                print(
                    f"  {desc}: 0/{total}",
                    end="",
                )

    def update(self, n: int = 1) -> None:
        self.current += n

        if self._tqdm:
            self._tqdm.update(n)

        elif self.enabled:
            print(
                f"\r  {self.desc}: "
                f"{self.current}/{self.total}",
                end="",
            )

    def close(self) -> None:
        if self._tqdm:
            self._tqdm.close()

        elif self.enabled:
            print()

    def __enter__(self):
        return self

    def __exit__(self, *args):
        self.close()


# ============================================================
# Helpers
# ============================================================

def html_escape(value: Any) -> str:
    return html_lib.escape(
        str(value),
        quote=True,
    )


def safe_display_path(
    path: Path,
    base: Path,
) -> str:
    try:
        return str(
            path.relative_to(base)
        )
    except ValueError:
        return str(path)


# ============================================================
# Data Classes
# ============================================================

@dataclass
class GitInfo:
    branch: str
    is_clean: bool
    last_commit_hash: str
    last_commit_message: str
    last_commit_date: str
    tags: List[str]
    ahead: int
    behind: int

    upstream: Optional[str] = None
    remote: Optional[str] = None
    repository_root: Optional[str] = None


@dataclass
class ArticleStats:
    total_expected: int
    total_detected: int
    ids: List[str]
    missing: List[str]
    duplicates: List[str]
    out_of_order: List[str]
    has_continuity: bool


@dataclass
class DocumentStats:
    name: str
    status: DocumentStatus
    size_bytes: int
    lines: int
    characters: int
    articles: ArticleStats
    has_placeholder: bool
    description: str


@dataclass
class DocumentInfo:
    path: str
    status: DocumentStatus
    size_bytes: int

    lines: int = 0
    characters: int = 0

    articles_count: Optional[int] = None
    detected_articles: Optional[int] = None

    detected_article_ids: List[str] = field(
        default_factory=list
    )

    missing_articles: List[str] = field(
        default_factory=list
    )

    description: str = ""
    is_placeholder: bool = False


@dataclass
class TagInfo:
    name: str
    commit: str
    date: str
    message: str
    tagger: str = ""


@dataclass
class LinkIssue:
    source: str
    target: str
    line: int
    status: LinkStatus
    description: str


@dataclass
class LinkReport:
    checked: int = 0
    valid: int = 0
    broken: int = 0
    skipped: int = 0

    issues: List[LinkIssue] = field(
        default_factory=list
    )


@dataclass
class HealthComponent:
    name: str
    score: int
    max_score: int
    status: str
    detail: str


@dataclass
class HealthReport:
    score: int
    max_score: int
    percent: float
    grade: str

    components: List[HealthComponent] = field(
        default_factory=list
    )


@dataclass
class ProjectStatus:
    git: GitInfo
    documents: Dict[str, DocumentInfo]

    document_stats: Dict[str, DocumentStats] = field(
        default_factory=dict
    )

    total_articles_v04: int = 61
    total_articles_v05: int = 73

    expected_v05_additional: Set[str] = field(
        default_factory=lambda: {
            "23-1",
            "32-1",
            "32-2",
            "32-3",
            "37-1",
            "43-1",
            "46-1",
            "48-1",
            "52-1",
            "52-2",
            "54-1",
            "54-2",
        }
    )

    total_changes: int = 21
    noop_changes: int = 2

    tool_version: str = TOOL_VERSION

    timestamp: str = field(
        default_factory=lambda:
        datetime.now().astimezone().isoformat()
    )


# ============================================================
# Console
# ============================================================

class Console:
    COLORS = {
        "green": "\033[92m",
        "red": "\033[91m",
        "yellow": "\033[93m",
        "blue": "\033[94m",
        "cyan": "\033[96m",
        "dim": "\033[2m",
        "reset": "\033[0m",
    }

    def __init__(
        self,
        enable_color: bool = True,
    ):
        self.enable_color = (
            enable_color
            and sys.stdout.isatty()
            and os.getenv("NO_COLOR") is None
        )

        if (
            os.name == "nt"
            and self.enable_color
        ):
            try:
                os.system("")
            except Exception:
                pass

    def color(
        self,
        text: str,
        color: str,
    ) -> str:
        if not self.enable_color:
            return text

        return (
            f"{self.COLORS.get(color, '')}"
            f"{text}"
            f"{self.COLORS['reset']}"
        )

    def success(self, text: str) -> str:
        return self.color(
            text,
            "green",
        )

    def error(self, text: str) -> str:
        return self.color(
            text,
            "red",
        )

    def warning(self, text: str) -> str:
        return self.color(
            text,
            "yellow",
        )

    def info(self, text: str) -> str:
        return self.color(
            text,
            "cyan",
        )


# ============================================================
# Verbose Logger
# ============================================================

class VerboseLogger:
    def __init__(
        self,
        enabled: bool = False,
    ):
        self.enabled = enabled

    def log(
        self,
        message: str,
    ) -> None:
        if self.enabled:
            print(
                f"[verbose] {message}"
            )


# ============================================================
# Git Collector
# ============================================================

class GitInfoCollector:
    def __init__(
        self,
        repo_path: Path,
        logger: Optional[VerboseLogger] = None,
    ):
        self.repo_path = repo_path.resolve()

        self.logger = (
            logger
            or VerboseLogger(False)
        )

    def _run_git(
        self,
        args: Sequence[str],
        check: bool = True,
    ) -> subprocess.CompletedProcess:

        cmd = [
            "git",
            "-C",
            str(self.repo_path),
            *args,
        ]

        self.logger.log(
            f"Git: {' '.join(cmd)}"
        )

        try:
            return subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                check=check,
            )

        except FileNotFoundError as exc:
            raise RuntimeError(
                "Git روی سیستم پیدا نشد "
                "یا در PATH قرار ندارد."
            ) from exc

        except subprocess.CalledProcessError as exc:
            if check:
                stderr = (
                    exc.stderr or ""
                ).strip()

                raise RuntimeError(
                    "Git command failed:\n"
                    f"{' '.join(cmd)}\n"
                    f"{stderr}"
                ) from exc

            return exc

    def check_repository(self) -> None:
        result = self._run_git(
            [
                "rev-parse",
                "--is-inside-work-tree",
            ],
            check=False,
        )

        if result.returncode != 0:
            raise RuntimeError(
                f"مسیر '{self.repo_path}' "
                "مخزن Git معتبر نیست."
            )

        if (
            result.stdout
            .strip()
            .lower()
            != "true"
        ):
            raise RuntimeError(
                f"مسیر '{self.repo_path}' "
                "داخل Git work tree نیست."
            )

    def get_repo_root(self) -> Path:
        output = self._run_git(
            [
                "rev-parse",
                "--show-toplevel",
            ]
        ).stdout.strip()

        return Path(
            output
        ).resolve()

    def get_upstream(
        self,
    ) -> Optional[str]:

        result = self._run_git(
            [
                "rev-parse",
                "--abbrev-ref",
                "--symbolic-full-name",
                "@{upstream}",
            ],
            check=False,
        )

        if result.returncode != 0:
            return None

        value = result.stdout.strip()

        return value or None

    def get_ahead_behind(
        self,
        upstream: Optional[str],
    ) -> Tuple[int, int]:

        if not upstream:
            return 0, 0

        result = self._run_git(
            [
                "rev-list",
                "--left-right",
                "--count",
                f"HEAD...{upstream}",
            ],
            check=False,
        )

        if result.returncode != 0:
            return 0, 0

        parts = (
            result.stdout
            .strip()
            .split()
        )

        if len(parts) != 2:
            return 0, 0

        try:
            ahead = int(parts[0])
            behind = int(parts[1])

            return ahead, behind

        except ValueError:
            return 0, 0

    def get_info(
        self,
    ) -> GitInfo:

        self.logger.log(
            "Collecting Git information"
        )

        self.check_repository()

        repo_root = self.get_repo_root()

        self.logger.log(
            f"Repository root: {repo_root}"
        )

        branch = self._run_git(
            [
                "rev-parse",
                "--abbrev-ref",
                "HEAD",
            ]
        ).stdout.strip()

        porcelain = self._run_git(
            [
                "status",
                "--porcelain",
            ]
        ).stdout

        is_clean = not bool(
            porcelain.strip()
        )

        commit_lines = (
            self._run_git(
                [
                    "log",
                    "-1",
                    "--format=%H%n%s%n%ai",
                ]
            )
            .stdout
            .rstrip("\n")
            .splitlines()
        )

        commit_hash = (
            commit_lines[0]
            if len(commit_lines) > 0
            else ""
        )

        commit_message = (
            commit_lines[1]
            if len(commit_lines) > 1
            else ""
        )

        commit_date = (
            commit_lines[2]
            if len(commit_lines) > 2
            else ""
        )

        tags_output = self._run_git(
            [
                "tag",
                "--list",
                "--sort=-creatordate",
            ]
        ).stdout

        tags = [
            line.strip()
            for line
            in tags_output.splitlines()
            if line.strip()
        ]

        upstream = self.get_upstream()

        ahead, behind = (
            self.get_ahead_behind(
                upstream
            )
        )

        remote_result = self._run_git(
            [
                "remote",
                "get-url",
                "origin",
            ],
            check=False,
        )

        remote = (
            remote_result.stdout.strip()
            if remote_result.returncode == 0
            else None
        )

        return GitInfo(
            branch=branch,
            is_clean=is_clean,
            last_commit_hash=(
                commit_hash[:8]
            ),
            last_commit_message=(
                commit_message
            ),
            last_commit_date=(
                commit_date
            ),
            tags=tags,
            ahead=ahead,
            behind=behind,
            upstream=upstream,
            remote=remote or None,
            repository_root=str(
                repo_root
            ),
        )

    def get_tags_history(
        self,
    ) -> List[TagInfo]:

        self.check_repository()

        tags = (
            self._run_git(
                [
                    "tag",
                    "--list",
                    "--sort=-creatordate",
                ]
            )
            .stdout
            .splitlines()
        )

        results: List[
            TagInfo
        ] = []

        for tag in tags:
            tag = tag.strip()

            if not tag:
                continue

            commit = self._run_git(
                [
                    "rev-list",
                    "-n",
                    "1",
                    tag,
                ],
                check=False,
            ).stdout.strip()

            log_output = (
                self._run_git(
                    [
                        "log",
                        "-1",
                        "--format=%h%n%ai%n%s",
                        commit or tag,
                    ],
                    check=False,
                )
                .stdout
                .splitlines()
            )

            commit_short = (
                log_output[0]
                if len(log_output) > 0
                else commit[:8]
            )

            date = (
                log_output[1]
                if len(log_output) > 1
                else ""
            )

            message = (
                log_output[2]
                if len(log_output) > 2
                else ""
            )

            tagger_result = (
                self._run_git(
                    [
                        "for-each-ref",
                        f"refs/tags/{tag}",
                        (
                            "--format="
                            "%(taggername) "
                            "<%(taggeremail)>"
                        ),
                    ],
                    check=False,
                )
            )

            tagger = (
                tagger_result
                .stdout
                .strip()
            )

            results.append(
                TagInfo(
                    name=tag,
                    commit=commit_short,
                    date=date,
                    message=message,
                    tagger=tagger,
                )
            )

        return results


# ============================================================
# Document Validator
# ============================================================

class DocumentValidator:
    ARTICLE_PATTERN = re.compile(
        r"^\s*\*\*ماده\s+"
        r"([۰-۹0-9]+(?:[–—\-][۰-۹0-9]+)?)"
        r"\s*[ـ–—-]",
        re.MULTILINE,
    )

    PLACEHOLDER_MARKERS = (
        "placeholder",
        "در انتظار درج متن",
        "در انتظار محتوای نهایی",
        "به‌زودی",
    )

    def __init__(
        self,
        docs_path: Path,
        logger: Optional[VerboseLogger] = None,
        config: Optional[ProjectConfig] = None,
    ):
        self.docs_path = (
            docs_path.resolve()
        )

        self.logger = (
            logger
            or VerboseLogger(False)
        )

        self.config = (
            config
            or ProjectConfig()
        )

    @staticmethod
    def normalize_digits(
        value: str,
    ) -> str:

        translation = (
            str.maketrans(
                "۰۱۲۳۴۵۶۷۸۹٠١٢٣٤٥٦٧٨٩",
                "01234567890123456789",
            )
        )

        return value.translate(
            translation
        )

    @classmethod
    def normalize_article_id(
        cls,
        value: str,
    ) -> str:

        value = (
            cls.normalize_digits(
                value
            )
        )

        value = (
            value
            .replace("–", "-")
            .replace("—", "-")
            .replace("−", "-")
        )

        return value.strip()

    def find_articles(
        self,
        content: str,
    ) -> List[str]:

        result: List[
            str
        ] = []

        for match in (
            self.ARTICLE_PATTERN
            .finditer(content)
        ):
            result.append(
                self.normalize_article_id(
                    match.group(1)
                )
            )

        return result

    @staticmethod
    def find_duplicates(
        items: Iterable[str],
    ) -> List[str]:

        seen: Set[str] = set()
        duplicates: Set[str] = set()

        for item in items:
            if item in seen:
                duplicates.add(item)

            seen.add(item)

        return sorted(
            duplicates
        )

    def extract_article_stats(
        self,
        content: str,
        expected_count: Optional[int] = None,
        expected_ids: Optional[Set[str]] = None,
    ) -> ArticleStats:

        ids = self.find_articles(
            content
        )

        total_detected = len(
            ids
        )

        duplicates = (
            self.find_duplicates(
                ids
            )
        )

        missing: List[
            str
        ] = []

        if expected_ids:
            found_set = set(
                ids
            )

            missing = sorted(
                expected_ids
                - found_set
            )

        out_of_order: List[
            str
        ] = []

        if ids:
            numeric_ids: List[
                int
            ] = []

            for article_id in ids:
                try:
                    base = (
                        article_id
                        .split("-")[0]
                    )

                    numeric_ids.append(
                        int(base)
                    )

                except ValueError:
                    numeric_ids.append(
                        -1
                    )

            for index in range(
                1,
                len(numeric_ids),
            ):
                if (
                    numeric_ids[index]
                    < numeric_ids[index - 1]
                    and numeric_ids[index] != -1
                ):
                    out_of_order.append(
                        ids[index]
                    )

        has_continuity = True

        if (
            ids
            and len(ids) > 1
        ):
            numeric_values: List[
                int
            ] = []

            for article_id in ids:
                try:
                    base = (
                        article_id
                        .split("-")[0]
                    )

                    numeric_values.append(
                        int(base)
                    )

                except ValueError:
                    numeric_values.append(
                        -1
                    )

            valid_numerics = [
                number
                for number
                in numeric_values
                if number >= 0
            ]

            if valid_numerics:
                min_value = min(
                    valid_numerics
                )

                max_value = max(
                    valid_numerics
                )

                expected_range = set(
                    range(
                        min_value,
                        max_value + 1,
                    )
                )

                actual_set = set(
                    valid_numerics
                )

                has_continuity = (
                    expected_range
                    == actual_set
                )

        return ArticleStats(
            total_expected=(
                expected_count
                or 0
            ),
            total_detected=(
                total_detected
            ),
            ids=ids,
            missing=missing,
            duplicates=duplicates,
            out_of_order=out_of_order,
            has_continuity=(
                has_continuity
            ),
        )

    def _get_doc_specs(
        self,
    ) -> Dict[
        str,
        Dict,
    ]:

        return {
            "0.4.md": {
                "min_size": 5000,
                "expected_start": (
                    "# قانون اساسی "
                    "«نظم داد» – "
                    "نسخه ۰.۴ نهایی"
                ),
                "articles": (
                    self.config
                    .expected_articles_v04
                ),
                "additional_articles": set(),
            },

            "0.5.md": {
                "min_size": 5000,
                "expected_start": (
                    "# قانون اساسی "
                    "«نظم داد» – "
                    "نسخه ۰.۵ نهایی"
                ),
                "articles": (
                    self.config
                    .expected_articles_v05
                ),
                "additional_articles": set(
                    self.config
                    .expected_v05_additional
                ),
            },

            "changelog.md": {
                "min_size": 1000,
                "expected_start": None,
                "articles": None,
                "additional_articles": set(),
            },

            "rules.md": {
                "min_size": 500,
                "expected_start": None,
                "articles": None,
                "additional_articles": set(),
            },

            "decisions.md": {
                "min_size": 500,
                "expected_start": None,
                "articles": None,
                "additional_articles": set(),
            },
        }

    def validate_all(
        self,
    ) -> Dict[
        str,
        DocumentInfo,
    ]:

        self.logger.log(
            "Validating documents in "
            f"{self.docs_path}"
        )

        result: Dict[
            str,
            DocumentInfo,
        ] = {}

        docs_spec = (
            self._get_doc_specs()
        )

        for (
            filename,
            spec,
        ) in docs_spec.items():

            file_path = (
                self.docs_path
                / filename
            )

            self.logger.log(
                f"Validating {file_path}"
            )

            result[
                filename
            ] = self.validate_single(
                file_path=file_path,
                spec=spec,
                filename=filename,
            )

        return result

    def validate_single(
        self,
        file_path: Path,
        spec: Dict,
        filename: str,
    ) -> DocumentInfo:

        if not file_path.exists():
            return DocumentInfo(
                path=str(file_path),
                status=(
                    DocumentStatus.MISSING
                ),
                size_bytes=0,
                description=(
                    f"فایل {filename} "
                    "وجود ندارد."
                ),
            )

        try:
            content = (
                file_path
                .read_text(
                    encoding="utf-8"
                )
            )

        except UnicodeDecodeError as exc:
            return DocumentInfo(
                path=str(file_path),
                status=(
                    DocumentStatus.INVALID
                ),
                size_bytes=(
                    file_path
                    .stat()
                    .st_size
                ),
                description=(
                    f"UTF-8 نامعتبر: "
                    f"{exc}"
                ),
            )

        except OSError as exc:
            return DocumentInfo(
                path=str(file_path),
                status=(
                    DocumentStatus.INVALID
                ),
                size_bytes=0,
                description=(
                    "خطا در خواندن فایل: "
                    f"{exc}"
                ),
            )

        size_bytes = (
            file_path
            .stat()
            .st_size
        )

        lines = len(
            content.splitlines()
        )

        characters = len(
            content
        )

        lowered = (
            content.lower()
        )

        is_placeholder = (
            any(
                marker.lower()
                in lowered
                for marker
                in self.PLACEHOLDER_MARKERS
            )
            or size_bytes
            < spec.get(
                "min_size",
                0,
            )
        )

        expected_start = (
            spec.get(
                "expected_start"
            )
        )

        start_ok = True

        if expected_start:
            start_ok = (
                content
                .lstrip(
                    "\ufeff \t\r\n"
                )
                .startswith(
                    expected_start
                )
            )

        expected_articles = (
            spec.get(
                "articles"
            )
        )

        detected_ids: List[
            str
        ] = []

        detected_count: Optional[
            int
        ] = None

        article_count_ok = True

        duplicates: List[
            str
        ] = []

        if expected_articles is not None:
            detected_ids = (
                self.find_articles(
                    content
                )
            )

            detected_count = len(
                detected_ids
            )

            article_count_ok = (
                detected_count
                == expected_articles
            )

            duplicates = (
                self.find_duplicates(
                    detected_ids
                )
            )

        expected_additional = set(
            spec.get(
                "additional_articles",
                set(),
            )
        )

        missing_additional: List[
            str
        ] = []

        if expected_additional:
            found_set = set(
                detected_ids
            )

            missing_additional = sorted(
                expected_additional
                - found_set
            )

        problems: List[
            str
        ] = []

        if not start_ok:
            problems.append(
                "شروع متن با ساختار "
                "مورد انتظار مطابقت ندارد"
            )

        if not article_count_ok:
            problems.append(
                "مواد تشخیص‌داده‌شده: "
                f"{detected_count}/"
                f"{expected_articles}"
            )

        if missing_additional:
            problems.append(
                "مواد الحاقی مفقود: "
                + ", ".join(
                    missing_additional
                )
            )

        if duplicates:
            problems.append(
                "شناسه مواد تکراری: "
                + ", ".join(
                    duplicates
                )
            )

        if is_placeholder:
            status = (
                DocumentStatus.PLACEHOLDER
            )

        elif problems:
            status = (
                DocumentStatus.INVALID
            )

        else:
            status = (
                DocumentStatus.COMPLETE
            )

        if (
            status
            == DocumentStatus.COMPLETE
        ):
            description = (
                "فایل معتبر است."
            )

        else:
            description = (
                f"size={size_bytes} bytes"
                f" | lines={lines}"
            )

            if problems:
                description += (
                    " | "
                    + " | ".join(
                        problems
                    )
                )

        return DocumentInfo(
            path=str(file_path),
            status=status,
            size_bytes=size_bytes,
            lines=lines,
            characters=characters,
            articles_count=(
                expected_articles
            ),
            detected_articles=(
                detected_count
            ),
            detected_article_ids=(
                detected_ids
            ),
            missing_articles=(
                missing_additional
            ),
            description=(
                description
            ),
            is_placeholder=(
                is_placeholder
            ),
        )


# ============================================================
# Link Checker
# ============================================================

class LinkChecker:
    MARKDOWN_LINK_PATTERN = re.compile(
        r"\[[^\]]+\]\(([^)]+)\)"
    )

    ARTICLE_REFERENCE_PATTERN = re.compile(
        r"ماده(?:ٔ|\s)+\s*"
        r"([۰-۹0-9]+"
        r"(?:[–—\-][۰-۹0-9]+)?)"
    )

    def __init__(
        self,
        repo_path: Path,
        docs_path: Path,
        validator: DocumentValidator,
        logger: Optional[VerboseLogger] = None,
        progress_enabled: bool = True,
    ):
        self.repo_path = (
            repo_path.resolve()
        )

        self.docs_path = (
            docs_path.resolve()
        )

        self.validator = validator

        self.logger = (
            logger
            or VerboseLogger(False)
        )

        self.progress_enabled = (
            progress_enabled
        )

    @staticmethod
    def _is_external(
        target: str,
    ) -> bool:

        lower = (
            target
            .lower()
            .strip()
        )

        return lower.startswith(
            (
                "http://",
                "https://",
                "mailto:",
                "tel:",
                "#",
            )
        )

    @staticmethod
    def _strip_anchor(
        target: str,
    ) -> str:

        return target.split(
            "#",
            1,
        )[0]

    def check_markdown_links(
        self,
        file_path: Path,
    ) -> List[LinkIssue]:

        issues: List[
            LinkIssue
        ] = []

        try:
            lines = (
                file_path
                .read_text(
                    encoding="utf-8"
                )
                .splitlines()
            )

        except (
            UnicodeDecodeError,
            OSError,
        ):
            return issues

        for (
            line_number,
            line,
        ) in enumerate(
            lines,
            start=1,
        ):

            for match in (
                self
                .MARKDOWN_LINK_PATTERN
                .finditer(line)
            ):
                target = (
                    match
                    .group(1)
                    .strip()
                )

                if self._is_external(
                    target
                ):
                    continue

                clean_target = (
                    self._strip_anchor(
                        target
                    )
                )

                if not clean_target:
                    continue

                resolved = (
                    file_path.parent
                    / clean_target
                ).resolve()

                if not resolved.exists():
                    issues.append(
                        LinkIssue(
                            source=(
                                safe_display_path(
                                    file_path,
                                    self.repo_path,
                                )
                            ),
                            target=target,
                            line=line_number,
                            status=(
                                LinkStatus.BROKEN
                            ),
                            description=(
                                "فایل مقصد لینک "
                                "وجود ندارد"
                            ),
                        )
                    )

        return issues

    def check_article_references(
        self,
        file_path: Path,
    ) -> List[LinkIssue]:

        issues: List[
            LinkIssue
        ] = []

        try:
            content = (
                file_path
                .read_text(
                    encoding="utf-8"
                )
            )

        except (
            UnicodeDecodeError,
            OSError,
        ):
            return issues

        article_ids = set(
            self.validator
            .find_articles(
                content
            )
        )

        if not article_ids:
            return issues

        lines = (
            content.splitlines()
        )

        for (
            line_number,
            line,
        ) in enumerate(
            lines,
            start=1,
        ):

            for match in (
                self
                .ARTICLE_REFERENCE_PATTERN
                .finditer(line)
            ):

                article_id = (
                    self.validator
                    .normalize_article_id(
                        match.group(1)
                    )
                )

                if article_id not in article_ids:
                    issues.append(
                        LinkIssue(
                            source=(
                                safe_display_path(
                                    file_path,
                                    self.repo_path,
                                )
                            ),
                            target=(
                                f"ماده "
                                f"{article_id}"
                            ),
                            line=line_number,
                            status=(
                                LinkStatus.BROKEN
                            ),
                            description=(
                                "ارجاع به ماده‌ای که "
                                "در همین نسخه "
                                "شناسایی نشد"
                            ),
                        )
                    )

        return issues

    def run(
        self,
    ) -> LinkReport:

        self.logger.log(
            "Checking local links "
            "and references"
        )

        report = (
            LinkReport()
        )

        if not self.docs_path.exists():
            return report

        markdown_files = sorted(
            self.docs_path
            .rglob("*.md")
        )

        total = (
            len(markdown_files)
            + 2
        )

        with ProgressReporter(
            enabled=(
                self.progress_enabled
            ),
            desc="Checking links",
            total=total,
        ) as progress:

            for file_path in (
                markdown_files
            ):
                markdown_issues = (
                    self
                    .check_markdown_links(
                        file_path
                    )
                )

                report.checked += 1

                if markdown_issues:
                    report.broken += len(
                        markdown_issues
                    )

                    report.issues.extend(
                        markdown_issues
                    )

                else:
                    report.valid += 1

                progress.update()

            for filename in (
                "0.4.md",
                "0.5.md",
            ):
                file_path = (
                    self.docs_path
                    / filename
                )

                if not file_path.exists():
                    progress.update()
                    continue

                article_issues = (
                    self
                    .check_article_references(
                        file_path
                    )
                )

                report.checked += 1

                if article_issues:
                    report.broken += len(
                        article_issues
                    )

                    report.issues.extend(
                        article_issues
                    )

                else:
                    report.valid += 1

                progress.update()

        return report


# ============================================================
# Project Status Builder
# ============================================================

class ProjectStatusBuilder:
    def __init__(
        self,
        repo_path: Path,
        logger: Optional[VerboseLogger] = None,
        config: Optional[ProjectConfig] = None,
        progress_enabled: bool = True,
    ):
        self.logger = (
            logger
            or VerboseLogger(False)
        )

        self.config = (
            config
            or ProjectConfig()
        )

        self.progress_enabled = (
            progress_enabled
        )

        initial_path = (
            repo_path.resolve()
        )

        collector = (
            GitInfoCollector(
                initial_path,
                self.logger,
            )
        )

        collector.check_repository()

        self.repo_path = (
            collector.get_repo_root()
        )

        self.git_collector = (
            GitInfoCollector(
                self.repo_path,
                self.logger,
            )
        )

        docs_path = (
            self.repo_path
            / "docs"
        )

        if self.config.docs_path:
            config_docs = Path(
                self.config.docs_path
            )

            if config_docs.is_absolute():
                docs_path = (
                    config_docs
                )

            else:
                docs_path = (
                    self.repo_path
                    / config_docs
                )

        self.doc_validator = (
            DocumentValidator(
                docs_path,
                self.logger,
                self.config,
            )
        )

    def build(
        self,
    ) -> ProjectStatus:

        git = (
            self.git_collector
            .get_info()
        )

        documents = (
            self.doc_validator
            .validate_all()
        )

        document_stats = (
            self._extract_stats(
                documents
            )
        )

        return ProjectStatus(
            git=git,
            documents=documents,
            document_stats=(
                document_stats
            ),
            total_articles_v04=(
                self.config
                .expected_articles_v04
            ),
            total_articles_v05=(
                self.config
                .expected_articles_v05
            ),
            expected_v05_additional=set(
                self.config
                .expected_v05_additional
            ),
            total_changes=(
                self.config
                .total_real_changes
            ),
            noop_changes=(
                self.config
                .total_noop_changes
            ),
        )

    def _extract_stats(
        self,
        documents: Dict[
            str,
            DocumentInfo,
        ],
    ) -> Dict[
        str,
        DocumentStats,
    ]:

        stats: Dict[
            str,
            DocumentStats,
        ] = {}

        for (
            name,
            doc,
        ) in documents.items():

            if (
                doc.status
                == DocumentStatus.MISSING
            ):
                continue

            try:
                content = (
                    Path(doc.path)
                    .read_text(
                        encoding="utf-8"
                    )
                )

            except (
                OSError,
                UnicodeDecodeError,
            ):
                continue

            expected_count = (
                doc.articles_count
            )

            expected_ids = None

            if name == "0.5.md":
                expected_ids = set(
                    self.config
                    .expected_v05_additional
                )

            article_stats = (
                self.doc_validator
                .extract_article_stats(
                    content,
                    expected_count,
                    expected_ids,
                )
            )

            stats[name] = (
                DocumentStats(
                    name=name,
                    status=doc.status,
                    size_bytes=(
                        doc.size_bytes
                    ),
                    lines=doc.lines,
                    characters=(
                        doc.characters
                    ),
                    articles=(
                        article_stats
                    ),
                    has_placeholder=(
                        doc.is_placeholder
                    ),
                    description=(
                        doc.description
                    ),
                )
            )

        return stats


# ============================================================
# Health Calculator
# ============================================================

class HealthCalculator:
    def __init__(
        self,
        status: ProjectStatus,
        link_report: Optional[LinkReport] = None,
        config: Optional[ProjectConfig] = None,
    ):
        self.status = status
        self.link_report = (
            link_report
        )

        self.config = (
            config
            or ProjectConfig()
        )

    def calculate(
        self,
    ) -> HealthReport:

        components: List[
            HealthComponent
        ] = []

        if self.status.git.is_clean:
            git_clean_score = 20
            git_clean_status = "OK"
            git_clean_detail = (
                "Working tree clean"
            )

        else:
            git_clean_score = 0
            git_clean_status = "WARN"
            git_clean_detail = (
                "Working tree "
                "دارای تغییرات است"
            )

        components.append(
            HealthComponent(
                name="Git cleanliness",
                score=git_clean_score,
                max_score=20,
                status=git_clean_status,
                detail=git_clean_detail,
            )
        )

        if not self.status.git.upstream:
            upstream_score = 5
            upstream_status = "WARN"
            upstream_detail = (
                "Upstream تعریف نشده است"
            )

        elif (
            self.status.git.ahead == 0
            and self.status.git.behind == 0
        ):
            upstream_score = 15
            upstream_status = "OK"
            upstream_detail = (
                "Local و upstream "
                "همگام هستند"
            )

        elif self.status.git.behind > 0:
            upstream_score = 5
            upstream_status = "WARN"
            upstream_detail = (
                f"{self.status.git.behind} "
                "commit behind"
            )

        else:
            upstream_score = 10
            upstream_status = "INFO"
            upstream_detail = (
                f"{self.status.git.ahead} "
                "commit ahead"
            )

        components.append(
            HealthComponent(
                name=(
                    "Upstream "
                    "synchronization"
                ),
                score=upstream_score,
                max_score=15,
                status=upstream_status,
                detail=upstream_detail,
            )
        )

        docs = list(
            self.status
            .documents
            .values()
        )

        if docs:
            per_doc = (
                35
                / len(docs)
            )

            doc_score_float = 0.0

            for doc in docs:
                if (
                    doc.status
                    == DocumentStatus.COMPLETE
                ):
                    doc_score_float += (
                        per_doc
                    )

                elif (
                    doc.status
                    == DocumentStatus.PUBLISHED
                ):
                    doc_score_float += (
                        per_doc
                    )

                elif (
                    doc.status
                    == DocumentStatus.PLACEHOLDER
                ):
                    doc_score_float += (
                        per_doc
                        * 0.25
                    )

            document_score = round(
                doc_score_float
            )

        else:
            document_score = 0

        invalid_names = [
            name
            for (
                name,
                doc,
            )
            in self.status
            .documents
            .items()
            if doc.status
            not in (
                DocumentStatus.COMPLETE,
                DocumentStatus.PUBLISHED,
            )
        ]

        components.append(
            HealthComponent(
                name=(
                    "Document validation"
                ),
                score=document_score,
                max_score=35,
                status=(
                    "OK"
                    if not invalid_names
                    else "WARN"
                ),
                detail=(
                    "تمام اسناد معتبرند"
                    if not invalid_names
                    else (
                        "نیازمند توجه: "
                        + ", ".join(
                            invalid_names
                        )
                    )
                ),
            )
        )

        doc05 = (
            self.status
            .documents
            .get("0.5.md")
        )

        if (
            doc05
            and not doc05.missing_articles
            and doc05.detected_articles
            == self.config
            .expected_articles_v05
        ):
            additional_score = 10
            additional_status = "OK"
            additional_detail = (
                "۱۲ ماده الحاقی "
                "مورد انتظار موجودند"
            )

        else:
            additional_score = 0
            additional_status = "WARN"
            additional_detail = (
                "مواد الحاقی یا "
                "شمارش ۰.۵ "
                "نیازمند بررسی است"
            )

        components.append(
            HealthComponent(
                name=(
                    "v0.5 structural "
                    "additions"
                ),
                score=(
                    additional_score
                ),
                max_score=10,
                status=(
                    additional_status
                ),
                detail=(
                    additional_detail
                ),
            )
        )

        doc05_stats = (
            self.status
            .document_stats
            .get("0.5.md")
        )

        if doc05_stats:
            if (
                doc05_stats
                .articles
                .has_continuity
            ):
                continuity_score = 10
                continuity_status = "OK"
                continuity_detail = (
                    "شماره‌های پایه "
                    "مواد ۰.۵ پیوسته‌اند"
                )

            else:
                continuity_score = 5
                continuity_status = "WARN"
                continuity_detail = (
                    "شماره‌های پایه "
                    "مواد ۰.۵ "
                    "دارای شکاف هستند"
                )

        else:
            continuity_score = 0
            continuity_status = "WARN"
            continuity_detail = (
                "آمار مواد ۰.۵ "
                "در دسترس نیست"
            )

        components.append(
            HealthComponent(
                name=(
                    "Article continuity"
                ),
                score=(
                    continuity_score
                ),
                max_score=10,
                status=(
                    continuity_status
                ),
                detail=(
                    continuity_detail
                ),
            )
        )

        if self.link_report is None:
            link_score = 10
            link_status = "INFO"
            link_detail = (
                "Link check اجرا نشده"
            )

        elif (
            self.link_report.broken
            == 0
        ):
            link_score = 10
            link_status = "OK"
            link_detail = (
                "لینک شکسته‌ای "
                "تشخیص داده نشد"
            )

        else:
            link_score = 0
            link_status = "WARN"
            link_detail = (
                f"{self.link_report.broken} "
                "مورد مشکل در "
                "لینک/ارجاع"
            )

        components.append(
            HealthComponent(
                name=(
                    "Links and references"
                ),
                score=link_score,
                max_score=10,
                status=link_status,
                detail=link_detail,
            )
        )

        total = sum(
            item.score
            for item
            in components
        )

        maximum = sum(
            item.max_score
            for item
            in components
        )

        percent = (
            (total / maximum) * 100
            if maximum
            else 0.0
        )

        if percent >= 95:
            grade = "A+"

        elif percent >= 90:
            grade = "A"

        elif percent >= 80:
            grade = "B"

        elif percent >= 70:
            grade = "C"

        elif percent >= 60:
            grade = "D"

        else:
            grade = "F"

        return HealthReport(
            score=total,
            max_score=maximum,
            percent=round(
                percent,
                1,
            ),
            grade=grade,
            components=components,
        )


# ============================================================
# Console Renderer
# ============================================================

class ConsoleRenderer:
    def __init__(
        self,
        console: Optional[Console] = None,
    ):
        self.console = (
            console
            or Console()
        )

    @staticmethod
    def document_icon(
        status: DocumentStatus,
    ) -> str:

        if status in (
            DocumentStatus.COMPLETE,
            DocumentStatus.PUBLISHED,
        ):
            return "✅"

        if (
            status
            == DocumentStatus.PLACEHOLDER
        ):
            return "⏳"

        if (
            status
            == DocumentStatus.INVALID
        ):
            return "❌"

        return "⚠️"

    def render(
        self,
        status: ProjectStatus,
    ) -> None:

        width = 78

        print(
            "=" * width
        )

        print(
            (
                " پروژه نظم داد — "
                f"Status Tool v{TOOL_VERSION} "
            ).center(
                width
            )
        )

        print(
            "=" * width
        )

        print(
            "\n📦 وضعیت Git"
        )

        print(
            f"  Branch: "
            f"{status.git.branch}"
        )

        clean_text = (
            self.console.success(
                "✅ clean"
            )
            if status.git.is_clean
            else self.console.error(
                "❌ dirty"
            )
        )

        print(
            f"  Working tree: "
            f"{clean_text}"
        )

        print(
            "  Last commit: "
            f"{status.git.last_commit_hash}"
            " — "
            f"{status.git.last_commit_message}"
        )

        if (
            status.git
            .last_commit_date
        ):
            print(
                "  Commit date: "
                f"{status.git.last_commit_date}"
            )

        if status.git.upstream:
            print(
                f"  Upstream: "
                f"{status.git.upstream}"
            )

            print(
                "  Ahead/Behind: "
                f"{status.git.ahead}/"
                f"{status.git.behind}"
            )

        else:
            print(
                "  Upstream: ندارد"
            )

        if status.git.remote:
            print(
                f"  Remote: "
                f"{status.git.remote}"
            )

        if status.git.tags:
            print(
                f"  Tags: "
                f"{', '.join(status.git.tags)}"
            )

        print(
            "\n📄 وضعیت اسناد"
        )

        for (
            name,
            doc,
        ) in (
            status.documents.items()
        ):

            icon = (
                self.document_icon(
                    doc.status
                )
            )

            article_text = ""

            if (
                doc.articles_count
                is not None
            ):
                article_text = (
                    " | مواد: "
                    f"{doc.detected_articles}/"
                    f"{doc.articles_count}"
                )

            print(
                f"  {icon} "
                f"{name}: "
                f"{doc.status.value}"
                f"{article_text}"
            )

            print(
                f"      "
                f"{doc.size_bytes} bytes | "
                f"{doc.lines} lines | "
                f"{doc.characters} chars"
            )

            if doc.description:
                print(
                    f"      "
                    f"{doc.description}"
                )

        print(
            "\n📊 آمار مواد"
        )

        for (
            name,
            stats,
        ) in (
            status
            .document_stats
            .items()
        ):
            print(
                f"  {name}:"
            )

            print(
                "      مواد شناسایی‌شده: "
                f"{stats.articles.total_detected}"
            )

            if (
                stats
                .articles
                .duplicates
            ):
                print(
                    "      تکراری: "
                    + ", ".join(
                        stats
                        .articles
                        .duplicates
                    )
                )

            if (
                stats
                .articles
                .out_of_order
            ):
                print(
                    "      خارج از ترتیب: "
                    + ", ".join(
                        stats
                        .articles
                        .out_of_order
                    )
                )

            print(
                "      پیوستگی "
                "شماره‌های پایه: "
                + (
                    "✅"
                    if stats
                    .articles
                    .has_continuity
                    else "❌"
                )
            )

        print(
            "\n📈 خلاصه"
        )

        print(
            "  تغییرات واقعی ۰.۴ → ۰.۵: "
            f"{status.total_changes}"
        )

        print(
            f"  No-op: "
            f"{status.noop_changes}"
        )

        print(
            f"  مواد ۰.۴: "
            f"{status.total_articles_v04}"
        )

        print(
            f"  مواد ۰.۵: "
            f"{status.total_articles_v05}"
        )

        print(
            "  افزایش مواد مستقل: "
            f"{status.total_articles_v05 - status.total_articles_v04}"
        )

        print(
            "\n"
            + "=" * width
        )

    def render_health(
        self,
        report: HealthReport,
    ) -> None:

        print(
            "🏥 سلامت پروژه"
        )

        print()

        print(
            "  امتیاز: "
            f"{report.score}/"
            f"{report.max_score}"
            f" ({report.percent}%)"
        )

        print(
            f"  Grade: "
            f"{report.grade}"
        )

        print()

        for item in (
            report.components
        ):
            if (
                item.status
                == "OK"
            ):
                icon = "✅"

            elif (
                item.status
                == "WARN"
            ):
                icon = "⚠️"

            else:
                icon = "ℹ️"

            print(
                f"  {icon} "
                f"{item.name}: "
                f"{item.score}/"
                f"{item.max_score}"
            )

            print(
                f"      "
                f"{item.detail}"
            )

    def render_links(
        self,
        report: LinkReport,
    ) -> None:

        print(
            "🔗 بررسی لینک‌ها "
            "و ارجاعات"
        )

        print()

        print(
            f"  Checked: "
            f"{report.checked}"
        )

        print(
            f"  Valid:   "
            f"{report.valid}"
        )

        print(
            f"  Broken:  "
            f"{report.broken}"
        )

        print(
            f"  Skipped: "
            f"{report.skipped}"
        )

        if not report.issues:
            print()
            print(
                "✅ مشکل قابل‌تشخیصی "
                "یافت نشد."
            )
            return

        print()
        print(
            "موارد نیازمند بررسی:"
        )

        for issue in (
            report.issues
        ):
            print(
                f"  ❌ "
                f"{issue.source}:"
                f"{issue.line}"
            )

            print(
                f"     Target: "
                f"{issue.target}"
            )

            print(
                f"     "
                f"{issue.description}"
            )


# ============================================================
# Markdown Renderer
# ============================================================

class MarkdownRenderer:
    def render(
        self,
        status: ProjectStatus,
        health: Optional[HealthReport] = None,
    ) -> str:

        lines: List[str] = [
            "# وضعیت پروژه نظم داد",
            "",
            f"**ابزار:** v{TOOL_VERSION}",
            f"**تاریخ تولید:** {status.timestamp}",
            "",
            "## 📦 Git",
            "",
            f"- **Branch:** `{status.git.branch}`",
            (
                "- **Working tree:** "
                + (
                    "✅ clean"
                    if status.git.is_clean
                    else "❌ dirty"
                )
            ),
            (
                "- **Last commit:** "
                f"`{status.git.last_commit_hash}`"
                " — "
                f"{status.git.last_commit_message}"
            ),
        ]

        if status.git.upstream:
            lines.append(
                "- **Upstream:** "
                f"`{status.git.upstream}`"
            )

            lines.append(
                "- **Ahead / Behind:** "
                f"{status.git.ahead} / "
                f"{status.git.behind}"
            )

        if status.git.tags:
            lines.append(
                "- **Tags:** "
                f"{', '.join(status.git.tags)}"
            )

        if status.git.remote:
            lines.append(
                "- **Remote:** "
                f"`{status.git.remote}`"
            )

        lines.extend(
            [
                "",
                "## 📄 اسناد",
                "",
                (
                    "| فایل | وضعیت | "
                    "اندازه | خطوط | مواد |"
                ),
                "|---|---|---:|---:|---:|",
            ]
        )

        for (
            name,
            doc,
        ) in (
            status.documents.items()
        ):

            if doc.status in (
                DocumentStatus.COMPLETE,
                DocumentStatus.PUBLISHED,
            ):
                icon = "✅"

            elif (
                doc.status
                == DocumentStatus.PLACEHOLDER
            ):
                icon = "⏳"

            elif (
                doc.status
                == DocumentStatus.INVALID
            ):
                icon = "❌"

            else:
                icon = "⚠️"

            article_value = (
                str(
                    doc.detected_articles
                )
                if (
                    doc.detected_articles
                    is not None
                )
                else "-"
            )

            lines.append(
                f"| {icon} `{name}` "
                f"| {doc.status.value} "
                f"| {doc.size_bytes} "
                f"| {doc.lines} "
                f"| {article_value} |"
            )

        lines.extend(
            [
                "",
                "## 📊 آمار مواد",
                "",
                (
                    "| فایل | "
                    "مواد شناسایی‌شده | "
                    "تکراری | "
                    "خارج از ترتیب | "
                    "پیوستگی پایه |"
                ),
                "|---|---:|---|---|---|",
            ]
        )

        for (
            name,
            stats,
        ) in (
            status
            .document_stats
            .items()
        ):

            duplicates_text = (
                ", ".join(
                    stats
                    .articles
                    .duplicates
                )
                if stats
                .articles
                .duplicates
                else "-"
            )

            out_of_order_text = (
                ", ".join(
                    stats
                    .articles
                    .out_of_order
                )
                if stats
                .articles
                .out_of_order
                else "-"
            )

            continuity_text = (
                "✅"
                if stats
                .articles
                .has_continuity
                else "❌"
            )

            lines.append(
                f"| `{name}` "
                f"| {stats.articles.total_detected} "
                f"| {duplicates_text} "
                f"| {out_of_order_text} "
                f"| {continuity_text} |"
            )

        lines.extend(
            [
                "",
                "## 📈 خلاصه",
                "",
                (
                    "- **تغییرات واقعی:** "
                    f"{status.total_changes}"
                ),
                (
                    f"- **No-op:** "
                    f"{status.noop_changes}"
                ),
                (
                    f"- **مواد ۰.۴:** "
                    f"{status.total_articles_v04}"
                ),
                (
                    f"- **مواد ۰.۵:** "
                    f"{status.total_articles_v05}"
                ),
                (
                    "- **مواد مستقل افزوده:** "
                    f"{status.total_articles_v05 - status.total_articles_v04}"
                ),
            ]
        )

        if health is not None:
            lines.extend(
                [
                    "",
                    "## 🏥 سلامت پروژه",
                    "",
                    (
                        f"**{health.score}/"
                        f"{health.max_score} "
                        f"({health.percent}%) "
                        f"— Grade "
                        f"{health.grade}**"
                    ),
                    "",
                    "| بخش | امتیاز | وضعیت |",
                    "|---|---:|---|",
                ]
            )

            for item in (
                health.components
            ):
                lines.append(
                    f"| {item.name} "
                    f"| {item.score}/"
                    f"{item.max_score} "
                    f"| {item.detail} |"
                )

        lines.extend(
            [
                "",
                "---",
                (
                    "_Generated by "
                    "Nazm Dad Project Status "
                    f"v{TOOL_VERSION}_"
                ),
            ]
        )

        return (
            "\n".join(lines)
            + "\n"
        )


# ============================================================
# JSON Renderer
# ============================================================

class JsonRenderer:
    def render(
        self,
        status: ProjectStatus,
        health: Optional[HealthReport] = None,
    ) -> str:

        data = {
            "tool": {
                "name": (
                    "nazm_dad_project_status"
                ),
                "version": (
                    TOOL_VERSION
                ),
            },

            "timestamp": (
                status.timestamp
            ),

            "git": asdict(
                status.git
            ),

            "documents": {
                name: {
                    **asdict(doc),
                    "status": (
                        doc.status.value
                    ),
                }
                for (
                    name,
                    doc,
                )
                in (
                    status
                    .documents
                    .items()
                )
            },

            "document_stats": {
                name: {
                    "status": (
                        stats.status.value
                    ),
                    "size_bytes": (
                        stats.size_bytes
                    ),
                    "lines": (
                        stats.lines
                    ),
                    "characters": (
                        stats.characters
                    ),
                    "articles": {
                        "total_expected": (
                            stats
                            .articles
                            .total_expected
                        ),
                        "total_detected": (
                            stats
                            .articles
                            .total_detected
                        ),
                        "ids": (
                            stats
                            .articles
                            .ids
                        ),
                        "missing": (
                            stats
                            .articles
                            .missing
                        ),
                        "duplicates": (
                            stats
                            .articles
                            .duplicates
                        ),
                        "out_of_order": (
                            stats
                            .articles
                            .out_of_order
                        ),
                        "has_continuity": (
                            stats
                            .articles
                            .has_continuity
                        ),
                    },
                    "has_placeholder": (
                        stats
                        .has_placeholder
                    ),
                    "description": (
                        stats.description
                    ),
                }

                for (
                    name,
                    stats,
                )
                in (
                    status
                    .document_stats
                    .items()
                )
            },

            "summary": {
                "total_changes": (
                    status.total_changes
                ),
                "noop_changes": (
                    status.noop_changes
                ),
                "articles_v04": (
                    status
                    .total_articles_v04
                ),
                "articles_v05": (
                    status
                    .total_articles_v05
                ),
                "added_articles": (
                    status
                    .total_articles_v05
                    - status
                    .total_articles_v04
                ),
                "expected_v05_additional": sorted(
                    status
                    .expected_v05_additional
                ),
            },
        }

        if health is not None:
            data["health"] = {
                "score": health.score,
                "max_score": (
                    health.max_score
                ),
                "percent": (
                    health.percent
                ),
                "grade": (
                    health.grade
                ),
                "components": [
                    asdict(component)
                    for component
                    in health.components
                ],
            }

        return (
            json.dumps(
                data,
                ensure_ascii=False,
                indent=2,
            )
            + "\n"
        )


# ============================================================
# HTML Renderer
# ============================================================

class HtmlRenderer:
    def render(
        self,
        status: ProjectStatus,
        health: Optional[HealthReport] = None,
    ) -> str:

        total_articles = sum(
            stats
            .articles
            .total_detected

            for stats
            in (
                status
                .document_stats
                .values()
            )
        )

        invalid_docs = [
            name

            for (
                name,
                doc,
            )

            in (
                status
                .documents
                .items()
            )

            if doc.status
            not in (
                DocumentStatus.COMPLETE,
                DocumentStatus.PUBLISHED,
            )
        ]

        health_percent = (
            health.percent
            if health
            else 0.0
        )

        health_score = (
            health.score
            if health
            else 0
        )

        health_max_score = (
            health.max_score
            if health
            else 0
        )

        health_grade = (
            html_escape(
                health.grade
            )
            if health
            else "N/A"
        )

        if health_percent >= 80:
            health_color = (
                "#27ae60"
            )

        elif health_percent >= 60:
            health_color = (
                "#f39c12"
            )

        else:
            health_color = (
                "#e74c3c"
            )

        branch = html_escape(
            status.git.branch
        )

        commit_hash = html_escape(
            status.git
            .last_commit_hash
        )

        commit_message = html_escape(
            status.git
            .last_commit_message
        )

        upstream = (
            html_escape(
                status.git.upstream
            )
            if status.git.upstream
            else ""
        )

        tags = (
            html_escape(
                ", ".join(
                    status.git.tags
                )
            )
            if status.git.tags
            else "بدون Tag"
        )

        clean_status = (
            "✅ clean"
            if status.git.is_clean
            else "❌ dirty"
        )

        valid_docs_count = sum(
            1
            for doc
            in (
                status
                .documents
                .values()
            )
            if doc.status
            in (
                DocumentStatus.COMPLETE,
                DocumentStatus.PUBLISHED,
            )
        )

        health_rows = (
            "".join(
                self._health_row(
                    item
                )
                for item
                in health.components
            )
            if health
            else (
                "<tr>"
                "<td colspan='3'>"
                "اطلاعات سلامت "
                "در دسترس نیست"
                "</td>"
                "</tr>"
            )
        )

        document_rows = "".join(
            self._doc_row(
                name,
                doc,
            )

            for (
                name,
                doc,
            )

            in (
                status
                .documents
                .items()
            )
        )

        stats_rows = "".join(
            self._stats_row(
                stats
            )

            for stats
            in (
                status
                .document_stats
                .values()
            )
        )

        return f"""<!DOCTYPE html>
<html lang="fa" dir="rtl">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>نظم داد — وضعیت پروژه</title>

<style>
body {{
    font-family: "Segoe UI", Tahoma, Geneva, Verdana, sans-serif;
    background-color: #F5F0E6;
    color: #0A1E3F;
    margin: 0;
    padding: 20px;
    direction: rtl;
}}

.container {{
    max-width: 1200px;
    margin: 0 auto;
    background: white;
    border-radius: 12px;
    padding: 30px;
    box-shadow: 0 4px 20px rgba(0,0,0,0.08);
}}

.header {{
    display: flex;
    justify-content: space-between;
    align-items: center;
    border-bottom: 2px solid #C9A86C;
    padding-bottom: 15px;
    margin-bottom: 25px;
}}

.header h1 {{
    margin: 0;
    font-size: 28px;
}}

.version {{
    color: #71889A;
}}

.health-score {{
    background: {health_color};
    color: white;
    padding: 15px 25px;
    border-radius: 8px;
}}

.grid {{
    display: grid;
    grid-template-columns: repeat(auto-fit, minmax(280px, 1fr));
    gap: 20px;
    margin-top: 20px;
}}

.card {{
    background: #f8f6f0;
    border-radius: 8px;
    padding: 16px 20px;
    border-right: 4px solid #C9A86C;
}}

.card h3 {{
    margin-top: 0;
}}

.value {{
    font-size: 24px;
    font-weight: bold;
}}

.sub {{
    color: #71889A;
    margin-top: 5px;
}}

.status-badge {{
    display: inline-block;
    padding: 4px 12px;
    border-radius: 20px;
    font-size: 12px;
    font-weight: 600;
}}

.status-ok {{
    background: #d4edda;
    color: #155724;
}}

.status-warn {{
    background: #fff3cd;
    color: #856404;
}}

.status-error {{
    background: #f8d7da;
    color: #721c24;
}}

.status-info {{
    background: #d1ecf1;
    color: #0c5460;
}}

table {{
    width: 100%;
    border-collapse: collapse;
    margin: 15px 0;
}}

th,
td {{
    padding: 10px 12px;
    text-align: right;
    border-bottom: 1px solid #eee;
}}

th {{
    background: #f5f0e6;
}}

.progress-bar {{
    width: 100%;
    height: 8px;
    background: #eee;
    border-radius: 4px;
    overflow: hidden;
}}

.progress-fill {{
    height: 100%;
    background: {health_color};
    width: {health_percent}%;
}}

.footer {{
    margin-top: 25px;
    border-top: 1px solid #eee;
    padding-top: 15px;
    color: #71889A;
    text-align: center;
}}
</style>
</head>

<body>

<div class="container">

<div class="header">
    <h1>نظم داد</h1>
    <div>
        <span class="version">v{TOOL_VERSION}</span>
        <span style="margin-right:15px;">
            {html_escape(status.timestamp[:10])}
        </span>
    </div>
</div>

<div style="display:flex;gap:20px;align-items:center;flex-wrap:wrap;">

    <div class="health-score">
        <div>سلامت پروژه</div>
        <div style="font-size:36px;font-weight:bold;">
            {health_percent:.0f}%
        </div>
        <div>{health_grade}</div>
    </div>

    <div style="flex:1;min-width:220px;">
        <div>
            امتیاز: {health_score}/{health_max_score}
        </div>

        <div class="progress-bar">
            <div class="progress-fill"></div>
        </div>
    </div>

</div>

<div class="grid">

<div class="card">
<h3>🔄 Git</h3>
<div class="value">{branch}</div>
<div class="sub">
{commit_hash} — {commit_message}
</div>
<div class="sub">
{clean_status}
{" | " + upstream if upstream else ""}
</div>
</div>

<div class="card">
<h3>📄 اسناد</h3>
<div class="value">{len(status.documents)}</div>
<div class="sub">
✅ {valid_docs_count} معتبر
{" | ⚠️ " + str(len(invalid_docs)) + " مشکل" if invalid_docs else ""}
</div>
</div>

<div class="card">
<h3>📊 مواد</h3>
<div class="value">{total_articles}</div>
<div class="sub">
مواد شناسایی‌شده در اسناد
</div>
</div>

<div class="card">
<h3>🏷️ نسخه‌ها</h3>
<div class="value">{len(status.git.tags)}</div>
<div class="sub">
{"آخرین: " + tags if status.git.tags else "بدون Tag"}
</div>
</div>

</div>

<h2>📄 وضعیت اسناد</h2>

<table>
<thead>
<tr>
<th>فایل</th>
<th>وضعیت</th>
<th>اندازه</th>
<th>خطوط</th>
<th>مواد</th>
</tr>
</thead>

<tbody>
{document_rows}
</tbody>
</table>

<h2>📊 آمار مواد</h2>

<table>
<thead>
<tr>
<th>فایل</th>
<th>مواد شناسایی‌شده</th>
<th>تکراری</th>
<th>خارج از ترتیب</th>
<th>پیوستگی پایه</th>
</tr>
</thead>

<tbody>
{stats_rows}
</tbody>
</table>

<h2>🏥 اجزای سلامت</h2>

<table>
<thead>
<tr>
<th>بخش</th>
<th>امتیاز</th>
<th>وضعیت</th>
</tr>
</thead>

<tbody>
{health_rows}
</tbody>
</table>

<div class="footer">
نظم داد — Nazm Dad |
Generated by Nazm Dad Project Status v{TOOL_VERSION}
</div>

</div>

</body>
</html>
"""

    def _doc_row(
        self,
        name: str,
        doc: DocumentInfo,
    ) -> str:

        status_class = {
            DocumentStatus.COMPLETE:
                "status-ok",

            DocumentStatus.PUBLISHED:
                "status-ok",

            DocumentStatus.PLACEHOLDER:
                "status-warn",

            DocumentStatus.INVALID:
                "status-error",

            DocumentStatus.MISSING:
                "status-error",
        }.get(
            doc.status,
            "status-info",
        )

        status_label = {
            DocumentStatus.COMPLETE:
                "✅ کامل",

            DocumentStatus.PUBLISHED:
                "✅ منتشر شده",

            DocumentStatus.PLACEHOLDER:
                "⏳ placeholder",

            DocumentStatus.INVALID:
                "❌ نامعتبر",

            DocumentStatus.MISSING:
                "⚠️ وجود ندارد",
        }.get(
            doc.status,
            "❓",
        )

        articles = (
            f"{doc.detected_articles}/"
            f"{doc.articles_count}"
            if (
                doc.articles_count
                is not None
            )
            else "-"
        )

        return f"""
<tr>
<td>{html_escape(name)}</td>
<td>
<span class="status-badge {status_class}">
{status_label}
</span>
</td>
<td>{doc.size_bytes}</td>
<td>{doc.lines}</td>
<td>{html_escape(articles)}</td>
</tr>
"""

    def _stats_row(
        self,
        stats: DocumentStats,
    ) -> str:

        duplicates = (
            ", ".join(
                stats
                .articles
                .duplicates
            )
            if stats
            .articles
            .duplicates
            else "-"
        )

        out_of_order = (
            ", ".join(
                stats
                .articles
                .out_of_order
            )
            if stats
            .articles
            .out_of_order
            else "-"
        )

        continuity = (
            "✅"
            if stats
            .articles
            .has_continuity
            else "❌"
        )

        return f"""
<tr>
<td>{html_escape(stats.name)}</td>
<td>{stats.articles.total_detected}</td>
<td>{html_escape(duplicates)}</td>
<td>{html_escape(out_of_order)}</td>
<td>{continuity}</td>
</tr>
"""

    def _health_row(
        self,
        item: HealthComponent,
    ) -> str:

        status_class = {
            "OK":
                "status-ok",

            "WARN":
                "status-warn",

            "INFO":
                "status-info",
        }.get(
            item.status,
            "status-info",
        )

        return f"""
<tr>
<td>{html_escape(item.name)}</td>
<td>{item.score}/{item.max_score}</td>
<td>
<span class="status-badge {status_class}">
{html_escape(item.detail)}
</span>
</td>
</tr>
"""


# ============================================================
# Diff Renderer
# ============================================================

class DiffRenderer:
    def __init__(
        self,
        console: Optional[Console] = None,
    ):
        self.console = (
            console
            or Console()
        )

    def render(
        self,
        left: Path,
        right: Path,
    ) -> int:

        if not left.exists():
            print(
                f"❌ فایل وجود ندارد: "
                f"{left}",
                file=sys.stderr,
            )
            return int(
                ExitCode.RUNTIME_ERROR
            )

        if not right.exists():
            print(
                f"❌ فایل وجود ندارد: "
                f"{right}",
                file=sys.stderr,
            )
            return int(
                ExitCode.RUNTIME_ERROR
            )

        try:
            left_lines = (
                left
                .read_text(
                    encoding="utf-8"
                )
                .splitlines(
                    keepends=True
                )
            )

            right_lines = (
                right
                .read_text(
                    encoding="utf-8"
                )
                .splitlines(
                    keepends=True
                )
            )

        except UnicodeDecodeError as exc:
            print(
                f"❌ Encoding error: "
                f"{exc}",
                file=sys.stderr,
            )
            return int(
                ExitCode.RUNTIME_ERROR
            )

        diff = difflib.unified_diff(
            left_lines,
            right_lines,
            fromfile=str(left),
            tofile=str(right),
        )

        found = False

        for line in diff:
            found = True

            printable = (
                line.rstrip("\n")
            )

            if (
                line.startswith("+++")
                or line.startswith("---")
            ):
                print(
                    self.console.color(
                        printable,
                        "blue",
                    )
                )

            elif (
                line.startswith("@@")
            ):
                print(
                    self.console.color(
                        printable,
                        "cyan",
                    )
                )

            elif (
                line.startswith("+")
            ):
                print(
                    self.console.color(
                        printable,
                        "green",
                    )
                )

            elif (
                line.startswith("-")
            ):
                print(
                    self.console.color(
                        printable,
                        "red",
                    )
                )

            else:
                print(
                    printable
                )

        if not found:
            print(
                "✅ دو فایل "
                "یکسان هستند."
            )

        return int(
            ExitCode.OK
        )


# ============================================================
# Global Helpers
# ============================================================

def all_documents_valid(
    status: ProjectStatus,
) -> bool:

    return all(
        doc.status
        in (
            DocumentStatus.COMPLETE,
            DocumentStatus.PUBLISHED,
        )

        for doc
        in (
            status
            .documents
            .values()
        )
    )


def missing_v05_additional(
    status: ProjectStatus,
) -> List[str]:

    doc = (
        status
        .documents
        .get("0.5.md")
    )

    if not doc:
        return sorted(
            status
            .expected_v05_additional
        )

    found = set(
        doc
        .detected_article_ids
    )

    return sorted(
        status
        .expected_v05_additional
        - found
    )


def load_config(
    path: Optional[str],
    repo_path: Optional[Path] = None,
) -> ProjectConfig:

    if not path:
        possible_paths: List[
            Path
        ] = []

        if repo_path:
            possible_paths.extend(
                [
                    repo_path
                    / ".nazm-dad-config.json",

                    repo_path
                    / "nazm-dad-config.json",
                ]
            )

        possible_paths.extend(
            [
                Path.cwd()
                / ".nazm-dad-config.json",

                Path.cwd()
                / "nazm-dad-config.json",

                Path.home()
                / ".nazm-dad-config.json",
            ]
        )

        seen: Set[
            Path
        ] = set()

        for config_path in (
            possible_paths
        ):
            resolved = (
                config_path
                .expanduser()
                .resolve()
            )

            if resolved in seen:
                continue

            seen.add(
                resolved
            )

            if resolved.exists():
                return (
                    ProjectConfig
                    .from_file(
                        resolved
                    )
                )

        return ProjectConfig()

    config_path = (
        Path(path)
        .expanduser()
        .resolve()
    )

    if not config_path.exists():
        print(
            "⚠️ فایل پیکربندی "
            f"'{config_path}' "
            "وجود ندارد. "
            "استفاده از پیش‌فرض.",
            file=sys.stderr,
        )

        return ProjectConfig()

    return ProjectConfig.from_file(
        config_path
    )


# ============================================================
# CLI
# ============================================================

def parse_args(
) -> argparse.Namespace:

    parser = (
        argparse.ArgumentParser(
            description=(
                "Nazm Dad "
                "Project Status Tool "
                f"v{TOOL_VERSION}"
            ),
            epilog=(
                "Git operations "
                "are read-only. "
                "--markdown, --json "
                "and --html write files "
                "only when explicitly "
                "requested."
            ),
        )
    )

    parser.add_argument(
        "--path",
        default=None,
        help=(
            "مسیر مخزن Git؛ "
            "اولویت CLI > config > "
            "دایرکتوری فعلی"
        ),
    )

    parser.add_argument(
        "--config",
        default=None,
        help=(
            "مسیر فایل "
            ".nazm-dad-config.json"
        ),
    )

    parser.add_argument(
        "--output-dir",
        default=None,
        help=(
            "مسیر ذخیره خروجی‌ها؛ "
            "اولویت CLI > config > "
            "ریشه مخزن"
        ),
    )

    parser.add_argument(
        "--verbose",
        action="store_true",
        help=(
            "نمایش جزئیات بیشتر"
        ),
    )

    parser.add_argument(
        "--no-color",
        action="store_true",
        help=(
            "غیرفعال کردن "
            "رنگ خروجی"
        ),
    )

    parser.add_argument(
        "--no-progress",
        action="store_true",
        help=(
            "غیرفعال کردن "
            "گزارش پیشرفت"
        ),
    )

    mode = (
        parser
        .add_mutually_exclusive_group()
    )

    mode.add_argument(
        "--check",
        action="store_true",
        help=(
            "خلاصه سریع وضعیت"
        ),
    )

    mode.add_argument(
        "--validate-docs",
        action="store_true",
        help=(
            "اعتبارسنجی اسناد"
        ),
    )

    mode.add_argument(
        "--health",
        action="store_true",
        help=(
            "محاسبه Health Score"
        ),
    )

    mode.add_argument(
        "--check-links",
        action="store_true",
        help=(
            "بررسی لینک‌ها "
            "و ارجاعات داخلی"
        ),
    )

    mode.add_argument(
        "--history",
        action="store_true",
        help=(
            "نمایش تاریخچه Tagها"
        ),
    )

    mode.add_argument(
        "--diff",
        nargs=2,
        metavar=(
            "FILE_A",
            "FILE_B",
        ),
        help=(
            "مقایسه دو فایل"
        ),
    )

    mode.add_argument(
        "--markdown",
        action="store_true",
        help=(
            "تولید STATUS.md"
        ),
    )

    mode.add_argument(
        "--json",
        action="store_true",
        help=(
            "تولید status.json"
        ),
    )

    mode.add_argument(
        "--html",
        action="store_true",
        help=(
            "تولید dashboard.html"
        ),
    )

    return parser.parse_args()


# ============================================================
# CLI Operations
# ============================================================

def render_check(
    status: ProjectStatus,
) -> int:

    print(
        "📋 خلاصه سریع"
    )

    print(
        f"  Branch: "
        f"{status.git.branch}"
    )

    print(
        "  Working tree: "
        + (
            "✅ clean"
            if status.git.is_clean
            else "❌ dirty"
        )
    )

    print(
        "  Commit: "
        f"{status.git.last_commit_hash}"
        " — "
        f"{status.git.last_commit_message}"
    )

    if status.git.upstream:
        print(
            "  Upstream: "
            f"{status.git.ahead} ahead / "
            f"{status.git.behind} behind"
        )

    else:
        print(
            "  Upstream: "
            "⚠️ not configured"
        )

    invalid: List[
        str
    ] = []

    placeholders: List[
        str
    ] = []

    missing: List[
        str
    ] = []

    for (
        name,
        doc,
    ) in (
        status.documents.items()
    ):

        if (
            doc.status
            == DocumentStatus.INVALID
        ):
            invalid.append(
                name
            )

        elif (
            doc.status
            == DocumentStatus.PLACEHOLDER
        ):
            placeholders.append(
                name
            )

        elif (
            doc.status
            == DocumentStatus.MISSING
        ):
            missing.append(
                name
            )

    if invalid:
        print(
            "  ❌ Invalid: "
            + ", ".join(
                invalid
            )
        )

    if placeholders:
        print(
            "  ⏳ Placeholder: "
            + ", ".join(
                placeholders
            )
        )

    if missing:
        print(
            "  ⚠️ Missing: "
            + ", ".join(
                missing
            )
        )

    if (
        not invalid
        and not placeholders
        and not missing
    ):
        print(
            "  Documents: "
            "✅ all valid"
        )

    missing_additional = (
        missing_v05_additional(
            status
        )
    )

    if missing_additional:
        print(
            "  ❌ Missing v0.5 "
            "additions: "
            + ", ".join(
                missing_additional
            )
        )

    else:
        print(
            "  Additional articles: "
            "✅ all 12 present"
        )

    doc05_stats = (
        status
        .document_stats
        .get("0.5.md")
    )

    if (
        doc05_stats
        and not doc05_stats
        .articles
        .has_continuity
    ):
        print(
            "  ⚠️ Article continuity: "
            "شماره‌های پایه مواد ۰.۵ "
            "دارای شکاف هستند"
        )

    failed = (
        not status.git.is_clean
        or status.git.behind > 0
        or not all_documents_valid(
            status
        )
        or bool(
            missing_additional
        )
        or (
            doc05_stats is not None
            and not doc05_stats
            .articles
            .has_continuity
        )
    )

    return int(
        ExitCode.VALIDATION_FAILED
        if failed
        else ExitCode.OK
    )


def render_document_validation(
    status: ProjectStatus,
) -> int:

    print(
        "🔍 اعتبارسنجی اسناد"
    )

    print()

    failed = False

    for (
        name,
        doc,
    ) in (
        status.documents.items()
    ):

        if (
            doc.status
            == DocumentStatus.COMPLETE
        ):
            icon = "✅"

        elif (
            doc.status
            == DocumentStatus.PUBLISHED
        ):
            icon = "✅"

        elif (
            doc.status
            == DocumentStatus.PLACEHOLDER
        ):
            icon = "⏳"
            failed = True

        elif (
            doc.status
            == DocumentStatus.INVALID
        ):
            icon = "❌"
            failed = True

        else:
            icon = "⚠️"
            failed = True

        print(
            f"{icon} "
            f"{name}: "
            f"{doc.status.value}"
        )

        if (
            doc.articles_count
            is not None
        ):
            print(
                "   مواد: "
                f"{doc.detected_articles}/"
                f"{doc.articles_count}"
            )

        print(
            f"   "
            f"{doc.size_bytes} bytes | "
            f"{doc.lines} lines"
        )

        if doc.description:
            print(
                f"   "
                f"{doc.description}"
            )

        print()

    print(
        "📊 آمار مواد"
    )

    for (
        name,
        stats,
    ) in (
        status
        .document_stats
        .items()
    ):

        print(
            f"  {name}:"
        )

        print(
            "      مواد شناسایی‌شده: "
            f"{stats.articles.total_detected}"
        )

        if (
            stats
            .articles
            .duplicates
        ):
            print(
                "      تکراری: "
                + ", ".join(
                    stats
                    .articles
                    .duplicates
                )
            )

        if (
            stats
            .articles
            .out_of_order
        ):
            print(
                "      خارج از ترتیب: "
                + ", ".join(
                    stats
                    .articles
                    .out_of_order
                )
            )

        print(
            "      پیوستگی شماره‌های پایه: "
            + (
                "✅"
                if stats
                .articles
                .has_continuity
                else "❌"
            )
        )

    return int(
        ExitCode.VALIDATION_FAILED
        if failed
        else ExitCode.OK
    )


def render_history(
    collector: GitInfoCollector,
) -> int:

    tags = (
        collector
        .get_tags_history()
    )

    if not tags:
        print(
            "ℹ️ هیچ Tagی "
            "یافت نشد."
        )

        return int(
            ExitCode.OK
        )

    print(
        "🏷 تاریخچه نسخه‌ها / Tags"
    )

    print()

    for (
        index,
        tag,
    ) in enumerate(
        tags,
        start=1,
    ):
        print(
            f"{index}. "
            f"{tag.name}"
        )

        print(
            f"   Commit: "
            f"{tag.commit}"
        )

        if tag.date:
            print(
                f"   Date: "
                f"{tag.date}"
            )

        if tag.tagger:
            print(
                f"   Tagger: "
                f"{tag.tagger}"
            )

        if tag.message:
            print(
                f"   Message: "
                f"{tag.message}"
            )

        print()

    return int(
        ExitCode.OK
    )


# ============================================================
# Main
# ============================================================

def main() -> int:
    args = parse_args()

    console = Console(
        enable_color=(
            not args.no_color
        )
    )

    logger = VerboseLogger(
        enabled=(
            args.verbose
        )
    )

    preliminary_repo = (
        Path(
            args.path or "."
        )
        .expanduser()
        .resolve()
    )

    config = load_config(
        args.config,
        preliminary_repo,
    )

    repo_value = (
        args.path
        or config.repo_path
        or "."
    )

    repo_path = (
        Path(repo_value)
        .expanduser()
        .resolve()
    )

    if not repo_path.exists():
        print(
            f"❌ مسیر وجود ندارد: "
            f"{repo_path}",
            file=sys.stderr,
        )

        return int(
            ExitCode.RUNTIME_ERROR
        )

    try:
        builder = (
            ProjectStatusBuilder(
                repo_path,
                logger,
                config,
                progress_enabled=(
                    not args.no_progress
                ),
            )
        )

        status = builder.build()

        actual_repo = Path(
            status.git.repository_root
            or repo_path
        ).resolve()

        if args.diff:
            (
                left_raw,
                right_raw,
            ) = args.diff

            left = Path(
                left_raw
            )

            right = Path(
                right_raw
            )

            if not left.is_absolute():
                left = (
                    actual_repo
                    / left
                )

            if not right.is_absolute():
                right = (
                    actual_repo
                    / right
                )

            return DiffRenderer(
                console
            ).render(
                left.resolve(),
                right.resolve(),
            )

        if args.history:
            return render_history(
                builder.git_collector
            )

        if args.check:
            return render_check(
                status
            )

        if args.validate_docs:
            return (
                render_document_validation(
                    status
                )
            )

        link_checker = (
            LinkChecker(
                actual_repo,
                builder
                .doc_validator
                .docs_path,
                builder
                .doc_validator,
                logger,
                progress_enabled=(
                    not args.no_progress
                ),
            )
        )

        if args.check_links:
            report = (
                link_checker.run()
            )

            ConsoleRenderer(
                console
            ).render_links(
                report
            )

            return int(
                ExitCode.VALIDATION_FAILED
                if report.broken
                else ExitCode.OK
            )

        if args.health:
            link_report = (
                link_checker.run()
            )

            health = (
                HealthCalculator(
                    status,
                    link_report,
                    config,
                ).calculate()
            )

            ConsoleRenderer(
                console
            ).render_health(
                health
            )

            return int(
                ExitCode.OK
                if (
                    health.percent
                    >= config
                    .health_threshold_ok
                )
                else
                ExitCode
                .VALIDATION_FAILED
            )

        if (
            args.markdown
            or args.json
            or args.html
        ):
            output_dir_value = (
                args.output_dir
                or config.output_dir
            )

            if output_dir_value:
                output_dir = Path(
                    output_dir_value
                ).expanduser()

                if (
                    not output_dir
                    .is_absolute()
                ):
                    output_dir = (
                        actual_repo
                        / output_dir
                    )

            else:
                output_dir = (
                    actual_repo
                )

            output_dir = (
                output_dir.resolve()
            )

            logger.log(
                "Output directory: "
                f"{output_dir}"
            )

            output_dir.mkdir(
                parents=True,
                exist_ok=True,
            )

            link_report = (
                link_checker.run()
            )

            health = (
                HealthCalculator(
                    status,
                    link_report,
                    config,
                ).calculate()
            )

            if args.markdown:
                output = (
                    output_dir
                    / "STATUS.md"
                )

                output.write_text(
                    MarkdownRenderer()
                    .render(
                        status,
                        health,
                    ),
                    encoding="utf-8",
                    newline="\n",
                )

                print(
                    "✅ STATUS.md "
                    "ذخیره شد:\n"
                    f"{output}"
                )

                return int(
                    ExitCode.OK
                )

            if args.json:
                output = (
                    output_dir
                    / "status.json"
                )

                output.write_text(
                    JsonRenderer()
                    .render(
                        status,
                        health,
                    ),
                    encoding="utf-8",
                    newline="\n",
                )

                print(
                    "✅ status.json "
                    "ذخیره شد:\n"
                    f"{output}"
                )

                return int(
                    ExitCode.OK
                )

            if args.html:
                output = (
                    output_dir
                    / "dashboard.html"
                )

                output.write_text(
                    HtmlRenderer()
                    .render(
                        status,
                        health,
                    ),
                    encoding="utf-8",
                    newline="\n",
                )

                print(
                    "✅ dashboard.html "
                    "ذخیره شد:\n"
                    f"{output}"
                )

                return int(
                    ExitCode.OK
                )

        ConsoleRenderer(
            console
        ).render(
            status
        )

        return int(
            ExitCode.OK
        )

    except KeyboardInterrupt:
        print(
            "\n⚠️ عملیات توسط "
            "کاربر متوقف شد.",
            file=sys.stderr,
        )

        return int(
            ExitCode.RUNTIME_ERROR
        )

    except RuntimeError as exc:
        print(
            f"❌ خطا: {exc}",
            file=sys.stderr,
        )

        return int(
            ExitCode.RUNTIME_ERROR
        )

    except OSError as exc:
        print(
            f"❌ خطای فایل/سیستم: "
            f"{exc}",
            file=sys.stderr,
        )

        return int(
            ExitCode.RUNTIME_ERROR
        )


if __name__ == "__main__":
    sys.exit(
        main()
    )