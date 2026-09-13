"""Tests for the rule engine (spec §8; design §10; plan P5).

The golden table compiles every rule from source text through :func:`compile_source`
with a shared phonology and orthography, runs it with :func:`run_section` and compares the
rendered output.
"""

import os
import time
import unittest

from yasc.compile import compile_source
from yasc.errors import NotImplementedYet, YascRuntimeError
from yasc.rules import (
    ApplyContext, BasicRule, Builder, Group, Outcome, RecordData, TraceStep, apply_rule, build, build_rules,
    insertion_attach, required_specs, run_section,
)

HDR = """$P := Phonology [[
  Syll Binary
  Cons Binary
  Son Binary
  Cont Binary
  Voice Binary
  Nasal Binary
  Place Node(Labial Coronal Dorsal)
  Labial Unary
  Coronal Unary
  Dorsal Unary
  High Binary
  Low Binary
  Back Binary
  Round Binary
  Long Binary
  {+Syll} --> {-Cons +Son +Cont -Nasal}
  {+Nasal} --> {+Son -Cont}
  {+High} --> {-Low}
  {+Low} --> {-High}
  {+Son} ~~> {+Voice}
]]
$O := Orthography [[
  [p t k] {-Syll +Cons -Son -Cont -Voice -Nasal}
  [b d g] {-Syll +Cons -Son -Cont +Voice -Nasal}
  [f s x] {-Syll +Cons -Son +Cont -Voice -Nasal}
  [v z] {-Syll +Cons -Son +Cont +Voice -Nasal}
  [m n N] {-Syll +Cons +Nasal}
  [l r] {-Syll +Cons +Son +Cont -Nasal}
  [j w] {-Syll -Cons +Son +Cont -Nasal +High}
  [p b f v m w] {!Labial}
  [t d s z n l r] {!Coronal}
  [k g x N] {!Dorsal}
  [i y u] {+Syll +High}
  [e o] {+Syll -High -Low}
  [a] {+Syll +Low}
  [i e y j] {-Back}
  [u o a w] {+Back}
  [i e a j] {-Round}
  [y u o w] {+Round}
  {+Long} ==> [#:]
]]
V === {+Syll}
C === {-Syll}
K === <<[p]|[t]|[k]>>
G === <<[b]|[d]|[g]>>
"""

_CACHE = {}


def section(rules, prelude=""):
    """Compile ``rules`` as the body of ``$R := Rules [[ ... ]]`` (cached)."""
    key = (prelude, rules)
    c = _CACHE.get(key)
    if c is None:
        body = "\n".join("  " + line for line in rules.strip("\n").split("\n"))
        c = _CACHE[key] = compile_source(HDR + prelude + "$R := Rules [[\n" + body + "\n]]\n")
    return c


def derive(rules, text, ctx=None, prelude=""):
    """Run section R of ``rules`` on ``text``; return the rendered output."""
    c = section(rules, prelude)
    o = c.orthography
    out = run_section(c, "R", o.parse(text), ctx or ApplyContext())
    return o.render(out.form)


