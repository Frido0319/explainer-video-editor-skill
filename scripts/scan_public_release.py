from __future__ import annotations

import argparse
import re
import subprocess
import sys
from pathlib import Path
from typing import Iterable, Sequence


PLACEHOLDERS = {
    "YOUR_API_KEY",
    "YOUR_ACCESS_TOKEN",
    "YOUR_AWS_ACCESS_KEY_ID",
    "YOUR_AWS_SECRET_ACCESS_KEY",
    "YOUR_AZURE_CLIENT_SECRET",
    "YOUR_CLIENT_SECRET",
    "YOUR_COOKIE_VALUE",
    "YOUR_GCP_API_KEY",
    "YOUR_GITHUB_TOKEN",
    "YOUR_GITLAB_TOKEN",
    "YOUR_OPENAI_API_KEY",
    "YOUR_PASSWORD",
    "YOUR_PRIVATE_KEY_HERE",
    "YOUR_SECRET",
    "YOUR_SECRET_HERE",
    "YOUR_SESSION_COOKIE",
    "YOUR_SLACK_TOKEN",
    "YOUR_STRIPE_SECRET_KEY",
    "YOUR_TOKEN",
}

ASSIGNMENT_NAMES = (
    "api_key",
    "apikey",
    "token",
    "access_token",
    "refresh_token",
    "secret",
    "client_secret",
    "password",
)

SECRET_PATTERNS: tuple[tuple[str, re.Pattern[str]], ...] = (
    ("github_token", re.compile(r"\bgh[pousr]_[A-Za-z0-9]{36,255}\b")),
    ("openai_key", re.compile(r"\bsk-(?:proj|live|test)-[A-Za-z0-9_-]{20,}\b")),
    ("gitlab_token", re.compile(r"\bglpat-[A-Za-z0-9\-_]{20,}\b")),
    ("stripe_secret_key", re.compile(r"\bsk_(?:live|test)_[A-Za-z0-9]{16,}\b")),
    ("slack_token", re.compile(r"\bxox[baprs]-[A-Za-z0-9-]{10,}\b")),
    ("aws_access_key", re.compile(r"\b(?:AKIA|ASIA)[A-Z0-9]{16}\b")),
    ("gcp_api_key", re.compile(r"\bAIza[0-9A-Za-z\-_]{35}\b")),
    (
        "aws_secret_key",
        re.compile(r"(?i)\baws[_-]?secret[_-]?access[_-]?key\b\s*[:=]\s*['\"]?([A-Za-z0-9/+=]{40})['\"]?"),
    ),
    (
        "azure_client_secret",
        re.compile(r"(?i)\bazure[_-]?client[_-]?secret\b\s*[:=]\s*['\"]?([A-Za-z0-9_\-+/=]{20,})['\"]?"),
    ),
    (
        "generic_secret_assignment",
        re.compile(
            r"(?i)\b(?:api[_-]?key|apikey|token|access[_-]?token|refresh[_-]?token|secret|client[_-]?secret|password)\b"
            r"\s*[:=]\s*['\"]?([A-Za-z0-9._/\-+=]{12,})['\"]?"
        ),
    ),
    ("cookie_header", re.compile(r"(?i)\bcookie\b\s*:\s*[^\n\r]{8,}")),
    ("x_api_key_header", re.compile(r"(?i)\bx-api-key\b\s*:\s*[A-Za-z0-9._/\-+=]{12,}")),
    ("authorization_header", re.compile(r"(?i)\bauthorization\b\s*:\s*(?:bearer|basic)\s+[A-Za-z0-9._/\-+=]{8,}")),
    ("pem_private_key", re.compile(r"-----BEGIN (?:RSA |EC |DSA |OPENSSH |PGP |ENCRYPTED )?PRIVATE KEY-----")),
    ("personal_home_path", re.compile(r"(?<![A-Za-z0-9._-])/(?:home|Users)/[^\s\"'`<>]+")),
)


def run_git(args: Sequence[str], cwd: Path) -> str:
    result = subprocess.run(
        ["git", *args],
        cwd=cwd,
        check=True,
        capture_output=True,
        text=True,
    )
    return result.stdout


def normalize_token(value: str) -> str:
    return value.strip().strip("\"'").strip()


def is_explicit_placeholder(value: str) -> bool:
    return normalize_token(value) in PLACEHOLDERS


def is_ignored_path(path: str | Path, repo_root: Path | None = None) -> bool:
    cwd = repo_root or Path.cwd()
    result = subprocess.run(
        ["git", "check-ignore", str(path)],
        cwd=cwd,
        capture_output=True,
        text=True,
    )
    return result.returncode == 0


def scan_text(text: str) -> str | None:
    for name, pattern in SECRET_PATTERNS:
        for match in pattern.finditer(text):
            token = match.group(1) if match.lastindex else match.group(0)
            if is_explicit_placeholder(token):
                continue
            return name
    return None


def iter_public_candidates(repo_root: Path) -> Iterable[Path]:
    output = run_git(["ls-files", "--cached", "--others", "--exclude-standard"], repo_root)
    for line in output.splitlines():
        if not line:
            continue
        path = repo_root / line
        if path.is_file():
            yield path


def scan_paths(paths: Iterable[Path], repo_root: Path) -> list[str]:
    findings: list[str] = []
    for path in paths:
        text = path.read_text(encoding="utf-8", errors="ignore")
        finding = scan_text(text)
        if finding:
            findings.append(f"{path.relative_to(repo_root)}: {finding}")
    return findings


def scan_staged_diff(repo_root: Path) -> list[str]:
    diff = run_git(["diff", "--cached", "--no-ext-diff", "--text", "-U0"], repo_root)
    finding = scan_text(diff)
    return [f"STAGED_DIFF: {finding}"] if finding else []


def scan_history(repo_root: Path) -> list[str]:
    history = run_git(["log", "-p", "HEAD", "--no-ext-diff", "--text"], repo_root)
    finding = scan_text(history)
    return [f"GIT_HISTORY: {finding}"] if finding else []


def collect_findings(repo_root: Path) -> list[str]:
    findings: list[str] = []
    findings.extend(scan_paths(iter_public_candidates(repo_root), repo_root))
    findings.extend(scan_staged_diff(repo_root))
    findings.extend(scan_history(repo_root))
    return findings


def scan_repository(repo_root: Path | str) -> tuple[int, list[str]]:
    root = Path(repo_root).resolve()
    findings = collect_findings(root)
    return (1, findings) if findings else (0, [])


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Scan public release candidates, staged diff, and HEAD-reachable git history for secrets or private paths."
    )
    parser.add_argument("repo_root", nargs="?", default=".")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    status, findings = scan_repository(args.repo_root)
    if status:
        print("PUBLIC_RELEASE_SCAN_FAILED")
        for finding in findings:
            print(finding)
        return status

    print("PUBLIC_RELEASE_SCAN_OK")
    return 0


if __name__ == "__main__":
    sys.exit(main())
