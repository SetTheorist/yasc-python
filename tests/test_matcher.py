"""Tests for yasc.matcher: anchored and unanchored matching, contexts, visibility,
captures, back-references, ordering, pre-filter, ret.py regressions and performance
(spec §5.2, §6.1, §6.3, §6.4, §8.2, §8.5; design §6, §8)."""

import os
import random
import time
import unittest
from unittest import mock

import yasc.matcher as matcher_mod
from yasc.marks import Bracket, Mark
from yasc.matcher import Visibility, find_all, match_anchored, match_context
from yasc.nfa import compile_pattern
from yasc.pattern import (
    CLOSE,
    OPEN,
    Alt,
    Anything,
    BackRef,
    Boundary,
    BracketAssert,
    Capture,
    Nothing,
    Opt,
    Ortho,
    Plus,
    Seq,
    Spec,
    Star,
    number_lhs,
    split_at_locus,
)
from yasc.pattern import Locus
from yasc.segment import EMPTY, Env, OpVar, SegmentSpec, Var

from tests.fakeform import FakeForm, make_inventory, make_system, spec


def reverse_pattern(p):
    """The mirror image of a pattern (test helper for backward == forward-on-reversed)."""
    if isinstance(p, Seq):
        return Seq(tuple(reverse_pattern(x) for x in reversed(p.items)))
    if isinstance(p, Ortho):
        return Ortho(tuple(reversed(p.specs)))
    if isinstance(p, Alt):
        return Alt(p.id, tuple(reverse_pattern(x) for x in p.items))
    if isinstance(p, (Opt, Star, Plus)):
        return type(p)(reverse_pattern(p.pattern))
    if isinstance(p, Capture):
        return Capture(p.n, reverse_pattern(p.pattern))
    if isinstance(p, BracketAssert):
        # Mirroring the *form* turns an opening bracket edge into a closing one.
        return BracketAssert(CLOSE if p.kind == OPEN else OPEN, p.label)
    return p


class Base(unittest.TestCase):
    def setUp(self):
        self.fs = make_system()
        self.inv = make_inventory(self.fs)
        self.V = Spec(spec(self.fs, "+Syll"))
        self.C = Spec(spec(self.fs, "-Syll"))
        self.ANY = Spec(spec(self.fs))

    def form(self, text, brackets=()):
        return FakeForm.parse(text, self.inv, brackets)

    def ends(self, pattern, form, gap, env=EMPTY, **kw):
        return [g for g, _ in match_anchored(compile_pattern(pattern), form, gap, env, **kw)]

    def spans(self, pattern, form, **kw):
        nfa = compile_pattern(pattern)
        return [(i, j) for i, j, _ in find_all(nfa, form, EMPTY, **kw)]

    def lit(self, ch, strict=False):
        return Spec(SegmentSpec.from_segment(self.inv[ch], strict=strict))


