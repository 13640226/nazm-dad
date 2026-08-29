#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
Nazm Dad — Project Status & Documentation Tool (extension layer)
نظم داد — ابزار وضعیت، اعتبارسنجی و مستندسازی پروژه (لایهٔ extension)

Version: 3.1

قابلیت‌های جدید v3.1
--------------------
- --validate-articles: اعتبارسنجی ساختار، شماره‌گذاری و توالی مواد
- --check-links: بررسی لینک‌های شکسته در اسناد (جدا از strict)
- --html-report: تولید گزارش HTML کامل با نمودارهای وضعیت (alias: --ci-html)
- --output: ذخیره خروجی در فایل برای فرمت‌های ساختاریافته
- --quiet: کاهش خروجی (در Watch فقط تغییرات را نشان می‌دهد)
- بهبود --summary: نمایش خلاصهٔ خوانا از Git، اسناد و Health
- بهبود --report: اضافه شدن آمار تفصیلی مواد
- پشتیبانی از --format json/yaml/csv در دستورات خروجی‌محور
- بهبود سرعت با بهینه‌سازی اسکن فایل‌ها

قابلیت‌های موجود از v2.7/v2.8/v2.9/v3.0 حفظ شده‌اند.
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import html
import json
import os
import re
import subprocess
import sys
import time
from dataclasses import asdict, dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Set, Tuple


# ============================================================
# Version & Constants
# ============================================================

TOOL_VERSION = "3.1"
DEFAULT_CORE = "nazm_dad_project_status.py"
STRICT_TIMEOUT_SECONDS = 120.0
TIMEOUT_EXIT_CODE = 124
DEFAULT_MANIFEST = ".nazm-dad-hashes.json"
DEFAULT_CONFIG = ".nazm-dad-config.json"

DEFAULT_REPAIR_FILES: Tuple[str, ...] = (
    "docs/0.4.md",
    "docs/0.5.md",
    "docs/changelog.md",
    "docs/rules.md",
    "docs/decisions.md",
)


# ============================================================
# Exit Codes
# ============================================================

EXIT_OK = 0
EXIT_VALIDATION_FAILED = 1
EXIT_RUNTIME_ERROR = 2
EXIT_USAGE_ERROR = 3
EXIT_INTERRUPTED = 130


# ============================================================
# Data Classes
# ============================================================

@dataclass
class DoctorCheck:
    name: str
    ok: bool
    detail: str
    warning: bool = False


@dataclass
class DoctorReport:
    ok: bool
    checks: List[DoctorCheck] = field(default_factory=list)

    @property
    def issues(self) -> List[str]:
        return [
            item.detail
            for item in self.checks
            if not item.ok and not item.warning
        ]

    @property
    def warnings(self) -> List[str]:
        return [
            item.detail
            for item in self.checks
            if item.warning
        ]


@dataclass
class RepairPreviewItem:
    path: str
    source: str
    target_exists: bool
    source_exists: bool
    changed: bool
    description: str
    diff: str = ""


@dataclass
class StrictResult:
    exit_code: int
    duration_seconds: float


@dataclass
class ArticleValidation:
    file: str
    total_expected: int
    total_found: int
    ids: List[str]
    missing: List[str]
    duplicates: List[str]
    out_of_order: List[str]
    has_continuity: bool
    is_valid: bool


@dataclass
class ProjectSummary:
    branch: str
    is_clean: bool
    commit: str
    commit_date: str
    total_documents: int
    valid_documents: int
    placeholder_documents: int
    invalid_documents: int
    total_articles: int
    health_score: Optional[int] = None
    health_grade: Optional[str] = None
    changes_ahead: int = 0
    changes_behind: int = 0


@dataclass
class LinkCheckResult:
    file: str
    line: int
    target: str
    status: str
    description: str


# ============================================================
# General Helpers
# ============================================================

def utc_timestamp() -> str:
    return datetime.now().astimezone().isoformat()


def normalize_rel_path(value: str) -> str:
    value = value.replace("\\", "/").strip()
    while value.startswith("./"):
        value = value[2:]
    return value


def rel(path: Path, base: Path) -> str:
    try:
        return path.resolve().relative_to(base.resolve()).as_posix()
    except ValueError:
        return str(path.resolve())


def find_repo(start: Path) -> Path:
    current = start.expanduser().resolve()

    if current.is_file():
        current = current.parent

    for candidate in (current, *current.parents):
        if (candidate / ".git").exists():
            return candidate

    return current


def safe_repo_path(repo: Path, relative: str) -> Path:
    normalized = normalize_rel_path(relative)

    if not normalized:
        raise ValueError("empty project-relative path")

    repo_root = repo.resolve()
    candidate = (repo_root / normalized).resolve()

    try:
        candidate.relative_to(repo_root)
    except ValueError as exc:
        raise ValueError(
            f"path escapes repository: {relative}"
        ) from exc

    return candidate


def child_python_env() -> Dict[str, str]:
    env = os.environ.copy()
    env["PYTHONIOENCODING"] = "utf-8"
    env["PYTHONUTF8"] = "1"
    return env


def configure_utf8_stdio() -> None:
    for stream in (sys.stdout, sys.stderr):
        reconfigure = getattr(stream, "reconfigure", None)

        if not callable(reconfigure):
            continue

        try:
            reconfigure(
                encoding="utf-8",
                errors="replace",
            )
        except (OSError, ValueError):
            pass


def load_config(
    config_path: Optional[Path],
) -> Dict[str, Any]:

    if config_path is None:
        config_path = Path(DEFAULT_CONFIG)

    if not config_path.exists():
        return {}

    try:
        data = json.loads(
            config_path.read_text(
                encoding="utf-8",
            )
        )

        if isinstance(data, dict):
            return data

    except (OSError, json.JSONDecodeError):
        pass

    return {}


def write_output(
    content: str,
    output_path: Optional[Path],
) -> bool:

    if output_path is None:
        print(content)
        return True

    try:
        output_path.parent.mkdir(
            parents=True,
            exist_ok=True,
        )

        output_path.write_text(
            content,
            encoding="utf-8",
        )

        print(
            f"✅ Output written to: {output_path}"
        )

        return True

    except OSError as exc:
        print(
            f"❌ Failed to write output: {exc}",
            file=sys.stderr,
        )
        return False


def format_output(
    data: Dict[str, Any],
    fmt: str,
) -> str:

    if fmt == "json":
        return json.dumps(
            data,
            ensure_ascii=False,
            indent=2,
        )

    if fmt == "yaml":
        try:
            import yaml
        except ImportError as exc:
            raise RuntimeError(
                "PyYAML is not installed. "
                "Please install: pip install pyyaml"
            ) from exc

        return yaml.dump(
            data,
            allow_unicode=True,
            default_flow_style=False,
            sort_keys=False,
        )

    if fmt == "csv":
        import io

        output = io.StringIO()
        writer = csv.writer(output)

        writer.writerow(
            ["key", "value"]
        )

        def flatten(
            value: Any,
            parent: str = "",
        ) -> None:

            if isinstance(value, dict):
                for key, child in value.items():
                    flatten(
                        child,
                        f"{parent}{key}.",
                    )

            elif isinstance(value, list):
                writer.writerow(
                    [
                        parent.rstrip("."),
                        json.dumps(
                            value,
                            ensure_ascii=False,
                        ),
                    ]
                )

            else:
                writer.writerow(
                    [
                        parent.rstrip("."),
                        str(value),
                    ]
                )

        flatten(data)
        return output.getvalue()

    raise ValueError(
        f"Unsupported format: {fmt}"
    )


# ============================================================
# Git Helpers
# ============================================================

def git_available() -> bool:
    try:
        proc = subprocess.run(
            ["git", "--version"],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=10,
        )

        return proc.returncode == 0

    except (
        OSError,
        subprocess.TimeoutExpired,
    ):
        return False


def git_run(
    repo: Path,
    args: Sequence[str],
    *,
    check: bool = False,
    timeout: Optional[float] = 30.0,
) -> subprocess.CompletedProcess:

    command = [
        "git",
        "-C",
        str(repo),
        *args,
    ]

    return subprocess.run(
        command,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        check=check,
        timeout=timeout,
    )


def git_commit(repo: Path) -> str:
    try:
        proc = git_run(
            repo,
            ["rev-parse", "HEAD"],
            timeout=15,
        )

        if proc.returncode == 0:
            return proc.stdout.strip()

    except (
        OSError,
        subprocess.TimeoutExpired,
    ):
        pass

    return ""


def git_commit_short(repo: Path) -> str:
    commit = git_commit(repo)

    return commit[:8] if commit else ""


def git_commit_date(repo: Path) -> str:
    try:
        proc = git_run(
            repo,
            [
                "log",
                "-1",
                "--format=%ai",
            ],
            timeout=15,
        )

        if proc.returncode == 0:
            return proc.stdout.strip()

    except (
        OSError,
        subprocess.TimeoutExpired,
    ):
        pass

    return ""


def git_branch(repo: Path) -> str:
    try:
        proc = git_run(
            repo,
            [
                "branch",
                "--show-current",
            ],
            timeout=15,
        )

        if proc.returncode == 0:
            return proc.stdout.strip()

    except (
        OSError,
        subprocess.TimeoutExpired,
    ):
        pass

    return ""


def git_upstream(
    repo: Path,
) -> Optional[str]:

    try:
        proc = git_run(
            repo,
            [
                "rev-parse",
                "--abbrev-ref",
                "--symbolic-full-name",
                "@{upstream}",
            ],
            timeout=15,
        )

        if proc.returncode == 0:
            value = proc.stdout.strip()
            return value or None

    except (
        OSError,
        subprocess.TimeoutExpired,
    ):
        pass

    return None


def git_is_clean(
    repo: Path,
) -> Optional[bool]:

    try:
        proc = git_run(
            repo,
            [
                "status",
                "--porcelain",
            ],
            timeout=15,
        )

        if proc.returncode == 0:
            return not bool(
                proc.stdout.strip()
            )

    except (
        OSError,
        subprocess.TimeoutExpired,
    ):
        pass

    return None


def git_ahead_behind(
    repo: Path,
) -> Tuple[int, int]:

    upstream = git_upstream(repo)

    if not upstream:
        return 0, 0

    try:
        proc = git_run(
            repo,
            [
                "rev-list",
                "--left-right",
                "--count",
                f"HEAD...{upstream}",
            ],
            timeout=15,
        )

        if proc.returncode == 0:
            parts = (
                proc.stdout
                .strip()
                .split()
            )

            if len(parts) == 2:
                return (
                    int(parts[0]),
                    int(parts[1]),
                )

    except (
        OSError,
        subprocess.TimeoutExpired,
        ValueError,
    ):
        pass

    return 0, 0


def git_ref_exists(
    repo: Path,
    ref: str,
) -> bool:

    try:
        proc = git_run(
            repo,
            [
                "rev-parse",
                "--verify",
                "--quiet",
                f"{ref}^{{commit}}",
            ],
            timeout=15,
        )

        return proc.returncode == 0

    except (
        OSError,
        subprocess.TimeoutExpired,
    ):
        return False


def git_file_history(
    repo: Path,
    file_path: str,
    max_count: int = 10,
) -> List[Dict[str, str]]:

    try:
        proc = git_run(
            repo,
            [
                "log",
                f"-{max_count}",
                "--format=%h|%ai|%s",
                "--",
                file_path,
            ],
            timeout=15,
        )

        if proc.returncode != 0:
            return []

        entries: List[
            Dict[str, str]
        ] = []

        for line in (
            proc.stdout
            .strip()
            .splitlines()
        ):
            if not line:
                continue

            parts = line.split(
                "|",
                2,
            )

            if len(parts) >= 3:
                entries.append(
                    {
                        "commit": parts[0],
                        "date": parts[1],
                        "message": parts[2],
                    }
                )

        return entries

    except (
        OSError,
        subprocess.TimeoutExpired,
    ):
        return []


# ============================================================
# File / Hash Helpers
# ============================================================

