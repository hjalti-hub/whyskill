"""Core data types shared across whyskill.

Every finding whyskill emits must describe a *silent* failure: something that
changes whether or how a skill loads, without Claude Code printing an error.
That constraint is enforced socially (in review) rather than mechanically, but
it is the reason each Finding carries a ``mechanic`` field: the documented
behaviour the rule is derived from.
"""

from __future__ import annotations

import enum
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


class Severity(enum.Enum):
    """How badly the skill is affected.

    ``ERROR``   the skill cannot do the thing you installed it to do
    ``WARNING`` the skill still loads, but routing to it is unreliable
    ``NOTE``    intentional configurations worth surfacing when you are
                asking "why didn't this fire?"
    """

    ERROR = "error"
    WARNING = "warning"
    NOTE = "note"

    @property
    def rank(self) -> int:
        return {"error": 3, "warning": 2, "note": 1}[self.value]

    # SARIF only defines error/warning/note/none, which happens to match.
    @property
    def sarif_level(self) -> str:
        return self.value


class Source(enum.Enum):
    """Where a skill was found, which determines precedence.

    Claude Code resolves same-named skills by source: enterprise overrides
    personal, and personal overrides project. Plugin skills are namespaced and
    cannot collide with the others.
    """

    ENTERPRISE = "enterprise"
    PERSONAL = "personal"
    PROJECT = "project"
    PLUGIN = "plugin"
    COMMAND = "command"

    @property
    def precedence(self) -> int:
        """Higher wins when two skills resolve to the same invocation name."""
        return {
            "enterprise": 40,
            "personal": 30,
            "project": 20,
            "command": 10,
            "plugin": 0,  # namespaced; never competes
        }[self.value]


@dataclass
class Finding:
    """One diagnosed silent failure."""

    rule: str
    severity: Severity
    message: str
    path: Path
    line: int = 1
    #: Which skill this is about, by invocation name where one exists.
    skill: str | None = None
    #: The documented Claude Code behaviour this rule is derived from.
    mechanic: str = ""
    #: Concrete remediation.
    fix: str = ""
    #: Other skills implicated, for cross-skill rules.
    related: list[str] = field(default_factory=list)

    #: Rule groups, ordered so root causes are reported before their symptoms:
    #: a file that never parsed explains every other complaint about it.
    _GROUP_ORDER = ("LOAD", "LIST", "INVOKE", "COLLIDE", "PORT")

    def sort_key(self) -> tuple:
        group = next(
            (i for i, g in enumerate(self._GROUP_ORDER) if self.rule.startswith(g)),
            len(self._GROUP_ORDER),
        )
        return (-self.severity.rank, str(self.path), group, self.line, self.rule)

    def to_dict(self) -> dict[str, Any]:
        out: dict[str, Any] = {
            "rule": self.rule,
            "severity": self.severity.value,
            "message": self.message,
            "path": str(self.path),
            "line": self.line,
        }
        if self.skill:
            out["skill"] = self.skill
        if self.mechanic:
            out["mechanic"] = self.mechanic
        if self.fix:
            out["fix"] = self.fix
        if self.related:
            out["related"] = list(self.related)
        return out


@dataclass
class Skill:
    """A discovered skill, parsed but not yet judged."""

    path: Path
    source: Source
    #: Directory name (or file stem for a command file). For personal and
    #: project skills this - not frontmatter ``name`` - is what you type.
    dir_name: str
    frontmatter: dict[str, Any]
    body: str
    #: Diagnostics raised while parsing frontmatter, promoted to findings later.
    parse_findings: list[Finding] = field(default_factory=list)
    #: True when an opening ``---`` was found on the file's first line.
    has_frontmatter: bool = False
    #: Line number of each frontmatter key, for precise reporting.
    key_lines: dict[str, int] = field(default_factory=dict)
    #: Plugin this skill belongs to, when source is PLUGIN.
    plugin: str | None = None

    @property
    def invocation_name(self) -> str:
        """The command you actually type, per Claude Code's naming rules.

        For personal and project skills the directory name wins and frontmatter
        ``name`` is only a display label. For plugin skills, frontmatter
        ``name`` replaces the last segment and the plugin prefix stays.
        """
        if self.source is Source.PLUGIN:
            declared = self.frontmatter.get("name")
            last = declared if isinstance(declared, str) and declared else self.dir_name
            if self.plugin and last.startswith(f"{self.plugin}:"):
                return last
            return f"{self.plugin}:{last}" if self.plugin else last
        return self.dir_name

    @property
    def display_name(self) -> str:
        declared = self.frontmatter.get("name")
        if isinstance(declared, str) and declared.strip():
            return declared.strip()
        return self.dir_name

    @property
    def description(self) -> str:
        value = self.frontmatter.get("description")
        return value.strip() if isinstance(value, str) else ""

    @property
    def when_to_use(self) -> str:
        value = self.frontmatter.get("when_to_use")
        return value.strip() if isinstance(value, str) else ""

    def line_of(self, key: str, default: int = 1) -> int:
        return self.key_lines.get(key, default)

    def finding(
        self,
        rule: str,
        severity: Severity,
        message: str,
        *,
        mechanic: str = "",
        fix: str = "",
        line: int | None = None,
        related: list[str] | None = None,
    ) -> Finding:
        return Finding(
            rule=rule,
            severity=severity,
            message=message,
            path=self.path,
            line=line if line is not None else 1,
            skill=self.invocation_name,
            mechanic=mechanic,
            fix=fix,
            related=related or [],
        )
