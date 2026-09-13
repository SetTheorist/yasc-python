"""Tests for yasc.nfa: Thompson construction, edge order, closures, reversal, predicates
(design §5.2; spec §5.2, §5.3)."""

import unittest

from yasc.errors import NotImplementedYet, YascDefinitionError
from yasc.marks import Bracket, Mark
from yasc.nfa import (
    ALT,
    ASSERT,
    BACKREF,
    CAP_CLOSE,
    CAP_OPEN,
    EPS,
    SEG,
    BoundaryPred,
    BracketPred,
    compile_pattern,
)
from yasc.pattern import (
    CLOSE,
    OPEN,
    Alt,
    Anything,
    AutoFloat,
    BackRef,
    Boundary,
    BracketAssert,
    Capture,
    Linked,
    Locus,
    Macro,
    Nothing,
    Opt,
    Plus,
    Seq,
    Spec,
    Star,
)

from tests.fakeform import FakeForm, make_inventory, make_system, spec


class Base(unittest.TestCase):
    def setUp(self):
        self.fs = make_system()
        self.inv = make_inventory(self.fs)
        self.V = Spec(spec(self.fs, "+Syll"))
        self.C = Spec(spec(self.fs, "-Syll"))

    def kinds(self, nfa):
        return sorted({e.kind for es in nfa.edges for e in es})


class ConstructionTests(Base):
    """Design §5.2: Thompson construction with ordered ε-edges."""

    def test_edge_kinds(self):
        p = Seq((Capture(1, self.C), Alt(4, (self.V, Boundary(Mark.WORD))), BackRef(1),
                 BracketAssert(OPEN, "N")))
        nfa = compile_pattern(p)
        self.assertEqual(self.kinds(nfa), [EPS, SEG, ASSERT, CAP_OPEN, CAP_CLOSE, ALT, BACKREF])
        payloads = [e.payload for es in nfa.edges for e in es if e.kind == ALT]
        self.assertEqual(payloads, [(4, 0), (4, 1)])
        preds = [e.payload for es in nfa.edges for e in es if e.kind == ASSERT]
        self.assertEqual(preds, [BoundaryPred(Mark.WORD), BracketPred(OPEN, "N")])

    def test_alt_without_id_records_nothing(self):
        nfa = compile_pattern(Alt(None, (self.V, self.C)))
        self.assertNotIn(ALT, self.kinds(nfa))

    def test_star_loop_before_exit(self):
        nfa = compile_pattern(Star(self.C))
        # The loop state has two ε out-edges: into the body first, then the exit.
        loops = [u for u, es in enumerate(nfa.edges) if len(es) == 2 and all(e.kind == EPS for e in es)]
        self.assertEqual(len(loops), 1)
        body, exit_ = nfa.edges[loops[0]]
        self.assertTrue(any(e.kind == SEG for e in nfa.edges[body.target]))
        self.assertEqual(nfa.closure[nfa.start][0][0], body.target)   # greedy: body first
        self.assertEqual(nfa.closure[nfa.start][-1][0], nfa.accept)

    def test_opt_take_before_skip(self):
        nfa = compile_pattern(Opt(self.C))
        targets = [t for t, _ in nfa.closure[nfa.start]]
        self.assertEqual(targets[-1], nfa.accept)
        self.assertEqual(len(targets), 2)

    def test_plus_loop_before_exit(self):
        nfa = compile_pattern(Plus(self.C))
        # From the start, only the body is reachable (no empty match).
        self.assertEqual([t for t, _ in nfa.closure[nfa.start]], [1])
        after = [u for u, es in enumerate(nfa.edges) if len(es) == 2]
        self.assertEqual(len(after), 1)
        loop, exit_ = nfa.edges[after[0]]
        self.assertEqual(loop.target, 1)

    def test_anything_single_state(self):
        nfa = compile_pattern(Anything())
        loops = [(u, e) for u, es in enumerate(nfa.edges) for e in es if e.kind == SEG]
        self.assertEqual(len(loops), 1)
        u, e = loops[0]
        self.assertEqual(e.target, u)
        self.assertIsNone(e.payload)

    def test_nothing(self):
        nfa = compile_pattern(Nothing())
        self.assertEqual(nfa.closure[nfa.start], ((nfa.accept, ()),))

    def test_errors(self):
        with self.assertRaises(YascDefinitionError):
            compile_pattern(Seq((self.V, Macro("C"))))
        with self.assertRaises(YascDefinitionError) as cm:
            compile_pattern(Seq((self.V, Locus())))
        self.assertIn("split_at_locus", cm.exception.hint)
        # Plan P8: ^X compiles to a zero-width FLOAT edge, S^X to a SEG edge on a LinkSpec.
        from yasc.nfa import FLOAT
        from yasc.tiers import FloatPred, LinkSpec
        nfa = compile_pattern(Seq((self.V, AutoFloat(None, "[H]"))))
        kinds = [e.kind for es in nfa.edges for e in es]
        self.assertIn(FLOAT, kinds)
        self.assertTrue(any(isinstance(e.payload, FloatPred) for es in nfa.edges for e in es))
        nfa = compile_pattern(Linked(self.V, None, "[H]"))
        segs = [e.payload for es in nfa.edges for e in es if e.kind == SEG]
        self.assertEqual(len(segs), 1)
        self.assertIsInstance(segs[0], LinkSpec)
        self.assertEqual(segs[0].canonical(), self.V.spec.canonical() + "^[H]")
        with self.assertRaises(YascDefinitionError):
            compile_pattern(Linked(Seq((self.V, self.V)), None, "[H]"))

    def test_first_specs_attribute(self):
        nfa = compile_pattern(Seq((Opt(self.C), self.V)))
        self.assertEqual(nfa.first_specs, (self.C.spec, self.V.spec))
        self.assertIsNone(nfa.reversed().first_specs)
        self.assertIsNone(compile_pattern(Star(self.V)).first_specs)