class ConstructTests(Base):
    """Every construct of the spec §6.1 table (P8 elements excluded)."""

    def test_spec(self):
        f = self.form("pa")
        self.assertEqual(match_anchored(compile_pattern(self.V), f, 1, EMPTY), [(2, EMPTY)])
        self.assertEqual(self.ends(self.V, f, 0), [])
        self.assertEqual(self.ends(self.V, f, 2), [])
        self.assertEqual(self.spans(self.ANY, self.form("pat")), [(0, 1), (1, 2), (2, 3)])

    def test_strict_spec(self):
        """'S: every feature identical (spec §6.1)."""
        strict_i = self.lit("i", strict=True)
        loose_i = self.lit("i")
        f = self.form("iu")
        self.assertEqual(self.spans(strict_i, f), [(0, 1)])
        self.assertEqual(self.spans(loose_i, f), [(0, 1), (1, 2)])  # u = i + Labial

    def test_combined(self):
        """S1:S2 — one segment satisfying both (spec §6.1)."""
        both = Spec(self.V.spec.combine(spec(self.fs, "+High")))
        self.assertEqual(self.spans(both, self.form("aip")), [(1, 2)])

    def test_ortho(self):
        o = Ortho((SegmentSpec.from_segment(self.inv["t"]), SegmentSpec.from_segment(self.inv["a"])), text="ta")
        self.assertEqual(self.spans(o, self.form("patata")), [(2, 4), (4, 6)])

    def test_nothing(self):
        f = self.form("pa")
        self.assertEqual(match_anchored(compile_pattern(Nothing()), f, 1), [(1, EMPTY)])
        self.assertEqual(self.spans(Nothing(), f), [(0, 0), (1, 1), (2, 2)])

    def test_bracket_assert(self):
        f = self.form("pata", [Bracket("N", 1, 3)])
        self.assertEqual(self.spans(Seq((BracketAssert(OPEN, "N"), self.V)), f), [(1, 2)])
        self.assertEqual(self.spans(Seq((self.C, BracketAssert(CLOSE, "N"))), f), [(2, 3)])
        self.assertEqual(self.spans(Seq((BracketAssert(OPEN, "*"), self.ANY)), f), [(1, 2)])
        self.assertEqual(self.spans(Seq((BracketAssert(OPEN, "V"), self.ANY)), f), [])

    def test_seq(self):
        self.assertEqual(self.spans(Seq((self.C, self.V)), self.form("patai")), [(0, 2), (2, 4)])

    def test_alt(self):
        p = Alt(1, (Seq((self.C, self.V)), self.V))
        self.assertEqual(self.spans(p, self.form("pai")), [(0, 2), (1, 2), (2, 3)])

    def test_opt(self):
        self.assertEqual(self.ends(Seq((self.V, Opt(self.C))), self.form("an"), 0), [2, 1])

    def test_star(self):
        self.assertEqual(self.ends(Star(self.C), self.form("ptka"), 0), [3, 2, 1, 0])

    def test_plus(self):
        self.assertEqual(self.ends(Plus(self.C), self.form("ptka"), 0), [3, 2, 1])
        self.assertEqual(self.ends(Plus(self.C), self.form("ptka"), 3), [])

    def test_anything(self):
        self.assertEqual(self.ends(Seq((self.C, Anything(), self.V)), self.form("pataki"), 0), [6, 4, 2])
        self.assertEqual(self.ends(Anything(), self.form("pa-ta"), 1), [4, 3, 2, 1])

    def test_backref_and_capture(self):
        p = number_lhs(Seq((self.ANY, BackRef(1))))
        res = list(find_all(compile_pattern(p), self.form("atta"), EMPTY))
        self.assertEqual([(i, j) for i, j, _ in res], [(1, 3)])
        self.assertEqual(res[0][2].caps, {0: (1, 3), 1: (1, 2), 2: (2, 3)})  # $1 is itself item 2


class BoundaryTests(Base):
    """Spec §5.2 boundary strength; gap 0 and gap n carry the phrase mark."""

    def setUp(self):
        super().setUp()
        # gaps: 1 syllable, 2 morpheme, 3 clitic, 4 word
        self.f = FakeForm([self.inv[c] for c in "patak"],
                          {1: [Mark.SYLLABLE], 2: [Mark.MORPHEME], 3: [Mark.CLITIC], 4: [Mark.WORD]})

    def gaps_for(self, mark):
        return [i for i, _ in self.spans_of(Boundary(mark))]

    def spans_of(self, p):
        return [(i, j) for i, j, _ in find_all(compile_pattern(p), self.f, EMPTY)]

    def test_strength(self):
        self.assertEqual(self.gaps_for(Mark.SYLLABLE), [0, 1, 4, 5])   # '.' does not match '-' or '='
        self.assertEqual(self.gaps_for(Mark.MORPHEME), [0, 2, 3, 4, 5])
        self.assertEqual(self.gaps_for(Mark.CLITIC), [0, 3, 4, 5])
        self.assertEqual(self.gaps_for(Mark.WORD), [0, 4, 5])          # '#' at gap 0 and gap n
        self.assertEqual(self.gaps_for(Mark.PHRASE), [0, 5])

    def test_dot_not_on_morpheme_only_gap(self):
        f = self.form("pa-ta")
        self.assertEqual(self.ends(Seq((self.V, Boundary(Mark.SYLLABLE), self.C)), f, 1), [])
        self.assertEqual(self.ends(Seq((self.V, Boundary(Mark.MORPHEME), self.C)), f, 1), [3])

    def test_word_at_edges(self):
        f = self.form("pata")
        p = Seq((Boundary(Mark.WORD), self.C))
        self.assertEqual(self.spans(p, f), [(0, 1)])
        self.assertEqual(self.spans(Seq((self.V, Boundary(Mark.WORD))), f), [(3, 4)])
        self.assertEqual(self.ends(Boundary(Mark.WORD), f, 4, direction=-1), [4])

    def test_skipping(self):
        """Segment patterns cross gaps whatever marks they carry (spec §6.1 Skipping)."""
        p = Seq((self.C, self.V, self.C, self.V))
        for text in ("pata", "pa-ta", "pa.ta", "pa=ta", "pa ta", "p-a.t=a"):
            with self.subTest(text=text):
                self.assertEqual(self.spans(p, self.form(text)), [(0, 4)])


