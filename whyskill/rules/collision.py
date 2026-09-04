"""Cross-skill rules: failures that only exist relative to your other skills.

This is the part a per-file linter cannot reach. Whether your skill loads, and
whether Claude routes to it rather than to something else, is a property of the
whole installed set. A skill can be flawless on its own and still never run
because another skill outranks it or reads like it.
"""

from __future__ import annotations

import math
import re
from collections import defaultdict

from ..model import Finding, Severity, Skill, Source
from ..normalize import fold, skeleton, suspicious_characters
from ..spec import BUNDLED_ALIASES, LISTING_CHAR_BUDGET, STOPWORDS
from . import Context, corpus
from .listing import listing_text

_SOURCE_LABEL = {
    Source.ENTERPRISE: "enterprise",
    Source.PERSONAL: "personal (~/.claude/skills)",
    Source.PROJECT: "project (.claude/skills)",
    Source.PLUGIN: "plugin",
    Source.COMMAND: "command file",
}


@corpus
def name_collisions(skills: list[Skill], ctx: Context) -> list[Finding]:
    """COLLIDE001 - two skills whose names Claude Code considers the same.

    Names are compared with case, spacing and invisible characters ignored, and
    compatibility forms such as fullwidth letters and dash variants folded to
    their plain equivalents. Only one of a colliding pair is reachable.
    """
    findings: list[Finding] = []
    groups: dict[str, list[Skill]] = defaultdict(list)

    for skill in skills:
        if skill.source is Source.PLUGIN:
            continue  # namespaced by plugin, cannot collide with the rest
        groups[fold(skill.invocation_name)].append(skill)

    for _, group in sorted(groups.items()):
        if len(group) < 2:
            continue

        ranked = sorted(group, key=lambda s: -s.source.precedence)
        winner = ranked[0]
        # Equal precedence means we cannot say which one wins; still a bug.
        ambiguous = ranked[1].source.precedence == winner.source.precedence

        for loser in ranked[1:]:
            identical = loser.invocation_name == winner.invocation_name
            relation = "have the same name" if identical else "fold to the same name"

            if ambiguous:
                message = (
                    f"{loser.invocation_name!r} and {winner.invocation_name!r} "
                    f"{relation}; only one of them loads"
                )
                fix = f"Rename one of them. The other is at {winner.path}."
            else:
                via = (
                    "of the same name"
                    if identical
                    else f"{winner.invocation_name!r}, which folds to the same name,"
                )
                message = (
                    f"{loser.invocation_name!r} is shadowed by the "
                    f"{_SOURCE_LABEL[winner.source]} skill {via} and never runs"
                )
                fix = (
                    f"Rename this skill, or remove the {_SOURCE_LABEL[winner.source]} "
                    f"one at {winner.path}."
                )

            mechanic = (
                "When skills share a name Claude Code resolves the conflict by "
                "source: enterprise overrides personal, and personal overrides "
                "project. When it compares names it ignores case, spacing and "
                "invisible characters, and treats compatibility forms such as "
                "fullwidth letters and dash variants as their plain equivalents."
            )
            if (
                loser.source is Source.PROJECT
                and winner.source is Source.PERSONAL
                and not ambiguous
            ):
                mechanic += (
                    " Note the direction: a personal skill beats a project skill, "
                    "which is the opposite of how most per-project configuration "
                    "behaves."
                )

            findings.append(
                Finding(
                    rule="COLLIDE001",
                    severity=Severity.ERROR,
                    message=message,
                    path=loser.path,
                    line=1,
                    skill=loser.invocation_name,
                    mechanic=mechanic,
                    fix=fix,
                    related=[str(winner.path)],
                )
            )

    return findings


@corpus
def lookalike_names(skills: list[Skill], ctx: Context) -> list[Finding]:
    """COLLIDE002 - names carrying look-alike or invisible characters.

    A name that differs only by a look-alike letter from another alphabet counts
    as a *different* name. Two skills can therefore look identical in every
    editor and never override one another.
    """
    findings: list[Finding] = []
    by_skeleton: dict[str, list[Skill]] = defaultdict(list)
    for skill in skills:
        by_skeleton[skeleton(skill.invocation_name)].append(skill)

    for skill in skills:
        hits = suspicious_characters(skill.invocation_name)
        if not hits:
            continue

        twins = [
            other
            for other in by_skeleton[skeleton(skill.invocation_name)]
            if other is not skill and fold(other.invocation_name) != fold(skill.invocation_name)
        ]

        detail = "; ".join(f"position {i}: {desc}" for i, _, desc in hits[:4])
        if twins:
            names = ", ".join(str(t.path) for t in twins[:3])
            message = (
                f"{skill.invocation_name!r} contains a look-alike character and is "
                f"a different name from the visually identical skill at {names}"
            )
            severity = Severity.ERROR
        else:
            message = (
                f"{skill.invocation_name!r} contains a character that is not what it appears to be"
            )
            severity = Severity.WARNING

        findings.append(
            Finding(
                rule="COLLIDE002",
                severity=severity,
                message=f"{message} ({detail})",
                path=skill.path,
                line=1,
                skill=skill.invocation_name,
                mechanic=(
                    "Claude Code folds case, spacing and invisible characters when "
                    "comparing names, but a name differing only by a look-alike "
                    "letter from another alphabet counts as a different name. The "
                    "difference is invisible in every editor."
                ),
                fix="Retype the name using plain ASCII characters.",
                related=[str(t.path) for t in twins],
            )
        )
    return findings


