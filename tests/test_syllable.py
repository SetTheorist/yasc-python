"""Syllabification, the syllable tier and syllable features (plan P7; spec §5.4, §5.7).

The test script below has a tiny phonology with a ``Scope(Syllable)`` Stress feature, an
orthography with ``.`` as syllable separator and ``'`` as the stress mark, and ``$S`` with
``Onset (C) Nucleus V Coda (C)``. ``show`` writes a syllabified form as syllables joined by
``.``, with unsyllabified segments in parentheses.
"""

import os
import time
import unittest

import yasc
from yasc.compile import compile_source
from yasc.errors import YascLoadError
from yasc.rules import ApplyContext, apply_rule
from yasc.syllable import CODA, NUCLEUS, ONSET, Syllabifier

HEAD = """\
$P := Phonology [[
  Syll Binary
  Son Binary
  Voice Binary
  High Binary
  Ph Scalar(0,20)
  Stress Scalar(0,2) Scope(Syllable)
]]
$O := Orthography [[
  [p t k s] {-Syll -Son -Voice}
  [b d g z] {-Syll -Son +Voice}
  [m n r l] {-Syll +Son +Voice}
  [a e o] {+Syll +Son +Voice -High}
  [i u] {+Syll +Son +Voice +High}
  [j w] {-Syll +Son +Voice +High}
  [p b] {1Ph}
  [t d] {2Ph}
  [k g] {3Ph}
  [s z] {4Ph}
  [m] {5Ph}
  [n] {6Ph}
  [r] {7Ph}
  [l] {8Ph}
  [a] {9Ph}
  [e] {10Ph}
  [o] {11Ph}
  [i j] {12Ph}
  [u w] {13Ph}
  SyllableSeparator == [.]
  SyllableMark {2Stress} == [']
]]
V === {+Syll}
C === {-Syll}
"""

SYL = "$S := Syllabification [[\n  Onset (C)\n  Nucleus V\n  Coda (C)\n%s]]\n"


def compiled(settings="", extra=""):
    """The test script with ``$S`` (plus ``settings`` lines) and ``extra`` statements."""
    return compile_source(HEAD + SYL % settings + extra, "t.yasc")


def syllabifier(settings=""):
    c = compiled(settings)
    return c, Syllabifier(c.syllabifications["S"])


def show(form, orth):
    """``a.ba.mor.di``; unsyllabified segments in parentheses, e.g. ``(s)ko.la``."""
    tier = form.syllables
    out = []
    prev = None
    for k, seg in enumerate(form.segs):
        text = orth.render_segment(seg)[0]
        s = tier.seg_to_syl[k] if tier is not None else None
        if s is None:
            out.append("(%s)" % text)
        else:
            if prev is not None and s != prev and out and not out[-1].endswith(")"):
                out.append(".")
            out.append(text)
        prev = s
    return "".join(out)


def roles(form):
    """One letter per segment: O(nset), N(ucleus), C(oda) or - (unsyllabified)."""
    letter = {ONSET: "O", NUCLEUS: "N", CODA: "C", None: "-"}
    return "".join(letter[form.syllables.role(k)] for k in range(form.n))


