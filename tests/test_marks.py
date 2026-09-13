"""Tests for yasc.marks: boundary strength (spec §5.2)."""

import unittest

from yasc.marks import Bracket, Mark, gap_satisfies, mark_from_symbol


class TestMarks(unittest.TestCase):
    def test_symbols_round_trip(self):
        for m in Mark:
            self.assertIs(mark_from_symbol(m.value), m)
        self.assertIsNone(mark_from_symbol("+"))

    def test_strength_table(self):
        # (pattern symbol, gap marks, expected)
        cases = [
            (Mark.SYLLABLE, {Mark.SYLLABLE}, True),
            (Mark.SYLLABLE, {Mark.WORD}, True),
            (Mark.SYLLABLE, {Mark.PHRASE}, True),
            (Mark.SYLLABLE, {Mark.MORPHEME}, False),
            (Mark.SYLLABLE, {Mark.CLITIC}, False),
            (Mark.MORPHEME, {Mark.MORPHEME}, True),
            (Mark.MORPHEME, {Mark.CLITIC}, True),
            (Mark.MORPHEME, {Mark.WORD}, True),
            (Mark.MORPHEME, {Mark.SYLLABLE}, False),
            (Mark.CLITIC, {Mark.MORPHEME}, False),
            (Mark.CLITIC, {Mark.WORD}, True),
            (Mark.WORD, {Mark.CLITIC}, False),
            (Mark.WORD, {Mark.PHRASE}, True),
            (Mark.PHRASE, {Mark.WORD}, False),
            (Mark.PHRASE, {Mark.PHRASE}, True),
            (Mark.WORD, set(), False),
        ]
        for wanted, marks, expected in cases:
            with self.subTest(wanted=wanted, marks=marks):
                self.assertEqual(gap_satisfies(frozenset(marks), wanted), expected)

    def test_bracket_is_value(self):
        self.assertEqual(Bracket("N", 0, 3), Bracket("N", 0, 3))
        self.assertEqual(hash(Bracket("N", 0, 3)), hash(Bracket("N", 0, 3)))


if __name__ == "__main__":
    unittest.main()