# (rule text, input, expected output). Rule lines are joined with newlines by "\n".
GOLDEN = [
    # -- spec §8.3: default simultaneous mode vs /:1 ------------------------------------------
    ("[p] --> [f]", "papa", "fafa"),
    ("[p] --> [f] /:1", "papa", "fapa"),
    ("[a] --> [e] /:1 /:<", "pata", "pate"),
    ("[a] [a] --> [e]", "aaa", "ea"),
    ("[a] [a] --> [e] /:<", "aaa", "ae"),
    # -- /:* repeat versus simultaneous ------------------------------------------------------
    ("V V --> $1", "paaaa", "paa"),
    ("V V --> $1 /:*", "paaaa", "pa"),
    # -- spreading: /* versus simultaneous, both directions (spec §8.3) ----------------------
    ("[a] --> [o] / [o] ___", "poaaa", "pooaa"),
    ("[a] --> [o] / [o] ___ /*", "poaaa", "poooo"),
    ("[a] --> [o] / ___ [o] /:<", "aaop", "aoop"),
    ("[a] --> [o] / ___ [o] /* /:<", "aaop", "ooop"),
    ("[a] --> [o] / ___ [o] /*", "aaop", "aoop"),
    # syncope-like deletion: three modes give three results
    ("V --> 0 / V C ___ C V", "patakana", "patkna"),
    ("V --> 0 / V C ___ C V /*", "patakana", "patkana"),
    ("V --> 0 / V C ___ C V /* /:<", "patakana", "patakna"),
    # -- vowel harmony across consonants with /:F- (spec §8.5) -------------------------------
    ("V:{-Low} --> {(a)Back (a)Round} / V:{(a)Back} ___ /:F- V /*", "kutipe", "kutupo"),
    ("V:{-Low} --> {(a)Back (a)Round} / V:{(a)Back} ___ /:F- V", "kutipe", "kutupe"),
    ("V:{-Low} --> {(a)Back (a)Round} / ___ V:{(a)Back} /:F- V /* /:<", "kitepo", "kutopo"),
    ("V:{-Low} --> {(a)Back (a)Round} / ___ V:{(a)Back} /:F- V /:<", "kitepo", "kitopo"),
    ("[a] --> [e] / ___ [i] /:F+ C", "pakti", "pekti"),
    ("[a] --> [e] / ___ [i]", "pakti", "pakti"),
    # -- metathesis and gemination (spec §6.4) -----------------------------------------------
    ("C V --> $2 $1 / ___ #", "pata", "paat"),
    ("C V --> $2 $1 / ___ #", "ka", "ak"),
    ("C --> $1 $1 / V ___ V", "ata", "atta"),
    ("C $1 --> $1", "atta", "ata"),
    ("V C --> $1 $2 $2 / ___ V", "pata", "patta"),
    # -- epenthesis at word edges (spec §8.2.4, attach) --------------------------------------
    ("0 --> [e] / # ___ [s] C", "sta", "esta"),
    ("0 --> [e] / # ___ [s] C", "ka#sta", "ka esta"),
    ("0 --> [e] / C ___ #", "kat#pas", "kate pase"),
    ("0 --> [e] / C ___ C", "pstk", "pesetek"),
    ("0 --> [e] / C ___ C /*", "pstk", "pesetek"),
    ("0 --> {+Syll +High -Back -Round} / C ___ #", "pat", "pati"),
    ("[a] --> [a] [j] / ___ #", "pa", "paj"),
    # -- deletion and boundaries (spec §8.2.4 "Boundaries") ----------------------------------
    ("C --> 0 / ___ #", "pat#kas", "pa ka"),
    ("[a] --> 0 / ___ - [a]", "pa+ata", "p+ata"),
    ("[e] --> 0", "pa+e+ta", "pa+ta"),
    ("C+ --> 0 / ___ #", "patsk", "pa"),
    ("[a] ([j]) --> [e] / ___ #", "paj", "pe"),
    ("[a] ([j]) --> [e] / ___ #", "pa", "pe"),
    ("[a] [j] --> [e]", "paj", "pe"),
    # -- class correspondence (spec §8.2.4) --------------------------------------------------
    ("K --> G / V ___ V", "apatak", "abadak"),
    ("K --> G / V ___ V", "akapa", "agaba"),
    ("<<[s]|[f]>> --> <<[z]|[v]>> / V ___", "asafa", "azava"),
    # -- place assimilation via a Node variable (spec §4.3) ----------------------------------
    ("{+Nasal} --> {(p)Place} / ___ {-Syll (p)Place}", "anpa", "ampa"),
    ("{+Nasal} --> {(p)Place} / ___ {-Syll (p)Place}", "anka", "aNka"),
    ("{+Nasal} --> {(p)Place} / ___ {-Syll (p)Place}", "amta", "anta"),
    ("{+Nasal} --> {(p)Place} / ___ {-Syll (p)Place}", "anta", "anta"),
    # -- spec §8.4: the three equivalent formulations (single words; see FilterEquivalenceTests)
    ("V --> 0 / ___# /! #C*___", "pata", "pat"),
    ("V --> 0 / ___# /:i- #C*V#", "pata", "pat"),
    ("V --> 0 / ___# /:o+ V", "pata", "pat"),
    ("V --> 0 / ___# /! #C*___", "pa", "pa"),
    ("V --> 0 / ___# /:i- #C*V#", "pa", "pa"),
    ("V --> 0 / ___# /:o+ V", "pa", "pa"),
    # -- filters with ___ and anywhere (spec §8.4) -------------------------------------------
    ("V --> 0 /:o+ C ___ C", "pata", "pta"),
    ("[a] --> [e] /:i+ ___ [t]", "pata", "peta"),
    ("[a] --> [e] /:i+ [i]", "pata", "pata"),
    ("[a] --> [e] /:i+ [i]", "patai", "petei"),
    ("[a] --> [e] /:i- [u]", "pata", "pete"),
    ("[a] --> [e] /:i- [u]", "patu", "patu"),
    # output filters force the next candidate (spec §8.2 step 6)
    ("V --> 0 /:1", "pataki", "ptaki"),
    ("V --> 0 /:1 /:o- C C", "pataki", "patak"),
    ("V --> 0 /:o- C C", "pataki", "patak"),
    ("V --> 0 /:o- C C", "apata", "pat"),
    ("V --> 0 /* /:o- C C", "apata", "pat"),
    # -- contexts (spec §8.2 steps 2-3, §6.4) ------------------------------------------------
    ("[t] --> [s] / ___ V /! ___ [i]", "tatitu", "satisu"),
    ("[k] --> [g] / V ___ / ___ V", "akak", "agak"),
    ("[k] --> [x] / ___ << # | C >>", "akta#ak", "axta ax"),
    ("{} --> 0 / $1 ___", "atta", "ata"),
    ("{} --> 0 / $1 ___", "aaa", "a"),
    ("[a] --> [e] / $0 ___", "paa", "pae"),
    ("C --> {-(v)Voice} / ___ C:{(v)Voice}", "abda", "apda"),
    # -- boundaries in patterns (spec §5.2, §6.1) --------------------------------------------
    ("[s] --> [z] / V - ___ V", "pa+sa", "pa+za"),
    ("[s] --> [z] / V - ___ V", "pasa", "pasa"),
    ("[k] --> [x] / ___ .", "pak.ta", "pax.ta"),
    ("[k] --> [x] / ___ .", "pak", "pax"),
    ("[t] --> [d] / V ___ # V", "pat#a", "pad a"),
    ("[s] --> [z] / ___ #", "pas#pas", "paz paz"),
    ("[a] --> [e] / ___ ... [i]", "pakati", "peketi"),
    ("[a] --> [e] / ___ C* [i]", "pakati", "paketi"),
    # -- replacement, merge, weak modification (spec §8.2.4) ---------------------------------
    ("V:{+Low} --> [i]", "pa:ta", "piti"),
    ("V:{+Low} --> '[i]", "pa:ta", "piti"),
    ("V:{+Low} --> +[i]", "pa:ta", "pi:ti"),
    ("'[a] --> [e]", "pa#pa:", "pe pa:"),
    ("V:{+Long} --> {_Long}", "pa:ta", "pata"),
    ("V --> ~{+Long} / ___ #", "pa#pa:", "pa: pa:"),
    # implications fill in a changed segment (spec §4.5)
    ("[b] --> {+Nasal}", "aba", "ama"),
    # -- groups (spec §8.8) ------------------------------------------------------------------
    ("[[\n  [p] --> [f]\n  [f] --> [v]\n]]", "pa", "va"),
    ("[[\n  [p] --> [f]\n  [k] --> [x]\n]]", "pata", "fata"),
    ("&&[[\n  [p] --> [f]\n  [k] --> [x]\n]]", "paka", "faxa"),
    ("&&[[\n  [p] --> [f]\n  [k] --> [x]\n]]", "pata", "pata"),
    ("||[[\n  [p] --> [f]\n  [t] --> [s]\n]]", "pat", "fat"),
    ("||[[\n  [p] --> [f]\n  [t] --> [s]\n]]", "tat", "sas"),
    # the chain shift t > d > y > t of the notes: the first member that applies wins
    ("||[[\n  '[t] --> '[d]\n  '[d] --> '[y]\n  '[y] --> '[t]\n]]", "ta", "da"),
    ("||[[\n  '[t] --> '[d]\n  '[d] --> '[y]\n  '[y] --> '[t]\n]]", "da", "ya"),
    ("||[[\n  '[t] --> '[d]\n  '[d] --> '[y]\n  '[y] --> '[t]\n]]", "ya", "ta"),
    ("||[[\n  '[t] --> '[d]\n  '[d] --> '[y]\n  '[y] --> '[t]\n]]", "tada", "dada"),
    # inherited modifiers, group repeat, group filters
    ("[[ / ___ #\n  [a] --> [e]\n  [t] --> [d]\n]]", "pat", "pad"),
    ("[[ / ___ #\n  [a] --> [e]\n  [t] --> [d]\n]]", "pata", "pate"),
    ("[[\n  V V --> $1 /:1\n]] /:*", "paaaa", "pa"),
    ("[[\n  [p] --> [f]\n]] /:i- [k]", "pap", "faf"),
    ("[[\n  [p] --> [f]\n]] /:i- [k]", "pak", "pak"),
    ("[[\n  [a] --> 0\n]] /:o+ V", "pa", "pa"),
    ("[[\n  [a] --> 0\n]] /:o+ V", "pai", "pi"),
    # /:~ weak success on a rule and on a group (spec §8.7)
    ("&&[[\n  [a] --> [a] /:~\n  [p] --> [f]\n]]", "pa", "fa"),
    ("&&[[\n  [a] --> [a]\n  [p] --> [f]\n]]", "pa", "pa"),
    ("&&[[\n  [[\n    [x] --> [s]\n  ]] /:~\n  [p] --> [f]\n]]", "pa", "fa"),
    ("&&[[\n  [[\n    [x] --> [s]\n  ]]\n  [p] --> [f]\n]]", "pa", "pa"),
    # -- persistent rules (spec §8.9) --------------------------------------------------------
    ("{+Nasal} --> {(p)Place} / ___ C:{(p)Place} /::\n[e] --> 0 / C ___ C", "anepa", "ampa"),
    ("{+Nasal} --> {(p)Place} / ___ C:{(p)Place}\n[e] --> 0 / C ___ C", "anepa", "anpa"),
    ("[[\n  {+Nasal} --> {(p)Place} / ___ C:{(p)Place} /::\n]]\n[e] --> 0 / C ___ C", "anepa", "anpa"),
]


