"""Plan P8: autosegmental tiers (spec §5.5, §6.5; design §11).

The first classes test :mod:`yasc.tiers` with Python constructors; the later ones run
scripts (patterns, RHS link operations, tier-only rules, commands, orthography)."""

import os
import subprocess
import sys
import unittest

from yasc.features import FeatureSystem, FeatureType, TierDecl
from yasc.form import Form
from yasc.tiers import Auto, AutoTier, TierCrossing, TierView, decompose, levels, ncc_ok

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def system(stray="float", ocp="off"):
    fs = FeatureSystem()
    fs.add_feature("Syll", FeatureType.binary())
    tone = fs.add_feature("Tone", FeatureType.enum(["H", "L", "M", "HL", "LH"]), tier=TierDecl(None, stray, ocp))
    fs.seal()
    return fs, tone


class LevelTests(unittest.TestCase):
    """Spec §5.5: contour values are sequences of level values (P8 decision 3)."""

    def test_levels_and_decompose(self):
        lv = levels(("H", "L", "M", "HL", "LH", "HM"))
        self.assertEqual(lv, ("H", "L", "M"))
        self.assertEqual(decompose("HL", lv), ("H", "L"))
        self.assertEqual(decompose("H", lv), ("H",))
        self.assertEqual(decompose("X", lv), ("X",))
        self.assertEqual(levels(("Hi", "Lo")), ("Hi", "Lo"))


class NoCrossingTests(unittest.TestCase):
    """Spec §5.5 No-Crossing Constraint; design §11."""

    def setUp(self):
        self.fs, self.tone = system()

    def test_ncc_ok(self):
        h, l = Auto(0, "H", 0), Auto(1, "L", 1)
        self.assertTrue(ncc_ok([h, l], [(0, 0), (1, 1), (1, 0)]))
        self.assertFalse(ncc_ok([h, l], [(0, 1), (1, 0)]))

    def test_constructor_rejects_crossing(self):
        with self.assertRaises(TierCrossing):
            AutoTier(self.tone, 3, [Auto(0, "H", 0), Auto(1, "L", 1)], [(1, 0), (0, 1)])

    def test_linking_across_a_line_fails(self):
        t = AutoTier(self.tone, 4).write(1, "H").write(2, "L")
        h = t.linked(1)[0].id
        with self.assertRaises(TierCrossing):
            t.link(3, [h])
        # A contour: H spreading to the left of L's segment is fine.
        self.assertEqual(t.link(0, [h]).value(0), "H")

    def test_spreading_the_second_part_of_a_contour_leftward_crosses(self):
        t = AutoTier(self.tone, 3).write(1, "HL")
        l = t.linked(1)[1].id
        with self.assertRaises(TierCrossing):
            t.link(0, [l])
        self.assertEqual(t.link(2, [l]).value(2), "L")

    def test_add_link_order(self):
        t = AutoTier(self.tone, 2).write(0, "H")
        self.assertEqual(t.link_new(0, ["L"], replace=False).value(0), "HL")


