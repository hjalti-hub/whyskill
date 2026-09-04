"""A frontmatter reader that mirrors Claude Code's loading behaviour.

whyskill deliberately does not use PyYAML. Two reasons:

1. **Fidelity beats permissiveness.** The question this tool answers is not
   "is this valid YAML?" but "will Claude Code read this?". The most important
   rule - that the opening ``---`` must be the file's *first line*, or the whole
   file is treated as content - is a property of the loader, not of YAML. A
   permissive parser that helpfully skips a leading blank line would hide the
   exact bug we are hunting.

2. **Zero dependencies.** The tool has to run in CI and in a bare container with
   nothing but ``python3``.

The parser covers the subset of YAML that skill frontmatter actually uses:
scalars, quoted strings, block and flow sequences, nested maps, and block
scalars. Anything outside that subset produces a diagnostic instead of a guess -
silently mis-parsing frontmatter is the failure mode this project exists to
prevent.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

BOM = "﻿"

#: Characters YAML treats as a document/frontmatter delimiter.
_CLOSERS = ("---", "...")


@dataclass
class ParseIssue:
    """A problem found while reading frontmatter."""

    code: str
    message: str
    line: int
    fix: str = ""


@dataclass
class ParseResult:
    data: dict[str, Any] = field(default_factory=dict)
    body: str = ""
    #: True when an opening delimiter was found on the first line, i.e. when
    #: Claude Code would read frontmatter at all.
    has_frontmatter: bool = False
    key_lines: dict[str, int] = field(default_factory=dict)
    issues: list[ParseIssue] = field(default_factory=list)
    #: Line number (1-based) of the closing delimiter, when present.
    close_line: int | None = None


def parse(text: str) -> ParseResult:
    """Read frontmatter the way Claude Code does."""
    result = ParseResult()

    if text.startswith(BOM):
        # The bytes EF BB BF sit in front of the delimiter, so the file does not
        # begin with `---`. Editors render this identically to a clean file,
        # which is what makes it worth its own diagnostic.
        result.issues.append(
            ParseIssue(
                code="LOAD002",
                message=(
                    "File starts with a UTF-8 byte order mark, so the opening "
                    "`---` is not the first thing in the file"
                ),
                line=1,
                fix="Re-save the file as UTF-8 without a BOM.",
            )
        )
        text = text[len(BOM) :]

    lines = text.splitlines()
    if not lines:
        return result

    first = lines[0]

    # Claude Code reads frontmatter only when the opening `---` is the file's
    # first line. Trailing whitespace is harmless; anything before it is not.
    if first.rstrip() != "---":
        if first.strip() == "---":
            result.issues.append(
                ParseIssue(
                    code="LOAD001",
                    message=(
                        "The opening `---` is indented, so it is not recognised "
                        "as a frontmatter delimiter"
                    ),
                    line=1,
                    fix="Remove the leading whitespace before `---`.",
                )
            )
        elif first.strip() == "":
            # The single most common version of this bug: a stray blank line.
            nxt = next((i for i, ln in enumerate(lines) if ln.strip() == "---"), None)
            if nxt is not None:
                result.issues.append(
                    ParseIssue(
                        code="LOAD001",
                        message=(
                            f"Blank line(s) before the `---` on line {nxt + 1}; "
                            "frontmatter is only read when `---` is line 1"
                        ),
                        line=1,
                        fix="Delete the blank line(s) so `---` is the first line.",
                    )
                )
        else:
            has_later_delim = any(ln.strip() == "---" for ln in lines[1:])
            if has_later_delim:
                result.issues.append(
                    ParseIssue(
                        code="LOAD001",
                        message=(
                            "Content appears before the `---` delimiter, so the "
                            "whole file is treated as skill content"
                        ),
                        line=1,
                        fix="Move the `---` block to the very top of the file.",
                    )
                )
        result.body = text
        return result

    # Find the closing delimiter.
    close_idx = None
    for i in range(1, len(lines)):
        if lines[i].rstrip() in _CLOSERS:
            close_idx = i
            break

    if close_idx is None:
        result.issues.append(
            ParseIssue(
                code="LOAD003",
                message="Frontmatter opened with `---` but is never closed",
                line=1,
                fix="Add a closing `---` after the last frontmatter field.",
            )
        )
        result.body = text
        return result

    result.has_frontmatter = True
    result.close_line = close_idx + 1
    result.body = "\n".join(lines[close_idx + 1 :]).strip()

    block = lines[1:close_idx]
    data, key_lines, issues = _parse_block(block, line_offset=2)
    result.data = data
    result.key_lines = key_lines
    result.issues.extend(issues)
    return result


def _strip_comment(value: str) -> str:
    """Remove a trailing ``#`` comment, respecting quotes.

    YAML only starts a comment at a ``#`` that follows whitespace (or begins the
    value), which matters for values like ``color: #fff``.
    """
    out: list[str] = []
    quote: str | None = None
    prev_space = True  # start of value counts as preceded by space
    i = 0
    while i < len(value):
        ch = value[i]
        if quote:
            out.append(ch)
            if ch == "\\" and quote == '"' and i + 1 < len(value):
                out.append(value[i + 1])
                i += 2
                continue
            if ch == quote:
                quote = None
            prev_space = False
        else:
            if ch in "\"'":
                quote = ch
                out.append(ch)
                prev_space = False
            elif ch == "#" and prev_space:
                break
            else:
                out.append(ch)
                prev_space = ch in " \t"
        i += 1
    return "".join(out).rstrip()


def _quote_closed(text: str) -> bool:
    """Whether the quote opening ``text`` is closed within it.

    Handles both escape conventions: ``\\"`` inside a double-quoted scalar, and
    a doubled ``''`` inside a single-quoted one.
    """
    if not text or text[0] not in "\"'":
        return True
    quote = text[0]
    i = 1
    while i < len(text):
        char = text[i]
        if quote == '"' and char == "\\":
            i += 2
            continue
        if char == quote:
            if quote == "'" and text[i + 1 : i + 2] == "'":
                i += 2
                continue
            return True
        i += 1
    return False


def _unquote(value: str) -> tuple[str, bool]:
    """Return (value, was_quoted)."""
    if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
        inner = value[1:-1]
        if value[0] == '"':
            inner = (
                inner.replace("\\n", "\n")
                .replace("\\t", "\t")
                .replace('\\"', '"')
                .replace("\\\\", "\\")
            )
        else:
            inner = inner.replace("''", "'")
        return inner, True
    return value, False


def _scalar(value: str) -> Any:
    """Convert an unquoted scalar to a Python value, YAML-style."""
    raw, quoted = _unquote(value)
    if quoted:
        return raw
    low = raw.strip().lower()
    if low in ("true", "yes", "on"):
        return True
    if low in ("false", "no", "off"):
        return False
    if low in ("null", "~", ""):
        return None
    text = raw.strip()
    try:
        if text.lstrip("+-").isdigit():
            return int(text)
        return float(text)
    except ValueError:
        return text


def _split_flow(inner: str) -> list[str]:
    """Split a flow collection body on commas that are not inside quotes."""
    parts: list[str] = []
    buf: list[str] = []
    quote: str | None = None
    depth = 0
    for ch in inner:
        if quote:
            buf.append(ch)
            if ch == quote:
                quote = None
            continue
        if ch in "\"'":
            quote = ch
            buf.append(ch)
        elif ch in "[{":
            depth += 1
            buf.append(ch)
        elif ch in "]}":
            depth -= 1
            buf.append(ch)
        elif ch == "," and depth == 0:
            parts.append("".join(buf).strip())
            buf = []
        else:
            buf.append(ch)
    tail = "".join(buf).strip()
    if tail:
        parts.append(tail)
    return parts


def _parse_flow(value: str) -> Any:
    if value.startswith("[") and value.endswith("]"):
        return [_scalar(p) for p in _split_flow(value[1:-1]) if p != ""]
    if value.startswith("{") and value.endswith("}"):
        out: dict[str, Any] = {}
        for part in _split_flow(value[1:-1]):
            if ":" not in part:
                continue
            k, v = part.split(":", 1)
            out[_unquote(k.strip())[0]] = _scalar(v.strip())
        return out
    return _scalar(value)


def _indent_of(line: str) -> int:
    return len(line) - len(line.lstrip(" "))


def _parse_block(
    lines: list[str], line_offset: int, base_indent: int = 0
) -> tuple[dict[str, Any], dict[str, int], list[ParseIssue]]:
    """Parse an indented block of ``key: value`` pairs."""
    data: dict[str, Any] = {}
    key_lines: dict[str, int] = {}
    issues: list[ParseIssue] = []

    i = 0
    while i < len(lines):
        raw = lines[i]
        lineno = line_offset + i

        if not raw.strip() or raw.lstrip().startswith("#"):
            i += 1
            continue

        if "\t" in raw[: _indent_of(raw) + 1] and raw.lstrip() != raw:
            issues.append(
                ParseIssue(
                    code="LOAD004",
                    message="Tab character used for indentation; YAML forbids tabs",
                    line=lineno,
                    fix="Replace tabs with spaces.",
                )
            )
            i += 1
            continue

        indent = _indent_of(raw)
        if indent < base_indent:
            break

        stripped = _strip_comment(raw.strip())
        if not stripped:
            i += 1
            continue

        if stripped.startswith("- "):
            issues.append(
                ParseIssue(
                    code="LOAD004",
                    message="Unexpected list item where a `key: value` pair was expected",
                    line=lineno,
                    fix="Frontmatter must be a mapping at the top level.",
                )
            )
            i += 1
            continue

        if ":" not in stripped:
            issues.append(
                ParseIssue(
                    code="LOAD004",
                    message=f"Cannot parse frontmatter line: {stripped[:60]!r}",
                    line=lineno,
                    fix="Expected `key: value`.",
                )
            )
            i += 1
            continue

        key_part, _, value_part = stripped.partition(":")
        key = _unquote(key_part.strip())[0]
        value_part = value_part.strip()

        if key in data:
            # YAML keeps the last occurrence, so the earlier value vanishes with
            # no warning from any loader.
            issues.append(
                ParseIssue(
                    code="LOAD005",
                    message=(
                        f"Duplicate key {key!r}; the later value silently "
                        f"replaces the one on line {key_lines.get(key, '?')}"
                    ),
                    line=lineno,
                    fix=f"Remove one of the two {key!r} entries.",
                )
            )

        # Block scalar: `key: |` or `key: >` with optional chomping indicator.
        if value_part and value_part[0] in "|>" and value_part[1:].strip(" +-0123456789") == "":
            folded = value_part[0] == ">"
            collected: list[str] = []
            j = i + 1
            child_indent = None
            while j < len(lines):
                nxt = lines[j]
                if not nxt.strip():
                    collected.append("")
                    j += 1
                    continue
                nxt_indent = _indent_of(nxt)
                if nxt_indent <= indent:
                    break
                if child_indent is None:
                    child_indent = nxt_indent
                collected.append(nxt[child_indent:])
                j += 1
            joined = (
                " ".join(x.strip() for x in collected if x.strip())
                if folded
                else "\n".join(collected)
            )
            data[key] = joined.strip("\n")
            key_lines[key] = lineno
            i = j
            continue

        # A quoted scalar may span several lines. YAML folds each line break
        # into a single space, so `description: "one\n  two"` is one value.
        # Reading only the first line made every continuation look like a
        # malformed `key: value` pair, which reported a working skill as broken.
        if value_part and value_part[0] in "\"'" and not _quote_closed(value_part):
            quote = value_part[0]
            parts = [value_part]
            j = i + 1
            closed = False
            while j < len(lines):
                # Inside a string: no comment stripping, no key detection.
                nxt = lines[j].strip()
                parts.append(nxt)
                if _quote_closed(quote + " ".join(parts)[1:]):
                    closed = True
                    j += 1
                    break
                j += 1

            if closed:
                data[key] = _parse_flow(" ".join(parts))
                key_lines[key] = lineno
                i = j
                continue

            issues.append(
                ParseIssue(
                    code="LOAD004",
                    message=f"Quoted value for {key!r} is never closed",
                    line=lineno,
                    fix="Add the missing closing quote.",
                )
            )
            data[key] = _parse_flow(" ".join(parts))
            key_lines[key] = lineno
            i = j
            continue

        if value_part:
            data[key] = _parse_flow(value_part)
            key_lines[key] = lineno
            i += 1
            continue

        # Empty value: either a block list, a nested map, or genuinely empty.
        j = i + 1
        child: list[str] = []
        while j < len(lines):
            nxt = lines[j]
            if not nxt.strip():
                child.append(nxt)
                j += 1
                continue
            if _indent_of(nxt) <= indent:
                break
            child.append(nxt)
            j += 1

        meaningful = [c for c in child if c.strip() and not c.lstrip().startswith("#")]
        if not meaningful:
            data[key] = None
        elif all(c.lstrip().startswith("- ") or c.strip() == "-" for c in meaningful):
            items: list[Any] = []
            for c in meaningful:
                item = _strip_comment(c.strip()[1:].strip())
                items.append(_parse_flow(item) if item else None)
            data[key] = items
        else:
            nested, nested_lines, nested_issues = _parse_block(
                child, line_offset=lineno + 1, base_indent=_indent_of(meaningful[0])
            )
            data[key] = nested
            issues.extend(nested_issues)
            for nk, nl in nested_lines.items():
                key_lines[f"{key}.{nk}"] = nl

        key_lines[key] = lineno
        i = j

    return data, key_lines, issues
