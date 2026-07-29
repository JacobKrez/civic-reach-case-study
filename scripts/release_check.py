#!/usr/bin/env python3
"""Reviewer-facing release check against a Git-free export of HEAD."""

from __future__ import annotations

import argparse
import subprocess
import sys
import tarfile
import tempfile
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
REQUIRED_TRACKED = (
    "README.md",
    "ARCHITECTURE.md",
    "EVALUATION.md",
    "requirements.txt",
    ".env.example",
    "src/agent.py",
    "redteam/reports/simulator_sanitized_2026-07-26.json",
)


class GitUnavailable(RuntimeError):
    """Git cannot be used against this checkout in this environment.

    Not a defect in the product: git may be absent, or -- the case a
    reviewer actually hit on Windows, 2026-07-29 -- it refuses to operate
    on a directory whose owner differs from the current user:

        fatal: detected dubious ownership in repository at 'C:/Users/...'

    That happens on a perfectly good clone (an elevated shell, or a
    directory owned by BUILTIN\\Administrators). The clean-export check
    needs git to do its job, so when git is unusable the honest outcome is
    "this check could not run here", not "the release is broken".
    """


def _run(command: list[str], cwd: Path, timeout: int = 900) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        command,
        cwd=cwd,
        capture_output=True,
        text=True,
        timeout=timeout,
        env={**__import__("os").environ, "PYTHONDONTWRITEBYTECODE": "1"},
    )


def export_head(destination: Path) -> None:
    archive = destination / "head.tar"
    try:
        proc = _run(["git", "archive", "--format=tar", "-o", str(archive), "HEAD"], REPO)
    except (OSError, subprocess.SubprocessError) as exc:
        raise GitUnavailable(f"could not run git: {exc}") from exc
    if proc.returncode:
        combined = f"{proc.stdout}\n{proc.stderr}"
        if "dubious ownership" in combined or "not a git repository" in combined:
            raise GitUnavailable(combined.strip())
        raise RuntimeError(f"git archive failed:\n{combined}")
    with tarfile.open(archive) as bundle:
        bundle.extractall(destination, filter="data")
    archive.unlink()
    if (destination / ".git").exists():
        raise RuntimeError("clean export unexpectedly contains .git")


def _filesystem_contract() -> None:
    missing = [path for path in REQUIRED_TRACKED if not (REPO / path).is_file()]
    if missing:
        raise RuntimeError(f"required reviewer artifacts are missing: {missing}")


def _tracked_contract() -> None:
    if not (REPO / ".git").exists():
        _filesystem_contract()
        return

    try:
        proc = _run(["git", "ls-tree", "-r", "--name-only", "HEAD"], REPO)
    except (OSError, subprocess.SubprocessError) as exc:  # git not installed
        raise GitUnavailable(f"could not run git: {exc}") from exc
    if proc.returncode:
        # Fall back to the same presence check used when there is no .git
        # at all, so a reviewer whose environment blocks git still gets the
        # artifact guarantee, then report the environment problem.
        _filesystem_contract()
        raise GitUnavailable(proc.stderr.strip() or "git exited non-zero")
    tracked = set(proc.stdout.splitlines())
    missing = [path for path in REQUIRED_TRACKED if path not in tracked]
    if missing:
        raise RuntimeError(f"required reviewer artifacts are not tracked in HEAD: {missing}")


def run_clean_export(*, only: str | None = None, all_checks: bool = False) -> tuple[int, str]:
    _tracked_contract()

    def run_checks(checkout: Path, *, already_exported: bool = False) -> tuple[int, str]:
        commands: list[list[str]]
        if all_checks:
            pytest_command = [sys.executable, "-m", "pytest", "-q", "-p", "no:cacheprovider"]
            if already_exported:
                # Avoid recursively asking an already Git-free tree to export itself.
                pytest_command += ["--ignore=tests/test_clean_export_gate.py"]
            commands = [
                pytest_command,
                [sys.executable, "scripts/gate.py"],
            ]
        else:
            command = [sys.executable, "scripts/gate.py"]
            if only:
                command += ["--only", only]
            commands = [command]

        output: list[str] = []
        for command in commands:
            proc = _run(command, checkout)
            rendered = f"$ {' '.join(command)}\n{proc.stdout}{proc.stderr}"
            output.append(rendered)
            if proc.returncode:
                return proc.returncode, "\n".join(output)
        return 0, "\n".join(output)

    if not (REPO / ".git").exists():
        return run_checks(REPO, already_exported=True)

    with tempfile.TemporaryDirectory(prefix="foodpath-release-") as raw:
        checkout = Path(raw)
        export_head(checkout)
        return run_checks(checkout)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--clean-export", action="store_true")
    parser.add_argument("--only", default=None)
    parser.add_argument("--all", action="store_true", dest="all_checks")
    args = parser.parse_args(argv)
    if not args.clean_export and not args.all_checks:
        parser.error("choose --clean-export or --all")
    code, output = run_clean_export(only=args.only, all_checks=args.all_checks)
    print(output)
    print("RELEASE_CHECK PASS" if code == 0 else "RELEASE_CHECK FAIL")
    return code


if __name__ == "__main__":
    raise SystemExit(main())
