"""Hook handlers, so nobody has to remember to run whyskill.

Hooks are executed by the Claude Code harness, not chosen by the model. That
distinction is the entire reason this module exists: a skill has to be *selected*
to run, and selection is exactly the thing that fails silently. A hook fires
whether or not anyone thought about it.

Two events carry the work:

``PostToolUse`` (matcher ``Edit|Write``)
    Fires the moment a ``SKILL.md`` is written. Exiting 2 puts stderr in front
    of Claude, which closes the loop: a skill written broken is reported broken
    within the same turn, and gets fixed before anyone sees it.

``SessionStart``
    Fires when a session opens. Plain stdout is injected as context, so Claude
    learns about skills that were already broken before this session began.

Three rules govern everything here, because a misbehaving hook is worse than no
hook at all:

1. **Never break the session.** Any unexpected failure exits 0 silently.
2. **Never speak unless something is wrong.** A clean run prints nothing, so it
   costs no context.
3. **Be cheap on the common path.** Most edits are not skill files, so that case
   returns before any heavy import happens.
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path

#: Severity gate for each event. SessionStart reports only what is definitely
#: broken - warnings every time you open a session would be noise, and noise is
#: how a useful signal gets ignored.
DEFAULT_GATES = {
    "SessionStart": "error",
    "PostToolUse": "warning",
}

_RANK = {"error": 3, "warning": 2, "note": 1}


def looks_like_skill_file(path_text: str) -> bool:
    """True for files that define a skill.

    Kept deliberately cheap: this runs after every single Edit and Write in a
    session, and the answer is almost always no.
    """
    if not path_text:
        return False
    path = Path(path_text)
    if path.name == "SKILL.md":
        return True
    # `.claude/commands/*.md` files share the skill namespace.
    return path.suffix == ".md" and "commands" in path.parts and ".claude" in path.parts


def _read_event() -> dict:
    """Parse the hook payload from stdin, tolerating anything malformed."""
    try:
        raw = sys.stdin.read()
    except Exception:
        return {}
    if not raw.strip():
        return {}
    try:
        parsed = json.loads(raw)
    except (ValueError, TypeError):
        return {}
    return parsed if isinstance(parsed, dict) else {}


def _project_root(event: dict) -> Path:
    cwd = event.get("cwd")
    if isinstance(cwd, str) and cwd:
        return Path(cwd)
    env = os.environ.get("CLAUDE_PROJECT_DIR")
    return Path(env) if env else Path.cwd()


def _format(findings, root: Path, *, limit: int = 12) -> str:
    """Render findings as compact plain text for another model to read.

    No colour and no box drawing: the audience is Claude, and every character
    costs context.
    """
    lines: list[str] = []
    for finding in findings[:limit]:
        try:
            location = finding.path.relative_to(root)
        except ValueError:
            location = finding.path
        lines.append(f"  {location}:{finding.line}")
        lines.append(f"    {finding.rule} ({finding.severity.value}): {finding.message}")
        if finding.fix:
            lines.append(f"    fix: {finding.fix}")
    if len(findings) > limit:
        lines.append(f"  ... and {len(findings) - limit} more")
    return "\n".join(lines)


def _analyse(root: Path, *, only_path: Path | None = None):
    """Run every rule, optionally narrowing the result to one file.

    Discovery always covers the whole visible set even when reporting on a
    single file, because the collision rules cannot judge a skill without
    knowing what it is competing against.
    """
    # Imported here rather than at module scope so the "not a skill file" path
    # costs nothing but interpreter startup.
    from .discover import _load, discover
    from .model import Source
    from .rules import Context, run_all

    skills = discover(project=root, include_personal=True, include_plugins=True)

    if only_path is not None:
        target = only_path.resolve()
        # Skills are not always kept in `.claude/skills`. A repository of skills
        # being published, or a plugin under development, lives somewhere else
        # entirely - and that is exactly when this check is most valuable. If the
        # edited file was not discovered, analyse it alongside everything that
        # was, so the cross-skill rules still have their context.
        if not any(s.path.resolve() == target for s in skills):
            skills = [*skills, _load(only_path, Source.PROJECT, only_path.parent.name)]

    findings = run_all(skills, Context(project=root))

    if only_path is not None:
        target = only_path.resolve()
        findings = [f for f in findings if f.path.resolve() == target or str(target) in f.related]
    return skills, findings


def _gate(findings, minimum: str):
    threshold = _RANK.get(minimum, 3)
    return [f for f in findings if f.severity.rank >= threshold]


def handle_post_tool_use(event: dict, minimum: str) -> int:
    """Report on a skill file the moment it is written."""
    tool_input = event.get("tool_input")
    if not isinstance(tool_input, dict):
        return 0

    file_path = tool_input.get("file_path")
    if not isinstance(file_path, str) or not looks_like_skill_file(file_path):
        return 0

    path = Path(file_path)
    if not path.is_file():
        return 0

    root = _project_root(event)
    _, findings = _analyse(root, only_path=path)
    findings = _gate(findings, minimum)
    if not findings:
        return 0

    body = _format(findings, root)
    errors = [f for f in findings if f.severity.value == "error"]

    if errors:
        # Exit 2 routes stderr to Claude, which is the whole point: the skill
        # that was just written does not work, and the turn that wrote it is
        # the cheapest possible moment to say so.
        sys.stderr.write(
            f"whyskill: {path.name} was written with "
            f"{len(errors)} error(s) that will make it fail silently.\n"
            f"{body}\n"
            "Fix these before moving on - none of them produce a runtime error.\n"
        )
        return 2

    # Warnings are worth knowing but not worth interrupting for.
    print(
        json.dumps(
            {
                "hookSpecificOutput": {
                    "hookEventName": "PostToolUse",
                    "additionalContext": (
                        f"whyskill checked {path.name} and found "
                        f"{len(findings)} issue(s) that affect whether the skill "
                        f"gets chosen:\n{body}"
                    ),
                }
            }
        )
    )
    return 0


def handle_session_start(event: dict, minimum: str) -> int:
    """Surface already-broken skills when a session opens."""
    root = _project_root(event)
    skills, findings = _analyse(root)
    findings = _gate(findings, minimum)
    if not findings:
        return 0  # Silence is the correct output for a healthy setup.

    affected = len({f.path for f in findings})
    # SessionStart is one of the events whose plain stdout is given to Claude
    # as context, so no JSON envelope is needed.
    print(
        f"whyskill: {affected} of {len(skills)} installed skill(s) will not "
        f"work as intended. These failures produce no error message anywhere.\n"
        f"{_format(findings, root)}\n"
        "Mention this to the user if it is relevant to what they ask for; "
        "`whyskill --explain` gives the full reasoning."
    )
    return 0


HANDLERS = {
    "PostToolUse": handle_post_tool_use,
    "SessionStart": handle_session_start,
}


def run(event_name: str | None = None, minimum: str | None = None) -> int:
    """Entry point for ``whyskill hook``.

    Any unexpected failure exits 0. A linter that breaks the tool it is meant to
    protect has negative value, and there is no output worth that risk.
    """
    event = _read_event()
    name = event_name or event.get("hook_event_name") or ""

    handler = HANDLERS.get(name)
    if handler is None:
        return 0

    gate = minimum or DEFAULT_GATES.get(name, "error")
    try:
        return handler(event, gate)
    except Exception as exc:  # noqa: BLE001 - deliberately total
        if os.environ.get("WHYSKILL_HOOK_DEBUG"):
            sys.stderr.write(f"whyskill hook error: {exc!r}\n")
        return 0