class GoldenDerivationTests(unittest.TestCase):
    """The golden derivation table (plan P5): rule text, input, expected output."""

    def test_table_size(self):
        self.assertGreaterEqual(len(GOLDEN), 80)

    def test_golden(self):
        for rules, text, expected in GOLDEN:
            with self.subTest(rules=rules, text=text):
                self.assertEqual(derive(rules, text), expected)


def compile_rules(rules, allow=False, prelude=""):
    """Uncached compile of a Rules section ``R`` (for pending constructs)."""
    body = "\n".join("  " + line for line in rules.strip("\n").split("\n"))
    return compile_source(HDR + prelude + "$R := Rules [[\n" + body + "\n]]\n", allow_unimplemented=allow)


def outcome(rules, text, ctx=None, prelude=""):
    c = section(rules, prelude)
    return run_section(c, "R", c.orthography.parse(text), ctx or ApplyContext())


class FilterEquivalenceTests(unittest.TestCase):
    """Spec §8.4: the three formulations are the same rule on single words."""

    RULES = ["V --> 0 / ___# /! #C*___", "V --> 0 / ___# /:i- #C*V#", "V --> 0 / ___# /:o+ V"]
    WORDS = ["pata", "pa", "a", "ata", "aa", "pai", "tri", "patak", "kapatu", "strak", "e", "sto", "aiu"]

    def test_identical_on_single_words(self):
        for w in self.WORDS:
            outs = [derive(r, w) for r in self.RULES]
            with self.subTest(word=w):
                self.assertEqual(len(set(outs)), 1, outs)

    def test_whole_form_filters_differ_on_phrases(self):
        # /:i- and /:o+ test the whole form, so on a phrase they are not equivalent.
        self.assertEqual(derive(self.RULES[0], "pata#ka"), "pat ka")
        self.assertEqual(derive(self.RULES[1], "pata#ka"), "pata ka")
        self.assertEqual(derive(self.RULES[2], "pata#ka"), "pat k")


