#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
Nazm Dad — Project Status & Documentation Tool
نظم داد — ابزار وضعیت، اعتبارسنجی، تعمیر و مستندسازی پروژه

Version: 2.6

ویژگی‌های اصلی
--------------
- پشتیبانی از config:
    CLI > config file > defaults
- بررسی Git و upstream
- اعتبارسنجی اسناد
- تشخیص فایل‌های آلوده/مخلوط‌شده
- تشخیص Python code داخل Markdown
- تشخیص فایل‌های خلاصه‌شده به‌جای متن authoritative
- بررسی شماره‌گذاری مواد
- بررسی مواد الحاقی نسخه ۰.۵
- بررسی لینک‌های Markdown و ارجاعات مواد
- Health Score
- خروجی Markdown / JSON / HTML
- مقایسه فایل‌ها
- مقایسه دو Git ref
- تعمیر محافظه‌کار اسناد از Git ref یا source directory
- حالت watch
- گزارش پیشرفت اختیاری

نمونه‌ها
--------
python nazm_dad_project_status.py --check
python nazm_dad_project_status.py --validate-docs
python nazm_dad_project_status.py --health
python nazm_dad_project_status.py --check-links
python nazm_dad_project_status.py --html

python nazm_dad_project_status.py \
    --compare-refs v0.2 main \
    --compare-path docs/0.5.md

python nazm_dad_project_status.py \
    --repair \
    --repair-from-ref v0.2 \
    --repair-files docs/changelog.md

python nazm_dad_project_status.py \
    --repair \
    --repair-source-dir C:\\backup\\nazm-dad \
    --repair-files docs/0.4.md docs/changelog.md

