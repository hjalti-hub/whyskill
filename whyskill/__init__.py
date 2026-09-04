"""whyskill - find Claude Code skills that fail silently.

A skill that will never load, never be chosen, or never win against another
skill produces no error message anywhere. This package finds those cases, using
only Claude Code's documented loading behaviour.
"""

from __future__ import annotations

from .discover import discover
from .model import Finding, Severity, Skill, Source
from .report import VERSION as __version__
from .rules import Context, run_all

__all__ = [
    "Context",
    "Finding",
    "Severity",
    "Skill",
    "Source",
    "discover",
    "run_all",
    "check",
    "__version__",
]


def check(
    project=None,
    *,
    include_personal: bool = True,
    include_plugins: bool = True,
    explicit=None,
    target: str = "claude-code",
    overlap_threshold: float = 0.5,
) -> tuple[list[Skill], list[Finding]]:
    """Discover skills and run every rule. Returns ``(skills, findings)``."""
    from pathlib import Path

    root = Path(project) if project is not None else None
    skills = discover(
        project=root,
        include_personal=include_personal,
        include_plugins=include_plugins,
        explicit=[Path(p) for p in explicit] if explicit else None,
    )
    ctx = Context(project=root, target=target, overlap_threshold=overlap_threshold)
    return skills, run_all(skills, ctx)
