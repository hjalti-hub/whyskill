"""Locate skills in every place Claude Code looks.

Cross-skill rules are the whole point of this tool, and they are only correct if
we see the same set of skills Claude Code sees. In particular, a *project* skill
can be silently shadowed by a same-named *personal* skill, so scanning only
``.claude/skills`` would miss the bug entirely.
"""

from __future__ import annotations

import os
from pathlib import Path

from .frontmatter import parse
from .model import Finding, Severity, Skill, Source

SKILL_FILE = "SKILL.md"


def _claude_home() -> Path:
    """The personal Claude Code directory, honouring ``CLAUDE_CONFIG_DIR``."""
    override = os.environ.get("CLAUDE_CONFIG_DIR")
    if override:
        return Path(override).expanduser()
    return Path.home() / ".claude"


def _load(path: Path, source: Source, dir_name: str, plugin: str | None = None) -> Skill:
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
    except OSError as exc:  # unreadable file is itself a silent failure
        skill = Skill(
            path=path,
            source=source,
            dir_name=dir_name,
            frontmatter={},
            body="",
            plugin=plugin,
        )
        skill.parse_findings.append(
            Finding(
                rule="LOAD000",
                severity=Severity.ERROR,
                message=f"Cannot read skill file: {exc.strerror or exc}",
                path=path,
                skill=dir_name,
                mechanic="A skill file Claude Code cannot read is a skill that does not exist.",
                fix="Check file permissions.",
            )
        )
        return skill

    result = parse(text)
    skill = Skill(
        path=path,
        source=source,
        dir_name=dir_name,
        frontmatter=result.data if isinstance(result.data, dict) else {},
        body=result.body,
        has_frontmatter=result.has_frontmatter,
        key_lines=result.key_lines,
        plugin=plugin,
    )

    severity_for = {
        "LOAD000": Severity.ERROR,
        "LOAD001": Severity.ERROR,
        "LOAD002": Severity.ERROR,
        "LOAD003": Severity.ERROR,
        "LOAD004": Severity.WARNING,
        "LOAD005": Severity.WARNING,
    }
    mechanic_for = {
        "LOAD001": (
            "Claude Code reads frontmatter only when the opening `---` is the "
            "file's first line. Otherwise it treats the whole file, `---` markers "
            "included, as skill content - so the skill has no description to "
            "match on and Claude never loads it automatically."
        ),
        "LOAD002": (
            "A byte order mark sits in front of the `---`, so the delimiter is "
            "not the first thing in the file and frontmatter is not read."
        ),
        "LOAD003": (
            "Frontmatter that never closes is not parsed, leaving the skill with no description."
        ),
        "LOAD004": "Fields on unparseable lines are dropped without any error.",
        "LOAD005": "YAML keeps the last duplicate key; the earlier value is discarded.",
    }

    for issue in result.issues:
        skill.parse_findings.append(
            Finding(
                rule=issue.code,
                severity=severity_for.get(issue.code, Severity.WARNING),
                message=issue.message,
                path=path,
                line=issue.line,
                skill=dir_name,
                mechanic=mechanic_for.get(issue.code, ""),
                fix=issue.fix,
            )
        )
    return skill


def _scan_skills_root(root: Path, source: Source, plugin: str | None = None) -> list[Skill]:
    """Collect every ``SKILL.md`` beneath a skills root."""
    if not root.is_dir():
        return []
    found: list[Skill] = []
    for path in sorted(root.rglob(SKILL_FILE)):
        if not path.is_file():
            continue
        # The directory holding SKILL.md supplies the invocation name.
        found.append(_load(path, source, path.parent.name, plugin=plugin))
    return found


def _scan_commands(root: Path, source: Source) -> list[Skill]:
    """Collect ``.claude/commands/*.md`` files.

    These share the skill namespace: if a skill and a command share a name, the
    skill wins and the command silently stops working.
    """
    if not root.is_dir():
        return []
    found: list[Skill] = []
    for path in sorted(root.rglob("*.md")):
        if not path.is_file():
            continue
        found.append(_load(path, source, path.stem))
    return found


def _scan_plugins(plugins_root: Path) -> list[Skill]:
    """Collect plugin skills, namespaced by their plugin.

    Plugin layouts vary by marketplace, so rather than assume a fixed depth we
    look for any ``skills/`` directory and treat its parent as the plugin.
    """
    if not plugins_root.is_dir():
        return []
    found: list[Skill] = []
    seen: set[Path] = set()

    for skills_dir in sorted(plugins_root.rglob("skills")):
        if not skills_dir.is_dir():
            continue
        plugin_name = skills_dir.parent.name
        for skill in _scan_skills_root(skills_dir, Source.PLUGIN, plugin=plugin_name):
            if skill.path not in seen:
                seen.add(skill.path)
                found.append(skill)

    # A plugin may also ship a SKILL.md at its root.
    for path in sorted(plugins_root.rglob(SKILL_FILE)):
        if path in seen or not path.is_file():
            continue
        if "skills" in path.parent.parts:
            continue
        plugin_name = path.parent.name
        seen.add(path)
        found.append(_load(path, Source.PLUGIN, plugin_name, plugin=plugin_name))

    return found


def discover(
    project: Path | None = None,
    *,
    include_personal: bool = True,
    include_plugins: bool = True,
    explicit: list[Path] | None = None,
) -> list[Skill]:
    """Find all skills that would be visible in ``project``.

    ``explicit`` paths are scanned as project-level skills, which is what you
    want when linting a repository of skills you intend to publish.
    """
    skills: list[Skill] = []

    if explicit:
        for target in explicit:
            target = target.expanduser()
            if target.is_file():
                skills.append(_load(target, Source.PROJECT, target.parent.name))
            elif target.is_dir():
                direct = target / SKILL_FILE
                if direct.is_file():
                    skills.append(_load(direct, Source.PROJECT, target.name))
                else:
                    skills.extend(_scan_skills_root(target, Source.PROJECT))
        return skills

    if project is not None:
        project = project.expanduser()
        skills.extend(_scan_skills_root(project / ".claude" / "skills", Source.PROJECT))
        skills.extend(_scan_commands(project / ".claude" / "commands", Source.COMMAND))

    if include_personal:
        home = _claude_home()
        skills.extend(_scan_skills_root(home / "skills", Source.PERSONAL))
        skills.extend(_scan_commands(home / "commands", Source.COMMAND))
        if include_plugins:
            skills.extend(_scan_plugins(home / "plugins"))

    return skills