python nazm_dad_project_status.py --watch
"""

from __future__ import annotations

import argparse
import difflib
import hashlib
import html as html_lib
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
from dataclasses import asdict, dataclass, field
from datetime import datetime
from enum import Enum
from pathlib import Path
from typing import (
    Any,
    Dict,
    Iterable,
    List,
    Optional,
    Sequence,
    Set,
    Tuple,
)


# ============================================================
# Version
# ============================================================

TOOL_VERSION = "2.6"


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
    PLACEHOLDER = "placeholder / خلاصه / موقت"
    CONTAMINATED = "آلوده یا مخلوط با محتوای نامرتبط"
    INVALID = "نامعتبر"
    MISSING = "وجود ندارد"


class LinkStatus(str, Enum):
    OK = "ok"
    BROKEN = "broken"
    SKIPPED = "skipped"


class Severity(str, Enum):
    INFO = "info"
    WARNING = "warning"
    ERROR = "error"


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

    watch_interval: float = 2.0

    min_size_v04: int = 5000
    min_size_v05: int = 5000
    min_size_changelog: int = 1000
    min_size_rules: int = 500
    min_size_decisions: int = 500

    detect_python_contamination: bool = True

    @classmethod
    def from_file(cls, path: Path) -> "ProjectConfig":
        if not path.exists():
            return cls()

        try:
            raw = path.read_text(encoding="utf-8")
            data = json.loads(raw)

            if not isinstance(data, dict):
                raise TypeError(
                    "ریشه config باید JSON object باشد."
                )

            valid_keys = set(cls.__dataclass_fields__.keys())

            filtered = {
                key: value
                for key, value in data.items()
                if key in valid_keys
            }

            config = cls(**filtered)
            config.validate()
            return config

        except (
            OSError,
            json.JSONDecodeError,
            TypeError,
            ValueError,
        ) as exc:
            print(
                f"⚠️ خطا در config '{path}': {exc}",
                file=sys.stderr,
            )
            print(
                "⚠️ استفاده از تنظیمات پیش‌فرض.",
                file=sys.stderr,
            )
            return cls()

    def validate(self) -> None:
        numeric_positive = {
            "expected_articles_v04": self.expected_articles_v04,
            "expected_articles_v05": self.expected_articles_v05,
            "health_threshold_ok": self.health_threshold_ok,
            "min_size_v04": self.min_size_v04,
            "min_size_v05": self.min_size_v05,
        }

        for name, value in numeric_positive.items():
            if not isinstance(value, int) or value < 0:
                raise ValueError(
                    f"{name} باید integer غیرمنفی باشد."
                )

        if not isinstance(
            self.expected_v05_additional,
            list,
        ):
            raise ValueError(
                "expected_v05_additional باید list باشد."
            )

        if self.watch_interval <= 0:
            raise ValueError(
                "watch_interval باید بزرگ‌تر از صفر باشد."
            )

    def to_dict(self) -> Dict[str, Any]:
        return {
            key: value
            for key, value in asdict(self).items()
            if value is not None
        }


# ============================================================
# Generic Helpers
# ============================================================

def html_escape(value: Any) -> str:
    return html_lib.escape(str(value), quote=True)


def normalize_slashes(value: str) -> str:
    return value.replace("\\", "/")


def safe_display_path(
    path: Path,
    base: Path,
) -> str:
    try:
        return str(path.resolve().relative_to(base.resolve()))
    except ValueError:
        return str(path.resolve())


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()

    with path.open("rb") as handle:
        while True:
            chunk = handle.read(1024 * 1024)

            if not chunk:
                break

            digest.update(chunk)

    return digest.hexdigest()


def ensure_parent(path: Path) -> None:
    path.parent.mkdir(
        parents=True,
        exist_ok=True,
    )


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
                )

            except ImportError:
                self._tqdm = None
                print(
                    f"{desc}: 0/{total}",
                    end="",
                    flush=True,
                )

    def update(self, n: int = 1) -> None:
        self.current += n

        if self._tqdm:
            self._tqdm.update(n)

        elif self.enabled:
            print(
                f"\r{self.desc}: "
                f"{self.current}/{self.total}",
                end="",
                flush=True,
            )

    def close(self) -> None:
        if self._tqdm:
            self._tqdm.close()

        elif self.enabled:
            print()

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        self.close()


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
class ContaminationIssue:
    severity: Severity
    kind: str
    line: int
    sample: str
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

    duplicate_articles: List[str] = field(
        default_factory=list
    )

    out_of_order_articles: List[str] = field(
        default_factory=list
    )

    has_continuity: bool = True

    description: str = ""
    is_placeholder: bool = False
    contamination: List[ContaminationIssue] = field(
        default_factory=list
    )


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

    total_articles_v04: int
    total_articles_v05: int

    expected_v05_additional: Set[str]

    total_changes: int
    noop_changes: int

    tool_version: str = TOOL_VERSION

    timestamp: str = field(
        default_factory=lambda: (
            datetime.now()
            .astimezone()
            .isoformat()
        )
    )


@dataclass
class RefComparison:
    left_ref: str
    right_ref: str
    path: str
    changed: bool
    diff: str


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

        if os.name == "nt" and self.enable_color:
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


# ============================================================
# Logger
# ============================================================

class VerboseLogger:

    def __init__(
        self,
        enabled: bool = False,
    ):
        self.enabled = enabled

    def log(self, message: str) -> None:
        if self.enabled:
            print(f"[verbose] {message}")


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
        self.logger = logger or VerboseLogger(False)

    def _run_git(
        self,
        args: Sequence[str],
        check: bool = True,
        text: bool = True,
    ) -> subprocess.CompletedProcess:

        cmd = [
            "git",
            "-C",
            str(self.repo_path),
            *args,
        ]

        self.logger.log(
            "Git: " + " ".join(cmd)
        )

        try:
            return subprocess.run(
                cmd,
                capture_output=True,
                text=text,
                encoding="utf-8" if text else None,
                errors="replace" if text else None,
                check=check,
            )

        except FileNotFoundError as exc:
            raise RuntimeError(
                "Git در PATH سیستم پیدا نشد."
            ) from exc

        except subprocess.CalledProcessError as exc:
            if not check:
                return exc

            stderr = ""

            if exc.stderr:
                stderr = (
                    exc.stderr.decode(
                        "utf-8",
                        errors="replace",
                    )
                    if isinstance(
                        exc.stderr,
                        bytes,
                    )
                    else str(exc.stderr)
                )

            raise RuntimeError(
                "Git command failed:\n"
                + " ".join(cmd)
                + (
                    f"\n{stderr.strip()}"
                    if stderr
                    else ""
                )
            ) from exc

    def check_repository(self) -> None:
        result = self._run_git(
            [
                "rev-parse",
                "--is-inside-work-tree",
            ],
            check=False,
        )

        if (
            result.returncode != 0
            or result.stdout.strip().lower()
            != "true"
        ):
            raise RuntimeError(
                f"'{self.repo_path}' "
                "مخزن Git معتبر نیست."
            )

    def get_repo_root(self) -> Path:
        result = self._run_git(
            [
                "rev-parse",
                "--show-toplevel",
            ]
        )

        return Path(
            result.stdout.strip()
        ).resolve()

    def get_upstream(self) -> Optional[str]:
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

        values = result.stdout.split()

        if len(values) != 2:
            return 0, 0

        try:
            return (
                int(values[0]),
                int(values[1]),
            )

        except ValueError:
            return 0, 0

    def ref_exists(self, ref: str) -> bool:
        result = self._run_git(
            [
                "rev-parse",
                "--verify",
                f"{ref}^{{commit}}",
            ],
            check=False,
        )

        return result.returncode == 0

    def show_file(
        self,
        ref: str,
        relative_path: str,
    ) -> Optional[str]:

        relative_path = normalize_slashes(
            relative_path
        )

        result = self._run_git(
            [
                "show",
                f"{ref}:{relative_path}",
            ],
            check=False,
        )

        if result.returncode != 0:
            return None

        return result.stdout

    def get_info(self) -> GitInfo:
        self.check_repository()

        repo_root = self.get_repo_root()

        branch = self._run_git(
            [
                "rev-parse",
                "--abbrev-ref",
                "HEAD",
            ]
        ).stdout.strip()

        status = self._run_git(
            [
                "status",
                "--porcelain",
            ]
        ).stdout

        commit_lines = self._run_git(
            [
                "log",
                "-1",
                "--format=%H%n%s%n%ai",
            ]
        ).stdout.rstrip().splitlines()

        full_hash = (
            commit_lines[0]
            if len(commit_lines) >= 1
            else ""
        )

        message = (
            commit_lines[1]
            if len(commit_lines) >= 2
            else ""
        )

        date = (
            commit_lines[2]
            if len(commit_lines) >= 3
            else ""
        )

        tags = [
            line.strip()
            for line in self._run_git(
                [
                    "tag",
                    "--list",
                    "--sort=-creatordate",
                ]
            ).stdout.splitlines()
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
            is_clean=not bool(
                status.strip()
            ),
            last_commit_hash=full_hash[:8],
            last_commit_message=message,
            last_commit_date=date,
            tags=tags,
            ahead=ahead,
            behind=behind,
            upstream=upstream,
            remote=remote or None,
            repository_root=str(repo_root),
        )

    def get_tags_history(
        self,
    ) -> List[TagInfo]:

        tags = self._run_git(
            [
                "tag",
                "--list",
                "--sort=-creatordate",
            ]
        ).stdout.splitlines()

        result: List[TagInfo] = []

        for tag in tags:
            tag = tag.strip()

            if not tag:
                continue

            log = self._run_git(
                [
                    "log",
                    "-1",
                    "--format=%h%n%ai%n%s",
                    tag,
                ],
                check=False,
            ).stdout.splitlines()

            tagger = self._run_git(
                [
                    "for-each-ref",
                    f"refs/tags/{tag}",
                    "--format=%(taggername) "
                    "<%(taggeremail)>",
                ],
                check=False,
            ).stdout.strip()

            result.append(
                TagInfo(
                    name=tag,
                    commit=(
                        log[0]
                        if len(log) > 0
                        else ""
                    ),
                    date=(
                        log[1]
                        if len(log) > 1
                        else ""
                    ),
                    message=(
                        log[2]
                        if len(log) > 2
                        else ""
                    ),
                    tagger=tagger,
                )
            )

        return result


# ============================================================
# Contamination Detector
# ============================================================

class ContaminationDetector:
    """
    تشخیص نشانه‌های واضح آلودگی Markdown با کد یا خروجی shell.

    هدف این کلاس اثبات خراب بودن فایل نیست؛
    بلکه پیدا کردن نشانه‌های قوی برای بازبینی انسانی است.
    """

    PYTHON_PATTERNS = (
        re.compile(r"^\s*import\s+\w+"),
        re.compile(r"^\s*from\s+\w+\s+import\s+"),
        re.compile(r"^\s*class\s+\w+"),
        re.compile(r"^\s*def\s+\w+\s*\("),
        re.compile(
            r"^\s*if\s+__name__\s*==\s*['\"]__main__['\"]"
        ),
        re.compile(r"^\s*@dataclass\b"),
        re.compile(r"^\s*sys\.exit\s*\("),
        re.compile(r"^\s*argparse\.ArgumentParser\s*\("),
    )

    POWERSHELL_PATTERNS = (
        re.compile(
            r"^\s*PS\s+[A-Za-z]:\\",
            re.IGNORECASE,
        ),
        re.compile(
            r"^\s*Get-Content\b",
            re.IGNORECASE,
        ),
        re.compile(
            r"^\s*Write-Host\b",
            re.IGNORECASE,
        ),
    )

    GIT_OUTPUT_PATTERNS = (
        re.compile(
            r"^\s*On branch\s+",
            re.IGNORECASE,
        ),
        re.compile(
            r"^\s*nothing to commit",
            re.IGNORECASE,
        ),
        re.compile(
            r"^\s*Changes to be committed:",
            re.IGNORECASE,
        ),
    )

    def detect(
        self,
        content: str,
    ) -> List[ContaminationIssue]:

        issues: List[ContaminationIssue] = []

        in_fenced_code = False

        for line_number, line in enumerate(
            content.splitlines(),
            start=1,
        ):
            stripped = line.strip()

            if stripped.startswith("```"):
                in_fenced_code = (
                    not in_fenced_code
                )
                continue

            # کد داخل fenced block الزاماً آلودگی نیست.
            if in_fenced_code:
                continue

            for pattern in self.PYTHON_PATTERNS:
                if pattern.search(line):
                    issues.append(
                        ContaminationIssue(
                            severity=Severity.ERROR,
                            kind="python-code",
                            line=line_number,
                            sample=stripped[:120],
                            description=(
                                "نشانه کد Python "
                                "در متن Markdown"
                            ),
                        )
                    )
                    break

            for pattern in self.POWERSHELL_PATTERNS:
                if pattern.search(line):
                    issues.append(
                        ContaminationIssue(
                            severity=Severity.WARNING,
                            kind="powershell-output",
                            line=line_number,
                            sample=stripped[:120],
                            description=(
                                "نشانه PowerShell "
                                "در سند"
                            ),
                        )
                    )
                    break

            for pattern in self.GIT_OUTPUT_PATTERNS:
                if pattern.search(line):
                    issues.append(
                        ContaminationIssue(
                            severity=Severity.WARNING,
                            kind="git-output",
                            line=line_number,
                            sample=stripped[:120],
                            description=(
                                "نشانه خروجی Git "
                                "در سند"
                            ),
                        )
                    )
                    break

        return issues


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
        "متن کامل",
        "نسخه اصلی موجود است",
        "برای نمایش در وب‌سایت خلاصه شده",
        "پایان خلاصه",
    )

    def __init__(
        self,
        docs_path: Path,
        logger: Optional[VerboseLogger] = None,
        config: Optional[ProjectConfig] = None,
    ):
        self.docs_path = docs_path.resolve()
        self.logger = logger or VerboseLogger(False)
        self.config = config or ProjectConfig()
        self.contamination_detector = (
            ContaminationDetector()
        )

    @staticmethod
    def normalize_digits(
        value: str,
    ) -> str:

        translation = str.maketrans(
            "۰۱۲۳۴۵۶۷۸۹٠١٢٣٤٥٦٧٨٩",
            "01234567890123456789",
        )

        return value.translate(
            translation
        )

    @classmethod
    def normalize_article_id(
        cls,
        value: str,
    ) -> str:

        value = cls.normalize_digits(
            value
        )

        return (
            value
            .replace("–", "-")
            .replace("—", "-")
            .replace("−", "-")
            .strip()
        )

    def find_articles(
        self,
        content: str,
    ) -> List[str]:

        return [
            self.normalize_article_id(
                match.group(1)
            )
            for match in self.ARTICLE_PATTERN.finditer(
                content
            )
        ]

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

    @staticmethod
    def article_sort_key(
        article_id: str,
    ) -> Tuple[int, int]:

        parts = article_id.split(
            "-",
            1,
        )

        try:
            base = int(parts[0])
        except ValueError:
            return (
                10**9,
                10**9,
            )

        sub = 0

        if len(parts) == 2:
            try:
                sub = int(parts[1])
            except ValueError:
                sub = 10**9

        return base, sub

    def article_stats(
        self,
        ids: List[str],
        expected_count: int = 0,
        expected_special: Optional[
            Set[str]
        ] = None,
    ) -> ArticleStats:

        duplicates = (
            self.find_duplicates(ids)
        )

        missing = []

        if expected_special:
            missing = sorted(
                expected_special
                - set(ids),
                key=self.article_sort_key,
            )

        out_of_order: List[str] = []

        for index in range(
            1,
            len(ids),
        ):
            previous = (
                self.article_sort_key(
                    ids[index - 1]
                )
            )

            current = (
                self.article_sort_key(
                    ids[index]
                )
            )

            if current < previous:
                out_of_order.append(
                    ids[index]
                )

        # پیوستگی فقط روی مواد پایه بررسی می‌شود.
        base_numbers: Set[int] = set()

        for article_id in ids:
            try:
                base_numbers.add(
                    int(
                        article_id.split(
                            "-",
                            1,
                        )[0]
                    )
                )
            except ValueError:
                pass

        has_continuity = True

        if base_numbers:
            minimum = min(
                base_numbers
            )

            maximum = max(
                base_numbers
            )

            expected_range = set(
                range(
                    minimum,
                    maximum + 1,
                )
            )

            has_continuity = (
                expected_range
                == base_numbers
            )

        return ArticleStats(
            total_expected=expected_count,
            total_detected=len(ids),
            ids=ids,
            missing=missing,
            duplicates=duplicates,
            out_of_order=out_of_order,
            has_continuity=has_continuity,
        )

    def _get_doc_specs(
        self,
    ) -> Dict[str, Dict[str, Any]]:

        return {
            "0.4.md": {
                "min_size": (
                    self.config.min_size_v04
                ),
                "articles": (
                    self.config.expected_articles_v04
                ),
                "additional": set(),
                "must_be_full": True,
            },

            "0.5.md": {
                "min_size": (
                    self.config.min_size_v05
                ),
                "articles": (
                    self.config.expected_articles_v05
                ),
                "additional": set(
                    self.config.expected_v05_additional
                ),
                "must_be_full": True,
            },

            "changelog.md": {
                "min_size": (
                    self.config.min_size_changelog
                ),
                "articles": None,
                "additional": set(),
                "must_be_full": False,
            },

            "rules.md": {
                "min_size": (
                    self.config.min_size_rules
                ),
                "articles": None,
                "additional": set(),
                "must_be_full": False,
            },

            "decisions.md": {
                "min_size": (
                    self.config.min_size_decisions
                ),
                "articles": None,
                "additional": set(),
                "must_be_full": False,
            },
        }

    def validate_all(
        self,
    ) -> Dict[str, DocumentInfo]:

        result: Dict[str, DocumentInfo] = {}

        for (
            filename,
            spec,
        ) in self._get_doc_specs().items():

            result[filename] = (
                self.validate_single(
                    self.docs_path
                    / filename,
                    filename,
                    spec,
                )
            )

        return result

    def validate_single(
        self,
        file_path: Path,
        filename: str,
        spec: Dict[str, Any],
    ) -> DocumentInfo:

        if not file_path.exists():
            return DocumentInfo(
                path=str(file_path),
                status=DocumentStatus.MISSING,
                size_bytes=0,
                description=(
                    f"{filename} وجود ندارد."
                ),
            )

        try:
            content = file_path.read_text(
                encoding="utf-8"
            )

        except UnicodeDecodeError as exc:
            return DocumentInfo(
                path=str(file_path),
                status=DocumentStatus.INVALID,
                size_bytes=(
                    file_path.stat().st_size
                ),
                description=(
                    f"UTF-8 نامعتبر: {exc}"
                ),
            )

        except OSError as exc:
            return DocumentInfo(
                path=str(file_path),
                status=DocumentStatus.INVALID,
                size_bytes=0,
                description=(
                    f"خطای خواندن: {exc}"
                ),
            )

        size_bytes = (
            file_path.stat().st_size
        )

        lines = len(
            content.splitlines()
        )

        characters = len(content)

        contamination: List[
            ContaminationIssue
        ] = []

        if (
            self.config
            .detect_python_contamination
        ):
            contamination = (
                self.contamination_detector
                .detect(content)
            )

        expected_count = spec.get(
            "articles"
        )

        article_ids = (
            self.find_articles(content)
            if expected_count is not None
            else []
        )

        stats = self.article_stats(
            article_ids,
            expected_count or 0,
            spec.get("additional"),
        )

        lower = content.lower()

        placeholder_marker_found = any(
            marker.lower() in lower
            for marker
            in self.PLACEHOLDER_MARKERS
        )

        undersized = (
            size_bytes
            < int(
                spec.get(
                    "min_size",
                    0,
                )
            )
        )

        must_be_full = bool(
            spec.get(
                "must_be_full",
                False,
            )
        )

        article_mismatch = (
            expected_count is not None
            and len(article_ids)
            != expected_count
        )

        errors = [
            issue
            for issue in contamination
            if issue.severity
            == Severity.ERROR
        ]

        is_placeholder = (
            undersized
            or (
                must_be_full
                and placeholder_marker_found
            )
        )

        problems: List[str] = []

        if undersized:
            problems.append(
                f"اندازه کمتر از حد مورد انتظار "
                f"({size_bytes} bytes)"
            )

        if (
            must_be_full
            and placeholder_marker_found
        ):
            problems.append(
                "نشانه خلاصه/placeholder "
                "در سند authoritative"
            )

        if article_mismatch:
            problems.append(
                f"مواد شناسایی‌شده: "
                f"{len(article_ids)}/"
                f"{expected_count}"
            )

        if stats.missing:
            problems.append(
                "مواد الحاقی مفقود: "
                + ", ".join(
                    stats.missing
                )
            )

        if stats.duplicates:
            problems.append(
                "مواد تکراری: "
                + ", ".join(
                    stats.duplicates
                )
            )

        if stats.out_of_order:
            problems.append(
                "مواد خارج از ترتیب: "
                + ", ".join(
                    stats.out_of_order
                )
            )

        if contamination:
            problems.append(
                f"{len(contamination)} "
                "نشانه محتوای نامرتبط"
            )

        if errors:
            status = (
                DocumentStatus.CONTAMINATED
            )

        elif is_placeholder:
            status = (
                DocumentStatus.PLACEHOLDER
            )

        elif (
            article_mismatch
            or stats.missing
            or stats.duplicates
            or stats.out_of_order
        ):
            status = (
                DocumentStatus.INVALID
            )

        else:
            status = (
                DocumentStatus.COMPLETE
            )

        description = (
            "فایل معتبر است."
            if status
            == DocumentStatus.COMPLETE
            else " | ".join(problems)
        )

        return DocumentInfo(
            path=str(file_path),
            status=status,
            size_bytes=size_bytes,
            lines=lines,
            characters=characters,
            articles_count=(
                expected_count
            ),
            detected_articles=(
                len(article_ids)
                if expected_count
                is not None
                else None
            ),
            detected_article_ids=(
                article_ids
            ),
            missing_articles=(
                stats.missing
            ),
            duplicate_articles=(
                stats.duplicates
            ),
            out_of_order_articles=(
                stats.out_of_order
            ),
            has_continuity=(
                stats.has_continuity
            ),
            description=description,
            is_placeholder=(
                is_placeholder
            ),
            contamination=(
                contamination
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
        logger: Optional[
            VerboseLogger
        ] = None,
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

        value = target.lower().strip()

        return value.startswith(
            (
                "http://",
                "https://",
                "mailto:",
                "tel:",
                "data:",
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
            lines = file_path.read_text(
                encoding="utf-8"
            ).splitlines()

        except (
            OSError,
            UnicodeDecodeError,
        ):
            return issues

        for line_number, line in enumerate(
            lines,
            start=1,
        ):
            for match in (
                self.MARKDOWN_LINK_PATTERN
                .finditer(line)
            ):
                target = (
                    match.group(1)
                    .strip()
                )

                if self._is_external(
                    target
                ):
                    continue

                clean = self._strip_anchor(
                    target
                )

                if not clean:
                    continue

                resolved = (
                    file_path.parent
                    / clean
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
                                "فایل مقصد "
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
                file_path.read_text(
                    encoding="utf-8"
                )
            )

        except (
            OSError,
            UnicodeDecodeError,
        ):
            return issues

        ids = set(
            self.validator
            .find_articles(content)
        )

        if not ids:
            return issues

        for line_number, line in enumerate(
            content.splitlines(),
            start=1,
        ):
            for match in (
                self.ARTICLE_REFERENCE_PATTERN
                .finditer(line)
            ):
                article_id = (
                    self.validator
                    .normalize_article_id(
                        match.group(1)
                    )
                )

                if article_id not in ids:
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
                                "ارجاع به ماده‌ای "
                                "که در سند "
                                "شناسایی نشد"
                            ),
                        )
                    )

        return issues

    def run(self) -> LinkReport:

        report = LinkReport()

        if not self.docs_path.exists():
            return report

        markdown_files = sorted(
            self.docs_path.rglob(
                "*.md"
            )
        )

        special_files = [
            self.docs_path / "0.4.md",
            self.docs_path / "0.5.md",
        ]

        total = (
            len(markdown_files)
            + len(special_files)
        )

        with ProgressReporter(
            enabled=(
                self.progress_enabled
            ),
            desc="Checking links",
            total=total,
        ) as progress:

            for file_path in markdown_files:
                issues = (
                    self.check_markdown_links(
                        file_path
                    )
                )

                report.checked += 1

                if issues:
                    report.broken += len(
                        issues
                    )

                    report.issues.extend(
                        issues
                    )

                else:
                    report.valid += 1

                progress.update()

            for file_path in special_files:

                if not file_path.exists():
                    report.skipped += 1
                    progress.update()
                    continue

                issues = (
                    self.check_article_references(
                        file_path
                    )
                )

                report.checked += 1

                if issues:
                    report.broken += len(
                        issues
                    )

                    report.issues.extend(
                        issues
                    )

                else:
                    report.valid += 1

                progress.update()

        return report


# ============================================================
# Project Builder
# ============================================================

class ProjectStatusBuilder:

    def __init__(
        self,
        repo_path: Path,
        logger: Optional[
            VerboseLogger
        ] = None,
        config: Optional[
            ProjectConfig
        ] = None,
    ):
        self.logger = (
            logger
            or VerboseLogger(False)
        )

        self.config = (
            config
            or ProjectConfig()
        )

        initial = (
            repo_path.resolve()
        )

        first_collector = (
            GitInfoCollector(
                initial,
                self.logger,
            )
        )

        first_collector.check_repository()

        self.repo_path = (
            first_collector
            .get_repo_root()
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
            configured = Path(
                self.config.docs_path
            ).expanduser()

            docs_path = (
                configured
                if configured.is_absolute()
                else self.repo_path
                / configured
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

        return ProjectStatus(
            git=git,
            documents=documents,
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


# ============================================================
# Health Calculator
# ============================================================

class HealthCalculator:

    def __init__(
        self,
        status: ProjectStatus,
        link_report: Optional[
            LinkReport
        ] = None,
    ):
        self.status = status
        self.link_report = (
            link_report
        )

    def calculate(
        self,
    ) -> HealthReport:

        components: List[
            HealthComponent
        ] = []

        # Git cleanliness — 20
        if self.status.git.is_clean:
            score = 20
            status = "OK"
            detail = (
                "Working tree clean"
            )
        else:
            score = 0
            status = "WARN"
            detail = (
                "Working tree دارای "
                "تغییرات است"
            )

        components.append(
            HealthComponent(
                "Git cleanliness",
                score,
                20,
                status,
                detail,
            )
        )

        # Upstream — 15
        git = self.status.git

        if not git.upstream:
            score = 5
            state = "WARN"
            detail = (
                "Upstream تعریف نشده"
            )

        elif (
            git.ahead == 0
            and git.behind == 0
        ):
            score = 15
            state = "OK"
            detail = (
                "Local و upstream "
                "همگام هستند"
            )

        elif git.behind > 0:
            score = 5
            state = "WARN"
            detail = (
                f"{git.behind} commit "
                "behind"
            )

        else:
            score = 10
            state = "INFO"
            detail = (
                f"{git.ahead} commit "
                "ahead"
            )

        components.append(
            HealthComponent(
                "Upstream synchronization",
                score,
                15,
                state,
                detail,
            )
        )

        # Documents — 35
        docs = list(
            self.status.documents.values()
        )

        doc_score = 0.0

        if docs:
            per_doc = (
                35.0
                / len(docs)
            )

            for doc in docs:

                if doc.status in (
                    DocumentStatus.COMPLETE,
                    DocumentStatus.PUBLISHED,
                ):
                    doc_score += per_doc

                elif doc.status == (
                    DocumentStatus.PLACEHOLDER
                ):
                    doc_score += (
                        per_doc * 0.25
                    )

        invalid = [
            name
            for name, doc
            in self.status.documents.items()
            if doc.status not in (
                DocumentStatus.COMPLETE,
                DocumentStatus.PUBLISHED,
            )
        ]

        components.append(
            HealthComponent(
                "Document validation",
                round(doc_score),
                35,
                (
                    "OK"
                    if not invalid
                    else "WARN"
                ),
                (
                    "تمام اسناد معتبرند"
                    if not invalid
                    else
                    "نیازمند توجه: "
                    + ", ".join(invalid)
                ),
            )
        )

        # v0.5 additions — 10
        doc05 = (
            self.status.documents
            .get("0.5.md")
        )

        additional_ok = bool(
            doc05
            and doc05.status
            == DocumentStatus.COMPLETE
            and not doc05.missing_articles
            and doc05.detected_articles
            == self.status.total_articles_v05
        )

        components.append(
            HealthComponent(
                "v0.5 structural additions",
                10 if additional_ok else 0,
                10,
                "OK" if additional_ok
                else "WARN",
                (
                    "مواد الحاقی مورد انتظار "
                    "موجودند"
                    if additional_ok
                    else
                    "مواد الحاقی یا شمارش "
                    "۰.۵ نیازمند بررسی است"
                ),
            )
        )

        # Continuity — 10
        continuity_ok = bool(
            doc05
            and doc05.has_continuity
        )

        components.append(
            HealthComponent(
                "Article continuity",
                (
                    10
                    if continuity_ok
                    else 5
                ),
                10,
                (
                    "OK"
                    if continuity_ok
                    else "WARN"
                ),
                (
                    "ترتیب مواد پیوسته است"
                    if continuity_ok
                    else
                    "ترتیب مواد دارای "
                    "شکاف است"
                ),
            )
        )

        # Links — 10
        if self.link_report is None:
            link_score = 10
            link_state = "INFO"
            link_detail = (
                "Link check اجرا نشده"
            )

        elif (
            self.link_report.broken
            == 0
        ):
            link_score = 10
            link_state = "OK"
            link_detail = (
                "لینک شکسته‌ای "
                "تشخیص داده نشد"
            )

        else:
            link_score = 0
            link_state = "WARN"
            link_detail = (
                f"{self.link_report.broken} "
                "لینک/ارجاع مشکل‌دار"
            )

        components.append(
            HealthComponent(
                "Links and references",
                link_score,
                10,
                link_state,
                link_detail,
            )
        )

        total = sum(
            item.score
            for item in components
        )

        maximum = sum(
            item.max_score
            for item in components
        )

        percent = (
            total
            / maximum
            * 100
            if maximum
            else 0
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
# Repair Manager
# ============================================================

class RepairManager:
    """
    Repair فقط از منبع صریح انجام می‌شود.

    منبع مجاز:
    1. Git ref
    2. directory خارجی

    هیچ متن authoritative داخل اسکریپت ساخته نمی‌شود.
    """

    def __init__(
        self,
        repo_path: Path,
        collector: GitInfoCollector,
    ):
        self.repo_path = (
            repo_path.resolve()
        )

        self.collector = collector

    @staticmethod
    def validate_relative_path(
        relative_path: str,
    ) -> str:

        normalized = (
            normalize_slashes(
                relative_path
            )
        )

        path = Path(normalized)

        if path.is_absolute():
            raise ValueError(
                "repair-files باید "
                "مسیر نسبی باشند."
            )

        if ".." in path.parts:
            raise ValueError(
                "استفاده از '..' "
                "در repair-files مجاز نیست."
            )

        return normalized

    def repair_from_ref(
        self,
        ref: str,
        files: Sequence[str],
        dry_run: bool = False,
    ) -> int:

        if not self.collector.ref_exists(
            ref
        ):
            raise RuntimeError(
                f"Git ref پیدا نشد: {ref}"
            )

        repaired = 0

        for relative in files:
            relative = (
                self.validate_relative_path(
                    relative
                )
            )

            content = (
                self.collector.show_file(
                    ref,
                    relative,
                )
            )

            if content is None:
                print(
                    f"⚠️ در {ref} یافت نشد: "
                    f"{relative}"
                )
                continue

            destination = (
                self.repo_path
                / relative
            )

            if dry_run:
                print(
                    f"[dry-run] "
                    f"{ref}:{relative} "
                    f"→ {destination}"
                )
                repaired += 1
                continue

            ensure_parent(
                destination
            )

            destination.write_text(
                content,
                encoding="utf-8",
                newline="\n",
            )

            print(
                f"✅ بازیابی شد: "
                f"{relative} ← {ref}"
            )

            repaired += 1

        return repaired

    def repair_from_directory(
        self,
        source_dir: Path,
        files: Sequence[str],
        dry_run: bool = False,
    ) -> int:

        source_dir = (
            source_dir
            .expanduser()
            .resolve()
        )

        if not source_dir.exists():
            raise RuntimeError(
                f"source directory "
                f"وجود ندارد: "
                f"{source_dir}"
            )

        repaired = 0

        for relative in files:
            relative = (
                self.validate_relative_path(
                    relative
                )
            )

            source = (
                source_dir
                / relative
            ).resolve()

            try:
                source.relative_to(
                    source_dir
                )
            except ValueError:
                raise RuntimeError(
                    "مسیر source از "
                    "دایرکتوری مجاز خارج شد."
                )

            destination = (
                self.repo_path
                / relative
            ).resolve()

            if not source.exists():
                print(
                    f"⚠️ منبع یافت نشد: "
                    f"{source}"
                )
                continue

            if dry_run:
                print(
                    f"[dry-run] "
                    f"{source} "
                    f"→ {destination}"
                )
                repaired += 1
                continue

            ensure_parent(
                destination
            )

            shutil.copy2(
                source,
                destination,
            )

            print(
                f"✅ بازیابی شد: "
                f"{relative}"
            )

            repaired += 1

        return repaired


# ============================================================
# Ref Comparator
# ============================================================

class RefComparator:

    def __init__(
        self,
        collector: GitInfoCollector,
    ):
        self.collector = collector

    def compare(
        self,
        left_ref: str,
        right_ref: str,
        path: str,
    ) -> RefComparison:

        if not self.collector.ref_exists(
            left_ref
        ):
            raise RuntimeError(
                f"ref یافت نشد: "
                f"{left_ref}"
            )

        if not self.collector.ref_exists(
            right_ref
        ):
            raise RuntimeError(
                f"ref یافت نشد: "
                f"{right_ref}"
            )

        normalized = (
            normalize_slashes(path)
        )

        left = (
            self.collector.show_file(
                left_ref,
                normalized,
            )
        )

        right = (
            self.collector.show_file(
                right_ref,
                normalized,
            )
        )

        if left is None:
            left = ""

        if right is None:
            right = ""

        diff = "".join(
            difflib.unified_diff(
                left.splitlines(
                    keepends=True
                ),
                right.splitlines(
                    keepends=True
                ),
                fromfile=(
                    f"{left_ref}:{normalized}"
                ),
                tofile=(
                    f"{right_ref}:{normalized}"
                ),
            )
        )

        return RefComparison(
            left_ref=left_ref,
            right_ref=right_ref,
            path=normalized,
            changed=bool(diff),
            diff=diff,
        )


# ============================================================
# File Diff
# ============================================================

class DiffRenderer:

    def __init__(
        self,
        console: Optional[
            Console
        ] = None,
    ):
        self.console = (
            console
            or Console()
        )

    def render_text(
        self,
        diff: str,
    ) -> None:

        if not diff:
            print(
                "✅ تفاوتی وجود ندارد."
            )
            return

        for line in diff.splitlines():
            if line.startswith(
                ("---", "+++")
            ):
                print(
                    self.console.color(
                        line,
                        "blue",
                    )
                )

            elif line.startswith("@@"):
                print(
                    self.console.color(
                        line,
                        "cyan",
                    )
                )

            elif line.startswith("+"):
                print(
                    self.console.color(
                        line,
                        "green",
                    )
                )

            elif line.startswith("-"):
                print(
                    self.console.color(
                        line,
                        "red",
                    )
                )

            else:
                print(line)

    def render_files(
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

        left_text = left.read_text(
            encoding="utf-8"
        )

        right_text = right.read_text(
            encoding="utf-8"
        )

        diff = "".join(
            difflib.unified_diff(
                left_text.splitlines(
                    keepends=True
                ),
                right_text.splitlines(
                    keepends=True
                ),
                fromfile=str(left),
                tofile=str(right),
            )
        )

        self.render_text(diff)

        return int(
            ExitCode.OK
        )


# ============================================================
# Renderers
# ============================================================

class ConsoleRenderer:

    @staticmethod
    def icon(
        status: DocumentStatus,
    ) -> str:

        if status in (
            DocumentStatus.COMPLETE,
            DocumentStatus.PUBLISHED,
        ):
            return "✅"

        if status == (
            DocumentStatus.PLACEHOLDER
        ):
            return "⏳"

        if status == (
            DocumentStatus.CONTAMINATED
        ):
            return "🧪"

        if status == (
            DocumentStatus.INVALID
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
                f" نظم داد — "
                f"Project Status "
                f"v{TOOL_VERSION} "
            ).center(width)
        )

        print(
            "=" * width
        )

        print(
            "\n📦 Git"
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
            f"{status.git.last_commit_hash} "
            f"— "
            f"{status.git.last_commit_message}"
        )

        if status.git.upstream:
            print(
                f"  Upstream: "
                f"{status.git.upstream}"
            )

            print(
                f"  Ahead/Behind: "
                f"{status.git.ahead}/"
                f"{status.git.behind}"
            )

        print(
            "\n📄 اسناد"
        )

        for (
            name,
            doc,
        ) in status.documents.items():

            print(
                f"  {self.icon(doc.status)} "
                f"{name}: "
                f"{doc.status.value}"
            )

            print(
                f"      "
                f"{doc.size_bytes} bytes | "
                f"{doc.lines} lines"
            )

            if (
                doc.articles_count
                is not None
            ):
                print(
                    f"      مواد: "
                    f"{doc.detected_articles}/"
                    f"{doc.articles_count}"
                )

            if doc.description:
                print(
                    f"      "
                    f"{doc.description}"
                )

            for issue in (
                doc.contamination[:5]
            ):
                print(
                    f"      ⚠️ "
                    f"L{issue.line}: "
                    f"{issue.description}"
                )

            if (
                len(doc.contamination)
                > 5
            ):
                print(
                    f"      ... و "
                    f"{len(doc.contamination)-5} "
                    f"مورد دیگر"
                )

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

    def render_links(
        self,
        report: LinkReport,
    ) -> None:

        print(
            "🔗 بررسی لینک‌ها"
        )

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

        for issue in report.issues:
            print(
                f"\n  ❌ "
                f"{issue.source}:"
                f"{issue.line}"
            )

            print(
                f"     "
                f"{issue.target}"
            )

            print(
                f"     "
                f"{issue.description}"
            )

    def render_health(
        self,
        report: HealthReport,
    ) -> None:

        print(
            "🏥 سلامت پروژه"
        )

        print(
            f"\n  امتیاز: "
            f"{report.score}/"
            f"{report.max_score} "
            f"({report.percent}%)"
        )

        print(
            f"  Grade: "
            f"{report.grade}\n"
        )

        for item in report.components:

            if item.status == "OK":
                icon = "✅"

            elif item.status == "WARN":
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


class MarkdownRenderer:

    def render(
        self,
        status: ProjectStatus,
        health: Optional[
            HealthReport
        ] = None,
        links: Optional[
            LinkReport
        ] = None,
    ) -> str:

        lines = [
            "# وضعیت پروژه نظم داد",
            "",
            f"**ابزار:** v{TOOL_VERSION}",
            f"**زمان:** {status.timestamp}",
            "",
            "## Git",
            "",
            f"- Branch: `{status.git.branch}`",
            (
                "- Working tree: "
                + (
                    "✅ clean"
                    if status.git.is_clean
                    else "❌ dirty"
                )
            ),
            (
                "- Last commit: "
                f"`{status.git.last_commit_hash}` "
                f"— "
                f"{status.git.last_commit_message}"
            ),
            "",
            "## اسناد",
            "",
            "| فایل | وضعیت | حجم | خطوط | مواد | آلودگی |",
            "|---|---|---:|---:|---:|---:|",
        ]

        for (
            name,
            doc,
        ) in status.documents.items():

            articles = (
                str(
                    doc.detected_articles
                )
                if doc.detected_articles
                is not None
                else "-"
            )

            lines.append(
                f"| `{name}` "
                f"| {doc.status.value} "
                f"| {doc.size_bytes} "
                f"| {doc.lines} "
                f"| {articles} "
                f"| {len(doc.contamination)} |"
            )

        if health:
            lines.extend(
                [
                    "",
                    "## سلامت",
                    "",
                    (
                        f"**{health.score}/"
                        f"{health.max_score} "
                        f"({health.percent}%) "
                        f"— Grade "
                        f"{health.grade}**"
                    ),
                    "",
                ]
            )

            for item in (
                health.components
            ):
                lines.append(
                    f"- **{item.name}:** "
                    f"{item.score}/"
                    f"{item.max_score} — "
                    f"{item.detail}"
                )

        if links:
            lines.extend(
                [
                    "",
                    "## لینک‌ها",
                    "",
                    f"- Checked: {links.checked}",
                    f"- Valid: {links.valid}",
                    f"- Broken: {links.broken}",
                    f"- Skipped: {links.skipped}",
                ]
            )

        lines.extend(
            [
                "",
                "---",
                (
                    "_Generated by "
                    f"Nazm Dad "
                    f"Project Status "
                    f"v{TOOL_VERSION}_"
                ),
                "",
            ]
        )

        return "\n".join(lines)


class JsonRenderer:

    def render(
        self,
        status: ProjectStatus,
        health: Optional[
            HealthReport
        ] = None,
        links: Optional[
            LinkReport
        ] = None,
    ) -> str:

        data: Dict[str, Any] = {
            "tool": {
                "name": (
                    "nazm_dad_project_status"
                ),
                "version": TOOL_VERSION,
            },
            "timestamp": (
                status.timestamp
            ),
            "git": asdict(
                status.git
            ),
            "documents": {},
            "summary": {
                "articles_v04": (
                    status.total_articles_v04
                ),
                "articles_v05": (
                    status.total_articles_v05
                ),
                "real_changes": (
                    status.total_changes
                ),
                "noop_changes": (
                    status.noop_changes
                ),
            },
        }

        for (
            name,
            doc,
        ) in status.documents.items():

            doc_data = asdict(doc)

            doc_data["status"] = (
                doc.status.value
            )

            for item in (
                doc_data[
                    "contamination"
                ]
            ):
                if isinstance(
                    item.get("severity"),
                    Severity,
                ):
                    item["severity"] = (
                        item["severity"].value
                    )

            data["documents"][
                name
            ] = doc_data

        if health:
            data["health"] = (
                asdict(health)
            )

        if links:
            links_data = (
                asdict(links)
            )

            for issue in (
                links_data["issues"]
            ):
                if isinstance(
                    issue.get("status"),
                    LinkStatus,
                ):
                    issue["status"] = (
                        issue["status"].value
                    )

            data["links"] = (
                links_data
            )

        return (
            json.dumps(
                data,
                ensure_ascii=False,
                indent=2,
            )
            + "\n"
        )


class HtmlRenderer:

    def render(
        self,
        status: ProjectStatus,
        health: Optional[
            HealthReport
        ] = None,
        links: Optional[
            LinkReport
        ] = None,
    ) -> str:

        health_percent = (
            health.percent
            if health
            else 0
        )

        if health_percent >= 90:
            health_color = "#14804a"

        elif health_percent >= 70:
            health_color = "#b7791f"

        else:
            health_color = "#c53030"

        rows = []

        for (
            name,
            doc,
        ) in status.documents.items():

            if doc.status in (
                DocumentStatus.COMPLETE,
                DocumentStatus.PUBLISHED,
            ):
                status_class = "ok"

            elif doc.status == (
                DocumentStatus.CONTAMINATED
            ):
                status_class = "error"

            else:
                status_class = "warn"

            articles = (
                f"{doc.detected_articles}/"
                f"{doc.articles_count}"
                if doc.articles_count
                is not None
                else "-"
            )

            rows.append(
                f"""
                <tr>
                    <td>{html_escape(name)}</td>
                    <td>
                        <span class="badge {status_class}">
                            {html_escape(doc.status.value)}
                        </span>
                    </td>
                    <td>{doc.size_bytes}</td>
                    <td>{doc.lines}</td>
                    <td>{articles}</td>
                    <td>{len(doc.contamination)}</td>
                </tr>
                """
            )

        health_rows = []

        if health:
            for item in health.components:
                health_rows.append(
                    f"""
                    <tr>
                        <td>{html_escape(item.name)}</td>
                        <td>{item.score}/{item.max_score}</td>
                        <td>{html_escape(item.detail)}</td>
                    </tr>
                    """
                )

        broken_links = (
            links.broken
            if links
            else 0
        )

        return f"""<!doctype html>