def sha256_file(
    path: Path,
) -> str:

    digest = hashlib.sha256()

    with path.open("rb") as handle:
        while True:
            chunk = handle.read(
                1024 * 1024
            )

            if not chunk:
                break

            digest.update(chunk)

    return digest.hexdigest()


def extract_health_score(
    text: str,
) -> Optional[int]:

    patterns = (
        r"(\d+)\s*/\s*100",
        r"score\s*[:=]\s*(\d+)",
        r"امتیاز\s*[:=]\s*(\d+)",
    )

    for pattern in patterns:
        match = re.search(
            pattern,
            text,
            re.IGNORECASE,
        )

        if not match:
            continue

        try:
            value = int(
                match.group(1)
            )
        except (
            TypeError,
            ValueError,
        ):
            continue

        if 0 <= value <= 100:
            return value

    return None


def extract_health_grade(
    score: Optional[int],
) -> str:

    if score is None:
        return "N/A"

    if score >= 95:
        return "A+"

    if score >= 90:
        return "A"

    if score >= 80:
        return "B"

    if score >= 70:
        return "C"

    if score >= 60:
        return "D"

    return "F"


# ============================================================
# Core Path & Execution
# ============================================================

def core_path(
    repo: Path,
    core: str,
) -> Path:

    candidate = (
        Path(core)
        .expanduser()
    )

    if candidate.is_absolute():
        return candidate.resolve()

    return safe_repo_path(
        repo,
        core,
    )


def run_core(
    repo: Path,
    core: str,
    args: Sequence[str],
    *,
    timeout_seconds: Optional[
        float
    ] = None,
    quiet: bool = False,
) -> int:

    try:
        path = core_path(
            repo,
            core,
        )
    except ValueError as exc:
        if not quiet:
            print(
                f"❌ {exc}",
                file=sys.stderr,
            )

        return EXIT_USAGE_ERROR

    if not path.exists():
        if not quiet:
            print(
                f"❌ core script missing: {path}",
                file=sys.stderr,
            )

        return EXIT_RUNTIME_ERROR

    if not path.is_file():
        if not quiet:
            print(
                f"❌ core path is not a file: {path}",
                file=sys.stderr,
            )

        return EXIT_RUNTIME_ERROR

    command = [
        sys.executable,
        str(path),
        *args,
    ]

    try:
        proc = subprocess.run(
            command,
            cwd=str(repo),
            timeout=timeout_seconds,
            stdout=(
                subprocess.PIPE
                if quiet
                else None
            ),
            stderr=(
                subprocess.PIPE
                if quiet
                else None
            ),
            text=quiet,
            encoding=(
                "utf-8"
                if quiet
                else None
            ),
            errors=(
                "replace"
                if quiet
                else None
            ),
            env=child_python_env(),
        )

        return int(
            proc.returncode
        )

    except subprocess.TimeoutExpired:
        if not quiet:
            timeout_text = (
                f"{timeout_seconds:.1f}s"
                if timeout_seconds
                is not None
                else "configured timeout"
            )

            print(
                (
                    f"⏱️ TIMEOUT after "
                    f"{timeout_text}: "
                    f"{' '.join(command)}"
                ),
                file=sys.stderr,
            )

        return TIMEOUT_EXIT_CODE

    except KeyboardInterrupt:
        if not quiet:
            print(
                "\n⚠️ interrupted by user",
                file=sys.stderr,
            )

        return EXIT_INTERRUPTED

    except OSError as exc:
        if not quiet:
            print(
                (
                    "❌ failed to execute "
                    f"core script: {exc}"
                ),
                file=sys.stderr,
            )

        return EXIT_RUNTIME_ERROR


def capture_core(
    repo: Path,
    core: str,
    args: Sequence[str],
    *,
    timeout_seconds: Optional[
        float
    ] = None,
) -> subprocess.CompletedProcess:

    path = core_path(
        repo,
        core,
    )

    if not path.exists():
        raise FileNotFoundError(
            f"core script missing: {path}"
        )

    if not path.is_file():
        raise FileNotFoundError(
            f"core path is not a file: {path}"
        )

    return subprocess.run(
        [
            sys.executable,
            str(path),
            *args,
        ],
        cwd=str(repo),
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=timeout_seconds,
        env=child_python_env(),
    )


# ============================================================
# Doctor
# ============================================================

def doctor(
    repo: Path,
    core: str,
) -> DoctorReport:

    checks: List[
        DoctorCheck
    ] = []

    checks.append(
        DoctorCheck(
            name="python",
            ok=True,
            detail=(
                "python: "
                f"{sys.version_info.major}."
                f"{sys.version_info.minor}."
                f"{sys.version_info.micro} "
                f"({sys.executable})"
            ),
        )
    )

    if git_available():
        try:
            proc = subprocess.run(
                [
                    "git",
                    "--version",
                ],
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=10,
            )

            checks.append(
                DoctorCheck(
                    name="git",
                    ok=True,
                    detail=(
                        proc.stdout
                        .strip()
                    ),
                )
            )

        except (
            OSError,
            subprocess.TimeoutExpired,
        ) as exc:

            checks.append(
                DoctorCheck(
                    name="git",
                    ok=False,
                    detail=(
                        f"git error: {exc}"
                    ),
                )
            )

    else:
        checks.append(
            DoctorCheck(
                name="git",
                ok=False,
                detail=(
                    "git executable "
                    "not available"
                ),
            )
        )

    is_repo = (
        repo / ".git"
    ).exists()

    checks.append(
        DoctorCheck(
            name="repository",
            ok=is_repo,
            detail=f"repository: {repo}",
        )
    )

    try:
        core_file = core_path(
            repo,
            core,
        )

    except ValueError as exc:
        checks.append(
            DoctorCheck(
                name="core-script",
                ok=False,
                detail=(
                    "core-script: "
                    f"invalid path: {exc}"
                ),
            )
        )

    else:
        if (
            core_file.exists()
            and core_file.is_file()
        ):
            try:
                compile_proc = (
                    subprocess.run(
                        [
                            sys.executable,
                            "-m",
                            "py_compile",
                            str(core_file),
                        ],
                        cwd=str(repo),
                        capture_output=True,
                        text=True,
                        encoding="utf-8",
                        errors="replace",
                        timeout=30,
                        env=child_python_env(),
                    )
                )

                if (
                    compile_proc.returncode
                    == 0
                ):
                    checks.append(
                        DoctorCheck(
                            name="core-script",
                            ok=True,
                            detail=(
                                "core-script: "
                                "syntax OK"
                            ),
                        )
                    )

                else:
                    detail = (
                        compile_proc
                        .stderr
                        .strip()
                        or compile_proc
                        .stdout
                        .strip()
                        or (
                            "exit="
                            f"{compile_proc.returncode}"
                        )
                    )

                    checks.append(
                        DoctorCheck(
                            name="core-script",
                            ok=False,
                            detail=(
                                "core-script: "
                                "syntax error: "
                                f"{detail}"
                            ),
                        )
                    )

            except subprocess.TimeoutExpired:
                checks.append(
                    DoctorCheck(
                        name="core-script",
                        ok=False,
                        detail=(
                            "core-script: "
                            "syntax check timed "
                            "out after 30s"
                        ),
                    )
                )

            except OSError as exc:
                checks.append(
                    DoctorCheck(
                        name="core-script",
                        ok=False,
                        detail=(
                            "core-script: "
                            "unable to run "
                            "syntax check: "
                            f"{exc}"
                        ),
                    )
                )

        else:
            checks.append(
                DoctorCheck(
                    name="core-script",
                    ok=False,
                    detail=(
                        "core-script missing: "
                        f"{core_file}"
                    ),
                )
            )

    if is_repo:
        clean = git_is_clean(
            repo
        )

        if clean is True:
            checks.append(
                DoctorCheck(
                    name="working-tree",
                    ok=True,
                    detail=(
                        "working-tree: clean"
                    ),
                )
            )

        elif clean is False:
            checks.append(
                DoctorCheck(
                    name="working-tree",
                    ok=True,
                    warning=True,
                    detail=(
                        "working-tree: dirty"
                    ),
                )
            )

        else:
            checks.append(
                DoctorCheck(
                    name="working-tree",
                    ok=False,
                    detail=(
                        "working-tree: "
                        "unable to determine"
                    ),
                )
            )

        branch = git_branch(
            repo
        )

        checks.append(
            DoctorCheck(
                name="branch",
                ok=bool(branch),
                detail=(
                    f"branch: {branch}"
                    if branch
                    else "branch: unavailable"
                ),
            )
        )

        upstream = git_upstream(
            repo
        )

        checks.append(
            DoctorCheck(
                name="upstream",
                ok=True,
                warning=not bool(
                    upstream
                ),
                detail=(
                    f"upstream: {upstream}"
                    if upstream
                    else (
                        "upstream: "
                        "not configured"
                    )
                ),
            )
        )

    docs = repo / "docs"

    if not docs.exists():
        checks.append(
            DoctorCheck(
                name="docs",
                ok=False,
                detail=(
                    "docs directory "
                    f"missing: {docs}"
                ),
            )
        )

    else:
        required = (
            (
                "docs/0.4.md",
                61,
            ),
            (
                "docs/0.5.md",
                73,
            ),
            (
                "docs/changelog.md",
                None,
            ),
            (
                "docs/rules.md",
                None,
            ),
            (
                "docs/decisions.md",
                None,
            ),
        )

        for (
            relative,
            expected_articles,
        ) in required:

            target = (
                repo / relative
            )

            if not target.exists():
                checks.append(
                    DoctorCheck(
                        name=(
                            f"doc:{relative}"
                        ),
                        ok=False,
                        detail=(
                            f"doc:{relative}: "
                            "missing"
                        ),
                    )
                )

                continue

            if not target.is_file():
                checks.append(
                    DoctorCheck(
                        name=(
                            f"doc:{relative}"
                        ),
                        ok=False,
                        detail=(
                            f"doc:{relative}: "
                            "not a regular file"
                        ),
                    )
                )

                continue

            try:
                text = target.read_text(
                    encoding="utf-8",
                    errors="strict",
                )

            except UnicodeDecodeError as exc:
                checks.append(
                    DoctorCheck(
                        name=(
                            f"doc:{relative}"
                        ),
                        ok=False,
                        detail=(
                            f"doc:{relative}: "
                            "invalid UTF-8: "
                            f"{exc}"
                        ),
                    )
                )

                continue

            except OSError as exc:
                checks.append(
                    DoctorCheck(
                        name=(
                            f"doc:{relative}"
                        ),
                        ok=False,
                        detail=(
                            f"doc:{relative}: "
                            f"read error: {exc}"
                        ),
                    )
                )

                continue

            if expected_articles is None:
                try:
                    size = (
                        target.stat()
                        .st_size
                    )

                except OSError as exc:
                    checks.append(
                        DoctorCheck(
                            name=(
                                f"doc:{relative}"
                            ),
                            ok=False,
                            detail=(
                                f"doc:{relative}: "
                                f"stat error: {exc}"
                            ),
                        )
                    )

                    continue

                checks.append(
                    DoctorCheck(
                        name=(
                            f"doc:{relative}"
                        ),
                        ok=True,
                        detail=(
                            f"doc:{relative}: "
                            f"{size} bytes"
                        ),
                    )
                )

            else:
                token_count = (
                    text.count("ماده")
                )

                count_ok = (
                    token_count
                    >= expected_articles
                )

                checks.append(
                    DoctorCheck(
                        name=(
                            f"doc:{relative}"
                        ),
                        ok=True,
                        warning=not count_ok,
                        detail=(
                            f"doc:{relative}: "
                            "rough article-token "
                            f"count={token_count}; "
                            "expected at least "
                            f"{expected_articles}"
                        ),
                    )
                )

    recommended = (
        (
            ".gitignore",
            (
                ".gitignore missing "
                "(recommended)"
            ),
        ),
        (
            "README.md",
            (
                "README.md missing "
                "(recommended)"
            ),
        ),
        (
            "LICENSE",
            (
                "LICENSE missing "
                "(recommended)"
            ),
        ),
        (
            "LICENSE-DOCS.md",
            (
                "LICENSE-DOCS.md "
                "missing (recommended)"
            ),
        ),
    )

    for (
        filename,
        description,
    ) in recommended:

        if not (
            repo / filename
        ).exists():

            checks.append(
                DoctorCheck(
                    name=filename,
                    ok=False,
                    warning=True,
                    detail=description,
                )
            )

    hard_failure = any(
        (
            not item.ok
            and not item.warning
        )
        for item in checks
    )

    return DoctorReport(
        ok=not hard_failure,
        checks=checks,
    )