class BackwardTests(Base):
    """Backward matching equals forward matching on the reversed form (design §5.2)."""

    def test_backward_equals_forward_on_reversed(self):
        fs = self.fs
        V, C = self.V, self.C
        H = Spec(spec(fs, "+Syll (a)High"))
        N = Spec(spec(fs, "+Nasal"))
        pa = Ortho((SegmentSpec.from_segment(self.inv["p"]), SegmentSpec.from_segment(self.inv["a"])))
        patterns = [
            Seq((C, V)),
            Seq((Star(C), V, Opt(C))),
            Seq((Plus(V), Boundary(Mark.MORPHEME), C)),
            Alt(1, (Seq((C, V)), C, V)),
            Seq((H, Anything(), H)),
            Seq((Capture(1, Seq((C, Opt(V)))), Star(self.ANY))),
            Seq((Boundary(Mark.WORD), Star(C), V)),
            Seq((pa, Opt(Boundary(Mark.SYLLABLE)))),
            Star(Alt(2, (C, Seq((V, V))))),
            Seq((BracketAssert(OPEN, "N"), C, Star(self.ANY), BracketAssert(CLOSE, "N"))),
            Seq((Capture(3, Alt(4, (N, V))), Opt(Boundary(Mark.CLITIC)), Capture(5, Plus(self.ANY)))),
            Seq((Capture(1, C), V, BackRef(1))),
        ]
        rng = random.Random(1234)
        letters = "ptkmnaiueo"
        marks = [Mark.SYLLABLE, Mark.MORPHEME, Mark.CLITIC, Mark.WORD]
        forms = []
        for _ in range(40):
            n = rng.randint(0, 7)
            segs = [self.inv[rng.choice(letters)] for _ in range(n)]
            gm = {g: [rng.choice(marks)] for g in range(1, n) if rng.random() < 0.3}
            brs = []
            if n >= 2 and rng.random() < 0.5:
                a = rng.randint(0, n - 1)
                brs.append(Bracket("N", a, rng.randint(a + 1, n)))
            vis = None
            if n and rng.random() < 0.4:
                vis = tuple(rng.random() < 0.7 for _ in range(n))
            forms.append((FakeForm(segs, gm, brs), vis))
        checked = 0
        for p in patterns:
            nfa = compile_pattern(p)
            rnfa = compile_pattern(reverse_pattern(p))
            for f, vis in forms:
                n = f.n
                rf = f.reversed()
                rvis = None if vis is None else tuple(reversed(vis))
                for g in range(n + 1):
                    back = match_anchored(nfa, f, g, EMPTY, direction=-1, visible=vis)
                    fwd = match_anchored(rnfa, rf, n - g, EMPTY, visible=rvis)
                    mapped = [(n - e, Env.of(vars=env.vars, alts=env.alts,
                                             caps={k: (n - j, n - i) for k, (i, j) in env.caps.items()}))
                              for e, env in fwd]
                    self.assertEqual(back, mapped, "%s at gap %d" % (p.canonical(), g))
                    checked += len(back)
        self.assertGreater(checked, 100)

    def test_backward_capture_spans(self):
        nfa = compile_pattern(Capture(5, Seq((self.C, self.V))))
        res = match_anchored(nfa, self.form("pata"), 4, EMPTY, direction=-1)
        self.assertEqual(res, [(2, Env.of(caps={5: (2, 4)}))])

    def test_reversed_nfa_may_be_supplied(self):
        nfa = compile_pattern(Seq((self.C, self.V)))
        f = self.form("pata")
        self.assertEqual(match_anchored(nfa.reversed(), f, 4, direction=-1),
                         match_anchored(nfa, f, 4, direction=-1))
        self.assertEqual(match_anchored(nfa.reversed(), f, 0), match_anchored(nfa, f, 0))
        with self.assertRaises(ValueError):
            match_anchored(nfa, f, 0, direction=0)
        with self.assertRaises(ValueError):
            match_anchored(nfa, f, 5)


