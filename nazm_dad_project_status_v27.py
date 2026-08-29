#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
Nazm Dad — Project Status & Documentation Tool (extension layer)
نظم داد — ابزار وضعیت، اعتبارسنجی و مستندسازی پروژه (لایهٔ extension)

Version: 2.9

قابلیت‌های جدید v2.9
--------------------
- --report: تولید گزارش جامع شامل doctor و health
- --watch: نظارت بر تغییرات فایل‌ها با اجرای خودکار
- --threshold: تنظیم آستانه Health Score برای CI/CD
- --compare: مقایسه وضعیت فعلی با یک ref
- --format: خروجی به JSON/YAML/CSV
- --dry-run: پیش‌نمایش auto-fix بدون نوشتن فایل
- --auto-fix: ایجاد فایل‌های توصیه‌شده مفقود (بدون محتوای حقوقی)

قابلیت‌های موجود از v2.7/v2.8 حفظ شده‌اند:
- --doctor / --doctor-json
- --repair-preview / --repair-from-ref / --repair-source-dir
- --hash-manifest / --verify-hashes
- --ci-json / --ci-strict
- --strict / --strict-timeout

نکته Windows / UTF-8
---------------------
برای جلوگیری از خطای:

    'charmap' codec can't encode character

فرآیندهای Python فرزند با این متغیرهای محیطی اجرا می‌شوند:

    PYTHONIOENCODING=utf-8
    PYTHONUTF8=1
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import os
import re
import subprocess
import sys
import time

from dataclasses import asdict, dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Tuple


# ============================================================
# Version & Constants
# ============================================================

TOOL_VERSION = "2.9"

DEFAULT_CORE = "nazm_dad_project_status.py"

STRICT_TIMEOUT_SECONDS = 120.0
TIMEOUT_EXIT_CODE = 124

DEFAULT_MANIFEST = ".nazm-dad-hashes.json"

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
        return path.resolve().relative_to(
            base.resolve()
        ).as_posix()
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
    """
    مسیر نسبی پروژه را resolve می‌کند و اجازه نمی‌دهد
    مسیر از repository خارج شود.
    """
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
    """
    Environment مناسب subprocessهای Python.

    روی Windows این بخش برای جلوگیری از خطای charmap
    هنگام چاپ فارسی یا emoji ضروری است.
    """
    env = os.environ.copy()

    env["PYTHONIOENCODING"] = "utf-8"
    env["PYTHONUTF8"] = "1"

    return env


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

    except (OSError, subprocess.TimeoutExpired):
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

    except (OSError, subprocess.TimeoutExpired):
        pass

    return ""


def git_branch(repo: Path) -> str:
    try:
        proc = git_run(
            repo,
            ["branch", "--show-current"],
            timeout=15,
        )

        if proc.returncode == 0:
            return proc.stdout.strip()

    except (OSError, subprocess.TimeoutExpired):
        pass

    return ""


def git_upstream(repo: Path) -> Optional[str]:
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

    except (OSError, subprocess.TimeoutExpired):
        pass

    return None


def git_is_clean(repo: Path) -> Optional[bool]:
    try:
        proc = git_run(
            repo,
            ["status", "--porcelain"],
            timeout=15,
        )

        if proc.returncode == 0:
            return not bool(proc.stdout.strip())

    except (OSError, subprocess.TimeoutExpired):
        pass

    return None


def git_ref_exists(repo: Path, ref: str) -> bool:
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

    except (OSError, subprocess.TimeoutExpired):
        return False


# ============================================================
# File / Hash Helpers
# ============================================================

def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()

    with path.open("rb") as handle:
        while True:
            chunk = handle.read(1024 * 1024)

            if not chunk:
                break

            digest.update(chunk)

    return digest.hexdigest()


def extract_health_score(text: str) -> Optional[int]:
    """
    استخراج Health Score از خروجی متنی core.

    نمونه‌های پشتیبانی‌شده:

        80/100
        score: 80
        score=80
        امتیاز: 80
    """
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
            value = int(match.group(1))
        except (TypeError, ValueError):
            continue

        if 0 <= value <= 100:
            return value

    return None


# ============================================================
# Core Path
# ============================================================

def core_path(repo: Path, core: str) -> Path:
    """
    مسیر core را resolve می‌کند.

    - مسیر absolute مجاز است.
    - مسیر relative باید داخل repository باشد.
    """
    candidate = Path(core).expanduser()

    if candidate.is_absolute():
        return candidate.resolve()

    return safe_repo_path(repo, core)


# ============================================================
# Core Execution
# ============================================================