def print_doctor(
    report: DoctorReport,
) -> None:

    print("=" * 72)
    print(
        f"Nazm Dad — Doctor v{TOOL_VERSION}"
    )
    print("=" * 72)

    for item in report.checks:
        if (
            item.ok
            and not item.warning
        ):
            icon = "✅"

        elif item.warning:
            icon = "⚠️"

        else:
            icon = "❌"

        print(
            f"{icon} {item.detail}"
        )

    print("=" * 72)

    print(
        "PASS"
        if report.ok
        else "FAIL"
    )


def doctor_payload(
    repo: Path,
    core: str,
    report: DoctorReport,
) -> Dict[str, Any]:

    try:
        resolved_core = str(
            core_path(
                repo,
                core,
            )
        )

    except ValueError as exc:
        resolved_core = (
            "<invalid core path: "
            f"{exc}>"
        )

    return {
        "schema": 1,
        "tool": (
            "nazm-dad-project-status"
        ),
        "version": TOOL_VERSION,
        "timestamp": utc_timestamp(),
        "repo": str(repo),
        "core": resolved_core,
        "ok": report.ok,
        "checks": [
            asdict(item)
            for item in report.checks
        ],
        "issues": report.issues,
        "warnings": report.warnings,
    }


# ============================================================
# Article Validation
# ============================================================

ARTICLE_PATTERN = re.compile(
    r"^\s*\*\*ماده\s+"
    r"([۰-۹0-9]+(?:[–—\-][۰-۹0-9]+)?)"
    r"\s*[ـ–—-]",
    re.MULTILINE,
)


def normalize_digits(
    value: str,
) -> str:

    return value.translate(
        str.maketrans(
            (
                "۰۱۲۳۴۵۶۷۸۹"
                "٠١٢٣٤٥٦٧٨٩"
            ),
            (
                "0123456789"
                "0123456789"
            ),
        )
    )


def normalize_article_id(
    value: str,
) -> str:

    return (
        normalize_digits(value)
        .replace("–", "-")
        .replace("—", "-")
        .replace("−", "-")
        .strip()
    )


def find_articles(
    content: str,
) -> List[str]:

    return [
        normalize_article_id(
            match.group(1)
        )
        for match
        in ARTICLE_PATTERN.finditer(
            content
        )
    ]


def validate_articles_in_file(
    file_path: Path,
    expected_count: Optional[int] = None,
) -> ArticleValidation:

    if not file_path.exists():
        return ArticleValidation(
            file=str(file_path),
            total_expected=(
                expected_count or 0
            ),
            total_found=0,
            ids=[],
            missing=[],
            duplicates=[],
            out_of_order=[],
            has_continuity=False,
            is_valid=False,
        )

    try:
        content = (
            file_path.read_text(
                encoding="utf-8",
                errors="strict",
            )
        )

    except (
        OSError,
        UnicodeDecodeError,
    ):
        return ArticleValidation(
            file=str(file_path),
            total_expected=(
                expected_count or 0
            ),
            total_found=0,
            ids=[],
            missing=[],
            duplicates=[],
            out_of_order=[],
            has_continuity=False,
            is_valid=False,
        )

    ids = find_articles(
        content
    )

    total_found = len(ids)

    seen: Set[str] = set()
    duplicate_seen: Set[str] = set()
    duplicates: List[str] = []

    for aid in ids:
        if (
            aid in seen
            and aid not in duplicate_seen
        ):
            duplicates.append(aid)
            duplicate_seen.add(aid)

        seen.add(aid)

    out_of_order: List[str] = []

    if ids:
        numeric: List[int] = []

        for aid in ids:
            try:
                numeric.append(
                    int(
                        aid.split("-")[0]
                    )
                )
            except ValueError:
                numeric.append(-1)

        for index in range(
            1,
            len(numeric),
        ):
            if (
                numeric[index]
                < numeric[index - 1]
                and numeric[index]
                != -1
            ):
                out_of_order.append(
                    ids[index]
                )

    has_continuity = True

    if ids and len(ids) > 1:
        values: List[int] = []

        for aid in ids:
            try:
                values.append(
                    int(
                        aid.split("-")[0]
                    )
                )
            except ValueError:
                values.append(-1)

        valid_values = [
            value
            for value in values
            if value >= 0
        ]

        if valid_values:
            has_continuity = (
                set(
                    range(
                        min(valid_values),
                        max(valid_values)
                        + 1,
                    )
                )
                == set(valid_values)
            )

    missing: List[str] = []

    if expected_count is not None:
        found_numeric: Set[int] = set()

        for aid in ids:
            if "-" in aid:
                continue

            try:
                found_numeric.add(
                    int(aid)
                )
            except ValueError:
                pass

        expected_numeric = set(
            range(
                1,
                expected_count + 1,
            )
        )

        missing_numeric = sorted(
            expected_numeric
            - found_numeric
        )

        missing = [
            str(item)
            for item in missing_numeric
        ]

    is_valid = (
        (
            expected_count is None
            or not missing
        )
        and not duplicates
        and not out_of_order
        and has_continuity
    )

    return ArticleValidation(
        file=str(file_path),
        total_expected=(
            expected_count or 0
        ),
        total_found=total_found,
        ids=ids,
        missing=missing,
        duplicates=duplicates,
        out_of_order=out_of_order,
        has_continuity=(
            has_continuity
        ),
        is_valid=is_valid,
    )


def validate_articles(
    repo: Path,
) -> List[ArticleValidation]:

    results: List[
        ArticleValidation
    ] = []

    doc_specs = {
        "docs/0.4.md": 61,
        "docs/0.5.md": 73,
        "docs/changelog.md": None,
        "docs/rules.md": None,
        "docs/decisions.md": None,
    }

    for (
        rel_path,
        expected,
    ) in doc_specs.items():

        target = (
            repo / rel_path
        )

        results.append(
            validate_articles_in_file(
                target,
                expected,
            )
        )

    return results


def print_article_validation(
    results: List[
        ArticleValidation
    ],
) -> None:

    print("=" * 72)
    print(
        "Nazm Dad — Article Validation"
    )
    print("=" * 72)

    all_valid = True

    for result in results:
        status = (
            "✅"
            if result.is_valid
            else "❌"
        )

        print(
            f"\n{status} {result.file}"
        )

        print(
            "   مواد یافت‌شده: "
            f"{result.total_found}"
        )

        if result.total_expected:
            print(
                "   مواد مورد انتظار: "
                f"{result.total_expected}"
            )

        if result.duplicates:
            print(
                "   ⚠️ تکراری: "
                + ", ".join(
                    result.duplicates
                )
            )
            all_valid = False

        if result.out_of_order:
            print(
                "   ⚠️ خارج از ترتیب: "
                + ", ".join(
                    result.out_of_order
                )
            )
            all_valid = False

        if (
            result.missing
            and result.total_expected
        ):
            print(
                "   ⚠️ مفقود: "
                + ", ".join(
                    result.missing[:10]
                )
            )

            if len(
                result.missing
            ) > 10:

                print(
                    "      ... و "
                    f"{len(result.missing) - 10} "
                    "مورد دیگر"
                )

            all_valid = False

        if (
            not result.has_continuity
            and result.total_found > 1
        ):
            print(
                "   ⚠️ ترتیب مواد "
                "پیوسته نیست"
            )

            all_valid = False

        if result.is_valid:
            print(
                "   ✅ ساختار مواد صحیح است"
            )

    print(
        "\n" + "=" * 72
    )

    print(
        (
            "✅ همه اسناد معتبر هستند"
            if all_valid
            else (
                "❌ برخی اسناد "
                "مشکل دارند"
            )
        )
    )


def article_validation_payload(
    results: List[
        ArticleValidation
    ],
) -> Dict[str, Any]:

    return {
        "schema": 1,
        "tool": (
            "nazm-dad-project-status"
        ),
        "version": TOOL_VERSION,
        "timestamp": utc_timestamp(),
        "results": [
            asdict(result)
            for result in results
        ],
        "all_valid": all(
            result.is_valid
            for result in results
        ),
    }


# ============================================================
# Link Checker
# ============================================================

MARKDOWN_LINK_PATTERN = re.compile(
    r"\[[^\]]+\]\(([^)]+)\)"
)


def check_links_in_file(
    file_path: Path,
    repo: Path,
) -> List[LinkCheckResult]:

    results: List[
        LinkCheckResult
    ] = []

    if not file_path.exists():
        return results

    try:
        lines = file_path.read_text(
            encoding="utf-8",
            errors="replace",
        ).splitlines()

    except OSError:
        return results

    repo_root = repo.resolve()

    for (
        line_num,
        line,
    ) in enumerate(
        lines,
        start=1,
    ):

        for match in (
            MARKDOWN_LINK_PATTERN
            .finditer(line)
        ):
            target = (
                match.group(1)
                .strip()
            )

            if target.startswith(
                (
                    "http://",
                    "https://",
                    "mailto:",
                    "tel:",
                    "#",
                )
            ):
                results.append(
                    LinkCheckResult(
                        file=rel(
                            file_path,
                            repo,
                        ),
                        line=line_num,
                        target=target,
                        status="external",
                        description=(
                            "External link "
                            "(skipped)"
                        ),
                    )
                )

                continue

            clean_target = (
                target.split(
                    "#",
                    1,
                )[0]
            )

            if not clean_target:
                continue

            resolved = (
                file_path.parent
                / clean_target
            ).resolve()

            try:
                resolved.relative_to(
                    repo_root
                )

            except ValueError:
                results.append(
                    LinkCheckResult(
                        file=rel(
                            file_path,
                            repo,
                        ),
                        line=line_num,
                        target=target,
                        status="broken",
                        description=(
                            "Link escapes "
                            "repository"
                        ),
                    )
                )

                continue

            if resolved.exists():
                results.append(
                    LinkCheckResult(
                        file=rel(
                            file_path,
                            repo,
                        ),
                        line=line_num,
                        target=target,
                        status="ok",
                        description=(
                            "Link exists"
                        ),
                    )
                )

            else:
                results.append(
                    LinkCheckResult(
                        file=rel(
                            file_path,
                            repo,
                        ),
                        line=line_num,
                        target=target,
                        status="broken",
                        description=(
                            "Link target "
                            "not found"
                        ),
                    )
                )

    return results


def check_all_links(
    repo: Path,
) -> List[LinkCheckResult]:

    results: List[
        LinkCheckResult
    ] = []

    docs_dir = repo / "docs"

    if not docs_dir.exists():
        return results

    for md_file in (
        docs_dir.rglob("*.md")
    ):
        results.extend(
            check_links_in_file(
                md_file,
                repo,
            )
        )

    return results


def print_link_results(
    results: List[
        LinkCheckResult
    ],
) -> None:

    print("=" * 72)
    print(
        "Nazm Dad — Link Check"
    )
    print("=" * 72)

    broken = [
        result
        for result in results
        if result.status == "broken"
    ]

    ok = [
        result
        for result in results
        if result.status == "ok"
    ]

    external = [
        result
        for result in results
        if result.status == "external"
    ]

    print(
        f"\n✅ OK: {len(ok)}"
    )

    print(
        f"🌐 External: {len(external)}"
    )

    print(
        f"❌ Broken: {len(broken)}"
    )

    if broken:
        print(
            "\n❌ لینک‌های شکسته:"
        )

        for result in broken:
            print(
                f"  {result.file}:"
                f"{result.line} -> "
                f"{result.target}"
            )

    print(
        "\n" + "=" * 72
    )


