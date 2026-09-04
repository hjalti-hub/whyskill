---
name: whyskill
description: Diagnose Claude Code skills that fail silently - ones that never load, never get chosen, or are shadowed by another skill. Use when a skill is not firing, when the user asks why a skill did not trigger, after writing or editing any SKILL.md, or before publishing skills to others.
allowed-tools: Bash(whyskill:*) Bash(python3 -m whyskill:*)
---

# Diagnosing a skill that will not fire

Skills fail without printing anything. Do not reason about why a skill did not
trigger from reading its file - run the checker, because most causes are
invisible on the page (a byte order mark, a description truncated at a character
limit, a same-named skill in another directory taking precedence).

## When a specific skill is not firing

```bash
whyskill why <skill-name>
```

This states plainly whether anything can invoke the skill, and lists what is
wrong with the reasoning behind each finding.

## After writing or editing a SKILL.md

```bash
whyskill <path-to-skill-directory>
```

Fix every `error` before moving on. Warnings affect whether the skill wins
against others and are usually worth fixing too.

## Before publishing skills

```bash
whyskill ./skills --target spec
```

`--target spec` adds the field restrictions that claude.ai uploads and the
Skills API enforce, which reject a file outright rather than ignoring it.

## Reading the output

Each finding carries a rule id, what is wrong, and a fix. Add `--explain` for
the documented Claude Code behaviour the rule comes from.

Findings are ordered by root cause: a `LOAD*` finding means the frontmatter was
never parsed, which explains every other complaint about that file. Fix those
first and re-run before touching anything else.

## What the rule groups mean

- `LOAD*` — the frontmatter never parsed, so the skill has no description
- `LIST*` — nothing useful for Claude to match a request against
- `INVOKE*` — nothing can invoke it, or a `paths` glob matches no file
- `COLLIDE*` — another skill takes precedence, or claims the same vocabulary
- `PORT*` — a frontmatter field that does nothing, or breaks publishing

## If it is not installed

```bash
pip install whyskill
```

To have this run automatically instead of on request, `whyskill install`
registers it as a hook: skills are then checked when a session starts and
whenever a `SKILL.md` is written.
