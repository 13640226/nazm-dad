#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
Nazm Dad — Project Status Tool v2.7 extension layer

This module adds higher-assurance operational features on top of the stable
v2.6 tool without modifying its internal implementation.

New in v2.7
-----------
- --doctor
- --repair-preview
- --strict
- --hash-manifest / --verify-hashes
- --ci-json

The existing v2.6 script remains the execution engine for all legacy options.
Unknown arguments are forwarded to nazm_dad_project_status.py unchanged.
"""

from __future__ import annotations

import argparse
import difflib
import hashlib
import json
import os
import shutil
import subprocess
import sys
from dataclasses import asdict, dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple

TOOL_VERSION = "2.7"
DEFAULT_CORE = "nazm_dad_project_status.py"
DEFAULT_MANIFEST = ".nazm-dad-hashes.json"
DEFAULT_HASH_FILES = (
    "docs/0.4.md",
    "docs/0.5.md",
    "docs/changelog.md",
    "docs/rules.md",
    "docs/decisions.md",
)


@dataclass
class CheckResult:
    name: str
    ok: bool
    detail: str
    severity: str = "error"


@dataclass
class DoctorReport:
    ok: bool
    timestamp: str
    tool_version: str
    repo: str
    checks: List[CheckResult] = field(default_factory=list)


@dataclass
class HashRecord:
    path: str
    sha256: str
    size: int


@dataclass
class HashManifest:
    schema: int
    tool_version: str
    generated_at: str
    git_commit: Optional[str]
    files: List[HashRecord]


def run(
    args: Sequence[str],
    cwd: Optional[Path] = None,
    check: bool = False,
) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        list(args),
        cwd=str(cwd) if cwd else None,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        check=check,
    )


def find_repo(start: Path) -> Path:
    probe = start.resolve()
    result = run(["git", "-C", str(probe), "rev-parse", "--show-toplevel"])
    if result.returncode == 0 and result.stdout.strip():
        return Path(result.stdout.strip()).resolve()
    return probe


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def rel(path: Path, base: Path) -> str:
    try:
        return path.resolve().relative_to(base.resolve()).as_posix()
    except ValueError:
        return str(path.resolve())


def core_path(repo: Path, core: str) -> Path:
    path = Path(core)
    return path if path.is_absolute() else repo / path


def check_python() -> CheckResult:
    return CheckResult(
        name="python",
        ok=sys.version_info >= (3, 9),
        detail=f"{sys.version.split()[0]} ({sys.executable})",
    )


def check_git() -> CheckResult:
    git = shutil.which("git")
    if not git:
        return CheckResult("git", False, "git not found in PATH")
    result = run([git, "--version"])
    return CheckResult("git", result.returncode == 0, result.stdout.strip() or result.stderr.strip())


def check_repo(repo: Path) -> CheckResult:
    result = run(["git", "-C", str(repo), "rev-parse", "--is-inside-work-tree"])
    return CheckResult(
        "repository",
        result.returncode == 0 and result.stdout.strip() == "true",
        str(repo),
    )


def check_core(repo: Path, core: str) -> CheckResult:
    path = core_path(repo, core)
    if not path.exists():
        return CheckResult("core-script", False, f"missing: {rel(path, repo)}")

    result = run([sys.executable, "-m", "py_compile", str(path)], cwd=repo)
    detail = "syntax OK" if result.returncode == 0 else (result.stderr.strip() or "compile failed")
    return CheckResult("core-script", result.returncode == 0, detail)


def check_docs(repo: Path) -> List[CheckResult]:
    results: List[CheckResult] = []
    expected = {
        "docs/0.4.md": ("ماده", 61),
        "docs/0.5.md": ("ماده", 73),
        "docs/changelog.md": (None, None),
        "docs/rules.md": (None, None),
        "docs/decisions.md": (None, None),
    }

    python_markers = (
        "from dataclasses import",
        "import argparse",
        "def main(",
        "if __name__ ==",
        "class ExitCode",
    )

    for name, (article_word, minimum) in expected.items():
        path = repo / name
        if not path.exists():
            results.append(CheckResult(f"doc:{name}", False, "missing"))
            continue

        text = path.read_text(encoding="utf-8", errors="replace")
        contaminated = any(marker in text for marker in python_markers)
        if contaminated:
            results.append(CheckResult(f"doc:{name}", False, "Python-like contamination detected"))
            continue

        if article_word and minimum:
            count = text.count(article_word)
            ok = count >= minimum
            results.append(CheckResult(
                f"doc:{name}",
                ok,
                f"article-token count={count}; expected at least {minimum}",
            ))
        else:
            results.append(CheckResult(f"doc:{name}", True, f"{path.stat().st_size} bytes"))

    return results


def check_git_state(repo: Path) -> List[CheckResult]:
    results: List[CheckResult] = []

    status = run(["git", "-C", str(repo), "status", "--porcelain"])
    results.append(CheckResult(
        "working-tree",
        status.returncode == 0 and not status.stdout.strip(),
        "clean" if not status.stdout.strip() else "dirty",
        severity="warning",
    ))

    branch = run(["git", "-C", str(repo), "branch", "--show-current"])
    branch_name = branch.stdout.strip() if branch.returncode == 0 else ""
    results.append(CheckResult("branch", bool(branch_name), branch_name or "detached HEAD", severity="warning"))

    upstream = run(["git", "-C", str(repo), "rev-parse", "--abbrev-ref", "--symbolic-full-name", "@{u}"])
    results.append(CheckResult(
        "upstream",
        upstream.returncode == 0,
        upstream.stdout.strip() if upstream.returncode == 0 else "no upstream configured",
        severity="warning",
    ))

    return results


def doctor(repo: Path, core: str) -> DoctorReport:
    checks = [check_python(), check_git(), check_repo(repo), check_core(repo, core)]
    checks.extend(check_git_state(repo))
    checks.extend(check_docs(repo))

    hard_failures = [item for item in checks if not item.ok and item.severity == "error"]
    return DoctorReport(
        ok=not hard_failures,
        timestamp=datetime.now().astimezone().isoformat(),
        tool_version=TOOL_VERSION,
        repo=str(repo),
        checks=checks,
    )


def print_doctor(report: DoctorReport) -> None:
    print("=" * 72)
    print(f"Nazm Dad — Doctor v{TOOL_VERSION}")
    print("=" * 72)
    for item in report.checks:
        icon = "✅" if item.ok else ("⚠️" if item.severity == "warning" else "❌")
        print(f"{icon} {item.name}: {item.detail}")
    print("-" * 72)
    print("PASS" if report.ok else "FAIL")


def git_show(repo: Path, ref: str, path: str) -> Tuple[bool, str]:
    result = run(["git", "-C", str(repo), "show", f"{ref}:{path}"])
    return result.returncode == 0, result.stdout if result.returncode == 0 else result.stderr


def source_file(
    repo: Path,
    relative: str,
    source_ref: Optional[str],
    source_dir: Optional[Path],
) -> Tuple[bool, str, str]:
    if source_ref:
        ok, content = git_show(repo, source_ref, relative)
        return ok, content, f"git:{source_ref}:{relative}"

    if source_dir:
        path = source_dir / relative
        if not path.exists():
            return False, "", str(path)
        return True, path.read_text(encoding="utf-8", errors="replace"), str(path)

    return False, "", "no repair source supplied"


def repair_preview(
    repo: Path,
    files: Sequence[str],
    source_ref: Optional[str],
    source_dir: Optional[Path],
) -> bool:
    changed_any = False

    for relative in files:
        current_path = repo / relative
        current = current_path.read_text(encoding="utf-8", errors="replace") if current_path.exists() else ""

        ok, proposed, source = source_file(repo, relative, source_ref, source_dir)
        print("=" * 72)
        print(f"FILE: {relative}")
        print(f"SOURCE: {source}")

        if not ok:
            print("❌ source unavailable")
            changed_any = True
            continue

        if current == proposed:
            print("✅ no change")
            continue

        changed_any = True
        before = current.splitlines(keepends=True)
        after = proposed.splitlines(keepends=True)
        diff = difflib.unified_diff(
            before,
            after,
            fromfile=f"a/{relative}",
            tofile=f"preview/{relative}",
        )
        sys.stdout.writelines(diff)

    return changed_any


def git_commit(repo: Path) -> Optional[str]:
    result = run(["git", "-C", str(repo), "rev-parse", "HEAD"])
    return result.stdout.strip() if result.returncode == 0 else None


def build_manifest(repo: Path, files: Iterable[str]) -> HashManifest:
    records: List[HashRecord] = []
    for relative in files:
        path = repo / relative
        if not path.exists() or not path.is_file():
            continue
        records.append(HashRecord(
            path=relative.replace("\\", "/"),
            sha256=sha256_file(path),
            size=path.stat().st_size,
        ))

    return HashManifest(
        schema=1,
        tool_version=TOOL_VERSION,
        generated_at=datetime.now().astimezone().isoformat(),
        git_commit=git_commit(repo),
        files=records,
    )


def write_manifest(repo: Path, manifest_path: Path, files: Iterable[str]) -> None:
    manifest = build_manifest(repo, files)
    target = manifest_path if manifest_path.is_absolute() else repo / manifest_path
    target.write_text(
        json.dumps(asdict(manifest), ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    print(f"✅ hash manifest written: {rel(target, repo)}")
    print(f"   files: {len(manifest.files)}")


def verify_manifest(repo: Path, manifest_path: Path) -> bool:
    target = manifest_path if manifest_path.is_absolute() else repo / manifest_path
    if not target.exists():
        print(f"❌ manifest not found: {rel(target, repo)}")
        return False

    data = json.loads(target.read_text(encoding="utf-8"))
    records = data.get("files", [])
    ok = True

    for record in records:
        relative = record["path"]
        path = repo / relative
        if not path.exists():
            print(f"❌ {relative}: missing")
            ok = False
            continue

        actual = sha256_file(path)
        expected = record["sha256"]
        if actual != expected:
            print(f"❌ {relative}: hash mismatch")
            print(f"   expected: {expected}")
            print(f"   actual:   {actual}")
            ok = False
        else:
            print(f"✅ {relative}: verified")

    return ok


def run_core(repo: Path, core: str, args: Sequence[str]) -> int:
    path = core_path(repo, core)
    if not path.exists():
        print(f"❌ core script missing: {path}", file=sys.stderr)
        return 2

    proc = subprocess.run([sys.executable, str(path), *args], cwd=str(repo))
    return int(proc.returncode)


def run_strict(repo: Path, core: str) -> Tuple[bool, Dict[str, int]]:
    commands = {
        "validate_docs": ["--validate-docs", "--no-progress"],
        "check_links": ["--check-links", "--no-progress"],
        "health": ["--health", "--no-progress"],
    }

    results: Dict[str, int] = {}
    for name, args in commands.items():
        print("=" * 72)
        print(f"STRICT: {name}")
        print("=" * 72)
        results[name] = run_core(repo, core, args)

    doc_report = doctor(repo, core)
    hard_doctor_ok = doc_report.ok
    results["doctor"] = 0 if hard_doctor_ok else 1

    success = all(code == 0 for code in results.values())
    return success, results


def ci_report(repo: Path, core: str, strict: bool) -> Dict[str, Any]:
    report = doctor(repo, core)
    payload: Dict[str, Any] = {
        "schema": 1,
        "tool": "nazm-dad-project-status",
        "version": TOOL_VERSION,
        "timestamp": datetime.now().astimezone().isoformat(),
        "repo": str(repo),
        "commit": git_commit(repo),
        "doctor": {
            "ok": report.ok,
            "checks": [asdict(item) for item in report.checks],
        },
    }

    if strict:
        success, results = run_strict(repo, core)
        payload["strict"] = {"ok": success, "results": results}

    payload["ok"] = bool(payload["doctor"]["ok"]) and bool(
        payload.get("strict", {"ok": True})["ok"]
    )
    return payload


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="nazm_dad_project_status_v27.py",
        description="Nazm Dad Project Status v2.7 extension layer",
        add_help=True,
    )

    parser.add_argument("--repo", default=".", help="repository path")
    parser.add_argument("--core", default=DEFAULT_CORE, help="v2.6 core script path")

    parser.add_argument("--doctor", action="store_true", help="run environment/repository diagnostics")
    parser.add_argument("--doctor-json", action="store_true", help="print doctor report as JSON")

    parser.add_argument("--repair-preview", action="store_true", help="preview document repair without writing")
    parser.add_argument("--repair-from-ref", help="Git ref used by --repair-preview")
    parser.add_argument("--repair-source-dir", help="source directory used by --repair-preview")
    parser.add_argument(
        "--repair-files",
        nargs="+",
        default=["docs/0.4.md", "docs/changelog.md"],
        help="files to preview",
    )

    parser.add_argument("--strict", action="store_true", help="strict CI-style validation")

    parser.add_argument("--hash-manifest", action="store_true", help="write SHA-256 manifest")
    parser.add_argument("--verify-hashes", action="store_true", help="verify SHA-256 manifest")
    parser.add_argument("--manifest", default=DEFAULT_MANIFEST, help="manifest path")
    parser.add_argument("--hash-files", nargs="+", default=list(DEFAULT_HASH_FILES))

    parser.add_argument("--ci-json", nargs="?", const="-", help="write CI JSON to file or '-' for stdout")
    parser.add_argument("--ci-strict", action="store_true", help="include strict checks in CI JSON")

    parser.add_argument("--version", action="store_true", help="show v2.7 version")
    return parser


def main(argv: Optional[Sequence[str]] = None) -> int:
    raw = list(sys.argv[1:] if argv is None else argv)

    parser = build_parser()
    known, unknown = parser.parse_known_args(raw)

    if known.version:
        print(TOOL_VERSION)
        return 0

    repo = find_repo(Path(known.repo))

    handled = False
    exit_code = 0

    if known.doctor or known.doctor_json:
        handled = True
        report = doctor(repo, known.core)
        if known.doctor_json:
            print(json.dumps(asdict(report), ensure_ascii=False, indent=2))
        else:
            print_doctor(report)
        if not report.ok:
            exit_code = 1

    if known.repair_preview:
        handled = True
        source_dir = Path(known.repair_source_dir).resolve() if known.repair_source_dir else None
        if not known.repair_from_ref and not source_dir:
            print("❌ --repair-preview requires --repair-from-ref or --repair-source-dir", file=sys.stderr)
            return 3
        repair_preview(
            repo,
            known.repair_files,
            known.repair_from_ref,
            source_dir,
        )

    if known.hash_manifest:
        handled = True
        write_manifest(repo, Path(known.manifest), known.hash_files)

    if known.verify_hashes:
        handled = True
        if not verify_manifest(repo, Path(known.manifest)):
            exit_code = 1

    if known.strict:
        handled = True
        success, results = run_strict(repo, known.core)
        print("=" * 72)
        print("STRICT SUMMARY")
        print("=" * 72)
        for name, code in results.items():
            print(f"{'✅' if code == 0 else '❌'} {name}: exit={code}")
        if not success:
            exit_code = 1

    if known.ci_json is not None:
        handled = True
        payload = ci_report(repo, known.core, known.ci_strict)
        text = json.dumps(payload, ensure_ascii=False, indent=2) + "\n"
        if known.ci_json == "-":
            sys.stdout.write(text)
        else:
            target = Path(known.ci_json)
            if not target.is_absolute():
                target = repo / target
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(text, encoding="utf-8")
            print(f"✅ CI JSON written: {rel(target, repo)}")
        if not payload["ok"]:
            exit_code = 1

    if handled:
        if unknown:
            print(
                "⚠️ ignored legacy arguments because a v2.7 action was selected: "
                + " ".join(unknown),
                file=sys.stderr,
            )
        return exit_code

    # No v2.7 action selected: preserve full backwards compatibility by
    # forwarding every argument to the stable v2.6 engine.
    return run_core(repo, known.core, raw)


if __name__ == "__main__":
    raise SystemExit(main())