<html lang="fa" dir="rtl">
<head>
<meta charset="utf-8">
<meta name="viewport"
      content="width=device-width,initial-scale=1">

<title>نظم داد — وضعیت پروژه</title>

<style>
body {{
    margin: 0;
    background: #f4f1e9;
    color: #14213d;
    font-family:
        "Segoe UI",
        Tahoma,
        sans-serif;
}}

.container {{
    max-width: 1150px;
    margin: 32px auto;
    background: white;
    padding: 28px;
    border-radius: 14px;
    box-shadow:
        0 8px 30px
        rgba(0,0,0,.08);
}}

.header {{
    display: flex;
    justify-content: space-between;
    align-items: center;
    border-bottom:
        2px solid #c8a96b;
    padding-bottom: 16px;
}}

.grid {{
    display: grid;
    grid-template-columns:
        repeat(auto-fit,minmax(220px,1fr));
    gap: 16px;
    margin: 24px 0;
}}

.card {{
    padding: 18px;
    background: #faf8f3;
    border-radius: 10px;
    border-right:
        4px solid #c8a96b;
}}

.value {{
    font-size: 28px;
    font-weight: 700;
}}

.small {{
    color: #677489;
    margin-top: 6px;
}}

.health {{
    color: white;
    background: {health_color};
}}