@corpus
def command_shadowed_by_skill(skills: list[Skill], ctx: Context) -> list[Finding]:
    """COLLIDE003 - a `.claude/commands/` file with the same name as a skill."""
    findings: list[Finding] = []
    skill_names = {
        fold(s.invocation_name): s
        for s in skills
        if s.source in (Source.PROJECT, Source.PERSONAL, Source.ENTERPRISE)
    }

    for cmd in skills:
        if cmd.source is not Source.COMMAND:
            continue
        match = skill_names.get(fold(cmd.invocation_name))
        if match is None:
            continue
        findings.append(
            Finding(
                rule="COLLIDE003",
                severity=Severity.WARNING,
                message=(
                    f"Command file {cmd.invocation_name!r} is shadowed by the skill "
                    f"at {match.path} and never runs"
                ),
                path=cmd.path,
                line=1,
                skill=cmd.invocation_name,
                mechanic=(
                    "If a skill and a command file share a name, the skill takes precedence."
                ),
                fix="Rename one of them, or delete the command file if the skill replaced it.",
                related=[str(match.path)],
            )
        )
    return findings


@corpus
def bundled_alias_shadow(skills: list[Skill], ctx: Context) -> list[Finding]:
    """COLLIDE004 - overriding a bundled skill whose alias still routes away."""
    findings: list[Finding] = []
    for skill in skills:
        if skill.source is Source.PLUGIN:
            continue
        aliases = BUNDLED_ALIASES.get(fold(skill.invocation_name))
        if not aliases:
            continue
        alias_list = ", ".join(f"/{a}" for a in aliases)
        findings.append(
            Finding(
                rule="COLLIDE004",
                severity=Severity.NOTE,
                message=(
                    f"{skill.invocation_name!r} overrides the bundled skill, but "
                    f"{alias_list} still runs the bundled one"
                ),
                path=skill.path,
                line=1,
                skill=skill.invocation_name,
                mechanic=(
                    "A skill at any local level overrides a bundled skill with the "
                    "same name, but not the bundled skill's aliases - typing an "
                    "alias never runs your skill."
                ),
                fix=f"Use /{skill.invocation_name} rather than {alias_list}, or pick a distinct name.",
            )
        )
    return findings


def _tokens(skill: Skill) -> set[str]:
    text = listing_text(skill)[:LISTING_CHAR_BUDGET].lower()
    words = re.findall(r"[a-z][a-z0-9_-]{2,}", text)
    return {w for w in words if w not in STOPWORDS}


@corpus
def description_overlap(skills: list[Skill], ctx: Context) -> list[Finding]:
    """COLLIDE005 - two skills competing for the same requests.

    Similarity is computed deterministically: an inverse-document-frequency
    weighted Jaccard over the listing vocabulary. Words shared by every skill
    contribute almost nothing, so the score reflects the *distinctive* terms two
    skills are both claiming. No model is involved, so the result is identical
    on every run and safe to gate CI on.
    """
    candidates = [
        s
        for s in skills
        if s.frontmatter.get("disable-model-invocation") is not True and listing_text(s)
    ]
    if len(candidates) < 2:
        return []

    vocab = {s.path: _tokens(s) for s in candidates}
    total = len(candidates)

    df: dict[str, int] = defaultdict(int)
    for tokens in vocab.values():
        for token in tokens:
            df[token] += 1

    # Smoothed IDF, always positive, so a pair sharing only ubiquitous words
    # still scores low rather than dividing by zero.
    idf = {t: math.log((total + 1) / (n + 1)) + 1.0 for t, n in df.items()}

    findings: list[Finding] = []
    for i in range(len(candidates)):
        for j in range(i + 1, len(candidates)):
            a, b = candidates[i], candidates[j]
            ta, tb = vocab[a.path], vocab[b.path]
            if not ta or not tb:
                continue
            shared = ta & tb
            if not shared:
                continue
            union = ta | tb
            score = sum(idf[t] for t in shared) / sum(idf[t] for t in union)
            if score < ctx.overlap_threshold:
                continue

            top = sorted(shared, key=lambda t: -idf[t])[:6]
            findings.append(
                Finding(
                    rule="COLLIDE005",
                    severity=Severity.WARNING,
                    message=(
                        f"{a.invocation_name!r} and {b.invocation_name!r} describe "
                        f"themselves {score:.0%} alike; routing between them is "
                        "unreliable"
                    ),
                    path=a.path,
                    line=a.line_of("description", 2),
                    skill=a.invocation_name,
                    mechanic=(
                        "Claude chooses between skills by comparing the request "
                        "against each description. When two descriptions claim the "
                        "same vocabulary it picks whichever seems closest, which "
                        "may be the wrong one, and nothing reports the near-miss."
                    ),
                    fix=(
                        "Make the descriptions disjoint. Both currently claim: "
                        + ", ".join(top)
                        + ". State what each one is *not* for, or name the other "
                        "skill explicitly to turn the ambiguity into a routing rule."
                    ),
                    related=[str(b.path)],
                )
            )
    return findings