class VisibilityTests(Base):
    """Spec §8.5: invisible segments are skipped; skipped boundaries are unioned."""

    def vis(self, f, keep):
        return tuple(bool(keep(f.seg(k))) for k in range(f.n))

    def vowels(self, f):
        return self.vis(f, lambda s: s.get("Syll") == "+")

    def test_skip_invisible(self):
        f = self.form("pataki")
        vis = self.vowels(f)
        self.assertEqual(self.ends(Seq((self.V, self.V)), f, 1, visible=vis), [4])   # tight end gap
        self.assertEqual(self.ends(Seq((self.V, self.V)), f, 1), [])
        self.assertEqual(self.ends(self.V, f, 3, direction=-1, visible=vis), [1])

    def test_union_of_marks(self):
        V = self.V
        for text in ("pa-taki", "pat-aki", "pa-t.aki"):
            with self.subTest(text=text):
                f = self.form(text)
                vis = self.vowels(f)
                self.assertEqual(self.ends(Seq((V, Boundary(Mark.MORPHEME), V)), f, 1, visible=vis), [4])
                self.assertEqual(self.ends(Seq((V, Boundary(Mark.WORD), V)), f, 1, visible=vis), [])
                self.assertEqual(self.ends(Seq((V, Boundary(Mark.MORPHEME), V)), f, 1), [])
        # '#' at the form edge seen through an invisible initial consonant
        f = self.form("pa")
        self.assertEqual(self.spans(Seq((Boundary(Mark.WORD), self.V)), f, visible=self.vowels(f)), [(1, 2)])

    def test_brackets_unioned(self):
        f = self.form("pataki", [Bracket("N", 2, 6)])
        vis = self.vowels(f)
        p = Seq((self.V, BracketAssert(OPEN, "N"), self.V))
        self.assertEqual(self.ends(p, f, 1, visible=vis), [4])
        self.assertEqual(self.ends(p, f, 1), [])

    def test_find_all_one_start_per_virtual_gap(self):
        f = self.form("pataki")
        vis = self.vowels(f)
        self.assertEqual(self.spans(self.V, f, visible=vis), [(1, 2), (3, 4), (5, 6)])
        self.assertEqual(self.spans(Nothing(), f, visible=vis), [(1, 1), (3, 3), (5, 5), (6, 6)])
        self.assertEqual(self.spans(Nothing(), f, visible=vis, direction=-1),
                         [(6, 6), (5, 5), (3, 3), (1, 1)])

    def test_backref_compares_visible_segments(self):
        f = self.form("apkpa")
        vis = self.vis(f, lambda s: s is not self.inv["k"])
        # Visible string: a p (k) p a.  C $1 finds p..p across the hidden k.
        q = number_lhs(Seq((self.C, BackRef(1))))
        # (V C) $1 = "ap" then "pa": the copy is compared in order, so no match.
        p = number_lhs(Seq((Seq((self.V, self.C)), BackRef(1))))
        self.assertEqual(self.spans(q, f), [])
        self.assertEqual(self.spans(q, f, visible=vis), [(1, 4)])
        self.assertEqual(self.spans(p, f, visible=vis), [])

    def test_visibility_object_and_errors(self):
        f = self.form("pat")
        self.assertIsNone(Visibility.of(f, (True, True, True)))
        v = Visibility(3, (False, True, False))
        self.assertEqual(v.virtual_gap(0), (0, 1))
        self.assertEqual(v.virtual_gap(3), (2, 3))
        self.assertEqual(v.start_gaps(), [1, 3])
        self.assertIs(Visibility.of(f, v), v)
        self.assertEqual(self.ends(self.V, f, 0, visible=v), [2])
        with self.assertRaises(ValueError):
            match_anchored(compile_pattern(self.V), f, 0, visible=(True,))


