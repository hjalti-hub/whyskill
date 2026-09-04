"""Install the hooks into ``settings.json`` without damaging what is there.

People keep real configuration in these files. Every operation here is additive
and reversible: existing hooks are preserved, our own entries are recognisable
so they can be replaced or removed cleanly, and the previous file is backed up
before anything is written.
"""

from __future__ import annotations

import json
import shutil
import sys
from pathlib import Path

#: Substring identifying an entry as ours, for upgrade and uninstall.
MARKER = "whyskill hook"

#: PostToolUse fires on every Edit and Write, so it gets a short timeout; the
#: handler returns immediately for files that are not skills. SessionStart runs
#: once and may walk a large project, so it gets more room.
EVENT_TIMEOUTS = {"PostToolUse": 20, "SessionStart": 30}


def hook_command() -> str:
    """The command to invoke whyskill, preferring the installed entry point.

    Falls back to the running interpreter so a clone works with no install at
    all - the hook must not depend on PATH being set up a particular way.
    """
    if shutil.which("whyskill"):
        return "whyskill hook"
    return f"{sys.executable} -m whyskill hook"


def hook_config(command: str | None = None) -> dict:
    """The hooks block whyskill wants to own."""
    command = command or hook_command()
    return {
        "PostToolUse": {
            "matcher": "Edit|Write",
            "hooks": [
                {
                    "type": "command",
                    "command": command,
                    "timeout": EVENT_TIMEOUTS["PostToolUse"],
                    "statusMessage": "Checking skill…",
                }
            ],
        },
        "SessionStart": {
            "hooks": [
                {
                    "type": "command",
                    "command": command,
                    "timeout": EVENT_TIMEOUTS["SessionStart"],
                }
            ],
        },
    }


def settings_path(*, user: bool, project: Path | None = None, local: bool = False) -> Path:
    if user:
        return Path.home() / ".claude" / "settings.json"
    root = project or Path.cwd()
    name = "settings.local.json" if local else "settings.json"
    return root / ".claude" / name


def _load(path: Path) -> tuple[dict, str | None]:
    """Read settings, returning ``(data, error)`` rather than raising."""
    if not path.exists():
        return {}, None
    try:
        text = path.read_text(encoding="utf-8")
    except OSError as exc:
        return {}, f"cannot read {path}: {exc}"
    if not text.strip():
        return {}, None
    try:
        data = json.loads(text)
    except ValueError as exc:
        # Overwriting a file we failed to understand would destroy real config.
        return {}, f"{path} is not valid JSON ({exc}); refusing to modify it"
    if not isinstance(data, dict):
        return {}, f"{path} does not contain a JSON object; refusing to modify it"
    return data, None


def _is_ours(group: object) -> bool:
    if not isinstance(group, dict):
        return False
    for hook in group.get("hooks", []) or []:
        if isinstance(hook, dict) and MARKER in str(hook.get("command", "")):
            return True
    return False


def _strip(settings: dict) -> int:
    """Remove our entries. Returns how many groups were removed."""
    hooks = settings.get("hooks")
    if not isinstance(hooks, dict):
        return 0
    removed = 0
    for event, groups in list(hooks.items()):
        if not isinstance(groups, list):
            continue
        kept = [g for g in groups if not _is_ours(g)]
        removed += len(groups) - len(kept)
        if kept:
            hooks[event] = kept
        else:
            # Leave no empty scaffolding behind.
            del hooks[event]
    if not hooks:
        settings.pop("hooks", None)
    return removed


def plan(settings: dict, command: str | None = None) -> dict:
    """Return a copy of ``settings`` with our hooks installed."""
    updated = json.loads(json.dumps(settings))  # deep copy via round-trip
    _strip(updated)  # replace rather than duplicate on re-install
    hooks = updated.setdefault("hooks", {})
    for event, group in hook_config(command).items():
        existing = hooks.get(event)
        if not isinstance(existing, list):
            existing = [] if existing is None else [existing]
        hooks[event] = [*existing, group]
    return updated


def _write(path: Path, settings: dict) -> str | None:
    """Write settings, backing up any previous file first."""
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        if path.exists():
            shutil.copy2(path, path.with_suffix(path.suffix + ".whyskill-backup"))
        path.write_text(json.dumps(settings, indent=2) + "\n", encoding="utf-8")
    except OSError as exc:
        return f"cannot write {path}: {exc}"
    return None


def install(path: Path, *, command: str | None = None, dry_run: bool = False) -> tuple[int, str]:
    settings, error = _load(path)
    if error:
        return 2, f"whyskill: {error}"

    updated = plan(settings, command)
    if dry_run:
        return 0, json.dumps(updated, indent=2)

    already = settings == updated
    write_error = _write(path, updated)
    if write_error:
        return 2, f"whyskill: {write_error}"

    verb = "already installed in" if already else "installed into"
    return 0, (
        f"whyskill hooks {verb} {path}\n"
        "  SessionStart  reports skills that are already broken\n"
        "  PostToolUse   checks every SKILL.md the moment it is written\n"
        "\nStart a new session for the hooks to take effect."
    )


def uninstall(path: Path) -> tuple[int, str]:
    settings, error = _load(path)
    if error:
        return 2, f"whyskill: {error}"
    if not settings:
        return 0, f"whyskill: nothing to remove from {path}"

    removed = _strip(settings)
    if not removed:
        return 0, f"whyskill: no whyskill hooks found in {path}"

    write_error = _write(path, settings)
    if write_error:
        return 2, f"whyskill: {write_error}"
    return 0, f"whyskill: removed {removed} hook entr(ies) from {path}"


def status(path: Path) -> tuple[int, str]:
    settings, error = _load(path)
    if error:
        return 2, f"whyskill: {error}"
    hooks = settings.get("hooks") if isinstance(settings.get("hooks"), dict) else {}
    installed = sorted(
        event
        for event, groups in hooks.items()
        if isinstance(groups, list) and any(_is_ours(g) for g in groups)
    )
    if not installed:
        return 1, (
            f"whyskill hooks are not installed in {path}\n"
            "Run `whyskill install` to have Claude check skills without being asked."
        )
    return 0, f"whyskill hooks installed in {path}: {', '.join(installed)}"