.progress {{
    width: 100%;
    height: 10px;
    background: #e6e8eb;
    border-radius: 6px;
    overflow: hidden;
}}

.progress > div {{
    width: {health_percent}%;
    height: 100%;
    background: {health_color};
}}

table {{
    width: 100%;
    border-collapse: collapse;
    margin-top: 12px;
}}

th, td {{
    padding: 10px;
    border-bottom:
        1px solid #e4e7eb;
    text-align: right;
}}

th {{
    background: #f4f1e9;
}}

.badge {{
    padding: 4px 9px;
    border-radius: 14px;
    font-size: 12px;
}}

.badge.ok {{
    background: #d8f3df;
    color: #17603a;
}}

.badge.warn {{
    background: #fff0c2;
    color: #7a5900;
}}

.badge.error {{
    background: #ffd7d7;
    color: #8f2020;
}}

code {{
    direction: ltr;
    unicode-bidi: embed;
}}

.footer {{
    margin-top: 28px;
    border-top:
        1px solid #ddd;
    padding-top: 16px;
    color: #718096;
    text-align: center;
}}
</style>
</head>

<body>
<div class="container">

<div class="header">
    <div>
        <h1>نظم داد</h1>
        <div class="small">
            Project Status v{TOOL_VERSION}
        </div>
    </div>

    <div>
        {html_escape(status.timestamp[:19])}
    </div>
