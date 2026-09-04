# whyskill

**Find the Claude Code skills that will never fire — before you spend an hour wondering why.**

A broken skill does not print an error. It does not warn you. It just sits there
while Claude behaves as though it does not exist. That silence is the entire
problem: you cannot debug something that never says anything.

`whyskill` finds those skills. Zero dependencies, no API key, no model calls —
just `python3`.

```console
$ whyskill

.claude/skills/deploy-staging/SKILL.md
  1: error LOAD001  Blank line(s) before the `---` on line 2; frontmatter is only read when `---` is line 1
      fix: Delete the blank line(s) so `---` is the first line.

.claude/skills/release-notes/SKILL.md
  4: error LIST004  Listing text is 1674 characters, 138 over the 1536-character cap - and every trigger phrase is in the truncated part
      fix: Move the trigger phrases to the front of `description`. Dropped text begins:
      'k procedure, and the on-call escalation path. Use when the user asks to deplo'

.claude/skills/git-helper/SKILL.md
  3: warning COLLIDE005  'git-helper' and 'git-assistant' describe themselves 59% alike; routing between them is unreliable
      fix: Make the descriptions disjoint. Both currently claim: branches, changes, git,
      commit, message, write. State what each one is *not* for, or name the other skill
      explicitly to turn the ambiguity into a routing rule.

3 error(s) · 4 warning(s) across 22 skills
Run with --explain to see why each one matters.
```

And the question you actually have:

```console
$ whyskill why deploy-staging

deploy-staging  .claude/skills/deploy-staging/SKILL.md
  source: project
  Claude cannot auto-invoke this skill.

  error LOAD001  Blank line(s) before the `---` on line 2
      why: Claude Code reads frontmatter only when the opening `---` is the file's first
           line. Otherwise it treats the whole file, `---` markers included, as skill
           content - so the skill has no description to match on.
      fix: Delete the blank line(s) so `---` is the first line.
```

---

## Why another skill linter?

There are already several good SKILL.md linters. Before writing this one I
checked what they cover, and the honest answer is: **they check different
things, and they check them one file at a time.**

Two gaps motivated this tool.

