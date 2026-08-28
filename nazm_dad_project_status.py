#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
نظم داد — ابزار وضعیت، اعتبارسنجی و مستندسازی پروژه
نسخه: 2.3

این ابزار وضعیت واقعی پروژه را از Git و فایل‌های محلی می‌خواند.

ویژگی‌ها:
    - خواندن وضعیت واقعی Git
    - نمایش branch، commit، tags و ahead/behind
    - اعتبارسنجی اسناد اصلی پروژه
    - تشخیص COMPLETE / PLACEHOLDER / INVALID / MISSING
    - شمارش مواد قانون اساسی
    - بررسی ۱۲ ماده مستقل افزوده‌شده در نسخه ۰.۵
    - تولید STATUS.md
    - تولید status.json
    - تعیین مسیر خروجی با --output-dir
    - حالت verbose برای نمایش مراحل اجرا
    - نمایش تاریخچه tagها با --history
    - مقایسه دو فایل با --diff
    - بدون وابستگی خارجی

نکته:
    عملیات Git، اعتبارسنجی، history و diff فقط خواندنی هستند.
    فقط --markdown و --json فایل خروجی ایجاد یا بازنویسی می‌کنند.

نمونه‌ها:

    python nazm_dad_project_status.py

    python nazm_dad_project_status.py --check

    python nazm_dad_project_status.py --validate-docs

    python nazm_dad_project_status.py --validate-docs --verbose

    python nazm_dad_project_status.py --history

    python nazm_dad_project_status.py --history --verbose

    python nazm_dad_project_status.py --diff docs/0.4.md docs/0.5.md

    python nazm_dad_project_status.py \
        --markdown \
        --output-dir docs/status

    python nazm_dad_project_status.py \
        --json \
        --output-dir docs/status \
        --verbose
