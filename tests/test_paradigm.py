"""Tests for paradigms (spec §9; plan P9; yasc/paradigm.py, decisions in docs/decisions/P9.md):
a Latin-style noun paradigm, cell restrictions, bracketed templates for cyclic rules,
paradigm-tagged records and the examples/paradigm/ expected output."""

import os
import unittest

import yasc
from yasc.lexicon import read_records
from yasc.marks import Bracket, Mark
from yasc.paradigm import build_cell, concatenate
from yasc.runtime import Runtime

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

PHON = """$P := Phonology [[
  Syll Binary
  Voice Binary
  Cont Binary
  Nasal Binary
  Lat Binary
  Lab Binary
  Dor Binary
  High Binary
  Back Binary
]]
$O := Orthography [[
  [p t k b d g s m n l r] {-Syll}
  [p t k s] {-Voice}
  [b d g m n l r] {+Voice}
  [p t k b d g m n] {-Cont}
  [s l r] {+Cont}
  [m n] {+Nasal}
  [p t k b d g s l r] {-Nasal}
  [l] {+Lat}
  [r] {-Lat}
  [p b m] {+Lab -Dor}
  [t d s n l r] {-Lab -Dor}
  [k g] {-Lab +Dor}
  [a e i o u] {+Syll}
  [i u] {+High}
  [a e o] {-High}
  [e i] {-Back -Lab}
  [a] {+Back -Lab}
  [o u] {+Back +Lab}
]]
V === {+Syll}
C === {-Syll}
"""

NOUN = """$Noun := Paradigm [[
  Nom.Sg : $_ - [us]
  Gen.Sg : $_ - [i]
  Acc.Sg : $_ - [um]   /:L- {!Neut}
  Nom.Pl : $_ - [i]
  Dat.Pl : $_ - [is]
  Loc.Sg : <N: $_ > - [i]   /:L+ {!Place}
]]
$Verb := Paradigm [[
  Inf : $_ - [are]
  Pres.1Sg : $_ - [o]
]]
"""


def script(rules, **kw):
    body = "\n".join("  " + x for x in rules.strip("\n").split("\n"))
    return yasc.loads(PHON + NOUN + "$R := Rules [[\n" + body + "\n]]\n", **kw)


def forms(res):
    return [(v.text, v.label) for v in res.outputs]


class ParadigmTests(unittest.TestCase):
    def test_latin_noun(self):
        res = script("!paradigm $Noun").apply("lup")
        # Gen.Sg and Nom.Pl are identical, so they merge (spec §8.11).
        self.assertEqual(forms(res), [("lup+us", "Nom.Sg"), ("lup+i", "Gen.Sg|Nom.Pl"), ("lup+um", "Acc.Sg"),
                                      ("lup+is", "Dat.Pl")])
        self.assertEqual(res.text.splitlines()[0], "lup\tlup+us\tNom.Sg")

    def test_following_rules_apply_to_every_cell(self):
        res = script("!paradigm $Noun\n[m] --> 0 / ___ #\n[u] --> [o] / ___ [s] #").apply("lup")
        self.assertEqual([v.text for v in res.outputs], ["lup+os", "lup+i", "lup+u", "lup+is"])

    def test_cell_restrictions(self):
        res = script("!paradigm $Noun").apply("templ", features="!Neut !Place")
        self.assertEqual([v.label for v in res.outputs], ["Nom.Sg", "Gen.Sg|Nom.Pl", "Dat.Pl", "Loc.Sg"])
        self.assertEqual(res.outputs[-1].text, "<N:templ>+i")

    def test_brackets_feed_cyclic_rules(self):
        res = script("!paradigm $Noun\n0 --> [e] / C ___ # /:C* /:C+ N").apply("templ", features="!Place")
        self.assertEqual(dict((v.label, v.text) for v in res.outputs)["Loc.Sg"], "temple+i")
        self.assertEqual(res.outputs[0].text, "templ+us")

    def test_tagged_records_expand_without_a_command(self):
        rt = script("[m] --> [n] / ___ #").runtime
        tagged, plain = read_records(["#! form paradigm", "lup\tNoun", "lup\t"])
        self.assertEqual([v.text for v in rt.run(tagged).outputs], ["lup+us", "lup+i", "lup+un", "lup+is"])
        self.assertEqual([v.text for v in rt.run(plain).outputs], ["lup"])

    def test_default_paradigm_option(self):
        c = script("[m] --> [n] / ___ #").compiled
        rec, = read_records(["am"])
        res = Runtime(c, paradigm="Verb").run(rec)
        self.assertEqual(forms(res), [("am+are", "Inf"), ("am+o", "Pres.1Sg")])

    def test_tag_selects_among_paradigm_commands(self):
        rt = script("!paradigm $Noun\n!paradigm $Verb").runtime
        rec, = read_records(["#! form paradigm", "am\tVerb"])
        self.assertEqual([v.label for v in rt.run(rec).outputs], ["Inf", "Pres.1Sg"])

    def test_unknown_tag_is_a_record_error(self):
        rt = script("[m] --> [n]").runtime
        rec, = read_records(["#! form paradigm", "lup\tAdj"])
        res = rt.run(rec)
        self.assertIn("paradigm $Adj", str(res.error))

    def test_dialect_restricted_cells(self):
        sc = yasc.loads(PHON + "$N := Paradigm [[\n  Nom : $_ - [us]\n  Voc : $_ - [e] /:D+ A\n]]\n"
                        "!dialects (A B)\n$R := Rules [[\n  !paradigm $N\n]]\n")
        res = sc.apply("lup")
        self.assertEqual([(v.text, v.label, v.dialect) for v in res.outputs],
                         [("lup+us", "Nom", "A"), ("lup+e", "Voc", "A"), ("lup+us", "Nom", "B")])


class BuildTests(unittest.TestCase):
    def test_build_cell_and_concatenate(self):
        sc = script("[m] --> [n]")
        o = sc.orthography
        cell = sc.compiled.paradigms["Noun"].cells[-1]
        f = build_cell(cell, o.parse("lu+p"))
        self.assertEqual(o.render(f), "<N:lu+p>+i")
        self.assertEqual(f.brackets, (Bracket("N", 0, 3),))
        self.assertEqual(o.render(concatenate([o.parse("lu"), o.parse("p")], [Mark.MORPHEME])), "lu+p")


class ExampleTests(unittest.TestCase):
    def test_examples_paradigm(self):
        from tests.test_cli import run_main
        d = "examples/paradigm/"
        status, out, err = run_main([d + "declension.yasc", d + "lexicon.tsv"])
        self.assertEqual(status, 0, err)
        with open(os.path.join(ROOT, d, "expected.out"), encoding="utf-8") as fh:
            self.assertEqual(out, fh.read())


if __name__ == "__main__":
    unittest.main()