class SegmentViewTests(unittest.TestCase):
    """Spec §5.5 segment view, writing through the view and Stray."""

    def setUp(self):
        self.fs, self.tone = system()

    def test_view_values(self):
        t = AutoTier(self.tone, 3).write(0, "HL").write(2, "M")
        self.assertEqual([t.value(k) for k in range(3)], ["HL", None, "M"])
        self.assertEqual([a.value for a in t.autos], ["H", "L", "M"])

    def test_write_makes_strays_float(self):
        t = AutoTier(self.tone, 3).write(1, "H").write(1, "L")
        self.assertEqual(t.value(1), "L")
        self.assertEqual([(a.value, a.anchor) for a in t.floating()], [("H", 1)])

    def test_write_makes_strays_vanish_with_stray_delete(self):
        _fs, tone = system(stray="delete")
        t = AutoTier(tone, 3).write(1, "H").write(1, "L")
        self.assertEqual([a.value for a in t.autos], ["L"])
        self.assertEqual(AutoTier(tone, 3).write(1, "H").write(1, None).autos, ())

    def test_spread_autosegment_survives_a_write_elsewhere(self):
        t = AutoTier(self.tone, 3).write(0, "H")
        t = t.link(1, [t.linked(0)[0].id])
        t2 = t.write(1, "L")
        self.assertEqual((t2.value(0), t2.value(1)), ("H", "L"))
        self.assertEqual(t2.floating(), ())

    def test_form_view_reads_tier_features(self):
        fs, tone = self.fs, self.tone
        v = fs.segment(Syll="+")
        t = AutoTier(tone, 2).write(1, "H")
        form = Form((v, v), (frozenset(),) * 3, (), None, (t,))
        view = form.view(1)
        self.assertIsInstance(view, TierView)
        self.assertEqual(view[tone.index], "H")
        self.assertEqual(view.get("Tone"), "H")
        self.assertIsNone(form.view(0)[tone.index])
        self.assertEqual(view.get("Syll"), "+")
        self.assertIs(form.tier(), t)
        self.assertIs(form.tier("Tone"), t)
        # Forms without tiers keep the plain view (no new slow path).
        self.assertNotIsInstance(Form.from_segments((v,)).view(0), TierView)

    def test_equality_ignores_ids(self):
        a = AutoTier(self.tone, 2).write(0, "H")
        b = AutoTier(self.tone, 2).write(1, "L").write(1, None).write(0, "H")
        self.assertNotEqual([x.id for x in a.autos], [x.id for x in b.autos])
        self.assertEqual(a, b.delete([x.id for x in b.floating()]))


class OcpTests(unittest.TestCase):
    """Spec §5.5 OCP: merge or delete adjacent identical autosegments."""

    def setUp(self):
        self.fs, self.tone = system()
        self.t = AutoTier(self.tone, 3).write(0, "H").write(2, "H")

    def test_merge(self):
        t = self.t.ocp("merge")
        self.assertEqual(len(t.autos), 1)
        self.assertEqual(t.segs_of(t.autos[0].id), (0, 2))

    def test_delete(self):
        t = self.t.ocp("delete")
        self.assertEqual([t.value(k) for k in range(3)], ["H", None, None])

    def test_word_boundary_blocks_the_ocp(self):
        from yasc.marks import Mark
        gaps = (frozenset(), frozenset(), frozenset({Mark.WORD}), frozenset())
        self.assertEqual(len(self.t.ocp("merge", gaps).autos), 2)
        self.assertIs(self.t.ocp("off"), self.t)
        self.assertIs(AutoTier(self.tone, 3).write(0, "H").write(1, "L").ocp("merge").value(1), "L")


class UpkeepTests(unittest.TestCase):
    """Design §4.3 step 5: links follow Form.replace; P8 decision 6: whole-focus remap."""

    def setUp(self):
        self.fs, self.tone = system()
        self.v = self.fs.segment(Syll="+")
        self.c = self.fs.segment(Syll="-")

    def form(self, segs, tier):
        return Form(tuple(segs), (frozenset(),) * (len(segs) + 1), (), None, (tier,))

    def test_deletion_insertion(self):
        t = AutoTier(self.tone, 4).write(1, "H").write(3, "L")
        f = self.form([self.c, self.v, self.c, self.v], t)
        g = f.replace(0, 1, ())                                   # delete the first consonant
        self.assertEqual([g.tiers[0].value(k) for k in range(3)], ["H", None, "L"])
        g = f.replace(2, 2, (self.c,))                            # insert before the 2nd consonant
        self.assertEqual([g.tiers[0].value(k) for k in range(5)], [None, "H", None, None, "L"])
        g = f.replace(1, 2, ())                                   # delete the H vowel: H floats
        self.assertEqual([(a.value, a.anchor) for a in g.tiers[0].floating()], [("H", 1)])

    def test_remap_follows_moved_segments(self):
        t = AutoTier(self.tone, 3).write(0, "H").write(2, "L")
        t2 = t.remap(3, {0: [2], 1: [1], 2: [0]}, lambda g: g)   # metathesis of the vowels
        self.assertEqual([t2.value(k) for k in range(3)], ["L", None, "H"])
        self.assertEqual([a.value for a in t2.autos], ["L", "H"])
        t3 = t.remap(4, {0: [0, 1], 1: [2], 2: [3]}, lambda g: g + (g > 0))  # gemination
        self.assertEqual([t3.value(k) for k in range(4)], ["H", "H", None, "L"])
        self.assertEqual(len(t3.autos), 2)