class LimitTests(unittest.TestCase):
    """Spec §8.3, §8.9: MaxIterations and cycle detection raise YascRuntimeError."""

    def test_iterative_cap(self):
        with self.assertRaises(YascRuntimeError) as cm:
            derive("0 --> [a] /*", "p", ApplyContext({"MaxIterations": 10}))
        self.assertIn("MaxIterations", cm.exception.message)
        self.assertEqual(cm.exception.rule_id, 1)

    def test_iterative_within_cap(self):
        self.assertEqual(derive("[a] --> [o] / [o] ___ /*", "poaaa", ApplyContext({"MaxIterations": 3})), "poooo")

    def test_repeat_cycle(self):
        with self.assertRaises(YascRuntimeError) as cm:
            derive("V:{(a)Back} --> {-(a)Back} /:*", "pe")
        self.assertIn("cycle", cm.exception.message)

    def test_repeat_cap(self):
        with self.assertRaises(YascRuntimeError) as cm:
            derive("0 --> [a] / # ___ /:*", "p", ApplyContext({"MaxIterations": 5}))
        self.assertIn("MaxIterations", cm.exception.message)

    def test_group_repeat_cycle(self):
        with self.assertRaises(YascRuntimeError):
            derive("[[\n  V:{(a)Back} --> {-(a)Back} /:1\n]] /:*", "pe")

    def test_persistent_cap(self):
        with self.assertRaises(YascRuntimeError) as cm:
            derive("0 --> [a] / # ___ /::\n[p] --> [f]", "p", ApplyContext({"MaxIterations": 5}))
        self.assertIn("persistent", cm.exception.message)


