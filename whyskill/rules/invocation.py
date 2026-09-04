"""Rules about whether anything can invoke the skill at all.

These are the configurations that make a skill unreachable. Each of them is a
perfectly legal setting that Claude Code accepts without complaint, which is why
they are so often the answer to "why didn't my skill fire?".
"""

from __future__ import annotations

import re
from pathlib import Path

from ..model import Finding, Severity, Skill, Source
from ..spec import EFFORT_LEVELS, FORK_ONLY_FIELDS
from . import Context, per_skill

#: Directories never worth walking when checking whether a `paths` glob matches.
_SKIP_DIRS = {
    ".git",
    "node_modules",
    ".venv",
    "venv",
    "__pycache__",
    "dist",
    "build",
    ".next",
    ".tox",
    ".mypy_cache",
    ".pytest_cache",
    "target",
    "vendor",
}


def _as_list(value: object) -> list[str]:
    """Normalise a frontmatter field that accepts a string or a YAML list."""
    if value is None:
        return []
    if isinstance(value, str):
        return [p.strip() for p in value.split(",") if p.strip()]
    if isinstance(value, list):
        return [str(v).strip() for v in value if str(v).strip()]
    return []


def glob_to_regex(pattern: str) -> re.Pattern[str]:
    """Compile a gitignore-style glob, with ``**`` spanning directories."""
    pattern = pattern.strip().lstrip("./")
    out: list[str] = []
    i = 0
    while i < len(pattern):
        ch = pattern[i]
        if pattern.startswith("**/", i):
            out.append("(?:.*/)?")
            i += 3
        elif pattern.startswith("**", i):
            out.append(".*")
            i += 2
        elif ch == "*":
            out.append("[^/]*")
            i += 1
        elif ch == "?":
            out.append("[^/]")
            i += 1
        elif ch == "[":
            end = pattern.find("]", i)
            if end == -1:
                out.append(re.escape(ch))
                i += 1
            else:
                out.append(pattern[i : end + 1])
                i = end + 1
        else:
            out.append(re.escape(ch))
            i += 1
    body = "".join(out)
    # A bare directory pattern should match everything beneath it.
    return re.compile(rf"^{body}$|^{body}/.*$")


def _project_files(root: Path, limit: int = 20000) -> list[str]:
    """Relative POSIX paths of files in the project, cheaply and bounded."""
    files: list[str] = []
    for dirpath, dirnames, filenames in _walk(root):
        dirnames[:] = [d for d in dirnames if d not in _SKIP_DIRS and not d.startswith(".")]
        for fname in filenames:
            rel = (Path(dirpath) / fname).relative_to(root).as_posix()
            files.append(rel)
            if len(files) >= limit:
                return files
    return files


def _walk(root: Path):
    import os

    yield from os.walk(root)


@per_skill
def unreachable(skill: Skill, ctx: Context) -> list[Finding]:
    """INVOKE001 - neither you nor Claude can invoke this skill."""
    model_blocked = skill.frontmatter.get("disable-model-invocation") is True
    user_blocked = skill.frontmatter.get("user-invocable") is False
    if not (model_blocked and user_blocked):
        return []
    return [
        skill.finding(
            "INVOKE001",
            Severity.ERROR,
            "`disable-model-invocation: true` and `user-invocable: false` leave no way to invoke this skill",
            mechanic=(
                "`disable-model-invocation` stops Claude loading the skill on its "
                "own; `user-invocable: false` stops you typing its name. With both "
                "set the skill loads into the listing and can never run."
            ),
            fix="Remove one of the two fields.",
            line=skill.line_of("disable-model-invocation", 2),
        )
    ]


@per_skill
def model_invocation_disabled(skill: Skill, ctx: Context) -> list[Finding]:
    """INVOKE002 - deliberate, but it is the answer to 'why didn't it fire?'."""
    if skill.frontmatter.get("disable-model-invocation") is not True:
        return []
    if skill.frontmatter.get("user-invocable") is False:
        return []  # already reported as INVOKE001
    return [
        skill.finding(
            "INVOKE002",
            Severity.NOTE,
            "Claude can never load this skill on its own (`disable-model-invocation: true`)",
            mechanic=(
                "The skill runs only when you type its name. This is usually "
                "intentional for skills with side effects, and is listed here "
                "because it is indistinguishable from a broken skill when you are "
                "waiting for one to activate."
            ),
            fix="Remove `disable-model-invocation` if you expect Claude to trigger it.",
            line=skill.line_of("disable-model-invocation", 2),
        )
    ]


@per_skill
def paths_match_nothing(skill: Skill, ctx: Context) -> list[Finding]:
    """INVOKE003 - a `paths` filter that no file in the project satisfies."""
    patterns = _as_list(skill.frontmatter.get("paths"))
    if not patterns or ctx.project is None:
        return []
    if skill.source is Source.PERSONAL:
        # A personal skill is evaluated against whatever project is open, so a
        # non-match here says nothing about other repositories.
        return []

    root = ctx.project
    if not root.is_dir():
        return []

    files = _project_files(root)
    if not files:
        return []

    unmatched = [p for p in patterns if not any(glob_to_regex(p).match(f) for f in files)]
    if not unmatched:
        return []

    all_dead = len(unmatched) == len(patterns)
    return [
        skill.finding(
            "INVOKE003",
            Severity.ERROR if all_dead else Severity.WARNING,
            (
                "`paths` pattern matches no file in this project: "
                + ", ".join(repr(p) for p in unmatched)
            ),
            mechanic=(
                "When `paths` is set, Claude loads the skill automatically only "
                "while working with files matching those globs. A pattern that "
                "matches nothing here means the skill can never auto-load in this "
                "project, and nothing reports the mismatch."
            ),
            fix=(
                "Correct the glob, or remove `paths` if the skill is not meant to be file-scoped."
            ),
            line=skill.line_of("paths", 2),
        )
    ]


@per_skill
def fork_only_fields(skill: Skill, ctx: Context) -> list[Finding]:
    """INVOKE004 - `agent`/`background` set without `context: fork`."""
    if skill.frontmatter.get("context") == "fork":
        return []
    stray = sorted(f for f in FORK_ONLY_FIELDS if f in skill.frontmatter)
    if not stray:
        return []
    return [
        skill.finding(
            "INVOKE004",
            Severity.WARNING,
            f"{', '.join('`' + s + '`' for s in stray)} has no effect without `context: fork`",
            mechanic=(
                "`agent` selects the subagent type and `background` controls "
                "whether the turn waits for it. Both only apply when the skill runs "
                "in a forked subagent context."
            ),
            fix="Add `context: fork`, or remove the field.",
            line=skill.line_of(stray[0], 2),
        )
    ]


@per_skill
def invalid_effort(skill: Skill, ctx: Context) -> list[Finding]:
    """INVOKE005 - an effort level Claude Code does not define."""
    value = skill.frontmatter.get("effort")
    if value is None or not isinstance(value, str):
        return []
    if value.strip().lower() in EFFORT_LEVELS:
        return []
    return [
        skill.finding(
            "INVOKE005",
            Severity.WARNING,
            f"`effort: {value}` is not one of {', '.join(sorted(EFFORT_LEVELS))}",
            mechanic="An unrecognised effort level leaves the session effort unchanged.",
            fix="Use one of the documented levels.",
            line=skill.line_of("effort", 2),
        )
    ]