class MaxOnsetTests(unittest.TestCase):
    """spec §5.7 MaxOnset."""

    def syl(self, word, settings=""):
        c, s = syllabifier(settings)
        orth = c.orthographies["O"]
        return show(s.syllabify(orth.parse(word)), orth)

    def test_abamordi(self):
        self.assertEqual(self.syl("abamordi"), "a.ba.mor.di")

    def test_unsyllabified_edges(self):
        # Coda (C) takes one consonant; the rest of a final cluster stays unsyllabified.
        self.assertEqual(self.syl("pakst"), "pak(s)(t)")
        self.assertEqual(self.syl("stra"), "(s)(t)ra")

    def test_onset_required(self):
        self.assertEqual(self.syl("abarno"), "a.bar.no")          # no OnsetRequired
        self.assertEqual(self.syl("baa"), "ba.a")
        # A V-initial syllable is allowed word-initially only (spec §5.7).
        self.assertEqual(self.syl("abarno", "  OnsetRequired yes\n"), "a.bar.no")
        self.assertEqual(self.syl("baa", "  OnsetRequired yes\n"), "ba(a)")

    def test_hiatus_is_the_default(self):
        # S3: every Nucleus match is a nucleus, so V V is a hiatus under Nucleus V.
        self.assertEqual(self.syl("pia"), "pi.a")

    def test_nucleus_preference_bui(self):
        # Phonix: bui = <b::u:i> [buj] (first) or <bu::i> [bwi] (last) (spec §5.7; S3).
        text = HEAD + "$S := Syllabification [[\n  Onset (C)(V)\n  Nucleus V\n  Coda (V)(C)\n  NucleusPreference %s\n]]\n"
        for pref, expected in (("first", "ONC"), ("last", "OON")):
            c = compile_source(text % pref, "t.yasc")
            form = Syllabifier(c.syllabifications["S"]).syllabify(c.orthographies["O"].parse("bui"))
            self.assertEqual(len(form.syllables.syls), 1, pref)
            self.assertEqual(roles(form), expected, pref)

    def test_existing_marks_are_hard_boundaries(self):
        self.assertEqual(self.syl("atra"), "at.ra")
        self.assertEqual(self.syl("a.tra"), "a(t)ra")

    def test_word_and_phrase_domain(self):
        self.assertEqual(self.syl("pat a"), "pat.a")
        c, s = syllabifier("  Domain phrase\n")
        orth = c.orthographies["O"]
        self.assertEqual(show(s.syllabify(orth.parse("pat a")), orth), "pa.ta")

    def test_empty_form(self):
        c, s = syllabifier()
        self.assertEqual(s.syllabify(c.orthographies["O"].parse("")).syllables.syls, ())


class CanonTests(unittest.TestCase):
    """spec §5.7 ``Algorithm Canon`` (the notes' option 5, orig-notes/scer.txt)."""

    SETTINGS = "  Algorithm Canon\n  Canons CV > CVC > VC > V\n"

    def test_abamordi(self):
        # The notes score 1) a.bam.or.di as 1+3+2+1 = 7 and 3) a.ba.mor.di as 1+4+3+2 = 10:
        # their final "di" (a CV) is mis-scored in both sums. With the ranking of scer.txt
        # (CV 4, CVC 3, VC 2, V 1) the scores are 10 and 12; the DP picks a.ba.mor.di.
        c, s = syllabifier(self.SETTINGS)
        orth = c.orthographies["O"]
        form = orth.parse("abamordi")
        self.assertEqual(show(s.syllabify(form), orth), "a.ba.mor.di")
        score, unsyll, sylls = s.canon_parse(form)
        self.assertEqual((score, unsyll), (12, 0))
        self.assertEqual([(a, b) for a, b, _x, _y in sylls], [(0, 1), (1, 3), (3, 6), (6, 8)])
        self.assertEqual(s.canon_score(form, [(0, 1), (1, 4), (4, 6), (6, 8)]), 10)   # a.bam.or.di
        self.assertEqual(s.canon_score(form, [(0, 1), (1, 3), (3, 6), (6, 8)]), 12)   # a.ba.mor.di
        self.assertEqual(roles(s.syllabify(form)), "NONONCON")

    def test_unparsable_segments(self):
        c, s = syllabifier(self.SETTINGS)
        orth = c.orthographies["O"]
        self.assertEqual(show(s.syllabify(orth.parse("pakst")), orth), "(p)(a)(k)(s)(t)")
        c, s = syllabifier(self.SETTINGS + "  AllowUnsyllabified yes\n")
        self.assertEqual(show(s.syllabify(orth.parse("pakst")), c.orthographies["O"]), "pak(s)(t)")

    def test_canon_needs_letters(self):
        with self.assertRaises(YascLoadError):
            compile_source(HEAD.replace("C === {-Syll}\n", "") + SYL % self.SETTINGS, "t.yasc")


# ------------------------------------------------------------------------------------------
# Rules over the syllable tier (spec §5.4)
# ------------------------------------------------------------------------------------------


