"""One-line summary of every rule, for ``whyskill rules`` and the README."""

from __future__ import annotations

CATALOG: dict[str, str] = {
    # Does the file load at all?
    "LOAD000": "Skill file cannot be read",
    "LOAD001": "Opening `---` is not the file's first line, so frontmatter is never parsed",
    "LOAD002": "A byte order mark sits before the `---`, so frontmatter is never parsed",
    "LOAD003": "Frontmatter is opened but never closed",
    "LOAD004": "A frontmatter line cannot be parsed and its field is dropped",
    "LOAD005": "A duplicate frontmatter key silently discards the earlier value",
    # Is there anything to route on?
    "LIST001": "No `description` and no body paragraph to fall back on",
    "LIST002": "No `description`; routing falls back to the first body paragraph",
    "LIST003": "Listing text exceeds the 1,536-character cap and is truncated",
    "LIST004": "Listing text is truncated and every trigger phrase is in the cut part",
    "LIST005": "Description says what the skill does but never when to use it",
    "LIST006": "Listing text has too few distinctive words to win a routing tie",
    # Can anything invoke it?
    "INVOKE001": "Both model and user invocation are disabled, so the skill can never run",
    "INVOKE002": "`disable-model-invocation: true` - Claude can never load this on its own",
    "INVOKE003": "A `paths` glob matches no file in the project, so the skill cannot auto-load",
    "INVOKE004": "`agent`/`background` set without `context: fork`, so they do nothing",
    "INVOKE005": "`effort` is not one of the documented levels",
    # What else is installed?
    "COLLIDE001": "Another skill's name folds to the same value; only one of them loads",
    "COLLIDE002": "The name contains a look-alike or invisible character",
    "COLLIDE003": "A command file is shadowed by a skill of the same name",
    "COLLIDE004": "Overrides a bundled skill, but its aliases still route elsewhere",
    "COLLIDE005": "Two skills describe themselves so alike that routing is unreliable",
    # Fields
    "PORT001": "A frontmatter field Claude Code does not read, so it has no effect",
    "PORT002": "A frontmatter field rejected by claude.ai uploads and the Skills API",
    "PORT003": "`compatibility` exceeds its 500-character limit",
    "PORT004": "`metadata` is dropped or reuses a frontmatter field name",
}

#: Grouping used by ``whyskill rules`` output.
GROUPS: dict[str, str] = {
    "LOAD": "Loading - will Claude Code read this file at all?",
    "LIST": "Listing - is there anything for Claude to route on?",
    "INVOKE": "Invocation - can anything actually invoke it?",
    "COLLIDE": "Collision - does it survive alongside your other skills?",
    "PORT": "Fields - do the frontmatter fields do what you think?",
}