**1. Some widely-used checks are not real.** A popular skill auditor reports
that 62% of public skills are "missing the `version` field". `version` is not a
Claude Code frontmatter field. It appears nowhere in the
[frontmatter reference](https://code.claude.com/docs/en/skills#frontmatter-reference);
Claude Code does not read it. Adding it fixes nothing. `whyskill` only
implements rules traceable to documented behaviour, and every finding it emits
names the mechanic it comes from — so you can check the claim yourself.

**2. Whether your skill loads is not a property of your file.** It depends on
what else is installed. A skill can be flawless on its own and still never run
because a same-named personal skill outranks it, or because another skill's
description claims the same vocabulary. A per-file linter cannot see this, no
matter how many rules it has. `whyskill` analyses the whole visible set at once.

It is not a replacement for the others — it does not check shell-script safety
or style. It answers one question they do not: **will this skill be there when
you need it?**

---

## Install

```bash
pip install whyskill
```

Or run it straight from a clone — there is nothing to install:

```bash
git clone https://github.com/hjalti-hub/claude-skill
python3 -m whyskill /path/to/your/skills
```

Requires Python 3.9+. No third-party packages, at runtime or otherwise.

## Use

```bash
whyskill                      # this project's skills, plus your personal ones
whyskill ./skills             # a directory of skills you are publishing
whyskill why deploy           # explain one skill
whyskill list                 # what was discovered, and from where
whyskill rules                # every rule, grouped
```

Useful flags:

| Flag | Effect |
| :--- | :--- |
| `--explain` | Show the documented mechanic behind each finding |
| `--target spec` | Also check claude.ai / Skills API field limits |
| `--json`, `--sarif` | Machine-readable output (SARIF annotates CI diffs) |
| `--fail-on warning` | Treat warnings as failures too (default: `error`) |
| `--disable IDS` | Suppress rules, e.g. `--disable INVOKE002,PORT001` |
| `--no-personal` | Skip `~/.claude/skills` — **turns off shadowing detection** |
| `--overlap N` | Similarity threshold for `COLLIDE005` (default `0.5`) |

Exit code is `0` when clean, `1` when something at or above `--fail-on` was
found, `2` for a usage error.

---

## What it checks

Every rule below is derived from documented Claude Code behaviour, and every one
of them fails **silently** — that is the bar for inclusion. Ordinary style
opinions are out of scope.

### Loading — will Claude Code read this file at all?

| Rule | Finding |
| :--- | :--- |
| `LOAD001` | The opening `---` is not the file's first line |
| `LOAD002` | A UTF-8 BOM sits before the `---` |
| `LOAD003` | Frontmatter is opened but never closed |
| `LOAD004` | A frontmatter line cannot be parsed and its field is dropped |
| `LOAD005` | A duplicate key silently discards the earlier value |

> Claude Code reads the frontmatter only when the opening `---` is the file's
> first line. Otherwise it treats the whole file, `---` markers included, as
> skill content.

A stray blank line, or a BOM your editor never shows you, turns your entire
frontmatter into prose. The skill still appears in `/skills`. It just has no
description, so Claude never picks it.

### Listing — is there anything for Claude to route on?

| Rule | Finding |
| :--- | :--- |
| `LIST001` | No `description` and no body paragraph to fall back on |
| `LIST002` | No `description`; routing falls back to the first body paragraph |
| `LIST003` | Listing text exceeds the 1,536-character cap and is truncated |
| `LIST004` | Listing text is truncated **and every trigger phrase is in the cut part** |
| `LIST005` | The description says what the skill does but never when to use it |
| `LIST006` | Too few distinctive words to win a routing tie |

> Put the key use case first: the combined `description` and `when_to_use` text
> is truncated at 1,536 characters in the skill listing to reduce context usage.

`LIST004` is the one worth the price of admission. Long descriptions put their
examples last — "…use this when the user asks to deploy" — which is exactly the
text the cap removes. The skill looks thoroughly documented and is, to the
router, undescribed.

### Invocation — can anything actually invoke it?

| Rule | Finding |
| :--- | :--- |
| `INVOKE001` | Both model and user invocation are disabled; nothing can run it |
| `INVOKE002` | `disable-model-invocation: true` — Claude can never load it on its own |
| `INVOKE003` | A `paths` glob matches no file in the project |
| `INVOKE004` | `agent`/`background` set without `context: fork`, so they do nothing |
| `INVOKE005` | `effort` is not one of the documented levels |

`INVOKE003` catches a typo that is invisible by construction: `paths:
src/component/**/*.tsx` when the directory is `components`. The skill is
correct, loaded, and permanently unreachable in that repository.

### Collision — does it survive alongside your other skills?

These are the rules a per-file linter cannot implement.

| Rule | Finding |
| :--- | :--- |
| `COLLIDE001` | Another skill's name folds to the same value; only one loads |
| `COLLIDE002` | The name contains a look-alike or invisible character |
| `COLLIDE003` | A `.claude/commands/` file is shadowed by a skill of the same name |
| `COLLIDE004` | Overrides a bundled skill, but its aliases still route elsewhere |
| `COLLIDE005` | Two skills describe themselves so alike that routing is unreliable |

> When skills share the same name, Claude Code resolves the conflict by source:
> enterprise overrides personal, and **personal overrides project**.

Note the direction — it is the opposite of how nearly all per-project
configuration behaves. A `deploy` skill you wrote for one repository is silently
replaced by a `deploy` skill you wrote months ago in `~/.claude/skills`, and
nothing anywhere tells you which one ran.

> When it compares names, Claude Code ignores case, spacing, and invisible
> characters, and treats compatibility forms such as fullwidth letters and dash
> variants as their plain equivalents […] A name that differs only by a
> look-alike letter from another alphabet counts as a different name.

Those two sentences cut in opposite directions, and `whyskill` implements both.
`Deploy—App` and `deploy-app` are the *same* name — one of them will not load.
But `review` and `rеview` (Cyrillic `е`) are *different* names, so the override
you intended never happens. Both cases are invisible in every editor.

**`COLLIDE005` uses no model.** Similarity is an inverse-document-frequency
weighted Jaccard over the listing vocabulary: words every skill uses count for
almost nothing, so the score reflects the *distinctive* terms two skills are
both claiming. It is identical on every run, which is what makes it safe to gate
CI on. The report names the contested words, so the fix is obvious.

### Fields — do they do what you think?

| Rule | Finding |
| :--- | :--- |
| `PORT001` | A field Claude Code does not read, so it has no effect |
| `PORT002` | A field rejected by claude.ai uploads and the Skills API (`--target spec`) |
| `PORT003` | `compatibility` exceeds its 500-character limit |
| `PORT004` | `metadata` is dropped or reuses a frontmatter field name |

`PORT001` knows the common ones by name: `version`, `author`, `tools`,
`when-to-use` (the field is `when_to_use`), `allowed_tools` (it is
`allowed-tools`). Claude Code ignores unknown fields silently, so a misspelled
field behaves exactly like a missing one.

`PORT002` is for publishing. claude.ai uploads, the Skills API and
`package_skill.py` accept only six fields — `name`, `description`, `license`,
`compatibility`, `metadata`, `allowed-tools` — and reject the file outright
otherwise. Run `--target spec` before you publish.

---

## In CI

```yaml
name: skills
on: [push, pull_request]

jobs:
  whyskill:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - uses: actions/setup-python@v5
        with:
          python-version: '3.11'
      - run: pipx install whyskill
      - run: whyskill .claude/skills --no-personal --fail-on warning
```

For inline annotations on the diff, emit SARIF and upload it:

```yaml
      - run: whyskill .claude/skills --no-personal --sarif --fail-on never > whyskill.sarif
      - uses: github/codeql-action/upload-sarif@v3
        with:
          sarif_file: whyskill.sarif
```

Pass `--no-personal` in CI: there is no `~/.claude/skills` on a runner, and
without it the shadowing rules have nothing to compare against.

---

## Examples

`examples/broken/` contains one skill per failure mode, each broken on purpose
and each verified by the test suite to still demonstrate its bug:

```console
$ whyskill examples/broken --no-personal
8 error(s) · 9 warning(s) across 15 skills
```

`examples/good/` contains one that passes. Both are worth reading — the broken
ones look completely fine.

## Library use

```python
import whyskill

skills, findings = whyskill.check(project=".")
for finding in findings:
    print(finding.rule, finding.severity.value, finding.path, finding.message)
```

## Contributing

The bar for a new rule is specific: it must describe a failure that produces
**no error message**, and it must be traceable to documented Claude Code
behaviour. Rules encoding a preference belong in one of the style-focused
linters instead.

If you add one, add its documented basis to `whyskill/spec.py`, a broken example
under `examples/broken/`, and a test asserting it fires there and stays silent on
`examples/good/`.

```bash
python3 -m unittest discover -s tests -t .
```

## Accuracy

If a rule here contradicts Claude Code's actual behaviour, that is a bug worth
reporting — the value of this tool is entirely in being right about mechanics.
Behaviour also changes between versions; findings are derived from the skills
documentation as of September 2026.

## License

MIT