# --------------------------------------------------------------------------------------------
# Scripts
# --------------------------------------------------------------------------------------------

from yasc import tiers as tiers_mod  # noqa: E402
from yasc.compile import compile_source  # noqa: E402
from yasc.errors import YascLoadError  # noqa: E402
from yasc.rules import ApplyContext, run_section  # noqa: E402

HEAD = """
Phonology [[
  Syll Binary
  Nasal Binary
  High Binary
  Place [lab] [cor] [dor]
  Tone [H] [L] [M] [HL] Tier(TBU={+Syll}, Stray=%s, OCP=%s)
]]
Orthography [[
  [p t k] {-Syll -Nasal}
  [m n] {-Syll +Nasal}
  [p m] {[lab]Place}
  [t n] {[cor]Place}
  [k] {[dor]Place}
  [a i] {+Syll -Nasal}
  [a] {-High}
  [i] {+High}
  {[H]Tone} ==> [#']
  {[L]Tone} ==> [#`]
  {[M]Tone} ==> [#-]
  {[HL]Tone} ==> [#~]
]]
V === {+Syll}
C === {-Syll}
"""


def compiled(rules, stray="float", ocp="off"):
    return compile_source(HEAD % (stray, ocp) + "$R := Rules [[\n" + rules + "\n]]\n", "t.yasc")


def on_command(ir, form, ctx):
    if getattr(ir, "name", None) in ("associate", "ocp"):
        return tiers_mod.command(ir, form)
    return None


def run(c, word):
    orth = c.orthography
    out = run_section(c, "R", orth.parse(word), ApplyContext(on_command=on_command)).form
    return orth.render(out)


def derive(rules, word, **kw):
    c = compiled(rules, **kw)
    return run(c, word)


class AssociationTests(unittest.TestCase):
    """Spec §5.5 !associate: the Association Convention."""

    def test_left_to_right_last_tone_spreads(self):
        self.assertEqual(derive("!associate Tone", "^'^`tamaka"), "ta'ma`ka`")

    def test_right_to_left(self):
        self.assertEqual(derive("!associate Tone dir=<", "^'^`tamaka"), "ta'ma'ka`")

    def test_no_spreading(self):
        self.assertEqual(derive("!associate Tone spread=none", "^'^`tamaka"), "ta'ma`ka")

    def test_leftover_tones_keep_floating_in_order(self):
        c = compiled("!associate Tone")
        f = c.orthography.parse("^'^`^'ta")
        out = run_section(c, "R", f, ApplyContext(on_command=on_command)).form
        t = out.tier("Tone")
        self.assertEqual([a.value for a in t.autos], ["H", "L", "H"])
        self.assertEqual(c.orthography.render(out), "ta'^`^'")

    def test_word_by_word(self):
        # A floating tone at a word-boundary gap belongs to the word before it (P8 decision 11).
        self.assertEqual(derive("!associate Tone", "^'tama ka^`"), "ta'ma' ka`")
        self.assertEqual(derive("!associate Tone", "^'tama ^`ka"), "ta'ma` ka")


