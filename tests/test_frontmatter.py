"""Tests for the frontmatter reader.

The behaviour under test is Claude Code's, not YAML's. Where the two differ,
Claude Code wins - that difference is the whole point of the parser.
"""

from __future__ import annotations

import unittest

from whyskill.frontmatter import parse


class FirstLineRule(unittest.TestCase):
    """`---` must be the file's first line or frontmatter is not read at all."""

    def test_clean_frontmatter_parses(self):
        result = parse("---\nname: demo\ndescription: A thing.\n---\n\nBody.\n")
        self.assertTrue(result.has_frontmatter)
        self.assertEqual(result.data["name"], "demo")
        self.assertEqual(result.body, "Body.")
        self.assertEqual(result.issues, [])

    def test_leading_blank_line_disables_frontmatter(self):
        result = parse("\n---\nname: demo\n---\n\nBody.\n")
        self.assertFalse(result.has_frontmatter)
        self.assertEqual(result.data, {})
        self.assertIn("LOAD001", [i.code for i in result.issues])

    def test_bom_disables_frontmatter(self):
        result = parse("﻿---\nname: demo\n---\n\nBody.\n")
        self.assertIn("LOAD002", [i.code for i in result.issues])

    def test_indented_delimiter_is_not_a_delimiter(self):
        result = parse("  ---\nname: demo\n---\n")
        self.assertFalse(result.has_frontmatter)
        self.assertIn("LOAD001", [i.code for i in result.issues])

    def test_prose_before_delimiter(self):
        result = parse("Some notes\n---\nname: demo\n---\n")
        self.assertFalse(result.has_frontmatter)
        self.assertIn("LOAD001", [i.code for i in result.issues])

    def test_trailing_whitespace_on_delimiter_is_fine(self):
        result = parse("---   \nname: demo\n---\n")
        self.assertTrue(result.has_frontmatter)
        self.assertEqual(result.data["name"], "demo")

    def test_unclosed_frontmatter(self):
        result = parse("---\nname: demo\ndescription: x\n")
        self.assertFalse(result.has_frontmatter)
        self.assertIn("LOAD003", [i.code for i in result.issues])

    def test_empty_file(self):
        result = parse("")
        self.assertFalse(result.has_frontmatter)
        self.assertEqual(result.data, {})


class ScalarParsing(unittest.TestCase):
    def test_booleans_and_null(self):
        result = parse(
            "---\ndisable-model-invocation: true\nuser-invocable: false\nagent: ~\n---\n"
        )
        self.assertIs(result.data["disable-model-invocation"], True)
        self.assertIs(result.data["user-invocable"], False)
        self.assertIsNone(result.data["agent"])

    def test_quoted_strings_keep_their_text(self):
        result = parse('---\ndescription: "Use when: the user asks. #1 case"\n---\n')
        self.assertEqual(result.data["description"], "Use when: the user asks. #1 case")

    def test_comment_stripped_only_outside_quotes(self):
        result = parse("---\nname: demo  # trailing note\ncolor: '#fff'\n---\n")
        self.assertEqual(result.data["name"], "demo")
        self.assertEqual(result.data["color"], "#fff")

    def test_colon_in_unquoted_value(self):
        result = parse("---\ndescription: Use when: the user asks\n---\n")
        self.assertEqual(result.data["description"], "Use when: the user asks")

    def test_numbers(self):
        result = parse("---\nmetadata:\n  count: 3\n---\n")
        self.assertEqual(result.data["metadata"]["count"], 3)


class CollectionParsing(unittest.TestCase):
    def test_inline_list(self):
        result = parse("---\nallowed-tools: [Read, Grep, Bash]\n---\n")
        self.assertEqual(result.data["allowed-tools"], ["Read", "Grep", "Bash"])

    def test_block_list(self):
        result = parse("---\npaths:\n  - src/**/*.ts\n  - lib/**/*.ts\n---\n")
        self.assertEqual(result.data["paths"], ["src/**/*.ts", "lib/**/*.ts"])

    def test_nested_map(self):
        result = parse("---\nmetadata:\n  team: platform\n  tier: gold\n---\n")
        self.assertEqual(result.data["metadata"], {"team": "platform", "tier": "gold"})

    def test_block_scalar(self):
        result = parse("---\ndescription: |\n  Line one.\n  Line two.\n---\n")
        self.assertEqual(result.data["description"], "Line one.\nLine two.")

    def test_folded_scalar(self):
        result = parse("---\ndescription: >\n  Line one.\n  Line two.\n---\n")
        self.assertEqual(result.data["description"], "Line one. Line two.")

    def test_commas_inside_quotes_do_not_split_a_list(self):
        result = parse('---\nallowed-tools: ["Bash(git diff:*, x)", Read]\n---\n')
        self.assertEqual(result.data["allowed-tools"], ["Bash(git diff:*, x)", "Read"])


