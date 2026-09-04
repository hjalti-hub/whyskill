---
name: summarize-diff
description: Summarizes uncommitted changes and flags anything risky. Use when the user asks what changed, wants a commit message, or asks to review their diff.
allowed-tools: Bash(git diff:*) Bash(git status:*)
---

# Summarize the working tree

1. Run `git status --short` and `git diff` to see what changed.
2. Group the changes by intent, not by file.
3. Call out anything that touches auth, migrations, or public APIs.
