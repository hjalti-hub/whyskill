"""Rules about the skill listing - the text Claude actually routes on.

Claude picks a skill from a listing built out of each skill's ``description``
and ``when_to_use``. That listing is the *only* thing it sees before deciding;
the body does not load until after the decision is made. So anything that
damages the listing text damages routing, and none of it produces an error.
"""

from __future__ import annotations

import re

from ..model import Finding, Severity, Skill
from ..spec import LISTING_CHAR_BUDGET, states_a_trigger
from . import Context, per_skill

DOCS = "https://code.claude.com/docs/en/skills"


def listing_text(skill: Skill) -> str:
    """Reproduce the text Claude Code puts in the skill listing.

    ``when_to_use`` is appended to ``description`` and counts toward the same
    character cap.
    """
    parts = [p for p in (skill.description, skill.when_to_use) if p]
    return " ".join(parts)


def _first_paragraph(body: str) -> str:
    """The markdown paragraph Claude falls back on when ``description`` is absent."""
    for chunk in re.split(r"\n\s*\n", body.strip()):
        text = chunk.strip()
        if not text:
            continue
        # A heading is not a description, and neither is a fenced block.
        if text.startswith("#") or text.startswith("```"):
            continue
        return text
    return ""


@per_skill
def no_routing_text(skill: Skill, ctx: Context) -> list[Finding]:
    """LIST001 - nothing for Claude to match the user's request against."""
    if not skill.has_frontmatter:
        # Already reported as a LOAD failure; don't pile on.
        return []
    if skill.description:
        return []

    fallback = _first_paragraph(skill.body)
    if fallback:
        return [
            skill.finding(
                "LIST002",
                Severity.WARNING,
                "No `description`; Claude will route on the first body paragraph instead",
                mechanic=(
                    "When `description` is omitted, Claude Code uses the first "
                    "paragraph of the markdown content. That paragraph was written "
                    "to be read after the skill loads, not to be matched against a "
                    "request, so routing quality is accidental."
                ),
                fix=(
                    "Add a `description` that says what the skill does and when to "
                    "use it. Currently falling back to: "
                    f"{fallback[:80]!r}"
                ),
                line=skill.line_of("name", 2),
            )
        ]

    return [
        skill.finding(
            "LIST001",
            Severity.ERROR,
            "Skill has no `description` and no body paragraph to fall back on",
            mechanic=(
                "Claude decides which skill to use from the description in the "
                "skill listing. With no description and no first paragraph there "
                "is nothing to match against, so the skill can only ever be "
                "invoked by typing its name."
            ),
            fix="Add a `description` field saying what the skill does and when to use it.",
            line=1,
        )
    ]


@per_skill
def listing_truncated(skill: Skill, ctx: Context) -> list[Finding]:
    """LIST003/LIST004 - routing text runs past the 1,536-character cap."""
    text = listing_text(skill)
    if len(text) <= LISTING_CHAR_BUDGET:
        return []

    overflow = len(text) - LISTING_CHAR_BUDGET
    kept = text[:LISTING_CHAR_BUDGET]
    dropped = text[LISTING_CHAR_BUDGET:]

    line = skill.line_of("when_to_use") or skill.line_of("description", 2)

    # Losing trailing prose is bad. Losing the trigger phrases is fatal, and it
    # is the common case, because people put examples last.
    if states_a_trigger(dropped) and not states_a_trigger(kept):
        return [
            skill.finding(
                "LIST004",
                Severity.ERROR,
                (
                    f"Listing text is {len(text)} characters, {overflow} over the "
                    f"{LISTING_CHAR_BUDGET}-character cap - and every trigger phrase "
                    "is in the truncated part"
                ),
                mechanic=(
                    "The combined `description` and `when_to_use` text is truncated "
                    f"at {LISTING_CHAR_BUDGET} characters in the skill listing. The "
                    "cut happens silently, so the phrases you wrote to make the "
                    "skill activate never reach the model."
                ),
                fix=(
                    "Move the trigger phrases to the front of `description`. "
                    f"Dropped text begins: {dropped[:80]!r}"
                ),
                line=line,
            )
        ]

    return [
        skill.finding(
            "LIST003",
            Severity.WARNING,
            (
                f"Listing text is {len(text)} characters, {overflow} over the "
                f"{LISTING_CHAR_BUDGET}-character cap"
            ),
            mechanic=(
                "The combined `description` and `when_to_use` text is truncated at "
                f"{LISTING_CHAR_BUDGET} characters in the skill listing. Text past "
                "the cap never reaches the model when it chooses a skill."
            ),
            fix=(
                "Shorten the description, putting the key use case first. "
                f"Dropped text begins: {dropped[:80]!r}"
            ),
            line=line,
        )
    ]


@per_skill
def no_trigger_cue(skill: Skill, ctx: Context) -> list[Finding]:
    """LIST005 - the description says what, never when."""
    text = listing_text(skill)
    if not text:
        return []

    # If the cues exist but fall past the cap, LIST004 already owns that story;
    # reporting "no trigger phrase" as well would contradict it.
    if states_a_trigger(text):
        return []

    return [
        skill.finding(
            "LIST005",
            Severity.WARNING,
            "Description says what the skill does but never says when to use it",
            mechanic=(
                "Claude matches a request against the description semantically. A "
                "description that only names a capability gives it nothing to match "
                "a real request against, which is the most common reason a skill "
                "that looks correct never fires."
            ),
            fix=(
                "Add the conditions that should trigger it, in the words you would "
                'actually type - e.g. "Use when the user asks to ... or mentions ...".'
            ),
            line=skill.line_of("description", 2),
        )
    ]


@per_skill
def description_too_thin(skill: Skill, ctx: Context) -> list[Finding]:
    """LIST006 - too few distinctive words to discriminate against other skills."""
    text = listing_text(skill)
    if not text:
        return []

    words = re.findall(r"[A-Za-z][A-Za-z0-9_-]{2,}", text)
    if len(words) >= 8:
        return []

    # A description that is just the skill's own name restated carries no signal.
    return [
        skill.finding(
            "LIST006",
            Severity.WARNING,
            f"Listing text is only {len(words)} words long",
            mechanic=(
                "Routing is a comparison against every other skill's description. A "
                "very short description has almost no distinctive vocabulary, so "
                "any skill with a fuller description wins ties against it."
            ),
            fix="Describe the task, the inputs, and the phrasing a user would use.",
            line=skill.line_of("description", 2),
        )
    ]