class ClosureTests(Base):
    """Design §5.2: precomputed ordered ε-closure, per-path ops, cycles cut."""

    def test_ops_are_per_path(self):
        """R2: ops of one alternative never leak into a sibling's path."""
        p = Alt(9, (Capture(1, self.V), Capture(2, self.V), Seq((Boundary(Mark.WORD), self.V))))
        nfa = compile_pattern(p)
        entries = nfa.closure[nfa.start]
        self.assertEqual([ops for _, ops in entries], [
            ((ALT, (9, 0)), (CAP_OPEN, 1)),
            ((ALT, (9, 1)), (CAP_OPEN, 2)),
            ((ALT, (9, 2)), (ASSERT, BoundaryPred(Mark.WORD))),
        ])

    def test_epsilon_cycles_terminate(self):
        """R2: flag-accumulating ε-cycles no longer grow without bound."""
        for p in (Star(Star(self.V)), Star(Star(Boundary(Mark.WORD))), Star(Opt(Capture(1, Nothing()))),
                  Star(Alt(2, (Boundary(Mark.MORPHEME), Nothing()))), Plus(Star(Nothing()))):
            with self.subTest(p=p.canonical()):
                nfa = compile_pattern(p)
                for u, entries in enumerate(nfa.closure):
                    self.assertLessEqual(len(entries), 2 * nfa.n_states)
                    self.assertEqual(len(entries), len(set(entries)))

    def test_targets_are_consuming_or_accept(self):
        nfa = compile_pattern(Seq((Star(self.C), Opt(self.V), BackRef(1))))
        for entries in nfa.closure:
            for t, _ in entries:
                self.assertTrue(t == nfa.accept or any(e.kind in (SEG, BACKREF) for e in nfa.edges[t]))