def script(rules, settings="", head=HEAD):
    """The test script with ``$S`` and a Rules section holding ``rules``."""
    return yasc.loads(head + SYL % settings + "$R := Rules [[\n" + rules + "\n]]\n", "t.yasc")


def out(sc, word, trace=False):
    """``(text, form, warnings)`` of the single output of ``word``."""
    res = sc.apply(word, trace=trace)
    return res.outputs[0].text, res.outputs[0].form, res.warnings


class RoleFeatureTests(unittest.TestCase):
    """Role pseudo-features (spec §5.4; notes.md §4 C2)."""

    def test_coda_devoicing(self):
        sc = script("!syllabify\n{-Son SylCoda} --> {-Voice}")
        self.assertEqual(out(sc, "badgab")[0], "batgap")      # bad.gab: codas d, b

    def test_roles_need_a_tier(self):
        sc = script("{-Son SylCoda} --> {-Voice}")               # no !syllabify: no codas
        self.assertEqual(out(sc, "badgab")[0], "badgab")

    def test_other_roles(self):
        sc = script("!syllabify\n{-Son SylOnset} --> {-Voice}\n{Syllabified -Syll +Son} --> {-Voice}")
        text, form, _ = out(sc, "bagzl")
        self.assertEqual(roles(form), "ONC--")
        self.assertEqual(text, "pagzl")

    def test_c2_role_features_resolve_but_are_read_only(self):
        c = compile_source(HEAD + "{SylNucleus} --> {+High} / {SylOnset} ___", "t.yasc")
        self.assertEqual(c.warnings, [])
        for rule in ("V --> {SylCoda}", "V --> {_Syllabified}", "V --> ~{SylOnset}"):
            with self.assertRaises(YascLoadError) as cm:
                compile_source(HEAD + rule, "t.yasc")
            self.assertIn("read-only role pseudo-feature", cm.exception.errors[0].message, rule)

    def test_role_features_are_not_printed(self):
        c = compile_source(HEAD, "t.yasc")
        fs = c.phonologies["P"]
        self.assertIn("SylCoda", [f.name for f in fs.features])
        self.assertNotIn("SylCoda", fs.canonical())


class SyllableFeatureTests(unittest.TestCase):
    """``Scope(Syllable)`` values: stored on the syllable, seen by its segments."""

    def test_write_then_read_through_segments(self):
        # Stress is written on the vowel and read on the consonants of the same syllable.
        sc = script("!syllabify\nV --> {2Stress} / # C* ___\n{-Syll 2Stress} --> {+Voice}")
        text, form, _ = out(sc, "patak")
        self.assertEqual(text, "'batak")
        self.assertEqual([s.feats.get("Stress") for s in form.syllables.syls], ["2", None])
        self.assertIsNone(form.segs[1].get("Stress"))           # not stored on the segment

    def test_default_is_unspecified(self):
        # S4: a new syllable has Stress unspecified, so {0Stress} fails and {_Stress} holds.
        sc = script("!syllabify\n{-Syll 0Stress} --> {+Voice}")
        self.assertEqual(out(sc, "pata")[0], "pata")
        sc = script("!syllabify\n{-Syll _Stress} --> {+Voice}")
        self.assertEqual(out(sc, "pata")[0], "bada")

    def test_write_to_unsyllabified_is_a_noop(self):
        sc = script("V --> {2Stress}")
        text, form, warnings = out(sc, "pa", trace=True)
        self.assertEqual((text, form.syllables), ("pa", None))
        self.assertTrue(any("unsyllabified" in w for w in warnings), warnings)
        self.assertEqual(out(sc, "pa")[2], [])                  # warnings only in trace mode

    def test_implications_may_not_mention_syllable_features(self):
        head = HEAD.replace("]]\n$O", "  {2Stress} --> {+Voice}\n]]\n$O", 1)
        with self.assertRaises(YascLoadError) as cm:
            compile_source(head, "t.yasc")
        self.assertIn("implication may not mention Stress", cm.exception.errors[0].message)

    def test_syllable_mark_sets_only_syllable_features(self):
        head = HEAD.replace("  SyllableMark", "  SyllableMark {+Voice} == [^]\n  SyllableMark", 1)
        with self.assertRaises(YascLoadError):
            compile_source(head, "t.yasc")


