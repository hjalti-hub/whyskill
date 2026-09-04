"""Name folding that matches how Claude Code compares skill names.

The documented rule: when Claude Code compares names it "ignores case, spacing,
and invisible characters, and treats compatibility forms such as fullwidth
letters and dash variants as their plain equivalents", so a `Commit` cannot load
beside a `commit`. And crucially: "A name that differs only by a look-alike
letter from another alphabet counts as a *different* name."

Those two rules cut in opposite directions, and both fail silently:

* Two names you think are distinct fold together, and one skill never loads.
* Two names you think are identical do *not* fold together, because one contains
  a Cyrillic ``а`` instead of a Latin ``a`` - so the override you intended never
  happens and both sit there looking the same in every editor.

:func:`fold` implements the first rule. :func:`skeleton` implements the second,
so we can spot the look-alike trap and say so out loud.
"""

from __future__ import annotations

import unicodedata

#: Characters that occupy no visual space. Claude Code ignores these when
#: comparing names, so they can hide a collision from a human reader.
INVISIBLE = {
    "­",  # soft hyphen
    "᠎",  # mongolian vowel separator
    "​",  # zero width space
    "‌",  # zero width non-joiner
    "‍",  # zero width joiner
    "‎",  # left-to-right mark
    "‏",  # right-to-left mark
    "⁠",  # word joiner
    "⁡",
    "⁢",
    "⁣",
    "⁤",
    "﻿",  # zero width no-break space / BOM
}
INVISIBLE |= {chr(c) for c in range(0x202A, 0x202F)}  # bidi embedding/override
INVISIBLE |= {chr(c) for c in range(0x2066, 0x206A)}  # bidi isolates
INVISIBLE |= {chr(c) for c in range(0xFE00, 0xFE10)}  # variation selectors

#: Dash-like characters that fold to a plain hyphen. NFKC already handles the
#: fullwidth form (U+FF0D) but leaves en/em dashes alone, so they are listed.
DASHES = {
    "‐",  # hyphen
    "‑",  # non-breaking hyphen
    "‒",  # figure dash
    "–",  # en dash
    "—",  # em dash
    "―",  # horizontal bar
    "⁃",  # hyphen bullet
    "−",  # minus sign
    "⸺",  # two-em dash
    "⸻",  # three-em dash
    "﹘",  # small em dash
    "﹣",  # small hyphen-minus
    "－",  # fullwidth hyphen-minus
    "_",  # underscore reads as a separator to humans comparing names
}

#: Non-Latin letters commonly mistaken for ASCII ones. Used only to *detect* the
#: look-alike trap - Claude Code itself treats these as distinct characters.
CONFUSABLES = {
    # Cyrillic
    "а": "a",
    "е": "e",
    "о": "o",
    "р": "p",
    "с": "c",
    "у": "y",
    "х": "x",
    "ѕ": "s",
    "і": "i",
    "ј": "j",
    "һ": "h",
    "ԁ": "d",
    "ԛ": "q",
    "ԝ": "w",
    "м": "m",
    "т": "t",
    "в": "b",
    "н": "h",
    "к": "k",
    "А": "a",
    "В": "b",
    "Е": "e",
    "К": "k",
    "М": "m",
    "Н": "h",
    "О": "o",
    "Р": "p",
    "С": "c",
    "Т": "t",
    "Х": "x",
    # Greek
    "ο": "o",
    "α": "a",
    "ρ": "p",
    "ν": "v",
    "ι": "i",
    "κ": "k",
    "ε": "e",
    "τ": "t",
    "υ": "u",
    "χ": "x",
    "β": "b",
    "Α": "a",
    "Β": "b",
    "Ε": "e",
    "Ζ": "z",
    "Η": "h",
    "Ι": "i",
    "Κ": "k",
    "Μ": "m",
    "Ν": "n",
    "Ο": "o",
    "Ρ": "p",
    "Τ": "t",
    "Υ": "y",
    "Χ": "x",
    # Other
    "ɡ": "g",  # latin small script g
    "ǃ": "!",  # latin letter retroflex click
    "ı": "i",  # dotless i
}


def fold(name: str) -> str:
    """Fold a name the way Claude Code does when comparing two of them.

    Two names with the same fold cannot coexist: one of them will not load.
    """
    # NFKC collapses compatibility forms, including fullwidth letters.
    text = unicodedata.normalize("NFKC", name)
    out = []
    for ch in text:
        if ch in INVISIBLE:
            continue
        if ch.isspace():
            continue
        if ch in DASHES:
            out.append("-")
            continue
        out.append(ch)
    return "".join(out).casefold()


def skeleton(name: str) -> str:
    """Fold *and* map look-alike letters to their ASCII twins.

    Two names with the same skeleton but different :func:`fold` values look
    identical to a human and are distinct to Claude Code - the silent trap.
    """
    folded = fold(name)
    return "".join(CONFUSABLES.get(ch, ch) for ch in folded)


def scripts_used(name: str) -> set[str]:
    """Unicode script families present among the letters of ``name``.

    A name mixing scripts is nearly always an accident (a pasted Cyrillic
    character), and it is invisible in every editor.
    """
    found: set[str] = set()
    for ch in name:
        if not ch.isalpha():
            continue
        try:
            block = unicodedata.name(ch).split(" ")[0]
        except ValueError:
            continue
        found.add(block)
    return found


def suspicious_characters(name: str) -> list[tuple[int, str, str]]:
    """Locate confusable or invisible characters.

    Returns ``(index, character, human description)`` for each offender.
    """
    hits: list[tuple[int, str, str]] = []
    for idx, ch in enumerate(name):
        if ch in INVISIBLE:
            try:
                label = unicodedata.name(ch)
            except ValueError:
                label = "invisible character"
            hits.append((idx, ch, f"invisible {label} (U+{ord(ch):04X})"))
        elif ch in CONFUSABLES:
            try:
                label = unicodedata.name(ch)
            except ValueError:
                label = "look-alike letter"
            hits.append((idx, ch, f"{label} (U+{ord(ch):04X}) looks like {CONFUSABLES[ch]!r}"))
    return hits