class CaptureBackrefTests(Base):
    """Spec §6.4: captures as spans; $n is an exact copy (strict Segment equality)."""

    def test_multi_segment_backref(self):
        p = number_lhs(Seq((Seq((self.C, self.V)), BackRef(1))))
        res = list(find_all(compile_pattern(p), self.form("atatak"), EMPTY))
        self.assertEqual([(i, j, e.cap(1)) for i, j, e in res], [(1, 5, (1, 3))])

    def test_backref_is_strict(self):
        # i and u agree on every feature the spec mentions, but are different segments.
        p = number_lhs(Seq((self.V, BackRef(1))))
        self.assertEqual(self.spans(p, self.form("iu")), [])
        self.assertEqual(self.spans(p, self.form("ii")), [(0, 2)])

    def test_zero_length_backref(self):
        p = number_lhs(Seq((Opt(self.C), BackRef(1), self.V)))
        res = list(find_all(compile_pattern(p), self.form("a"), EMPTY))
        self.assertEqual([(i, j, e.cap(1)) for i, j, e in res], [(0, 1, (0, 0))])

    def test_unbound_backref_fails(self):
        self.assertEqual(self.ends(Seq((BackRef(3), self.V)), self.form("a"), 0), [])

    def test_backref_in_left_context(self):
        """{-Syll} --> ... / $1 ___ : legal, since the LHS is matched first (spec §6.4, A4)."""
        lhs = compile_pattern(number_lhs(self.C))
        left, right = split_at_locus(Seq((BackRef(1), Locus())))
        c_rev = compile_pattern(left).reversed()
        f = self.form("atta")
        hits = []
        for i, j, env in find_all(lhs, f, EMPTY, first_specs=lhs.first_specs):
            if list(match_context(c_rev, None, f, i, j, env)):
                hits.append((i, j))
        self.assertEqual(hits, [(2, 3)])

    def test_multi_segment_backref_in_left_context_keeps_order(self):
        """$0 ___ compares the span in textual order, not mirrored."""
        lhs = compile_pattern(number_lhs(Seq((self.C, self.V))))
        c_rev = compile_pattern(BackRef(0)).reversed()
        for text, expected in (("tata", [(2, 4)]), ("atta", [])):
            f = self.form(text)
            hits = [(i, j) for i, j, env in find_all(lhs, f, EMPTY)
                    if next(match_context(c_rev, None, f, i, j, env), None) is not None]
            self.assertEqual(hits, expected, text)


class OrderingTests(Base):
    """Spec §6.3: by start gap in direction, then end gap (longest first), then preference."""

    def test_longest_first_then_preference(self):
        p = Alt(1, (self.C, Seq((self.C, self.V)), self.ANY))
        res = match_anchored(compile_pattern(p), self.form("pa"), 0, EMPTY)
        self.assertEqual([(g, e.alt(1)) for g, e in res], [(2, 1), (1, 0), (1, 2)])

    def test_greedy_preference_within_same_end(self):
        p = Seq((Capture(1, Star(self.C)), Capture(2, Star(self.C))))
        res = match_anchored(compile_pattern(p), self.form("ppa"), 0, EMPTY)
        self.assertEqual([(g, e.cap(1), e.cap(2)) for g, e in res], [
            (2, (0, 2), (2, 2)), (2, (0, 1), (1, 2)), (2, (0, 0), (0, 2)),
            (1, (0, 1), (1, 1)), (1, (0, 0), (0, 1)),
            (0, (0, 0), (0, 0))])

    def test_find_all_order_and_direction(self):
        p = Seq((self.C, Opt(self.V)))
        f = self.form("patk")
        self.assertEqual(self.spans(p, f), [(0, 2), (0, 1), (2, 3), (3, 4)])
        self.assertEqual(self.spans(p, f, direction=-1), [(3, 4), (2, 3), (0, 2), (0, 1)])

    def test_disjunction_indices(self):
        p = Alt(7, (self.V, Spec(spec(self.fs, "+High")), self.C))
        res = match_anchored(compile_pattern(p), self.form("i"), 0, EMPTY)
        self.assertEqual([(g, e.alts) for g, e in res], [(1, {7: 0}), (1, {7: 1})])
        # Inside a repetition the last iteration's index is kept.
        q = Star(Alt(8, (self.C, self.V)))
        res = match_anchored(compile_pattern(q), self.form("pa"), 0, EMPTY)
        self.assertEqual([(g, e.alt(8)) for g, e in res], [(2, 1), (1, 0), (0, None)])
        # Nested disjunctions record both ids (class correspondence, spec §8.2.4).
        r = Alt(1, (Alt(2, (self.C, self.V)),))
        (res,) = match_anchored(compile_pattern(r), self.form("a"), 0, EMPTY)
        self.assertEqual(res[1].alts, {1: 0, 2: 1})