</div>

<div class="grid">

    <div class="card health">
        <div>سلامت پروژه</div>
        <div class="value">
            {health_percent:.0f}%
        </div>
        <div>
            {html_escape(health.grade if health else "N/A")}
        </div>
    </div>

    <div class="card">
        <div>Branch</div>
        <div class="value">
            {html_escape(status.git.branch)}
        </div>
        <div class="small">
            {"clean" if status.git.is_clean else "dirty"}
        </div>
    </div>

    <div class="card">
        <div>اسناد</div>
        <div class="value">
            {len(status.documents)}
        </div>
        <div class="small">
            اسناد کنترل‌شده
        </div>
    </div>

    <div class="card">
        <div>Broken links</div>
        <div class="value">
            {broken_links}
        </div>
    </div>

</div>

<div class="progress">
    <div></div>
</div>

<h2>وضعیت اسناد</h2>

<table>
<thead>
<tr>
    <th>فایل</th>
    <th>وضعیت</th>
    <th>Bytes</th>
    <th>Lines</th>
    <th>مواد</th>
    <th>آلودگی</th>
</tr>
</thead>
<tbody>
{''.join(rows)}
</tbody>
</table>

<h2>Health Components</h2>

<table>
<thead>
<tr>
    <th>بخش</th>
    <th>امتیاز</th>
    <th>توضیح</th>