def run_core(
    repo: Path,
    core: str,
    args: Sequence[str],
    *,
    timeout_seconds: Optional[float] = None,
    quiet: bool = False,
) -> int:
    """
    اجرای core.

    quiet=True برای CI باعث capture شدن stdout/stderr می‌شود.
    UTF-8 نیز داخل child process اجباری می‌شود.
    """

    try:
        path = core_path(repo, core)

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

        return int(proc.returncode)

    except subprocess.TimeoutExpired:
        if not quiet:
            timeout_text = (
                f"{timeout_seconds:.1f}s"
                if timeout_seconds is not None
                else "configured timeout"
            )

            print(
                f"⏱️ TIMEOUT after {timeout_text}: "
                f"{' '.join(command)}",
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
                f"❌ failed to execute core script: {exc}",
                file=sys.stderr,
            )

        return EXIT_RUNTIME_ERROR


def capture_core(
    repo: Path,
    core: str,
    args: Sequence[str],
    *,
    timeout_seconds: Optional[float] = None,
) -> subprocess.CompletedProcess:
    """
    اجرای core و گرفتن stdout/stderr به UTF-8.

    PYTHONIOENCODING و PYTHONUTF8 برای Windows مهم هستند.
    """

    path = core_path(repo, core)

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

    checks: List[DoctorCheck] = []

    # --------------------------------------------------------
    # Python
    # --------------------------------------------------------

    checks.append(
        DoctorCheck(
            name="python",
            ok=True,
            detail=(
                f"python: "
                f"{sys.version_info.major}."
                f"{sys.version_info.minor}."
                f"{sys.version_info.micro} "
                f"({sys.executable})"
            ),
        )
    )

    # --------------------------------------------------------
    # Git
    # --------------------------------------------------------

    if git_available():
        try:
            proc = subprocess.run(
                ["git", "--version"],
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
                    detail=proc.stdout.strip(),
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
                    detail=f"git error: {exc}",
                )
            )

    else:
        checks.append(
            DoctorCheck(
                name="git",
                ok=False,
                detail="git executable not available",
            )
        )

    # --------------------------------------------------------
    # Repository
    # --------------------------------------------------------

    is_repo = (repo / ".git").exists()

    checks.append(
        DoctorCheck(
            name="repository",
            ok=is_repo,
            detail=f"repository: {repo}",
        )
    )

    # --------------------------------------------------------
    # Core
    # --------------------------------------------------------

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
                    f"core-script: invalid path: {exc}"
                ),
            )
        )

    else:
        if core_file.exists() and core_file.is_file():
            try:
                compile_proc = subprocess.run(
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

                if compile_proc.returncode == 0:
                    checks.append(
                        DoctorCheck(
                            name="core-script",
                            ok=True,
                            detail="core-script: syntax OK",
                        )
                    )

                else:
                    detail = (
                        compile_proc.stderr.strip()
                        or compile_proc.stdout.strip()
                        or (
                            f"exit="
                            f"{compile_proc.returncode}"
                        )
                    )

                    checks.append(
                        DoctorCheck(
                            name="core-script",
                            ok=False,
                            detail=(
                                "core-script: "
                                f"syntax error: {detail}"
                            ),
                        )
                    )

            except subprocess.TimeoutExpired:
                checks.append(
                    DoctorCheck(
                        name="core-script",
                        ok=False,
                        detail=(
                            "core-script: syntax check "
                            "timed out after 30s"
                        ),
                    )
                )

            except OSError as exc:
                checks.append(
                    DoctorCheck(
                        name="core-script",
                        ok=False,
                        detail=(
                            "core-script: unable to run "
                            f"syntax check: {exc}"
                        ),
                    )
                )

        else:
            checks.append(
                DoctorCheck(
                    name="core-script",
                    ok=False,
                    detail=(
                        f"core-script missing: "
                        f"{core_file}"
                    ),
                )
            )

    # --------------------------------------------------------
    # Git repository state
    # --------------------------------------------------------

    if is_repo:
        clean = git_is_clean(repo)

        if clean is True:
            checks.append(
                DoctorCheck(
                    name="working-tree",
                    ok=True,
                    detail="working-tree: clean",
                )
            )

        elif clean is False:
            checks.append(
                DoctorCheck(
                    name="working-tree",
                    ok=True,
                    warning=True,
                    detail="working-tree: dirty",
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

        branch = git_branch(repo)

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

        upstream = git_upstream(repo)

        checks.append(
            DoctorCheck(
                name="upstream",
                ok=True,
                warning=not bool(upstream),
                detail=(
                    f"upstream: {upstream}"
                    if upstream
                    else "upstream: not configured"
                ),
            )
        )

    # --------------------------------------------------------
    # Documents
    # --------------------------------------------------------

    docs = repo / "docs"

    if not docs.exists():
        checks.append(
            DoctorCheck(
                name="docs",
                ok=False,
                detail=(
                    f"docs directory missing: {docs}"
                ),
            )
        )

    else:
        required = (
            ("docs/0.4.md", 61),
            ("docs/0.5.md", 73),
            ("docs/changelog.md", None),
            ("docs/rules.md", None),
            ("docs/decisions.md", None),
        )

        for relative, expected_articles in required:
            target = repo / relative

            if not target.exists():
                checks.append(
                    DoctorCheck(
                        name=f"doc:{relative}",
                        ok=False,
                        detail=f"doc:{relative}: missing",
                    )
                )

                continue

            if not target.is_file():
                checks.append(
                    DoctorCheck(
                        name=f"doc:{relative}",
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
                        name=f"doc:{relative}",
                        ok=False,
                        detail=(
                            f"doc:{relative}: "
                            f"invalid UTF-8: {exc}"
                        ),
                    )
                )

                continue

            except OSError as exc:
                checks.append(
                    DoctorCheck(
                        name=f"doc:{relative}",
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
                    size = target.stat().st_size

                except OSError as exc:
                    checks.append(
                        DoctorCheck(
                            name=f"doc:{relative}",
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
                        name=f"doc:{relative}",
                        ok=True,
                        detail=(
                            f"doc:{relative}: "
                            f"{size} bytes"
                        ),
                    )
                )

            else:
                token_count = text.count("ماده")

                count_ok = (
                    token_count >= expected_articles
                )

                # Heuristic only:
                # insufficient count is warning,
                # NOT hard validation failure.
                checks.append(
                    DoctorCheck(
                        name=f"doc:{relative}",
                        ok=True,
                        warning=not count_ok,
                        detail=(
                            f"doc:{relative}: "
                            "rough article-token count="
                            f"{token_count}; "
                            "expected at least "
                            f"{expected_articles}"
                        ),
                    )
                )

    # --------------------------------------------------------
    # Recommended files — warnings only
    # --------------------------------------------------------

    recommended = (
        (
            ".gitignore",
            ".gitignore missing (recommended)",
        ),
        (
            "README.md",
            "README.md missing (recommended)",
        ),
        (
            "LICENSE",
            "LICENSE missing (recommended)",
        ),
        (
            "LICENSE-DOCS.md",
            "LICENSE-DOCS.md missing "
            "(recommended)",
        ),
    )

    for filename, description in recommended:
        if not (repo / filename).exists():
            checks.append(
                DoctorCheck(
                    name=filename,
                    ok=False,
                    warning=True,
                    detail=description,
                )
            )

    hard_failure = any(
        not item.ok and not item.warning
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
    print(f"Nazm Dad — Doctor v{TOOL_VERSION}")
    print("=" * 72)

    for item in report.checks:
        if item.ok and not item.warning:
            icon = "✅"

        elif item.warning:
            icon = "⚠️"

        else:
            icon = "❌"

        print(f"{icon} {item.detail}")

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
            core_path(repo, core)
        )

    except ValueError as exc:
        resolved_core = (
            f"<invalid core path: {exc}>"
        )

    return {
        "schema": 1,
        "tool": "nazm-dad-project-status",
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
# Strict
# ============================================================

def run_strict(
    repo: Path,
    core: str,
    *,
    timeout_seconds: float = STRICT_TIMEOUT_SECONDS,
    quiet: bool = False,
    include_health: bool = True,
) -> Tuple[
    bool,
    Dict[str, StrictResult],
]:

    commands: Dict[str, List[str]] = {
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

    for name, arguments in commands.items():
        if not quiet:
            print("=" * 72)
            print(f"STRICT: {name}")
            print("=" * 72)

        started = time.monotonic()

        code = run_core(
            repo,
            core,
            arguments,
            timeout_seconds=timeout_seconds,
            quiet=quiet,
        )

        elapsed = (
            time.monotonic()
            - started
        )

        results[name] = StrictResult(
            exit_code=code,
            duration_seconds=elapsed,
        )

        if not quiet:
            if code == EXIT_OK:
                print(
                    f"✅ {name}: "
                    f"exit=0 "
                    f"({elapsed:.2f}s)"
                )

            elif code == TIMEOUT_EXIT_CODE:
                print(
                    f"⏱️ {name}: "
                    f"TIMEOUT "
                    f"({elapsed:.2f}s)"
                )

            else:
                print(
                    f"❌ {name}: "
                    f"exit={code} "
                    f"({elapsed:.2f}s)"
                )

    # --------------------------------------------------------
    # Doctor
    # --------------------------------------------------------

    if not quiet:
        print("=" * 72)
        print("STRICT: doctor")
        print("=" * 72)

    started = time.monotonic()

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
        doctor_code = EXIT_INTERRUPTED

    except Exception as exc:
        doctor_code = EXIT_RUNTIME_ERROR

        if not quiet:
            print(
                f"❌ doctor failed: {exc}",
                file=sys.stderr,
            )

    elapsed = (
        time.monotonic()
        - started
    )

    results["doctor"] = StrictResult(
        exit_code=doctor_code,
        duration_seconds=elapsed,
    )

    # --------------------------------------------------------
    # Summary
    # --------------------------------------------------------

    if not quiet:
        print()

        print("=" * 72)
        print("STRICT SUMMARY")
        print("=" * 72)

        ordered_names = [
            *commands.keys(),
            "doctor",
        ]

        for name in ordered_names:
            result = results[name]

            if result.exit_code == EXIT_OK:
                print(
                    f"✅ {name}: "
                    f"exit=0 "
                    f"({result.duration_seconds:.2f}s)"
                )

            elif (
                result.exit_code
                == TIMEOUT_EXIT_CODE
            ):
                print(
                    f"⏱️ {name}: "
                    f"TIMEOUT "
                    f"({result.duration_seconds:.2f}s)"
                )

            else:
                print(
                    f"❌ {name}: "
                    f"exit={result.exit_code} "
                    f"({result.duration_seconds:.2f}s)"
                )

    success = all(
        item.exit_code == EXIT_OK
        for item in results.values()
    )

    return success, results


def strict_results_payload(
    results: Dict[
        str,
        StrictResult,
    ],
) -> Dict[str, Any]:

    return {
        name: {
            "exit_code": result.exit_code,
            "duration_seconds": round(
                result.duration_seconds,
                6,
            ),
            "timed_out": (
                result.exit_code
                == TIMEOUT_EXIT_CODE
            ),
        }
        for name, result
        in results.items()
    }


# ============================================================
# Repair Preview
# ============================================================

def git_file_at_ref(
    repo: Path,
    ref: str,
    relative: str,
) -> Optional[bytes]:
    """
    خواندن فایل از Git ref.

    relative قبلاً در repair_preview با safe_repo_path
    بررسی می‌شود.
    """

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
    """
    فایل منبع فقط وقتی خوانده می‌شود
    که داخل source_dir باشد.
    """

    normalized = normalize_rel_path(
        relative
    )

    try:
        root = source_dir.resolve()

        target = (
            root
            / normalized
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
            keepends=True,
        )
    )

    replacement_text = (
        replacement
        .decode(
            "utf-8",
            errors="replace",
        )
        .splitlines(
            keepends=True,
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

    if bool(from_ref) == bool(source_dir):
        raise ValueError(
            "repair preview requires exactly "
            "one source: --repair-from-ref "
            "or --repair-source-dir"
        )

    items: List[
        RepairPreviewItem
    ] = []

    all_sources_found = True

    for raw_relative in files:
        relative = normalize_rel_path(
            raw_relative
        )

        # ----------------------------------------------------
        # Destination safety
        # ----------------------------------------------------

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
                        f"invalid path: {exc}"
                    ),
                )
            )

            continue

        # ----------------------------------------------------
        # Source
        # ----------------------------------------------------

        if from_ref:
            source_name = (
                f"git:{from_ref}:{relative}"
            )

            replacement = git_file_at_ref(
                repo,
                from_ref,
                relative,
            )

        else:
            assert source_dir is not None

            source_name = str(
                source_dir
                / Path(relative)
            )

            replacement = source_file_bytes(
                source_dir,
                relative,
            )

        # ----------------------------------------------------
        # Source missing
        # ----------------------------------------------------

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
                        "source file not found"
                    ),
                )
            )

            continue

        # ----------------------------------------------------
        # Current file
        # ----------------------------------------------------

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
                replacement_name=source_name,
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
    print("Nazm Dad — Repair Preview")
    print("=" * 72)

    print(
        "DRY-RUN: no file will be modified."
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
            f"{icon} "
            f"{item.path}: "
            f"{item.description}"
        )

        print(
            f"   source: "
            f"{item.source}"
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
            normalize_rel_path(item)
            for item in requested
        ]

    else:
        candidates = [
            *DEFAULT_REPAIR_FILES,
            DEFAULT_CORE,
        ]

    unique: List[str] = []
    seen = set()

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
        normalized = normalize_rel_path(
            relative
        )

        try:
            target = safe_repo_path(
                repo,
                normalized,
            )

        except ValueError as exc:
            raise ValueError(
                "manifest file escapes "
                f"repository: {relative}"
            ) from exc

        if (
            not target.exists()
            or not target.is_file()
        ):
            raise FileNotFoundError(
                "file not found for manifest: "
                f"{relative}"
            )

        records.append(
            {
                "path": normalized,
                "sha256": sha256_file(
                    target
                ),
                "size": (
                    target.stat().st_size
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
            f"❌ failed to write manifest: "
            f"{exc}",
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
            "❌ invalid manifest: "
            "'files' must be a list",
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
                "❌ invalid manifest "
                "record fields",
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
                f"unable to hash: {exc}"
            )

            ok = False
            continue

        if actual != expected:
            print(
                f"❌ {relative}: "
                "hash mismatch"
            )

            print(
                f"   expected: {expected}"
            )

            print(
                f"   actual:   {actual}"
            )

            ok = False

        else:
            print(
                f"✅ {relative}: verified"
            )

    return ok


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
        "threshold_enforced": strict,
        "doctor": {
            "ok": report.ok,
            "checks": [
                asdict(item)
                for item
                in report.checks
            ],
        },
    }

    # --------------------------------------------------------
    # Strict checks
    # health separately handled below
    # --------------------------------------------------------

    if strict:
        success, results = run_strict(
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

    # --------------------------------------------------------
    # Health
    # --------------------------------------------------------

    health_ok = False
    health_score: Optional[int] = None

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

        # Core health معمولاً می‌تواند:
        # 0 = PASS
        # 1 = validation/health below perfect
        #
        # 2+ = runtime failure
        if health_proc.returncode in (
            EXIT_OK,
            EXIT_VALIDATION_FAILED,
        ):
            health_score = (
                extract_health_score(
                    health_proc.stdout
                )
            )

            if health_score is not None:
                payload[
                    "health_score"
                ] = health_score

                health_ok = (
                    health_score
                    >= threshold
                )

            else:
                health_ok = False

        else:
            health_ok = False

    except subprocess.TimeoutExpired:
        payload["health"] = {
            "exit_code": (
                TIMEOUT_EXIT_CODE
            ),
            "timed_out": True,
        }

        health_ok = False

    except (
        FileNotFoundError,
        ValueError,
        OSError,
    ) as exc:
        payload["health"] = {
            "error": str(exc)
        }

        health_ok = False

    # --------------------------------------------------------
    # Overall
    # --------------------------------------------------------

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

    # v2.8 compatibility:
    # basic CI does not enforce threshold.
    if not strict:
        payload["ok"] = doctor_ok

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
            "❌ failed to write JSON: "
            f"{exc}",
            file=sys.stderr,
        )

        return False

    print(
        f"✅ CI JSON written: {target}"
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

        score = extract_health_score(
            health_proc.stdout
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
    }


# ============================================================
# Format Output
# ============================================================

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
                "Please install: "
                "pip install pyyaml"
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

        writer = csv.writer(
            output
        )

        writer.writerow(
            [
                "key",
                "value",
            ]
        )

        def flatten(
            value: Any,
            parent: str = "",
        ) -> None:

            if isinstance(
                value,
                dict,
            ):
                for key, child in value.items():
                    next_parent = (
                        f"{parent}{key}."
                    )

                    flatten(
                        child,
                        next_parent,
                    )

            elif isinstance(
                value,
                list,
            ):
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
# Compare
# ============================================================

def compare_status(
    repo: Path,
    ref: str,
    core: str,
) -> int:

    print("=" * 72)

    print(
        "Nazm Dad — Compare with ref: "
        f"{ref}"
    )

    print("=" * 72)

    if not git_ref_exists(
        repo,
        ref,
    ):
        print(
            f"❌ Git ref not found: {ref}",
            file=sys.stderr,
        )

        return EXIT_VALIDATION_FAILED

    print()
    print("📋 Current status:")

    current_report = doctor(
        repo,
        core,
    )

    print_doctor(
        current_report
    )

    print()
    print("-" * 72)

    print(
        "📋 Files changed relative "
        f"to {ref}:"
    )

    print("-" * 72)

    try:
        sources_ok, items = (
            repair_preview(
                repo,
                DEFAULT_REPAIR_FILES,
                from_ref=ref,
            )
        )

        for item in items:
            if not item.source_exists:
                print(
                    f"❌ {item.path}: "
                    "source file not found "
                    f"at {ref}"
                )

            elif item.changed:
                print(
                    f"🟡 {item.path}: "
                    f"differs from {ref}"
                )

                diff_lines = (
                    item.diff.splitlines()
                )

                for line in diff_lines[:10]:
                    print(
                        f"   {line}"
                    )

                if len(diff_lines) > 10:
                    remaining = (
                        len(diff_lines)
                        - 10
                    )

                    print(
                        f"   ... "
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
                f"❌ ref '{ref}' could not "
                "provide all required files."
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

    print()
    print("=" * 72)

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
            part in self.IGNORED_DIRS
            for part in path.parts
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
                        and not self._is_ignored(
                            path
                        )
                    ):
                        files.append(path)

            except OSError:
                continue

        return sorted(
            set(files)
        )

    def has_changes(
        self,
    ) -> List[Path]:

        changed: List[
            Path
        ] = []

        current_files = (
            self.scan_files()
        )

        current_set = set(
            current_files
        )

        # Changed / new
        for file_path in current_files:
            try:
                mtime = (
                    file_path
                    .stat()
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

        # Deleted files
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

    def run(
        self,
    ) -> None:

        print("=" * 72)

        print(
            "Nazm Dad — Watch Mode "
            f"v{TOOL_VERSION}"
        )

        print("=" * 72)

        print(
            f"Repository: {self.repo}"
        )

        print(
            "Patterns: "
            f"{', '.join(self.patterns)}"
        )

        print(
            f"Interval: {self.interval:g}s"
        )

        print(
            "Timeout: "
            f"{self.timeout_seconds:g}s"
        )

        print(
            "Ignored dirs: "
            f"{', '.join(sorted(self.IGNORED_DIRS))}"
        )

        print(
            "Press Ctrl+C to stop"
        )

        print("-" * 72)

        # Initial snapshot
        for file_path in (
            self.scan_files()
        ):
            try:
                self._last_mtime[
                    file_path
                ] = (
                    file_path
                    .stat()
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

                print()

                print(
                    "🔄 Changed: "
                    f"{', '.join(changed_names)}"
                )

                print("-" * 72)

                print(
                    "Running doctor..."
                )

                report = doctor(
                    self.repo,
                    self.core,
                )

                print_doctor(
                    report
                )

                print()

                print(
                    "Running health check..."
                )

                run_core(
                    self.repo,
                    self.core,
                    [
                        "--health",
                        "--no-progress",
                    ],
                    timeout_seconds=(
                        self.timeout_seconds
                    ),
                    quiet=False,
                )

                print("-" * 72)

        except KeyboardInterrupt:
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
    """
    Auto-fix فقط برای فایل‌های غیرحقوقی.

    LICENSE و LICENSE-DOCS عمداً ساخته نمی‌شوند.
    """

    created: List[str] = []

    # --------------------------------------------------------
    # .gitignore
    # --------------------------------------------------------

    gitignore = (
        repo
        / ".gitignore"
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
                "[dry-run] would create: "
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
                    "❌ failed to create "
                    f".gitignore: {exc}",
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

    # --------------------------------------------------------
    # README
    # --------------------------------------------------------

    readme = (
        repo
        / "README.md"
    )

    if not readme.exists():
        content = (
            "# Nazm Dad\n\n"
            "A constitutional framework "
            "for Iran.\n"
        )

        if dry_run:
            print(
                "[dry-run] would create: "
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
                    "❌ failed to create "
                    f"README.md: {exc}",
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

    # --------------------------------------------------------
    # Result
    # --------------------------------------------------------

    if dry_run:
        if (
            gitignore.exists()
            and readme.exists()
        ):
            print(
                "[dry-run] no safe "
                "auto-fix changes required."
            )

        return EXIT_OK

    if created:
        print(
            "✅ Auto-fix created: "
            f"{', '.join(created)}"
        )

    else:
        print(
            "✅ No missing recommended "
            "files to create."
        )

    return EXIT_OK


# ============================================================
# Parser
# ============================================================

def build_parser(
) -> argparse.ArgumentParser:

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

    # --------------------------------------------------------
    # General
    # --------------------------------------------------------

    parser.add_argument(
        "--repo",
        default=".",
        help="repository path",
    )

    parser.add_argument(
        "--core",
        default=DEFAULT_CORE,
        help=(
            "core project-status script "
            f"(default: {DEFAULT_CORE})"
        ),
    )

    # --------------------------------------------------------
    # Doctor
    # --------------------------------------------------------

    parser.add_argument(
        "--doctor",
        action="store_true",
        help=(
            "run environment/repository "
            "diagnostics"
        ),
    )

    parser.add_argument(
        "--doctor-json",
        action="store_true",
        help=(
            "print doctor report as JSON"
        ),
    )

    # --------------------------------------------------------
    # Repair preview
    # --------------------------------------------------------

    parser.add_argument(
        "--repair-preview",
        action="store_true",
        help=(
            "preview document repair "
            "without writing"
        ),
    )

    parser.add_argument(
        "--repair-from-ref",
        metavar="REF",
        default=None,
        help=(
            "Git ref used by "
            "--repair-preview "
            "(preview only)"
        ),
    )

    parser.add_argument(
        "--repair-source-dir",
        default=None,
        help=(
            "source directory used by "
            "--repair-preview "
            "(preview only)"
        ),
    )

    parser.add_argument(
        "--repair-files",
        nargs="+",
        default=None,
        help=(
            "project-relative files to "
            "preview for repair"
        ),
    )

    # --------------------------------------------------------
    # Strict
    # --------------------------------------------------------

    parser.add_argument(
        "--strict",
        action="store_true",
        help=(
            "strict CI-style validation"
        ),
    )

    parser.add_argument(
        "--strict-timeout",
        type=float,
        default=STRICT_TIMEOUT_SECONDS,
        metavar="SECONDS",
        help=(
            "timeout per strict subprocess "
            f"(default: "
            f"{STRICT_TIMEOUT_SECONDS:g})"
        ),
    )

    # --------------------------------------------------------
    # Hashes
    # --------------------------------------------------------

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
            f"(default: {DEFAULT_MANIFEST})"
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

    # --------------------------------------------------------
    # CI
    # --------------------------------------------------------

    parser.add_argument(
        "--ci-json",
        nargs="?",
        const="-",
        default=None,
        metavar="FILE",
        help=(
            "write CI JSON to FILE, "
            "or '-' / no value for stdout"
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

    # --------------------------------------------------------
    # Version
    # --------------------------------------------------------

    parser.add_argument(
        "--version",
        action="store_true",
        help=(
            f"show v{TOOL_VERSION} version"
        ),
    )

    # --------------------------------------------------------
    # Report
    # --------------------------------------------------------

    parser.add_argument(
        "--report",
        action="store_true",
        help=(
            "generate comprehensive report"
        ),
    )

    parser.add_argument(
        "--format",
        choices=[
            "json",
            "yaml",
            "csv",
        ],
        default="json",
        help=(
            "output format "
            "(default: json)"
        ),
    )

    # --------------------------------------------------------
    # Watch
    # --------------------------------------------------------

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
            "file patterns to watch "
            "(default: *.md *.py "
            "*.json *.txt)"
        ),
    )

    # --------------------------------------------------------
    # Threshold
    # --------------------------------------------------------

    parser.add_argument(
        "--threshold",
        type=int,
        default=90,
        help=(
            "health score threshold "
            "(default: 90)"
        ),
    )

    # --------------------------------------------------------
    # Compare
    # --------------------------------------------------------

    parser.add_argument(
        "--compare",
        metavar="REF",
        help=(
            "compare with a given Git ref"
        ),
    )

    # --------------------------------------------------------
    # Auto-fix
    # --------------------------------------------------------

    parser.add_argument(
        "--dry-run",
        action="store_true",
        help=(
            "preview --auto-fix "
            "without writing files"
        ),
    )

    parser.add_argument(
        "--auto-fix",
        action="store_true",
        help=(
            "create missing recommended "
            "files "
            "(safe only, no legal content)"
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

    parser = build_parser()

    known, unknown = (
        parser.parse_known_args(
            argv
        )
    )

    # --------------------------------------------------------
    # v2.7 / v2.8 compatibility shortcuts
    # --------------------------------------------------------

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

    # --------------------------------------------------------
    # Repository
    # --------------------------------------------------------

    repo = find_repo(
        Path(known.repo)
    )

    # --------------------------------------------------------
    # Version
    # --------------------------------------------------------

    if known.version:
        print(TOOL_VERSION)

        return EXIT_OK

    # --------------------------------------------------------
    # Argument validation
    # --------------------------------------------------------

    if known.strict_timeout <= 0:
        print(
            "❌ --strict-timeout must "
            "be greater than zero",
            file=sys.stderr,
        )

        return EXIT_USAGE_ERROR

    if not (
        0
        <= known.threshold
        <= 100
    ):
        print(
            "❌ --threshold must be "
            "between 0 and 100",
            file=sys.stderr,
        )

        return EXIT_USAGE_ERROR

    if (
        known.watch
        and known.watch_interval <= 0
    ):
        print(
            "❌ --watch-interval must "
            "be greater than zero",
            file=sys.stderr,
        )

        return EXIT_USAGE_ERROR

    # --------------------------------------------------------
    # Watch
    # --------------------------------------------------------

    if known.watch:
        watcher = WatchMode(
            repo,
            known.core,
            known.watch_interval,
            known.watch_patterns,
            known.strict_timeout,
        )

        watcher.run()

        return EXIT_OK

    # --------------------------------------------------------
    # Doctor
    # --------------------------------------------------------

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

            print(
                json.dumps(
                    payload,
                    ensure_ascii=False,
                    indent=2,
                )
            )

        else:
            print_doctor(
                report
            )

        return (
            EXIT_OK
            if report.ok
            else EXIT_VALIDATION_FAILED
        )

    # --------------------------------------------------------
    # Repair Preview
    # --------------------------------------------------------

    if known.repair_preview:
        if (
            known.repair_from_ref
            and known.repair_source_dir
        ):
            print(
                "❌ choose only one "
                "repair source: "
                "--repair-from-ref OR "
                "--repair-source-dir",
                file=sys.stderr,
            )

            return EXIT_USAGE_ERROR

        if (
            not known.repair_from_ref
            and not known.repair_source_dir
        ):
            print(
                "❌ --repair-preview requires "
                "--repair-from-ref REF or "
                "--repair-source-dir DIR",
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
                    known.repair_source_dir
                )
                .expanduser()
                .resolve()
            )

            if (
                not source_dir.exists()
                or not source_dir.is_dir()
            ):
                print(
                    "❌ repair source "
                    "directory not found: "
                    f"{source_dir}",
                    file=sys.stderr,
                )

                return (
                    EXIT_RUNTIME_ERROR
                )

        try:
            sources_ok, items = (
                repair_preview(
                    repo,
                    files,
                    from_ref=(
                        known
                        .repair_from_ref
                    ),
                    source_dir=source_dir,
                )
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
            else EXIT_VALIDATION_FAILED
        )

    # --------------------------------------------------------
    # Manifest path
    # --------------------------------------------------------

    manifest_path = (
        Path(known.manifest)
        .expanduser()
    )

    if not manifest_path.is_absolute():
        manifest_path = (
            repo
            / manifest_path
        )

    manifest_path = (
        manifest_path.resolve()
    )

    # --------------------------------------------------------
    # Hash manifest
    # --------------------------------------------------------

    if known.hash_manifest:
        files = resolve_hash_files(
            repo,
            known.hash_files,
        )

        if not files:
            print(
                "❌ no files available "
                "for hashing",
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

    # --------------------------------------------------------
    # Verify hashes
    # --------------------------------------------------------

    if known.verify_hashes:
        ok = verify_manifest(
            repo,
            manifest_path,
        )

        return (
            EXIT_OK
            if ok
            else EXIT_VALIDATION_FAILED
        )

    # --------------------------------------------------------
    # CI JSON
    # --------------------------------------------------------

    if known.ci_json is not None:
        payload = ci_report(
            repo,
            known.core,
            strict=known.ci_strict,
            timeout_seconds=(
                known.strict_timeout
            ),
            threshold=(
                known.threshold
            ),
        )

        write_ok = write_json_output(
            payload,
            known.ci_json,
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
            else EXIT_VALIDATION_FAILED
        )

    # --------------------------------------------------------
    # Strict
    # --------------------------------------------------------

    if known.strict:
        success, _results = (
            run_strict(
                repo,
                known.core,
                timeout_seconds=(
                    known.strict_timeout
                ),
                quiet=False,
                include_health=True,
            )
        )

        return (
            EXIT_OK
            if success
            else EXIT_VALIDATION_FAILED
        )

    # --------------------------------------------------------
    # Auto-fix
    # --------------------------------------------------------

    if known.auto_fix:
        return auto_fix(
            repo,
            dry_run=known.dry_run,
        )

    # --------------------------------------------------------
    # Compare
    # --------------------------------------------------------

    if known.compare:
        return compare_status(
            repo,
            known.compare,
            known.core,
        )

    # --------------------------------------------------------
    # Report
    # --------------------------------------------------------

    if known.report:
        try:
            report_data = (
                generate_report(
                    repo,
                    known.core,
                    known.strict_timeout,
                )
            )

            formatted = (
                format_output(
                    report_data,
                    known.format,
                )
            )

            print(formatted)

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

    # --------------------------------------------------------
    # Forward unknown args to core
    # --------------------------------------------------------

    if unknown:
        return run_core(
            repo,
            known.core,
            unknown,
            timeout_seconds=None,
            quiet=False,
        )

    # --------------------------------------------------------
    # Nothing selected
    # --------------------------------------------------------

    parser.print_help()

    return EXIT_OK


# ============================================================
# Entry Point
# ============================================================

if __name__ == "__main__":
    raise SystemExit(
        main()
    )