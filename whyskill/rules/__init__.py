"""Rule registry.

Rules come in two shapes:

* **per-skill** - judged from one ``SKILL.md`` in isolation.
* **corpus** - judged from the whole set of visible skills at once. These are
  the rules a per-file linter structurally cannot implement, because whether
  your skill loads depends on what *else* is installed.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable

from ..model import Finding, Skill


@dataclass
class Context:
    """Everything a rule may need beyond the skills themselves."""

    project: Path | None = None
    #: ``"claude-code"`` or ``"spec"`` (claude.ai uploads and the Skills API).
    target: str = "claude-code"
    #: Threshold for description-overlap reporting, 0..1.
    overlap_threshold: float = 0.5
    #: Rule ids to suppress.
    disabled: frozenset[str] = field(default_factory=frozenset)

    def enabled(self, rule: str) -> bool:
        return rule not in self.disabled


PerSkillRule = Callable[[Skill, Context], list[Finding]]
CorpusRule = Callable[[list[Skill], Context], list[Finding]]

_PER_SKILL: list[PerSkillRule] = []
_CORPUS: list[CorpusRule] = []


def per_skill(fn: PerSkillRule) -> PerSkillRule:
    _PER_SKILL.append(fn)
    return fn


def corpus(fn: CorpusRule) -> CorpusRule:
    _CORPUS.append(fn)
    return fn


def run_all(skills: list[Skill], ctx: Context) -> list[Finding]:
    """Run every registered rule and return findings sorted by severity."""
    # Import for side effects: each module registers its rules on import.
    from . import collision, invocation, listing, portability  # noqa: F401

    findings: list[Finding] = []

    for skill in skills:
        findings.extend(f for f in skill.parse_findings if ctx.enabled(f.rule))

    for skill in skills:
        for rule in _PER_SKILL:
            findings.extend(f for f in rule(skill, ctx) if ctx.enabled(f.rule))

    for rule in _CORPUS:
        findings.extend(f for f in rule(skills, ctx) if ctx.enabled(f.rule))

    findings.sort(key=lambda f: f.sort_key())
    return findings


def rule_catalog() -> dict[str, str]:
    """Every rule id with a one-line summary, for ``whyskill rules``."""
    from .catalog import CATALOG

    return CATALOG