class StressMarkTests(unittest.TestCase):
    """``SyllableMark`` parsing, consumption by syllabification and rendering (spec §5.6)."""

    def setUp(self):
        self.c, self.s = syllabifier()
        self.orth = self.c.orthographies["O"]

    def roundtrip(self, text):
        form = self.s.syllabify(self.orth.parse(text))
        shown = self.orth.render(form)
        again = self.s.syllabify(self.orth.parse(shown))
        self.assertEqual(self.orth.render(again), shown)
        self.assertEqual(again.syllables, form.syllables)
        return form, shown

    def test_marks_round_trip(self):
        form, shown = self.roundtrip("pa'ta")
        self.assertEqual(shown, "pa'ta")
        self.assertEqual(form.pending_syllable_marks, ())
        self.assertEqual([s.feats.get("Stress") for s in form.syllables.syls], [None, "2"])
        self.assertEqual(self.roundtrip("'pa.ta")[1], "'pa.ta")
        self.assertEqual(self.roundtrip("pata")[1], "pata")

    def test_mark_before_unsyllabified_material(self):
        # The mark goes to the first syllable at or after its gap and is written before it.
        # A mark at a word edge stores no explicit boundary (the edge implies one), so no
        # spurious "." is written back (design §13 entry 126).
        form, shown = self.roundtrip("'stra")
        self.assertEqual(shown, "st'ra")
        self.assertEqual(roles(form), "--ON")
        self.assertEqual(self.orth.parse("'stra").gaps[0], frozenset())
        self.assertEqual(self.roundtrip("pa 'stra")[1], "pa st'ra")

    def test_runtime_output_has_marks(self):
        sc = script("!syllabify")
        self.assertEqual(out(sc, "pa'ta")[0], "pa'ta")


class DotAssertionTests(unittest.TestCase):
    """The ``.`` assertion matches derived syllable edges (spec §5.4)."""

    def test_derived_edges(self):
        sc = script("!syllabify\nC --> {+Voice} / . ___")
        text, form, _ = out(sc, "patka")                     # pat.ka
        self.assertEqual(text, "batga")                      # derived edges are not written
        self.assertEqual(sorted(g for g in range(form.n + 1) if form.gap_marks(g) & {yasc.marks.Mark.SYLLABLE}),
                         [0, 3, 5])
        sc = script("C --> {+Voice} / . ___")               # no tier: only the form edge
        self.assertEqual(out(sc, "patka")[0], "batka")


class UpkeepTests(unittest.TestCase):
    """spec §5.4 "Upkeep after rules" (not Persistent)."""

    def run_(self, rules, word, settings=""):
        text, form, _ = out(script("!syllabify\n" + rules, settings), word)
        return text, form.syllables.spans(), roles(form)

    def test_deletion(self):
        self.assertEqual(self.run_("[t] --> 0", "pata"), ("paa", ((0, 2), (2, 3)), "ONN"))

    def test_orphans_become_a_coda(self):
        # S2 (CĪVITĀTEM): pa.ta.ka, the middle a is deleted; "tk" is no Onset, so t is a coda.
        self.assertEqual(self.run_("[a] --> 0 / [t] ___ [k]", "pataka"), ("patka", ((0, 3), (3, 5)), "ONCON"))

    def test_orphans_prefer_the_next_onset(self):
        # S2 (SENIŌREM): pa.ri.a, i is deleted; r forms an Onset with the empty onset of a.
        self.assertEqual(self.run_("[i] --> 0", "paria"), ("para", ((0, 2), (2, 4)), "ONON"))

    def test_orphans_without_room_are_unsyllabified(self):
        # pat.ka.ta: deleting the first a of "kata"... here "tk" fits neither template.
        self.assertEqual(self.run_("[a] --> 0 / [k] ___", "patkata"), ("patkta", ((0, 3), (4, 6)), "ONC-ON"))

    def test_insertion(self):
        self.assertEqual(self.run_("[a] --> [a] [i]", "pa"), ("pai", ((0, 3),), "ONN"))    # joins the nucleus
        self.assertEqual(self.run_("0 --> [s] / [t] ___", "pat"), ("pats", ((0, 4),), "ONCC"))
        # prothesis: no syllabified neighbour in the word, so it stays unsyllabified
        self.assertEqual(self.run_("0 --> [e] / # ___ [s]", "sta")[2], "--ON")
        # across a word boundary the left neighbour is not used
        self.assertEqual(self.run_("0 --> [s] / # ___ [t]", "pa ta")[2], "ONOON")

    def test_metathesis_keeps_positions(self):
        # V C --> $2 $1 rewrites each position by a copy (design §13 entry 88): the tier
        # keeps its positions and roles until it is recomputed.
        self.assertEqual(self.run_("V C --> $2 $1", "pat"), ("pta", ((0, 3),), "ONC"))

    def test_feature_change_keeps_stress(self):
        rules = "V --> {2Stress} / ___ C* #\n[k] --> [a]"
        self.assertEqual(self.run_(rules, "pakta"), ("paa'ta", ((0, 3), (3, 5)), "ONCON"))