</tr>
</thead>
<tbody>
{
    ''.join(health_rows)
    if health_rows
    else
    '<tr><td colspan="3">N/A</td></tr>'
}
</tbody>
</table>

<div class="footer">
    Nazm Dad Project Status v{TOOL_VERSION}
</div>

</div>
</body>
</html>
"""


# ============================================================
# Watcher
# ============================================================

class ProjectWatcher:

    def __init__(
        self,
        paths: Sequence[Path],
        interval: float,
    ):
        self.paths = [
            path.resolve()
            for path in paths
        ]

        self.interval = interval

    def snapshot(
        self,
    ) -> Dict[str, Tuple[int, int]]:

        result: Dict[
            str,
            Tuple[int, int],
        ] = {}

        for root in self.paths:

            if root.is_file():
                files = [root]

            elif root.is_dir():
                files = [
                    path
                    for path in root.rglob("*")
                    if path.is_file()
                ]

            else:
                continue

            for path in files:
                try:
                    stat = path.stat()

                    result[str(path)] = (
                        stat.st_mtime_ns,
                        stat.st_size,
                    )

                except OSError:
                    pass

        return result

    def run(
        self,
        callback,
    ) -> None:

        print(
            "👁️ Watch mode فعال شد."
        )

        print(
            "برای خروج Ctrl+C بزنید."
        )

        previous = self.snapshot()

        while True:
            time.sleep(
                self.interval
            )

            current = self.snapshot()

            if current != previous:
                print(
                    "\n🔄 تغییر فایل "
                    "تشخیص داده شد."
                )

                callback()

                previous = current


# ============================================================
# Helpers
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
        in status.documents.values()
    )


def missing_v05_additional(
    status: ProjectStatus,
) -> List[str]:

    doc = (
        status.documents
        .get("0.5.md")
    )

    if not doc:
        return sorted(
            status
            .expected_v05_additional
        )

    return sorted(
        status.expected_v05_additional
        - set(
            doc.detected_article_ids
        )
    )


def load_config(
    explicit_path: Optional[str],
    candidate_repo: Optional[
        Path
    ] = None,
) -> ProjectConfig:

    if explicit_path:
        path = Path(
            explicit_path
        ).expanduser().resolve()

        if not path.exists():
            print(
                f"⚠️ config وجود ندارد: "
                f"{path}",
                file=sys.stderr,
            )

            return ProjectConfig()

        return (
            ProjectConfig
            .from_file(path)
        )

    candidates: List[Path] = []

    if candidate_repo:
        candidates.extend(
            [
                candidate_repo
                / ".nazm-dad-config.json",

                candidate_repo
                / "nazm-dad-config.json",
            ]
        )

    candidates.extend(
        [
            Path.cwd()
            / ".nazm-dad-config.json",

            Path.cwd()
            / "nazm-dad-config.json",

            Path.home()
            / ".nazm-dad-config.json",
        ]
    )

    seen: Set[Path] = set()

    for path in candidates:

        path = (
            path.expanduser()
            .resolve()
        )

        if path in seen:
            continue

        seen.add(path)

        if path.exists():
            return (
                ProjectConfig
                .from_file(path)
            )

    return ProjectConfig()


# ============================================================
# CLI
# ============================================================

def parse_args() -> argparse.Namespace:

    parser = argparse.ArgumentParser(
        description=(
            "Nazm Dad Project Status "
            f"v{TOOL_VERSION}"
        )
    )

    parser.add_argument(
        "--path",
        default=None,
        help="مسیر repository",
    )

    parser.add_argument(
        "--config",
        default=None,
        help="مسیر config JSON",
    )

    parser.add_argument(
        "--output-dir",
        default=None,
        help="مسیر خروجی",
    )

    parser.add_argument(
        "--verbose",
        action="store_true",
    )

    parser.add_argument(
        "--no-color",
        action="store_true",
    )

    parser.add_argument(
        "--no-progress",
        action="store_true",
    )

    mode = (
        parser
        .add_mutually_exclusive_group()
    )

    mode.add_argument(
        "--check",
        action="store_true",
    )

    mode.add_argument(
        "--validate-docs",
        action="store_true",
    )

    mode.add_argument(
        "--health",
        action="store_true",
    )

    mode.add_argument(
        "--check-links",
        action="store_true",
    )

    mode.add_argument(
        "--history",
        action="store_true",
    )

    mode.add_argument(
        "--diff",
        nargs=2,
        metavar=(
            "FILE_A",
            "FILE_B",
        ),
    )

    mode.add_argument(
        "--compare-refs",
        nargs=2,
        metavar=(
            "REF_A",
            "REF_B",
        ),
    )

    mode.add_argument(
        "--markdown",
        action="store_true",
    )

    mode.add_argument(
        "--json",
        action="store_true",
    )

    mode.add_argument(
        "--html",
        action="store_true",
    )

    mode.add_argument(
        "--repair",
        action="store_true",
    )

    mode.add_argument(
        "--watch",
        action="store_true",
    )

    parser.add_argument(
        "--compare-path",
        default=(
            "docs/0.5.md"
        ),
        help=(
            "مسیر فایل برای "
            "--compare-refs"
        ),
    )

    parser.add_argument(
        "--repair-files",
        nargs="+",
        default=None,
        help=(
            "فایل‌های قابل تعمیر"
        ),
    )

    parser.add_argument(
        "--repair-from-ref",
        default=None,
        help=(
            "بازیابی از Git ref"
        ),
    )

    parser.add_argument(
        "--repair-source-dir",
        default=None,
        help=(
            "بازیابی از source dir"
        ),
    )

    parser.add_argument(
        "--dry-run",
        action="store_true",
    )

    parser.add_argument(
        "--watch-interval",
        type=float,
        default=None,
    )

    return parser.parse_args()


# ============================================================
# CLI Render Functions
# ============================================================

def render_quick_check(
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
        "  Git: "
        + (
            "✅ clean"
            if status.git.is_clean
            else "❌ dirty"
        )
    )

    bad_docs = [
        name
        for name, doc
        in status.documents.items()
        if doc.status not in (
            DocumentStatus.COMPLETE,
            DocumentStatus.PUBLISHED,
        )
    ]

    contaminated = [
        name
        for name, doc
        in status.documents.items()
        if doc.status
        == DocumentStatus.CONTAMINATED
    ]

    if bad_docs:
        print(
            "  ⚠️ Documents: "
            + ", ".join(bad_docs)
        )

    else:
        print(
            "  Documents: ✅"
        )

    if contaminated:
        print(
            "  🧪 Contaminated: "
            + ", ".join(
                contaminated
            )
        )

    missing = (
        missing_v05_additional(
            status
        )
    )

    if missing:
        print(
            "  ❌ Missing v0.5 additions: "
            + ", ".join(missing)
        )

    failed = (
        not status.git.is_clean
        or status.git.behind > 0
        or bool(bad_docs)
        or bool(missing)
    )

    return int(
        ExitCode.VALIDATION_FAILED
        if failed
        else ExitCode.OK
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
        enabled=args.verbose
    )

    initial_repo = Path(
        args.path or "."
    ).expanduser().resolve()

    config = load_config(
        args.config,
        initial_repo,
    )

    repo_value = (
        args.path
        or config.repo_path
        or "."
    )

    repo_path = Path(
        repo_value
    ).expanduser().resolve()

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
            )
        )

        status = builder.build()

        actual_repo = Path(
            status.git.repository_root
            or repo_path
        ).resolve()

        collector = (
            builder.git_collector
        )

        link_checker = (
            LinkChecker(
                actual_repo,
                (
                    builder
                    .doc_validator
                    .docs_path
                ),
                (
                    builder
                    .doc_validator
                ),
                logger,
                progress_enabled=(
                    not args.no_progress
                ),
            )
        )

        # ------------------------------------------
        # Diff
        # ------------------------------------------

        if args.diff:
            left = Path(
                args.diff[0]
            )

            right = Path(
                args.diff[1]
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

            return (
                DiffRenderer(
                    console
                )
                .render_files(
                    left.resolve(),
                    right.resolve(),
                )
            )

        # ------------------------------------------
        # Compare Git refs
        # ------------------------------------------

        if args.compare_refs:
            left_ref, right_ref = (
                args.compare_refs
            )

            comparison = (
                RefComparator(
                    collector
                )
                .compare(
                    left_ref,
                    right_ref,
                    args.compare_path,
                )
            )

            print(
                f"🔀 Compare "
                f"{comparison.left_ref} "
                f"↔ "
                f"{comparison.right_ref}"
            )

            print(
                f"📄 "
                f"{comparison.path}\n"
            )

            DiffRenderer(
                console
            ).render_text(
                comparison.diff
            )

            return int(
                ExitCode.OK
            )

        # ------------------------------------------
        # Repair
        # ------------------------------------------

        if args.repair:

            repair_files = (
                args.repair_files
                or [
                    "docs/0.4.md",
                    "docs/changelog.md",
                ]
            )

            if (
                args.repair_from_ref
                and args.repair_source_dir
            ):
                print(
                    "❌ فقط یکی از "
                    "--repair-from-ref "
                    "یا "
                    "--repair-source-dir "
                    "مجاز است.",
                    file=sys.stderr,
                )

                return int(
                    ExitCode.USAGE_ERROR
                )

            if not (
                args.repair_from_ref
                or args.repair_source_dir
            ):
                print(
                    "❌ --repair نیازمند "
                    "--repair-from-ref "
                    "یا "
                    "--repair-source-dir "
                    "است.",
                    file=sys.stderr,
                )

                return int(
                    ExitCode.USAGE_ERROR
                )

            manager = RepairManager(
                actual_repo,
                collector,
            )

            if args.repair_from_ref:
                repaired = (
                    manager
                    .repair_from_ref(
                        args.repair_from_ref,
                        repair_files,
                        dry_run=(
                            args.dry_run
                        ),
                    )
                )

            else:
                repaired = (
                    manager
                    .repair_from_directory(
                        Path(
                            args
                            .repair_source_dir
                        ),
                        repair_files,
                        dry_run=(
                            args.dry_run
                        ),
                    )
                )

            print(
                f"\nتعداد فایل‌های "
                f"قابل بازیابی: "
                f"{repaired}"
            )

            if not args.dry_run:
                print(
                    "\n🔍 اعتبارسنجی "
                    "پس از تعمیر:"
                )

                new_status = (
                    ProjectStatusBuilder(
                        actual_repo,
                        logger,
                        config,
                    )
                    .build()
                )

                ConsoleRenderer().render(
                    new_status
                )

            return int(
                ExitCode.OK
            )

        # ------------------------------------------
        # History
        # ------------------------------------------

        if args.history:
            tags = (
                collector
                .get_tags_history()
            )

            if not tags:
                print(
                    "ℹ️ Tag یافت نشد."
                )

            for index, tag in enumerate(
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

                print(
                    f"   Date: "
                    f"{tag.date}"
                )

                print(
                    f"   Message: "
                    f"{tag.message}\n"
                )

            return int(
                ExitCode.OK
            )

        # ------------------------------------------
        # Check
        # ------------------------------------------

        if args.check:
            return (
                render_quick_check(
                    status
                )
            )

        # ------------------------------------------
        # Validate docs
        # ------------------------------------------

        if args.validate_docs:
            ConsoleRenderer().render(
                status
            )

            return int(
                ExitCode.OK
                if all_documents_valid(
                    status
                )
                else
                ExitCode
                .VALIDATION_FAILED
            )

        # ------------------------------------------
        # Links
        # ------------------------------------------

        if args.check_links:
            links = (
                link_checker.run()
            )

            ConsoleRenderer().render_links(
                links
            )

            return int(
                ExitCode.OK
                if links.broken == 0
                else
                ExitCode
                .VALIDATION_FAILED
            )

        # ------------------------------------------
        # Health
        # ------------------------------------------

        if args.health:
            links = (
                link_checker.run()
            )

            health = (
                HealthCalculator(
                    status,
                    links,
                )
                .calculate()
            )

            ConsoleRenderer().render_health(
                health
            )

            return int(
                ExitCode.OK
                if health.percent
                >= config.health_threshold_ok
                else
                ExitCode
                .VALIDATION_FAILED
            )

        # ------------------------------------------
        # Outputs
        # ------------------------------------------

        if (
            args.markdown
            or args.json
            or args.html
        ):
            output_value = (
                args.output_dir
                or config.output_dir
            )

            if output_value:
                output_dir = Path(
                    output_value
                ).expanduser()

                if not (
                    output_dir
                    .is_absolute()
                ):
                    output_dir = (
                        actual_repo
                        / output_dir
                    )

            else:
                output_dir = (
                    actual_repo
                    / "docs"
                    / "status"
                )

            output_dir = (
                output_dir.resolve()
            )

            output_dir.mkdir(
                parents=True,
                exist_ok=True,
            )

            links = (
                link_checker.run()
            )

            health = (
                HealthCalculator(
                    status,
                    links,
                )
                .calculate()
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
                        links,
                    ),
                    encoding="utf-8",
                    newline="\n",
                )

                print(
                    f"✅ ذخیره شد:\n"
                    f"{output}"
                )

            elif args.json:
                output = (
                    output_dir
                    / "status.json"
                )

                output.write_text(
                    JsonRenderer()
                    .render(
                        status,
                        health,
                        links,
                    ),
                    encoding="utf-8",
                    newline="\n",
                )

                print(
                    f"✅ ذخیره شد:\n"
                    f"{output}"
                )

            elif args.html:
                output = (
                    output_dir
                    / "dashboard.html"
                )

                output.write_text(
                    HtmlRenderer()
                    .render(
                        status,
                        health,
                        links,
                    ),
                    encoding="utf-8",
                    newline="\n",
                )

                print(
                    f"✅ ذخیره شد:\n"
                    f"{output}"
                )

            return int(
                ExitCode.OK
            )

        # ------------------------------------------
        # Watch
        # ------------------------------------------

        if args.watch:
            interval = (
                args.watch_interval
                or config.watch_interval
            )

            def validate_callback():
                latest_builder = (
                    ProjectStatusBuilder(
                        actual_repo,
                        logger,
                        config,
                    )
                )

                latest = (
                    latest_builder
                    .build()
                )

                latest_link_checker = (
                    LinkChecker(
                        actual_repo,
                        (
                            latest_builder
                            .doc_validator
                            .docs_path
                        ),
                        (
                            latest_builder
                            .doc_validator
                        ),
                        logger,
                        progress_enabled=False,
                    )
                )

                latest_links = (
                    latest_link_checker
                    .run()
                )

                latest_health = (
                    HealthCalculator(
                        latest,
                        latest_links,
                    )
                    .calculate()
                )

                print(
                    "\n"
                    + "=" * 60
                )

                ConsoleRenderer().render_health(
                    latest_health
                )

                print(
                    "=" * 60
                )

            watcher = (
                ProjectWatcher(
                    [
                        builder
                        .doc_validator
                        .docs_path,
                        actual_repo
                        / "nazm_dad_project_status.py",
                    ],
                    interval,
                )
            )

            validate_callback()

            watcher.run(
                validate_callback
            )

            return int(
                ExitCode.OK
            )

        # ------------------------------------------
        # Default
        # ------------------------------------------

        ConsoleRenderer().render(
            status
        )

        return int(
            ExitCode.OK
        )

    except KeyboardInterrupt:
        print(
            "\n⚠️ عملیات متوقف شد."
        )

        return int(
            ExitCode.OK
        )

    except (
        RuntimeError,
        OSError,
        ValueError,
    ) as exc:

        print(
            f"❌ خطا: {exc}",
            file=sys.stderr,
        )

        return int(
            ExitCode.RUNTIME_ERROR
        )


if __name__ == "__main__":
    sys.exit(main())