"""

from __future__ import annotations

import argparse
import difflib
import json
import re
import subprocess
import sys

from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from pathlib import Path
from typing import Dict, List, Optional, Set, Tuple


# ============================================================
# Constants
# ============================================================

VERSION = "2.3"

DEFAULT_V04_ARTICLES = 61
DEFAULT_V05_ARTICLES = 73
DEFAULT_TOTAL_CHANGES = 21
DEFAULT_NOOP_CHANGES = 2


# ============================================================
# Terminal Colors
# ============================================================

class Colors:
    RESET = "\033[0m"
    RED = "\033[91m"
    GREEN = "\033[92m"
    YELLOW = "\033[93m"
    BLUE = "\033[94m"
    CYAN = "\033[96m"
    DIM = "\033[2m"


def colorize(text: str, color: str, enabled: bool = True) -> str:
    """
    رنگ‌بندی ساده ANSI.

    در صورت عدم پشتیبانی ترمینال، خروجی همچنان قابل خواندن است.
    """
    if not enabled:
        return text

    return f"{color}{text}{Colors.RESET}"


# ============================================================
# Verbose Logger
# ============================================================

class VerboseLogger:
    """
    ثبت مراحل اجرا در حالت --verbose.
    """

    def __init__(self, enabled: bool = False):
        self.enabled = enabled

    def log(self, message: str) -> None:
        if self.enabled:
            print(
                colorize(
                    f"[verbose] {message}",
                    Colors.DIM,
                )
            )


# ============================================================
# Enums
# ============================================================

class DocumentStatus(str, Enum):
    COMPLETE = "کامل و authoritative"
    PUBLISHED = "منتشر شده"
    PLACEHOLDER = "placeholder (در انتظار محتوای نهایی)"
    INVALID = "نامعتبر (ساختار یا محتوا اشتباه است)"
    MISSING = "وجود ندارد"


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
    repository_root: Optional[Path] = None


@dataclass
class TagInfo:
    name: str
    commit_hash: str
    commit_date: str
    subject: str


@dataclass
class DocumentInfo:
    path: Path
    status: DocumentStatus

    size_bytes: int
    lines: int = 0
    characters: int = 0

    articles_count: Optional[int] = None
    detected_articles: Optional[int] = None

    article_ids: List[str] = field(default_factory=list)

    description: str = ""
    is_placeholder: bool = False

    duplicate_articles: List[str] = field(default_factory=list)
    missing_additional: List[str] = field(default_factory=list)


@dataclass
class ProjectStatus:
    git: GitInfo
    documents: Dict[str, DocumentInfo]

    total_articles_v04: int = DEFAULT_V04_ARTICLES
    total_articles_v05: int = DEFAULT_V05_ARTICLES

    expected_v05_additional: Set[str] = field(default_factory=set)

    total_changes: int = DEFAULT_TOTAL_CHANGES
    noop_changes: int = DEFAULT_NOOP_CHANGES

    timestamp: str = field(
        default_factory=lambda: datetime.now().isoformat(
            timespec="seconds"
        )
    )

    def __post_init__(self) -> None:
        if not self.expected_v05_additional:
            self.expected_v05_additional = {
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


# ============================================================
# Git Collector
# ============================================================

class GitInfoCollector:
    """
    خواندن اطلاعات Git با subprocess.

    این کلاس هیچ دستور تغییردهنده Git اجرا نمی‌کند.
    """

    def __init__(
        self,
        repo_path: Path,
        logger: Optional[VerboseLogger] = None,
    ):
        self.repo_path = repo_path.resolve()
        self.logger = logger or VerboseLogger(False)

    def _run_git(
        self,
        args: List[str],
        check: bool = True,
    ) -> subprocess.CompletedProcess:

        command = [
            "git",
            "-C",
            str(self.repo_path),
            *args,
        ]

        self.logger.log(
            "Git: " + " ".join(command)
        )

        try:
            result = subprocess.run(
                command,
                capture_output=True,
                text=True,
                check=check,
                encoding="utf-8",
                errors="replace",
            )

            return result

        except FileNotFoundError as exc:
            raise RuntimeError(
                "Git روی سیستم پیدا نشد. "
                "اطمینان حاصل کنید Git نصب شده و در PATH قرار دارد."
            ) from exc

        except subprocess.CalledProcessError as exc:
            if check:
                stderr = exc.stderr or ""

                raise RuntimeError(
                    "Git command failed:\n"
                    + " ".join(command)
                    + "\n"
                    + stderr
                ) from exc

            return exc

    def check_repository(self) -> bool:
        """
        بررسی اینکه مسیر یک مخزن Git معتبر است.
        """
        result = self._run_git(
            [
                "rev-parse",
                "--is-inside-work-tree",
            ],
            check=False,
        )

        return (
            result.returncode == 0
            and result.stdout.strip().lower() == "true"
        )

    def get_repository_root(self) -> Path:
        result = self._run_git(
            [
                "rev-parse",
                "--show-toplevel",
            ]
        )

        return Path(
            result.stdout.strip()
        ).resolve()

    def get_info(self) -> GitInfo:
        """
        جمع‌آوری اطلاعات اصلی Git.
        """

        if not self.check_repository():
            raise RuntimeError(
                f"مسیر زیر یک مخزن Git معتبر نیست:\n"
                f"{self.repo_path}"
            )

        repository_root = self.get_repository_root()

        self.logger.log(
            f"Repository root: {repository_root}"
        )

        # ----------------------------------------------------
        # Branch
        # ----------------------------------------------------

        branch_result = self._run_git(
            [
                "rev-parse",
                "--abbrev-ref",
                "HEAD",
            ]
        )

        branch = branch_result.stdout.strip()

        # ----------------------------------------------------
        # Working tree
        # ----------------------------------------------------

        status_result = self._run_git(
            [
                "status",
                "--porcelain",
            ]
        )

        is_clean = (
            status_result.stdout.strip() == ""
        )

        # ----------------------------------------------------
        # Last commit
        # ----------------------------------------------------

        commit_result = self._run_git(
            [
                "log",
                "-1",
                "--format=%H%n%s%n%ai",
            ]
        )

        commit_lines = (
            commit_result.stdout
            .strip()
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

        # ----------------------------------------------------
        # Tags
        # ----------------------------------------------------

        tags_result = self._run_git(
            [
                "tag",
                "--list",
                "--sort=-creatordate",
            ]
        )

        tags = [
            line.strip()
            for line in (
                tags_result.stdout
                .splitlines()
            )
            if line.strip()
        ]

        # ----------------------------------------------------
        # Upstream
        # ----------------------------------------------------

        upstream = None

        upstream_result = self._run_git(
            [
                "rev-parse",
                "--abbrev-ref",
                "--symbolic-full-name",
                "@{upstream}",
            ],
            check=False,
        )

        if (
            upstream_result.returncode == 0
            and upstream_result.stdout.strip()
        ):
            upstream = (
                upstream_result.stdout.strip()
            )

        # ----------------------------------------------------
        # Ahead / Behind
        # ----------------------------------------------------

        ahead = 0
        behind = 0

        if upstream:

            relation_result = self._run_git(
                [
                    "rev-list",
                    "--left-right",
                    "--count",
                    f"HEAD...{upstream}",
                ],
                check=False,
            )

            if (
                relation_result.returncode == 0
                and relation_result.stdout.strip()
            ):

                parts = (
                    relation_result.stdout
                    .strip()
                    .split()
                )

                if len(parts) == 2:
                    try:
                        ahead = int(parts[0])
                        behind = int(parts[1])
                    except ValueError:
                        ahead = 0
                        behind = 0

        # ----------------------------------------------------
        # Remote
        # ----------------------------------------------------

        remote = None

        remote_result = self._run_git(
            [
                "remote",
                "get-url",
                "origin",
            ],
            check=False,
        )

        if (
            remote_result.returncode == 0
            and remote_result.stdout.strip()
        ):
            remote = (
                remote_result.stdout.strip()
            )

        return GitInfo(
            branch=branch,
            is_clean=is_clean,

            last_commit_hash=(
                commit_hash[:8]
                if commit_hash
                else ""
            ),

            last_commit_message=commit_message,
            last_commit_date=commit_date,

            tags=tags,

            ahead=ahead,
            behind=behind,

            upstream=upstream,
            remote=remote,

            repository_root=repository_root,
        )

    def get_tag_history(self) -> List[TagInfo]:
        """
        دریافت tagها و commit مرتبط با هر tag.
        """

        if not self.check_repository():
            raise RuntimeError(
                f"مسیر زیر یک مخزن Git معتبر نیست:\n"
                f"{self.repo_path}"
            )

        self.logger.log(
            "Reading tag history"
        )

        result = self._run_git(
            [
                "for-each-ref",
                "--sort=-creatordate",
                "--format=%(refname:short)|%(objectname:short)|%(creatordate:iso8601)|%(subject)",
                "refs/tags",
            ]
        )

        history: List[TagInfo] = []

        for raw_line in result.stdout.splitlines():

            line = raw_line.strip()

            if not line:
                continue

            parts = line.split(
                "|",
                3,
            )

            name = (
                parts[0]
                if len(parts) > 0
                else ""
            )

            commit_hash = (
                parts[1]
                if len(parts) > 1
                else ""
            )

            commit_date = (
                parts[2]
                if len(parts) > 2
                else ""
            )

            subject = (
                parts[3]
                if len(parts) > 3
                else ""
            )

            # annotated tag ممکن است object خود tag را بدهد.
            # commit واقعی resolve می‌شود.
            resolved_result = self._run_git(
                [
                    "rev-list",
                    "-n",
                    "1",
                    name,
                ],
                check=False,
            )

            if (
                resolved_result.returncode == 0
                and resolved_result.stdout.strip()
            ):

                resolved_hash = (
                    resolved_result.stdout
                    .strip()[:8]
                )

                commit_info = self._run_git(
                    [
                        "show",
                        "-s",
                        "--format=%ai|%s",
                        name,
                    ],
                    check=False,
                )

                if (
                    commit_info.returncode == 0
                    and commit_info.stdout.strip()
                ):

                    info_parts = (
                        commit_info.stdout
                        .strip()
                        .split(
                            "|",
                            1,
                        )
                    )

                    if len(info_parts) > 0:
                        commit_date = (
                            info_parts[0]
                        )

                    if len(info_parts) > 1:
                        subject = (
                            info_parts[1]
                        )

                commit_hash = (
                    resolved_hash
                )

            history.append(
                TagInfo(
                    name=name,
                    commit_hash=commit_hash,
                    commit_date=commit_date,
                    subject=subject,
                )
            )

        return history


# ============================================================
# Document Validator
# ============================================================

class DocumentValidator:
    """
    اعتبارسنجی اسناد پروژه.
    """

    ARTICLE_PATTERN = re.compile(
        r"^\s*\*\*ماده\s+"
        r"([۰-۹0-9]+(?:[–—\-][۰-۹0-9]+)?)"
        r"\s*[ـ–—-]",
        re.MULTILINE,
    )

    EXPECTED_V05_ADDITIONAL = {
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

    EXPECTED_DOCS = {

        "0.4.md": {
            "min_size": 5000,

            "expected_start":
                "# قانون اساسی «نظم داد» – نسخه ۰.۴ نهایی",

            "articles":
                DEFAULT_V04_ARTICLES,

            "additional_articles":
                set(),
        },

        "0.5.md": {
            "min_size": 5000,

            "expected_start":
                "# قانون اساسی «نظم داد» – نسخه ۰.۵ نهایی",

            "articles":
                DEFAULT_V05_ARTICLES,

            "additional_articles":
                EXPECTED_V05_ADDITIONAL,
        },

        "changelog.md": {
            "min_size": 500,

            "expected_start":
                None,

            "articles":
                None,

            "additional_articles":
                set(),
        },

        "rules.md": {
            "min_size": 100,

            "expected_start":
                None,

            "articles":
                None,

            "additional_articles":
                set(),
        },

        "decisions.md": {
            "min_size": 100,

            "expected_start":
                None,

            "articles":
                None,

            "additional_articles":
                set(),
        },

    }

    PLACEHOLDER_MARKERS = (
        "placeholder",
        "در انتظار درج متن",
        "در انتظار جایگزینی",
        "لطفاً کل متن",
        "به‌زودی",
        "[اینجا کل متن",
    )

    def __init__(
        self,
        docs_path: Path,
        logger: Optional[VerboseLogger] = None,
    ):
        self.docs_path = docs_path.resolve()
        self.logger = logger or VerboseLogger(False)

    @staticmethod
    def normalize_digits(
        value: str,
    ) -> str:
        """
        تبدیل ارقام فارسی و عربی به لاتین.
        """

        translation = str.maketrans(
            "۰۱۲۳۴۵۶۷۸۹٠١٢٣٤٥٦٧٨٩",
            "01234567890123456789",
        )

        return value.translate(
            translation
        )

    def find_articles(
        self,
        content: str,
    ) -> List[str]:

        articles: List[str] = []

        for match in (
            self.ARTICLE_PATTERN
            .finditer(content)
        ):

            article_id = (
                self.normalize_digits(
                    match.group(1)
                )
            )

            article_id = (
                article_id
                .replace("–", "-")
                .replace("—", "-")
            )

            articles.append(
                article_id
            )

        return articles

    @staticmethod
    def find_duplicates(
        values: List[str],
    ) -> List[str]:

        seen: Set[str] = set()
        duplicates: Set[str] = set()

        for value in values:

            if value in seen:
                duplicates.add(
                    value
                )

            seen.add(
                value
            )

        return sorted(
            duplicates
        )

    def validate_all(
        self,
    ) -> Dict[str, DocumentInfo]:

        self.logger.log(
            f"Validating documents in {self.docs_path}"
        )

        results: Dict[
            str,
            DocumentInfo
        ] = {}

        for (
            filename,
            spec,
        ) in self.EXPECTED_DOCS.items():

            file_path = (
                self.docs_path
                / filename
            )

            self.logger.log(
                f"Validating {file_path}"
            )

            results[filename] = (
                self._validate_single(
                    file_path,
                    spec,
                    filename,
                )
            )

        return results

    def _validate_single(
        self,
        file_path: Path,
        spec: Dict,
        filename: str,
    ) -> DocumentInfo:

        # ----------------------------------------------------
        # Missing
        # ----------------------------------------------------

        if not file_path.exists():

            return DocumentInfo(
                path=file_path,

                status=(
                    DocumentStatus.MISSING
                ),

                size_bytes=0,

                description=(
                    f"فایل {filename} وجود ندارد."
                ),
            )

        if not file_path.is_file():

            return DocumentInfo(
                path=file_path,

                status=(
                    DocumentStatus.INVALID
                ),

                size_bytes=0,

                description=(
                    f"{filename} فایل عادی نیست."
                ),
            )

        # ----------------------------------------------------
        # Read UTF-8
        # ----------------------------------------------------

        try:

            content = file_path.read_text(
                encoding="utf-8"
            )

        except UnicodeDecodeError as exc:

            return DocumentInfo(
                path=file_path,

                status=(
                    DocumentStatus.INVALID
                ),

                size_bytes=(
                    file_path.stat().st_size
                ),

                description=(
                    "فایل UTF-8 معتبر نیست: "
                    f"{exc}"
                ),
            )

        except OSError as exc:

            return DocumentInfo(
                path=file_path,

                status=(
                    DocumentStatus.INVALID
                ),

                size_bytes=0,

                description=(
                    "خطا هنگام خواندن فایل: "
                    f"{exc}"
                ),
            )

        # ----------------------------------------------------
        # Metrics
        # ----------------------------------------------------

        size_bytes = (
            file_path.stat().st_size
        )

        characters = len(
            content
        )

        lines = (
            content.count("\n") + 1
            if content
            else 0
        )

        # ----------------------------------------------------
        # Placeholder
        # ----------------------------------------------------

        lowered = (
            content.lower()
        )

        marker_found = any(
            marker.lower() in lowered
            for marker
            in self.PLACEHOLDER_MARKERS
        )

        min_size = spec.get(
            "min_size",
            100,
        )

        is_placeholder = (
            marker_found
            or size_bytes < min_size
        )

        # ----------------------------------------------------
        # Heading
        # ----------------------------------------------------

        expected_start = spec.get(
            "expected_start"
        )

        start_ok = True

        if expected_start:

            normalized_content = (
                content
                .lstrip("\ufeff")
                .strip()
            )

            start_ok = (
                normalized_content
                .startswith(
                    expected_start
                )
            )

        # ----------------------------------------------------
        # Articles
        # ----------------------------------------------------

        expected_articles = spec.get(
            "articles"
        )

        article_ids: List[str] = []
        detected_articles = None
        articles_ok = True

        duplicate_articles: List[str] = []

        if expected_articles is not None:

            article_ids = (
                self.find_articles(
                    content
                )
            )

            detected_articles = len(
                article_ids
            )

            duplicate_articles = (
                self.find_duplicates(
                    article_ids
                )
            )

            articles_ok = (
                detected_articles
                == expected_articles
                and not duplicate_articles
            )

        # ----------------------------------------------------
        # Additional Articles
        # ----------------------------------------------------

        expected_additional = (
            spec.get(
                "additional_articles"
            )
            or set()
        )

        missing_additional: List[str] = []

        additional_ok = True

        if expected_additional:

            found_set = set(
                article_ids
            )

            missing_additional = sorted(
                expected_additional
                - found_set
            )

            additional_ok = (
                not missing_additional
            )

        # ----------------------------------------------------
        # Changelog sanity
        # ----------------------------------------------------

        changelog_ok = True

        if filename == "changelog.md":

            required_fragments = [
                "۰.۴",
                "۰.۵",
            ]

            changelog_ok = all(
                fragment in content
                for fragment
                in required_fragments
            )

        # ----------------------------------------------------
        # Diagnostics
        # ----------------------------------------------------

        details: List[str] = [
            f"size={size_bytes} bytes",
            f"lines={lines}",
        ]

        if not start_ok:
            details.append(
                "شروع متن با انتظار مطابقت ندارد"
            )

        if (
            expected_articles is not None
            and detected_articles
            != expected_articles
        ):

            details.append(
                f"مواد={detected_articles}/{expected_articles}"
            )

        if duplicate_articles:

            details.append(
                "مواد تکراری: "
                + ", ".join(
                    duplicate_articles
                )
            )

        if missing_additional:

            details.append(
                "مواد الحاقی مفقود: "
                + ", ".join(
                    missing_additional
                )
            )

        if not changelog_ok:

            details.append(
                "changelog شامل ارجاع لازم "
                "به نسخه‌های ۰.۴ و ۰.۵ نیست"
            )

        # ----------------------------------------------------
        # Status
        # ----------------------------------------------------

        if is_placeholder:

            status = (
                DocumentStatus.PLACEHOLDER
            )

            description = (
                " | ".join(details)
            )

        elif (
            not start_ok
            or not articles_ok
            or not additional_ok
            or not changelog_ok
        ):

            status = (
                DocumentStatus.INVALID
            )

            description = (
                " | ".join(details)
            )

        else:

            status = (
                DocumentStatus.COMPLETE
            )

            description = (
                "فایل معتبر است."
            )

        return DocumentInfo(
            path=file_path,

            status=status,

            size_bytes=size_bytes,
            lines=lines,
            characters=characters,

            articles_count=(
                expected_articles
            ),

            detected_articles=(
                detected_articles
            ),

            article_ids=(
                article_ids
            ),

            description=(
                description
            ),

            is_placeholder=(
                is_placeholder
            ),

            duplicate_articles=(
                duplicate_articles
            ),

            missing_additional=(
                missing_additional
            ),
        )


# ============================================================
# Project Builder
# ============================================================

class ProjectStatusBuilder:

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

        self.git_collector = (
            GitInfoCollector(
                self.repo_path,
                logger=self.logger,
            )
        )

        self.document_validator = (
            DocumentValidator(
                self.repo_path
                / "docs",
                logger=self.logger,
            )
        )

    def build(
        self,
    ) -> ProjectStatus:

        self.logger.log(
            "Collecting Git information"
        )

        git_info = (
            self.git_collector
            .get_info()
        )

        self.logger.log(
            "Validating documents"
        )

        documents = (
            self.document_validator
            .validate_all()
        )

        return ProjectStatus(
            git=git_info,

            documents=documents,

            total_articles_v04=(
                DEFAULT_V04_ARTICLES
            ),

            total_articles_v05=(
                DEFAULT_V05_ARTICLES
            ),

            expected_v05_additional=set(
                DocumentValidator
                .EXPECTED_V05_ADDITIONAL
            ),

            total_changes=(
                DEFAULT_TOTAL_CHANGES
            ),

            noop_changes=(
                DEFAULT_NOOP_CHANGES
            ),
        )


# ============================================================
# Helpers
# ============================================================

def document_icon(
    status: DocumentStatus,
) -> str:

    if status in {
        DocumentStatus.COMPLETE,
        DocumentStatus.PUBLISHED,
    }:
        return "✅"

    if status == DocumentStatus.PLACEHOLDER:
        return "⏳"

    if status == DocumentStatus.INVALID:
        return "❌"

    if status == DocumentStatus.MISSING:
        return "🚫"

    return "⚠️"


def all_documents_valid(
    status: ProjectStatus,
) -> bool:

    return all(
        doc.status
        in {
            DocumentStatus.COMPLETE,
            DocumentStatus.PUBLISHED,
        }
        for doc
        in status.documents.values()
    )


def get_missing_v05_articles(
    status: ProjectStatus,
) -> List[str]:

    doc = (
        status.documents.get(
            "0.5.md"
        )
    )

    if not doc:
        return sorted(
            status.expected_v05_additional
        )

    found = set(
        doc.article_ids
    )

    return sorted(
        status.expected_v05_additional
        - found
    )


# ============================================================
# Console Renderer
# ============================================================

class ConsoleRenderer:

    def render(
        self,
        status: ProjectStatus,
    ) -> None:

        width = 78

        print(
            "=" * width
        )

        print(
            " پروژه نظم داد — Nazm Dad "
            .center(width)
        )

        print(
            "=" * width
        )

        print(
            f"\nنسخه ابزار: v{VERSION}"
        )

        # ----------------------------------------------------
        # Git
        # ----------------------------------------------------

        print(
            "\n📦 وضعیت Git"
        )

        print(
            f"  شاخه فعلی: "
            f"{status.git.branch}"
        )

        print(
            "  Working tree: "
            + (
                colorize(
                    "✅ clean",
                    Colors.GREEN,
                )
                if status.git.is_clean
                else colorize(
                    "❌ dirty",
                    Colors.RED,
                )
            )
        )

        print(
            "  آخرین commit: "
            f"{status.git.last_commit_hash}"
        )

        if status.git.last_commit_message:

            print(
                "  پیام: "
                f"{status.git.last_commit_message}"
            )

        if status.git.last_commit_date:

            print(
                "  تاریخ: "
                f"{status.git.last_commit_date}"
            )

        if status.git.tags:

            print(
                "  Tagها: "
                + ", ".join(
                    status.git.tags
                )
            )

        else:

            print(
                "  Tagها: هیچ"
            )

        if status.git.remote:

            print(
                "  Remote: "
                f"{status.git.remote}"
            )

        if status.git.upstream:

            print(
                "  Upstream branch: "
                f"{status.git.upstream}"
            )

        print(
            "  نسبت به upstream: "
            f"{status.git.ahead} ahead / "
            f"{status.git.behind} behind"
        )

        # ----------------------------------------------------
        # Documents
        # ----------------------------------------------------

        print(
            "\n📄 وضعیت اسناد"
        )

        for (
            name,
            doc,
        ) in status.documents.items():

            icon = (
                document_icon(
                    doc.status
                )
            )

            print(
                f"  {icon} {name}"
            )

            print(
                f"      وضعیت: "
                f"{doc.status.value}"
            )

            print(
                f"      اندازه: "
                f"{doc.size_bytes} bytes"
            )

            print(
                f"      خطوط: "
                f"{doc.lines}"
            )

            if (
                doc.articles_count
                is not None
            ):

                print(
                    "      مواد: "
                    f"{doc.detected_articles}/"
                    f"{doc.articles_count}"
                )

            if doc.description:

                print(
                    f"      {doc.description}"
                )

        # ----------------------------------------------------
        # 0.5 Articles
        # ----------------------------------------------------

        print(
            "\n🔍 مواد مستقل افزوده‌شده در ۰.۵"
        )

        missing = (
            get_missing_v05_articles(
                status
            )
        )

        if missing:

            print(
                "  ❌ مفقود: "
                + ", ".join(
                    missing
                )
            )

        else:

            print(
                "  ✅ هر ۱۲ ماده مستقل افزوده‌شده موجود است."
            )

        # ----------------------------------------------------
        # Summary
        # ----------------------------------------------------

        print(
            "\n📈 خلاصه"
        )

        print(
            f"  تغییرات واقعی: "
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
            "  اعتبار تمام اسناد: "
            + (
                "✅"
                if all_documents_valid(
                    status
                )
                else "⚠️"
            )
        )

        print(
            "\n"
            + "=" * width
        )


# ============================================================
# Markdown Renderer
# ============================================================

class MarkdownRenderer:

    def render(
        self,
        status: ProjectStatus,
    ) -> str:

        lines: List[str] = []

        lines.append(
            "# وضعیت پروژه نظم داد"
        )

        lines.append("")

        lines.append(
            f"**زمان تولید:** "
            f"{status.timestamp}"
        )

        lines.append("")

        lines.append(
            f"**نسخه ابزار:** "
            f"`{VERSION}`"
        )

        lines.append("")

        # ----------------------------------------------------
        # Git
        # ----------------------------------------------------

        lines.append(
            "## وضعیت Git"
        )

        lines.append("")

        lines.append(
            f"- **شاخه فعلی:** "
            f"`{status.git.branch}`"
        )

        lines.append(
            "- **Working tree:** "
            + (
                "✅ clean"
                if status.git.is_clean
                else "❌ dirty"
            )
        )

        lines.append(
            f"- **آخرین commit:** "
            f"`{status.git.last_commit_hash}`"
            f" — "
            f"{status.git.last_commit_message}"
        )

        if status.git.last_commit_date:

            lines.append(
                f"- **تاریخ commit:** "
                f"{status.git.last_commit_date}"
            )

        if status.git.tags:

            lines.append(
                "- **Tagها:** "
                + ", ".join(
                    f"`{tag}`"
                    for tag in status.git.tags
                )
            )

        if status.git.remote:

            lines.append(
                f"- **Remote:** "
                f"`{status.git.remote}`"
            )

        if status.git.upstream:

            lines.append(
                f"- **Upstream:** "
                f"`{status.git.upstream}`"
            )

        lines.append(
            f"- **نسبت به upstream:** "
            f"{status.git.ahead} ahead / "
            f"{status.git.behind} behind"
        )

        lines.append("")

        # ----------------------------------------------------
        # Documents
        # ----------------------------------------------------

        lines.append(
            "## وضعیت اسناد"
        )

        lines.append("")

        lines.append(
            "| فایل | وضعیت | اندازه | خطوط | مواد |"
        )

        lines.append(
            "|---|---|---:|---:|---:|"
        )

        for (
            name,
            doc,
        ) in status.documents.items():

            detected = (
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
                f"| {name} "
                f"| {document_icon(doc.status)} "
                f"{doc.status.value} "
                f"| {doc.size_bytes} B "
                f"| {doc.lines} "
                f"| {detected} |"
            )

        lines.append("")

        # ----------------------------------------------------
        # Additional Articles
        # ----------------------------------------------------

        lines.append(
            "## مواد مستقل افزوده‌شده در ۰.۵"
        )

        lines.append("")

        missing = (
            get_missing_v05_articles(
                status
            )
        )

        if missing:

            lines.append(
                "❌ مواد مفقود: "
                + ", ".join(
                    missing
                )
            )

        else:

            lines.append(
                "✅ هر ۱۲ ماده مستقل افزوده‌شده "
                "در نسخه ۰.۵ موجود است."
            )

        lines.append("")

        # ----------------------------------------------------
        # Summary
        # ----------------------------------------------------

        lines.append(
            "## خلاصه"
        )

        lines.append("")

        lines.append(
            f"- **تغییرات واقعی:** "
            f"{status.total_changes}"
        )

        lines.append(
            f"- **No-op:** "
            f"{status.noop_changes}"
        )

        lines.append(
            f"- **مواد ۰.۴:** "
            f"{status.total_articles_v04}"
        )

        lines.append(
            f"- **مواد ۰.۵:** "
            f"{status.total_articles_v05}"
        )

        lines.append(
            f"- **افزایش مواد مستقل:** "
            f"{status.total_articles_v05 - status.total_articles_v04}"
        )

        lines.append(
            "- **اعتبار تمام اسناد:** "
            + (
                "✅"
                if all_documents_valid(
                    status
                )
                else "⚠️"
            )
        )

        lines.append("")

        lines.append("---")

        lines.append("")

        lines.append(
            "_تولیدشده توسط "
            "`nazm_dad_project_status.py`_"
        )

        return "\n".join(
            lines
        )


# ============================================================
# JSON Renderer
# ============================================================

class JsonRenderer:

    def render(
        self,
        status: ProjectStatus,
    ) -> str:

        documents: Dict[
            str,
            Dict
        ] = {}

        for (
            name,
            doc,
        ) in status.documents.items():

            documents[name] = {
                "path":
                    str(doc.path),

                "status":
                    doc.status.value,

                "size_bytes":
                    doc.size_bytes,

                "lines":
                    doc.lines,

                "characters":
                    doc.characters,

                "expected_articles":
                    doc.articles_count,

                "detected_articles":
                    doc.detected_articles,

                "article_ids":
                    doc.article_ids,

                "duplicate_articles":
                    doc.duplicate_articles,

                "missing_additional":
                    doc.missing_additional,

                "is_placeholder":
                    doc.is_placeholder,

                "description":
                    doc.description,
            }

        missing = (
            get_missing_v05_articles(
                status
            )
        )

        data = {
            "tool": {
                "name":
                    "nazm_dad_project_status",

                "version":
                    VERSION,
            },

            "timestamp":
                status.timestamp,

            "git": {
                "branch":
                    status.git.branch,

                "is_clean":
                    status.git.is_clean,

                "last_commit":
                    status.git.last_commit_hash,

                "last_commit_message":
                    status.git.last_commit_message,

                "last_commit_date":
                    status.git.last_commit_date,

                "tags":
                    status.git.tags,

                "ahead":
                    status.git.ahead,

                "behind":
                    status.git.behind,

                "upstream":
                    status.git.upstream,

                "remote":
                    status.git.remote,

                "repository_root":
                    (
                        str(
                            status.git.repository_root
                        )
                        if status.git.repository_root
                        else None
                    ),
            },

            "documents":
                documents,

            "v05_additional_articles": {
                "expected":
                    sorted(
                        status.expected_v05_additional
                    ),

                "missing":
                    missing,

                "all_present":
                    not missing,
            },

            "summary": {
                "total_changes":
                    status.total_changes,

                "noop_changes":
                    status.noop_changes,

                "articles_v04":
                    status.total_articles_v04,

                "articles_v05":
                    status.total_articles_v05,

                "added_articles":
                    (
                        status.total_articles_v05
                        - status.total_articles_v04
                    ),

                "all_documents_valid":
                    all_documents_valid(
                        status
                    ),
            },
        }

        return json.dumps(
            data,
            ensure_ascii=False,
            indent=2,
        )


# ============================================================
# History Renderer
# ============================================================

class HistoryRenderer:

    def render(
        self,
        history: List[TagInfo],
    ) -> None:

        print(
            "🏷️ تاریخچه نسخه‌ها / Tagها"
        )

        print()

        if not history:

            print(
                "هیچ Tagی در مخزن یافت نشد."
            )

            return

        for index, tag in enumerate(
            history,
            start=1,
        ):

            print(
                f"{index}. {tag.name}"
            )

            print(
                f"   Commit: "
                f"{tag.commit_hash}"
            )

            print(
                f"   Date: "
                f"{tag.commit_date}"
            )

            if tag.subject:

                print(
                    f"   Message: "
                    f"{tag.subject}"
                )

            print()


# ============================================================
# File Diff
# ============================================================

class FileDiffRenderer:

    def __init__(
        self,
        use_color: bool = True,
    ):
        self.use_color = use_color

    @staticmethod
    def _read_file(
        path: Path,
    ) -> List[str]:

        try:

            text = path.read_text(
                encoding="utf-8"
            )

        except UnicodeDecodeError as exc:

            raise RuntimeError(
                f"فایل UTF-8 معتبر نیست:\n"
                f"{path}\n"
                f"{exc}"
            ) from exc

        except OSError as exc:

            raise RuntimeError(
                f"امکان خواندن فایل وجود ندارد:\n"
                f"{path}\n"
                f"{exc}"
            ) from exc

        return text.splitlines(
            keepends=True
        )

    def render(
        self,
        first: Path,
        second: Path,
    ) -> None:

        if not first.exists():

            raise RuntimeError(
                f"فایل اول وجود ندارد:\n{first}"
            )

        if not second.exists():

            raise RuntimeError(
                f"فایل دوم وجود ندارد:\n{second}"
            )

        if not first.is_file():

            raise RuntimeError(
                f"مسیر اول فایل نیست:\n{first}"
            )

        if not second.is_file():

            raise RuntimeError(
                f"مسیر دوم فایل نیست:\n{second}"
            )

        left_lines = self._read_file(
            first
        )

        right_lines = self._read_file(
            second
        )

        diff = difflib.unified_diff(
            left_lines,
            right_lines,

            fromfile=str(first),
            tofile=str(second),

            lineterm="",
        )

        diff_lines = list(
            diff
        )

        if not diff_lines:

            print(
                "✅ دو فایل یکسان هستند."
            )

            return

        print(
            f"🔎 Diff:"
        )

        print(
            f"  A: {first}"
        )

        print(
            f"  B: {second}"
        )

        print()

        for line in diff_lines:

            clean = line.rstrip(
                "\n"
            )

            if clean.startswith(
                "+++"
            ) or clean.startswith(
                "---"
            ):

                print(
                    colorize(
                        clean,
                        Colors.BLUE,
                        self.use_color,
                    )
                )

            elif clean.startswith(
                "@@"
            ):

                print(
                    colorize(
                        clean,
                        Colors.CYAN,
                        self.use_color,
                    )
                )

            elif clean.startswith(
                "+"
            ):

                print(
                    colorize(
                        clean,
                        Colors.GREEN,
                        self.use_color,
                    )
                )

            elif clean.startswith(
                "-"
            ):

                print(
                    colorize(
                        clean,
                        Colors.RED,
                        self.use_color,
                    )
                )

            else:

                print(
                    clean
                )


# ============================================================
# Quick Check
# ============================================================

def print_quick_check(
    status: ProjectStatus,
) -> None:

    print(
        "📋 خلاصه سریع وضعیت نظم داد"
    )

    print(
        f"  Tool: v{VERSION}"
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
        f"  Commit: "
        f"{status.git.last_commit_hash}"
        f" — "
        f"{status.git.last_commit_message}"
    )

    if status.git.upstream:

        print(
            f"  Upstream branch: "
            f"{status.git.upstream}"
        )

    print(
        "  Relation: "
        f"{status.git.ahead} ahead / "
        f"{status.git.behind} behind"
    )

    if status.git.tags:

        print(
            "  Tags: "
            + ", ".join(
                status.git.tags
            )
        )

    invalid: List[str] = []
    placeholders: List[str] = []
    missing_docs: List[str] = []

    for (
        name,
        doc,
    ) in status.documents.items():

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

            missing_docs.append(
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

    if missing_docs:

        print(
            "  🚫 Missing: "
            + ", ".join(
                missing_docs
            )
        )

    if (
        not invalid
        and not placeholders
        and not missing_docs
    ):

        print(
            "  Documents: ✅ all valid"
        )

    missing_articles = (
        get_missing_v05_articles(
            status
        )
    )

    if missing_articles:

        print(
            "  ❌ Missing 0.5 additional articles: "
            + ", ".join(
                missing_articles
            )
        )

    else:

        print(
            "  12 additional articles: ✅ all present"
        )


# ============================================================
# Document Validation Output
# ============================================================

def print_document_validation(
    status: ProjectStatus,
) -> None:

    print(
        "🔍 اعتبارسنجی اسناد"
    )

    print()

    for (
        name,
        doc,
    ) in status.documents.items():

        print(
            f"{document_icon(doc.status)} "
            f"{name}"
        )

        print(
            f"   وضعیت: "
            f"{doc.status.value}"
        )

        print(
            f"   مسیر: "
            f"{doc.path}"
        )

        print(
            f"   اندازه: "
            f"{doc.size_bytes} bytes"
        )

        print(
            f"   خطوط: "
            f"{doc.lines}"
        )

        if (
            doc.articles_count
            is not None
        ):

            print(
                f"   مواد: "
                f"{doc.detected_articles}/"
                f"{doc.articles_count}"
            )

        if doc.duplicate_articles:

            print(
                "   مواد تکراری: "
                + ", ".join(
                    doc.duplicate_articles
                )
            )

        if doc.missing_additional:

            print(
                "   مواد الحاقی مفقود: "
                + ", ".join(
                    doc.missing_additional
                )
            )

        if doc.description:

            print(
                f"   توضیح: "
                f"{doc.description}"
            )

        print()

    missing = (
        get_missing_v05_articles(
            status
        )
    )

    print(
        "مواد مستقل افزوده‌شده در ۰.۵:"
    )

    if missing:

        print(
            "❌ مفقود: "
            + ", ".join(
                missing
            )
        )

    else:

        print(
            "✅ هر ۱۲ ماده موجود است."
        )


# ============================================================
# Output Directory
# ============================================================

def resolve_output_dir(
    repo_path: Path,
    output_dir_arg: Optional[str],
) -> Path:

    if not output_dir_arg:

        return repo_path

    raw = Path(
        output_dir_arg
    ).expanduser()

    if raw.is_absolute():

        output_dir = raw.resolve()

    else:

        output_dir = (
            repo_path
            / raw
        ).resolve()

    return output_dir


def ensure_output_dir(
    output_dir: Path,
    logger: VerboseLogger,
) -> None:

    logger.log(
        f"Output directory: {output_dir}"
    )

    try:

        output_dir.mkdir(
            parents=True,
            exist_ok=True,
        )

    except OSError as exc:

        raise RuntimeError(
            f"امکان ایجاد مسیر خروجی وجود ندارد:\n"
            f"{output_dir}\n"
            f"{exc}"
        ) from exc


# ============================================================
# CLI
# ============================================================

def parse_args() -> argparse.Namespace:

    parser = argparse.ArgumentParser(

        description=(
            "ابزار وضعیت، اعتبارسنجی، "
            "تاریخچه و مقایسه پروژه نظم داد"
        ),

        epilog=(
            "Git، validation، history و diff فقط خواندنی‌اند. "
            "فقط --markdown و --json فایل خروجی می‌نویسند."
        ),

    )

    parser.add_argument(
        "--version",

        action="version",

        version=(
            f"%(prog)s {VERSION}"
        ),
    )

    parser.add_argument(
        "--path",

        type=str,

        default=".",

        help=(
            "مسیر مخزن Git "
            "(پیش‌فرض: پوشه فعلی)"
        ),
    )

    parser.add_argument(
        "--output-dir",

        type=str,

        default=None,

        help=(
            "مسیر ذخیره STATUS.md یا status.json. "
            "برای --markdown و --json کاربرد دارد."
        ),
    )

    parser.add_argument(
        "--verbose",

        action="store_true",

        help=(
            "نمایش جزئیات بیشتر درباره مراحل اجرا"
        ),
    )

    parser.add_argument(
        "--no-color",

        action="store_true",

        help=(
            "غیرفعال کردن رنگ ANSI در خروجی diff"
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
            "نمایش خلاصه سریع وضعیت"
        ),
    )

    mode.add_argument(
        "--validate-docs",

        action="store_true",

        help=(
            "اعتبارسنجی اسناد پروژه"
        ),
    )

    mode.add_argument(
        "--history",

        action="store_true",

        help=(
            "نمایش تاریخچه Tagها و commit مرتبط"
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
        "--diff",

        nargs=2,

        metavar=(
            "FILE_A",
            "FILE_B",
        ),

        help=(
            "مقایسه دو فایل متنی"
        ),
    )

    return parser.parse_args()


# ============================================================
# Main
# ============================================================

def main() -> None:

    args = parse_args()

    logger = VerboseLogger(
        args.verbose
    )

    repo_path = (
        Path(
            args.path
        )
        .expanduser()
        .resolve()
    )

    logger.log(
        f"Requested repository path: {repo_path}"
    )

    if not repo_path.exists():

        print(
            "❌ مسیر وجود ندارد:\n"
            f"{repo_path}",
            file=sys.stderr,
        )

        sys.exit(1)

    if not repo_path.is_dir():

        print(
            "❌ مسیر باید یک پوشه باشد:\n"
            f"{repo_path}",
            file=sys.stderr,
        )

        sys.exit(1)

    # --------------------------------------------------------
    # Diff
    # --------------------------------------------------------

    if args.diff:

        first_arg = Path(
            args.diff[0]
        ).expanduser()

        second_arg = Path(
            args.diff[1]
        ).expanduser()

        first = (
            first_arg.resolve()
            if first_arg.is_absolute()
            else (
                repo_path
                / first_arg
            ).resolve()
        )

        second = (
            second_arg.resolve()
            if second_arg.is_absolute()
            else (
                repo_path
                / second_arg
            ).resolve()
        )

        logger.log(
            f"Diff first file: {first}"
        )

        logger.log(
            f"Diff second file: {second}"
        )

        try:

            FileDiffRenderer(
                use_color=(
                    not args.no_color
                )
            ).render(
                first,
                second,
            )

        except RuntimeError as exc:

            print(
                f"❌ خطا:\n{exc}",
                file=sys.stderr,
            )

            sys.exit(1)

        return

    # --------------------------------------------------------
    # Git-only History
    # --------------------------------------------------------

    if args.history:

        try:

            collector = (
                GitInfoCollector(
                    repo_path,
                    logger=logger,
                )
            )

            history = (
                collector
                .get_tag_history()
            )

        except RuntimeError as exc:

            print(
                f"❌ خطا:\n{exc}",
                file=sys.stderr,
            )

            sys.exit(1)

        HistoryRenderer().render(
            history
        )

        return

    # --------------------------------------------------------
    # Build project status
    # --------------------------------------------------------

    try:

        builder = (
            ProjectStatusBuilder(
                repo_path,
                logger=logger,
            )
        )

        status = (
            builder.build()
        )

    except RuntimeError as exc:

        print(
            f"❌ خطا:\n{exc}",
            file=sys.stderr,
        )

        sys.exit(1)

    # --------------------------------------------------------
    # Validate
    # --------------------------------------------------------

    if args.validate_docs:

        print_document_validation(
            status
        )

        return

    # --------------------------------------------------------
    # Markdown
    # --------------------------------------------------------

    if args.markdown:

        output_dir = (
            resolve_output_dir(
                repo_path,
                args.output_dir,
            )
        )

        try:

            ensure_output_dir(
                output_dir,
                logger,
            )

            output_path = (
                output_dir
                / "STATUS.md"
            )

            content = (
                MarkdownRenderer()
                .render(
                    status
                )
            )

            logger.log(
                f"Writing Markdown to {output_path}"
            )

            output_path.write_text(
                content + "\n",
                encoding="utf-8",
            )

        except OSError as exc:

            print(
                f"❌ خطا هنگام نوشتن فایل:\n"
                f"{exc}",
                file=sys.stderr,
            )

            sys.exit(1)

        except RuntimeError as exc:

            print(
                f"❌ خطا:\n{exc}",
                file=sys.stderr,
            )

            sys.exit(1)

        print(
            "✅ STATUS.md ساخته شد:"
        )

        print(
            output_path
        )

        return

    # --------------------------------------------------------
    # JSON
    # --------------------------------------------------------

    if args.json:

        output_dir = (
            resolve_output_dir(
                repo_path,
                args.output_dir,
            )
        )

        try:

            ensure_output_dir(
                output_dir,
                logger,
            )

            output_path = (
                output_dir
                / "status.json"
            )

            content = (
                JsonRenderer()
                .render(
                    status
                )
            )

            logger.log(
                f"Writing JSON to {output_path}"
            )

            output_path.write_text(
                content + "\n",
                encoding="utf-8",
            )

        except OSError as exc:

            print(
                f"❌ خطا هنگام نوشتن فایل:\n"
                f"{exc}",
                file=sys.stderr,
            )

            sys.exit(1)

        except RuntimeError as exc:

            print(
                f"❌ خطا:\n{exc}",
                file=sys.stderr,
            )

            sys.exit(1)

        print(
            "✅ status.json ساخته شد:"
        )

        print(
            output_path
        )

        return

    # --------------------------------------------------------
    # Quick Check
    # --------------------------------------------------------

    if args.check:

        print_quick_check(
            status
        )

        return

    # --------------------------------------------------------
    # Full report
    # --------------------------------------------------------

    ConsoleRenderer().render(
        status
    )


# ============================================================
# Entry Point
# ============================================================

if __name__ == "__main__":
    main()