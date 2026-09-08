"""Locate skills in every place Claude Code looks.

Cross-skill rules are the whole point of this tool, and they are only correct if
we see the same set of skills Claude Code sees. In particular, a *project* skill
can be silently shadowed by a same-named *personal* skill, so scanning only
``.claude/skills`` would miss the bug entirely.
"""

from __future__ import annotations

import json
import os
import re
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


#: Where Claude Code puts the plugins it has actually downloaded, laid out as
#: ``cache/<marketplace>/<plugin>/<version>/``.
#:
#: Its sibling ``marketplaces/`` is deliberately not scanned. That directory
#: holds full git clones of plugin *catalogues* - every plugin a marketplace
#: offers, installed or not, plus whatever else those repositories happen to
#: contain. A catalogue clone of a project supporting many AI tools carries the
#: same skill under `.cursor/`, `.grok/`, `.gemini/`, `plugin/` and a dozen
#: more. Reading it reported one skill as sixteen colliding duplicates, and
#: reported five plugins the reader had never installed. ``data/`` is per-plugin
#: storage and holds no skills either.
_INSTALLED_ROOT = "cache"

#: Claude Code's own record of which plugins are installed.
_INSTALLED_RECORD = "installed_plugins.json"


def _installed_plugin_names(plugins_root: Path) -> frozenset[str] | None:
    """Plugin names Claude Code records as installed.

    ``None`` means the record could not be read, in which case everything
    downloaded is scanned - going silent because a file moved would be its own
    kind of wrong.

    An empty set is a real answer, not a missing one: `{"version": 2,
    "plugins": {}}` is what Claude Code writes when a marketplace has been
    added but nothing from it installed, and the whole point of this function
    is to stop reporting on those.

    Only the empty shape has been observed directly. Keys are therefore matched
    on their last path-like segment, so `plugin`, `marketplace:plugin` and
    `marketplace/plugin` all resolve the same way.
    """
    try:
        data = json.loads((plugins_root / _INSTALLED_RECORD).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    if not isinstance(data, dict) or not isinstance(data.get("plugins"), dict):
        return None
    return frozenset(re.split(r"[:/]", str(key))[-1].casefold() for key in data["plugins"])


def _plugin_of(relative: Path) -> str:
    """The plugin a path under the cache belongs to.

    The layout is ``<marketplace>/<plugin>/<version>/skills/...``, so the name
    is the second segment. Taking the directory above ``skills/`` instead - the
    obvious reading - yields the *version*, which is how a skill came to be
    reported under the name `4.1.1`.
    """
    parts = relative.parts
    return parts[1] if len(parts) >= 3 else parts[0]


def _scan_plugins(plugins_root: Path) -> list[Skill]:
    """Collect skills from installed plugins, namespaced by their plugin."""
    cache = plugins_root / _INSTALLED_ROOT
    if not cache.is_dir():
        return []

    installed = _installed_plugin_names(plugins_root)
    if installed is not None and not installed:
        return []

    found: list[Skill] = []
    seen: set[Path] = set()

    def wanted(path: Path) -> str | None:
        name = _plugin_of(path.relative_to(cache))
        if installed and name.casefold() not in installed:
            return None
        return name

    for skills_dir in sorted(cache.rglob("skills")):
        if not skills_dir.is_dir():
            continue
        plugin_name = wanted(skills_dir)
        if plugin_name is None:
            continue
        for skill in _scan_skills_root(skills_dir, Source.PLUGIN, plugin=plugin_name):
            if skill.path not in seen:
                seen.add(skill.path)
                found.append(skill)

    # A plugin may also ship a SKILL.md at its root.
    for path in sorted(cache.rglob(SKILL_FILE)):
        if path in seen or not path.is_file():
            continue
        if "skills" in path.parent.parts:
            continue
        plugin_name = wanted(path)
        if plugin_name is None:
            continue
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