class ImplicationTests(unittest.TestCase):
    """Spec §4.5, §8.2.4: implications run on changed and inserted segments, not with /:Raw."""

    def test_strong_implication_after_change(self):
        seg = outcome("[b] --> {+Nasal}", "aba").form.segs[1]
        self.assertEqual((seg.get("Nasal"), seg.get("Son"), seg.get("Cont")), ("+", "+", "-"))

    def test_raw_suppresses_implications(self):
        seg = outcome("[b] --> {+Nasal} /:Raw", "aba").form.segs[1]
        self.assertEqual((seg.get("Nasal"), seg.get("Son")), ("+", "-"))

    def test_weak_default_refilled(self):
        out = outcome("{+Son} --> {_Voice}", "ana")
        self.assertFalse(out.applied)
        self.assertEqual(out.form.segs[1].get("Voice"), "+")

    def test_weak_default_not_refilled_with_raw(self):
        out = outcome("{+Son} --> {_Voice} /:Raw", "ana")
        self.assertTrue(out.applied)
        self.assertIsNone(out.form.segs[1].get("Voice"))

    def test_inserted_segment_gets_defaults(self):
        seg = outcome("0 --> {+Syll +High -Back -Round} / C ___ #", "pat").form.segs[3]
        self.assertEqual((seg.get("Cons"), seg.get("Son"), seg.get("Voice"), seg.get("Low")), ("-", "+", "+", "-"))


