"""Regression tests for the P10 changes to earlier modules (design §12 "P10"; §13 entries
163 and 170).

- ``FeatureSystem.segment`` accepts a bundle string, as spec §11.2 documents
  (``sc.phonology.segment("{+Syll +High}")``); the mapping form is unchanged.
- ``cli``: the ``--allow-pending`` help text no longer claims that P7–P9 are pending.
- ``compile``: ``!include "lib:..."`` (tested in ``tests/test_lib.py``).
"""

import os
import unittest

import yasc
from yasc.cli import build_arg_parser
from yasc.errors import YascDefinitionError

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


class SegmentStringTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.sc = yasc.load(os.path.join(ROOT, "examples", "simple", "grimm.yasc"))
        cls.fs = cls.sc.phonology

    def test_spec_11_2_example(self):
        seg = self.sc.phonology.segment("{+Syll +High}")
        self.assertEqual(seg, self.fs.segment({"Syll": "+", "High": "+"}))
        self.assertEqual(seg.get("Syll"), "+")
        self.assertIsNone(seg.get("Voice"))

    def test_forms_of_constraints(self):
        seg = self.fs.segment("{-Voice !Labial Coronal _Asp}")   # Unary shorthand; _F unset
        self.assertEqual(seg.get("Voice"), "-")
        self.assertEqual(seg.get("Labial"), "!")
        self.assertEqual(seg.get("Coronal"), "!")
        self.assertIsNone(seg.get("Asp"))
        self.assertEqual(self.fs.segment("+Syll"), self.fs.segment({"Syll": "+"}))   # braces optional
        self.assertEqual(self.fs.segment(""), self.fs.empty)

    def test_values_and_aliases(self):
        sc = yasc.loads('!include "lib:ipa.yasc"\n', "<p10>")
        fs = sc.phonology
        self.assertEqual(fs.segment("{2Stress +Tense}").get("ATR"), "+")
        self.assertEqual(fs.segment("{2Stress}").get("Stress"), "2")
        tone = yasc.loads("Phonology [[\n  Syll Binary\n  Tone [H] [L]\n]]\n", "<p10>").compiled.phonology
        self.assertEqual(tone.segment("{[H]Tone}").get("Tone"), "H")

    def test_errors(self):
        for bad in ("{+Nope}", "{High}", "{+Place}", "{+Syll", "{(a)High}"):
            with self.subTest(bad=bad):
                with self.assertRaises(YascDefinitionError):
                    self.fs.segment(bad)


class CliHelpTests(unittest.TestCase):
    def test_allow_pending_help_is_current(self):
        text = build_arg_parser().format_help()
        self.assertNotIn("P7-P9", text)
        self.assertIn("--allow-pending", text)


if __name__ == "__main__":
    unittest.main()