class ReversalTests(Base):
    """Design §5.2: reverse every edge, swap start/accept and capture open/close."""

    def test_reverse(self):
        nfa = compile_pattern(Seq((Capture(1, self.C), Star(self.V))))
        rev = nfa.reversed()
        self.assertTrue(rev.is_reversed)
        self.assertEqual((rev.start, rev.accept), (nfa.accept, nfa.start))
        fwd_edges = sorted((u, e.kind, e.target) for u, es in enumerate(nfa.edges) for e in es)
        swap = {CAP_OPEN: CAP_CLOSE, CAP_CLOSE: CAP_OPEN}
        back = sorted((e.target, swap.get(e.kind, e.kind), u) for u, es in enumerate(rev.edges) for e in es)
        self.assertEqual(fwd_edges, back)
        self.assertIs(nfa.reversed(), rev)
        self.assertIs(rev.reversed(), nfa)
        self.assertIs(rev.match_cache, nfa.match_cache)
        # The capture is opened first in matching order in both directions.
        first_rev_cap = [ops for _, ops in rev.closure[rev.start]][0]
        self.assertEqual(first_rev_cap, ())
        caps = [e.kind for es in rev.edges for e in es if e.kind in (CAP_OPEN, CAP_CLOSE)]
        self.assertEqual(sorted(caps), [CAP_OPEN, CAP_CLOSE])

    def test_reversed_star_is_greedy(self):
        rev = compile_pattern(Star(self.C)).reversed()
        self.assertEqual(rev.closure[rev.start][-1][0], rev.accept)

    def test_reversed_alt_keeps_textual_order(self):
        from yasc.matcher import match_anchored
        from yasc.segment import EMPTY
        V = self.V
        rev = compile_pattern(Alt(3, (Seq((self.C, V)), Seq((self.C, V)), V))).reversed()
        # Reversed, each alternative is entered at its last element, in textual order.
        self.assertEqual(len(rev.closure[rev.start]), 3)
        f = FakeForm([self.inv["p"], self.inv["a"]])
        res = match_anchored(rev, f, 2, EMPTY, direction=-1)
        self.assertEqual([(g, e.alt(3)) for g, e in res], [(0, 0), (0, 1), (1, 2)])


class PredicateTests(Base):
    """Spec §5.2 (boundary strength), §5.3 (brackets), §8.5 (union over a virtual gap)."""

    def test_boundary(self):
        f = FakeForm([self.inv["p"], self.inv["a"], self.inv["t"]], {1: [Mark.MORPHEME], 2: [Mark.SYLLABLE]})
        dot, mor, word = BoundaryPred(Mark.SYLLABLE), BoundaryPred(Mark.MORPHEME), BoundaryPred(Mark.WORD)
        self.assertFalse(dot.holds(f, 1))
        self.assertTrue(mor.holds(f, 1))
        self.assertTrue(dot.holds(f, 2))
        self.assertTrue(word.holds(f, 0) and word.holds(f, 3))
        self.assertTrue(dot.holds(f, 1, 2))  # union of gaps 1..2
        self.assertFalse(word.holds(f, 1, 2))
        self.assertEqual(word.canonical(), "#")

    def test_bracket(self):
        f = FakeForm([self.inv["p"]] * 4, brackets=[Bracket("N", 1, 3), Bracket("V", 0, 4)])
        self.assertTrue(BracketPred(OPEN, "N").holds(f, 1))
        self.assertFalse(BracketPred(OPEN, "N").holds(f, 3))
        self.assertTrue(BracketPred(CLOSE, "N").holds(f, 3))
        self.assertTrue(BracketPred(OPEN, "*").holds(f, 0))
        self.assertFalse(BracketPred(OPEN, "V").holds(f, 1))
        self.assertTrue(BracketPred(CLOSE, "N").holds(f, 2, 3))
        self.assertEqual(BracketPred(CLOSE, "*").canonical(), ">:*")


if __name__ == "__main__":
    unittest.main()
