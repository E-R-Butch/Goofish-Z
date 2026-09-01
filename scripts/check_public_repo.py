#!/usr/bin/env python3
"""Fail closed when private runtime or business data could enter GitHub."""

from __future__ import annotations

import os
import re
import subprocess
import sys
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]

# These roots contain runtime/business data, never public source code.  Keep
# sale-side website and warehouse data out of this generic tool repository even
# if somebody removes or broadens a .gitignore rule later.
FORBIDDEN_ROOTS = (
    "data/",
    "exports/",
    "local/",
    "website/",
    "warehouse/",
    "business-data/",
    "personal-data/",
    "listings/",
)
FORBIDDEN_DIR_NAMES = {"cookies", "tokens", "sessions"}
FORBIDDEN_FILE_NAMES = {
    ".env",
    "device_id.json",
    "limiter.json",
    "circuit.json",
    "goofish-cache.json",
    "manual-items.json",
    "goofish-exclusions.json",
    "goofish-overrides.json",
}
BUSINESS_JSON_NAME = re.compile(
    r"(?:^|[-_])(?:account|catalog|dump|export|inventory|items?|listings?|orders?|products?|seller|users?)(?:[-_].*)?\.json$",
    re.IGNORECASE,
)
SENSITIVE_JSON_MARKERS = ("cookie", "token", "session", "login-state", "login_state")
DATABASE_SUFFIXES = (
    ".db",
    ".db-shm",
    ".db-wal",
    ".sqlite",
    ".sqlite-shm",
    ".sqlite-wal",
    ".sqlite3",
    ".sqlite3-shm",
    ".sqlite3-wal",
)
PRIVATE_ABSOLUTE_PATHS = (
    re.compile("/" + r"Users/[^/\s]+/"),
    re.compile("/" + r"home/[^/\s]+/"),
    re.compile(r"[A-Za-z]:\\" + r"Users\\[^\\\s]+\\"),
)
LONG_TEST_NUMBER = re.compile(r"(?<!\d)(\d{10,15})(?!\d)")
LONG_NUMBER_EXEMPT_PATHS = {
    "src/goofish_z/static/goofish_js_version_2.js",
    "static/goofish_js_version_2.js",
}
MAX_PUBLIC_FILE_SIZE = 2_000_000
ALLOWED_TEXT_SUFFIXES = {
    ".html",
    ".js",
    ".kt",
    ".kts",
    ".md",
    ".pro",
    ".properties",
    ".py",
    ".toml",
    ".xml",
    ".yaml",
    ".yml",
}
ALLOWED_EXTENSIONLESS_FILES = {".gitignore", "LICENSE", "NOTICE"}


def candidate_paths() -> list[str]:
    """Return tracked files plus non-ignored files that a broad add would stage."""
    result = subprocess.run(
        ["git", "ls-files", "--cached", "--others", "--exclude-standard", "-z"],
        cwd=REPO_ROOT,
        check=True,
        capture_output=True,
    )
    return sorted(
        value.decode("utf-8", "surrogateescape")
        for value in result.stdout.split(b"\0")
        if value
    )


def path_violations(relative: str) -> list[str]:
    normalized = relative.replace(os.sep, "/")
    parts = normalized.split("/")
    name = parts[-1].lower()
    violations: list[str] = []

    if normalized.startswith(FORBIDDEN_ROOTS):
        violations.append("forbidden runtime/business-data root")
    if any(part.lower() in FORBIDDEN_DIR_NAMES for part in parts[:-1]):
        violations.append("forbidden credential/session directory")
    if name in FORBIDDEN_FILE_NAMES:
        violations.append("forbidden credential/business-data file")
    if BUSINESS_JSON_NAME.search(name):
        violations.append("forbidden account/catalog/business-data JSON")
    if name.endswith(".json") and any(marker in name for marker in SENSITIVE_JSON_MARKERS):
        violations.append("forbidden credential/session JSON")
    if name.startswith(".env.") and name != ".env.example":
        violations.append("forbidden environment file")
    if name.endswith(DATABASE_SUFFIXES):
        violations.append("forbidden local database")
    suffix = Path(name).suffix.lower()
    if suffix not in ALLOWED_TEXT_SUFFIXES and name not in ALLOWED_EXTENSIONLESS_FILES:
        violations.append("unreviewed binary or opaque file type")
    return violations


def text_violations(relative: str, text: str) -> list[str]:
    violations: list[str] = []
    if any(pattern.search(text) for pattern in PRIVATE_ABSOLUTE_PATHS):
        violations.append("private absolute filesystem path")

    # Long platform/account/listing identifiers are private-data-shaped even in
    # documentation.  The two vendored signing scripts contain harmless numeric
    # constants and are the only reviewed exception.  All-zero values remain
    # available for unmistakably synthetic fixtures.
    if relative not in LONG_NUMBER_EXEMPT_PATHS:
        realistic = [match.group(1) for match in LONG_TEST_NUMBER.finditer(text)]
        if any(set(value) != {"0"} for value in realistic):
            violations.append("realistic long numeric account/listing-shaped ID")
    return violations


def main() -> int:
    failures: list[tuple[str, str]] = []
    paths = candidate_paths()
    for relative in paths:
        for reason in path_violations(relative):
            failures.append((relative, reason))

        path = REPO_ROOT / relative
        if path.is_symlink():
            failures.append((relative, "symlinks are not allowed in the public repository"))
            link_text = os.readlink(path)
            for reason in text_violations(relative, link_text):
                failures.append((relative, f"symlink target contains {reason}"))
            continue
        if not path.is_file():
            continue
        if path.stat().st_size > MAX_PUBLIC_FILE_SIZE:
            failures.append((relative, "file exceeds public source size limit"))
            continue
        try:
            text = path.read_text(encoding="utf-8")
        except (UnicodeDecodeError, OSError):
            continue
        for reason in text_violations(relative, text):
            failures.append((relative, reason))

    if failures:
        print("Public repository privacy gate failed:", file=sys.stderr)
        for relative, reason in sorted(set(failures)):
            print(f"- {relative}: {reason}", file=sys.stderr)
        return 1

    print(f"Public repository privacy gate passed ({len(paths)} candidate files checked).")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
