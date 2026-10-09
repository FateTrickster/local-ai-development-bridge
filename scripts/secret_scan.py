from __future__ import annotations

import re
import subprocess
from dataclasses import dataclass
from pathlib import Path


ROOT = Path(__file__).resolve().parent.parent
MAX_FILE_BYTES = 2 * 1024 * 1024
_ALLOW_MARKER = "secret-scan: allow"


@dataclass(frozen=True)
class PatternRule:
    name: str
    regex: re.Pattern[str]


RULES = [
    PatternRule(
        "capability-url",
        re.compile(r"https://[a-z0-9-]+\.trycloudflare\.com/[A-Za-z0-9_-]{32,100}/mcp\b", re.IGNORECASE),
    ),
    PatternRule("github-token", re.compile(r"\b(?:gh[pousr]_[A-Za-z0-9]{20,}|github_pat_[A-Za-z0-9_]{30,})\b")),
    PatternRule("openai-style-key", re.compile(r"\bsk-[A-Za-z0-9_-]{24,}\b")),
    PatternRule("aws-access-key", re.compile(r"\bAKIA[0-9A-Z]{16}\b")),
    PatternRule("bearer-token", re.compile(r"(?i)\bBearer\s+[A-Za-z0-9._~-]{24,}\b")),
    PatternRule(
        "secret-assignment",
        re.compile(
            r"(?i)\b(?:BRIDGE_TOKEN|OPENAI_API_KEY|ANTHROPIC_API_KEY|API_KEY|ACCESS_TOKEN|PASSWORD|SECRET)\b\s*[:=]\s*['\"]?([A-Za-z0-9._~+/-]{24,})"
        ),
    ),
]

_PLACEHOLDERS = (
    "[REDACTED]",
    "<capability-token>",
    "your-token",
    "your_token",
    "example-token",
    "secret-token",
    "private-token",
    "should-not-appear",
    "observability-smoke-token",
)


def _candidate_files() -> list[Path]:
    completed = subprocess.run(
        ["git", "ls-files", "-z", "--cached", "--others", "--exclude-standard"],
        cwd=ROOT,
        check=True,
        capture_output=True,
    )
    paths: list[Path] = []
    seen: set[str] = set()
    for raw in completed.stdout.split(b"\x00"):
        if not raw:
            continue
        relative = raw.decode("utf-8", errors="surrogateescape")
        if relative in seen:
            continue
        seen.add(relative)
        paths.append(ROOT / relative)
    return paths


def scan_text(text: str) -> list[tuple[int, str]]:
    findings: list[tuple[int, str]] = []
    for line_number, line in enumerate(text.splitlines(), start=1):
        if _ALLOW_MARKER in line.casefold():
            continue
        if any(marker.casefold() in line.casefold() for marker in _PLACEHOLDERS):
            continue
        for rule in RULES:
            if rule.regex.search(line):
                findings.append((line_number, rule.name))
    return findings


def scan_repository() -> list[tuple[str, int, str]]:
    findings: list[tuple[str, int, str]] = []
    local_token: str | None = None
    token_file = ROOT / ".runtime" / "access-token.txt"
    if token_file.is_file():
        try:
            candidate = token_file.read_text(encoding="utf-8").strip()
            if len(candidate) >= 20:
                local_token = candidate
        except OSError:
            local_token = None

    for path in _candidate_files():
        try:
            if not path.is_file() or path.stat().st_size > MAX_FILE_BYTES:
                continue
            raw = path.read_bytes()
            if b"\x00" in raw:
                continue
            text = raw.decode("utf-8")
        except (OSError, UnicodeDecodeError):
            continue
        relative = path.relative_to(ROOT).as_posix()
        for line_number, rule_name in scan_text(text):
            findings.append((relative, line_number, rule_name))
        if local_token and local_token in text:
            for line_number, line in enumerate(text.splitlines(), start=1):
                if local_token in line and _ALLOW_MARKER not in line.casefold():
                    findings.append((relative, line_number, "local-capability-token"))
    return findings


def main() -> int:
    findings = scan_repository()
    if findings:
        print("Secret scan FAILED. Potential credentials were found:")
        for path, line, rule in findings:
            print(f"  {path}:{line} [{rule}]")
        print("Values are intentionally not printed.")
        return 1
    print("Secret scan OK: no credential patterns found in tracked or untracked non-ignored files.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