def link_payload(
    results: List[
        LinkCheckResult
    ],
) -> Dict[str, Any]:

    return {
        "schema": 1,
        "tool": (
            "nazm-dad-project-status"
        ),
        "version": TOOL_VERSION,
        "timestamp": utc_timestamp(),
        "stats": {
            "total": len(results),
            "ok": len(
                [
                    result
                    for result
                    in results
                    if (
                        result.status
                        == "ok"
                    )
                ]
            ),
            "external": len(
                [
                    result
                    for result
                    in results
                    if (
                        result.status
                        == "external"
                    )
                ]
            ),
            "broken": len(
                [
                    result
                    for result
                    in results
                    if (
                        result.status
                        == "broken"
                    )
                ]
            ),
        },
        "broken": [
            asdict(result)
            for result in results
            if result.status == "broken"
        ],
    }


# ============================================================
# Summary
# ============================================================

def build_summary(
    repo: Path,
    core: str,
) -> ProjectSummary:

    branch = git_branch(repo)

    clean_state = git_is_clean(
        repo
    )

    is_clean = (
        clean_state
        if clean_state is not None
        else False
    )

    commit = git_commit_short(
        repo
    )

    commit_date = git_commit_date(
        repo
    )

    health_score: Optional[
        int
    ] = None

    try:
        health_proc = capture_core(
            repo,
            core,
            [
                "--health",
                "--no-progress",
            ],
            timeout_seconds=(
                STRICT_TIMEOUT_SECONDS
            ),
        )

        if health_proc.returncode in (
            EXIT_OK,
            EXIT_VALIDATION_FAILED,
        ):
            health_score = (
                extract_health_score(
                    health_proc.stdout
                )
            )

    except (
        FileNotFoundError,
        ValueError,
        OSError,
        subprocess.TimeoutExpired,
    ):
        pass

    doc_dir = repo / "docs"

    total_documents = 0
    valid_documents = 0
    placeholder_documents = 0
    invalid_documents = 0
    total_articles = 0

    if doc_dir.exists():
        for relative in (
            DEFAULT_REPAIR_FILES
        ):
            target = (
                repo / relative
            )

            total_documents += 1

            if (
                not target.exists()
                or not target.is_file()
            ):
                invalid_documents += 1
                continue

            try:
                content = (
                    target.read_text(
                        encoding="utf-8",
                        errors="strict",
                    )
                )

                if (
                    "placeholder"
                    in content.lower()
                    or "در انتظار"
                    in content
                ):
                    placeholder_documents += 1

                else:
                    valid_documents += 1

                    total_articles += (
                        content.count(
                            "ماده"
                        )
                    )

            except (
                OSError,
                UnicodeDecodeError,
            ):
                invalid_documents += 1

    ahead, behind = (
        git_ahead_behind(repo)
    )

    return ProjectSummary(
        branch=(
            branch
            or "detached"
        ),
        is_clean=is_clean,
        commit=(
            commit
            or "unknown"
        ),
        commit_date=(
            commit_date
            or ""
        ),
        total_documents=(
            total_documents
        ),
        valid_documents=(
            valid_documents
        ),
        placeholder_documents=(
            placeholder_documents
        ),
        invalid_documents=(
            invalid_documents
        ),
        total_articles=(
            total_articles
        ),
        health_score=health_score,
        health_grade=(
            extract_health_grade(
                health_score
            )
        ),
        changes_ahead=ahead,
        changes_behind=behind,
    )


def summary_payload(
    summary: ProjectSummary,
) -> Dict[str, Any]:

    return {
        "schema": 1,
        "tool": (
            "nazm-dad-project-status"
        ),
        "version": TOOL_VERSION,
        "timestamp": utc_timestamp(),
        "git": {
            "branch": summary.branch,
            "clean": summary.is_clean,
            "commit": summary.commit,
            "commit_date": (
                summary.commit_date
            ),
            "ahead": (
                summary.changes_ahead
            ),
            "behind": (
                summary.changes_behind
            ),
        },
        "documents": {
            "total": (
                summary.total_documents
            ),
            "valid": (
                summary.valid_documents
            ),
            "placeholder": (
                summary
                .placeholder_documents
            ),
            "invalid": (
                summary.invalid_documents
            ),
            "total_articles": (
                summary.total_articles
            ),
        },
        "health": {
            "score": (
                summary.health_score
            ),
            "grade": (
                summary.health_grade
            ),
        },
    }


def print_summary(
    summary: ProjectSummary,
) -> None:

    print("=" * 72)
    print(
        f"Nazm Dad — Summary v{TOOL_VERSION}"
    )
    print("=" * 72)

    print("\n📦 Git:")

    print(
        f"  Branch: {summary.branch}"
    )

    print(
        "  Working tree: "
        + (
            "✅ clean"
            if summary.is_clean
            else "❌ dirty"
        )
    )

    print(
        f"  Commit: "
        f"{summary.commit} "
        f"({summary.commit_date})"
    )

    if (
        summary.changes_ahead
        or summary.changes_behind
    ):
        print(
            "  Ahead/Behind: "
            f"{summary.changes_ahead}/"
            f"{summary.changes_behind}"
        )

    print("\n📄 Documents:")

    print(
        "  Total: "
        f"{summary.total_documents}"
    )

    if summary.total_documents:
        progress = (
            summary.valid_documents
            / summary.total_documents
            * 100
        )

        bar_length = 20

        filled = int(
            bar_length
            * progress
            / 100
        )

        bar = (
            "█" * filled
            + "░"
            * (
                bar_length
                - filled
            )
        )

        print(
            f"  Progress: "
            f"[{bar}] "
            f"{progress:.1f}%"
        )

    else:
        print(
            "  Progress: N/A"
        )

    print(
        "  ✅ Valid: "
        f"{summary.valid_documents}"
    )

    print(
        "  ⏳ Placeholder: "
        f"{summary.placeholder_documents}"
    )

    print(
        "  ❌ Invalid: "
        f"{summary.invalid_documents}"
    )

    print(
        "  📊 Total articles "
        "(rough): "
        f"{summary.total_articles}"
    )

    print("\n🏥 Health:")

    if (
        summary.health_score
        is not None
    ):
        print(
            "  Score: "
            f"{summary.health_score}/100"
        )

        print(
            "  Grade: "
            f"{summary.health_grade}"
        )

    else:
        print(
            "  Score: N/A"
        )

    print(
        "\n" + "=" * 72
    )


# ============================================================
# History
# ============================================================

def show_history(
    repo: Path,
    max_count: int = 10,
) -> int:

    print("=" * 72)
    print(
        f"Nazm Dad — History v{TOOL_VERSION}"
    )
    print("=" * 72)

    doc_files = [
        "docs/0.4.md",
        "docs/0.5.md",
        "docs/changelog.md",
    ]

    for doc_file in doc_files:
        target = (
            repo / doc_file
        )

        if not target.exists():
            print(
                f"\n❌ {doc_file}: "
                "not found"
            )
            continue

        print(
            f"\n📄 {doc_file}:"
        )

        entries = git_file_history(
            repo,
            doc_file,
            max_count,
        )

        if not entries:
            print(
                "  No history found"
            )
            continue

        for entry in entries:
            print(
                f"  {entry['commit']} "
                f"| {entry['date'][:10]} "
                f"| {entry['message']}"
            )

    print(
        "\n" + "=" * 72
    )

    return EXIT_OK


# ============================================================
# Repair Preview
# ============================================================

def git_file_at_ref(
    repo: Path,
    ref: str,
    relative: str,
) -> Optional[bytes]:

    relative = normalize_rel_path(
        relative
    )

    try:
        proc = subprocess.run(
            [
                "git",
                "-C",
                str(repo),
                "show",
                f"{ref}:{relative}",
            ],
            capture_output=True,
            timeout=30,
        )

        if proc.returncode != 0:
            return None

        return proc.stdout

    except (
        OSError,
        subprocess.TimeoutExpired,
    ):
        return None


def source_file_bytes(
    source_dir: Path,
    relative: str,
) -> Optional[bytes]:

    normalized = normalize_rel_path(
        relative
    )

    try:
        root = (
            source_dir.resolve()
        )

        target = (
            root / normalized
        ).resolve()

        target.relative_to(root)

    except (
        ValueError,
        OSError,
    ):
        return None

    if (
        not target.exists()
        or not target.is_file()
    ):
        return None

    try:
        return target.read_bytes()

    except OSError:
        return None


def text_diff(
    current: bytes,
    replacement: bytes,
    *,
    current_name: str,
    replacement_name: str,
) -> str:

    import difflib

    current_text = (
        current
        .decode(
            "utf-8",
            errors="replace",
        )
        .splitlines(
            keepends=True
        )
    )

    replacement_text = (
        replacement
        .decode(
            "utf-8",
            errors="replace",
        )
        .splitlines(
            keepends=True
        )
    )

    return "".join(
        difflib.unified_diff(
            current_text,
            replacement_text,
            fromfile=current_name,
            tofile=replacement_name,
        )
    )


def repair_preview(
    repo: Path,
    files: Sequence[str],
    *,
    from_ref: Optional[str] = None,
    source_dir: Optional[Path] = None,
) -> Tuple[
    bool,
    List[RepairPreviewItem],
]:

    if (
        bool(from_ref)
        == bool(source_dir)
    ):
        raise ValueError(
            (
                "repair preview requires "
                "exactly one source: "
                "--repair-from-ref or "
                "--repair-source-dir"
            )
        )

    items: List[
        RepairPreviewItem
    ] = []

    all_sources_found = True

    for raw_relative in files:
        relative = (
            normalize_rel_path(
                raw_relative
            )
        )

        try:
            target = safe_repo_path(
                repo,
                relative,
            )

        except ValueError as exc:
            all_sources_found = False

            items.append(
                RepairPreviewItem(
                    path=relative,
                    source="invalid",
                    target_exists=False,
                    source_exists=False,
                    changed=False,
                    description=(
                        "invalid path: "
                        f"{exc}"
                    ),
                )
            )

            continue

        if from_ref:
            source_name = (
                f"git:{from_ref}:"
                f"{relative}"
            )

            replacement = (
                git_file_at_ref(
                    repo,
                    from_ref,
                    relative,
                )
            )

        else:
            assert (
                source_dir
                is not None
            )

            source_name = str(
                source_dir
                / Path(relative)
            )

            replacement = (
                source_file_bytes(
                    source_dir,
                    relative,
                )
            )

        if replacement is None:
            all_sources_found = False

            items.append(
                RepairPreviewItem(
                    path=relative,
                    source=source_name,
                    target_exists=(
                        target.exists()
                    ),
                    source_exists=False,
                    changed=False,
                    description=(
                        "source file "
                        "not found"
                    ),
                )
            )

            continue

        try:
            current = (
                target.read_bytes()
                if target.exists()
                else b""
            )

        except OSError:
            current = b""

        changed = (
            current
            != replacement
        )

        diff = ""

        if changed:
            diff = text_diff(
                current,
                replacement,
                current_name=relative,
                replacement_name=(
                    source_name
                ),
            )

        items.append(
            RepairPreviewItem(
                path=relative,
                source=source_name,
                target_exists=(
                    target.exists()
                ),
                source_exists=True,
                changed=changed,
                description=(
                    "would be replaced"
                    if changed
                    else "already identical"
                ),
                diff=diff,
            )
        )

    return (
        all_sources_found,
        items,
    )


def print_repair_preview(
    items: Sequence[
        RepairPreviewItem
    ],
) -> None:

    print("=" * 72)
    print(
        "Nazm Dad — Repair Preview"
    )
    print("=" * 72)
    print(
        "DRY-RUN: no file will "
        "be modified."
    )
    print()

    for item in items:
        if not item.source_exists:
            icon = "❌"

        elif item.changed:
            icon = "🟡"

        else:
            icon = "✅"

        print(
            f"{icon} {item.path}: "
            f"{item.description}"
        )

        print(
            f"   source: {item.source}"
        )

        if item.diff:
            print()

            for line in (
                item.diff.splitlines()
            ):
                print(
                    f"   {line}"
                )

            print()