class DockingAndFloatTests(unittest.TestCase):
    """Spec §6.5: ^X in patterns, ^[H] and ^=h --> 0 on the RHS."""

    def test_floating_h_docks(self):
        self.assertEqual(derive("V^0 --> V^=h / ^[H]=h C* ___", "^'tama"), "ta'ma")

    def test_delete_floating_after_h(self):
        self.assertEqual(derive("^[H]=h --> 0 / V^[H] ___", "ta'^'ma^'"), "ta'ma^'")

    def test_insert_floating(self):
        self.assertEqual(derive("0 --> ^[H] / V ___ #", "tama"), "tama^'")

    def test_relabel_floating(self):
        self.assertEqual(derive("^[H] --> ^[L]", "ta^'"), "ta^`")


class OcpScriptTests(unittest.TestCase):
    """Spec §5.5 OCP setting and !ocp."""

    def test_ocp_merge_and_delete_commands(self):
        c = compiled("!ocp Tone merge")
        out = run_section(c, "R", c.orthography.parse("ta'ma'"), ApplyContext(on_command=on_command)).form
        self.assertEqual(len(out.tier().autos), 1)
        self.assertEqual(c.orthography.render(out), "ta'ma'")
        self.assertEqual(derive("!ocp Tone delete", "ta'ma'"), "ta'ma")

    def test_ocp_setting_applies_after_rules(self):
        c = compiled("V --> {[H]Tone} / ___ #", ocp="merge")
        out = run_section(c, "R", c.orthography.parse("ta'ma"), ApplyContext()).form
        self.assertEqual(len(out.tier().autos), 1)
        self.assertEqual(c.orthography.render(out), "ta'ma'")


class TierRuleTests(unittest.TestCase):
    """Spec §6.5 tier-only rules (/:T)."""

    def test_downstep(self):
        self.assertEqual(derive("[H] --> [M] / [H] ___ /:T Tone", "ta'ma'ka'"), "ta'ma-ka-")

    def test_deleting_and_inserting_on_the_tier(self):
        self.assertEqual(derive("[L] --> 0 / [H] ___ /:T Tone", "ta'ma`ka'"), "ta'maka'")
        self.assertEqual(derive("0 --> [L] / [H] ___ # /:T Tone", "ta'"), "ta'^`")

    def test_variables_on_the_tier(self):
        self.assertEqual(derive("[M] --> (a) / (a) ___ /:T Tone", "ta`ma-"), "ta`ma`")


class LinkRuleTests(unittest.TestCase):
    """Spec §6.5 link notation, the No-Crossing Constraint in rules, spreading."""

    def test_crossing_is_rejected(self):
        self.assertEqual(derive("V^0 --> V^=h / V^*=h C V C ___", "ta'ma`ka"), "ta'ma`ka")
        self.assertEqual(derive("V^0 --> V^=h / V^*=h C ___", "ta'ma`ka"), "ta'ma`ka`")

    def test_spreading_iteratively(self):
        self.assertEqual(derive("V^0 --> V^=h / V^*=h C* ___ /*", "ta'makapa"), "ta'ma'ka'pa'")
        c = compiled("V^0 --> V^=h / V^*=h C* ___ /*")
        out = run_section(c, "R", c.orthography.parse("ta'makapa"), ApplyContext()).form
        self.assertEqual(len(out.tier().autos), 1)

    def test_link_forms(self):
        self.assertEqual(derive("V^[H]' --> V^+[L] / ___ #", "ta'"), "ta~")
        self.assertEqual(derive("V^[H]' --> {[L]Tone}", "ta~ ma'"), "ta~ m^'a`")
        self.assertEqual(derive("V^[L]=h --> V^-=h", "ta~"), "ta'^`")
        self.assertEqual(derive("V --> V^0 / ___ #", "ta'", stray="delete"), "ta")
        self.assertEqual(derive("V^0 --> V^[L] / ___ #", "ta'ma"), "ta'ma`")
        self.assertEqual(derive("V^0 --> V^(a) / V^(a) C ___", "ta-ma"), "ta-ma-")