class VariableTests(Base):
    """Spec §6.2, §6.3: bindings, op-variables, D bound then C checked (§8.2)."""

    def test_variables_agree(self):
        H = Spec(spec(self.fs, "+Syll (a)High"))
        p = Seq((H, self.C, H))
        self.assertEqual(self.spans(p, self.form("itu")), [(0, 3)])
        self.assertEqual(self.spans(p, self.form("ita")), [])
        (res,) = match_anchored(compile_pattern(p), self.form("ete"), 0, EMPTY)
        self.assertEqual(res[1].vars, {"a": "-"})
        self.assertEqual(self.ends(p, self.form("ete"), 0, EMPTY.bind_var("a", "+")), [])

    def test_op_variable_preimages(self):
        """An unbound op-variable yields one Env per preimage, in value order (spec §6.2)."""
        maxlen = Spec(SegmentSpec(self.fs, [OpVar(self.fs.feature("Len"), "n", ("<Max>",))]))
        res = match_anchored(compile_pattern(maxlen), self.form("o"), 0, EMPTY)
        self.assertEqual([e.var("n") for _, e in res], ["0", "1", "2", "3"])
        again = Spec(SegmentSpec(self.fs, [Var(self.fs.feature("Len"), "n")]))
        res = match_anchored(compile_pattern(Seq((maxlen, again))), self.form("oi"), 0, EMPTY)
        self.assertEqual([(g, e.var("n")) for g, e in res], [(2, "1")])

    def test_variable_bound_in_d_checked_in_c(self):
        """C ___ D: D is matched first, so a variable bound in D is checked in C (spec §8.2)."""
        H = Spec(spec(self.fs, "+Syll (a)High"))
        c_rev = compile_pattern(H).reversed()
        d = compile_pattern(H)
        f = self.form("iti")
        self.assertEqual(list(match_context(c_rev, d, f, 1, 2, EMPTY)), [Env.of(vars={"a": "+"})])
        self.assertEqual(list(match_context(c_rev, d, self.form("ite"), 1, 2, EMPTY)), [])
        # Order: D-major. D yields alt 0 then alt 1; C yields alt 0 then alt 1.
        both = Alt(5, (self.V, Spec(spec(self.fs, "+High"))))
        both_c = Alt(6, (self.V, Spec(spec(self.fs, "+High"))))
        got = list(match_context(compile_pattern(both_c), compile_pattern(both), f, 1, 2, EMPTY))
        self.assertEqual([(e.alt(5), e.alt(6)) for e in got], [(0, 0), (0, 1), (1, 0), (1, 1)])

    def test_op_variable_in_context(self):
        maxlen = Spec(SegmentSpec(self.fs, [OpVar(self.fs.feature("Len"), "n", ("<Max>",))]))
        again = Spec(SegmentSpec(self.fs, [Var(self.fs.feature("Len"), "n")]))
        # D = {<Max>(n)Len} on o gives four Envs; C = {(n)Len} on i keeps n = 1.
        got = list(match_context(compile_pattern(again), compile_pattern(maxlen), self.form("ipo"), 1, 2, EMPTY))
        self.assertEqual([e.var("n") for e in got], ["1"])