# ============================================================
# Hash Manifest
# ============================================================

def resolve_hash_files(
    repo: Path,
    requested: Optional[
        Sequence[str]
    ],
) -> List[str]:

    if requested:
        candidates = [
            normalize_rel_path(
                item
            )
            for item in requested
        ]

    else:
        candidates = [
            *DEFAULT_REPAIR_FILES,
            DEFAULT_CORE,
        ]

    unique: List[str] = []
    seen: Set[str] = set()

    for relative in candidates:
        if relative in seen:
            continue

        seen.add(relative)

        try:
            path = safe_repo_path(
                repo,
                relative,
            )

        except ValueError:
            continue

        if (
            path.exists()
            and path.is_file()
        ):
            unique.append(
                normalize_rel_path(
                    relative
                )
            )

    return unique


def build_manifest(
    repo: Path,
    files: Sequence[str],
) -> Dict[str, Any]:

    records: List[
        Dict[str, Any]
    ] = []

    for relative in files:
        normalized = (
            normalize_rel_path(
                relative
            )
        )

        try:
            target = safe_repo_path(
                repo,
                normalized,
            )

        except ValueError as exc:
            raise ValueError(
                "manifest file escapes "
                "repository: "
                f"{relative}"
            ) from exc

        if (
            not target.exists()
            or not target.is_file()
        ):
            raise FileNotFoundError(
                (
                    "file not found for "
                    "manifest: "
                    f"{relative}"
                )
            )

        records.append(
            {
                "path": normalized,
                "sha256": (
                    sha256_file(target)
                ),
                "size": (
                    target.stat()
                    .st_size
                ),
            }
        )

    return {
        "schema": 1,
        "algorithm": "sha256",
        "tool": (
            "nazm-dad-project-status"
        ),
        "version": TOOL_VERSION,
        "created_at": utc_timestamp(),
        "commit": git_commit(repo),
        "files": records,
    }


def write_manifest(
    repo: Path,
    manifest_path: Path,
    files: Sequence[str],
) -> bool:

    try:
        payload = build_manifest(
            repo,
            files,
        )

    except (
        ValueError,
        FileNotFoundError,
        OSError,
    ) as exc:

        print(
            f"❌ {exc}",
            file=sys.stderr,
        )

        return False

    try:
        manifest_path.parent.mkdir(
            parents=True,
            exist_ok=True,
        )

        manifest_path.write_text(
            json.dumps(
                payload,
                ensure_ascii=False,
                indent=2,
            )
            + "\n",
            encoding="utf-8",
        )

    except OSError as exc:
        print(
            "❌ failed to write "
            f"manifest: {exc}",
            file=sys.stderr,
        )

        return False

    print(
        "✅ hash manifest written: "
        f"{rel(manifest_path, repo)}"
    )

    for record in payload["files"]:
        print(
            f"✅ {record['path']}: "
            f"{record['sha256']}"
        )

    return True


def verify_manifest(
    repo: Path,
    manifest_path: Path,
) -> bool:

    if not manifest_path.exists():
        print(
            "❌ manifest not found: "
            f"{rel(manifest_path, repo)}"
        )

        return False

    try:
        data = json.loads(
            manifest_path.read_text(
                encoding="utf-8"
            )
        )

    except (
        OSError,
        json.JSONDecodeError,
    ) as exc:

        print(
            f"❌ invalid manifest: {exc}",
            file=sys.stderr,
        )

        return False

    records = data.get(
        "files",
        [],
    )

    if not isinstance(
        records,
        list,
    ):
        print(
            (
                "❌ invalid manifest: "
                "'files' must be a list"
            ),
            file=sys.stderr,
        )

        return False

    ok = True

    for record in records:
        if not isinstance(
            record,
            dict,
        ):
            print(
                "❌ invalid manifest record",
                file=sys.stderr,
            )

            ok = False
            continue

        relative = record.get(
            "path"
        )

        expected = record.get(
            "sha256"
        )

        if (
            not isinstance(
                relative,
                str,
            )
            or not isinstance(
                expected,
                str,
            )
        ):
            print(
                (
                    "❌ invalid manifest "
                    "record fields"
                ),
                file=sys.stderr,
            )

            ok = False
            continue

        try:
            path = safe_repo_path(
                repo,
                relative,
            )

        except ValueError as exc:
            print(
                f"❌ {relative}: {exc}"
            )

            ok = False
            continue

        if (
            not path.exists()
            or not path.is_file()
        ):
            print(
                f"❌ {relative}: missing"
            )

            ok = False
            continue

        try:
            actual = sha256_file(
                path
            )

        except OSError as exc:
            print(
                f"❌ {relative}: "
                "unable to hash: "
                f"{exc}"
            )

            ok = False
            continue

        if actual != expected:
            print(
                f"❌ {relative}: "
                "hash mismatch"
            )

            print(
                f"   expected: "
                f"{expected}"
            )

            print(
                f"   actual:   "
                f"{actual}"
            )

            ok = False

        else:
            print(
                f"✅ {relative}: verified"
            )

    return ok


# ============================================================
# Strict
# ============================================================

def run_strict(
    repo: Path,
    core: str,
    *,
    timeout_seconds: float = (
        STRICT_TIMEOUT_SECONDS
    ),
    quiet: bool = False,
    include_health: bool = True,
) -> Tuple[
    bool,
    Dict[str, StrictResult],
]:

    commands: Dict[
        str,
        List[str],
    ] = {
        "validate_docs": [
            "--validate-docs",
            "--no-progress",
        ],
        "check_links": [
            "--check-links",
            "--no-progress",
        ],
    }

    if include_health:
        commands["health"] = [
            "--health",
            "--no-progress",
        ]

    results: Dict[
        str,
        StrictResult,
    ] = {}

    for (
        name,
        arguments,
    ) in commands.items():

        if not quiet:
            print("=" * 72)
            print(
                f"STRICT: {name}"
            )
            print("=" * 72)

        started = (
            time.monotonic()
        )

        code = run_core(
            repo,
            core,
            arguments,
            timeout_seconds=(
                timeout_seconds
            ),
            quiet=quiet,
        )

        elapsed = (
            time.monotonic()
            - started
        )

        results[name] = (
            StrictResult(
                exit_code=code,
                duration_seconds=(
                    elapsed
                ),
            )
        )

        if not quiet:
            if code == EXIT_OK:
                print(
                    f"✅ {name}: "
                    "exit=0 "
                    f"({elapsed:.2f}s)"
                )

            elif (
                code
                == TIMEOUT_EXIT_CODE
            ):
                print(
                    f"⏱️ {name}: "
                    "TIMEOUT "
                    f"({elapsed:.2f}s)"
                )

            else:
                print(
                    f"❌ {name}: "
                    f"exit={code} "
                    f"({elapsed:.2f}s)"
                )

    if not quiet:
        print("=" * 72)
        print(
            "STRICT: doctor"
        )
        print("=" * 72)

    started = (
        time.monotonic()
    )

    try:
        report = doctor(
            repo,
            core,
        )

        doctor_code = (
            EXIT_OK
            if report.ok
            else EXIT_VALIDATION_FAILED
        )

    except KeyboardInterrupt:
        doctor_code = (
            EXIT_INTERRUPTED
        )

    except Exception as exc:
        doctor_code = (
            EXIT_RUNTIME_ERROR
        )

        if not quiet:
            print(
                (
                    "❌ doctor failed: "
                    f"{exc}"
                ),
                file=sys.stderr,
            )

    elapsed = (
        time.monotonic()
        - started
    )

    results["doctor"] = (
        StrictResult(
            exit_code=doctor_code,
            duration_seconds=elapsed,
        )
    )

    if not quiet:
        print()
        print("=" * 72)
        print(
            "STRICT SUMMARY"
        )
        print("=" * 72)

        ordered_names = [
            *commands.keys(),
            "doctor",
        ]

        for name in ordered_names:
            result = results[name]

            if (
                result.exit_code
                == EXIT_OK
            ):
                print(
                    f"✅ {name}: "
                    "exit=0 "
                    f"({result.duration_seconds:.2f}s)"
                )

            elif (
                result.exit_code
                == TIMEOUT_EXIT_CODE
            ):
                print(
                    f"⏱️ {name}: "
                    "TIMEOUT "
                    f"({result.duration_seconds:.2f}s)"
                )

            else:
                print(
                    f"❌ {name}: "
                    f"exit={result.exit_code} "
                    f"({result.duration_seconds:.2f}s)"
                )

    success = all(
        (
            item.exit_code
            == EXIT_OK
        )
        for item in (
            results.values()
        )
    )

    return (
        success,
        results,
    )


def strict_results_payload(
    results: Dict[
        str,
        StrictResult,
    ],
) -> Dict[str, Any]:

    return {
        name: {
            "exit_code": (
                result.exit_code
            ),
            "duration_seconds": round(
                result.duration_seconds,
                6,
            ),
            "timed_out": (
                result.exit_code
                == TIMEOUT_EXIT_CODE
            ),
        }
        for (
            name,
            result,
        ) in results.items()
    }


# ============================================================
# CI
# ============================================================

def ci_report(
    repo: Path,
    core: str,
    *,
    strict: bool,
    timeout_seconds: float,
    threshold: int = 90,
) -> Dict[str, Any]:

    report = doctor(
        repo,
        core,
    )

    payload: Dict[
        str,
        Any,
    ] = {
        "schema": 1,
        "tool": (
            "nazm-dad-project-status"
        ),
        "version": TOOL_VERSION,
        "timestamp": utc_timestamp(),
        "repo": str(repo),
        "branch": git_branch(repo),
        "commit": git_commit(repo),
        "timeout_seconds": (
            timeout_seconds
        ),
        "threshold": threshold,
        "threshold_enforced": (
            strict
        ),
        "doctor": {
            "ok": report.ok,
            "checks": [
                asdict(item)
                for item
                in report.checks
            ],
        },
    }

    if strict:
        (
            success,
            results,
        ) = run_strict(
            repo,
            core,
            timeout_seconds=(
                timeout_seconds
            ),
            quiet=True,
            include_health=False,
        )

        payload["strict"] = {
            "ok": success,
            "results": (
                strict_results_payload(
                    results
                )
            ),
        }

    health_ok = False
    health_score: Optional[
        int
    ] = None

    try:
        health_proc = capture_core(
            repo,
            core,
            [
                "--health",
                "--no-progress",
            ],
            timeout_seconds=(
                timeout_seconds
            ),
        )

        payload["health"] = {
            "exit_code": (
                health_proc.returncode
            ),
            "stdout": (
                health_proc.stdout
            ),
            "stderr": (
                health_proc.stderr
            ),
        }

        if (
            health_proc.returncode
            in (
                EXIT_OK,
                EXIT_VALIDATION_FAILED,
            )
        ):
            health_score = (
                extract_health_score(
                    health_proc.stdout
                )
            )

            if (
                health_score
                is not None
            ):
                payload[
                    "health_score"
                ] = health_score

                health_ok = (
                    health_score
                    >= threshold
                )

    except subprocess.TimeoutExpired:
        payload["health"] = {
            "exit_code": (
                TIMEOUT_EXIT_CODE
            ),
            "timed_out": True,
        }

    except (
        FileNotFoundError,
        ValueError,
        OSError,
    ) as exc:

        payload["health"] = {
            "error": str(exc),
        }

    doctor_ok = bool(
        payload["doctor"]["ok"]
    )

    strict_ok = bool(
        payload.get(
            "strict",
            {"ok": True},
        )["ok"]
    )

    payload["health_ok"] = (
        health_ok
    )

    if not strict:
        payload["ok"] = (
            doctor_ok
        )

    else:
        payload["ok"] = (
            doctor_ok
            and strict_ok
            and health_ok
        )

    return payload