class MultiLineQuotedScalars(unittest.TestCase):
    """Regression: a quoted value spanning lines was read as broken frontmatter.

    Found on a real install - one skill produced ten LOAD004 findings because
    the parser only ever read `key: value` from a single line. The file was
    valid YAML the whole time. False alarms are worse than silence for a tool
    whose pitch is being right about mechanics.
    """

    def test_double_quoted_value_spanning_lines(self):
        result = parse(
            "---\n"
            "name: demo\n"
            'description: "Solve competition problems (IMO, Putnam) with\n'
            "  verification. Use when asked to 'prove this' or 'check that'.\"\n"
            "allowed-tools: Read\n"
            "---\n"
        )
        self.assertEqual(result.issues, [])
        self.assertIn("Solve competition problems", result.data["description"])
        self.assertIn("check that", result.data["description"])
        # A key after the multi-line value must still be found.
        self.assertEqual(result.data["allowed-tools"], "Read")

    def test_line_breaks_fold_to_single_spaces(self):
        result = parse('---\ndescription: "one\n  two\n  three"\n---\n')
        self.assertEqual(result.data["description"], "one two three")

    def test_single_quoted_value_spanning_lines(self):
        result = parse("---\ndescription: 'alpha\n  beta'\nname: x\n---\n")
        self.assertEqual(result.issues, [])
        self.assertEqual(result.data["description"], "alpha beta")
        self.assertEqual(result.data["name"], "x")

    def test_embedded_quotes_do_not_end_the_value_early(self):
        result = parse('---\ndescription: "he said \\"maybe\\" and\n  then left"\nname: x\n---\n')
        self.assertEqual(result.issues, [])
        self.assertEqual(result.data["name"], "x")
        self.assertIn("maybe", result.data["description"])

    def test_doubled_single_quote_is_an_escape(self):
        result = parse("---\ndescription: 'it''s fine\n  really'\nname: x\n---\n")
        self.assertEqual(result.issues, [])
        self.assertEqual(result.data["description"], "it's fine really")

    def test_single_line_quoted_value_is_unaffected(self):
        result = parse('---\ndescription: "all on one line"\nname: x\n---\n')
        self.assertEqual(result.issues, [])
        self.assertEqual(result.data["description"], "all on one line")
        self.assertEqual(result.data["name"], "x")

    def test_a_genuinely_unterminated_quote_is_still_reported(self):
        result = parse('---\nname: demo\ndescription: "never closed\n---\n')
        self.assertIn("LOAD004", [i.code for i in result.issues])


class Diagnostics(unittest.TestCase):
    def test_duplicate_key_reported(self):
        result = parse("---\ndescription: First.\ndescription: Second.\n---\n")
        self.assertIn("LOAD005", [i.code for i in result.issues])
        # YAML keeps the last one, and so do we.
        self.assertEqual(result.data["description"], "Second.")

    def test_tab_indentation_reported(self):
        result = parse("---\nmetadata:\n\tteam: platform\n---\n")
        self.assertIn("LOAD004", [i.code for i in result.issues])

    def test_unparseable_line_reported(self):
        result = parse("---\nname: demo\nthis line has no colon\n---\n")
        self.assertIn("LOAD004", [i.code for i in result.issues])

    def test_key_lines_are_recorded(self):
        result = parse("---\nname: demo\ndescription: x\n---\n")
        self.assertEqual(result.key_lines["name"], 2)
        self.assertEqual(result.key_lines["description"], 3)


if __name__ == "__main__":
    unittest.main()
