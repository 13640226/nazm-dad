#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
Nazm Dad — Project Status & Documentation Tool
نظم داد — ابزار وضعیت، اعتبارسنجی و مستندسازی پروژه

Version: 4.2.1
"""

from __future__ import annotations

import argparse
import csv
import difflib
import hashlib
import html
import io
import json
import os
import re
import subprocess
import sys
import time
import urllib.error
import urllib.request
from dataclasses import asdict, dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Set, Tuple
from urllib.parse import unquote, urlparse

TOOL_VERSION = "4.2.1"
DEFAULT_CORE = "nazm_dad_project_status.py"
STRICT_TIMEOUT_SECONDS = 120.0
TIMEOUT_EXIT_CODE = 124
DEFAULT_MANIFEST = ".nazm-dad-hashes.json"
DEFAULT_CONFIG = ".nazm-dad-config.json"
DEFAULT_STATE_FILE = ".nazm-dad-state.json"
DEFAULT_REPAIR_FILES: Tuple[str, ...] = (
    "docs/0.4.md",
    "docs/0.5.md",
    "docs/changelog.md",
    "docs/rules.md",
    "docs/decisions.md",
)

EXIT_OK = 0
EXIT_VALIDATION_FAILED = 1
EXIT_RUNTIME_ERROR = 2
EXIT_USAGE_ERROR = 3
EXIT_INTERRUPTED = 130


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
        return [x.detail for x in self.checks if not x.ok and not x.warning]

    @property
    def warnings(self) -> List[str]:
        return [x.detail for x in self.checks if x.warning]


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


@dataclass
class ConsistencyIssue:
    file: str
    line: int
    target: str
    description: str


class Colors:
    GREEN = "\033[92m"
    RED = "\033[91m"
    YELLOW = "\033[93m"
    BLUE = "\033[94m"
    CYAN = "\033[96m"
    MAGENTA = "\033[95m"
    DIM = "\033[2m"
    BOLD = "\033[1m"
    RESET = "\033[0m"
    _enabled = True

    @classmethod
    def enabled(cls, enable: bool = True) -> None:
        cls._enabled = enable

    @classmethod
    def colorize(cls, text: str, color: str) -> str:
        return f"{color}{text}{cls.RESET}" if cls._enabled else text

    @classmethod
    def green(cls, text: str) -> str: return cls.colorize(text, cls.GREEN)
    @classmethod
    def red(cls, text: str) -> str: return cls.colorize(text, cls.RED)
    @classmethod
    def yellow(cls, text: str) -> str: return cls.colorize(text, cls.YELLOW)
    @classmethod
    def blue(cls, text: str) -> str: return cls.colorize(text, cls.BLUE)
    @classmethod
    def cyan(cls, text: str) -> str: return cls.colorize(text, cls.CYAN)
    @classmethod
    def magenta(cls, text: str) -> str: return cls.colorize(text, cls.MAGENTA)
    @classmethod
    def dim(cls, text: str) -> str: return cls.colorize(text, cls.DIM)
    @classmethod
    def bold(cls, text: str) -> str: return cls.colorize(text, cls.BOLD)


@dataclass
class NazmDadConfig:
    repo_path: Optional[str] = None
    docs_path: Optional[str] = None
    output_dir: Optional[str] = None
    exclude_patterns: List[str] = field(default_factory=lambda: [".git", "__pycache__", "venv", ".venv"])
    watch_patterns: List[str] = field(default_factory=lambda: ["*.md", "*.py", "*.json", "*.txt"])
    watch_interval: float = 5.0
    strict_timeout: float = 120.0
    threshold: int = 90
    check_external: bool = False
    no_color: bool = False
    quiet: bool = False
    benchmark: bool = False

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "NazmDadConfig":
        valid = set(cls.__dataclass_fields__)
        return cls(**{k: v for k, v in data.items() if k in valid})

    @classmethod
    def from_file(cls, path: Path) -> "NazmDadConfig":
        if not path.exists():
            return cls()
        try:
            if path.suffix.lower() in (".yaml", ".yml"):
                try:
                    import yaml
                except ImportError:
                    return cls()
                data = yaml.safe_load(path.read_text(encoding="utf-8"))
            else:
                data = json.loads(path.read_text(encoding="utf-8"))
            return cls.from_dict(data) if isinstance(data, dict) else cls()
        except Exception:
            return cls()

    def apply_env(self) -> "NazmDadConfig":
        prefix = "NAZM_DAD_"
        for key, value in os.environ.items():
            if not key.startswith(prefix):
                continue
            field_name = key[len(prefix):].lower()
            if not hasattr(self, field_name):
                continue
            current = getattr(self, field_name)
            if isinstance(current, bool):
                setattr(self, field_name, value.lower() in ("true", "1", "yes", "on"))
            elif isinstance(current, int) and not isinstance(current, bool):
                try: setattr(self, field_name, int(value))
                except ValueError: pass
            elif isinstance(current, float):
                try: setattr(self, field_name, float(value))
                except ValueError: pass
            elif isinstance(current, list):
                setattr(self, field_name, [x.strip() for x in value.split(",") if x.strip()])
            else:
                setattr(self, field_name, value)
        return self


class Benchmark:
    _enabled = False
    _timings: Dict[str, float] = {}

    @classmethod
    def enable(cls) -> None:
        cls._enabled = True
        cls._timings = {}

    @classmethod
    def disable(cls) -> None:
        cls._enabled = False
        cls._timings = {}

    def __init__(self, name: str):
        self.name = name
        self._start = 0.0

    def __enter__(self):
        if self._enabled:
            self._start = time.perf_counter()
        return self

    def __exit__(self, *args):
        if self._enabled:
            elapsed = time.perf_counter() - self._start
            self._timings[self.name] = self._timings.get(self.name, 0.0) + elapsed

    @classmethod
    def print_timings(cls) -> None:
        if not cls._enabled or not cls._timings:
            return
        print("\n" + Colors.bold("⏱️ Benchmark Timings:"))
        max_duration = max(cls._timings.values())
        max_name = max(len(k) for k in cls._timings)
        total = 0.0
        for name, duration in sorted(cls._timings.items(), key=lambda x: x[1], reverse=True):
            bar_len = int(duration * 20 / max_duration) if max_duration > 0 else 0
            bar = "█" * bar_len + "░" * (20 - bar_len)
            color = Colors.green if duration < 1 else Colors.yellow if duration < 5 else Colors.red
            print(f"  {name:>{max_name}}: {color(f'{duration:7.3f}s')} [{bar}]")
            total += duration
        print(f"  {'Total':>{max_name}}: {Colors.cyan(f'{total:.3f}s')}")


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
    root = repo.resolve()
    candidate = (root / normalized).resolve()
    try:
        candidate.relative_to(root)
    except ValueError as exc:
        raise ValueError(f"path escapes repository: {relative}") from exc
    return candidate


def child_python_env() -> Dict[str, str]:
    env = os.environ.copy()
    env["PYTHONIOENCODING"] = "utf-8"
    env["PYTHONUTF8"] = "1"
    return env


def configure_utf8_stdio() -> None:
    for stream in (sys.stdout, sys.stderr):
        reconfigure = getattr(stream, "reconfigure", None)
        if callable(reconfigure):
            try:
                reconfigure(encoding="utf-8", errors="replace")
            except (OSError, ValueError):
                pass


def write_output(content: str, output_path: Optional[Path], quiet: bool = False) -> bool:
    if output_path is None:
        print(content)
        return True
    try:
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_text(content, encoding="utf-8")
    except OSError as exc:
        print(f"❌ Failed to write output: {exc}", file=sys.stderr)
        return False
    if not quiet:
        print(f"✅ Output written to: {output_path}")
    return True


def is_excluded(path: Path, patterns: Sequence[str]) -> bool:
    normalized = path.as_posix().lower()
    parts = {p.lower() for p in path.parts}
    for raw in patterns:
        p = str(raw).strip().replace("\\", "/").lower()
        if not p:
            continue
        if "/" not in p and p in parts:
            return True
        if p in normalized:
            return True
    return False


def format_output(data: Dict[str, Any], fmt: str) -> str:
    if fmt == "json":
        return json.dumps(data, ensure_ascii=False, indent=2)
    if fmt == "yaml":
        try:
            import yaml
        except ImportError as exc:
            raise RuntimeError("PyYAML is not installed. Run: pip install pyyaml") from exc
        return yaml.dump(data, allow_unicode=True, default_flow_style=False, sort_keys=False)
    if fmt == "csv":
        output = io.StringIO()
        writer = csv.writer(output)
        writer.writerow(["key", "value"])
        def flatten(value: Any, parent: str = "") -> None:
            if isinstance(value, dict):
                for key, child in value.items():
                    flatten(child, f"{parent}{key}.")
            elif isinstance(value, list):
                writer.writerow([parent.rstrip("."), json.dumps(value, ensure_ascii=False)])
            else:
                writer.writerow([parent.rstrip("."), str(value)])
        flatten(data)
        return output.getvalue()
    if fmt == "table":
        return _format_table(data)
    raise ValueError(f"Unsupported format: {fmt}")


def _format_table(data: Dict[str, Any], indent: int = 0) -> str:
    lines: List[str] = []
    for key, value in data.items():
        pad = "  " * indent
        if isinstance(value, dict):
            lines.append(f"{pad}{Colors.bold(key)}:")
            lines.append(_format_table(value, indent + 1))
        elif isinstance(value, list):
            lines.append(f"{pad}{Colors.bold(key)}: [{len(value)} items]")
            for i, item in enumerate(value[:5], 1):
                if isinstance(item, dict):
                    lines.append(f"{'  ' * (indent + 1)}#{i}:")
                    lines.append(_format_table(item, indent + 2))
                else:
                    lines.append(f"{'  ' * (indent + 1)}- {item}")
            if len(value) > 5:
                lines.append(f"{'  ' * (indent + 1)}... and {len(value) - 5} more")
        else:
            lines.append(f"{pad}{Colors.bold(key)}: {value}")
    return "\n".join(lines)


def git_available() -> bool:
    try:
        return subprocess.run(["git", "--version"], capture_output=True, timeout=10).returncode == 0
    except (OSError, subprocess.TimeoutExpired):
        return False


def git_run(repo: Path, args: Sequence[str], *, check: bool = False, timeout: Optional[float] = 30.0) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["git", "-C", str(repo), *args], capture_output=True, text=True,
        encoding="utf-8", errors="replace", check=check, timeout=timeout,
    )


def _git_stdout(repo: Path, args: Sequence[str]) -> str:
    try:
        proc = git_run(repo, args, timeout=15)
        return proc.stdout.strip() if proc.returncode == 0 else ""
    except (OSError, subprocess.TimeoutExpired):
        return ""


def git_commit(repo: Path) -> str: return _git_stdout(repo, ["rev-parse", "HEAD"])
def git_commit_short(repo: Path) -> str: return git_commit(repo)[:8]
def git_commit_date(repo: Path) -> str: return _git_stdout(repo, ["log", "-1", "--format=%ai"])
def git_branch(repo: Path) -> str: return _git_stdout(repo, ["branch", "--show-current"])


def git_upstream(repo: Path) -> Optional[str]:
    value = _git_stdout(repo, ["rev-parse", "--abbrev-ref", "--symbolic-full-name", "@{upstream}"])
    return value or None


def git_is_clean(repo: Path) -> Optional[bool]:
    try:
        proc = git_run(repo, ["status", "--porcelain"], timeout=15)
        return not bool(proc.stdout.strip()) if proc.returncode == 0 else None
    except (OSError, subprocess.TimeoutExpired):
        return None


def git_ahead_behind(repo: Path) -> Tuple[int, int]:
    upstream = git_upstream(repo)
    if not upstream:
        return 0, 0
    try:
        proc = git_run(repo, ["rev-list", "--left-right", "--count", f"HEAD...{upstream}"], timeout=15)
        if proc.returncode == 0:
            left, right = proc.stdout.strip().split()
            return int(left), int(right)
    except (OSError, subprocess.TimeoutExpired, ValueError):
        pass
    return 0, 0


def git_ref_exists(repo: Path, ref: str) -> bool:
    try:
        return git_run(repo, ["rev-parse", "--verify", "--quiet", f"{ref}^{{commit}}"], timeout=15).returncode == 0
    except (OSError, subprocess.TimeoutExpired):
        return False


def git_file_history(repo: Path, file_path: str, max_count: int = 10) -> List[Dict[str, str]]:
    try:
        proc = git_run(repo, ["log", f"-{max_count}", "--format=%h|%ai|%s", "--", file_path], timeout=15)
    except (OSError, subprocess.TimeoutExpired):
        return []
    if proc.returncode != 0:
        return []
    out: List[Dict[str, str]] = []
    for line in proc.stdout.splitlines():
        parts = line.split("|", 2)
        if len(parts) == 3:
            out.append({"commit": parts[0], "date": parts[1], "message": parts[2]})
    return out


_hash_cache: Dict[Tuple[str, int, int], str] = {}


def sha256_file(path: Path) -> str:
    stat = path.stat()
    key = (str(path.resolve()), stat.st_mtime_ns, stat.st_size)
    if key in _hash_cache:
        return _hash_cache[key]
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    value = digest.hexdigest()
    _hash_cache[key] = value
    return value


def clear_hash_cache() -> None:
    _hash_cache.clear()


def extract_health_score(text: str) -> Optional[int]:
    for pattern in (r"(\d+)\s*/\s*100", r"score\s*[:=]\s*(\d+)", r"امتیاز\s*[:=]\s*(\d+)"):
        match = re.search(pattern, text, re.IGNORECASE)
        if match:
            try:
                value = int(match.group(1))
                if 0 <= value <= 100:
                    return value
            except ValueError:
                pass
    return None


def extract_health_grade(score: Optional[int]) -> str:
    if score is None: return "N/A"
    if score >= 95: return "A+"
    if score >= 90: return "A"
    if score >= 80: return "B"
    if score >= 70: return "C"
    if score >= 60: return "D"
    return "F"


def core_path(repo: Path, core: str) -> Path:
    candidate = Path(core).expanduser()
    return candidate.resolve() if candidate.is_absolute() else safe_repo_path(repo, core)


def run_core(repo: Path, core: str, args: Sequence[str], *, timeout_seconds: Optional[float] = None, quiet: bool = False) -> int:
    try:
        path = core_path(repo, core)
    except ValueError as exc:
        if not quiet: print(f"❌ {exc}", file=sys.stderr)
        return EXIT_USAGE_ERROR
    if not path.is_file():
        if not quiet: print(f"❌ core script missing: {path}", file=sys.stderr)
        return EXIT_RUNTIME_ERROR
    try:
        proc = subprocess.run(
            [sys.executable, str(path), *args], cwd=str(repo), timeout=timeout_seconds,
            stdout=subprocess.PIPE if quiet else None,
            stderr=subprocess.PIPE if quiet else None,
            text=quiet, encoding="utf-8" if quiet else None,
            errors="replace" if quiet else None, env=child_python_env(),
        )
        return int(proc.returncode)
    except subprocess.TimeoutExpired:
        if not quiet: print("⏱️ TIMEOUT", file=sys.stderr)
        return TIMEOUT_EXIT_CODE
    except KeyboardInterrupt:
        return EXIT_INTERRUPTED
    except OSError as exc:
        if not quiet: print(f"❌ failed to execute core script: {exc}", file=sys.stderr)
        return EXIT_RUNTIME_ERROR


def capture_core(repo: Path, core: str, args: Sequence[str], *, timeout_seconds: Optional[float] = None) -> subprocess.CompletedProcess:
    path = core_path(repo, core)
    if not path.is_file():
        raise FileNotFoundError(f"core script missing: {path}")
    return subprocess.run(
        [sys.executable, str(path), *args], cwd=str(repo), capture_output=True, text=True,
        encoding="utf-8", errors="replace", timeout=timeout_seconds, env=child_python_env(),
    )


def doctor(repo: Path, core: str) -> DoctorReport:
    checks: List[DoctorCheck] = [
        DoctorCheck("python", True, f"python: {sys.version_info.major}.{sys.version_info.minor}.{sys.version_info.micro} ({sys.executable})")
    ]
    if git_available():
        checks.append(DoctorCheck("git", True, _git_stdout(repo, ["--version"]) or "git available"))
    else:
        checks.append(DoctorCheck("git", False, "git executable not available"))
    is_repo = (repo / ".git").exists()
    checks.append(DoctorCheck("repository", is_repo, f"repository: {repo}"))
    try:
        cp = core_path(repo, core)
        if cp.is_file():
            proc = subprocess.run([sys.executable, "-m", "py_compile", str(cp)], cwd=str(repo), capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=30, env=child_python_env())
            checks.append(DoctorCheck("core-script", proc.returncode == 0, "core-script: syntax OK" if proc.returncode == 0 else f"core-script: syntax error: {proc.stderr.strip()}"))
        else:
            checks.append(DoctorCheck("core-script", False, f"core-script missing: {cp}"))
    except Exception as exc:
        checks.append(DoctorCheck("core-script", False, f"core-script: {exc}"))
    if is_repo:
        clean = git_is_clean(repo)
        checks.append(DoctorCheck("working-tree", clean is not None, "working-tree: clean" if clean else "working-tree: dirty", warning=(clean is False)))
        branch = git_branch(repo)
        checks.append(DoctorCheck("branch", bool(branch), f"branch: {branch}" if branch else "branch: unavailable"))
        upstream = git_upstream(repo)
        checks.append(DoctorCheck("upstream", True, f"upstream: {upstream}" if upstream else "upstream: not configured", warning=not bool(upstream)))
    docs = repo / "docs"
    if not docs.is_dir():
        checks.append(DoctorCheck("docs", False, f"docs directory missing: {docs}"))
    else:
        required = (("docs/0.4.md", 61), ("docs/0.5.md", 73), ("docs/changelog.md", None), ("docs/rules.md", None), ("docs/decisions.md", None))
        for relative, expected in required:
            target = repo / relative
            name = f"doc:{relative}"
            if not target.is_file():
                checks.append(DoctorCheck(name, False, f"{name}: missing"))
                continue
            try:
                text = target.read_text(encoding="utf-8", errors="strict")
            except Exception as exc:
                checks.append(DoctorCheck(name, False, f"{name}: {exc}"))
                continue
            if expected is None:
                checks.append(DoctorCheck(name, True, f"{name}: {target.stat().st_size} bytes"))
            else:
                count = text.count("ماده")
                checks.append(DoctorCheck(name, True, f"{name}: rough article-token count={count}; expected at least {expected}", warning=count < expected))
    for filename in (".gitignore", "README.md", "LICENSE", "LICENSE-DOCS.md"):
        if not (repo / filename).exists():
            checks.append(DoctorCheck(filename, False, f"{filename} missing (recommended)", warning=True))
    hard_failure = any(not x.ok and not x.warning for x in checks)
    return DoctorReport(ok=not hard_failure, checks=checks)


def print_doctor(report: DoctorReport) -> None:
    print("=" * 72, f"\nNazm Dad — Doctor v{TOOL_VERSION}\n" + "=" * 72)
    for item in report.checks:
        icon = Colors.green("✅") if item.ok and not item.warning else Colors.yellow("⚠️") if item.warning else Colors.red("❌")
        print(f"{icon} {item.detail}")
    print("=" * 72)
    print(Colors.green("PASS") if report.ok else Colors.red("FAIL"))


def doctor_payload(repo: Path, core: str, report: DoctorReport) -> Dict[str, Any]:
    try: resolved_core = str(core_path(repo, core))
    except ValueError as exc: resolved_core = f"<invalid core path: {exc}>"
    return {"schema": 1, "tool": "nazm-dad-project-status", "version": TOOL_VERSION, "timestamp": utc_timestamp(), "repo": str(repo), "core": resolved_core, "ok": report.ok, "checks": [asdict(x) for x in report.checks], "issues": report.issues, "warnings": report.warnings}


ARTICLE_PATTERN = re.compile(
    r"""
    ^\s*
    (?:\#{1,6}\s*)?
    (?:\*\*)?
    ماده
    (?:\s*ٔ|\s*‌ی)?
    \s+
    ([۰-۹٠-٩0-9]+(?:\s*[-–—−]\s*[۰-۹٠-٩0-9]+)?)
    (?=
        \s*
        (?:
            \*\*
            |
            [-ـ–—−:：.]
            |
            $
        )
    )
    """,
    re.MULTILINE | re.VERBOSE,
)

_DIGIT_TRANS = str.maketrans(
    "۰۱۲۳۴۵۶۷۸۹٠١٢٣٤٥٦٧٨٩",
    "01234567890123456789",
)


def normalize_digits(value: str) -> str:
    return value.translate(_DIGIT_TRANS)


def normalize_article_id(value: str) -> str:
    """یکسان‌سازی ارقام، خط تیره و فاصله‌های شناسه ماده."""
    value = normalize_digits(value)
    value = (
        value
        .replace("–", "-")
        .replace("—", "-")
        .replace("−", "-")
        .strip()
    )
    return re.sub(r"\s*-\s*", "-", value)


def find_articles(content: str) -> List[str]:
    return [normalize_article_id(m.group(1)) for m in ARTICLE_PATTERN.finditer(content)]


def article_sort_key(article_id: str) -> Optional[Tuple[int, int]]:
    normalized = normalize_article_id(article_id)
    parts = normalized.split("-", 1)
    try:
        base = int(parts[0])
    except (ValueError, IndexError):
        return None
    sub = 0
    if len(parts) == 2:
        try:
            sub = int(parts[1])
        except ValueError:
            sub = 0
    return base, sub


def article_base_number(article_id: str) -> Optional[int]:
    key = article_sort_key(article_id)
    return key[0] if key is not None else None


def validate_articles_in_file(file_path: Path, expected_count: Optional[int] = None) -> ArticleValidation:
    """
    اعتبارسنجی مواد یک فایل.

    expected_count در این پروژه تعداد کل تیترهای مادهٔ مورد انتظار است،
    نه بزرگ‌ترین شمارهٔ ماده. بنابراین شناسه‌های مرکب مانند 62-1 و 62-2
    مواد مستقل‌اند و نباید باعث گزارش اشتباه «62، 63، ...» به‌عنوان مفقود شوند.
    """
    if not file_path.is_file():
        return ArticleValidation(str(file_path), expected_count or 0, 0, [], [], [], [], False, False)

    try:
        content = file_path.read_text(encoding="utf-8", errors="strict")
    except (OSError, UnicodeDecodeError):
        return ArticleValidation(str(file_path), expected_count or 0, 0, [], [], [], [], False, False)

    ids = find_articles(content)

    seen: Set[str] = set()
    duplicate_seen: Set[str] = set()
    duplicates: List[str] = []
    for article_id in ids:
        normalized = normalize_article_id(article_id)
        if normalized in seen and normalized not in duplicate_seen:
            duplicates.append(normalized)
            duplicate_seen.add(normalized)
        seen.add(normalized)

    out_of_order: List[str] = []
    previous_key: Optional[Tuple[int, int]] = None
    for article_id in ids:
        current_key = article_sort_key(article_id)
        if current_key is None:
            continue
        if previous_key is not None and current_key < previous_key:
            out_of_order.append(normalize_article_id(article_id))
        previous_key = current_key

    total_found = len(ids)
    missing: List[str] = []
    if expected_count is not None and total_found < expected_count:
        shortage = expected_count - total_found
        missing = [f"count:{n}" for n in range(total_found + 1, total_found + shortage + 1)]

    has_continuity = not out_of_order
    count_is_valid = expected_count is None or total_found == expected_count
    is_valid = count_is_valid and not duplicates and not out_of_order and has_continuity

    return ArticleValidation(
        str(file_path), expected_count or 0, total_found, ids,
        missing, duplicates, out_of_order, has_continuity, is_valid,
    )


def validate_articles(repo: Path) -> List[ArticleValidation]:
    specs = {
        "docs/0.4.md": 61,
        "docs/0.5.md": 73,
        "docs/changelog.md": None,
        "docs/rules.md": None,
        "docs/decisions.md": None,
    }
    return [validate_articles_in_file(repo / path, expected) for path, expected in specs.items()]


def print_article_validation(results: List[ArticleValidation]) -> None:
    print("=" * 72, "\nNazm Dad — Article Validation\n" + "=" * 72)
    for result in results:
        print(f"\n{Colors.green('✅') if result.is_valid else Colors.red('❌')} {result.file}")
        print(f"   مواد یافت‌شده: {result.total_found}")
        if result.total_expected: print(f"   مواد مورد انتظار: {result.total_expected}")
        if result.duplicates: print("   ⚠️ تکراری: " + Colors.yellow(", ".join(result.duplicates)))
        if result.out_of_order: print("   ⚠️ خارج از ترتیب: " + Colors.yellow(", ".join(result.out_of_order)))
        if result.missing:
            preview = ", ".join(result.missing[:10])
            print(f"   ⚠️ مفقود: {Colors.yellow(preview)}")
            if len(result.missing) > 10: print(f"      ... و {len(result.missing)-10} مورد دیگر")
        if not result.has_continuity and result.total_found > 1: print("   ⚠️ ترتیب مواد پیوسته نیست")
        if result.is_valid: print("   ✅ ساختار مواد صحیح است")
    print("\n" + "=" * 72)
    print(Colors.green("✅ همه اسناد معتبر هستند") if all(x.is_valid for x in results) else Colors.red("❌ برخی اسناد مشکل دارند"))


def article_validation_payload(results: List[ArticleValidation]) -> Dict[str, Any]:
    return {"schema": 1, "tool": "nazm-dad-project-status", "version": TOOL_VERSION, "timestamp": utc_timestamp(), "results": [asdict(x) for x in results], "all_valid": all(x.is_valid for x in results)}


MARKDOWN_LINK_PATTERN = re.compile(r"\[[^\]]+\]\(([^)]+)\)")


def strip_markdown_link_title(target: str) -> str:
    target = target.strip()
    if target.startswith("<") and ">" in target:
        return target[1:target.index(">")]
    match = re.match(r'''^(.*?)(?:\s+["'][^"']*["'])$''', target)
    return match.group(1).strip() if match else target


def check_external_link(target: str, timeout: float = 5.0) -> bool:
    try:
        if urlparse(target).scheme not in ("http", "https"):
            return True
        req = urllib.request.Request(target, method="HEAD", headers={"User-Agent": f"NazmDad/{TOOL_VERSION}"})
        try:
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                return 200 <= getattr(resp, "status", 200) < 400
        except urllib.error.HTTPError as exc:
            if exc.code not in (405, 501):
                return False
            req = urllib.request.Request(target, method="GET", headers={"User-Agent": f"NazmDad/{TOOL_VERSION}"})
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                return 200 <= getattr(resp, "status", 200) < 400
    except Exception:
        return False


def check_links_in_file(file_path: Path, repo: Path, check_external: bool = False, exclude_patterns: Optional[List[str]] = None) -> List[LinkCheckResult]:
    if not file_path.is_file() or (exclude_patterns and is_excluded(file_path, exclude_patterns)):
        return []
    try:
        lines = file_path.read_text(encoding="utf-8", errors="replace").splitlines()
    except OSError:
        return []
    results: List[LinkCheckResult] = []
    root = repo.resolve()
    for line_number, line in enumerate(lines, 1):
        for match in MARKDOWN_LINK_PATTERN.finditer(line):
            raw = strip_markdown_link_title(match.group(1))
            target = unquote(raw)
            lower = target.lower()
            if lower.startswith(("http://", "https://")):
                if check_external:
                    ok = check_external_link(target)
                    results.append(LinkCheckResult(rel(file_path, repo), line_number, raw, "ok" if ok else "broken", "External link OK" if ok else "External link failed"))
                else:
                    results.append(LinkCheckResult(rel(file_path, repo), line_number, raw, "external", "External link (skipped)"))
                continue
            if target.startswith(("mailto:", "tel:")):
                results.append(LinkCheckResult(rel(file_path, repo), line_number, raw, "external", "External link (not fetched)"))
                continue
            if target.startswith("#"):
                results.append(LinkCheckResult(rel(file_path, repo), line_number, raw, "anchor", "Local anchor (not validated)"))
                continue
            clean_target = target.split("#", 1)[0].strip()
            if not clean_target:
                continue
            resolved = (file_path.parent / clean_target).resolve()
            try:
                resolved.relative_to(root)
            except ValueError:
                results.append(LinkCheckResult(rel(file_path, repo), line_number, raw, "broken", "Link escapes repository"))
                continue
            results.append(LinkCheckResult(rel(file_path, repo), line_number, raw, "ok" if resolved.exists() else "broken", "Link exists" if resolved.exists() else "Link target not found"))
    return results


def check_all_links(repo: Path, check_external: bool = False, exclude_patterns: Optional[List[str]] = None) -> List[LinkCheckResult]:
    docs = repo / "docs"
    if not docs.is_dir(): return []
    results: List[LinkCheckResult] = []
    for md in sorted(docs.rglob("*.md")):
        results.extend(check_links_in_file(md, repo, check_external, exclude_patterns))
    return results


def link_payload(results: List[LinkCheckResult]) -> Dict[str, Any]:
    return {
        "schema": 1, "tool": "nazm-dad-project-status", "version": TOOL_VERSION, "timestamp": utc_timestamp(),
        "stats": {"total": len(results), "ok": sum(x.status == "ok" for x in results), "external": sum(x.status == "external" for x in results), "anchor": sum(x.status == "anchor" for x in results), "broken": sum(x.status == "broken" for x in results)},
        "broken": [asdict(x) for x in results if x.status == "broken"],
    }


def print_link_results(results: List[LinkCheckResult]) -> None:
    stats = link_payload(results)["stats"]
    print("=" * 72, "\nNazm Dad — Link Check\n" + "=" * 72)
    print(f"\n✅ OK: {Colors.green(str(stats['ok']))}")
    print(f"🌐 External: {Colors.blue(str(stats['external']))}")
    print(f"🔗 Anchors: {Colors.dim(str(stats['anchor']))}")
    print(f"❌ Broken: {(Colors.red if stats['broken'] else Colors.green)(str(stats['broken']))}")
    for item in [x for x in results if x.status == "broken"]:
        print(f"  {item.file}:{item.line} -> {item.target} ({item.description})")
    print("\n" + "=" * 72)


def build_summary(repo: Path, core: str, timeout_seconds: float = STRICT_TIMEOUT_SECONDS) -> ProjectSummary:
    score: Optional[int] = None
    try:
        proc = capture_core(repo, core, ["--health", "--no-progress"], timeout_seconds=timeout_seconds)
        if proc.returncode in (0, 1): score = extract_health_score(proc.stdout)
    except Exception:
        pass
    total = valid = placeholder = invalid = articles = 0
    for relative in DEFAULT_REPAIR_FILES:
        total += 1
        target = repo / relative
        if not target.is_file():
            invalid += 1; continue
        try: content = target.read_text(encoding="utf-8", errors="strict")
        except Exception: invalid += 1; continue
        if "placeholder" in content.lower() or "در انتظار" in content: placeholder += 1
        else: valid += 1
        articles += len(find_articles(content))
    ahead, behind = git_ahead_behind(repo)
    return ProjectSummary(git_branch(repo) or "detached", bool(git_is_clean(repo)), git_commit_short(repo) or "unknown", git_commit_date(repo), total, valid, placeholder, invalid, articles, score, extract_health_grade(score), ahead, behind)


def summary_payload(s: ProjectSummary) -> Dict[str, Any]:
    return {"schema": 1, "tool": "nazm-dad-project-status", "version": TOOL_VERSION, "timestamp": utc_timestamp(), "git": {"branch": s.branch, "clean": s.is_clean, "commit": s.commit, "commit_date": s.commit_date, "ahead": s.changes_ahead, "behind": s.changes_behind}, "documents": {"total": s.total_documents, "valid": s.valid_documents, "placeholder": s.placeholder_documents, "invalid": s.invalid_documents, "total_articles": s.total_articles}, "health": {"score": s.health_score, "grade": s.health_grade}}


def print_summary(s: ProjectSummary) -> None:
    print("=" * 72, f"\nNazm Dad — Summary v{TOOL_VERSION}\n" + "=" * 72)
    print("\n📦 Git:")
    print(f"  Branch: {Colors.cyan(s.branch)}")
    print("  Working tree: " + (Colors.green("✅ clean") if s.is_clean else Colors.red("❌ dirty")))
    print(f"  Commit: {Colors.blue(s.commit)} ({s.commit_date})")
    if s.changes_ahead or s.changes_behind: print(f"  Ahead/Behind: {s.changes_ahead}/{s.changes_behind}")
    print("\n📄 Documents:")
    print(f"  Total: {s.total_documents}")
    if s.total_documents:
        progress = s.valid_documents / s.total_documents * 100
        filled = max(0, min(30, round(30 * progress / 100)))
        bar = "█" * filled + "░" * (30 - filled)
        color = Colors.green if progress >= 80 else Colors.yellow if progress >= 50 else Colors.red
        print(f"  Progress: [{color(bar)}] {progress:.1f}%")
        print(f"  ✅ Valid: {Colors.green(str(s.valid_documents))}")
        print(f"  ⏳ Placeholder: {Colors.yellow(str(s.placeholder_documents))}")
        print(f"  ❌ Invalid: {Colors.red(str(s.invalid_documents))}")
    print(f"  📊 Total articles: {s.total_articles}")
    print("\n🏥 Health:")
    if s.health_score is None: print("  Score: N/A")
    else:
        color = Colors.green if s.health_score >= 80 else Colors.yellow if s.health_score >= 60 else Colors.red
        print(f"  Score: {color(str(s.health_score))}/100")
        print(f"  Grade: {s.health_grade}")
    print("\n" + "=" * 72)


def check_consistency(repo: Path, exclude_patterns: Optional[List[str]] = None) -> List[ConsistencyIssue]:
    docs = repo / "docs"
    if not docs.is_dir(): return []
    article_ids: Set[str] = set()
    files = [x for x in docs.rglob("*.md") if not (exclude_patterns and is_excluded(x, exclude_patterns))]
    for md in files:
        try: article_ids.update(find_articles(md.read_text(encoding="utf-8", errors="replace")))
        except OSError: pass
    if not article_ids: return []
    issues: List[ConsistencyIssue] = []
    pattern = re.compile(r"ماده(?:ٔ|‌ی)?\s+([۰-۹٠-٩0-9]+(?:\s*[-–—−]\s*[۰-۹٠-٩0-9]+)?)")
    for md in files:
        try: lines = md.read_text(encoding="utf-8", errors="replace").splitlines()
        except OSError: continue
        for line_num, line in enumerate(lines, 1):
            for m in pattern.finditer(line):
                raw = m.group(1); normalized = normalize_article_id(raw)
                if normalized not in article_ids:
                    issues.append(ConsistencyIssue(rel(md, repo), line_num, raw, f"ارجاع به ماده‌ای که وجود ندارد: {normalized}"))
    return issues


def print_consistency(issues: List[ConsistencyIssue]) -> None:
    print("=" * 72, "\nNazm Dad — Consistency Check\n" + "=" * 72)
    if not issues: print("\n✅ همه ارجاعات معتبر هستند.")
    else:
        print(f"\n❌ {len(issues)} مشکل سازگاری یافت شد:")
        for x in issues: print(f"  {x.file}:{x.line} -> {Colors.yellow(x.target)}: {Colors.dim(x.description)}")
    print("\n" + "=" * 72)


def consistency_payload(issues: List[ConsistencyIssue]) -> Dict[str, Any]:
    return {"schema": 1, "tool": "nazm-dad-project-status", "version": TOOL_VERSION, "timestamp": utc_timestamp(), "total_issues": len(issues), "issues": [asdict(x) for x in issues], "valid": not issues}


def fix_issues(repo: Path, dry_run: bool = False, auto_fix: bool = False, exclude_patterns: Optional[List[str]] = None) -> int:
    if not auto_fix and not dry_run:
        print("❌ No fix mode specified. Use --auto-fix or --dry-run.", file=sys.stderr); return EXIT_USAGE_ERROR
    print("=" * 72, f"\nNazm Dad — Fix Issues v{TOOL_VERSION}\n" + "=" * 72)
    if dry_run: print(Colors.yellow("\n⚠️ DRY-RUN: No files will be modified.\n"))
    fixed = 0
    safe_files = {
        ".gitignore": "# Python\n__pycache__/\n*.pyc\n*.pyo\n.venv/\nvenv/\n.idea/\n.vscode/\n.DS_Store\n",
        "README.md": "# Nazm Dad\n\nA constitutional framework for Iran.\n",
    }
    for filename, content in safe_files.items():
        target = repo / filename
        if target.exists(): continue
        if dry_run: print(f"  [dry-run] would create: {filename}")
        else:
            try: target.write_text(content, encoding="utf-8"); print(f"  ✅ created: {filename}"); fixed += 1
            except OSError as exc: print(f"  ❌ failed to create {filename}: {exc}")
    for filename in ("LICENSE", "LICENSE-DOCS.md"):
        if not (repo / filename).exists():
            print(Colors.yellow(f"  ⚠️ {filename} missing; not created automatically because licensing requires an explicit project decision."))
    if auto_fix:
        broken = [x for x in check_all_links(repo, exclude_patterns=exclude_patterns) if x.status == "broken"]
        if broken:
            print(f"\n🔗 Found {len(broken)} broken links.")
            for x in broken[:5]: print(f"  {x.file}:{x.line} -> {x.target}")
    print("\n" + "=" * 72)
    print(Colors.green("✅ Dry-run completed." if dry_run else f"✅ Fixed {fixed} issues."))
    return EXIT_OK


def show_history(repo: Path, max_count: int = 10) -> int:
    print("=" * 72, f"\nNazm Dad — History v{TOOL_VERSION}\n" + "=" * 72)
    for file_name in ("docs/0.4.md", "docs/0.5.md", "docs/changelog.md"):
        print(f"\n📄 {file_name}:")
        entries = git_file_history(repo, file_name, max_count)
        if not entries: print("  No history found")
        for e in entries: print(f"  {e['commit']} | {e['date'][:10]} | {e['message']}")
    print("\n" + "=" * 72); return EXIT_OK


def git_file_at_ref(repo: Path, ref: str, relative: str) -> Optional[bytes]:
    try:
        proc = subprocess.run(["git", "-C", str(repo), "show", f"{ref}:{normalize_rel_path(relative)}"], capture_output=True, timeout=30)
        return proc.stdout if proc.returncode == 0 else None
    except (OSError, subprocess.TimeoutExpired): return None


def source_file_bytes(source_dir: Path, relative: str) -> Optional[bytes]:
    try:
        root = source_dir.resolve(); target = (root / normalize_rel_path(relative)).resolve(); target.relative_to(root)
        return target.read_bytes() if target.is_file() else None
    except (OSError, ValueError): return None


def repair_preview(repo: Path, files: Sequence[str], *, from_ref: Optional[str] = None, source_dir: Optional[Path] = None) -> Tuple[bool, List[RepairPreviewItem]]:
    if bool(from_ref) == bool(source_dir): raise ValueError("repair preview requires exactly one source")
    items: List[RepairPreviewItem] = []; all_found = True
    for raw in files:
        relative = normalize_rel_path(raw)
        try: target = safe_repo_path(repo, relative)
        except ValueError as exc:
            all_found = False; items.append(RepairPreviewItem(relative, "invalid", False, False, False, str(exc))); continue
        if from_ref:
            source_name = f"git:{from_ref}:{relative}"; replacement = git_file_at_ref(repo, from_ref, relative)
        else:
            assert source_dir is not None
            source_name = str(source_dir / relative); replacement = source_file_bytes(source_dir, relative)
        if replacement is None:
            all_found = False; items.append(RepairPreviewItem(relative, source_name, target.exists(), False, False, "source file not found")); continue
        current = target.read_bytes() if target.exists() else b""
        changed = current != replacement
        diff = ""
        if changed:
            diff = "".join(difflib.unified_diff(current.decode("utf-8", "replace").splitlines(True), replacement.decode("utf-8", "replace").splitlines(True), fromfile=relative, tofile=source_name))
        items.append(RepairPreviewItem(relative, source_name, target.exists(), True, changed, "would be replaced" if changed else "already identical", diff))
    return all_found, items


def print_repair_preview(items: Sequence[RepairPreviewItem]) -> None:
    print("=" * 72, "\nNazm Dad — Repair Preview\n" + "=" * 72)
    print(Colors.dim("DRY-RUN: no file will be modified.\n"))
    for x in items:
        icon = Colors.red("❌") if not x.source_exists else Colors.yellow("🟡") if x.changed else Colors.green("✅")
        print(f"{icon} {x.path}: {x.description}\n   source: {Colors.dim(x.source)}")
        if x.diff: print(x.diff)


def save_state(repo: Path, state_path: Path, core: str = DEFAULT_CORE, timeout_seconds: float = STRICT_TIMEOUT_SECONDS, exclude_patterns: Optional[List[str]] = None) -> bool:
    try:
        state = {"version": TOOL_VERSION, "timestamp": utc_timestamp(), "commit": git_commit(repo), "branch": git_branch(repo), "summary": asdict(build_summary(repo, core, timeout_seconds)), "articles": [asdict(x) for x in validate_articles(repo)], "links": link_payload(check_all_links(repo, exclude_patterns=exclude_patterns))}
        state_path.parent.mkdir(parents=True, exist_ok=True); state_path.write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"✅ State saved to: {state_path}"); return True
    except Exception as exc:
        print(f"❌ Failed to save state: {exc}", file=sys.stderr); return False


def diff_state(repo: Path, state_path: Path, core: str = DEFAULT_CORE, timeout_seconds: float = STRICT_TIMEOUT_SECONDS) -> int:
    if not state_path.exists(): print(f"❌ State file not found: {state_path}", file=sys.stderr); return EXIT_RUNTIME_ERROR
    try:
        saved = json.loads(state_path.read_text(encoding="utf-8")); old = ProjectSummary(**saved.get("summary", {})); cur = build_summary(repo, core, timeout_seconds)
    except Exception as exc:
        print(f"❌ Failed to read state: {exc}", file=sys.stderr); return EXIT_RUNTIME_ERROR
    print("=" * 72, "\nNazm Dad — State Diff\n" + "=" * 72)
    changes = []
    for label, attr in (("Health Score", "health_score"), ("Valid Documents", "valid_documents"), ("Invalid Documents", "invalid_documents"), ("Total Articles", "total_articles"), ("Working Tree", "is_clean")):
        a, b = getattr(old, attr), getattr(cur, attr)
        if a != b: changes.append(f"  {label}: {a} → {b}")
    if changes:
        print("\n📋 Changes detected:\n" + "\n".join(changes)); return EXIT_VALIDATION_FAILED
    print("\n✅ No changes detected."); return EXIT_OK


def generate_markdown_report(repo: Path, core: str, output_path: Path, *, check_external: bool = False, timeout_seconds: float = STRICT_TIMEOUT_SECONDS, exclude_patterns: Optional[List[str]] = None) -> bool:
    summary = build_summary(repo, core, timeout_seconds); articles = validate_articles(repo); links = check_all_links(repo, check_external, exclude_patterns)
    health_text = f"{summary.health_score}/100" if summary.health_score is not None else "N/A"
    lines = ["# Nazm Dad — Status Report", "", f"**Version:** {TOOL_VERSION}", f"**Generated:** {utc_timestamp()}", f"**Repository:** `{repo}`", f"**Branch:** `{summary.branch}`", f"**Commit:** `{summary.commit}` ({summary.commit_date})", f"**Working Tree:** {'✅ Clean' if summary.is_clean else '❌ Dirty'}", "", "## 📊 Summary", "", "| Metric | Value |", "|---|---:|", f"| Total Documents | {summary.total_documents} |", f"| ✅ Valid | {summary.valid_documents} |", f"| ⏳ Placeholder | {summary.placeholder_documents} |", f"| ❌ Invalid | {summary.invalid_documents} |", f"| 📄 Total Articles | {summary.total_articles} |", f"| 🏥 Health Score | {health_text} |", f"| Grade | {summary.health_grade or 'N/A'} |", "", "## 📄 Document Validation", "", "| File | Status | Articles Found | Expected | Issues |", "|---|---:|---:|---|---|"]
    for a in articles:
        issues = []
        if a.duplicates: issues.append("duplicates: " + ", ".join(a.duplicates))
        if a.out_of_order: issues.append("out of order: " + ", ".join(a.out_of_order))
        if a.missing: issues.append("missing: " + ", ".join(a.missing[:5]))
        lines.append(f"| `{Path(a.file).name}` | {'✅' if a.is_valid else '❌'} | {a.total_found} | {a.total_expected or 'N/A'} | {'; '.join(issues) if issues else '✅'} |")
    stats = link_payload(links)["stats"]
    lines += ["", "## 🔗 Links", "", "| Total | ✅ OK | 🌐 External | 🔗 Anchors | ❌ Broken |", "|---:|---:|---:|---:|---:|", f"| {stats['total']} | {stats['ok']} | {stats['external']} | {stats['anchor']} | {stats['broken']} |", "", "---", f"*Generated by Nazm Dad v{TOOL_VERSION}*"]
    try:
        output_path.parent.mkdir(parents=True, exist_ok=True); output_path.write_text("\n".join(lines), encoding="utf-8"); print(f"✅ Markdown report written: {output_path}"); return True
    except OSError as exc:
        print(f"❌ Failed to write Markdown: {exc}", file=sys.stderr); return False


def resolve_hash_files(repo: Path, requested: Optional[Sequence[str]]) -> List[str]:
    candidates = [normalize_rel_path(x) for x in requested] if requested else [*DEFAULT_REPAIR_FILES, DEFAULT_CORE]
    result: List[str] = []; seen: Set[str] = set()
    for relative in candidates:
        if relative in seen: continue
        seen.add(relative)
        try: path = safe_repo_path(repo, relative)
        except ValueError: continue
        if path.is_file(): result.append(relative)
    return result


def build_manifest(repo: Path, files: Sequence[str]) -> Dict[str, Any]:
    records = []
    for relative in files:
        target = safe_repo_path(repo, relative)
        if not target.is_file(): raise FileNotFoundError(f"file not found for manifest: {relative}")
        records.append({"path": normalize_rel_path(relative), "sha256": sha256_file(target), "size": target.stat().st_size})
    return {"schema": 1, "algorithm": "sha256", "tool": "nazm-dad-project-status", "version": TOOL_VERSION, "created_at": utc_timestamp(), "commit": git_commit(repo), "files": records}


def write_manifest(repo: Path, manifest_path: Path, files: Sequence[str]) -> bool:
    try:
        manifest_path.parent.mkdir(parents=True, exist_ok=True); manifest_path.write_text(json.dumps(build_manifest(repo, files), ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        print(f"✅ hash manifest written: {rel(manifest_path, repo)}"); return True
    except Exception as exc:
        print(f"❌ {exc}", file=sys.stderr); return False


def verify_manifest(repo: Path, manifest_path: Path) -> bool:
    try: data = json.loads(manifest_path.read_text(encoding="utf-8"))
    except Exception as exc: print(f"❌ invalid manifest: {exc}", file=sys.stderr); return False
    ok = True
    for record in data.get("files", []):
        try:
            path = safe_repo_path(repo, record["path"])
            if not path.is_file(): raise FileNotFoundError("missing")
            actual = sha256_file(path)
            if actual != record["sha256"]: print(f"❌ {record['path']}: hash mismatch"); ok = False
            else: print(f"✅ {record['path']}: verified")
        except Exception as exc:
            print(f"❌ {record.get('path', '?')}: {exc}"); ok = False
    return ok


def run_strict(repo: Path, core: str, *, timeout_seconds: float = STRICT_TIMEOUT_SECONDS, quiet: bool = False, include_health: bool = True) -> Tuple[bool, Dict[str, StrictResult]]:
    commands = {"validate_docs": ["--validate-docs", "--no-progress"], "check_links": ["--check-links", "--no-progress"]}
    if include_health: commands["health"] = ["--health", "--no-progress"]
    results: Dict[str, StrictResult] = {}
    for name, arguments in commands.items():
        started = time.monotonic(); code = run_core(repo, core, arguments, timeout_seconds=timeout_seconds, quiet=quiet); results[name] = StrictResult(code, time.monotonic() - started)
        if not quiet: print(f"{'✅' if code == 0 else '❌'} {name}: exit={code} ({results[name].duration_seconds:.2f}s)")
    started = time.monotonic(); report = doctor(repo, core); results["doctor"] = StrictResult(EXIT_OK if report.ok else EXIT_VALIDATION_FAILED, time.monotonic() - started)
    return all(x.exit_code == EXIT_OK for x in results.values()), results


def strict_results_payload(results: Dict[str, StrictResult]) -> Dict[str, Any]:
    return {k: {"exit_code": v.exit_code, "duration_seconds": round(v.duration_seconds, 6), "timed_out": v.exit_code == TIMEOUT_EXIT_CODE} for k, v in results.items()}


def ci_report(repo: Path, core: str, *, strict: bool, timeout_seconds: float, threshold: int = 90) -> Dict[str, Any]:
    report = doctor(repo, core)
    payload: Dict[str, Any] = {"schema": 1, "tool": "nazm-dad-project-status", "version": TOOL_VERSION, "timestamp": utc_timestamp(), "repo": str(repo), "branch": git_branch(repo), "commit": git_commit(repo), "timeout_seconds": timeout_seconds, "threshold": threshold, "threshold_enforced": strict, "doctor": {"ok": report.ok, "checks": [asdict(x) for x in report.checks]}}
    if strict:
        success, results = run_strict(repo, core, timeout_seconds=timeout_seconds, quiet=True, include_health=False); payload["strict"] = {"ok": success, "results": strict_results_payload(results)}
    health_score = None; health_ok = False
    try:
        proc = capture_core(repo, core, ["--health", "--no-progress"], timeout_seconds=timeout_seconds); health_score = extract_health_score(proc.stdout); health_ok = health_score is not None and health_score >= threshold; payload["health"] = {"exit_code": proc.returncode, "stdout": proc.stdout, "stderr": proc.stderr}
    except subprocess.TimeoutExpired: payload["health"] = {"exit_code": TIMEOUT_EXIT_CODE, "timed_out": True}
    except Exception as exc: payload["health"] = {"error": str(exc)}
    if health_score is not None: payload["health_score"] = health_score
    payload["health_ok"] = health_ok
    payload["ok"] = report.ok and (payload.get("strict", {"ok": True})["ok"] if strict else True) and (health_ok if strict else True)
    return payload


def generate_report(repo: Path, core: str, timeout_seconds: float, *, check_external: bool = False, exclude_patterns: Optional[List[str]] = None) -> Dict[str, Any]:
    report = doctor(repo, core); articles = validate_articles(repo); links = check_all_links(repo, check_external, exclude_patterns)
    try:
        proc = capture_core(repo, core, ["--health", "--no-progress"], timeout_seconds=timeout_seconds); health_data = {"exit_code": proc.returncode, "score": extract_health_score(proc.stdout), "stdout": proc.stdout, "stderr": proc.stderr}
    except subprocess.TimeoutExpired: health_data = {"exit_code": TIMEOUT_EXIT_CODE, "timed_out": True, "score": None}
    except Exception as exc: health_data = {"error": str(exc), "score": None}
    return {"schema": 1, "timestamp": utc_timestamp(), "version": TOOL_VERSION, "repo": str(repo), "branch": git_branch(repo), "commit": git_commit(repo), "clean": git_is_clean(repo), "doctor": {"ok": report.ok, "issues": report.issues, "warnings": report.warnings}, "health": health_data, "articles": {"total_files": len(articles), "valid_files": sum(x.is_valid for x in articles), "total_articles": sum(x.total_found for x in articles), "valid": all(x.is_valid for x in articles), "results": [asdict(x) for x in articles]}, "links": link_payload(links)}


def generate_html_report(payload: Dict[str, Any], output_path: Path) -> bool:
    def esc(v: Any) -> str: return html.escape(str(v))
    checks = payload.get("doctor", {}).get("checks", []); rows = []
    for check in checks:
        status = "✅" if check.get("ok") and not check.get("warning") else "⚠️" if check.get("warning") else "❌"
        rows.append(f"<tr><td>{esc(check.get('name',''))}</td><td>{status}</td><td>{esc(check.get('detail',''))}</td></tr>")
    score = payload.get("health_score"); health = f"<h2>Health</h2><p><strong>Score:</strong> {score}/100</p><p><strong>Grade:</strong> {extract_health_grade(score)}</p>" if isinstance(score, int) else ""
    content = f'''<!doctype html><html><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>Nazm Dad CI Report</title><style>body{{font-family:Arial,sans-serif;margin:24px;background:#f5f0e6}}.container{{max-width:960px;margin:auto;background:white;padding:28px;border-radius:14px}}table{{width:100%;border-collapse:collapse}}th,td{{text-align:left;padding:8px;border-bottom:1px solid #ddd}}</style></head><body><div class="container"><h1>Nazm Dad — CI Report</h1><p><strong>Version:</strong> {TOOL_VERSION}</p><p><strong>Timestamp:</strong> {esc(payload.get('timestamp',''))}</p><p><strong>Status:</strong> {'✅ PASS' if payload.get('ok') else '❌ FAIL'}</p><h2>Git</h2><p><strong>Branch:</strong> {esc(payload.get('branch',''))}</p><p><strong>Commit:</strong> {esc(str(payload.get('commit',''))[:8])}</p><h2>Doctor</h2><table><tr><th>Check</th><th>Status</th><th>Detail</th></tr>{''.join(rows)}</table>{health}<hr><p><em>Generated by Nazm Dad Project Status v{TOOL_VERSION}</em></p></div></body></html>'''
    try:
        output_path.parent.mkdir(parents=True, exist_ok=True); output_path.write_text(content, encoding="utf-8"); print(f"✅ HTML report written: {output_path}"); return True
    except OSError as exc:
        print(f"❌ Failed to write HTML: {exc}", file=sys.stderr); return False


def compare_status(repo: Path, ref: str, core: str) -> int:
    if not git_ref_exists(repo, ref): print(f"❌ Git ref not found: {ref}", file=sys.stderr); return EXIT_VALIDATION_FAILED
    print_doctor(doctor(repo, core)); ok, items = repair_preview(repo, DEFAULT_REPAIR_FILES, from_ref=ref); print_repair_preview(items); return EXIT_OK if ok else EXIT_VALIDATION_FAILED


class WatchMode:
    IGNORED_DIRS = {".git", "__pycache__", "venv", ".venv", "env", ".env", "node_modules", ".pytest_cache", ".mypy_cache", "dist", "build"}
    def __init__(self, repo: Path, core: str, interval: float, patterns: List[str], timeout_seconds: float, custom_commands: Optional[List[str]], exclude_patterns: List[str], quiet: bool):
        self.repo, self.core, self.interval, self.patterns = repo, core, interval, patterns
        self.timeout_seconds, self.custom_commands, self.exclude_patterns, self.quiet = timeout_seconds, custom_commands or [], exclude_patterns, quiet
        self.snapshot_data: Dict[Path, int] = {}
    def ignored(self, path: Path) -> bool:
        return is_excluded(path, self.exclude_patterns) or any(p in self.IGNORED_DIRS for p in path.parts)
    def scan(self) -> Dict[Path, int]:
        snapshot: Dict[Path, int] = {}
        for pattern in self.patterns:
            for path in self.repo.glob(f"**/{pattern}"):
                if path.is_file() and not self.ignored(path):
                    try: snapshot[path] = path.stat().st_mtime_ns
                    except OSError: pass
        return snapshot
    def run(self) -> None:
        if not self.quiet: print(f"Nazm Dad — Watch v{TOOL_VERSION}\nPress Ctrl+C to stop")
        self.snapshot_data = self.scan()
        try:
            while True:
                time.sleep(self.interval); current = self.scan(); changed = [p for p,m in current.items() if self.snapshot_data.get(p) != m] + [p for p in self.snapshot_data if p not in current]; self.snapshot_data = current
                if not changed: continue
                if not self.quiet: print("🔄 Changed: " + ", ".join(rel(p, self.repo) for p in sorted(set(changed))))
                run_core(self.repo, self.core, ["--health", "--no-progress"], timeout_seconds=self.timeout_seconds, quiet=self.quiet)
                for command in self.custom_commands:
                    try: subprocess.run(command, shell=True, cwd=str(self.repo), timeout=self.timeout_seconds)
                    except (OSError, subprocess.TimeoutExpired): pass
        except KeyboardInterrupt:
            if not self.quiet: print("\n👋 Watch stopped.")


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="nazm_dad_project_status_v27.py", description=f"Nazm Dad Project Status v{TOOL_VERSION}")
    p.add_argument("--repo", default=None); p.add_argument("--core", default=DEFAULT_CORE); p.add_argument("--config")
    p.add_argument("--quiet", action="store_true", default=None); p.add_argument("--output"); p.add_argument("--version", action="store_true")
    p.add_argument("--no-color", action="store_true", default=None); p.add_argument("--benchmark", action="store_true", default=None); p.add_argument("--profile", action="store_true", default=None)
    p.add_argument("--exclude", nargs="+", default=None); p.add_argument("--doctor", action="store_true"); p.add_argument("--doctor-json", action="store_true")
    p.add_argument("--summary", action="store_true"); p.add_argument("--validate-articles", action="store_true"); p.add_argument("--check-links", action="store_true")
    p.add_argument("--check-external", action="store_true", default=None); p.add_argument("--check-consistency", action="store_true"); p.add_argument("--history", action="store_true"); p.add_argument("--history-count", type=int, default=10)
    p.add_argument("--report", action="store_true"); p.add_argument("--format", choices=["json", "yaml", "csv", "table"], default=None); p.add_argument("--html-report", action="store_true"); p.add_argument("--markdown-report", action="store_true")
    p.add_argument("--ci-html", action="store_true", help=argparse.SUPPRESS); p.add_argument("--ci-json", nargs="?", const="-", default=None); p.add_argument("--ci-strict", action="store_true"); p.add_argument("--strict", action="store_true")
    p.add_argument("--strict-timeout", type=float, default=None); p.add_argument("--threshold", type=int, default=None); p.add_argument("--watch", action="store_true"); p.add_argument("--watch-interval", type=float, default=None); p.add_argument("--watch-patterns", nargs="+", default=None); p.add_argument("--watch-command", nargs="+")
    p.add_argument("--repair-preview", action="store_true"); p.add_argument("--repair-from-ref"); p.add_argument("--repair-source-dir"); p.add_argument("--repair-files", nargs="+")
    p.add_argument("--hash-manifest", action="store_true"); p.add_argument("--verify-hashes", action="store_true"); p.add_argument("--manifest", default=DEFAULT_MANIFEST); p.add_argument("--hash-files", nargs="+"); p.add_argument("--compare")
    p.add_argument("--auto-fix", action="store_true"); p.add_argument("--fix", action="store_true"); p.add_argument("--dry-run", action="store_true")
    p.add_argument("--save-state", action="store_true"); p.add_argument("--state-file", default=None); p.add_argument("--diff-state", action="store_true"); p.add_argument("--export")
    return p


def main(argv: Optional[Sequence[str]] = None) -> int:
    configure_utf8_stdio(); parser = build_parser(); known, unknown = parser.parse_known_args(argv)
    effective_config_path = Path(known.config).expanduser() if known.config else Path(os.environ.get("NAZM_DAD_CONFIG", DEFAULT_CONFIG)).expanduser()
    cfg = NazmDadConfig.from_file(effective_config_path).apply_env()
    if known.repo is None: known.repo = cfg.repo_path or "."
    if known.quiet is None: known.quiet = cfg.quiet
    if known.no_color is None: known.no_color = cfg.no_color
    if not (known.benchmark or known.profile): known.benchmark = cfg.benchmark
    else: known.benchmark = True
    known.profile = bool(known.profile)
    if known.check_external is None: known.check_external = cfg.check_external
    if known.strict_timeout is None: known.strict_timeout = cfg.strict_timeout
    if known.threshold is None: known.threshold = cfg.threshold
    if known.watch_interval is None: known.watch_interval = cfg.watch_interval
    if known.watch_patterns is None: known.watch_patterns = list(cfg.watch_patterns)
    if known.exclude is None: known.exclude = list(cfg.exclude_patterns)
    cfg.exclude_patterns = list(known.exclude)
    Colors.enabled(not known.no_color); Benchmark.enable() if known.benchmark else Benchmark.disable()
    if known.ci_html: known.html_report = True
    if known.doctor_json: known.doctor = True
    if known.ci_strict and known.ci_json is None: known.ci_json = "-"
    if known.repair_from_ref or known.repair_source_dir or known.repair_files: known.repair_preview = True
    if known.fix: known.auto_fix = True
    repo = find_repo(Path(known.repo))
    if known.version: print(TOOL_VERSION); return EXIT_OK
    if known.strict_timeout <= 0: print("❌ --strict-timeout must be greater than zero", file=sys.stderr); return EXIT_USAGE_ERROR
    if not (0 <= known.threshold <= 100): print("❌ --threshold must be between 0 and 100", file=sys.stderr); return EXIT_USAGE_ERROR
    if known.watch and known.watch_interval <= 0: print("❌ --watch-interval must be greater than zero", file=sys.stderr); return EXIT_USAGE_ERROR
    if known.output and known.export: print("❌ --export and --output cannot be used together", file=sys.stderr); return EXIT_USAGE_ERROR
    output_path = Path(known.output).expanduser().resolve() if known.output else None
    if known.export:
        conflicting = any((known.summary, known.doctor, known.validate_articles, known.check_links, known.check_consistency, known.history, known.markdown_report, known.html_report, known.report, known.watch, known.auto_fix, known.dry_run, known.repair_preview, known.hash_manifest, known.verify_hashes, known.ci_json is not None, known.strict, bool(known.compare), known.save_state, known.diff_state))
        if conflicting: print("❌ --export cannot be combined with another primary action", file=sys.stderr); return EXIT_USAGE_ERROR
        output_path = Path(known.export).expanduser().resolve(); suffix = output_path.suffix.lower()
        if suffix == ".json": known.report = True; known.format = "json"
        elif suffix in (".yaml", ".yml"): known.report = True; known.format = "yaml"
        elif suffix == ".csv": known.report = True; known.format = "csv"
        elif suffix in (".html", ".htm"): known.html_report = True
        elif suffix in (".md", ".markdown"): known.markdown_report = True
        else: print(f"❌ unsupported export extension: {suffix or '<none>'}", file=sys.stderr); return EXIT_USAGE_ERROR
    if known.state_file:
        state_path = Path(known.state_file).expanduser(); state_path = (repo / state_path).resolve() if not state_path.is_absolute() else state_path.resolve()
    else: state_path = (repo / DEFAULT_STATE_FILE).resolve()

    if known.save_state: return EXIT_OK if save_state(repo, state_path, known.core, known.strict_timeout, cfg.exclude_patterns) else EXIT_RUNTIME_ERROR
    if known.diff_state: return diff_state(repo, state_path, known.core, known.strict_timeout)
    if known.summary:
        with Benchmark("summary"): summary = build_summary(repo, known.core, known.strict_timeout)
        if known.format is None: print_summary(summary)
        else:
            try: content = format_output(summary_payload(summary), known.format)
            except Exception as exc: print(f"❌ {exc}", file=sys.stderr); return EXIT_RUNTIME_ERROR
            if not write_output(content, output_path, known.quiet): return EXIT_RUNTIME_ERROR
        Benchmark.print_timings(); return EXIT_OK
    if known.doctor:
        with Benchmark("doctor"): report = doctor(repo, known.core)
        if known.doctor_json:
            if not write_output(json.dumps(doctor_payload(repo, known.core, report), ensure_ascii=False, indent=2), output_path, known.quiet): return EXIT_RUNTIME_ERROR
        else: print_doctor(report)
        Benchmark.print_timings(); return EXIT_OK if report.ok else EXIT_VALIDATION_FAILED
    if known.validate_articles:
        with Benchmark("validate_articles"): results = validate_articles(repo)
        payload = article_validation_payload(results)
        if known.format is None: print_article_validation(results)
        else:
            try: content = format_output(payload, known.format)
            except Exception as exc: print(f"❌ {exc}", file=sys.stderr); return EXIT_RUNTIME_ERROR
            if not write_output(content, output_path, known.quiet): return EXIT_RUNTIME_ERROR
        Benchmark.print_timings(); return EXIT_OK if payload["all_valid"] else EXIT_VALIDATION_FAILED
    if known.check_links:
        if not (repo / "docs").is_dir(): print(f"❌ docs directory missing: {repo/'docs'}", file=sys.stderr); return EXIT_VALIDATION_FAILED
        with Benchmark("check_links"): results = check_all_links(repo, known.check_external, cfg.exclude_patterns)
        payload = link_payload(results)
        if known.format is None: print_link_results(results)
        else:
            try: content = format_output(payload, known.format)
            except Exception as exc: print(f"❌ {exc}", file=sys.stderr); return EXIT_RUNTIME_ERROR
            if not write_output(content, output_path, known.quiet): return EXIT_RUNTIME_ERROR
        Benchmark.print_timings(); return EXIT_OK if payload["stats"]["broken"] == 0 else EXIT_VALIDATION_FAILED
    if known.check_consistency:
        with Benchmark("check_consistency"): issues = check_consistency(repo, cfg.exclude_patterns)
        payload = consistency_payload(issues)
        if known.format is None: print_consistency(issues)
        else:
            try: content = format_output(payload, known.format)
            except Exception as exc: print(f"❌ {exc}", file=sys.stderr); return EXIT_RUNTIME_ERROR
            if not write_output(content, output_path, known.quiet): return EXIT_RUNTIME_ERROR
        Benchmark.print_timings(); return EXIT_OK if payload["valid"] else EXIT_VALIDATION_FAILED
    if known.history: return show_history(repo, known.history_count)
    if known.markdown_report:
        ok = generate_markdown_report(repo, known.core, output_path or repo / "STATUS.md", check_external=known.check_external, timeout_seconds=known.strict_timeout, exclude_patterns=cfg.exclude_patterns); Benchmark.print_timings(); return EXIT_OK if ok else EXIT_RUNTIME_ERROR
    if known.html_report:
        with Benchmark("html_report"): payload = ci_report(repo, known.core, strict=known.ci_strict, timeout_seconds=known.strict_timeout, threshold=known.threshold)
        ok = generate_html_report(payload, output_path or repo / "ci-report.html"); Benchmark.print_timings(); return EXIT_OK if ok and payload.get("ok") else EXIT_VALIDATION_FAILED if ok else EXIT_RUNTIME_ERROR
    if known.report:
        try:
            with Benchmark("generate_report"): data = generate_report(repo, known.core, known.strict_timeout, check_external=known.check_external, exclude_patterns=cfg.exclude_patterns)
            content = format_output(data, known.format) if known.format else json.dumps(data, ensure_ascii=False, indent=2)
        except Exception as exc: print(f"❌ {exc}", file=sys.stderr); return EXIT_RUNTIME_ERROR
        if not write_output(content, output_path, known.quiet): return EXIT_RUNTIME_ERROR
        Benchmark.print_timings(); return EXIT_OK
    if known.watch:
        WatchMode(repo, known.core, known.watch_interval, known.watch_patterns, known.strict_timeout, known.watch_command, cfg.exclude_patterns, known.quiet).run(); return EXIT_OK
    if known.auto_fix or known.dry_run:
        with Benchmark("auto_fix"): result = fix_issues(repo, known.dry_run, known.auto_fix, cfg.exclude_patterns)
        Benchmark.print_timings(); return result
    if known.repair_preview:
        if known.repair_from_ref and known.repair_source_dir: print("❌ choose only one repair source", file=sys.stderr); return EXIT_USAGE_ERROR
        if not known.repair_from_ref and not known.repair_source_dir: print("❌ repair source required", file=sys.stderr); return EXIT_USAGE_ERROR
        source_dir = Path(known.repair_source_dir).expanduser().resolve() if known.repair_source_dir else None
        try: ok, items = repair_preview(repo, known.repair_files or list(DEFAULT_REPAIR_FILES), from_ref=known.repair_from_ref, source_dir=source_dir)
        except ValueError as exc: print(f"❌ {exc}", file=sys.stderr); return EXIT_USAGE_ERROR
        print_repair_preview(items); return EXIT_OK if ok else EXIT_VALIDATION_FAILED
    manifest_path = Path(known.manifest).expanduser(); manifest_path = (repo / manifest_path).resolve() if not manifest_path.is_absolute() else manifest_path.resolve()
    if known.hash_manifest:
        files = resolve_hash_files(repo, known.hash_files)
        if not files: print("❌ no files available for hashing", file=sys.stderr); return EXIT_RUNTIME_ERROR
        return EXIT_OK if write_manifest(repo, manifest_path, files) else EXIT_RUNTIME_ERROR
    if known.verify_hashes: return EXIT_OK if verify_manifest(repo, manifest_path) else EXIT_VALIDATION_FAILED
    if known.ci_json is not None:
        payload = ci_report(repo, known.core, strict=known.ci_strict, timeout_seconds=known.strict_timeout, threshold=known.threshold); text = json.dumps(payload, ensure_ascii=False, indent=2)
        if known.ci_json == "-": print(text)
        elif not write_output(text, Path(known.ci_json).expanduser().resolve(), known.quiet): return EXIT_RUNTIME_ERROR
        Benchmark.print_timings(); return EXIT_OK if payload.get("ok") else EXIT_VALIDATION_FAILED
    if known.strict:
        with Benchmark("strict"): success, _ = run_strict(repo, known.core, timeout_seconds=known.strict_timeout, quiet=known.quiet, include_health=True)
        Benchmark.print_timings(); return EXIT_OK if success else EXIT_VALIDATION_FAILED
    if known.compare: return compare_status(repo, known.compare, known.core)
    if unknown: return run_core(repo, known.core, unknown, timeout_seconds=None, quiet=known.quiet)
    parser.print_help(); return EXIT_OK


if __name__ == "__main__":
    raise SystemExit(main())