def write_json_output(
    payload: Dict[str, Any],
    destination: Optional[str],
) -> bool:

    text = (
        json.dumps(
            payload,
            ensure_ascii=False,
            indent=2,
        )
        + "\n"
    )

    if destination in (
        None,
        "",
        "-",
    ):
        print(
            text,
            end="",
        )

        return True

    target = (
        Path(destination)
        .expanduser()
        .resolve()
    )

    try:
        target.parent.mkdir(
            parents=True,
            exist_ok=True,
        )

        target.write_text(
            text,
            encoding="utf-8",
        )

    except OSError as exc:
        print(
            "❌ failed to write "
            f"JSON: {exc}",
            file=sys.stderr,
        )

        return False

    print(
        f"✅ CI JSON written: "
        f"{target}"
    )

    return True


# ============================================================
# Report
# ============================================================

def generate_report(
    repo: Path,
    core: str,
    timeout_seconds: float,
) -> Dict[str, Any]:

    report = doctor(
        repo,
        core,
    )

    try:
        health_proc = capture_core(
            repo,
            core,
            [
                "--health",
                "--no-progress",
            ],
            timeout_seconds=(
                timeout_seconds
            ),
        )

        score = (
            extract_health_score(
                health_proc.stdout
            )
        )

        health_data: Dict[
            str,
            Any,
        ] = {
            "exit_code": (
                health_proc.returncode
            ),
            "score": score,
            "stdout": (
                health_proc.stdout
            ),
            "stderr": (
                health_proc.stderr
            ),
        }

    except subprocess.TimeoutExpired:
        health_data = {
            "exit_code": (
                TIMEOUT_EXIT_CODE
            ),
            "timed_out": True,
            "score": None,
        }

    except (
        FileNotFoundError,
        ValueError,
        OSError,
    ) as exc:

        health_data = {
            "error": str(exc),
            "score": None,
        }

    article_results = (
        validate_articles(repo)
    )

    article_stats = {
        "total_files": len(
            article_results
        ),
        "valid_files": sum(
            1
            for result
            in article_results
            if result.is_valid
        ),
        "total_articles": sum(
            result.total_found
            for result
            in article_results
        ),
        "valid": all(
            result.is_valid
            for result
            in article_results
        ),
        "results": [
            asdict(result)
            for result
            in article_results
        ],
    }

    return {
        "timestamp": utc_timestamp(),
        "version": TOOL_VERSION,
        "repo": str(repo),
        "branch": git_branch(repo),
        "commit": git_commit(repo),
        "clean": git_is_clean(repo),
        "doctor": {
            "ok": report.ok,
            "issues": report.issues,
            "warnings": (
                report.warnings
            ),
        },
        "health": health_data,
        "articles": article_stats,
    }


# ============================================================
# Compare
# ============================================================

def compare_status(
    repo: Path,
    ref: str,
    core: str,
) -> int:

    print("=" * 72)
    print(
        "Nazm Dad — Compare "
        f"with ref: {ref}"
    )
    print("=" * 72)

    if not git_ref_exists(
        repo,
        ref,
    ):
        print(
            "❌ Git ref not found: "
            f"{ref}",
            file=sys.stderr,
        )

        return (
            EXIT_VALIDATION_FAILED
        )

    print(
        "\n📋 Current status:"
    )

    current_report = doctor(
        repo,
        core,
    )

    print_doctor(
        current_report
    )

    print(
        "\n" + "-" * 72
    )

    print(
        "📋 Files changed "
        f"relative to {ref}:"
    )

    print("-" * 72)

    try:
        (
            sources_ok,
            items,
        ) = repair_preview(
            repo,
            DEFAULT_REPAIR_FILES,
            from_ref=ref,
        )

        for item in items:
            if not item.source_exists:
                print(
                    f"❌ {item.path}: "
                    "source file not "
                    f"found at {ref}"
                )

            elif item.changed:
                print(
                    f"🟡 {item.path}: "
                    f"differs from {ref}"
                )

                diff_lines = (
                    item.diff
                    .splitlines()
                )

                for line in (
                    diff_lines[:10]
                ):
                    print(
                        f"   {line}"
                    )

                if (
                    len(diff_lines)
                    > 10
                ):
                    remaining = (
                        len(diff_lines)
                        - 10
                    )

                    print(
                        "   ... "
                        f"({remaining} "
                        "more lines)"
                    )

            else:
                print(
                    f"✅ {item.path}: "
                    f"identical to {ref}"
                )

        if not sources_ok:
            print()

            print(
                f"❌ ref '{ref}' "
                "could not provide "
                "all required files."
            )

            return (
                EXIT_VALIDATION_FAILED
            )

    except ValueError as exc:
        print(
            f"❌ {exc}",
            file=sys.stderr,
        )

        return EXIT_USAGE_ERROR

    print(
        "\n" + "=" * 72
    )

    return EXIT_OK


# ============================================================
# Watch
# ============================================================

class WatchMode:
    IGNORED_DIRS = {
        ".git",
        "__pycache__",
        "venv",
        ".venv",
        "env",
        ".env",
        "node_modules",
        ".pytest_cache",
        ".mypy_cache",
        "dist",
        "build",
    }

    def __init__(
        self,
        repo: Path,
        core: str,
        interval: float = 5.0,
        patterns: Optional[
            List[str]
        ] = None,
        timeout_seconds: float = (
            STRICT_TIMEOUT_SECONDS
        ),
        custom_commands: Optional[
            List[str]
        ] = None,
        quiet: bool = False,
    ):
        self.repo = repo
        self.core = core
        self.interval = interval

        self.patterns = (
            patterns
            or [
                "*.md",
                "*.py",
                "*.json",
                "*.txt",
            ]
        )

        self.timeout_seconds = (
            timeout_seconds
        )

        self.custom_commands = (
            custom_commands or []
        )

        self.quiet = quiet

        self._last_mtime: Dict[
            Path,
            float,
        ] = {}

        self._running = True

    def _is_ignored(
        self,
        path: Path,
    ) -> bool:

        return any(
            part
            in self.IGNORED_DIRS
            for part
            in path.parts
        )

    def scan_files(
        self,
    ) -> List[Path]:

        files: List[Path] = []

        for pattern in self.patterns:
            try:
                matches = (
                    self.repo.glob(
                        f"**/{pattern}"
                    )
                )

                for path in matches:
                    if (
                        path.is_file()
                        and not (
                            self._is_ignored(
                                path
                            )
                        )
                    ):
                        files.append(
                            path
                        )

            except OSError:
                continue

        return sorted(
            set(files)
        )

    def has_changes(
        self,
    ) -> List[Path]:

        changed: List[Path] = []

        current_files = (
            self.scan_files()
        )

        current_set = set(
            current_files
        )

        for file_path in (
            current_files
        ):
            try:
                mtime = (
                    file_path.stat()
                    .st_mtime
                )

                previous = (
                    self._last_mtime
                    .get(file_path)
                )

                if (
                    previous is not None
                    and mtime > previous
                ):
                    changed.append(
                        file_path
                    )

                elif previous is None:
                    changed.append(
                        file_path
                    )

                self._last_mtime[
                    file_path
                ] = mtime

            except OSError:
                continue

        deleted = [
            path
            for path
            in list(
                self._last_mtime
            )
            if path not in current_set
        ]

        for path in deleted:
            changed.append(path)

            self._last_mtime.pop(
                path,
                None,
            )

        return changed

    def run(self) -> None:
        if not self.quiet:
            print("=" * 72)

            print(
                "Nazm Dad — Watch "
                f"Mode v{TOOL_VERSION}"
            )

            print("=" * 72)

            print(
                f"Repository: "
                f"{self.repo}"
            )

            print(
                "Patterns: "
                + ", ".join(
                    self.patterns
                )
            )

            print(
                "Interval: "
                f"{self.interval:g}s"
            )

            print(
                "Timeout: "
                f"{self.timeout_seconds:g}s"
            )

            if self.custom_commands:
                print(
                    "Custom commands: "
                    + ", ".join(
                        self.custom_commands
                    )
                )

            print(
                "Ignored dirs: "
                + ", ".join(
                    sorted(
                        self.IGNORED_DIRS
                    )
                )
            )

            print(
                "Press Ctrl+C to stop"
            )

            print("-" * 72)

        for file_path in (
            self.scan_files()
        ):
            try:
                self._last_mtime[
                    file_path
                ] = (
                    file_path.stat()
                    .st_mtime
                )
            except OSError:
                pass

        try:
            while self._running:
                time.sleep(
                    self.interval
                )

                changed = (
                    self.has_changes()
                )

                if not changed:
                    continue

                changed_names: List[
                    str
                ] = []

                for path in changed:
                    try:
                        changed_names.append(
                            rel(
                                path,
                                self.repo,
                            )
                        )

                    except OSError:
                        changed_names.append(
                            str(path)
                        )

                if self.quiet:
                    print(
                        "🔄 Changed: "
                        + ", ".join(
                            changed_names
                        )
                    )

                else:
                    print()

                    print(
                        "🔄 Changed: "
                        + ", ".join(
                            changed_names
                        )
                    )

                    print("-" * 72)
                    print(
                        "Running doctor..."
                    )

                report = doctor(
                    self.repo,
                    self.core,
                )

                if not self.quiet:
                    print_doctor(
                        report
                    )

                    print()

                    print(
                        "Running health "
                        "check..."
                    )

                run_core(
                    self.repo,
                    self.core,
                    [
                        "--health",
                        "--no-progress",
                    ],
                    timeout_seconds=(
                        self
                        .timeout_seconds
                    ),
                    quiet=self.quiet,
                )

                for cmd in (
                    self.custom_commands
                ):
                    if not self.quiet:
                        print()

                        print(
                            f"Running: {cmd}"
                        )

                    try:
                        subprocess.run(
                            cmd,
                            shell=True,
                            cwd=str(
                                self.repo
                            ),
                            timeout=(
                                self
                                .timeout_seconds
                            ),
                            stdout=(
                                subprocess
                                .DEVNULL
                                if self.quiet
                                else None
                            ),
                            stderr=(
                                subprocess
                                .DEVNULL
                                if self.quiet
                                else None
                            ),
                        )

                    except (
                        subprocess
                        .TimeoutExpired
                    ):
                        if not self.quiet:
                            print(
                                "⏱️ Command "
                                "timed out: "
                                f"{cmd}"
                            )

                    except OSError as exc:
                        if not self.quiet:
                            print(
                                "❌ Failed to "
                                f"run: {cmd} "
                                f"({exc})"
                            )

                if not self.quiet:
                    print("-" * 72)

        except KeyboardInterrupt:
            if not self.quiet:
                print(
                    "\n👋 Watch stopped."
                )


# ============================================================
# Auto-Fix
# ============================================================

def auto_fix(
    repo: Path,
    dry_run: bool = False,
) -> int:

    created: List[str] = []

    gitignore = (
        repo / ".gitignore"
    )

    if not gitignore.exists():
        content = (
            "# Python\n"
            "__pycache__/\n"
            "*.pyc\n"
            "*.pyo\n"
            "*.pyd\n"
            ".Python\n"
            "env/\n"
            "venv/\n"
            ".venv/\n"
            ".idea/\n"
            ".vscode/\n"
            ".DS_Store\n"
        )

        if dry_run:
            print(
                "[dry-run] would "
                "create: "
                f"{rel(gitignore, repo)}"
            )

        else:
            try:
                gitignore.write_text(
                    content,
                    encoding="utf-8",
                )

            except OSError as exc:
                print(
                    (
                        "❌ failed to "
                        "create .gitignore: "
                        f"{exc}"
                    ),
                    file=sys.stderr,
                )

                return (
                    EXIT_RUNTIME_ERROR
                )

            created.append(
                rel(
                    gitignore,
                    repo,
                )
            )

    readme = repo / "README.md"

    if not readme.exists():
        content = (
            "# Nazm Dad\n\n"
            "A constitutional "
            "framework for Iran.\n"
        )

        if dry_run:
            print(
                "[dry-run] would "
                "create: "
                f"{rel(readme, repo)}"
            )

        else:
            try:
                readme.write_text(
                    content,
                    encoding="utf-8",
                )

            except OSError as exc:
                print(
                    (
                        "❌ failed to "
                        "create README.md: "
                        f"{exc}"
                    ),
                    file=sys.stderr,
                )

                return (
                    EXIT_RUNTIME_ERROR
                )

            created.append(
                rel(
                    readme,
                    repo,
                )
            )

    if dry_run:
        if (
            gitignore.exists()
            and readme.exists()
        ):
            print(
                "[dry-run] no safe "
                "auto-fix changes "
                "required."
            )

        return EXIT_OK

    if created:
        print(
            "✅ Auto-fix created: "
            + ", ".join(created)
        )

    else:
        print(
            "✅ No missing recommended "
            "files to create."
        )

    return EXIT_OK