class SegmentViewScriptTests(unittest.TestCase):
    """Spec §5.5: ordinary feature rules read and write the segment view."""

    def test_read_and_write(self):
        self.assertEqual(derive("V:{[H]Tone} --> {[L]Tone} / ___ #", "ta'ma'", stray="delete"), "ta'ma`")
        self.assertEqual(derive("V:{[HL]Tone} --> {[H]Tone}", "ta~", stray="delete"), "ta'")
        self.assertEqual(derive("{_Tone} --> {[M]Tone} / C ___", "ta'ma"), "ta'ma-")
        self.assertEqual(derive("V --> ~{[L]Tone}", "ta'ma"), "ta'ma`")

    def test_stray_float_versus_delete(self):
        self.assertEqual(derive("V:{[H]Tone} --> {[L]Tone} / ___ #", "ta'ma'"), "ta'm^'a`")
        self.assertEqual(derive("V --> 0 / ___ #", "tama'"), "tam^'")
        self.assertEqual(derive("V --> 0 / ___ #", "tama'", stray="delete"), "tam")


class LinksFollowSegmentsTests(unittest.TestCase):
    """Design §4.3 step 5 and P8 decision 6: deletion, insertion, metathesis, copies."""

    def test_deletion_and_insertion(self):
        self.assertEqual(derive("C --> 0 / V ___ V", "ta'ma`"), "ta'a`")
        self.assertEqual(derive("0 --> [k] / V ___ V", "ta'a`"), "ta'ka`")

    def test_metathesis(self):
        self.assertEqual(derive("V C V --> $3 $2 $1", "ta'mi`"), "ti`ma'")
        self.assertEqual(derive("V C --> $2 $1 / ___ #", "ta'm"), "tma'")

    def test_copies_share_their_autosegment(self):
        c = compiled("V --> $1 $1 / ___ #")
        out = run_section(c, "R", c.orthography.parse("ta'"), ApplyContext()).form
        self.assertEqual(c.orthography.render(out), "ta'a'")
        self.assertEqual(len(out.tier().autos), 1)


class OrthographyTests(unittest.TestCase):
    """Spec §5.5 "Orthography": tone diacritics and floating tones round-trip."""

    def test_round_trips(self):
        orth = compiled("").orthography
        for text in ["^'ta", "ta^'", "ta'^`", "pa' ta^'", "ta~", "ta'ma`", "ta^' ma", "^-^`"]:
            f = orth.parse(text)
            self.assertEqual(orth.render(f), text)
            self.assertEqual(orth.parse(orth.render(f)), f)

    def test_parse_builds_links(self):
        orth = compiled("").orthography
        f = orth.parse("ta~ma")
        t = f.tier("Tone")
        self.assertEqual([a.value for a in t.autos], ["H", "L"])
        self.assertEqual(t.value(1), "HL")
        self.assertIsNone(f.segs[1].get("Tone"))      # the value lives on the tier
        self.assertEqual(f.view(1).get("Tone"), "HL")

    def test_bad_floating_text(self):
        from yasc.orthography import UnparsableError
        with self.assertRaises(UnparsableError):
            compiled("").orthography.parse("ta^")


class CompileTests(unittest.TestCase):
    """Spec §6.5 static checks."""

    def test_errors(self):
        for rule in ["V^[X] --> 0", "V --> V^[X]", "V^+[L] --> V"]:
            with self.assertRaises(YascLoadError, msg=rule):
                compiled(rule)
        two = HEAD.replace("]]\nOrthography", "  T2 [a] [b] Tier(TBU={+Syll})\n]]\nOrthography", 1)
        with self.assertRaises(YascLoadError):
            compile_source(two % ("float", "off") + "V^[H] --> 0\n", "t.yasc")
        compile_source(two % ("float", "off") + "V^Tone.[H] --> V^T2.[a]\n", "t.yasc")

    def test_appendix_b(self):
        path = os.path.join(ROOT, "examples", "revised-example.yasc")
        with open(path, encoding="utf-8") as fh:
            c = compile_source(fh.read(), path)
        self.assertEqual(c.warnings, [])
        orth = c.orthography
        out = run_section(c, "R", orth.parse("ta'mima"), ApplyContext()).form
        t = out.tier("Tone")
        self.assertEqual([t.value(k) for k in range(out.n)], [None, "H", None, "H", None])
        self.assertEqual(len(t.autos), 1)                 # one H spread over both vowels


