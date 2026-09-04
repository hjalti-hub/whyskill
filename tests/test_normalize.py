"""Tests for name folding.

Two documented rules pull in opposite directions, and both are tested here:
names fold across case/spacing/invisibles/compatibility forms, but a look-alike
letter from another alphabet makes a *different* name.
"""

from __future__ import annotations

import unittest

from whyskill.normalize import fold, skeleton, suspicious_characters


class Folding(unittest.TestCase):
    def test_case_is_ignored(self):
        self.assertEqual(fold("Commit"), fold("commit"))

    def test_spacing_is_ignored(self):
        self.assertEqual(fold("code review"), fold("codereview"))

    def test_dash_variants_fold_together(self):
        self.assertEqual(fold("deploy-app"), fold("deploy—app"))
        self.assertEqual(fold("deploy-app"), fold("deploy–app"))
        self.assertEqual(fold("deploy-app"), fold("deploy‑app"))

    def test_fullwidth_letters_fold_to_ascii(self):
        self.assertEqual(fold("ｄｅｐｌｏｙ"), fold("deploy"))

    def test_invisible_characters_are_ignored(self):
        self.assertEqual(fold("com​mit"), fold("commit"))
        self.assertEqual(fold("com­mit"), fold("commit"))

    def test_distinct_names_stay_distinct(self):
        self.assertNotEqual(fold("deploy"), fold("deploy-app"))


class LookAlikes(unittest.TestCase):
    """A Cyrillic letter makes a different name, invisibly."""

    def test_cyrillic_does_not_fold_to_latin(self):
        cyrillic = "rеview"  # Cyrillic IE
        self.assertNotEqual(fold(cyrillic), fold("review"))

    def test_skeleton_exposes_the_twin(self):
        cyrillic = "rеview"
        self.assertEqual(skeleton(cyrillic), skeleton("review"))

    def test_suspicious_characters_are_located(self):
        hits = suspicious_characters("rеview")
        self.assertEqual(len(hits), 1)
        index, char, description = hits[0]
        self.assertEqual(index, 1)
        self.assertEqual(char, "е")
        self.assertIn("CYRILLIC", description)

    def test_clean_name_has_nothing_suspicious(self):
        self.assertEqual(suspicious_characters("review-pr"), [])

    def test_invisible_character_is_flagged(self):
        hits = suspicious_characters("com​mit")
        self.assertTrue(hits)
        self.assertIn("invisible", hits[0][2])


if __name__ == "__main__":
    unittest.main()