class ResyllabifyTests(unittest.TestCase):
    """``/:$``, ``Persistent`` and ``!syllabify`` (spec §5.4, §5.7, §8.7, §10.3)."""

    def test_resyllabify_modifier(self):
        rules = "!syllabify\nV C --> $2 $1\n{SylOnset} --> {+Voice} %s"
        self.assertEqual(out(script(rules % ""), "pat")[0], "bta")        # positional: p is the onset
        self.assertEqual(out(script(rules % "/:$"), "pat")[0], "pda")     # recomputed: p.ta -> t is

    def test_persistent(self):
        rules = "!syllabify\nV C --> $2 $1"
        self.assertEqual(roles(out(script(rules), "pat")[1]), "ONC")
        self.assertEqual(roles(out(script(rules, "  Persistent yes\n"), "pat")[1]), "-ON")

    def test_persistent_keeps_stress(self):
        # S1: the recomputed syllables inherit Stress through their nucleus.
        rules = "!syllabify\nV --> {2Stress} / ___ C* #\n[k] --> [a]"
        text, form, _ = out(script(rules, "  Persistent yes\n"), "pakta")
        self.assertEqual((text, roles(form)), ("paa'ta", "ONNON"))

    def test_syllabify_selects_a_definition(self):
        other = "$T := Syllabification [[\n  Onset\n  Nucleus V\n  Coda (C)(C)\n]]\n"
        head = HEAD + SYL % "" + other
        rules = "{SylOnset} --> {+Voice}"

        def run(cmds):
            return yasc.loads(head + "$R := Rules [[\n" + cmds + rules + "\n]]\n", "t.yasc").apply("pata").text

        self.assertEqual(run("!syllabify\n"), "pata\tpata\n")              # $T: the last defined
        self.assertEqual(run("!syllabify $S\n"), "pata\tbada\n")
        self.assertEqual(run("!use $S\n!syllabify\n"), "pata\tbada\n")

    def test_no_syllabification(self):
        sc = yasc.loads(HEAD + "$R := Rules [[\n  !syllabify\n]]\n", "t.yasc")
        with self.assertRaises(yasc.errors.YascRuntimeError):
            sc.apply("pa")


@unittest.skipIf(os.environ.get("YASC_SKIP_PERF"), "YASC_SKIP_PERF is set")
class PerformanceTests(unittest.TestCase):
    """Plan P7: syllabifying 1,000 short words takes well under a second."""

    def test_thousand_words(self):
        c, s = syllabifier()
        orth = c.orthographies["O"]
        words = [orth.parse(w) for w in ("abamordi", "pataka", "stra", "kiwitatem", "baa", "badgab",
                                          "sepetem", "paria") * 125]
        t0 = time.perf_counter()
        for w in words:
            s.syllabify(w)
        elapsed = time.perf_counter() - t0
        self.assertLess(elapsed, 1.0, "1,000 words took %.2f s" % elapsed)