class ToneExampleTests(unittest.TestCase):
    """Plan P8: examples/tone runs through the CLI and matches its expected output."""

    def test_cli(self):
        script = os.path.join("examples", "tone", "tone.yasc")
        lexicon = os.path.join("examples", "tone", "lexicon.tsv")
        proc = subprocess.run([sys.executable, "-m", "yasc", script, lexicon], cwd=ROOT, stdout=subprocess.PIPE,
                              stderr=subprocess.PIPE)
        self.assertEqual(proc.returncode, 0, proc.stderr)
        with open(os.path.join(ROOT, "examples", "tone", "expected.out"), encoding="utf-8") as fh:
            self.assertEqual(proc.stdout.decode("utf-8"), fh.read())
        self.assertEqual(proc.stderr.decode("utf-8"), "")

    def test_revised_example_runs_strictly(self):
        script = os.path.join("examples", "revised-example.yasc")
        proc = subprocess.run([sys.executable, "-m", "yasc", script, "--word", "tabi", "ta'mima"], cwd=ROOT,
                              stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertEqual(proc.stdout.decode("utf-8").splitlines()[0], "tabi > dab")


class DomainAndParadigmTests(unittest.TestCase):
    """Plan P9 interplay (P8 decisions 21, 22): tier rules inside /:C± and /:C* domains,
    and tones carried into paradigm cells."""

    def test_rules_inside_a_domain(self):
        # The H outside the N domain is invisible to the rule.
        self.assertEqual(derive("V^0 --> V^=h / V^*=h C ___ /:C+ N", "ta'<N:ma>ka"), "ta'<N:ma>ka")
        self.assertEqual(derive("V^0 --> V^=h / V^*=h C ___ /:C+ N", "<N:ta'ma>ka"), "<N:ta'ma'>ka")
        self.assertEqual(derive("^[H]=h --> 0 /:C+ N", "<N:ta^'ma>ka^'"), "<N:tama>ka^'")
        self.assertEqual(derive("0 --> ^[L] / V ___ # /:C+ N", "<N:ta'ma>ka"), "<N:ta'ma^`>ka")

    def test_metathesis_inside_a_domain(self):
        self.assertEqual(derive("V C V --> $3 $2 $1 /:C+ N", "ta<N:ka'mi`>"), "ta<N:ki`ma'>")

    def test_tier_rule_inside_a_domain(self):
        self.assertEqual(derive("[H] --> [M] / [H] ___ /:T Tone /:C+ N", "ta'<N:ma'ka'>"), "ta'<N:ma'ka->")
        self.assertEqual(derive("[H] --> [M] / [H] ___ /:T Tone", "ta'<N:ma'ka'>"), "ta'<N:ma-ka->")

    def test_cyclic_spreading(self):
        self.assertEqual(derive("V^0 --> V^=h / V^*=h C ___ /:C*", "<N:ta'<N:ma>ka>"), "ta'ma'ka'")

    def test_paradigm_cells_keep_tones(self):
        from yasc.paradigm import build_cell
        c = compile_source(HEAD % ("float", "off") + "$N := Paradigm [[\n  Nom : $_\n  Gen : $_ - [i`]\n]]\n", "t.yasc")
        orth = c.orthography
        stem = orth.parse("^'ta'ma")
        nom, gen = c.paradigms["N"].cells
        self.assertEqual(orth.render(build_cell(nom, stem)), "^'ta'ma")
        cell = build_cell(gen, stem)
        self.assertEqual(orth.render(cell), "^'ta'ma+i`")
        self.assertEqual([a.value for a in cell.tier("Tone").autos], ["H", "H", "L"])