# ============================================================
# HTML Report
# ============================================================

def generate_html_report(
    repo: Path,
    payload: Dict[str, Any],
    output_path: Path,
) -> bool:

    def e(
        value: Any,
    ) -> str:
        return html.escape(
            str(value)
        )

    status_ok = payload.get(
        "ok",
        False,
    )

    status_class = (
        "pass"
        if status_ok
        else "fail-bg"
    )

    status_text = (
        "✅ PASS"
        if status_ok
        else "❌ FAIL"
    )

    doc_stats = (
        payload
        .get(
            "doctor",
            {},
        )
        .get(
            "checks",
            [],
        )
    )

    doc_checks = [
        check
        for check
        in doc_stats
        if str(
            check.get(
                "name",
                "",
            )
        ).startswith(
            "doc:"
        )
    ]

    total_docs = len(
        doc_checks
    )

    valid_docs = sum(
        1
        for check
        in doc_checks
        if check.get(
            "ok",
            False,
        )
    )

    percentage = (
        valid_docs
        / total_docs
        * 100
        if total_docs
        else 0
    )

    html_content = f"""<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Nazm Dad CI Report</title>
    <style>
        body {{
            font-family: sans-serif;
            margin: 20px;
            background: #f5f0e6;
        }}

        .container {{
            max-width: 900px;
            margin: auto;
            background: #fff;
            padding: 30px;
            border-radius: 12px;
        }}

        table {{
            width: 100%;
            border-collapse: collapse;
            margin: 15px 0;
        }}

        th, td {{
            padding: 8px 12px;
            border-bottom: 1px solid #ddd;
            text-align: left;
        }}

        .status {{
            display: inline-block;
            padding: 4px 12px;
            border-radius: 12px;
            font-weight: bold;
        }}

        .pass {{
            background: #d4edda;
            color: #155724;
        }}

        .fail-bg {{
            background: #f8d7da;
            color: #721c24;
        }}

        .progress-bar {{
            width: 100%;
            height: 20px;
            background: #eee;
            border-radius: 10px;
            overflow: hidden;
            margin: 5px 0;
        }}

        .progress-fill {{
            height: 100%;
            background: #28a745;
            border-radius: 10px;
        }}

        .chart-row {{
            display: flex;
            justify-content: space-between;
            margin: 4px 0;
        }}
    </style>
</head>
<body>
<div class="container">

<h1>Nazm Dad — CI Report</h1>

<p>
<strong>Version:</strong>
{e(TOOL_VERSION)}
</p>

<p>
<strong>Timestamp:</strong>
{e(payload.get("timestamp", ""))}
</p>

<p>
<strong>Status:</strong>
<span class="status {e(status_class)}">
{e(status_text)}
</span>
</p>

<hr>

<h2>Git</h2>

<p>
<strong>Branch:</strong>
{e(payload.get("branch", ""))}
</p>

<p>
<strong>Commit:</strong>
{e(str(payload.get("commit", ""))[:8])}
</p>

<hr>

<h2>Doctor</h2>

<p>
<strong>OK:</strong>
{"✅" if payload.get("doctor", {}).get("ok") else "❌"}
</p>

<h3>Checks</h3>

<table>
<tr>
<th>Check</th>
<th>Status</th>
<th>Detail</th>
</tr>
"""

    for check in (
        payload
        .get(
            "doctor",
            {},
        )
        .get(
            "checks",
            [],
        )
    ):
        if check.get(
            "warning"
        ):
            status = "⚠️"

        elif check.get(
            "ok"
        ):
            status = "✅"

        else:
            status = "❌"

        html_content += (
            "<tr>"
            f"<td>{e(check.get('name', ''))}</td>"
            f"<td>{e(status)}</td>"
            f"<td>{e(check.get('detail', ''))}</td>"
            "</tr>\n"
        )

    html_content += f"""
</table>

<h3>Document Status</h3>

<div class="progress-bar">
<div class="progress-fill"
style="width: {e(f'{percentage:.1f}')}%;">
</div>
</div>

<div class="chart-row">
<span>
Valid:
{e(valid_docs)}
/
{e(total_docs)}
</span>

<span>
{e(f'{percentage:.1f}')}%
</span>
</div>
"""

    if (
        payload.get(
            "health_score"
        )
        is not None
    ):
        score = int(
            payload[
                "health_score"
            ]
        )

        grade = (
            extract_health_grade(
                score
            )
        )

        if score >= 80:
            color = "#28a745"

        elif score >= 60:
            color = "#ffc107"

        else:
            color = "#dc3545"

        html_content += f"""
<h2>Health</h2>

<p>
<strong>Score:</strong>
{e(score)}/100
</p>

<p>
<strong>Grade:</strong>
{e(grade)}
</p>

<div class="progress-bar">
<div class="progress-fill"
style="width: {e(score)}%;
background: {e(color)};">
</div>
</div>
"""

    if payload.get(
        "strict"
    ):
        strict_ok = (
            payload
            .get(
                "strict",
                {},
            )
            .get(
                "ok",
                False,
            )
        )

        html_content += f"""
<h2>Strict Checks</h2>

<p>
<strong>OK:</strong>
{"✅" if strict_ok else "❌"}
</p>

<table>
<tr>
<th>Check</th>
<th>Exit Code</th>
<th>Duration</th>
<th>Timed Out</th>
</tr>
"""

        strict_results = (
            payload
            .get(
                "strict",
                {},
            )
            .get(
                "results",
                {},
            )
        )

        for (
            name,
            result,
        ) in strict_results.items():

            timed_out = bool(
                result.get(
                    "timed_out",
                    False,
                )
            )

            timed_out_display = (
                "❌ Yes"
                if timed_out
                else "✅ No"
            )

            duration = float(
                result.get(
                    "duration_seconds",
                    0,
                )
            )

            html_content += (
                "<tr>"
                f"<td>{e(name)}</td>"
                f"<td>{e(result.get('exit_code', ''))}</td>"
                f"<td>{e(f'{duration:.2f}s')}</td>"
                f"<td>{e(timed_out_display)}</td>"
                "</tr>\n"
            )

        html_content += (
            "</table>\n"
        )

    html_content += f"""
<hr>

<p>
<em>
Generated by Nazm Dad Project Status
v{e(TOOL_VERSION)}
</em>
</p>

</div>
</body>
</html>
"""

    try:
        output_path.parent.mkdir(
            parents=True,
            exist_ok=True,
        )

        output_path.write_text(
            html_content,
            encoding="utf-8",
        )

        print(
            "✅ HTML report written: "
            f"{output_path}"
        )

        return True

    except OSError as exc:
        print(
            "❌ Failed to write HTML: "
            f"{exc}",
            file=sys.stderr,
        )

        return False


# ============================================================
# Parser
# ============================================================

def build_parser() -> argparse.ArgumentParser:

    parser = argparse.ArgumentParser(
        prog=(
            "nazm_dad_project_status_v27.py"
        ),
        description=(
            "Nazm Dad Project Status "
            f"v{TOOL_VERSION} "
            "extension layer"
        ),
        add_help=True,
    )

    parser.add_argument(
        "--repo",
        default=".",
        help="repository path",
    )

    parser.add_argument(
        "--core",
        default=DEFAULT_CORE,
        help=(
            "core script "
            f"(default: {DEFAULT_CORE})"
        ),
    )

    parser.add_argument(
        "--config",
        help=(
            "path to config file "
            "(.nazm-dad-config.json)"
        ),
    )

    parser.add_argument(
        "--quiet",
        action="store_true",
        help=(
            "reduce output "
            "(watch: only show changes)"
        ),
    )

    parser.add_argument(
        "--output",
        help=(
            "write structured output "
            "or HTML report to file"
        ),
    )

    parser.add_argument(
        "--doctor",
        action="store_true",
        help="run diagnostics",
    )

    parser.add_argument(
        "--doctor-json",
        action="store_true",
        help=(
            "print doctor report "
            "as JSON"
        ),
    )

    parser.add_argument(
        "--summary",
        action="store_true",
        help=(
            "quick project summary"
        ),
    )

    parser.add_argument(
        "--history",
        action="store_true",
        help=(
            "show document history"
        ),
    )

    parser.add_argument(
        "--history-count",
        type=int,
        default=10,
        help=(
            "max history entries "
            "(default: 10)"
        ),
    )

    parser.add_argument(
        "--validate-articles",
        action="store_true",
        help=(
            "validate article "
            "structure and numbering"
        ),
    )

    parser.add_argument(
        "--check-links",
        action="store_true",
        help=(
            "check for broken links "
            "in documents"
        ),
    )

    parser.add_argument(
        "--repair-preview",
        action="store_true",
        help=(
            "preview document repair"
        ),
    )

    parser.add_argument(
        "--repair-from-ref",
        metavar="REF",
        default=None,
        help=(
            "Git ref for "
            "repair preview"
        ),
    )

    parser.add_argument(
        "--repair-source-dir",
        default=None,
        help=(
            "source directory for "
            "repair preview"
        ),
    )

    parser.add_argument(
        "--repair-files",
        nargs="+",
        default=None,
        help=(
            "files to preview"
        ),
    )

    parser.add_argument(
        "--strict",
        action="store_true",
        help=(
            "strict CI-style "
            "validation"
        ),
    )

    parser.add_argument(
        "--strict-timeout",
        type=float,
        default=(
            STRICT_TIMEOUT_SECONDS
        ),
        metavar="SECONDS",
        help=(
            "timeout per strict "
            "subprocess "
            f"(default: "
            f"{STRICT_TIMEOUT_SECONDS:g})"
        ),
    )

    parser.add_argument(
        "--hash-manifest",
        action="store_true",
        help=(
            "write SHA-256 manifest"
        ),
    )

    parser.add_argument(
        "--verify-hashes",
        action="store_true",
        help=(
            "verify SHA-256 manifest"
        ),
    )

    parser.add_argument(
        "--manifest",
        default=DEFAULT_MANIFEST,
        help=(
            "manifest path "
            f"(default: "
            f"{DEFAULT_MANIFEST})"
        ),
    )

    parser.add_argument(
        "--hash-files",
        nargs="+",
        default=None,
        help=(
            "files included in "
            "hash manifest"
        ),
    )

    parser.add_argument(
        "--ci-json",
        nargs="?",
        const="-",
        default=None,
        metavar="FILE",
        help=(
            "write CI JSON to FILE"
        ),
    )

    parser.add_argument(
        "--ci-strict",
        action="store_true",
        help=(
            "include strict checks "
            "in CI JSON"
        ),
    )

    parser.add_argument(
        "--ci-html",
        action="store_true",
        help=argparse.SUPPRESS,
    )

    parser.add_argument(
        "--html-report",
        action="store_true",
        help=(
            "generate HTML report "
            "with status charts"
        ),
    )

    parser.add_argument(
        "--version",
        action="store_true",
        help=(
            f"show v{TOOL_VERSION} "
            "version"
        ),
    )

    parser.add_argument(
        "--report",
        action="store_true",
        help=(
            "generate comprehensive "
            "report"
        ),
    )

    parser.add_argument(
        "--format",
        choices=[
            "json",
            "yaml",
            "csv",
        ],
        default=None,
        help=(
            "structured output "
            "format: json/yaml/csv"
        ),
    )

    parser.add_argument(
        "--watch",
        action="store_true",
        help=(
            "watch mode for changes"
        ),
    )

    parser.add_argument(
        "--watch-interval",
        type=float,
        default=5.0,
        help=(
            "watch interval in seconds "
            "(default: 5.0)"
        ),
    )

    parser.add_argument(
        "--watch-patterns",
        nargs="+",
        default=[
            "*.md",
            "*.py",
            "*.json",
            "*.txt",
        ],
        help=(
            "file patterns to watch"
        ),
    )

    parser.add_argument(
        "--watch-command",
        nargs="+",
        default=None,
        help=(
            "custom commands to run "
            "on change"
        ),
    )

    parser.add_argument(
        "--threshold",
        type=int,
        default=90,
        help=(
            "health score threshold "
            "(default: 90)"
        ),
    )

    parser.add_argument(
        "--compare",
        metavar="REF",
        help=(
            "compare with a given "
            "Git ref"
        ),
    )

    parser.add_argument(
        "--dry-run",
        action="store_true",
        help=(
            "preview --auto-fix "
            "without writing"
        ),
    )

    parser.add_argument(
        "--auto-fix",
        action="store_true",
        help=(
            "create missing "
            "recommended files "
            "(safe only)"
        ),
    )

    return parser


