"""Constants taken from Claude Code's documented behaviour.

Everything in this module is a fact from the official documentation, not a
preference. If a number here is wrong, a rule built on it is wrong, so each one
records where it comes from.

Source: https://code.claude.com/docs/en/skills
"""

from __future__ import annotations

import re

#: The combined ``description`` and ``when_to_use`` text is truncated at 1,536
#: characters in the skill listing, to reduce context usage. Anything past this
#: point never reaches the model when it is choosing a skill.
LISTING_CHAR_BUDGET = 1536

#: ``compatibility`` accepts a string of up to 500 characters.
COMPATIBILITY_MAX = 500

#: Every frontmatter field Claude Code documents. Anything outside this set is
#: not acted on - notably ``version``, which is *not* a Claude Code frontmatter
#: field despite being required by several third-party skill linters.
CLAUDE_CODE_FIELDS = frozenset(
    {
        "name",
        "description",
        "when_to_use",
        "argument-hint",
        "arguments",
        "disable-model-invocation",
        "user-invocable",
        "allowed-tools",
        "disallowed-tools",
        "model",
        "effort",
        "context",
        "agent",
        "background",
        "hooks",
        "paths",
        "shell",
        "metadata",
        "license",
        "compatibility",
    }
)

#: The six fields accepted by the Agent Skills spec, claude.ai skill uploads and
#: the Skills API. Using anything else on those distribution paths fails with
#: "Unexpected key(s) in SKILL.md frontmatter".
SPEC_FIELDS = frozenset(
    {
        "name",
        "description",
        "license",
        "compatibility",
        "metadata",
        "allowed-tools",
    }
)

#: Fields that only mean something alongside ``context: fork``.
FORK_ONLY_FIELDS = frozenset({"agent", "background"})

#: Documented effort levels.
EFFORT_LEVELS = frozenset({"low", "medium", "high", "xhigh", "max"})

#: Bundled commands and skills whose names Claude Code reserves. A local skill
#: overrides a bundled skill of the same name, but never the bundled aliases,
#: so an alias keeps routing away from your skill. This list is deliberately
#: conservative: it holds only names the documentation names explicitly.
BUNDLED_ALIASES: dict[str, tuple[str, ...]] = {
    "code-review": ("review",),
}

#: Wording that signals a description explains *when* to use the skill rather
#: than only what it does. Descriptions stating no conditions at all are the
#: single most common reason a skill never activates.
#:
#: This is a pattern rather than a list of phrases because phrase lists produce
#: false positives on perfectly good descriptions: "Use this skill *whenever*
#: the user wants ..." states its trigger clearly, but does not contain the
#: substring "when the user".
TRIGGER_PATTERN = re.compile(
    r"""
      \bwhen(?:ever)?\b          # when / whenever
    | \bif\b                     # "if the user mentions ..."
    | \bunless\b
    | \btrigger                  # trigger, triggers, triggered
    | \bfor\s+example\b
    | \be\.g\.
    | \bsuch\s+as\b
    | \bapplies?\s+to\b
    | \buse\s+(?:this|it|for)\b
    | \binvoke
    | \basks?\s+(?:for|to|about)\b
    | \bwants?\s+to\b
    | \breach\s+for\b
    | \bany\s+time\b
    """,
    re.IGNORECASE | re.VERBOSE,
)


def states_a_trigger(text: str) -> bool:
    """True when ``text`` says anything about *when* to use the skill."""
    return bool(TRIGGER_PATTERN.search(text))


#: Words too common in skill descriptions to distinguish one skill from another.
STOPWORDS = frozenset(
    """
    a an and are as at be but by for from has have how in into is it its of on
    or that the this to use used uses using was were what when where which who
    will with you your users user claude code skill skills should would can
    could not do does doing if then than there their they them these those
    also any all more most other some such only own same so up out over
    """.split()
)
