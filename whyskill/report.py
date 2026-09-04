"""Rendering findings for humans and for machines."""

from __future__ import annotations

import json
import os
import sys
from collections import defaultdict
from collections.abc import Iterable
from pathlib import Path
from typing import TextIO

from .model import Finding, Severity, Skill

VERSION = "0.1.0"


class Style:
    """ANSI colours, switched off when the output is not a terminal."""

    def __init__(self, stream: TextIO, force: bool | None = None) -> None:
        if force is None:
            enabled = (
                hasattr(stream, "isatty")
                and stream.isatty()
                and os.environ.get("NO_COLOR") is None
                and os.environ.get("TERM") != "dumb"
            )
        else:
            enabled = force
        self.enabled = enabled

    def _wrap(self, code: str, text: str) -> str:
        return f"\033[{code}m{text}\033[0m" if self.enabled else text

    def bold(self, t: str) -> str:
        return self._wrap("1", t)

    def dim(self, t: str) -> str:
        return self._wrap("2", t)

    def red(self, t: str) -> str:
        return self._wrap("31", t)

    def yellow(self, t: str) -> str:
        return self._wrap("33", t)

    def blue(self, t: str) -> str:
        return self._wrap("34", t)

    def cyan(self, t: str) -> str:
        return self._wrap("36", t)

    def severity(self, sev: Severity) -> str:
        return {
            Severity.ERROR: self.red("error"),
            Severity.WARNING: self.yellow("warning"),
            Severity.NOTE: self.blue("note"),
        }[sev]


def _wrap_text(text: str, width: int, indent: str) -> list[str]:
    """Naive word wrap; avoids a textwrap import for one call site's sake."""
    words = text.split()
    lines: list[str] = []
    current = indent
    for word in words:
        candidate = word if current == indent else f"{current} {word}"
        if len(candidate) > width and current != indent:
            lines.append(current)
            current = f"{indent}{word}"
        else:
            current = candidate if current != indent else f"{indent}{word}"
    if current.strip():
        lines.append(current)
    return lines


def _rel(path: Path, root: Path | None) -> str:
    if root is None:
        return str(path)
    try:
        return str(path.relative_to(root))
    except ValueError:
        return str(path)


def render_human(
    findings: list[Finding],
    skills: list[Skill],
    *,
    stream: TextIO | None = None,
    root: Path | None = None,
    verbose: bool = False,
    width: int = 88,
) -> None:
    stream = stream or sys.stdout
    st = Style(stream)

    if not skills:
        stream.write(
            "No skills found. Point whyskill at a directory containing SKILL.md "
            "files, or run it from a project with .claude/skills/.\n"
        )
        return

    if not findings:
        stream.write(
            st.bold(f"✓ {len(skills)} skill{'s' if len(skills) != 1 else ''} checked, ")
            + st.bold("nothing silently broken.\n")
        )
        return

    by_file: dict[Path, list[Finding]] = defaultdict(list)
    for finding in findings:
        by_file[finding.path].append(finding)

    for path in sorted(by_file, key=str):
        stream.write(f"\n{st.bold(_rel(path, root))}\n")
        for finding in by_file[path]:
            location = st.dim(f"{finding.line}:")
            stream.write(
                f"  {location} {st.severity(finding.severity)} "
                f"{st.dim(finding.rule)}  {finding.message}\n"
            )
            if verbose and finding.mechanic:
                for line in _wrap_text(finding.mechanic, width, "      "):
                    stream.write(st.dim(line) + "\n")
            if finding.fix:
                for line in _wrap_text(f"fix: {finding.fix}", width, "      "):
                    stream.write(st.cyan(line) + "\n")

    counts = defaultdict(int)
    for finding in findings:
        counts[finding.severity] += 1

    parts = []
    if counts[Severity.ERROR]:
        parts.append(st.red(f"{counts[Severity.ERROR]} error(s)"))
    if counts[Severity.WARNING]:
        parts.append(st.yellow(f"{counts[Severity.WARNING]} warning(s)"))
    if counts[Severity.NOTE]:
        parts.append(st.blue(f"{counts[Severity.NOTE]} note(s)"))

    stream.write(
        f"\n{st.bold(' · '.join(parts))} across {len(skills)} skill"
        f"{'s' if len(skills) != 1 else ''}\n"
    )
    if not verbose:
        stream.write(st.dim("Run with --explain to see why each one matters.\n"))


def render_json(findings: list[Finding], skills: list[Skill], stream: TextIO | None = None) -> None:
    stream = stream or sys.stdout
    payload = {
        "version": VERSION,
        "summary": {
            "skills": len(skills),
            "errors": sum(1 for f in findings if f.severity is Severity.ERROR),
            "warnings": sum(1 for f in findings if f.severity is Severity.WARNING),
            "notes": sum(1 for f in findings if f.severity is Severity.NOTE),
        },
        "skills": [
            {
                "name": s.invocation_name,
                "path": str(s.path),
                "source": s.source.value,
            }
            for s in skills
        ],
        "findings": [f.to_dict() for f in findings],
    }
    json.dump(payload, stream, indent=2)
    stream.write("\n")


def render_sarif(
    findings: list[Finding],
    skills: list[Skill],
    stream: TextIO | None = None,
    root: Path | None = None,
) -> None:
    """SARIF 2.1.0, so CI can annotate the offending lines in a diff."""
    from .rules.catalog import CATALOG

    stream = stream or sys.stdout
    used = sorted({f.rule for f in findings})
    rules = [
        {
            "id": rule,
            "shortDescription": {"text": CATALOG.get(rule, rule)},
            "helpUri": "https://code.claude.com/docs/en/skills",
        }
        for rule in used
    ]

    results = []
    for finding in findings:
        text = finding.message
        if finding.fix:
            text += f"\n\nFix: {finding.fix}"
        results.append(
            {
                "ruleId": finding.rule,
                "level": finding.severity.sarif_level,
                "message": {"text": text},
                "locations": [
                    {
                        "physicalLocation": {
                            "artifactLocation": {"uri": _rel(finding.path, root)},
                            "region": {"startLine": max(1, finding.line)},
                        }
                    }
                ],
            }
        )

    doc = {
        "$schema": "https://json.schemastore.org/sarif-2.1.0.json",
        "version": "2.1.0",
        "runs": [
            {
                "tool": {
                    "driver": {
                        "name": "whyskill",
                        "version": VERSION,
                        "informationUri": "https://github.com/hjalti-hub/whyskill",
                        "rules": rules,
                    }
                },
                "results": results,
            }
        ],
    }
    json.dump(doc, stream, indent=2)
    stream.write("\n")


def render_skill_list(skills: Iterable[Skill], stream: TextIO | None = None) -> None:
    stream = stream or sys.stdout
    st = Style(stream)
    rows = sorted(skills, key=lambda s: (s.source.value, s.invocation_name))
    if not rows:
        stream.write("No skills found.\n")
        return
    width = max(len(s.invocation_name) for s in rows) + 2
    for skill in rows:
        flags = []
        if skill.frontmatter.get("disable-model-invocation") is True:
            flags.append("user-only")
        if skill.frontmatter.get("paths"):
            flags.append("path-scoped")
        if not skill.has_frontmatter:
            flags.append("no frontmatter")
        suffix = st.dim("  [" + ", ".join(flags) + "]") if flags else ""
        stream.write(
            f"  {skill.invocation_name.ljust(width)}"
            f"{st.dim(skill.source.value.ljust(12))}{suffix}\n"
        )