class EngineSemanticsTests(unittest.TestCase):
    """Assorted spec §8 semantics that the golden table cannot show by rendering."""

    def test_spec_8_5_example(self):
        # V --> {(a)Back} / V:{(a)Back} ___ /:F- V /*: the i of kuki becomes +Back.
        out = outcome("V --> {(a)Back} / V:{(a)Back} ___ /:F- V /*", "kuki")
        self.assertEqual(out.form.segs[3].get("Back"), "+")

    def test_invisible_segments_are_kept(self):
        self.assertEqual(derive("V V --> $1 /:F- V", "pata"), "pat")

    def test_interior_boundary_keeps_its_position(self):
        self.assertEqual(derive("V C V --> $3 $2 $1", "pe+ta"), "pa+te")

    def test_prothesis_attaches_right_and_suffix_left(self):
        c = section("0 --> [e] / # ___ [s] C")
        self.assertEqual(insertion_attach(c.rules[0].contexts), "right")
        c = section("0 --> [e] / C ___ #")
        self.assertEqual(insertion_attach(c.rules[0].contexts), "left")

    def test_required_specs_and_quick_reject(self):
        c = section("[p] ([a]) [t] --> 0")
        ir = c.rules[0]
        self.assertEqual(len(required_specs(ir.lhs_pattern)), 2)
        rule = build(ir)
        o = c.orthography
        self.assertTrue(rule.quick_reject(o.parse("kaka")))
        self.assertFalse(rule.quick_reject(o.parse("pat")))
        self.assertEqual(o.render(apply_rule(rule, o.parse("pat")).form), "")

    def test_default_mode_once(self):
        ctx = ApplyContext({"DefaultMode": "once"})
        self.assertEqual(derive("[p] --> [f]", "papa", ctx), "fapa")
        self.assertEqual(derive("[p] --> [f] /*", "papa", ApplyContext({"DefaultMode": "once"})), "fafa")

    def test_lexical_restrictions(self):
        rule = "[yi] --> [i] /:L+ {!N}"
        self.assertEqual(derive(rule, "yi", ApplyContext(record=RecordData({"N": "!"}))), "i")
        self.assertEqual(derive(rule, "yi"), "yi")
        self.assertEqual(derive("[yi] --> [i] /:L- {!N}", "yi", ApplyContext(record=RecordData({"N": "!"}))), "yi")
        grp = "[[\n  [p] --> [f]\n]] /:L+ {+Romance}"
        self.assertEqual(derive(grp, "pa", ApplyContext(record=RecordData({"Romance": "+"}))), "fa")
        self.assertEqual(derive(grp, "pa", ApplyContext(record=RecordData({"Romance": "-"}))), "pa")

    def test_invoke_section_and_named_group(self):
        self.assertEqual(derive("$A\n[f] --> [v]", "pa", prelude="$A := Rules [[\n  [p] --> [f]\n]]\n"), "va")
        self.assertEqual(derive('[[\n  [p] --> [f]\n]] /" G\n[f] --> [p]\n$G', "pa"), "fa")

    def test_only_and_skip(self):
        rules = '[p] --> [f] /" PF\n[t] --> [s] /" TS'
        self.assertEqual(derive(rules, "pat", ApplyContext(skip=["PF"])), "pas")
        self.assertEqual(derive(rules, "pat", ApplyContext(only=["PF"])), "fat")

    def test_commands_go_to_the_runtime(self):
        seen = []
        ctx = ApplyContext(on_command=lambda ir, form, ctx: seen.append(type(ir).__name__))
        self.assertEqual(derive("$x := $_\n[p] --> [f]", "pa", ctx), "fa")
        self.assertEqual(seen, ["IRAssign"])

    def test_outcome_and_trace(self):
        trace = []
        out = outcome('[[\n  [p] --> [f] /" PF\n  [k] --> [x]\n]]', "pa", ApplyContext(trace=trace))
        self.assertIsInstance(out, Outcome)
        self.assertTrue(out.applied)
        self.assertEqual(len(trace), 1)
        st = trace[0]
        self.assertIsInstance(st, TraceStep)
        self.assertEqual((st.rule_id, st.name, st.foci), (1, "PF", ((0, 1),)))
        self.assertTrue(st.before.segs != st.after.segs)

    def test_weak_rule_is_traced_without_change(self):
        trace = []
        out = outcome("[a] --> [a] /:~", "pa", ApplyContext(trace=trace))
        self.assertTrue(out.applied)
        self.assertEqual(len(trace), 1)
        self.assertEqual(trace[0].before, trace[0].after)

    def test_and_undo_removes_trace(self):
        trace = []
        out = outcome("&&[[\n  [p] --> [f]\n  [k] --> [x]\n]]", "pa", ApplyContext(trace=trace))
        self.assertFalse(out.applied)
        self.assertEqual(trace, [])