class ContextTests(Base):
    """match_context helper (spec §8.2)."""

    def test_empty_sides_and_dedup(self):
        f = self.form("pata")
        self.assertEqual(list(match_context(None, None, f, 1, 2, EMPTY)), [EMPTY])
        d = compile_pattern(Seq((self.C, Star(self.ANY))))  # open right end: 3 ends, 1 env
        self.assertEqual(list(match_context(None, d, f, 1, 2, EMPTY)), [EMPTY])
        c = compile_pattern(Seq((Boundary(Mark.WORD), self.C)))
        self.assertEqual(list(match_context(c, d, f, 1, 2, EMPTY)), [EMPTY])
        self.assertEqual(list(match_context(c, d, f, 3, 4, EMPTY)), [])

    def test_context_with_visibility(self):
        f = self.form("pataki")
        vis = tuple(f.seg(k).get("Syll") == "+" for k in range(f.n))
        v = compile_pattern(self.V)
        # focus = the a at 3; neighbours through the invisible consonants
        self.assertEqual(list(match_context(v, v, f, 3, 4, EMPTY, vis)), [EMPTY])
        self.assertEqual(list(match_context(v, v, f, 3, 4, EMPTY)), [])

    def test_lazy(self):
        f = self.form("pata")
        gen = match_context(None, compile_pattern(Star(self.ANY)), f, 0, 0, EMPTY)
        self.assertEqual(next(gen), EMPTY)


class PrefilterTests(Base):
    """Design §6: first_specs pre-filter skips start gaps without changing results."""

    def test_same_results_fewer_runs(self):
        f = self.form("pa-takimu")
        patterns = [Seq((self.C, self.V)), Seq((Boundary(Mark.MORPHEME), self.C)), Seq((Opt(self.C), self.V)),
                    number_lhs(Alt(1, (self.lit("k"), self.lit("m"))))]
        for p in patterns:
            nfa = compile_pattern(p)
            with self.subTest(p=p.canonical()):
                plain = list(find_all(nfa, f, EMPTY))
                calls = []
                real = matcher_mod._run

                def counting(*a, **kw):
                    calls.append(a[2])
                    return real(*a, **kw)
                with mock.patch.object(matcher_mod, "_run", counting):
                    filtered = list(find_all(nfa, f, EMPTY, first_specs=nfa.first_specs))
                self.assertEqual(plain, filtered)
                self.assertLess(len(calls), f.n + 1)

    def test_prefilter_with_variables_uses_env(self):
        H = Spec(spec(self.fs, "+Syll (a)High"))
        nfa = compile_pattern(H)
        f = self.form("ia")
        self.assertEqual([(i, j) for i, j, _ in find_all(nfa, f, EMPTY.bind_var("a", "-"),
                                                          first_specs=nfa.first_specs)], [(1, 2)])

    def test_cache(self):
        nfa = compile_pattern(Seq((self.C, self.V)))
        list(find_all(nfa, self.form("pata"), EMPTY))
        self.assertTrue(any(nfa.match_cache.values()))
        nfa.clear_cache()
        self.assertFalse(any(nfa.match_cache.values()))
        self.assertEqual(self.spans(Seq((self.C, self.V)), self.form("pata")), [(0, 2), (2, 4)])