# ============================================================
# Main
# ============================================================

def main(
    argv: Optional[
        Sequence[str]
    ] = None,
) -> int:

    configure_utf8_stdio()

    parser = build_parser()

    known, unknown = (
        parser.parse_known_args(
            argv
        )
    )

    if known.ci_html:
        known.html_report = True

    if known.doctor_json:
        known.doctor = True

    if (
        known.ci_strict
        and known.ci_json is None
    ):
        known.ci_json = "-"

    if (
        known.repair_from_ref
        or known.repair_source_dir
        or known.repair_files
    ):
        known.repair_preview = True

    if known.config:
        config = load_config(
            Path(
                known.config
            ).expanduser()
        )
    else:
        config = load_config(
            Path(DEFAULT_CONFIG)
        )

    if (
        known.repo == "."
        and config.get(
            "repo_path"
        )
    ):
        known.repo = str(
            config[
                "repo_path"
            ]
        )

    repo = find_repo(
        Path(known.repo)
    )

    if known.version:
        print(
            TOOL_VERSION
        )
        return EXIT_OK

    if (
        known.strict_timeout
        <= 0
    ):
        print(
            (
                "❌ --strict-timeout "
                "must be greater "
                "than zero"
            ),
            file=sys.stderr,
        )

        return EXIT_USAGE_ERROR

    if not (
        0
        <= known.threshold
        <= 100
    ):
        print(
            (
                "❌ --threshold must "
                "be between 0 and 100"
            ),
            file=sys.stderr,
        )

        return EXIT_USAGE_ERROR

    if (
        known.watch
        and known.watch_interval
        <= 0
    ):
        print(
            (
                "❌ --watch-interval "
                "must be greater "
                "than zero"
            ),
            file=sys.stderr,
        )

        return EXIT_USAGE_ERROR

    output_path: Optional[
        Path
    ] = None

    if known.output:
        output_path = (
            Path(
                known.output
            )
            .expanduser()
            .resolve()
        )

    if known.summary:
        summary = build_summary(
            repo,
            known.core,
        )

        if known.format is None:
            print_summary(
                summary
            )

            return EXIT_OK

        try:
            content = format_output(
                summary_payload(
                    summary
                ),
                known.format,
            )

        except (
            RuntimeError,
            ValueError,
        ) as exc:

            print(
                f"❌ {exc}",
                file=sys.stderr,
            )

            return (
                EXIT_RUNTIME_ERROR
            )

        if output_path:
            if not write_output(
                content,
                output_path,
            ):
                return (
                    EXIT_RUNTIME_ERROR
                )

        else:
            print(content)

        return EXIT_OK

    if known.history:
        return show_history(
            repo,
            known.history_count,
        )

    if known.validate_articles:
        results = (
            validate_articles(
                repo
            )
        )

        payload = (
            article_validation_payload(
                results
            )
        )

        if known.format is None:
            print_article_validation(
                results
            )

        else:
            try:
                content = (
                    format_output(
                        payload,
                        known.format,
                    )
                )

            except (
                RuntimeError,
                ValueError,
            ) as exc:

                print(
                    f"❌ {exc}",
                    file=sys.stderr,
                )

                return (
                    EXIT_RUNTIME_ERROR
                )

            if output_path:
                if not write_output(
                    content,
                    output_path,
                ):
                    return (
                        EXIT_RUNTIME_ERROR
                    )

            else:
                print(content)

        return (
            EXIT_OK
            if payload[
                "all_valid"
            ]
            else (
                EXIT_VALIDATION_FAILED
            )
        )

    if known.check_links:
        docs_dir = (
            repo / "docs"
        )

        if (
            not docs_dir.exists()
            or not docs_dir.is_dir()
        ):
            print(
                (
                    "❌ docs directory "
                    f"missing: {docs_dir}"
                ),
                file=sys.stderr,
            )

            return (
                EXIT_VALIDATION_FAILED
            )

        results = (
            check_all_links(
                repo
            )
        )

        payload = link_payload(
            results
        )

        if known.format is None:
            print_link_results(
                results
            )

        else:
            try:
                content = (
                    format_output(
                        payload,
                        known.format,
                    )
                )

            except (
                RuntimeError,
                ValueError,
            ) as exc:

                print(
                    f"❌ {exc}",
                    file=sys.stderr,
                )

                return (
                    EXIT_RUNTIME_ERROR
                )

            if output_path:
                if not write_output(
                    content,
                    output_path,
                ):
                    return (
                        EXIT_RUNTIME_ERROR
                    )

            else:
                print(content)

        return (
            EXIT_OK
            if (
                payload[
                    "stats"
                ]["broken"]
                == 0
            )
            else (
                EXIT_VALIDATION_FAILED
            )
        )

    if known.html_report:
        payload = ci_report(
            repo,
            known.core,
            strict=(
                known.ci_strict
            ),
            timeout_seconds=(
                known.strict_timeout
            ),
            threshold=(
                known.threshold
            ),
        )

        html_path = (
            output_path
            if output_path
            else (
                repo
                / "ci-report.html"
            )
        )

        ok = generate_html_report(
            repo,
            payload,
            html_path,
        )

        if not ok:
            return (
                EXIT_RUNTIME_ERROR
            )

        return (
            EXIT_OK
            if payload.get(
                "ok",
                False,
            )
            else (
                EXIT_VALIDATION_FAILED
            )
        )

    if known.watch:
        watcher = WatchMode(
            repo,
            known.core,
            known.watch_interval,
            known.watch_patterns,
            known.strict_timeout,
            known.watch_command,
            known.quiet,
        )

        watcher.run()

        return EXIT_OK

    if known.doctor:
        report = doctor(
            repo,
            known.core,
        )

        if known.doctor_json:
            payload = doctor_payload(
                repo,
                known.core,
                report,
            )

            content = json.dumps(
                payload,
                ensure_ascii=False,
                indent=2,
            )

            if output_path:
                if not write_output(
                    content,
                    output_path,
                ):
                    return (
                        EXIT_RUNTIME_ERROR
                    )

            else:
                print(content)

        else:
            print_doctor(
                report
            )

        return (
            EXIT_OK
            if report.ok
            else (
                EXIT_VALIDATION_FAILED
            )
        )

    if known.repair_preview:
        if (
            known.repair_from_ref
            and (
                known
                .repair_source_dir
            )
        ):
            print(
                (
                    "❌ choose only one "
                    "repair source: "
                    "--repair-from-ref "
                    "OR "
                    "--repair-source-dir"
                ),
                file=sys.stderr,
            )

            return EXIT_USAGE_ERROR

        if (
            not known.repair_from_ref
            and not (
                known
                .repair_source_dir
            )
        ):
            print(
                (
                    "❌ --repair-preview "
                    "requires "
                    "--repair-from-ref REF "
                    "or "
                    "--repair-source-dir DIR"
                ),
                file=sys.stderr,
            )

            return EXIT_USAGE_ERROR

        files = (
            known.repair_files
            or list(
                DEFAULT_REPAIR_FILES
            )
        )

        source_dir: Optional[
            Path
        ] = None

        if known.repair_source_dir:
            source_dir = (
                Path(
                    known
                    .repair_source_dir
                )
                .expanduser()
                .resolve()
            )

            if (
                not source_dir.exists()
                or not (
                    source_dir.is_dir()
                )
            ):
                print(
                    (
                        "❌ repair source "
                        "directory not "
                        f"found: {source_dir}"
                    ),
                    file=sys.stderr,
                )

                return (
                    EXIT_RUNTIME_ERROR
                )

        try:
            (
                sources_ok,
                items,
            ) = repair_preview(
                repo,
                files,
                from_ref=(
                    known
                    .repair_from_ref
                ),
                source_dir=(
                    source_dir
                ),
            )

        except ValueError as exc:
            print(
                f"❌ {exc}",
                file=sys.stderr,
            )

            return EXIT_USAGE_ERROR

        print_repair_preview(
            items
        )

        return (
            EXIT_OK
            if sources_ok
            else (
                EXIT_VALIDATION_FAILED
            )
        )

    manifest_path = (
        Path(
            known.manifest
        )
        .expanduser()
    )

    if not (
        manifest_path
        .is_absolute()
    ):
        manifest_path = (
            repo
            / manifest_path
        )

    manifest_path = (
        manifest_path.resolve()
    )

    if known.hash_manifest:
        files = resolve_hash_files(
            repo,
            known.hash_files,
        )

        if not files:
            print(
                (
                    "❌ no files available "
                    "for hashing"
                ),
                file=sys.stderr,
            )

            return (
                EXIT_RUNTIME_ERROR
            )

        ok = write_manifest(
            repo,
            manifest_path,
            files,
        )

        return (
            EXIT_OK
            if ok
            else EXIT_RUNTIME_ERROR
        )

    if known.verify_hashes:
        ok = verify_manifest(
            repo,
            manifest_path,
        )

        return (
            EXIT_OK
            if ok
            else (
                EXIT_VALIDATION_FAILED
            )
        )

    if known.ci_json is not None:
        payload = ci_report(
            repo,
            known.core,
            strict=(
                known.ci_strict
            ),
            timeout_seconds=(
                known.strict_timeout
            ),
            threshold=(
                known.threshold
            ),
        )

        write_ok = (
            write_json_output(
                payload,
                known.ci_json,
            )
        )

        if not write_ok:
            return (
                EXIT_RUNTIME_ERROR
            )

        return (
            EXIT_OK
            if payload.get(
                "ok",
                False,
            )
            else (
                EXIT_VALIDATION_FAILED
            )
        )

    if known.strict:
        (
            success,
            _results,
        ) = run_strict(
            repo,
            known.core,
            timeout_seconds=(
                known.strict_timeout
            ),
            quiet=False,
            include_health=True,
        )

        return (
            EXIT_OK
            if success
            else (
                EXIT_VALIDATION_FAILED
            )
        )

    if known.auto_fix:
        return auto_fix(
            repo,
            dry_run=(
                known.dry_run
            ),
        )

    if known.compare:
        return compare_status(
            repo,
            known.compare,
            known.core,
        )

    if known.report:
        try:
            report_data = (
                generate_report(
                    repo,
                    known.core,
                    known.strict_timeout,
                )
            )

            if known.format:
                content = format_output(
                    report_data,
                    known.format,
                )

            else:
                content = json.dumps(
                    report_data,
                    ensure_ascii=False,
                    indent=2,
                )

            if output_path:
                if not write_output(
                    content,
                    output_path,
                ):
                    return (
                        EXIT_RUNTIME_ERROR
                    )

            else:
                print(content)

            return EXIT_OK

        except (
            RuntimeError,
            FileNotFoundError,
            ValueError,
            OSError,
        ) as exc:

            print(
                f"❌ {exc}",
                file=sys.stderr,
            )

            return (
                EXIT_RUNTIME_ERROR
            )

    if unknown:
        return run_core(
            repo,
            known.core,
            unknown,
            timeout_seconds=None,
            quiet=False,
        )

    parser.print_help()

    return EXIT_OK


# ============================================================
# Entry Point
# ============================================================

if __name__ == "__main__":
    raise SystemExit(main())