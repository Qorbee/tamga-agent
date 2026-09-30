#!/usr/bin/env python3
"""Find invisible or direction-changing Unicode that can hide instructions from a human reviewer.

Agents read these characters; people don't see them. The Unicode Tags block can spell out a whole
hidden prompt inside AGENTS.md, a SKILL.md or a code comment, and bidirectional overrides make code
display differently from how it runs ("Trojan Source"). Standard library only.

    python3 find_hidden_unicode.py [PATH ...]    # files or directories; default: current directory

Prints file:line:col, the character, and — for tag characters — the text they spell. Exits 1 when
anything is found, so it can gate CI or a pre-commit hook.
"""

import sys
import unicodedata
from pathlib import Path

SKIP_DIRS = {".git", "node_modules", ".venv", "__pycache__", "dist", ".next", ".astro"}
SKIP_SUFFIXES = {".png", ".jpg", ".jpeg", ".gif", ".ico", ".pdf", ".zip", ".woff", ".woff2", ".lock"}


def classify(cp: int) -> str | None:
    if 0xE0000 <= cp <= 0xE007F:
        return "tag (hidden ASCII)"
    if 0xE0100 <= cp <= 0xE01EF or 0xFE00 <= cp <= 0xFE0E:
        return "variation selector (can encode data)"
    if cp in (0x202A, 0x202B, 0x202C, 0x202D, 0x202E, 0x2066, 0x2067, 0x2068, 0x2069):
        return "bidi override/isolate"
    if cp in (0x200B, 0x200C, 0x200D, 0x200E, 0x200F, 0x2060, 0x2061, 0x2062, 0x2063, 0x2064):
        return "zero-width / invisible"
    if cp in (0x00AD, 0x180E, 0x3164, 0xFFA0, 0x115F, 0x1160):
        return "invisible filler"
    if cp == 0xFEFF:
        return "zero-width no-break space / BOM"
    return None


def scan(path: Path) -> list[str]:
    try:
        text = path.read_text(encoding="utf-8")
    except (UnicodeDecodeError, OSError):
        return []
    findings: list[str] = []
    for lineno, line in enumerate(text.splitlines(), start=1):
        hidden_tags: list[str] = []
        for col, ch in enumerate(line, start=1):
            cp = ord(ch)
            kind = classify(cp)
            if kind is None or (cp == 0xFEFF and lineno == 1 and col == 1):
                continue  # a BOM as the very first character is an encoding marker
            if kind.startswith("tag"):
                if 0xE0020 <= cp <= 0xE007E:
                    hidden_tags.append(chr(cp - 0xE0000))
                continue
            name = unicodedata.name(ch, "UNNAMED")
            findings.append(f"{path}:{lineno}:{col}: U+{cp:04X} {name} — {kind}")
        if hidden_tags:
            findings.append(f"{path}:{lineno}: tag characters spell: {''.join(hidden_tags)!r}")
    return findings


def walk(root: Path) -> list[Path]:
    if root.is_file():
        return [root]
    return [
        p
        for p in root.rglob("*")
        if p.is_file()
        and p.suffix.lower() not in SKIP_SUFFIXES
        and not SKIP_DIRS.intersection(p.relative_to(root).parts)
    ]


def main(argv: list[str]) -> int:
    roots = [Path(a) for a in argv] or [Path(".")]
    findings = [f for root in roots for path in walk(root) for f in scan(path)]
    for finding in findings:
        print(finding)
    if not findings:
        print("no hidden or direction-changing characters found")
    return 1 if findings else 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