class RetPyRegressionTests(Base):
    """Regression tests for the orig-notes/ret.py defects listed in design §8."""

    def test_r2_closure_flags_do_not_leak(self):
        """R2: ret.py's epsilon_closure extended one shared list with every sibling ε-edge's
        meta flags, so the flags of alternative 0 leaked into alternative 1, and it re-queued
        states on flag-accumulating cycles. Scenario: `<< V | V >>` with captures on "a" must
        give two Envs, each with only its own alternative index and capture; and
        `((#)*)*`-style ε-cycles must terminate."""
        p = Alt(1, (Capture(1, self.V), Capture(2, self.V)))
        res = match_anchored(compile_pattern(p), self.form("a"), 0, EMPTY)
        self.assertEqual([e for _, e in res], [Env.of(caps={1: (0, 1)}, alts={1: 0}),
                                               Env.of(caps={2: (0, 1)}, alts={1: 1})])
        for q in (Star(Star(Boundary(Mark.WORD))), Star(Alt(2, (Boundary(Mark.MORPHEME), Capture(3, Nothing()))))):
            self.assertEqual(self.ends(Seq((q, self.V)), self.form("a"), 0), [1])

    def test_r3_all_edges_followed(self):
        """R3: Node.follow_match returned only the first matching out-edge. Scenario:
        `<< {+Syll} {-Syll} | {+Syll +High} {+Syll} >>` on "ia" — the first alternative's
        spec matches i but then fails, so ret.py lost the second alternative's match. Also an
        op-variable with several preimages must yield every Env."""
        p = Alt(1, (Seq((self.V, self.C)), Seq((Spec(spec(self.fs, "+Syll +High")), self.V))))
        res = match_anchored(compile_pattern(p), self.form("ia"), 0, EMPTY)
        self.assertEqual([(g, e.alt(1)) for g, e in res], [(2, 1)])
        maxlen = Spec(SegmentSpec(self.fs, [OpVar(self.fs.feature("Len"), "n", ("<Max>",))]))
        self.assertEqual(len(match_anchored(compile_pattern(maxlen), self.form("o"), 0, EMPTY)), 4)

    def test_r4_ordered_complete_results(self):
        """R4: Matcher.next_match pop()ed results (LIFO, no leftmost/longest order) and stopped
        after the first position with any match. Scenario: `V C*` on "apt" must return every end
        gap longest first, and find_all must continue past the first start gap with a match."""
        p = Seq((self.V, Star(self.C)))
        self.assertEqual(self.ends(p, self.form("apt"), 0), [3, 2, 1])
        self.assertEqual(self.spans(self.C, self.form("pata")), [(0, 1), (2, 3)])

    def test_r5_true_start_and_spans(self):
        """R5: the `{}*` "anywhere" prefix lost the real match start, and the `[`/`]` meta flags
        were overwritten along multiple ε paths. Scenario: LHS `(C) V` numbered, on "papa":
        every result reports its real start gap and Capture(0) equals (start, end)."""
        p = number_lhs(Seq((Opt(self.C), self.V)))
        res = list(find_all(compile_pattern(p), self.form("papa"), EMPTY))
        self.assertEqual([(i, j) for i, j, _ in res], [(0, 2), (1, 2), (2, 4), (3, 4)])
        for i, j, e in res:
            self.assertEqual(e.cap(0), (i, j))
            self.assertEqual(e.cap(2), (j - 1, j))
            self.assertEqual(e.cap(1), (i, j - 1))

    def test_r13_alt_and_plus(self):
        """R13: MatchTree.compile_graph called a nonexistent g.disjunct, started from an empty
        Graph() (a spurious empty alternative) and did not support '+'. Scenario: `<< C | V >>`
        must not match the empty string, and `C+` compiles and needs at least one segment."""
        f = self.form("pa")
        self.assertEqual(self.ends(Alt(0, (self.C, self.V)), f, 0), [1])
        self.assertEqual(self.ends(Alt(0, (self.C, self.V)), f, 2), [])
        nfa = compile_pattern(Alt(0, (self.C, self.V, Nothing())))
        self.assertEqual(len(nfa.edges[nfa.start]), 3)
        self.assertEqual(self.ends(Plus(self.C), self.form("pta"), 0), [2, 1])
        self.assertEqual(self.ends(Plus(self.C), self.form("a"), 0), [])


class PerformanceTests(Base):
    """Plan P2 acceptance: 10k anchored matches of a 5-element pattern on 8-segment forms < 1 s."""

    @unittest.skipIf(os.environ.get("YASC_SKIP_PERF"), "YASC_SKIP_PERF is set")
    def test_10k_anchored_matches(self):
        H = Spec(spec(self.fs, "+Syll (a)High"))
        p = number_lhs(Seq((self.C, H, Opt(self.C), H, Star(self.C))))
        nfa = compile_pattern(p)
        forms = [self.form(t) for t in ("patikupe", "tipukmai", "kanatomp", "mitiskan".replace("s", "k"),
                                        "pukitake", "nemepoti", "bidutaka", "gomaneki")]
        for f in forms:
            self.assertEqual(f.n, 8)
        total = 0
        t0 = time.perf_counter()
        for k in range(10000):
            total += len(match_anchored(nfa, forms[k % 8], 0, EMPTY))
        dt = time.perf_counter() - t0
        print("\n[perf] 10k anchored matches (5-element pattern, 8-segment forms): %.3f s, %d results" % (dt, total))
        self.assertGreater(total, 0)
        self.assertLess(dt, 1.0)


if __name__ == "__main__":
    unittest.main()
