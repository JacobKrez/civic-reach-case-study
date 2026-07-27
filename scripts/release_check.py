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
    proc = _run(["git", "archive", "--format=tar", "-o", str(archive), "HEAD"], REPO)
    if proc.returncode:
        raise RuntimeError(f"git archive failed:\n{proc.stdout}\n{proc.stderr}")
    with tarfile.open(archive) as bundle:
        bundle.extractall(destination, filter="data")
    archive.unlink()
    if (destination / ".git").exists():
        raise RuntimeError("clean export unexpectedly contains .git")


def _tracked_contract() -> None:
    proc = _run(["git", "ls-tree", "-r", "--name-only", "HEAD"], REPO)
    if proc.returncode:
        raise RuntimeError(proc.stderr)
    tracked = set(proc.stdout.splitlines())
    missing = [path for path in REQUIRED_TRACKED if path not in tracked]
    if missing:
        raise RuntimeError(f"required reviewer artifacts are not tracked in HEAD: {missing}")


def run_clean_export(*, only: str | None = None, all_checks: bool = False) -> tuple[int, str]:
    _tracked_contract()
    with tempfile.TemporaryDirectory(prefix="foodpath-release-") as raw:
        checkout = Path(raw)
        export_head(checkout)
        commands: list[list[str]]
        if all_checks:
            commands = [
                [sys.executable, "-m", "pytest", "-q", "-p", "no:cacheprovider"],
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