class PendingTests(unittest.TestCase):
    """Plan P5: pending constructs raise NotImplementedYet unless skip_pending."""

    @staticmethod
    def _mark_pending(c, *indices, phase="PX"):
        """Plan P9: /%, /??? and !dialects no longer pend, so mark members of section R as
        pending by hand (the mechanism is phase-independent)."""
        import dataclasses
        sec = c.rule_sections["R"]
        members = tuple(dataclasses.replace(m, pending=(phase,) if isinstance(m.pending, tuple) else phase)
                        if k in indices else m for k, m in enumerate(sec.members))
        return dataclasses.replace(sec, members=members)

    def test_pending_modifier_raises(self):
        c = compile_rules("[p] --> [f]")
        with self.assertRaises(NotImplementedYet) as cm:
            run_section(c, self._mark_pending(c, 0), c.orthography.parse("pa"), ApplyContext())
        self.assertEqual(cm.exception.phase, "PX")

    def test_pending_modifier_skipped_and_traced(self):
        c = compile_rules("[p] --> [f]\n[t] --> [s]")
        trace = []
        ctx = ApplyContext(skip_pending=True, trace=trace)
        out = run_section(c, self._mark_pending(c, 0), c.orthography.parse("pat"), ctx)
        self.assertEqual(c.orthography.render(out.form), "pas")
        self.assertEqual(ctx.skipped, [(1, None, "PX")])
        self.assertIn("PX", trace[0].note)

    def test_pending_group_and_command(self):
        # P7 and P9: !syllabify, /% and !dialects no longer pend; the group and the command
        # are marked pending by hand.
        c = compile_rules("[[\n  [p] --> [f]\n]] /%50\n!dialects (A B)")
        sec = self._mark_pending(c, 0, 1)
        with self.assertRaises(NotImplementedYet):
            run_section(c, sec, c.orthography.parse("pa"), ApplyContext())
        ctx = ApplyContext(skip_pending=True)
        run_section(c, sec, c.orthography.parse("pa"), ctx)
        self.assertEqual([p for _i, _n, p in ctx.skipped], ["PX", "PX"])

    def test_placeholder(self):
        from yasc.ir import IRPlaceholder
        rule = build(IRPlaceholder("P8", "tier rule", name="T", id=7))
        c = section("[p] --> [f]")
        with self.assertRaises(NotImplementedYet) as cm:
            apply_rule(rule, c.orthography.parse("pa"))
        self.assertEqual(cm.exception.phase, "P8")
        out = apply_rule(rule, c.orthography.parse("pa"), ApplyContext(skip_pending=True))
        self.assertFalse(out.applied)

    def test_build_rules(self):
        c = section("[p] --> [f]\n[[\n  [t] --> [s]\n]]")
        rules = build_rules(c)
        self.assertEqual(sorted(rules), [1, 2])
        self.assertTrue(all(isinstance(r, BasicRule) for r in rules.values()))
        b = Builder()
        self.assertIs(b.build(c.rule_sections["R"]), b.build(c.rule_sections["R"]))
        self.assertIsInstance(b.build(c.rule_sections["R"]), Group)


@unittest.skipIf(os.environ.get("YASC_SKIP_PERF"), "YASC_SKIP_PERF is set")
class PerformanceTests(unittest.TestCase):
    """Plan P6 acceptance, engine only: 1,000 forms x 100 simple rules in 10 s or less."""

    def test_1000_forms_100_rules(self):
        import random
        cons = "ptkbdgfsxvzmnNlr"
        vows = "aeiou"
        ctxs = ["/ V ___ V", "/ ___ #", "/ # ___", "/ ___ C", "/ C ___", ""]
        rules = []
        i = 0
        while len(rules) < 90:
            a, b = cons[i % 16], cons[(i * 7 + 3) % 16]
            if a != b:
                rules.append("[%s] --> [%s] %s" % (a, b, ctxs[i % len(ctxs)]))
            i += 1
        for k in range(10):
            rules.append("[%s] --> [%s] / ___ C* [%s]" % (vows[k % 5], vows[(k + 1) % 5], vows[(k + 2) % 5]))
        c = section("\n".join(rules))
        rng = random.Random(1234)
        words = []
        for _ in range(1000):
            w = "".join(rng.choice(cons) + rng.choice(vows) for _ in range(rng.randint(2, 4)))
            words.append(w + (rng.choice(cons) if rng.random() < 0.5 else ""))
        forms = [c.orthography.parse(w) for w in words]
        ctx = ApplyContext()
        t0 = time.perf_counter()
        for f in forms:
            run_section(c, "R", f, ctx)
        elapsed = time.perf_counter() - t0
        print("\n[perf] rule engine: 1000 forms x %d rules in %.2f s" % (len(rules), elapsed))
        self.assertLessEqual(elapsed, 10.0)
