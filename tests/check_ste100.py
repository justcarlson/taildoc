#!/usr/bin/env python3
from __future__ import annotations

import ast
import io
import re
import sys
import tokenize
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
MAX_WORDS = 25
WORD_RE = re.compile(r"[A-Za-z0-9]+(?:[-'][A-Za-z0-9]+)*")
SENTENCE_RE = re.compile(r"(?<=[.!?])\s+")
DISALLOWED_PHRASES = (
    "in order to",
    "please note",
    "it is important to",
    "simply",
    "obviously",
)


def document_paths() -> list[Path]:
    paths = list(ROOT.glob("*.md"))
    paths.extend((ROOT / "docs").rglob("*.md"))
    paths.extend((ROOT / "skills").rglob("*.md"))
    paths.extend((ROOT / ".github").rglob("*.md"))
    return sorted({path for path in paths if path.is_file()})


def source_paths() -> list[Path]:
    paths = list(ROOT.glob("*.py"))
    paths.extend(ROOT.glob("*.sh"))
    paths.extend(ROOT.glob("*.yml"))
    paths.extend(ROOT.glob("*.yaml"))
    paths.extend(ROOT.glob("Dockerfile*"))
    paths.extend((ROOT / "bin").iterdir())
    paths.extend((ROOT / "systemd").iterdir())
    paths.extend((ROOT / "tests").glob("*.py"))
    paths.extend((ROOT / "tests").glob("*.sh"))
    paths.extend((ROOT / ".github").rglob("*.yml"))
    paths.extend((ROOT / ".github").rglob("*.yaml"))
    return sorted({path for path in paths if path.is_file()})


def sentence_errors(text: str, location: str) -> list[str]:
    errors: list[str] = []
    for sentence in SENTENCE_RE.split(text.strip()):
        words = WORD_RE.findall(sentence)
        if len(words) > MAX_WORDS:
            errors.append(f"{location}: sentence has {len(words)} words; limit is {MAX_WORDS}")
        lowered = sentence.casefold()
        for phrase in DISALLOWED_PHRASES:
            if phrase in lowered:
                errors.append(f"{location}: disallowed phrase: {phrase}")
    return errors


def markdown_errors(path: Path) -> list[str]:
    errors: list[str] = []
    lines = path.read_text(encoding="utf-8").splitlines()
    in_fence = False
    in_frontmatter = bool(lines and lines[0] == "---")
    paragraph: list[str] = []
    paragraph_line = 1

    def check_paragraph() -> None:
        nonlocal paragraph
        if paragraph:
            errors.extend(sentence_errors(" ".join(paragraph), f"{path}:{paragraph_line}"))
            paragraph = []

    for number, raw in enumerate(lines, 1):
        stripped = raw.strip()
        if in_frontmatter:
            if number != 1 and stripped == "---":
                in_frontmatter = False
            continue
        if stripped.startswith("```"):
            check_paragraph()
            in_fence = not in_fence
            continue
        if in_fence:
            continue
        if not stripped:
            check_paragraph()
            continue
        if stripped.startswith("|") or re.fullmatch(r"[-:| ]+", stripped):
            check_paragraph()
            continue
        cleaned = re.sub(r"^(?:#{1,6}|>|[-*+] |[0-9]+[.)] )\s*", "", stripped)
        cleaned = re.sub(r"\[([^]]+)]\([^)]+\)", r"\1", cleaned)
        cleaned = cleaned.replace("`", "")
        if not paragraph:
            paragraph_line = number
        paragraph.append(cleaned)
    check_paragraph()
    return errors


def python_comment_errors(path: Path) -> list[str]:
    errors: list[str] = []
    text = path.read_text(encoding="utf-8")
    try:
        tokens = tokenize.generate_tokens(io.StringIO(text).readline)
        for token in tokens:
            if token.type != tokenize.COMMENT or token.string.startswith("#!"):
                continue
            comment = token.string.lstrip("# ")
            comment = re.sub(r"^(?:type:\s*ignore|noqa)(?:\[[^]]+])?(?:\s*-\s*)?", "", comment)
            if comment:
                errors.extend(sentence_errors(comment, f"{path}:{token.start[0]}"))
        tree = ast.parse(text, filename=str(path))
    except (SyntaxError, tokenize.TokenError) as error:
        return [f"{path}: cannot inspect Python text: {error}"]
    for node in ast.walk(tree):
        doc = ast.get_docstring(node, clean=True) if isinstance(
            node,
            (ast.Module, ast.ClassDef, ast.AsyncFunctionDef, ast.FunctionDef),
        ) else None
        if doc:
            errors.extend(sentence_errors(doc, f"{path}:{getattr(node, 'lineno', 1)}"))
    return errors


def shell_comment_errors(path: Path) -> list[str]:
    errors: list[str] = []
    for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        stripped = line.lstrip()
        if stripped.startswith("#") and not stripped.startswith("#!"):
            errors.extend(sentence_errors(stripped.lstrip("# "), f"{path}:{number}"))
    return errors


def main() -> int:
    errors: list[str] = []
    for path in document_paths():
        errors.extend(markdown_errors(path))
    for path in source_paths():
        first_line = path.read_text(encoding="utf-8").partition("\n")[0]
        if path.suffix == ".py" or (first_line.startswith("#!") and "python" in first_line):
            errors.extend(python_comment_errors(path))
        else:
            errors.extend(shell_comment_errors(path))
    if errors:
        print("\n".join(errors), file=sys.stderr)
        return 1
    print("ASD-STE100 text check passed.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
